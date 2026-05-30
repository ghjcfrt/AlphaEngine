import asyncio
from collections import defaultdict

from app.acp.message import ACPMessage


class InMemoryACPBus:
    def __init__(self) -> None:
        self._messages: dict[str, list[ACPMessage]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, message: ACPMessage) -> None:
        async with self._lock:
            self._messages[message.trace_id].append(message)

    async def list_trace(self, trace_id: str) -> list[ACPMessage]:
        async with self._lock:
            return list(self._messages.get(trace_id, []))
