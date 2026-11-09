"""QuickStatements must not silently change or inject claims."""

import pytest

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.quickstatements import QuickStatementsExporter


def _item(statement: WikidataStatement, *, existing: bool = True) -> WikidataItem:
    return WikidataItem(
        local_id="ms:1", entity_type="manuscript", existing_qid="Q42" if existing else None,
        labels={"en": "Manuscript"}, statements=[statement],
    )


@pytest.mark.parametrize("type_key", ["type", "value_type"])
@pytest.mark.parametrize("precision", [9, 11])
def test_time_qualifier_preserves_type_and_precision(type_key: str, precision: int) -> None:
    statement = WikidataStatement("P31", "Q5", "item", qualifiers=[{
        "property_id": "P585", "value": "+2020-01-02T00:00:00Z",
        type_key: "time", "precision": precision,
    }])
    output = QuickStatementsExporter().export_item(_item(statement))
    assert f"P585\t+2020-01-02T00:00:00Z/{precision}" in output
    assert '"+2020-01-02T00:00:00Z"' not in output


@pytest.mark.parametrize("value,precision", [
    ("+2020-00-00T00:00:00Z", 0),
    ("+2020-01-02T13:45:06Z", 12),
    ("+2020-01-02T13:45:06Z", 14),
])
def test_time_qualifier_supports_quickstatements_precision_range(
    value: str, precision: int,
) -> None:
    statement = WikidataStatement("P31", "Q5", "item", qualifiers=[{
        "property": "P585", "value": value, "value_type": "time", "precision": precision,
    }])
    output = QuickStatementsExporter().export_item(_item(statement))
    assert f"P585\t{value}/{precision}" in output


@pytest.mark.parametrize("rank", ["preferred", "deprecated", "normal\nQ9\tP31\tQ5"])
def test_non_normal_rank_excludes_the_statement(rank: str) -> None:
    statement = WikidataStatement("P31", "Q5", "item", rank=rank)
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output
    assert "rank" in output.lower()
    assert "Q42\tP31\tQ5" not in output
    assert "Q9\tP31\tQ5" not in output
    assert "incomplete" in output.lower()


@pytest.mark.parametrize("value_type", ["item", "wikibase-item", "wikibase-entityid"])
def test_unresolved_main_value_has_visible_diagnostic(value_type: str) -> None:
    item = _item(WikidataStatement("P50", "__LOCAL:author", value_type), existing=False)
    output = QuickStatementsExporter().export([item])
    assert "Statement omitted" in output
    assert "local" in output.lower()
    assert "__LOCAL:author" not in output
    assert "LAST\tP50" not in output
    assert "incomplete" in output.lower()


@pytest.mark.parametrize("value_type", ["item", "wikibase-item", "wikibase-entityid"])
def test_unresolved_qualifier_excludes_the_whole_statement(value_type: str) -> None:
    statement = WikidataStatement("P127", "Q42", "item", qualifiers=[{
        "property": "P3831", "value": "__LOCAL:author", "value_type": value_type,
    }])
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output
    assert "local" in output.lower()
    assert "__LOCAL:author" not in output
    assert "Q42\tP127" not in output


def test_nested_local_qualifier_is_reported_without_leaking_the_target() -> None:
    statement = WikidataStatement("P127", "Q42", "item", qualifiers=[{
        "property": "P3831", "datatype": "wikibase-item", "snaktype": "value",
        "datavalue": {"type": "wikibase-entityid", "value": {"id": "__LOCAL:author"}},
    }])
    output = QuickStatementsExporter().export([_item(statement)])
    assert "unresolved local qualifier" in output
    assert "__LOCAL:author" not in output and "Q42\tP127" not in output


