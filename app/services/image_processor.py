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


# -- title overlay: fixed top band + measure + forbid product/logo --

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
    # Windows fallbacks
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


def _resolve_font_files(type_mood: str) -> tuple[str, str]:
    """
    Map type_mood → (display_font, body_font) under data/fonts.
    Falls back to Open Sans when a pack file is missing.
    """
    packs = {
        "minimal_clean": ("OpenSans-Bold.ttf", "OpenSans-SemiBold.ttf"),
        "festive_bold": ("Montserrat-Bold.ttf", "OpenSans-SemiBold.ttf"),
        # Prefer Playfair when present; otherwise Montserrat for a heavier display look
        "luxury_editorial": ("PlayfairDisplay.ttf", "OpenSans-SemiBold.ttf"),
        "playful_soft": ("OpenSans-Bold.ttf", "OpenSans-SemiBold.ttf"),
        "bold_street": ("Montserrat-Bold.ttf", "OpenSans-Bold.ttf"),
    }
    display, body = packs.get(type_mood, packs["minimal_clean"])
    fonts = _fonts_dir()
    if not (fonts / display).exists():
        # luxury / festive fallbacks
        if type_mood == "luxury_editorial" and (fonts / "Montserrat-Bold.ttf").exists():
            display = "Montserrat-Bold.ttf"
        elif (fonts / "Montserrat-Bold.ttf").exists() and type_mood in ("festive_bold", "bold_street"):
            display = "Montserrat-Bold.ttf"
        else:
            display = "OpenSans-Bold.ttf"
    if not (fonts / body).exists():
        body = "OpenSans-SemiBold.ttf" if (fonts / "OpenSans-SemiBold.ttf").exists() else display
    return display, body


def _mood_recipe(type_mood: str) -> dict:
    """Typography knobs beyond the font file."""
    recipes = {
        "minimal_clean": {"size_scale": 1.0, "uppercase": False, "tracking_note": "normal"},
        "festive_bold": {"size_scale": 1.08, "uppercase": True, "tracking_note": "tight"},
        "luxury_editorial": {"size_scale": 1.05, "uppercase": False, "tracking_note": "open"},
        "playful_soft": {"size_scale": 1.0, "uppercase": False, "tracking_note": "normal"},
        "bold_street": {"size_scale": 1.12, "uppercase": True, "tracking_note": "tight"},
    }
    return recipes.get(type_mood, recipes["minimal_clean"])


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


def _measure_text_block(
    draw: ImageDraw.ImageDraw,
    headline: str,
    subhead: str,
    font_h: ImageFont.ImageFont,
    font_s: ImageFont.ImageFont,
    gap: int,
) -> tuple[int, int, list[tuple[str, ImageFont.ImageFont, int]]]:
    """Return (block_w, block_h, lines as (text, font, y_offset))."""
    lines: list[tuple[str, ImageFont.ImageFont, int]] = []
    y = 0
    max_w = 0

    if headline:
        bbox = draw.textbbox((0, 0), headline, font=font_h)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lines.append((headline, font_h, y))
        max_w = max(max_w, tw)
        y += th + (gap if subhead else 0)

    if subhead:
        bbox = draw.textbbox((0, 0), subhead, font=font_s)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lines.append((subhead, font_s, y))
        max_w = max(max_w, tw)
        y += th

    return max_w, y, lines


def _score_region(img_rgb: Image.Image, rect: tuple[int, int, int, int]) -> dict:
    """Brightness + clutter (std of luminance) for a crop."""
    x1, y1, x2, y2 = rect
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img_rgb.width, x2), min(img_rgb.height, y2)
    if x2 <= x1 or y2 <= y1:
        return {"brightness": 128.0, "clutter": 999.0}
    crop = img_rgb.crop((x1, y1, x2, y2))
    arr = np.array(crop, dtype=float)
    # luminance approx
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    brightness = float(lum.mean())
    clutter = float(lum.std())
    return {"brightness": brightness, "clutter": clutter}


