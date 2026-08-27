"""Regression test for the Wikidata Studio creating zero work items.

**The bug** (Heroku app, 2026-06-02): the studio's items summary
reported ``works: 0`` even though the run had contents_ner WORK
entities in ``extraction_approvals``.

**The root cause**: ``app.pipeline.wikidata_studio.build_items_for_run``
attached ``marc_authority_matches`` to each record but never the
``entities`` list. The desktop ``WikidataItemBuilder`` creates work
items from ``record["entities"]`` filtered to
``source == "contents_ner"`` (item_builder.py:_add_works_and_authorities)
— without that channel populated, the work-creation path is dead.

**The fix**: ``build_items_for_run`` accepts ``entities_by_cn`` and
merges into ``record["entities"]``; the studio router loads them
from ``ExtractionApproval``.

This test pins the contract: pass contents_ner WORK entities through
``entities_by_cn`` → expect ``summary.works > 0``.
"""

from __future__ import annotations

import pytest

from app.pipeline import wikidata_studio


def _fake_marc_record(control_number: str = "1") -> dict:
    """Minimal MARC record shape the studio glue accepts."""
    return {
        "_control_number": control_number,
        "title":           "Some Hebrew Manuscript",
        "authors":         [],
        "contributors":    [],
        "subjects":        [],
        "dates":           {"year": 1500},
        "language":        "heb",
    }


def _fake_work_entity(text: str, control_number: str = "1") -> dict:
    """Shape matching what ``_load_entities_by_cn`` emits."""
    return {
        "text":             text,
        "type":             "WORK",
        "role":             "",
        "source":           "contents_ner",
        "start":            0,
        "end":              len(text),
        "confidence":       0.92,
        "model_confidence": 0.92,
        "approved":         True,
    }


