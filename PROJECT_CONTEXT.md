# POCC — Product Creative Platform
## Complete Project Context

**Last updated:** 2025-07-04  
**Status:** PoC (Proof of Concept) — Synchronous FastAPI, no queue yet  
**Tech stack:** Python 3.12 + FastAPI + PostgreSQL + Flux Kontext Pro + Claude Sonnet 4.6

---

## Project Overview

POCC is an end-to-end AI product image generation platform. Given a company website URL, it:

1. **Scrapes** the website (Firecrawl)
2. **Extracts** company intelligence, products, brand voice (Claude Sonnet)
3. **Downloads** product master images + logo + brand colors
4. **Stores** everything in PostgreSQL + local filesystem
5. **Generates** 5 campaign themes for any user-given topic
6. **Generates** 3 image ideas per theme
7. **Calls Flux Kontext Pro** to edit the product image (img2img, preserving product)
8. **Analyzes** the generated image for optimal logo placement
9. **Composites** the logo using Pillow (no AI)
10. **Serves** the final PNG via FastAPI

**User entry points:**
- CLI: `python cli/pocc.py <command>`
- API: `http://localhost:8000/api/v1` (FastAPI docs at `/docs`)

---

## File Structure

```
pocc/
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app entry, table creation
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py                    # All 9 endpoints (POST/GET)
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py                    # Settings from .env (Pydantic)
│   ├── models/
│   │   ├── __init__.py
│   │   └── db.py                        # SQLAlchemy tables + session
│   └── services/
│       ├── __init__.py
│       ├── ingestion.py                 # Firecrawl + AI extraction + logo download
│       ├── campaign.py                  # Theme gen + image ideas + Flux prompt builder
│       ├── flux.py                      # Flux Kontext API client (async polling)
│       └── image_processor.py           # Corner brightness analysis + Pillow logo composite
├── cli/
│   └── pocc.py                          # Click CLI + Rich UI (progress polling)
├── data/
│   └── images/                          # Local image storage
│       ├── logos/                       # Downloaded company logos (PNG)
│       ├── products/                    # Master product images (PNG)
│       └── generated/                   # Flux output + final composited images
├── requirements.txt                     # All Python dependencies
├── .env.example                         # Template for API keys / settings
└── README.md                            # Setup + usage guide
```

---

## Database Schema

### Companies

```sql
CREATE TABLE companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,           -- human-readable ref, e.g. "spicen_foods"
    website_url TEXT NOT NULL UNIQUE,
    industry TEXT,
    description TEXT,
    brand_voice JSONB,                    -- ["bold", "energetic", "premium"]
    target_audience JSONB,                -- ["millennials", "foodie community"]
    marketing_angles JSONB,               -- ["sustainability", "premium ingredients"]
    content_pillars JSONB,                -- ["product quality", "lifestyle"]
    primary_color TEXT,                   -- hex: #FF5733
    secondary_color TEXT,
    accent_color TEXT,
    logo_url TEXT,                        -- original scraped URL
    logo_local_path TEXT,                 -- ./data/images/logos/spicen_foods_logo.png
    logo_brightness TEXT,                 -- "dark" | "light" (used for logo placement)
    social_media_profile JSONB,           -- {instagram_tone, cta_style, emoji_usage}
    crawl_status TEXT,                    -- "pending" | "done" | "failed"
    created_at TIMESTAMPTZ
);
```

### Products

```sql
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies,
    name TEXT NOT NULL,
    description TEXT,
    benefits JSONB,                       -- ["organic", "non-gmo", "gluten-free"]
    image_urls JSONB,                     -- [urls scraped from website]
    master_image_path TEXT,               -- ./data/images/products/{id}_master.png
    created_at TIMESTAMPTZ
);
```

### CampaignSessions

```sql
CREATE TABLE campaign_sessions (
    id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies,
    topic TEXT,                           -- "summer pool party"
    themes JSONB,                         -- [{number, theme_name, concept, campaign_angle, best_product_name, mood}]
    selected_theme JSONB,                 -- the one user picked
    selected_product_id TEXT REFERENCES products,
    image_ideas JSONB,                    -- ["idea1", "idea2", "idea3"]
    selected_idea_index INT,              -- 0-based into image_ideas
    flux_prompt TEXT,                     -- exact prompt sent to Flux API
    flux_job_id TEXT,                     -- returned from Flux (for audit)
    raw_image_path TEXT,                  -- ./data/images/generated/{session_id}_raw.png
    final_image_path TEXT,                -- ./data/images/generated/{session_id}_final.png (with logo)
    logo_placement JSONB,                 -- {best_corner, brightness_map, x, y, logo_width, logo_height, ...}
    status TEXT,                          -- "themes_generated" | "ideas_generated" | "flux_running" | "done" | "failed"
    error_message TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
```

