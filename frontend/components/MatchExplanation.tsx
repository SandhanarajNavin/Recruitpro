"use client";

import { useState } from "react";
import type { CategoryAssessment, EvidenceRecord, Explanation } from "@/lib/types";

/**
 * Why match, then why not (architecture doc §12), with the raw evidence record
 * underneath. Stacked rather than in two columns: the reasons are prose of
 * unpredictable length, and side by side one column ran to six short lines while
 * the other wrapped into a narrow ribbon of text.
 *
 * The gaps are never hidden behind a toggle: a shortlist that only shows reasons
 * to say yes is a worse tool than no shortlist.
 */
export function MatchExplanation({
  explanation,
  evidence,
}: {
  explanation: Explanation | null;
  evidence: EvidenceRecord;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const categories: CategoryAssessment[] = evidence.categories ?? [];

  const whyMatch = explanation?.why_match ?? evidence.strengths ?? [];
  const whyNot = explanation?.why_not ?? evidence.concerns ?? [];

  return (
    <div className="explanation">
      {explanation?.verdict ? <p className="verdict">{explanation.verdict}</p> : null}

      <div className="why-stack">
        <div className="why why-match">
          <h4>Why match</h4>
          {whyMatch.length ? (
            <ul>
              {whyMatch.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="hint">No supporting evidence was recorded.</p>
          )}
        </div>

        <div className="why why-not">
          <h4>Why not &middot; gaps</h4>
          {whyNot.length ? (
            <ul>
              {whyNot.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="hint">No gap recorded &mdash; verify in interview.</p>
          )}
        </div>
      </div>

      {categories.length ? (
        <>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setShowEvidence((value) => !value)}
            aria-expanded={showEvidence}
          >
            {showEvidence ? "Hide" : "Show"} per-requirement evidence
          </button>

          {showEvidence ? (
            <div className="evidence">
              {categories.map((category) => (
                <div className="evidence-group" key={category.category}>
                  <div className="evidence-head">
                    <span>{category.category.replace(/_/g, " ")}</span>
                    <span className="evidence-score">{category.score.toFixed(0)}</span>
                  </div>
                  <p className="hint">{category.reasoning}</p>
                  <ul>
                    {category.verdicts.map((verdict, index) => (
                      <li key={index} className={verdict.met ? "met" : "unmet"}>
                        <span className="verdict-mark">{verdict.met ? "met" : "not met"}</span>
                        <span className="verdict-req">{verdict.requirement}</span>
                        <span className="verdict-conf">{verdict.confidence} confidence</span>
                        {verdict.evidence.length ? (
                          <span className="verdict-quote">&ldquo;{verdict.evidence[0]}&rdquo;</span>
                        ) : (
                          <span className="verdict-quote verdict-none">
                            no supporting evidence found
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {evidence.evaluated_by ? (
                <p className="hint">Evaluated by {evidence.evaluated_by}.</p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
