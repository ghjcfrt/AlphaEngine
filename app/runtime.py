from fastapi import FastAPI

from app.acp.bus import InMemoryACPBus
from app.agents.ai_advisor import AIAdvisorAgent
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.coordinator import AdviceCoordinatorAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.core.config import AI_AGENT_LABELS, Settings
from app.services.ai_advisor import AIAdvisorService, build_ai_advisor_service, clear_ai_failure_cache
from app.services.market_data import build_market_data_service


async def configure_runtime(app: FastAPI, settings: Settings, close_existing: bool = False) -> None:
    old_market_service = getattr(app.state, "market_service", None)
    old_ai_advisor_services = getattr(app.state, "ai_advisor_services", None)

    clear_ai_failure_cache()
    market_service = build_market_data_service(settings)
    ai_advisor_services = {
        agent_key: build_ai_advisor_service(settings, agent_key) for agent_key in AI_AGENT_LABELS
    }
    bus = getattr(app.state, "acp_bus", None) or InMemoryACPBus()

    market_agent = MarketDataAgent(market_service)
    ai_advisor_agent = AIAdvisorAgent(ai_advisor_services["ai_advisor"])
    agents = [
        RiskAssessmentAgent(ai_advisor_services["risk_assessment"]),
        AssetAllocationAgent(ai_advisor_services["asset_allocation"]),
        market_agent,
        ReturnAnalysisAgent(ai_advisor_services["return_analysis"]),
        ComplianceAgent(ai_advisor_services["compliance_review"]),
        ai_advisor_agent,
    ]

    app.state.settings = settings
    app.state.acp_bus = bus
    app.state.market_service = market_service
    app.state.ai_advisor_services = ai_advisor_services
    app.state.ai_advisor_service = ai_advisor_services["ai_advisor"]
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
        await _close_ai_services(old_ai_advisor_services)


async def close_runtime(app: FastAPI) -> None:
    market_service = getattr(app.state, "market_service", None)
    ai_advisor_services = getattr(app.state, "ai_advisor_services", None)
    if market_service is not None:
        await market_service.close()
    await _close_ai_services(ai_advisor_services)


async def _close_ai_services(services: dict[str, AIAdvisorService] | None) -> None:
    if not services:
        return
    closed: set[int] = set()
    for service in services.values():
        service_id = id(service)
        if service_id in closed:
            continue
        closed.add(service_id)
        await service.close()