def _anchor_xy(
    anchor: str,
    block_w: int,
    block_h: int,
    w: int,
    h: int,
    padding: int,
) -> tuple[int, int]:
    band_h = int(h * TITLE_BAND_FRAC)
    # vertical: center block within top band with padding
    y = max(padding, (band_h - block_h) // 2)
    y = min(y, max(padding, band_h - block_h - padding // 2))

    if anchor == "top_left":
        x = padding
    elif anchor == "top_right":
        x = w - block_w - padding
    else:  # top_center
        x = (w - block_w) // 2
    x = max(padding, min(x, w - block_w - padding))
    return x, y


def _logo_side_penalty(anchor: str, logo_corner: str | None) -> float:
    """Deprioritize title anchors that sit on the same side as the logo."""
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


def plan_title_placement(
    image_path: str,
    headline: str,
    subhead: str = "",
    type_mood: str = "minimal_clean",
    logo_placement: dict | None = None,
    primary_color: str | None = None,
) -> dict:
    """
    Measure title box, score top-band candidates (L/C/R), forbid product + logo.
    Returns placement dict used by composite_title.
    """
    logger.info("Planning title placement for: %s", image_path)
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    padding = int(w * EDGE_PADDING_FRAC)
    band_h = int(h * TITLE_BAND_FRAC)
    product_rect = _product_forbid_rect(w, h)
    logo_rect = _logo_forbid_rect(logo_placement, w, h)
    logo_corner = (logo_placement or {}).get("best_corner")

    type_mood = resolve_type_mood(type_mood)
    recipe = _mood_recipe(type_mood)
    display_file, body_file = _resolve_font_files(type_mood)

    # Apply mood casing before measure/draw
    render_headline = headline.upper() if recipe["uppercase"] else headline
    render_subhead = subhead  # keep product/occasion casing on subhead

    # start font size ~4.5% of width for headline, scaled by mood
    base_size = max(18, int(w * 0.045 * recipe["size_scale"]))
    sub_size = max(14, int(base_size * 0.55))

    draw = ImageDraw.Draw(img)  # for measuring only
    anchors = ["top_left", "top_center", "top_right"]
    # prefer opposite of logo when logo is top
    if logo_corner == "top_right":
        anchors = ["top_left", "top_center", "top_right"]
    elif logo_corner == "top_left":
        anchors = ["top_right", "top_center", "top_left"]

    use_subhead = bool(render_subhead)
    font_scale = 1.0
    chosen = None
    fallbacks_used: list[str] = []

    for attempt in range(6):
        fs = max(14, int(base_size * font_scale))
        ss = max(12, int(sub_size * font_scale))
        font_h = _load_font(display_file, fs)
        font_s = _load_font(body_file, ss)
        gap = max(4, int(fs * 0.2))

        block_w, block_h, lines = _measure_text_block(
            draw,
            render_headline,
            render_subhead if use_subhead else "",
            font_h,
            font_s,
            gap,
        )

        # title must fit in band height
        if block_h > band_h - padding:
            font_scale *= 0.85
            fallbacks_used.append("shrink_for_band_height")
            continue

        # max width ~70% of image
        max_title_w = int(w * 0.70)
        if block_w > max_title_w:
            font_scale *= 0.85
            fallbacks_used.append("shrink_for_width")
            continue

        candidates = []
        for anchor in anchors:
            x, y = _anchor_xy(anchor, block_w, block_h, w, h, padding)
            title_rect = (x, y, x + block_w, y + block_h)

            # must stay inside title band
            if title_rect[3] > band_h + padding // 2:
                continue

            hits_product = _rects_intersect(title_rect, product_rect, pad=2)
            hits_logo = bool(logo_rect and _rects_intersect(title_rect, logo_rect, pad=2))
            if hits_product or hits_logo:
                logger.debug(
                    "Reject %s: product=%s logo=%s rect=%s",
                    anchor, hits_product, hits_logo, title_rect,
                )
                continue

            metrics = _score_region(img, title_rect)
            score = metrics["clutter"] + _logo_side_penalty(anchor, logo_corner)
            # slight preference for top_left when scores close
            if anchor == "top_left":
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
                "block_w": block_w,
                "block_h": block_h,
                "use_subhead": use_subhead,
            })

        if candidates:
            candidates.sort(key=lambda c: c["score"])
            chosen = candidates[0]
            break

        # fallback ladder
        if use_subhead:
            use_subhead = False
            fallbacks_used.append("drop_subhead")
            continue
        font_scale *= 0.85
        fallbacks_used.append("shrink_retry")

    if not chosen:
        # last resort: force top_left smallest text, ignore product if only logo conflict
        logger.warning("No clean title candidate; forcing top_left minimal")
        fallbacks_used.append("force_top_left")
        fs = max(12, int(base_size * 0.55))
        ss = max(10, int(sub_size * 0.55))
        font_h = _load_font(display_file, fs)
        font_s = _load_font(body_file, ss)
        block_w, block_h, _ = _measure_text_block(draw, render_headline, "", font_h, font_s, 4)
        x, y = _anchor_xy("top_left", block_w, block_h, w, h, padding)
        # if still hits logo, shift to opposite
        title_rect = (x, y, x + block_w, y + block_h)
        if logo_rect and _rects_intersect(title_rect, logo_rect):
            x, y = _anchor_xy("top_right", block_w, block_h, w, h, padding)
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
            "block_w": block_w,
            "block_h": block_h,
            "use_subhead": False,
        }

    brightness = chosen["brightness"]
    # dark text on bright bg, light text on dark bg
    if brightness >= 140:
        text_rgb = (20, 20, 20)
        shadow_rgb = (255, 255, 255)
        scrim_dark = False
    else:
        text_rgb = (255, 255, 255)
        shadow_rgb = (0, 0, 0)
        scrim_dark = True

    accent = _hex_to_rgb(primary_color, default=(255, 255, 255) if scrim_dark else (30, 30, 30))

    placement = {
        "headline": render_headline,
        "headline_source": headline,
        "subhead": render_subhead if chosen["use_subhead"] else "",
        "type_mood": type_mood,
        "uppercase": recipe["uppercase"],
        "anchor": chosen["anchor"],
        "x": chosen["x"],
        "y": chosen["y"],
        "title_rect": list(chosen["title_rect"]),
        "block_w": chosen["block_w"],
        "block_h": chosen["block_h"],
        "font_size": chosen["font_size"],
        "sub_font_size": chosen["sub_font_size"],
        "display_font": display_file,
        "body_font": body_file,
        "text_color": list(text_rgb),
        "shadow_color": list(shadow_rgb),
        "scrim_dark": scrim_dark,
        "accent_color": list(accent),
        "brightness": brightness,
        "clutter": chosen["clutter"],
        "product_rect": list(product_rect),
        "logo_rect": list(logo_rect) if logo_rect else None,
        "fallbacks": fallbacks_used,
        "image_width": w,
        "image_height": h,
        "band_height": band_h,
    }
    logger.info(
        "Title placement: anchor=%s size=%d brightness=%.1f clutter=%.1f fallbacks=%s",
        placement["anchor"],
        placement["font_size"],
        brightness,
        chosen["clutter"],
        fallbacks_used,
    )
    return placement


