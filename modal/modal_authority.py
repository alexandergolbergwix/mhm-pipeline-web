"""Modal app — Mazal + KIMA authority enrichment for the MHM Pipeline.

Two serverless lookup functions served as a FastAPI ASGI app:
  POST /match_person  — Mazal person authority matching (SQLite, 983 MB index)
  POST /match_place   — KIMA geographic place authority matching (SQLite, 15 MB)

Both databases are baked into the container image at deploy time so
cold start is just opening a file — no network download.

Deploy:
    cd modal/
    modal deploy modal_authority.py

Set on the Heroku backend after deploy:
    heroku config:set AUTHORITY_MODE=modal
    heroku config:set MODAL_AUTHORITY_URL=https://<workspace>--mhm-authority-mhmauthority-web.modal.run

Economics: CPU-only container, ~1 ms per SQLite lookup.
~$0.000002 per call — effectively free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import modal

# ── Paths resolved at deploy time (on the developer's machine) ────────────
# This file is at <web-repo>/modal/modal_authority.py so:
#   PIPELINE_ROOT  → <web-repo>/../../pipeline  (desktop pipeline)
#   BACKEND_ROOT   → <web-repo>/backend

PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent / "pipeline"
BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"

_MAZAL_LOCAL = PIPELINE_ROOT / "converter" / "authority" / "mazal_index.db"
_KIMA_LOCAL = BACKEND_ROOT / "data" / "kima" / "kima_index.db"

# ── Image ─────────────────────────────────────────────────────────────────
# Build order:
#   1. pip_install  — pinned runtime deps
#   2. add_local_dir — vendor the authority matcher source (copy=True required
#      when a run_commands step follows, per Modal's constraint)
#   3. run_commands  — create __init__ stubs so Python sees the packages
#   4. add_local_file — bake the Mazal + KIMA SQLite DBs into the image
#      (last because adding large local files after copy=True dirs is fine;
#      we want the expensive DB layer cached separately from source changes)

authority_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(["rapidfuzz>=3.0", "sqlalchemy>=2.0"])
    # Vendor the authority matcher modules from the desktop pipeline.
    # ignore=["*.db"] keeps the 983 MB Mazal DB out of this layer — the DB
    # lands in its own add_local_file layer below so it is cached independently
    # of source code changes.
    .add_local_dir(
        local_path=str(PIPELINE_ROOT / "converter" / "authority"),
        remote_path="/root/converter/authority",
        ignore=["*.db", "*.pyc", "__pycache__"],
        copy=True,
    )
    # Stub __init__ files so Python treats the directories as packages.
    .run_commands(
        "touch /root/converter/__init__.py",
        "touch /root/converter/authority/__init__.py",
    )
    .env({"PYTHONPATH": "/root"})
    # Bake the Mazal SQLite database (~983 MB) into the image.
    .add_local_file(str(_MAZAL_LOCAL), "/data/mazal_index.db")
    # Bake the KIMA SQLite database (~15 MB) into the image.
    .add_local_file(str(_KIMA_LOCAL), "/data/kima_index.db")
)

# ── App ───────────────────────────────────────────────────────────────────

app = modal.App("mhm-authority", image=authority_image)


@app.cls(
    cpu=1.0,
    memory=2048,           # 983 MB DB + Python overhead + room to breathe
    scaledown_window=300,  # 5-min warm window — same as NER app
    max_containers=8,
)
class MhmAuthority:
    """Serves Mazal + KIMA lookups over HTTPS."""

    @modal.enter()
    def load(self) -> None:
        import sys  # noqa: PLC0415

        sys.path.insert(0, "/root")

        from converter.authority.mazal_matcher import MazalMatcher  # noqa: PLC0415
        from converter.authority.kima_matcher import KimaMatcher  # noqa: PLC0415

        self._mazal: Any = MazalMatcher(
            index_path="/data/mazal_index.db",
            track_stats=False,
        )
        self._kima: Any = KimaMatcher(db_path="/data/kima_index.db")
        print("MhmAuthority: Mazal + KIMA matchers loaded")

    @modal.asgi_app()
    def web(self):  # type: ignore[override]
        import re  # noqa: PLC0415

        from fastapi import FastAPI  # noqa: PLC0415
        from pydantic import BaseModel  # noqa: PLC0415

        api = FastAPI(title="mhm-authority", docs_url="/docs")

        class PersonMatchRequest(BaseModel):
            name: str

        class PersonMatchResponse(BaseModel):
            matched: bool
            mazal_id: str | None = None
            preferred_name_lat: str | None = None
            preferred_name_heb: str | None = None
            dates: str | None = None
            viaf_id: str | None = None
            birth_year: int | None = None
            death_year: int | None = None

        class PlaceMatchRequest(BaseModel):
            text: str

        class PlaceMatchResponse(BaseModel):
            matched: bool
            wikidata_uri: str | None = None
            kima_id: str | None = None
            kima_heb: str | None = None
            kima_rom: str | None = None
            lat: float | None = None
            lon: float | None = None
            geonames_id: str | None = None

        @api.get("/health")
        def health() -> dict:
            return {"status": "ok"}

        @api.post("/match_person", response_model=PersonMatchResponse)
        def match_person(body: PersonMatchRequest) -> PersonMatchResponse:
            import logging  # noqa: PLC0415

            log = logging.getLogger("mhm-authority")
            try:
                hit = self._mazal.match_person(body.name)
                if hit is None:
                    return PersonMatchResponse(matched=False)
                mazal_id = str(hit)
                details: dict = self._mazal.get_person_details(mazal_id) or {}
                # Parse birth/death years from dates string if present.
                dates_str = str(details.get("dates") or "").strip()
                birth_year: int | None = None
                death_year: int | None = None
                year_match = re.search(r"(\d{4})\s*[-–]\s*(\d{4})", dates_str)
                if year_match:
                    birth_year = int(year_match.group(1))
                    death_year = int(year_match.group(2))
                elif (m := re.search(r"\b(\d{4})\b", dates_str)):
                    birth_year = int(m.group(1))
                return PersonMatchResponse(
                    matched=True,
                    mazal_id=mazal_id,
                    preferred_name_lat=details.get("preferred_name_lat"),
                    preferred_name_heb=details.get("preferred_name_heb"),
                    dates=dates_str or None,
                    viaf_id=str(details["viaf_id"]) if details.get("viaf_id") else None,
                    birth_year=birth_year,
                    death_year=death_year,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("match_person error for %r: %s", body.name, exc)
                return PersonMatchResponse(matched=False)

        @api.post("/match_place", response_model=PlaceMatchResponse)
        def match_place(body: PlaceMatchRequest) -> PlaceMatchResponse:
            import logging  # noqa: PLC0415

            log = logging.getLogger("mhm-authority")
            try:
                uri = self._kima.match_place(body.text)
                if uri is None:
                    return PlaceMatchResponse(matched=False)
                # Enrich from KIMA index (same lookup the backend does via
                # KimaMatcher._lookup → KimaIndex.lookup_place).
                row: dict = {}
                try:
                    idx = self._kima.index
                    if idx is not None:
                        row = idx.lookup_place(body.text) or {}
                except Exception:  # noqa: BLE001
                    pass
                return PlaceMatchResponse(
                    matched=True,
                    wikidata_uri=str(uri),
                    kima_id=str(row["kima_id"]) if row.get("kima_id") is not None else None,
                    kima_heb=row.get("primary_heb"),
                    kima_rom=row.get("primary_rom"),
                    lat=float(row["lat"]) if row.get("lat") is not None else None,
                    lon=float(row["lon"]) if row.get("lon") is not None else None,
                    geonames_id=str(row["geonames_id"]) if row.get("geonames_id") else None,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("match_place error for %r: %s", body.text, exc)
                return PlaceMatchResponse(matched=False)

        return api


# Smoke-test after deploy with:
#   curl https://<workspace>--mhm-authority-mhmauthority-web.modal.run/health
#   curl -X POST ".../match_person" -H "Content-Type: application/json" \
#     -d '{"name": "Moses Maimonides"}'
#   curl -X POST ".../match_place" -H "Content-Type: application/json" \
#     -d '{"text": "ירושלים"}'
