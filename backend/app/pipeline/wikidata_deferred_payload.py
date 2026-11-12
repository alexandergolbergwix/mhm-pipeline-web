"""Build the source payload for the second upload pass."""

from dataclasses import replace

from converter.wikidata.item_models import (
    WikidataItem,
    WikidataStatement,
    matching_qualifiers,
    merge_statement_references,
)
from converter.wikidata.uploader import resolve_statement_locals


def _same_claim(left: WikidataStatement, right: WikidataStatement) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("property_id", "value", "value_type", "precision", "language", "unit")
    ) and matching_qualifiers(left, right)


def build_deferred_link_payload(
    source: WikidataItem,
    *,
    source_qid: str,
    resolved: list[WikidataStatement],
    known_qids: dict[str, str],
    is_test: bool,
) -> WikidataItem:
    if not any(s.property_id == "P31" for s in source.statements):
        raise ValueError("The deferred source must be a complete native item with P31.")
    statements: list[WikidataStatement] = []
    for original in source.statements:
        statement, pending = resolve_statement_locals(
            original, known_qids, test_namespace=is_test,
        )
        if not pending:
            statements.append(statement)
    for original in resolved:
        statement, pending = resolve_statement_locals(
            original, known_qids, test_namespace=is_test,
        )
        if pending:
            raise ValueError("The deferred payload still contains an unresolved local target.")
        existing = next((s for s in statements if _same_claim(s, statement)), None)
        if existing is None:
            statements.append(statement)
        else:
            merge_statement_references(existing, statement)
    return replace(source, existing_qid=source_qid, statements=statements)
