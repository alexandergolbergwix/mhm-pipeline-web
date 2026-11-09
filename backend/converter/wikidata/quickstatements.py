"""Export Wikidata items as QuickStatements v2 format.

QuickStatements is a batch-editing tool for Wikidata that accepts
tab-separated commands. This module generates QuickStatements text
for dry-run review and manual upload via the web tool.

See: https://www.wikidata.org/wiki/Help:QuickStatements
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import re
import unicodedata
from collections.abc import Callable
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)


def reference_data(
    references: object, reference_blocks: object,
) -> tuple[list[dict[str, object]], list[list[dict[str, object]]]]:
    """Validate reference shapes and return independent compatibility and block views."""
    if not isinstance(references, list) or any(
        not isinstance(snak, dict) or any(not isinstance(key, str) for key in snak)
        for snak in references
    ):
        raise ValueError("references must be a list of objects")
    if not isinstance(reference_blocks, list) or any(
        not isinstance(block, list)
        or any(not isinstance(snak, dict) or any(not isinstance(key, str) for key in snak)
               for snak in block)
        for block in reference_blocks
    ):
        raise ValueError("reference_blocks must be a list of lists of objects")
    flat = deepcopy(references)
    if reference_blocks:
        from converter.wikidata.item_models import snak_identity  # noqa: PLC0415

        flat_semantics = {snak_identity(snak) for snak in references}
        block_semantics = {
            snak_identity(snak) for block in reference_blocks for snak in block
        }
        if flat_semantics != block_semantics:
            raise ValueError("references do not match reference_blocks; rebuild from source")
        flat = []
        seen: set[str] = set()
        for block in reference_blocks:
            for snak in block:
                identity = snak_identity(snak)
                if identity not in seen:
                    seen.add(identity)
                    flat.append(deepcopy(snak))
    blocks = deepcopy(reference_blocks)
    return flat, blocks


def validate_reference_time(
    value: object, precision: object, *, calendarmodel: object = None,
) -> None:
    if not isinstance(value, str):
        raise ValueError("Invalid reference time or precision")
    match = re.fullmatch(
        r"([+-])(\d{4,16})-(\d{2})-(\d{2})T00:00:00Z", value,
    )
    if not match or type(precision) is not int or not 1 <= precision <= 11:
        raise ValueError("Invalid reference time or precision")
    if calendarmodel is not None and (
        not isinstance(calendarmodel, str)
        or re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", calendarmodel) is None
    ):
        raise ValueError("Invalid reference calendar")
    sign, year, month, day = match.groups()
    year_i = int(year) * (-1 if sign == "-" else 1)
    month_i, day_i = int(month), int(day)
    if (month_i > 12 or day_i > 31 or (month_i == 0 and day_i != 0)
            or (precision >= 10 and month_i == 0) or (precision == 11 and day_i == 0)):
        raise ValueError("Invalid reference time or precision")
    if month_i and day_i:
        if calendarmodel is None or str(calendarmodel).endswith("/Q1985727"):
            max_day = calendar.monthrange(year_i, month_i)[1]
        elif str(calendarmodel).endswith("/Q1985786"):
            max_day = (
                31, 29 if year_i % 4 == 0 else 28, 31, 30, 31, 30,
                31, 31, 30, 31, 30, 31,
            )[month_i - 1]
        else:
            max_day = 31
        if day_i > max_day:
            raise ValueError("Invalid reference time or precision")


def validate_source_references(
    references: object, reference_blocks: object, *, allow_local: bool = False,
    require_proven_blocks: bool = False,
) -> None:
    flat, blocks = reference_data(references, reference_blocks)
    if require_proven_blocks and not blocks and len(flat) > 1:
        raise ValueError("Ambiguous flat references; rebuild from source with reference blocks")
    views = [references, *blocks] if blocks else ([flat] if flat else [])
    for block in views:
        if not block:
            raise ValueError("A reference block cannot be empty")
        for snak in block:
            if set(snak) - {
                "property", "property_id", "value", "type", "value_type", "precision",
                "property_label", "value_label", "value_namespace",
            }:
                raise ValueError("A reference contains unsupported metadata")
            prop = snak.get("property") or snak.get("property_id")
            value = snak.get("value")
            if any(key in snak and (not isinstance(snak[key], str) or not snak[key].strip())
                   for key in ("type", "value_type")):
                raise ValueError("A reference value type must be a non-empty string")
            value_type = snak.get("value_type") or snak.get("type") or "string"
            if "value_namespace" in snak and (
                snak["value_namespace"] != "wikidata"
                or value_type not in ("item", "wikibase-item", "wikibase-entityid")
                or not isinstance(value, str)
                or re.fullmatch(r"Q[1-9][0-9]*", value) is None
            ):
                raise ValueError("Invalid reference value namespace")
            if snak.get("property") and snak.get("property_id") and (
                snak["property"] != snak["property_id"]
            ):
                raise ValueError("A reference has conflicting properties")
            if snak.get("type") and snak.get("value_type"):
                if not isinstance(snak["type"], str) or not isinstance(snak["value_type"], str):
                    raise ValueError("A reference value type must be a string")
                types = {snak["type"], snak["value_type"]}
                if len(types) > 1 and not types <= {"item", "wikibase-item", "wikibase-entityid"}:
                    raise ValueError("A reference has conflicting value types")
            if "precision" in snak and value_type != "time":
                raise ValueError("Only a time reference may have precision")
            if not isinstance(prop, str) or not re.fullmatch(r"P[1-9][0-9]*", prop):
                raise ValueError("A reference requires a valid property")
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError("A reference requires a non-empty value without controls")
            if value_type in ("item", "wikibase-item", "wikibase-entityid"):
                if not re.fullmatch(r"Q[1-9][0-9]*", value) and not (
                    allow_local and re.fullmatch(r"__LOCAL:[^\s]+", value)
                ):
                    raise ValueError("A reference item must be a QID or a deferred local ID")
            elif value_type == "time":
                validate_reference_time(value, snak.get("precision", 11))
            elif value_type == "url":
                parsed = urlsplit(value)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise ValueError("A reference URL must use HTTP or HTTPS")
            elif value_type not in ("string", "external-id"):
                raise ValueError(f"Unsupported reference type: {value_type}")


def statement_reference_kwargs(raw: dict[str, object], statement_type: type) -> dict[str, object]:
    references, blocks = reference_data(raw.get("references", []), raw.get("reference_blocks", []))
    result: dict[str, object] = {"references": references}
    if "reference_blocks" in {field.name for field in fields(statement_type)}:
        result["reference_blocks"] = blocks
    elif blocks:
        raise ValueError("WikidataStatement does not support reference_blocks")
    return result


class _QuickStatementsOmissionError(ValueError):
    """A value cannot be represented without changing its meaning."""


def _escape_qs(value: object) -> str:
    """Validate a quoted value for the QuickStatements V1 command parser."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or "||" in value or any(unicodedata.category(char) in {"Cc", "Zl", "Zp"}
                                   for char in value)):
        raise _QuickStatementsOmissionError("unsafe or unsupported text")
    return value