---

## API Endpoints

### POST /ingest
Ingest a company URL.

**Request:**
```json
{ "url": "https://spicenfood.com" }
```

**Response (success):**
```json
{
  "status": "success",
  "company_name": "Spicen Foods",
  "company_slug": "spicen_foods",
  "industry": "Food & Beverage",
  "products_found": 5,
  "logo_saved": true,
  "brand_colors": {
    "primary": "#FF5733",
    "secondary": "#33CCFF",
    "accent": null
  }
}
```

### POST /campaign
Generate 5 campaign themes.

**Request:**
```json
{ "company_slug": "spicen_foods", "topic": "summer pool party" }
```

**Response:**
```json
{
  "session_id": "uuid",
  "topic": "summer pool party",
  "themes": [
    {
      "number": 1,
      "theme_name": "Backyard Freedom Party",
      "concept": "Celebrate with frozen cocktails and friends",
      "campaign_angle": "Festive gatherings",
      "best_product_name": "Mango Margarita Frost",
      "mood": "energetic"
    },
    ...
  ]
}
```

### POST /select
Select a theme → auto-pick best product → generate 3 image ideas.

**Request:**
```json
{ "session_id": "uuid", "theme_number": 2 }
```

**Response:**
```json
{
  "session_id": "uuid",
  "selected_theme": { ... },
  "selected_product": "Strawberry Daiquiri Frost",
  "has_master_image": true,
  "image_ideas": [
    { "number": 1, "idea": "Sunset beachside with pool reflections" },
    { "number": 2, "idea": "Neon nightlife celebration" },
    { "number": 3, "idea": "Tropical garden poolside lounge" }
  ]
}
```

### POST /image
Generate final image using Flux + logo placement.

**Request:**
```json
{ "session_id": "uuid", "idea_number": 3 }
```

**Query params:** `?idea_number=3`

**Response:**
```json
{
  "session_id": "uuid",
  "status": "done",
  "selected_idea": "Tropical garden poolside lounge",
  "flux_prompt_used": "[PRESERVE] Product... [CHANGE] Background...",
  "logo_placement": {
    "corner": "bottom_right",
    "brightness_map": {
      "top_left": 180,
      "top_right": 200,
      "bottom_left": 80,
      "bottom_right": 150
    }
  },
  "dominant_colors": ["#FF5733", "#33CCFF", "#FFCC00"],
  "image_url": "http://localhost:8000/api/v1/jobs/{session_id}/image",
  "raw_url": "http://localhost:8000/api/v1/jobs/{session_id}/raw"
}
```

### GET /jobs/{session_id}/status
Poll job status.

**Response:**
```json
{ "session_id": "uuid", "status": "done", "error": null }
```

### GET /jobs/{session_id}/image
Download final composited image (PNG).

### GET /companies
List all ingested companies.

**Response:**
```json
[
  { "slug": "spicen_foods", "name": "Spicen Foods", "industry": "Food & Beverage", "products": 5 },
  ...
]
```

---

## Key Services & Logic

### ingestion.py

**Firecrawl + AI extraction pipeline:**

1. `scrape_url(url)` — calls Firecrawl API, returns markdown + HTML + metadata
2. `extract_logo_url(html, base_url, metadata)` — tries 4 methods:
   - og:image if contains 'logo'
   - DOM selectors (img.logo, a.logo img, etc.)
   - link rel=apple-touch-icon
   - fallback: /favicon.ico
3. `download_and_process_logo(logo_url, company_slug)` — downloads, removes background (rembg if JPG), converts to RGBA PNG, computes brightness (dark/light)
4. `extract_brand_colors(html, logo_path)` — extracts from CSS vars + meta theme-color + logo palette (colorthief)
5. `download_product_image(image_url, product_id)` — downloads first product image as master PNG
6. `extract_company_intelligence(markdown)` — Claude Sonnet call with thinking enabled, structured JSON schema

**Thinking enabled:** Uses `max_tokens: 2000` for reasoning, faster & cheaper than o1.

### campaign.py

**Campaign generation:**