def composite_title(
    base_image_path: str,
    title_placement: dict,
    session_id: str,
    output_suffix: str = "final",
) -> str:
    """
    Draw title (scrim + text + accent) onto base image. Saves as {session_id}_{suffix}.png.
    """
    logger.info("Compositing title onto %s session=%s", base_image_path, session_id)
    base = Image.open(base_image_path).convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    band_h = title_placement.get("band_height") or int(h * TITLE_BAND_FRAC)
    scrim_dark = title_placement.get("scrim_dark", True)

    # soft top scrim for readability
    scrim = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for row in range(band_h):
        t = 1.0 - (row / max(1, band_h - 1))
        alpha = int(110 * (t ** 1.2))
        if scrim_dark:
            color = (0, 0, 0, alpha)
        else:
            color = (255, 255, 255, int(alpha * 0.85))
        scrim_draw.line([(0, row), (w, row)], fill=color)
    overlay.paste(scrim, (0, 0), scrim)

    font_h = _load_font(title_placement["display_font"], title_placement["font_size"])
    font_s = _load_font(title_placement["body_font"], title_placement["sub_font_size"])
    headline = title_placement.get("headline") or ""
    subhead = title_placement.get("subhead") or ""
    gap = max(4, int(title_placement["font_size"] * 0.2))
    x = int(title_placement["x"])
    y = int(title_placement["y"])
    text_color = tuple(title_placement.get("text_color") or (255, 255, 255)) + (255,)
    shadow_color = tuple(title_placement.get("shadow_color") or (0, 0, 0)) + (140,)
    accent = tuple(title_placement.get("accent_color") or (255, 255, 255)) + (220,)

    # soft text shadow for contrast
    shadow_offset = max(1, title_placement["font_size"] // 18)

    def _line_height(text: str, font: ImageFont.ImageFont) -> int:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[3] - bb[1]

    def _draw_line(text: str, font: ImageFont.ImageFont, ly: int):
        draw.text((x + shadow_offset, ly + shadow_offset), text, font=font, fill=shadow_color)
        draw.text((x, ly), text, font=font, fill=text_color)

    cy = y
    if headline:
        _draw_line(headline, font_h, cy)
        cy += _line_height(headline, font_h) + (gap if subhead else 0)

    if subhead:
        _draw_line(subhead, font_s, cy)
        cy += _line_height(subhead, font_s)

    # thin accent underline under the text block
    block_w = int(title_placement.get("block_w") or 0)
    underline_y = cy + max(4, title_placement["font_size"] // 10)
    underline_h = max(2, title_placement["font_size"] // 14)
    underline_w = max(int(block_w * 0.35), min(block_w, int(w * 0.12)))
    draw.rectangle(
        [x, underline_y, x + underline_w, underline_y + underline_h],
        fill=accent,
    )

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
    logo_placement: dict | None = None,
    primary_color: str | None = None,
    brand_voice: list | None = None,
) -> tuple[str, dict]:
    """
    Plan + composite title in one call.
    Returns (final_path, title_placement_dict).
    """
    mood = resolve_type_mood(type_mood, brand_voice=brand_voice)
    placement = plan_title_placement(
        image_path=base_image_path,
        headline=headline,
        subhead=subhead,
        type_mood=mood,
        logo_placement=logo_placement,
        primary_color=primary_color,
    )
    path = composite_title(base_image_path, placement, session_id)
    return path, placement

