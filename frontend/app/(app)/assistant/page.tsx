"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { IconPlus, IconSend } from "@/components/Icons";
import { ApiError, api } from "@/lib/api";
import type { ChatMessageRow, Conversation } from "@/lib/types";

/**
 * The recruiter assistant.
 *
 * A turn is synchronous and can take a minute when the model runs the matching
 * pipeline, so the pending state is explicit rather than a spinner that looks stuck.
 */

const SUGGESTIONS = [
  "How many candidates do I have, and what roles?",
  "Who has the most Kubernetes experience?",
  "Show me my open job descriptions",
  "Which candidates have 5+ years and know Python?",
];

/** Local echo of the user's message, before the server assigns it an id. */
type PendingRow = Pick<ChatMessageRow, "role" | "content" | "tool_name"> & {
  id: string;
  pending?: boolean;
};

function Bubble({ row }: { row: PendingRow }) {
  return (
    <div className={row.role === "user" ? "chat-row chat-row-user" : "chat-row"}>
      <div className={row.role === "user" ? "bubble bubble-user" : "bubble"}>
        {row.content.split("\n").map((line, index) =>
          line.trim() ? <p key={index}>{line}</p> : <br key={index} />,
        )}
      </div>
    </div>
  );
}

export default function AssistantPage() {
  const [rows, setRows] = useState<PendingRow[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await api.conversations());
    } catch {
      // A failed sidebar load must not block the conversation itself.
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [rows, busy]);

  async function openConversation(id: string) {
    setError(null);
    try {
      const detail = await api.conversation(id);
      setConversationId(id);
      setRows(
        detail.messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          tool_name: m.tool_name,
        })),
      );
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not open that conversation.");
    }
  }

  function startNew() {
    setConversationId(undefined);
    setRows([]);
    setError(null);
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;

    setDraft("");
    setError(null);
    setBusy(true);
    setRows((current) => [
      ...current,
      { id: `local-${Date.now()}`, role: "user", content: message, tool_name: null },
    ]);

    try {
      const reply = await api.chat(message, conversationId);
      setConversationId(reply.conversation_id);
      setRows((current) => [
        ...current,
        ...reply.tool_calls.map((call, index) => ({
          id: `tool-${Date.now()}-${index}`,
          role: "tool" as const,
          content: call.name,
          tool_name: call.is_write ? `${call.name} · changed data` : call.name,
        })),
        {
          id: `reply-${Date.now()}`,
          role: "assistant" as const,
          content: reply.message,
          tool_name: null,
        },
      ]);
      void loadConversations();
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "The assistant did not respond. Your message is saved — try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  // Tool calls are an implementation detail of a turn; the transcript shows only
  // what the recruiter and the assistant said.
  const visibleRows = rows.filter((row) => row.role !== "tool");

  return (
    <div className="chat-layout">
      <aside className="chat-side">
        <button type="button" className="btn btn-primary chat-new" onClick={startNew}>
          <IconPlus size={16} /> New conversation
        </button>
        <div className="chat-history">
          {conversations.length === 0 ? (
            <p className="dash-empty">No conversations yet.</p>
          ) : (
            conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                className={
                  conversation.id === conversationId
                    ? "chat-history-item chat-history-on"
                    : "chat-history-item"
                }
                onClick={() => void openConversation(conversation.id)}
              >
                {conversation.title}
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="chat-main">
        <div className="chat-scroll">
          {visibleRows.length === 0 ? (
            <div className="chat-intro">
              <h2>Ask about your candidates</h2>
              <p>
                The assistant reads your repository and can run matching, create a job
                description, or record your recommendation. It reports scores from the
                scoring engine — it never invents one.
              </p>
              <div className="chat-suggestions">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => void send(suggestion)}
                    disabled={busy}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            visibleRows.map((row) => <Bubble key={row.id} row={row} />)
          )}

          {busy ? (
            <div className="chat-row">
              <div className="bubble bubble-thinking">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
                <em>Working — a matching run can take a minute.</em>
              </div>
            </div>
          ) : null}

          {error ? <div className="error-banner">{error}</div> : null}
          <div ref={endRef} />
        </div>

        <form
          className="chat-compose"
          onSubmit={(event) => {
            event.preventDefault();
            void send(draft);
          }}
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask about candidates, jobs or a screening…"
            aria-label="Message the assistant"
            disabled={busy}
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !draft.trim()}
            aria-label="Send"
          >
            <IconSend size={17} />
          </button>
        </form>
      </section>
    </div>
  );
}
