import uuid
import asyncio
import logging
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from pathlib import Path

logger = logging.getLogger("app.api.routes")

from app.models.db import get_db, Company, Product, CampaignSession
from app.services.ingestion import ingest_company
from app.services.campaign import (
    generate_themes, generate_image_ideas,
    build_flux_prompt, select_best_product,
    generate_image_title,
)
from app.services.flux import generate_with_flux
from app.services.image_processor import (
    analyze_image_for_logo_placement,
    composite_logo,
    extract_dominant_colors,
    apply_title_overlay,
)

router = APIRouter()


# -- request / response schemas --

class IngestRequest(BaseModel):
    url: str

class CampaignRequest(BaseModel):
    company_slug: str
    topic: str

class SelectThemeRequest(BaseModel):
    session_id: str
    theme_number: int  # 1-5

class SelectIdeaRequest(BaseModel):
    session_id: str
    idea_number: int   # 1-3

class GenerateImageRequest(BaseModel):
    session_id: str

class FluxPreviewRequest(BaseModel):
    session_id: str
    idea_number: int          # 1-3 — which idea to compress into a Flux prompt
    user_tweak: str | None = None  # optional inline edit instruction e.g. "make it golden hour"

class FluxGenerateRequest(BaseModel):
    session_id: str
    flux_prompt: str   # the confirmed (or user-edited) prompt to send to Flux

class TitlePreviewRequest(BaseModel):
    session_id: str
    idea_number: int | None = None  # defaults to session.selected_idea_index + 1
    headline: str | None = None     # optional override (skip LLM if provided with subhead/mood)
    subhead: str | None = None
    type_mood: str | None = None

class ApplyTitleRequest(BaseModel):
    """Re-composite logo + title on an existing raw image (no Flux call)."""
    session_id: str
    headline: str | None = None
    subhead: str | None = None
    type_mood: str | None = None
    regenerate_copy: bool = False  # force a new LLM title if no headline override


# -- helper --

def _get_company_or_404(slug: str, db: Session) -> Company:
    company = db.query(Company).filter(Company.slug == slug).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company '{slug}' not found. Run --url first.")
    return company

