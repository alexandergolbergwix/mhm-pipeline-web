"""Postgres authority backend typed Mazal matchers (work / corporate / subject)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_postgres_match_corporate_returns_row() -> None:
    from app.pipeline.authority_backend import PostgresAuthorityBackend

    backend = PostgresAuthorityBackend("postgresql://fake")
    mock_row = ("NLI-CORP", "corporate", "עברית", "Latin", None, "ALEPH1", "110")
    cur = MagicMock()
    cur.fetchone.return_value = mock_row
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch.object(backend, "_get_conn", return_value=conn), patch.object(
        backend, "_normalize_mazal", return_value="corp norm",
    ):
        result = await backend.match_corporate("Test Corp")

    assert result is not None
    assert result["mazal_id"] == "NLI-CORP"
    assert result["entity_type"] == "corporate"
    assert result["main_marc_tag"] == "110"
    sql = cur.execute.call_args[0][0]
    assert "mazal_name_index" in sql
    assert cur.execute.call_args[0][1] == ("corp norm", "corporate")


@pytest.mark.asyncio
async def test_postgres_match_subject_prefers_150_tag_order() -> None:
    from app.pipeline.authority_backend import PostgresAuthorityBackend

    backend = PostgresAuthorityBackend("postgresql://fake")
    mock_row = ("NLI-SUBJ", "subject", "נושא", "Subject", None, None, "150")
    cur = MagicMock()
    cur.fetchone.return_value = mock_row
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch.object(backend, "_get_conn", return_value=conn), patch.object(
        backend, "_normalize_mazal", return_value="topic norm",
    ):
        result = await backend.match_subject("Bible")

    assert result is not None
    assert result["mazal_id"] == "NLI-SUBJ"
    sql = cur.execute.call_args[0][0]
    assert "main_marc_tag" in sql
    assert "150" in sql
    assert cur.execute.call_args[0][1] == ("topic norm", "subject")


@pytest.mark.asyncio
async def test_postgres_match_work_returns_none_when_no_row() -> None:
    from app.pipeline.authority_backend import PostgresAuthorityBackend

    backend = PostgresAuthorityBackend("postgresql://fake")
    cur = MagicMock()
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch.object(backend, "_get_conn", return_value=conn), patch.object(
        backend, "_normalize_mazal", return_value="work norm",
    ):
        result = await backend.match_work("Unknown work")

    assert result is None
