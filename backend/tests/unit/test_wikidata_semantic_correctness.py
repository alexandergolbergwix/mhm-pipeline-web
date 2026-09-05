"""Semantic publication rules at the shared Wikidata builder seam."""

from __future__ import annotations

import pytest

from app.models.run_job import JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline import wikidata_upload, wikidata_upload_job
from app.pipeline.wikidata_export_quality_gate import (
    FindingSeverity,
    IdentityGroup,
    assert_wikidata_export_quality,
    strong_external_id_collision_findings,
    wikidata_export_quality_findings,
)
from app.pipeline.wikidata_upload import prepare_items_for_upload
from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def test_structured_content_responsibility_becomes_work_author_claim() -> None:
    record = {
        "_control_number": "record-alpha",
        "title": "כתב יד לדוגמה",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": "ספר הדוגמה",
            "responsibility": "מחבר לדוגמה",
            "source_field": "505",
            "candidate_kind": "named_work",
            "source_text": "ספר הדוגמה / מחבר לדוגמה",
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert [
        (statement.property_id, statement.value)
        for statement in work.statements
        if statement.property_id in {"P50", "P2093"}
    ] == [("P2093", "מחבר לדוגמה")]
    assert work.work_candidate_evidence[0]["author_name"] == "מחבר לדוגמה"
    assert work.work_candidate_evidence[0]["author_source"] == "responsibility"


def test_nested_structured_responsibility_becomes_work_author_claim() -> None:
    record = {
        "_control_number": "record-nested",
        "title": "כתב יד עם אחריות מובנית",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": {"value": "חיבור מובנה"},
            "responsibility": {
                "value": {"name": "מחברת מזוהה", "role": "author"},
            },
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert work.labels["he"] == "חיבור מובנה"
    assert [
        (statement.property_id, statement.value)
        for statement in work.statements
        if statement.property_id in {"P50", "P2093"}
    ] == [("P2093", "מחברת מזוהה")]


@pytest.mark.parametrize(
    ("content_text", "expected_title", "expected_author"),
    [
        ("חיבור בלוכסן / מחבר לדוגמה", "חיבור בלוכסן", "מחבר לדוגמה"),
        ("חיבור באנגלית — by Example Author", "חיבור באנגלית", "Example Author"),
        ("חיבור בעברית — מאת מחבר לדוגמה", "חיבור בעברית", "מחבר לדוגמה"),
    ],
)
def test_explicit_content_separators_keep_title_and_author_together(
    content_text: str,
    expected_title: str,
    expected_author: str,
) -> None:
    record = {
        "_control_number": f"record-{expected_title}",
        "title": "כתב יד עם אחריות",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": content_text,
            "source_field": "505",
            "candidate_kind": "named_work",
            "source_text": content_text,
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert work.labels["he"] == expected_title
    assert [
        statement.value
        for statement in work.statements
        if statement.property_id == "P2093"
    ] == [expected_author]


def test_hebrew_lamed_title_suffix_does_not_imply_an_author() -> None:
    title = "סידור ליצחק בן אברהם"
    record = {
        "_control_number": "record-lamed-title",
        "title": "כתב יד עם כותרת",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": title,
            "source_field": "505",
            "candidate_kind": "named_work",
            "source_text": title,
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert work.labels["he"] == title
    assert not any(
        statement.property_id in {"P50", "P2093"}
        for statement in work.statements
    )


def test_bare_dash_title_suffix_does_not_imply_an_author() -> None:
    title = "חיבור לדוגמה — שם נוסף"
    record = {
        "_control_number": "record-bare-dash-title",
        "title": "כתב יד עם כותרת",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": title,
            "source_field": "505",
            "candidate_kind": "named_work",
            "source_text": title,
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert work.labels["he"] == title
    assert not any(
        statement.property_id in {"P50", "P2093"}
        for statement in work.statements
    )


def test_contents_ner_lamed_suffix_does_not_imply_an_author() -> None:
    title = "סידור ליצחק בן אברהם"
    record = {
        "_control_number": "record-ner-lamed-title",
        "title": "כתב יד עם כותרת",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [],
        "entities": [{
            "source": "contents_ner",
            "type": "WORK",
            "text": title,
            "start": 0,
            "end": len(title),
            "approved": True,
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert work.labels["he"] == title
    assert not any(
        statement.property_id in {"P50", "P2093"}
        for statement in work.statements
    )


def test_record_author_does_not_leak_across_multiple_content_entries() -> None:
    record = {
        "_control_number": "record-many-works",
        "title": "כתב יד עם חיבורים",
        "authors": [{"name": "עורך כתב היד", "role": "author"}],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [
            {
                "title": "חיבור ראשון",
                "responsibility": "מחבר ראשון",
                "source_field": "505",
                "candidate_kind": "named_work",
            },
            {
                "title": "חיבור שני",
                "source_field": "505",
                "candidate_kind": "named_work",
            },
        ],
    }

    items = WikidataItemBuilder().build_all([record])

    works = {
        item.labels["he"]: item
        for item in items
        if item.entity_type == "work"
    }
    assert [
        statement.value
        for statement in works["חיבור ראשון"].statements
        if statement.property_id == "P2093"
    ] == ["מחבר ראשון"]
    assert not any(
        statement.property_id in {"P50", "P2093"}
        for statement in works["חיבור שני"].statements
    )


def test_exact_identifier_backed_person_replaces_author_name_string() -> None:
    author_name = "מחבר מזוהה"
    record = {
        "_control_number": "record-beta",
        "title": "כתב יד נוסף",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "language": "heb",
        "contents": [{
            "title": "חיבור מזוהה",
            "responsibility": author_name,
            "source_field": "505",
            "candidate_kind": "named_work",
            "source_text": f"חיבור מזוהה / {author_name}",
        }],
        "marc_authority_matches": [{
            "name": author_name,
            "role": "scribe",
            "mazal_id": "987000000000000125",
            "wikidata_qid": "Q123",
            "approved": True,
        }],
    }

    items = WikidataItemBuilder().build_all([record])

    work = next(item for item in items if item.entity_type == "work")
    assert [
        (statement.property_id, statement.value)
        for statement in work.statements
        if statement.property_id in {"P50", "P2093"}
    ] == [("P50", "Q123")]


def test_strong_external_id_collision_is_a_typed_hard_finding() -> None:
    shared_external_id = "987000000000000123"
    people = [
        WikidataItem(
            local_id=local_id,
            entity_type="person",
            labels={"he": label},
            statements=[WikidataStatement(
                property_id="P8189",
                value=shared_external_id,
                value_type="external-id",
            )],
        )
        for local_id, label in (
            ("person:alpha", "מחבר ראשון"),
            ("person:beta", "מחבר שני"),
        )
    ]

    findings = wikidata_export_quality_findings(people)

    collision = next(
        finding
        for finding in findings
        if finding.code == "STRONG_EXTERNAL_ID_COLLISION"
    )
    assert collision.severity is FindingSeverity.HARD
    assert collision.identity_group == IdentityGroup(
        property_id="P8189",
        external_id=shared_external_id,
        entity_ids=("person:alpha", "person:beta"),
    )


def test_collision_rule_accepts_iterables_and_a_profile_property_set() -> None:
    entities = (
        WikidataItem(
            local_id=f"entity:{index}",
            entity_type="custom",
            labels={"en": f"Entity {index}"},
            statements=[WikidataStatement(
                property_id="P999",
                value="shared-profile-id",
                value_type="external-id",
            )],
        )
        for index in range(2)
    )
    properties = (property_id for property_id in ["P999"])

    findings = strong_external_id_collision_findings(
        entities,
        property_ids=properties,
    )

    assert [finding.code for finding in findings] == [
        "STRONG_EXTERNAL_ID_COLLISION",
    ]


def test_entity_quality_error_is_a_typed_hard_finding() -> None:
    item = WikidataItem(
        local_id="work:without-label",
        entity_type="work",
        labels={},
    )

    findings = wikidata_export_quality_findings([item])

    missing_label = next(
        finding for finding in findings if finding.code == "MISSING_LABEL"
    )
    assert missing_label.severity is FindingSeverity.HARD
    assert missing_label.entity_ids == ("work:without-label",)


def test_quality_gate_reads_author_from_full_content_entry() -> None:
    work = WikidataItem(
        local_id="work:structured-author",
        entity_type="work",
        labels={"he": "חיבור מיוחס"},
        statements=[
            WikidataStatement("P31", "Q17537576", "item"),
            WikidataStatement("P1476", "חיבור מיוחס", "monolingualtext"),
        ],
        records=["record-quality"],
        work_candidate_evidence=[{"accepted": True, "title": "חיבור מיוחס"}],
    )
    marc_records = [{
        "_control_number": "record-quality",
        "title": "כתב יד לבדיקת איכות",
        "authors": [],
        "contents": [{
            "title": "חיבור מיוחס",
            "responsibility": {"name": "מחברת מתועדת", "role": "author"},
        }],
    }]

    with pytest.raises(ValueError, match="WORK_MISSING_AUTHOR_CLAIM"):
        assert_wikidata_export_quality([work], marc_records=marc_records)


def test_strong_external_id_collision_blocks_the_quality_gate() -> None:
    people = [
        WikidataItem(
            local_id=local_id,
            entity_type="person",
            labels={"he": label},
            statements=[WikidataStatement(
                property_id="P8189",
                value="987000000000000124",
                value_type="external-id",
            )],
        )
        for local_id, label in (
            ("person:gamma", "מחבר שלישי"),
            ("person:delta", "מחבר רביעי"),
        )
    ]

    with pytest.raises(ValueError, match="STRONG_EXTERNAL_ID_COLLISION"):
        assert_wikidata_export_quality(people)


def test_upload_preflight_blocks_corpus_finding_before_reconciliation() -> None:
    people = [
        WikidataItem(
            local_id=local_id,
            entity_type="person",
            labels={"he": label},
            statements=[WikidataStatement(
                property_id="P8189",
                value="987000000000000126",
                value_type="external-id",
            )],
        )
        for local_id, label in (
            ("person:epsilon", "מחבר חמישי"),
            ("person:zeta", "מחבר שישי"),
        )
    ]

    class UnexpectedReconciler:
        def reconcile_person_by_identifiers(self, *args: object) -> None:
            raise AssertionError("the quality gate must run before reconciliation")

    prepared = prepare_items_for_upload(people, UnexpectedReconciler())

    assert len(prepared) == 2
    assert all(item.blocked for item in prepared)
    assert all(
        "STRONG_EXTERNAL_ID_COLLISION" in item.block_message
        for item in prepared
    )


def test_upload_preflight_applies_source_aware_quality_findings() -> None:
    work = WikidataItem(
        local_id="work:missing-projected-author",
        entity_type="work",
        labels={"he": "חיבור עם אחריות"},
        statements=[
            WikidataStatement("P31", "Q17537576", "item"),
            WikidataStatement("P1476", "חיבור עם אחריות", "monolingualtext"),
        ],
        records=["record-preflight"],
        work_candidate_evidence=[{
            "accepted": True,
            "title": "חיבור עם אחריות",
        }],
    )
    marc_records = [{
        "_control_number": "record-preflight",
        "title": "כתב יד לבדיקת הכנה",
        "authors": [],
        "contents": [{
            "title": "חיבור עם אחריות",
            "responsibility": "מחברת מתועדת",
        }],
    }]

    class NoMatchReconciler:
        def reconcile_work_by_label_and_author(self, *args: object, **kwargs: object) -> None:
            return None

    prepared = prepare_items_for_upload(
        [work],
        NoMatchReconciler(),
        marc_records=marc_records,
    )

    assert prepared[0].blocked is True
    assert "WORK_MISSING_AUTHOR_CLAIM" in prepared[0].block_message


def test_upload_preflight_uses_persisted_work_author_evidence() -> None:
    work = WikidataItem(
        local_id="work:persisted-author-evidence",
        entity_type="work",
        labels={"he": "חיבור עם ראיה"},
        statements=[
            WikidataStatement("P31", "Q17537576", "item"),
            WikidataStatement("P1476", "חיבור עם ראיה", "monolingualtext"),
        ],
        records=["record-persisted-evidence"],
        work_candidate_evidence=[{
            "accepted": True,
            "title": "חיבור עם ראיה",
            "author_name": "מחבר מתועד",
            "author_source": "responsibility",
        }],
    )

    class NoMatchReconciler:
        def reconcile_work_by_label_and_author(self, *args: object, **kwargs: object) -> None:
            return None

    prepared = prepare_items_for_upload([work], NoMatchReconciler())

    assert prepared[0].blocked is True
    assert "WORK_MISSING_AUTHOR_CLAIM" in prepared[0].block_message


def test_upload_preflight_checks_local_references_against_the_full_corpus() -> None:
    person = WikidataItem(
        local_id="person:local-author",
        entity_type="person",
        labels={"he": "מחבר מקומי"},
        existing_qid="Q123",
        statements=[WikidataStatement(
            property_id="P8189",
            value="987000000000000127",
            value_type="external-id",
        )],
    )
    work = WikidataItem(
        local_id="work:local-author-link",
        entity_type="work",
        labels={"he": "חיבור עם קישור מקומי"},
        existing_qid="Q456",
        statements=[
            WikidataStatement("P31", "Q17537576", "item"),
            WikidataStatement("P1476", "חיבור עם קישור מקומי", "monolingualtext"),
            WikidataStatement("P50", "__LOCAL:person:local-author", "item"),
        ],
    )

    prepared = prepare_items_for_upload([work, person], object())

    assert not any(row.blocked for row in prepared)


@pytest.mark.asyncio
async def test_upload_job_blocks_only_members_of_a_corpus_identity_group(
    sample_run: dict[str, object],
    db_session: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_external_id = "987000000000000128"
    colliding_people = [
        WikidataItem(
            local_id=local_id,
            entity_type="person",
            labels={"he": label},
            statements=[WikidataStatement(
                property_id="P8189",
                value=shared_external_id,
                value_type="external-id",
            )],
        )
        for local_id, label in (
            ("person:job-alpha", "מחבר ראשון"),
            ("person:job-beta", "מחבר שני"),
        )
    ]
    eligible = WikidataItem(
        local_id="other:job-eligible",
        entity_type="other",
        labels={"en": "Eligible entity"},
    )
    native = [*colliding_people, eligible]
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        params={"dry_run": True, "source": "canonical"},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    async def build_native_items(*args: object, **kwargs: object) -> list[WikidataItem]:
        return native

    class NoMatchReconciler:
        def reconcile_person_by_identifiers(
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        "app.routers.wikidata_studio._build_native_items",
        build_native_items,
    )
    monkeypatch.setattr(
        wikidata_upload,
        "_make_reconciler",
        NoMatchReconciler,
    )

    await wikidata_upload_job.run_wikidata_upload_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_SUCCEEDED
    outcomes = {
        row["local_id"]: row
        for row in job.result["outcomes"]
    }
    assert outcomes["person:job-alpha"]["status"] == "blocked"
    assert outcomes["person:job-beta"]["status"] == "blocked"
    assert "STRONG_EXTERNAL_ID_COLLISION" in outcomes["person:job-alpha"]["message"]
    assert outcomes["other:job-eligible"]["status"] == "success"


@pytest.mark.asyncio
async def test_upload_job_stops_for_an_unmapped_corpus_finding(
    sample_run: dict[str, object],
    db_session: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dangling = WikidataItem(
        local_id="other:job-dangling",
        entity_type="other",
        labels={"en": "Entity with a dangling link"},
        statements=[WikidataStatement(
            property_id="P999",
            value="__LOCAL:missing-target",
            value_type="item",
        )],
    )
    eligible = WikidataItem(
        local_id="other:job-never-written",
        entity_type="other",
        labels={"en": "Otherwise eligible entity"},
    )
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        params={"dry_run": True, "source": "canonical"},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    async def build_native_items(*args: object, **kwargs: object) -> list[WikidataItem]:
        return [dangling, eligible]

    monkeypatch.setattr(
        "app.routers.wikidata_studio._build_native_items",
        build_native_items,
    )

    await wikidata_upload_job.run_wikidata_upload_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_FAILED
    assert "DANGLING_LOCAL_REFERENCE" in job.error
    assert job.result["outcomes"] == []
    assert job.result["quality_finding_count"] == 1


@pytest.mark.asyncio
async def test_upload_job_preserves_identifierless_person_skip_and_author_fallback(
    sample_run: dict[str, object],
    db_session: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = WikidataItem(
        local_id="person:job-unpublishable",
        entity_type="person",
        labels={"he": "מחבר ללא מזהה"},
    )
    source = WikidataItem(
        local_id="other:job-source",
        entity_type="other",
        labels={"en": "Source entity"},
        statements=[WikidataStatement(
            property_id="P50",
            value="__LOCAL:person:job-unpublishable",
            value_type="item",
        )],
    )
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        params={"dry_run": True, "source": "canonical"},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    async def build_native_items(*args: object, **kwargs: object) -> list[WikidataItem]:
        return [source, person]

    monkeypatch.setattr(
        "app.routers.wikidata_studio._build_native_items",
        build_native_items,
    )

    await wikidata_upload_job.run_wikidata_upload_job(job.id)

    await db_session.refresh(job)
    outcomes = {
        row["local_id"]: row
        for row in job.result["outcomes"]
    }
    assert outcomes["person:job-unpublishable"]["status"] == "skipped"
    assert outcomes["other:job-source"]["status"] == "success"
    assert job.result["links_unresolved"] == 0
    assert job.result["link_outcomes"][0]["property_id"] == "P2093"
