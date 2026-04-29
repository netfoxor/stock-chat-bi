from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Security
    jwt_secret: str = Field(default="change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expires_days: int = Field(default=7, alias="JWT_EXPIRES_DAYS")

    # Database (MySQL)
    database_url: str = Field(
        default="mysql+aiomysql://root:root@localhost:3306/stock?charset=utf8mb4",
        alias="DATABASE_URL",
    )

    # LLM provider key (nanobot may use it)
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")

    # CORS
    cors_allow_origins: str = Field(default="http://localhost:5173,http://localhost:3000", alias="CORS_ALLOW_ORIGINS")


settings = Settings()