@pytest.mark.parametrize("qualifier", [
    {"property": "P585\nQ9\tP31\tQ5", "value": "+2020-01-02T00:00:00Z", "type": "time"},
    {"property": "P585", "value": "+2020-01-02T00:00:00Z\nQ9\tP31\tQ5", "type": "time"},
    {"property": "P585", "value": "+2020-01-02T00:00:00Z", "type": "time",
     "precision": "11\nQ9\tP31\tQ5"},
    {"property": "P585", "value": "+2020-01-02T00:00:00Z", "type": "time",
     "value_type": "string", "precision": 11},
    {"property": "P3831", "value": "Q1\nQ9\tP31\tQ5", "type": "item"},
    {"property": "P1932", "value": "text\nQ9\tP31\tQ5", "type": "string"},
    {"property": "P1932", "value": "text", "type": "unsupported"},
    {"property": "P585", "value": "+2020-01-02T00:00:00Z", "type": "time",
     "calendarmodel": "http://www.wikidata.org/entity/Q1985786"},
])
def test_unsafe_or_unsupported_qualifier_excludes_the_statement(
    qualifier: dict[str, object],
) -> None:
    statement = WikidataStatement("P31", "Q5", "item", qualifiers=[qualifier])
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output
    assert "Q42\tP31\tQ5" not in output
    assert "Q9\tP31\tQ5" not in output


@pytest.mark.parametrize("field,value", [
    ("property_id", "P31\nQ9\tP31\tQ5"),
    ("value", "Q5\nQ9\tP31\tQ5"),
    ("value_type", "item\nQ9\tP31\tQ5"),
])
def test_unsafe_main_fields_never_inject_a_command(field: str, value: str) -> None:
    statement = WikidataStatement("P31", "Q5", "item")
    setattr(statement, field, value)
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output
    assert "Q9\tP31\tQ5" not in output


def test_known_local_target_does_not_prevent_a_studio_build() -> None:
    from app.pipeline.wikidata_studio import finish_native_items

    person = WikidataItem(
        local_id="author", entity_type="person", existing_qid="Q900",
        labels={"en": "Author"}, statements=[WikidataStatement("P31", "Q5", "item")],
    )
    work = WikidataItem(
        local_id="work:1", entity_type="work", labels={"en": "Work"},
        statements=[WikidataStatement("P50", "__LOCAL:author", "item")],
    )
    result = finish_native_items([work, person])
    assert "Statement omitted" in result["quickstatements"]
    assert "__LOCAL:author" not in result["quickstatements"]
    work_row = next(item for item in result["items"] if item["local_id"] == "work:1")
    assert work_row["statements"][0]["value"] == "__LOCAL:author"


def test_supported_qualifier_types_keep_their_values() -> None:
    statement = WikidataStatement("P31", "Q5", "item", qualifiers=[
        {"property": "P3831", "value": "Q42", "value_type": "wikibase-item"},
        {"property": "P1104", "value": "12.5", "type": "quantity", "unit": "Q174728"},
        {"property": "P1476", "value": 'A "quoted" title', "type": "monolingualtext",
         "language": "en"},
    ])
    output = QuickStatementsExporter().export_item(_item(statement))
    assert '\tP3831\tQ42\tP1104\t12.5U174728\tP1476\ten:"A "quoted" title"' in output
    assert "omitted" not in output


def test_live_quantity_unit_uri_is_preserved_in_quickstatements() -> None:
    statement = WikidataStatement(
        "P1104", "12", "quantity", unit="http://www.wikidata.org/entity/Q174728",
    )
    assert "Q42\tP1104\t12U174728" in QuickStatementsExporter().export_item(_item(statement))


@pytest.mark.parametrize("value", ["unsafe||Q9|P31|Q5", "unsafe\x7ftext", " unsafe"])
def test_unsafe_text_is_excluded_without_rewriting(value: str) -> None:
    statement = WikidataStatement("P2093", value, "string")
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output and "incomplete" in output.lower()
    assert 'Q42\tP2093\t"' not in output
    assert "Q9\tP31\tQ5" not in output


def test_unsafe_item_metadata_cannot_inject_a_command() -> None:
    item = _item(WikidataStatement("P31", "Q5", "item"))
    item.labels = {"en\nQ9\tP31\tQ5": "Name", "en": "Text\nQ9\tP31\tQ5"}
    output = QuickStatementsExporter().export([item])
    assert "Label omitted" in output and "incomplete" in output.lower()
    assert "Q9\tP31\tQ5" not in output


def test_invalid_alias_shape_is_not_split_into_commands() -> None:
    item = _item(WikidataStatement("P31", "Q5", "item"))
    item.aliases = {"en": "Alice"}
    output = QuickStatementsExporter().export([item])
    assert "Alias omitted: invalid alias list" in output
    assert "\tAen\t" not in output and "\tP31\tQ5" not in output


