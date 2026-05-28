import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "@/stores/auth";

export default function Login() {
  const { user, login, error } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  if (user) return <Navigate to="/" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(email, password);
      const dest = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(dest, { replace: true });
    } catch {
      /* error surfaced via the auth store */
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid place-items-center min-h-screen px-4">
      <form onSubmit={onSubmit} className="glass p-8 w-full max-w-sm space-y-4">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">MHM Pipeline</h1>
          <p className="text-sm text-glass-inkSub">Sign in to continue.</p>
        </header>

        <label className="block space-y-1">
          <span className="text-sm text-glass-inkSub">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input-glass"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-glass-inkSub">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input-glass"
          />
        </label>

        {error && (
          <p className="text-sm text-red-300" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting} className="button-primary w-full">
          {submitting ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-xs text-glass-inkSub text-center">
          Accounts are admin-invited. Contact your project owner.
        </p>
      </form>
    </div>
  );
}