def _get_session_or_404(session_id: str, db: Session) -> CampaignSession:
    session = db.query(CampaignSession).filter(CampaignSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")
    return session


def normalize_url(url: str) -> str:
    """
    Standardize incoming URLs to prevent duplication:
    - Strips spaces and converts to lowercase
    - Force scheme to 'https://' (scheme-agnostic duplication check)
    - Strips 'www.' subdomain
    - Strips trailing slashes
    """
    url_str = url.strip().lower()
    
    # Remove any existing scheme so we can force a uniform 'https://'
    if url_str.startswith("http://"):
        url_str = url_str[7:]
    elif url_str.startswith("https://"):
        url_str = url_str[8:]
        
    parsed = urlparse("https://" + url_str)  # Force https
    netloc = parsed.netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
        
    path = parsed.path.rstrip("/")
    return f"https://{netloc}{path}"


# ============================================================
# POST /ingest
# ingests a company url — full pipeline
# ============================================================

@router.post("/ingest")
async def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    # Normalize URL immediately
    normalized_url = normalize_url(req.url)
    logger.info("POST /ingest called. Original URL: %s, Normalized: %s", req.url, normalized_url)
    
    # Get all ingested companies and compare normalized URLs to catch legacy records
    companies = db.query(Company).all()
    existing = None
    for c in companies:
        if normalize_url(c.website_url) == normalized_url:
            existing = c
            break

    if existing:
        logger.info("Company already ingested: %s (slug=%s)", existing.name, existing.slug)
        return {
            "status": "already_exists",
            "company_name": existing.name,
            "company_slug": existing.slug,
            "message": f"Company already loaded. Reference as: {existing.slug}"
        }

    # run full ingestion pipeline with normalized URL
    try:
        data = await ingest_company(normalized_url)
        intel = data["intelligence"]
        colors = data["colors"]
    except Exception as e:
        logger.error("Ingestion failed for URL %s: %s", normalized_url, str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    logger.debug("Ingestion successful. Saving company to database: %s", intel["company_name"])
    # save company to db
    company = Company(
        id=str(uuid.uuid4()),
        name=intel["company_name"],
        slug=data["slug"],
        website_url=normalized_url,
        industry=intel.get("industry"),
        description=intel.get("description"),
        brand_voice=intel.get("brand_voice", []),
        target_audience=intel.get("target_audience", []),
        marketing_angles=intel.get("marketing_angles", []),
        content_pillars=intel.get("content_pillars", []),
        primary_color=colors.get("primary"),
        secondary_color=colors.get("secondary"),
        accent_color=colors.get("accent"),
        logo_url=data.get("logo_url"),
        logo_local_path=data.get("logo_local_path"),
        logo_brightness=data.get("logo_brightness", "dark"),
        social_media_profile=intel.get("social_media_profile"),
        crawl_status="done"
    )
    db.add(company)
    db.flush()  # get company.id before products

    # save products
    logger.debug("Saving %d products for company to database...", len(data.get("products", [])))
    for p_data in data.get("products", []):
        product = Product(
            id=p_data.get("_id", str(uuid.uuid4())),
            company_id=company.id,
            name=p_data["name"],
            description=p_data.get("description"),
            benefits=p_data.get("benefits", []),
            image_urls=p_data.get("image_urls", []),
            master_image_path=p_data.get("master_image_path")
        )
        db.add(product)

    db.commit()
    logger.info("Database commit successful. Company %s (slug=%s) fully saved.", company.name, company.slug)

    return {
        "status": "success",
        "company_name": company.name,
        "company_slug": company.slug,
        "industry": company.industry,
        "products_found": len(data.get("products", [])),
        "logo_saved": bool(company.logo_local_path),
        "logo_url": company.logo_url,
        "brand_colors": colors,
        "message": f"Company loaded. Reference as: {company.slug}"
    }



# ============================================================
# POST /campaign
# generate 5 campaign themes for a company + topic
# ============================================================

@router.post("/campaign")
def campaign(req: CampaignRequest, db: Session = Depends(get_db)):
    logger.info("POST /campaign called with company_slug: %s, topic: '%s'", req.company_slug, req.topic)
    company = _get_company_or_404(req.company_slug, db)
    products = db.query(Product).filter(Product.company_id == company.id).all()

    if not products:
        logger.error("No products found in DB for company: %s", company.name)
        raise HTTPException(status_code=400, detail="No products found for this company. Re-run ingestion.")

    # build lightweight company dict for AI calls
    company_dict = {
        "name": company.name,
        "industry": company.industry,
        "brand_voice": company.brand_voice or [],
        "products": [
            {"name": p.name, "description": p.description, "benefits": p.benefits}
            for p in products
        ]
    }

    logger.debug("Generating themes for company %s", company.name)
    themes = generate_themes(company_dict, req.topic)

    # create session to track this campaign
    session = CampaignSession(
        id=str(uuid.uuid4()),
        company_id=company.id,
        topic=req.topic,
        themes=themes,
        status="themes_generated"
    )
    db.add(session)
    db.commit()
    logger.info("Campaign session %s created with status themes_generated", session.id)

    return {
        "session_id": session.id,
        "topic": req.topic,
        "themes": themes,
        "next": f"POST /select with session_id and theme_number (1-5)"
    }


# ============================================================
# POST /select
# select theme → auto-pick product → generate 3 image ideas
# ============================================================

@router.post("/select")
def select_theme(req: SelectThemeRequest, db: Session = Depends(get_db)):
    logger.info("POST /select called with session_id: %s, theme_number: %d", req.session_id, req.theme_number)
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    products = db.query(Product).filter(Product.company_id == company.id).all()

    themes = session.themes
    if not themes or req.theme_number < 1 or req.theme_number > len(themes):
        logger.error("Invalid theme number requested: %d (available: 1-%d)", req.theme_number, len(themes) if themes else 0)
        raise HTTPException(status_code=400, detail=f"Invalid theme number. Choose 1-{len(themes)}.")

    chosen_theme = themes[req.theme_number - 1]
    logger.info("Selected theme: %s", chosen_theme.get("theme_name"))

    # auto-select best product for this theme
    products_dicts = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "master_image_path": p.master_image_path
        }
        for p in products
    ]
    logger.debug("Invoking product selection algorithm...")
    best_product = select_best_product(products_dicts, chosen_theme)
    if not best_product:
        logger.error("Product selection algorithm failed to find a candidate product")
        raise HTTPException(status_code=400, detail="No suitable product found for this theme.")

    logger.info("Selected best product: %s (id=%s)", best_product["name"], best_product["id"])

    company_dict = {
        "name": company.name,
        "brand_voice": company.brand_voice or [],
    }

    logger.debug("Generating 3 image ideas via campaign service...")
    ideas = generate_image_ideas(company_dict, chosen_theme, best_product)

    # update session
    session.selected_theme = chosen_theme
    session.selected_product_id = best_product["id"]
    session.image_ideas = ideas
    session.status = "ideas_generated"
    db.commit()
    logger.info("Campaign session %s updated with ideas and product mapping. Status: ideas_generated", session.id)

    return {
        "session_id": session.id,
        "selected_theme": chosen_theme,
        "selected_product": best_product["name"],
        "has_master_image": bool(best_product.get("master_image_path")),
        "image_ideas": [
            {"number": i + 1, "idea": idea}
            for i, idea in enumerate(ideas)
        ],
        "next": f"POST /image with session_id and idea_number (1-3)"
    }



