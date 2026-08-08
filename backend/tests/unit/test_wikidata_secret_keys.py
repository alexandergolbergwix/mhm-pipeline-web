"""Target-scoped Wikidata Settings secrets (live vs test.wikidata.org)."""

from __future__ import annotations

from app.pipeline.wikidata_upload import (
    UPLOAD_TARGET_DRY_RUN,
    UPLOAD_TARGET_LIVE,
    UPLOAD_TARGET_TEST,
    WIKIDATA_SECRET_LIVE,
    WIKIDATA_SECRET_TEST,
    wikidata_secret_key_for_target,
)
from app.routers.api_keys import _KEY_ORDER, _VALID_KEYS


def test_secret_key_for_test_target() -> None:
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_TEST) == WIKIDATA_SECRET_TEST


def test_secret_key_for_live_and_dry_run() -> None:
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_LIVE) == WIKIDATA_SECRET_LIVE
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_DRY_RUN) == WIKIDATA_SECRET_LIVE
    assert wikidata_secret_key_for_target(None) == WIKIDATA_SECRET_LIVE


def test_api_keys_allowlist_includes_wikidata_test() -> None:
    assert "wikidata_test" in _VALID_KEYS
    assert _KEY_ORDER.index("wikidata") < _KEY_ORDER.index("wikidata_test")
