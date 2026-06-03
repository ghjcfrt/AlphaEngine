from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.acp.message import ACPMessage
from app.core.config import (
    AIModelFamily,
    AIProvider,
    normalize_ai_model_family,
    normalize_ai_provider,
)


def utc_now() -> datetime:
    # 统一使用 UTC，避免前端导出或测试在不同时区下出现时间歧义。
    return datetime.now(UTC)


class LiquidityNeed(StrEnum):
    """投资人对可随时取回资金的需求强弱。"""

    low = "low"
    medium = "medium"
    high = "high"


class InvestmentObjective(StrEnum):
    """投资目标会影响风险评分与资产配置模板的微调方向。"""

    capital_preservation = "capital_preservation"
    income = "income"
    balanced = "balanced"
    growth = "growth"


class RiskLevel(StrEnum):
    """规则引擎和 AI 复核都必须落到这四档风险等级之一。"""

    conservative = "conservative"
    balanced = "balanced"
    growth = "growth"
    aggressive = "aggressive"


# 金额单位只影响展示和报告，不在后端做外汇换算。
AmountCurrency = Literal["CNY", "USD", "HKD", "EUR", "JPY"]


class Position(BaseModel):
    """用户已有持仓，用于合规集中度检查和行情补充查询。"""

    symbol: str = Field(min_length=1, max_length=16)
    quantity: float = Field(ge=0)
    average_cost: float | None = Field(default=None, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        # 后续行情查询、去重和持仓集中度展示都基于统一大写代码。
        return value.strip().upper()


class InvestorProfile(BaseModel):
    """投资计划的核心输入画像。

    这里的约束是第一道输入防线：年龄、期限、本金等明显非法值会在 API 层
    被 Pydantic 拦截，避免进入 Agent 之后才出现难懂的计算错误。
    """

    age: int = Field(ge=18, le=100)
    amount_currency: AmountCurrency = "CNY"
    annual_income: float = Field(ge=0)
    net_worth: float = Field(ge=0)
    initial_capital: float = Field(gt=0)
    investment_horizon_years: int = Field(ge=1, le=50)
    liquidity_need: LiquidityNeed = LiquidityNeed.medium
    investment_objective: InvestmentObjective = InvestmentObjective.balanced
    risk_answers: list[int] = Field(
        default_factory=list,
        description="1-5 分制的风险问卷答案。",
    )
    current_positions: list[Position] = Field(default_factory=list)

    @field_validator("amount_currency", mode="before")
    @classmethod
    def normalize_amount_currency(cls, value: object) -> object:
        # 前端下拉框展示 RMB，但领域模型统一使用 ISO 4217 的 CNY。
        if value is None or value == "":
            return "CNY"
        normalized = str(value).strip().upper()
        if normalized == "RMB":
            return "CNY"
        return normalized

    @field_validator("risk_answers")
    @classmethod
    def validate_risk_answers(cls, value: list[int]) -> list[int]:
        # 风险问卷固定按 1-5 分制计算平均值，越界会扭曲风险评分。
        for answer in value:
            if answer < 1 or answer > 5:
                raise ValueError("risk_answers must be integers from 1 to 5")
        return value


class InvestmentPlanRequest(BaseModel):
    """生成投资计划的 API 请求。"""

    # request_id 用于调用方自己的幂等/审计跟踪；trace_id 会由 coordinator 另行生成。
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str = Field(min_length=1)
    profile: InvestorProfile
    symbols: list[str] = Field(default_factory=list)
    include_acp_trace: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        # 关注标的按输入顺序去重，避免相同股票被重复请求行情。
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            symbol = item.strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)
        return normalized


class QuoteSnapshot(BaseModel):
    """统一后的行情快照。

    不同 provider 返回字段差异很大，服务层会全部转换到这个模型，Agent 层只关心
    current_price、涨跌、币种、更新时间和来源。
    """

    symbol: str
    current_price: float = Field(gt=0)
    open_price: float | None = Field(default=None, ge=0)
    previous_close: float | None = Field(default=None, ge=0)
    high_price: float | None = Field(default=None, ge=0)
    low_price: float | None = Field(default=None, ge=0)
    change: float | None = None
    change_percent: float | None = None
    currency: str = "USD"
    updated_at: datetime
    source: str
    is_realtime: bool
    data_delay_seconds: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    """风险画像输出，既包含评分，也包含可执行的风险约束。"""

    risk_score: float = Field(ge=0, le=100)
    risk_level: RiskLevel
    max_equity_pct: float = Field(ge=0, le=100)
    max_single_stock_pct: float = Field(ge=0, le=100)
    rationale: list[str]


class AllocationBucket(BaseModel):
    """单个资产桶，例如宽基美股 ETF、债券 ETF 或现金等价物。"""

    asset_class: str
    instrument: str
    target_weight_pct: float = Field(ge=0, le=100)
    target_amount: float = Field(ge=0)
    rationale: str


