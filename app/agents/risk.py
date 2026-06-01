from statistics import mean

from pydantic import ValidationError

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import (
    InvestmentObjective,
    InvestorProfile,
    LiquidityNeed,
    RiskAssessment,
    RiskLevel,
)
from app.services.ai_advisor import AIAdvisorError, AIAdvisorJSONService


class RiskAssessmentAgent(BaseAgent):
    agent_id = "risk-assessment-agent"
    description = "由模型复核风险画像，并以规则评分作为可审计基线。"
    capabilities = ["ai_risk_scoring", "suitability_inputs", "risk_constraints"]

    def __init__(self, ai_advisor_service: AIAdvisorJSONService | None = None) -> None:
        self.ai_advisor_service = ai_advisor_service

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> RiskAssessment:
        payload = message.first_json()
        profile = InvestorProfile.model_validate(payload["profile"])
        baseline = self._rule_assessment(profile)
        if not self.ai_advisor_service or not self.ai_advisor_service.is_model_generated:
            return baseline
        try:
            generated = await self.ai_advisor_service.generate_json(
                task_name="risk_assessment",
                system_instructions=(
                    "你是投资适当性和风险画像 Agent。"
                    "必须基于客户画像、风险问卷和规则基线输出 JSON。"
                    "不得给出交易指令，不得承诺收益。"
                ),
                user_prompt=(
                    "请复核规则基线的风险评分、风险等级、权益上限和单一股票上限。"
                    "若你调整结果，必须在 rationale 中说明原因。"
                ),
                schema=RiskAssessment.model_json_schema(),
                context={
                    "profile": profile.model_dump(mode="json"),
                    "baseline": baseline.model_dump(mode="json"),
                },
            )
            assessment = RiskAssessment.model_validate(generated)
            assessment.rationale.append(
                f"AI协作: {self.ai_advisor_service.provider_name} 已复核风险画像。"
            )
            return assessment
        except (AIAdvisorError, ValidationError, ValueError) as exc:
            baseline.rationale.append(f"AI协作失败，已回退规则基线：{exc}")
            return baseline

    @staticmethod
    def _rule_assessment(profile: InvestorProfile) -> RiskAssessment:

        questionnaire = mean(profile.risk_answers) if profile.risk_answers else 3
        score = 50.0
        rationale: list[str] = []

        age_adjustment = max(min((45 - profile.age) * 0.6, 12), -20)
        score += age_adjustment
        rationale.append(f"年龄因素调整：{age_adjustment:+.1f} 分。")

        horizon_adjustment = min(profile.investment_horizon_years * 2.2, 22)
        score += horizon_adjustment
        rationale.append(
            f"投资期限 {profile.investment_horizon_years} 年，"
            f"支持风险预算调整 {horizon_adjustment:+.1f} 分。"
        )

        questionnaire_adjustment = (questionnaire - 3) * 12
        score += questionnaire_adjustment
        rationale.append(f"风险问卷平均分为 {questionnaire:.1f}/5。")

        liquidity_adjustment = {
            LiquidityNeed.low: 6,
            LiquidityNeed.medium: 0,
            LiquidityNeed.high: -14,
        }[profile.liquidity_need]
        score += liquidity_adjustment
        rationale.append(f"流动性需求调整：{liquidity_adjustment:+.1f} 分。")

        objective_adjustment = {
            InvestmentObjective.capital_preservation: -18,
            InvestmentObjective.income: -8,
            InvestmentObjective.balanced: 0,
            InvestmentObjective.growth: 12,
        }[profile.investment_objective]
        score += objective_adjustment
        rationale.append(f"投资目标调整：{objective_adjustment:+.1f} 分。")

        net_worth_ratio = profile.initial_capital / max(profile.net_worth, 1)
        if net_worth_ratio > 0.6:
            score -= 12
            rationale.append("初始投资本金占净资产比例较高，降低风险预算。")

        score = round(max(0, min(score, 100)), 2)
        if score < 35:
            risk_level = RiskLevel.conservative
            max_equity_pct = 35
        elif score < 60:
            risk_level = RiskLevel.balanced
            max_equity_pct = 60
        elif score < 80:
            risk_level = RiskLevel.growth
            max_equity_pct = 80
        else:
            risk_level = RiskLevel.aggressive
            max_equity_pct = 92

        return RiskAssessment(
            risk_score=score,
            risk_level=risk_level,
            max_equity_pct=max_equity_pct,
            max_single_stock_pct=(
                12 if risk_level in {RiskLevel.growth, RiskLevel.aggressive} else 8
            ),
            rationale=rationale,
        )
