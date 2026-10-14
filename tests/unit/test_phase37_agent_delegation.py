"""Phase 37 — "Task 카드에 위임/배정 버튼 분리"(2026-10-12) → 사용자 결정
"위임은 Gate A-G 그대로 타고, audit_log에도 남겨줘".

"위임"(`POST /api/tasks/{id}/delegate`)은 "배정"(`POST /api/tasks/{id}/
assignments`)과 달리 결재 체인 없이 즉시 `status='confirmed'`가 된다 —
그 결과 (1) `execute-agent`가 평소처럼 바로 쓸 수 있고(Gate A-G는 기존
`run_agent_execution_job()` 경로가 그대로 매긴다, 이 기능이 추가로 할 일
없음), (2) `assignment_requested`/`assignment_confirmed`가 아니라 별도
`agent_delegated` action으로 audit_log에 남는다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task(client, task_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def _make_active_agent(conn, role_name="백엔드 개발자"):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def test_gc_delegate_confirms_immediately_without_approval_chain(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn)

    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"

    row = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (body["assignment_id"],)
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["approval_id"] is None  # 결재 체인 없음

    # 결재 테이블에 이 배정을 가리키는 행이 전혀 없어야 한다(결재를 완전히
    # 건너뛰었다는 것의 직접적인 증거).
    approvals = conn.execute(
        "SELECT * FROM approvals WHERE subject_type = 'assignment' "
        "AND subject_id = ?",
        (body["assignment_id"],),
    ).fetchall()
    assert approvals == []


def test_gc_delegate_logs_distinct_audit_action(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn)

    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent_id})
    assignment_id = resp.json()["assignment_id"]

    entries = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'agent_delegated'"
    ).fetchall()
    assert len(entries) == 1
    assert entries[0]["subject_type"] == "assignment"
    assert entries[0]["subject_id"] == assignment_id
    # 일반 배정 확정(assignment_confirmed)과 구분돼야 한다 — 이 흐름에서는
    # 그 action이 전혀 기록되지 않는다(결재 자체가 없었으므로).
    confirmed_actions = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'assignment_confirmed'"
    ).fetchall()
    assert confirmed_actions == []


def test_gc_delegated_agent_can_execute_immediately(client_and_conn):
    """위임만으로(결재 없이) execute-agent가 바로 통과해야 한다 — Gate A-G는
    기존 run_agent_execution_job() 경로가 그대로 매기므로 이 기능이 추가로
    할 일이 없다는 걸 "실행 권한이 통과되는지"로 간접 확인한다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn)
    client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent_id})

    resp = client.get(f"/api/tasks/{task_id}/execution-permission")
    assert resp.status_code == 200, resp.text
    assert resp.json()["eligible"] is True


def test_gc_delegate_requires_manager_or_admin(client_and_conn):
    client, conn = client_and_conn
    agent_id = _make_active_agent(conn)
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent_id})
    assert resp.status_code == 403

    _login(client, "viewer1", "viewerpass")
    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent_id})
    assert resp.status_code == 403


def test_gc_delegate_rejects_inactive_or_missing_agent(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)

    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": 999999})
    assert resp.status_code == 400

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) "
        "VALUES ('비활성 Agent', '[]', 'deprecated')"
    )
    conn.commit()
    resp = client.post(
        f"/api/tasks/{task_id}/delegate", json={"agent_id": cur.lastrowid}
    )
    assert resp.status_code == 400


def test_gc_delegate_rejects_missing_task(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    agent_id = _make_active_agent(conn)
    resp = client.post("/api/tasks/999999/delegate", json={"agent_id": agent_id})
    assert resp.status_code == 400


def test_gc_multiple_confirmed_assignments_coexist_delegation_and_formal(
    client_and_conn,
):
    """위임 하나 + (별도) 정식 배정 하나가 같은 task에 공존해도 서로 안
    깨져야 한다 — 다중 confirmed 허용(2026-10-06 결정)과 같은 전제."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent1 = _make_active_agent(conn, "위임된 Agent")
    agent2 = _make_active_agent(conn, "정식 배정 Agent")

    resp = client.post(f"/api/tasks/{task_id}/delegate", json={"agent_id": agent1})
    assert resp.status_code == 201

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "agent", "resource_id": agent2},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "requested"  # 정식 배정은 여전히 결재 대기

    confirmed = conn.execute(
        "SELECT resource_id FROM assignments WHERE task_id = ? AND status = 'confirmed'",
        (task_id,),
    ).fetchall()
    assert {r["resource_id"] for r in confirmed} == {agent1}


def test_gc_delegate_endpoint_shares_create_delegation_with_direct_call(
    client_and_conn,
):
    """함수 직접 호출 경로도 같은 결과를 내는지(엔드포인트가 그대로 위임만
    하는지) 확인 — `assignments.create_delegation()` 자체의 단위 검증."""
    from hair_platform import assignments

    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')",
        (admin_id,),
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')",
        (brief_id,),
    )
    task_id = cur.lastrowid
    agent_id = _make_active_agent(conn)
    conn.commit()

    result = assignments.create_delegation(conn, task_id, agent_id, admin_id)
    assert result["status"] == "confirmed"
