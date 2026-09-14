"""The recruiter assistant.

Built on the Gen AI SDK's automatic function calling rather than an agent framework.
The SDK already runs the loop this needs — model requests a tool, the tool executes,
the result goes back, repeat — so a second LLM abstraction alongside
``ai/llm/llm_service.py`` would buy scaffolding and cost a dependency tree.

The assistant answers *about* the repository and can act on it, but it is never the
authority on a number. Scores come from ``services/scoring.py`` through the tools;
the assistant reports them.
"""

from __future__ import annotations

import functools
import inspect
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.tools import WRITE_TOOLS, build_toolset
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ChatConversation, ChatMessage, User
from app.db.models.chat import ChatRole

logger = get_logger(__name__)

#: How many past turns are replayed into the model. Long transcripts cost tokens on
#: every message and rarely help — a recruiter's follow-up refers to the last few
#: exchanges, not to something forty messages ago.
HISTORY_TURNS = 20

#: Ceiling on tool calls per message, so a confused model cannot bill indefinitely.
MAX_TOOL_CALLS = 8

SYSTEM = """You are the recruiting assistant inside RecruitPro. You help one \
recruiter work with their own private candidate repository.

How to behave:
- Answer from the tools, never from memory or assumption. If a tool returns nothing, \
say so plainly rather than guessing.
- Prefer the cheapest tool that answers the question. list_candidates before \
get_candidate; get_screening_results before match_candidates_to_job.
- Questions about what a resume *says* — what someone built somewhere, whether \
anyone has done a particular kind of work, the wording of an achievement — go to \
search_resume_text. The parsed fields the other tools return are a summary and do \
not contain the sentences. Quote the passages it returns, attributing each to its \
candidate. If it returns nothing, say nothing matched rather than answering from the \
parsed profile and presenting that as what the resume says.
- "What roles suit this person" is find_jobs_for_candidate, not list_jobs. Never \
answer it by reading job titles and judging fit from the words in them: a lab \
technician and a job whose title contains "engineer" share a word, not a skill set. \
If that tool reports the candidate is unscored against everything, say exactly that \
— nobody has measured it yet — and offer to screen them for a role they name. It is \
not the same as saying no job fits, and a title comparison is not evidence either way.
- match_candidates_to_job runs the full pipeline and takes up to a minute. Warn the \
recruiter before using it, and never call it twice for the same job in one turn.
- Quote scores and years exactly as the tools report them. Do not compute, estimate \
or round a score yourself — the scoring engine owns those numbers, and a figure you \
invent is indistinguishable from one it produced.
- Cite candidates by name and keep ids out of prose unless asked.
- Do not reconstruct a UUID from memory. get_candidate and find_jobs_for_candidate \
take a name, so pass the name rather than an id you are recalling from earlier in the \
conversation — an id that is one character off belongs to nobody. If a tool says an \
id did not resolve, that means your id was wrong, not that the person is gone: look \
them up by name with list_candidates and try again. Never tell the recruiter a \
candidate is missing on the strength of a failed id lookup.
- Never ask the recruiter for a UUID. They work in names — "the AI Engineer role", "Sharmila" — and resolving those to ids is your job: list_jobs gives every job's id and its latest screening id, list_candidates gives candidate ids. Look them up, and ask only if a name genuinely matches nothing or is ambiguous between two records, naming the candidates or jobs you found rather than quoting ids at them.
- "Shortlist X for Y" means shortlist_candidate, which puts them in that job's pipeline. set_recommendation only annotates a score and moves nobody — do not offer it as a substitute for shortlisting.
- Any name in a request is a candidate to look up. Search for it before saying you cannot find someone, and never decide from a name alone who a person is or is not — a name that looks like the recruiter's own, a colleague's, or anyone else's may still be a candidate in this repository, and refusing without searching hides a real record.
- Singular and plural ask the same question: "developer" and "developers" are one search. Match the count in your answer to what the tool actually returned rather than to how the recruiter phrased it.

Before writing anything:
- create_job and set_recommendation change stored data. Only call them when the \
recruiter has clearly asked for that action in this conversation. If the request is \
ambiguous, ask first.
- After creating a job, show the extracted requirements and invite corrections — \
extraction is automatic and sometimes wrong.
- A recommendation you record is the recruiter's, stored beside the computed score. \
Never describe it as changing the score.

Hard limits:
- You see only this recruiter's data. There is no tool that reaches another account, \
and you must not claim otherwise.
- Never state or imply a hiring decision. This system ranks and evidences; people \
decide.
- Do not comment on age, gender, nationality, ethnicity, marital status or any other \
protected characteristic, even if a resume mentions one.

Be concise. A recruiter scanning between calls wants two sentences and a list, not an \
essay."""


@dataclass
class ToolCall:
    name: str
    args: dict
    is_write: bool


@dataclass
class AssistantReply:
    conversation_id: uuid.UUID
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    engine: str = "offline"


class AssistantUnavailable(RuntimeError):
    """No model configured. The assistant has no deterministic fallback: unlike
    parsing or scoring, there is no rules engine that can hold a conversation."""


