"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  IconCandidates,
  IconChevron,
  IconJobs,
  IconPlus,
  IconResumes,
  IconShortlist,
} from "@/components/Icons";
import { ErrorBanner, Loading, RecommendationPill } from "@/components/Shared";
import { StatusPill } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { displayName } from "@/lib/format";
import type {
  ApplicationRow,
  JobDetail,
  JobRequirement,
  RankedCandidate,
} from "@/lib/types";

type TabKey = "overview" | "matches" | "applicants" | "activity";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** Requirement bullets assembled from what the parser actually extracted.
 *
 *  The design shows a prose "Requirements" list. There is no such field — the
 *  parser produces structured facts — so these are composed from those facts
 *  rather than invented, and the list is omitted entirely when nothing was
 *  extracted. */
function requirementBullets(requirement: JobRequirement | null): string[] {
  if (!requirement) return [];
  const bullets: string[] = [];

  if (requirement.min_years_experience > 0) {
    bullets.push(
      `${requirement.min_years_experience}+ years of professional experience.`,
    );
  }
  const mustHave = requirement.hard_filters?.must_have_skills ?? [];
  if (mustHave.length) {
    bullets.push(`Must have: ${mustHave.join(", ")}.`);
  }
  for (const line of requirement.education) bullets.push(line);
  if (requirement.domains.length) {
    bullets.push(`Domain background: ${requirement.domains.join(", ")}.`);
  }
  for (const flag of requirement.red_flags) bullets.push(`Watch for: ${flag}`);
  return bullets;
}

function StatCard({
  value,
  label,
  hint,
  Icon,
}: {
  value: string | number;
  label: string;
  hint?: string | null;
  Icon: (props: { size?: number }) => React.ReactElement;
}) {
  return (
    <article className="stat-tile">
      <span className="stat-tile-icon">
        <Icon size={20} />
      </span>
      <div className="stat-tile-body">
        <span className="stat-tile-value">{value}</span>
        <span className="stat-tile-label">{label}</span>
        {hint ? (
          <span className="stat-tile-hint">{hint}</span>
        ) : null}
      </div>
    </article>
  );
}

function CoverageBars({ rows }: { rows: JobDetail["skill_coverage"] }) {
  if (rows.length === 0) {
    return (
      <p className="note-body">
        No completed screening yet — run one to see how much of the pool has each
        required skill.
      </p>
    );
  }
  return (
    <ul className="coverage">
      {rows.map((row) => (
        <li className="coverage-row" key={row.skill}>
          <span className="coverage-skill" title={row.skill}>
            {row.skill}
          </span>
          <span className="coverage-track">
            <span
              className="coverage-fill"
              style={{ width: `${row.of > 0 ? (row.matched / row.of) * 100 : 0}%` }}
            />
          </span>
          <span className="coverage-count">
            {row.matched}/{row.of}
          </span>
        </li>
      ))}
    </ul>
  );
}

