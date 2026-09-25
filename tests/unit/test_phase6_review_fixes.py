"""Phase 6 코드 재점검(2026-09-26, 2회차) 골든셋 — 1회차 수정(GC42-46) 이후에도
남아있던 4건을 발견·수정하고 회귀 테스트로 고정한다.

1. GC47: `GET /api/users` 없음 — 1회차에 추가한 `PATCH .../manager`를 실제로 쓰려면
   user_id를 조회할 방법이 있어야 하는데 없었다(ADMIN 전용으로 신설).
2. GC48: 승인 대기함(inbox) 조회 API가 없어 승인권자가 자신에게 무엇이 결재
   대기 중인지 알아낼 방법이 전무했다 — `GET /api/approvals`(scope=inbox/mine) 신설.
3. GC49: `GET /api/audit-log`가 페이지네이션/필터 없이 전체 테이블을 반환하던 것을
   `limit`/`offset`/`actor_id`/`subject_type` 지원하도록 고쳤다.
4. GC50: 세션 `secret_key`가 하드코딩돼 "배포 시 환경변수로 교체한다"는 주석이
   실제 코드로 지켜지지 않던 것을 `HAIR_PLATFORM_SECRET_KEY` 환경변수로 고쳤다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hair_platform.app import create_app
from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_with_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def test_gc47_admin_only_user_listing(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.get("/api/users")
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/users")
    assert resp.status_code == 200
    usernames = {row["username"] for row in resp.json()}
    assert {"admin1", "manager1", "member1", "viewer1"} <= usernames
    # password_hash는 노출되면 안 된다.
    assert all("password_hash" not in row for row in resp.json())


def test_gc48_approval_inbox_scope_matches_decide_authorization(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _, task_id = _make_brief_with_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]

    # 요청자 본인(manager1)의 "내 요청" 목록에는 보이지만, inbox에는 자기 요청
    # 건은 안 뜬다(self-approval 방지, ADR 결정 3-A).
    resp = client.get("/api/approvals", params={"scope": "mine"})
    assert approval_id in {row["id"] for row in resp.json()}
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert approval_id not in {row["id"] for row in resp.json()}

    # Admin은 자기가 요청한 게 아니므로 inbox에서 보인다.
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert resp.status_code == 200
    assert approval_id in {row["id"] for row in resp.json()}

    # MEMBER/VIEWER는 inbox 조회 권한이 없다.
    _login(client, "member1", "memberpass")
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert resp.status_code == 403

    # 결정 후에는 inbox(=pending만)에서 사라진다.
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert approval_id not in {row["id"] for row in resp.json()}


def test_gc49_audit_log_pagination_and_filter(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    # submit_brief 자체는 감사기록을 남기지 않는다 — F4 생성 파이프라인(jobs.py)이
    # 남기므로 이를 이용해 여러 건을 만든다.
    for i in range(3):
        brief_id, task_id = _make_brief_with_task(client, f"역할-{i}")
        client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/audit-log", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert len(body["items"]) == 2
    assert body["total"] >= 3
    assert body["limit"] == 2
    assert body["offset"] == 0

    resp = client.get("/api/audit-log", params={"subject_type": "job"})
    items = resp.json()["items"]
    assert items
    assert all(row["subject_type"] == "job" for row in items)

    # limit은 200을 넘지 못하도록 상한을 둔다.
    resp = client.get("/api/audit-log", params={"limit": 9999})
    assert resp.json()["limit"] == 200


def test_gc50_session_secret_key_configurable_via_env(monkeypatch):
    monkeypatch.setenv("HAIR_PLATFORM_SECRET_KEY", "test-secret-A")
    app_a = create_app(":memory:")
    create_user(app_a.state.db, "u1", "pw", Role.ADMIN)
    with TestClient(app_a) as client_a:
        resp = client_a.post("/login", json={"username": "u1", "password": "pw"})
        assert resp.status_code == 200
        session_cookie = client_a.cookies.get("session")
    assert session_cookie is not None

    monkeypatch.setenv("HAIR_PLATFORM_SECRET_KEY", "test-secret-B")
    app_b = create_app(":memory:")
    create_user(app_b.state.db, "u1", "pw", Role.ADMIN)
    with TestClient(app_b) as client_b:
        client_b.cookies.set("session", session_cookie)
        # 서로 다른 secret_key로 서명된 세션이라 복호화(검증)에 실패해야 한다.
        resp = client_b.get("/api/audit-log")
        assert resp.status_code == 401
