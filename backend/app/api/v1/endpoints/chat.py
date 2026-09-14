"""Recruiter assistant endpoints.

    POST /chat                        send a message, get a reply
    GET  /chat/conversations          list this recruiter's conversations
    GET  /chat/conversations/{id}     full transcript
    DELETE /chat/conversations/{id}   delete a conversation

``POST /chat`` runs synchronously and returns the whole reply. ``POST /chat/stream``
sends the same turn as server-sent events, so the reader sees prose as it is written
instead of watching nothing for the length of the turn. Both persist identically; the
streaming one commits only once the turn finishes, so an abandoned request cannot
leave a truncated assistant message in the transcript.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import assistant
from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import ChatConversation, User
from app.schemas import (
    ChatMessageOut,
    ChatReply,
    ChatRequest,
    ConversationDetail,
    ConversationOut,
    ToolCallOut,
)

router = APIRouter(prefix="/chat", tags=["assistant"])


def _sse(event: dict) -> str:
    """One server-sent event. The blank line after the payload is the delimiter —
    without it the client buffers waiting for the rest of the frame."""
    return f"data: {json.dumps(event)}\n\n"


@router.post("/stream")
def stream_message(
    body: ChatRequest,
    user: User = Depends(current_user),
) -> StreamingResponse:
    """The same turn as POST /chat, streamed as server-sent events.

    Deliberately takes no session dependency. FastAPI closes a dependency-provided
    session when the endpoint function returns, which for a StreamingResponse is
    before the generator has produced anything — so the turn would run against a
    closed session. The generator opens and owns its own.
    """

    def events():
        from app.db.database import session_scope

        session = session_scope()
        try:
            owner = session.get(User, user.id)
            for event in assistant.send_streaming(
                session,
                user=owner,
                message=body.message,
                conversation_id=body.conversation_id,
            ):
                yield _sse(event)
        except LookupError:
            yield _sse({"type": "error", "message": "Conversation not found."})
        except Exception as exc:  # noqa: BLE001 - the stream has already started, so
            # there is no status code left to set; the error has to travel as an event.
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            session.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # Without this an intermediary may buffer the whole response and deliver
            # it at once, which is exactly the behaviour being replaced.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=ChatReply)
def send_message(
    body: ChatRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ChatReply:
    try:
        reply = assistant.send(
            session,
            user=user,
            message=body.message,
            conversation_id=body.conversation_id,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.") from exc
    except assistant.AssistantUnavailable as exc:
        # 503, not 500: the request was fine, the capability is not configured or the
        # upstream failed. The transcript already holds the user's message.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return ChatReply(
        conversation_id=reply.conversation_id,
        message=reply.text,
        tool_calls=[
            ToolCallOut(name=call.name, args=call.args, is_write=call.is_write)
            for call in reply.tool_calls
        ],
        engine=reply.engine,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[ConversationOut]:
    rows = session.execute(
        select(ChatConversation)
        .where(ChatConversation.recruiter_id == user.id)
        .order_by(ChatConversation.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [ConversationOut.model_validate(row) for row in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ConversationDetail:
    try:
        conversation = assistant.get_or_create_conversation(
            session, recruiter_id=user.id, conversation_id=conversation_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.") from exc

    return ConversationDetail(
        conversation=ConversationOut.model_validate(conversation),
        messages=[ChatMessageOut.model_validate(m) for m in conversation.messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    try:
        conversation = assistant.get_or_create_conversation(
            session, recruiter_id=user.id, conversation_id=conversation_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.") from exc

    session.delete(conversation)
    session.commit()
