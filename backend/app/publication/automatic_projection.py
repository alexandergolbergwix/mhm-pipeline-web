"""Build a new proposal without unsafe nodes or dependent claims."""
from copy import deepcopy
from app.publication.automatic_policy import POLICY_VERSION


def contains_target(value, targets):
    if isinstance(value, str):
        return value in targets
    if isinstance(value, dict):
        return any(contains_target(item, targets) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_target(item, targets) for item in value)
    return False


def build_documents(documents, resolutions, job_id):
    unsafe = set()
    qids = {}
    for key, resolution in resolutions.items():
        if resolution['action'] != 'reuse_existing':
            unsafe.add('__LOCAL:' + key)
            if resolution.get('qid'):
                unsafe.add(resolution['qid'])
        elif resolution['action'] == 'reuse_existing':
            qids['__LOCAL:' + key] = resolution['qid']
    def resolve(value):
        if isinstance(value, str):
            return qids.get(value, value)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [resolve(item) for item in value]
        return value
    result = {}
    for key, original in documents.items():
        resolution = resolutions[key]
        item = deepcopy(original)
        statements = item.get('statements') or []
        item['policy_original_statements'] = deepcopy(statements)
        item['publication_policy'] = {'version': POLICY_VERSION, 'job_id': str(job_id), **resolution}
        item.pop('publication_reference_only', None)
        item.pop('publication_deferred', None)
        item['deferred_statements'] = []
        if resolution['action'] == 'reuse_existing':
            item['existing_qid'] = resolution['qid']
            item['publication_reference_only'] = {'qid': resolution['qid'], 'remote_revision': resolution['remote_revision']}
            item['statements'] = []
        elif resolution['action'] == 'defer':
            item['publication_deferred'] = resolution['reason']
            item['existing_qid'] = None
            item['statements'] = []
        else:
            item['existing_qid'] = None
            item['statements'] = []
            for index, statement in enumerate(statements):
                if index not in resolution['statement_indices'] or contains_target(statement, unsafe):
                    item['deferred_statements'].append(statement)
                else:
                    item['statements'].append(resolve(statement))
            if not any((row.get('property') or row.get('property_id')) == 'P31' for row in item['statements']):
                item['publication_deferred'] = 'No supported instance-of statement remains after dependency checks.'
        result[key] = item
    return result
