"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { RejectedUpload, ResumeStatus } from "@/lib/types";
import { RESUME_TERMINAL } from "@/lib/types";

interface Tracked {
  id: string;
  filename: string;
  status: ResumeStatus;
  error: string | null;
}

const POLL_MS = 1500;

/**
 * Drag-and-drop upload with per-file ingestion status.
 *
 * Upload returns 202 and the pipeline continues on a worker, so the component polls
 * each resume until it reaches a terminal state. Partial failure is normal and shown
 * per file — one scanned image must not hide twenty-nine successful uploads.
 */
export function ResumeUploader({ onIngested }: { onIngested?: () => void }) {
  const [tracked, setTracked] = useState<Tracked[]>([]);
  const [rejected, setRejected] = useState<RejectedUpload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const notifiedRef = useRef(false);

  const upload = useCallback(async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setRejected([]);
    notifiedRef.current = false;

    try {
      const result = await api.uploadResumes(files);
      setRejected(result.rejected);
      setTracked((current) => [
        ...current,
        ...result.accepted.map((resume) => ({
          id: resume.id,
          filename: resume.original_filename,
          status: resume.status,
          error: resume.error,
        })),
      ]);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }, []);

  // Poll only the resumes still in flight; stop entirely once all are terminal.
  useEffect(() => {
    const pending = tracked.filter((item) => !RESUME_TERMINAL.includes(item.status));
    if (!pending.length) {
      if (tracked.length && !notifiedRef.current) {
        notifiedRef.current = true;
        onIngested?.();
      }
      return;
    }

    const timer = setTimeout(async () => {
      const updates = await Promise.all(
        pending.map(async (item) => {
          try {
            const status = await api.resumeStatus(item.id);
            return { id: item.id, status: status.status, error: status.error };
          } catch {
            return null;
          }
        }),
      );

      setTracked((current) =>
        current.map((item) => {
          const update = updates.find((entry) => entry && entry.id === item.id);
          return update ? { ...item, status: update.status, error: update.error } : item;
        }),
      );
    }, POLL_MS);

    return () => clearTimeout(timer);
  }, [tracked, onIngested]);

  const ready = tracked.filter((item) => item.status === "ready").length;
  const failed = tracked.filter((item) => item.status === "failed").length;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Add resumes</h2>
        <p className="hint">
          PDF, DOCX or plain text. Each file is parsed and embedded once, then reusable
          against every future job description.
        </p>
      </div>

      <div className="panel-body">
        <div
          className={dragging ? "dropzone dropzone-active" : "dropzone"}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            void upload(Array.from(event.dataTransfer.files));
          }}
        >
          <p>Drop resumes here</p>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            {busy ? "Uploading…" : "Choose files"}
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.doc,.txt,.md"
            hidden
            onChange={(event) => {
              void upload(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        {rejected.length ? (
          <div className="error-banner">
            <strong>{rejected.length} file(s) rejected</strong>
            <ul>
              {rejected.map((item) => (
                <li key={item.filename}>
                  {item.filename} — {item.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {tracked.length ? (
          <div className="upload-list">
            <div className="upload-summary">
              {ready} ready · {tracked.length - ready - failed} processing · {failed} failed
            </div>
            {tracked.map((item) => (
              <div className="upload-row" key={item.id}>
                <span className="upload-name">{item.filename}</span>
                <span
                  className={
                    item.status === "ready"
                      ? "status-badge status-ready"
                      : item.status === "failed"
                        ? "status-badge status-failed"
                        : "status-badge status-working"
                  }
                >
                  {item.status}
                </span>
                {item.error ? <span className="upload-error">{item.error}</span> : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
