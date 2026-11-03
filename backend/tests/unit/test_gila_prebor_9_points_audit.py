"""Unit tests for Prof. Gila Prebor's 9-point Wikidata conversion audit (Rule W-293)."""

from __future__ import annotations

import csv
from pathlib import Path

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_qid_ledger import ledger_key_for_item
from app.pipeline.wikidata_studio import finish_native_items
from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.property_mapping import (
    Q_ADDRESSEE_ROLE,
    Q_COLOPHON,
    Q_DIGITAL_COLLECTION,
    Q_FORMER_OWNER_ROLE,
    Q_KTIV,
    Q_LATIN_SCRIPT,
    Q_MANUSCRIPT,
    Q_MANUSCRIPT_CODEX,
    Q_PLACE_OF_ATTESTATION,
    Q_SCROLL,
    Q_SENDER_ROLE,
    Q_TITLE_PAGE,
)


def _statements(item: object, pid: str) -> list[object]:
    return [s for s in getattr(item, "statements", []) if s.property_id == pid]


def test_point1_942_structured_label_and_shelfmark_for_hizzuk_emunah() -> None:
    raw = {
        "001": '"990000885680205171"',
        "041$a": '"heb"',
        "090$a": '"F 28726"',
        "245$a": '"חזוק אמונה :"',
        "245$b": '"חלקים א-ב."',
        "260$c": '"תמ""א (1681)."',
        "300$a": '"73 דף."',
        "710$a": '"The Jewish Theological Seminary of America|Adler, Elkan Nathan"',
        "710$e": '"current owner|former owner"',
        "942$a": '"The Jewish Theological Seminary of America|Adler, Elkan Nathan"',
        "942$x": '"New York, NY"',
        "942$c": '"USA"',
        "942$z": '"Ms. 2473|Ms. 4088"',
        "958$a": '"אשכנזית"',
    }
    rec = prepare_record_for_pipeline(raw)
    item = WikidataItemBuilder().build_manuscript_item(rec)
    assert item.labels["en"] == (
        "The Jewish Theological Seminary of America. New York, USA. Ms. 2473"
    )
    assert [s.value for s in _statements(item, "P217")] == ["Ms. 2473"]
    assert "Ms. 2473" in item.descriptions["en"]
    assert "New York" in item.descriptions["en"]
    assert rec["former_shelfmarks"] == [
        {"holder": "Adler, Elkan Nathan", "shelfmark": "Ms. 4088"},
    ]
    assert rec["microfilm_numbers"] == ["F 28726"]


def test_point1_942_labels_for_iggeret_sukkah_and_derushim() -> None:
    rec_sukkah = prepare_record_for_pipeline({
        "001": '"990000802880205171"',
        "090$a": '"F 51052 (Reel 52)"',
        "245$a": '"אגרת סוכה."',
        "710$a": '"The National Library of Russia"',
        "710$e": '"current owner"',
        "942$a": '"The National Library of Russia"',
        "942$x": '"St. Petersburg"',
        "942$c": '"Russia"',
        "942$z": '"Ms. EVR I 726"',
    })
    ms_sukkah = WikidataItemBuilder().build_manuscript_item(rec_sukkah)
    assert ms_sukkah.labels["en"] == (
        "The National Library of Russia. St. Petersburg, Russia. Ms. EVR I 726"
    )
    assert [s.value for s in _statements(ms_sukkah, "P217")] == ["Ms. EVR I 726"]

    rec_derushim = prepare_record_for_pipeline({
        "001": '"990001915930205171"',
        "090$a": '"F 12658|MSS-D 7817"',
        "245$a": '"דרושים."',
        "710$a": '"Library of the Hungarian Academy of Sciences"',
        "710$e": '"current owner"',
        "942$a": '"Library of the Hungarian Academy of Sciences"',
        "942$x": '"Budapest"',
        "942$c": '"Hungary"',
        "942$z": '"Ms. Kaufmann A 315"',
    })
    ms_derushim = WikidataItemBuilder().build_manuscript_item(rec_derushim)
    assert ms_derushim.labels["en"] == (
        "Library of the Hungarian Academy of Sciences. Budapest, Hungary. Ms. Kaufmann A 315"
    )
    assert [s.value for s in _statements(ms_derushim, "P217")] == ["Ms. Kaufmann A 315"]

    rec_ambrosiana = prepare_record_for_pipeline({
        "001": '"990000987120205171"',
        "245$a": '"קובץ."',
        "710$a": '"Ambrosian Library"',
        "710$e": '"current owner"',
        "942$a": '"Ambrosian Library|Catalogue Bernheimer"',
        "942$x": '"Milan|Milan"',
        "942$c": '"Italy|Italy"',
        "942$z": '"Ms. O 97 Sup.|107"',
    })
    ms_ambrosiana = WikidataItemBuilder().build_manuscript_item(rec_ambrosiana)
    assert not ms_ambrosiana.labels["he"].endswith(".")
    assert not ms_ambrosiana.labels["en"].endswith(".")


