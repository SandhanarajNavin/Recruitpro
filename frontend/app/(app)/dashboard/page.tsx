"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  IconCandidates,
  IconChevron,
  IconJobs,
  IconResumes,
  IconShortlist,
} from "@/components/Icons";
import { ErrorBanner, Loading } from "@/components/Shared";
import { PipelineBars, WeeklyActivity } from "@/components/DashboardCharts";
import { RecommendationPill } from "@/components/Shared";
import { PipelineStrip, StatusPill } from "@/components/ui";
import { ApiError, api, getStoredUser } from "@/lib/api";
import { displayName } from "@/lib/format";
import type {
  AttentionItem,
  Dashboard,
  Metric,
  OpenJobRow,
  ReviewCandidate,
} from "@/lib/types";

/**
 * Recruiter dashboard.
 *
 * One `/dashboard` call feeds the whole page: four headline cards, then the
 * pipeline, the jobs needing a decision, and the two tables, each full width and
 * stacked. The activity feed lives in the top-bar bell instead of a panel here.
 *
 * The role donut and the 30-day area chart from the first design are gone; the
 * aggregates behind them (`candidates_by_role`, `candidates_added`) are still
 * returned by the API, so restoring them is a render change rather than a rebuild.
 */

/** Windows the pipeline deltas can be measured over. */
const WINDOWS = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

/** Where each tile goes — each to a screen that actually shows what it counts. */
const METRIC_LINKS: Record<string, string> = {
  to_review: "/candidates?tab=unreviewed",
  jobs: "/jobs?status=open",
  interview: "/candidates",
  interviews_week: "/interviews",
};

const METRIC_ICONS: Record<string, (props: { size?: number }) => React.ReactElement> = {
  to_review: IconCandidates,
  jobs: IconJobs,
  interview: IconShortlist,
  interviews_week: IconResumes,
};

/** The call to action for each reason a job needs attention. */
const ATTENTION_ACTIONS: Record<
  AttentionItem["kind"],
  { label: string; primary: boolean; href: (item: AttentionItem) => string }
> = {
  decide: { label: "Review", primary: true, href: (i) => `/jobs/${i.job_id}` },
  review: { label: "Review", primary: true, href: (i) => `/jobs/${i.job_id}` },
  matches: { label: "View matches", primary: false, href: (i) => `/jobs/${i.job_id}` },
  find: { label: "Find candidates", primary: false, href: (i) => `/jobs/${i.job_id}` },
};

