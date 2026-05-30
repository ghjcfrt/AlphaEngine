from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AlphaEngine"
    market_data_provider: Literal["hybrid", "finnhub", "polygon", "eastmoney", "mock"] = Field(
        default="hybrid",
        validation_alias=AliasChoices("ALPHA_MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER"),
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    override = getattr(get_settings, "override", None)
    if override is not None:
        return override
    return Settings()
