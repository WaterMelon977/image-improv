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
    generate 3 image ideas for the selected theme + product.
    each idea describes background environment and scene only (not product).
    ideas vary in intensity: theme-rich → simplified → ultra-minimal.
    all ideas incorporate theme ambience and reserve space for logo.
    returns list of 3 one-liner strings.
    """
    logger.info("Generating image ideas for theme: %s, product: %s", theme.get("theme_name"), product.get("name"))
    brand_voice = ', '.join(company.get('brand_voice', []))
    ambience = theme.get('ambience', '')
    
    prompt = f"""You are an Instagram product photographer and creative director specializing in premium product photography for social media.

Company: {company['name']}
Brand voice: {brand_voice}
Campaign theme: {theme['theme_name']}
Campaign concept: {theme['concept']}
Campaign angle: {theme['campaign_angle']}
Mood: {theme['mood']}
Scene ambience: {ambience}
Product: {product['name']} — {product['description']}

Task: Generate exactly 3 background scene ideas for this product photo, each with a different intensity of theme presence.

Composition rule for all 3:
- Describe ONLY background/environment/lighting/props — NEVER the product itself.
- Product occupies the left 60% of frame, mid-to-lower in the frame (not pressed against the top edge).
- Reserve the upper ~15% of the frame as relatively open headroom (soft sky, wall, bokeh, or simple surface) for an on-image title — no tall props, hanging elements, or busy objects in that top band.
- Reserve right 30% as clean negative space for a logo.
- No object should overlap or compete with where the product sits.

Intensity variation (this is the core instruction):

- Idea 1 — THEME-RICH: Bring the campaign theme fully to life. Use 2-4 concrete elements that visually anchor "{theme['theme_name']}" (seasonal props, textures, colors, occasion cues). This should look like a fully styled Instagram hero shot — think real brand campaign photography, not a bare studio backdrop. Still no busy clutter — every element should be deliberate and photograph-worthy.
- Idea 2 — SIMPLIFIED: Take the same scene direction as Idea 1 but strip it down to 1 supporting element max. Keep the same lighting/mood/color language as Idea 1 so it reads as a quieter version of the same shoot.
- Idea 3 — ULTRA MINIMAL: Almost no elements. Near-monochromatic surface, single light source, no props. Most subdued of the three.

Each idea must be:
- ONE sentence, vivid, concrete (nameable textures, light quality, real materials — not abstract adjectives).
- Grounded in the ambience: "{ambience}"
- Realistic and photographable — describe things a real photographer could actually set up, not surreal or CGI-style concepts.

