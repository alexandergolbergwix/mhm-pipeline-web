"""SHACL upload gate helpers for HMO Wikibase item writes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

BLOCKING_SEVERITIES = frozenset({"Violation", "Error"})
_WIKIBASE_UNSUPPORTED_LANGS = frozenset({"und", ""})


def blocking_shacl_issues(
    issues: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return SHACL issues that must block a live upload by default."""
    if not issues:
        return []
    return [
        issue for issue in issues
        if str(issue.get("severity") or "") in BLOCKING_SEVERITIES
    ]


def format_shacl_block_message(issues: list[dict[str, Any]]) -> str:
    """Human-readable summary for audit logs and upload outcomes."""
    if not issues:
        return "blocked by SHACL validation"
    parts = [
        str(issue.get("message") or "SHACL violation").strip()
        for issue in issues[:5]
    ]
    extra = len(issues) - len(parts)
    if extra > 0:
        parts.append(f"(+{extra} more)")
    return "; ".join(parts)


def sanitize_wikibase_labels(labels: dict[str, str]) -> dict[str, str]:
    """Drop unsupported language codes and never emit ``und`` to Wikibase."""
    out: dict[str, str] = {}
    for lang, value in labels.items():
        text = str(value or "").strip()
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _WIKIBASE_UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    if not out:
        out["en"] = "Untitled"
    if "en" not in out:
        out["en"] = next(iter(out.values()))
    return out


def sanitize_wikibase_descriptions(descriptions: dict[str, str]) -> dict[str, str]:
    """Same language hygiene as :func:`sanitize_wikibase_labels`."""
    out: dict[str, str] = {}
    for lang, value in descriptions.items():
        text = str(value or "").strip()
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _WIKIBASE_UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    return out


def drop_descriptions_equal_to_labels(
    labels: dict[str, str],
    descriptions: dict[str, str],
) -> tuple[dict[str, str], int]:
    """Wikibase Cloud rejects a write whose label equals its description in
    the same language (``modification-failed: Label and description for
    language code X can not have the same value``). The builder falls back to
    the label when no real description was generated, so identical pairs
    reached the wiki and failed hundreds of writes on a large corpus.

    Returns the filtered descriptions plus the dropped count; the label
    carries the information and the write becomes valid.
    """
    out: dict[str, str] = {}
    dropped = 0
    for lang, value in descriptions.items():
        text = str(value or "").strip()
        if text and text == str(labels.get(lang) or "").strip():
            dropped += 1
            continue
        out[lang] = value
    return out, dropped


def _normalize_lang_code(lang: str) -> str:
    code = str(lang or "").strip().lower()
    return "en" if code in _WIKIBASE_UNSUPPORTED_LANGS else code


def compute_disambiguated_labels(entities: Iterable[Any]) -> dict[str, dict[str, str]]:
    """Wikibase Cloud enforces unique ``(label, description)`` pairs per
    language. The build yields structural nodes (CanonRef / TextTradition /
    Expression / Work) that share a pair with another item of the same
    corpus — the second create fails with ``modification-failed: … already
    has label …`` and retries identically forever (871 such items on run
    3494ebf5).

    Returns, per local_id, disambiguated payload labels for every item that
    is not the first claimant (sorted by local_id) of its
    ``(language, label, description)`` key: the colliding language's label
    gains `` — <first control number>`` (falling back to the local_id).

    Purely content-based — the same build cache always yields the same
    labels, so later ``update_item`` pushes stay consistent instead of
    tripping uniqueness again. Mapped items may be renamed once if they are
    not their group's first claimant; claims and source URIs never change.
    """
    claimed: set[tuple[str, str, str]] = set()
    overrides: dict[str, dict[str, str]] = {}
    for entity in sorted(entities, key=lambda e: str(e.local_id)):
        labels = sanitize_wikibase_labels(dict(entity.labels))
        descriptions, _dropped = drop_descriptions_equal_to_labels(
            labels,
            sanitize_wikibase_descriptions(dict(entity.descriptions)),
        )
        new_labels = dict(labels)
        changed = False
        for lang, text in labels.items():
            code = _normalize_lang_code(lang)
            desc = str(descriptions.get(code) or "").strip()
            key = (code, text.strip(), desc)
            if key in claimed:
                cn = next(iter(getattr(entity, "control_numbers", None) or []), "")
                base = f"{text} — {str(cn).strip() or entity.local_id}"
                candidate = base
                n = 2
                while (code, candidate.strip(), desc) in claimed:
                    candidate = f"{base} ({n})"
                    n += 1
                new_labels[lang] = candidate
                changed = True
                claimed.add((code, candidate.strip(), desc))
            else:
                claimed.add(key)
        if changed:
            overrides[str(entity.local_id)] = new_labels
    return overrides
