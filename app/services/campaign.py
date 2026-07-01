import json
import re
from openai import OpenAI
from app.core.config import settings

client = OpenAI(api_key=settings.openai_api_key)


def generate_themes(company: dict, topic: str) -> list[dict]:
    """
    generate 5 campaign themes for the given topic.
    auto-selects best product per theme based on fit.
    returns list of theme dicts.
    """
    products_summary = "\n".join([
        f"- {p['name']}: {p['description']}"
        for p in company.get("products", [])
    ])

    prompt = f"""You are a creative campaign strategist for Instagram marketing.

Company: {company['name']}
Industry: {company['industry']}
Brand voice: {', '.join(company.get('brand_voice', []))}

Products:
{products_summary}

Campaign topic: {topic}

Generate exactly 5 distinct campaign themes.
For each theme, select the single best-fitting product from the list above.

Return ONLY valid JSON:
{{
  "themes": [
    {{
      "number": 1,
      "theme_name": "punchy 3-5 word name",
      "concept": "one-liner description",
      "campaign_angle": "one-liner angle",
      "best_product_name": "exact product name from list above",
      "mood": "one word mood e.g. energetic / luxurious / playful"
    }}
  ]
}}"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    text = _extract_text(response)
    data = _parse_json(text)
    return data["themes"]


def generate_image_ideas(
    company: dict,
    theme: dict,
    product: dict
) -> list[str]:
    """
    generate 3 image ideas for the selected theme + product.
    returns list of 3 one-liner strings.
    """
    prompt = f"""You are an Instagram creative director.

Company: {company['name']}
Brand voice: {', '.join(company.get('brand_voice', []))}
Campaign theme: {theme['theme_name']}
Campaign concept: {theme['concept']}
Campaign angle: {theme['campaign_angle']}
Mood: {theme['mood']}
Product: {product['name']} — {product['description']}

Generate exactly 3 distinct visual scene ideas for an Instagram product photo.
Each idea should only describe the background environment and scene — NOT the product itself.
The product stays unchanged. Only the setting changes.
Keep each idea to one sentence, vivid and specific.

Return ONLY valid JSON:
{{
  "ideas": [
    "idea one sentence",
    "idea two sentence",
    "idea three sentence"
  ]
}}"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    text = _extract_text(response)
    data = _parse_json(text)
    return data["ideas"]


def build_flux_prompt(
    company: dict,
    theme: dict,
    product: dict,
    idea: str
) -> str:
    """
    build a surgical Flux Kontext prompt for image-to-image editing.
    locks product, changes only environment/background/lighting.
    """
    brand_voice = ', '.join(company.get('brand_voice', []))
    primary_color = company.get('primary_color', '')
    secondary_color = company.get('secondary_color', '')

    color_hint = ""
    if primary_color:
        color_hint = f"Color palette inspired by {primary_color}"
        if secondary_color:
            color_hint += f" and {secondary_color}"
        color_hint += "."

    prompt = f"""Edit only the background and environment of this product photograph.

PRESERVE EXACTLY — do not change these under any circumstances:
- The product packaging, shape, geometry
- All label text, logo, branding on the product
- Product colors and material finish
- Product placement and scale in the frame

CHANGE ONLY:
- Background environment: {idea}
- Lighting mood: {theme['mood']}
- Atmospheric elements that support the scene

STYLE:
- Brand voice: {brand_voice}
- Campaign angle: {theme['campaign_angle']}
- {color_hint}
- Premium, minimalist, high-end Instagram product photography
- The product is the hero. Everything else serves the product.
- No text overlays. No watermarks. No logos added."""

    return prompt.strip()


def select_best_product(products: list[dict], theme: dict) -> dict | None:
    """
    find the product whose name matches theme['best_product_name'].
    falls back to first product if no match.
    """
    target = theme.get("best_product_name", "").lower().strip()
    for p in products:
        if p["name"].lower().strip() == target:
            return p
    # fuzzy fallback — partial match
    for p in products:
        if target in p["name"].lower() or p["name"].lower() in target:
            return p
    # last resort — first product with a master image
    for p in products:
        if p.get("master_image_path"):
            return p
    return products[0] if products else None


# -- helpers --

def _extract_text(response) -> str:
    return response.choices[0].message.content or ""


def _parse_json(text: str) -> dict:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in AI response: {text[:200]}")
    return json.loads(match.group())
