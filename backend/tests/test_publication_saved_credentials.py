"""Saved credentials remain user-bound and target-bound in Publication."""
import uuid

import pytest

from app.crypto.kek import derive_kek
from app.publication.credentials import SavedPublicationCredentialResolver


@pytest.mark.asyncio
async def test_publication_uses_both_saved_credentials_without_server_tokens(auth_user, db_session, monkeypatch):
    user, client = auth_user
    monkeypatch.setattr('app.publication.credentials.get_settings', lambda: type('Settings', (), {
        'wikidata_publication_live_token': '', 'wikidata_publication_test_token': '',
    })())
    for name, value in [('wikidata', 'Fixture@Live:live-fixture'), ('wikidata_test', 'Fixture@Test:test-fixture')]:
        response = await client.put(f'/api/me/api-keys/{name}', json={'value': value})
        assert response.status_code == 200
    resolver = SavedPublicationCredentialResolver(db_session, user.id, derive_kek(user._test_password, salt=user.kek_salt))
    live = await resolver.resolve(f'wikidata:production:{user.id}')
    test = await resolver.resolve(f'wikidata:test:{user.id}')
    assert live.secret == 'Fixture@Live:live-fixture'
    assert test.secret == 'Fixture@Test:test-fixture'
    assert live.target.site == 'www.wikidata.org'
    assert test.target.site == 'test.wikidata.org'
    with pytest.raises(ValueError, match='account'):
        await resolver.resolve(f'wikidata:production:{uuid.uuid4()}')


@pytest.mark.asyncio
async def test_worker_credential_is_encrypted_expiring_and_execution_bound():
    import time
    from app.publication.credentials import ExecutionCredentialResolver, seal_execution_credential
    from app.publication.types import TargetRef
    from app.publication.wikidata_gateway import CredentialMaterial
    material = CredentialMaterial(credential_id='wikidata:production:curator',
        target=TargetRef(site='www.wikidata.org', environment='production'), secret='fixture-worker-secret')
    envelope = seal_execution_credential(material, publication_id='pub-1', execution_id='exec-1')
    assert material.secret not in envelope
    resolver = ExecutionCredentialResolver(envelope, publication_id='pub-1', execution_id='exec-1', actor_id='curator')
    assert (await resolver.resolve(material.credential_id)).secret == material.secret
    for pub, execution, actor, ref in [
        ('pub-2', 'exec-1', 'curator', material.credential_id),
        ('pub-1', 'exec-2', 'curator', material.credential_id),
        ('pub-1', 'exec-1', 'other', material.credential_id),
        ('pub-1', 'exec-1', 'curator', 'wikidata:test:curator'),
    ]:
        with pytest.raises(ValueError, match='credential'):
            await ExecutionCredentialResolver(envelope, publication_id=pub, execution_id=execution, actor_id=actor).resolve(ref)
    import base64
    damaged = bytearray(base64.b64decode(envelope))
    damaged[-1] ^= 1
    tampered = base64.b64encode(damaged).decode("ascii")
    with pytest.raises(ValueError, match='credential'):
        await ExecutionCredentialResolver(tampered, publication_id='pub-1', execution_id='exec-1', actor_id='curator').resolve(material.credential_id)
    expired = seal_execution_credential(material, publication_id='pub-1', execution_id='exec-1', expires_at=time.time()-1)
    with pytest.raises(ValueError, match='expired'):
        await ExecutionCredentialResolver(expired, publication_id='pub-1', execution_id='exec-1', actor_id='curator').resolve(material.credential_id)
    with pytest.raises(ValueError, match='credential'):
        await ExecutionCredentialResolver('invalid', publication_id='pub-1', execution_id='exec-1', actor_id='curator').resolve(material.credential_id)


@pytest.mark.asyncio
async def test_saved_test_credential_cannot_supply_a_production_request(auth_user, db_session, monkeypatch):
    user, client = auth_user
    monkeypatch.setattr('app.publication.credentials.get_settings', lambda: type('Settings', (), {
        'wikidata_publication_live_token': '', 'wikidata_publication_test_token': '',
    })())
    response = await client.put('/api/me/api-keys/wikidata_test', json={'value':'Fixture@Test:only-test-fixture'})
    assert response.status_code == 200
    resolver = SavedPublicationCredentialResolver(db_session, user.id, derive_kek(user._test_password, salt=user.kek_salt))
    with pytest.raises(ValueError, match='not configured'):
        await resolver.resolve(f'wikidata:production:{user.id}')
    wrong_session = SavedPublicationCredentialResolver(db_session, user.id, bytes(32))
    with pytest.raises(ValueError, match='Unlock or replace'):
        await wrong_session.resolve(f'wikidata:test:{user.id}')
