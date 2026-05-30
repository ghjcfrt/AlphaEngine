from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.acp.bus import InMemoryACPBus
from app.acp.message import ACPMessage


class AgentInfo(BaseModel):
    agent_id: str
    description: str
    capabilities: list[str]


class BaseAgent(ABC):
    agent_id: str
    description: str
    capabilities: list[str]

    @property
    def info(self) -> AgentInfo:
        return AgentInfo(
            agent_id=self.agent_id,
            description=self.description,
            capabilities=self.capabilities,
        )

    @abstractmethod
    async def handle(self, message: ACPMessage, bus: InMemoryACPBus) -> Any:
        raise NotImplementedError
