"""Export quality gate for HMO Wikibase entity drafts."""

from __future__ import annotations

from converter.wikibase.hmo_export_quality import audit_entity_drafts
from converter.wikibase.models import WikibaseEntityDraft


def assert_export_quality(drafts: list[WikibaseEntityDraft]) -> None:
    """Fail fast when exported drafts violate label/description hygiene."""
    issues = audit_entity_drafts(drafts)
    if not issues:
        return
    sample = issues[:12]
    lines = [
        f"{issue.code} {issue.local_id} ({issue.entity_type}): {issue.message}"
        for issue in sample
    ]
    suffix = f" (+{len(issues) - len(sample)} more)" if len(issues) > len(sample) else ""
    raise ValueError(
        f"HMO export quality gate failed with {len(issues)} issue(s){suffix}:\n"
        + "\n".join(lines),
    )
