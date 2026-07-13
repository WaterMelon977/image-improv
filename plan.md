# Plan: Campaign Pack + Export Kit

**Status:** Proposed  
**Last updated:** 2026-07-13  
**Goal:** One topic in → a full postable Instagram set out (images + copy + files), not a single PNG.

---

## 1. Product statement

### Today
User runs: campaign → select → image (one idea) → gets one final image with logo + title.

### Target
User runs: **campaign pack** for a company + topic (+ selected theme) and receives a **ready-to-post kit**:

| Asset | Format | Purpose |
|-------|--------|---------|
| 3× feed posts | 1:1 or 4:5 PNG | Main grid (one per image idea) |
| 1× story cover | 9:16 PNG | Stories / Reels cover from best idea |
| Captions | `.txt` per post | Instagram caption under the image |
| Hashtags | section in caption or separate file | Discoverability |
| Alt text | short line per image | Accessibility / Meta |
| Manifest | `manifest.json` + `README.txt` | What’s in the kit, how to post |
| Zip | single download | Drop into Later / Buffer / drive |

**One-liner for users:**  
> “Give me this week’s Instagram for my product brand.”

---

## 2. Success criteria

| Metric | Definition |
|--------|------------|
| Time-to-pack | &lt; 15 min wall clock for 3 feeds + story (Flux-bound) |
| Completeness | Zip always contains images + captions + manifest |
| Brand consistency | Same company type system bias across pack items unless overridden |
| Zero designer | No Canva/Photoshop required to schedule |
| Iteration | User can retitle or regenerate one pack item without rebuilding whole pack |

**Non-goals (v1):** auto-post to Instagram API, paid ads variants, multi-language, full carousel multi-slide product education (slide 2 can be v1.1).

---

## 3. User journeys

### Journey A — Happy path (CLI)

```text
pocc campaign --company spicen_foods --topic "summer pool party"
pocc select --session <id> --theme 2
pocc pack --session <id>
  → builds 3 feed assets (ideas 1–3)
  → builds 1 story from idea 1 (or highest “theme-rich” idea)
  → writes captions + zip
  → prints pack path + summary table
```

Optional flags:

```text
pocc pack --session <id> --ideas 1,2,3
pocc pack --session <id> --story-idea 1
pocc pack --session <id> --skip-story
pocc pack --session <id> --aspect 4:5
```

### Journey B — API

```text
POST /campaign
POST /select
POST /pack/generate   { session_id, idea_numbers?, story_idea?, aspect? }
GET  /pack/{pack_id}/status
GET  /pack/{pack_id}/download   → application/zip
GET  /pack/{pack_id}/manifest
```

### Journey C — Partial regenerate

```text
pocc pack-item --pack <pack_id> --item feed_2 --retitle
pocc pack-item --pack <pack_id> --item feed_1 --regenerate-scene
```

Rebuild one asset, re-zip. Do not re-Flux the whole pack.

---

## 4. Pack contents (canonical folder layout)

```text
data/packs/{pack_id}/
  manifest.json
  README.txt
  feed/
    01_hook/
      final.png          # logo + title, feed aspect
      raw.png            # flux only (optional keep)
      caption.txt
      meta.json          # idea, title pack, flux prompt, placement
    02_product/
      ...
    03_minimal/
      ...
  story/
    cover/
      final.png          # 9:16
      caption.txt        # shorter
      meta.json
  export/
    {company_slug}_{topic_slug}_pack.zip
```

### Naming for consumers (inside zip, friendlier)

```text
spicen_foods_summer_pool_party/
  01_feed_hook.png
  01_caption.txt
  02_feed_product.png
  02_caption.txt
  03_feed_minimal.png
  03_caption.txt
  story_cover.png
  story_caption.txt
  HOW_TO_POST.txt
  manifest.json
```

---

## 5. What each pack item generates

### 5.1 Feed item (×3)

Reuse existing pipeline **per idea**:

1. `build_flux_prompt` for idea i  
2. `generate_with_flux` → raw  
3. Logo composite  
4. Title: use `generate_image_titles` → **auto-pick** mapped to idea intensity:
   - Idea 1 (theme-rich) → title option style **hook** / `campaign_impact` + `hero_headroom`
   - Idea 2 (simplified) → **product** / `modern_dtc` + `hero_headroom`
   - Idea 3 (ultra minimal) → **editorial** / `editorial_luxe` + `magazine_stack`
5. Optional: still allow override via pack config later  
6. Write caption pack (LLM)  
7. Save meta  

**v1 decision:** pack is **non-interactive** on titles/prompts (auto) so “one command” holds. Power users keep `pocc image` for interactive single posts. v1.1 can add `--interactive` pack mode.