function EditJobForm({
  detail,
  onSaved,
  onCancel,
}: {
  detail: JobDetail;
  onSaved: (next: JobDetail) => void;
  onCancel: () => void;
}) {
  const { job } = detail;
  const [title, setTitle] = useState(job.title);
  const [location, setLocation] = useState(job.location ?? "");
  const [department, setDepartment] = useState(job.department ?? "");
  const [employmentType, setEmploymentType] = useState(job.employment_type ?? "");
  const [hiringManager, setHiringManager] = useState(job.hiring_manager ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(
        await api.updateJob(job.id, {
          title,
          location,
          department,
          employment_type: employmentType,
          hiring_manager: hiringManager,
        }),
      );
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not save the job.");
      setSaving(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Edit job</h2>
      </div>
      <div className="card-body">
        {error ? <ErrorBanner message={error} /> : null}
        <div className="form-grid">
          <label>
            <span className="field-label">Title</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label>
            <span className="field-label">Location</span>
            <input value={location} onChange={(event) => setLocation(event.target.value)} />
          </label>
          <label>
            <span className="field-label">Department</span>
            <input
              value={department}
              onChange={(event) => setDepartment(event.target.value)}
            />
          </label>
          <label>
            <span className="field-label">Employment type</span>
            <input
              value={employmentType}
              placeholder="Full Time"
              onChange={(event) => setEmploymentType(event.target.value)}
            />
          </label>
          <label>
            <span className="field-label">Hiring manager</span>
            <input
              value={hiringManager}
              onChange={(event) => setHiringManager(event.target.value)}
            />
          </label>
        </div>
        <p className="note-body">
          The job description is not editable — it is what the requirements were parsed
          from, so changing it would leave those requirements describing a different job.
        </p>
        <div className="btn-row">
          <button type="button" className="btn btn-primary btn-sm" disabled={saving} onClick={save}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          <button type="button" className="btn btn-sm" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </section>
  );
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;

  const [data, setData] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [tab, setTab] = useState<TabKey>("overview");
  const [editing, setEditing] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [applicants, setApplicants] = useState<ApplicationRow[] | null>(null);
  const [matches, setMatches] = useState<RankedCandidate[] | null>(null);
  const [copied, setCopied] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.job(id));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the job.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Applicants back the tab's count, so they load with the page rather than on
  // first click — a tab labelled "Applicants" with no number reads as broken.
  useEffect(() => {
    api.applications({ job_id: id }).then(setApplicants).catch(() => setApplicants([]));
  }, [id]);

  const latest = data?.stats.latest_screening_id ?? null;

  useEffect(() => {
    if (tab !== "matches" || !latest || matches !== null) return;
    api
      .screeningResults(latest)
      .then((results) => setMatches([...results.shortlist, ...results.also_considered]))
      .catch(() => setMatches([]));
  }, [tab, latest, matches]);

  useEffect(() => {
    if (!moreOpen) return;
    function onDown(event: MouseEvent) {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [moreOpen]);

  const bullets = useMemo(() => requirementBullets(data?.requirement ?? null), [data]);

  if (error && !data) return <ErrorBanner message={error} onRetry={load} />;
  if (!data) return <Loading label="Loading job…" />;

  const { job, requirement, stats } = data;

  async function startScreening() {
    setStarting(true);
    setError(null);
    try {
      const started = await api.startScreening(job.id);
      router.push(`/screenings/${started.screening.id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not start the screening.");
      setStarting(false);
    }
  }

  async function setStatus(next: "open" | "on_hold" | "closed") {
    setMoreOpen(false);
    try {
      setData(await api.updateJob(job.id, { status: next }));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not update the job.");
    }
  }

  async function shareJob() {
    setMoreOpen(false);
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access is refused in some contexts; the URL is in the address bar.
    }
  }

  async function duplicate() {
    setMoreOpen(false);
    try {
      // Same description, so the requirements are parsed afresh rather than copied
      // — a stale copy would describe the old job.
      const created = await api.createJob({
        title: `${job.title} (copy)`,
        description: job.description,
        location: job.location ?? undefined,
      });
      router.push(`/jobs/${created.job.id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not duplicate the job.");
    }
  }

  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: "overview", label: "Overview" },
    { key: "matches", label: "Candidate matches" },
    {
      key: "applicants",
      label: applicants ? `Applicants (${applicants.length})` : "Applicants",
    },
    { key: "activity", label: "Activity" },
  ];

  return (
    <div className="job-page">
      <Link href="/jobs" className="back-link">
        <IconChevron size={15} className="chev-left" /> Back to Jobs
      </Link>

      <div className="masthead">
        <div>
          <h1 className="masthead-title">
            {job.title}
            <StatusPill status={job.status} />
          </h1>
          <p className="job-meta">
            {[
              job.location,
              job.department,
              requirement && requirement.min_years_experience > 0
                ? `${requirement.min_years_experience}+ years`
                : null,
              job.employment_type,
              `Posted on ${shortDate(job.created_at)}`,
            ]
              .filter(Boolean)
              .join("  ·  ")}
          </p>
        </div>
        <div className="btn-row">
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setEditing((value) => !value)}
          >
            {editing ? "Cancel edit" : "Edit job"}
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={startScreening}
            disabled={starting}
          >
            <IconCandidates size={15} /> {starting ? "Starting…" : "Find candidates"}
          </button>
          <div className="more-wrap" ref={moreRef}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setMoreOpen((value) => !value)}
              aria-expanded={moreOpen}
              aria-haspopup="menu"
            >
              More
            </button>
            {moreOpen ? (
              <div className="more-menu" role="menu">
                <button type="button" role="menuitem" onClick={shareJob}>
                  {copied ? "Link copied" : "Share job"}
                </button>
                <button type="button" role="menuitem" onClick={duplicate}>
                  Duplicate
                </button>
                {job.status !== "on_hold" ? (
                  <button type="button" role="menuitem" onClick={() => setStatus("on_hold")}>
                    Put on hold
                  </button>
                ) : null}
                {job.status !== "open" ? (
                  <button type="button" role="menuitem" onClick={() => setStatus("open")}>
                    Reopen
                  </button>
                ) : null}
                {job.status !== "closed" ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-danger"
                    onClick={() => setStatus("closed")}
                  >
                    Close job
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {error ? <ErrorBanner message={error} /> : null}

      <nav className="tabs" aria-label="Job sections">
        {tabs.map((entry) => (
          <button
            key={entry.key}
            type="button"
            className={tab === entry.key ? "tab tab-on" : "tab"}
            onClick={() => setTab(entry.key)}
            aria-current={tab === entry.key ? "page" : undefined}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {editing ? (
        <EditJobForm
          detail={data}
          onSaved={(next) => {
            setData(next);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      ) : null}

      {tab === "overview" ? (
        <>
          <section className="stat-row">
            <StatCard
              value={stats.candidate_count}
              label="Total candidates"
              hint={stats.new_this_week ? `${stats.new_this_week} new this week` : null}
              Icon={IconCandidates}
            />
            <StatCard
              value={stats.strong_match_count}
              label="Strong matches"
              hint="Based on latest screening"
              Icon={IconShortlist}
            />
            <StatCard
              value={`${stats.days_open} days`}
              label="Open"
              hint={`Posted on ${shortDate(job.created_at)}`}
              Icon={IconResumes}
            />
            <StatCard
              value={stats.in_pipeline}
              label="In pipeline"
              hint="Applications not rejected"
              Icon={IconJobs}
            />
          </section>

          <div className="job-split">
            <div className="job-col">
              <section className="card">
                <div className="card-head">
                  <h2>Key skills</h2>
                </div>
                <div className="card-body">
                  {requirement ? (
                    <>
                      <span className="field-label">
                        Required <span className="hint">importance 1&ndash;5</span>
                      </span>
                      <div className="tag-row">
                        {requirement.required_skills.map((entry) => (
                          <span
                            className={entry.importance >= 5 ? "chip chip-critical" : "chip"}
                            key={entry.skill}
                            title={
                              entry.importance >= 5
                                ? "Importance 5 — applied as a hard filter before retrieval"
                                : `Importance ${entry.importance}/5`
                            }
                          >
                            {entry.skill}
                          </span>
                        ))}
                      </div>
                      {requirement.preferred_skills.length ? (
                        <>
                          <span className="field-label">Preferred</span>
                          <div className="tag-row">
                            {requirement.preferred_skills.map((skill) => (
                              <span className="chip" key={skill}>
                                {skill}
                              </span>
                            ))}
                          </div>
                        </>
                      ) : null}
                    </>
                  ) : (
                    <p className="note-body">No requirements were parsed for this job.</p>
                  )}
                </div>
              </section>

              {requirement?.responsibilities.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Responsibilities</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {requirement.responsibilities.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}

              {bullets.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Requirements</h2>
                    <p className="hint">Composed from the parsed facts, not free text.</p>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {bullets.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}
            </div>

            <div className="job-col">
              <section className="card">
                <div className="card-head">
                  <h2>AI-parsed requirements</h2>
                </div>
                <div className="card-body">
                  <dl className="detail-list">
                    <div className="detail-pair">
                      <dt>Total skills extracted</dt>
                      <dd>
                        {(requirement?.required_skills.length ?? 0) +
                          (requirement?.preferred_skills.length ?? 0)}
                      </dd>
                    </div>
                    <div className="detail-pair">
                      <dt>Required skills</dt>
                      <dd>{requirement?.required_skills.length ?? 0}</dd>
                    </div>
                    <div className="detail-pair">
                      <dt>Preferred skills</dt>
                      <dd>{requirement?.preferred_skills.length ?? 0}</dd>
                    </div>
                    <div className="detail-pair">
                      <dt>Experience level</dt>
                      <dd>
                        {requirement && requirement.min_years_experience > 0
                          ? `${requirement.min_years_experience}+ years`
                          : (requirement?.seniority ?? "—")}
                      </dd>
                    </div>
                    <div className="detail-pair">
                      <dt>Employment type</dt>
                      <dd>{job.employment_type ?? "—"}</dd>
                    </div>
                    {requirement ? (
                      <div className="detail-pair">
                        <dt>Parsed by</dt>
                        <dd>
                          <code>{requirement.parser_model}</code>
                        </dd>
                      </div>
                    ) : null}
                  </dl>
                </div>
              </section>

              <section className="card">
                <div className="card-head">
                  <h2>Top matching skills</h2>
                  {latest ? (
                    <Link href={`/screenings/${latest}`} className="card-link">
                      View run
                    </Link>
                  ) : null}
                </div>
                <div className="card-body">
                  <CoverageBars rows={data.skill_coverage} />
                </div>
              </section>

              <section className="card">
                <div className="card-head">
                  <h2>Job description</h2>
                </div>
                <div className="card-body">
                  <pre className="jd-text">{job.description}</pre>
                </div>
              </section>

              <section className="card">
                <div className="card-head">
                  <h2>Quick actions</h2>
                </div>
                <div className="card-body side-actions">
                  <button type="button" className="btn btn-outline btn-sm" onClick={shareJob}>
                    {copied ? "Link copied" : "Share job"}
                  </button>
                  <button type="button" className="btn btn-outline btn-sm" onClick={duplicate}>
                    <IconPlus size={15} /> Duplicate
                  </button>
                  {job.status !== "closed" ? (
                    <button
                      type="button"
                      className="btn btn-outline btn-sm btn-danger"
                      onClick={() => setStatus("closed")}
                    >
                      Close job
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => setStatus("open")}
                    >
                      Reopen job
                    </button>
                  )}
                </div>
              </section>
            </div>
          </div>
        </>
      ) : null}

      {tab === "matches" ? (
        <section className="card">
          <div className="card-head">
            <h2>Candidate matches</h2>
            {latest ? (
              <Link href={`/screenings/${latest}`} className="card-link">
                Open full run
              </Link>
            ) : null}
          </div>
          <div className="card-body card-body-flush">
            {!latest ? (
              <div className="empty-state">
                No screening has completed for this job yet. Use Find candidates to run one.
              </div>
            ) : matches === null ? (
              <Loading label="Loading matches…" />
            ) : matches.length === 0 ? (
              <div className="empty-state">That run produced no scored candidates.</div>
            ) : (
              <div className="table-wrap">
                <table className="listing">
                  <thead>
                    <tr>
                      <th className="num">Rank</th>
                      <th>Candidate</th>
                      <th>Role</th>
                      <th className="num">Score</th>
                      <th>Recommendation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matches.map((entry) => (
                      <tr key={entry.candidate_id}>
                        <td className="num">{entry.rank}</td>
                        <td>
                          <Link
                            href={`/candidates/${entry.candidate_id}`}
                            className="who-name who-link"
                          >
                            {displayName(entry.name)}
                          </Link>
                        </td>
                        <td className="muted">{entry.role ?? "—"}</td>
                        <td className="num">{entry.composite_score.toFixed(1)}</td>
                        <td>
                          <RecommendationPill recommendation={entry.effective_recommendation} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "applicants" ? (
        <section className="card">
          <div className="card-head">
            <h2>Applicants</h2>
          </div>
          <div className="card-body card-body-flush">
            {applicants === null ? (
              <Loading label="Loading applicants…" />
            ) : applicants.length === 0 ? (
              <div className="empty-state">
                Nobody has been moved into the pipeline for this job yet.
              </div>
            ) : (
              <div className="table-wrap">
                <table className="listing">
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      <th>Stage</th>
                      <th>Opened</th>
                    </tr>
                  </thead>
                  <tbody>
                    {applicants.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <Link
                            href={`/candidates/${row.candidate_id}`}
                            className="who-name who-link"
                          >
                            {displayName(row.candidate_name)}
                          </Link>
                        </td>
                        <td>
                          <StatusPill status={row.stage} />
                        </td>
                        <td className="muted">{shortDate(row.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "activity" ? (
        <section className="card">
          <div className="card-head">
            <h2>Activity</h2>
          </div>
          <div className="card-body">
            <p className="note-body">
              A per-job event log is not built yet. What exists today is the pipeline
              history on each application and the screening runs themselves — open a
              candidate for their stage history, or the run for its funnel and evidence.
            </p>
            {latest ? (
              <Link href={`/screenings/${latest}`} className="btn btn-outline btn-sm">
                Open the latest run
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
