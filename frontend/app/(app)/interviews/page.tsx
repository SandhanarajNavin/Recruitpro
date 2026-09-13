"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { IconCandidates, IconResumes } from "@/components/Icons";
import { ErrorBanner, Loading } from "@/components/Shared";
import { EmptyState, PageHead, StatusPill, Tabs, type Tab } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { displayName } from "@/lib/format";
import type {
  ApplicationRow,
  Interview,
  InterviewOutcome,
  InterviewWindow,
} from "@/lib/types";

const WINDOWS: Array<{ key: InterviewWindow; label: string }> = [
  { key: "week", label: "This week" },
  { key: "upcoming", label: "Upcoming" },
  { key: "past", label: "Past" },
  { key: "all", label: "All" },
];

const OUTCOMES: InterviewOutcome[] = ["completed", "no_show", "scheduled"];

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Value for a `datetime-local` input, in the viewer's own timezone. */
function localInputValue(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function ScheduleForm({ onScheduled }: { onScheduled: () => void }) {
  const [open, setOpen] = useState(false);
  const [applications, setApplications] = useState<ApplicationRow[] | null>(null);
  const [applicationId, setApplicationId] = useState("");
  const [at, setAt] = useState(() =>
    localInputValue(new Date(Date.now() + 24 * 60 * 60 * 1000)),
  );
  const [kind, setKind] = useState("");
  const [interviewer, setInterviewer] = useState("");
  const [advance, setAdvance] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || applications !== null) return;
    api.applications().then(setApplications).catch(() => setApplications([]));
  }, [open, applications]);

  // Rejected applications cannot be booked against, so they are not offered.
  const bookable = useMemo(
    () => (applications ?? []).filter((row) => row.stage !== "rejected"),
    [applications],
  );

  async function submit() {
    if (!applicationId) {
      setError("Pick who the interview is with.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.scheduleInterview({
        application_id: applicationId,
        // The input is local time; the API takes an instant.
        scheduled_at: new Date(at).toISOString(),
        kind: kind || undefined,
        interviewer: interviewer || undefined,
        advance_stage: advance,
      });
      setOpen(false);
      setApplicationId("");
      setKind("");
      setInterviewer("");
      onScheduled();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not schedule.");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
        Schedule interview
      </button>
    );
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Schedule an interview</h2>
      </div>
      <div className="card-body">
        {error ? <ErrorBanner message={error} /> : null}
        {applications === null ? (
          <Loading label="Loading applications…" />
        ) : bookable.length === 0 ? (
          <p className="note-body">
            Nobody is in a pipeline yet. Shortlist a candidate from a screening first
            — an interview is booked against an application, not against a person, so
            the notes and outcome belong to a specific role.
          </p>
        ) : (
          <>
            <div className="form-grid">
              <label>
                <span className="field-label">Candidate and role</span>
                <select
                  value={applicationId}
                  onChange={(event) => setApplicationId(event.target.value)}
                >
                  <option value="">Choose…</option>
                  {bookable.map((row) => (
                    <option key={row.id} value={row.id}>
                      {displayName(row.candidate_name)} — {row.job_title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="field-label">When</span>
                <input
                  type="datetime-local"
                  value={at}
                  onChange={(event) => setAt(event.target.value)}
                />
              </label>
              <label>
                <span className="field-label">Kind</span>
                <input
                  value={kind}
                  placeholder="Technical screen"
                  onChange={(event) => setKind(event.target.value)}
                />
              </label>
              <label>
                <span className="field-label">Interviewer</span>
                <input
                  value={interviewer}
                  onChange={(event) => setInterviewer(event.target.value)}
                />
              </label>
            </div>
            <label className="check-row">
              <input
                type="checkbox"
                checked={advance}
                onChange={(event) => setAdvance(event.target.checked)}
              />
              <span>
                Move them to the interview stage
                <small>
                  So the booking shows up in the hiring pipeline. Uncheck to hold a
                  slot before committing. Any stages in between are recorded too —
                  nobody reaches an interview without having been shortlisted.
                </small>
              </span>
            </label>
          </>
        )}
        <div className="btn-row">
          {bookable.length ? (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={saving}
              onClick={submit}
            >
              {saving ? "Booking…" : "Book it"}
            </button>
          ) : null}
          <button type="button" className="btn btn-sm" onClick={() => setOpen(false)}>
            Cancel
          </button>
        </div>
      </div>
    </section>
  );
}

function Row({ row, onChanged }: { row: Interview; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  async function set(outcome: InterviewOutcome) {
    setBusy(true);
    try {
      await api.updateInterview(row.id, { outcome });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td className="col-tight muted">{when(row.scheduled_at)}</td>
      <td className="col-clip">
        <Link href={`/candidates/${row.candidate_id}`} className="who-name who-link">
          {displayName(row.candidate_name)}
        </Link>
      </td>
      <td className="col-clip">
        <Link href={`/jobs/${row.job_id}`} className="muted">
          {row.job_title}
        </Link>
      </td>
      <td className="muted col-tight">{row.kind ?? "—"}</td>
      <td className="muted col-tight">{row.interviewer ?? "—"}</td>
      <td className="col-tight">
        <StatusPill status={row.outcome} />
      </td>
      <td className="col-action">
        <select
          className="filter-select"
          value=""
          disabled={busy}
          aria-label={`Set outcome for ${row.candidate_name}`}
          onChange={(event) => {
            const next = event.target.value as InterviewOutcome | "cancel";
            if (!next) return;
            if (next === "cancel") {
              setBusy(true);
              api.cancelInterview(row.id).then(onChanged).finally(() => setBusy(false));
            } else {
              void set(next);
            }
          }}
        >
          <option value="">Record…</option>
          {OUTCOMES.map((outcome) => (
            <option key={outcome} value={outcome}>
              {outcome.replace(/_/g, " ")}
            </option>
          ))}
          <option value="cancel">cancel</option>
        </select>
      </td>
    </tr>
  );
}

export default function InterviewsPage() {
  const [window_, setWindow] = useState<InterviewWindow>("week");
  const [rows, setRows] = useState<Interview[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await api.interviews(window_);
      setRows(page.items);
      setCounts(page.counts);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load interviews.");
    }
  }, [window_]);

  useEffect(() => {
    void load();
  }, [load]);

  const tabs: Tab[] = WINDOWS.map((entry) => ({
    key: entry.key,
    label: entry.label,
    count: entry.key === "all" ? undefined : counts[entry.key],
  }));

  return (
    <div className="dash-grid">
      <PageHead
        title="Interviews"
        subtitle="Everything booked against a candidate's application."
        actions={<ScheduleForm onScheduled={load} />}
      />

      <div className="card">
        <div className="card-body">
          <Tabs
            tabs={tabs}
            active={window_}
            onChange={(key) => setWindow(key as InterviewWindow)}
          />
        </div>

        <div className="card-body card-body-flush">
          {error ? <ErrorBanner message={error} onRetry={load} /> : null}
          {!error && rows === null ? <Loading label="Loading interviews…" /> : null}

          {rows !== null && rows.length === 0 ? (
            <EmptyState
              Icon={IconResumes}
              title={
                window_ === "past" ? "No interviews yet" : "Nothing scheduled"
              }
              body={
                window_ === "past"
                  ? "Completed interviews will appear here."
                  : "Book one against an application to see it here."
              }
            />
          ) : null}

          {rows !== null && rows.length > 0 ? (
            <table className="listing">
              <thead>
                <tr>
                  <th className="col-tight">When</th>
                  <th className="col-clip">Candidate</th>
                  <th className="col-clip">Role</th>
                  <th className="col-tight">Kind</th>
                  <th className="col-tight">Interviewer</th>
                  <th className="col-tight">Outcome</th>
                  <th className="col-action">Record</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row key={row.id} row={row} onChanged={load} />
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      </div>

      <p className="footnote">
        <IconCandidates size={14} /> An interview belongs to an application, not to a
        person — the same candidate can be interviewed for two roles, and the notes and
        outcome belong to one of them. Cancelling keeps the record rather than deleting
        it, so the history stays honest.
      </p>
    </div>
  );
}
