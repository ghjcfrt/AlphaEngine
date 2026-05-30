from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ACPPart(BaseModel):
    content_type: str = "application/json"
    content: Any


class ACPMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    sender: str
    receiver: str
    action: str
    parts: list[ACPPart] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def first_json(self) -> Any:
        if not self.parts:
            return {}
        return self.parts[0].content
