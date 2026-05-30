from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import (
    AllocationBucket,
    AllocationPlan,
    InvestmentObjective,
    RiskAssessment,
    RiskLevel,
)


class AssetAllocationAgent(BaseAgent):
    agent_id = "asset-allocation-agent"
    description = "根据风险画像构建多元化战略资产配置。"
    capabilities = ["strategic_allocation", "risk_budgeting", "rebalance_policy"]

    _templates: dict[RiskLevel, list[tuple[str, str, float, str]]] = {
        RiskLevel.conservative: [
            (
                "US equity ETF",
                "VTI",
                22,
                "Core equity exposure kept below conservative risk budget.",
            ),
            ("Global equity ETF", "VXUS", 8, "Adds geographic diversification."),
            ("Bond ETF", "BND", 50, "Stabilizes drawdowns and supports income."),
            ("Treasury bill ETF", "BIL", 15, "Preserves liquidity for short-term needs."),
            ("Gold ETF", "GLD", 5, "Diversifier against macro shocks."),
        ],
        RiskLevel.balanced: [
            ("US equity ETF", "VTI", 40, "Primary growth engine with broad diversification."),
            ("Global equity ETF", "VXUS", 15, "Reduces single-market concentration."),
            ("Bond ETF", "BND", 30, "Balances equity risk."),
            ("Treasury bill ETF", "BIL", 5, "Keeps deployable liquidity."),
            ("Gold ETF", "GLD", 10, "Adds non-equity diversification."),
        ],
        RiskLevel.growth: [
            ("US equity ETF", "VTI", 55, "Higher growth allocation within risk budget."),
            ("Global equity ETF", "VXUS", 20, "Global equity diversification."),
            ("Bond ETF", "BND", 15, "Controls total portfolio volatility."),
            ("Treasury bill ETF", "BIL", 3, "Maintains operating liquidity."),
            ("Gold ETF", "GLD", 7, "Diversifies against inflation and stress scenarios."),
        ],
        RiskLevel.aggressive: [
            ("US equity ETF", "VTI", 65, "Maximum broad equity exposure for long horizon."),
            ("Global equity ETF", "VXUS", 25, "Complements US equity risk."),
            ("Bond ETF", "BND", 5, "Small stabilizer sleeve."),
            ("Treasury bill ETF", "BIL", 2, "Minimal liquidity reserve."),
            ("Gold ETF", "GLD", 3, "Small diversifier sleeve."),
        ],
    }

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> AllocationPlan:
        payload = message.first_json()
        risk_assessment = RiskAssessment.model_validate(payload["risk_assessment"])
        initial_capital = float(payload["initial_capital"])
        objective = InvestmentObjective(
            payload.get("investment_objective", InvestmentObjective.balanced)
        )

        template = list(self._templates[risk_assessment.risk_level])
        if objective == InvestmentObjective.income:
            template = self._shift_weight(
                template,
                from_instrument="VTI",
                to_instrument="BND",
                amount=5,
            )
        elif objective == InvestmentObjective.capital_preservation:
            template = self._shift_weight(
                template,
                from_instrument="VTI",
                to_instrument="BIL",
                amount=8,
            )

        buckets = [
            AllocationBucket(
                asset_class=asset_class,
                instrument=instrument,
                target_weight_pct=weight,
                target_amount=round(initial_capital * weight / 100, 2),
                rationale=rationale,
            )
            for asset_class, instrument, weight, rationale in template
        ]
        self._normalize_last_bucket(buckets)

        notes = [
            "Use diversified ETFs as default instruments; replace with locally approved "
            "products if needed.",
            "Single-stock exposure should stay below "
            f"{risk_assessment.max_single_stock_pct:.0f}% per name.",
        ]
        if objective == InvestmentObjective.income:
            notes.append("Income objective shifted 5% from broad US equity to bonds.")
        if objective == InvestmentObjective.capital_preservation:
            notes.append(
                "Capital preservation objective shifted 8% from broad US equity to "
                "cash equivalents."
            )

        return AllocationPlan(
            buckets=buckets,
            rebalance_frequency="quarterly_or_5_pct_drift",
            notes=notes,
        )

    @staticmethod
    def _shift_weight(
        template: list[tuple[str, str, float, str]],
        from_instrument: str,
        to_instrument: str,
        amount: float,
    ) -> list[tuple[str, str, float, str]]:
        shifted: list[tuple[str, str, float, str]] = []
        for asset_class, instrument, weight, rationale in template:
            if instrument == from_instrument:
                weight -= amount
            if instrument == to_instrument:
                weight += amount
            shifted.append((asset_class, instrument, weight, rationale))
        return shifted

    @staticmethod
    def _normalize_last_bucket(buckets: list[AllocationBucket]) -> None:
        total_before_last = round(sum(bucket.target_weight_pct for bucket in buckets[:-1]), 2)
        last_weight = round(100 - total_before_last, 2)
        buckets[-1].target_weight_pct = last_weight
