"""MARC 958 script and 957 colophon reach the projection (Rule W-281).

Both fields carried real data that never arrived anywhere: `958$a` held the
paleographic attribution on 768 of the 897 records in the 17th-century corpus
while `P9302` sat at 5.4%, and `957$a` held the colophon text on 617 records
while `P1684` sat at 0.9%.

The colophon work must not weaken Rule W-72. `is_catalog_note_placeholder`
rejects any string containing ``קולופון`` because a scribe never names his own
colophon — that word marks cataloger apparatus (``בסופו קולופון`` /
``קולופון המחבר בדף …``). NLI's 957$a *labels* the field with that word
(``קולופון: "תם ונשלם …"``), so the label is stripped at ingest and the guard
is left untouched. These tests pin both halves.
"""
from __future__ import annotations

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from converter.wikidata.item_builder import (
    WikidataItemBuilder,
    _is_catalog_note_placeholder,
)


def _p9302(record: dict) -> set[str]:
    item = WikidataItemBuilder().build_manuscript_item(prepare_record_for_pipeline(record))
    return {str(s.value) for s in item.statements if s.property_id == "P9302"}


def _p1684(record: dict) -> list[str]:
    item = WikidataItemBuilder().build_manuscript_item(prepare_record_for_pipeline(record))
    return [str(s.value) for s in item.statements if s.property_id == "P1684"]


# ── 958 → P9302 ──────────────────────────────────────────────────────────


def test_958a_script_tradition_reaches_p9302() -> None:
    assert _p9302({"001": "a", "245$a": "כותרת", "958$a": "תימנית"}) == {"Q121094936"}


def test_958a_covers_the_traditions_in_the_corpus() -> None:
    expect = {
        "תימנית": "Q121094936",
        "איטלקית": "Q133370075",
        "אשכנזית": "Q121094898",
        "מזרחית": "Q133327488",
        "ספרדית": "Q133177480",
    }
    for he, qid in expect.items():
        assert _p9302({"001": "a", "958$a": he}) == {qid}, he


def test_958a_unmapped_tradition_fails_closed() -> None:
    """פרובנצלית / מערבית / קראית have no verified QID (Rule R8)."""
    for he in ("פרובנצלית", "מערבית", "קראית"):
        assert _p9302({"001": "a", "958$a": he}) == set(), he


def test_958b_writing_style_is_recorded_as_script_mode() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "958$b": "מרובעת"})
    assert prepared["script_mode"] == "SquareScript"


def test_958b_multiword_resolves_to_first_matching_token() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "958$b": "מרובעת ורהוטה"})
    assert prepared["script_mode"] in {"SquareScript", "CursiveScript"}


def test_958_does_not_overwrite_an_existing_script_type() -> None:
    prepared = prepare_record_for_pipeline({
        "001": "a", "script_type": "AshkenaziScript", "958$a": "תימנית",
    })
    assert prepared["script_type"] == "AshkenaziScript"


# ── 957 → P1684 ──────────────────────────────────────────────────────────


def test_957_colophon_reaches_p1684() -> None:
    vals = _p1684({"001": "a", "245$a": "כותרת", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert vals, "the colophon text must reach P1684"
    assert any("תם ונשלם" in v for v in vals)


def test_the_colophon_label_is_stripped_not_kept() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert not prepared["colophon_text"].startswith("קולופון")


def test_957_label_with_parenthetical_folio_is_stripped() -> None:
    prepared = prepare_record_for_pipeline({
        "001": "a", "957$a": 'קולופון (דף קצה ע"א): ""ותהי השלמת הספר"".',
    })
    assert not prepared["colophon_text"].startswith("קולופון")
    assert "ותהי השלמת" in prepared["colophon_text"]


def test_catalog_apparatus_still_rejected_despite_the_label_strip() -> None:
    """Rule W-72 must not weaken: third-person prose stays a placeholder."""
    for text in (
        "בסופו קולופון בדף 4.",
        "קולופון המחבר בדף 12.",
        "(קולופון 4)",
    ):
        assert _is_catalog_note_placeholder(text), text


def test_stripped_colophon_is_not_a_placeholder() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert not _is_catalog_note_placeholder(prepared["colophon_text"])


def test_one_apparatus_piece_does_not_poison_a_real_colophon() -> None:
    """A `רשומה זמנית` note in 590$a used to sink the whole compound."""
    prepared = prepare_record_for_pipeline({
        "001": "a",
        "957$a": 'קולופון: ""תם ונשלם"".',
        "590$a": "רשומה זמנית",
    })
    assert "תם ונשלם" in prepared["colophon_text"]
    assert "רשומה זמנית" not in prepared["colophon_text"]


def test_590_colophon_still_works() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "590$a": "תם ונשלם הספר"})
    assert prepared["colophon_text"] == "תם ונשלם הספר"


def test_590_apparatus_is_dropped_by_design() -> None:
    """A 590 note naming the colophon is catalog apparatus (Rule W-72).

    Before the piece filter this string became `colophon_text` and was then
    rejected at projection time by `is_catalog_note_placeholder` — so it never
    reached P1684 either way. The filter just moves that rejection earlier,
    where it can no longer take a real colophon down with it.
    """
    prepared = prepare_record_for_pipeline({"001": "a", "590$a": "קולופון מקומי"})
    assert "colophon_text" not in prepared or not prepared["colophon_text"]


