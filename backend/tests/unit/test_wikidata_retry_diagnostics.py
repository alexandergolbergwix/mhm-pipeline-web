from datetime import UTC, datetime

import pytest

from app.pipeline.wikidata_retry_diagnostics import ApiRetryDiagnostics


def test_safe_api_fields_round_trip_without_private_response_data():
    diagnostics = ApiRetryDiagnostics.from_error({
        "code": "maxlag", "host": "wdqs1014", "type": "wikibase-queryservice",
        "lag": 15.0666666667, "queryserviceLag": 904,
        "info": "private-response", "headers": {"Authorization": "private-value"},
    }, retry_after_seconds=60, observed_at=datetime(2026, 11, 17, tzinfo=UTC))
    detail = diagnostics.to_detail("The API rejected the repair (maxlag).")
    assert "private-" not in detail and "Authorization" not in detail
    assert ApiRetryDiagnostics.from_detail(detail) == diagnostics


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "904", {}, 10 ** 1000])
def test_invalid_remote_numbers_do_not_enter_durable_json(value):
    diagnostics = ApiRetryDiagnostics.from_error({
        "code": "maxlag", "lag": value, "queryserviceLag": value,
        "host": "https://invalid/private?token=x", "type": "unsafe\\nfield",
    }, retry_after_seconds=value)
    assert diagnostics.lag_seconds is None
    assert diagnostics.queryservice_lag_seconds is None
    assert diagnostics.retry_after_seconds is None
    assert diagnostics.host is None and diagnostics.lag_type is None
    assert ApiRetryDiagnostics.from_detail(diagnostics.to_detail("maxlag")) == diagnostics


@pytest.mark.parametrize("value", [None, "old maxlag text", "{", "[]", '"string"',
    '{"api_retry_version":1,"api_retry":{}}',
    '{"api_retry_version":1,"api_retry":{"code":"maxlag","observed_at":"2026-11-17"}}',
    '{"api_retry_version":2,"api_retry":{}}', "[" * 9000])
def test_old_or_invalid_detail_cannot_invent_retry_evidence(value):
    assert ApiRetryDiagnostics.from_detail(value) is None


def test_non_retryable_error_does_not_become_retryable_diagnostics():
    assert ApiRetryDiagnostics.from_error({"code": "editconflict"}) is None
