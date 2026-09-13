"use client";

import { useState } from "react";
import Link from "next/link";
import { MatchExplanation } from "@/components/MatchExplanation";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { ScoreRing } from "@/components/ScoreRing";
import { RecommendationPill } from "@/components/Shared";
import { displayName } from "@/lib/format";
import type { RankedCandidate, Recommendation } from "@/lib/types";
import { RECOMMENDATION_LABELS } from "@/lib/types";

const OPTIONS: Recommendation[] = ["strong_hire", "interview", "maybe", "pass"];

export function ShortlistCard({
  entry,
  onOverride,
  onShortlist,
  stage,
}: {
  entry: RankedCandidate;
  onOverride?: (recommendation: Recommendation, note: string) => Promise<void>;
  /** Opens an application, moving the candidate into the hiring pipeline. */
  onShortlist?: () => Promise<void>;
  /** Pipeline stage if an application already exists for this candidate. */
  stage?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(entry.override_note ?? "");
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);
  const overridden = entry.effective_recommendation !== entry.recommendation;

  return (
    <article className="card">
      <div className="card-head">
        <div className="card-identity">
          <span className="rank-badge">#{entry.rank}</span>
          <div>
            <h3>
              <Link href={`/candidates/${entry.candidate_id}`}>{displayName(entry.name)}</Link>
            </h3>
            <div className="tag-row">
              <RecommendationPill
                recommendation={entry.effective_recommendation}
                overridden={overridden}
              />
              {entry.rerank_score !== null ? (
                <span className="tag">rerank {entry.rerank_score.toFixed(0)}</span>
              ) : null}
              {entry.retrieval_score !== null ? (
                <span className="tag">similarity {entry.retrieval_score.toFixed(3)}</span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="card-score">
          <ScoreRing score={entry.composite_score} />
        </div>
      </div>

      <div className="card-body">
        <ScoreBreakdown subscores={entry.subscores} composite={entry.composite_score} />

        {entry.matched_skills.length || entry.missing_skills.length ? (
          <div className="skill-lists">
            {entry.matched_skills.length ? (
              <div>
                <span className="field-label">Matched</span>
                <div className="tag-row">
                  {entry.matched_skills.map((skill) => (
                    <span className="tag tag-good" key={skill}>
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            {entry.missing_skills.length ? (
              <div>
                <span className="field-label">Missing</span>
                <div className="tag-row">
                  {entry.missing_skills.map((skill) => (
                    <span className="tag tag-bad" key={skill}>
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        <MatchExplanation explanation={entry.explanation} evidence={entry.evidence} />

        {onShortlist ? (
          stage ? (
            // Already in the pipeline: show where, rather than offering to add
            // them again. The endpoint is idempotent, but a button that appears
            // to do nothing is worse than no button.
            <span className="pill pill-good">In pipeline · {stage.replace(/_/g, " ")}</span>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-inline"
              disabled={adding}
              onClick={async () => {
                setAdding(true);
                try {
                  await onShortlist();
                } finally {
                  setAdding(false);
                }
              }}
            >
              {adding ? "Adding…" : "Shortlist"}
            </button>
          )
        ) : null}

        {onOverride ? (
          <div className="override">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
            >
              {open ? "Cancel" : overridden ? "Change decision" : "Override recommendation"}
            </button>

            {open ? (
              <div className="override-form">
                <p className="hint">
                  Your decision is recorded alongside the computed score &mdash; the score
                  and evidence are never rewritten.
                </p>
                <input
                  type="text"
                  placeholder="Why (optional, shown in the audit log)"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
                <div className="btn-row">
                  {OPTIONS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={saving}
                      onClick={async () => {
                        setSaving(true);
                        try {
                          await onOverride(option, note);
                          setOpen(false);
                        } finally {
                          setSaving(false);
                        }
                      }}
                    >
                      {RECOMMENDATION_LABELS[option]}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}