# ── Rule W-282: two silent-total defects ─────────────────────────────────
#
# (a) `build_context` accepted `wikibase_endpoint` and never passed it to
#     `RuleContext`, so it stayed "" and every live rule built a host-less
#     "/w/api.php" and failed with "unknown url type".
# (b) `_build_claim_spec` shaped an empty monolingualtext into a claim, so 885
#     items carried {"text": "", "language": "he"} on P288 and every one failed
#     `claims.datatype`.


def test_build_context_passes_the_wikibase_endpoint() -> None:
    """The endpoint must reach the rules, not die in the factory."""
    from app.pipeline.rule_verify.scope import build_context

    ctx = build_context(
        run_id="r", items=[], api_enabled=True,
        wikibase_endpoint="https://mhm-hmo.wikibase.cloud",
    )
    assert ctx.wikibase_endpoint == "https://mhm-hmo.wikibase.cloud"


def test_build_context_defaults_to_an_empty_endpoint() -> None:
    from app.pipeline.rule_verify.scope import build_context

    assert build_context(run_id="r", items=[], api_enabled=True).wikibase_endpoint == ""


def test_empty_monolingualtext_is_not_a_claim() -> None:
    """An empty text is not a claim value — it becomes a skipped statement."""
    from types import SimpleNamespace

    from converter.wikibase.hmo_exporter import _build_claim_spec

    for blank in ("", "   "):
        spec = _build_claim_spec(
            SimpleNamespace(wikibase_id="P288", datatype="monolingualtext"),
            SimpleNamespace(value=blank, language="he"),
        )
        assert spec is None, repr(blank)


def test_empty_string_is_not_a_claim() -> None:
    from types import SimpleNamespace

    from converter.wikibase.hmo_exporter import _build_claim_spec

    assert _build_claim_spec(
        SimpleNamespace(wikibase_id="P288", datatype="string"),
        SimpleNamespace(value="", language=None),
    ) is None


def test_real_monolingualtext_still_shapes() -> None:
    from types import SimpleNamespace

    from converter.wikibase.hmo_exporter import _build_claim_spec

    spec = _build_claim_spec(
        SimpleNamespace(wikibase_id="P288", datatype="monolingualtext"),
        SimpleNamespace(value="תם ונשלם", language="he"),
    )
    assert spec is not None
    assert spec.value == {"text": "תם ונשלם", "language": "he"}


# ── Rule W-283: the three remaining findings ─────────────────────────────
#
# run 47e03437 read `fail 976`: 52 within-run duplicates, 28 Wikidata label
# collisions, 11 items with no claims. All three were classification defects
# rather than bad data.


def test_canon_ref_labels_keep_the_hierarchy() -> None:
    """Mishnah: Berakhot and Talmud Bavli: Berakhot are different works.

    The exporter used to strip everything before the colon
    (`.split(":", 1)[-1]`), so every same-record canon-ref pair collapsed
    onto one label and read as a within-run duplicate. All 52 duplicates in
    run 47e03437 were this shape.
    """
    from rdflib import Graph, Literal, RDF, RDFS

    from converter.config.namespaces import HM
    from converter.wikibase.hmo_exporter import HmoWikibaseExporter

    graph = Graph()
    a, b = HM.CanonRef_A, HM.CanonRef_B
    for node, hier in ((a, "Mishnah"), (b, "Talmud_Bavli")):
        graph.add((node, RDF.type, HM.CanonicalReference))
        graph.add((node, RDFS.label, Literal(f"{hier}: ברכות", lang="en")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    labels = {d.source_uri: (d.labels or {}).get("en") for d in drafts}
    values = list(labels.values())
    assert values[0] != values[1], labels
    assert all("_" not in (v or "") for v in values), labels


def test_vocabulary_stubs_are_not_claim_failures() -> None:
    """Ontology-declared individuals are vocabulary terms, not entities."""
    from app.pipeline.rule_verify.rules.hmo import _run_claims_present

    for et in ("CertaintyLevel", "DateFormatType", "HierarchyType",
               "AttributionSource", "EpistemologicalStatus"):
        res = _run_claims_present({"entity_type": et, "claims": []}, None)
        assert res.state == "not_relevant", et


def test_a_real_entity_with_no_claims_still_fails() -> None:
    from app.pipeline.rule_verify.rules.hmo import _run_claims_present

    res = _run_claims_present(
        {"entity_type": "F4_Manifestation_Singleton", "claims": []}, None,
    )
    assert res.state == "fail"


def test_label_candidates_are_reported_not_failed() -> None:
    """A same-label Wikidata item is a link candidate, not a defect."""
    from tests.test_rule_verify import _engine as engine, _item

    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        if call == "inlabel_search":
            return [{"qid": "Q777", "label": arg}]
        raise AssertionError(call)

    item = _item(claims=[])
    results = engine([item], api=True, fetcher=fetcher).run_scope(
        [item], include_api=True,
    )
    cand = {r.rule_id: r for r in results["QDraft_MS1"]}[
        "hmo.wikidata.label_candidates"
    ]
    assert cand.state == "pass", cand.message
    assert cand.evidence["requires_curator_confirmation"] is True
    assert cand.evidence["candidates"][0]["qid"] == "Q777"
