from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/rompecabezas"
    debug: bool = True
    secret_key: str = "cambiar-esto-en-.env-con-un-valor-random-largo"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"  # "none" + cookie_secure=True en producción (cross-domain)
    # D7: Render usa ALLOWED_ORIGINS (JSON); el .env local usa CORS_ORIGINS (nombre
    # histórico del campo). Ambos conviven vía AliasChoices.
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        validation_alias=AliasChoices("ALLOWED_ORIGINS", "CORS_ORIGINS", "cors_origins"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()