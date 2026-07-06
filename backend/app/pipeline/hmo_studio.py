"""HMO Wikibase Studio backend.

Glues three desktop modules into the web pipeline:

* :class:`converter.wikidata.iiif_manifest_builder.IiifManifestBuilder`
  — pure RDF → IIIF Presentation API 3.0 manifest builder.
* :class:`converter.wikidata.iiif_uploader.IiifManifestUploader` +
  :class:`converter.wikibase.cloud_client.WikibaseCloudWriter`
  — authenticated upload to ``mhm-hmo.wikibase.cloud``.
* :func:`converter.wikidata.projection_coverage.build_projection_coverage_report`
  — JSON report of HMO classes mapped vs. unmapped to Wikidata.

All three desktop modules are sync-by-design (the desktop pipeline runs
them on a Qt worker thread). The web wraps each one in
``asyncio.to_thread`` so the FastAPI event loop stays free.

The cloud writer reads credentials from constructor kwargs only — the
desktop's keychain-bound ``SettingsManager`` is never imported. The
router unwraps the user's stored bot password (via the encrypted-key
flow lifted from :func:`ai_verifier.unwrap_user_gemini_key`) and passes
the plaintext to :func:`upload_manifests_for_run`.

Trust boundary (Rule 45, ``CLAUDE.md`` in the desktop repo): writing to
``wikibase.cloud`` is a separate trust boundary from ``wikidata.org``.
Rule 25's moratorium gate applies ONLY to wikidata.org and does NOT
apply here. The cloud writer still enforces ``assert=bot``, idempotency,
and exponential-backoff retry on every edit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── On-disk locations ──────────────────────────────────────────────────


_STATE_ROOT = Path(__file__).resolve().parents[2] / "state" / "runs"


def manifest_dir_for_run(run_id: str) -> Path:
    """Per-run IIIF manifest output directory.

    Mirrors :func:`app.pipeline.rdf_build.rdf_output_path_for_run` so the
    Studio router can find both the source ``manuscripts.ttl`` and the
    generated manifests in the same per-run state directory.
    """
    return _STATE_ROOT / run_id / "iiif_manifests"


def coverage_path_for_run(run_id: str) -> Path:
    """Canonical location of the cached projection-coverage JSON."""
    return _STATE_ROOT / run_id / "hmo_projection_coverage.json"


def upload_report_path_for_run(run_id: str) -> Path:
    """Canonical location of the cached IIIF upload-report JSON."""
    return _STATE_ROOT / run_id / "iiif_upload_report.json"


# ── Result shapes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class HmoBuildResult:
    """Outcome of an HMO manifest-build pass."""

    manifest_count: int
    total_canvases: int
    total_ranges: int
    total_annotations: int
    manifest_dir: str
    manifests: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class HmoUploadOutcome:
    """One manifest's upload outcome."""

    shelfmark: str
    page_url: str
    status: str  # "created" | "updated" | "unchanged" | "failed" | "dry_run"
    message: str
    edit_id: int | None
    new_revid: int | None
    canvas_count: int
    range_count: int
    annotation_count: int


@dataclass(frozen=True)
class HmoUploadResult:
    """Aggregate outcome of an HMO upload pass."""

    dry_run: bool
    uploaded: int
    unchanged: int
    failed: int
    outcomes: list[HmoUploadOutcome] = field(default_factory=list)


# ── Build manifests ────────────────────────────────────────────────────


_DEFAULT_BASE_URL = "https://mhm-hmo.wikibase.cloud"


async def build_manifests_for_run(
    *, ttl_path: Path, manifest_dir: Path,
    base_url: str = _DEFAULT_BASE_URL,
) -> HmoBuildResult:
    """Generate one IIIF manifest per manuscript in *ttl_path*.

    Manifests are written to ``manifest_dir/MS_<shelfmark>.json``. The
    directory is created if absent. Existing files are overwritten — the
    builder is deterministic so re-running on the same TTL produces the
    same bytes.
    """
    return await asyncio.to_thread(
        _build_manifests_sync, ttl_path, manifest_dir, base_url,
    )


