"""Web-side pipeline package.

Thin orchestration layer that drives the *real* desktop pipeline modules
copied verbatim into ``backend/converter/``. Nothing in here re-implements
desktop logic — every function delegates to a sibling ``converter.*``
module via :func:`fastapi.concurrency.run_in_threadpool` so the sync
desktop code runs without blocking the async event loop.
"""
