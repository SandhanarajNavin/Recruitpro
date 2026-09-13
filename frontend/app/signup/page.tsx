"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { PasswordInput } from "@/components/PasswordInput";
import { ApiError, api } from "@/lib/api";

/** Mirrors the server rule: bcrypt hashes at most 72 bytes. */
const MAX_PASSWORD_BYTES = 72;
const MIN_PASSWORD_LENGTH = 8;

function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
    return `Password must be at most ${MAX_PASSWORD_BYTES} bytes.`;
  }
  if (password !== confirm) return "Passwords do not match.";
  return null;
}

export default function SignupPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();

    // Checked here purely to save a round trip — the server enforces the same
    // rules and is the authority.
    const problem = passwordProblem(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }

    setBusy(true);
    setError(null);
    try {
      await api.register(name, email, password);
      router.replace("/dashboard");
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not create the account. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="panel login-panel" onSubmit={submit}>
        <div className="panel-head">
          <h1>Create your account</h1>
          <p className="hint">
            Every recruiter gets their own private candidate repository. Nothing you
            upload is visible to anyone else.
          </p>
        </div>
        <div className="panel-body">
          <label className="field-label" htmlFor="name">
            Full name
          </label>
          <input
            id="name"
            type="text"
            value={name}
            autoComplete="name"
            minLength={2}
            onChange={(event) => setName(event.target.value)}
            required
          />

          <label className="field-label" htmlFor="email">
            Work email
          </label>
          <input
            id="email"
            type="email"
            value={email}
            autoComplete="username"
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <PasswordInput
            id="password"
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            minLength={MIN_PASSWORD_LENGTH}
          />

          <PasswordInput
            id="confirm"
            label="Confirm password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
          />

          {error ? <div className="error-banner">{error}</div> : null}

          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Creating account…" : "Create account"}
          </button>

          <p className="hint auth-switch">
            Already have an account? <Link href="/login">Sign in</Link>
          </p>
        </div>
      </form>
    </div>
  );
}
