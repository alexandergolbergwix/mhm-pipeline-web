"""Pytest fixtures — ensure crypto keys are present before app import."""

from __future__ import annotations

import os
import secrets


def _seed_test_env() -> None:
    """Provide fresh test keys if the env doesn't already supply them."""
    os.environ.setdefault("MASTER_KEY", secrets.token_urlsafe(32))
    os.environ.setdefault("EMAIL_HMAC_KEY", secrets.token_urlsafe(32))
    os.environ.setdefault("ENV", "test")


_seed_test_env()
