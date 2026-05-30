from statistics import mean

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


class RiskAssessmentAgent(BaseAgent):
    agent_id = "risk-assessment-agent"
    description = "评估适当性、风险承受能力和风险偏好。"
    capabilities = ["risk_scoring", "suitability_inputs", "risk_constraints"]

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> RiskAssessment:
        payload = message.first_json()
        profile = InvestorProfile.model_validate(payload["profile"])

        questionnaire = mean(profile.risk_answers) if profile.risk_answers else 3
        score = 50.0
        rationale: list[str] = []

        age_adjustment = max(min((45 - profile.age) * 0.6, 12), -20)
        score += age_adjustment
        rationale.append(f"Age adjustment: {age_adjustment:+.1f} points.")

        horizon_adjustment = min(profile.investment_horizon_years * 2.2, 22)
        score += horizon_adjustment
        rationale.append(
            f"Investment horizon of {profile.investment_horizon_years} years supports "
            f"{horizon_adjustment:+.1f} points."
        )

        questionnaire_adjustment = (questionnaire - 3) * 12
        score += questionnaire_adjustment
        rationale.append(f"Risk questionnaire average {questionnaire:.1f}/5.")

        liquidity_adjustment = {
            LiquidityNeed.low: 6,
            LiquidityNeed.medium: 0,
            LiquidityNeed.high: -14,
        }[profile.liquidity_need]
        score += liquidity_adjustment
        rationale.append(f"Liquidity need adjustment: {liquidity_adjustment:+.1f} points.")

        objective_adjustment = {
            InvestmentObjective.capital_preservation: -18,
            InvestmentObjective.income: -8,
            InvestmentObjective.balanced: 0,
            InvestmentObjective.growth: 12,
        }[profile.investment_objective]
        score += objective_adjustment
        rationale.append(f"Objective adjustment: {objective_adjustment:+.1f} points.")

        net_worth_ratio = profile.initial_capital / max(profile.net_worth, 1)
        if net_worth_ratio > 0.6:
            score -= 12
            rationale.append("Initial capital is a large share of net worth; reduce risk budget.")

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
