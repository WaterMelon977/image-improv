import uuid
import asyncio
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from pathlib import Path

from app.models.db import get_db, Company, Product, CampaignSession
from app.services.ingestion import ingest_company
from app.services.campaign import (
    generate_themes, generate_image_ideas,
    build_flux_prompt, select_best_product
)
from app.services.flux import generate_with_flux
from app.services.image_processor import (
    analyze_image_for_logo_placement,
    composite_logo,
    extract_dominant_colors
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


# ============================================================
# POST /ingest
# ingests a company url — full pipeline
# ============================================================

@router.post("/ingest")
async def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    # check if already ingested
    existing = db.query(Company).filter(Company.website_url == req.url).first()
    if existing:
        return {
            "status": "already_exists",
            "company_name": existing.name,
            "company_slug": existing.slug,
            "message": f"Company already loaded. Reference as: {existing.slug}"
        }

    # run full ingestion pipeline
    data = await ingest_company(req.url)
    intel = data["intelligence"]
    colors = data["colors"]

    # save company to db
    company = Company(
        id=str(uuid.uuid4()),
        name=intel["company_name"],
        slug=data["slug"],
        website_url=req.url,
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

    return {
        "status": "success",
        "company_name": company.name,
        "company_slug": company.slug,
        "industry": company.industry,
        "products_found": len(data.get("products", [])),
        "logo_saved": bool(company.logo_local_path),
        "brand_colors": colors,
        "message": f"Company loaded. Reference as: {company.slug}"
    }


# ============================================================
# POST /campaign
# generate 5 campaign themes for a company + topic
# ============================================================

@router.post("/campaign")
def campaign(req: CampaignRequest, db: Session = Depends(get_db)):
    company = _get_company_or_404(req.company_slug, db)
    products = db.query(Product).filter(Product.company_id == company.id).all()

    if not products:
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
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    products = db.query(Product).filter(Product.company_id == company.id).all()

    themes = session.themes
    if not themes or req.theme_number < 1 or req.theme_number > len(themes):
        raise HTTPException(status_code=400, detail=f"Invalid theme number. Choose 1-{len(themes)}.")

    chosen_theme = themes[req.theme_number - 1]

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
    best_product = select_best_product(products_dicts, chosen_theme)
    if not best_product:
        raise HTTPException(status_code=400, detail="No suitable product found for this theme.")

    company_dict = {
        "name": company.name,
        "brand_voice": company.brand_voice or [],
    }

    ideas = generate_image_ideas(company_dict, chosen_theme, best_product)

    # update session
    session.selected_theme = chosen_theme
    session.selected_product_id = best_product["id"]
    session.image_ideas = ideas
    session.status = "ideas_generated"
    db.commit()

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
    session = _get_session_or_404(req.session_id, db)
    company = db.query(Company).filter(Company.id == session.company_id).first()
    product = db.query(Product).filter(Product.id == session.selected_product_id).first()

    if not product:
        raise HTTPException(status_code=400, detail="No product selected. Run /select first.")

    ideas = session.image_ideas or []
    if idea_number < 1 or idea_number > len(ideas):
        raise HTTPException(status_code=400, detail=f"Invalid idea number. Choose 1-{len(ideas)}.")

    chosen_idea = ideas[idea_number - 1]

    if not product.master_image_path or not Path(product.master_image_path).exists():
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

    # call flux
    try:
        raw_path, flux_job_id = await generate_with_flux(
            master_image_path=product.master_image_path,
            prompt=flux_prompt,
            session_id=session.id
        )
    except Exception as e:
        session.status = "failed"
        session.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Flux generation failed: {e}")

    session.raw_image_path = raw_path
    session.flux_job_id = flux_job_id
    session.status = "flux_done"
    db.commit()

    # analyze generated image for logo placement
    placement = analyze_image_for_logo_placement(raw_path)

    # extract dominant colors for reporting
    dominant_colors = extract_dominant_colors(raw_path)

    # composite logo if available
    final_path = raw_path  # default: no logo
    if company.logo_local_path and Path(company.logo_local_path).exists():
        final_path = composite_logo(
            base_image_path=raw_path,
            logo_path=company.logo_local_path,
            placement=placement,
            session_id=session.id
        )
        session.status = "logo_placed"
    else:
        session.status = "done_no_logo"

    session.final_image_path = final_path
    session.logo_placement = placement
    session.status = "done"
    db.commit()

    return {
        "session_id": session.id,
        "status": "done",
        "selected_idea": chosen_idea,
        "flux_prompt_used": flux_prompt,
        "logo_placement": {
            "corner": placement["best_corner"],
            "brightness_map": placement["brightness_map"]
        },
        "dominant_colors": dominant_colors,
        "image_url": f"http://localhost:8000/jobs/{session.id}/image",
        "raw_url":   f"http://localhost:8000/jobs/{session.id}/raw"
    }


# ============================================================
# GET /jobs/{session_id}/status
# ============================================================

@router.get("/jobs/{session_id}/status")
def job_status(session_id: str, db: Session = Depends(get_db)):
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
    session = _get_session_or_404(session_id, db)
    if not session.final_image_path or not Path(session.final_image_path).exists():
        raise HTTPException(status_code=404, detail="Final image not ready yet.")
    return FileResponse(session.final_image_path, media_type="image/png")


@router.get("/jobs/{session_id}/raw")
def serve_raw(session_id: str, db: Session = Depends(get_db)):
    session = _get_session_or_404(session_id, db)
    if not session.raw_image_path or not Path(session.raw_image_path).exists():
        raise HTTPException(status_code=404, detail="Raw image not ready.")
    return FileResponse(session.raw_image_path, media_type="image/png")


# ============================================================
# GET /companies
# list all ingested companies
# ============================================================

@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "industry": c.industry,
            "products": db.query(Product).filter(Product.company_id == c.id).count()
        }
        for c in companies
    ]
