import httpx

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import AIAdvisorReview
from app.services.ai_advisor import AIAdvisorError, AIAdvisorService, describe_ai_error


class AIAdvisorAgent(BaseAgent):
    """最终中文解读 Agent。

    它消费前面所有 Agent 的结构化结果，输出摘要、洞察、行动项和限制说明。
    """

    agent_id = "ai-advisor-agent"
    description = "调用大模型生成投资计划的中文解释和执行关注点。"
    capabilities = ["llm_explanation", "suitability_narrative", "action_checklist"]

    def __init__(self, ai_advisor_service: AIAdvisorService) -> None:
        self.ai_advisor_service = ai_advisor_service

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> AIAdvisorReview:
        try:
            return await self.ai_advisor_service.create_review(message.first_json())
        except (AIAdvisorError, httpx.HTTPError, ValueError) as exc:
            # 最终解读失败时，不让整份计划失败；前端仍可展示规则型结果和错误原因。
            return AIAdvisorReview(
                provider=self.ai_advisor_service.provider_name,
                model=self.ai_advisor_service.provider_model,
                is_model_generated=False,
                summary="AI 模型解读生成失败；本次计划仅返回规则型分析结果。",
                key_insights=[
                    "风险评估、资产配置、收益测算和合规复核已完成。",
                    "AI 总结未生成，请检查模型 API Key、模型名称、接口 URL 和账号权限。",
                ],
                action_items=[
                    "在配置源中更新有效的模型 API Key 后重新生成计划。",
                    "确认所选模型对当前账号可用，并确认接口 URL 与模型类型匹配。",
                ],
                limitations=[f"AI 连接错误：{describe_ai_error(exc)}"],
            )
