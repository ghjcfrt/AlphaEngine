from math import sqrt
from typing import Any

import httpx
from pydantic import ValidationError

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import AllocationPlan, ProjectionPoint, QuoteSnapshot, ReturnAnalysis
from app.services.ai_advisor import AIAdvisorError, AIAdvisorJSONService, describe_ai_error


class ReturnAnalysisAgent(BaseAgent):
    agent_id = "return-analysis-agent"
    description = "由模型复核收益情景，并以量化假设作为测算基线。"
    capabilities = ["ai_return_projection", "scenario_analysis", "quote_context"]

    def __init__(self, ai_advisor_service: AIAdvisorJSONService | None = None) -> None:
        self.ai_advisor_service = ai_advisor_service

    _return_assumptions = {
        "US equity ETF": (0.07, 0.16),
        "Global equity ETF": (0.065, 0.18),
        "Bond ETF": (0.035, 0.06),
        "Treasury bill ETF": (0.02, 0.01),
        "Gold ETF": (0.045, 0.12),
    }

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> ReturnAnalysis:
        payload = message.first_json()
        baseline = self._rule_analysis(payload)
        if not self.ai_advisor_service or not self.ai_advisor_service.is_model_generated:
            return baseline
        try:
            generated = await self.ai_advisor_service.generate_json(
                task_name="return_analysis",
                system_instructions=(
                    "你是收益情景分析 Agent。必须基于配置、行情、初始本金和规则基线输出 JSON。"
                    "不得承诺收益，必须保留不确定性。"
                ),
                user_prompt=(
                    "请复核预期收益、波动率和多期限情景。"
                    "如果调整假设，需在 quote_summary 中说明限制。"
                ),
                schema=ReturnAnalysis.model_json_schema(),
                context={
                    "allocation": payload["allocation"],
                    "quotes": payload.get("quotes", []),
                    "initial_capital": payload["initial_capital"],
                    "baseline": baseline.model_dump(mode="json"),
                },
            )
            analysis = ReturnAnalysis.model_validate(generated)
            analysis.quote_summary.append(
                f"AI协作: {self.ai_advisor_service.provider_name} 已复核收益情景。"
            )
            return analysis
        except (AIAdvisorError, httpx.HTTPError, ValidationError, ValueError) as exc:
            baseline.quote_summary.append(
                f"AI协作失败，已回退规则基线：{describe_ai_error(exc)}"
            )
            return baseline

    @staticmethod
    def _rule_analysis(payload: dict[str, Any]) -> ReturnAnalysis:
        allocation = AllocationPlan.model_validate(payload["allocation"])
        quotes = [QuoteSnapshot.model_validate(item) for item in payload.get("quotes", [])]
        initial_capital = float(payload["initial_capital"])

        expected_return = 0.0
        variance = 0.0
        for bucket in allocation.buckets:
            weight = bucket.target_weight_pct / 100
            asset_return, asset_volatility = ReturnAnalysisAgent._return_assumptions.get(
                bucket.asset_class, (0.04, 0.10)
            )
            expected_return += weight * asset_return
            variance += (weight * asset_volatility) ** 2

        volatility = sqrt(variance)
        downside_return = max(expected_return - 1.5 * volatility, -0.45)
        upside_return = expected_return + volatility

        projections = [
            ProjectionPoint(
                years=years,
                expected_value=round(initial_capital * (1 + expected_return) ** years, 2),
                downside_value=round(initial_capital * (1 + downside_return) ** years, 2),
                upside_value=round(initial_capital * (1 + upside_return) ** years, 2),
            )
            for years in [1, 3, 5, 10]
        ]

        quote_summary = [
            f"{quote.symbol}：当前价格 {quote.current_price:.2f}，"
            f"更新时间 {quote.updated_at.isoformat()}，来源 {quote.source}。"
            for quote in quotes
        ]

        return ReturnAnalysis(
            expected_annual_return_pct=round(expected_return * 100, 2),
            expected_annual_volatility_pct=round(volatility * 100, 2),
            projections=projections,
            quote_summary=quote_summary,
        )