def test_existing_person_and_unsupported_type_are_not_silently_dropped() -> None:
    existing = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    unknown = WikidataItem(local_id="other:1", entity_type="other")
    output = QuickStatementsExporter().export([existing, unknown])
    assert "existing person is not exported" in output
    assert "unsupported entity type" in output
    assert "incomplete" in output.lower()


def test_unsupported_duplicate_qualifier_sets_exclude_both_claims() -> None:
    statements = [
        WikidataStatement("P50", "Q5", "item", qualifiers=[{
            "property": "P1932", "value": name, "type": "string",
        }])
        for name in ("First", "Second")
    ]
    item = _item(statements[0])
    item.statements = statements
    output = QuickStatementsExporter().export([item])
    assert output.count("Statement omitted") == 2
    assert "Q42\tP50\tQ5" not in output
    assert "conflicting statement qualifiers" in output


def test_normal_claim_is_not_exported_next_to_same_value_with_unsupported_rank() -> None:
    item = _item(WikidataStatement("P31", "Q5", "item"))
    item.statements.append(WikidataStatement("P31", "Q5", "item", rank="preferred"))
    output = QuickStatementsExporter().export([item])
    assert output.count("Statement omitted") == 2
    assert "Q42\tP31\tQ5" not in output


@pytest.mark.parametrize("existing", [False, True])
def test_an_omitted_statement_cannot_produce_a_partial_item(existing: bool) -> None:
    item = _item(WikidataStatement("P31", "Q5", "item"), existing=existing)
    item.statements.append(WikidataStatement("P50", "__LOCAL:author", "item"))
    output = QuickStatementsExporter().export([item])
    assert "Statement omitted" in output and "incomplete" in output.lower()
    assert "CREATE" not in output
    assert "\tP31\tQ5" not in output
    assert "\tLen\t" not in output


def test_unknown_value_on_create_is_omitted() -> None:
    item = _item(WikidataStatement("P31", None, "somevalue"), existing=False)
    output = QuickStatementsExporter().export([item])
    assert "Statement omitted" in output
    assert "LAST\tP31\tsomevalue" not in output


@pytest.mark.parametrize("statement", [
    WikidataStatement("P571", "+2020-01-02T00:00:00Z\nQ9\tP31\tQ5", "time"),
    WikidataStatement("P1104", "12\nQ9\tP31\tQ5", "quantity"),
    WikidataStatement("P1104", "12", "quantity", unit="Q1\nQ9\tP31\tQ5"),
    WikidataStatement("P1476", "Title", "monolingualtext", language="en\nQ9\tP31\tQ5"),
])
def test_unsafe_unquoted_main_values_do_not_inject_commands(statement: WikidataStatement) -> None:
    output = QuickStatementsExporter().export([_item(statement)])
    assert "Statement omitted" in output
    assert "Q9\tP31\tQ5" not in output


def test_invalid_existing_target_is_not_exported() -> None:
    item = _item(WikidataStatement("P31", "Q5", "item"))
    item.existing_qid = "Q42\nQ9\tP31\tQ5"
    output = QuickStatementsExporter().export([item])
    assert "Item omitted" in output and "incomplete" in output.lower()
    assert "Q9\tP31\tQ5" not in output


def test_duplicate_qualifiers_are_not_silently_collapsed() -> None:
    qualifier = {"property": "P1932", "value": "Name", "type": "string"}
    statement = WikidataStatement("P50", "Q1", "item", qualifiers=[qualifier, qualifier])
    output = QuickStatementsExporter().export([_item(statement)])
    assert "duplicate qualifiers cannot be preserved" in output
    assert "Q42\tP50\tQ1" not in output


def test_empty_item_is_not_silently_dropped() -> None:
    item = WikidataItem(local_id="ms:empty", entity_type="manuscript")
    output = QuickStatementsExporter().export([item])
    assert "Item omitted" in output and "incomplete" in output.lower()
    assert "CREATE" not in output


