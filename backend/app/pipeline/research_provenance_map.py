"""Assemble a geo+time-ordered provenance movement map for one manuscript.

Pure functions (no I/O, no network) so the integrity guards are unit-testable.
The router resolves the DB rows + owner coordinates, then calls
:func:`build_provenance_map`.

Stops (map points):
  - ``production``        — production place (KIMA / gazetteer coords) + production date band
  - ``acquisition``       — acquisition event place (MARC 541)
  - ``conservation``      — conservation event place (MARC 583)
  - ``exhibition``        — exhibition event place (MARC 583)
  - ``significant_place`` — a related/significant place (MARC 751/752, KIMA / gazetteer coords)
  - ``owner``             — a former owner or former holding institution
  - ``current_holder``    — the attested physical holding institution (or NLI default when unattested)

Integrity guards (plan §A–§D). Every guard has a test in
``test_provenance_map_guards.py``. Governing rule: missing/failed signal →
drop or visibly mark uncertain, never fabricate.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Anachronism buffer (plan A5; mirrors the desktop Stage-3 DATE_BIRTH_BUFFER_YEARS).
DATE_BIRTH_BUFFER_YEARS = 5

# Default holder anchor when a record carries no explicit holding_institution:
# National Library of Israel, Jerusalem.
NLI_HOLDER = {
    "label": "National Library of Israel",
    "lat": 31.7942,
    "lon": 35.2007,
}

_OWNER_ROLE_TOKENS = (
    "owner", "former owner", "current owner", "former_owner", "current_owner",
    "possessor", "provenance",
    # Hebrew: בעלים / בעל / בעלים קודמים (former owner)
    "בעלים", "בעל",
)

_ACCEPTED_CONFIDENCE = ("high", "medium")

# Audited WGS84 city coordinates for MARC 942$x holding cities and institutional seats.
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "new york": (40.7128, -74.0060),
    "brooklyn": (40.6782, -73.9442),
    "jerusalem": (31.7789, 35.2256),
    "london": (51.5072, -0.1275),
    "oxford": (51.7519, -1.2578),
    "st. petersburg": (59.9343, 30.3351),
    "saint petersburg": (59.9343, 30.3351),
    "moscow": (55.7558, 37.6173),
    "cincinnati": (39.1031, -84.5120),
    "amsterdam": (52.3676, 4.9041),
    "paris": (48.8566, 2.3522),
    "hamburg": (53.5511, 9.9937),
    "cambridge, england": (52.2053, 0.1218),
    "cambridge, usa": (42.3736, -71.1097),
    "cambridge": (52.2053, 0.1218),
    "budapest": (47.4979, 19.0402),
    "leeds": (53.8008, -1.5491),
    "ramat gan": (32.0684, 34.8248),
    "san francisco": (37.7749, -122.4194),
    "vatican city": (41.9029, 12.4534),
    "zurich": (47.3769, 8.5417),
    "zürich": (47.3769, 8.5417),
    "holon": (32.0158, 34.7874),
    "berlin": (52.5200, 13.4050),
    "parma": (44.8015, 10.3279),
    "frankfurt am main": (50.1109, 8.6821),
    "frankfurt": (50.1109, 8.6821),
    "tel aviv": (32.0853, 34.7818),
    "strasbourg": (48.5734, 7.7521),
    "mantua": (45.1564, 10.7914),
    "copenhagen": (55.6761, 12.5683),
    "florence": (43.7696, 11.2558),
    "chicago": (41.8781, -87.6298),
    "turin": (45.0703, 7.6869),
    "manchester": (53.4808, -2.2426),
    "bnei brak": (32.0833, 34.8333),
    "milan": (45.4642, 9.1900),
    "vienna": (48.2082, 16.3738),
    "munich": (48.1351, 11.5820),
    "montréal, québec": (45.5017, -73.5673),
    "montreal": (45.5017, -73.5673),
    "leiden": (52.1601, 4.4970),
    "prague": (50.0875, 14.4213),
    "uppsala": (59.8586, 17.6389),
    "liverpool": (53.4084, -2.9916),
    "modena": (44.6458, 10.9257),
    "los angeles": (34.0522, -118.2437),
    "livorno": (43.5485, 10.3106),
    "warsaw": (52.2297, 21.0122),
    "hannover": (52.3759, 9.7320),
    "le mans": (48.0061, 0.1996),
    "erlangen": (49.5897, 11.0040),
    "basel": (47.5596, 7.5886),
    "geneva": (46.2044, 6.1432),
    "carpentras": (44.0550, 5.0481),
    "rehovot": (31.8928, 34.8113),
    "haifa": (32.7940, 34.9896),
    "baltimore": (39.2904, -76.6122),
    "montserrat": (41.5933, 1.8370),
    "weimar": (50.9795, 11.3235),
    "ramle": (31.9292, 34.8656),
    "mainz": (49.9929, 8.2473),
    "piacenza": (45.0526, 9.6930),
    "skokie": (42.0324, -87.7416),
    "ferrara": (44.8378, 11.6196),
    "ancona": (43.6167, 13.5167),
    "verona": (45.4382, 10.9937),
    "safed": (32.9646, 35.4960),
}

# Audited seats for holding institutions and major former collections (when 942$x is absent).
_HOLDER_SEAT_COORDS: dict[str, tuple[str, float, float]] = {
    "national library of israel": ("Jerusalem", 31.7942, 35.2007),
    "the national library of israel": ("Jerusalem", 31.7942, 35.2007),
    "israel museum": ("Jerusalem", 31.7724, 35.2045),
    "the israel museum": ("Jerusalem", 31.7724, 35.2045),
    "british library": ("London", 51.5299, -0.1277),
    "the british library": ("London", 51.5299, -0.1277),
    "bodleian library": ("Oxford", 51.7541, -1.2540),
    "the bodleian library": ("Oxford", 51.7541, -1.2540),
    "the bodleian libraries, university of oxford": ("Oxford", 51.7541, -1.2540),
    "jewish theological seminary library": ("New York", 40.8122, -73.9605),
    "the jewish theological seminary of america": ("New York", 40.8122, -73.9605),
    "russian state library": ("Moscow", 55.7517, 37.6092),
    "the russian state library": ("Moscow", 55.7517, 37.6092),
    "national library of russia": ("St. Petersburg", 59.9336, 30.3356),
    "the national library of russia": ("St. Petersburg", 59.9336, 30.3356),
    "institute of oriental manuscripts of the russian academy of sciences": ("St. Petersburg", 59.9442, 30.3222),
    "institute of oriental manuscripts, the russian academy of sciences": ("St. Petersburg", 59.9442, 30.3222),
    "hebrew union college – jewish institute of religion": ("Cincinnati", 39.1292, -84.5169),
    "hebrew union college library": ("Cincinnati", 39.1292, -84.5169),
    "columbia university libraries": ("New York", 40.8063, -73.9631),
    "columbia university library": ("New York", 40.8063, -73.9631),
    "cambridge university library": ("Cambridge", 52.2051, 0.1078),
    "the cambridge university library": ("Cambridge", 52.2051, 0.1078),
    "trinity college library": ("Cambridge", 52.2070, 0.1169),
    "christ church library": ("Oxford", 51.7502, -1.2559),
    "university of leeds libraries": ("Leeds", 53.8067, -1.5550),
    "leeds university library": ("Leeds", 53.8067, -1.5550),
    "hungarian academy of sciences library and information centre": ("Budapest", 47.5011, 19.0462),
    "library of the hungarian academy of sciences": ("Budapest", 47.5011, 19.0462),
    "national széchényi library": ("Budapest", 47.4958, 19.0394),
    "hamburg state and university library carl von ossietzky": ("Hamburg", 53.5673, 9.9854),
    "the hamburg state and university library carl von ossietzky": ("Hamburg", 53.5673, 9.9854),
    "bar-ilan university libraries and information division": ("Ramat Gan", 32.0684, 34.8435),
    "bar-ilan university library": ("Ramat Gan", 32.0684, 34.8435),
    "san francisco state university": ("San Francisco", 37.7219, -122.4782),
    "san francisco state university library": ("San Francisco", 37.7219, -122.4782),
    "ets haim library": ("Amsterdam", 52.3676, 4.9041),
    "the ets haim library": ("Amsterdam", 52.3676, 4.9041),
    "bibliotheca rosenthaliana": ("Amsterdam", 52.3683, 4.8903),
    "jewish historical museum": ("Amsterdam", 52.3672, 4.9029),
    "vatican library": ("Vatican City", 41.9047, 12.4546),
    "the vatican library": ("Vatican City", 41.9047, 12.4546),
    "rabbinical school of france": ("Paris", 48.8422, 2.3452),
    "bibliothèque nationale de france": ("Paris", 48.8336, 2.3758),
    "the national library of france": ("Paris", 48.8336, 2.3758),
    "alliance israélite universelle": ("Paris", 48.8772, 2.3324),
    "library of the alliance israélite universelle": ("Paris", 48.8772, 2.3324),
    "biblioteca palatina di parma": ("Parma", 44.8047, 10.3261),
    "the palatina library": ("Parma", 44.8047, 10.3261),
    "berlin state library": ("Berlin", 52.5075, 13.3704),
    "state library of berlin": ("Berlin", 52.5075, 13.3704),
    "the jewish museum berlin": ("Berlin", 52.5024, 13.3955),
    "manfred and anne lehmann foundation": ("New York", 40.7580, -73.9855),
    "new york public library": ("New York", 40.7532, -73.9822),
    "yeshiva university library": ("New York", 40.8505, -73.9292),
    "the schocken institute for jewish research": ("Jerusalem", 31.7717, 35.2178),
    "the ben zvi institute": ("Jerusalem", 31.7714, 35.2144),
    "library of mossad ha-rav kook": ("Jerusalem", 31.7891, 35.1972),
    "hekhal shlomo": ("Jerusalem", 31.7758, 35.2165),
    "umberto nahon museum of italian jewish art": ("Jerusalem", 31.7801, 35.2181),
    "allony, nehemia": ("Jerusalem", 31.7789, 35.2256),
    "benayahu, meir": ("Jerusalem", 31.7789, 35.2256),
    "krupp, michael": ("Jerusalem", 31.7789, 35.2256),
    "klagsbald, victor": ("Jerusalem", 31.7789, 35.2256),
    "mehlman, israel": ("Jerusalem", 31.7789, 35.2256),
    "the montefiore library": ("London", 51.5250, -0.1300),
    "the beth din & beth hamidrash library": ("London", 51.6080, -0.1930),
    "gaster, moses collection": ("London", 51.5072, -0.1275),
    "sassoon, david solomon": ("London", 51.5072, -0.1275),
    "valmadonna trust": ("London", 51.5072, -0.1275),
    "adler, elkan nathan": ("London", 51.5072, -0.1275),
    "roth, cecil": ("Oxford", 51.7519, -1.2578),
    "halberstam, solomon joachim chayim": ("Bielsko-Biała", 49.8224, 19.0444),
    "university library johann christian senckenberg": ("Frankfurt am Main", 50.1206, 8.6524),
    "bibliothèque nationale et universitaire de strasbourg": ("Strasbourg", 48.5872, 7.7567),
    "the national and university library of strasbourg": ("Strasbourg", 48.5872, 7.7567),
    "zentralbibliothek zürich": ("Zurich", 47.3742, 8.5453),
    "zürich central library": ("Zurich", 47.3742, 8.5453),
    "braginsky collection": ("Zurich", 47.3769, 8.5417),
    "jewish community of mantua": ("Mantua", 45.1564, 10.7914),
    "spertus institute for jewish learning and leadership": ("Chicago", 41.8698, -87.6245),
    "university of manchester library": ("Manchester", 53.4648, -2.2325),
    "the university of manchester library": ("Manchester", 53.4648, -2.2325),
    "biblioteca ambrosiana": ("Milan", 45.4633, 9.1862),
    "ambrosian library": ("Milan", 45.4633, 9.1862),
    "bavarian state library": ("Munich", 48.1475, 11.5802),
    "royal danish library": ("Copenhagen", 55.6738, 12.5822),
    "national central library of florence": ("Florence", 43.7670, 11.2623),
    "biblioteca medicea laurenziana": ("Florence", 43.7747, 11.2542),
    "harvard library": ("Cambridge, USA", 42.3732, -71.1165),
    "leiden university library": ("Leiden", 52.1573, 4.4814),
    "leiden university libraries": ("Leiden", 52.1573, 4.4814),
    "uppsala university library": ("Uppsala", 59.8550, 17.6314),
    "biblioteca estense universitaria": ("Modena", 44.6481, 10.9212),
    "the jewish museum in prague": ("Prague", 50.0899, 14.4178),
    "the national library of the czech republic": ("Prague", 50.0866, 14.4160),
    "talmud tora library of livorno": ("Livorno", 43.5485, 10.3106),
    "jewish historical institute": ("Warsaw", 52.2442, 21.0028),
    "austrian national library": ("Vienna", 48.2061, 16.3662),
    "national university library of turin": ("Turin", 45.0686, 7.6869),
    "inguimbertine library": ("Carpentras", 44.0550, 5.0481),
    "johns hopkins university library": ("Baltimore", 39.3289, -76.6203),
}

# Audited historical settlement coordinates for MARC 751$a places missing from KIMA's
# coords column (e.g. Ridāʻ in KIMA row 11568 has lat=None, Safed has no Roman variant).
_HISTORICAL_PLACE_COORDS: dict[str, tuple[float, float]] = {
    "safed (israel)": (32.9646, 35.4960),
    "safed": (32.9646, 35.4960),
    "צפת": (32.9646, 35.4960),
    "ridāʻ (yemen)": (14.4167, 44.8333),
    "rida (yemen)": (14.4167, 44.8333),
    "רידא (תימן)": (14.4167, 44.8333),
    "al-tawilah (yemen)": (15.4917, 43.7750),
    "bayt al-ajal (yemen)": (15.3500, 44.2000),
    "san'a (yemen)": (15.3547, 44.2067),
    "sanaa (yemen)": (15.3547, 44.2067),
    "dhamār (yemen)": (14.5425, 44.4050),
    "dhamar (yemen)": (14.5425, 44.4050),
    "ʻamrān (yemen)": (15.6594, 43.9439),
    "amran (yemen)": (15.6594, 43.9439),
    "maabar (yemen)": (14.7956, 44.2903),
    "tanam (yemen)": (15.4250, 44.4083),
    "delag (yemen)": (13.9700, 44.1800),
    "asnāf (yemen)": (15.2833, 44.4167),
    "staryĭ krym (crimea, ukraine)": (45.0292, 35.0886),
    "staryi krym": (45.0292, 35.0886),
    "ērakleion (greece)": (35.3387, 25.1442),
    "houmt souk (tunisia)": (33.8758, 10.8575),
    "patra (greece)": (38.2466, 21.7346),
    "i︠e︡vpatorii︠a︡ (ukraine)": (45.1904, 33.3669),
    "morano sul po site (italy)": (45.1683, 8.3669),
    "grossenenglis (hessen, germany)": (51.0744, 9.2661),
    "mainz kastel (wiesbaden, germany)": (50.0072, 8.2831),
}

_NON_SPECIFIC_COUNTRIES = frozenset({
    "italy", "yemen (republic)", "yemen", "egypt", "israel", "germany",
    "france", "poland", "morocco", "spain", "ottoman empire", "kurdistan",
})


def is_owner_role(role: str) -> bool:
    """True when a role string denotes ownership (English or Hebrew)."""
    r = (role or "").strip().lower()
    if not r:
        return False
    return any(tok in r for tok in _OWNER_ROLE_TOKENS)


def _coords_from_payload(payload: dict[str, Any]) -> tuple[float, float] | None:
    """KIMA coords from an authority-match payload, validated (guard B1/A6)."""
    lat_raw = payload.get("kima_lat", payload.get("lat"))
    lon_raw = payload.get("kima_lon", payload.get("lon"))
    if lat_raw is None or lon_raw is None:
        return None
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    if not _valid_coords(lat, lon):
        return None
    return lat, lon


def _valid_coords(lat: float, lon: float) -> bool:
    for v in (lat, lon):
        if v != v or v in (float("inf"), float("-inf")):
            return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return False
    return True


@lru_cache(maxsize=512)
def _kima_or_gazetteer_coords(place_text: str) -> tuple[float, float] | None:
    """Resolve a MARC place string (751$a / 260$a / 942$x) via the local KIMA
    index and the Ashkenazi community gazetteer when ``authority_matches`` has
    no pre-matched row for it. Never fabricates country centroids."""
    clean = " ".join(str(place_text or "").split()).strip()
    if not clean:
        return None
    cf = clean.casefold()
    if cf in _NON_SPECIFIC_COUNTRIES:
        return None
    if cf in _HISTORICAL_PLACE_COORDS:
        return _HISTORICAL_PLACE_COORDS[cf]
    if cf in _CITY_COORDS:
        return _CITY_COORDS[cf]

    base = re.sub(r"\s*\([^)]*\)\s*$", "", cf).strip()
    if base in _HISTORICAL_PLACE_COORDS:
        return _HISTORICAL_PLACE_COORDS[base]
    if base in _CITY_COORDS:
        return _CITY_COORDS[base]

    try:
        from converter.authority.kima_index import KimaIndex  # noqa: PLC0415

        db_path = Path(__file__).resolve().parents[2] / "data" / "kima" / "kima_index.db"
        if db_path.exists():
            with KimaIndex(str(db_path)) as idx:
                row = idx.lookup_place(clean)
                if row and row.get("lat") is not None and row.get("lon") is not None:
                    lat_f, lon_f = float(row["lat"]), float(row["lon"])
                    if _valid_coords(lat_f, lon_f):
                        return lat_f, lon_f
    except Exception:
        pass

    try:
        from app.pipeline.ashkenazi_gazetteer import lookup as _ashk_lookup  # noqa: PLC0415

        hit = _ashk_lookup(clean) or (_ashk_lookup(base) if base else None)
        if hit and hit.get("lat") is not None and hit.get("lon") is not None:
            lat_f, lon_f = float(hit["lat"]), float(hit["lon"])
            if _valid_coords(lat_f, lon_f):
                return lat_f, lon_f
    except Exception:
        pass

    return None


def resolve_place_coords_from_matches_or_gazetteer(
    place_text: str,
    matches: list[dict[str, Any]],
    *,
    production_only: bool = False,
) -> tuple[float, float] | None:
    """Resolve *place_text* from ``matches`` first (honouring explicit bad-coord
    test payloads), then fall back to KIMA / gazetteer when no match row exists."""
    clean = str(place_text or "").strip()
    if not clean:
        return None
    clean_lc = clean.lower()
    matched_any_row = False
    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        role = (m.get("role") or "").lower()
        text = str(m.get("entity_text") or "").strip()
        text_lc = text.lower()
        is_hit = (
            (production_only and role.startswith("production") and (not text_lc or text_lc == clean_lc))
            or (text_lc and (text_lc == clean_lc or text_lc in clean_lc or clean_lc in text_lc))
        )
        if is_hit:
            matched_any_row = True
            c = _coords_from_payload(m.get("payload") or {})
            if c is not None:
                return c
    if matched_any_row:
        # A match row existed for this place but its coordinates were invalid/None
        # (e.g. guard B1 test with (0, 0)): respect the guard and do not override.
        return None
    return _kima_or_gazetteer_coords(clean)


def resolve_record_holder_stop(record: dict[str, Any]) -> dict[str, Any]:
    """Resolve the current physical holder and its coordinates for *record*.

    Uses the audited holding institution (`holding_institutions.py` + MARC
    `942$a`/`942$x`/`942$c` and `710$a[current owner]`). Falls back to NLI
    Jerusalem only when the record attests no specific holder.
    """
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _holding_institution_name,
        holder_names_from_record,
    )

    raw_a942 = str(record.get("942$a") or "").strip().strip('"').casefold()
    raw_a710 = str(record.get("710$a") or "").strip().strip('"').casefold()
    explicit_unknown = (
        str(record.get("holding_institution") or "").strip().casefold() in ("unknown library", "unknown")
        or raw_a942.startswith("unknown library")
        or raw_a710.startswith("unknown library")
    )
    if explicit_unknown and not holder_names_from_record(record):
        return {
            "label": "Unknown holding institution",
            "place": None,
            "lat": None,
            "lon": None,
        }

    holder_name = _holding_institution_name(record)
    raw_942 = str(record.get("holding_942_name") or "").strip()
    city_942 = str(record.get("holding_942_city") or "").strip()
    country_942 = str(record.get("holding_942_country") or "").strip()

    display_holder = holder_name or raw_942
    if not display_holder or display_holder.casefold() in ("unknown library", "unknown"):
        return {
            "label": NLI_HOLDER["label"],
            "place": "Jerusalem",
            "lat": NLI_HOLDER["lat"],
            "lon": NLI_HOLDER["lon"],
        }

    holder_cf = display_holder.casefold()
    raw_cf = raw_942.casefold()
    seat = _HOLDER_SEAT_COORDS.get(holder_cf) or _HOLDER_SEAT_COORDS.get(raw_cf)

    city_label = city_942 or (seat[0] if seat else "")
    coords: tuple[float, float] | None = None
    if seat is not None:
        coords = (seat[1], seat[2])
    if coords is None and city_942 and country_942:
        combo = f"{city_942}, {country_942}".casefold()
        coords = _CITY_COORDS.get(combo)
    if coords is None and city_942:
        coords = _CITY_COORDS.get(city_942.casefold()) or _kima_or_gazetteer_coords(city_942)

    if coords is None:
        return {
            "label": f"{display_holder} ({city_label})" if city_label else display_holder,
            "place": city_label or None,
            "lat": None,
            "lon": None,
        }

    full_label = (
        f"{display_holder} ({city_label})"
        if city_label and city_label.casefold() not in display_holder.casefold()
        else display_holder
    )
    return {
        "label": full_label,
        "place": city_label or display_holder,
        "lat": coords[0],
        "lon": coords[1],
    }


def resolve_former_holder_coords(
    holder_name: str, city_hint: str | None = None,
) -> tuple[float, float, str] | None:
    """Resolve a former holding institution / collector from MARC 942 / 710 to
    ``(lat, lon, place_label)`` when audited."""
    clean = " ".join(str(holder_name or "").split()).strip(" ,.;:")
    if not clean:
        return None
    cf = clean.casefold()
    seat = _HOLDER_SEAT_COORDS.get(cf)
    if seat is not None:
        return seat[1], seat[2], seat[0]
    if city_hint:
        c = _CITY_COORDS.get(city_hint.strip().casefold()) or _kima_or_gazetteer_coords(city_hint)
        if c is not None:
            return c[0], c[1], city_hint.strip()
    return None


def _year_band(dates: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """Return (year_certain, year_earliest, year_latest) from a MARC dates dict."""
    if not isinstance(dates, dict):
        return None, None, None

    def _int(v: Any) -> int | None:
        try:
            return int(str(v).split("-")[0].split("T")[0])
        except (TypeError, ValueError, AttributeError):
            return None

    year = _int(dates.get("year"))
    early = _int(dates.get("date_start"))
    if early is None:
        early = _int(dates.get("year_start"))
    if early is None:
        early = year
    late = _int(dates.get("date_end"))
    if late is None:
        late = _int(dates.get("year_end"))
    if late is None:
        late = year
    return year, early, late


def _event_coords(
    ev: dict[str, Any], ev_place: str, matches: list[dict[str, Any]],
) -> tuple[float, float] | None:
    """Coords for a provenance event: matched authority place first, then the
    event's own merged lat/lon. Returns None when neither is geolocated."""
    ev_lc = ev_place.strip().lower()
    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        mtext = str(m.get("entity_text") or "").strip().lower()
        if mtext and (mtext == ev_lc or mtext in ev_lc or ev_lc in mtext):
            c = _coords_from_payload(m.get("payload") or {})
            if c is not None:
                return c
    lat, lon = ev.get("lat"), ev.get("lon")
    if lat is not None and lon is not None:
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if _valid_coords(lat_f, lon_f):
            return lat_f, lon_f
    return None


