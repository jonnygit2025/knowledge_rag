from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    youtube_api_key: SecretStr = SecretStr("")
    config_file: Path = Path("config.yaml")
    export_dir: Path = Path("data/exports")
    import_dir: Path = Path("data/imports")
    embedding_provider: str = "demo"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=64, le=4096)
    llm_model: str = ""
    provider_base_url: str = "https://api.openai.com/v1"
    provider_api_key: SecretStr = SecretStr("")
    max_paid_calls_per_day: int = Field(default=0, ge=0)
    startup_sync: bool = True
    sync_interval_minutes: int = Field(default=60, ge=5)
    job_lease_seconds: int = Field(default=600, ge=30)
    max_job_attempts: int = Field(default=5, ge=1)
    chunk_chars: int = Field(default=1400, ge=200, le=10000)

    @property
    def namespace(self) -> str:
        import hashlib

        name = (
            "demo-hash-v1-256"
            if self.embedding_provider == "demo"
            else (
                f"{self.embedding_provider}-{self.provider_base_url}-"
                f"{self.embedding_model}-{self.embedding_dimensions}"
            )
        )
        return (
            "kr_"
            + hashlib.sha256(f"{name}-segments-v1-{self.chunk_chars}".encode()).hexdigest()[:20]
        )

    @property
    def dimensions(self) -> int:
        return 256 if self.embedding_provider == "demo" else self.embedding_dimensions


@lru_cache
def settings() -> Settings:
    return Settings()