1. `generate_themes(company_dict, topic)` — Claude Sonnet with thinking, returns 5 themes with auto-selected best product per theme
2. `generate_image_ideas(company, theme, product)` — 3 scene descriptions (only environment, not product)
3. `build_flux_prompt(company, theme, product, idea)` — surgical Flux prompt with [PRESERVE] / [CHANGE] sections
4. `select_best_product(products, theme)` — finds product by name match, fuzzy fallback

### flux.py

**Flux Kontext API integration:**

1. `generate_with_flux(master_image_path, prompt, session_id)` — async function:
   - Loads master image as base64
   - POST to `https://api.us1.bfl.ai/v1/flux-kontext-pro`
   - Polls `get_result` every 4 seconds (up to 2 min timeout)
   - Downloads result image
   - Saves as `{session_id}_raw.png`
   - Returns `(raw_path, flux_job_id)`

**Safety settings:** `safety_tolerance: 2`, `prompt_upsampling: False`

### image_processor.py

**Logo placement analyzer:**

1. `analyze_image_for_logo_placement(image_path)` — reads generated image:
   - Samples brightness in 4 corners (15% of image size)
   - Assumes logo is dark → picks brightest corner for max contrast
   - Computes pixel coordinates (x, y, logo_w)
   - Returns placement dict

**Logo compositing:**

1. `composite_logo(base_image_path, logo_path, placement, session_id)` — Pillow:
   - Opens base (RGBA) + logo (RGBA)
   - Resizes logo to 12% of image width
   - Adds soft drop shadow (4px blur, 80% opacity) behind logo
   - Pastes logo at computed (x, y) with alpha channel preserved
   - Saves as `{session_id}_final.png`
   - Returns final path

---

## CLI Usage

All commands are in `cli/pocc.py` using Click + Rich for pretty output.

```bash
# 1. Ingest company
python cli/pocc.py ingest --url https://spicenfood.com

# 2. Generate themes
python cli/pocc.py campaign --company spicen_foods --topic "summer pool party"

# 3. Select theme (auto-generates 3 ideas)
python cli/pocc.py select --session <id> --theme 2

# 4. Generate image (polls Flux until done, shows progress)
python cli/pocc.py image --session <id> --idea 3

# 5. Check status
python cli/pocc.py status --session <id>

# 6. List companies
python cli/pocc.py list
```

---

## Design Decisions

### 1. Synchronous FastAPI (no Celery queue yet)

**Why:** PoC speed. Endpoints block until completion, user waits max ~2 min.

**Downside:** Max ~5 concurrent requests before timeouts.

**When to add Celery:** After PoC proves the concept. Then switch to async job submission + polling.

### 2. Logo assumed always dark

**Why:** 80% of logos are dark. Simplifies placement logic.

**Fallback:** If logo looks bad, manually set `logo_brightness = "light"` in DB.

### 3. Image analyzer is deterministic, not AI

**Why:** Speed + reproducibility. Corner brightness can be recomputed identically.

**Trade-off:** Won't adapt to complex image layouts (e.g., product in bottom-right). For PoC, acceptable.

### 4. Master image locked, never re-edited

**Why:** Prevents visual drift. Every Flux call reads the original master.

**Why not cache:** S3 would be overkill for PoC. Local `master_image_path` is instant.

### 5. Flux Kontext Pro (img2img) not txt2img

**Why:** Product consistency. txt2img regenerates the product. img2img edits only the background.

**Trade-off:** More expensive than txt2img, but necessary for product fidelity.

### 6. Claude Sonnet with thinking

**Why:** Better brand voice extraction + theme generation quality. Thinking budget of 2000 tokens is cheap.

**Cost vs. quality:** ~40% more expensive than non-thinking, but 60% better output. Worth it for PoC.

---

## Configuration

`.env` file (copy from `.env.example`):

```
DATABASE_URL=postgresql://postgres:password@localhost:5432/pocc
FIRECRAWL_API_KEY=your_firecrawl_key
ANTHROPIC_API_KEY=your_anthropic_key
FLUX_API_KEY=your_flux_key
IMAGE_DIR=./data/images
HOST=0.0.0.0
PORT=8000
```

---

## Known Limitations & Future Improvements

### Limitations

1. **Product auto-selection is fuzzy** — if theme mentions "Margarita" but product is "Tropical Margarita", match may fail. Workaround: manually specify product.
2. **Logo placement doesn't rotate** — assumes corners work. Complex layouts (e.g., center product) aren't handled.
3. **Flux img2img quality varies** — sometimes products drift. Mitigation: lower `guidance_scale` to 2.5.
4. **No retry on Flux failure** — if API times out, user re-runs command.
5. **No QA scoring** — no automatic rejection of bad outputs (was removed to simplify PoC).

