# Deploying MHM Pipeline Web to Heroku

This guide takes a fresh clone of the repo to a live URL on Heroku for
**≈ $16/month** total: 1 Basic dyno + Mini Postgres + sliding to Basic
Postgres if/when the Mazal authority dump pushes you past 1 GB.

The app is a single dyno: FastAPI serves both `/api/*` and the
pre-built Vite frontend from `frontend/dist/`. WebSocket fan-out runs
over Postgres `LISTEN/NOTIFY` — no Redis required.

---

## 0. Prereqs

* a Heroku account
* the [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) (`brew install heroku/brew/heroku`)
* git (you already have it — this is a checked-in repo)
* Node 20+ locally **OR** the buildpack (we use the buildpack below — no
  local node required during deploy)

---

## 1. Create the app + add-ons

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web

# Login (one-time)
heroku login

# Create the app. Pick any unique name; the snippet below uses one;
# replace ${APP} with yours.
APP=mhm-pipeline-web
heroku create "$APP"

# Two buildpacks — Node builds the frontend during slug compile, Python
# builds the FastAPI backend. Order matters.
heroku buildpacks:add heroku/nodejs --app "$APP"
heroku buildpacks:add heroku/python --app "$APP"

# Database — Mini is $5/mo (1 GB). If you'll load the full Mazal authority
# dump (~1 GB), step up to essential-1 ($10/mo, 10 GB) now to avoid a
# mid-flight migration. The app's NOTIFY listener works on all tiers.
heroku addons:create heroku-postgresql:essential-0 --app "$APP"
# OR:
# heroku addons:create heroku-postgresql:essential-1 --app "$APP"
```

---

## 2. Generate + set the encryption keys

These two values are the **only** server-held secrets. Set them ONCE
per environment and **never rotate without re-encrypting** (see §5).

```bash
heroku config:set --app "$APP" \
    MASTER_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
    EMAIL_HMAC_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
    ENV=production \
    COOKIE_SECURE=true \
    SESSION_TTL_HOURS=12 \
    FRONTEND_ORIGIN="https://${APP}.herokuapp.com"
```

> If you ever lose `MASTER_KEY`, **every encrypted column becomes
> unreadable** (emails, names, invitations). API keys are encrypted with
> a *user-derived* KEK, so they're tied to each user's password and
> survive the loss of `MASTER_KEY` — but the user must log in once to
> have them unwrapped server-side.

The full set of server-held config vars, including the seven new ones
added for the public **request-access** flow (§2.2), is:

| Var | Purpose | Required? |
|---|---|---|
| `MASTER_KEY` | AES-GCM master key for PII columns | yes |
| `EMAIL_HMAC_KEY` | HMAC key for the email blind-index | yes |
| `ENV` | `production` enables strict cookie + CORS | yes |
| `COOKIE_SECURE` | `true` in production (HTTPS-only cookies) | yes |
| `SESSION_TTL_HOURS` | sliding session lifetime | yes |
| `FRONTEND_ORIGIN` | CORS allow-list — your real public URL | yes |
| `RESEND_API_KEY` | Resend (resend.com) API key for outbound mail | yes (prod) |
| `RESEND_FROM_EMAIL` | `From:` header — e.g. `MHM Pipeline <noreply@yourdomain.org>` | yes (prod) |
| `ADMIN_NOTIFICATION_EMAIL` | inbox that receives new-request alerts | yes (prod) |
| `TURNSTILE_SITE_KEY` | Cloudflare Turnstile public site key | yes (prod) |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile server secret | yes (prod) |
| `EXTRACTION_MODE` | `modal` / `hf-api` / `local` (see §2.1) | yes |
| `MODAL_NER_URL` | Modal endpoint when `EXTRACTION_MODE=modal` | conditional |
| `HF_TOKEN` | HF token when `EXTRACTION_MODE=hf-api` | conditional |
| `REDIS_URL` | Set automatically by Heroku Redis add-on; used as inference cache L1 | strongly recommended |
| `RATELIMIT_STORAGE_URI` | Same value as `REDIS_URL`; used by `slowapi` for distributed rate limiting | strongly recommended |

### 2.1 — Extraction backend (Modal vs local vs HF)

`EXTRACTION_MODE` picks which inference backend AI Extraction uses
(CLAUDE.md Rule W-11). The Heroku slug is too small to bundle the
model weights, so the realistic choices in production are:

```bash
# Modal (recommended — pay-per-call, all four models in one container)
heroku config:set --app "$APP" \
    EXTRACTION_MODE=modal \
    MODAL_NER_URL=https://<workspace>--mhm-ner-mhmner-web.modal.run