# ============================================================
# POST /image
# select idea → flux edit → analyze → logo placement → final image
# ============================================================

@router.post("/image")
async def generate_image(req: GenerateImageRequest, idea_number: int, db: Session = Depends(get_db)):
    logger.info("POST /image called for session_id: %s, idea_number: %d", req.session_id, idea_number)
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        logger.error("No product selected for session: %s", session.id)
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    ideas = session.image_ideas or []
    if idea_number < 1 or idea_number > len(ideas):
        logger.error("Invalid idea number requested: %d (available: 1-%d)", idea_number, len(ideas))
        raise HTTPException(status_code=400, detail=f"Invalid idea number. Choose 1-{len(ideas)}.")

    chosen_idea = ideas[idea_number - 1]
    logger.info("Generating image using idea: '%s' for product '%s'", chosen_idea, product.name)

    if not product.master_image_path or not Path(product.master_image_path).exists():
        logger.error("Master image path does not exist or is missing: %s", product.master_image_path)
        raise HTTPException(
            status_code=400,
            detail=f"No master image found for product '{product.name}'. "
                   "Product images may not have been downloaded during ingestion."
        )

    # build context dicts
    company_dict = {
        "name": company.name,
        "brand_voice": company.brand_voice or [],
        "primary_color": company.primary_color,
        "secondary_color": company.secondary_color,
    }

    # build flux prompt
    flux_prompt = build_flux_prompt(
        company=company_dict,
        theme=session.selected_theme,
        product={"name": product.name, "description": product.description},
        idea=chosen_idea
    )

    # update session — mark as running
    session.selected_idea_index = idea_number - 1
    session.flux_prompt = flux_prompt
    session.status = "flux_running"
    db.commit()
    logger.info("Session %s updated to status flux_running", session.id)

    # call flux
    try:
        raw_path, flux_job_id = await generate_with_flux(
            master_image_path=product.master_image_path,
            prompt=flux_prompt,
            session_id=session.id
        )
    except Exception as e:
        logger.error("Flux generation failed for session %s: %s", session.id, str(e), exc_info=True)
        session.status = "failed"
        session.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Flux generation failed: {e}")

    session.raw_image_path = raw_path
    session.flux_job_id = flux_job_id
    session.status = "flux_done"
    db.commit()
    logger.info("Flux job %s completed. Raw image saved to %s", flux_job_id, raw_path)

    post = _postprocess_image(
        session=session,
        company=company,
        product=product,
        raw_path=raw_path,
        idea=chosen_idea,
        db=db,
    )

    return {
        "session_id": session.id,
        "status": "done",
        "selected_idea": chosen_idea,
        "flux_prompt_used": flux_prompt,
        "logo_placement": {
            "corner": post["logo_placement"]["best_corner"],
            "brightness_map": post["logo_placement"]["brightness_map"],
        },
        "title_overlay": {
            "headline": post["title_overlay"].get("headline"),
            "subhead": post["title_overlay"].get("subhead"),
            "type_mood": post["title_overlay"].get("type_mood"),
            "anchor": post["title_overlay"].get("anchor"),
        },
        "dominant_colors": post["dominant_colors"],
        "image_url": f"http://localhost:8000/api/v1/jobs/{session.id}/image",
        "raw_url":   f"http://localhost:8000/api/v1/jobs/{session.id}/raw",
    }


