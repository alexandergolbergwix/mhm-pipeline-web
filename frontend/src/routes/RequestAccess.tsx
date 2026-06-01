import { type ChangeEvent, type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AccessRequests } from "@/api/accessRequests";

declare global {
  interface Window {
    onTurnstileSuccess?: (token: string) => void;
  }
}

interface FormState {
  name: string;
  email: string;
  affiliation: string;
  justification: string;
  website: string;
  turnstile_token: string;
}

const INITIAL_STATE: FormState = {
  name: "",
  email: "",
  affiliation: "",
  justification: "",
  website: "",
  turnstile_token: "",
};

const TURNSTILE_SITE_KEY: string =
  import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "1x00000000000000000000AA";

const MIN_JUSTIFICATION_CHARS = 40;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function RequestAccess() {
  const [formState, setFormState] = useState<FormState>(INITIAL_STATE);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attemptedSubmit, setAttemptedSubmit] = useState(false);

  const charCount = formState.justification.length;
  const justificationOk = charCount >= MIN_JUSTIFICATION_CHARS;

  const nameInvalid = attemptedSubmit && formState.name.trim().length === 0;
  const emailInvalid =
    attemptedSubmit &&
    (formState.email.trim().length === 0 || !EMAIL_RE.test(formState.email));
  const affiliationInvalid =
    attemptedSubmit && formState.affiliation.trim().length === 0;

  useEffect(() => {
    window.onTurnstileSuccess = (token: string) => {
      setFormState((s) => ({ ...s, turnstile_token: token }));
    };
    return () => {
      window.onTurnstileSuccess = undefined;
    };
  }, []);

  function updateField<K extends keyof FormState>(key: K) {
    return (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const value = e.target.value;
      setFormState((s) => ({ ...s, [key]: value }));
    };
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setAttemptedSubmit(true);
    if (!justificationOk || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await AccessRequests.submit(formState);
      setSuccess(true);
    } catch (err: unknown) {
      const status =
        typeof err === "object" && err !== null && "status" in err
          ? (err as { status?: number }).status
          : undefined;
      if (status === 429) {
        setError("Too many requests, please try again later.");
      } else if (status === 400 || status === 422) {
        setError("Please check your responses and try again.");
      } else {
        setError("Something went wrong. Please try again later.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (success) {
    return (
      <div className="grid place-items-center min-h-screen px-4">
        <div
          className="glass p-8 w-full max-w-md space-y-4"
          data-testid="success-message"
        >
          <header className="space-y-1">
            <div className="kicker">Bar-Ilan University · MHM</div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Request received
            </h1>
          </header>
          <p className="text-sm muted">
            If your email is eligible, you&apos;ll receive next steps shortly.
            Please check your inbox.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid place-items-center min-h-screen px-4 py-8">
      <form
        onSubmit={onSubmit}
        className="glass p-8 w-full max-w-lg space-y-5"
        data-testid="request-access-form"
      >
        <header className="space-y-1">
          <div className="kicker">Bar-Ilan University · MHM</div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Request access to MHM Pipeline
          </h1>
          <p className="text-sm muted">
            The MHM Pipeline workbench is invite-only. Submissions are reviewed
            by the project team; we&apos;ll be in touch once your request has
            been considered.
          </p>
        </header>

        <label className="block space-y-1">
          <span className="text-sm muted">Name</span>
          <input
            type="text"
            autoComplete="name"
            required
            value={formState.name}
            onChange={updateField("name")}
            className="input-glass"
            data-testid="input-name"
            aria-invalid={nameInvalid ? "true" : undefined}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm muted">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={formState.email}
            onChange={updateField("email")}
            className="input-glass"
            data-testid="input-email"
            aria-invalid={emailInvalid ? "true" : undefined}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm muted">Affiliation</span>
          <input
            type="text"
            autoComplete="organization"
            required
            placeholder="University / institution"
            value={formState.affiliation}
            onChange={updateField("affiliation")}
            className="input-glass"
            data-testid="input-affiliation"
            aria-invalid={affiliationInvalid ? "true" : undefined}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm muted">
            Why would you like access? (Research interest, intended use)
          </span>
          <textarea
            rows={5}
            required
            value={formState.justification}
            onChange={updateField("justification")}
            className="input-glass resize-y"
            data-testid="input-justification"
            aria-invalid={!justificationOk ? "true" : undefined}
            aria-describedby="justification-char-count"
          />
          <div
            id="justification-char-count"
            className={`text-xs ${
              justificationOk ? "muted" : "text-amber-300"
            }`}
            data-testid="justification-char-count"
          >
            {charCount} / {MIN_JUSTIFICATION_CHARS} characters minimum
          </div>
        </label>

        <input
          type="text"
          name="website"
          tabIndex={-1}
          autoComplete="off"
          aria-hidden="true"
          value={formState.website}
          onChange={updateField("website")}
          style={{ position: "absolute", left: "-9999px", opacity: 0 }}
          data-testid="input-website"
        />

        <div
          className="cf-turnstile"
          data-sitekey={TURNSTILE_SITE_KEY}
          data-callback="onTurnstileSuccess"
        />

        {error && (
          <p className="text-sm text-red-300" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !justificationOk}
          className="button-primary w-full"
          data-testid="submit-button"
        >
          {submitting ? "Submitting…" : "Submit request"}
        </button>

        <div
          className="text-xs muted text-center"
          data-testid="privacy-notice-link"
        >
          By submitting, you agree to our{" "}
          <Link
            to="/privacy"
            className="hover:text-ink underline-offset-2 hover:underline"
          >
            Privacy Notice
          </Link>
          .
        </div>

        <div className="text-xs muted text-center">
          Already have an account?{" "}
          <a
            href="/login"
            className="hover:text-ink underline-offset-2 hover:underline"
          >
            Sign in
          </a>
        </div>
      </form>
    </div>
  );
}
