from pydantic import ValidationError

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
from app.services.ai_advisor import AIAdvisorError, AIAdvisorService


class AssetAllocationAgent(BaseAgent):
    agent_id = "asset-allocation-agent"
    description = "由模型复核战略资产配置，并以规则模板作为风险预算基线。"
    capabilities = ["ai_strategic_allocation", "risk_budgeting", "rebalance_policy"]

    def __init__(self, ai_advisor_service: AIAdvisorService | None = None) -> None:
        self.ai_advisor_service = ai_advisor_service

    _templates: dict[RiskLevel, list[tuple[str, str, float, str]]] = {
        RiskLevel.conservative: [
            (
                "US equity ETF",
                "VTI",
                22,
                "核心权益仓位控制在保守型风险预算内。",
            ),
            ("Global equity ETF", "VXUS", 8, "增加地域分散度。"),
            ("Bond ETF", "BND", 50, "稳定回撤并补充收入属性。"),
            ("Treasury bill ETF", "BIL", 15, "保留短期流动性。"),
            ("Gold ETF", "GLD", 5, "用于分散宏观冲击风险。"),
        ],
        RiskLevel.balanced: [
            ("US equity ETF", "VTI", 40, "作为主要增长来源，并保持宽基分散。"),
            ("Global equity ETF", "VXUS", 15, "降低单一市场集中度。"),
            ("Bond ETF", "BND", 30, "平衡权益资产波动。"),
            ("Treasury bill ETF", "BIL", 5, "保留可调配流动性。"),
            ("Gold ETF", "GLD", 10, "补充非权益资产分散。"),
        ],
        RiskLevel.growth: [
            ("US equity ETF", "VTI", 55, "在风险预算内提高成长性仓位。"),
            ("Global equity ETF", "VXUS", 20, "提供全球权益分散。"),
            ("Bond ETF", "BND", 15, "控制组合整体波动。"),
            ("Treasury bill ETF", "BIL", 3, "维持基础流动性。"),
            ("Gold ETF", "GLD", 7, "分散通胀和压力情景风险。"),
        ],
        RiskLevel.aggressive: [
            ("US equity ETF", "VTI", 65, "面向长期期限配置较高宽基权益仓位。"),
            ("Global equity ETF", "VXUS", 25, "补充美国权益以外的市场风险暴露。"),
            ("Bond ETF", "BND", 5, "保留少量稳定资产。"),
            ("Treasury bill ETF", "BIL", 2, "保留最低流动性缓冲。"),
            ("Gold ETF", "GLD", 3, "保留少量分散资产。"),
        ],
    }

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> AllocationPlan:
        payload = message.first_json()
        baseline = self._rule_plan(payload)
        if not self.ai_advisor_service or not self.ai_advisor_service.is_model_generated:
            return baseline
        try:
            generated = await self.ai_advisor_service.generate_json(
                task_name="allocation_plan",
                system_instructions=(
                    "你是资产配置 Agent。必须基于风险评估、投资目标和规则基线输出 JSON。"
                    "资产权重总和必须为 100，不得突破输入中的风险约束。"
                ),
                user_prompt=(
                    "请复核并必要时调整规则资产配置。"
                    "需要保持分散、说明每个资产桶理由，并给出再平衡说明。"
                ),
                schema=AllocationPlan.model_json_schema(),
                context={
                    "risk_assessment": payload["risk_assessment"],
                    "initial_capital": payload["initial_capital"],
                    "investment_objective": payload.get("investment_objective"),
                    "baseline": baseline.model_dump(mode="json"),
                },
            )
            plan = AllocationPlan.model_validate(generated)
            self._recalculate_amounts(plan.buckets, float(payload["initial_capital"]))
            plan.notes.append(f"AI协作: {self.ai_advisor_service.provider_name} 已复核配置。")
            return plan
        except (AIAdvisorError, ValidationError, ValueError) as exc:
            baseline.notes.append(f"AI协作失败，已回退规则基线：{exc}")
            return baseline

    def _rule_plan(self, payload: dict[str, object]) -> AllocationPlan:
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
            "默认使用分散化 ETF 作为示例工具，正式执行前需替换为本地准入产品。",
            f"单一股票风险暴露应低于每只 {risk_assessment.max_single_stock_pct:.0f}%。",
        ]
        if objective == InvestmentObjective.income:
            notes.append("收入目标下，从宽基美股向债券调低 5% 权重。")
        if objective == InvestmentObjective.capital_preservation:
            notes.append("保值目标下，从宽基美股向现金等价物调低 8% 权重。")

        return AllocationPlan(
            buckets=buckets,
            rebalance_frequency="季度复核或偏离 5% 时再平衡",
            notes=notes,
        )

    @staticmethod
    def _recalculate_amounts(buckets: list[AllocationBucket], initial_capital: float) -> None:
        for bucket in buckets:
            bucket.target_amount = round(initial_capital * bucket.target_weight_pct / 100, 2)

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
