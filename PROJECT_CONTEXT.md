# POCC — Product Creative Platform
## Complete Project Context

**Last updated:** 2026-07-13  
**Status:** PoC (Proof of Concept) — Synchronous FastAPI, no queue yet  
**Tech stack:** Python 3.12+ + FastAPI + PostgreSQL (Supabase) + Flux 2 Pro + OpenAI + Pillow

---

## Project Overview

POCC is an end-to-end AI product image generation platform. Given a company website URL, it:

1. **Scrapes** the website (Firecrawl)
2. **Extracts** company intelligence, products, brand voice (OpenAI)
3. **Downloads** product master images + logo + brand colors
4. **Stores** everything in PostgreSQL + local filesystem
5. **Generates** 5 campaign themes for any user-given topic
6. **Generates** 3 image ideas per theme (with top headroom for titles)
7. **Builds and previews** a compressed, surgical Flux editing prompt (LLM-based)
8. **Previews 3 on-image title options** (hook / product / minimal) — user picks one (or custom edit)
9. **Calls Flux 2 Pro** to edit the product image (img2img, preserving product)
10. **Analyzes** the generated image for optimal logo placement
11. **Composites** the logo using Pillow (no AI)
12. **Composites** the title in a fixed top band (measure text box; forbid product + logo regions; clutter scoring)
13. **Serves** the final PNG via FastAPI
14. **Optionally re-titles** the same raw image without re-running Flux (`/apply-title`, `pocc retitle`)

**User entry points:**
- CLI: `python cli/pocc.py <command>`
- API: `http://localhost:8000/api/v1` (docs at `/docs`)

---

## File Structure

```
image-improv/
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI entry; create_tables on startup
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py                    # All endpoints + _postprocess_image (logo + title)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                    # Settings from .env (Pydantic)
│   │   └── logging.py                   # Backend logging setup
│   ├── models/
│   │   ├── __init__.py
│   │   └── db.py                        # SQLAlchemy models + title_overlay migrate
│   └── services/
│       ├── __init__.py
│       ├── ingestion.py                 # Firecrawl + AI extraction + logo download
│       ├── campaign.py                  # Themes, ideas, flux prompt, generate_image_title
│       ├── flux.py                      # Flux 2 Pro API client (async polling)
│       └── image_processor.py           # Logo placement + title plan/composite
├── cli/
│   └── pocc.py                          # Click + Rich CLI
├── data/
│   ├── fonts/                           # Bundled TTFs for on-image titles
│   │   ├── OpenSans-Bold.ttf
│   │   ├── OpenSans-SemiBold.ttf
│   │   ├── Montserrat-Bold.ttf
│   │   ├── Montserrat.ttf               # optional variable
│   │   └── PlayfairDisplay.ttf          # luxury_editorial
│   └── images/
│       ├── logos/                       # Company logos (PNG/SVG)
│       ├── products/                    # Master product images
│       └── generated/                   # {session}_raw.png + {session}_final.png
├── requirements.txt
├── .env.example
├── README.md                            # Setup + usage
└── PROJECT_CONTEXT.md                   # This file
```

---

## Database Schema

### Companies

```sql
CREATE TABLE companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,           -- e.g. "spicen_foods"
    website_url TEXT NOT NULL UNIQUE,
    industry TEXT,
    description TEXT,
    brand_voice JSONB,                    -- ["bold", "energetic", "premium"]
    target_audience JSONB,
    marketing_angles JSONB,
    content_pillars JSONB,
    primary_color TEXT,                   -- hex — used as title accent underline
    secondary_color TEXT,
    accent_color TEXT,
    logo_url TEXT,
    logo_local_path TEXT,
    logo_brightness TEXT,                 -- "dark" | "light"
    social_media_profile JSONB,           -- {instagram_tone, cta_style, emoji_usage}
    crawl_status TEXT,
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
    benefits JSONB,
    image_urls JSONB,
    master_image_path TEXT,
    created_at TIMESTAMPTZ
);
```

### CampaignSessions

```sql
CREATE TABLE campaign_sessions (
    id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies,
    topic TEXT,
    themes JSONB,                         -- 5 theme objects
    selected_theme JSONB,
    selected_product_id TEXT REFERENCES products,
    image_ideas JSONB,                    -- 3 idea strings
    selected_idea_index INT,              -- 0-based
    flux_prompt TEXT,
    flux_job_id TEXT,
    raw_image_path TEXT,                  -- Flux output before logo/title
    final_image_path TEXT,                -- after logo + title
    logo_placement JSONB,                 -- corner, coords, brightness_map, logo size
    title_overlay JSONB,                  -- headline, subhead, type_mood, placement meta
    status TEXT,                          -- themes_generated | ideas_generated | flux_running | done | failed
    error_message TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
```

