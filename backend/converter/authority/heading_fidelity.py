"""Does an authority heading actually name the person the MARC record names?

Rule W-166. ``person_projection`` used to overwrite ``labels["he"]`` with the
authority row's ``preferred_name_heb`` unconditionally, demoting the MARC heading
to an alias with no comparison at all. In run 48ba6c13 that published a person
labelled ``יצחק בן שלמה בן חיים גבאי`` (from Mazal 987007299516905171) for a
manuscript whose MARC contributor is ``גבאי, טוביה בן חיים יצחק`` — the same
family, a different given name, and therefore a different scribe.

The distance check itself is NOT reimplemented here:
``wikidata_crosscheck.hebrew_label_matches`` already does diacritic-normalised
Levenshtein and is the function the authority hardening guard uses, so both sides
of the pipeline agree on what "the same name" means.
"""

from __future__ import annotations

# Hebrew patronymic connectors. "בן" in `גבאי, טוביה בן חיים יצחק` is structure,
# not a name part, so it must not pad the token overlap.
_PATRONYMIC_CONNECTORS = frozenset({"בן", "בת", "בר", "אבן", "ן׳", "ב״ר", "ben", "bar", "ibn"})

def _name_tokens(name: str) -> list[str]:
    """Name tokens in natural order, with patronymic connectors removed."""
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _normalise_label,
        _strip_person_name_qualifiers,
        _to_natural_name_order,
    )

    text = _normalise_label(
        _strip_person_name_qualifiers(_to_natural_name_order(str(name or ""))),
    )
    out: list[str] = []
    for raw in text.replace(",", " ").split():
        token = raw.strip(" .,;:\"'")
        if token and token.casefold() not in _PATRONYMIC_CONNECTORS:
            out.append(token)
    return out


def _given_and_surname(name: str) -> tuple[str, str]:
    """``(given, surname)`` from a heading already in natural order.

    Hebrew headings put the family name last once inverted form is undone, so the
    first token is the given name and the last is the family name. With a single
    token the two coincide.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return "", ""
    return tokens[0], tokens[-1]


def heading_matches(
    marc_heading: str,
    authority_heading: str,
    *,
    max_distance: int = 2,
) -> bool:
    """True when *authority_heading* plausibly names the MARC heading's person.

    A shared family name is not enough. ``גבאי, טוביה בן חיים יצחק`` and
    ``יצחק בן שלמה בן חיים גבאי`` share the surname and two patronymic elements —
    a bare token-overlap score passes them — but their GIVEN names are Toviah and
    Yitzhak. They are relatives, not the same scribe.
    """
    if not marc_heading or not authority_heading:
        return False

    from converter.authority.wikidata_crosscheck import (  # noqa: PLC0415
        hebrew_label_matches,
    )

    # A spelling variant of the same full name.
    if hebrew_label_matches(marc_heading, [authority_heading], max_distance=max_distance):
        return True

    marc_given, marc_surname = _given_and_surname(marc_heading)
    auth_given, auth_surname = _given_and_surname(authority_heading)
    if not marc_given or not auth_given:
        return False
    if not hebrew_label_matches(marc_surname, [auth_surname], max_distance=1):
        return False
    return bool(hebrew_label_matches(marc_given, [auth_given], max_distance=1))


def heading_mismatch_reason(marc_heading: str, authority_heading: str) -> str:
    """Why the two headings do not name the same person, for the evidence row."""
    marc_given, marc_surname = _given_and_surname(marc_heading)
    auth_given, auth_surname = _given_and_surname(authority_heading)
    if marc_surname and auth_surname and marc_surname != auth_surname:
        detail = f"different family name ({marc_surname} vs {auth_surname})"
    elif marc_given and auth_given and marc_given != auth_given:
        detail = f"different given name ({marc_given} vs {auth_given})"
    else:
        detail = "the names differ beyond the allowed edit distance"
    return (
        f"MARC heading {marc_heading!r} and authority heading "
        f"{authority_heading!r} do not name the same person: {detail}"
    )


def identity_compatible(
    marc_heading: str,
    authority_or_label: str,
    *,
    max_distance: int = 2,
) -> bool:
    """True when a public person identity may be attached to this MARC agent.

    Corpus-scale gate (Rule W-171): emit P8189 / public QIDs only when the
    headings name the same person. Same-family / different-given is always
    incompatible. When both sides have Hebrew given+surname and the surnames
    disagree, still treat as compatible only via ``heading_matches`` — Latin
    transliteration and toponymic bynames must not force a strip on their own
    (those ship on many full-passing rows). Callers that already know a hard
    ``heading_mismatch`` should treat that as incompatible regardless.
    """
    marc = str(marc_heading or "").strip()
    other = str(authority_or_label or "").strip()
    if not marc or not other:
        return True
    if heading_matches(marc, other, max_distance=max_distance):
        return True
    marc_given, marc_surname = _given_and_surname(marc)
    other_given, other_surname = _given_and_surname(other)
    if not (marc_given and other_given and marc_surname and other_surname):
        return True
    from converter.authority.wikidata_crosscheck import (  # noqa: PLC0415
        hebrew_label_matches,
    )

    surname_ok = bool(hebrew_label_matches(marc_surname, [other_surname], max_distance=1))
    given_ok = bool(hebrew_label_matches(marc_given, [other_given], max_distance=1))
    if surname_ok and not given_ok:
        return False
    return True
