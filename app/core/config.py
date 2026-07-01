from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    database_url: str
    firecrawl_api_key: str
    openai_api_key: str
    flux_api_key: str
    image_dir: str = "./data/images"
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_image_dir(self) -> Path:
        p = Path(self.image_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