**Schema migration:** `create_tables()` runs `Base.metadata.create_all` then `_ensure_title_overlay_column()` which `ALTER TABLE ... ADD COLUMN title_overlay JSON` if missing (PoC-friendly; no Alembic yet).

**Example `title_overlay` payload:**

```json
{
  "headline": "POOLSIDE HAPPY HOUR",
  "headline_source": "Poolside Happy Hour",
  "subhead": "Mango Margarita Frost",
  "type_mood": "festive_bold",
  "anchor": "top_center",
  "title_rect": [120, 16, 400, 58],
  "font_size": 24,
  "display_font": "Montserrat-Bold.ttf",
  "text_color": [255, 255, 255],
  "scrim_dark": true,
  "product_rect": [25, 92, 281, 471],
  "logo_rect": [10, 400, 120, 480],
  "fallbacks": [],
  "previewed": false
}
```

---

## API Endpoints

Base path: `/api/v1`

### POST /ingest
Ingest a company URL → scrape, extract, save products + logo.

### POST /campaign
Generate 5 campaign themes for `{ company_slug, topic }` → returns `session_id`.

### POST /select
Select theme by number → best product + 3 image ideas.

### POST /preview-prompt
LLM-compressed Flux prompt for review (no Flux call).  
Request: `{ session_id, idea_number, user_tweak? }`  
Persists `flux_prompt` + `selected_idea_index` on session.

### POST /preview-title
On-image title options for review (no Flux call).

**Default:** generates **3 distinct titles** and selects #1.

```json
{
  "session_id": "uuid",
  "idea_number": 3,
  "title_number": null,
  "regenerate": false,
  "headline": null,
  "subhead": null,
  "type_mood": null
}
```

| Field | Behavior |
|-------|----------|
| (none) | Generate 3 options; select #1 |
| `title_number` 1–3 | Pick stored option (no new LLM call) |
| `regenerate: true` | Fresh batch of 3 |
| `headline` | Custom single title |

**Response:**

```json
{
  "titles": [
    {"number": 1, "headline": "...", "subhead": "...", "type_mood": "festive_bold"},
    {"number": 2, "headline": "...", "subhead": "...", "type_mood": "minimal_clean"},
    {"number": 3, "headline": "...", "subhead": "...", "type_mood": "luxury_editorial"}
  ],
  "selected_title_number": 1,
  "title": { "headline": "...", "subhead": "...", "type_mood": "...", "number": 1 }
}
```

Persists `title_options` + selected pack on `session.title_overlay` with `previewed: true`.

### POST /generate-from-prompt
Confirmed Flux prompt → Flux → logo → title → final.

Request: `{ session_id, flux_prompt }`

Response includes:

```json
{
  "session_id": "uuid",
  "status": "done",
  "selected_idea": "...",
  "flux_prompt_used": "...",
  "logo_placement": { "corner": "bottom_right", "brightness_map": {} },
  "title_overlay": {
    "headline": "...",
    "subhead": "...",
    "type_mood": "festive_bold",
    "anchor": "top_left"
  },
  "dominant_colors": ["#..."],
  "image_url": "http://localhost:8000/api/v1/jobs/{id}/image",
  "raw_url": "http://localhost:8000/api/v1/jobs/{id}/raw"
}
```

### POST /apply-title
Re-composite **logo + title** on existing `raw_image_path` (**no Flux**).

```json
{
  "session_id": "uuid",
  "headline": "optional override",
  "subhead": "optional",
  "type_mood": "festive_bold",
  "regenerate_copy": false
}
```

| Mode | Behavior |
|------|----------|
| `headline` set | Use override copy |
| `regenerate_copy: true` | New LLM title |
| neither | Reuse last / previewed title |

CLI: `pocc retitle --session <id> --headline "..." --mood festive_bold`

### POST /image
Legacy one-shot: build prompt + Flux + postprocess (logo + title). Prefer preview → generate flow.

### GET /jobs/{session_id}/status
### GET /jobs/{session_id}/image
Final PNG (logo + title).

### GET /jobs/{session_id}/raw
Flux-only PNG.

### GET /companies
List ingested companies.

---

## Key Services & Logic

### ingestion.py

1. `scrape_url` — Firecrawl → markdown + HTML + metadata  
2. `extract_logo_url` — multiple heuristics; strip query params  
3. `download_and_process_logo` — SVG via PyMuPDF → PNG; rembg when needed  
4. `extract_brand_colors` — CSS / theme-color / logo palette  
5. `download_product_image` — master PNG  
6. `extract_company_intelligence` — structured company + products + `social_media_profile`

