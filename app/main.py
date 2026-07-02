from fastapi import FastAPI
from app.api.routes import router
from app.models.db import create_tables
from app.core.logging import setup_backend_logging

setup_backend_logging()

app = FastAPI(
    title="POCC — Product Creative Platform",
    description="Company intelligence + AI product image generation",
    version="0.1.0"
)

# create tables on startup
@app.on_event("startup")
def startup():
    create_tables()

app.include_router(router, prefix="/api/v1")

@app.get("/")
def root():
    return {"status": "running", "docs": "/docs"}
