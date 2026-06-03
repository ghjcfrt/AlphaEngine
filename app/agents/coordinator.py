from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage, ACPPart
from app.agents.ai_advisor import AIAdvisorAgent
from app.agents.allocation import AssetAllocationAgent
from app.agents.base import BaseAgent
from app.agents.compliance import ComplianceAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.domain.schemas import (
    AIAdvisorReview,
    AllocationPlan,
    ComplianceReview,
    InvestmentPlanRequest,
    InvestmentPlanResponse,
    QuoteSnapshot,
    ReturnAnalysis,
    RiskAssessment,
)


class AdviceCoordinatorAgent:
    """总控编排 Agent。

    它本身不做投资判断，而是负责生成 trace_id、按业务顺序调用专业 Agent，
    并把每次 request/result 都写入 ACP bus，形成可追踪的协作链路。
    """

    agent_id = "advice-coordinator-agent"

    def __init__(
        self,
        bus: InMemoryACPBus,
        risk_agent: RiskAssessmentAgent,
        allocation_agent: AssetAllocationAgent,
        market_agent: MarketDataAgent,
        return_agent: ReturnAnalysisAgent,
        compliance_agent: ComplianceAgent,
        ai_advisor_agent: AIAdvisorAgent,
    ) -> None:
        self.bus = bus
        self.risk_agent = risk_agent
        self.allocation_agent = allocation_agent
        self.market_agent = market_agent
        self.return_agent = return_agent
        self.compliance_agent = compliance_agent
        self.ai_advisor_agent = ai_advisor_agent

    async def create_plan(self, request: InvestmentPlanRequest) -> InvestmentPlanResponse:
        # trace_id 代表一次完整计划生成，与外部 request_id 分开，方便审计 Agent 流程。
        trace_id = str(uuid4())
        profile_payload = request.profile.model_dump(mode="json")

        # 1. 风险画像：先确定风险等级和权益/单只股票上限。
        risk_assessment: RiskAssessment = await self._invoke(
            self.risk_agent,
            trace_id,
            "risk.assess",
            {"profile": profile_payload},
        )

        # 2. 资产配置：在风险约束内生成目标权重和目标金额。
        allocation: AllocationPlan = await self._invoke(
            self.allocation_agent,
            trace_id,
            "allocation.build",
            {
                "risk_assessment": risk_assessment.model_dump(mode="json"),
                "initial_capital": request.profile.initial_capital,
                "investment_objective": request.profile.investment_objective.value,
            },
        )

        # 3. 行情快照：关注标的与已有持仓标的会合并去重后查询。
        symbols = self._quote_symbols(request)
        quotes: list[QuoteSnapshot] = await self._invoke(
            self.market_agent,
            trace_id,
            "market.quotes",
            {"symbols": symbols},
        )

        # 4. 收益情景：基于配置权重、资产假设和行情上下文生成多期限估算。
        return_analysis: ReturnAnalysis = await self._invoke(
            self.return_agent,
            trace_id,
            "returns.analyze",
            {
                "allocation": allocation.model_dump(mode="json"),
                "quotes": [quote.model_dump(mode="json") for quote in quotes],
                "initial_capital": request.profile.initial_capital,
            },
        )

        # 5. 合规复核：结合画像、风险、配置和收益结果检查红线与披露完整性。
        compliance_review: ComplianceReview = await self._invoke(
            self.compliance_agent,
            trace_id,
            "compliance.review",
            {
                "profile": profile_payload,
                "risk_assessment": risk_assessment.model_dump(mode="json"),
                "allocation": allocation.model_dump(mode="json"),
                "return_analysis": return_analysis.model_dump(mode="json"),
            },
        )

        # 6. 中文解读：最后汇总所有结构化结果，生成给用户看的摘要与行动项。
        ai_review: AIAdvisorReview = await self._invoke(
            self.ai_advisor_agent,
            trace_id,
            "ai.review",
            {
                "profile": profile_payload,
                "risk_assessment": risk_assessment.model_dump(mode="json"),
                "allocation": allocation.model_dump(mode="json"),
                "quotes": [quote.model_dump(mode="json") for quote in quotes],
                "return_analysis": return_analysis.model_dump(mode="json"),
                "compliance_review": compliance_review.model_dump(mode="json"),
            },
        )

        # trace 可能较长，默认不返回；前端勾选“返回 trace”时再附带。
        acp_trace = await self.bus.list_trace(trace_id) if request.include_acp_trace else None
        return InvestmentPlanResponse(
            trace_id=trace_id,
            request_id=request.request_id,
            user_id=request.user_id,
            risk_assessment=risk_assessment,
            allocation=allocation,
            quotes=quotes,
            return_analysis=return_analysis,
            compliance_review=compliance_review,
            ai_review=ai_review,
            acp_trace=acp_trace,
        )

    async def _invoke(
        self,
        agent: BaseAgent,
        trace_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> Any:
        """调用单个 Agent，并在调用前后记录 ACP 消息。"""

        request_message = ACPMessage(
            trace_id=trace_id,
            sender=self.agent_id,
            receiver=agent.agent_id,
            action=action,
            parts=[ACPPart(content=payload)],
        )
        await self.bus.publish(request_message)
        result = await agent.handle(request_message, self.bus)
        # Pydantic 对象需要转换成 JSON 友好结构，才能完整写入 trace。
        result_payload = self._jsonable(result)
        response_message = ACPMessage(
            trace_id=trace_id,
            sender=agent.agent_id,
            receiver=self.agent_id,
            action=f"{action}.result",
            parts=[ACPPart(content=result_payload)],
        )
        await self.bus.publish(response_message)
        return result

    @staticmethod
    def _jsonable(value: Any) -> Any:
        """递归转换为可被 ACPMessage 序列化的普通 JSON 数据。"""

        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [AdviceCoordinatorAgent._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: AdviceCoordinatorAgent._jsonable(item) for key, item in value.items()}
        return value

    @staticmethod
    def _quote_symbols(request: InvestmentPlanRequest) -> list[str]:
        """合并用户关注标的和已有持仓标的，并保留首次出现的顺序。"""

        symbols: list[str] = []
        for symbol in request.symbols:
            if symbol not in symbols:
                symbols.append(symbol)
        for position in request.profile.current_positions:
            if position.symbol not in symbols:
                symbols.append(position.symbol)
        return symbols