def _build_manifests_sync(
    ttl_path: Path, manifest_dir: Path, base_url: str,
) -> HmoBuildResult:
    from rdflib import Graph  # noqa: PLC0415

    from converter.wikidata.iiif_manifest_builder import IiifManifestBuilder  # noqa: PLC0415

    if not ttl_path.exists():
        raise FileNotFoundError(
            f"RDF artefact missing: {ttl_path}. Build the RDF graph first.",
        )

    graph = Graph()
    graph.parse(str(ttl_path), format="turtle")
    builder = IiifManifestBuilder(graph, base_url=base_url)

    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifests_summary: list[dict[str, Any]] = []
    total_canvases = 0
    total_ranges = 0
    total_annotations = 0
    count = 0
    for shelfmark, manifest, stats in builder.build_all():
        out_path = manifest_dir / f"MS_{shelfmark}.json"
        out_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifests_summary.append(
            {
                "shelfmark": shelfmark,
                "file": out_path.name,
                "canvas_count": stats.canvas_count,
                "range_count": stats.range_count,
                "annotation_count": stats.annotation_count,
                "seealso_count": stats.seealso_count,
            }
        )
        total_canvases += stats.canvas_count
        total_ranges += stats.range_count
        total_annotations += stats.annotation_count
        count += 1

    return HmoBuildResult(
        manifest_count=count,
        total_canvases=total_canvases,
        total_ranges=total_ranges,
        total_annotations=total_annotations,
        manifest_dir=str(manifest_dir),
        manifests=manifests_summary,
    )


# ── Upload manifests ───────────────────────────────────────────────────


async def upload_manifests_for_run(
    *,
    manifest_dir: Path,
    writer: Any,
    dry_run: bool = True,
    db: Any | None = None,
    audit_ctx: Any | None = None,
) -> HmoUploadResult:
    """Upload every ``MS_*.json`` manifest from *manifest_dir* to the
    Wikibase Cloud under the ``IIIF:`` namespace.

    ``dry_run=True`` (the default) skips the network call entirely and
    reports what would be sent — same shape as a live upload.
    """
    result = await asyncio.to_thread(
        _upload_manifests_sync,
        manifest_dir, writer, dry_run,
    )
    if audit_ctx is not None and db is not None and not dry_run:
        from app.models.wikibase_cloud_write import (  # noqa: PLC0415
            OPERATION_CREATE,
            OPERATION_FAILED,
            OPERATION_UNCHANGED,
            OPERATION_UPDATE,
            TARGET_PAGE,
        )
        from app.services.wikibase_audit import record_wikibase_write  # noqa: PLC0415

        op_map = {
            "created": OPERATION_CREATE,
            "updated": OPERATION_UPDATE,
            "unchanged": OPERATION_UNCHANGED,
            "failed": OPERATION_FAILED,
        }
        for outcome in result.outcomes:
            operation = op_map.get(outcome.status, OPERATION_FAILED)
            await record_wikibase_write(
                db, audit_ctx,
                operation=operation,
                target_kind=TARGET_PAGE,
                target_key=f"IIIF:{outcome.shelfmark}",
                outcome_message=outcome.message or "ok",
            )
    return result


def _upload_manifests_sync(
    manifest_dir: Path,
    writer: Any,
    dry_run: bool,
) -> HmoUploadResult:
    from converter.wikidata.iiif_manifest_builder import BuildStats  # noqa: PLC0415
    from converter.wikidata.iiif_uploader import IiifManifestUploader  # noqa: PLC0415

    if not manifest_dir.exists():
        raise FileNotFoundError(
            f"Manifest directory missing: {manifest_dir}. Build manifests first.",
        )

    uploader = IiifManifestUploader(writer, dry_run=dry_run)

    outcomes: list[HmoUploadOutcome] = []
    uploaded = 0
    unchanged = 0
    failed = 0

    for manifest_path in sorted(manifest_dir.glob("MS_*.json")):
        shelfmark = manifest_path.stem[len("MS_"):]
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable manifest %s: %s", manifest_path, exc)
            failed += 1
            outcomes.append(HmoUploadOutcome(
                shelfmark=shelfmark,
                page_url="",
                status="failed",
                message=f"Could not read manifest: {exc}",
                edit_id=None, new_revid=None,
                canvas_count=0, range_count=0, annotation_count=0,
            ))
            continue

        # Reconstruct stats from the manifest body. canvas_count = len(items);
        # range_count = len(structures); annotation_count = sum over annotation
        # pages. seealso_count is not needed here (already on disk).
        canvas_count = len(payload.get("items") or [])
        range_count = len(payload.get("structures") or [])
        annotation_count = sum(
            len((page or {}).get("items") or [])
            for page in (payload.get("annotations") or [])
        )
        stats = BuildStats(
            canvas_count=canvas_count,
            range_count=range_count,
            annotation_count=annotation_count,
            seealso_count=len(payload.get("seeAlso") or []),
        )

        try:
            result = uploader.upload(shelfmark, payload, stats)
        except Exception as exc:  # noqa: BLE001 - never let one bad manifest kill the batch
            logger.warning("Upload failed for %s: %s", shelfmark, exc)
            failed += 1
            outcomes.append(HmoUploadOutcome(
                shelfmark=shelfmark,
                page_url="",
                status="failed",
                message=str(exc),
                edit_id=None, new_revid=None,
                canvas_count=canvas_count,
                range_count=range_count,
                annotation_count=annotation_count,
            ))
            continue

        outcome = HmoUploadOutcome(
            shelfmark=result.shelfmark,
            page_url=result.page_url,
            status=result.status,
            message=result.message,
            edit_id=result.edit_id,
            new_revid=result.new_revid,
            canvas_count=result.canvas_count,
            range_count=result.range_count,
            annotation_count=result.annotation_count,
        )
        outcomes.append(outcome)
        if result.status in ("created", "updated", "dry_run"):
            uploaded += 1
        elif result.status == "unchanged":
            unchanged += 1
        else:
            failed += 1

    return HmoUploadResult(
        dry_run=dry_run,
        uploaded=uploaded,
        unchanged=unchanged,
        failed=failed,
        outcomes=outcomes,
    )


