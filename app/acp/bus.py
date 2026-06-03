import asyncio
from collections import defaultdict

from app.acp.message import ACPMessage


class InMemoryACPBus:
    """进程内 ACP 消息总线。

    这是演示/原型用途的实现：消息只存在内存中，进程重启后 trace 会丢失。
    生产环境如果需要审计留痕，应替换为数据库、队列或日志系统。
    """

    def __init__(self) -> None:
        self._messages: dict[str, list[ACPMessage]] = defaultdict(list)
        # FastAPI 同时处理多个请求，锁可以避免同一 trace 的列表写入互相穿插。
        self._lock = asyncio.Lock()

    async def publish(self, message: ACPMessage) -> None:
        # 按 trace_id 分桶保存，后续 /api/v1/acp/traces/{trace_id} 可直接读取。
        async with self._lock:
            self._messages[message.trace_id].append(message)

    async def list_trace(self, trace_id: str) -> list[ACPMessage]:
        # 返回列表副本，避免调用方意外修改总线内部状态。
        async with self._lock:
            return list(self._messages.get(trace_id, []))
