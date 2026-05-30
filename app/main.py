from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.acp.bus import InMemoryACPBus
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.coordinator import AdviceCoordinatorAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.services.market_data import build_market_data_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    market_service = build_market_data_service(settings)
    bus = InMemoryACPBus()

    market_agent = MarketDataAgent(market_service)
    app.state.settings = settings
    app.state.acp_bus = bus
    app.state.market_service = market_service
    app.state.agents = [
        RiskAssessmentAgent(),
        AssetAllocationAgent(),
        market_agent,
        ReturnAnalysisAgent(),
        ComplianceAgent(),
    ]
    app.state.coordinator = AdviceCoordinatorAgent(
        bus=bus,
        risk_agent=app.state.agents[0],
        allocation_agent=app.state.agents[1],
        market_agent=market_agent,
        return_agent=app.state.agents[3],
        compliance_agent=app.state.agents[4],
    )

    try:
        yield
    finally:
        await market_service.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is not None:
        get_settings.cache_clear()
        get_settings.override = settings

    app = FastAPI(
        title="AlphaEngine AI Investment Advisor",
        version="0.1.0",
        description="使用 ACP 风格 trace 的多智能体投资规划后端。",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