def cache_upload_report(run_id: str, result: HmoUploadResult) -> Path:
    """Persist the upload-report JSON next to the run's other artefacts."""
    path = upload_report_path_for_run(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = asdict(result)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ── Coverage report ────────────────────────────────────────────────────


async def coverage_report_for_run(*, ttl_path: Path) -> dict[str, Any]:
    """Build the HMO → Wikidata projection-coverage report for *ttl_path*.

    Uses the desktop's :func:`build_projection_coverage_report` —
    no logic is duplicated here. The report is a JSON-serialisable dict
    listing every RDF class in the graph and its mapping strategy.
    """
    return await asyncio.to_thread(_coverage_sync, ttl_path)


def _coverage_sync(ttl_path: Path) -> dict[str, Any]:
    from converter.wikidata.hmo_crosswalk import build_items_from_hmo_ttl  # noqa: PLC0415
    from converter.wikidata.projection_coverage import (  # noqa: PLC0415
        build_projection_coverage_report,
    )

    if not ttl_path.exists():
        raise FileNotFoundError(
            f"RDF artefact missing: {ttl_path}. Build the RDF graph first.",
        )

    # Build the Wikidata projection from the HMO TTL so the coverage
    # report can show real per-property counts (instead of zero).
    result = build_items_from_hmo_ttl(ttl_path)
    return build_projection_coverage_report(ttl_path, result.items)


def cache_coverage_report(run_id: str, report: dict[str, Any]) -> Path:
    """Persist the coverage report JSON; returns the on-disk path."""
    path = coverage_path_for_run(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ── Durable (Postgres) coverage cache ───────────────────────────────────
#
# The on-disk cache above lives under the dyno's local filesystem, which
# every Heroku deploy or dyno restart wipes. Coverage builds parse the RDF
# TTL twice via rdflib and can take 9-14 minutes on a large corpus, so
# losing the cache on every dyno recycle forced curators to wait through a
# full rebuild repeatedly for an RDF graph that had not actually changed.
# This mirrors ``RdfArtifact`` (the TTL's own durable-restore mechanism)
# and ``HmoStudioItemCache`` (the item-build cache's fingerprint pattern).


def _hash_ttl_bytes(ttl_path: Path) -> str:
    return hashlib.sha256(ttl_path.read_bytes()).hexdigest()


async def compute_coverage_fingerprint(ttl_path: Path) -> str:
    """SHA-256 over the RDF TTL bytes the coverage report is built from.

    The coverage report is a pure function of the TTL (no schema-mapping
    or other DB state factors in, unlike the item-build cache), so the
    TTL hash alone is a sufficient cache key.
    """
    return await asyncio.to_thread(_hash_ttl_bytes, ttl_path)


async def load_cached_coverage_from_db(
    db: AsyncSession, run_id: uuid.UUID, fingerprint: str,
) -> dict[str, Any] | None:
    """Return the Postgres-cached report for *run_id* iff the fingerprint
    still matches the current RDF graph; ``None`` on a miss."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.hmo_coverage_cache import HmoCoverageCache  # noqa: PLC0415

    row = (
        await db.execute(
            select(HmoCoverageCache).where(HmoCoverageCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    if row is None or row.input_fingerprint != fingerprint:
        return None
    return row.report


async def save_coverage_to_db(
    db: AsyncSession, run_id: uuid.UUID, fingerprint: str, report: dict[str, Any],
) -> None:
    """Upsert the durable coverage cache row for *run_id*."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.hmo_coverage_cache import HmoCoverageCache  # noqa: PLC0415

    existing = (
        await db.execute(
            select(HmoCoverageCache).where(HmoCoverageCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(HmoCoverageCache(
            run_id=run_id, input_fingerprint=fingerprint, report=report,
        ))
    else:
        existing.input_fingerprint = fingerprint
        existing.report = report
    await db.commit()