# ============================================================
# GET /jobs/{session_id}/status
# ============================================================

@router.get("/jobs/{session_id}/status")
def job_status(session_id: str, db: Session = Depends(get_db)):
    logger.debug("GET /jobs/%s/status called", session_id)
    session = _get_session_or_404(session_id, db)
    return {
        "session_id": session_id,
        "status": session.status,
        "error": session.error_message
    }


# ============================================================
# GET /jobs/{session_id}/image
# serves the final image file
# ============================================================

@router.get("/jobs/{session_id}/image")
def serve_image(session_id: str, db: Session = Depends(get_db)):
    logger.info("GET /jobs/%s/image called", session_id)
    session = _get_session_or_404(session_id, db)
    if not session.final_image_path or not Path(session.final_image_path).exists():
        logger.warning("Final image file not found or not ready yet for session: %s", session_id)
        raise HTTPException(status_code=404, detail="Final image not ready yet.")
    logger.debug("Serving final image file: %s", session.final_image_path)
    return FileResponse(session.final_image_path, media_type="image/png")


@router.get("/jobs/{session_id}/raw")
def serve_raw(session_id: str, db: Session = Depends(get_db)):
    logger.info("GET /jobs/%s/raw called", session_id)
    session = _get_session_or_404(session_id, db)
    if not session.raw_image_path or not Path(session.raw_image_path).exists():
        logger.warning("Raw image file not found or not ready yet for session: %s", session_id)
        raise HTTPException(status_code=404, detail="Raw image not ready.")
    logger.debug("Serving raw image file: %s", session.raw_image_path)
    return FileResponse(session.raw_image_path, media_type="image/png")


# ============================================================
# GET /companies
# list all ingested companies
# ============================================================

@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    logger.info("GET /companies called")
    companies = db.query(Company).all()
    logger.debug("Retrieved %d companies from DB", len(companies))
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "industry": c.industry,
            "products": db.query(Product).filter(Product.company_id == c.id).count()
        }
        for c in companies
    ]


# ============================================================
# POST /preview-prompt
# generate + return the compressed Flux prompt for user review.
# DOES NOT call Flux — cheap LLM-only operation.
# ============================================================

@router.post("/preview-prompt")
def preview_flux_prompt(req: FluxPreviewRequest, db: Session = Depends(get_db)):
    logger.info("POST /preview-prompt called for session_id: %s, idea_number: %d", req.session_id, req.idea_number)
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    ideas = session.image_ideas or []
    if req.idea_number < 1 or req.idea_number > len(ideas):
        raise HTTPException(status_code=400, detail=f"Invalid idea number. Choose 1-{len(ideas)}.")

    chosen_idea = ideas[req.idea_number - 1]

    company_dict = {
        "name": company.name,
        "brand_voice": company.brand_voice or [],
        "primary_color": company.primary_color,
        "secondary_color": company.secondary_color,
    }

    logger.debug("Building compressed Flux prompt via LLM for idea: '%s', tweak: '%s'", chosen_idea, req.user_tweak)
    flux_prompt = build_flux_prompt(
        company=company_dict,
        theme=session.selected_theme,
        product={"name": product.name, "description": product.description},
        idea=chosen_idea,
        user_tweak=req.user_tweak
    )

    # persist the prompt and idea index so /generate-from-prompt can read them
    session.selected_idea_index = req.idea_number - 1
    session.flux_prompt = flux_prompt
    db.commit()
    logger.info("Flux prompt preview generated and saved to session %s", session.id)

    return {
        "session_id": session.id,
        "idea_number": req.idea_number,
        "selected_idea": chosen_idea,
        "flux_prompt": flux_prompt,
        "ready_to_generate": True,
        "next": "POST /generate-from-prompt with session_id and flux_prompt (edit the prompt if needed)"
    }


