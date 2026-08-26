"""Deferred __LOCAL: connections (Rule W-192)."""

from __future__ import annotations

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import (
    partition_unresolved_local,
    resolve_statement_locals,
)
from app.pipeline.wikidata_upload import remember_created_qid
from app.pipeline.wikidata_upload_job import (
    STEP_ADD_LINKS,
    STEP_WRITE_ITEMS,
    build_upload_progress,
    estimate_remaining_seconds,
)


def _work_with_author() -> WikidataItem:
    return WikidataItem(
        local_id="QDraft_Work",
        entity_type="work",
        labels={"he": "אב הרחמים"},
        statements=[
            WikidataStatement(
                property_id="P31",
                value="Q47461344",
                value_type="wikibase-item",
            ),
            WikidataStatement(
                property_id="P1476",
                value="אב הרחמים",
                value_type="monolingualtext",
            ),
            WikidataStatement(
                property_id="P50",
                value="__LOCAL:person:x",
                value_type="item",
            ),
        ],
    )


def test_partition_defers_unresolved_author_keeps_p31() -> None:
    write, deferred = partition_unresolved_local(_work_with_author(), {})
    assert [s.property_id for s in write.statements] == ["P31", "P1476"]
    assert [s.property_id for s in deferred] == ["P50"]
    assert deferred[0].value == "__LOCAL:person:x"


def test_partition_keeps_author_once_person_qid_exists() -> None:
    write, deferred = partition_unresolved_local(
        _work_with_author(), {"person:x": "Q99"},
    )
    assert deferred == []
    assert [s.property_id for s in write.statements] == ["P31", "P1476", "P50"]


def test_partition_defers_qualifier_local() -> None:
    item = WikidataItem(
        local_id="ms1",
        entity_type="manuscript",
        statements=[
            WikidataStatement(
                property_id="P127",
                value="Q123",
                value_type="item",
                qualifiers=[{"property": "P585", "value": "__LOCAL:person:x", "type": "item"}],
            ),
        ],
    )
    write, deferred = partition_unresolved_local(item, {})
    assert write.statements == []
    assert deferred[0].property_id == "P127"


def test_partition_does_not_mutate_original() -> None:
    original = _work_with_author()
    partition_unresolved_local(original, {})
    assert [s.property_id for s in original.statements] == ["P31", "P1476", "P50"]
    assert original.statements[2].value == "__LOCAL:person:x"


def test_resolve_statement_locals_rewrites_when_qid_present() -> None:
    stmt = WikidataStatement(
        property_id="P50", value="__LOCAL:person:x", value_type="item",
    )
    rewritten, leftover = resolve_statement_locals(stmt, {"person:x": "Q77"})
    assert leftover == []
    assert rewritten.value == "Q77"
    assert stmt.value == "__LOCAL:person:x"


def test_remember_created_qid_dry_run_placeholder() -> None:
    session: dict[str, str] = {}
    remember_created_qid(session, "QDraft_Work", None, "would_create", dry_run=True)
    assert session["QDraft_Work"] == "dry:QDraft_Work"
    remember_created_qid(session, "person:x", "Q12", "created", dry_run=False)
    assert session["person:x"] == "Q12"
    remember_created_qid(
        session, "QDraft_Skipped_Work", "Q2873224", "skipped",
        entity_type="work",
    )
    assert session["QDraft_Skipped_Work"] == "Q2873224"


def test_estimate_remaining_seconds_hidden_until_three_samples() -> None:
    assert estimate_remaining_seconds(0, 10, 5.0) is None
    assert estimate_remaining_seconds(2, 10, 4.0) is None
    eta = estimate_remaining_seconds(3, 10, 6.0)
    assert eta == 14


def test_build_upload_progress_never_zero_of_two_steps() -> None:
    p = build_upload_progress(
        phase="uploading",
        message="Step 1 of 2: writing items",
        upload_target="test",
        step=STEP_WRITE_ITEMS,
        item_done=0,
        item_total=10,
        link_done=0,
        link_total=4,
        current_label="work · אב הרחמים",
        eta_seconds=None,
        elapsed_seconds=1,
    )
    assert p["processed"] == 1
    assert p["total"] == 2
    assert p["unit"] == "steps"
    assert p["sub_unit"] == "items"
    assert p["steps"][0]["status"] == "running"
    assert p["steps"][1]["status"] == "pending"


def test_build_upload_progress_step_two_marks_step_one_done() -> None:
    p = build_upload_progress(
        phase="uploading",
        message="Step 2 of 2: adding connections",
        upload_target="test",
        step=STEP_ADD_LINKS,
        item_done=10,
        item_total=10,
        link_done=1,
        link_total=4,
        current_label="links · אב הרחמים",
        eta_seconds=30,
        elapsed_seconds=12,
    )
    assert p["processed"] == 2
    assert p["sub_unit"] == "links"
    assert p["steps"][0]["status"] == "done"
    assert p["steps"][1]["status"] == "running"
    assert p["steps"][1]["eta_seconds"] == 30
