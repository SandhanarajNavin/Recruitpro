"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { FunnelProgress } from "@/components/FunnelProgress";
import {
  IconChevron,
  IconJobs,
  IconPlus,
  IconResumes,
  IconSearch,
  IconShortlist,
} from "@/components/Icons";
import { ShortlistCard } from "@/components/ShortlistCard";
import { ErrorBanner, Loading, ModeChip, RecommendationPill } from "@/components/Shared";
import { ScoreRing } from "@/components/ScoreRing";
import { StatusPill } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { displayName } from "@/lib/format";
import type {
  Funnel,
  JobDetail,
  RankedCandidate,
  Recommendation,
  Screening,
  ScreeningResultsResponse,
} from "@/lib/types";
import { SCREENING_TERMINAL } from "@/lib/types";

const POLL_MS = 1200;

type SortKey = "score" | "name" | "recommendation";

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: "score", label: "Match score" },
  { key: "name", label: "Name" },
  { key: "recommendation", label: "Recommendation" },
];

/** Ranking used when sorting by verdict rather than by number. */
const RECOMMENDATION_RANK: Record<Recommendation, number> = {
  strong_hire: 0,
  interview: 1,
  maybe: 2,
  pass: 3,
};

function longDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function shortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** "AI Engineer · 2.6 years · Chennai" — empty parts drop out rather than
 *  leaving stray separators for a resume that never yielded a location. */
