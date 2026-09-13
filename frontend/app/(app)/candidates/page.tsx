"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  IconCandidates,
  IconResumes,
  IconShortlist,
} from "@/components/Icons";
import { ErrorBanner, Loading } from "@/components/Shared";
import {
  DataTable,
  EmptyState,
  PageHead,
  SearchField,
  StatTiles,
  StatusPill,
  Tabs,
  type Column,
  type Tab,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { displayName } from "@/lib/format";
import type { CandidateListItem, CandidateSummary } from "@/lib/types";

const PAGE_SIZE = 15;

/** Bands rather than a free number: the design shows a dropdown here, and a raw
 *  number input renders the browser's stepper arrows, which look nothing like the
 *  rest of the toolbar. */
const EXPERIENCE_BANDS = [
  { label: "Any experience", value: "" },
  { label: "1+ years", value: "1" },
  { label: "3+ years", value: "3" },
  { label: "5+ years", value: "5" },
  { label: "10+ years", value: "10" },
];

/** Tabs filter on the pipeline stage the candidate has reached. */
const TABS: Tab[] = [
  { key: "all", label: "All" },
  { key: "unreviewed", label: "To review" },
  { key: "shortlisted", label: "Shortlisted" },
  { key: "interview", label: "Interview" },
  { key: "rejected", label: "Rejected" },
];

/** Stages that count as "still moving forward" for the Shortlisted tab. */
const IN_PLAY = ["shortlisted", "interview", "offer", "hired"];

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

function MatchCell({ score }: { score: number | null }) {
  // Null means never screened, which is not the same as scoring zero — showing
  // "0%" here would libel the candidate.
  if (score === null) return <span className="muted">—</span>;
  const tone = score >= 82 ? "good" : score >= 68 ? "info" : score >= 52 ? "warn" : "mute";
  return <span className={`pill pill-${tone}`}>{Math.round(score)}%</span>;
}

export default function CandidatesPage() {
  const params = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [skill, setSkill] = useState("");
  const [minYears, setMinYears] = useState("");
  // Seeded from the URL so the dashboard tiles can deep-link to a filter.
  const [tab, setTab] = useState(
    TABS.some((entry) => entry.key === params.get("tab")) ? params.get("tab")! : "all",
  );
  const [page, setPage] = useState(0);

  const [rows, setRows] = useState<CandidateListItem[] | null>(null);
  const [summary, setSummary] = useState<CandidateSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const result = await api.candidates({
        q: query || undefined,
        skill: skill || undefined,
        min_years: minYears ? Number(minYears) : undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setRows(result.items);
      setSummary(result.summary);
      setTotal(result.total);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load candidates.");
    }
  }, [query, skill, minYears, page]);

  useEffect(() => {
    void load();
  }, [load]);

  // Stage filtering happens client-side: the tabs partition the current page, and
  // the API filters on skills and experience rather than pipeline stage.
  const visible = useMemo(() => {
    if (!rows) return [];
    if (tab === "unreviewed") return rows.filter((row) => row.stage === null);
    if (tab === "shortlisted") {
      return rows.filter((row) => row.stage !== null && IN_PLAY.includes(row.stage));
    }
    // Interview and Rejected are exact stages rather than "this far or beyond":
    // `stage` is the furthest stage the candidate reached on any job, so someone
    // who has moved on to offer is no longer awaiting interview.
    if (tab === "interview") return rows.filter((row) => row.stage === "interview");
    if (tab === "rejected") return rows.filter((row) => row.stage === "rejected");
    return rows;
  }, [rows, tab]);

  const columns: Column<CandidateListItem>[] = [
    {
      key: "name",
      header: "Name",
      render: (row) => (
        <Link href={`/candidates/${row.id}`} className="who">
          <span className="avatar avatar-soft">{initials(row.full_name)}</span>
          <span className="who-text">
            <span className="who-name">{displayName(row.full_name)}</span>
            {row.email ? <span className="who-sub">{row.email}</span> : null}
          </span>
        </Link>
      ),
    },
    {
      key: "role",
      header: "Current role",
      render: (row) => (
        <span className="muted">{row.current_title ?? row.primary_role ?? "—"}</span>
      ),
    },
    {
      key: "years",
      header: "Experience",
      numeric: true,
      render: (row) => <span className="muted">{row.total_years_experience.toFixed(1)} yrs</span>,
    },
    {
      key: "skills",
      header: "Top skills",
      render: (row) => (
        <span className="chips">
          {row.skills.slice(0, 3).map((s) => (
            <span className="chip" key={s}>
              {s}
            </span>
          ))}
          {row.skills.length > 3 ? (
            <span className="chip chip-more">+{row.skills.length - 3}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "match",
      header: "Match",
      numeric: true,
      render: (row) => <MatchCell score={row.best_match_score} />,
    },
    {
      key: "status",
      header: "Status",
      render: (row) =>
        row.stage ? (
          <StatusPill status={row.stage} />
        ) : row.resume_status ? (
          <StatusPill status={row.resume_status} />
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="dash-grid">
      <PageHead
        title="Candidates"
        subtitle="Manage and evaluate your talent pool."
        actions={
          <Link href="/resumes" className="btn btn-primary">
            Upload resumes
          </Link>
        }
      />

      {summary ? (
        <StatTiles
          tiles={[
            {
              key: "total",
              label: "Total candidates",
              value: summary.total,
              Icon: IconCandidates,
            },
            {
              key: "new",
              label: "Added today",
              value: summary.new_today,
              Icon: IconResumes,
            },
            {
              key: "review",
              label: "To review",
              value: summary.to_review,
              hint: "no application opened",
              Icon: IconResumes,
            },
            {
              key: "shortlisted",
              label: "In pipeline",
              value: summary.shortlisted,
              Icon: IconShortlist,
            },
          ]}
        />
      ) : null}

      <div className="card">
        <div className="card-body">
          <div className="filter-row">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search by name, email or title…"
              onSubmit={() => setPage(0)}
            />
            <input
              type="text"
              value={skill}
              placeholder="Skill, e.g. Kubernetes"
              aria-label="Filter by skill"
              onChange={(event) => {
                setSkill(event.target.value);
                setPage(0);
              }}
            />
            <select
              className="filter-select"
              value={minYears}
              aria-label="Minimum years of experience"
              onChange={(event) => {
                setMinYears(event.target.value);
                setPage(0);
              }}
            >
              {EXPERIENCE_BANDS.map((band) => (
                <option key={band.label} value={band.value}>
                  {band.label}
                </option>
              ))}
            </select>
          </div>

          <Tabs tabs={TABS} active={tab} onChange={setTab} />
        </div>

        <div className="card-body card-body-flush">
          {error ? <ErrorBanner message={error} onRetry={load} /> : null}
          {!error && rows === null ? <Loading label="Loading candidates…" /> : null}

          {rows !== null ? (
            <DataTable
              columns={columns}
              rows={visible}
              rowKey={(row) => row.id}
              href={(row) => `/candidates/${row.id}`}
              empty={
                <EmptyState
                  Icon={IconCandidates}
                  title="No candidates found"
                  body={
                    query || skill || minYears
                      ? "Try adjusting your filters, or upload more resumes."
                      : "Upload a resume to start building your talent pool."
                  }
                  suggestions={[
                    "Check for spelling errors in your search",
                    "Try a broader skill or lower the minimum years",
                    "Upload more resumes to expand the pool",
                  ]}
                  action={
                    <Link href="/resumes" className="btn btn-primary">
                      Upload resumes
                    </Link>
                  }
                />
              }
            />
          ) : null}
        </div>

        {rows !== null && total > PAGE_SIZE ? (
          <div className="card-foot">
            <span className="muted">
              Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
            </span>
            <div className="pager">
              <button
                type="button"
                className="btn"
                disabled={page === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                Previous
              </button>
              <span className="muted">
                {page + 1} / {pages}
              </span>
              <button
                type="button"
                className="btn"
                disabled={page + 1 >= pages}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