# HF Inference Providers — only works for repos HF has actually
# deployed. Our four models all currently return `inference: None`
# on the free tier, so this mode is for repos HF will serve (i.e.
# none of ours, today).
heroku config:set --app "$APP" \
    EXTRACTION_MODE=hf-api \
    HF_TOKEN=hf_xxxxx

# Local — only if you've upgraded to a Performance dyno with ≥2.5 GB
# RAM AND solved the slug-size problem (e.g. pulling .pt files from
# HF Hub at boot). Not recommended on Heroku.
heroku config:set --app "$APP" EXTRACTION_MODE=local
```

Deploy the Modal app from the web repo:
```bash
cd modal && modal deploy modal_app.py
```
The deploy prints the URL to set as `MODAL_NER_URL`. See
`modal/README.md` and `.claude/commands/deploy-modal.md` for details.

### 2.2 — Spam protection setup (request-access flow)

The public `/request-access` page is protected by a layered spam stack:
a hidden honeypot field, a Cloudflare Turnstile challenge, a free-text
justification ≥ 40 characters, and a double-opt-in email confirmation
before the request is queued for admin review. Outbound mail (the
confirm-your-email link, admin notifications, and the
"someone-tried-to-register-with-your-email" notice) is sent via
[Resend](https://resend.com).

**Resend.** Free tier covers 3,000 emails/month — plenty for this flow.
Sign up at resend.com, then either verify your own domain (recommended
for production) or use the shared `onboarding@resend.dev` sender while
you're still developing. Generate an API key from the Resend dashboard
(it starts with `re_`).

**Cloudflare Turnstile.** Free, unlimited, no CAPTCHA-fatigue UX.
Go to [challenges.cloudflare.com](https://challenges.cloudflare.com) →
**My Account → Turnstile → Add Site**, point it at your production
hostname, and copy the **Site Key** (public, embedded in the form) and
**Secret Key** (server-side verification).

Then set the five new config vars:

```bash
heroku config:set --app "$APP" \
    RESEND_API_KEY="re_..." \
    RESEND_FROM_EMAIL="MHM Pipeline <noreply@yourdomain.org>" \
    ADMIN_NOTIFICATION_EMAIL="you@yourdomain.org" \
    TURNSTILE_SITE_KEY="0x..." \
    TURNSTILE_SECRET_KEY="0x..."
```

**Local dev.** Leave `RESEND_API_KEY` unset and the email transport
falls back to stdout — every outbound message is logged to the console
so you can copy confirmation links by hand. For Turnstile, Cloudflare
publishes documented **test keys** that always pass without contacting
their servers:

| Var | Test value |
|---|---|
| `TURNSTILE_SITE_KEY` | `1x00000000000000000000AA` |
| `TURNSTILE_SECRET_KEY` | `1x0000000000000000000000000000000AA` |

With those set, the widget renders and submits successfully without
real verification — perfect for local + CI.

---

## 3. Tell the Node buildpack to build the frontend

The Node buildpack runs `npm install` + `npm run build` from the
**root** `package.json` by default; we don't have one. Add a tiny shim:

```bash
cat > package.json <<'JSON'
{
  "name": "mhm-pipeline-web",
  "version": "0.1.0",
  "private": true,
  "engines": { "node": "20.x" },
  "scripts": {
    "build": "cd frontend && npm install --no-audit --no-fund && npm run build"
  }
}
JSON

git add package.json && git commit -m "build: root package.json so the Node buildpack builds frontend/"
```

That single `build` script runs on every push; the `frontend/dist/`
output is picked up by `app.main._mount_frontend`.

---

## 4. Deploy

```bash
# First deploy
git remote add heroku https://git.heroku.com/${APP}.git   # CLI usually does this
git push heroku main

# Heroku will:
#   1. Run the Node buildpack → builds frontend/dist/
#   2. Run the Python buildpack → installs backend/pyproject.toml deps
#   3. Run the release dyno → scripts/release.sh → alembic upgrade head
#   4. Boot the web dyno → scripts/start.sh → uvicorn