# ============================================================
# POST /preview-title
# LLM title pack for the selected idea — cheap, no Flux call.
# Persist on session so /generate-from-prompt can reuse it.
# ============================================================

@router.post("/preview-title")
def preview_title(req: TitlePreviewRequest, db: Session = Depends(get_db)):
    logger.info(
        "POST /preview-title session=%s idea_number=%s",
        req.session_id,
        req.idea_number,
    )
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    ideas = session.image_ideas or []
    if req.idea_number is not None:
        if req.idea_number < 1 or req.idea_number > len(ideas):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid idea number. Choose 1-{len(ideas)}.",
            )
        idea_index = req.idea_number - 1
        session.selected_idea_index = idea_index
    elif session.selected_idea_index is not None:
        idea_index = session.selected_idea_index
    elif ideas:
        idea_index = 0
        session.selected_idea_index = 0
    else:
        raise HTTPException(status_code=400, detail="No image ideas. Run /select first.")

    idea_text = ideas[idea_index] if ideas else ""

    # Manual override path (user-edited title)
    if req.headline and req.headline.strip():
        title_pack = {
            "headline": req.headline.strip(),
            "subhead": (req.subhead or "").strip(),
            "type_mood": (req.type_mood or "minimal_clean").strip().lower(),
        }
    else:
        company_dict = {
            "name": company.name,
            "brand_voice": company.brand_voice or [],
            "social_media_profile": company.social_media_profile or {},
            "primary_color": company.primary_color,
        }
        title_pack = generate_image_title(
            company=company_dict,
            theme=session.selected_theme or {},
            product={"name": product.name, "description": product.description or ""},
            idea=idea_text,
        )
        if req.subhead is not None:
            title_pack["subhead"] = req.subhead.strip()
        if req.type_mood:
            title_pack["type_mood"] = req.type_mood.strip().lower()

    # Store copy-only pack; placement is computed at composite time against the real image
    session.title_overlay = {
        **(session.title_overlay or {}),
        "headline": title_pack["headline"],
        "subhead": title_pack.get("subhead") or "",
        "type_mood": title_pack.get("type_mood") or "minimal_clean",
        "previewed": True,
    }
    db.commit()
    logger.info("Title preview saved for session %s: %s", session.id, title_pack)

    return {
        "session_id": session.id,
        "idea_number": idea_index + 1,
        "selected_idea": idea_text,
        "title": title_pack,
        "ready_to_generate": True,
        "next": "POST /generate-from-prompt (title is reused from session.title_overlay)",
    }


# ============================================================
# POST /generate-from-prompt
# user confirmed (or edited) the Flux prompt — now call Flux.
# accepts the prompt string directly so user can tweak before submission.
# ============================================================

@router.post("/generate-from-prompt")
async def generate_from_prompt(req: FluxGenerateRequest, db: Session = Depends(get_db)):
    logger.info("POST /generate-from-prompt called for session_id: %s", req.session_id)
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    if not product.master_image_path or not Path(product.master_image_path).exists():
        raise HTTPException(
            status_code=400,
            detail=f"No master image found for product '{product.name}'. Re-run ingestion."
        )

    flux_prompt = req.flux_prompt.strip()
    if not flux_prompt:
        raise HTTPException(status_code=400, detail="flux_prompt cannot be empty.")

    logger.info("Using Flux prompt (length=%d): %s", len(flux_prompt), flux_prompt)

    # persist final prompt in use (may differ from preview if user edited it)
    session.flux_prompt = flux_prompt
    session.status = "flux_running"
    db.commit()

    # call Flux
    try:
        raw_path, flux_job_id = await generate_with_flux(
            master_image_path=product.master_image_path,
            prompt=flux_prompt,
            session_id=session.id
        )
    except Exception as e:
        logger.error("Flux generation failed for session %s: %s", session.id, str(e), exc_info=True)
        session.status = "failed"
        session.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Flux generation failed: {e}")

    session.raw_image_path = raw_path
    session.flux_job_id = flux_job_id
    session.status = "flux_done"
    db.commit()
    logger.info("Flux job %s completed. Raw image: %s", flux_job_id, raw_path)

    # resolve idea text for title generation
    ideas = session.image_ideas or []
    idea_text = ""
    if session.selected_idea_index is not None and 0 <= session.selected_idea_index < len(ideas):
        idea_text = ideas[session.selected_idea_index]
    elif ideas:
        idea_text = ideas[0]

    post = _postprocess_image(
        session=session,
        company=company,
        product=product,
        raw_path=raw_path,
        idea=idea_text,
        db=db,
    )

    return {
        "session_id": session.id,
        "status": "done",
        "selected_idea": idea_text,
        "flux_prompt_used": flux_prompt,
        "logo_placement": {
            "corner": post["logo_placement"]["best_corner"],
            "brightness_map": post["logo_placement"]["brightness_map"],
        },
        "title_overlay": {
            "headline": post["title_overlay"].get("headline"),
            "subhead": post["title_overlay"].get("subhead"),
            "type_mood": post["title_overlay"].get("type_mood"),
            "anchor": post["title_overlay"].get("anchor"),
        },
        "dominant_colors": post["dominant_colors"],
        "image_url": f"http://localhost:8000/api/v1/jobs/{session.id}/image",
        "raw_url":   f"http://localhost:8000/api/v1/jobs/{session.id}/raw",
    }


