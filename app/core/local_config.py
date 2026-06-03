import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_PATH = ROOT_DIR / ".alphaengine.local.json"

# 前端设置弹窗会写本地 JSON。白名单可以防止手工编辑文件时塞入未知键，
# 也避免把未来不该落盘的运行时对象误写进去。
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
    """读取前端保存的本地配置。

    读取失败时返回空配置，让应用仍可启动；配置页保存后会覆盖为合法 JSON。
    """

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
        # 兼容早期 mock 行情配置；当前默认行为改为 hybrid。
        config["market_data_provider"] = "hybrid"
    return config


def save_local_config(config: dict[str, Any]) -> None:
    """保存清理后的本地配置。

    空字符串和 None 不落盘，避免配置文件里保留看似有效但不可用的占位字段。
    """

    clean_config = {
        key: value
        for key, value in config.items()
        if key in ALLOWED_KEYS and value is not None and value != ""
    }
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(clean_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
