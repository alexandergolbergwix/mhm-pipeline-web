"""Heroku Scheduler / one-shot entry-point scripts.

Each module is runnable as ``python -m scripts.<name>`` from inside
the ``backend/`` directory:

- scripts.run_purge                  — daily access-request TTL purge (Rule W-20)
- scripts.run_snapshot               — 3x/day entity_snapshot writes (Rule W-21)
- scripts.run_prune_events           — daily 1000-event-cap prune (Rule W-21)
- scripts.run_prune_inference_cache  — daily expired inference_cache prune (Rule W-12)
- scripts.backfill_versioning        — one-shot OP_CREATE event backfill (Rule W-21)
- scripts.create_user                — bootstrap the first admin (DEPLOY.md §5)

This file MUST exist for the ``-m`` import path to work — without it,
Python refuses to treat ``backend/scripts/`` as a package and emits
``No module named scripts.<name>``.
"""