### 5.2 Story cover (×1)

Derived from **one** feed raw (default idea 1):

1. Center-crop / pad to **9:16** from raw (or re-Flux at story aspect if crop kills product — prefer crop first)  
2. Re-run logo placement for new canvas  
3. Title with **larger type**, shorter headline (reuse selected title or regenerate 3–4 word variant)  
4. Stronger scrim (story needs more contrast)  
5. Short story caption (1 line + emoji policy from brand)

### 5.3 Caption system (new)

New LLM function: `generate_post_caption(company, theme, product, idea, title_pack, channel)`  

Returns:

```json
{
  "caption_body": "…",
  "cta": "Shop the drop ↗",
  "hashtags": ["#…", …],
  "alt_text": "…",
  "emoji_usage": "light"
}
```

Rules:

- Respect `social_media_profile.instagram_tone`, `cta_style`, `emoji_usage`
- Brand voice list
- No false claims; no invented certifications
- Feed caption 80–150 words max; story ≤ 40 words  
- Hashtags: 5–12, mix branded + topical  
- Final `caption.txt` format:

```text
{caption_body}

{cta}

{hashtags joined}
```

---

## 6. Architecture

### 6.1 New modules

| File | Responsibility |
|------|----------------|
| `app/services/pack.py` | Orchestrate multi-item generation, status, paths |
| `app/services/export_kit.py` | Write files, zip, HOW_TO_POST, friendly names |
| `app/services/caption.py` | LLM captions / alt text / hashtags |
| `app/services/aspect.py` | Resize/crop to 1:1, 4:5, 9:16; product-safe crop heuristics |

### 6.2 Extend existing

| File | Change |
|------|--------|
| `app/services/campaign.py` | Optional helpers for pack title auto-selection mapping |
| `app/services/image_processor.py` | Optional `composite_for_aspect` / story-stronger scrim flag |
| `app/api/routes.py` | `/pack/generate`, status, download, manifest |
| `app/models/db.py` | `ContentPack` (+ optional `PackItem`) tables |
| `cli/pocc.py` | `pack`, later `pack-item` |
| `PROJECT_CONTEXT.md` / `README.md` | Document after ship |

### 6.3 Orchestration flow

```text
POST /pack/generate
  → create ContentPack (status=queued|running)
  → for each idea in plan:
        status item=running
        flux → postprocess (logo+title) → caption → write files
        item=done | failed
  → build story from chosen raw
  → zip
  → pack status=done | partial (if ≥1 item ok)
```

**v1 execution model:** synchronous sequential (same as current PoC). Expect 3× Flux time (~3–8 min).  
**v1.1:** background task / Celery when pain is real.

Progress:

- Pack `status`: `pending | generating | exporting | done | partial | failed`  
- Per item `status` in JSON for CLI table  
- CLI: Rich progress “Feed 1/3… Feed 2/3… Story… Zipping…”

### 6.4 Failure policy

| Failure | Behavior |
|---------|----------|
| Flux fails on idea 2 | Mark item failed, continue 1 & 3 |
| Caption LLM fails | Fallback: title + theme concept + product name + 3 generic hashtags |
| Logo missing | Continue without logo (same as today) |
| All feeds fail | pack `failed`, no zip |
| ≥1 feed ok | `partial` + zip of successes + README notes failures |

---

## 7. Data model

### 7.1 Table `content_packs`

```sql
CREATE TABLE content_packs (
  id                TEXT PRIMARY KEY,
  session_id        TEXT REFERENCES campaign_sessions(id),
  company_id        TEXT REFERENCES companies(id),
  topic             TEXT,
  theme             JSONB,              -- snapshot of selected_theme
  product_id        TEXT,
  config            JSONB,              -- aspect, idea_numbers, story_idea, auto_title_map
  items             JSONB,              -- list of item descriptors + paths + statuses
  pack_dir          TEXT,               -- data/packs/{id}
  zip_path          TEXT,
  status            TEXT,               -- pending|generating|exporting|done|partial|failed
  error_message     TEXT,
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ
);
```

### 7.2 Item shape (JSON in `items`)

```json
{
  "id": "feed_01",
  "kind": "feed",
  "idea_index": 0,
  "idea": "...",
  "status": "done",
  "raw_path": "...",
  "final_path": "...",
  "caption_path": "...",
  "title": { "headline": "...", "type_system": "...", "layout": "..." },
  "flux_prompt": "...",
  "error": null
}
```

### 7.3 Session link

- Keep `campaign_sessions` as creative parent (themes/ideas).  
- Pack is a **downstream deliverable** of a session after select.  
- Multiple packs per session allowed (re-run with different config).

### 7.4 Migration

