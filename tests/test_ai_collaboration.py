from typing import Any

import httpx
import pytest

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage, ACPPart
from app.agents.allocation import AssetAllocationAgent
from app.agents.compliance import ComplianceAgent
from app.agents.returns import ReturnAnalysisAgent
from app.agents.risk import RiskAssessmentAgent


class FakeModelService:
    is_model_generated = True
    provider_name = "fake-model"

    def __init__(self) -> None:
        self.tasks: list[str] = []

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.tasks.append(task_name)
        return context["baseline"]


class FailingModelService:
    is_model_generated = True
    provider_name = "failing-model"

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        request = httpx.Request("POST", "https://model.example/v1/chat/completions")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)


PROFILE = {
    "age": 35,
    "annual_income": 300000,
    "net_worth": 900000,
    "initial_capital": 200000,
    "investment_horizon_years": 10,
    "liquidity_need": "medium",
    "investment_objective": "growth",
    "risk_answers": [4, 4, 4, 5, 4],
    "current_positions": [],
}


@pytest.mark.asyncio
async def test_domain_agents_invoke_model_for_ai_collaboration() -> None:
    model_service = FakeModelService()
    bus = InMemoryACPBus()

    risk = await RiskAssessmentAgent(model_service).handle(
        _message("risk.assess", {"profile": PROFILE}),
        bus,
    )
    allocation = await AssetAllocationAgent(model_service).handle(
        _message(
            "allocation.build",
            {
                "risk_assessment": risk.model_dump(mode="json"),
                "initial_capital": PROFILE["initial_capital"],
                "investment_objective": PROFILE["investment_objective"],
            },
        ),
        bus,
    )
    returns = await ReturnAnalysisAgent(model_service).handle(
        _message(
            "returns.analyze",
            {
                "allocation": allocation.model_dump(mode="json"),
                "quotes": [],
                "initial_capital": PROFILE["initial_capital"],
            },
        ),
        bus,
    )
    compliance = await ComplianceAgent(model_service).handle(
        _message(
            "compliance.review",
            {
                "profile": PROFILE,
                "risk_assessment": risk.model_dump(mode="json"),
                "allocation": allocation.model_dump(mode="json"),
                "return_analysis": returns.model_dump(mode="json"),
            },
        ),
        bus,
    )

    assert model_service.tasks == [
        "risk_assessment",
        "allocation_plan",
        "return_analysis",
        "compliance_review",
    ]
    assert any("AI协作" in item for item in risk.rationale)
    assert any("AI协作" in item for item in allocation.notes)
    assert any("AI协作" in item for item in returns.quote_summary)
    assert any("AI协作" in item for item in compliance.warnings)


@pytest.mark.asyncio
async def test_domain_agents_fallback_on_model_http_errors() -> None:
    bus = InMemoryACPBus()

    risk = await RiskAssessmentAgent(FailingModelService()).handle(
        _message("risk.assess", {"profile": PROFILE}),
        bus,
    )

    assert risk.risk_score > 0
    assert any("AI协作失败" in item for item in risk.rationale)
    assert any("401 Unauthorized" in item for item in risk.rationale)


def _message(action: str, payload: dict[str, Any]) -> ACPMessage:
    return ACPMessage(
        trace_id="trace",
        sender="test",
        receiver="agent",
        action=action,
        parts=[ACPPart(content=payload)],
    )
