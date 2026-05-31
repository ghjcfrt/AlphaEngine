from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import AIAdvisorReview
from app.services.ai_advisor import AIAdvisorService


class AIAdvisorAgent(BaseAgent):
    agent_id = "ai-advisor-agent"
    description = "调用大模型生成投资计划的中文解释和执行关注点。"
    capabilities = ["llm_explanation", "suitability_narrative", "action_checklist"]

    def __init__(self, ai_advisor_service: AIAdvisorService) -> None:
        self.ai_advisor_service = ai_advisor_service

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> AIAdvisorReview:
        return await self.ai_advisor_service.create_review(message.first_json())
