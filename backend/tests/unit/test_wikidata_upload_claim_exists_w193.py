"""Exists-check quantity/time equality and BCE time encoding (Rule W-193)."""

from __future__ import annotations

from types import SimpleNamespace

from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.property_mapping import (
    date_to_wikidata,
    format_wikidata_time,
    repair_wikidata_time,
    wikidata_time_year,
)
from converter.wikidata.uploader import (
    native_value_matches_wiki,
    quantity_amounts_equal,
)


def _quantity_item(pid: str, amount: str) -> SimpleNamespace:
    claim = SimpleNamespace(
        mainsnak=SimpleNamespace(
            datavalue={"value": {"amount": amount, "unit": "1"}},
        ),
    )
    return SimpleNamespace(
        claims=SimpleNamespace(get=lambda p, _pid=pid, _claim=claim: [_claim] if p == _pid else []),
    )


def test_format_wikidata_time_pads_bce_without_plus_minus_glitch() -> None:
    assert format_wikidata_time(-199) == "-0199-00-00T00:00:00Z"
    assert format_wikidata_time(199) == "+0199-00-00T00:00:00Z"
    assert format_wikidata_time(1697) == "+1697-00-00T00:00:00Z"
    assert "+-" not in format_wikidata_time(-199)


def test_date_to_wikidata_negative_year_is_valid_wikibase_time() -> None:
    result = date_to_wikidata({"year": -199})
    assert result is not None
    assert result[0] == "-0199-00-00T00:00:00Z"
    assert "+-" not in result[0]


def test_repair_wikidata_time_fixes_concatenated_plus_on_bce() -> None:
    assert repair_wikidata_time("+-199-00-00T00:00:00Z") == "-0199-00-00T00:00:00Z"
    assert repair_wikidata_time("+1697-00-00T00:00:00Z") == "+1697-00-00T00:00:00Z"


def test_wikidata_time_year_reads_padded_bce() -> None:
    assert wikidata_time_year("-0199-00-00T00:00:00Z") == -199
    assert wikidata_time_year("+1697-00-00T00:00:00Z") == 1697


def test_quantity_amounts_equal_wikibase_plus_and_float() -> None:
    assert quantity_amounts_equal(11, "+11")
    assert quantity_amounts_equal(95.0, "+95")
    assert quantity_amounts_equal("11.0", "+11")
    assert not quantity_amounts_equal(11, "+1")


def test_claim_exists_treats_plus_amount_as_already_on_item() -> None:
    from converter.wikidata.uploader import WikidataUploader

    up = WikidataUploader(
        token="user@bot:xxxxxxxxxxxxxxxx",
        is_test=True,
        allow_live=False,
    )
    wbi_item = _quantity_item("P97584", "+11")
    stmt = WikidataStatement(
        property_id="P97584",
        value=11,
        value_type="quantity",
    )
    assert up._claim_exists(wbi_item, stmt)
    missing = WikidataStatement(
        property_id="P97584",
        value=12,
        value_type="quantity",
    )
    assert not up._claim_exists(wbi_item, missing)


def test_native_value_matches_wiki_time_after_repair() -> None:
    stmt = WikidataStatement(
        property_id="P571",
        value="+-199-00-00T00:00:00Z",
        value_type="time",
    )
    assert native_value_matches_wiki(stmt, "-0199-00-00T00:00:00Z")


def test_build_claim_accepts_concatenated_bce_time() -> None:
    from converter.wikidata.uploader import WikidataUploader

    up = WikidataUploader(
        token="user@bot:xxxxxxxxxxxxxxxx",
        is_test=True,
        allow_live=False,
    )
    stmt = WikidataStatement(
        property_id="P771",
        value="+-199-00-00T00:00:00Z",
        value_type="time",
        precision=9,
    )
    claim = up._build_claim(stmt)
    assert claim is not None
    assert claim.mainsnak.datavalue["value"]["time"] == "-0199-00-00T00:00:00Z"
