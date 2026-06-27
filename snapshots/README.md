# Run snapshots (local only — not committed)

Pre-mutation backups of production curator state. Files here are created
manually before risky operations (re-enrich, bulk import, etc.).

## Authority re-enrich

**Save** (before re-enrich):

```bash
export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web | tr -d '\n')"
cd backend && .venv/bin/python -m scripts.snapshot_authority_run \
  --run-id <RUN_UUID> \
  --output ../snapshots/authority-<RUN_UUID>-pre-reenrich-$(date +%Y%m%d).json \
  --note "before re-enrich"
```

**Restore** (if re-enrich went wrong):

```bash
export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web | tr -d '\n')"
cd backend && .venv/bin/python -m scripts.restore_authority_run \
  --snapshot ../snapshots/authority-<RUN_UUID>-pre-reenrich-YYYYMMDD.json \
  --apply
```

Dry-run first (omit `--apply`) to see how many rows would be updated.

Restore updates rows that existed in the snapshot by `id`. Rows added
during re-enrich are left untouched.
