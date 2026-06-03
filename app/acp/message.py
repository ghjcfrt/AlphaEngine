from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ACPPart(BaseModel):
    """ACP 消息的一段内容。

    当前系统只使用 JSON，但保留 content_type，方便未来扩展文本、图片或文件引用。
    """

    content_type: str = "application/json"
    content: Any


class ACPMessage(BaseModel):
    """Agent 之间传递的轻量消息。

    trace_id 把同一次计划生成中的 request/result 消息串起来，前端可以用它还原
    多 Agent 协作链路。
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    sender: str
    receiver: str
    action: str
    parts: list[ACPPart] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def first_json(self) -> Any:
        # 本项目约定每条消息的第一个 part 就是业务 payload。
        if not self.parts:
            return {}
        return self.parts[0].content
