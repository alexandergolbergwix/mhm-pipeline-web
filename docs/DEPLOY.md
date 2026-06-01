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

---

## 7. Cost reference (typical, as of 2026)

| Item                                | $/month |
|-------------------------------------|--------:|
| Heroku Basic dyno                   | **$7**  |
| Heroku Postgres Essential-0 (1 GB)  | **$5**  |
| Heroku Postgres Essential-1 (10 GB) | $10     |
| Standard-1X dyno (if you outgrow Basic) | $25  |
| Standard-0 Postgres (64 GB)         | $50     |

Recommended starter: Basic + Essential-0 = **$12/month**. Step up to
Essential-1 the moment you intend to load the full Mazal dump.

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
