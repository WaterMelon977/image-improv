import logging
import io
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger("app.services.image_processor")

# -- layout contract (fractions of image size) --
TITLE_BAND_FRAC = 0.15          # top band reserved for title
PRODUCT_RECT_FRAC = (0.05, 0.18, 0.55, 0.92)  # left, top, right, bottom
EDGE_PADDING_FRAC = 0.03
LOGO_PAD_FRAC = 0.01

# -- image analyzer --

def analyze_image_for_logo_placement(image_path: str) -> dict:
    """
    read the generated flux image.
    sample brightness in all 4 corners.
    pick the corner with highest contrast opportunity based on logo brightness.
    
    assumes logo is dark (brightness strategy: place logo in brightest corner).
    returns placement metadata dict.
    """
    logger.info("Analyzing image for logo placement: %s", image_path)
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    logger.debug("Base image dimensions: width=%d, height=%d", w, h)

    # sample 15% of image dimensions for each corner
    sample_w = int(w * 0.15)
    sample_h = int(h * 0.15)
    logger.debug("Sampling crop box dimensions: width=%d, height=%d", sample_w, sample_h)

    corners = {
        "top_left":     img.crop((0,          0,          sample_w,     sample_h)),
        "top_right":    img.crop((w-sample_w, 0,          w,            sample_h)),
        "bottom_left":  img.crop((0,          h-sample_h, sample_w,     h)),
        "bottom_right": img.crop((w-sample_w, h-sample_h, w,            h)),
    }

    # compute mean brightness per corner (0-255)
    brightness_map = {}
    for name, crop in corners.items():
        arr = np.array(crop, dtype=float)
        brightness_map[name] = float(arr.mean())
    logger.debug("Computed corner brightness values: %s", brightness_map)

    # logo is assumed dark — place in brightest corner for maximum contrast
    best_corner = max(brightness_map, key=brightness_map.get)
    logger.info("Selected best corner for dark logo (highest brightness): %s (value=%.2f)", 
                best_corner, brightness_map[best_corner])

    # compute pixel coordinates for logo placement
    logo_w = int(w * 0.20)       # logo = 20% of image width
    padding = int(w * 0.03)      # 3% padding from edges

    # aspect ratio will be applied at composite time when we have the actual logo
    placement_map = {
        "top_left":     (padding,          padding),
        "top_right":    (w - logo_w - padding, padding),
        "bottom_left":  (padding,          None),   # y computed at composite time
        "bottom_right": (w - logo_w - padding, None),
    }

    x, y = placement_map[best_corner]
    logger.debug("Resolved initial placement coordinates: x=%d, y=%s (logo_target_width=%d, padding=%d)", 
                 x, y, logo_w, padding)

    return {
        "best_corner":    best_corner,
        "brightness_map": brightness_map,
        "logo_target_width": logo_w,
        "x":              x,
        "y":              y,        # None for bottom positions — computed later
        "image_width":    w,
        "image_height":   h,
        "padding":        padding
    }


# -- logo compositing --

