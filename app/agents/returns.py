from math import sqrt

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import AllocationPlan, ProjectionPoint, QuoteSnapshot, ReturnAnalysis


class ReturnAnalysisAgent(BaseAgent):
    agent_id = "return-analysis-agent"
    description = "估算预期收益、风险和多期限情景。"
    capabilities = ["return_projection", "scenario_analysis", "quote_context"]

    _return_assumptions = {
        "US equity ETF": (0.07, 0.16),
        "Global equity ETF": (0.065, 0.18),
        "Bond ETF": (0.035, 0.06),
        "Treasury bill ETF": (0.02, 0.01),
        "Gold ETF": (0.045, 0.12),
    }

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> ReturnAnalysis:
        payload = message.first_json()
        allocation = AllocationPlan.model_validate(payload["allocation"])
        quotes = [QuoteSnapshot.model_validate(item) for item in payload.get("quotes", [])]
        initial_capital = float(payload["initial_capital"])

        expected_return = 0.0
        variance = 0.0
        for bucket in allocation.buckets:
            weight = bucket.target_weight_pct / 100
            asset_return, asset_volatility = self._return_assumptions.get(
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
            f"{quote.symbol}: {quote.current_price:.2f} at {quote.updated_at.isoformat()} "
            f"from {quote.source}"
            for quote in quotes
        ]

        return ReturnAnalysis(
            expected_annual_return_pct=round(expected_return * 100, 2),
            expected_annual_volatility_pct=round(volatility * 100, 2),
            projections=projections,
            quote_summary=quote_summary,
        )
