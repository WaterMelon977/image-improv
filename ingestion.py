import httpx
import re
import io
import requests
from bs4 import BeautifulSoup
from PIL import Image
from colorthief import ColorThief
from anthropic import Anthropic
from app.core.config import settings
from pathlib import Path
import json
import uuid


client = Anthropic(api_key=settings.anthropic_api_key)


# -- firecrawl --

async def scrape_url(url: str) -> dict:
    """scrape url using firecrawl, return markdown + html + metadata"""
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
        return {
            "markdown": data.get("markdown", ""),
            "html":     data.get("html", ""),
            "metadata": data.get("metadata", {})
        }


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
    from urllib.parse import urljoin, urlparse

    # 1: og:image if it contains 'logo'
    og = metadata.get("og:image", "")
    if og and "logo" in og.lower():
        return og

    # 2: dom selectors
    soup = BeautifulSoup(html, "html.parser")
    for sel in LOGO_SELECTORS:
        el = soup.select_one(sel)
        if el:
            src = el.get("src") or el.get("data-src")
            if src:
                return urljoin(base_url, src)

    # 3: apple-touch-icon
    tag = soup.find("link", rel="apple-touch-icon")
    if tag and tag.get("href"):
        return urljoin(base_url, tag["href"])

    # 4: favicon
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def download_and_process_logo(logo_url: str, company_slug: str) -> tuple[str, str]:
    """
    download logo, remove background if jpg, save as png.
    returns (local_path, brightness: 'light'|'dark')
    """
    image_dir = settings.get_image_dir() / "logos"
    image_dir.mkdir(exist_ok=True)

    resp = requests.get(logo_url, timeout=15)
    resp.raise_for_status()

    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")

    # remove background if the image has no transparency (likely jpg)
    has_transparency = any(px[3] < 255 for px in img.getdata())
    if not has_transparency:
        try:
            from rembg import remove
            img = remove(img)
        except Exception:
            pass  # if rembg fails, keep original

    # compute brightness on rgb version (ignoring transparent pixels)
    rgb = img.convert("RGB")
    arr = list(rgb.getdata())
    avg_brightness = sum(sum(px) / 3 for px in arr) / len(arr)
    brightness = "light" if avg_brightness > 128 else "dark"

    # save as png
    path = image_dir / f"{company_slug}_logo.png"
    img.save(str(path), "PNG")

    return str(path), brightness


# -- brand color extraction --

def extract_brand_colors(html: str, logo_path: str | None) -> dict:
    """extract primary/secondary/accent from css vars and logo"""
    colors = {"primary": None, "secondary": None, "accent": None}

    # css custom properties
    pattern = re.compile(
        r'--(primary|brand|accent|main|secondary)[^:]*:\s*(#[0-9a-fA-F]{3,8})',
        re.IGNORECASE
    )
    matches = pattern.findall(html)
    if matches:
        colors["primary"] = matches[0][1]
    if len(matches) > 1:
        colors["secondary"] = matches[1][1]

    # meta theme-color
    soup = BeautifulSoup(html, "html.parser")
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and not colors["primary"]:
        colors["primary"] = theme.get("content")

    # fallback: colorthief on logo
    if logo_path and (not colors["primary"] or not colors["secondary"]):
        try:
            ct = ColorThief(logo_path)
            palette = ct.get_palette(color_count=3, quality=1)
            hex_p = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
            if not colors["primary"]:
                colors["primary"] = hex_p[0]
            if not colors["secondary"] and len(hex_p) > 1:
                colors["secondary"] = hex_p[1]
            if not colors["accent"] and len(hex_p) > 2:
                colors["accent"] = hex_p[2]
        except Exception:
            pass

    return colors


# -- product image download --

def download_product_image(image_url: str, product_id: str) -> str | None:
    """download product master image, save as png locally"""
    image_dir = settings.get_image_dir() / "products"
    image_dir.mkdir(exist_ok=True)

    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        path = image_dir / f"{product_id}_master.png"
        img.save(str(path), "PNG")
        return str(path)
    except Exception:
        return None


# -- ai extraction --

def extract_company_intelligence(markdown: str) -> dict:
    """
    use claude sonnet to extract structured company profile from scraped markdown.
    returns parsed dict matching our schema.
    """
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

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": 2000},
        messages=[{"role": "user", "content": prompt}]
    )

    # extract text from response (skip thinking blocks)
    text = ""
    for block in response.content:
        if block.type == "text":
            text = block.text
            break

    # parse json from response
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError("AI extraction returned no valid JSON")

    return json.loads(match.group())


# -- main ingestion function --

async def ingest_company(url: str) -> dict:
    """
    full ingestion pipeline:
    scrape → extract intelligence → download logo + products → return structured data
    """
    # normalize url
    if not url.startswith("http"):
        url = f"https://{url}"

    # 1. scrape
    scraped = await scrape_url(url)

    # 2. ai extraction
    intelligence = extract_company_intelligence(scraped["markdown"])

    # 3. generate slug from company name
    slug = re.sub(r'[^a-z0-9]+', '_', intelligence["company_name"].lower()).strip('_')

    # 4. logo extraction + download
    logo_url = extract_logo_url(scraped["html"], url, scraped["metadata"])
    logo_path, logo_brightness = None, "dark"
    if logo_url:
        try:
            logo_path, logo_brightness = download_and_process_logo(logo_url, slug)
        except Exception as e:
            print(f"[warn] logo download failed: {e}")

    # 5. brand colors
    colors = extract_brand_colors(scraped["html"], logo_path)

    # 6. download product master images
    products_with_images = []
    for product in intelligence.get("products", []):
        product_id = str(uuid.uuid4())
        master_path = None
        image_urls = product.get("image_urls", [])
        if image_urls:
            master_path = download_product_image(image_urls[0], product_id)
        products_with_images.append({
            **product,
            "_id": product_id,
            "master_image_path": master_path
        })

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
