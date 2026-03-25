from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-lite"

    # Embeddings
    embedding_model: str = "models/text-embedding-004"
    embedding_dimension: int = 768

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://tenderscout:tenderscout@localhost:5432/tenderscout"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"

    # Pipeline thresholds
    tier0_cpv_confidence_threshold: float = 0.85
    tier2_batch_size: int = 10
    min_description_length: int = 20
    max_description_length: int = 10000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
