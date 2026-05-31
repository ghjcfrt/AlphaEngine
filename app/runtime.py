from fastapi import FastAPI

from app.acp.bus import InMemoryACPBus
from app.agents.ai_advisor import AIAdvisorAgent
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.coordinator import AdviceCoordinatorAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.core.config import Settings
from app.services.ai_advisor import build_ai_advisor_service
from app.services.market_data import build_market_data_service


async def configure_runtime(app: FastAPI, settings: Settings, close_existing: bool = False) -> None:
    old_market_service = getattr(app.state, "market_service", None)
    old_ai_advisor_service = getattr(app.state, "ai_advisor_service", None)

    market_service = build_market_data_service(settings)
    ai_advisor_service = build_ai_advisor_service(settings)
    bus = getattr(app.state, "acp_bus", None) or InMemoryACPBus()

    market_agent = MarketDataAgent(market_service)
    ai_advisor_agent = AIAdvisorAgent(ai_advisor_service)
    agents = [
        RiskAssessmentAgent(ai_advisor_service),
        AssetAllocationAgent(ai_advisor_service),
        market_agent,
        ReturnAnalysisAgent(ai_advisor_service),
        ComplianceAgent(ai_advisor_service),
        ai_advisor_agent,
    ]

    app.state.settings = settings
    app.state.acp_bus = bus
    app.state.market_service = market_service
    app.state.ai_advisor_service = ai_advisor_service
    app.state.agents = agents
    app.state.coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=agents[0],
        allocation_agent=agents[1],
        market_agent=market_agent,
        return_agent=agents[3],
        compliance_agent=agents[4],
        ai_advisor_agent=ai_advisor_agent,
    )

    if close_existing:
        if old_market_service is not None:
            await old_market_service.close()
        if old_ai_advisor_service is not None:
            await old_ai_advisor_service.close()


async def close_runtime(app: FastAPI) -> None:
    market_service = getattr(app.state, "market_service", None)
    ai_advisor_service = getattr(app.state, "ai_advisor_service", None)
    if market_service is not None:
        await market_service.close()
    if ai_advisor_service is not None:
        await ai_advisor_service.close()
