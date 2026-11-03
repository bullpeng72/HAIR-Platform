"""Phase 56 — 결재 화면에 요청자/결재권자 이름 표시(사용자 요청,
2026-11-03, "결재 내 승인 대기의 경우 요청자가, 내가 요청한 건의 경우
결재권자가 표시되도록 개선").

`approvals` 테이블의 `requested_by`/`approver_id`/`approved_by`는
전부 숫자 user id뿐이라 화면에 이름을 못 보여줬다 — `GET /api/approvals`
(scope=inbox/mine 둘 다)에 `requested_by_username`/`approver_username`/
`approved_by_username`을 조인해 내려준다."""

from __future__ import annotations

from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task_with_candidate(client, task_name):
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    resp = client.post(
        "/api/human-pool", json={"name": "후보A", "role": task_name, "skill_tags": []}
    )
    resource_id = resp.json()["id"]
    return brief_id, task_id, resource_id


def test_inbox_shows_requester_username(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _, task_id, resource_id = _make_task_with_candidate(client, "백엔드 개발자-inbox")

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    rows = client.get("/api/approvals", params={"scope": "inbox"}).json()
    row = next(r for r in rows if r["id"] == approval_id)
    assert row["requested_by_username"] == "manager1"


def test_mine_shows_unassigned_approver_when_no_manager_chain(client_and_conn):
    """manager1은 기본 fixture에서 상사(manager_id)가 지정돼 있지
    않다 — 결재 체인이 "미지정"(Admin/매니저 누구나)으로 떨어지는
    경우를 확인한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _, task_id, resource_id = _make_task_with_candidate(
        client, "백엔드 개발자-unassigned"
    )

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]

    rows = client.get("/api/approvals", params={"scope": "mine"}).json()
    row = next(r for r in rows if r["id"] == approval_id)
    assert row["status"] == "pending"
    assert row["approver_id"] is None
    assert row["approver_username"] is None


def test_mine_shows_assigned_approver_username(client_and_conn):
    client, conn = client_and_conn
    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    create_user(conn, "manager2", "managerpass2", Role.MANAGER, manager_id=manager1_id)
    conn.commit()

    _login(client, "admin1", "adminpass")
    _, task_id, resource_id = _make_task_with_candidate(
        client, "백엔드 개발자-assigned"
    )

    _login(client, "manager2", "managerpass2")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]

    rows = client.get("/api/approvals", params={"scope": "mine"}).json()
    row = next(r for r in rows if r["id"] == approval_id)
    assert row["status"] == "pending"
    assert row["approver_id"] == manager1_id
    assert row["approver_username"] == "manager1"


def test_mine_shows_decider_username_after_decision(client_and_conn):
    client, conn = client_and_conn
    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    create_user(conn, "manager3", "managerpass3", Role.MANAGER, manager_id=manager1_id)
    conn.commit()

    _login(client, "admin1", "adminpass")
    _, task_id, resource_id = _make_task_with_candidate(
        client, "백엔드 개발자-decided"
    )

    _login(client, "manager3", "managerpass3")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    approval_id = resp.json()["approval_id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text

    _login(client, "manager3", "managerpass3")
    rows = client.get("/api/approvals", params={"scope": "mine"}).json()
    row = next(r for r in rows if r["id"] == approval_id)
    assert row["status"] == "approved"
    assert row["approved_by_username"] == "manager1"
