"""Rule W-162 — every projected claim has a channel, and "unsupported" names why.

Rule W-146 added P3342 / P1891 to ROLE_TO_PID and to the emitter but never added
a claim-source row, so 21 statements in run 48ba6c13 reached the judge as
`channels: ["unmapped"], supported: false`. The judge is instructed to treat that
as a claim it cannot trace — so a missing mapping read as missing data.
"""

from __future__ import annotations

from app.pipeline.wikidata_verify_evidence import (
    SUPPORT_CHANNEL_EMPTY,
    SUPPORT_NO_CHANNEL,
    SUPPORT_STRUCTURAL,
    SUPPORT_SUPPORTED,
    build_claim_sources,
    claim_channel_table_pids,
    projectable_property_ids,
    unmapped_projectable_pids,
)


class TestExhaustiveness:
    def test_every_projectable_pid_has_a_channel_row(self) -> None:
        """The mechanism that makes the next W-146-style addition impossible to forget."""
        assert unmapped_projectable_pids() == frozenset()

    def test_the_projectable_set_is_computed_not_hand_listed(self) -> None:
        """A hand-listed set is what went stale; this must follow ROLE_TO_PID."""
        from converter.wikidata.property_mapping import ROLE_TO_PID

        assert {"P3342", "P1891"} <= set(ROLE_TO_PID.values())
        assert {"P3342", "P1891"} <= projectable_property_ids()

    def test_qualifier_and_reference_pids_are_reviewed_data(self) -> None:
        from converter.wikidata.property_mapping import (
            QUALIFIER_ONLY_PIDS,
            REFERENCE_ONLY_PIDS,
        )

        # These never appear as a main snak, so they need no channel row.
        assert "P1319" in QUALIFIER_ONLY_PIDS
        assert "P248" in REFERENCE_ONLY_PIDS
        assert not (QUALIFIER_ONLY_PIDS & projectable_property_ids())
        assert not (REFERENCE_ONLY_PIDS & projectable_property_ids())

    def test_the_channel_tables_cover_the_person_links(self) -> None:
        assert {"P3342", "P1891", "P127"} <= claim_channel_table_pids()


def _item(pid: str, *, role: str, entity_type: str = "manuscript") -> dict:
    return {
        "local_id": "QDraft_MS_1",
        "entity_type": entity_type,
        "statements": [
            {"property_id": "P31", "value": "Q87167"},
            {"property_id": pid, "value": "__LOCAL:person::x"},
        ],
        "authority_evidence": [
            {"name": "גבאי, טוביה", "role": role, "mazal_id": "1"},
        ],
    }


class TestPersonLinkEvidence:
    def test_p3342_cites_the_role_that_produced_the_edge(self) -> None:
        rows = build_claim_sources(_item("P3342", role="former owner"), {}, [])
        row = rows["P3342"]
        assert "authority.person_link" in row["channels"]
        assert row["support_status"] == SUPPORT_SUPPORTED
        assert "גבאי, טוביה" in row["evidence"]["person_link"]

    def test_p1891_cites_the_signatory_role(self) -> None:
        rows = build_claim_sources(_item("P1891", role="signatory"), {}, [])
        assert rows["P1891"]["support_status"] == SUPPORT_SUPPORTED

    def test_a_hebrew_role_resolves_too(self) -> None:
        rows = build_claim_sources(_item("P3342", role="בעלים קודמים"), {}, [])
        assert rows["P3342"]["support_status"] == SUPPORT_SUPPORTED

    def test_the_marc_contributors_blob_can_supply_the_role_row(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [{"property_id": "P3342", "value": "__LOCAL:person::x"}],
        }
        marc = {"contributors": '{"name": "יכיני, אברהם", "role": "בעלים קודמים"}'}
        rows = build_claim_sources(item, marc, [])
        assert rows["P3342"]["support_status"] == SUPPORT_SUPPORTED


