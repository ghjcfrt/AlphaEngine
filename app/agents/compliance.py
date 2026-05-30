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


class ComplianceAgent(BaseAgent):
    agent_id = "compliance-agent"
    description = "补充适当性、风险和人工复核护栏。"
    capabilities = ["disclosures", "suitability_review", "human_review_flags"]

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> ComplianceReview:
        payload = message.first_json()
        profile = InvestorProfile.model_validate(payload["profile"])
        risk_assessment = RiskAssessment.model_validate(payload["risk_assessment"])
        allocation = AllocationPlan.model_validate(payload["allocation"])

        warnings = [
            "This output is educational and does not constitute personalized investment advice.",
            "Market prices, expected returns, and volatility assumptions can change quickly.",
        ]
        suitability_notes = [
            f"Risk level is {risk_assessment.risk_level.value} with score "
            f"{risk_assessment.risk_score:.1f}/100.",
            f"Recommended broad equity exposure stays within the "
            f"{risk_assessment.max_equity_pct:.0f}% maximum equity budget.",
        ]
        guardrails = [
            "Confirm identity, investment objective, risk questionnaire, and source of "
            "funds before execution.",
            "Keep audit logs for all agent messages and data-provider responses.",
            "Use licensed market data and locally approved investment products in production.",
        ]

        requires_human_review = False
        if profile.age >= 65 and risk_assessment.risk_level in {
            RiskLevel.growth,
            RiskLevel.aggressive,
        }:
            requires_human_review = True
            warnings.append("Senior investor with elevated risk profile requires human review.")

        cash_weight = sum(
            bucket.target_weight_pct
            for bucket in allocation.buckets
            if "Treasury" in bucket.asset_class
        )
        if profile.liquidity_need.value == "high" and cash_weight < 10:
            requires_human_review = True
            warnings.append("High liquidity need conflicts with low cash-equivalent allocation.")

        concentrated_positions = [
            position.symbol
            for position in profile.current_positions
            if position.average_cost
            and position.quantity * position.average_cost > profile.net_worth * 0.3
        ]
        if concentrated_positions:
            requires_human_review = True
            warnings.append(
                "Existing concentrated positions need review: "
                + ", ".join(sorted(concentrated_positions))
            )

        return ComplianceReview(
            warnings=warnings,
            suitability_notes=suitability_notes,
            guardrails=guardrails,
            requires_human_review=requires_human_review,
        )