heroku open --app "$APP"
```

If the release phase fails, check `heroku logs --tail --app "$APP"`. The
most common cause is a missed `MASTER_KEY` / `EMAIL_HMAC_KEY` config var.

---

## 5. Bootstrap the first admin

The web app is invite-only — there's no public signup. Bootstrap the
first admin via a one-shot dyno:

```bash
heroku run --app "$APP" bash
# inside the dyno:
python -m scripts.create_user --email you@example.org --name "Your Name"
# enter a password at the prompt
exit
```

That user starts as `role='editor'`; promote to admin once:

```bash
heroku pg:psql --app "$APP"
mhm-pipeline-web=> UPDATE users SET role='admin';
mhm-pipeline-web=> \q
```

(With only one user the blanket UPDATE is safe; for multi-user contexts
constrain on the email_index of the bootstrap account.)

From there: log in, go to **/admin/invites**, send invites to your
collaborators.

---

## 6. Ongoing operations

```bash
# tail logs
heroku logs --tail --app "$APP"

# run a one-off shell (useful for migrations + ad-hoc queries)
heroku run --app "$APP" bash

# run alembic explicitly
heroku run --app "$APP" "cd backend && alembic upgrade head"

# scale up if you outgrow Basic
heroku ps:resize web=standard-1x --app "$APP"

# back up Postgres on demand
heroku pg:backups:capture --app "$APP"
heroku pg:backups:download --app "$APP"
```

### 6.5 — DDoS hardening (Cloudflare in front of Heroku)

The `slowapi` rate-limit middleware on the dyno is the **last** line of
defence, not the first — it sees every request that's already paid the
TLS + bandwidth + dyno-CPU cost. For production, put Cloudflare's free
proxy in front of Heroku so the abusive traffic never reaches the dyno
at all.

Steps:

1. Add your custom hostname to the Heroku app:
   ```bash
   heroku domains:add myapp.example.org --app "$APP"
   ```
   Heroku will print a target like `whispering-foo-1234.herokudns.com`.
2. In Cloudflare DNS, create a **CNAME** record:
   `myapp.example.org → whispering-foo-1234.herokudns.com`, with
   **Proxy status: Proxied (orange cloud) ON**.
3. In the Cloudflare dashboard under **Security → Bots**, enable
   **Bot Fight Mode** (free).
4. Add a **Rate Limiting Rule** (or a legacy **Page Rule**) for
   `/api/access-request` and `/api/auth/login` — e.g. 10 requests/minute
   per IP for the access-request endpoint, 20/minute for login. These
   are the two endpoints that are reachable without a session and so
   carry the most spam + brute-force risk.

This is optional but strongly recommended for any deployment exposed to
the open internet: Cloudflare absorbs the volumetric layer for free,
Bot Fight Mode swats the obvious automated traffic, and `slowapi`
remains the in-process safety net for anything that slips through.

### 6.6 — GDPR retention purge

The self-service access-request flow collects PII (email, name,
affiliation, justification). GDPR Article 5(1)(e) — storage limitation
— forbids us from keeping that PII any longer than the lawful basis
lasts. The `scripts/run_purge.py` job enforces a daily TTL sweep; run
it under Heroku Scheduler.

- Add the free Scheduler add-on:
  ```bash
  heroku addons:create scheduler:standard --app "$APP"
  ```
- Open the Scheduler dashboard to register the job:
  ```bash
  heroku addons:open scheduler --app "$APP"
  ```
- Add a daily job at **03:00 UTC** with command `python -m scripts.run_purge`.
- What it deletes:
  - `pending_email_confirm` rows older than **7 days** from `created_at` —
    the requester never clicked the double-opt-in link, so we have no
    lawful basis to keep their PII.
  - `denied` rows older than **30 days** from `reviewed_at` — the audit
    window for triaging a denial has by then expired.
- What it does NOT delete:
  - `approved` rows (audit trail of who let whom in).
  - `pending_admin` rows. If one has been sitting un-reviewed for more
    than **14 days** since `confirmed_at` the job logs a `WARNING` with
    the row IDs so an admin can act — but never auto-decides on the
    requester's behalf.

The job is idempotent and safe to run on demand:
`heroku run --app "$APP" "cd backend && python -m scripts.run_purge"`.

---

### 6.7 — Entity versioning: snapshots + prune (Rule W-21)

Two scheduled jobs back the entity-event log:

1. **snapshot-entities** — runs 3 times per day at **00:05, 08:05, 16:05 UTC**.
   - Command: `python -m scripts.run_snapshot`
   - Frequency: Every 8 hours (00:05 / 08:05 / 16:05 UTC)
   - Materialises a full-state row into `entity_snapshot` for every
     `(project, entity_type, entity_id)` that was touched in the
     just-finished slot. The `entity_snapshot` table is the cold archive
     tier — kept forever, the only thing that survives the 1000-event
     prune.

2. **prune-events** — runs daily at **03:05 UTC**.
   - Command: `python -m scripts.run_prune_events`
   - Frequency: Every day at 03:05 UTC
   - Deletes the oldest events past the 1000-per-entity cap. Never
     deletes anchor events (latest create / snapshot) — those are
     required to replay `state_at_rev` for older history.

Before either job runs in production, the database tier **MUST** be
Essential-1 or higher — Essential-0 (1 GB) will fill within one full
100k-record upload + its event history (~1.7 GB per project). Upgrade
with:

```bash
heroku addons:upgrade heroku-postgresql:essential-1 --app mhm-pipeline-web
```

One-time backfill — after the migration `0012_entity_versioning` lands,
run the backfill script once to add `OP_CREATE` events for every
existing read-model row:

```bash
heroku run --app mhm-pipeline-web -- bash -lc "cd backend && python -m scripts.backfill_versioning"
```

The script is idempotent; safe to re-run after partial failure.

---

## 6.8 — Redis (inference cache L1 + rate-limit storage)

A Heroku Redis Mini add-on ($3/month) provides the L1 inference
cache and the distributed `slowapi` rate-limit storage.

```bash
# Provision (one-time)
heroku addons:create heroku-redis:mini --app "$APP"

