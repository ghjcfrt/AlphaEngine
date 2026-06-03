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
from app.services.ai_advisor import (
    AIAdvisorService,
    build_ai_advisor_service,
    clear_ai_failure_cache,
)
from app.services.market_data import build_market_data_service


async def configure_runtime(app: FastAPI, settings: Settings, close_existing: bool = False) -> None:
    """根据当前配置重新组装运行时依赖。

    app.state 是 FastAPI 推荐的进程内共享状态容器。这里集中存放：
    settings、ACP 消息总线、行情服务、每个 AI Agent 的模型服务，以及总控 Agent。
    """

    # 设置页保存配置后会调用本函数并传 close_existing=True。
    # 先保留旧实例，等新实例成功创建并挂到 app.state 后再关闭，减少半更新状态。
    old_market_service = getattr(app.state, "market_service", None)
    old_ai_advisor_services = getattr(app.state, "ai_advisor_services", None)

    # AI 调用失败会进入短暂冷却。配置变更通常意味着用户修复了 Key/URL/模型名，
    # 因此重新装配时主动清空失败缓存，让下一次调用立即尝试新配置。
    clear_ai_failure_cache()
    market_service = build_market_data_service(settings)
    # 每个专业 Agent 都可以单独配置模型；没有单独配置时会继承全局 AI 设置。
    ai_advisor_services = {
        agent_key: build_ai_advisor_service(settings, agent_key) for agent_key in AI_AGENT_LABELS
    }
    # ACP bus 保存 trace 消息。配置热更新不重建 bus，便于继续查看旧 trace。
    bus = getattr(app.state, "acp_bus", None) or InMemoryACPBus()

    # MarketDataAgent 和 AIAdvisorAgent 被后续 coordinator 明确引用，所以保留变量。
    market_agent = MarketDataAgent(market_service)
    ai_advisor_agent = AIAdvisorAgent(ai_advisor_services["ai_advisor"])
    # agents 列表同时用于 /api/v1/agents 展示，因此顺序对应业务编排顺序。
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
        # coordinator 不自己创建依赖，便于测试替换和运行时热更新。
        bus=bus,
        risk_agent=agents[0],
        allocation_agent=agents[1],
        market_agent=market_agent,
        return_agent=agents[3],
        compliance_agent=agents[4],
        ai_advisor_agent=ai_advisor_agent,
    )

    if close_existing:
        # 新配置已经挂载完毕，此时再释放旧 provider 的 httpx 连接。
        if old_market_service is not None:
            await old_market_service.close()
        await _close_ai_services(old_ai_advisor_services)


async def close_runtime(app: FastAPI) -> None:
    """应用退出时释放所有可能持有网络连接的服务。"""

    market_service = getattr(app.state, "market_service", None)
    ai_advisor_services = getattr(app.state, "ai_advisor_services", None)
    if market_service is not None:
        await market_service.close()
    await _close_ai_services(ai_advisor_services)


async def _close_ai_services(services: dict[str, AIAdvisorService] | None) -> None:
    if not services:
        return
    # 多个 Agent 未来可能复用同一个 service 实例；按 id 去重可避免重复关闭。
    closed: set[int] = set()
    for service in services.values():
        service_id = id(service)
        if service_id in closed:
            continue
        closed.add(service_id)
        await service.close()
