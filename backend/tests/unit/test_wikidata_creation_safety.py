"""The last live creation boundary rejects known source and identity defects."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.item_validator import validate_item
from converter.wikidata.reconciler import ReconciliationUnavailableError
from converter.wikidata.uploader import WikidataUploader


def _person() -> WikidataItem:
    return WikidataItem(
        local_id="person:reviewed",
        entity_type="person",
        labels={"en": "Reviewed manuscript author"},
        descriptions={"en": "Hebrew manuscript author"},
        statements=[
            WikidataStatement("P31", "Q5", "item"),
            WikidataStatement("P214", "123456789", "external-id"),
        ],
    )


def _catalogue_scribe() -> WikidataItem:
    return WikidataItem(
        local_id="person:al-tawili",
        entity_type="person",
        records=["990000598840205171", "990000898590205171"],
        labels={"he": "שלום בן יהודה אלטוילי", "en": "Shalom ben Judah al-Tawili"},
        aliases={"he": ["שלום בן יהודה בן שלום בן יהודה אלטוילי"]},
        descriptions={"en": "17th-century Hebrew scribe"},
        statements=[
            WikidataStatement("P31", "Q5", "item"),
            WikidataStatement("P106", "Q916292", "item"),
            WikidataStatement("P973", "https://teman.org.il/content/11620", "url"),
        ],
    )


@pytest.mark.parametrize("defect", ["unreviewed_url", "wrong_records", "wrong_name", "deprecated"])
def test_a_person_url_requires_the_exact_reviewed_scope(defect: str) -> None:
    item = _catalogue_scribe()
    if defect == "unreviewed_url":
        item.statements[-1].value = "https://teman.org.il/content/123"
    elif defect == "wrong_records":
        item.records.pop()
    elif defect == "wrong_name":
        item.labels["he"] = "שלום בן דוד"
    else:
        item.statements[-1].rank = "deprecated"

    assert any(issue.code == "NO_IDENTIFIER" for issue in validate_item(item))


@pytest.mark.parametrize("source_match", [(), ("Q456",), "unavailable"])
def test_create_checks_the_reviewed_catalogue_url_at_the_final_write(
    boundary, monkeypatch: pytest.MonkeyPatch, source_match: tuple[str, ...] | str,
) -> None:
    uploader, writes = boundary
    item = _catalogue_scribe()
    source = Mock(return_value=source_match)
    if source_match == "unavailable":
        source.side_effect = ReconciliationUnavailableError("The source check is unavailable.")
    names = Mock(return_value=())
    monkeypatch.setattr("app.pipeline.wikidata_upload._reconcile_for_upload", lambda *args: (None, "none"))
    monkeypatch.setattr(
        "converter.wikidata.reconciler.WikidataReconciler",
        lambda: SimpleNamespace(
            person_creation_source_candidates=source,
            person_creation_name_candidates=names,
        ),
    )

    result = uploader.upload_item(item)

    urls = source.call_args.args[0]
    assert "https://teman.org.il/content/11620" in urls
    assert "https://www.teman.org.il/ar/node/11620" in urls
    if source_match:
        assert result.status == "blocked"
        names.assert_not_called()
        writes.assert_not_called()
    else:
        assert result.status == "success"
        names.assert_called_once_with(item.labels, item.aliases)
        writes.assert_called_once()


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch):
    uploader = WikidataUploader(token="", allow_live=True)
    writes = Mock()

    class NewItem:
        id = None

        def write(self, **kwargs):
            writes(kwargs)
            self.id = "Q123"
            return self

    monkeypatch.setattr(uploader, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(uploader, "_get_authenticated_user", lambda: "Project")
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda item: (NewItem(), 1, ["P214"]))
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    return uploader, writes


@pytest.mark.parametrize(
    "defect,code",
    [
        ("person_without_identifier", "NO_IDENTIFIER"),
        ("empty_label", "EMPTY_LABEL"),
        ("untyped_item", "CREATE_ENTITY_TYPE"),
        ("work_author_on_manuscript", "P50_ON_MANUSCRIPT"),
        ("wrong_identifier_property", "IDENTIFIER_WRONG_PROPERTY"),
        ("conflicting_identifiers", "CREATE_IDENTITY_CONFLICT"),
        ("missing_description", "CREATE_DESCRIPTION"),
        ("missing_class", "CREATE_INSTANCE_OF"),
        ("generated_role_description", "GENERATED_PERSON_DESCRIPTION"),
        ("catalogue_code_replaced_by_qid", "CATALOG_CODE_IS_QID"),
    ],
)
def test_direct_create_rechecks_semantic_errors_before_login(
    boundary,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
    code: str,
) -> None:
    uploader, writes = boundary
    item = _person()
    if defect == "person_without_identifier":
        item.statements = item.statements[:1]
    elif defect == "empty_label":
        item.labels = {}
    elif defect == "untyped_item":
        item.entity_type = ""
    elif defect == "wrong_identifier_property":
        item.statements[1].value = "987007259482805171"
    elif defect == "conflicting_identifiers":
        item.statements.append(WikidataStatement("P214", "234567890", "external-id"))
    elif defect == "missing_description":
        item.descriptions = {}
    elif defect == "missing_class":
        item.statements = item.statements[1:]
    elif defect == "catalogue_code_replaced_by_qid":
        item.statements.append(WikidataStatement("P528", "Q141754916", "string"))
    elif defect == "generated_role_description":
        item.descriptions = {"en": "Person (scribe) linked to manuscript 990000593730205171"}
    else:
        item.entity_type = "manuscript"
        item.statements = [
            WikidataStatement("P31", "Q87167", "item"),
            WikidataStatement("P50", "Q168171", "item"),
        ]
    login = Mock(side_effect=AssertionError("Invalid data must not reach login"))
    monkeypatch.setattr(uploader, "_init_wbi", login)

    result = uploader.upload_item(item)

    assert result.status == "blocked"
    assert code in result.message
    login.assert_not_called()
    writes.assert_not_called()


@pytest.mark.parametrize("existing_qid,unavailable", [("Q456", False), (None, True)])
def test_create_rechecks_duplicate_absence_at_the_final_write(
    boundary,
    monkeypatch: pytest.MonkeyPatch,
    existing_qid: str | None,
    unavailable: bool,
) -> None:
    uploader, writes = boundary

    def reconcile(*args):
        if unavailable:
            raise ReconciliationUnavailableError("The identity lookup is unavailable")
        return existing_qid, "P214"

    monkeypatch.setattr("app.pipeline.wikidata_upload._reconcile_for_upload", reconcile)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.WikidataReconciler", lambda: SimpleNamespace()
    )

    result = uploader.upload_item(_person())

    assert result.status == "blocked"
    assert "CREATE_IDENTITY" in result.message
    if existing_qid:
        assert existing_qid in result.message
    writes.assert_not_called()


def test_generated_person_role_lists_are_a_blocking_semantic_error() -> None:
    item = _person()
    item.descriptions = {
        "en": (
            "Person (contributor) linked to manuscript 990000593730205171 · "
            "Person (scribe) linked to manuscript 990000593730205171"
        )
    }

    assert any(
        issue.code == "GENERATED_PERSON_DESCRIPTION" and issue.severity == "error"
        for issue in validate_item(item)
    )


def test_a_conclusive_fresh_absence_allows_a_valid_create(
    boundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader, writes = boundary
    lookup = Mock(return_value=(None, "none"))
    monkeypatch.setattr("app.pipeline.wikidata_upload._reconcile_for_upload", lookup)
    names = Mock(return_value=())
    monkeypatch.setattr(
        "converter.wikidata.reconciler.WikidataReconciler",
        lambda: SimpleNamespace(person_creation_name_candidates=names),
    )

    result = uploader.upload_item(_person())

    assert result.status == "success"
    assert result.qid == "Q123"
    lookup.assert_called_once()
    names.assert_called_once_with(_person().labels, {})
    writes.assert_called_once()


@pytest.mark.parametrize("candidates", [("Q456",), ("Q456", "Q789")])
def test_create_defers_exact_names_even_without_an_identifier_match(
    boundary, monkeypatch: pytest.MonkeyPatch, candidates: tuple[str, ...],
) -> None:
    uploader, writes = boundary
    item = _person()
    item.aliases = {"he": ["משה הסופר"]}
    monkeypatch.setattr("app.pipeline.wikidata_upload._reconcile_for_upload", lambda *args: (None, "none"))
    names = Mock(return_value=candidates)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.WikidataReconciler",
        lambda: SimpleNamespace(person_creation_name_candidates=names),
    )

    result = uploader.upload_item(item)

    assert result.status == "blocked"
    assert all(qid in result.message for qid in candidates)
    assert result.qid is None
    names.assert_called_once_with(item.labels, item.aliases)
    writes.assert_not_called()


def test_deprecated_identity_is_not_used_for_duplicate_absence(
    boundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader, writes = boundary
    item = _person()
    item.statements.insert(
        0, WikidataStatement("P214", "234567890", "external-id", rank="deprecated")
    )

    def lookup(reconciler, candidate, entity_type):
        assert [s.value for s in candidate.statements if s.property_id == "P214"] == ["123456789"]
        return "Q456", "identifier"

    monkeypatch.setattr("app.pipeline.wikidata_upload._reconcile_for_upload", lookup)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.WikidataReconciler", lambda: SimpleNamespace()
    )

    result = uploader.upload_item(item)

    assert result.status == "blocked"
    assert "Q456" in result.message
    assert item.statements[0].rank == "deprecated"
    writes.assert_not_called()


def test_ambiguous_942_does_not_publish_microfilm_as_current_shelfmark() -> None:
    from app.pipeline.marc_ingest import prepare_record_for_pipeline
    from converter.wikidata.item_builder import WikidataItemBuilder

    record = prepare_record_for_pipeline(
        {
            "001": "990001680850205171",
            "245$a": "אגרות ביידיש",
            "090$a": "F 39896",
            "942$a": (
                "Lower Saxony State Archives of Hannover|Lower Saxony State Archives of Hannover"
            ),
            "942$x": "Hannover|Hannover",
            "942$c": "Germany|Germany",
            "942$z": "Ms. Cod. Br. 23|Ms. 775",
            "942$9": "lat|lat",
        }
    )
    assert record.get("holding_subfield_reviews")

    item = WikidataItemBuilder().build_manuscript_item(record)

    assert not [s for s in item.statements if s.property_id == "P217"]
    assert all("F 39896" not in label for label in item.labels.values())
    assert record["090$a"] == "F 39896"


def test_new_manuscript_requires_a_current_shelfmark() -> None:
    from app.pipeline.wikidata_creation_safety import creation_errors

    item = WikidataItem(
        entity_type="manuscript",
        local_id="ms:review",
        labels={"en": "Library. City. F 39896"},
        descriptions={"en": "Yiddish manuscript in Library"},
        statements=[
            WikidataStatement("P31", "Q87167", "item"),
            WikidataStatement("P3959", "990001680850205171", "external-id"),
        ],
    )

    assert any("CREATE_SHELFMARK" in error for error in creation_errors(item))


@pytest.mark.parametrize("second_candidate,conflicts", [("Q456", ["P8189"]), ("Q789", [])])
def test_conflicting_person_identity_cannot_prove_duplicate_absence(
    boundary,
    monkeypatch: pytest.MonkeyPatch,
    second_candidate: str,
    conflicts: list[str],
) -> None:
    from converter.wikidata.reconciler import WikidataReconciler

    uploader, writes = boundary
    item = _person()
    item.statements.append(WikidataStatement("P8189", "987007259482805171", "external-id"))
    reconciler = WikidataReconciler()
    monkeypatch.setattr(reconciler, "reconcile_person_by_viaf", lambda value: "Q456")
    monkeypatch.setattr(reconciler, "reconcile_person_by_nli_id", lambda value: second_candidate)
    monkeypatch.setattr(reconciler, "_candidate_conflicts", lambda qid, proposed: conflicts)
    monkeypatch.setattr("converter.wikidata.reconciler.WikidataReconciler", lambda: reconciler)

    result = uploader.upload_item(item)

    assert result.status == "blocked"
    assert "CREATE_IDENTITY" in result.message
    assert "needs review" in result.message
    writes.assert_not_called()


def test_microfilm_without_942_cannot_supply_a_current_shelfmark() -> None:
    from app.pipeline.marc_ingest import prepare_record_for_pipeline
    from converter.wikidata.item_builder import WikidataItemBuilder

    record = prepare_record_for_pipeline(
        {
            "001": "990001680850205171",
            "245$a": "אגרות ביידיש",
            "090$a": "F 39896",
        }
    )

    item = WikidataItemBuilder().build_manuscript_item(record)

    assert not [statement for statement in item.statements if statement.property_id == "P217"]
    assert all("F 39896" not in label for label in item.labels.values())
    assert record["090$a"] == "F 39896"


def test_index_person_url_requires_the_reviewed_id_and_manuscript() -> None:
    from converter.wikidata.reviewed_scribe_authority import reviewed_scribe_authority_urls

    item = _catalogue_scribe()
    item.records = ["990001285000205171"]
    item.labels["he"] = "נתן נטע בן משה נפתלי הירש המרשלק"
    item.statements[-1].value = "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend"

    urls = reviewed_scribe_authority_urls(item)
    assert "https://cja.huji.ac.il/browser.php?id=660&f=maker_name&mode=treefriend" in urls
    assert item.statements[-1].value in urls
    assert not any(issue.code == "NO_IDENTIFIER" for issue in validate_item(item))

    item.statements[-1].value = "https://cja.huji.ac.il/?f=maker_name&id=661&mode=treefriend"
    assert not reviewed_scribe_authority_urls(item)
    assert any(issue.code == "NO_IDENTIFIER" for issue in validate_item(item))


def test_catalogue_duplicate_lookup_checks_variants_in_bounded_batches() -> None:
    from converter.wikidata.reconciler import PersonIdentityUncertainError, WikidataReconciler

    reconciler = WikidataReconciler()
    item_url = "http://www.wikidata.org/entity/Q456"
    lookup = Mock(side_effect=[[], [{"item": {"value": item_url}}]])
    reconciler._query = lookup
    urls = tuple(f"https://teman.org.il/content/{value}" for value in range(1000, 1065))
    cja = "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend"

    assert reconciler.person_creation_source_candidates((*urls, cja)) == ("Q456",)
    assert lookup.call_count == 2
    assert cja in lookup.call_args_list[1].args[0]
    assert urls[64] not in lookup.call_args_list[0].args[0]
    with pytest.raises(PersonIdentityUncertainError):
        reconciler.person_creation_source_candidates((cja + "> }",))
