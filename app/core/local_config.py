import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_PATH = ROOT_DIR / ".alphaengine.local.json"

ALLOWED_KEYS = {
    "market_data_provider",
    "ai_advisor_provider",
    "ai_model_family",
    "ai_agents",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "finnhub_api_key",
    "polygon_api_key",
    "alpha_vantage_api_key",
    "request_timeout_seconds",
    "quote_cache_ttl_seconds",
}


def load_local_config() -> dict[str, Any]:
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    config = {key: value for key, value in payload.items() if key in ALLOWED_KEYS}
    if config.get("market_data_provider") == "mock":
        config["market_data_provider"] = "hybrid"
    return config


def save_local_config(config: dict[str, Any]) -> None:
    clean_config = {
        key: value
        for key, value in config.items()
        if key in ALLOWED_KEYS and value is not None and value != ""
    }
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(clean_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
