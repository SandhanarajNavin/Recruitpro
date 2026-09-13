"use client";

import { useRef, useState } from "react";
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
  const [reading, setReading] = useState(false);
  const [sourceFile, setSourceFile] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const tooShort = description.trim().length < MIN_DESCRIPTION;

  /**
   * Fills the textarea from a file rather than creating the job outright.
   *
   * Extraction is lossy in ways only the recruiter can judge — a two-column PDF
   * interleaves, a requirements table flattens — and the parsed requirements and JD
   * vector are built from whatever text goes in. So the file lands in the editor and
   * they confirm it, which is the same review step pasting already gets.
   */
  async function readFile(file: File | undefined) {
    if (!file) return;
    setReading(true);
    setError(null);
    try {
      const extracted = await api.extractJobDescription(file);
      setDescription(extracted.text);
      setSourceFile(extracted.filename);
      if (!title.trim()) {
        // Only a suggestion — the parser takes the title from the text when blank.
        const stem = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
        if (stem) setTitle(stem.slice(0, 200));
      }
    } catch (cause) {
      setSourceFile(null);
      setError(
        cause instanceof ApiError ? cause.message : "Could not read that file.",
      );
    } finally {
      setReading(false);
    }
  }

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
            Upload or paste the job description. Requirements are extracted immediately
            so you can check them before running a screening.
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

          <div
            className={dragging ? "dropzone dropzone-active" : "dropzone"}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              void readFile(event.dataTransfer.files[0]);
            }}
          >
            <p>Drop a PDF, DOCX or text file — or paste below</p>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={reading}
              onClick={() => fileRef.current?.click()}
            >
              {reading ? "Reading…" : "Choose file"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.doc,.txt,.md"
              hidden
              onChange={(event) => {
                void readFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </div>

          {sourceFile ? (
            <p className="hint">
              Read from <strong>{sourceFile}</strong>. Check it below before creating —
              PDFs in particular can interleave columns or flatten tables.
            </p>
          ) : null}

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

          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || reading || tooShort}
          >
            {busy ? "Parsing requirements…" : "Create job"}
          </button>
        </div>
      </form>
    </>
  );
}
