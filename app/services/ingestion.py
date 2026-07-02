import logging
import httpx
import re
import io
import requests
from bs4 import BeautifulSoup
from PIL import Image
from colorthief import ColorThief
from openai import OpenAI
from app.core.config import settings
from pathlib import Path
import json
import uuid

logger = logging.getLogger("app.services.ingestion")


client = OpenAI(api_key=settings.openai_api_key)


# -- firecrawl --

async def scrape_url(url: str) -> dict:
    """scrape url using firecrawl, return markdown + html + metadata"""
    logger.debug("Entering scrape_url(url=%s)", url)
    import time
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                json={
                    "url": url,
                    "onlyMainContent": False,
                    "formats": ["markdown", "html"],
                    "maxAge": 172800000
                }
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            markdown = data.get("markdown", "")
            html = data.get("html", "")
            metadata = data.get("metadata", {})
            elapsed = time.monotonic() - t0
            logger.debug(
                "Exiting scrape_url: success (elapsed=%.2fs, markdown=%d chars, html=%d chars)",
                elapsed, len(markdown), len(html)
            )
            return {
                "markdown": markdown,
                "html":     html,
                "metadata": metadata
            }
    except Exception as e:
        logger.error("Error in scrape_url for %s: %s", url, str(e), exc_info=True)
        raise


# -- logo extraction (deterministic, no ai) --

LOGO_SELECTORS = [
    'img[class*="logo"]',
    'img[id*="logo"]',
    'a[class*="logo"] img',
    'a[href="/"] img',
    'header img:first-of-type',
    '[class*="brand"] img',
    '[itemprop="logo"] img',
]

def extract_logo_url(html: str, base_url: str, metadata: dict) -> str | None:
    """try selectors in order, return first found logo url"""
    logger.debug("Entering extract_logo_url(base_url=%s)", base_url)
    from urllib.parse import urljoin, urlparse

    def clean_url(u: str) -> str:
        return u.split("?")[0] if u else u

    # 1: og:image if it contains 'logo'
    og = metadata.get("og:image", "")
    if og and "logo" in og.lower():
        res = clean_url(og)
        logger.debug("Found logo via og:image: %s", res)
        return res

    # 2: dom selectors
    soup = BeautifulSoup(html, "html.parser")
    for sel in LOGO_SELECTORS:
        el = soup.select_one(sel)
        if el:
            src = el.get("src") or el.get("data-src")
            if src:
                res = clean_url(urljoin(base_url, src))
                logger.debug("Found logo via selector '%s': %s", sel, res)
                return res

    # 3: apple-touch-icon
    tag = soup.find("link", rel="apple-touch-icon")
    if tag and tag.get("href"):
        res = clean_url(urljoin(base_url, tag["href"]))
        logger.debug("Found logo via apple-touch-icon: %s", res)
        return res

    # 4: favicon
    parsed = urlparse(base_url)
    res = clean_url(f"{parsed.scheme}://{parsed.netloc}/favicon.ico")
    logger.debug("Logo fallback to favicon: %s", res)
    return res


