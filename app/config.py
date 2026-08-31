from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/rompecabezas"
    debug: bool = True
    secret_key: str = "cambiar-esto-en-.env-con-un-valor-random-largo"
    cookie_secure: bool = False  
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
