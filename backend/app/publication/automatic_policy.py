"""Conservative policy for automatic Publication decisions; no foreign consent."""
from typing import Literal
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, ValidationError

POLICY_VERSION = 'reference-first-v1'
STRONG_IDS = {'P214', 'P8189', 'P244', 'P227', 'P213', 'P268', 'P3959'}


class ClaimDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    index: int = Field(ge=0)
    status: Literal['supported', 'unsupported', 'conflicting', 'already_present']
    evidence: list[str]


class IdentityDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    identity: Literal['same_entity', 'different_entity', 'unresolved', 'new_entity']
    identity_evidence: list[str]
    labels_supported: bool
    claims: list[ClaimDecision]
    reason: str = Field(min_length=1)


def shared_identifiers(document, remote):
    matches = set()
    for statement in document.get('statements') or []:
        prop = statement.get('property') or statement.get('property_id')
        value = str(statement.get('value') or '').strip()
        if prop not in STRONG_IDS or not value:
            continue
        for claim in (remote or {}).get('claims', {}).get(prop, []):
            other = claim.get('mainsnak', {}).get('datavalue', {}).get('value')
            if str(other).strip() == value:
                matches.add(value)
    return matches



def same_work_and_author(document, remote):
    if document.get('entity_type') != 'work':
        return False
    def normal(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold() if c.isalnum())
    titles = {normal(value) for value in (document.get('labels') or {}).values() if value}
    remote_titles = {normal(value.get('value')) for value in (remote or {}).get('labels', {}).values() if value.get('value')}
    authors = {s.get('value') for s in document.get('statements', [])
        if (s.get('property') or s.get('property_id')) == 'P50' and isinstance(s.get('value'), str) and s['value'].startswith('Q')}
    remote_authors = {c.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
        for c in (remote or {}).get('claims', {}).get('P50', [])
        if isinstance(c.get('mainsnak', {}).get('datavalue', {}).get('value'), dict)}
    return bool(titles & remote_titles and authors & remote_authors)


def decide(document, remote, observation, first, second, evidence):
    def defer(reason):
        return {'action': 'defer', 'statement_indices': [], 'reason': reason}
    try:
        reviews = [IdentityDecision.model_validate(value) for value in (first, second)]
    except ValidationError:
        return defer('A structured review is incomplete or invalid.')
    primary = {row['id']: row['text'] for row in evidence if row.get('kind') == 'primary' and row.get('text')}
    for review in reviews:
        if not review.identity_evidence or not set(review.identity_evidence) <= primary.keys():
            return defer('Identity lacks cited primary evidence.')
    if observation in {'present_foreign', 'present_owned'}:
        ids = shared_identifiers(document, remote)
        identifier_supported = bool(ids) and all(any(value in primary[key] for value in ids for key in review.identity_evidence) for review in reviews)
        if not identifier_supported and not same_work_and_author(document, remote):
            return defer('The target lacks a shared identifier verified against primary evidence.')
        if all(review.identity == 'same_entity' for review in reviews):
            return {'action': 'reuse_existing', 'statement_indices': [],
                'reason': 'Both checks support identity. The existing item receives no updates.'}
        return defer('The identity checks disagree or identify a different entity.')
    if observation != 'absent' or not all(review.identity == 'new_entity' and review.labels_supported for review in reviews):
        return defer('A complete duplicate check and supported labels are required for creation.')
    statements = document.get('statements') or []
    claim_sets = []
    for review in reviews:
        indices = [claim.index for claim in review.claims]
        if sorted(indices) != list(range(len(statements))):
            return defer('Each proposed statement requires exactly one decision.')
        claim_sets.append({claim.index for claim in review.claims if claim.status == 'supported'
            and claim.evidence and set(claim.evidence) <= primary.keys()})
    kept = sorted(claim_sets[0] & claim_sets[1])
    if not any((statements[index].get('property') or statements[index].get('property_id')) == 'P31' for index in kept):
        return defer('A supported instance-of statement is required for creation.')
    if not kept:
        return defer('No statement has independent source support.')
    return {'action': 'create', 'statement_indices': kept,
        'reason': 'Both checks support a new item and its retained claims.'}
