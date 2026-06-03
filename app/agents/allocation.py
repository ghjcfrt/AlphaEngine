from typing import Any

import httpx
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
from app.services.ai_advisor import AIAdvisorError, AIAdvisorJSONService, describe_ai_error


class AssetAllocationAgent(BaseAgent):
    """资产配置 Agent。

    规则模板给出不同风险等级的基础组合；AI 只能在 JSON Schema 和权重校验范围内
    复核调整，不能绕过 100% 权重合计约束。
    """

    agent_id = "asset-allocation-agent"
    description = "由模型复核战略资产配置，并以规则模板作为风险预算基线。"
    capabilities = ["ai_strategic_allocation", "risk_budgeting", "rebalance_policy"]

    def __init__(self, ai_advisor_service: AIAdvisorJSONService | None = None) -> None:
        self.ai_advisor_service = ai_advisor_service

    _templates: dict[RiskLevel, list[tuple[str, str, float, str]]] = {
        # 每个 tuple 依次为：资产类别、示例工具、目标权重、配置理由。
        # 示例工具用于原型展示，合规文本会提醒正式执行前替换为本地准入产品。
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
            # 规则模板已满足权重合计和基本风险约束，AI 不可用时直接使用。
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
            # AI 可能改了权重，金额必须按最终权重重新计算。
            self._recalculate_amounts(plan.buckets, float(payload["initial_capital"]))
            plan.notes.append(f"AI协作: {self.ai_advisor_service.provider_name} 已复核配置。")
            return plan
        except (AIAdvisorError, httpx.HTTPError, ValidationError, ValueError) as exc:
            baseline.notes.append(f"AI协作失败，已回退规则基线：{describe_ai_error(exc)}")
            return baseline

    def _rule_plan(self, payload: dict[str, Any]) -> AllocationPlan:
        """根据风险等级和目标生成规则配置方案。"""

        risk_assessment = RiskAssessment.model_validate(payload["risk_assessment"])
        initial_capital = float(payload["initial_capital"])
        objective = InvestmentObjective(
            payload.get("investment_objective", InvestmentObjective.balanced)
        )

        # list(...) 是浅拷贝，避免后续权重迁移污染类级模板。
        template = list(self._templates[risk_assessment.risk_level])
        if objective == InvestmentObjective.income:
            # 收入目标更偏稳定现金流，因此从权益转向债券。
            template = self._shift_weight(
                template,
                from_instrument="VTI",
                to_instrument="BND",
                amount=5,
            )
        elif objective == InvestmentObjective.capital_preservation:
            # 保值目标更重视本金和流动性，因此转向现金等价物。
            template = self._shift_weight(
                template,
                from_instrument="VTI",
                to_instrument="BIL",
                amount=8,
            )

        # target_amount 由本金 * 权重得到，只是计划金额，不代表实际下单数量。
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
        # 浮点数和权重调整可能造成 99.99/100.01，最后一个桶承担修正。
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
        """按最终权重重算目标金额。"""

        for bucket in buckets:
            bucket.target_amount = round(initial_capital * bucket.target_weight_pct / 100, 2)

    @staticmethod
    def _shift_weight(
        template: list[tuple[str, str, float, str]],
        from_instrument: str,
        to_instrument: str,
        amount: float,
    ) -> list[tuple[str, str, float, str]]:
        """在两个工具之间平移固定权重，保持总权重不变。"""

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
        """用最后一个资产桶吸收小数误差，保证模型校验能通过。"""

        total_before_last = round(sum(bucket.target_weight_pct for bucket in buckets[:-1]), 2)
        last_weight = round(100 - total_before_last, 2)
        buckets[-1].target_weight_pct = last_weight