def composite_logo(
    base_image_path: str,
    logo_path: str,
    placement: dict,
    session_id: str
) -> str:
    """
    place logo on the generated image at the analyzed position.
    pure pillow — no AI involved.
    returns path to final composited image.
    """
    logger.info("Compositing logo: base=%s, logo=%s, session_id=%s", base_image_path, logo_path, session_id)
    base = Image.open(base_image_path).convert("RGBA")
    w, h = base.size
    
    logo_target_w = placement["logo_target_width"]
    padding       = placement["padding"]

    # check for SVG file
    svg_path = logo_path.rsplit(".", 1)[0] + ".svg"
    logo = None
    
    if Path(svg_path).exists():
        try:
            logger.info("Found SVG logo at %s. Rendering on-the-fly...", svg_path)
            import fitz
            doc = fitz.open(svg_path)
            page = doc[0]
            rect = page.rect
            svg_w = rect.width
            # render at the exact target width first
            scale = logo_target_w / svg_w
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
            png_data = pix.tobytes("png")
            logo = Image.open(io.BytesIO(png_data)).convert("RGBA")
            logger.debug("SVG on-the-fly rendering successful")
        except Exception as e_svg:
            logger.warning("Failed to render SVG logo on-the-fly: %s. Falling back to PNG.", str(e_svg))
            logo = Image.open(logo_path).convert("RGBA")
    else:
        logo = Image.open(logo_path).convert("RGBA")

    # Crop transparent borders (empty margins)
    bbox = logo.getbbox()
    if bbox:
        logo_cropped = logo.crop(bbox)
        logger.debug("Cropped empty transparent margins. Bounding box: %s, size: %s -> %s", 
                     bbox, logo.size, logo_cropped.size)
        logo = logo_cropped
    else:
        logger.debug("No logo bounding box detected, keeping original logo size")

    # Resize logo proportionally to the target width
    logo_aspect = logo.height / logo.width
    logo_target_h = int(logo_target_w * logo_aspect)
    logo_resized  = logo.resize((logo_target_w, logo_target_h), Image.LANCZOS)
    logger.debug("Resized logo: width=%d, height=%d (aspect ratio=%.4f)",
                 logo_target_w, logo_target_h, logo_aspect)

    # resolve y for bottom positions
    x = placement["x"]
    y = placement["y"]
    if y is None:
        y = h - logo_target_h - padding
        logger.debug("Resolved dynamic bottom y coordinate: %d", y)

    # add subtle drop shadow behind logo for visibility on any background
    logger.debug("Creating shadow backdrop for logo visibility...")
    shadow = _make_shadow(logo_resized, blur_radius=4, opacity=80)
    shadow_offset = 3
    base.paste(shadow, (x + shadow_offset, y + shadow_offset), shadow)

    # paste logo with its own alpha
    logger.debug("Pasting logo onto base image at coordinates: (x=%d, y=%d)", x, y)
    base.paste(logo_resized, (x, y), logo_resized)

    # save final as png
    output_dir = settings.get_image_dir() / "generated"
    output_dir.mkdir(exist_ok=True)

    final_path = output_dir / f"{session_id}_final.png"
    base.convert("RGB").save(str(final_path), "PNG", optimize=True)
    logger.info("Composited image saved successfully to: %s", final_path)

    # update placement dict with resolved coordinates
    placement["x"] = x
    placement["y"] = y
    placement["logo_width"]  = logo_target_w
    placement["logo_height"] = logo_target_h

    return str(final_path)


def _make_shadow(
    image: Image.Image,
    blur_radius: int = 4,
    opacity: int = 80
) -> Image.Image:
    """
    create a soft drop shadow from the logo's alpha channel.
    helps logo remain visible on any background color.
    """
    from PIL import ImageFilter

    # extract alpha, fill with black, blur
    alpha = image.split()[3]
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, opacity), mask=alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    return shadow


# -- color theme extraction (for reporting / future use) --

def extract_dominant_colors(image_path: str, count: int = 5) -> list[str]:
    """
    extract dominant hex colors from a generated image.
    used for reporting and potential future tint overlay.
    """
    logger.info("Extracting dominant colors from: %s", image_path)
    try:
        from colorthief import ColorThief
        ct = ColorThief(image_path)
        palette = ct.get_palette(color_count=count, quality=1)
        hex_p = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
        logger.info("Dominant colors extracted: %s", hex_p)
        return hex_p
    except Exception as e:
        logger.warning("Failed to extract dominant colors: %s", str(e))
        return []


# -- title overlay: type systems + layouts + safe top band --

# Layout band heights (fraction of image height)
LAYOUT_BAND_FRAC = {
    "hero_headroom": 0.18,
    "magazine_stack": 0.24,
}
DEFAULT_LAYOUT = "hero_headroom"
ALLOWED_LAYOUTS = set(LAYOUT_BAND_FRAC.keys())

# Three premium type systems (display + support weights)
TYPE_SYSTEMS: dict[str, dict] = {
    "editorial_luxe": {
        "display": "CormorantGaramond-Bold.ttf",
        "display_fallback": "PlayfairDisplay.ttf",
        "body": "Montserrat-Light.ttf",
        "body_fallback": "OpenSans-SemiBold.ttf",
        "kicker_font": "Montserrat-Light.ttf",
        "size_scale": 1.12,
        "uppercase": False,
        "tracking_px": 1,       # open letter-spacing on display
        "sub_tracking_px": 2,
        "kicker_tracking_px": 4,
        "scrim_strength": 1.15,
        "rule_style": "hairline",  # thin long rule
    },
    "campaign_impact": {
        "display": "Montserrat-Black.ttf",
        "display_fallback": "Montserrat-Bold.ttf",
        "body": "Montserrat-Bold.ttf",
        "body_fallback": "OpenSans-Bold.ttf",
        "kicker_font": "Montserrat-Bold.ttf",
        "size_scale": 1.18,
        "uppercase": True,
        "tracking_px": -1,      # tight
        "sub_tracking_px": 1,
        "kicker_tracking_px": 3,
        "scrim_strength": 1.25,
        "rule_style": "bar",    # short thick accent
    },
    "modern_dtc": {
        "display": "Outfit-Bold.ttf",
        "display_fallback": "DMSans-Bold.ttf",
        "body": "Outfit-Regular.ttf",
        "body_fallback": "DMSans-Regular.ttf",
        "kicker_font": "Outfit-Regular.ttf",
        "size_scale": 1.08,
        "uppercase": False,
        "tracking_px": 0,
        "sub_tracking_px": 1,
        "kicker_tracking_px": 3,
        "scrim_strength": 1.0,
        "rule_style": "short",
    },
}
ALLOWED_TYPE_SYSTEMS = set(TYPE_SYSTEMS.keys())


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fonts_dir() -> Path:
    return _project_root() / "data" / "fonts"


