import pytest

from app.acp.bus import InMemoryACPBus
from app.agents.ai_advisor import AIAdvisorAgent
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.coordinator import AdviceCoordinatorAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.domain.schemas import InvestmentPlanRequest
from app.services.ai_advisor import AIAdvisorService, MockAIAdvisorProvider
from app.services.market_data import MarketDataService, MockMarketDataProvider


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


@pytest.mark.asyncio
async def test_coordinator_builds_plan_with_acp_trace() -> None:
    bus = InMemoryACPBus()
    coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=RiskAssessmentAgent(),
        allocation_agent=AssetAllocationAgent(),
        market_agent=MarketDataAgent(MarketDataService(MockMarketDataProvider())),
        return_agent=ReturnAnalysisAgent(),
        compliance_agent=ComplianceAgent(),
        ai_advisor_agent=AIAdvisorAgent(AIAdvisorService(MockAIAdvisorProvider())),
    )

    response = await coordinator.create_plan(_request(include_trace=True))

    assert response.risk_assessment.risk_score > 0
    assert response.allocation.buckets
    assert round(sum(bucket.target_weight_pct for bucket in response.allocation.buckets), 2) == 100
    assert {quote.symbol for quote in response.quotes} >= {"AAPL", "MSFT", "VTI", "BND"}
    assert response.return_analysis.projections
    assert response.compliance_review.warnings
    assert response.ai_review.provider == "Mock AI"
    assert response.ai_review.is_model_generated is False
    assert response.acp_trace is not None
    assert len(response.acp_trace) == 12
