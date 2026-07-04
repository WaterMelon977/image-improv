# POCC — Product Creative Platform

Telegram-driven AI product image generation. FastAPI + Flux 2 Pro + Claude Sonnet.

---

## Setup

# 1. venv + deps
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. copy .env with your Supabase DATABASE_URL
cp .env.example .env
# fill in: DATABASE_URL=postgresql://user:password@db.supabase.co:5432/postgres

# 3. start server
uvicorn app.main:app --reload --port 8000

# 4. run CLI
python cli/pocc.py ingest --url https://example.com
python cli/pocc.py --help

# or install as command
pip install -e .
pocc --help


---

## Usage

### Step 1 — Ingest a company
```bash
python cli/pocc.py ingest --url https://spicenfood.com
```
Output:
```
Company saved.
Name:     Spicen Foods
Industry: Food & Beverage
Products: 5 found
Reference this company as: spicen_foods
```

### Step 2 — Generate campaign themes
```bash
python cli/pocc.py campaign --company spicen_foods --topic "summer pool party"
```
Output: table of 5 themes + session ID

### Step 3 — Select a theme
```bash
python cli/pocc.py select --session <session_id> --theme 2
```
Output: selected theme details + 3 image ideas

### Step 4 — Generate the image
```bash
python cli/pocc.py image --session <session_id> --idea 3
```
Output: progress bar → final image URL

### View the image
```
http://localhost:8000/api/v1/jobs/<session_id>/image
```

### List all companies
```bash
python cli/pocc.py list
```

### Check job status
```bash
python cli/pocc.py status --session <session_id>
```

---

## API docs
```
http://localhost:8000/docs
```

---

## Architecture

```
CLI (Click + Rich)
  ↓ HTTP
FastAPI
  ├── POST /ingest                  → Firecrawl → AI extract → PostgreSQL
  ├── POST /campaign                → Claude Sonnet → 5 themes
  ├── POST /select                  → pick theme → 3 image ideas
  ├── POST /preview-prompt          → LLM-based Flux prompt builder (review & edit)
  ├── POST /generate-from-prompt    → Flux 2 Pro (img2img) → logo placement → final PNG
  ├── GET  /jobs/{id}/status
  └── GET  /jobs/{id}/image         → serves final PNG
```

---

## File layout

```
pocc/
  app/
    api/routes.py        — all fastapi endpoints
    core/config.py       — settings from .env
    models/db.py         — sqlalchemy models + table creation
    services/
      ingestion.py       — firecrawl + ai extraction + logo download
      campaign.py        — theme gen + image ideas + flux prompt builder
      flux.py            — flux kontext api client
      image_processor.py — corner analysis + pillow logo compositing
  cli/
    pocc.py              — click + rich cli
  data/images/           — local image storage
    logos/
    products/
    generated/
  docker-compose.yml
  requirements.txt
  .env.example
```

---

## Logging
The CLI includes structured logging output:
- **File log**: All command operations are logged in detail at the `DEBUG` level inside `./pocc.log` in the format:
  `[timestamp] [level] [command] message` (e.g., `[2025-07-04 12:30:45] [INFO] [ingest] Company saved: spicen (slug=spicen)`)
- **Console feedback**: Crucial execution steps and warnings/errors are streamed to stderr to keep stdout clean.
- **Timing**: The execution time is tracked and logged upon completion of each command.

---

## Troubleshooting

**Flux returns blank image:** Lower `guidance_scale` in `flux.py` (try 2.5).

**Logo download failed / SVG support:** Logo URLs are automatically cleaned of query parameters prior to download. If the logo is an SVG, it is saved directly as-is to `data/images/logos/{company_slug}_logo.svg` and automatically rasterized to a PNG using `pymupdf` (fitz) so that no native Cairo C-libraries are required on Windows.

**rembg slow on first run:** It downloads the U2Net model (~170MB). Subsequent runs are fast.

**Database already exists error:** Tables auto-create on startup. If schema changes, run `docker-compose down -v` and restart.