def _postprocess_image(
    session: CampaignSession,
    company: Company,
    product: Product,
    raw_path: str,
    idea: str,
    db: Session,
    title_override: dict | None = None,
    force_new_title: bool = False,
) -> dict:
    """
    Logo placement → logo composite → title composite (safe top band).
    Updates session final_image_path, logo_placement, title_overlay, status.

    title_override: optional {headline, subhead, type_mood} skips LLM / preview reuse.
    force_new_title: ignore previewed title and call LLM again.
    """
    logger.debug("Post-processing image for session %s", session.id)

    placement = analyze_image_for_logo_placement(raw_path)
    dominant_colors = extract_dominant_colors(raw_path)

    work_path = raw_path
    if company.logo_local_path and Path(company.logo_local_path).exists():
        logger.info("Compositing logo onto image...")
        work_path = composite_logo(
            base_image_path=raw_path,
            logo_path=company.logo_local_path,
            placement=placement,
            session_id=session.id,
        )
    else:
        logger.warning("No local logo; skipping logo composite")

    company_dict = {
        "name": company.name,
        "brand_voice": company.brand_voice or [],
        "social_media_profile": company.social_media_profile or {},
        "primary_color": company.primary_color,
    }
    theme = session.selected_theme or {}
    product_dict = {
        "name": product.name,
        "description": product.description or "",
    }

    title_pack = _resolve_title_pack(
        session=session,
        company_dict=company_dict,
        theme=theme,
        product_dict=product_dict,
        idea=idea or "",
        title_override=title_override,
        force_new_title=force_new_title,
    )

    try:
        final_path, title_placement = apply_title_overlay(
            base_image_path=work_path,
            headline=title_pack.get("headline") or "New Drop",
            session_id=session.id,
            subhead=title_pack.get("subhead") or "",
            type_mood=title_pack.get("type_mood") or "minimal_clean",
            logo_placement=placement,
            primary_color=company.primary_color,
            brand_voice=company.brand_voice or [],
        )
        title_overlay = {**title_pack, **title_placement, "previewed": False}
    except Exception as e:
        logger.error("Title composite failed: %s", e, exc_info=True)
        final_path = work_path
        title_overlay = {**title_pack, "error": str(e)}

    session.final_image_path = final_path
    session.logo_placement = placement
    session.title_overlay = title_overlay
    session.status = "done"
    db.commit()
    logger.info(
        "Post-process done session=%s title=%r anchor=%s",
        session.id,
        title_overlay.get("headline"),
        title_overlay.get("anchor"),
    )

    return {
        "logo_placement": placement,
        "title_overlay": title_overlay,
        "dominant_colors": dominant_colors,
        "final_path": final_path,
    }


