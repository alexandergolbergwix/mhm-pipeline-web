"""Canonical MARC date sources — re-exported from the converter mirror.

The dating policy moved to ``converter.transformer.production_year`` so the
public Wikidata projection can apply it too: P571 used to bypass this module
entirely and encode a century start (16th century → 1501) even when the record
carried a colophon-attested year (Rule W-164). A second copy under
``app/pipeline`` would be exactly the drift Rule W-142 exists to prevent, so this
module is a thin re-export and the four existing callers are unchanged.
"""

from __future__ import annotations

from converter.transformer.production_year import (
    _parse_year_scalar,
    manuscript_production_year,
    manuscript_year_provenance,
)

__all__ = [
    "_parse_year_scalar",
    "manuscript_production_year",
    "manuscript_year_provenance",
]
