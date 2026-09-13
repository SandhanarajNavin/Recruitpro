"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { IconResumes } from "@/components/Icons";
import { ResumeUploader } from "@/components/ResumeUploader";
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
import { displayName } from "@/lib/format";
import type { CandidateListItem } from "@/lib/types";

/** Ingestion states that mean "still working". */
const IN_FLIGHT = ["queued", "extracting", "parsing", "embedding"];

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function ResumesPage() {
  const [rows, setRows] = useState<CandidateListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("all");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await api.candidates({ limit: 100 });
      setRows(page.items);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load resumes.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Anything mid-ingestion means the worker is still going, so poll until the
  // queue drains rather than leaving the recruiter to refresh.
  const processing = useMemo(
    () => (rows ?? []).filter((row) => IN_FLIGHT.includes(row.resume_status ?? "")).length,
    [rows],
  );

  useEffect(() => {
    if (processing === 0) return;
    const timer = setTimeout(() => void load(), 2500);
    return () => clearTimeout(timer);
  }, [processing, load, rows]);

  const tabs = useMemo(() => {
    const all = rows ?? [];
    return [
      { key: "all", label: "All resumes", count: all.length },
      { key: "processing", label: "Processing", count: processing },
      {
        key: "failed",
        label: "Failed",
        count: all.filter((row) => row.resume_status === "failed").length,
      },
    ];
  }, [rows, processing]);

  const visible = useMemo(() => {
    let list = rows ?? [];
    if (tab === "processing") {
      list = list.filter((row) => IN_FLIGHT.includes(row.resume_status ?? ""));
    } else if (tab === "failed") {
      list = list.filter((row) => row.resume_status === "failed");
    }
    const needle = query.trim().toLowerCase();
    if (needle) {
      list = list.filter(
        (row) =>
          row.full_name.toLowerCase().includes(needle) ||
          (row.current_title ?? "").toLowerCase().includes(needle),
      );
    }
    return list;
  }, [rows, tab, query]);

  const columns: Column<CandidateListItem>[] = [
    {
      key: "candidate",
      header: "Candidate",
      render: (row) => (
        <Link href={`/candidates/${row.id}`} className="who">
          <span className="job-tile">
            <IconResumes size={17} />
          </span>
          <span className="who-text">
            <span className="who-name">{displayName(row.full_name)}</span>
            {row.email ? <span className="who-sub">{row.email}</span> : null}
          </span>
        </Link>
      ),
    },
    {
      key: "role",
      header: "Parsed role",
      render: (row) => (
        <span className="muted">{row.primary_role ?? row.current_title ?? "—"}</span>
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
      header: "Skills",
      numeric: true,
      render: (row) => <span className="muted">{row.skills.length}</span>,
    },
    {
      key: "added",
      header: "Added",
      render: (row) => <span className="muted">{shortDate(row.created_at)}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (row) =>
        row.resume_status ? (
          <StatusPill status={row.resume_status} />
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];

  return (
    <div className="dash-grid">
      <PageHead title="Resumes" subtitle="Upload and manage candidate resumes." />

      <div className="card">
        <div className="card-body">
          <ResumeUploader onIngested={load} />
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <div className="filter-row">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search resumes by name or title…"
            />
          </div>
          <Tabs tabs={tabs} active={tab} onChange={setTab} />
        </div>

        <div className="card-body card-body-flush">
          {error ? <ErrorBanner message={error} onRetry={load} /> : null}
          {!error && rows === null ? <Loading label="Loading resumes…" /> : null}

          {rows !== null ? (
            <DataTable
              columns={columns}
              rows={visible}
              rowKey={(row) => row.id}
              href={(row) => `/candidates/${row.id}`}
              empty={
                <EmptyState
                  Icon={IconResumes}
                  title={
                    tab === "failed"
                      ? "No failed resumes"
                      : tab === "processing"
                        ? "Nothing processing"
                        : "No resumes yet"
                  }
                  body={
                    tab === "all"
                      ? "Drop a PDF, DOCX or TXT above to build your talent pool."
                      : "Nothing in this state right now."
                  }
                  suggestions={
                    tab === "failed"
                      ? [
                          "A scanned image PDF extracts almost no text and needs OCR",
                          "Check the file is not password protected",
                          "Re-upload, or open the candidate to reprocess",
                        ]
                      : undefined
                  }
                />
              }
            />
          ) : null}
        </div>

        {processing > 0 ? (
          <div className="card-foot">
            <span className="muted">
              <span className="spinner" aria-hidden="true" /> {processing} still
              processing — this list refreshes itself.
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