class TestSupportStatusIsDeOverloaded:
    def test_no_channel_mapped_is_distinguishable_from_an_empty_channel(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            # P9999 is in no channel table — the shape of the P3342 bug.
            "statements": [
                {"property_id": "P9999", "value": "x"},
                {"property_id": "P1104", "value": "12 ff."},
            ],
        }
        rows = build_claim_sources(item, {}, [])
        assert rows["P9999"]["support_status"] == SUPPORT_NO_CHANNEL
        assert "BUILD DEFECT" in rows["P9999"]["note"]
        # P1104 has a channel (marc.extent); this record's extent is empty.
        assert rows["P1104"]["support_status"] == SUPPORT_CHANNEL_EMPTY
        assert "sparsity" in rows["P1104"]["note"]

    def test_a_structural_claim_is_neither(self) -> None:
        rows = build_claim_sources(
            {"entity_type": "manuscript", "statements": [{"property_id": "P31", "value": "Q1"}]},
            {}, [],
        )
        assert rows["P31"]["support_status"] == SUPPORT_STRUCTURAL

    def test_supported_stays_for_backward_compatibility(self) -> None:
        rows = build_claim_sources(
            {
                "entity_type": "manuscript",
                "statements": [{"property_id": "P1104", "value": "12 ff."}],
            },
            {"extent": "12 ff."}, [],
        )
        assert rows["P1104"]["supported"] is True
        assert rows["P1104"]["support_status"] == SUPPORT_SUPPORTED

    def test_our_wikiproject_membership_is_structural_not_unsupported(self) -> None:
        rows = build_claim_sources(
            {
                "entity_type": "manuscript",
                "statements": [{"property_id": "P5008", "value": "Q104534511"}],
            },
            {}, [],
        )
        assert rows["P5008"]["support_status"] == SUPPORT_STRUCTURAL


class TestGateBlocksUntraceableClaims:
    def _report(self, serialised: list[dict], marc: list[dict]) -> dict:
        from app.pipeline.wikidata_export_quality_gate import (
            wikidata_export_quality_report,
        )

        return wikidata_export_quality_report(
            [], serialised_items=serialised, marc_records=marc,
        )

    def test_a_pid_with_no_channel_row_blocks_the_build(self) -> None:
        report = self._report(
            [{
                "local_id": "QDraft_MS_1", "entity_type": "manuscript",
                "statements": [{"property": "P9999", "value": "x"}],
                "record_ids": ["990000403370205171"],
            }],
            [{"_control_number": "990000403370205171"}],
        )
        assert any(
            f.startswith("CLAIM_WITHOUT_CHANNEL_ROW") for f in report["blocking"]
        )

    def test_an_empty_channel_is_informational_not_blocking(self) -> None:
        report = self._report(
            [{
                "local_id": "QDraft_MS_1", "entity_type": "manuscript",
                "statements": [{"property": "P1104", "value": "12 ff."}],
                "record_ids": ["990000403370205171"],
            }],
            [{"_control_number": "990000403370205171"}],
        )
        assert not any("CLAIM_WITHOUT" in f for f in report["blocking"])
        assert any(f.startswith("claim_channel_empty") for f in report["informational"])

    def test_the_person_link_pids_no_longer_block(self) -> None:
        """The exact 21-row regression from run 48ba6c13."""
        report = self._report(
            [{
                "local_id": "QDraft_MS_1", "entity_type": "manuscript",
                "statements": [
                    {"property": "P3342", "value": "__LOCAL:person::x"},
                    {"property": "P1891", "value": "__LOCAL:person::y"},
                ],
                "authority_evidence": [
                    {"name": "א", "role": "former owner"},
                    {"name": "ב", "role": "signatory"},
                ],
                "record_ids": ["990000403370205171"],
            }],
            [{"_control_number": "990000403370205171"}],
        )
        assert not any("CLAIM_WITHOUT" in f for f in report["blocking"])


class TestP921CitesTheChannelItCameFrom:
    """Rule W-162 — a canonical citation is not a subject heading.

    P921 cited only `marc.subjects`, so a value derived from
    `canonical_references` (Bible book / Talmud tractate → QID) was evidenced by
    whatever happened to be in 650/600. The judge was shown a PERSON heading as
    the evidence for "main subject = Exodus" and correctly called it unsupported —
    19 partials in run 48ba6c13's rebuild turned on P921.
    """

    def test_p921_names_both_channels(self) -> None:
        from app.pipeline.wikidata_verify_evidence import CLAIM_SOURCE_SLICES

        assert set(CLAIM_SOURCE_SLICES["P921"]) == {"subjects", "canonical_references"}

    def test_a_canonical_reference_supports_p921(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [{"property_id": "P921", "value": "Q9190"}],
        }
        marc = {"canonical_references": "Bible / Exodus"}
        row = build_claim_sources(item, marc, [])["P921"]
        assert row["support_status"] == SUPPORT_SUPPORTED
        assert "marc.canonical_references" in row["channels"]

    def test_a_subject_heading_still_supports_p921(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [{"property_id": "P921", "value": "Q123006"}],
        }
        row = build_claim_sources(item, {"subjects": "Cabala"}, [])["P921"]
        assert row["support_status"] == SUPPORT_SUPPORTED

    def test_neither_channel_reads_as_sparse_not_as_a_build_defect(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [{"property_id": "P921", "value": "Q9190"}],
        }
        assert build_claim_sources(item, {}, [])["P921"]["support_status"] == (
            SUPPORT_CHANNEL_EMPTY
        )
