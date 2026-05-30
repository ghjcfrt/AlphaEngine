from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.acp.message import ACPMessage


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
    acp_trace: list[ACPMessage] | None = None