# heroku-redis:mini auto-sets REDIS_URL. Wire it to the rate limiter:
heroku config:set --app "$APP" \
    RATELIMIT_STORAGE_URI="$(heroku config:get REDIS_URL --app "$APP")"
```

The backend reads `REDIS_URL` / `RATELIMIT_STORAGE_URI` at startup.
When neither is set (dev/CI), `get_redis()` returns `None` and the
app falls back to Postgres-only caching and in-process rate limiting
— no code-path change required.

**TLS note.** Heroku Redis uses a self-signed certificate on
`rediss://` URLs. The client is initialised with
`ssl_cert_reqs=None` to disable hostname verification while keeping
transport encrypted — this is intentional (see Rule W-25 in
`CLAUDE.md`).

## 7. Cost reference (typical, as of 2026)

| Item                                | $/month |
|-------------------------------------|--------:|
| Heroku Basic dyno                   | **$7**  |
| Heroku Postgres Essential-0 (1 GB)  | **$5**  |
| Heroku Redis Mini                   | **$3**  |
| Heroku Postgres Essential-1 (10 GB) | $10     |
| Standard-1X dyno (if you outgrow Basic) | $25  |
| Standard-0 Postgres (64 GB)         | $50     |

Recommended starter: Basic + Essential-0 + Redis Mini = **$15/month**.
Step up to Essential-1 the moment you intend to load the full Mazal dump.

---

## 8. Notes on the production hardening already baked in

* **CORS**: `allow_origins=[FRONTEND_ORIGIN]` — set this to your real
  URL before pushing live.
* **Cookie**: `Secure`, `HttpOnly`, `SameSite=Lax`, max-age =
  `SESSION_TTL_HOURS`. Sliding expiry refreshes on every authenticated
  request.
* **Encryption**: PII at rest under `MASTER_KEY` (AES-GCM), email lookup
  via HMAC blind index, user API keys envelope-wrapped with a
  password-derived KEK (zero-knowledge).
* **WebSockets**: AuthN/AuthZ happens *before* `accept()` — the upgrade
  is rejected with 1008 if the cookie is missing or the user isn't a
  project member.
* **Release-phase migrations**: `scripts/release.sh` runs Alembic on
  every deploy so the schema and the code never disagree.