class TestEntitiesByCnDrivesWorkCreation:
    """The contract Rule 47 + desktop's ``_add_works_and_authorities``
    rely on: NER WORK entities flowing through
    ``record["entities"]`` produce work items."""

    @pytest.mark.asyncio
    async def test_no_entities_zero_works(self) -> None:
        """Baseline — without entities the bug reproduces exactly."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record()],
            approved_matches=[],
            entities_by_cn=None,
            return_native=False,
        )
        assert result["summary"]["works"] == 0
        assert result["summary"]["manuscripts"] >= 1

    @pytest.mark.asyncio
    async def test_contents_ner_work_entities_create_work_items(self) -> None:
        """Fix invariant — contents_ner WORK entities on a record
        create work items."""
        ents = {
            "1": [
                _fake_work_entity("פירוש המשנה"),
                _fake_work_entity("ספר המצוות"),
            ],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record(control_number="1")],
            approved_matches=[],
            entities_by_cn=ents,
            return_native=False,
        )
        # Two distinct WORK titles → at least two work items. The
        # desktop builder dedupes within a record, so >= 2 is the
        # tight lower bound. Some builds may also emit a stub work
        # for a structured ``record["contents"]`` entry — we just
        # require the contents_ner path to fire.
        assert result["summary"]["works"] >= 2, (
            f"contents_ner WORK entities did not produce work items: "
            f"summary={result['summary']}"
        )

    @pytest.mark.asyncio
    async def test_empty_entities_by_cn_does_not_crash(self) -> None:
        """An empty dict is valid input (no entities for any record)."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record()],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        assert result["summary"]["manuscripts"] >= 1

    @pytest.mark.asyncio
    async def test_en_label_is_not_hebrew(self) -> None:
        """Regression for 2026-06-04 bug: Hebrew title must NOT appear in the
        ``en`` label slot. Both `en` and `he` were identical Hebrew strings."""
        import re

        hebrew_re = re.compile(r"[\u0590-\u05ff]")
        rec = _fake_marc_record(control_number="1")
        rec["title"] = "פנקס חשבונות של סוחר היושב במנטובה"
        rec["shelfmark"] = ""  # no shelfmark so the fallback logic is exercised

        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        for item in result["items"]:
            if item.get("entity_type") != "manuscript":
                continue
            en_label = (item.get("labels") or {}).get("en") or ""
            assert not (hebrew_re.search(en_label) and not re.search(r"[A-Za-z]", en_label)), (
                f"en label contains Hebrew-only text: {en_label!r}. "
                "Hebrew text must go in the `he` slot only."
            )

    @pytest.mark.asyncio
    async def test_entities_only_for_unmatched_cn_ignored(self) -> None:
        """An entity bucket for a control_number we never built must
        not crash the pipeline (silent skip)."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record(control_number="1")],
            approved_matches=[],
            entities_by_cn={"OTHER_CN": [_fake_work_entity("orphan")]},
            return_native=False,
        )
        assert result["summary"]["works"] == 0

    @pytest.mark.asyncio
    async def test_known_content_qid_does_not_create_authorless_related_work(self) -> None:
        title = "מנחת יהודה : פרוש על שמואל מלכים וישעיהו"
        record = {
            **_fake_marc_record(),
            "authors": [{"name": "חנין, יהודה בן יעקב"}],
            "contents": [{"title": title, "wikidata_qid": "Q141175558"}],
            "related_works": [{"title": title, "approved": True}],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[record], approved_matches=[], entities_by_cn=None, return_native=True,
        )
        works = [item for item in result["native_items"] if item.entity_type == "work"]
        manuscripts = [item for item in result["native_items"] if item.entity_type == "manuscript"]
        assert works == []
        assert any(
            statement.property_id == "P1574" and statement.value == "Q141175558"
            for statement in manuscripts[0].statements
        )

    @pytest.mark.asyncio
    async def test_approved_author_match_recovers_main_work_author(self) -> None:
        title = "מנחת יהודה : פרוש על שמואל, מלכים וישעיהו"
        record = {
            **_fake_marc_record(),
            "title": title,
            "authors": [],
            "contents": [{"title": title}],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[record],
            approved_matches=[{
                "control_number": "1",
                "entity_text": "חנין, יהודה בן יעקב",
                "role": "author",
                "approved": True,
            }],
            entities_by_cn=None,
            return_native=True,
        )
        works = [item for item in result["native_items"] if item.entity_type == "work"]
        assert len(works) == 1
        assert any(
            statement.property_id == "P2093"
            and statement.value == "חנין, יהודה בן יעקב"
            for statement in works[0].statements
        )

    @pytest.mark.asyncio
    async def test_same_title_with_different_authors_stays_separate(self) -> None:
        title = "אותו חיבור"
        records = [
            {**_fake_marc_record("1"), "contents": [{"title": title}],
             "authors": [{"name": "מחבר ראשון"}]},
            {**_fake_marc_record("2"), "contents": [{"title": title}],
             "authors": [{"name": "מחבר שני"}]},
        ]
        matches = [
            {
                "control_number": "1", "entity_text": "מחבר ראשון",
                "role": "author", "approved": True, "wikidata_qid": "Q101",
            },
            {
                "control_number": "2", "entity_text": "מחבר שני",
                "role": "author", "approved": True, "wikidata_qid": "Q102",
            },
        ]
        result = await wikidata_studio.build_items_for_run(
            marc_records=records, approved_matches=matches, entities_by_cn=None, return_native=True,
        )
        works = [item for item in result["native_items"] if item.entity_type == "work"]
        assert len(works) == 2
        assert {
            next(statement.value for statement in item.statements if statement.property_id == "P50")
            for item in works
        } == {"Q101", "Q102"}


class TestMarcDictEntriesDoNotCrashBuild:
    """Regression: dict-shaped related_places / related_works must not
    call ``.strip()`` on a dict during WikidataItemBuilder."""

    @pytest.mark.asyncio
    async def test_dict_related_places_with_kima_places(self) -> None:
        rec = {
            **_fake_marc_record(),
            "related_places": [{"place": "Prague", "hierarchy": ["Czech"]}],
            "kima_places": {"Prague": "https://www.wikidata.org/entity/Q1085"},
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn=None,
            return_native=False,
        )
        assert result["summary"]["manuscripts"] >= 1

    @pytest.mark.asyncio
    async def test_dict_related_works_title(self) -> None:
        rec = {
            **_fake_marc_record(),
            "related_works": [{"title": "עת שערי רצון", "relationship": "related"}],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn=None,
            return_native=False,
        )
        assert result["summary"]["manuscripts"] >= 1
        # Bare related_works without known QID / approval must not mint works.
        assert result["summary"]["works"] == 0
        manuscript = next(
            item for item in result["items"] if item["entity_type"] == "manuscript"
        )
        evidence = manuscript.get("work_candidate_evidence") or []
        assert any(
            row.get("accepted") is False and row.get("source_text") == "עת שערי רצון"
            for row in evidence
            if isinstance(row, dict)
        )

    @pytest.mark.asyncio
    async def test_empty_genre_dict_shells_do_not_crash_build(self) -> None:
        """Legacy rows store ``[{"name": "", "field": "655"}]`` — must not crash."""
        rec = {
            **_fake_marc_record(),
            "genres": [{"name": "", "field": "655"}],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn=None,
            return_native=False,
        )
        assert result["summary"]["manuscripts"] >= 1


class TestSourceRecordAssociation:
    @pytest.mark.asyncio
    async def test_every_built_item_retains_its_own_marc_records(self) -> None:
        first = _fake_marc_record(control_number="990000000000000001")
        second = _fake_marc_record(control_number="990000000000000002")
        result = await wikidata_studio.build_items_for_run(
            marc_records=[first, second],
            approved_matches=[
                {
                    "control_number": first["_control_number"],
                    "entity_text": "אברהם ראשון",
                    "role": "author",
                    "mazal_id": "987000000000000001",
                    "viaf_id": "",
                    "wikidata_qid": "",
                    "confidence": "high",
                    "source": "mazal",
                    "payload": {},
                },
                {
                    "control_number": second["_control_number"],
                    "entity_text": "אברהם שני",
                    "role": "author",
                    "mazal_id": "987000000000000002",
                    "viaf_id": "",
                    "wikidata_qid": "",
                    "confidence": "high",
                    "source": "mazal",
                    "payload": {},
                },
            ],
            entities_by_cn={
                first["_control_number"]: [_fake_work_entity("חיבור משותף")],
                second["_control_number"]: [_fake_work_entity("חיבור משותף")],
            },
            return_native=False,
        )

        items = result["items"]
        manuscripts = {
            item["local_id"]: item
            for item in items
            if item["entity_type"] == "manuscript"
        }
        assert manuscripts[first["_control_number"]]["records"] == [first["_control_number"]]
        assert manuscripts[second["_control_number"]]["records"] == [second["_control_number"]]

        persons = {
            item["labels"].get("he"): item
            for item in items
            if item["entity_type"] == "person"
        }
        assert persons["אברהם ראשון"]["records"] == [first["_control_number"]]
        assert persons["אברהם שני"]["records"] == [second["_control_number"]]

        works = [item for item in items if item["entity_type"] == "work"]
        assert len(works) == 2
        work_records = {
            tuple(item["records"]): {
                statement["value"]
                for statement in item["statements"]
                if statement["property_id"] in {"P50", "P2093"}
            }
            for item in works
        }
        assert work_records == {
            (first["_control_number"],): {"__LOCAL:mazal:987000000000000001"},
            (second["_control_number"],): {"__LOCAL:mazal:987000000000000002"},
        }


class TestProjectionQuality:
    @pytest.mark.asyncio
    async def test_catalog_id_stays_out_of_work_label_and_unknown_role_is_skipped(self) -> None:
        rec = _fake_marc_record(control_number="\"990000000000000001\"")
        rec["title"] = "פירוש המשנה"
        rec["marc_authority_matches"] = [{
            "name": "Unknown Contributor",
            "role": "unmapped role",
            "field": "700",
            "mazal_id": "",
        }]
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[{
                "control_number": "990000000000000001",
                "entity_text": "Unknown Contributor",
                "role": "unmapped role",
                "field": "700",
                "mazal_id": "",
            }],
            entities_by_cn=None,
            return_native=False,
        )
        manuscript = next(item for item in result["items"] if item["entity_type"] == "manuscript")
        assert manuscript["records"] == ["990000000000000001"]
        assert all("NLI 990000000000000001" not in str(item.get("labels")) for item in result["items"])
        assert all("Unknown Contributor" not in str(item.get("labels")) for item in result["items"])


class TestSourceAwareMarcWorks:
    @pytest.mark.asyncio
    async def test_clean_505_work_is_restored_with_evidence(self) -> None:
        rec = {
            **_fake_marc_record(),
            "contents": [{
                "title": "ספר הדרושים",
                "source_field": "505",
                "candidate_kind": "named_work",
                "folio_range": "א-ב",
            }],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        work = next(item for item in result["items"] if item["entity_type"] == "work")
        assert work["labels"]["he"] == "ספר הדרושים"
        assert work["work_candidate_evidence"][0]["reason"] == "named_work_in_505"
        manuscript = next(
            item for item in result["items"] if item["entity_type"] == "manuscript"
        )
        p1574 = next(
            stmt for stmt in manuscript["statements"]
            if stmt["property_id"] == "P1574"
        )
        assert any(
            qualifier["property"] == "P958" and qualifier["value"] == "א-ב"
            for qualifier in p1574["qualifiers"]
        )

    @pytest.mark.asyncio
    async def test_unverified_latin_505_heading_does_not_create_work(self) -> None:
        rec = {
            **_fake_marc_record(),
            "contents": [{
                "title": "Diodati Segre",
                "source_field": "505",
                "candidate_kind": "named_work",
            }],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        assert result["summary"]["works"] == 0
        manuscript = next(
            item for item in result["items"] if item["entity_type"] == "manuscript"
        )
        evidence = manuscript["work_candidate_evidence"]
        assert evidence[0]["reason"] == "latin_title_requires_authority"
        assert evidence[0]["accepted"] is False

    @pytest.mark.asyncio
    async def test_work_does_not_inherit_manuscript_language(self) -> None:
        rec = {
            **_fake_marc_record(),
            "languages": ["heb", "ara"],
            "contents": [{
                "title": "ספר הדרושים",
                "source_field": "505",
                "candidate_kind": "named_work",
            }],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        work = next(item for item in result["items"] if item["entity_type"] == "work")
        assert not any(stmt["property_id"] == "P407" for stmt in work["statements"])


@pytest.mark.asyncio
async def test_structured_505_author_suffix_is_not_part_of_work_label() -> None:
    rec = {
        **_fake_marc_record(),
        "contents": [{
            "title": "ספר היראה ליונה בן אברהם גרונדי",
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    assert work["labels"]["he"] == "ספר היראה"
    assert work["work_candidate_evidence"][0]["source_text"] == (
        "ספר היראה ליונה בן אברהם גרונדי"
    )
    assert any(
        stmt["property_id"] == "P2093"
        and stmt["value"] == "יונה בן אברהם גרונדי"
        for stmt in work["statements"]
    )
    assert not any("יונה" in value for value in work["descriptions"].values())


@pytest.mark.asyncio
async def test_contents_author_field_becomes_p2093_and_stays_out_of_description() -> None:
    rec = {
        **_fake_marc_record(),
        "contents": [{
            "title": 'תשב"ץ',
            "author": "שמשון בן צדוק",
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    assert any(
        stmt["property_id"] == "P2093"
        and stmt["value"] == "שמשון בן צדוק"
        for stmt in work["statements"]
    )
    assert not any("שמשון" in value for value in work["descriptions"].values())


@pytest.mark.asyncio
async def test_approved_work_qid_on_content_is_reused() -> None:
    rec = {
        **_fake_marc_record(),
        "contents": [{
            "title": "תלמוד בבלי",
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[{
            "control_number": "1",
            "entity_text": "תלמוד בבלי",
            "entity_kind": "work",
            "role": "contained_work",
            "wikidata_qid": "Q192043",
            "approved": True,
        }],
        entities_by_cn={},
        return_native=False,
    )
    manuscript = next(
        item for item in result["items"] if item["entity_type"] == "manuscript"
    )
    assert any(
        stmt["property_id"] == "P1574" and stmt["value"] == "Q192043"
        for stmt in manuscript["statements"]
    )
    assert result["summary"]["works"] == 0


@pytest.mark.asyncio
async def test_title_phrase_starting_lamed_is_not_misread_as_author() -> None:
    rec = {
        **_fake_marc_record(),
        "500$a": 'החבור כולל כוונות התפילה לכל השנה עפ"י קבלת האר"י.',
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    assert work["labels"]["he"] == 'כוונות התפילה לכל השנה עפ"י קבלת האר"י'
    assert "by " not in work["descriptions"]["en"]


@pytest.mark.asyncio
async def test_work_uses_exact_approved_author_qid_before_person_pass() -> None:
    rec = {
        **_fake_marc_record(),
        "contents": [{
            "title": "ספר היראה ליונה בן אברהם גרונדי",
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[{
            "control_number": "1",
            "entity_text": "יונה בן אברהם גרונדי",
            "role": "author",
            "mazal_id": "987000000000000001",
            "wikidata_qid": "Q123",
            "approved": True,
        }],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    assert any(
        stmt["property_id"] == "P50" and stmt["value"] == "Q123"
        for stmt in work["statements"]
    )
    assert not any(stmt["property_id"] == "P2093" for stmt in work["statements"])


@pytest.mark.asyncio
async def test_reused_work_gains_author_claim_from_later_source() -> None:
    title = "מנחת יהודה : פרוש על שמואל, מלכים וישעיהו"
    first = {
        **_fake_marc_record(control_number="1"),
        "contents": [{
            "title": title,
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    second = {
        **_fake_marc_record(control_number="2"),
        "contents": [{
            "title": title,
            "author": "חנין, יהודה בן יעקב",
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[first, second],
        approved_matches=[{
            "control_number": "2",
            "entity_text": "חנין, יהודה בן יעקב",
            "entity_kind": "person",
            "role": "author",
            "wikidata_qid": "Q118186113",
            "approved": True,
        }],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    author = next(
        stmt for stmt in work["statements"]
        if stmt["property_id"] == "P50"
    )
    assert author["value"] == "Q118186113"
    assert any(
        ref["property_id"] == "P3959" and ref["value"] == "2"
        for ref in author["references"]
    )


@pytest.mark.asyncio
async def test_approved_work_qid_keeps_author_on_existing_local_work() -> None:
    title = "מנחת יהודה : פרוש על שמואל, מלכים וישעיהו"
    first = {
        **_fake_marc_record(control_number="1"),
        "contents": [{
            "title": title,
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    second = {
        **_fake_marc_record(control_number="2"),
        "authors": [{"name": "חנין, יהודה בן יעקב", "role": "author"}],
        "contents": [{
            "title": title,
            "source_field": "505",
            "candidate_kind": "named_work",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[first, second],
        approved_matches=[
            {
                "control_number": "2",
                "entity_text": "חנין, יהודה בן יעקב",
                "entity_kind": "person",
                "role": "author",
                "wikidata_qid": "Q118186113",
                "approved": True,
            },
            {
                "control_number": "2",
                "entity_text": title,
                "entity_kind": "work",
                "role": "contained work",
                "wikidata_qid": "Q141175558",
                "approved": True,
            },
        ],
        entities_by_cn={},
        return_native=False,
    )
    work = next(item for item in result["items"] if item["entity_type"] == "work")
    author = next(
        stmt for stmt in work["statements"]
        if stmt["property_id"] == "P50"
    )
    assert author["value"] == "Q118186113"


def test_known_work_title_aliases_are_exact_and_not_fuzzy() -> None:
    from converter.wikidata.property_mapping import known_work_qid_for_title

    assert known_work_qid_for_title("יוסיפון") == "Q1561132"
    assert known_work_qid_for_title('פרוש המשנה לרמב"ם (פרק חלק)') == "Q6124976"
    assert known_work_qid_for_title("משנה תורה") == "Q201029"
    assert known_work_qid_for_title("משנה תורה (ספר זמנים)") is None
    assert known_work_qid_for_title("יצירה דומה") is None
    assert known_work_qid_for_title("Bible") == "Q1845"
    assert known_work_qid_for_title('תנ"ך') == "Q83367"
    assert known_work_qid_for_title("Tanakh") == "Q83367"
    assert known_work_qid_for_title("הגדה של פסח") == "Q623354"
    assert known_work_qid_for_title("Passover Haggadah") == "Q623354"
    assert known_work_qid_for_title("תיקון חצות") == "Q2740944"
    assert known_work_qid_for_title("Tikkun Chatzot") == "Q2740944"
    from converter.wikidata.property_mapping import work_item_existing_qid_for_title

    assert work_item_existing_qid_for_title("תיקון חצות") is None
    assert work_item_existing_qid_for_title("Tikkun Chatzot") is None


@pytest.mark.asyncio
async def test_related_works_known_qid_links_without_local_work() -> None:
    """Known related works emit P1574 to the live QID — no evidence-less CREATE."""
    from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality

    rec = {
        **_fake_marc_record(),
        "related_works": [
            {"title": "Bible"},
            {"title": 'תנ"ך'},
            {"title": "תיקון חצות"},
            {"title": "הגדה של פסח"},
        ],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[],
        entities_by_cn=None,
        return_native=True,
    )
    assert result["summary"]["works"] == 0
    manuscript = next(
        item for item in result["native_items"] if item.entity_type == "manuscript"
    )
    exemplar_values = [
        stmt.value for stmt in manuscript.statements if stmt.property_id == "P1574"
    ]
    assert set(exemplar_values) >= {"Q1845", "Q83367", "Q2740944", "Q623354"}
    assert all(not str(v).startswith("__LOCAL:") for v in exemplar_values)
    accepted = [
        row for row in manuscript.work_candidate_evidence
        if isinstance(row, dict) and row.get("accepted") is True
    ]
    assert len(accepted) >= 4
    assert_wikidata_export_quality(result["native_items"])


@pytest.mark.asyncio
async def test_related_works_curator_approved_stamps_evidence_on_local_work() -> None:
    rec = {
        **_fake_marc_record(),
        "related_works": [{
            "title": "עת שערי רצון",
            "approved": True,
            "source_field": "787",
        }],
    }
    result = await wikidata_studio.build_items_for_run(
        marc_records=[rec],
        approved_matches=[],
        entities_by_cn=None,
        return_native=True,
    )
    assert result["summary"]["works"] == 1
    work = next(item for item in result["native_items"] if item.entity_type == "work")
    assert any(
        isinstance(row, dict) and row.get("accepted") is True
        for row in work.work_candidate_evidence
    )
    from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality

    assert_wikidata_export_quality(result["native_items"])
