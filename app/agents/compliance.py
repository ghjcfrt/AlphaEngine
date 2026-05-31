from pydantic import ValidationError

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import (
    AllocationPlan,
    ComplianceReview,
    InvestorProfile,
    RiskAssessment,
    RiskLevel,
)
from app.services.ai_advisor import AIAdvisorError, AIAdvisorService


class ComplianceAgent(BaseAgent):
    agent_id = "compliance-agent"
    description = "由模型复核适当性和披露完整性，并以规则红线作为合规基线。"
    capabilities = ["ai_suitability_review", "disclosures", "human_review_flags"]

    def __init__(self, ai_advisor_service: AIAdvisorService | None = None) -> None:
        self.ai_advisor_service = ai_advisor_service

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> ComplianceReview:
        payload = message.first_json()
        baseline = self._rule_review(payload)
        if not self.ai_advisor_service or not self.ai_advisor_service.is_model_generated:
            return baseline
        try:
            generated = await self.ai_advisor_service.generate_json(
                task_name="compliance_review",
                system_instructions=(
                    "你是投顾合规复核 Agent。"
                    "必须基于客户画像、风险、配置、收益分析和规则基线输出 JSON。"
                    "不得删除规则基线中的风险警示，不得把输出描述成正式投资建议。"
                ),
                user_prompt=(
                    "请复核适当性、披露完整性、人工复核条件和执行护栏。"
                    "可以增加警示，但不得弱化 baseline 中已有警示。"
                ),
                schema=ComplianceReview.model_json_schema(),
                context={
                    "profile": payload["profile"],
                    "risk_assessment": payload["risk_assessment"],
                    "allocation": payload["allocation"],
                    "return_analysis": payload.get("return_analysis"),
                    "baseline": baseline.model_dump(mode="json"),
                },
            )
            review = ComplianceReview.model_validate(generated)
            return self._merge_with_baseline(review, baseline)
        except (AIAdvisorError, ValidationError, ValueError) as exc:
            baseline.warnings.append(f"AI协作失败，已回退规则合规基线：{exc}")
            return baseline

    @staticmethod
    def _rule_review(payload: dict[str, object]) -> ComplianceReview:
        profile = InvestorProfile.model_validate(payload["profile"])
        risk_assessment = RiskAssessment.model_validate(payload["risk_assessment"])
        allocation = AllocationPlan.model_validate(payload["allocation"])

        warnings = [
            "本输出仅用于教育和原型演示，不构成个性化投资建议。",
            "市场价格、预期收益和波动率假设可能快速变化。",
        ]
        suitability_notes = [
            f"风险等级为 {risk_assessment.risk_level.value}，"
            f"评分 {risk_assessment.risk_score:.1f}/100。",
            f"建议的宽基权益暴露保持在 {risk_assessment.max_equity_pct:.0f}% "
            "权益上限以内。",
        ]
        guardrails = [
            "执行前确认客户身份、投资目标、风险问卷和资金来源。",
            "保留所有 Agent 消息和数据源响应的审计日志。",
            "生产环境必须使用授权行情和本地准入投资产品。",
        ]

        requires_human_review = False
        if profile.age >= 65 and risk_assessment.risk_level in {
            RiskLevel.growth,
            RiskLevel.aggressive,
        }:
            requires_human_review = True
            warnings.append("高龄客户且风险等级较高，需要人工复核。")

        cash_weight = sum(
            bucket.target_weight_pct
            for bucket in allocation.buckets
            if "Treasury" in bucket.asset_class
        )
        if profile.liquidity_need.value == "high" and cash_weight < 10:
            requires_human_review = True
            warnings.append("高流动性需求与较低现金等价物配置存在冲突。")

        concentrated_positions = [
            position.symbol
            for position in profile.current_positions
            if position.average_cost
            and position.quantity * position.average_cost > profile.net_worth * 0.3
        ]
        if concentrated_positions:
            requires_human_review = True
            warnings.append(
                "已有集中持仓需要复核："
                + ", ".join(sorted(concentrated_positions))
            )

        return ComplianceReview(
            warnings=warnings,
            suitability_notes=suitability_notes,
            guardrails=guardrails,
            requires_human_review=requires_human_review,
        )

    def _merge_with_baseline(
        self,
        review: ComplianceReview,
        baseline: ComplianceReview,
    ) -> ComplianceReview:
        return ComplianceReview(
            warnings=_dedupe(
                [
                    *baseline.warnings,
                    *review.warnings,
                    f"AI协作: {self.ai_advisor_service.provider_name} 已复核合规披露。",
                ]
            ),
            suitability_notes=_dedupe([*baseline.suitability_notes, *review.suitability_notes]),
            guardrails=_dedupe([*baseline.guardrails, *review.guardrails]),
            requires_human_review=baseline.requires_human_review or review.requires_human_review,
        )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