Return ONLY valid JSON:
{{
  "ideas": [
    "idea one — theme-rich, 2-4 concrete elements",
    "idea two — simplified version of idea one, 1 element max",
    "idea three — ultra minimal, near-monochrome, no elements"
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
    idea: str,
    user_tweak: str | None = None
) -> str:
    """
    calls an LLM to compress the scene idea into a short, surgical Flux 2 Pro
    image-editing prompt (under 70 words, 3-line structure).
    long descriptive prompts degrade Flux 2 Pro output — this keeps it minimal.
    if user_tweak is provided, the LLM incorporates it while still respecting constraints.
    """
    logger.debug("Entering build_flux_prompt(company=%s, theme=%s, product=%s, user_tweak=%s)",
                 company.get("name"), theme.get("theme_name"), product.get("name"), user_tweak)
    primary_color = company.get('primary_color', '')
    secondary_color = company.get('secondary_color', '')

    tweak_section = ""
    if user_tweak and user_tweak.strip():
        tweak_section = f"""
User override instruction (incorporate this into line 2, while keeping everything else the same):
"{user_tweak.strip()}"
"""

    meta_prompt = f"""You are a technical prompt engineer for Flux 2 Pro, an image-to-image model used for product photo editing.
NOT FLUX KONTEXT PRO
Your ONLY job: write a short, surgical image-editing instruction. Flux performs worse with long or descriptive prompts — it starts inventing extra objects, textures, or lighting artifacts when given too much input. Your prompt must be the shortest possible version that still gets the scene right.

Context (for your understanding only — do NOT restate brand voice, campaign concept, or marketing language in the output):
Selected scene idea: "{idea}"
Mood: {theme['mood']}
Primary color: {primary_color}
Secondary color: {secondary_color}
{tweak_section}
Task: Write a Flux editing prompt using this exact structure, and nothing else:

1. One line: what stays untouched (product, label, logo, geometry, scale — always the same wording, don't vary this).
2. One line: what changes (background/environment only) — compress the scene idea into its visual essentials. Drop any word that isn't a physical, photographable detail (no "energetic," "premium," "festive" — describe light, surface, and the 1-2 real objects only). Keep the upper frame relatively open/simple (soft empty headroom near the top edge) and place the product mid-to-lower — physical framing only, not marketing language.
3. One line: photographic realism directive — real phone/DSLR product photography, natural light behavior, authentic imperfections (soft shadow falloff, slight grain, true-to-life color), NOT airbrushed or synthetic-looking.

Hard constraints:
- Total output under 70 words.
- No brand adjectives, no marketing language, no campaign names, no text/typography instructions.
- No more than 2 physical props mentioned total.
- Every phrase must describe something a camera could literally capture.

Return ONLY valid JSON:
{{
  "flux_prompt": "the complete compressed prompt as one string, following the 3-line structure above"
}}"""

    import time
    t0 = time.monotonic()
    logger.debug("Calling OpenAI API (model=gpt-4.1-mini) for Flux prompt compression...")
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        max_tokens=300,
        temperature=0.3,
        messages=[{"role": "user", "content": meta_prompt}]
    )
    elapsed = time.monotonic() - t0
    text = _extract_text(response)
    logger.debug("OpenAI response received in %.2fs. Content: %s", elapsed, text)
    data = _parse_json(text)
    flux_prompt = data["flux_prompt"]
    logger.debug("Generated Flux prompt (length=%d chars): %s", len(flux_prompt), flux_prompt)
    return flux_prompt


def generate_image_title(
    company: dict,
    theme: dict,
    product: dict,
    idea: str,
) -> dict:
    """
    Generate a short on-image Instagram-style title pack for the chosen idea.
    Returns dict: headline, subhead (optional), type_mood.
    """
    logger.info(
        "Generating image title for theme=%s product=%s",
        theme.get("theme_name"),
        product.get("name"),
    )
    brand_voice = ", ".join(company.get("brand_voice") or [])
    social = company.get("social_media_profile") or {}
    instagram_tone = social.get("instagram_tone", "")
    cta_style = social.get("cta_style", "")

    prompt = f"""You are an Instagram creative director writing ON-IMAGE titles for premium product photos (text that will be overlaid on the photo, not the caption under the post).

Company: {company.get('name', '')}
Brand voice: {brand_voice}
Instagram tone: {instagram_tone}
CTA style: {cta_style}
Campaign theme: {theme.get('theme_name', '')}
Concept: {theme.get('concept', '')}
Mood: {theme.get('mood', '')}
Product: {product.get('name', '')} — {product.get('description', '')}
Scene idea: {idea}

Write one title pack:
- headline: 3–6 words max. Punchy Instagram hook that matches brand voice + mood. Not a full sentence. No hashtags, no emojis, no quotes.
- subhead: optional one short line (product name or occasion). Empty string if not needed. Max ~6 words.
- type_mood: exactly one of: minimal_clean | festive_bold | luxury_editorial | playful_soft | bold_street

Rules:
- No generic filler like "Premium Quality" or "Best Ever" unless the brand voice is literally that flat.
- Headline should feel native to the campaign, not a product-spec label.
- Prefer concrete, visual, or occasion-led language over corporate slogans.

Return ONLY valid JSON:
{{
  "headline": "short hook",
  "subhead": "optional or empty",
  "type_mood": "minimal_clean"
}}"""

    import time
    t0 = time.monotonic()
    logger.debug("Calling OpenAI API for image title...")
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        max_tokens=250,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.monotonic() - t0
    text = _extract_text(response)
    logger.debug("Title LLM response in %.2fs: %s", elapsed, text)
    data = _parse_json(text)

    allowed_moods = {
        "minimal_clean",
        "festive_bold",
        "luxury_editorial",
        "playful_soft",
        "bold_street",
    }
    type_mood = (data.get("type_mood") or "minimal_clean").strip().lower()
    if type_mood not in allowed_moods:
        type_mood = "minimal_clean"

    headline = (data.get("headline") or theme.get("theme_name") or "New Drop").strip()
    subhead = (data.get("subhead") or "").strip()

    # hard clamps for overlay safety
    headline_words = headline.split()
    if len(headline_words) > 8:
        headline = " ".join(headline_words[:6])
    if len(subhead.split()) > 8:
        subhead = " ".join(subhead.split()[:6])

    result = {
        "headline": headline,
        "subhead": subhead,
        "type_mood": type_mood,
    }
    logger.info("Image title generated: %s", result)
    return result


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