def test_point2_manuscript_codex_scroll_and_fragment_classifications() -> None:
    builder = WikidataItemBuilder()
    codex_qids = builder._determine_instance_type({
        "title": "חזוק אמונה",
        "extent_text": "73 דף.",
        "extent": 73,
    })
    assert Q_MANUSCRIPT_CODEX in codex_qids
    assert Q_MANUSCRIPT in codex_qids

    scroll_qids = builder._determine_instance_type({
        "title": "מגלת אסתר.",
        "extent_text": "23 עמודות ;",
    })
    assert Q_SCROLL in scroll_qids
    assert Q_MANUSCRIPT_CODEX not in scroll_qids

    fragment_qids = builder._determine_instance_type({
        "title": "תכלאל (קטע).",
        "extent_text": "9 דפים ;",
        "extent": 9,
    })
    assert Q_MANUSCRIPT_CODEX not in fragment_qids


def test_point3_work_ledger_keys_and_reconciler_separate_homonymous_titles() -> None:
    rec_cantarini = prepare_record_for_pipeline({
        "001": '"990001915930205171"',
        "100$a": '"קנטריני, יצחק חיים,"',
        "245$a": '"דרושים."',
    })
    rec_avraham = prepare_record_for_pipeline({
        "001": '"990001123390205171"',
        "100$a": '"אברהם"',
        "100$c": '"הלוי, ממרוקו"',
        "245$a": '"דרושים."',
    })
    items = WikidataItemBuilder().build_all([rec_cantarini, rec_avraham])
    works = [i for i in items if i.entity_type == "work"]
    assert len(works) == 2
    keys = {ledger_key_for_item(w) for w in works}
    assert len(keys) == 2


def test_point4_ktiv_part_of_with_digital_collection_role() -> None:
    rec = prepare_record_for_pipeline({
        "001": '"990000885680205171"',
        "245$a": '"חזוק אמונה"',
    })
    item = WikidataItemBuilder().build_manuscript_item(rec)
    p361 = _statements(item, "P361")
    assert len(p361) == 1
    assert p361[0].value == Q_KTIV
    assert p361[0].qualifiers == [
        {"property": "P3831", "value": Q_DIGITAL_COLLECTION, "type": "item"},
    ]


def test_point5_author_100c_join_former_owner_sender_addressee_and_uncertain_scribe() -> None:
    rec = prepare_record_for_pipeline({
        "001": '"990000885680205171"',
        "100$a": '"יצחק בן אברהם,"',
        "100$c": '"מטרוקי,"',
        "245$a": '"חזוק אמונה"',
        "700$a": '"פיבש יואל בן שמואל שליט|אליהו בן אשר,|משה בן יהודה,|מינסטר, סבסטין,"',
        "700$c": '"הלוי, אשכנזי,|מבוצ\'ץ\'"',
        "700$e": '"(מעתיק)|(ממנו)|(מוזכר)|(אליו)"',
        "710$a": '"The Jewish Theological Seminary of America|Adler, Elkan Nathan"',
        "710$e": '"current owner|former owner"',
        "942$a": '"The Jewish Theological Seminary of America|Adler, Elkan Nathan"',
        "942$z": '"Ms. 2473|Ms. 4088"',
    })
    assert rec["authors"][0]["name"] == "יצחק בן אברהם מטרוקי"
    contrib_names = [c["name"] for c in rec["contributors"]]
    assert "אליהו בן אשר הלוי, אשכנזי" in contrib_names
    assert "משה בן יהודה מבוצ'ץ'" in contrib_names

    rec["marc_authority_matches"] = [
        {
            "name": "Adler, Elkan Nathan",
            "role": "former owner",
            "field": "710",
            "entity_kind": "person",
            "wikidata_qid": "Q1170731",
            "approved": True,
        },
        {
            "name": "אליהו בן אשר הלוי, אשכנזי",
            "role": "(ממנו)",
            "field": "700",
            "entity_kind": "person",
            "wikidata_qid": "Q66031",
            "approved": True,
        },
        {
            "name": "מינסטר, סבסטין",
            "role": "(אליו)",
            "field": "700",
            "entity_kind": "person",
            "wikidata_qid": "Q61073",
            "approved": True,
        },
    ]
    items = WikidataItemBuilder().build_all([rec])
    ms = next(i for i in items if i.entity_type == "manuscript")
    p3342 = {s.value: s.qualifiers for s in _statements(ms, "P3342")}
    assert p3342["Q1170731"] == [
        {"property": "P3831", "value": Q_FORMER_OWNER_ROLE, "type": "item"},
        {"property": "P217", "value": "Ms. 4088", "type": "string"},
    ]
    assert p3342["Q66031"] == [
        {"property": "P3831", "value": Q_SENDER_ROLE, "type": "item"},
    ]
    assert p3342["Q61073"] == [
        {"property": "P3831", "value": Q_ADDRESSEE_ROLE, "type": "item"},
    ]


