from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage


class AgentInfo(BaseModel):
    """暴露给前端的 Agent 元信息。"""

    agent_id: str
    description: str
    capabilities: list[str]


class BaseAgent(ABC):
    """所有专业 Agent 的统一接口。"""

    agent_id: str
    description: str
    capabilities: list[str]

    @property
    def info(self) -> AgentInfo:
        # /api/v1/agents 只需要元信息，不暴露 Agent 内部 service 或配置细节。
        return AgentInfo(
            agent_id=self.agent_id,
            description=self.description,
            capabilities=self.capabilities,
        )

    @abstractmethod
    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> Any:
        # message 是输入，bus 可用于发布额外协作消息；当前多数 Agent 只返回结果。
        raise NotImplementedError
