"""Phase 15 — 각 메뉴 데이터 삭제/초기화 기능(사용자 요청, 2026-09-28,
"각 메뉴에서 데이터 삭제나, 초기화 기능 추가").

범위(사용자가 명시적으로 선택): 도구/스킬 Pool + 사람/AI 에이전트 Pool +
업무 요청/할 일(brief/task)까지. 활동 기록(감사로그)은 NF10(append-only)에
따라 DB 트리거가 삭제를 막고 있어 제외한다.

전부 `PRAGMA foreign_keys=ON`(`db.get_connection`)이 걸린 채로 테스트해
FK 순서를 어기면 `sqlite3.IntegrityError`가 실제로 나는 것까지 확인한다
(추측이 아니라 진짜 제약 위반 여부를 실측).
"""

from __future__ import annotations

import pytest

from hair_platform import data_admin, pool_registry


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task_with_candidate(client, task_name="백엔드 개발자"):
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


def _confirm_assignment(client, task_id, resource_type, resource_id):
    """요청자(manager1)와 승인자(admin1)를 명시적으로 분리한다 — self-
    approval 방지(ADR 결정 3-A) 때문에 호출 시점에 이미 admin1으로
    로그인돼 있으면(예: 직전에 다른 admin 전용 API를 호출한 뒤) 요청 자체가
    admin1 명의로 나가 승인이 403으로 막힌다(실제로 겪은 테스트 버그)."""
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": resource_type, "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["assignment"]["status"] == "confirmed"


def test_gc116_delete_task_cascades_assignments_and_job_items(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id, resource_id = _make_task_with_candidate(client)
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201  # requested 상태 — confirmed 아님

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    data_admin.delete_task(conn, admin_id, task_id)

    assert (
        conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() is None
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM assignments WHERE task_id = ?", (task_id,)
        ).fetchone()["c"]
        == 0
    )
    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "task_deleted" in actions


def test_gc117_delete_task_blocked_when_confirmed_assignment_exists(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id, resource_id = _make_task_with_candidate(client)
    _confirm_assignment(client, task_id, "human", resource_id)

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    with pytest.raises(PermissionError):
        data_admin.delete_task(conn, admin_id, task_id)
    # 안 지워졌는지 확인
    assert (
        conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        is not None
    )


def test_gc118_delete_brief_cascades_everything_below_it(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id, resource_id = _make_task_with_candidate(client)
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201

    # F4 생성으로 jobs/job_items도 만들어 브리프 아래 전부 걸리게 한다.
    resp2 = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "다른역할", "required_skills": []}]},
    )
    task_id_2 = resp2.json()["tasks"][0]["id"]
    resp3 = client.post(
        f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id_2]}
    )
    assert resp3.status_code == 201

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    data_admin.delete_brief(conn, admin_id, brief_id)

    assert (
        conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE brief_id = ?", (brief_id,)
        ).fetchone()["c"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE brief_id = ?", (brief_id,)
        ).fetchone()["c"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM job_items WHERE task_id IN (?, ?)",
            (task_id, task_id_2),
        ).fetchone()["c"]
        == 0
    )
    # F4가 만든 agent_pool 행 자체는 살아있어야 한다(source_job_item_id만 NULL로).
    agent_row = conn.execute(
        "SELECT * FROM agent_pool WHERE role_name = '다른역할'"
    ).fetchone()
    assert agent_row is not None
    assert agent_row["source_job_item_id"] is None


def test_gc119_delete_brief_blocked_when_any_task_has_confirmed_assignment(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id, resource_id = _make_task_with_candidate(client)
    _confirm_assignment(client, task_id, "human", resource_id)

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    with pytest.raises(PermissionError):
        data_admin.delete_brief(conn, admin_id, brief_id)
    assert (
        conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        is not None
    )


def test_gc120_delete_human_blocked_when_confirmed_else_succeeds(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id, resource_id = _make_task_with_candidate(client)
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    # 아직 배정이 없으니 바로 지워진다.
    data_admin.delete_human(conn, admin_id, resource_id)
    assert (
        conn.execute("SELECT * FROM human_pool WHERE id = ?", (resource_id,)).fetchone()
        is None
    )

    # 새 후보로 confirmed 배정을 만들면 삭제가 막힌다.
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "후보B", "role": "백엔드 개발자", "skill_tags": []},
    )
    resource_id_2 = resp.json()["id"]
    _confirm_assignment(client, task_id, "human", resource_id_2)
    with pytest.raises(PermissionError):
        data_admin.delete_human(conn, admin_id, resource_id_2)


def test_gc121_delete_agent_cascades_knowledge_and_tool_links(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('기획자', '[\"요구사항정의\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.execute(
        "INSERT INTO knowledge_chunks "
        "(source_agent_id, text, embedding, embedding_provider, embedding_model) "
        "VALUES (?, 'dummy', '[0.1]', 'openai', 'text-embedding-3-small')",
        (agent_id,),
    )
    tool_id = pool_registry.list_tools(conn)[0]["id"]
    pool_registry.link_agent_tool(conn, admin_id, agent_id, tool_id)
    conn.commit()

    data_admin.delete_agent(conn, admin_id, agent_id)

    assert (
        conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM knowledge_chunks WHERE source_agent_id = ?",
            (agent_id,),
        ).fetchone()["c"]
        == 0
    )
    assert pool_registry.list_agent_tool_ids(conn, agent_id) == []


def test_gc122_delete_tool_removes_agent_tool_links(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('기획자', '[\"요구사항정의\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()
    tool_id = pool_registry.list_tools(conn)[0]["id"]
    pool_registry.link_agent_tool(conn, admin_id, agent_id, tool_id)

    pool_registry.delete_tool(conn, admin_id, tool_id)

    assert (
        conn.execute("SELECT * FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
        is None
    )
    assert pool_registry.list_agent_tool_ids(conn, agent_id) == []

    with pytest.raises(ValueError):
        pool_registry.delete_tool(conn, admin_id, tool_id)


def test_gc123_delete_skill_endpoint(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    skill_id = pool_registry.create_skill(conn, admin_id, "웹검색", "", [], "tool")

    pool_registry.delete_skill(conn, admin_id, skill_id)

    assert skill_id not in {s["id"] for s in pool_registry.list_skills(conn)}
    with pytest.raises(ValueError):
        pool_registry.delete_skill(conn, admin_id, skill_id)


def test_gc124_delete_endpoints_rbac_and_error_mapping(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id, resource_id = _make_task_with_candidate(client)

    # 매니저는 삭제 권한이 없다(ADMIN 전용).
    resp = client.delete(f"/api/briefs/{brief_id}")
    assert resp.status_code == 403
    resp = client.delete(f"/api/human-pool/{resource_id}")
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.delete("/api/briefs/999999")
    assert resp.status_code == 404

    _confirm_assignment(client, task_id, "human", resource_id)
    resp = client.delete(f"/api/briefs/{brief_id}")
    assert resp.status_code == 409

    resp = client.delete(f"/api/briefs/{brief_id}/tasks/{task_id}")
    assert resp.status_code == 409

    tool_id = pool_registry.list_tools(conn)[0]["id"]
    resp = client.delete(f"/api/tool-pool/{tool_id}")
    assert resp.status_code == 200
    resp = client.delete(f"/api/tool-pool/{tool_id}")
    assert resp.status_code == 404