def _load_font(filename: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _fonts_dir() / filename
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception as e:
            logger.warning("Failed to load font %s: %s", path, e)
    for fallback in (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if Path(fallback).exists():
            try:
                return ImageFont.truetype(fallback, size=size)
            except Exception:
                continue
    logger.warning("Using Pillow default font (no TTF found)")
    return ImageFont.load_default()


def _pick_existing_font(*candidates: str) -> str:
    fonts = _fonts_dir()
    for name in candidates:
        if name and (fonts / name).exists():
            return name
    return "OpenSans-Bold.ttf"


def resolve_type_mood(type_mood: str | None, brand_voice: list | None = None) -> str:
    """Normalize type_mood; fall back from brand_voice keywords when missing/invalid."""
    allowed = {
        "minimal_clean",
        "festive_bold",
        "luxury_editorial",
        "playful_soft",
        "bold_street",
    }
    mood = (type_mood or "").strip().lower()
    if mood in allowed:
        return mood

    voice = " ".join(brand_voice or []).lower()
    mapping = [
        (("luxury", "elegant", "premium", "refined", "intimate"), "luxury_editorial"),
        (("playful", "fun", "friendly", "whimsical"), "playful_soft"),
        (("energetic", "festive", "party", "vibrant", "bold"), "festive_bold"),
        (("edgy", "urban", "street", "raw"), "bold_street"),
        (("minimal", "clean", "modern", "simple"), "minimal_clean"),
    ]
    for keys, pack in mapping:
        if any(k in voice for k in keys):
            return pack
    return "minimal_clean"


def resolve_type_system(
    type_system: str | None = None,
    type_mood: str | None = None,
    brand_voice: list | None = None,
) -> str:
    """Resolve a premium type system id."""
    sys_id = (type_system or "").strip().lower()
    if sys_id in ALLOWED_TYPE_SYSTEMS:
        return sys_id

    mood = resolve_type_mood(type_mood, brand_voice=brand_voice)
    mood_map = {
        "luxury_editorial": "editorial_luxe",
        "minimal_clean": "modern_dtc",
        "playful_soft": "modern_dtc",
        "festive_bold": "campaign_impact",
        "bold_street": "campaign_impact",
    }
    voice = " ".join(brand_voice or []).lower()
    if any(k in voice for k in ("luxury", "elegant", "premium", "refined", "intimate")):
        return "editorial_luxe"
    if any(k in voice for k in ("bold", "energetic", "festive", "party", "street")):
        return "campaign_impact"
    return mood_map.get(mood, "modern_dtc")


def resolve_layout(layout: str | None = None, type_system: str | None = None) -> str:
    lay = (layout or "").strip().lower()
    if lay in ALLOWED_LAYOUTS:
        return lay
    # editorial defaults to magazine stack; impact/dtc to hero
    if type_system == "editorial_luxe":
        return "magazine_stack"
    return DEFAULT_LAYOUT


def _resolve_system_fonts(type_system: str) -> dict:
    cfg = TYPE_SYSTEMS.get(type_system, TYPE_SYSTEMS["modern_dtc"])
    display = _pick_existing_font(cfg["display"], cfg.get("display_fallback", ""), "Montserrat-Bold.ttf", "OpenSans-Bold.ttf")
    body = _pick_existing_font(cfg["body"], cfg.get("body_fallback", ""), "OpenSans-SemiBold.ttf", display)
    kicker = _pick_existing_font(cfg.get("kicker_font", ""), body, "Montserrat-Light.ttf", "OpenSans-SemiBold.ttf")
    return {
        **cfg,
        "display_file": display,
        "body_file": body,
        "kicker_file": kicker,
    }


def _short_headline_boost(headline: str) -> float:
    """Billboard scale: fewer words → larger type."""
    n = len((headline or "").split())
    if n <= 2:
        return 1.35
    if n <= 3:
        return 1.22
    if n <= 4:
        return 1.10
    return 1.0


def _split_headline_lines(headline: str, max_words_per_line: int = 3) -> list[str]:
    """Balance into 1–2 display lines for premium stacking."""
    words = (headline or "").split()
    if len(words) <= max_words_per_line:
        return [" ".join(words)] if words else [""]
    # prefer split near middle
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, tracking: int = 0) -> tuple[int, int]:
    if not text:
        return 0, 0
    if tracking == 0 or len(text) < 2:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]
    # measured with tracking
    x = 0
    h = 0
    for i, ch in enumerate(text):
        bb = draw.textbbox((0, 0), ch, font=font)
        cw, chh = bb[2] - bb[0], bb[3] - bb[1]
        x += cw
        if i < len(text) - 1:
            x += tracking
        h = max(h, chh)
    return x, h