def test_empty_export_is_explicit_and_safe() -> None:
    calls: list[tuple[int, int]] = []
    output = QuickStatementsExporter().export(
        [], on_progress=lambda done, total: calls.append((done, total)),
    )
    assert output == "/* No items to export */"
    assert "CREATE" not in output and calls == [(0, 1)]


def test_empty_existing_item_reports_its_identifier() -> None:
    item = WikidataItem(entity_type="manuscript", existing_qid="Q42")
    assert "Item omitted: Q42; no exportable data" in QuickStatementsExporter().export_item(item)


def test_unsupported_reference_omits_only_its_item_with_local_id() -> None:
    source = [{"property": "P854", "value": "12", "type": "quantity"}]
    statement = WikidataStatement("P31", "Q5", "item", references=source,
                                  reference_blocks=[source])
    bad = _item(statement, existing=False)
    good = _item(WikidataStatement("P31", "Q5", "item"), existing=False)
    bad.local_id, good.local_id = "ms:bad", "ms:good"
    output = QuickStatementsExporter().export([bad, good])
    assert "Item omitted: ms:bad" in output
    assert "Statement omitted: invalid or unsupported reference" in output
    assert "unsupported type" in output and "[P31]" in output
    assert output.count("\nCREATE\n") == 1
    assert output.count("LAST\tP31\tQ5") == 1
    assert "incomplete" in output.lower()


def test_reference_validation_still_rejects_unsupported_metadata() -> None:
    from converter.wikidata.quickstatements import validate_source_references

    source = [{"property": "P854", "value": "12", "type": "quantity"}]
    with pytest.raises(ValueError, match="Unsupported reference type"):
        validate_source_references(source, [source])


def test_reference_error_text_cannot_escape_diagnostics() -> None:
    source = [{
        "property": "P854", "value": "x", "type": "quantity*/\r\nQ9\tP31\tQ5",
    }]
    item = _item(WikidataStatement("P31", "Q5", "item", references=source,
                                   reference_blocks=[source]), existing=False)
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: ms:1" in output and "unsupported type" in output
    assert "Q9\tP31\tQ5" not in output and "\r" not in output
    assert all(line.startswith("/* ") and line.endswith(" */") for line in output.splitlines())


def test_unsupported_reference_does_not_prevent_a_studio_build() -> None:
    from app.pipeline.wikidata_studio import finish_native_items

    source = [{"property": "P854", "value": "12", "type": "quantity"}]
    statement = WikidataStatement("P31", "Q5", "item", references=source,
                                  reference_blocks=[source])
    item = _item(statement, existing=False)
    result = finish_native_items([item])
    assert "Item omitted: ms:1" in result["quickstatements"]
    assert "invalid or unsupported reference" in result["quickstatements"]
    assert "\nCREATE\n" not in result["quickstatements"]
    actual = result["items"][0]["statements"][0]["reference_blocks"][0][0]
    assert {key: actual[key] for key in source[0]} == source[0]
    assert item.statements[0].reference_blocks == [source]


@pytest.mark.parametrize("bad_id", [
    "work:bad*/\r\nQ9\tP31\tQ5||CREATE",
    "work:bad\u202eQ9",
    "work:" + "x" * 300,
])
def test_omission_id_cannot_escape_its_comment(bad_id: str) -> None:
    item = _item(WikidataStatement("P50", "__LOCAL:author", "item"), existing=False)
    item.local_id = bad_id
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: work:" in output
    assert "id-sha256:" in output
    assert "Q9\tP31\tQ5" not in output
    assert "\r" not in output and "\t" not in output and "||" not in output
    assert len(output.splitlines()) == 2
    assert all(line.startswith("/* ") and line.endswith(" */") for line in output.splitlines())
    assert len(output.splitlines()[0]) < 180


def test_every_item_omission_identifies_the_item() -> None:
    rank = _item(WikidataStatement("P31", "Q5", "item", rank="preferred"))
    rank.local_id = "ms:rank"
    person = WikidataItem(local_id="person:123", entity_type="person", existing_qid="Q42")
    unknown = WikidataItem(local_id="other:456", entity_type="other")
    output = QuickStatementsExporter().export([rank, person, unknown])
    assert "Item omitted: ms:rank" in output
    assert "Item omitted: person:123" in output
    assert "Item omitted: other:456" in output
