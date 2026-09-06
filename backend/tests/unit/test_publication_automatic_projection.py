from app.publication.automatic_projection import build_documents


def test_automatic_projection_defers_unsafe_dependencies_and_resolves_qualifiers():
    docs = {'a': {'statements': [{'property': 'P50', 'value': '__LOCAL:b'},
        {'property': 'P31', 'value': 'Q1', 'qualifiers': [{'value': '__LOCAL:c'}]}]},
        'b': {'statements': []}, 'c': {'statements': []}}
    decisions = {'a': {'action': 'create', 'statement_indices': [0, 1], 'reason': 'Supported'},
        'b': {'action': 'defer', 'reason': 'Wrong identity', 'qid': 'Q2'},
        'c': {'action': 'reuse_existing', 'qid': 'Q3', 'remote_revision': 7, 'reason': 'Same identity'}}
    result = build_documents(docs, decisions, 'job')
    assert result['a']['statements'] == [{'property': 'P31', 'value': 'Q1', 'qualifiers': [{'value': 'Q3'}]}]
    assert result['a']['deferred_statements'] == [{'property': 'P50', 'value': '__LOCAL:b'}]
    assert result['b']['publication_deferred'] == 'Wrong identity'
    assert result['c']['statements'] == []
    assert docs['a']['statements'][0]['value'] == '__LOCAL:b'


def test_loss_of_required_type_defers_the_whole_item():
    docs = {'a': {'statements': [{'property': 'P31', 'value': '__LOCAL:b'},
        {'property': 'P1476', 'value': 'A title'}]}, 'b': {'statements': []}}
    decisions = {'a': {'action': 'create', 'statement_indices': [0, 1], 'reason': 'Source support'},
        'b': {'action': 'defer', 'reason': 'Unknown type'}}
    assert build_documents(docs, decisions, 'job')['a'].get('publication_deferred')