def _draw_text_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple,
    tracking: int = 0,
):
    x, y = xy
    if tracking == 0 or len(text) < 2:
        draw.text((x, y), text, font=font, fill=fill)
        return
    for i, ch in enumerate(text):
        draw.text((x, y), ch, font=font, fill=fill)
        bb = draw.textbbox((0, 0), ch, font=font)
        x += (bb[2] - bb[0]) + tracking


def _hex_to_rgb(hex_color: str | None, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    if not hex_color:
        return default
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return default
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return default


def _rects_intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int], pad: int = 0) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    if pad:
        ax1 -= pad
        ay1 -= pad
        ax2 += pad
        ay2 += pad
    return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)


def _product_forbid_rect(w: int, h: int) -> tuple[int, int, int, int]:
    l, t, r, b = PRODUCT_RECT_FRAC
    return (int(w * l), int(h * t), int(w * r), int(h * b))


def _logo_forbid_rect(placement: dict | None, w: int, h: int) -> tuple[int, int, int, int] | None:
    if not placement:
        return None
    x = placement.get("x")
    y = placement.get("y")
    lw = placement.get("logo_width")
    lh = placement.get("logo_height")
    if x is None or y is None or not lw or not lh:
        return None
    pad = int(w * LOGO_PAD_FRAC)
    return (int(x) - pad, int(y) - pad, int(x) + int(lw) + pad, int(y) + int(lh) + pad)


def _score_region(img_rgb: Image.Image, rect: tuple[int, int, int, int]) -> dict:
    """Brightness + clutter (std of luminance) for a crop."""
    x1, y1, x2, y2 = rect
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img_rgb.width, x2), min(img_rgb.height, y2)
    if x2 <= x1 or y2 <= y1:
        return {"brightness": 128.0, "clutter": 999.0}
    crop = img_rgb.crop((x1, y1, x2, y2))
    arr = np.array(crop, dtype=float)
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    return {"brightness": float(lum.mean()), "clutter": float(lum.std())}


