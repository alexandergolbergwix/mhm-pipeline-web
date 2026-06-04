"""Merge all per-run RDF graphs that belong to a project into one graph.

The merge is cached in-process (LRU + dirty invalidation) so repeated
requests to the Research Explorer don't reload TTL files from disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from pathlib import Path

import rdflib

logger = logging.getLogger(__name__)

_STATE_ROOT = Path(__file__).resolve().parents[2] / "state" / "runs"


def _ttl_paths_for_runs(run_ids: list[str]) -> list[Path]:
    paths = []
    for rid in run_ids:
        p = _STATE_ROOT / rid / "manuscripts.ttl"
        if p.exists():
            paths.append(p)
    return paths


def _file_fingerprint(paths: list[Path]) -> str:
    """Hash the combined mtime + size of all TTL files."""
    parts = []
    for p in sorted(paths):
        try:
            st = p.stat()
            parts.append(f"{p}:{st.st_mtime}:{st.st_size}")
        except OSError:
            pass
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# In-process cache keyed by (sorted_run_ids_tuple, fingerprint)
_CACHE: dict[tuple[tuple[str, ...], str], rdflib.Graph] = {}
_MAX_CACHE_ENTRIES = 4


def _load_graph_sync(run_ids: list[str]) -> rdflib.Graph:
    paths = _ttl_paths_for_runs(run_ids)
    if not paths:
        return rdflib.Graph()

    fp = _file_fingerprint(paths)
    cache_key = (tuple(sorted(run_ids)), fp)

    if cache_key in _CACHE:
        return _CACHE[cache_key]

    merged = rdflib.Graph()
    for p in paths:
        try:
            g = rdflib.Graph()
            g.parse(str(p), format="turtle")
            for triple in g:
                merged.add(triple)
            logger.debug("merged %s triples from %s", len(g), p)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", p, exc)

    # Evict old entries when cache is full
    if len(_CACHE) >= _MAX_CACHE_ENTRIES:
        oldest = next(iter(_CACHE))
        del _CACHE[oldest]
    _CACHE[cache_key] = merged
    return merged


async def load_merged_graph(run_ids: list[str]) -> rdflib.Graph:
    """Load and merge TTL files for the given run IDs (async wrapper)."""
    return await asyncio.to_thread(_load_graph_sync, run_ids)


def invalidate_cache(run_id: str) -> None:
    """Drop all cached graphs that include run_id (called after RDF rebuild)."""
    to_delete = [k for k in _CACHE if run_id in k[0]]
    for k in to_delete:
        del _CACHE[k]
