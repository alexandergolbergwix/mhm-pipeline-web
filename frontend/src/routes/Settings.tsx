import { type FormEvent, useState } from "react";

import { Layout } from "@/components/Layout";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/stores/auth";

export default function Settings() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function changePw(e: FormEvent) {
    e.preventDefault();
    setError(null); setOk(false);
    if (next !== confirm) { setError("New passwords don't match."); return; }
    if (next.length < 8)  { setError("Min 8 characters.");          return; }
    setSubmitting(true);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      setOk(true);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Change failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Layout>
      <div className="space-y-6 max-w-2xl">
        <section className="glass p-6 space-y-2">
          <div className="kicker">Profile</div>
          <h2 className="text-xl font-semibold">{user?.name}</h2>
          <p className="muted text-sm">
            {user?.email} · <span className="kicker inline-block">{user?.role}</span>
          </p>
        </section>

        <section className="glass p-6 space-y-3">
          <div className="kicker">Security</div>
          <h3 className="text-lg font-medium">Change password</h3>
          <p className="muted text-sm leading-relaxed">
            Changing your password here keeps your saved API keys intact —
            we re-derive your encryption key from the new password and
            re-wrap every stored secret in place.
          </p>
          <form onSubmit={changePw} className="space-y-3 mt-2">
            <input type="password" required placeholder="Current password"
                   value={current} onChange={(e) => setCurrent(e.target.value)}
                   autoComplete="current-password" className="input-glass" />
            <input type="password" required placeholder="New password"
                   value={next} onChange={(e) => setNext(e.target.value)}
                   autoComplete="new-password" className="input-glass" />
            <input type="password" required placeholder="Confirm new password"
                   value={confirm} onChange={(e) => setConfirm(e.target.value)}
                   autoComplete="new-password" className="input-glass" />
            {error && <p className="text-red-300 text-sm">{error}</p>}
            {ok && <p className="text-biu-sky text-sm">Password changed.</p>}
            <button type="submit" disabled={submitting} className="button-primary">
              {submitting ? "Saving…" : "Change password"}
            </button>
          </form>
        </section>

        <section className="glass p-6 space-y-2">
          <div className="kicker">API Keys</div>
          <h3 className="text-lg font-medium">Gemini · Wikidata · Wikibase Cloud</h3>
          <p className="muted text-sm leading-relaxed">
            The encrypted-key entry surface lands in Phase 9. Once shipped,
            keys you save here are wrapped with a key derived from your
            password and only readable while your cookie is presented.
            Forgetting your password permanently wipes them (zero-knowledge).
          </p>
        </section>
      </div>
    </Layout>
  );
}
