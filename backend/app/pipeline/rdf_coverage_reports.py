"""Child-process RDF post-processing (graph index + coverage reports).

Invoked by ``rdf_build._run_coverage_reports_subprocess`` so the job
parent never re-parses the full artifact into an rdflib Graph — that
spike is what R14/R15-killed the 512 MB dyno (job-service R26).

Usage:
    python -m app.pipeline.rdf_coverage_reports <artifact.ttl> \
        [--stats-out rdf_build_stats.json]

Writes, next to the artifact:
- ``graph_catalog.json`` + ``graph_index.sqlite``  (graph_index)
- ``rdf_projection_coverage.json``                 (projection coverage)
- ``ontology_coverage.json``                       (ontology coverage)
- ``rdf_build_stats.json``                         (small summary for the parent)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Path to the built .ttl artifact")
    parser.add_argument(
        "--stats-out", type=Path, default=None,
        help="Where to write the small JSON summary (default: sibling rdf_build_stats.json)",
    )
    args = parser.parse_args()

    artifact: Path = args.artifact
    run_dir = artifact.parent
    stats_out: Path = args.stats_out or (run_dir / "rdf_build_stats.json")

    from rdflib import Graph

    from app.pipeline.graph_index import build_and_persist_index

    # One full-graph parse; this process's whole purpose is to absorb
    # this spike so the job parent never does.
    graph = Graph().parse(str(artifact), format="turtle")

    catalog = build_and_persist_index(graph, run_dir)
    manuscripts = catalog.manuscript_count

    unknown_count: int | None = None
    coverage_path = run_dir / "rdf_projection_coverage.json"
    try:
        from converter.wikidata.projection_coverage import (  # noqa: PLC0415
            write_projection_coverage_report,
        )

        write_projection_coverage_report(artifact, [], coverage_path)
        report = json.loads(coverage_path.read_text(encoding="utf-8"))
        unknown_count = sum(
            1 for cls in report.get("classes", [])
            if cls.get("projection_status") == "unknown"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"projection coverage report failed: {exc}", file=sys.stderr)

    ontology_class_count: int | None = None
    ontology_property_count: int | None = None
    ontology_missing_terms: list[str] = []
    try:
        from converter.rdf.ontology_coverage import (  # noqa: PLC0415
            build_coverage_report,
            write_coverage_report,
        )

        ontology_path = Path(__file__).resolve().parents[2] / "ontology" / "hebrew-manuscripts.ttl"
        ontology_report = build_coverage_report(artifact, ontology_path)
        write_coverage_report(ontology_report, run_dir / "ontology_coverage.json")
        ontology_class_count = ontology_report.classes_covered
        ontology_property_count = ontology_report.properties_covered
        ontology_missing_terms = (
            ontology_report.missing_classes + ontology_report.missing_properties
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ontology coverage report failed: {exc}", file=sys.stderr)

    stats_out.write_text(
        json.dumps({
            "triples_count": len(graph),
            "manuscripts_count": manuscripts,
            "unknown_count": unknown_count,
            "ontology_class_count": ontology_class_count,
            "ontology_property_count": ontology_property_count,
            "ontology_missing_terms": ontology_missing_terms,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
