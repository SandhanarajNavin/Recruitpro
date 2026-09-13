"""Recruiter assistant conversations.

Stored in our own tables rather than an agent framework's checkpointer: the history
is recruiter-owned data subject to the same isolation rules as everything else, and
it has to be queryable by the audit trail. A conversation is scoped to one recruiter
and cascades with them.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class ChatRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    #: A tool call the assistant made, kept so the transcript shows what it did
    #: rather than only what it said.
    TOOL = "tool"


class ChatConversation(Base, TimestampMixin):
    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conv_recruiter_created", "recruiter_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    recruiter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: First user message, truncated. Written once so the sidebar has a label
    #: without re-reading the transcript.
    title: Mapped[str] = mapped_column(String(160), default="New conversation", nullable=False)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.sequence",
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_msg_conversation_seq", "conversation_id", "sequence"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Monotonic within a conversation. created_at is not enough — several messages
    #: land in the same millisecond when a turn runs multiple tools.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: For role="tool": which tool ran and what it was asked. Arguments are stored
    #: because a write the assistant performed must be reconstructable later.
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_args: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
