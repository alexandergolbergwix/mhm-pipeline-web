"""Policy outcomes require evidence, independent decisions, and duplicate checks."""
import pytest
from app.publication.automatic_policy import decide


def fixture():
    document = {'labels': {'en': 'Author'}, 'statements': [
        {'property': 'P8189', 'value': '123'}, {'property': 'P2888', 'value': 'https://mirror.invalid/Q1'}]}
    remote = {'claims': {'P8189': [{'mainsnak': {'datavalue': {'value': '123'}}}]}}
    evidence = [{'id': 'authority:123', 'kind': 'primary', 'text': 'Authority 123: Author'}]
    verdict = {'identity': 'same_entity', 'identity_evidence': ['authority:123'], 'labels_supported': True,
        'claims': [{'index': 0, 'status': 'supported', 'evidence': ['authority:123']},
                   {'index': 1, 'status': 'unsupported', 'evidence': []}], 'reason': 'Same authority, unsupported mirror link.'}
    return document, remote, evidence, verdict


def test_same_identity_can_reuse_without_unsupported_updates():
    doc, remote, evidence, verdict = fixture()
    result = decide(doc, remote, 'present_foreign', verdict, verdict, evidence)
    assert result['action'] == 'reuse_existing'
    assert result['statement_indices'] == []


@pytest.mark.parametrize('fault', ['conflict', 'missing_source', 'label_only', 'disagreement', 'unknown'])
def test_automatic_policy_defers_instead_of_inventing_approval(fault):
    doc, remote, evidence, verdict = fixture()
    other = dict(verdict)
    status = 'present_foreign'
    if fault == 'conflict': verdict['identity'] = 'different_entity'
    if fault == 'missing_source': evidence = []
    if fault == 'label_only': remote = {'claims': {}}
    if fault == 'disagreement': other['identity'] = 'unresolved'
    if fault == 'unknown': status = 'unknown'
    assert decide(doc, remote, status, verdict, other, evidence)['action'] == 'defer'


def test_create_keeps_only_independently_supported_claims():
    doc, remote, evidence, verdict = fixture()
    verdict['identity'] = 'new_entity'
    doc['statements'].append({'property': 'P31', 'value': 'Q5'})
    verdict['claims'].append({'index': 2, 'status': 'supported', 'evidence': ['authority:123']})
    assert decide(doc, None, 'absent', verdict, verdict, evidence) == {
        'action': 'create', 'statement_indices': [0, 2], 'reason': 'Both checks support a new item and its retained claims.'}
    verdict['claims'].append(verdict['claims'][0])
    assert decide(doc, None, 'absent', verdict, verdict, evidence)['action'] == 'defer'


def test_work_identity_can_use_title_and_verified_author_with_primary_evidence():
    doc = {'entity_type': 'work', 'labels': {'he': 'מגיד מישרים'}, 'statements': [{'property': 'P50', 'value': 'Q467148'}]}
    remote = {'labels': {'he': {'value': 'מגיד מישרים'}}, 'claims': {
        'P50': [{'mainsnak': {'datavalue': {'value': {'id': 'Q467148'}}}}]}}
    evidence = [{'id': 'marc:1', 'kind': 'primary', 'text': 'מגיד מישרים by Joseph Karo'}]
    review = {'identity': 'same_entity', 'identity_evidence': ['marc:1'], 'labels_supported': True,
        'claims': [], 'reason': 'Catalogue supports the title and author.'}
    assert decide(doc, remote, 'present_foreign', review, review, evidence)['action'] == 'reuse_existing'
    remote['claims']['P50'][0]['mainsnak']['datavalue']['value']['id'] = 'Q123'
    assert decide(doc, remote, 'present_foreign', review, review, evidence)['action'] == 'defer'