### campaign.py

1. `generate_themes` — 5 themes (name, concept, angle, product, mood, ambience)  
2. `generate_image_ideas` — 3 intensity variants; **product left ~60%, mid-lower; upper ~15% open headroom for title; right for logo**  
3. `build_flux_prompt` — surgical 3-line edit prompt (&lt;70 words); physical open upper frame; **no marketing language, no typography instructions**  
4. `generate_image_titles` — LLM returns **3** packs `{ number, headline, subhead, type_mood }` (hook / product / minimal angles); `generate_image_title` returns the first for non-interactive paths  
5. `select_best_product` — exact → fuzzy → substring → first with master image  

### flux.py

1. `generate_with_flux` — scale master ≤1MP, POST flux-2-pro, poll, save `{session_id}_raw.png`  
2. Safety: `safety_tolerance: 2`, `prompt_upsampling: False`

### image_processor.py

**Logo**

1. `analyze_image_for_logo_placement` — 4-corner brightness; darkest-logo → brightest corner  
2. `composite_logo` — resize ~20% width, drop shadow, paste RGBA  

**Title overlay (premium Instagram-style)**

**Type systems** (`TYPE_SYSTEMS`):

| ID | Display | Support | Notes |
|----|---------|---------|-------|
| `campaign_impact` | Montserrat Black | Montserrat Bold | UPPERCASE, tight tracking, thick accent bar |
| `modern_dtc` | Outfit Bold | Outfit Regular | Clean geometric DTC |
| `editorial_luxe` | Cormorant Garamond Bold | Montserrat Light | Open tracking, hairline rule, magazine kicker |

**Layouts:**

| ID | Band | Stack |
|----|------|-------|
| `hero_headroom` | ~18% | Large headline + subhead + accent rule + soft scrim |
| `magazine_stack` | ~24% | Kicker → 1–2 line display → hairline → subhead |

**Safety:** product forbid rect; logo rect; candidates L/C/R; clutter scoring. Short headlines get a size boost (billboard).

**Pipeline:**

1. `generate_image_titles` — 3 packs with copy + `type_system` + `layout` + `kicker`  
2. `resolve_type_system` / `resolve_layout`  
3. `plan_title_placement` — measure tracked stack; collision avoid; fallbacks  
4. `composite_title` / `apply_title_overlay` — scrim + type + brand accent  
5. Fonts under `data/fonts/`

**Postprocess order** (`routes._postprocess_image`):

```
raw.png
  → analyze logo placement
  → composite_logo → (temp) final
  → resolve title pack (override | previewed | last | LLM | theme fallback)
  → apply_title_overlay → final.png
  → persist logo_placement + title_overlay
```

---

## CLI Usage

Commands in `cli/pocc.py` (Click + Rich). Base URL: `http://localhost:8000/api/v1`.

```bash
# 1. Ingest
python cli/pocc.py ingest --url https://spicenfood.com

# 2. Themes
python cli/pocc.py campaign --company spicen_foods --topic "summer pool party"

# 3. Select theme → ideas
python cli/pocc.py select --session <id> --theme 2

# 4. Generate (3-step interactive)
python cli/pocc.py image --session <id> --idea 1
#   Step 1/3 — Flux prompt (edit optional)
#   Step 2/3 — On-image title (edit optional)
#   Step 3/3 — Flux → logo → title

# 5. Retitle without Flux
python cli/pocc.py retitle --session <id> --headline "Poolside Happy Hour" --mood festive_bold
python cli/pocc.py retitle --session <id> --regenerate
python cli/pocc.py retitle --session <id>   # re-composite last title

# 6. Status / list
python cli/pocc.py status --session <id>
python cli/pocc.py list
```

### CLI Logging
- File: `./pocc.log` at DEBUG  
- Stderr: progress + errors  
- Timing per command  

### Backend Logging
- `app/core/logging.py` → stdout for Uvicorn  
- Coverage: ingestion, campaign, flux, image_processor, routes  

---

## Design Decisions

### 1. Synchronous FastAPI (no Celery yet)
PoC simplicity; endpoints block up to ~2 min (Flux).

### 2. Logo assumed dark
Brightest corner for contrast. Override `logo_brightness` in DB if needed.

### 3. Deterministic placement analyzers
Logo corners + title band/forbid rects: fast, reproducible. Not vision-model product masks (future).

### 4. Master image locked
Every Flux call uses original master — no cumulative drift.

### 5. Flux 2 Pro img2img (not txt2img)
Product fidelity over scene freedom.

### 6. Titles via Pillow, not Flux
Flux text is unreliable. Copy = LLM; pixels = Pillow + bundled fonts. Same pattern as logo.

