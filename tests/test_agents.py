from datetime import UTC, datetime

import pytest

from app.acp.bus import InMemoryACPBus
from app.agents.ai_advisor import AIAdvisorAgent
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.coordinator import AdviceCoordinatorAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.domain.schemas import AIAdvisorReview, InvestmentPlanRequest, QuoteSnapshot
from app.services.ai_advisor import AIAdvisorError
from app.services.market_data import MarketDataService


class StaticReviewService:
    provider_name = "OpenAI"
    provider_model = "gpt-test"

    async def create_review(self, context: dict[str, object]) -> AIAdvisorReview:
        return AIAdvisorReview(
            provider=self.provider_name,
            model=self.provider_model,
            is_model_generated=True,
            summary="模型解读",
            key_insights=["风险画像、配置和行情已由模型汇总。"],
            action_items=["复核客户适当性材料。"],
            limitations=["不构成投资建议。"],
        )


class FailingReviewService:
    provider_name = "OpenAI Compatible"
    provider_model = "gpt-test"

    async def create_review(self, context: dict[str, object]):
        raise AIAdvisorError("模型接口返回 401 Unauthorized。")


class StaticMarketProvider:
    name = "static"

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol.upper(),
            current_price=100,
            updated_at=datetime.now(UTC),
            source=self.name,
            is_realtime=False,
        )

    async def close(self) -> None:
        return None


def _request(include_trace: bool = False) -> InvestmentPlanRequest:
    return InvestmentPlanRequest.model_validate(
        {
            "user_id": "user-1",
            "profile": {
                "age": 32,
                "annual_income": 300000,
                "net_worth": 800000,
                "initial_capital": 200000,
                "investment_horizon_years": 8,
                "liquidity_need": "medium",
                "investment_objective": "growth",
                "risk_answers": [4, 4, 3, 5, 4],
            },
            "symbols": ["AAPL", "MSFT"],
            "include_acp_trace": include_trace,
        }
    )


def test_investor_profile_amount_currency_defaults_to_rmb() -> None:
    request = _request()

    assert request.profile.amount_currency == "CNY"

    request = InvestmentPlanRequest.model_validate(
        {
            "user_id": "user-1",
            "profile": {
                "age": 32,
                "amount_currency": "rmb",
                "annual_income": 300000,
                "net_worth": 800000,
                "initial_capital": 200000,
                "investment_horizon_years": 8,
                "risk_answers": [4, 4, 3, 5, 4],
            },
        }
    )

    assert request.profile.amount_currency == "CNY"


@pytest.mark.asyncio
async def test_coordinator_builds_plan_with_acp_trace() -> None:
    bus = InMemoryACPBus()
    coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=RiskAssessmentAgent(),
        allocation_agent=AssetAllocationAgent(),
        market_agent=MarketDataAgent(MarketDataService(StaticMarketProvider())),
        return_agent=ReturnAnalysisAgent(),
        compliance_agent=ComplianceAgent(),
        ai_advisor_agent=AIAdvisorAgent(StaticReviewService()),
    )

    response = await coordinator.create_plan(_request(include_trace=True))

    assert response.risk_assessment.risk_score > 0
    assert response.allocation.buckets
    assert round(sum(bucket.target_weight_pct for bucket in response.allocation.buckets), 2) == 100
    assert {quote.symbol for quote in response.quotes} == {"AAPL", "MSFT"}
    assert response.return_analysis.projections
    assert response.compliance_review.warnings
    assert response.ai_review.provider == "OpenAI"
    assert response.ai_review.is_model_generated is True
    assert response.acp_trace is not None
    assert len(response.acp_trace) == 12


@pytest.mark.asyncio
async def test_coordinator_builds_plan_without_symbols_or_positions() -> None:
    bus = InMemoryACPBus()
    coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=RiskAssessmentAgent(),
        allocation_agent=AssetAllocationAgent(),
        market_agent=MarketDataAgent(MarketDataService(StaticMarketProvider())),
        return_agent=ReturnAnalysisAgent(),
        compliance_agent=ComplianceAgent(),
        ai_advisor_agent=AIAdvisorAgent(StaticReviewService()),
    )
    request = _request()
    request.symbols = []
    request.profile.current_positions = []

    response = await coordinator.create_plan(request)

    assert response.allocation.buckets
    assert response.quotes == []
    assert response.return_analysis.quote_summary == []
    assert response.ai_review.provider == "OpenAI"


@pytest.mark.asyncio
async def test_coordinator_returns_plan_with_ai_error_review_when_final_review_fails() -> None:
    bus = InMemoryACPBus()
    coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=RiskAssessmentAgent(),
        allocation_agent=AssetAllocationAgent(),
        market_agent=MarketDataAgent(MarketDataService(StaticMarketProvider())),
        return_agent=ReturnAnalysisAgent(),
        compliance_agent=ComplianceAgent(),
        ai_advisor_agent=AIAdvisorAgent(FailingReviewService()),
    )

    response = await coordinator.create_plan(_request())

    assert response.ai_review.provider == "OpenAI Compatible"
    assert response.ai_review.model == "gpt-test"
    assert response.ai_review.is_model_generated is False
    assert "AI 模型解读生成失败" in response.ai_review.summary
    assert any("401 Unauthorized" in item for item in response.ai_review.limitations)