def download_and_process_logo(logo_url: str, company_slug: str) -> tuple[str, str]:
    """
    download logo, remove background if jpg, save as png.
    returns (local_path, brightness: 'light'|'dark')
    """
    logger.debug("Entering download_and_process_logo(logo_url=%s, slug=%s)", logo_url, company_slug)
    image_dir = settings.get_image_dir() / "logos"
    image_dir.mkdir(exist_ok=True)

    # remove query parameters before downloading
    logo_url = logo_url.split("?")[0]

    logger.debug("Downloading logo: %s", logo_url)
    resp = requests.get(logo_url, timeout=15)
    resp.raise_for_status()
    logger.debug("Logo download complete. Content-Type: %s, size=%d bytes", resp.headers.get("Content-Type", ""), len(resp.content))

    # check if SVG
    is_svg = logo_url.lower().endswith(".svg") or "image/svg" in resp.headers.get("Content-Type", "")

    if is_svg:
        logger.debug("Processing logo as SVG")
        # Save the SVG as-is first to the filesystem
        svg_path = image_dir / f"{company_slug}_logo.svg"
        with open(svg_path, "wb") as f:
            f.write(resp.content)
        logger.debug("SVG saved to: %s", svg_path)

        # Convert SVG to PNG for Pillow operations
        png_data = None
        try:
            logger.debug("Attempting SVG to PNG conversion via PyMuPDF (fitz)")
            import fitz  # PyMuPDF
            doc = fitz.open(stream=resp.content, filetype="svg")
            page = doc[0]
            pix = page.get_pixmap(dpi=150)
            png_data = pix.tobytes("png")
            logger.debug("PyMuPDF SVG conversion successful")
        except Exception as e_fitz:
            logger.debug("PyMuPDF conversion failed: %s. Trying CairoSVG...", str(e_fitz))
            try:
                import cairosvg
                png_data = cairosvg.svg2png(bytestring=resp.content)
                logger.debug("CairoSVG conversion successful")
            except Exception as e_cairo:
                logger.error("All SVG to PNG conversion backends failed!")
                raise RuntimeError(
                    f"Failed to convert SVG to PNG. Tested PyMuPDF ({e_fitz}) and CairoSVG ({e_cairo})."
                )

        img = Image.open(io.BytesIO(png_data)).convert("RGBA")
    else:
        logger.debug("Processing logo as raster image")
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")

    # remove background if the image has no transparency (likely jpg)
    has_transparency = any(px[3] < 255 for px in img.getdata())
    logger.debug("Logo has transparency: %s", has_transparency)
    if not has_transparency:
        try:
            logger.debug("Attempting background removal via rembg...")
            from rembg import remove
            img = remove(img)
            logger.debug("rembg background removal successful")
        except Exception as e_rembg:
            logger.warning("rembg failed to remove background: %s. Keeping original.", str(e_rembg))
            pass  # if rembg fails, keep original

    # compute brightness on rgb version (ignoring transparent pixels)
    rgb = img.convert("RGB")
    arr = list(rgb.getdata())
    avg_brightness = sum(sum(px) / 3 for px in arr) / len(arr)
    brightness = "light" if avg_brightness > 128 else "dark"
    logger.debug("Computed average brightness score: %.2f (resolved as '%s')", avg_brightness, brightness)

    # save as png
    path = image_dir / f"{company_slug}_logo.png"
    img.save(str(path), "PNG")
    logger.debug("Processed logo saved to: %s", path)

    return str(path), brightness



# -- brand color extraction --

def extract_brand_colors(html: str, logo_path: str | None) -> dict:
    """extract primary/secondary/accent from css vars and logo"""
    logger.debug("Entering extract_brand_colors(logo_path=%s)", logo_path)
    colors = {"primary": None, "secondary": None, "accent": None}

    # css custom properties
    pattern = re.compile(
        r'--(primary|brand|accent|main|secondary)[^:]*:\s*(#[0-9a-fA-F]{3,8})',
        re.IGNORECASE
    )
    matches = pattern.findall(html)
    logger.debug("Regex custom properties matches: %s", matches)
    if matches:
        colors["primary"] = matches[0][1]
    if len(matches) > 1:
        colors["secondary"] = matches[1][1]

    # meta theme-color
    soup = BeautifulSoup(html, "html.parser")
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and not colors["primary"]:
        colors["primary"] = theme.get("content")
        logger.debug("Found primary color via meta theme-color: %s", colors["primary"])

    # fallback: colorthief on logo
    if logo_path and (not colors["primary"] or not colors["secondary"]):
        try:
            logger.debug("Applying ColorThief on logo: %s", logo_path)
            ct = ColorThief(logo_path)
            palette = ct.get_palette(color_count=3, quality=1)
            hex_p = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
            logger.debug("ColorThief palette: %s", hex_p)
            if not colors["primary"]:
                colors["primary"] = hex_p[0]
            if not colors["secondary"] and len(hex_p) > 1:
                colors["secondary"] = hex_p[1]
            if not colors["accent"] and len(hex_p) > 2:
                colors["accent"] = hex_p[2]
        except Exception as e_ct:
            logger.debug("ColorThief extraction failed: %s", str(e_ct))
            pass

    logger.debug("Exiting extract_brand_colors: %s", colors)
    return colors


# -- product image download --

def download_product_image(image_url: str, product_id: str) -> str | None:
    """download product master image, save as png locally"""
    logger.debug("Entering download_product_image(url=%s, product_id=%s)", image_url, product_id)
    image_dir = settings.get_image_dir() / "products"
    image_dir.mkdir(exist_ok=True)

    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        path = image_dir / f"{product_id}_master.png"
        img.save(str(path), "PNG")
        logger.debug("Product image downloaded and saved to: %s", path)
        return str(path)
    except Exception as e:
        logger.error("Failed to download product image from %s: %s", image_url, str(e), exc_info=True)
        return None