def _next_sequence(session: Session, conversation_id: uuid.UUID) -> int:
    current = session.execute(
        select(func.max(ChatMessage.sequence)).where(
            ChatMessage.conversation_id == conversation_id
        )
    ).scalar()
    return int(current or 0) + 1


def get_or_create_conversation(
    session: Session, *, recruiter_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> ChatConversation:
    """Ownership is a filter, not a check — an unknown id and someone else's id are
    indistinguishable to the caller."""
    if conversation_id is not None:
        found = session.execute(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.recruiter_id == recruiter_id,
            )
        ).scalars().first()
        if found is None:
            raise LookupError(f"No conversation {conversation_id} for this recruiter.")
        return found

    conversation = ChatConversation(recruiter_id=recruiter_id)
    session.add(conversation)
    session.commit()
    return conversation


def _history(session: Session, conversation_id: uuid.UUID) -> list[dict]:
    """Recent turns in the SDK's content shape. Tool rows are skipped — the SDK
    replays its own function-call parts, and duplicating them here confuses it."""
    rows = session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role.in_([ChatRole.USER.value, ChatRole.ASSISTANT.value]),
        )
        .order_by(ChatMessage.sequence.desc())
        .limit(HISTORY_TURNS)
    ).scalars().all()

    return [
        {
            "role": "user" if row.role == ChatRole.USER.value else "model",
            "parts": [{"text": row.content}],
        }
        for row in reversed(rows)
        if row.content.strip()
    ]


def _record(
    session: Session,
    conversation: ChatConversation,
    *,
    role: str,
    content: str = "",
    tool_name: str | None = None,
    tool_args: dict | None = None,
) -> None:
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            sequence=_next_sequence(session, conversation.id),
            role=role,
            content=content,
            tool_name=tool_name,
            tool_args=tool_args or {},
        )
    )
    session.commit()


def _recording_toolset(
    tools: list, sink: list[ToolCall]
) -> list:
    """Wrap each tool so invocations are recorded as they happen.

    The SDK exposes ``automatic_function_calling_history`` on a response, but it comes
    back empty through the chat interface, so relying on it meant a write could happen
    with nothing in the transcript to show it. Wrapping is also the more durable
    choice: it records what was actually invoked regardless of how the SDK reports it.

    ``__signature__`` is copied explicitly because the SDK builds the function-calling
    schema from it — a bare ``*args, **kwargs`` wrapper would advertise every tool as
    taking no arguments.
    """
    wrapped = []
    for tool in tools:
        def record(*args, _tool=tool, **kwargs):
            sink.append(
                ToolCall(
                    name=_tool.__name__,
                    args={k: v for k, v in kwargs.items() if v not in ("", 0, None)},
                    is_write=_tool.__name__ in WRITE_TOOLS,
                )
            )
            return _tool(*args, **kwargs)

        functools.update_wrapper(record, tool)
        record.__signature__ = inspect.signature(tool)
        wrapped.append(record)
    return wrapped


def send(
    session: Session,
    *,
    user: User,
    message: str,
    conversation_id: uuid.UUID | None = None,
) -> AssistantReply:
    """Run one turn: persist the question, call the model, persist what came back."""
    if not settings.llm_available:
        raise AssistantUnavailable(
            "The assistant needs Gemini credentials. Set GOOGLE_CLOUD_PROJECT and run "
            "`gcloud auth application-default login`."
        )

    from google.genai import types

    from app.ai.llm.llm_service import _get_client

    conversation = get_or_create_conversation(
        session, recruiter_id=user.id, conversation_id=conversation_id
    )
    if not conversation.messages and not conversation.title.startswith(message[:20]):
        conversation.title = message.strip()[:160] or "New conversation"

    history = _history(session, conversation.id)
    _record(session, conversation, role=ChatRole.USER.value, content=message)

    calls: list[ToolCall] = []
    config = types.GenerateContentConfig(
        system_instruction=(
            f"{SYSTEM}\n\n"
            f"Today is {datetime.now(UTC).date().isoformat()}. "
            f"You are assisting {user.name}."
        ),
        temperature=settings.llm_temperature,
        tools=_recording_toolset(build_toolset(session, user), calls),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=MAX_TOOL_CALLS,
            # Keep the call history on the response. Without this the SDK runs the
            # tools but reports nothing about them, so the transcript cannot show
            # that a write happened — which is the one thing a recruiter must be
            # able to see after the fact.
            ignore_call_history=False,
        ),
        thinking_config=types.ThinkingConfig(
            thinking_budget=settings.recruiter_thinking_budget
        ),
    )

    chat = _get_client().chats.create(
        model=settings.recruiter_model, config=config, history=history
    )

    try:
        response = chat.send_message(message)
    except Exception as exc:  # noqa: BLE001 - a failed turn must not lose the transcript
        logger.exception("Assistant turn failed")
        text = (
            "I could not complete that — the model call failed. "
            "The message is saved, so you can retry it."
        )
        _record(session, conversation, role=ChatRole.ASSISTANT.value, content=text)
        raise AssistantUnavailable(str(exc)) from exc

    for call in calls:
        _record(
            session,
            conversation,
            role=ChatRole.TOOL.value,
            content=f"Ran {call.name}",
            tool_name=call.name,
            tool_args=call.args,
        )

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        # A turn that ran tools but produced no prose is a dead end for the reader.
        text = (
            "I ran the lookup but did not get a usable answer back. Try rephrasing?"
        )
    _record(session, conversation, role=ChatRole.ASSISTANT.value, content=text)

    if any(call.is_write for call in calls):
        logger.info(
            "Assistant performed %s write(s) for recruiter %s",
            sum(call.is_write for call in calls), user.id,
        )

    return AssistantReply(
        conversation_id=conversation.id,
        text=text,
        tool_calls=calls,
        engine=settings.recruiter_model,
    )


