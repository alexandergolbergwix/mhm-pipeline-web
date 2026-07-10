"""Tests for Phase C / Feature 6 — relationship drill / path-finding.

Endpoints:
  GET /api/projects/{project_id}/research/neighbors?uri=<uri>
    → [{uri, label, type, edge_type}]

  GET /api/projects/{project_id}/research/path?from=<uri>&to=<uri>
    → {path: [{uri, label, type}], edges: [{source, target, label}]}
    OR {path: [], edges: []}  when no path exists
    OR {path: [{uri, label, type}], edges: []}  when from == to (trivial)

Rules:
  - Neighbor and path queries work over the merged rdflib graph.
  - Depth-bounded (max 6 hops for path-finding).
  - Unknown URI → neighbors returns [] (not 404); path from unknown → {path:[],edges:[]}.
  - Non-member → 403.
  - Self → trivial path [{self}], edges [].
"""
from __future__ import annotations

import shutil
import pytest
import pytest_asyncio

_HM = "https://w3id.org/mhm/ontology#"

# ── shared TTL fixture ─────────────────────────────────────────────────────
# Graph:  MS-1 --(has_author)--> Person-A
#         MS-1 --(has_scribe)--> Person-B
#         MS-2 --(has_author)--> Person-A    (shared author — bridge)
#         MS-2 --(has_production_place)--> Place-X
#         Person-B --(? via MS)--> Place-X   (path through MS-1 then MS-2)
_PATH_TTL = f"""\
@prefix hm:   <{_HM}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<urn:hm:MS_1> a hm:Manuscript_Object ; rdfs:label "MS One" ;
    hm:has_author <urn:person:A> ;
    hm:has_scribe <urn:person:B> .

<urn:hm:MS_2> a hm:Manuscript_Object ; rdfs:label "MS Two" ;
    hm:has_author <urn:person:A> ;
    hm:has_production_place <urn:place:X> .

<urn:person:A> a hm:Person ; rdfs:label "Person A" .
<urn:person:B> a hm:Person ; rdfs:label "Person B" .
<urn:place:X>  a hm:Place  ; rdfs:label "Place X" .
"""


@pytest_asyncio.fixture
async def project_with_graph(sample_run):
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_PATH_TTL, encoding="utf-8")
    _CACHE.clear()
    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


# ── helpers ────────────────────────────────────────────────────────────────

def _nb_url(project_id, uri: str) -> str:
    return f"/api/projects/{project_id}/research/neighbors?uri={uri}"


def _path_url(project_id, frm: str, to: str) -> str:
    return f"/api/projects/{project_id}/research/path?from={frm}&to={to}"


# ── neighbors ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_neighbors_returns_list(project_with_graph):
    """neighbors endpoint returns a list."""
    client = project_with_graph["client"]
    resp = await client.get(_nb_url(project_with_graph["project_id"], "urn:person:A"))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_neighbors_shape(project_with_graph):
    """Each neighbor has uri, label, type, edge_type."""
    client = project_with_graph["client"]
    resp = await client.get(_nb_url(project_with_graph["project_id"], "urn:person:A"))
    neighbors = resp.json()
    assert len(neighbors) > 0
    for n in neighbors:
        for key in ("uri", "label", "type", "edge_type"):
            assert key in n, f"missing key {key!r}"


@pytest.mark.asyncio
async def test_person_a_neighbors_include_manuscripts(project_with_graph):
    """Person A is author of MS-1 and MS-2 → both appear as neighbors."""
    client = project_with_graph["client"]
    resp = await client.get(_nb_url(project_with_graph["project_id"], "urn:person:A"))
    uris = {n["uri"] for n in resp.json()}
    assert "urn:hm:MS_1" in uris
    assert "urn:hm:MS_2" in uris


@pytest.mark.asyncio
async def test_manuscript_neighbors_include_persons_and_place(project_with_graph):
    """MS-2 has author + production place → both appear as neighbors."""
    client = project_with_graph["client"]
    resp = await client.get(_nb_url(project_with_graph["project_id"], "urn:hm:MS_2"))
    uris = {n["uri"] for n in resp.json()}
    assert "urn:person:A" in uris
    assert "urn:place:X" in uris


@pytest.mark.asyncio
async def test_neighbors_unknown_uri_returns_empty(project_with_graph):
    """Unknown URI returns 200 with an empty list, not 404."""
    client = project_with_graph["client"]
    resp = await client.get(_nb_url(project_with_graph["project_id"], "urn:person:NOBODY"))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_neighbors_non_member_returns_403(project_with_graph, db_session, async_client):
    """Non-member cannot call the neighbors endpoint."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider_nb@example.com"
    password = "Strong-Pass-Nb-1!"
    outsider = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Outsider"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()
    kek = kek_mod.derive_kek(password, salt=outsider.kek_salt)
    row, secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)

    resp = await async_client.get(_nb_url(project_with_graph["project_id"], "urn:person:A"))
    assert resp.status_code == 403


# ── path-finding ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_path_returns_dict_with_path_and_edges(project_with_graph):
    """Path endpoint returns {path: [...], edges: [...]}."""
    client = project_with_graph["client"]
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:B", "urn:place:X")
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "path" in data
    assert "edges" in data


@pytest.mark.asyncio
async def test_path_node_shape(project_with_graph):
    """Each path node has uri, label, type."""
    client = project_with_graph["client"]
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:B", "urn:place:X")
    )
    for node in resp.json()["path"]:
        for key in ("uri", "label", "type"):
            assert key in node


@pytest.mark.asyncio
async def test_path_from_person_b_to_place_x_found(project_with_graph):
    """Person B → MS-1 → (shared via Person A or directly MS-2) → Place X.
    A path must exist within 6 hops."""
    client = project_with_graph["client"]
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:B", "urn:place:X")
    )
    data = resp.json()
    path_uris = [n["uri"] for n in data["path"]]
    assert "urn:person:B" in path_uris
    assert "urn:place:X"  in path_uris


@pytest.mark.asyncio
async def test_path_endpoints_in_result(project_with_graph):
    """First and last nodes of a found path are the requested endpoints."""
    client = project_with_graph["client"]
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:B", "urn:place:X")
    )
    data = resp.json()
    if data["path"]:
        assert data["path"][0]["uri"]  == "urn:person:B"
        assert data["path"][-1]["uri"] == "urn:place:X"


@pytest.mark.asyncio
async def test_self_path_trivial(project_with_graph):
    """Path from a node to itself is a trivial single-node result."""
    client = project_with_graph["client"]
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:A", "urn:person:A")
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["path"]) == 1
    assert data["path"][0]["uri"] == "urn:person:A"
    assert data["edges"] == []


@pytest.mark.asyncio
async def test_no_path_returns_empty(project_with_graph):
    """No path between two disconnected nodes → empty path + edges."""
    client = project_with_graph["client"]
    # Add an isolated node by using a URI that appears in neither subject nor object
    resp = await client.get(
        _path_url(project_with_graph["project_id"], "urn:person:B", "urn:person:NOBODY")
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == []
    assert data["edges"] == []


@pytest.mark.asyncio
async def test_path_non_member_returns_403(project_with_graph, db_session, async_client):
    """Non-member cannot call the path endpoint."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider_path@example.com"
    password = "Strong-Pass-Path-1!"
    outsider = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Outsider"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()
    kek = kek_mod.derive_kek(password, salt=outsider.kek_salt)
    row, secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)

    resp = await async_client.get(
        _path_url(project_with_graph["project_id"], "urn:person:A", "urn:place:X")
    )
    assert resp.status_code == 403
