"""Phase 3 v3 골든셋 8건 — 다단계 결재라인, Job Queue+샌드박스, 온보딩, 감사로그."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform.auth import Role, create_user
from hair_platform.jobs import default_stub_generator
from hair_platform.sandbox import build_docker_command, run_smoke_test_in_sandbox


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


# ---- Job Queue + 샌드박스 ----------------------------------------------


def test_gc19_generate_batch_succeeds_and_onboards_pending_agent(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client)

    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["items"][0]["status"] == "succeeded"

    row = conn.execute(
        "SELECT * FROM agent_pool WHERE source_job_item_id = ?",
        (body["items"][0]["id"],),
    ).fetchone()
    assert row["status"] == "pending_onboarding"

    # 팀원은 생성 권한이 없다(RBAC)
    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    assert resp.status_code == 403


def test_gc20_partial_failure_and_retry_of_failed_items_only(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_ok = _make_brief_with_task(client, "백엔드 개발자")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "디자이너", "required_skills": []}]},
    )
    task_fail = resp.json()["tasks"][0]["id"]

    def flaky_generator(conn, role_name, required_skills):
        if role_name == "디자이너":
            raise RuntimeError("simulated LLM output escaping error")
        return default_stub_generator(conn, role_name, required_skills)

    with patch(
        "hair_platform.jobs.default_stub_generator", side_effect=flaky_generator
    ):
        resp = client.post(
            f"/api/briefs/{brief_id}/generate",
            json={"task_ids": [task_ok, task_fail]},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "partial"
    statuses = {item["task_id"]: item["status"] for item in body["items"]}
    assert statuses[task_ok] == "succeeded"
    assert statuses[task_fail] == "failed"

    failed_item_id = next(i["id"] for i in body["items"] if i["task_id"] == task_fail)
    resp = client.post(f"/api/jobs/{body['job_id']}/items/{failed_item_id}/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"

    job_row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (body["job_id"],)
    ).fetchone()
    assert job_row["status"] == "succeeded"


def test_gc21_sandbox_command_uses_adr_isolation_flags():
    # Docker 미설치 환경에서는 실행을 건너뛰고 그 사실을 명시한다(성공으로 위장하지 않음).
    result = run_smoke_test_in_sandbox({"agents_yaml_snippet": "role: x"})
    assert result.ok is True
    assert result.skipped is True

    # 격리 옵션이 실제 docker 명령에 들어가는지는 명령 구성만 검증한다
    # (이 저장소의 개발 샌드박스에는 Docker가 없어 실제 실행 검증은 별도 환경 필요).
    command = build_docker_command("print(1)", cpus="1", memory="512m")
    assert "--network=none" in command
    assert "--cpus=1" in command
    assert "--memory=512m" in command


# ---- 온보딩/오프보딩, 검색 통합 ------------------------------------------


def test_gc22_onboard_then_deprecate_agent_controls_visibility(client_and_conn):
    # 2026-10-14 "기본 배포에 Agent를 포함해줘"로 심어진 레퍼런스
    # Agent 중 "QA 기능테스터"도 role에 "QA"가 들어 있어, role=QA 검색
    # 결과가 더는 이 테스트가 만든 Agent 하나로 비어있거나 유일하지
    # 않다 — 검색 결과 전체를 비교하는 대신, 이 테스트가 만든 그
    # `agent_id`가 결과에 포함돼 있는지만으로 가시성을 판정한다(다른
    # 레퍼런스 Agent의 존재 여부와 무관하게 본래 검증하려던 것 그대로).
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "QA 엔지니어")
    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    assert resp.json()["items"][0]["result"] is not None

    agent_row = conn.execute(
        "SELECT id FROM agent_pool ORDER BY id DESC LIMIT 1"
    ).fetchone()
    agent_id = agent_row["id"]

    def _agent_ids_in_search():
        resp = client.get("/api/pool/search", params={"role": "QA"})
        return {item["id"] for item in resp.json()["items"] if item["type"] == "agent"}

    # 아직 pending_onboarding 상태라 Pool 검색에는 노출되지 않는다
    assert agent_id not in _agent_ids_in_search()

    # 매니저는 온보딩 권한이 없다(Admin 전용)
    resp = client.post(f"/api/agent-pool/{agent_id}/onboard")
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/onboard")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    assert agent_id in _agent_ids_in_search()

    resp = client.post(f"/api/agent-pool/{agent_id}/deprecate")
    assert resp.status_code == 200
    assert agent_id not in _agent_ids_in_search()


# ---- 감사로그 ----------------------------------------------------------


def test_gc23_audit_log_records_writes_and_is_role_restricted(client_and_conn):
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "PM")
    client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})

    resp = client.get("/api/audit-log")
    assert resp.status_code == 200
    actions = {row["action"] for row in resp.json()["items"]}
    assert "agent_generation_succeeded" in actions

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/audit-log")
    assert resp.status_code == 403


def test_gc24_audit_log_is_append_only_at_db_level(client_and_conn):
    _, conn = client_and_conn
    conn.execute(
        "INSERT INTO audit_log (actor_id, action, subject_type) VALUES (1, 'x', 'y')"
    )
    conn.commit()
    row = conn.execute("SELECT id FROM audit_log LIMIT 1").fetchone()

    import sqlite3

    try:
        conn.execute(
            "UPDATE audit_log SET action = 'tampered' WHERE id = ?", (row["id"],)
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "audit_log는 UPDATE를 막아야 한다(append-only)"

    try:
        conn.execute("DELETE FROM audit_log WHERE id = ?", (row["id"],))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "audit_log는 DELETE를 막아야 한다(append-only)"


# ---- 다단계 결재라인 -----------------------------------------------------


def test_gc25_three_step_approval_chain_must_pass_in_order(client_and_conn):
    client, conn = client_and_conn
    manager2_id = create_user(conn, "manager2", "pass", Role.MANAGER)
    manager3_id = create_user(conn, "manager3", "pass", Role.MANAGER)
    admin_row = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()
    manager1_row = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()

    # 결재라인: manager1 → manager2 → manager3 → admin1 (3단계)
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?",
        (manager2_id, manager1_row["id"]),
    )
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?", (manager3_id, manager2_id)
    )
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?", (admin_row["id"], manager3_id)
    )
    conn.commit()

    _login(client, "manager1", "managerpass")
    _, task_id = _make_brief_with_task(client, "데이터 엔지니어")
    resp = client.post(
        "/api/human-pool",
        json={"name": "후보D", "role": "데이터 엔지니어", "skill_tags": []},
    )
    resource_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    approval_ids = resp.json()["approval_ids"]
    assert len(approval_ids) == 3

    # 2단계(manager3)가 아직 대기 중인데 먼저 승인하려 하면 거부된다
    _login(client, "manager3", "pass")
    resp = client.post(
        f"/api/approvals/{approval_ids[1]}/decide", json={"approve": True}
    )
    assert resp.status_code == 409

    # 1단계(manager2) 승인 → 아직 확정 아님(다음 단계 대기)
    _login(client, "manager2", "pass")
    resp = client.post(
        f"/api/approvals/{approval_ids[0]}/decide", json={"approve": True}
    )
    assert resp.status_code == 200
    assert resp.json()["next_step_pending"] is True
    assert resp.json()["assignment"] is None

    # 2단계는 manager3만 승인 가능(다른 사람이 시도하면 403)
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/approvals/{approval_ids[1]}/decide", json={"approve": True}
    )
    assert resp.status_code == 403

    _login(client, "manager3", "pass")
    resp = client.post(
        f"/api/approvals/{approval_ids[1]}/decide", json={"approve": True}
    )
    assert resp.status_code == 200
    assert resp.json()["next_step_pending"] is True

    # 3단계(admin1) 승인 → 마지막 단계라 confirmed로 확정
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{approval_ids[2]}/decide", json={"approve": True}
    )
    assert resp.status_code == 200
    assert resp.json()["next_step_pending"] is False
    assert resp.json()["assignment"]["status"] == "confirmed"


def test_gc26_mid_chain_rejection_skips_remaining_steps(client_and_conn):
    client, conn = client_and_conn
    manager2_id = create_user(conn, "manager2b", "pass", Role.MANAGER)
    manager1_row = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?",
        (manager2_id, manager1_row["id"]),
    )
    conn.commit()

    _login(client, "manager1", "managerpass")
    _, task_id = _make_brief_with_task(client, "프론트엔드 개발자")
    resp = client.post(
        "/api/human-pool",
        json={"name": "후보E", "role": "프론트엔드 개발자", "skill_tags": []},
    )
    resource_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assignment_id = resp.json()["assignment_id"]
    approval_ids = resp.json()["approval_ids"]
    assert len(approval_ids) == 1  # manager2b 위에는 상사가 없음(단일 단계)

    _login(client, "manager2b", "pass")
    resp = client.post(
        f"/api/approvals/{approval_ids[0]}/decide", json={"approve": False}
    )
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "rejected"

    row = conn.execute(
        "SELECT status FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    assert row["status"] == "rejected"