def _resolve_title_pack(
    session: CampaignSession,
    company_dict: dict,
    theme: dict,
    product_dict: dict,
    idea: str,
    title_override: dict | None = None,
    force_new_title: bool = False,
) -> dict:
    """Pick title copy: explicit override → previewed → LLM → theme fallback."""
    if title_override and title_override.get("headline"):
        pack = {
            "headline": title_override["headline"].strip(),
            "subhead": (title_override.get("subhead") or "").strip(),
            "type_mood": (title_override.get("type_mood") or "minimal_clean").strip().lower(),
        }
        logger.info("Using title override: %s", pack)
        return pack

    existing = session.title_overlay or {}
    if (
        not force_new_title
        and existing.get("previewed")
        and existing.get("headline")
    ):
        pack = {
            "headline": existing["headline"],
            "subhead": existing.get("subhead") or "",
            "type_mood": existing.get("type_mood") or "minimal_clean",
        }
        logger.info("Reusing previewed title: %s", pack)
        return pack

    # Prefer last composited headline when re-applying without override
    if (
        not force_new_title
        and existing.get("headline")
        and not existing.get("error")
    ):
        pack = {
            "headline": existing.get("headline_source") or existing["headline"],
            "subhead": existing.get("subhead") or "",
            "type_mood": existing.get("type_mood") or "minimal_clean",
        }
        # If stored headline was uppercased for render, prefer source
        logger.info("Reusing last session title: %s", pack)
        return pack

    pack = {
        "headline": theme.get("theme_name") or product_dict.get("name") or "New Drop",
        "subhead": product_dict.get("name") or "",
        "type_mood": "minimal_clean",
    }
    try:
        pack = generate_image_title(
            company=company_dict,
            theme=theme,
            product=product_dict,
            idea=idea or "",
        )
    except Exception as e:
        logger.warning("Title LLM failed, using fallback: %s", e)
    return pack


# ============================================================
# POST /apply-title
# Re-run logo + title composite on an existing raw image (no Flux).
# ============================================================

@router.post("/apply-title")
def apply_title(req: ApplyTitleRequest, db: Session = Depends(get_db)):
    logger.info(
        "POST /apply-title session=%s regenerate=%s headline=%r",
        req.session_id,
        req.regenerate_copy,
        req.headline,
    )
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    raw_path = session.raw_image_path
    if not raw_path or not Path(raw_path).exists():
        raise HTTPException(
            status_code=400,
            detail="No raw image on this session. Run image generation first.",
        )

    ideas = session.image_ideas or []
    idea_text = ""
    if session.selected_idea_index is not None and 0 <= session.selected_idea_index < len(ideas):
        idea_text = ideas[session.selected_idea_index]
    elif ideas:
        idea_text = ideas[0]

    title_override = None
    if req.headline and req.headline.strip():
        title_override = {
            "headline": req.headline.strip(),
            "subhead": (req.subhead or "").strip(),
            "type_mood": (req.type_mood or "minimal_clean").strip().lower(),
        }
    elif req.subhead is not None or req.type_mood:
        # partial override on top of last/previewed title
        base = session.title_overlay or {}
        title_override = {
            "headline": base.get("headline_source") or base.get("headline") or product.name,
            "subhead": req.subhead if req.subhead is not None else (base.get("subhead") or ""),
            "type_mood": (req.type_mood or base.get("type_mood") or "minimal_clean"),
        }

    post = _postprocess_image(
        session=session,
        company=company,
        product=product,
        raw_path=raw_path,
        idea=idea_text,
        db=db,
        title_override=title_override,
        force_new_title=req.regenerate_copy and not title_override,
    )

    return {
        "session_id": session.id,
        "status": "done",
        "selected_idea": idea_text,
        "logo_placement": {
            "corner": post["logo_placement"]["best_corner"],
            "brightness_map": post["logo_placement"]["brightness_map"],
        },
        "title_overlay": {
            "headline": post["title_overlay"].get("headline"),
            "subhead": post["title_overlay"].get("subhead"),
            "type_mood": post["title_overlay"].get("type_mood"),
            "anchor": post["title_overlay"].get("anchor"),
        },
        "dominant_colors": post["dominant_colors"],
        "image_url": f"http://localhost:8000/api/v1/jobs/{session.id}/image",
        "raw_url": f"http://localhost:8000/api/v1/jobs/{session.id}/raw",
        "recomposited": True,
    }