def _anchor_xy(
    anchor: str,
    block_w: int,
    block_h: int,
    w: int,
    h: int,
    padding: int,
    band_h: int,
) -> tuple[int, int]:
    y = max(padding, min(padding + int(band_h * 0.08), max(padding, band_h - block_h - padding // 2)))
    if anchor == "top_left":
        x = padding
    elif anchor == "top_right":
        x = w - block_w - padding
    else:
        x = (w - block_w) // 2
    x = max(padding, min(x, w - block_w - padding))
    return x, y


def _logo_side_penalty(anchor: str, logo_corner: str | None) -> float:
    if not logo_corner:
        return 0.0
    if logo_corner.startswith("top_"):
        if anchor == "top_left" and "left" in logo_corner:
            return 50.0
        if anchor == "top_right" and "right" in logo_corner:
            return 50.0
        if anchor == "top_center" and logo_corner.startswith("top_"):
            return 15.0
    return 0.0


def _measure_stack(
    draw: ImageDraw.ImageDraw,
    layout: str,
    kicker: str,
    headline_lines: list[str],
    subhead: str,
    font_k: ImageFont.ImageFont,
    font_h: ImageFont.ImageFont,
    font_s: ImageFont.ImageFont,
    tracking_h: int,
    tracking_s: int,
    tracking_k: int,
    fs: int,
) -> tuple[int, int, dict]:
    """
    Measure full type stack. Returns block_w, block_h, geometry meta.
    """
    gaps = {
        "after_kicker": max(6, fs // 5),
        "between_headline": max(2, fs // 12),
        "after_headline": max(8, fs // 4),
        "after_rule": max(6, fs // 6),
    }
    y = 0
    max_w = 0
    meta: dict = {"lines": []}

    if layout == "magazine_stack" and kicker:
        tw, th = _text_size(draw, kicker, font_k, tracking_k)
        meta["lines"].append({"role": "kicker", "text": kicker, "y": y, "h": th, "w": tw})
        max_w = max(max_w, tw)
        y += th + gaps["after_kicker"]

    for i, line in enumerate(headline_lines):
        if not line:
            continue
        tw, th = _text_size(draw, line, font_h, tracking_h)
        meta["lines"].append({"role": "headline", "text": line, "y": y, "h": th, "w": tw})
        max_w = max(max_w, tw)
        y += th
        if i < len(headline_lines) - 1:
            y += gaps["between_headline"]

    y += gaps["after_headline"]
    rule_h = max(2, fs // 18) if layout == "magazine_stack" else max(2, fs // 14)
    meta["rule_y"] = y
    meta["rule_h"] = rule_h
    y += rule_h + gaps["after_rule"]

    if subhead:
        tw, th = _text_size(draw, subhead, font_s, tracking_s)
        meta["lines"].append({"role": "subhead", "text": subhead, "y": y, "h": th, "w": tw})
        max_w = max(max_w, tw)
        y += th

    meta["block_w"] = max_w
    meta["block_h"] = y
    return max_w, y, meta


def plan_title_placement(
    image_path: str,
    headline: str,
    subhead: str = "",
    type_mood: str = "minimal_clean",
    type_system: str | None = None,
    layout: str | None = None,
    kicker: str = "",
    logo_placement: dict | None = None,
    primary_color: str | None = None,
    brand_voice: list | None = None,
) -> dict:
    """
    Plan premium title stack: type system + layout template + safe zones.
    """
    logger.info("Planning title placement for: %s", image_path)
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    padding = int(w * EDGE_PADDING_FRAC)
    product_rect = _product_forbid_rect(w, h)
    logo_rect = _logo_forbid_rect(logo_placement, w, h)
    logo_corner = (logo_placement or {}).get("best_corner")

    type_mood = resolve_type_mood(type_mood, brand_voice=brand_voice)
    type_system = resolve_type_system(type_system, type_mood=type_mood, brand_voice=brand_voice)
    layout = resolve_layout(layout, type_system=type_system)
    band_frac = LAYOUT_BAND_FRAC.get(layout, TITLE_BAND_FRAC)
    band_h = int(h * band_frac)
    sys_fonts = _resolve_system_fonts(type_system)

    uppercase = bool(sys_fonts.get("uppercase"))
    render_headline = headline.upper() if uppercase else headline
    render_subhead = (subhead or "").strip()
    render_kicker = (kicker or "").strip().upper() if kicker else ""
    if layout == "magazine_stack" and not render_kicker:
        render_kicker = "CAMPAIGN"

    # multi-line display for magazine / long hooks
    if layout == "magazine_stack" or len(render_headline.split()) >= 4:
        headline_lines = _split_headline_lines(render_headline, max_words_per_line=3)
    else:
        headline_lines = [render_headline]

    boost = _short_headline_boost(render_headline)
    base_size = max(20, int(w * 0.052 * float(sys_fonts["size_scale"]) * boost))
    sub_size = max(13, int(base_size * (0.38 if layout == "magazine_stack" else 0.48)))
    kicker_size = max(11, int(base_size * 0.28))

    tracking_h = int(sys_fonts.get("tracking_px") or 0)
    tracking_s = int(sys_fonts.get("sub_tracking_px") or 0)
    tracking_k = int(sys_fonts.get("kicker_tracking_px") or 0)

    draw = ImageDraw.Draw(img)
    anchors = ["top_left", "top_center", "top_right"]
    if logo_corner == "top_right":
        anchors = ["top_left", "top_center", "top_right"]
    elif logo_corner == "top_left":
        anchors = ["top_right", "top_center", "top_left"]
    # magazine prefers left editorial lockup
    if layout == "magazine_stack":
        anchors = ["top_left", "top_center", "top_right"]

    use_subhead = bool(render_subhead)
    use_kicker = layout == "magazine_stack" and bool(render_kicker)
    font_scale = 1.0
    chosen = None
    fallbacks_used: list[str] = []
    stack_meta = {}

    for _attempt in range(8):
        fs = max(16, int(base_size * font_scale))
        ss = max(11, int(sub_size * font_scale))
        ks = max(10, int(kicker_size * font_scale))
        font_h = _load_font(sys_fonts["display_file"], fs)
        font_s = _load_font(sys_fonts["body_file"], ss)
        font_k = _load_font(sys_fonts["kicker_file"], ks)

        block_w, block_h, stack_meta = _measure_stack(
            draw,
            layout,
            render_kicker if use_kicker else "",
            headline_lines,
            render_subhead if use_subhead else "",
            font_k,
            font_h,
            font_s,
            tracking_h,
            tracking_s,
            tracking_k,
            fs,
        )

        if block_h > band_h - padding:
            font_scale *= 0.88
            fallbacks_used.append("shrink_for_band_height")
            continue

        max_title_w = int(w * (0.72 if layout == "hero_headroom" else 0.68))
        if block_w > max_title_w:
            font_scale *= 0.88
            fallbacks_used.append("shrink_for_width")
            continue

        candidates = []
        for anchor in anchors:
            x, y = _anchor_xy(anchor, block_w, block_h, w, h, padding, band_h)
            title_rect = (x, y, x + block_w, y + block_h)
            if title_rect[3] > band_h + padding:
                continue
            hits_product = _rects_intersect(title_rect, product_rect, pad=2)
            hits_logo = bool(logo_rect and _rects_intersect(title_rect, logo_rect, pad=2))
            if hits_product or hits_logo:
                continue
            metrics = _score_region(img, title_rect)
            score = metrics["clutter"] + _logo_side_penalty(anchor, logo_corner)
            if layout == "magazine_stack" and anchor == "top_left":
                score -= 4.0
            elif anchor == "top_left":
                score -= 2.0
            candidates.append({
                "anchor": anchor,
                "x": x,
                "y": y,
                "title_rect": title_rect,
                "score": score,
                "brightness": metrics["brightness"],
                "clutter": metrics["clutter"],
                "font_size": fs,
                "sub_font_size": ss,
                "kicker_font_size": ks,
                "block_w": block_w,
                "block_h": block_h,
                "use_subhead": use_subhead,
                "use_kicker": use_kicker,
                "stack_meta": stack_meta,
            })

        if candidates:
            candidates.sort(key=lambda c: c["score"])
            chosen = candidates[0]
            break

        if use_kicker:
            use_kicker = False
            fallbacks_used.append("drop_kicker")
            continue
        if use_subhead:
            use_subhead = False
            fallbacks_used.append("drop_subhead")
            continue
        if len(headline_lines) > 1:
            headline_lines = [" ".join(headline_lines)]
            fallbacks_used.append("single_line_headline")
            continue
        font_scale *= 0.88
        fallbacks_used.append("shrink_retry")

    if not chosen:
        logger.warning("No clean title candidate; forcing top_left minimal")
        fallbacks_used.append("force_top_left")
        fs = max(14, int(base_size * 0.55))
        ss = max(11, int(sub_size * 0.55))
        ks = max(10, int(kicker_size * 0.55))
        font_h = _load_font(sys_fonts["display_file"], fs)
        font_s = _load_font(sys_fonts["body_file"], ss)
        font_k = _load_font(sys_fonts["kicker_file"], ks)
        headline_lines = [render_headline]
        block_w, block_h, stack_meta = _measure_stack(
            draw, "hero_headroom", "", headline_lines, "",
            font_k, font_h, font_s, tracking_h, tracking_s, tracking_k, fs,
        )
        x, y = _anchor_xy("top_left", block_w, block_h, w, h, padding, band_h)
        title_rect = (x, y, x + block_w, y + block_h)
        if logo_rect and _rects_intersect(title_rect, logo_rect):
            x, y = _anchor_xy("top_right", block_w, block_h, w, h, padding, band_h)
            title_rect = (x, y, x + block_w, y + block_h)
            anchor = "top_right"
        else:
            anchor = "top_left"
        metrics = _score_region(img, title_rect)
        chosen = {
            "anchor": anchor,
            "x": x,
            "y": y,
            "title_rect": title_rect,
            "score": metrics["clutter"],
            "brightness": metrics["brightness"],
            "clutter": metrics["clutter"],
            "font_size": fs,
            "sub_font_size": ss,
            "kicker_font_size": ks,
            "block_w": block_w,
            "block_h": block_h,
            "use_subhead": False,
            "use_kicker": False,
            "stack_meta": stack_meta,
        }
        layout = "hero_headroom"

    brightness = chosen["brightness"]
    if brightness >= 140:
        text_rgb = (18, 18, 18)
        shadow_rgb = (255, 255, 255)
        scrim_dark = False
        kicker_rgb = (60, 60, 60)
    else:
        text_rgb = (255, 255, 255)
        shadow_rgb = (0, 0, 0)
        scrim_dark = True
        kicker_rgb = (220, 220, 220)

    accent = _hex_to_rgb(primary_color, default=(255, 255, 255) if scrim_dark else (28, 28, 28))

    # rule width by style
    rule_style = sys_fonts.get("rule_style", "short")
    bw = chosen["block_w"]
    if rule_style == "hairline":
        rule_w = max(int(bw * 0.55), int(w * 0.14))
        rule_h = max(1, chosen["font_size"] // 28)
    elif rule_style == "bar":
        rule_w = max(int(bw * 0.28), int(w * 0.08))
        rule_h = max(3, chosen["font_size"] // 12)
    else:
        rule_w = max(int(bw * 0.35), int(w * 0.10))
        rule_h = max(2, chosen["font_size"] // 16)

    placement = {
        "headline": render_headline,
        "headline_source": headline,
        "headline_lines": headline_lines if chosen.get("use_subhead") is not None else headline_lines,
        "subhead": render_subhead if chosen["use_subhead"] else "",
        "kicker": render_kicker if chosen.get("use_kicker") else "",
        "type_mood": type_mood,
        "type_system": type_system,
        "layout": layout,
        "uppercase": uppercase,
        "anchor": chosen["anchor"],
        "x": chosen["x"],
        "y": chosen["y"],
        "title_rect": list(chosen["title_rect"]),
        "block_w": chosen["block_w"],
        "block_h": chosen["block_h"],
        "font_size": chosen["font_size"],
        "sub_font_size": chosen["sub_font_size"],
        "kicker_font_size": chosen.get("kicker_font_size", 12),
        "display_font": sys_fonts["display_file"],
        "body_font": sys_fonts["body_file"],
        "kicker_font": sys_fonts["kicker_file"],
        "tracking_px": tracking_h,
        "sub_tracking_px": tracking_s,
        "kicker_tracking_px": tracking_k,
        "text_color": list(text_rgb),
        "kicker_color": list(kicker_rgb),
        "shadow_color": list(shadow_rgb),
        "scrim_dark": scrim_dark,
        "scrim_strength": float(sys_fonts.get("scrim_strength") or 1.0),
        "accent_color": list(accent),
        "rule_style": rule_style,
        "rule_w": rule_w,
        "rule_h": rule_h,
        "stack_meta": chosen.get("stack_meta") or stack_meta,
        "brightness": brightness,
        "clutter": chosen["clutter"],
        "product_rect": list(product_rect),
        "logo_rect": list(logo_rect) if logo_rect else None,
        "fallbacks": fallbacks_used,
        "image_width": w,
        "image_height": h,
        "band_height": band_h,
        "short_headline_boost": boost,
    }
    # ensure headline_lines always present for composite
    placement["headline_lines"] = headline_lines if not chosen.get("use_kicker") and layout != "magazine_stack" else (
        placement.get("headline_lines") or headline_lines
    )
    # fix: always use the lines we measured with
    sm = chosen.get("stack_meta") or {}
    hl_from_meta = [ln["text"] for ln in sm.get("lines", []) if ln.get("role") == "headline"]
    if hl_from_meta:
        placement["headline_lines"] = hl_from_meta

    logger.info(
        "Title placement: system=%s layout=%s anchor=%s size=%d boost=%.2f fallbacks=%s",
        type_system,
        layout,
        placement["anchor"],
        placement["font_size"],
        boost,
        fallbacks_used,
    )
    return placement


def composite_title(
    base_image_path: str,
    title_placement: dict,
    session_id: str,
    output_suffix: str = "final",
) -> str:
    """Draw premium title stack (scrim + kicker + display + rule + subhead)."""
    logger.info(
        "Compositing title (%s / %s) onto %s",
        title_placement.get("type_system"),
        title_placement.get("layout"),
        base_image_path,
    )
    base = Image.open(base_image_path).convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    band_h = int(title_placement.get("band_height") or int(h * TITLE_BAND_FRAC))
    scrim_dark = title_placement.get("scrim_dark", True)
    strength = float(title_placement.get("scrim_strength") or 1.0)

    # softer, longer top scrim
    scrim = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for row in range(band_h):
        t = 1.0 - (row / max(1, band_h - 1))
        alpha = int(min(160, 125 * strength) * (t ** 1.35))
        if scrim_dark:
            color = (0, 0, 0, alpha)
        else:
            color = (255, 255, 255, int(alpha * 0.9))
        scrim_draw.line([(0, row), (w, row)], fill=color)
    overlay.paste(scrim, (0, 0), scrim)

    fs = int(title_placement["font_size"])
    font_h = _load_font(title_placement["display_font"], fs)
    font_s = _load_font(title_placement["body_font"], int(title_placement["sub_font_size"]))
    font_k = _load_font(
        title_placement.get("kicker_font") or title_placement["body_font"],
        int(title_placement.get("kicker_font_size") or max(11, fs // 3)),
    )

    x = int(title_placement["x"])
    y = int(title_placement["y"])
    text_color = tuple(title_placement.get("text_color") or (255, 255, 255)) + (255,)
    kicker_color = tuple(title_placement.get("kicker_color") or text_color[:3]) + (230,)
    shadow_color = tuple(title_placement.get("shadow_color") or (0, 0, 0)) + (110,)
    accent = tuple(title_placement.get("accent_color") or (255, 255, 255)) + (230,)
    tracking_h = int(title_placement.get("tracking_px") or 0)
    tracking_s = int(title_placement.get("sub_tracking_px") or 0)
    tracking_k = int(title_placement.get("kicker_tracking_px") or 0)
    shadow_offset = max(1, fs // 22)

    stack = title_placement.get("stack_meta") or {}
    lines = stack.get("lines") or []

    def _draw_tracked(text: str, font, ly: int, fill, tracking: int, with_shadow: bool = True):
        if with_shadow:
            _draw_text_tracked(draw, (x + shadow_offset, ly + shadow_offset), text, font, shadow_color, tracking)
        _draw_text_tracked(draw, (x, ly), text, font, fill, tracking)

    if lines:
        for ln in lines:
            role = ln.get("role")
            text = ln.get("text") or ""
            ly = y + int(ln.get("y") or 0)
            if role == "kicker":
                _draw_tracked(text, font_k, ly, kicker_color, tracking_k, with_shadow=False)
            elif role == "headline":
                _draw_tracked(text, font_h, ly, text_color, tracking_h)
            elif role == "subhead":
                _draw_tracked(text, font_s, ly, text_color, tracking_s)
        rule_y = y + int(stack.get("rule_y") or 0)
    else:
        # fallback simple draw
        cy = y
        kicker = title_placement.get("kicker") or ""
        if kicker:
            _draw_tracked(kicker, font_k, cy, kicker_color, tracking_k, with_shadow=False)
            tw, th = _text_size(draw, kicker, font_k, tracking_k)
            cy += th + max(6, fs // 5)
        for line in title_placement.get("headline_lines") or [title_placement.get("headline") or ""]:
            if not line:
                continue
            _draw_tracked(line, font_h, cy, text_color, tracking_h)
            tw, th = _text_size(draw, line, font_h, tracking_h)
            cy += th + max(2, fs // 12)
        rule_y = cy + max(4, fs // 8)
        sub = title_placement.get("subhead") or ""
        if sub:
            # rule then subhead drawn below
            pass

    rule_h = int(title_placement.get("rule_h") or max(2, fs // 16))
    rule_w = int(title_placement.get("rule_w") or max(int((title_placement.get("block_w") or 40) * 0.35), 24))
    if "rule_y" not in stack and lines:
        rule_y = y + int(stack.get("rule_y") or 0)
    elif not lines:
        rule_y = rule_y  # from fallback branch

    draw.rectangle([x, rule_y, x + rule_w, rule_y + rule_h], fill=accent)

    # if fallback path had subhead after rule
    if not lines:
        sub = title_placement.get("subhead") or ""
        if sub:
            sy = rule_y + rule_h + max(6, fs // 6)
            _draw_tracked(sub, font_s, sy, text_color, tracking_s)

    composed = Image.alpha_composite(base, overlay)
    output_dir = settings.get_image_dir() / "generated"
    output_dir.mkdir(exist_ok=True)
    final_path = output_dir / f"{session_id}_{output_suffix}.png"
    composed.convert("RGB").save(str(final_path), "PNG", optimize=True)
    logger.info("Title composited image saved to: %s", final_path)
    return str(final_path)


def apply_title_overlay(
    base_image_path: str,
    headline: str,
    session_id: str,
    subhead: str = "",
    type_mood: str = "minimal_clean",
    type_system: str | None = None,
    layout: str | None = None,
    kicker: str = "",
    logo_placement: dict | None = None,
    primary_color: str | None = None,
    brand_voice: list | None = None,
) -> tuple[str, dict]:
    """Plan + composite premium title. Returns (final_path, placement)."""
    placement = plan_title_placement(
        image_path=base_image_path,
        headline=headline,
        subhead=subhead,
        type_mood=type_mood,
        type_system=type_system,
        layout=layout,
        kicker=kicker,
        logo_placement=logo_placement,
        primary_color=primary_color,
        brand_voice=brand_voice,
    )
    path = composite_title(base_image_path, placement, session_id)
    return path, placement

