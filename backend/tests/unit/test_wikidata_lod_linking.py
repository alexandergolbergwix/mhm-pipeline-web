"""Rule W-146 — created items MUST be reachable from one another."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder


def _build(record: dict[str, object]) -> list[object]:
    result = WikidataItemBuilder().build_all([record])
    return result["items"] if isinstance(result, dict) else result


def _edges(items: list[object], pid: str) -> list[str]:
    out: list[str] = []
    for item in items:
        et = item.get("entity_type") if isinstance(item, dict) else item.entity_type
        if et != "manuscript":
            continue
        statements = item.get("statements") if isinstance(item, dict) else item.statements
        for s in statements:
            prop = s.get("property_id") if isinstance(s, dict) else s.property_id
            value = s.get("value") if isinstance(s, dict) else s.value
            if prop == pid:
                out.append(str(value))
    return out


def _person(name: str, role: str, viaf: str) -> dict[str, object]:
    return {
        "name": name, "role": role, "approved": True, "entity_kind": "person",
        "viaf_uri": f"https://viaf.org/viaf/{viaf}",
    }


class TestApprovedRolesBecomeEdges:
    """49 former-owner, 27 mentioned and 13 signatory rows produced nothing."""

    def test_former_owner_becomes_a_significant_person_edge(self) -> None:
        items = _build({
            "_control_number": "LOD-1", "title": "כתב יד",
            "marc_authority_matches": [_person("Gaster, Moses", "former owner", "12345")],
        })
        assert _edges(items, "P3342") == ["__LOCAL:viaf:12345"]

    def test_a_former_owner_is_still_never_a_current_owner(self) -> None:
        # The whole reason it lands on P3342: an unqualified P127 asserts current
        # ownership, which we cannot support without P580/P582 date evidence.
        items = _build({
            "_control_number": "LOD-2", "title": "כתב יד",
            "marc_authority_matches": [_person("Gaster, Moses", "former owner", "12345")],
        })
        assert _edges(items, "P127") == []

    def test_signatory_and_mentioned_become_edges(self) -> None:
        items = _build({
            "_control_number": "LOD-3", "title": "כתב יד",
            "marc_authority_matches": [
                _person("Levi, Shimon", "signatory", "999"),
                _person("Cohen, Yosef", "mentioned", "777"),
            ],
        })
        assert _edges(items, "P1891") == ["__LOCAL:viaf:999"]
        assert _edges(items, "P3342") == ["__LOCAL:viaf:777"]

    def test_a_seller_is_never_linked_as_an_owner_or_a_person(self) -> None:
        # The data model forbids modelling auction houses as P127 owners.
        items = _build({
            "_control_number": "LOD-4", "title": "כתב יד",
            "marc_authority_matches": [_person("Auction House", "seller", "1")],
        })
        assert _edges(items, "P127") == []
        assert _edges(items, "P3342") == []

    def test_a_censor_is_never_linked(self) -> None:
        items = _build({
            "_control_number": "LOD-5", "title": "כתב יד",
            "marc_authority_matches": [_person("Censor Guy", "censor", "2")],
        })
        assert _edges(items, "P3342") == []

    def test_an_identifierless_person_is_not_created_for_the_sake_of_an_edge(self) -> None:
        # Wikidata:Notability. An edge is not worth an item the community deletes.
        items = _build({
            "_control_number": "LOD-6", "title": "כתב יד",
            "marc_authority_matches": [{
                "name": "Nobody Known", "role": "former owner",
                "approved": True, "entity_kind": "person", "viaf_uri": "",
            }],
        })
        assert _edges(items, "P3342") == []
        assert not [
            i for i in items
            if (i.get("entity_type") if isinstance(i, dict) else i.entity_type) == "person"
        ]


class TestCanonicalCarriesAuthorityMatches:
    """Rule W-146: the canonical context dropped the rows the builder reads."""

    def test_records_are_stamped_in_the_desktop_shape(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import canonical_studio_context

        context = canonical_studio_context(
            marc_records=[{"_control_number": "990001882630205171", "title": "כתב יד"}],
            approved_matches=[{
                "control_number": '"990001882630205171"',
                "entity_text": "עדני, שלמה בן ישועה,",
                "role": 'former owner"',
                "entity_kind": "person",
                "viaf_id": "12345",
                "mazal_id": "",
                "approved": True,
                "payload": {},
            }],
        )
        record = context.marc_by_cn["990001882630205171"]
        rows = record["marc_authority_matches"]
        assert len(rows) == 1
        # `name`/`viaf_uri`, not the raw `entity_text`/`viaf_id`: the raw row has
        # no `name`, so every person failed the notability check for want of an id.
        assert rows[0]["name"] == "עדני, שלמה בן ישועה"
        assert rows[0]["viaf_uri"] == "https://viaf.org/viaf/12345"
        assert rows[0]["role"] == "former owner"

    def test_a_record_with_no_matches_gets_an_empty_list_not_a_missing_key(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import canonical_studio_context

        context = canonical_studio_context(
            marc_records=[{"_control_number": "990000000000000001", "title": "x"}],
            approved_matches=[],
        )
        assert context.marc_by_cn["990000000000000001"]["marc_authority_matches"] == []


def test_hard_rejected_authority_dates_do_not_reach_person_item() -> None:
    items = _build({
        "_control_number": "DATE-CONFLICT",
        "title": "כתב יד",
        "dates": {"year": 1672},
        "marc_authority_matches": [{
            "name": "Modern Person",
            "role": "signatory",
            "approved": True,
            "entity_kind": "person",
            "mazal_id": "987000000000000001",
            "birth_year": 1956,
            "guard_flags": ["modern_person"],
        }],
    })

    person = next(item for item in items if item.entity_type == "person")
    assert all(statement.property_id not in {"P569", "P570"} for statement in person.statements)
