# POCC — Product Creative Platform

AI product image generation for Instagram-style creatives.  
**FastAPI + Flux 2 Pro + OpenAI + Pillow** (logo + on-image title overlays).

---

## Setup

```powershell
# 1. venv + deps
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows
# source venv/bin/activate    # macOS/Linux
pip install -r requirements.txt

# 2. env
copy .env.example .env
# fill: DATABASE_URL, FIRECRAWL_API_KEY, OPENAI_API_KEY, FLUX_API_KEY

# 3. start API (Terminal A)
uvicorn app.main:app --reload --port 8000

# 4. CLI (Terminal B)
python cli/pocc.py --help
```

On startup the API creates tables and migrates `campaign_sessions.title_overlay` if missing.

API docs: http://localhost:8000/docs

---

## Usage

### Step 1 — Ingest a company

```bash
python cli/pocc.py ingest --url https://spicenfood.com
```

Saves company, products, logo, brand voice/colors. Note the **slug** (e.g. `spicen_foods`).

### Step 2 — Campaign themes

```bash
python cli/pocc.py campaign --company spicen_foods --topic "summer pool party"
```

Returns 5 themes + **session_id**.

### Step 3 — Select a theme → image ideas

```bash
python cli/pocc.py select --session <session_id> --theme 2
```

Auto-picks best product and generates 3 scene ideas (with top headroom for titles).

### Step 4 — Generate image (prompt + title + Flux)

```bash
python cli/pocc.py image --session <session_id> --idea 1
```

Interactive **3-step** flow:

1. **Flux prompt** — preview; optional tweak (`y`/`n`)
2. **On-image title** — **3 options** (hook / product / minimal); pick `1`–`3`, `(e)dit`, or `(r)egenerate`
3. **Generate** — Flux 2 Pro → logo composite → title overlay → final PNG

### View images

```
Final (logo + title):  http://localhost:8000/api/v1/jobs/<session_id>/image
Raw (Flux only):       http://localhost:8000/api/v1/jobs/<session_id>/raw
```

### Step 5 — Iterate title only (no Flux)

Reuse the existing raw photo; re-place logo + title:

```bash
python cli/pocc.py retitle --session <session_id> --headline "Poolside Happy Hour" --mood festive_bold
python cli/pocc.py retitle --session <session_id> --title 2
python cli/pocc.py retitle --session <session_id> --regenerate   # 3 new options, then pick
```

### Other commands

```bash
python cli/pocc.py list
python cli/pocc.py status --session <session_id>
```

---

## On-image titles

Titles are **not** drawn by Flux (unreliable text). Pipeline:

```
LLM: 3 title options (hook / product / minimal) → user picks one
  → Flux scene (product preserved)
  → Pillow logo (corner brightness)
  → Pillow title in fixed top band
```

**Safety layout**

| Region | Role |
|--------|------|
| Top ~15% | Title band only |
| Left ~5–55%, y ~18–92% | Product forbid zone |
| Logo corner box | Title must not overlap |

Candidates: `top_left` / `top_center` / `top_right`, scored by clutter + brightness. Soft scrim + auto light/dark text + brand-color underline.

**Type systems** (letter DNA) + **layouts** (how type sits)

| System | Fonts | Feels like |
|--------|-------|------------|
| `campaign_impact` | Montserrat Black + Bold | Launch / Nike energy, often UPPERCASE |
| `modern_dtc` | Outfit Bold + Regular | Clean DTC / Glossier-ish |
| `editorial_luxe` | Cormorant Garamond + Montserrat Light | Magazine / Aesop-adjacent |

| Layout | Treatment |
|--------|-----------|
| `hero_headroom` | Large type in top band + soft scrim + accent rule |
| `magazine_stack` | Kicker → stacked display → hairline rule → subhead |

Each of the **3 title options** carries its own `type_system` + `layout`. Short headlines auto-scale larger (billboard). Fonts live in `data/fonts/`.

---

## Architecture

```
CLI (Click + Rich)
  ↓ HTTP
FastAPI  /api/v1
  ├── POST /ingest               → Firecrawl → AI extract → PostgreSQL + files
  ├── POST /campaign             → 5 themes
  ├── POST /select               → theme → product → 3 image ideas
  ├── POST /preview-prompt       → compressed Flux prompt (edit)
  ├── POST /preview-title        → title pack (edit); stored on session
  ├── POST /generate-from-prompt  → Flux → logo → title → final PNG
  ├── POST /apply-title          → re-composite logo + title (no Flux)
  ├── POST /image                → legacy one-shot generate
  ├── GET  /jobs/{id}/status
  ├── GET  /jobs/{id}/image
  ├── GET  /jobs/{id}/raw
  └── GET  /companies
```

---

## File layout

```
image-improv/
  app/
    api/routes.py          — FastAPI endpoints + postprocess (logo + title)
    core/config.py         — settings from .env
    models/db.py           — SQLAlchemy models + title_overlay migrate
    services/
      ingestion.py         — Firecrawl + AI extract + logo download
      campaign.py          — themes, ideas, flux prompt, generate_image_title
      flux.py              — Flux 2 Pro client
      image_processor.py   — logo placement + title plan/composite
  cli/pocc.py              — ingest | campaign | select | image | retitle | status | list
  data/
    fonts/                 — OpenSans, Montserrat, Playfair (bundled TTFs)
    images/
      logos/
      products/
      generated/           — {session}_raw.png, {session}_final.png
  PROJECT_CONTEXT.md       — full design context
  requirements.txt
  .env.example
```

---

## Logging

- **CLI file:** `./pocc.log` (`DEBUG`, `[timestamp] [level] [command] message`)
- **CLI stderr:** progress + errors
- **API:** stdout via `app/core/logging.py`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Flux blank / drift | Lower guidance / tighten preserve line in prompt |
| Logo missing | Check `data/images/logos/`; SVG uses PyMuPDF rasterize |
| rembg slow first run | Downloads ~170MB U2Net once |
| Title missing on final | Ensure generate finished; check `title_overlay` on session |
| Title over product | Top band + forbid rects; try shorter headline or `retitle` |
| DB column errors | Restart API (auto-adds `title_overlay`) |
| CLI connection refused | Is `uvicorn` running on port 8000? |

---

## Quick reference

| Task | Command |
|------|---------|
| Start server | `uvicorn app.main:app --reload --port 8000` |
| Ingest | `python cli/pocc.py ingest --url <url>` |
| Themes | `python cli/pocc.py campaign --company <slug> --topic "..."` |
| Select theme | `python cli/pocc.py select --session <id> --theme <n>` |
| Generate | `python cli/pocc.py image --session <id> --idea <n>` |
| Retitle only | `python cli/pocc.py retitle --session <id> --headline "..."` |
| Status | `python cli/pocc.py status --session <id>` |
| List companies | `python cli/pocc.py list` |
| Final image | `http://localhost:8000/api/v1/jobs/<id>/image` |

For deeper design notes, schema, and service internals see **[PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md)**.
