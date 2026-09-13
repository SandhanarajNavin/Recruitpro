"use client";

import type { Recommendation } from "@/lib/types";
import { RECOMMENDATION_LABELS } from "@/lib/types";

const REC_CLASS: Record<Recommendation, string> = {
  strong_hire: "rec-strong",
  interview: "rec-interview",
  maybe: "rec-maybe",
  pass: "rec-pass",
};

export function RecommendationPill({
  recommendation,
  overridden = false,
}: {
  recommendation: Recommendation;
  overridden?: boolean;
}) {
  return (
    <span className={`rec-pill ${REC_CLASS[recommendation]}`}>
      {RECOMMENDATION_LABELS[recommendation]}
      {overridden ? <span className="rec-override" title="Set by a recruiter">· override</span> : null}
    </span>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-banner">
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty-state">
      <span className="spinner" aria-hidden="true" /> {label}
    </div>
  );
}

export function ModeChip({ mode }: { mode: "gemini" | "offline" }) {
  return (
    <span className={`mode-chip ${mode === "gemini" ? "mode-live" : "mode-offline"}`}>
      <span className="dot" aria-hidden="true" />
      {mode === "gemini" ? "Gemini engine" : "Deterministic engine"}
    </span>
  );
}

/** Status dot for an ingestion state. Colour-plus-text, never colour alone. */
export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="status-badge status-unknown">no resume</span>;
  const tone =
    status === "ready"
      ? "status-ready"
      : status === "failed"
        ? "status-failed"
        : "status-working";
  return <span className={`status-badge ${tone}`}>{status}</span>;
}
