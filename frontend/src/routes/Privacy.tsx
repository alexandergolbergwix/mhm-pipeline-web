import { Link } from "react-router-dom";

const LAST_UPDATED = "2026-06-01";

export default function Privacy() {
  return (
    <div className="grid place-items-center min-h-screen px-4 py-8">
      <article
        className="glass p-8 w-full max-w-2xl space-y-6"
        data-testid="privacy-notice"
      >
        <header className="space-y-1">
          <div className="kicker">Bar-Ilan University · MHM</div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Privacy Notice
          </h1>
          <p className="text-xs muted" data-testid="last-updated">
            Last updated: {LAST_UPDATED}
          </p>
        </header>

        <p className="text-sm muted">
          This notice explains how the MHM Pipeline collects and processes
          personal data, in line with Article 13 of the EU General Data
          Protection Regulation (GDPR) and applicable Israeli privacy law.
        </p>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">Controller</h2>
          <p className="text-sm muted">
            MHM Pipeline is operated by{" "}
            <span data-testid="controller-name">
              Alex Goldberg, Bar-Ilan University
            </span>{" "}
            (contact:{" "}
            <span data-testid="controller-contact">
              privacy@TODO
            </span>
            ).
          </p>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">
            What we collect
          </h2>
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">
              For the public access-request form
            </h3>
            <ul className="list-disc list-inside text-sm muted space-y-1">
              <li>Name</li>
              <li>Email address</li>
              <li>Institutional affiliation</li>
              <li>Justification text (the reason you are requesting access)</li>
              <li>IP address (stored only as an HMAC hash, not in plaintext)</li>
              <li>User-agent string (truncated to 512 characters)</li>
            </ul>
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">For account-holders</h3>
            <p className="text-sm muted">
              All of the above, plus an Argon2id-hashed password. We never
              store your password in plaintext or in a reversible form.
            </p>
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">Legal basis</h2>
          <p className="text-sm muted">
            We process the data above under Article 6(1)(e) GDPR — performance
            of a task carried out in the public interest, namely academic
            research on the cataloguing of Hebrew manuscripts — and/or
            Article 6(1)(a) GDPR — your explicit consent at the point of
            submission.
          </p>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">
            How we store it
          </h2>
          <ul className="list-disc list-inside text-sm muted space-y-1">
            <li>
              Every personally-identifiable column is encrypted at rest with
              AES-256-GCM.
            </li>
            <li>
              Email lookup is performed via an HMAC blind index, so no
              plaintext email address ever touches disk.
            </li>
            <li>Passwords are hashed with Argon2id.</li>
            <li>
              IP addresses are HMAC-hashed before storage; we never retain the
              plaintext IP.
            </li>
          </ul>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">
            Sub-processors
          </h2>
          <ul className="list-disc list-inside text-sm muted space-y-1">
            <li>
              <strong>Resend</strong> — transactional email delivery (United
              States).
            </li>
            <li>
              <strong>Cloudflare</strong> — Turnstile bot-detection on public
              forms.
            </li>
            <li>
              <strong>Heroku / Salesforce</strong> — application hosting and
              managed PostgreSQL.
            </li>
            <li>
              <strong>Modal</strong> — named-entity recognition inference
              (United States).
            </li>
          </ul>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">Retention</h2>
          <ul className="list-disc list-inside text-sm muted space-y-1">
            <li>
              <strong>Denied requests:</strong> retained for 30 days from the
              date of review, then deleted.
            </li>
            <li>
              <strong>Abandoned requests</strong> (email never confirmed):
              retained for 7 days from creation, then deleted.
            </li>
            <li>
              <strong>Approved accounts:</strong> retained for the lifetime of
              the account plus 6 months after closure.
            </li>
          </ul>
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold tracking-tight">Your rights</h2>
          <p className="text-sm muted">
            Under the GDPR you have the right to:
          </p>
          <ul className="list-disc list-inside text-sm muted space-y-1">
            <li>Access your personal data (Article 15)</li>
            <li>Request rectification of inaccurate data (Article 16)</li>
            <li>Request erasure (Article 17)</li>
            <li>Receive your data in a portable format (Article 20)</li>
            <li>Object to processing (Article 21)</li>
          </ul>
          <p className="text-sm muted">
            To exercise any of these rights, contact{" "}
            <span data-testid="rights-contact">privacy@TODO</span>. We aim to
            respond within 30 days.
          </p>
          <p className="text-sm muted">
            You also have the right to lodge a complaint with the Israeli
            Privacy Protection Authority (PPA) and, where applicable, with
            your lead EU supervisory authority.
          </p>
        </section>

        <div className="pt-2 text-xs muted">
          <Link
            to="/request-access"
            className="hover:text-ink underline-offset-2 hover:underline"
          >
            ← Back to access request
          </Link>
        </div>
      </article>
    </div>
  );
}
