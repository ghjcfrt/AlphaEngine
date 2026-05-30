from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage, ACPPart
from app.agents.allocation import AssetAllocationAgent
from app.agents.base import BaseAgent
from app.agents.compliance import ComplianceAgent
from app.agents.market import MarketDataAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent
from app.domain.schemas import (
    AllocationPlan,
    ComplianceReview,
    InvestmentPlanRequest,
    InvestmentPlanResponse,
    QuoteSnapshot,
    ReturnAnalysis,
    RiskAssessment,
)


class AdviceCoordinatorAgent:
    agent_id = "advice-coordinator-agent"

    def __init__(
        self,
        bus: InMemoryACPBus,
        risk_agent: RiskAssessmentAgent,
        allocation_agent: AssetAllocationAgent,
        market_agent: MarketDataAgent,
        return_agent: ReturnAnalysisAgent,
        compliance_agent: ComplianceAgent,
    ) -> None:
        self.bus = bus
        self.risk_agent = risk_agent
        self.allocation_agent = allocation_agent
        self.market_agent = market_agent
        self.return_agent = return_agent
        self.compliance_agent = compliance_agent

    async def create_plan(self, request: InvestmentPlanRequest) -> InvestmentPlanResponse:
        trace_id = str(uuid4())
        profile_payload = request.profile.model_dump(mode="json")

        risk_assessment: RiskAssessment = await self._invoke(
            self.risk_agent,
            trace_id,
            "risk.assess",
            {"profile": profile_payload},
        )

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

        symbols = self._quote_symbols(request, allocation)
        quotes: list[QuoteSnapshot] = await self._invoke(
            self.market_agent,
            trace_id,
            "market.quotes",
            {"symbols": symbols},
        )

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
            acp_trace=acp_trace,
        )

    async def _invoke(
        self,
        agent: BaseAgent,
        trace_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> Any:
        request_message = ACPMessage(
            trace_id=trace_id,
            sender=self.agent_id,
            receiver=agent.agent_id,
            action=action,
            parts=[ACPPart(content=payload)],
        )
        await self.bus.publish(request_message)
        result = await agent.handle(request_message, self.bus)
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
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [AdviceCoordinatorAgent._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: AdviceCoordinatorAgent._jsonable(item) for key, item in value.items()}
        return value

    @staticmethod
    def _quote_symbols(request: InvestmentPlanRequest, allocation: AllocationPlan) -> list[str]:
        symbols: list[str] = []
        for symbol in request.symbols:
            if symbol not in symbols:
                symbols.append(symbol)
        for position in request.profile.current_positions:
            if position.symbol not in symbols:
                symbols.append(position.symbol)
        for bucket in allocation.buckets:
            if bucket.instrument not in symbols:
                symbols.append(bucket.instrument)
        return symbols
