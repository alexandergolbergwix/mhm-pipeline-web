"""Merge Mazal / VIAF / KIMA biodata into a storable authority payload slice.

Pure functions only — no I/O. Callers pass blobs already fetched by the
matchers (VIAF cluster JSON is typically already in the matcher cache).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from converter.authority.biodata import (
    BioData,
    extract_kima_biodata,
    extract_mazal_biodata,
    extract_viaf_biodata,
)

BIODATA_VERSION = 1
MAX_NAME_VARIANTS_PER_LANG = 30
MAX_OCCUPATIONS = 20
MAX_PLACE_VALUES = 10
MAX_NOTES = 15


def biodata_to_dict(bio: BioData) -> dict[str, Any]:
    """Serialize :class:`BioData` for JSON / SQLite payload storage."""
    raw = asdict(bio)
    return {
        "dates": dict(raw.get("dates") or {}),
        "places": {k: list(v) for k, v in (raw.get("places") or {}).items()},
        "names": {k: list(v) for k, v in (raw.get("names") or {}).items()},
        "occupations": list(raw.get("occupations") or []),
        "notes": list(raw.get("notes") or []),
    }


def _bio_has_content(bio: BioData) -> bool:
    if bio.dates or bio.occupations or bio.notes:
        return True
    if any(bio.places.values()):
        return True
    return any(bio.names.values())


def _dedupe_strings(items: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _merge_date_maps(primary: dict[str, str], secondary: dict[str, str]) -> dict[str, str]:
    merged = dict(secondary)
    for key, value in primary.items():
        if value and not merged.get(key):
            merged[key] = value
    return merged


def _merge_names(
    mazal_names: dict[str, list[str]],
    viaf_names: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Hebrew names prefer Mazal order; Latin/English prefer VIAF order."""
    out: dict[str, list[str]] = {}
    for lang in ("he", "lat", "en", "ar", "und"):
        if lang == "he":
            combined = list(mazal_names.get("he") or []) + list(viaf_names.get("he") or [])
        else:
            combined = list(viaf_names.get(lang) or []) + list(mazal_names.get(lang) or [])
        if combined:
            out[lang] = _dedupe_strings(combined, limit=MAX_NAME_VARIANTS_PER_LANG)
    return out


def _merge_places(
    viaf_places: dict[str, list[str]],
    mazal_places: dict[str, list[str]],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in ("birth_place", "death_place", "associated_places", "country", "admin_region", "coords"):
        viaf_vals = list(viaf_places.get(key) or [])
        mazal_vals = list(mazal_places.get(key) or [])
        combined = viaf_vals + [v for v in mazal_vals if v not in viaf_vals]
        if combined:
            out[key] = _dedupe_strings(combined, limit=MAX_PLACE_VALUES)
    return out


def merge_authority_biodata(
    *,
    mazal_bio: BioData | None = None,
    viaf_bio: BioData | None = None,
    kima_bio: BioData | None = None,
) -> tuple[BioData, list[str]]:
    """Merge authority-side biodata with source attribution."""
    sources: list[str] = []
    mazal = mazal_bio if mazal_bio and _bio_has_content(mazal_bio) else None
    viaf = viaf_bio if viaf_bio and _bio_has_content(viaf_bio) else None
    kima = kima_bio if kima_bio and _bio_has_content(kima_bio) else None

    if mazal:
        sources.append("mazal")
    if viaf:
        sources.append("viaf")
    if kima:
        sources.append("kima")

    if not sources:
        return BioData(), []

    dates = _merge_date_maps(mazal.dates if mazal else {}, viaf.dates if viaf else {})
    if kima and kima.dates:
        dates = _merge_date_maps(dates, kima.dates)

    places = _merge_places(
        viaf.places if viaf else {},
        mazal.places if mazal else {},
    )
    if kima and kima.places:
        for key, vals in kima.places.items():
            existing = list(places.get(key) or [])
            places[key] = _dedupe_strings(existing + list(vals), limit=MAX_PLACE_VALUES)

    names = _merge_names(
        mazal.names if mazal else {},
        viaf.names if viaf else {},
    )
    if kima and kima.names:
        for lang, vals in kima.names.items():
            existing = list(names.get(lang) or [])
            names[lang] = _dedupe_strings(existing + list(vals), limit=MAX_NAME_VARIANTS_PER_LANG)

    occupations = _dedupe_strings(
        list(viaf.occupations if viaf else []) + list(mazal.occupations if mazal else []),
        limit=MAX_OCCUPATIONS,
    )
    notes = _dedupe_strings(
        list(mazal.notes if mazal else [])
        + list(viaf.notes if viaf else [])
        + list(kima.notes if kima else []),
        limit=MAX_NOTES,
    )

    return BioData(dates=dates, places=places, names=names, occupations=occupations, notes=notes), sources


def build_biodata_payload_slice(
    *,
    mazal_entry: dict[str, Any] | None = None,
    viaf_cluster_raw: dict[str, Any] | None = None,
    kima_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build additive ``payload`` keys for authority matches."""
    mazal_bio = extract_mazal_biodata(mazal_entry) if mazal_entry else None
    viaf_bio = extract_viaf_biodata(viaf_cluster_raw) if viaf_cluster_raw else None
    kima_bio = extract_kima_biodata(kima_entry) if kima_entry else None

    merged, sources = merge_authority_biodata(
        mazal_bio=mazal_bio,
        viaf_bio=viaf_bio,
        kima_bio=kima_bio,
    )
    if not sources:
        return {}

    biodata_dict = biodata_to_dict(merged)
    slice_out: dict[str, Any] = {
        "biodata_authority": biodata_dict,
        "biodata_sources": sources,
        "biodata_version": BIODATA_VERSION,
    }
    if biodata_dict.get("occupations"):
        slice_out["occupations"] = biodata_dict["occupations"]
    names = biodata_dict.get("names") or {}
    if names.get("he"):
        slice_out["name_variants_he"] = names["he"]
    if names.get("lat"):
        slice_out["name_variants_lat"] = names["lat"]
    places = biodata_dict.get("places") or {}
    if places.get("birth_place"):
        slice_out["birth_places"] = places["birth_place"]
    if places.get("death_place"):
        slice_out["death_places"] = places["death_place"]
    return slice_out


__all__ = [
    "BIODATA_VERSION",
    "biodata_to_dict",
    "build_biodata_payload_slice",
    "merge_authority_biodata",
]
