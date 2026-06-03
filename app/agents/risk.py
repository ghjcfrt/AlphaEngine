from statistics import mean

import httpx
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
from app.services.ai_advisor import AIAdvisorError, AIAdvisorJSONService, describe_ai_error


class RiskAssessmentAgent(BaseAgent):
    """风险画像 Agent。

    设计原则是“规则先行，AI 复核”：规则评分提供可解释、可审计的底线；
    如果模型可用，再让模型在 JSON Schema 约束下复核或微调。
    """

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
            # AI 关闭或未配置时直接返回规则基线，保证系统仍能生成完整计划。
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
        except (AIAdvisorError, httpx.HTTPError, ValidationError, ValueError) as exc:
            # 模型失败不阻断计划生成，而是把失败原因写入 rationale 供审计。
            baseline.rationale.append(f"AI协作失败，已回退规则基线：{describe_ai_error(exc)}")
            return baseline

    @staticmethod
    def _rule_assessment(profile: InvestorProfile) -> RiskAssessment:
        """根据画像字段计算 0-100 风险分。

        分数越高，说明可承受权益波动越大。每项调整都写入 rationale，方便前端
        展示“为什么是这个风险等级”。
        """

        # 没填问卷时用中性 3 分，避免空问卷直接把用户推向保守或进取。
        questionnaire = mean(profile.risk_answers) if profile.risk_answers else 3
        score = 50.0
        rationale: list[str] = []

        # 年龄越低，理论上投资期限和收入修复能力越强；高龄则下调风险预算。
        age_adjustment = max(min((45 - profile.age) * 0.6, 12), -20)
        score += age_adjustment
        rationale.append(f"年龄因素调整：{age_adjustment:+.1f} 分。")

        # 长期限更能承受短期波动，但上限封顶，避免 50 年期限把分数推得过高。
        horizon_adjustment = min(profile.investment_horizon_years * 2.2, 22)
        score += horizon_adjustment
        rationale.append(
            f"投资期限 {profile.investment_horizon_years} 年，"
            f"支持风险预算调整 {horizon_adjustment:+.1f} 分。"
        )

        # 问卷以 3 为中性点，每高/低 1 分调整 12 分。
        questionnaire_adjustment = (questionnaire - 3) * 12
        score += questionnaire_adjustment
        rationale.append(f"风险问卷平均分为 {questionnaire:.1f}/5。")

        # 高流动性需求意味着可能很快要用钱，因此明显降低风险预算。
        liquidity_adjustment = {
            LiquidityNeed.low: 6,
            LiquidityNeed.medium: 0,
            LiquidityNeed.high: -14,
        }[profile.liquidity_need]
        score += liquidity_adjustment
        rationale.append(f"流动性需求调整：{liquidity_adjustment:+.1f} 分。")

        # 投资目标会把同一画像推向不同风险偏好：保值/收入更保守，增长更积极。
        objective_adjustment = {
            InvestmentObjective.capital_preservation: -18,
            InvestmentObjective.income: -8,
            InvestmentObjective.balanced: 0,
            InvestmentObjective.growth: 12,
        }[profile.investment_objective]
        score += objective_adjustment
        rationale.append(f"投资目标调整：{objective_adjustment:+.1f} 分。")

        # 初始本金占净资产比例过高，说明这笔钱对家庭资产影响较大，需要降风险。
        net_worth_ratio = profile.initial_capital / max(profile.net_worth, 1)
        if net_worth_ratio > 0.6:
            score -= 12
            rationale.append("初始投资本金占净资产比例较高，降低风险预算。")

        # 分数落在 0-100 后映射到四档风险等级，并给出权益上限。
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
            # 成长/进取用户允许略高的单只股票上限，但仍然保持分散原则。
            max_single_stock_pct=(
                12 if risk_level in {RiskLevel.growth, RiskLevel.aggressive} else 8
            ),
            rationale=rationale,
        )