#: Emitted instead of an empty reply, in both the streaming and blocking paths.
_EMPTY_REPLY = "I ran the lookup but did not get a usable answer back. Try rephrasing?"


def send_streaming(
    session: Session,
    *,
    user: User,
    message: str,
    conversation_id: uuid.UUID | None = None,
) -> Iterator[dict]:
    """One turn, yielding the reply as it is generated.

    Same model call, same tools, same persistence as :func:`send` — the difference is
    only when the reader sees the text. A turn is 2-8 seconds of nothing on screen
    otherwise, and the wait is the model composing prose it could have been emitting
    all along.

    Yields dicts the endpoint serialises as server-sent events:

        {"type": "delta", "text": ...}   a fragment of the reply
        {"type": "tool", "name": ...}    a tool finished, so the UI can name it
        {"type": "done", ...}            ids and the full text, after persistence
        {"type": "error", "message": ...} the turn failed; nothing was persisted

    Persistence happens at the end, once: a half-streamed turn that the client
    abandoned must not leave a truncated assistant message in the transcript.
    """
    from google.genai import types

    from app.ai.llm.llm_service import _get_client

    if not settings.llm_available:
        yield {"type": "error", "message": "The assistant needs Gemini credentials."}
        return

    conversation = get_or_create_conversation(
        session, recruiter_id=user.id, conversation_id=conversation_id
    )
    if not conversation.messages and not conversation.title.startswith(message[:20]):
        conversation.title = message.strip()[:160] or "New conversation"

    history = _history(session, conversation.id)
    _record(session, conversation, role=ChatRole.USER.value, content=message)

    calls: list[ToolCall] = []
    config = types.GenerateContentConfig(
        system_instruction=(
            f"{SYSTEM}\n\n"
            f"Today is {datetime.now(UTC).date().isoformat()}. "
            f"You are assisting {user.name}."
        ),
        temperature=settings.llm_temperature,
        tools=_recording_toolset(build_toolset(session, user), calls),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=MAX_TOOL_CALLS,
            ignore_call_history=False,
        ),
        thinking_config=types.ThinkingConfig(
            thinking_budget=settings.recruiter_thinking_budget
        ),
    )

    chat = _get_client().chats.create(
        model=settings.recruiter_model, config=config, history=history
    )

    chunks: list[str] = []
    announced = 0
    try:
        for chunk in chat.send_message_stream(message):
            # Tools run inside the stream, so a call can complete between two text
            # fragments. Announcing them as they land is what makes a minute-long
            # matching run legible instead of silent.
            while announced < len(calls):
                yield {"type": "tool", "name": calls[announced].name}
                announced += 1

            piece = getattr(chunk, "text", None)
            if piece:
                chunks.append(piece)
                yield {"type": "delta", "text": piece}
    except Exception as exc:  # noqa: BLE001 - a failed turn must not lose the transcript
        logger.exception("Streaming assistant turn failed")
        text = (
            "I could not complete that — the model call failed. "
            "The message is saved, so you can retry it."
        )
        _record(session, conversation, role=ChatRole.ASSISTANT.value, content=text)
        session.commit()
        yield {"type": "error", "message": str(exc), "conversation_id": str(conversation.id)}
        return

    while announced < len(calls):
        yield {"type": "tool", "name": calls[announced].name}
        announced += 1

    for call in calls:
        _record(
            session,
            conversation,
            role=ChatRole.TOOL.value,
            content=f"Ran {call.name}",
            tool_name=call.name,
            tool_args=call.args,
        )

    text = "".join(chunks).strip() or _EMPTY_REPLY
    _record(session, conversation, role=ChatRole.ASSISTANT.value, content=text)
    session.commit()

    if any(call.is_write for call in calls):
        logger.info(
            "Assistant performed %s write(s) for recruiter %s",
            sum(call.is_write for call in calls), user.id,
        )

    yield {
        "type": "done",
        "conversation_id": str(conversation.id),
        "text": text,
        "tool_calls": [
            {"name": c.name, "args": c.args, "is_write": c.is_write} for c in calls
        ],
    }
