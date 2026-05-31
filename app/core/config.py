from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.local_config import load_local_config


class Settings(BaseSettings):
    app_name: str = "AlphaEngine"
    market_data_provider: Literal["hybrid", "finnhub", "polygon", "eastmoney", "mock"] = Field(
        default="hybrid",
        validation_alias=AliasChoices("ALPHA_MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER"),
    )
    ai_advisor_provider: Literal["auto", "openai", "mock", "disabled"] = Field(
        default="auto",
        validation_alias=AliasChoices("ALPHA_AI_ADVISOR_PROVIDER", "AI_ADVISOR_PROVIDER"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALPHA_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    ai_model_family: Literal["gpt", "openai_compatible", "gemini", "claude", "deepseek"] = Field(
        default="gpt",
        validation_alias=AliasChoices("ALPHA_AI_MODEL_FAMILY", "AI_MODEL_FAMILY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com",
        validation_alias=AliasChoices("ALPHA_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    openai_model: str = Field(
        default="gpt-5.4-mini",
        validation_alias=AliasChoices("ALPHA_OPENAI_MODEL", "OPENAI_MODEL"),
    )
    finnhub_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALPHA_FINNHUB_API_KEY", "FINNHUB_API_KEY"),
    )
    polygon_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALPHA_POLYGON_API_KEY", "POLYGON_API_KEY"),
    )
    request_timeout_seconds: float = Field(
        default=10,
        validation_alias=AliasChoices("ALPHA_REQUEST_TIMEOUT_SECONDS", "REQUEST_TIMEOUT_SECONDS"),
    )
    quote_cache_ttl_seconds: int = Field(
        default=2,
        validation_alias=AliasChoices("ALPHA_QUOTE_CACHE_TTL_SECONDS", "QUOTE_CACHE_TTL_SECONDS"),
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    override = getattr(get_settings, "override", None)
    if override is not None:
        return override
    settings = Settings()
    local_config = load_local_config()
    if local_config:
        return Settings.model_validate(settings.model_dump() | local_config)
    return settings