# -- ai extraction --

def extract_company_intelligence(markdown: str) -> dict:
    """
    use claude sonnet to extract structured company profile from scraped markdown.
    returns parsed dict matching our schema.
    """
    logger.debug("Entering extract_company_intelligence, markdown length=%d", len(markdown))
    prompt = f"""Analyze the following website content and extract structured business information.

Return ONLY a valid JSON object with these exact keys:
{{
  "company_name": "string",
  "industry": "string",
  "description": "string (2-3 sentences)",
  "brand_voice": ["string", ...],
  "target_audience": ["string", ...],
  "marketing_angles": ["string", ...],
  "content_pillars": ["string", ...],
  "products": [
    {{
      "name": "string",
      "description": "string",
      "benefits": ["string", ...],
      "image_urls": ["string", ...]
    }}
  ],
  "social_media_profile": {{
    "instagram_tone": "string",
    "cta_style": "string",
    "emoji_usage": "string"
  }}
}}

Rules:
- Do not invent products. Only extract what is clearly on the page.
- Extract real product image URLs when visible near product descriptions.
- Keep brand_voice as 3-5 descriptive words/phrases.
- Keep target_audience as 2-4 audience segments.
- Remove duplicate products.

Website content:
{markdown[:12000]}"""

    import time
    t0 = time.monotonic()
    logger.debug("Calling OpenAI API (model=gpt-5-mini)...")
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed = time.monotonic() - t0
    text = response.choices[0].message.content or ""
    logger.debug("OpenAI response received in %.2fs. Content length: %d chars", elapsed, len(text))

    # parse json from response
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        logger.error("Failed to parse JSON from AI response: %s", text)
        raise ValueError("AI extraction returned no valid JSON")

    parsed = json.loads(match.group())
    logger.debug("Successfully extracted company intelligence: name=%s, products_count=%d", 
                 parsed.get("company_name"), len(parsed.get("products", [])))
    return parsed


# -- main ingestion function --

async def ingest_company(url: str) -> dict:
    """
    full ingestion pipeline:
    scrape → extract intelligence → download logo + products → return structured data
    """
    logger.info("Starting ingestion pipeline for URL: %s", url)
    # normalize url
    if not url.startswith("http"):
        url = f"https://{url}"

    # 1. scrape
    logger.info("Step 1: Scraping website...")
    scraped = await scrape_url(url)

    # 2. ai extraction
    logger.info("Step 2: Extracting company intelligence via LLM...")
    intelligence = extract_company_intelligence(scraped["markdown"])

    # 3. generate slug from company name
    slug = re.sub(r'[^a-z0-9]+', '_', intelligence["company_name"].lower()).strip('_')
    logger.debug("Generated company slug: %s", slug)

    # 4. logo extraction + download
    logger.info("Step 3: Extracting and downloading logo...")
    logo_url = extract_logo_url(scraped["html"], url, scraped["metadata"])
    logo_path, logo_brightness = None, "dark"
    if logo_url:
        try:
            logo_path, logo_brightness = download_and_process_logo(logo_url, slug)
        except Exception as e:
            logger.warning("Logo download/processing failed: %s", str(e), exc_info=True)

    # 5. brand colors
    logger.info("Step 4: Extracting brand colors...")
    colors = extract_brand_colors(scraped["html"], logo_path)

    # 6. download product master images
    logger.info("Step 5: Downloading product master images...")
    products_with_images = []
    for idx, product in enumerate(intelligence.get("products", [])):
        product_id = str(uuid.uuid4())
        master_path = None
        image_urls = product.get("image_urls", [])
        if image_urls:
            logger.debug("Downloading image for product index %d: %s", idx, product["name"])
            master_path = download_product_image(image_urls[0], product_id)
        else:
            logger.debug("No image URLs listed for product: %s", product["name"])
        products_with_images.append({
            **product,
            "_id": product_id,
            "master_image_path": master_path
        })

    logger.info("Ingestion pipeline completed successfully for %s", url)
    return {
        "url": url,
        "slug": slug,
        "intelligence": intelligence,
        "logo_url": logo_url,
        "logo_local_path": logo_path,
        "logo_brightness": logo_brightness,
        "colors": colors,
        "products": products_with_images
    }