function greeting(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function initials(name: string): string {
  return (
    name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?"
  );
}

function MetricCard({ metric }: { metric: Metric }) {
  const Icon = METRIC_ICONS[metric.key] ?? IconCandidates;
  const href = METRIC_LINKS[metric.key];

  const body = (
    <>
      <span className="stat-tile-icon">
        <Icon size={20} />
      </span>
      <div className="stat-tile-body">
        <span className="stat-tile-label" title={metric.label}>
          {metric.label}
        </span>
        <span className="stat-tile-value">{metric.value.toLocaleString()}</span>
        {metric.hint ? <span className="stat-tile-hint">{metric.hint}</span> : null}
      </div>
    </>
  );

  if (!href) return <article className="stat-tile">{body}</article>;

  return (
    <Link href={href} className="stat-tile stat-tile-link">
      {body}
    </Link>
  );
}

function NeedsAttention({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) {
    return (
      <p className="dash-empty">
        Nothing is waiting on you. Every open job has been screened and reviewed.
      </p>
    );
  }
  return (
    <ul className="attention-list">
      {items.map((item) => {
        const action = ATTENTION_ACTIONS[item.kind];
        return (
          <li className="attention-row" key={item.job_id}>
            <span className="job-tile">
              <IconJobs size={17} />
            </span>
            <div className="attention-text">
              <Link href={`/jobs/${item.job_id}`} className="attention-title">
                {item.title}
              </Link>
              <span className="attention-detail">{item.detail}</span>
            </div>
            <Link
              href={action.href(item)}
              className={`btn btn-sm ${action.primary ? "btn-primary" : "btn-outline"}`}
            >
              {action.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function MatchBadge({ score }: { score: number | null }) {
  // Never screened is not zero — a candidate who has never been put in front of a
  // job has no score to report, and "0%" would libel them.
  if (score === null) return <span className="muted">&mdash;</span>;
  const tone = score >= 82 ? "good" : score >= 68 ? "info" : score >= 52 ? "warn" : "mute";
  return <span className={`pill pill-${tone}`}>{Math.round(score)}%</span>;
}

/** Compact list, not a table: it sits in a narrow column beside a chart, and six
 *  columns of table there would either wrap or scroll. */
function TopCandidates({ rows }: { rows: ReviewCandidate[] }) {
  if (rows.length === 0) {
    return <p className="dash-empty">No candidates yet. Upload a resume to begin.</p>;
  }
  return (
    <ul className="top-list">
      {rows.map((row) => (
        <li className="top-row" key={row.candidate_id}>
          <Link href={`/candidates/${row.candidate_id}`} className="who">
            <span className="avatar avatar-soft">{initials(row.name)}</span>
            <span className="who-text">
              <span className="who-name">{displayName(row.name)}</span>
              <span className="who-sub">{row.role ?? "Role not parsed"}</span>
            </span>
          </Link>
          <span className="top-right">
            {/* Pipeline stage first; failing that the screening verdict. "Ready"
                is the resume's ingestion state and says nothing about the person,
                so it is the last resort rather than the default. */}
            {row.recommendation ? (
              <RecommendationPill recommendation={row.recommendation} />
            ) : (
              <StatusPill status={row.status} />
            )}
            <span className="top-score">
              <MatchBadge score={row.match_score} />
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function OpenJobsTable({ rows }: { rows: OpenJobRow[] }) {
  if (rows.length === 0) {
    return <p className="dash-empty">No open jobs. Create one to start screening.</p>;
  }
  return (
    // No table-wrap: this table is sized to fit its card, so it must never be the
    // thing that makes the page scroll sideways.
    <table className="listing">
      <thead>
        <tr>
          <th className="col-clip">Job title</th>
          <th className="col-clip col-location">Location</th>
          <th className="num col-tight">Candidates</th>
          <th className="num col-tight">Strong matches</th>
          <th className="num col-tight">Days open</th>
          <th className="col-tight">Status</th>
          <th className="col-action">Action</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td className="col-clip">
              <Link href={`/jobs/${row.id}`} className="who-name who-link" title={row.title}>
                {row.title}
              </Link>
            </td>
            <td className="col-clip col-location muted">{row.location ?? "—"}</td>
            <td className="num col-tight muted">{row.candidate_count}</td>
            <td className="num col-tight">
              {row.strong_match_count > 0 ? (
                <span className="strong-count">{row.strong_match_count}</span>
              ) : (
                <span className="muted">0</span>
              )}
            </td>
            <td className="num col-tight muted">{row.days_open}</td>
            <td className="col-tight">
              <StatusPill status={row.attention_status} />
            </td>
            <td className="col-action">
              <Link href={`/jobs/${row.id}`} className="btn btn-outline btn-sm">
                View
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PanelHead({
  title,
  count,
  href,
}: {
  title: string;
  count?: number;
  href: string;
}) {
  return (
    <div className="card-head">
      <h2>
        {title}
        {count !== undefined && count > 0 ? <span className="head-count">{count}</span> : null}
      </h2>
      <Link href={href} className="card-link">
        View all <IconChevron size={13} className="chev-right" />
      </Link>
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [firstName, setFirstName] = useState("");
  const [windowDays, setWindowDays] = useState(7);

  // Client only: rendering a locale-dependent greeting during SSR and again after
  // hydration produces a mismatch whenever the two disagree about the clock.
  useEffect(() => {
    setNow(new Date());
    setFirstName((getStoredUser()?.name ?? "").trim().split(/\s+/)[0] ?? "");
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.dashboard(windowDays));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the dashboard.");
    }
  }, [windowDays]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <ErrorBanner message={error} onRetry={load} />;
  if (!data) return <Loading label="Loading the repository…" />;

  return (
    <div className="dash-grid">
      <header className="dash-hello">
        <div>
          <h2>
            {now ? greeting(now) : "Welcome back"}
            {firstName ? `, ${firstName}` : ""}
          </h2>
          <p>Here&rsquo;s what&rsquo;s happening with your recruitment today.</p>
        </div>
        <select
          className="filter-select"
          value={windowDays}
          aria-label="Reporting window"
          onChange={(event) => setWindowDays(Number(event.target.value))}
        >
          {WINDOWS.map((option) => (
            <option key={option.days} value={option.days}>
              {option.label}
            </option>
          ))}
        </select>
      </header>

      <section className="stat-row">
        {data.metrics.map((metric) => (
          <MetricCard key={metric.key} metric={metric} />
        ))}
      </section>

      <section className="dash-split">
        <div className="card">
          <div className="card-head">
            <div>
              <h2>Hiring pipeline</h2>
              <p className="hint">Active candidates per stage</p>
            </div>
            <Link href="/candidates" className="card-link">
              View all <IconChevron size={13} className="chev-right" />
            </Link>
          </div>
          <div className="card-body">
            <PipelineBars stages={data.pipeline} />
            {/* Labels and counts come from the chart's axis and bars above. */}
            <PipelineStrip stages={data.pipeline} deltas={data.pipeline_deltas} compact />
            <p className="pipeline-note">
              Change is entries into each stage over the last {data.window_days} days,
              against the {data.window_days} before. A stage with no prior activity
              shows no figure rather than an invented one.
            </p>
          </div>
        </div>

        <div className="card">
          <PanelHead title="Needs attention" count={data.attention.length} href="/jobs" />
          <div className="card-body">
            <NeedsAttention items={data.attention} />
          </div>
        </div>
      </section>

      <section className="dash-split">
        <div className="card">
          <div className="card-head">
            <div>
              <h2>Weekly activity</h2>
              <p className="hint">Applications received vs hires made</p>
            </div>
            <span className="chart-legend">
              <span className="legend-item">
                <i className="legend-dot" style={{ background: "var(--accent-strong)" }} />
                Applied
              </span>
              <span className="legend-item">
                <i className="legend-dot" style={{ background: "var(--good)" }} />
                Hired
              </span>
            </span>
          </div>
          <div className="card-body">
            <WeeklyActivity points={data.weekly_activity} />
          </div>
        </div>

        <div className="card">
          <PanelHead title="Top candidates" href="/candidates" />
          <div className="card-body card-body-flush">
            <TopCandidates rows={data.review_queue} />
          </div>
        </div>
      </section>

      <div className="card">
        <PanelHead title="Open jobs" href="/jobs" />
        <div className="card-body card-body-flush">
          <OpenJobsTable rows={data.open_jobs} />
        </div>
      </div>

    </div>
  );
}
