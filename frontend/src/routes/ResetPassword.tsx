import { type FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError } from "@/api/client";
import {Glass} from "@/components/glass";

interface Resp { ok: boolean; api_keys_wiped: number; }

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) { setError("Passwords don't match."); return; }
    if (password.length < 8)  { setError("Min 8 characters.");      return; }
    setSubmitting(true);
    try {
      const r = await api.post<Resp>("/onboarding/reset-password", { token, new_password: password });
      if (r.api_keys_wiped > 0) {
        alert(`Reset complete. ${r.api_keys_wiped} stored API key(s) were wiped — please re-enter them in Settings.`);
      }
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Reset failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (!confirmed) {
    return (
      <div className="grid place-items-center min-h-screen px-4">
        <Glass className="p-8 w-full max-w-md space-y-5">
          <div className="kicker">Heads up</div>
          <h1 className="text-2xl font-semibold">Resetting your password</h1>
          <p className="muted text-sm leading-relaxed">
            For your security, your saved API keys (Gemini, Wikidata,
            Wikibase Cloud) are encrypted with a key derived from your{" "}
            <b className="text-ink">current</b> password. We don't have your
            current password — only its hash — so resetting will{" "}
            <b className="text-ink">permanently wipe</b> those stored keys.
            You'll need to paste them back in after signing in.
          </p>
          <div className="flex gap-3">
            <button onClick={() => setConfirmed(true)} className="button-primary">
              I understand, continue
            </button>
            <Link to="/login" className="button-ghost">Cancel</Link>
          </div>
        </Glass>
      </div>
    );
  }

  return (
    <div className="grid place-items-center min-h-screen px-4">
      <Glass as="form" className="p-8 w-full max-w-sm space-y-4" onSubmit={onSubmit}>
        <div className="kicker">Password reset</div>
        <h1 className="text-2xl font-semibold">Set a new password</h1>
        <input type="password" required placeholder="New password"
               value={password} onChange={(e) => setPassword(e.target.value)}
               className="input-glass" />
        <input type="password" required placeholder="Confirm"
               value={confirm} onChange={(e) => setConfirm(e.target.value)}
               className="input-glass" />
        {error && <p className="text-red-300 text-sm">{error}</p>}
        <button type="submit" disabled={submitting} className="button-primary w-full">
          {submitting ? "Resetting…" : "Reset password"}
        </button>
      </Glass>
    </div>
  );
}
