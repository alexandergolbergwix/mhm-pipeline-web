from eval_agent.evaluators import REGISTRY


def test_publication_review_includes_exact_proposal_and_remote_revision():
    evaluator = REGISTRY['wikidata_publication_review']()
    evidence = {'target': 'www.wikidata.org', 'qid': 'Q123', 'revision': 7,
        'entity_digest': 'digest-7', 'proposed_entity': {'statements': [{'property': 'P214', 'value': '123'}]},
        'remote_entity': {'id': 'Q123', 'claims': {'P214': [{'value': '123'}]}}}
    candidate = list(evaluator.extract_candidates(ner_record={'local_id': 'work:1',
        'entity_type': 'work', 'labels': {'en': 'Work 1'}, 'publication_review': evidence},
        marc_record={}, threshold=0))[0]
    assert candidate.payload['publication_review'] == evidence
    prompt = evaluator.build_prompt(candidate)
    assert 'digest-7' in prompt and 'P214' in prompt and 'Q123' in prompt
    assert 'same entity' in prompt
    assert 'every proposed claim' in prompt
    assert 'untrusted data' in prompt


def test_publication_review_ignores_ordinary_studio_rows():
    evaluator = REGISTRY['wikidata_publication_review']()
    assert list(evaluator.extract_candidates(ner_record={'local_id': 'work:ordinary',
        'entity_type': 'work', 'labels': {'en': 'Ordinary work'}}, marc_record={}, threshold=0)) == []


def test_automatic_review_preserves_structured_identity_and_claim_decisions():
    evaluator = REGISTRY['wikidata_publication_review']()
    candidate = list(evaluator.extract_candidates(ner_record={'local_id': 'work:1', 'entity_type': 'work',
        'labels': {'en': 'Work'}, 'publication_review': {'automatic': True, 'evidence': []}},
        marc_record={}, threshold=0))[0]
    decision = {'identity': 'unresolved', 'identity_evidence': [], 'labels_supported': False,
        'claims': [], 'reason': 'No primary source.'}
    verdict = evaluator.parse_verdict({'name_ok': 'no', 'type_ok': 'yes', 'role_ok': 'n/a',
        'overall': 'partial', 'reasoning': 'No primary source.', 'publication_decision': decision}, candidate)
    assert verdict.to_jsonl_record()['verdict']['publication_decision'] == decision
    assert 'publication_decision' in evaluator.build_prompt(candidate)