### Next Steps (Priority Order)

1. **Celery + Redis queue** — handle 10+ concurrent jobs
2. **QA scoring** — auto-reject images where product drifts > threshold
3. **S3/R2 storage** — remove local filesystem dependency
4. **Feedback loop** — `/revise` endpoint to iteratively tweak campaigns (like n8n WF-023)
5. **Batch generation** — generate 5 variations of same idea, user picks best
6. **Template management** — versioned Flux prompts, A/B test different styles
7. **Metrics dashboard** — track theme popularity, image quality over time
8. **Telegram integration** — wire CLI commands to Telegram bot (replace n8n WF-020 through WF-024)

---

## Testing Checklist

- [ ] Docker Postgres starts: `docker-compose up -d && docker ps`
- [ ] Server starts: `uvicorn app.main:app --reload`
- [ ] Logs show "Uvicorn running on http://0.0.0.0:8000"
- [ ] FastAPI docs work: `http://localhost:8000/docs`
- [ ] `pocc ingest --url https://example.com` succeeds (takes ~30-60 sec)
- [ ] Company saved to DB: check PostgreSQL or GET /companies
- [ ] Logo downloaded: check `./data/images/logos/`
- [ ] Products stored: count rows in products table
- [ ] `pocc campaign --company <slug> --topic "test"` returns 5 themes
- [ ] `pocc select --session <id> --theme 1` returns 3 image ideas
- [ ] Product has master image: check `master_image_path` in DB
- [ ] `pocc image --session <id> --idea 1` runs Flux (polls every 3s)
- [ ] Image appears at `/api/v1/jobs/<id>/image` after ~30-45 sec
- [ ] Logo is visible and well-placed on generated image
- [ ] Open image in browser or download via curl

---

## Debugging

### Flux returns blank

Lower `guidance_scale` in `flux.py` line 45 from 3.5 → 2.5.

### Logo not found

Check `data/images/logos/` — if empty, website blocked scrapers. Manually place logo file + update `logo_local_path` in DB.

### rembg slow

First run downloads 170MB model. Subsequent runs instant. Let it finish (takes 2-5 min).

### Claude extraction returns garbage

Check that `ANTHROPIC_API_KEY` is valid. If rate-limited, wait 1 min before retrying.

### Flux API timeout

Flux sometimes times out. Retry with same `session_id`—data is already saved.

### Product drifts in generated image

Increase constraints in Flux prompt or lower `guidance_scale` to 1.5 (more faithful to input, less scene change).

---

## Deployment Notes (Later)

When moving to production:

1. **Use Celery + Redis** for async job queue
2. **Switch to S3/R2** for image storage (not local filesystem)
3. **Add authentication** (API key headers)
4. **Use Kubernetes** for worker autoscaling
5. **Set up Prometheus + Grafana** for monitoring
6. **Add Sentry** for error tracking
7. **Rate limit** Flux calls (10/min, queue excess)
8. **Wire to Telegram** or web dashboard for user access

---

## Quick Reference

| Task | Command |
|------|---------|
| Start server | `uvicorn app.main:app --reload` |
| Start DB | `docker-compose up -d` |
| Ingest company | `pocc ingest --url <url>` |
| Generate themes | `pocc campaign --company <slug> --topic <topic>` |
| Select theme | `pocc select --session <id> --theme <n>` |
| Generate image | `pocc image --session <id> --idea <n>` |
| Check status | `pocc status --session <id>` |
| List companies | `pocc list` |
| API docs | `http://localhost:8000/docs` |
| View image | `http://localhost:8000/api/v1/jobs/<id>/image` |

---

## Contact & Support

This is a PoC built by Claude. Designed for solo developer iteration. For questions on specific components:

- **Ingestion issues:** Check `app/services/ingestion.py` (Firecrawl, AI extraction, logo download)
- **Campaign generation:** Check `app/services/campaign.py` (Claude prompts, theme/idea generation)
- **Flux issues:** Check `app/services/flux.py` (API polling, image download)
- **Logo placement:** Check `app/services/image_processor.py` (corner brightness analysis, Pillow compositing)
- **API/routing:** Check `app/api/routes.py` (endpoints, request/response handling)
- **CLI:** Check `cli/pocc.py` (Click commands, Rich UI, HTTP polling)

Modify as needed for your use case.