def build_provenance_map(
    *,
    control_number: str,
    ms_label: str | None,
    record: dict[str, Any],
    matches: list[dict[str, Any]],
    owner_places: dict[str, dict[str, Any] | None],
    include_unapproved: bool = False,
) -> dict[str, Any]:
    """Build the movement map for one manuscript.

    ``owner_places`` maps owner QID → resolved place (``{lat,lon,geo_source,
    geo_source_label}``) or None, pre-fetched by the caller so this stays pure.
    """
    stops: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    # ── Production stop ────────────────────────────────────────────────
    prod_year, prod_early, prod_late = _year_band(record.get("dates"))
    prod_place_text = str(record.get("place") or "").strip()
    prod_coords: tuple[float, float] | None = None
    if prod_place_text:
        prod_coords = resolve_place_coords_from_matches_or_gazetteer(
            prod_place_text, matches, production_only=True,
        )
    prod_sort = prod_year if prod_year is not None else prod_early
    if prod_coords is not None or prod_year is not None or prod_early is not None:
        lat, lon = prod_coords if prod_coords else (None, None)
        stops.append({
            "kind": "production",
            "label": prod_place_text or "Production",
            "lat": lat, "lon": lon,
            "year": prod_year, "year_earliest": prod_early, "year_latest": prod_late,
            "certain": prod_year is not None,
            "inferred_geo": False,
            "time": float(prod_sort) if prod_sort is not None else None,
            "has_point": prod_coords is not None,
        })

    latest_for_anachronism = prod_late if prod_late is not None else prod_year

    # ── Provenance-event stops (acquisition / conservation / exhibition) ─
    seen_place_text = {prod_place_text} if prod_place_text else set()
    _TYPED_EVENT_KINDS = {"acquisition", "conservation", "exhibition"}
    for ev in record.get("provenance_events") or []:
        if not isinstance(ev, dict):
            continue
        ev_place = str(ev.get("place_text") or "").strip()
        etype = str(ev.get("type") or "").lower()
        if not ev_place or etype not in _TYPED_EVENT_KINDS or ev_place in seen_place_text:
            continue
        coords = _event_coords(ev, ev_place, matches)
        if coords is None:
            continue  # no point → no map stop (never fabricate)
        seen_place_text.add(ev_place)
        ev_year = ev.get("year")
        try:
            ev_year = int(ev_year) if ev_year is not None else None
        except (TypeError, ValueError):
            ev_year = None
        stops.append({
            "kind": etype,
            "label": ev_place,
            "lat": coords[0], "lon": coords[1],
            "year": ev_year,
            "year_earliest": ev.get("year_earliest"),
            "year_latest": ev.get("year_latest"),
            "certain": ev_year is not None,
            "inferred_geo": False,
            "time": float(ev_year) if ev_year is not None else None,
            "has_point": True,
        })

    # ── Significant / related places (undated waypoints) ───────────────
    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        role = (m.get("role") or "").lower()
        if role.startswith("production"):
            continue
        text = str(m.get("entity_text") or "").strip()
        if not text or text in seen_place_text:
            continue
        c = _coords_from_payload(m.get("payload") or {})
        if c is None:
            continue
        seen_place_text.add(text)
        stops.append({
            "kind": "significant_place",
            "label": text,
            "lat": c[0], "lon": c[1],
            "year": None, "year_earliest": None, "year_latest": None,
            "certain": False, "inferred_geo": False,
            "time": None, "has_point": True,
        })

    for rp in record.get("related_places") or []:
        text = str(rp or "").strip()
        if not text or text in seen_place_text:
            continue
        c = resolve_place_coords_from_matches_or_gazetteer(text, matches)
        if c is None:
            continue
        seen_place_text.add(text)
        stops.append({
            "kind": "significant_place",
            "label": text,
            "lat": c[0], "lon": c[1],
            "year": None, "year_earliest": None, "year_latest": None,
            "certain": False, "inferred_geo": False,
            "time": None, "has_point": True,
        })

    # ── Owner stops ────────────────────────────────────────────────────
    owners_dated: list[dict[str, Any]] = []
    owners_undated: list[dict[str, Any]] = []
    seen_owner: set[str] = set()
    for m in matches:
        if m.get("entity_kind") not in ("person", "organization") or not is_owner_role(
            m.get("role", "")
        ):
            continue
        name = str(m.get("entity_text") or m.get("matched_name") or "").strip()
        if not name or name in seen_owner:
            continue
        seen_owner.add(name)
        qid = str(m.get("wikidata_qid") or "").strip()
        payload = m.get("payload") or {}
        birth = payload.get("birth_year")
        death = payload.get("death_year")
        try:
            birth = int(birth) if birth is not None else None
        except (TypeError, ValueError):
            birth = None

        # A1 — approval gate.
        if not include_unapproved and not m.get("approved"):
            dropped.append({"label": name, "reason": "unapproved"})
            continue
        # A2 — confidence gate.
        if (m.get("confidence") or "").lower() not in _ACCEPTED_CONFIDENCE:
            dropped.append({"label": name, "reason": "low_confidence"})
            continue
        # A5 — anachronism: born after the manuscript could not have owned it.
        if (birth is not None and latest_for_anachronism is not None
                and birth > latest_for_anachronism + DATE_BIRTH_BUFFER_YEARS):
            dropped.append({"label": name, "reason": "anachronism"})
            continue

        place = owner_places.get(qid) if qid else None
        if place is None:
            fh = resolve_former_holder_coords(name)
            if fh is not None:
                place = {
                    "lat": fh[0],
                    "lon": fh[1],
                    "geo_source": "holder_seat",
                    "geo_source_label": fh[2],
                }
        if place is None:
            # A7 — no resolvable coordinate → side list, never a fabricated point.
            dropped.append({"label": name, "reason": "no_location"})
            continue

        stop = {
            "kind": "owner",
            "label": name,
            "uri": qid,
            "lat": place["lat"], "lon": place["lon"],
            "year": None,
            "year_earliest": None, "year_latest": None,
            "birth_year": birth, "death_year": death,
            "certain": False, "inferred_geo": True,
            "geo_source": place.get("geo_source"),
            "geo_source_label": place.get("geo_source_label"),
            "approved": bool(m.get("approved")),
            "time": float(birth) if birth is not None else None,
            "has_point": True,
        }
        (owners_dated if birth is not None else owners_undated).append(stop)

    # Attested MARC 942 / 710 former holders (e.g. Mossad ha-Rav Kook, Gaster, Adler).
    for fh_row in record.get("former_holders") or []:
        if not isinstance(fh_row, dict):
            continue
        fh_name = str(fh_row.get("holder") or "").strip()
        if not fh_name or fh_name in seen_owner:
            continue
        resolved_fh = resolve_former_holder_coords(fh_name, fh_row.get("city"))
        if resolved_fh is None:
            continue
        seen_owner.add(fh_name)
        sm = str(fh_row.get("shelfmark") or "").strip()
        label = f"{fh_name} ({sm})" if sm else fh_name
        owners_undated.append({
            "kind": "owner",
            "label": label,
            "uri": None,
            "lat": resolved_fh[0],
            "lon": resolved_fh[1],
            "year": None,
            "year_earliest": None,
            "year_latest": None,
            "birth_year": None,
            "death_year": None,
            "certain": True,
            "inferred_geo": False,
            "geo_source": "marc_942",
            "geo_source_label": resolved_fh[2],
            "approved": True,
            "time": None,
            "has_point": True,
        })

    owners_dated.sort(key=lambda s: s["time"])
    stops.extend(owners_dated)
    stops.extend(owners_undated)

    # ── Current holder (present-day physical holder, last) ─────────────
    holder_stop = resolve_record_holder_stop(record)
    has_holder_point = holder_stop["lat"] is not None and holder_stop["lon"] is not None
    stops.append({
        "kind": "current_holder",
        "label": holder_stop["label"],
        "lat": holder_stop["lat"],
        "lon": holder_stop["lon"],
        "year": None, "year_earliest": None, "year_latest": None,
        "certain": has_holder_point, "inferred_geo": False, "is_present": True,
        "time": None, "has_point": has_holder_point,
    })

    # ── Edges between consecutive geo-located stops ────────────────────
    mapped = [i for i, s in enumerate(stops) if s.get("has_point")]
    edges: list[dict[str, Any]] = []
    for a, b in zip(mapped, mapped[1:], strict=False):
        sa, sb = stops[a], stops[b]
        both_certain = bool(sa.get("certain")) and bool(sb.get("certain"))
        inferred = (not both_certain) or sa.get("inferred_geo") or sb.get("inferred_geo")
        # D2 — no implied direction between two undated stops.
        directed = sa.get("time") is not None or sb.get("time") is not None
        edges.append({"from": a, "to": b, "inferred": bool(inferred), "directed": bool(directed)})

    return {
        "control_number": control_number,
        "ms_label": ms_label,
        "stops": stops,
        "edges": edges,
        "dropped": dropped,
    }


def data_fingerprint(parts: list[str]) -> str:
    """Short stable hash for cache keys — invalidates when the inputs change."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