### 7. Separate copy vs layout
- **Copy / aura intent:** `generate_image_title` + brand voice  
- **Look:** font packs + recipes + contrast/scrim  
- Human can edit via `preview-title` / CLI / `retitle`

### 8. Composition prompts reserve title headroom
Ideas + Flux prompts keep upper ~15% relatively open so titles rarely fight busy props.

### 9. Title iteration without Flux
`/apply-title` recomposites from raw → cheap taste iteration.

---

## Configuration

`.env` (from `.env.example`):

```
DATABASE_URL=postgresql://...
FIRECRAWL_API_KEY=...
OPENAI_API_KEY=...
FLUX_API_KEY=...
IMAGE_DIR=./data/images
HOST=0.0.0.0
PORT=8000
```

---

## Known Limitations & Future Improvements

### Limitations

1. **Product auto-selection is fuzzy** — name mismatch can pick wrong product  
2. **Logo placement is corner-only** — no rotation / smart product avoidance  
3. **Product forbid rect is fixed** — not a real segmentation mask; tall products can still clip title band occasionally  
4. **Flux quality varies** — product can drift; prompt + guidance trade-offs  
5. **No retry queue** on Flux timeout  
6. **No QA scoring** of final creatives  
7. **Font packs are small** — 4–5 moods, not full brand design systems  

### Next steps (priority)

1. Celery + Redis for concurrent jobs  
2. rembg / segmentation product mask for tighter title safety  
3. Title layout templates (hero / bar / editorial packs)  
4. QA scoring (product drift detection)  
5. S3/R2 storage  
6. Batch variations + pick best  
7. Telegram / web UI  
8. Metrics dashboard  

---

## Testing Checklist

- [ ] Server starts: `uvicorn app.main:app --reload --port 8000`
- [ ] DB reachable; `title_overlay` column exists on `campaign_sessions`
- [ ] Fonts present under `data/fonts/`
- [ ] FastAPI docs: `http://localhost:8000/docs`
- [ ] `pocc list` / ingest works
- [ ] `pocc campaign` → 5 themes + session_id
- [ ] `pocc select` → 3 ideas
- [ ] `pocc image` → Flux prompt panel → title panel → final URL
- [ ] Final image shows logo **and** title in top band
- [ ] Title does not sit on logo; product mid-frame mostly clear
- [ ] `pocc retitle --headline "..."` updates final without new Flux job
- [ ] Raw URL has no logo/title; final has both

---

## Debugging

| Issue | Action |
|-------|--------|
| Flux blank / drift | Tighten preserve line; lower guidance if configured |
| Logo missing | Check `data/images/logos/`; fix `logo_local_path` |
| rembg slow first run | Allow U2Net download (~170MB) |
| Title LLM fail | Fallback uses theme/product name; check `OPENAI_API_KEY` |
| Title over busy area | Shorter headline; different mood; `retitle`; improve headroom in idea |
| `title_overlay` missing column | Restart API or run migrate via `create_tables()` |
| CLI connection refused | Uvicorn not running on :8000 |

---

## Deployment Notes (Later)

1. Celery + Redis  
2. S3/R2 for images  
3. Auth on API  
4. Worker autoscaling  
5. Monitoring + Sentry  
6. Rate-limit Flux  
7. Telegram or dashboard UI  

---

## Quick Reference

| Task | Command |
|------|---------|
| Start server | `uvicorn app.main:app --reload --port 8000` |
| Ingest | `python cli/pocc.py ingest --url <url>` |
| Themes | `python cli/pocc.py campaign --company <slug> --topic <topic>` |
| Select theme | `python cli/pocc.py select --session <id> --theme <n>` |
| Generate (+ title) | `python cli/pocc.py image --session <id> --idea <n>` |
| Retitle only | `python cli/pocc.py retitle --session <id> [--headline ...] [--mood ...] [--regenerate]` |
| Status | `python cli/pocc.py status --session <id>` |
| List companies | `python cli/pocc.py list` |
| Final image | `http://localhost:8000/api/v1/jobs/<id>/image` |
| Raw image | `http://localhost:8000/api/v1/jobs/<id>/raw` |
| API docs | `http://localhost:8000/docs` |

---

## Contact & Support

PoC for solo iteration. Component map:

| Area | File |
|------|------|
| Ingestion | `app/services/ingestion.py` |
| Campaign / titles copy | `app/services/campaign.py` |
| Flux | `app/services/flux.py` |
| Logo + title pixels | `app/services/image_processor.py` |
| API / postprocess | `app/api/routes.py` |
| Schema | `app/models/db.py` |
| CLI | `cli/pocc.py` |
