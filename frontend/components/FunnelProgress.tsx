"use client";

import type { Funnel, Screening } from "@/lib/types";

const STAGES: Array<{ key: keyof Funnel; label: string; engine: string }> = [
  { key: "pool", label: "Repository", engine: "all active candidates" },
  { key: "filtered", label: "Hard filters", engine: "SQL gates" },
  { key: "retrieved", label: "Semantic retrieval", engine: "pgvector ANN" },
  { key: "reranked", label: "Reranked", engine: "cross-encoder" },
  { key: "evaluated", label: "Evaluated", engine: "LLM + rules" },
  { key: "shortlisted", label: "Shortlist", engine: "weighted scoring" },
];

/**
 * The funnel, measured for this run. Bar width is candidate count relative to the
 * pool, so the taper a recruiter sees is the real cost curve: the expensive stages
 * are the narrow ones.
 */
export function FunnelProgress({ funnel, screening }: { funnel: Funnel; screening: Screening }) {
  const widest = Math.max(funnel.pool, 1);

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Funnel</h2>
        <p className="hint">
          {screening.status === "completed"
            ? "Each stage is cheaper per candidate than the next and more selective than the last."
            : `Running — currently ${screening.stage}.`}
        </p>
      </div>
      <div className="panel-body funnel">
        {STAGES.map((stage) => {
          const count = funnel[stage.key];
          const pct = Math.max((count / widest) * 100, count > 0 ? 3 : 0);
          return (
            <div className="funnel-row" key={stage.key}>
              <div className="funnel-label">
                <strong>{stage.label}</strong>
                <span className="funnel-engine">{stage.engine}</span>
              </div>
              <div className="funnel-track">
                <div
                  className={`funnel-bar ${stage.key === "pool" ? "funnel-bar-pool" : ""}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="funnel-count">{count.toLocaleString()}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
