import logging
import difflib
import json
import re
from openai import OpenAI
from app.core.config import settings

logger = logging.getLogger("app.services.campaign")

client = OpenAI(api_key=settings.openai_api_key)


def generate_image_ideas(
    company: dict,
    theme: dict,
    product: dict
) -> list[str]:
    """
    generate 3 minimalist image ideas for the selected theme + product.
    each idea describes background environment and scene only (not product).
    ideas vary in intensity: 2 equally minimal, 1 very minimal.
    all ideas incorporate theme ambience and reserve space for logo.
    returns list of 3 one-liner strings.
    """
    logger.info("Generating image ideas for theme: %s, product: %s", theme.get("theme_name"), product.get("name"))
    brand_voice = ', '.join(company.get('brand_voice', []))
    ambience = theme.get('ambience', '')
    
    prompt = f"""You are an Instagram product photographer and creative director specializing in minimalist product photography.

Company: {company['name']}
Brand voice: {brand_voice}
Campaign theme: {theme['theme_name']}
Campaign concept: {theme['concept']}
Campaign angle: {theme['campaign_angle']}
Mood: {theme['mood']}
Scene ambience: {ambience}
Product: {product['name']} — {product['description']}

Task: Generate exactly 3 minimalist background scene ideas for this product photo.

Constraints for EXTREME MINIMALISM:
- Each idea must describe ONLY background/environment/lighting — NEVER the product.
- The product will occupy the left 60% of the frame. Reserve the right 30% as negative space (will contain company logo).
- Maximum 1-2 subtle supporting props per idea (if any). Examples: single wrapped gift, small decorative plant, folded fabric, single candle.
- NO large objects, busy textures, high-contrast elements, or competing visual focal points.
- Safe elements: subtle color shifts, directional lighting, depth-of-field blur, minimal props, atmospheric effects.
- Support the campaign theme and ambience WITHOUT overwhelming the scene.

Intensity variation (important):
- Ideas 1 & 2: Equally minimal, distinct from each other. Each leans into different aspects of the ambience.
- Idea 3: VERY minimal. Almost monochromatic or single-element. Most subdued background of the three.

Examples of minimalist scene ideas for reference:
- Theme: Christmas luxury → Idea 1: "Soft warm gold light on white marble surface, single small wrapped gift in background blur."
- Theme: Christmas luxury → Idea 2: "Cool daylight, subtle white snowflake bokeh on soft grey backdrop, neutral tone."
- Theme: Christmas luxury → Idea 3: "Pale cream background, no elements, soft directional window light casting gentle shadow."
- Theme: Summer pool party → Idea 1: "Dappled sunlight on white surface with faint turquoise water reflection, no props."
- Theme: Summer pool party → Idea 2: "Soft blue bokeh background with single lime slice edge, natural daylight."
- Theme: Summer pool party → Idea 3: "Pale beige background, single light ray, minimal depth."

Each idea must be:
- ONE sentence, vivid but minimal.
- Grounded in the ambience: "{ambience}"
- Compositionally aware: product left, logo space right.
- Thematically relevant to "{theme['theme_name']}" without visual overload.

Return ONLY valid JSON:
{{
  "ideas": [
    "idea one sentence — equally minimal, distinct variation",
    "idea two sentence — equally minimal, different from idea one",
    "idea three sentence — VERY minimal, most subdued"
  ]
}}"""

    import time
    t0 = time.monotonic()
    logger.debug("Calling OpenAI API (model=gpt-4.1-mini) for image ideas...")
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        max_tokens=800,
        temperature=0.75,
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed = time.monotonic() - t0
    text = _extract_text(response)
    logger.debug("OpenAI response received in %.2fs. Content: %s", elapsed, text)
    data = _parse_json(text)
    ideas = data["ideas"]
    logger.info("Successfully generated %d image ideas", len(ideas))
    return ideas




