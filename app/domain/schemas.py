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
    return datetime.now(UTC)


class LiquidityNeed(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class InvestmentObjective(StrEnum):
    capital_preservation = "capital_preservation"
    income = "income"
    balanced = "balanced"
    growth = "growth"


class RiskLevel(StrEnum):
    conservative = "conservative"
    balanced = "balanced"
    growth = "growth"
    aggressive = "aggressive"


class Position(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    quantity: float = Field(ge=0)
    average_cost: float | None = Field(default=None, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class InvestorProfile(BaseModel):
    age: int = Field(ge=18, le=100)
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

    @field_validator("risk_answers")
    @classmethod
    def validate_risk_answers(cls, value: list[int]) -> list[int]:
        for answer in value:
            if answer < 1 or answer > 5:
                raise ValueError("risk_answers must be integers from 1 to 5")
        return value


class InvestmentPlanRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str = Field(min_length=1)
    profile: InvestorProfile
    symbols: list[str] = Field(default_factory=list)
    include_acp_trace: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            symbol = item.strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)
        return normalized


class QuoteSnapshot(BaseModel):
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
    risk_score: float = Field(ge=0, le=100)
    risk_level: RiskLevel
    max_equity_pct: float = Field(ge=0, le=100)
    max_single_stock_pct: float = Field(ge=0, le=100)
    rationale: list[str]


class AllocationBucket(BaseModel):
    asset_class: str
    instrument: str
    target_weight_pct: float = Field(ge=0, le=100)
    target_amount: float = Field(ge=0)
    rationale: str


class AllocationPlan(BaseModel):
    buckets: list[AllocationBucket]
    rebalance_frequency: str
    notes: list[str]

    @model_validator(mode="after")
    def validate_total_weight(self) -> "AllocationPlan":
        total = round(sum(bucket.target_weight_pct for bucket in self.buckets), 2)
        if total != 100:
            raise ValueError(f"allocation weights must sum to 100, got {total}")
        return self


class ProjectionPoint(BaseModel):
    years: int
    expected_value: float
    downside_value: float
    upside_value: float


class ReturnAnalysis(BaseModel):
    expected_annual_return_pct: float
    expected_annual_volatility_pct: float
    projections: list[ProjectionPoint]
    quote_summary: list[str]


class ComplianceReview(BaseModel):
    warnings: list[str]
    suitability_notes: list[str]
    guardrails: list[str]
    requires_human_review: bool


class AIAdvisorReview(BaseModel):
    provider: str
    model: str | None = None
    is_model_generated: bool
    summary: str
    key_insights: list[str]
    action_items: list[str]
    limitations: list[str]


class InvestmentPlanResponse(BaseModel):
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
    market_data_provider: Literal["hybrid", "finnhub", "polygon", "eastmoney", "mock"]
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
    local_config_path: str
    ai_agents: dict[str, AgentAISettingsResponse]


class AgentAISettingsUpdate(BaseModel):
    ai_advisor_provider: AIProvider | None = None
    ai_model_family: AIModelFamily | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False

    @field_validator("ai_advisor_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        return normalize_ai_provider(value)

    @field_validator("ai_model_family", mode="before")
    @classmethod
    def normalize_model_family(cls, value: object) -> object:
        return normalize_ai_model_family(value)


class RuntimeSettingsUpdate(BaseModel):
    market_data_provider: Literal["hybrid", "finnhub", "polygon", "eastmoney", "mock"] | None = None
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
