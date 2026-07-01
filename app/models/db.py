from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, JSON, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func
from app.core.config import settings
import uuid

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class Company(Base):
    __tablename__ = "companies"

    id             = Column(String, primary_key=True, default=gen_uuid)
    name           = Column(String, nullable=False)
    slug           = Column(String, nullable=False, unique=True)   # human-readable reference
    website_url    = Column(String, nullable=False, unique=True)
    industry       = Column(String)
    description    = Column(Text)
    brand_voice    = Column(JSON)          # list of strings
    target_audience = Column(JSON)         # list of strings
    marketing_angles = Column(JSON)        # list of strings
    content_pillars  = Column(JSON)        # list of strings
    primary_color  = Column(String)        # hex
    secondary_color = Column(String)       # hex
    accent_color   = Column(String)        # hex
    logo_url       = Column(String)        # original scraped url
    logo_local_path = Column(String)       # saved locally as png
    logo_brightness = Column(String)       # 'light' | 'dark'
    social_media_profile = Column(JSON)    # instagram_tone, cta_style, emoji_usage
    crawl_status   = Column(String, default="pending")
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    products       = relationship("Product", back_populates="company")
    campaigns      = relationship("CampaignSession", back_populates="company")


class Product(Base):
    __tablename__ = "products"

    id              = Column(String, primary_key=True, default=gen_uuid)
    company_id      = Column(String, ForeignKey("companies.id"), nullable=False)
    name            = Column(String, nullable=False)
    description     = Column(Text)
    benefits        = Column(JSON)         # list of strings
    image_urls      = Column(JSON)         # scraped source urls
    master_image_path = Column(String)     # local path to downloaded master png
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    company         = relationship("Company", back_populates="products")


class CampaignSession(Base):
    __tablename__ = "campaign_sessions"

    id                  = Column(String, primary_key=True, default=gen_uuid)
    company_id          = Column(String, ForeignKey("companies.id"), nullable=False)
    topic               = Column(String, nullable=False)
    themes              = Column(JSON)     # list of 5 theme objects
    selected_theme      = Column(JSON)     # the chosen theme
    selected_product_id = Column(String, ForeignKey("products.id"))
    image_ideas         = Column(JSON)     # list of 3 idea strings
    selected_idea_index = Column(Integer)  # 0-based index into image_ideas
    flux_prompt         = Column(Text)     # exact prompt sent to flux
    flux_job_id         = Column(String)
    raw_image_path      = Column(String)   # flux output before logo
    final_image_path    = Column(String)   # after logo placement
    logo_placement      = Column(JSON)     # {corner, x, y, width, height, brightness_map}
    status              = Column(String, default="pending")
    # status: pending | themes_generated | theme_selected | ideas_generated
    #         idea_selected | flux_running | flux_done | logo_placed | done | failed
    error_message       = Column(Text)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    company             = relationship("Company", back_populates="campaigns")
    product             = relationship("Product")


# -- database setup --

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