def _quantity_value(value: object, unit: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _QuickStatementsOmissionError("unsupported quantity")
    amount = str(value)
    if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", amount):
        raise _QuickStatementsOmissionError("unsupported quantity")
    if unit in (None, "", "1"):
        return amount
    if not isinstance(unit, str):
        raise _QuickStatementsOmissionError("unsupported quantity unit")
    from converter.wikidata.test_wiki_compat import QUANTITY_UNIT_ALIASES  # noqa: PLC0415

    qid = QUANTITY_UNIT_ALIASES.get(unit, unit)
    if re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", qid):
        qid = qid.rsplit("/", 1)[1]
    if not re.fullmatch(r"Q[1-9][0-9]*", qid):
        raise _QuickStatementsOmissionError("unsupported quantity unit")
    return f"{amount}U{qid[1:]}"


def _language(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_-]{1,35}", value):
        raise _QuickStatementsOmissionError("unsupported language")
    return value


def _contains_local(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith("__LOCAL:")
    if isinstance(value, dict):
        return any(_contains_local(entry) for entry in value.values())
    if isinstance(value, list):
        return any(_contains_local(entry) for entry in value)
    return False


def _item_omission(item: WikidataItem, reason: str = "") -> str:
    identifier = getattr(item, "local_id", None) or getattr(item, "existing_qid", None)
    if not isinstance(identifier, str) or not identifier:
        safe = "unidentified"
    else:
        safe = "".join(char if char.isalnum() or char in "._:-" else "_" for char in identifier)
        if safe != identifier or len(safe) > 80:
            digest = hashlib.sha256(identifier.encode("utf-8", "surrogatepass")).hexdigest()[:16]
            safe = f"{safe[:80]} [id-sha256:{digest}]"
    suffix = f"; {reason}" if reason else ""
    return f"/* Item omitted: {safe}{suffix} */"


def _reference_omission(exc: Exception) -> str:
    message = str(exc)
    if message.startswith((
        "Unsupported reference type", "Unsupported QuickStatements reference type",
    )):
        detail = "unsupported type"
    elif "unsupported metadata" in message:
        detail = "unsupported metadata"
    elif "reference time" in message:
        detail = "invalid time or precision"
    elif "reference_blocks" in message or "reference block" in message:
        detail = "invalid reference blocks"
    elif "namespace" in message:
        detail = "invalid namespace"
    else:
        detail = "invalid reference"
    return f"invalid or unsupported reference ({detail})"


def _time_value(value: object, precision: object) -> str:
    if not isinstance(value, str) or type(precision) is not int or not 0 <= precision <= 14:
        raise _QuickStatementsOmissionError("unsupported time value")
    match = re.fullmatch(
        r"([+-])(\d{4,16})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z", value,
    )
    if not match:
        raise _QuickStatementsOmissionError("unsupported time value")
    sign, year, month, day, hour, minute, second = match.groups()
    year_i = int(year) * (-1 if sign == "-" else 1)
    month_i, day_i = int(month), int(day)
    if (month_i > 12 or day_i > 31 or (month_i == 0 and day_i != 0)
            or (precision >= 10 and month_i == 0) or (precision >= 11 and day_i == 0)
            or int(hour) > 23 or int(minute) > 59 or int(second) > 59
            or (month_i and day_i and day_i > calendar.monthrange(year_i, month_i)[1])):
        raise _QuickStatementsOmissionError("unsupported time value")
    return f"{value}/{precision}"


def _url_value(value: object) -> str:
    text = _escape_qs(value)
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise _QuickStatementsOmissionError("invalid URL")
    except ValueError as exc:
        raise _QuickStatementsOmissionError("invalid URL") from exc
    return f'"{text}"'


def _format_value(stmt: WikidataStatement) -> str:
    """Format a statement value for QuickStatements v2.

    Args:
        stmt: The WikidataStatement to format.

    Returns:
        QuickStatements-compatible value string.
    """
    # Rule 42: somevalue/novalue are native QS v2 tokens (no quotes).
    if stmt.value_type == "somevalue":
        return "somevalue"
    if stmt.value_type == "novalue":
        return "novalue"

    if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
        raise _QuickStatementsOmissionError("unresolved local value")

    if stmt.value_type in ("item", "wikibase-item", "wikibase-entityid"):
        value = stmt.value
        if not isinstance(value, str) or not re.fullmatch(r"Q[1-9][0-9]*", value):
            raise _QuickStatementsOmissionError("invalid item value")
        return value

    if stmt.value_type == "string":
        return f'"{_escape_qs(stmt.value)}"'

    if stmt.value_type == "external-id":
        return f'"{_escape_qs(stmt.value)}"'

    if stmt.value_type == "time":
        return _time_value(stmt.value, stmt.precision)

    if stmt.value_type == "quantity":
        return _quantity_value(stmt.value, stmt.unit)

    if stmt.value_type == "url":
        return _url_value(stmt.value)

    if stmt.value_type == "monolingualtext":
        lang = _language(stmt.language)
        return f'{lang}:"{_escape_qs(stmt.value)}"'

    raise _QuickStatementsOmissionError("unsupported value type")


def _format_qualifier(qual: dict[str, object]) -> str:
    """Format a single qualifier snak for QuickStatements.

    Qualifiers use the P-prefix (unlike references which use S-prefix).

    Args:
        qual: Dict with 'property', 'value', 'type' keys.

    Returns:
        Tab-separated qualifier components.
    """
    if _contains_local(qual):
        raise _QuickStatementsOmissionError("unresolved local qualifier")
    allowed = {
        "property", "property_id", "value", "type", "value_type", "precision",
        "language", "unit", "property_label", "value_label",
    }
    if not isinstance(qual, dict) or set(qual) - allowed:
        raise _QuickStatementsOmissionError("unsupported qualifier metadata")
    pid = qual.get("property") or qual.get("property_id")
    if (not isinstance(pid, str) or not re.fullmatch(r"P[1-9][0-9]*", pid)
            or (qual.get("property") and qual.get("property_id")
                and qual["property"] != qual["property_id"])):
        raise _QuickStatementsOmissionError("invalid qualifier property")
    types = [qual[key] for key in ("type", "value_type") if key in qual]
    if any(not isinstance(value, str) or not value for value in types):
        raise _QuickStatementsOmissionError("invalid qualifier type")
    normalized = ["item" if value in ("wikibase-item", "wikibase-entityid") else value
                  for value in types]
    if len(set(normalized)) > 1:
        raise _QuickStatementsOmissionError("conflicting qualifier types")
    vtype = normalized[0] if normalized else "string"
    value = qual.get("value")
    if ("precision" in qual and vtype != "time") or ("unit" in qual and vtype != "quantity"):
        raise _QuickStatementsOmissionError("unsupported qualifier metadata")
    if "language" in qual and vtype != "monolingualtext":
        raise _QuickStatementsOmissionError("unsupported qualifier metadata")
    if vtype == "item":
        if not isinstance(value, str) or not re.fullmatch(r"Q[1-9][0-9]*", value):
            raise _QuickStatementsOmissionError("invalid qualifier item")
        return f"{pid}\t{value}"
    if vtype == "time":
        return f"{pid}\t{_time_value(value, qual.get('precision', 11))}"
    if vtype == "quantity":
        return f"{pid}\t{_quantity_value(value, qual.get('unit'))}"
    if vtype == "monolingualtext":
        return f'{pid}\t{_language(qual.get("language"))}:"{_escape_qs(value)}"'
    if vtype == "url":
        return f"{pid}\t{_url_value(value)}"
    if vtype in ("string", "external-id"):
        return f'{pid}\t"{_escape_qs(value)}"'
    raise _QuickStatementsOmissionError("unsupported qualifier type")


def _format_reference(ref_snak: dict[str, object]) -> str:
    """Format a single reference snak for QuickStatements.

    Reference snaks use S-prefix (e.g., S248 instead of P248).

    Args:
        ref_snak: Dict with 'property', 'value', 'type' keys.

    Returns:
        Tab-separated reference components.
    """
    prop = ref_snak.get("property") or ref_snak.get("property_id")
    if not isinstance(prop, str) or not re.fullmatch(r"P[1-9][0-9]*", prop):
        raise ValueError("A QuickStatements reference requires a valid property")
    if "value" not in ref_snak or ref_snak["value"] is None:
        raise ValueError("A QuickStatements reference requires a value")
    pid = "S" + prop[1:]
    value = ref_snak["value"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A QuickStatements reference requires a non-empty string value")
    value_type = ref_snak.get("value_type") or ref_snak.get("type") or "string"

    if value_type in ("item", "wikibase-item", "wikibase-entityid"):
        if not re.fullmatch(r"Q[1-9][0-9]*", value):
            raise ValueError("A QuickStatements reference item must be a Wikidata QID")
        return f"{pid}\t{value}"
    if value_type == "url":
        return f'{pid}\t"{_escape_qs(value)}"'
    if value_type == "time":
        precision = ref_snak.get("precision", 11)
        validate_reference_time(value, precision)
        return f"{pid}\t{value}/{precision}"
    if value_type not in ("string", "external-id"):
        raise ValueError(f"Unsupported QuickStatements reference type: {value_type}")
    return f'{pid}\t"{_escape_qs(value)}"'


class QuickStatementsExporter:
    """Export WikidataItem instances to QuickStatements v2 text format.

    Usage::

        exporter = QuickStatementsExporter()
        text = exporter.export(items)
        exporter.export_to_file(items, Path("quickstatements.txt"))
    """

    def export_item(self, item: WikidataItem) -> str:
        """Export a single WikidataItem to QuickStatements lines.

        Args:
            item: The WikidataItem to export.

        Returns:
            Multi-line QuickStatements text for this item.
        """
        # Bug fix 2026-04-19: notability-filtered persons have no labels or
        # statements. Emitting a lone CREATE line for them is invalid QS syntax.
        if not item.existing_qid and not item.labels and not item.statements:
            return _item_omission(item, "no label or statement")

        lines: list[str] = []

        if item.existing_qid:
            if (not isinstance(item.existing_qid, str)
                    or not re.fullmatch(r"Q[1-9][0-9]*", item.existing_qid)):
                return _item_omission(item, "invalid Wikidata target")
            qid = item.existing_qid
        else:
            lines.append("CREATE")
            qid = "LAST"

        # Labels
        for lang, label in item.labels.items():
            try:
                lines.append(f'{qid}\tL{_language(lang)}\t"{_escape_qs(label)}"')
            except _QuickStatementsOmissionError:
                lines.append("/* Label omitted: unsafe or unsupported value */")

        # Descriptions
        for lang, desc in item.descriptions.items():
            try:
                lines.append(f'{qid}\tD{_language(lang)}\t"{_escape_qs(desc)}"')
            except _QuickStatementsOmissionError:
                lines.append("/* Description omitted: unsafe or unsupported value */")

        # Aliases
        for lang, alias_list in item.aliases.items():
            if not isinstance(alias_list, list):
                lines.append("/* Alias omitted: invalid alias list */")
                continue
            for alias in alias_list:
                try:
                    lines.append(f'{qid}\tA{_language(lang)}\t"{_escape_qs(alias)}"')
                except _QuickStatementsOmissionError:
                    lines.append("/* Alias omitted: unsafe or unsupported value */")

        # Statements
        groups: dict[tuple[str, str], set[tuple[str, ...]]] = {}
        for stmt in item.statements:
            if (not isinstance(stmt.property_id, str)
                    or not re.fullmatch(r"P[1-9][0-9]*", stmt.property_id)):
                continue
            try:
                value = _format_value(stmt)
                if stmt.rank != "normal":
                    signature = ("unsupported rank",)
                elif isinstance(stmt.qualifiers, list):
                    signature = tuple(sorted(_format_qualifier(qual) for qual in stmt.qualifiers))
                else:
                    continue
            except _QuickStatementsOmissionError:
                continue
            groups.setdefault((stmt.property_id, value), set()).add(signature)

        for stmt in item.statements:
            if (not isinstance(stmt.property_id, str)
                    or not re.fullmatch(r"P[1-9][0-9]*", stmt.property_id)):
                lines.append("/* Statement omitted: invalid property */")
                continue
            if stmt.rank != "normal":
                lines.append(
                    "/* Statement omitted: QuickStatements cannot preserve rank "
                    f"[{stmt.property_id}] */"
                )
                continue
            try:
                value_str = _format_value(stmt)
                if qid == "LAST" and stmt.value_type in {"somevalue", "novalue"}:
                    raise _QuickStatementsOmissionError("unsupported unknown value on CREATE")
                if not isinstance(stmt.qualifiers, list):
                    raise _QuickStatementsOmissionError("invalid qualifiers")
                qualifiers = [_format_qualifier(qual) for qual in stmt.qualifiers]
                if len(qualifiers) != len(set(qualifiers)):
                    raise _QuickStatementsOmissionError("duplicate qualifiers cannot be preserved")
                if len(groups.get((stmt.property_id, value_str), ())) > 1:
                    raise _QuickStatementsOmissionError("conflicting statement qualifiers or ranks")
            except _QuickStatementsOmissionError as exc:
                lines.append(f"/* Statement omitted: {exc} [{stmt.property_id}] */")
                continue
            try:
                validate_source_references(
                    stmt.references, getattr(stmt, "reference_blocks", []), allow_local=True,
                )
                references, blocks = reference_data(
                    stmt.references, getattr(stmt, "reference_blocks", []),
                )
            except (ValueError, TypeError, OverflowError) as exc:
                lines.append(
                    f"/* Statement omitted: {_reference_omission(exc)} "
                    f"[{stmt.property_id}] */"
                )
                continue
            if not blocks and len(references) > 1:
                lines.append(
                    "/* Statement omitted: ambiguous flat reference provenance "
                    f"[{stmt.property_id}] */"
                )
                continue
            if any(
                isinstance(snak.get("value"), str) and snak["value"].startswith("__LOCAL:")
                for block in blocks or [references] for snak in block
            ):
                lines.append(
                    f"/* Statement omitted: unresolved local reference [{stmt.property_id}] */"
                )
                continue
            line_parts = [qid, stmt.property_id, value_str]

            # Qualifiers must appear before references per QS v2 format
            line_parts.extend(qualifiers)

            try:
                for index, block in enumerate(blocks or ([references] if references else [])):
                    if not block:
                        raise ValueError(
                            "QuickStatements cannot represent an empty reference block"
                        )
                    for snak_index, ref_snak in enumerate(block):
                        formatted = _format_reference(ref_snak)
                        if index and not snak_index:
                            formatted = "!" + formatted
                        line_parts.append(formatted)
            except _QuickStatementsOmissionError as exc:
                lines.append(f"/* Statement omitted: {exc} [{stmt.property_id}] */")
                continue
            except (ValueError, TypeError, OverflowError) as exc:
                lines.append(
                    f"/* Statement omitted: {_reference_omission(exc)} "
                    f"[{stmt.property_id}] */"
                )
                continue

            lines.append("\t".join(line_parts))

        omissions = [line for line in lines if line.startswith("/* ") and " omitted:" in line]
        if omissions:
            return "\n".join([_item_omission(item), *omissions])
        if not lines:
            return _item_omission(item, "no exportable data")
        return "\n".join(lines)

    def export(
        self,
        items: list[WikidataItem],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Export all items to QuickStatements v2 format.

        Args:
            items: List of WikidataItem instances.
            on_progress: Optional ``(done, total)`` callback for the item loop.

        Returns:
            Complete QuickStatements text.
        """
        if not items:
            if on_progress is not None:
                on_progress(0, 1)
            return "/* No items to export */"
        blocks: list[str] = []

        # Separate by type and ORDER OF DEPENDENCY:
        #   persons first  — work items reference them via P50 (author).
        #   works second   — manuscript items reference them via P1574 (exemplar of).
        #   manuscripts last.
        #
        # Bug fix 2026-05-18: works were previously dropped entirely from
        # QS export (only `persons` + `manuscripts` were iterated). Work
        # items now appear as their own CREATE blocks so the en label
        # "Takanut rivno gereshem meor hagola (NLI 990001801390205171)"
        # composed in `_get_or_create_work` reaches Wikidata.
        persons = [i for i in items if i.entity_type == "person"]
        works = [i for i in items if i.entity_type == "work"]
        manuscripts = [i for i in items if i.entity_type == "manuscript"]
        unsupported = [i for i in items if i.entity_type not in {"person", "work", "manuscript"}]
        ordered = (*persons, *works, *manuscripts, *unsupported)
        total = len(ordered)
        done = 0

        def _tick() -> None:
            nonlocal done
            done += 1
            if on_progress is not None and total and (
                done == 1 or done == total or done % 25 == 0
            ):
                on_progress(done, total)

        if on_progress is not None:
            on_progress(0, max(total, 1))

        blocks.append(
            "/* MHM Pipeline — Wikidata QuickStatements Export */\n"
            "/* Persons (create first; works and manuscripts reference them) */\n"
        )

        for person in persons:
            if person.existing_qid:
                blocks.append(_item_omission(person, "existing person is not exported"))
                _tick()
                continue
            if block := self.export_item(person):
                blocks.append(block)
                blocks.append("")  # Blank line between items
            _tick()

        blocks.append(
            "\n/* Works (create second; manuscripts P1574 them) */\n"
        )

        for work in works:
            if work.existing_qid:
                blocks.append(_item_omission(work, "existing work is not exported"))
                _tick()
                continue
            if block := self.export_item(work):
                blocks.append(block)
                blocks.append("")
            _tick()

        blocks.append("\n/* Manuscripts */\n")

        for ms in manuscripts:
            blocks.append(self.export_item(ms))
            blocks.append("")
            _tick()

        for _item in unsupported:
            blocks.append(_item_omission(_item, "unsupported entity type"))
            _tick()

        if any(" omitted:" in block for block in blocks):
            blocks.insert(
                0, "/* WARNING: Incomplete export. Omitted data requires another write path. */",
            )

        return "\n".join(blocks)

    def export_to_file(
        self,
        items: list[WikidataItem],
        output_path: Path,
    ) -> Path:
        """Write QuickStatements text to a file.

        Args:
            items: List of WikidataItem instances.
            output_path: Destination file path.

        Returns:
            The output path written to.
        """
        text = self.export(items)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        logger.info(
            "Exported %d items to QuickStatements: %s",
            len(items),
            output_path,
        )
        return output_path
