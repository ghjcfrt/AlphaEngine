from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.local_config import load_local_config

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"

# provider 描述“服务商/后端”，family 描述“请求协议/模型族”。
# OpenAI 兼容接口、DeepSeek 等可能共享 Chat Completions 风格，但默认端点不同。
AIProvider = Literal[
    "openai",
    "openai_compatible",
    "gemini",
    "anthropic",
    "deepseek",
    "disabled",
]
AIModelFamily = Literal["gpt", "openai_compatible", "gemini", "claude", "deepseek"]

# provider -> family 用于后端推导实际请求协议。
AI_PROVIDER_TO_FAMILY: dict[str, AIModelFamily] = {
    "openai": "gpt",
    "openai_compatible": "openai_compatible",
    "gemini": "gemini",
    "anthropic": "claude",
    "deepseek": "deepseek",
}
# family -> provider 用于前端选择模型接口后反推 provider。
AI_FAMILY_TO_PROVIDER: dict[str, AIProvider] = {
    "gpt": "openai",
    "openai_compatible": "openai_compatible",
    "gemini": "gemini",
    "claude": "anthropic",
    "deepseek": "deepseek",
}
# 兼容历史配置和用户常见输入：比如 claude 实际映射到 anthropic provider。
AI_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "gemini": "gemini",
    "google": "gemini",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "deep seek": "deepseek",
    "mock": "openai",
    "mock-ai": "openai",
    "mock ai": "openai",
    "disabled": "disabled",
    "disable": "disabled",
    "off": "disabled",
    "auto": "openai",
}
# 仅用于展示，不参与逻辑分支。
AI_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openai_compatible": "OpenAI Compatible",
    "gemini": "Gemini",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "disabled": "Disabled",
}

# 每个专业 Agent 可以单独配置模型；这些 key 也是 API payload 的稳定字段。
AI_AGENT_LABELS = {
    "risk_assessment": "RiskAssessmentAgent",
    "asset_allocation": "AssetAllocationAgent",
    "return_analysis": "ReturnAnalysisAgent",
    "compliance_review": "ComplianceAgent",
    "ai_advisor": "AIAdvisorAgent",
}


class AIModelSettings(BaseModel):
    """某个 Agent 最终使用的 AI 模型配置。"""

    ai_advisor_provider: AIProvider = "openai"
    ai_model_family: AIModelFamily = "gpt"
    openai_api_key: str | None = None
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    openai_model: str = DEFAULT_OPENAI_MODEL

    @field_validator("ai_advisor_provider", mode="before")
    @classmethod
    def normalize_ai_provider(cls, value: object) -> object:
        return normalize_ai_provider(value)

    @field_validator("ai_model_family", mode="before")
    @classmethod
    def normalize_ai_model_family(cls, value: object) -> object:
        return normalize_ai_model_family(value)

    @model_validator(mode="after")
    def align_provider_and_family(self) -> "AIModelSettings":
        # provider 和 family 是两种视角。用户只改其中一个时，这里负责补齐另一侧。
        self.ai_advisor_provider, self.ai_model_family = align_ai_provider_and_family(
            self.ai_advisor_provider,
            self.ai_model_family,
        )
        if self.ai_model_family == "openai_compatible":
            # 兼容接口不能默认指向官方 OpenAI URL/模型，否则用户容易误以为已配置。
            if self.openai_base_url.strip().rstrip("/") == DEFAULT_OPENAI_BASE_URL:
                self.openai_base_url = ""
            if self.openai_model.strip() == DEFAULT_OPENAI_MODEL:
                self.openai_model = ""
        return self


