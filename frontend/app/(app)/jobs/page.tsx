"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { IconJobs, IconPlus } from "@/components/Icons";
import { ErrorBanner, Loading } from "@/components/Shared";
import {
  DataTable,
  EmptyState,
  PageHead,
  SearchField,
  StatusPill,
  Tabs,
  type Column,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { JobListItem } from "@/lib/types";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const params = useSearchParams();
  // Seeded from the URL so the dashboard tiles can deep-link to a status.
  const [tab, setTab] = useState(() => {
    const wanted = params.get("status");
    return wanted && ["all", "open", "on_hold", "closed"].includes(wanted) ? wanted : "all";
  });
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      setJobs(await api.jobs());
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load jobs.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Counts come from the full list, so a tab shows its size before you open it.
  const tabs = useMemo(() => {
    const all = jobs ?? [];
    const count = (status: string) => all.filter((job) => job.status === status).length;
    return [
      { key: "all", label: "All jobs", count: all.length },
      { key: "open", label: "Open", count: count("open") },
      { key: "on_hold", label: "On hold", count: count("on_hold") },
      { key: "closed", label: "Closed", count: count("closed") },
    ];
  }, [jobs]);

  const visible = useMemo(() => {
    let rows = jobs ?? [];
    if (tab !== "all") rows = rows.filter((job) => job.status === tab);
    const needle = query.trim().toLowerCase();
    if (needle) {
      rows = rows.filter(
        (job) =>
          job.title.toLowerCase().includes(needle) ||
          (job.location ?? "").toLowerCase().includes(needle) ||
          (job.department ?? "").toLowerCase().includes(needle),
      );
    }
    return rows;
  }, [jobs, tab, query]);

  const columns: Column<JobListItem>[] = [
    {
      key: "title",
      header: "Job title",
      render: (row) => (
        <Link href={`/jobs/${row.id}`} className="who">
          <span className="job-tile">
            <IconJobs size={18} />
          </span>
          <span className="who-text">
            <span className="who-name">{row.title}</span>
            <span className="who-sub">{row.department ?? row.location ?? "—"}</span>
          </span>
        </Link>
      ),
    },
    {
      key: "location",
      header: "Location",
      render: (row) => <span className="muted">{row.location ?? "—"}</span>,
    },
    {
      key: "matched",
      header: "Matched",
      numeric: true,
      render: (row) => <span className="muted">{row.candidate_count}</span>,
    },
    {
      key: "strong",
      header: "Strong",
      numeric: true,
      render: (row) =>
        row.strong_match_count > 0 ? (
          <span className="pill pill-good">{row.strong_match_count}</span>
        ) : (
          <span className="muted">0</span>
        ),
    },
    {
      key: "pipeline",
      header: "In pipeline",
      numeric: true,
      render: (row) => <span className="muted">{row.in_pipeline}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (row) => <StatusPill status={row.status} />,
    },
    {
      key: "created",
      header: "Created",
      render: (row) => <span className="muted">{shortDate(row.created_at)}</span>,
    },
  ];

  return (
    <div className="dash-grid">
      <PageHead
        title="Jobs"
        subtitle="Manage your open positions and find the best candidates."
        actions={
          <Link href="/jobs/new" className="btn btn-primary">
            <IconPlus size={16} /> Create job
          </Link>
        }
      />

      <div className="card">
        <div className="card-body">
          <div className="filter-row">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search jobs by title, location or department…"
            />
          </div>
          <Tabs tabs={tabs} active={tab} onChange={setTab} />
        </div>

        <div className="card-body card-body-flush">
          {error ? <ErrorBanner message={error} onRetry={load} /> : null}
          {!error && jobs === null ? <Loading label="Loading jobs…" /> : null}

          {jobs !== null ? (
            <DataTable
              columns={columns}
              rows={visible}
              rowKey={(row) => row.id}
              href={(row) => `/jobs/${row.id}`}
              empty={
                <EmptyState
                  Icon={IconJobs}
                  title={
                    jobs.length === 0 ? "No jobs yet" : "No jobs match these filters"
                  }
                  body={
                    jobs.length === 0
                      ? "Paste a job description and the requirements are extracted for you."
                      : "Try a different status tab, or clear the search."
                  }
                  action={
                    jobs.length === 0 ? (
                      <Link href="/jobs/new" className="btn btn-primary">
                        Create your first job
                      </Link>
                    ) : undefined
                  }
                />
              }
            />
          ) : null}
        </div>

        {jobs !== null && visible.length > 0 ? (
          <div className="card-foot">
            <span className="muted">
              Showing {visible.length} of {jobs.length} job{jobs.length === 1 ? "" : "s"}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
