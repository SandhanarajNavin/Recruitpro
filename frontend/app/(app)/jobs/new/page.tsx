"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ErrorBanner } from "@/components/Shared";
import { ApiError, api } from "@/lib/api";

const MIN_DESCRIPTION = 40;

export default function NewJobPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = description.trim().length < MIN_DESCRIPTION;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (tooShort) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createJob({
        title: title.trim() || undefined,
        description: description.trim(),
        location: location.trim() || undefined,
      });
      router.push(`/jobs/${created.job.id}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not create the job.");
      setBusy(false);
    }
  }

  return (
    <>
      <div className="masthead">
        <div>
          <h1>New job</h1>
          <p>
            Paste the job description. Requirements are extracted immediately so you can
            check them before running a screening.
          </p>
        </div>
        <Link href="/jobs" className="btn btn-ghost btn-sm">
          Cancel
        </Link>
      </div>

      <form className="panel" onSubmit={submit}>
        <div className="panel-body">
          <div className="field-pair">
            <div>
              <label className="field-label" htmlFor="title">
                Title <span className="hint">optional — taken from the description if blank</span>
              </label>
              <input
                id="title"
                type="text"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Senior Full-Stack Engineer"
              />
            </div>
            <div>
              <label className="field-label" htmlFor="location">
                Location <span className="hint">optional</span>
              </label>
              <input
                id="location"
                type="text"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                placeholder="Remote (EU)"
              />
            </div>
          </div>

          <label className="field-label" htmlFor="description">
            Job description
          </label>
          <textarea
            id="description"
            rows={18}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={
              "Paste the full description — responsibilities, requirements and nice-to-haves.\n" +
              "A clear Requirements section produces much better required/preferred separation."
            }
            required
          />
          <div className="char-count">
            {description.trim().length} characters
            {tooShort ? ` · at least ${MIN_DESCRIPTION} needed` : ""}
          </div>

          {error ? <ErrorBanner message={error} /> : null}

          <button type="submit" className="btn btn-primary" disabled={busy || tooShort}>
            {busy ? "Parsing requirements…" : "Create job"}
          </button>
        </div>
      </form>
    </>
  );
}
