"""Bounded reads of primary sources; source text is untrusted evidence."""
import asyncio
import json
import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlsplit
import httpx
from sqlalchemy import text
from app.pipeline.inference_cache import cache_lookup_or_call
from app.publication.digests import canonical_digest
from app.pipeline.marc_verify_context import load_run_marc_records_scoped
from app.pipeline.wikidata_verdict_cache import record_ids_for_wikidata_item

ALLOWED_HOSTS = {'www.nli.org.il', 'nli.org.il', 'viaf.org', 'www.viaf.org', 'id.loc.gov', 'd-nb.info'}


def source_urls(value):
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme == 'https' and parsed.hostname in ALLOWED_HOSTS and not parsed.username and parsed.port in {None, 443}:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from source_urls(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from source_urls(item)


async def read_source(url):
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
        async with client.stream('GET', url, headers={'User-Agent': 'MHM-Publication-Evidence/1.0'}) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 262144:
                    raise ValueError('The primary source exceeds the evidence size limit')
    content = data.decode('utf-8', errors='replace')
    content = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', content, flags=re.I | re.S)
    content = unescape(re.sub('<[^>]+>', ' ', content))
    return {'text': ' '.join(content.split())[:48000], 'retrieved_at': datetime.now(UTC).isoformat()}


async def collect_evidence(db, document, actor_id, force_refresh=False, *, run_id=None):
    evidence = []
    if run_id is not None:
        record_ids = set(record_ids_for_wikidata_item(document)[:4])
        for record in await load_run_marc_records_scoped(db, run_id, record_ids):
            evidence.append({'id': 'marc:' + record['_control_number'], 'kind': 'primary',
                'source': 'Original imported catalogue record',
                'text': json.dumps(record, ensure_ascii=False, default=str)[:48000]})
    ids = [str(s.get('value')) for s in document.get('statements', [])
        if (s.get('property') or s.get('property_id')) == 'P8189']
    if db.bind.dialect.name == 'postgresql' and ids:
        exists = await db.scalar(text("SELECT to_regclass('public.mazal_authorities') IS NOT NULL"))
        if exists:
            for identifier in sorted(set(ids))[:4]:
                row = (await db.execute(text('SELECT nli_id, preferred_name_heb, preferred_name_lat, dates '
                    'FROM mazal_authorities WHERE nli_id = :identifier'), {'identifier': identifier})).mappings().first()
                if row:
                    evidence.append({'id': f'authority:{identifier}', 'kind': 'primary',
                        'text': json.dumps(dict(row), ensure_ascii=False, default=str),
                        'source': 'Imported NLI authority record', 'retrieved_at': datetime.now(UTC).isoformat()})
    await db.commit()
    for url in sorted(set(source_urls(document)))[:4]:
        try:
            result = await asyncio.wait_for(cache_lookup_or_call(db, kind='publication.evidence', user_id=actor_id,
                query_summary={'url': url, 'reader': 'primary-v1'}, fetch=lambda: read_source(url), skip_cache=force_refresh), timeout=20)
            evidence.append({'id': 'source:' + canonical_digest(url), 'kind': 'primary', 'url': url, **result})
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            evidence.append({'id': 'source:' + canonical_digest(url), 'kind': 'unavailable',
                'url': url, 'text': '', 'error': type(exc).__name__})
    return evidence


async def search_candidates(db, site, label, actor_id, force_refresh=False):
    if site not in {'www.wikidata.org', 'test.wikidata.org'}:
        raise ValueError('Unsupported Wikidata target')
    language = 'he' if re.search(r'[\u0590-\u05ff]', label) else 'en'
    async def fetch():
        async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f'https://{site}/w/api.php', params={'action': 'wbsearchentities',
                'format': 'json', 'language': language, 'search': label[:250], 'type': 'item', 'limit': 3})
            response.raise_for_status()
            if len(response.content) > 262144:
                raise ValueError('Candidate search exceeded its size limit')
            payload = response.json()
            if 'error' in payload or not isinstance(payload.get('search'), list):
                raise ValueError('Candidate search did not complete')
            return [row['id'] for row in payload['search'] if re.fullmatch(r'Q[1-9][0-9]*', str(row.get('id')))][:3]
    return await cache_lookup_or_call(db, kind='wikidata.candidate_search', user_id=actor_id,
        query_summary={'site': site, 'label': label, 'language': language, 'reader': 'candidate-v1'},
        fetch=fetch, skip_cache=force_refresh)
