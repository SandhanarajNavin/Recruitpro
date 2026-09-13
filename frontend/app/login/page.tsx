"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { PasswordInput } from "@/components/PasswordInput";
import { ApiError, api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      router.replace("/dashboard");
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not sign in. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="panel login-panel" onSubmit={submit}>
        <div className="panel-head">
          <h1>RecruitPro</h1>
          <p className="hint">Sign in to continue.</p>
        </div>
        <div className="panel-body">
          <label className="field-label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            value={email}
            placeholder="you@company.com"
            autoComplete="username"
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <PasswordInput
            id="password"
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
          />

          {error ? <div className="error-banner">{error}</div> : null}

          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <p className="hint auth-switch">
            New recruiter? <Link href="/signup">Create an account</Link>
          </p>
        </div>
      </form>
    </div>
  );
}
