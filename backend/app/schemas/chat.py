"""Assistant wire contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    #: Omit to start a new conversation.
    conversation_id: uuid.UUID | None = None


class ToolCallOut(BaseModel):
    """Shown in the transcript so a recruiter can see what the assistant did —
    especially that a write happened."""

    name: str
    args: dict
    is_write: bool


class ChatReply(BaseModel):
    conversation_id: uuid.UUID
    message: str
    tool_calls: list[ToolCallOut] = []
    engine: str


class ChatMessageOut(ORMModel):
    id: uuid.UUID
    sequence: int
    role: str
    content: str
    tool_name: str | None = None
    created_at: datetime


class ConversationOut(ORMModel):
    id: uuid.UUID
    title: str
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[ChatMessageOut]
