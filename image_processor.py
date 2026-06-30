import io
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from app.core.config import settings


# -- image analyzer --

def analyze_image_for_logo_placement(image_path: str) -> dict:
    """
    read the generated flux image.
    sample brightness in all 4 corners.
    pick the corner with highest contrast opportunity based on logo brightness.
    
    assumes logo is dark (brightness strategy: place logo in brightest corner).
    returns placement metadata dict.
    """
    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    # sample 15% of image dimensions for each corner
    sample_w = int(w * 0.15)
    sample_h = int(h * 0.15)

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

    # logo is assumed dark — place in brightest corner for maximum contrast
    best_corner = max(brightness_map, key=brightness_map.get)

    # compute pixel coordinates for logo placement
    logo_w = int(w * 0.12)       # logo = 12% of image width
    padding = int(w * 0.03)      # 3% padding from edges

    # aspect ratio will be applied at composite time when we have the actual logo
    placement_map = {
        "top_left":     (padding,          padding),
        "top_right":    (w - logo_w - padding, padding),
        "bottom_left":  (padding,          None),   # y computed at composite time
        "bottom_right": (w - logo_w - padding, None),
    }

    x, y = placement_map[best_corner]

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
    base = Image.open(base_image_path).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")

    w, h = base.size
    logo_target_w = placement["logo_target_width"]
    padding       = placement["padding"]

    # resize logo proportionally
    logo_aspect = logo.height / logo.width
    logo_target_h = int(logo_target_w * logo_aspect)
    logo_resized  = logo.resize((logo_target_w, logo_target_h), Image.LANCZOS)

    # resolve y for bottom positions
    x = placement["x"]
    y = placement["y"]
    if y is None:
        y = h - logo_target_h - padding

    # add subtle drop shadow behind logo for visibility on any background
    shadow = _make_shadow(logo_resized, blur_radius=4, opacity=80)
    shadow_offset = 3
    base.paste(shadow, (x + shadow_offset, y + shadow_offset), shadow)

    # paste logo with its own alpha
    base.paste(logo_resized, (x, y), logo_resized)

    # save final as png
    output_dir = settings.get_image_dir() / "generated"
    output_dir.mkdir(exist_ok=True)

    final_path = output_dir / f"{session_id}_final.png"
    base.convert("RGB").save(str(final_path), "PNG", optimize=True)

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
    try:
        from colorthief import ColorThief
        ct = ColorThief(image_path)
        palette = ct.get_palette(color_count=count, quality=1)
        return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
    except Exception:
        return []
