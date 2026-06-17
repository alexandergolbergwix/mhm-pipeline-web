import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError } from "@/api/client";
import type { AuthUser } from "@/stores/auth";
import { useAuth } from "@/stores/auth";
import {Glass} from "@/components/glass";

interface Preview {
  email: string;
  role: "admin" | "editor";
  expires_at: string;
}

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  const { bootstrap } = useAuth();

  const [preview, setPreview] = useState<Preview | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("Missing token.");
      return;
    }
    api
      .get<Preview>(`/onboarding/invite/${token}`)
      .then(setPreview)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Invitation lookup failed"));
  }, [token]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setSubmitting(true);
    try {
      await api.post<AuthUser>("/onboarding/accept-invite", { token, password });
      await bootstrap();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Accept failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid place-items-center min-h-screen px-4">
      <Glass className="p-8 w-full max-w-sm space-y-5">
        <header className="space-y-1">
          <div className="kicker">Accept invitation</div>
          <h1 className="text-2xl font-semibold">Welcome to MHM Pipeline</h1>
        </header>

        {preview && (
          <div className="text-sm muted">
            Setting up the account for <b className="text-ink">{preview.email}</b>{" "}
            (<span className="kicker">{preview.role}</span>).
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-3">
          <label className="block space-y-1">
            <span className="text-sm muted">Choose a password</span>
            <input type="password" autoComplete="new-password" required
                   value={password} onChange={(e) => setPassword(e.target.value)}
                   className="input-glass" />
          </label>
          <label className="block space-y-1">
            <span className="text-sm muted">Confirm</span>
            <input type="password" autoComplete="new-password" required
                   value={confirm} onChange={(e) => setConfirm(e.target.value)}
                   className="input-glass" />
          </label>

          {error && <p className="text-red-300 text-sm">{error}</p>}

          <button type="submit" disabled={!preview || submitting} className="button-primary w-full">
            {submitting ? "Creating account…" : "Create account"}
          </button>
        </form>
      </Glass>
    </div>
  );
}
