import { type FormEvent, useEffect, useState } from "react";

import { Layout } from "@/components/Layout";
import { api, ApiError } from "@/api/client";
import {
  ApiKeys,
  type ApiKeyName,
  type ApiKeyStatus,
} from "@/api/apiKeys";
import {Glass, GlassPill} from "@/components/glass";
import {ThemeToggle} from "@/components/ThemeToggle";
import {useAuth} from "@/stores/auth";

const KEY_LABELS: Record<ApiKeyName, { label: string; hint: string }> = {
  gemini: {
    label: "Gemini API key",
    hint: "Used by the AI-verification step. Get one at aistudio.google.com/app/apikey.",
  },
  wikidata_test: {
    label: "Wikidata (test) bot password",
    hint: (
      "One line only: Username@BotName:password from " +
      "test.wikidata.org/wiki/Special:BotPasswords. Example: " +
      "Alexander Goldberg IL@MHMPipelineTest:xxxxxxxx (bot name + colon + the " +
      "generated password — not the bot name alone)."
    ),
  },
  wikidata: {
    label: "Wikidata (live) bot password",
    hint: (
      "One line: Username@BotName:password from wikidata.org Special:BotPasswords, " +
      "or OAuth key|secret."
    ),
  },
  huggingface: {
    label: "Hugging Face token",
    hint: (
      "Used to download the Hebrew NER model weights (gated repo) AND to " +
      "call inference on HuggingFace's servers. Create at " +
      "huggingface.co/settings/tokens with Fine-grained type. Tick: " +
      "Repositories → 'Read access to contents of all public gated repos " +
      "you can access' + Inference → 'Make calls to Inference Providers'. " +
      "Leave everything else unchecked."
    ),
  },
};


export default function Settings() {
  const {user} = useAuth();
  const [keys, setKeys] = useState<ApiKeyStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try { setKeys(await ApiKeys.list()); }
    catch (e) { setError(e instanceof ApiError ? e.detail : String(e)); }
  }
  useEffect(() => { void refresh(); }, []);

  return (
    <Layout>
      <div className="space-y-6 max-w-3xl">
        <Glass as="section" className="p-6 space-y-2">
          <div className="kicker">Profile</div>
          <h2 className="text-xl font-semibold">{user?.name}</h2>
          <p className="muted text-sm">
            {user?.email} · <span className="kicker inline-block">{user?.role}</span>
          </p>
        </Glass>

        <Glass as="section" className="p-6 space-y-3">
          <div className="kicker">Appearance</div>
          <h3 className="text-lg font-medium">Color scheme</h3>
          <p className="muted text-sm leading-relaxed">
            Switch between light and dark mode. Your choice is saved in this
            browser and applies across the app, including liquid-glass panels.
          </p>
          <ThemeToggle />
        </Glass>

        <PasswordChangeSection />

        <Glass as="section" className="p-6 space-y-4">
          <div>
            <div className="kicker">Encrypted API keys · zero-knowledge</div>
            <h3 className="text-lg font-medium">Gemini · Wikidata live/test · Hugging Face</h3>
            <p className="muted text-sm leading-relaxed mt-2">
              Each key is wrapped with a Data Encryption Key; the DEK is
              wrapped with the encryption key derived from your password
              and only unlocks while your cookie is presented.{" "}
              <b className="text-ink">The server cannot read these
              without your active session.</b> They never appear back in
              this form — type a new value to replace, or click Clear.
            </p>
          </div>

          {error && <p className="text-danger text-sm">{error}</p>}
          {keys === null && <p className="muted">Loading…</p>}

          {keys?.map((k) => (
            <ApiKeyRow key={k.name} status={k} onChanged={refresh} setError={setError} />
          ))}
        </Glass>
      </div>
    </Layout>
  );
}


function PasswordChangeSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext]       = useState("");
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
      await api.post("/auth/change-password",
        { current_password: current, new_password: next });
      setOk(true);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Change failed");
    } finally { setSubmitting(false); }
  }

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div className="kicker">Security</div>
      <h3 className="text-lg font-medium">Change password</h3>
      <p className="muted text-sm leading-relaxed">
        Changing here preserves your saved API keys: we re-derive your
        encryption key from the new password and re-wrap each stored DEK
        in place.
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
        {error && <p className="text-danger text-sm">{error}</p>}
        {ok && <p className="text-biu-sky text-sm">Password changed.</p>}
        <button type="submit" disabled={submitting} className="button-primary">
          {submitting ? "Saving…" : "Change password"}
        </button>
      </form>
    </Glass>
  );
}


function ApiKeyRow({
  status, onChanged, setError,
}: {
  status: ApiKeyStatus;
  onChanged: () => Promise<void>;
  setError: (s: string | null) => void;
}) {
  const meta = KEY_LABELS[status.name];
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await ApiKeys.set(status.name, value);
      setValue(""); setShow(false); setEditing(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Save failed");
    } finally { setBusy(false); }
  }

  async function clear() {
    if (!confirm(`Delete the stored ${meta.label}?`)) return;
    setBusy(true); setError(null);
    try {
      await ApiKeys.delete(status.name);
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Delete failed");
    } finally { setBusy(false); }
  }

  return (
    <div className="border-t border-white/5 pt-4 space-y-2 first:border-t-0 first:pt-0">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <p className="font-medium">{meta.label}</p>
          <p className="muted text-xs">{meta.hint}</p>
        </div>
        <GlassPill className={`px-3 py-0.5 text-[10px] kicker ${status.set ? "text-biu-sky" : "muted"}`}>
          {status.set ? "stored" : "not set"}
        </GlassPill>
      </div>

      {!editing ? (
        <div className="flex gap-2 items-center text-sm muted">
          <span>{status.set ? "●●●●●●●●●●●●" : "—"}</span>
          <button onClick={() => setEditing(true)} className="button-ghost text-xs">
            {status.set ? "Replace" : "Add"}
          </button>
          {status.set && (
            <button onClick={clear} disabled={busy} className="button-ghost text-xs text-danger">Clear</button>
          )}
        </div>
      ) : (
        <form onSubmit={save} className="flex flex-wrap gap-2 items-center">
          <input type={show ? "text" : "password"} required autoFocus
                 placeholder={status.set ? "stored — type to replace" : "paste new value"}
                 value={value} onChange={(e) => setValue(e.target.value)}
                 className="input-glass flex-1 min-w-[200px]"
                 autoComplete="off" />
          <label className="text-xs muted flex items-center gap-1">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
            show
          </label>
          <button type="submit" disabled={busy} className="button-primary text-xs">Save</button>
          <button type="button" onClick={() => { setEditing(false); setValue(""); setShow(false); }} className="button-ghost text-xs">Cancel</button>
        </form>
      )}
    </div>
  );
}