class Settings(BaseSettings):
    """应用全局配置。

    读取顺序：环境变量/.env 先生成 Settings，然后 get_settings() 再覆盖本地 JSON。
    Pydantic 的 validation_alias 让新旧环境变量名都能被识别。
    """

    app_name: str = "AlphaEngine"
    market_data_provider: Literal[
        "hybrid", "finnhub", "polygon", "alphavantage", "eastmoney"
    ] = Field(
        default="hybrid",
        validation_alias=AliasChoices("ALPHA_MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER"),
    )
    ai_advisor_provider: AIProvider = Field(
        default="openai",
        validation_alias=AliasChoices("ALPHA_AI_ADVISOR_PROVIDER", "AI_ADVISOR_PROVIDER"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALPHA_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    ai_model_family: AIModelFamily = Field(
        default="gpt",
        validation_alias=AliasChoices("ALPHA_AI_MODEL_FAMILY", "AI_MODEL_FAMILY"),
    )
    openai_base_url: str = Field(
        default=DEFAULT_OPENAI_BASE_URL,
        validation_alias=AliasChoices("ALPHA_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    openai_model: str = Field(
        default=DEFAULT_OPENAI_MODEL,
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
    alpha_vantage_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ALPHA_VANTAGE_API_KEY", "ALPHAVANTAGE_API_KEY"),
    )
    request_timeout_seconds: float = Field(
        default=60,
        validation_alias=AliasChoices("ALPHA_REQUEST_TIMEOUT_SECONDS", "REQUEST_TIMEOUT_SECONDS"),
    )
    quote_cache_ttl_seconds: int = Field(
        default=2,
        validation_alias=AliasChoices("ALPHA_QUOTE_CACHE_TTL_SECONDS", "QUOTE_CACHE_TTL_SECONDS"),
    )
    ai_agents: dict[str, dict[str, Any]] = Field(default_factory=dict)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @field_validator("ai_advisor_provider", mode="before")
    @classmethod
    def normalize_ai_provider(cls, value: object) -> object:
        return normalize_ai_provider(value)

    @field_validator("ai_model_family", mode="before")
    @classmethod
    def normalize_ai_model_family(cls, value: object) -> object:
        return normalize_ai_model_family(value)

    @model_validator(mode="after")
    def align_provider_and_family(self) -> "Settings":
        self.ai_advisor_provider, self.ai_model_family = align_ai_provider_and_family(
            self.ai_advisor_provider,
            self.ai_model_family,
        )
        return self


def resolve_ai_model_settings(settings: Settings, agent_key: str | None = None) -> AIModelSettings:
    """解析某个 Agent 的最终模型配置。

    没有 agent_key 时返回全局配置；有 agent_key 时用 settings.ai_agents 中的局部配置
    覆盖全局配置，从而支持“风险 Agent 用 Gemini，配置 Agent 用 Claude”等组合。
    """

    base = AIModelSettings(
        ai_advisor_provider=settings.ai_advisor_provider,
        ai_model_family=settings.ai_model_family,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
        openai_model=settings.openai_model,
    )
    if not agent_key:
        return base
    override = settings.ai_agents.get(agent_key)
    if not isinstance(override, dict):
        return base
    return AIModelSettings.model_validate(base.model_dump() | override)


def normalize_ai_provider(value: object) -> object:
    """把用户输入的 provider 别名归一到内部枚举值。"""

    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("_", " ")
    return AI_PROVIDER_ALIASES.get(normalized, value.strip())


def normalize_ai_model_family(value: object) -> object:
    """把用户输入的模型族/协议别名归一到内部 family。"""

    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized == "anthropic":
        return "claude"
    if normalized == "openai":
        return "gpt"
    return normalized


def align_ai_provider_and_family(
    provider: AIProvider,
    family: AIModelFamily,
) -> tuple[AIProvider, AIModelFamily]:
    """让 provider 与 family 保持一致。

    provider=openai 时允许 family 决定最终 provider，因为前端旧版本可能只传 family；
    provider 明确为 gemini/anthropic/deepseek 时，则以 provider 为准反推 family。
    """

    if provider == "openai":
        return AI_FAMILY_TO_PROVIDER.get(family, "openai"), family
    if provider in AI_PROVIDER_TO_FAMILY:
        return provider, AI_PROVIDER_TO_FAMILY[provider]
    return provider, family


_settings_override: Settings | None = None


def set_settings_override(settings: Settings | None) -> None:
    """测试辅助：注入 Settings，绕过真实环境变量和本地配置文件。"""

    global _settings_override
    _settings_override = settings
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    """读取并缓存运行配置。

    缓存能避免每个请求反复解析 .env 和 JSON 文件；设置更新后必须 cache_clear()。
    """

    if _settings_override is not None:
        return _settings_override
    settings = Settings()
    local_config = load_local_config()
    if local_config:
        # 本地配置来自前端配置弹窗，优先级高于 .env，便于运行时调整。
        return Settings.model_validate(settings.model_dump() | local_config)
    return settings
