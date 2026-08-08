"""Export-36 / Rule W-176 — designation labels keep only the 245 as alias."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder


class TestDesignationAliasRestrict:
    def test_second_contained_work_alias_dropped(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "990001360630205171",
            "title": "פרוש התורה",
            "shelfmark": "F 1",
            "holding_institution": "British Library",
            "variant_titles": ["זהר"],
            "related_works": [{"title": "זהר", "approved": True}],
            "extent": "10 דף",
        })
        he_aliases = item.aliases.get("he") or []
        assert "זהר" not in he_aliases
        assert any("פרוש התורה" in a for a in he_aliases)

    def test_245_alias_survives_under_designation(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "990000856010205171",
            "title": "קונטרס בית כנסת בקהילת קנדיאה",
            "shelfmark": "F 10117",
            "holding_institution": "Jewish Historical Institute",
            "variant_titles": ["תקון עזרא"],
            "extent": "74 דף",
        })
        # English label is the WPM designation; Hebrew may still hold 245 on
        # the legacy path — either way, only the 245 may remain as an alias.
        assert ", " in str(item.labels.get("en") or "")
        he_aliases = item.aliases.get("he") or []
        assert "תקון עזרא" not in he_aliases
        assert he_aliases == ["קונטרס בית כנסת בקהילת קנדיאה"]
