"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  IconCandidates,
  IconChevron,
  IconClose,
  IconEdit,
  IconJobs,
  IconPlus,
  IconResumes,
  IconSearch,
  IconShortlist,
} from "@/components/Icons";
import { JobMatchRow } from "@/components/JobMatchRow";
import { ErrorBanner, Loading, RecommendationPill } from "@/components/Shared";
import { ScoreRing } from "@/components/ScoreRing";
import { StatusPill } from "@/components/ui";
import { API_BASE, ApiError, api, getToken } from "@/lib/api";
import { displayName } from "@/lib/format";
import {
  STAGE_LABELS,
  STAGE_MOVES,
  type CandidateDetail,
  type Resume,
  type Stage,
} from "@/lib/types";

type TabKey = "overview" | "resume" | "skills" | "matches" | "activity";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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

function kb(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** The score card under the header: a real score, or an honest absence.
 *
 *  There is still no "screen this candidate" action, because screening runs against
 *  a job's requirements — a candidate alone has nothing to be scored against. But
 *  once any screening has scored them, this page already holds the ranking, so the
 *  action is to open it rather than to leave for the jobs list. With nothing scored
 *  yet there is no ranking to open and picking a job really is the next step. */
function MatchPanel({
  detail,
  onShowMatches,
}: {
  detail: CandidateDetail;
  onShowMatches: () => void;
}) {
  const match = detail.best_match;
  const matchCount = detail.job_matches.length;

  return (
    <section className="card match-card">
      <div className="card-body match-body">
        {match ? (
          <ScoreRing score={match.score} size={78} />
        ) : (
          <span className="ring-empty" aria-hidden="true">
            Not
            <br />
            screened
          </span>
        )}

        <div className="match-text">
          <span className="field-label">Match score</span>
          {match ? (
            <>
              <h2>
                {Math.round(match.score)}% &middot;{" "}
                <RecommendationPill recommendation={match.recommendation} />
              </h2>
              <p>
                Best score across every screening, measured against{" "}
                <Link href={`/jobs/${match.job_id}`}>{match.job_title}</Link>.{" "}
                <Link href={`/screenings/${match.screening_id}`}>See the evidence</Link>.
              </p>
            </>
          ) : (
            <>
              <h2>Not screened</h2>
              <p>
                Run a screening against a job to see a match score and recommendation.
                A score only means something relative to a specific role.
              </p>
            </>
          )}
        </div>

        <div className="match-actions">
          {matchCount ? (
            <button type="button" className="btn btn-primary btn-sm" onClick={onShowMatches}>
              <IconSearch size={15} /> View job matches ({matchCount})
            </button>
          ) : (
            // Nothing scored yet, so there is no ranking to show — the honest next
            // step is to pick a job and run a screening.
            <Link href="/jobs" className="btn btn-primary btn-sm">
              <IconSearch size={15} /> Screen against a job
            </Link>
          )}
          {detail.applications.length === 0 ? null : (
            <span className="hint">
              In {detail.applications.length} pipeline
              {detail.applications.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      </div>
    </section>
  );
}

function ResumeCard({
  resume,
  onReprocessed,
}: {
  resume: Resume;
  onReprocessed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reprocess() {
    setBusy(true);
    setError(null);
    try {
      await api.reprocessResume(resume.id);
      onReprocessed();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not reprocess.");
    } finally {
      setBusy(false);
    }
  }

  // The download endpoint needs the bearer token, so it is fetched and handed to
  // the browser as a blob rather than linked directly.
  async function download() {
    setError(null);
    try {
      const response = await fetch(api.resumeDownloadUrl(resume.id), {
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      });
      if (!response.ok) throw new Error(String(response.status));
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = resume.original_filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setError(`Could not download. The API is at ${API_BASE} — is it running?`);
    }
  }

  return (
    <li className="resume-item">
      <div className="resume-head">
        <span className="job-tile">
          <IconResumes size={16} />
        </span>
        <div className="resume-meta">
          <span className="resume-name" title={resume.original_filename}>
            v{resume.version} {resume.original_filename}
          </span>
          <span className="who-sub">
            {kb(resume.size_bytes)} &middot; {shortDate(resume.created_at)}
          </span>
        </div>
        <StatusPill status={resume.status} />
      </div>
      {resume.error ? <p className="hint hint-bad">{resume.error}</p> : null}
      {error ? <p className="hint hint-bad">{error}</p> : null}
      <div className="btn-row">
        <button type="button" className="btn btn-outline btn-sm" onClick={download}>
          Download
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={reprocess}
          disabled={busy}
        >
          {busy ? "Queued…" : "Reprocess"}
        </button>
      </div>
    </li>
  );
}

/** Moves one application along the pipeline.
 *
 *  Only the legal next stages are offered — the server enforces a stage machine, and
 *  a picker listing every stage would mostly produce 409s. A terminal stage renders
 *  as a plain pill: there is nowhere left to go.
 */
function StageControl({
  applicationId,
  stage,
  jobTitle,
  editing,
  onMoved,
}: {
  applicationId: string;
  stage: Stage;
  jobTitle?: string;
  editing: boolean;
  onMoved: () => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const moves = STAGE_MOVES[stage] ?? [];

  // A pill by default, a picker only while editing. Nothing to pick from a terminal
  // stage either way.
  if (!editing || !moves.length) return <StatusPill status={stage} />;

  async function move(to: Stage) {
    setSaving(true);
    setError(null);
    try {
      await api.moveApplication(applicationId, to);
      await onMoved();
    } catch (cause) {
      // The server owns the stage machine; show what it said rather than guessing.
      setError(cause instanceof ApiError ? cause.message : "Could not change the stage.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="stage-control">
      <select
        className="filter-select"
        value={stage}
        disabled={saving}
        aria-label={jobTitle ? `Stage for ${jobTitle}` : "Pipeline stage"}
        onChange={(event) => void move(event.target.value as Stage)}
      >
        {/* The current stage is the selected option and not a move. */}
        <option value={stage}>{STAGE_LABELS[stage]}</option>
        {moves.map((next) => (
          <option key={next} value={next}>
            Move to {STAGE_LABELS[next]}
          </option>
        ))}
      </select>
      {error ? <span className="field-error">{error}</span> : null}
    </span>
  );
}

/** Active or archived. Archiving takes the candidate out of every future ranking. */
function RecordStatusControl({
  detail,
  editing,
  onSaved,
}: {
  detail: CandidateDetail;
  editing: boolean;
  onSaved: (next: CandidateDetail) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!editing) return <StatusPill status={detail.candidate.status} />;

  async function set(status: "active" | "archived") {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updateCandidate(detail.candidate.id, { status }));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not change the status.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="stage-control">
      <select
        className="filter-select"
        value={detail.candidate.status}
        disabled={saving}
        aria-label="Record status"
        onChange={(event) => void set(event.target.value as "active" | "archived")}
      >
        <option value="active">Active</option>
        <option value="archived">Archived</option>
      </select>
      {error ? <span className="field-error">{error}</span> : null}
    </span>
  );
}

function NotesBox({
  detail,
  onSaved,
}: {
  detail: CandidateDetail;
  onSaved: (next: CandidateDetail) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(detail.candidate.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updateCandidate(detail.candidate.id, { notes: text }));
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not save notes.");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className="detail-pair">
        <dt>Notes</dt>
        <dd>
          {detail.candidate.notes ? (
            <button type="button" className="link-btn" onClick={() => setEditing(true)}>
              {detail.candidate.notes}
            </button>
          ) : (
            <button type="button" className="link-btn" onClick={() => setEditing(true)}>
              + Add notes
            </button>
          )}
        </dd>
      </div>
    );
  }

  return (
    <div className="notes-edit">
      <span className="field-label">Notes</span>
      {error ? <p className="hint hint-bad">{error}</p> : null}
      <textarea
        rows={4}
        value={text}
        placeholder="What you want to remember about this candidate…"
        onChange={(event) => setText(event.target.value)}
      />
      <div className="btn-row">
        <button type="button" className="btn btn-primary btn-sm" disabled={saving} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => {
            setText(detail.candidate.notes ?? "");
            setEditing(false);
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function CandidateDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [data, setData] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("overview");
  const [moreOpen, setMoreOpen] = useState(false);
  const [editingStatus, setEditingStatus] = useState(false);
  const [editingStages, setEditingStages] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.candidate(id));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the candidate.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!moreOpen) return;
    function onDown(event: MouseEvent) {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [moreOpen]);

  if (error && !data) return <ErrorBanner message={error} onRetry={load} />;
  if (!data) return <Loading label="Loading candidate…" />;

  const {
    candidate,
    profile,
    resumes,
    skill_groups: groups,
    applications,
    job_matches: jobMatches,
  } = data;
  const ready = resumes.find((resume) => resume.status === "ready") ?? resumes[0];

  async function setStatus(next: "active" | "archived") {
    setMoreOpen(false);
    setBusy(true);
    try {
      setData(await api.updateCandidate(candidate.id, { status: next }));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not update the candidate.");
    } finally {
      setBusy(false);
    }
  }

  async function share() {
    setMoreOpen(false);
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access is refused in some contexts; the URL is in the address bar.
    }
  }

  const identity = [
    profile?.current_title,
    profile ? `${profile.total_years_experience.toFixed(1)} years` : null,
    candidate.email,
  ]
    .filter(Boolean)
    .join("  ·  ");

  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: "overview", label: "Overview" },
    { key: "skills", label: "Skills & Experience" },
    { key: "resume", label: `Resumes (${resumes.length})` },
    // Counts the ranked jobs, which is what the tab leads with now.
    { key: "matches", label: `Job matches (${jobMatches.length})` },
    { key: "activity", label: "Activity" },
  ];

  return (
    <div className="job-page">
      <Link href="/candidates" className="back-link">
        <IconChevron size={15} className="chev-left" /> Back to Candidates
      </Link>

      <div className="masthead">
        <div className="who-head">
          <span className="avatar avatar-lg">{initials(candidate.full_name)}</span>
          <div>
            <h1 className="masthead-title">
              {displayName(candidate.full_name)}
              {candidate.status !== "active" ? (
                <StatusPill status={candidate.status} />
              ) : null}
            </h1>
            {identity ? <p className="job-meta">{identity}</p> : null}
            <p className="job-meta">
              {[candidate.location, candidate.phone].filter(Boolean).join("  ·  ") || "—"}
            </p>
          </div>
        </div>
        <div className="btn-row">
          <Link href="/jobs" className="btn btn-primary btn-sm">
            <IconShortlist size={15} /> Add to a job
          </Link>
          <div className="more-wrap" ref={moreRef}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setMoreOpen((value) => !value)}
              aria-expanded={moreOpen}
              aria-haspopup="menu"
              disabled={busy}
            >
              More
            </button>
            {moreOpen ? (
              <div className="more-menu" role="menu">
                <button type="button" role="menuitem" onClick={share}>
                  {copied ? "Link copied" : "Share profile"}
                </button>
                {candidate.status === "active" ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-danger"
                    onClick={() => setStatus("archived")}
                  >
                    Archive candidate
                  </button>
                ) : (
                  <button type="button" role="menuitem" onClick={() => setStatus("active")}>
                    Restore candidate
                  </button>
                )}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {error ? <ErrorBanner message={error} /> : null}

      <MatchPanel detail={data} onShowMatches={() => setTab("matches")} />

      <nav className="tabs" aria-label="Candidate sections">
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

      <div className="job-split">
        <div className="job-col">
          {tab === "overview" || tab === "skills" ? (
            <>
              {candidate.summary ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Professional summary</h2>
                  </div>
                  <div className="card-body">
                    <p className="lede">{candidate.summary}</p>
                  </div>
                </section>
              ) : null}

              <section className="card">
                <div className="card-head">
                  <h2>Key skills</h2>
                  <p className="hint">
                    Grouped by a fixed keyword map, not by the model — so the same
                    skill always lands in the same row.
                  </p>
                </div>
                <div className="card-body">
                  {Object.keys(groups).length === 0 ? (
                    <p className="note-body">No skills were parsed from this resume.</p>
                  ) : (
                    <div className="skill-groups">
                      {Object.entries(groups).map(([category, skills]) => (
                        <div className="skill-group" key={category}>
                          <span className="skill-group-label">{category}</span>
                          <div className="tag-row">
                            {skills.map((skill) => (
                              <span className="chip" key={skill}>
                                {skill}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              {profile?.achievements.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Experience &amp; Achievements</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {profile.achievements.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}

              {profile?.experience.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Work history</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {profile.experience.map((entry, index) => (
                        <li key={index}>
                          <strong>{String(entry.title ?? entry.role ?? "Role")}</strong>
                          {entry.company ? ` — ${String(entry.company)}` : ""}
                          {entry.start || entry.end ? (
                            <span className="who-sub">
                              {" "}
                              {String(entry.start ?? "?")} – {String(entry.end ?? "present")}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}

              {profile?.education.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Education</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {profile.education.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}

              {profile?.certifications.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Certifications</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {profile.certifications.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}

              {profile?.projects.length ? (
                <section className="card">
                  <div className="card-head">
                    <h2>Projects</h2>
                  </div>
                  <div className="card-body">
                    <ul className="bullet-list">
                      {profile.projects.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  </div>
                </section>
              ) : null}
            </>
          ) : null}

          {tab === "resume" ? (
            <section className="card">
              <div className="card-head">
                <h2>Resume versions</h2>
                <p className="hint">Newest last. Reprocessing re-parses without re-uploading.</p>
              </div>
              <div className="card-body">
                {resumes.length === 0 ? (
                  <p className="note-body">No resume on file.</p>
                ) : (
                  <ul className="resume-list">
                    {resumes.map((resume) => (
                      <ResumeCard key={resume.id} resume={resume} onReprocessed={load} />
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}

          {tab === "matches" ? (
            <>
            <section className="card">
              <div className="card-head">
                <h2>Best-fit jobs</h2>
                <p className="hint">
                  {jobMatches.length
                    ? `Ranked by the score each screening produced${
                        data.unscored_jobs
                          ? `. ${data.unscored_jobs} job${
                              data.unscored_jobs === 1 ? "" : "s"
                            } not screened against them yet.`
                          : "."
                      }`
                    : "Scored once a screening has run for a job."}
                </p>
              </div>
              <div className="card-body card-body-flush">
                {jobMatches.length === 0 ? (
                  <div className="empty-state">
                    Not scored against any job yet. Open a job and run a screening —
                    the scores shown here are the ones that run produces.
                  </div>
                ) : (
                  <ul className="result-list">
                    {jobMatches.map((match, index) => (
                      <JobMatchRow key={match.job_id} match={match} position={index + 1} />
                    ))}
                  </ul>
                )}
              </div>
            </section>

            <section className="card">
              <div className="card-head">
                <h2>In these pipelines</h2>
                <button
                  type="button"
                  className="icon-btn"
                  aria-pressed={editingStages}
                  aria-label={editingStages ? "Finish editing stages" : "Edit stages"}
                  title={editingStages ? "Done" : "Edit"}
                  onClick={() => setEditingStages((wasEditing) => !wasEditing)}
                >
                  {editingStages ? <IconClose size={17} /> : <IconEdit size={17} />}
                </button>
              </div>
              <div className="card-body card-body-flush">
                {applications.length === 0 ? (
                  <div className="empty-state">
                    Not in any pipeline yet. Open a job and run a screening to place them.
                  </div>
                ) : (
                  <div className="table-wrap">
                    <table className="listing">
                      <thead>
                        <tr>
                          <th>Job</th>
                          <th>Stage</th>
                          <th>Opened</th>
                        </tr>
                      </thead>
                      <tbody>
                        {applications.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <Link href={`/jobs/${row.job_id}`} className="who-name who-link">
                                {row.job_title}
                              </Link>
                            </td>
                            <td>
                              <StageControl
                                applicationId={row.id}
                                stage={row.stage as Stage}
                                jobTitle={row.job_title}
                                editing={editingStages}
                                onMoved={load}
                              />
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
            </>
          ) : null}

          {tab === "activity" ? (
            <section className="card">
              <div className="card-head">
                <h2>Activity</h2>
              </div>
              <div className="card-body">
                <p className="note-body">
                  A per-candidate event log is not built yet. What exists is the pipeline
                  history behind each application and the screening runs themselves —
                  the Job matches tab lists where this candidate stands, and each
                  screening carries its own funnel and evidence.
                </p>
              </div>
            </section>
          ) : null}
        </div>

        <div className="job-col">
          <section className="card">
            <div className="card-head">
              <h2>Candidate status</h2>
              <button
                type="button"
                className="icon-btn"
                aria-pressed={editingStatus}
                aria-label={
                  editingStatus ? "Finish editing candidate status" : "Edit candidate status"
                }
                title={editingStatus ? "Done" : "Edit"}
                onClick={() => setEditingStatus((wasEditing) => !wasEditing)}
              >
                {editingStatus ? <IconClose size={17} /> : <IconEdit size={17} />}
              </button>
            </div>
            <div className="card-body">
              <dl className="detail-list">
                <div className="detail-pair">
                  <dt>Record status</dt>
                  <dd>
                    <RecordStatusControl
                      detail={data}
                      editing={editingStatus}
                      onSaved={setData}
                    />
                  </dd>
                </div>
                <div className="detail-pair">
                  <dt>Pipeline</dt>
                  <dd>
                    {applications.length ? (
                      <>
                        <StageControl
                          applicationId={applications[0].id}
                          stage={applications[0].stage as Stage}
                          jobTitle={applications[0].job_title}
                          editing={editingStatus}
                          onMoved={load}
                        />
                        {/* Applications are ordered most-recently-updated first, so
                            this is the pipeline they are actually moving through.
                            The rest are editable per job under Job matches. */}
                        <span className="hint stage-context">
                          {applications[0].job_title}
                          {applications.length > 1
                            ? ` · +${applications.length - 1} other pipeline${
                                applications.length > 2 ? "s" : ""
                              }`
                            : ""}
                        </span>
                      </>
                    ) : (
                      "Not in a pipeline"
                    )}
                  </dd>
                </div>
                <div className="detail-pair">
                  <dt>Added on</dt>
                  <dd>{shortDate(candidate.created_at)}</dd>
                </div>
                <div className="detail-pair">
                  <dt>Source</dt>
                  {/* Upload is the only ingestion path there is. */}
                  <dd>{resumes.length ? "Resume upload" : "Created directly"}</dd>
                </div>
                {profile ? (
                  <div className="detail-pair">
                    <dt>Parsed by</dt>
                    <dd>
                      <code>{profile.parser_model}</code>
                    </dd>
                  </div>
                ) : null}
                <NotesBox detail={data} onSaved={setData} />
              </dl>
            </div>
          </section>

          {ready ? (
            <section className="card">
              <div className="card-head">
                <h2>Latest resume</h2>
                <button
                  type="button"
                  className="card-link"
                  onClick={() => setTab("resume")}
                >
                  All {resumes.length}
                </button>
              </div>
              <div className="card-body">
                <ul className="resume-list">
                  <ResumeCard resume={ready} onReprocessed={load} />
                </ul>
              </div>
            </section>
          ) : null}

          <section className="card">
            <div className="card-head">
              <h2>Quick actions</h2>
            </div>
            <div className="card-body side-actions">
              <button type="button" className="btn btn-outline btn-sm" onClick={share}>
                {copied ? "Link copied" : "Share profile"}
              </button>
              <Link href="/jobs" className="btn btn-outline btn-sm">
                <IconPlus size={15} /> Add to job
              </Link>
              <Link href="/candidates" className="btn btn-outline btn-sm">
                <IconCandidates size={15} /> All candidates
              </Link>
              {candidate.status === "active" ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm btn-danger"
                  onClick={() => setStatus("archived")}
                  disabled={busy}
                >
                  Archive candidate
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setStatus("active")}
                  disabled={busy}
                >
                  Restore candidate
                </button>
              )}
            </div>
          </section>

          {profile?.domains.length ? (
            <section className="card">
              <div className="card-head">
                <h2>Domains</h2>
              </div>
              <div className="card-body">
                <div className="tag-row">
                  {profile.domains.map((domain) => (
                    <span className="chip" key={domain}>
                      {domain}
                    </span>
                  ))}
                </div>
              </div>
            </section>
          ) : null}

          <section className="card note-card">
            <div className="card-body">
              <h2 className="note-title">
                <IconJobs size={15} /> Why is there no single match score?
              </h2>
              <p className="note-body">
                A score is always relative to one job&rsquo;s requirements. The figure
                above is the best across every screening this candidate appears in, and
                it names the role it came from.
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