class AllocationPlan(BaseModel):
    """完整配置方案，所有资产桶权重必须严格合计 100%。"""

    buckets: list[AllocationBucket]
    rebalance_frequency: str
    notes: list[str]

    @model_validator(mode="after")
    def validate_total_weight(self) -> "AllocationPlan":
        # 模型复核后也必须通过这一关，防止 AI 输出权重合计 99 或 101。
        total = round(sum(bucket.target_weight_pct for bucket in self.buckets), 2)
        if total != 100:
            raise ValueError(f"allocation weights must sum to 100, got {total}")
        return self


class ProjectionPoint(BaseModel):
    """某个投资期限下的下行、预期和上行情景。"""

    years: int
    expected_value: float
    downside_value: float
    upside_value: float


class ReturnAnalysis(BaseModel):
    """收益情景分析输出，注意这里是估算假设，不是收益承诺。"""

    expected_annual_return_pct: float
    expected_annual_volatility_pct: float
    projections: list[ProjectionPoint]
    quote_summary: list[str]


class ComplianceReview(BaseModel):
    """合规复核输出。

    warnings 用于风险披露，suitability_notes 用于适当性解释，guardrails 用于
    执行前必须满足的操作护栏。
    """

    warnings: list[str]
    suitability_notes: list[str]
    guardrails: list[str]
    requires_human_review: bool


class AIAdvisorReview(BaseModel):
    """总结解读 Agent 输出给前端展示的结构化中文内容。"""

    provider: str
    model: str | None = None
    is_model_generated: bool
    summary: str
    key_insights: list[str]
    action_items: list[str]
    limitations: list[str]


class InvestmentPlanResponse(BaseModel):
    """生成计划的总响应，聚合所有专业 Agent 的结果。"""

    trace_id: str
    request_id: str
    user_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    risk_assessment: RiskAssessment
    allocation: AllocationPlan
    quotes: list[QuoteSnapshot]
    return_analysis: ReturnAnalysis
    compliance_review: ComplianceReview
    ai_review: AIAdvisorReview
    acp_trace: list[ACPMessage] | None = None


class AgentAISettingsResponse(BaseModel):
    """单个 Agent 的模型配置和实际运行状态。"""

    label: str
    ai_advisor_provider: AIProvider
    ai_model_family: AIModelFamily
    ai_runtime_provider: str
    ai_runtime_model: str | None
    ai_is_model_generated: bool
    openai_base_url: str
    openai_model: str
    has_openai_api_key: bool


class RuntimeSettingsResponse(BaseModel):
    """运行时设置响应。

    所有密钥都只返回布尔状态，不返回明文，避免配置弹窗或健康检查泄露凭证。
    """

    market_data_provider: Literal["hybrid", "finnhub", "polygon", "alphavantage", "eastmoney"]
    ai_advisor_provider: AIProvider
    ai_model_family: AIModelFamily
    ai_runtime_provider: str
    ai_runtime_model: str | None
    ai_is_model_generated: bool
    openai_base_url: str
    openai_model: str
    request_timeout_seconds: float
    quote_cache_ttl_seconds: int
    has_openai_api_key: bool
    has_finnhub_api_key: bool
    has_polygon_api_key: bool
    has_alpha_vantage_api_key: bool
    local_config_path: str
    ai_agents: dict[str, AgentAISettingsResponse]


class AgentAISettingsUpdate(BaseModel):
    """单个 AI Agent 的配置更新负载。"""

    ai_advisor_provider: AIProvider | None = None
    ai_model_family: AIModelFamily | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False

    @field_validator("ai_advisor_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        # 允许用户输入 OpenAI、Claude、DeepSeek 等别名，统一转换为内部枚举。
        return normalize_ai_provider(value)

    @field_validator("ai_model_family", mode="before")
    @classmethod
    def normalize_model_family(cls, value: object) -> object:
        # family 表示接口协议，而不是品牌名；例如 anthropic 会归一为 claude。
        return normalize_ai_model_family(value)


class RuntimeSettingsUpdate(BaseModel):
    """全局运行配置更新负载。

    明文密钥字段为空时表示“不修改”，clear_xxx 字段为真才表示删除已保存密钥。
    """

    market_data_provider: Literal[
        "hybrid", "finnhub", "polygon", "alphavantage", "eastmoney"
    ] | None = None
    ai_advisor_provider: AIProvider | None = None
    ai_model_family: AIModelFamily | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False
    finnhub_api_key: str | None = None
    clear_finnhub_api_key: bool = False
    polygon_api_key: str | None = None
    clear_polygon_api_key: bool = False
    alpha_vantage_api_key: str | None = None
    clear_alpha_vantage_api_key: bool = False
    request_timeout_seconds: float | None = Field(default=None, gt=0)
    quote_cache_ttl_seconds: int | None = Field(default=None, ge=0)
    ai_agents: dict[str, AgentAISettingsUpdate] | None = None

    @field_validator("ai_advisor_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        return normalize_ai_provider(value)

    @field_validator("ai_model_family", mode="before")
    @classmethod
    def normalize_model_family(cls, value: object) -> object:
        return normalize_ai_model_family(value)
