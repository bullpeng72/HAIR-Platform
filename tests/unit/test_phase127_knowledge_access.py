"""지식 베이스 문서별 공개 범위(2026-11-25, P1-5 — `knowledge_access.py`)."""

from __future__ import annotations

import hashlib
import math

import pytest

from hair_platform import db, knowledge_access, vectorstore
from hair_platform.auth import Role, create_user

MODEL = "fake-embed"


def _fake_embed(text, provider=None, model=None):
    vec = [0.0] * 32
    for word in text.split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 32] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return "ollama", MODEL, [v / norm for v in vec]


@pytest.fixture
def kb(monkeypatch):
    monkeypatch.setattr(vectorstore, "embed_text", _fake_embed)
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    users = {
        "admin": create_user(conn, "ka_admin", "pw", Role.ADMIN),
        "manager": create_user(conn, "ka_manager", "pw", Role.MANAGER),
        "member": create_user(conn, "ka_member", "pw", Role.MEMBER),
        "insider": create_user(conn, "ka_insider", "pw", Role.MEMBER),
    }
    team = conn.execute("INSERT INTO teams (name, description) VALUES ('재무팀', '')").lastrowid
    human = conn.execute(
        "INSERT INTO human_pool (name, role, user_id) VALUES ('재무 담당', '재무 담당자', ?)", (users["insider"],)
    ).lastrowid
    conn.execute("INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'human', ?)", (team, human))
    docs = {}
    for name, vis in (("공개", "all"), ("관리자급", "manager"), ("관리자만", "admin"), ("재무팀", f"team:{team}")):
        doc = conn.execute(
            "INSERT INTO documents (filename, uploaded_by, visibility) VALUES (?, ?, ?)", (name, users["admin"], vis)
        ).lastrowid
        chunk = vectorstore.index_text(conn, None, f"분기 예산 보고 {name} 문서", provider="ollama", model=MODEL)
        conn.execute("UPDATE knowledge_chunks SET source_document_id = ? WHERE id = ?", (doc, chunk))
        docs[name] = doc
    conn.commit()
    return conn, users, docs, team


def _visible(conn, user_id):
    with knowledge_access.as_viewer(user_id):
        hits = vectorstore.search(conn, "분기 예산 보고", top_k=10, min_score=0.0, provider="ollama", model=MODEL)
    return sorted(h["text"].split()[3] for h in hits)


def test_each_role_sees_only_documents_in_its_scope(kb):
    conn, users, _docs, _team = kb
    assert _visible(conn, users["admin"]) == ["공개", "관리자급", "관리자만", "재무팀"]
    assert _visible(conn, users["manager"]) == ["공개", "관리자급"]
    assert _visible(conn, users["member"]) == ["공개"]
    assert _visible(conn, users["insider"]) == ["공개", "재무팀"]  # 팀 구성원(계정 연결된 사람 인력)


def test_unknown_requester_gets_only_public_documents(kb):
    """문맥 밖(요청자를 모름 — 다른 스레드의 도구 실행 등)은 닫힌 쪽: 전체 공개만."""
    conn, _users, _docs, _team = kb
    hits = vectorstore.search(conn, "분기 예산 보고", top_k=10, min_score=0.0, provider="ollama", model=MODEL)
    assert sorted(h["text"].split()[3] for h in hits) == ["공개"]


def test_agent_output_chunks_without_document_stay_public(kb):
    conn, users, _docs, _team = kb
    vectorstore.index_text(conn, None, "분기 예산 보고 결과물 문서", provider="ollama", model=MODEL)
    assert "결과물" in _visible(conn, users["member"])


def test_execute_agent_sets_the_requester_as_viewer(kb, monkeypatch):
    """작업 입구(단일 Agent 실행)가 요청자를 문맥에 남긴다 — 그 안의 검색은 요청자 기준."""
    from hair_platform import agent_runtime

    conn, users, _docs, _team = kb
    seen = []

    def fake_search(conn_, query, **kw):
        seen.append(knowledge_access.current_viewer())
        return []

    monkeypatch.setattr(vectorstore, "search", fake_search)
    agent = conn.execute("SELECT id FROM agent_pool WHERE role_name = '사내 지식 검색가'").fetchone()
    agent_id = agent[0] if agent else conn.execute("SELECT id FROM agent_pool LIMIT 1").fetchone()[0]
    monkeypatch.setattr("hair_platform.llm.resolve_provider_and_model", lambda *a, **k: ("ollama", "m"))
    monkeypatch.setattr("hair_platform.llm.call_llm", lambda *a, **k: "답변")
    monkeypatch.setattr(agent_runtime, "retrieve_context",
                        lambda conn_, skills, query: (fake_search(conn_, query), None)[1])
    agent_runtime.execute_agent(conn, agent_id, users["member"], "분기 예산 알려줘")
    assert seen and seen[-1] == users["member"]
    assert knowledge_access.current_viewer() is None  # 블록을 나오면 원래대로


def test_set_visibility_validates_and_audits(kb):
    conn, users, docs, team = kb
    out = knowledge_access.set_visibility(conn, users["admin"], docs["공개"], "manager")
    assert out["visibility"] == "manager"
    with pytest.raises(ValueError):
        knowledge_access.set_visibility(conn, users["admin"], docs["공개"], "everyone")
    with pytest.raises(ValueError):
        knowledge_access.set_visibility(conn, users["admin"], docs["공개"], "team:99999")
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'knowledge_document_visibility_changed'"
    ).fetchone()[0] == 1


def test_visibility_endpoint_rbac(client_and_conn):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    doc = conn.execute("INSERT INTO documents (filename, uploaded_by) VALUES ('사내 규정', ?)", (admin,)).lastrowid
    conn.commit()
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.patch(f"/api/documents/{doc}/visibility", json={"visibility": "manager"}).status_code == 403
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.patch(f"/api/documents/{doc}/visibility", json={"visibility": "manager"}).status_code == 200
    # manager는 자기가 못 보게 되는 "admin만"으로는 못 바꾼다
    assert client.patch(f"/api/documents/{doc}/visibility", json={"visibility": "admin"}).status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.patch(f"/api/documents/{doc}/visibility", json={"visibility": "admin"})
    assert r.status_code == 200 and r.json()["visibility"] == "admin"
    assert client.patch(f"/api/documents/{doc}/visibility", json={"visibility": "아무나"}).status_code == 400