def generate_themes(company: dict, topic: str) -> list[dict]:
    """
    generate 5 campaign themes for the given topic.
    each theme is text-based: name, concept, angle, product match, mood, and ambience.
    themes explore different emotional/contextual interpretations of the topic.
    ambience is lightweight (1-2 sentences) describing the overall feeling/atmosphere.
    """
    logger.info("Generating campaign themes for company: %s, topic: '%s'", company.get("name"), topic)
    products_summary = "\n".join([
        f"- {p['name']}: {p['description']}"
        for p in company.get("products", [])
    ])

    brand_voice_str = ', '.join(company.get('brand_voice', []))
    
    prompt = f"""You are a creative campaign strategist designing Instagram product campaigns.

Company Context:
- Name: {company['name']}
- Industry: {company['industry']}
- Brand voice: {brand_voice_str}

Available Products:
{products_summary}

Campaign Topic: "{topic}"

Task: Generate exactly 5 campaign themes. Each theme explores a different emotional or contextual interpretation of "{topic}". Do NOT make all themes literal interpretations—instead, vary the angles: celebration vs. relaxation, luxury vs. casual, social vs. intimate, seasonal vs. timeless, etc.

For each theme:
1. Select the SINGLE best-fitting product (exact name match).
2. Create a punchy theme name (3-5 words).
3. Write a one-liner concept connecting the product to "{topic}".
4. Define a unique campaign angle that differentiates this theme from the others.
5. Set a single-word mood guiding the overall tone.
6. Write a lightweight ambience description (1-2 sentences max) that sets the scene/feeling without specifying visual details.
   - Ambience describes atmosphere, lighting mood, emotional tone, occasion context.
   - Examples: "Intimate evening escape with warm, golden lighting and a sense of luxury and calm." or "Energetic daytime celebration with bright natural light, playful social energy, and vibrant atmosphere."

Constraints:
- Each theme must be DISTINCT (different angles, moods, and emotional contexts).
- Ambience should be evocative but NOT prescriptive (no specific props, colors, or compositions yet).
- True to brand voice.

Return ONLY valid JSON:
{{
  "themes": [
    {{
      "number": 1,
      "theme_name": "punchy name",
      "concept": "one-liner connecting product to topic",
      "campaign_angle": "unique angle (e.g., luxury, nostalgia, celebration, wellness, adventure)",
      "best_product_name": "exact product name from list",
      "mood": "single word (energetic, luxurious, playful, intimate, bold, serene, adventurous, cozy)",
      "ambience": "1-2 sentence scene atmosphere description"
    }}
  ]
}}"""
    import time
    t0 = time.monotonic()
    logger.debug("Calling OpenAI API (model=gpt-5-mini) for theme generation...")
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed = time.monotonic() - t0
    text = _extract_text(response)
    logger.debug("OpenAI response received in %.2fs. Content: %s", elapsed, text)
    data = _parse_json(text)
    themes = data["themes"]
    logger.info("Successfully generated %d themes", len(themes))
    return themes


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
    logger.debug("Entering build_flux_prompt(company=%s, theme=%s, product=%s)", 
                 company.get("name"), theme.get("theme_name"), product.get("name"))
    brand_voice = ', '.join(company.get('brand_voice', []))
    primary_color = company.get('primary_color', '')
    secondary_color = company.get('secondary_color', '')

    color_hint = ""
    if primary_color:
        color_hint = f"Color palette inspired by {primary_color}"
        if secondary_color:
            color_hint += f" and {secondary_color}"
        color_hint += "."
        logger.debug("Using color hint: %s", color_hint)

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

    res = prompt.strip()
    logger.debug("Generated Flux prompt (length=%d chars)", len(res))
    return res


def select_best_product(products: list[dict], theme: dict) -> dict | None:
    """
    find the product whose name matches theme['best_product_name'].
    falls back to first product if no match.
    """
    target = theme.get("best_product_name", "").strip()
    logger.debug("Entering select_best_product(target='%s')", target)
    if not products:
        logger.warning("No products available to select from")
        return None

    product_names = [p["name"] for p in products]
    logger.debug("Available products list: %s", product_names)

    # 1. Try exact match (case-insensitive)
    for p in products:
        if p["name"].lower().strip() == target.lower():
            logger.debug("Exact match found: %s", p["name"])
            return p

    # 2. Try fast fuzzy matching using standard library difflib
    names_map = {p["name"].lower().strip(): p for p in products}
    matches = difflib.get_close_matches(target.lower(), names_map.keys(), n=1, cutoff=0.6)
    if matches:
        matched_product = names_map[matches[0]]
        logger.debug("Fuzzy match found via difflib: %s (for target '%s')", matched_product["name"], target)
        return matched_product

    # 3. Fallback to partial substring match
    for p in products:
        if target.lower() in p["name"].lower() or p["name"].lower() in target.lower():
            logger.debug("Substring match found: %s (for target '%s')", p["name"], target)
            return p

    # 4. Last resort — first product with a master image
    for p in products:
        if p.get("master_image_path"):
            logger.debug("Last resort fallback: found product with master image: %s", p["name"])
            return p
    
    logger.debug("Fallback to first available product: %s", products[0]["name"])
    return products[0]



# -- helpers --

def _extract_text(response) -> str:
    return response.choices[0].message.content or ""


def _parse_json(text: str) -> dict:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in AI response: {text[:200]}")
    return json.loads(match.group())
