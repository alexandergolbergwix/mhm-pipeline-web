import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "@/api/client";

interface Resp {
  ok: boolean;
  dev_reset_url: string | null;
}

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<Resp | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      setDone(await api.post<Resp>("/onboarding/forgot-password", { email }));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Request failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid place-items-center min-h-screen px-4">
      <div className="glass p-8 w-full max-w-sm space-y-5">
        <header className="space-y-1">
          <div className="kicker">Password reset</div>
          <h1 className="text-2xl font-semibold">Forgot your password?</h1>
          <p className="muted text-sm">
            Enter your email. If we have an account for you, you'll receive a
            single-use reset link.
          </p>
        </header>

        {done ? (
          <div className="space-y-3 text-sm">
            <p className="muted">
              If <b className="text-ink">{email}</b> matches an account, the
              reset link has been issued.
            </p>
            {done.dev_reset_url && (
              <div>
                <div className="kicker mb-1">Dev mode</div>
                <div className="rounded-xl px-3 py-2 font-mono text-xs break-all"
                     style={{ background: "rgba(0,0,0,0.36)", border: "1px solid var(--line)" }}>
                  {done.dev_reset_url}
                </div>
              </div>
            )}
            <Link to="/login" className="button-ghost inline-block">Back to sign in</Link>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-3">
            <input type="email" required placeholder="email@example.com"
                   value={email} onChange={(e) => setEmail(e.target.value)}
                   className="input-glass" />
            {error && <p className="text-red-300 text-sm">{error}</p>}
            <button type="submit" disabled={submitting} className="button-primary w-full">
              {submitting ? "Sending…" : "Send reset link"}
            </button>
            <Link to="/login" className="block text-center text-xs muted hover:text-ink">
              Back to sign in
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
