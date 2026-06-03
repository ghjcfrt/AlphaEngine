from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage
from app.agents.base import BaseAgent
from app.domain.schemas import QuoteSnapshot
from app.services.market_data import MarketDataService


class MarketDataAgent(BaseAgent):
    """行情 Agent。

    业务 Agent 不直接接触第三方 API；统一通过 MarketDataService 取标准化快照。
    """

    agent_id = "market-data-agent"
    description = "从已配置的实时行情提供方获取当前行情。"
    capabilities = ["quote_snapshot", "stock_realtime_data", "provider_abstraction"]

    def __init__(self, market_data_service: MarketDataService) -> None:
        self.market_data_service = market_data_service

    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> list[QuoteSnapshot]:
        payload = message.first_json()
        # symbols 可以为空；服务层会返回空列表，便于没有关注标的时仍生成计划。
        return await self.market_data_service.get_quotes(payload.get("symbols", []))