function identityLine(entry: RankedCandidate): string {
  return [
    entry.role,
    entry.years !== null ? `${entry.years.toFixed(1)} years` : null,
    entry.location,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The four-step summary above the results.
 *
 * Distinct from the six-stage `FunnelProgress`, which describes the machinery. This
 * says what happened to the people: how many existed, how many cleared the hard
 * filters, how many scored well, how many are being recommended.
 */
function Overview({ funnel, strong }: { funnel: Funnel; strong: number }) {
  const share = (part: number, whole: number) =>
    whole > 0 ? `${Math.round((part / whole) * 100)}%` : "—";

  const steps = [
    { value: funnel.pool, label: "Total candidates", note: null as string | null },
    {
      value: funnel.filtered,
      label: "Eligible",
      note: `${share(funnel.filtered, funnel.pool)} passed`,
    },
    {
      value: strong,
      label: "Strong matches",
      note: `${share(strong, funnel.filtered)} of eligible`,
    },
    {
      value: funnel.shortlisted,
      label: "Recommended",
      note: `${share(funnel.shortlisted, strong)} of strong matches`,
    },
  ];

  return (
    <section className="card">
      <div className="card-head">
        <h2>Screening overview</h2>
      </div>
      <div className="card-body">
        <ol className="overview">
          {steps.map((step, index) => (
            <li className="overview-step" key={step.label}>
              <span className="overview-value">{step.value}</span>
              <span className="overview-text">
                <strong>{step.label}</strong>
                {step.note ? <small>{step.note}</small> : null}
              </span>
              {index < steps.length - 1 ? (
                <span className="overview-arrow" aria-hidden="true">
                  &rarr;
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function AiSummary({ summary }: { summary: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="card summary-card">
      <div className="card-body summary-body">
        <span className="summary-mark" aria-hidden="true">
          <IconShortlist size={17} />
        </span>
        <div className="summary-text">
          <h2>AI summary</h2>
          <p>{summary}</p>
        </div>
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(summary);
              setCopied(true);
              setTimeout(() => setCopied(false), 1800);
            } catch {
              // Clipboard access is refused in some contexts; the text is on
              // screen and selectable, so there is nothing to recover.
            }
          }}
        >
          {copied ? "Copied" : "Copy summary"}
        </button>
      </div>
    </section>
  );
}

/** One collapsed result row; expanding swaps in the full card with the evidence. */
function ResultRow({
  entry,
  stage,
  onShortlist,
  onOverride,
  onHide,
}: {
  entry: RankedCandidate;
  stage: string | null;
  onShortlist: () => Promise<void>;
  onOverride: (recommendation: Recommendation, note: string) => Promise<void>;
  onHide: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [hiding, setHiding] = useState(false);
  const line = identityLine(entry);
  const skills = entry.matched_skills;
  // Rejected for *this* job. Shown rather than hidden: the point is to recognise
  // someone already turned down for this role when their name comes back up.
  const rejected = stage === "rejected";

  return (
    <li
      className={[
        "result-row",
        open ? "result-row-open" : "",
        rejected ? "result-row-rejected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="result-main">
        <span className="rank-badge">{entry.rank}</span>

        <div className="result-identity">
          <div className="result-name-line">
            <Link href={`/candidates/${entry.candidate_id}`} className="result-name">
              {displayName(entry.name)}
            </Link>
            <RecommendationPill
              recommendation={entry.effective_recommendation}
              overridden={entry.effective_recommendation !== entry.recommendation}
            />
            {rejected ? <span className="pill pill-bad">Rejected for this job</span> : null}
          </div>
          {line ? <p className="result-meta">{line}</p> : null}
          {skills.length ? (
            <div className="tag-row">
              {skills.slice(0, 4).map((skill) => (
                // title: a long parsed skill is truncated to fit the row.
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
          <ScoreRing score={entry.composite_score} />
        </div>

        <div className="result-actions">
          <Link href={`/candidates/${entry.candidate_id}`} className="btn btn-outline btn-sm">
            View profile
          </Link>
          {rejected ? (
            // Nothing to add them to, and nothing to advance — the useful action on
            // a rejected row is taking it out of the list.
            <button
              type="button"
              className="btn btn-outline btn-sm"
              disabled={hiding}
              onClick={async () => {
                setHiding(true);
                try {
                  await onHide();
                } finally {
                  setHiding(false);
                }
              }}
            >
              {hiding ? "Removing…" : "Remove"}
            </button>
          ) : stage ? (
            // Already in the pipeline: say where, rather than offering to add them
            // again. The endpoint is idempotent, but a button that appears to do
            // nothing is worse than no button.
            <span className="pill pill-good">In pipeline · {stage.replace(/_/g, " ")}</span>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-sm"
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
          )}
          <button
            type="button"
            className="icon-btn result-toggle"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={open ? `Hide ${entry.name}'s detail` : `Show ${entry.name}'s detail`}
          >
            <IconChevron size={16} className={open ? "chev-up" : "chev-down"} />
          </button>
        </div>
      </div>

      {open ? (
        <div className="result-detail">
          <ShortlistCard entry={entry} onOverride={onOverride} />
        </div>
      ) : null}
    </li>
  );
}

function SidebarJob({ detail }: { detail: JobDetail }) {
  const { job, requirement } = detail;
  const experience = requirement
    ? requirement.min_years_experience > 0
      ? `${requirement.min_years_experience}+ years`
      : "No minimum"
    : "—";

  const rows: Array<[string, string]> = [
    ["Job title", job.title],
    ["Department", job.department ?? "—"],
    ["Location", job.location ?? "—"],
    ["Experience", experience],
    ["Employment type", job.employment_type ?? "—"],
    ["Posted on", shortDate(job.created_at)],
  ];

  // Ordered by importance so the chips lead with what the role actually needs.
  const skills = [...(requirement?.required_skills ?? [])]
    .sort((a, b) => b.importance - a.importance)
    .map((entry) => entry.skill)
    .filter(Boolean);

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Job details</h2>
          {/* No job-edit endpoint exists, so this opens the job rather than
              offering an edit that would fail. */}
          <Link href={`/jobs/${job.id}`} className="card-link">
            Open job
          </Link>
        </div>
        <div className="card-body">
          <dl className="detail-list">
            {rows.map(([label, value]) => (
              <div className="detail-pair" key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {skills.length ? (
        <section className="card">
          <div className="card-head">
            <h2>Required skills</h2>
          </div>
          <div className="card-body">
            <div className="tag-row">
              {skills.map((skill) => (
                <span className="chip" key={skill}>
                  {skill}
                </span>
              ))}
            </div>
          </div>
        </section>
      ) : null}
    </>
  );
}

function SidebarSettings({
  screening,
  analysed,
}: {
  screening: Screening;
  analysed: number;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Screening settings</h2>
      </div>
      <div className="card-body">
        <dl className="detail-list">
          <div className="detail-pair">
            <dt>Mode</dt>
            <dd>
              <ModeChip mode={screening.mode} />
            </dd>
          </div>
          <div className="detail-pair">
            <dt>Screening date</dt>
            <dd>{longDate(screening.finished_at ?? screening.created_at)}</dd>
          </div>
          <div className="detail-pair">
            <dt>Candidates analysed</dt>
            <dd>{analysed}</dd>
          </div>
          <div className="detail-pair">
            <dt>Status</dt>
            <dd>
              <StatusPill status={screening.status} />
            </dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

function ScoringNote({ weights }: { weights: Record<string, number> }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card note-card">
      <div className="card-body">
        <h2 className="note-title">How are candidates scored?</h2>
        <p className="note-body">
          Candidates are scored on required skills, experience, role alignment, industry
          relevance and semantic similarity. Scores are decision support, not a decision.
        </p>
        <button
          type="button"
          className="btn btn-ghost btn-sm note-toggle"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          {open ? "Show less" : "Learn more"}
        </button>
        {open ? (
          <>
            <ul className="weight-list">
              {Object.entries(weights).map(([category, weight]) => (
                <li key={category}>
                  <span>{category.replace(/_/g, " ")}</span>
                  <strong>{Math.round(weight * 100)}%</strong>
                </li>
              ))}
            </ul>
            <p className="note-body">
              The weighted total sets the band: 82 and above is a strong hire, 68 an
              interview, 52 a maybe. The bands are fixed in code, not chosen by the model.
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}

export default function ScreeningPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [data, setData] = useState<ScreeningResultsResponse | null>(null);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** candidate id -> pipeline stage, for candidates already in the pipeline. */
  const [stages, setStages] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<SortKey>("score");
  const [showRun, setShowRun] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const jobId = data?.screening.job_id;

  const loadStages = useCallback(async () => {
    if (!jobId) return;
    try {
      const rows = await api.applications({ job_id: jobId });
      setStages(Object.fromEntries(rows.map((row) => [row.candidate_id, row.stage])));
    } catch {
      // A failed stage lookup must not blank the results the recruiter came for.
    }
  }, [jobId]);

  const load = useCallback(async () => {
    try {
      // Results are safe to read while running — the endpoint returns whatever has
      // been persisted so far, so the overview fills in live.
      setData(await api.screeningResults(id));
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the screening.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!jobId) return;
    // The sidebar is supporting detail; failing to load it must not take the
    // results down with it.
    api.job(jobId).then(setJob).catch(() => undefined);
  }, [jobId]);

  // Poll until the run reaches a terminal state, then stop.
  useEffect(() => {
    if (!data) return;
    if (SCREENING_TERMINAL.includes(data.screening.status)) return;

    timerRef.current = setTimeout(() => void load(), POLL_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [data, load]);

  useEffect(() => {
    void loadStages();
  }, [loadStages]);

  const shortlist = useMemo(() => {
    const rows = [...(data?.shortlist ?? [])];
    if (sort === "name") rows.sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === "recommendation") {
      rows.sort(
        (a, b) =>
          RECOMMENDATION_RANK[a.effective_recommendation] -
            RECOMMENDATION_RANK[b.effective_recommendation] ||
          b.composite_score - a.composite_score,
      );
    } else rows.sort((a, b) => b.composite_score - a.composite_score);
    return rows;
  }, [data?.shortlist, sort]);

  if (error && !data) return <ErrorBanner message={error} onRetry={load} />;
  if (!data) return <Loading label="Loading screening…" />;

  const { screening, funnel, also_considered: alsoConsidered } = data;
  const running = !SCREENING_TERMINAL.includes(screening.status);
  const analysed = funnel.evaluated || data.shortlist.length + alsoConsidered.length;
  const strong = [...data.shortlist, ...alsoConsidered].filter(
    (entry) => entry.composite_score >= 82,
  ).length;

  async function override(candidateId: string, recommendation: Recommendation, note: string) {
    await api.override(screening.id, candidateId, recommendation, note || undefined);
    await load();
    await loadStages();
  }

  // Named for the action, not the noun: `shortlist` is the sorted results array.
  async function addToPipeline(candidateId: string) {
    await api.openApplication({
      candidate_id: candidateId,
      job_id: screening.job_id,
      stage: "shortlisted",
    });
    await loadStages();
  }

  /** Takes a candidate out of this job's rankings, now and on every future run. */
  async function hideFromRankings(candidateId: string) {
    await api.hideFromRankings(screening.id, candidateId);
    // Reload rather than splicing the row out locally: the endpoint also changes
    // what a re-run would return, and `load` is the single source of that truth.
    await load();
  }

  async function rerun() {
    setRerunning(true);
    try {
      const started = await api.startScreening(screening.job_id);
      window.location.href = `/screenings/${started.screening.id}`;
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not start a new run.");
      setRerunning(false);
    }
  }

  /** Results as CSV, built from what is already loaded — no export endpoint needed. */
  function download() {
    const header = ["Rank", "Name", "Role", "Years", "Location", "Score", "Recommendation"];
    const escape = (value: string) => `"${value.replace(/"/g, '""')}"`;
    const lines = [...data!.shortlist, ...alsoConsidered].map((entry) =>
      [
        entry.rank,
        entry.name,
        entry.role ?? "",
        entry.years ?? "",
        entry.location ?? "",
        entry.composite_score.toFixed(1),
        entry.effective_recommendation,
      ]
        .map((cell) => escape(String(cell)))
        .join(","),
    );
    const blob = new Blob([[header.join(","), ...lines].join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `screening-${screening.id.slice(0, 8)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="screen-layout">
      <div className="screen-main">
        <Link href="/jobs" className="back-link">
          <IconChevron size={15} className="chev-left" /> Back to Jobs
        </Link>

        <div className="masthead">
          <div>
            <h1 className="masthead-title">
              {job?.job.title ?? "Screening results"}
              <StatusPill status={screening.status} />
            </h1>
            <p>
              {running ? (
                <>
                  <span className="spinner" aria-hidden="true" /> {screening.stage} — this
                  page updates itself.
                </>
              ) : screening.status === "failed" ? (
                "This run failed."
              ) : (
                <>
                  Screening completed &middot; {analysed} candidate
                  {analysed === 1 ? "" : "s"} analyzed &middot;{" "}
                  {longDate(screening.finished_at ?? screening.created_at)}
                </>
              )}
            </p>
          </div>
          <div className="btn-row">
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => setShowRun((value) => !value)}
              aria-expanded={showRun}
            >
              {showRun ? "Hide run details" : "View run details"}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={rerun}
              disabled={rerunning || running}
            >
              {rerunning ? "Starting…" : "Run screening again"}
            </button>
          </div>
        </div>

        {screening.error ? <ErrorBanner message={screening.error} /> : null}
        {error ? <ErrorBanner message={error} /> : null}

        <Overview funnel={funnel} strong={strong} />

        {showRun ? (
          <>
            <FunnelProgress funnel={funnel} screening={screening} />
            {screening.degradations.length ? (
              <section className="card">
                <div className="card-head">
                  <h2>How this run was adjusted</h2>
                </div>
                <div className="card-body">
                  <ul className="bullet-list">
                    {screening.degradations.map((note, index) => (
                      <li key={index}>{note}</li>
                    ))}
                  </ul>
                </div>
              </section>
            ) : null}
          </>
        ) : null}

        {screening.panel_summary ? <AiSummary summary={screening.panel_summary} /> : null}

        <section className="card">
          <div className="card-head">
            <h2>Top candidates ({shortlist.length})</h2>
            <label className="sort-field">
              <span className="muted">Sort by:</span>
              <select
                className="filter-select"
                value={sort}
                onChange={(event) => setSort(event.target.value as SortKey)}
                aria-label="Sort candidates"
              >
                {SORTS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="card-body card-body-flush">
            {shortlist.length ? (
              <ul className="result-list">
                {shortlist.map((entry) => (
                  <ResultRow
                    key={entry.candidate_id}
                    entry={entry}
                    stage={stages[entry.candidate_id] ?? null}
                    onShortlist={() => addToPipeline(entry.candidate_id)}
                    onOverride={(recommendation, note) =>
                      override(entry.candidate_id, recommendation, note)
                    }
                    onHide={() => hideFromRankings(entry.candidate_id)}
                  />
                ))}
              </ul>
            ) : running ? (
              <div className="empty-state">
                <span className="spinner" aria-hidden="true" /> Scoring candidates…
              </div>
            ) : (
              <div className="empty-state">
                No candidate reached the shortlist. Open Run Details — if the repository
                row is zero, no resumes have finished ingesting yet.
              </div>
            )}
          </div>
        </section>

        {alsoConsidered.length ? (
          <section className="card">
            <div className="card-head">
              <h2>Also considered</h2>
              <p className="hint">
                Scored against the same requirements but outside the top{" "}
                {data.shortlist.length}.
              </p>
            </div>
            <div className="card-body card-body-flush">
              <div className="table-wrap">
                <table className="listing">
                  <thead>
                    <tr>
                      <th className="num">Rank</th>
                      <th>Candidate</th>
                      <th className="num">Score</th>
                      <th>Recommendation</th>
                      <th>Kept out by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alsoConsidered.map((entry) => (
                      <tr key={entry.candidate_id}>
                        <td className="num">{entry.rank}</td>
                        <td>
                          <Link href={`/candidates/${entry.candidate_id}`}>{displayName(entry.name)}</Link>
                        </td>
                        <td className="num">{entry.composite_score.toFixed(1)}</td>
                        <td>
                          <RecommendationPill recommendation={entry.effective_recommendation} />
                        </td>
                        <td>
                          {entry.missing_skills.length ? (
                            <span className="hint">
                              missing {entry.missing_skills.slice(0, 3).join(", ")}
                            </span>
                          ) : (
                            <span className="hint">
                              {entry.evidence.concerns?.[0] ?? "lower weighted total"}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        ) : null}

        <p className="footnote">
          Scores are decision support, not a decision. The pipeline reads only what is on
          the resume, so it inherits whatever the resume over- or under-states — open a
          candidate and check the evidence before acting on a rank.
        </p>
      </div>

      <aside className="screen-side">
        {job ? <SidebarJob detail={job} /> : null}

        <SidebarSettings screening={screening} analysed={analysed} />

        <section className="card">
          <div className="card-head">
            <h2>Actions</h2>
          </div>
          <div className="card-body side-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={rerun}
              disabled={rerunning || running}
            >
              <IconPlus size={15} /> {rerunning ? "Starting…" : "Run screening again"}
            </button>
            <Link href="/candidates" className="btn btn-outline btn-sm">
              <IconSearch size={15} /> View all candidates
            </Link>
            <button type="button" className="btn btn-outline btn-sm" onClick={download}>
              <IconResumes size={15} /> Download results
            </button>
            <Link href={`/jobs/${screening.job_id}`} className="btn btn-outline btn-sm">
              <IconJobs size={15} /> Open job
            </Link>
          </div>
        </section>

        <ScoringNote weights={screening.weights} />
      </aside>
    </div>
  );
}
