"use client";

import type { Subscore } from "@/lib/types";

/**
 * Where the composite came from. Shows score, weight and contribution per category
 * so the total is reproducible by hand — the point of keeping the arithmetic in code.
 */
export function ScoreBreakdown({ subscores, composite }: { subscores: Subscore[]; composite: number }) {
  const total = subscores.reduce((sum, entry) => sum + entry.contribution, 0);

  return (
    <div className="breakdown-body">
      <div className="dimensions">
        {subscores.map((entry) => (
          <div className="dimension" key={entry.category}>
            <div className="dimension-head">
              <span className="dimension-name">{entry.label}</span>
              <span className="dimension-weight">×{entry.weight.toFixed(2)}</span>
            </div>
            <div className="bar">
              <div className="bar-fill" style={{ width: `${Math.min(100, entry.score)}%` }} />
            </div>
            <div className="dimension-numbers">
              <span className="dimension-score">{entry.score.toFixed(0)}</span>
              <span className="dimension-contribution">
                +{entry.contribution.toFixed(1)}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="breakdown-total">
        <span>Weighted total</span>
        <span className="breakdown-sum">{total.toFixed(1)}</span>
        {Math.abs(total - composite) > 0.05 ? (
          <span className="breakdown-warn">
            stored composite {composite.toFixed(1)} — weights changed since this run
          </span>
        ) : null}
      </div>
    </div>
  );
}
