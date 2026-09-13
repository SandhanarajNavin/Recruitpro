"use client";

import { useState } from "react";
import Link from "next/link";
import { IconChevron } from "@/components/Icons";
import { MatchExplanation } from "@/components/MatchExplanation";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { ScoreRing } from "@/components/ScoreRing";
import { RecommendationPill } from "@/components/Shared";
import type { JobMatch } from "@/lib/types";

/** "Engineering · Bangalore · Screened Sep 8" — whatever of it exists. */
function jobLine(match: JobMatch): string {
  const screened = match.screened_at
    ? `Screened ${new Date(match.screened_at).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })}`
    : null;
  return [
    match.department,
    match.location,
    screened,
    // Where they placed against everyone else in that run — a score means more
    // next to the field it was measured against.
    `#${match.rank} in that run`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * One job a candidate was scored against, in the same shape as a candidate row on
 * the job page.
 *
 * The row expands in place rather than linking away: the question it answers is
 * "which of my roles does this person fit, and why", and following a link to the
 * job loses the comparison the list exists to show. The breakdown, skills and
 * explanation are the ones the screening produced, rendered by the same components
 * the job page uses, so the two views cannot drift apart.
 */
export function JobMatchRow({ match, position }: { match: JobMatch; position: number }) {
  const [open, setOpen] = useState(false);
  const line = jobLine(match);
  const skills = match.matched_skills;

  return (
    <li className={open ? "result-row result-row-open" : "result-row"}>
      <div className="result-main">
        <span className="rank-badge">{position}</span>

        <div className="result-identity">
          <div className="result-name-line">
            <Link href={`/jobs/${match.job_id}`} className="result-name">
              {match.job_title}
            </Link>
            <RecommendationPill recommendation={match.recommendation} />
            {match.shortlisted ? (
              <span className="pill pill-good">Shortlisted</span>
            ) : null}
            {match.job_status !== "open" ? (
              // A strong fit for a closed role is not something to act on.
              <span className="pill pill-mute">{match.job_status}</span>
            ) : null}
          </div>
          {line ? <p className="result-meta">{line}</p> : null}
          {skills.length ? (
            <div className="tag-row">
              {skills.slice(0, 4).map((skill) => (
                <span className="chip" key={skill} title={skill}>
                  {skill}
                </span>
              ))}
              {skills.length > 4 ? (
                <span className="chip chip-more">+{skills.length - 4}</span>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="result-score">
          <ScoreRing score={match.score} />
        </div>

        <div className="result-actions">
          <Link href={`/jobs/${match.job_id}`} className="btn btn-outline btn-sm">
            Open job
          </Link>
          <Link
            href={`/screenings/${match.screening_id}`}
            className="btn btn-outline btn-sm"
          >
            Screening
          </Link>
          <button
            type="button"
            className="icon-btn result-toggle"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={
              open ? `Hide why ${match.job_title} matched` : `Show why ${match.job_title} matched`
            }
          >
            <IconChevron size={16} className={open ? "chev-up" : "chev-down"} />
          </button>
        </div>
      </div>

      {open ? (
        <div className="result-detail">
          <article className="card">
            <div className="card-body">
              <ScoreBreakdown subscores={match.subscores} composite={match.score} />

              {match.matched_skills.length || match.missing_skills.length ? (
                <div className="skill-lists">
                  {match.matched_skills.length ? (
                    <div>
                      <span className="field-label">Matched</span>
                      <div className="tag-row">
                        {match.matched_skills.map((skill) => (
                          <span className="tag tag-good" key={skill}>
                            {skill}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {match.missing_skills.length ? (
                    <div>
                      <span className="field-label">Missing for this role</span>
                      <div className="tag-row">
                        {match.missing_skills.map((skill) => (
                          <span className="tag tag-bad" key={skill}>
                            {skill}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              <MatchExplanation
                explanation={match.explanation}
                evidence={match.evidence}
              />
            </div>
          </article>
        </div>
      ) : null}
    </li>
  );
}