- `create_tables()` + lightweight `ALTER` helper pattern (same as `title_overlay`).

---

## 8. API contracts

### `POST /api/v1/pack/generate`

```json
{
  "session_id": "uuid",
  "idea_numbers": [1, 2, 3],
  "story_idea_number": 1,
  "feed_aspect": "1:1",
  "include_story": true,
  "include_raw_in_zip": false
}
```

Response (sync v1 when finished):

```json
{
  "pack_id": "uuid",
  "status": "done",
  "items": [ ...summary... ],
  "zip_url": "/api/v1/pack/{pack_id}/download",
  "manifest_url": "/api/v1/pack/{pack_id}/manifest",
  "elapsed_seconds": 312
}
```

### `GET /api/v1/pack/{pack_id}/status`
### `GET /api/v1/pack/{pack_id}/manifest`
### `GET /api/v1/pack/{pack_id}/download` → zip `FileResponse`

---

## 9. CLI

```text
pocc pack --session <id>
pocc pack --session <id> --ideas 1,2,3 --story-idea 1 --aspect 1:1
pocc pack-status --pack <pack_id>
```

Output:

```text
┌ Pack ready ─────────────────────────────────────┐
│ Company   Spicen Foods                          │
│ Topic     summer pool party                     │
│ Theme     Backyard Freedom Party                │
│ Feeds     3/3 ok                                │
│ Story     ok                                    │
│ Zip       data/packs/.../export/....zip         │
│ Download  http://localhost:8000/api/v1/pack/... │
└─────────────────────────────────────────────────┘
```

---

## 10. Aspect / crop rules (`aspect.py`)

| Target | Size (v1) | Method |
|--------|-----------|--------|
| Feed 1:1 | 1080×1080 | Center crop from Flux output; prefer keeping product left zone |
| Feed 4:5 | 1080×1350 | Vertical bias crop (slightly more bottom) |
| Story 9:16 | 1080×1920 | Vertical crop; title band still top |

Product safety:

- Prefer crop windows that keep estimated product rect (existing forbid-rect heuristic) inside frame  
- If impossible, letterbox with blurred fill of dominant color (better than cutting product)

**v1:** center/product-aware crop only (no second Flux call).  
**v1.1:** optional re-Flux at target aspect for story if crop quality score low.

---

## 11. Export kit details

### `export_kit.py` responsibilities

1. Create pack directory structure  
2. Copy/rename finals into consumer-friendly names  
3. Write `caption.txt`, `HOW_TO_POST.txt`  
4. Write `manifest.json` (machine-readable)  
5. Zip with compression  
6. Return zip path  

### `HOW_TO_POST.txt` (template)

```text
POCC Campaign Pack
Company: {name}
Topic: {topic}
Theme: {theme_name}

How to post (Instagram):
1. Open Meta Business Suite or Instagram app
2. Upload 01_feed_*.png → paste 01_caption.txt
3. Repeat for 02 and 03
4. Stories: upload story_cover.png → paste story_caption.txt
5. Best practice: space posts 1 day apart or same-day series

Generated: {timestamp}
```

### `manifest.json` fields

- pack_id, company_slug, topic, theme, product  
- items[] with filenames, title, type_system, layout, idea  
- generated_at, tool_version  

---

## 12. Implementation phases

### Phase 0 — Scaffold (0.5 day)

- [ ] `ContentPack` model + migrate  
- [ ] `data/packs/` directory convention  
- [ ] Empty `pack.py`, `export_kit.py`, `caption.py`, `aspect.py`  
- [ ] Routes stubs + CLI `pack` stub  

### Phase 1 — Single-item pack path (1 day)

- [ ] Given session + one idea, run existing postprocess into pack folder  
- [ ] Caption LLM + caption.txt  
- [ ] Zip with one feed  
- [ ] Prove end-to-end without multi-Flux complexity  

### Phase 2 — Full 3-feed pack (1–2 days)

- [ ] Loop ideas 1–3 with progress  
- [ ] Auto title mapping (hook / product / editorial)  
- [ ] Partial failure handling  
- [ ] CLI table + status  

### Phase 3 — Story + aspects (1 day)

- [ ] 9:16 crop/composite  
- [ ] Story title treatment (larger type, stronger scrim)  
- [ ] Optional 4:5 feed aspect  

### Phase 4 — Polish export (0.5 day)

- [ ] Friendly zip names  
- [ ] HOW_TO_POST  
- [ ] Download endpoint  
- [ ] README / PROJECT_CONTEXT update  

### Phase 5 — Pack item regenerate (optional, 1 day)

- [ ] `pack-item` retitle / regenerate-scene  
- [ ] Re-zip  