def test_point6_folio_prefixed_500_notes_and_740_related_works() -> None:
    rec = prepare_record_for_pipeline({
        "001": '"990000802880205171"',
        "100$a": '"בגי, יוסף בן משה"',
        "245$a": '"אגרת סוכה."',
        "500$a": '"בדפים 21ב-22ב: לקוטים בין השאר מספר עץ חיים."',
        "740$a": '"כרוניקה על גזירות תתנ""ו (קצור)"',
    })
    items = WikidataItemBuilder().build_all([rec])
    ms = next(i for i in items if i.entity_type == "manuscript")
    p1574 = _statements(ms, "P1574")
    titles = {
        next(q["value"] for q in s.qualifiers if q.get("property") == "P1932"): s.qualifiers
        for s in p1574
    }
    assert "אגרת סוכה" in titles
    assert "לקוטים בין השאר מספר עץ חיים" in titles
    assert any(
        q.get("property") == "P958" and q.get("value") == "21ב-22ב"
        for q in titles["לקוטים בין השאר מספר עץ חיים"]
    )
    assert 'כרוניקה על גזירות תתנ"ו (קצור)' in titles


def test_point7_exact_year_has_no_circa_and_preserves_hebrew_date_and_places_and_scripts() -> None:
    rec_sukkah = prepare_record_for_pipeline({
        "001": '"990000802880205171"',
        "041$a": '"heb"',
        "245$a": '"אגרת סוכה."',
        "260$c": '"ת""י (1650)."',
        "751$a": '"Panevezys (Lithuania)"',
        "751$e": '"place of writing"',
        "958$a": '"קראית-אשכנזית"',
    })
    ms_sukkah = WikidataItemBuilder().build_manuscript_item(rec_sukkah)
    p571 = _statements(ms_sukkah, "P571")[0]
    assert not any(q.get("property") == "P1480" for q in p571.qualifiers)
    assert any(q.get("property") == "P1932" and 'ת"י' in q.get("value", "") for q in p571.qualifiers)
    assert [s.value for s in _statements(ms_sukkah, "P1071")] == ["Q1719466"]
    assert [s.value for s in _statements(ms_sukkah, "P9302")] == ["Q121094898"]

    rec_hizzuk = prepare_record_for_pipeline({
        "001": '"990000885680205171"',
        "041$a": '"heb"',
        "245$a": '"חזוק אמונה"',
        "260$c": '"תמ""א (1681)."',
        "751$a": '"Metz (France)|Hamburg (Germany)|Buchach (Ukraine)"',
        "751$e": '"place of writing|related place|related place"',
    })
    ms_hizzuk = WikidataItemBuilder().build_manuscript_item(rec_hizzuk)
    assert [s.value for s in _statements(ms_hizzuk, "P1071")] == ["Q22690"]
    p7153 = _statements(ms_hizzuk, "P7153")
    assert {s.value for s in p7153} == {"Q1055", "Q849109"}
    for s in p7153:
        assert s.qualifiers == [
            {"property": "P3831", "value": Q_PLACE_OF_ATTESTATION, "type": "item"},
        ]

    rec_derushim = prepare_record_for_pipeline({
        "001": '"990001915930205171"',
        "041$a": '"ita|heb"',
        "245$a": '"דרושים."',
        "260$c": '"תל""ה (1675)."',
        "500$a": '"באיטלקית באותיות לטיניות.|נושא נוסף: דרשות"',
    })
    ms_derushim = WikidataItemBuilder().build_manuscript_item(rec_derushim)
    assert Q_LATIN_SCRIPT in [s.value for s in _statements(ms_derushim, "P282")]
    assert _statements(ms_derushim, "P9302") == []


def test_point8_subjects_bibliography_581_colophon_folio_and_title_page_p1684() -> None:
    rec = prepare_record_for_pipeline({
        "001": '"990000885680205171"',
        "245$a": '"חזוק אמונה"',
        "500$a": '"בשער שבראשו: ""ספר אמרי שפר נקרא בשמו חיזוק אמונה""."',
        "581$a": '"Marx, A. ""The polemical Manuscripts in the library of the JTS"" (New York 1929), p. 52."',
        "650$a": '"Jewish sermons, Hebrew|Messiah|Religious disputations"',
        "957$a": '"קולופון (בדף 21א): ""הותחלה במחוז פוניוויש... שמואל(?) הצעיר""."',
    })
    ms = WikidataItemBuilder().build_manuscript_item(rec)
    assert {s.value for s in _statements(ms, "P921")} == {
        "Q60797", "Q5421977", "Q1123131",
    }
    assert [s.value for s in _statements(ms, "P528")] == [
        'Marx, A. "The polemical Manuscripts in the library of the JTS" (New York 1929), p. 52.'
    ]
    inscriptions = _statements(ms, "P1684")
    roles = {
        next(q["value"] for q in s.qualifiers if q.get("property") == "P3831"): s
        for s in inscriptions
    }
    assert Q_COLOPHON in roles
    assert any(
        q.get("property") == "P7416" and q.get("value") == "21א"
        for q in roles[Q_COLOPHON].qualifiers
    )
    assert "שמואל(?)" in str(roles[Q_COLOPHON].value)
    assert Q_TITLE_PAGE in roles
    assert "ספר אמרי שפר" in str(roles[Q_TITLE_PAGE].value)