**Recommended ship gate:** end of Phase 4.

---

## 13. Defaults & config

| Setting | Default | Notes |
|---------|---------|-------|
| `idea_numbers` | `[1,2,3]` | All ideas from select |
| `feed_aspect` | `1:1` | IG classic |
| `include_story` | `true` | From idea 1 |
| `include_raw_in_zip` | `false` | Keep disk raw server-side only |
| `auto_title` | `true` | Non-interactive pack |
| Output size | 1080 on long edge | Social native |

Env (optional later):

```text
PACK_DIR=./data/packs
PACK_FEED_SIZE=1080
```

---

## 14. Reuse map (what not to rewrite)

| Capability | Already exists | Pack uses it as |
|------------|----------------|-----------------|
| Themes / ideas | `campaign.py` | Pack input |
| Flux img2img | `flux.py` | Per feed raw |
| Logo + title | `image_processor.py` + `_postprocess_image` | Extract shared `finalize_creative()` |
| 3 titles + systems | `generate_image_titles` | Auto-pick by idea index |
| Brand voice / social | Company columns | Caption LLM |
| Retitle without Flux | `/apply-title` | Pack-item retitle later |

**Important refactor (small, do in Phase 1):**  
Extract `_postprocess_image` core into `services/finalize.py` (or pack-callable function) so pack and single-image share logo+title logic without duplicating routes.

---

## 15. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| 3× Flux cost/time | Clear CLI estimate; sequential progress; partial packs |
| Sync HTTP timeout | Raise proxy/uvicorn timeouts; v1.1 async |
| Crop cuts product | Product-rect aware crop; letterbox fallback |
| Caption brand-unsafe | Strict prompt; no medical claims; fallback short caption |
| Disk growth | Pack retention policy later; zip is source of truth for users |
| Interactive users hate auto titles | Keep `pocc image` path; pack docs say “batch mode” |

---

## 16. Testing plan

### Automated / scripted

- [ ] Caption JSON parse + file write unit test  
- [ ] Aspect crop produces exact 1080×1080 / 1080×1920  
- [ ] Zip contains expected file list  
- [ ] Partial: mock one Flux fail → zip still has 2 feeds  

### Manual

- [ ] Real company session → full pack  
- [ ] Open zip, paste captions into IG test account  
- [ ] Story looks readable on phone  
- [ ] Titles don’t cover product on all 3 feeds  
- [ ] `HOW_TO_POST` is understandable to non-engineer  

---

## 17. Docs & messaging (when shipping)

**README section:** “Campaign Pack”

```bash
python cli/pocc.py campaign --company <slug> --topic "..."
python cli/pocc.py select --session <id> --theme 2
python cli/pocc.py pack --session <id>
# → zip path + download URL
```

**Positioning line:**  
*POCC doesn’t just make an image — it delivers a postable campaign kit.*

---

## 18. Future extensions (out of v1 scope, planned adjacent)

1. Carousel slide 2 (ingredients / benefits typography, no Flux)  
2. Interactive pack (pick title per item before Flux)  
3. Shopify product pull → multi-product pack  
4. Weekly autopilot from content pillars  
5. Gallery UI to approve pack items  
6. Async workers  
7. Direct schedule integrations (Buffer API)

---

## 19. Decision log (locked for v1)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interactivity | Auto titles/prompts in pack | True one-command utility |
| Story source | Crop from existing raw first | Cost/speed |
| Failure | Partial pack OK | Don’t waste successful Flux jobs |
| Zip | Always on success/partial | Primary consumer artifact |
| Aspects | 1:1 feed + 9:16 story | Covers 80% IG use |
| Caption | Separate LLM call per item | Quality over one giant prompt |
| Storage | Local `data/packs` | Matches current PoC storage |

---

## 20. Implementation order (checklist for builder)

1. Extract shared finalize (logo + title) callable from routes  
2. `caption.py` + unit-friendly pure writers  
3. `aspect.py` crop helpers  
4. `export_kit.py` folder + zip  
5. `pack.py` orchestrator (1 item → 3 items → story)  
6. DB model + migrate  
7. API endpoints  
8. CLI `pack`  
9. Manual E2E on Spicen-like company  
10. Docs  

---

## 21. Definition of done

- [ ] From an ideas-ready session, one CLI command produces a zip  
- [ ] Zip has 3 feed images (or partial with clear failures) + captions  
- [ ] Zip has story cover when enabled  
- [ ] Manifest + HOW_TO_POST included  
- [ ] Download URL works via API  
- [ ] README documents the flow  
- [ ] No regression to single `pocc image` path  

---

*This plan is ready for implementation without further product Q&A. Execute Phase 0→4 in order unless a dependency forces a skip.*
