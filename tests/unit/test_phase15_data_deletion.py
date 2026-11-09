"""Phase 15 — 각 메뉴 데이터 삭제/초기화 기능(사용자 요청, 2026-09-28,
"각 메뉴에서 데이터 삭제나, 초기화 기능 추가").

범위(사용자가 명시적으로 선택): 도구/스킬 Pool + 사람/AI 에이전트 Pool +
업무 요청/할 일(brief/task)까지. 활동 기록(감사로그)은 append-only 원칙에
따라 DB 트리거가 삭제를 막고 있어 제외한다.

전부 `PRAGMA foreign_keys=ON`(`db.get_connection`)이 걸린 채로 테스트해
FK 순서를 어기면 `sqlite3.IntegrityError`가 실제로 나는 것까지 확인한다
(추측이 아니라 진짜 제약 위반 여부를 실측).

Phase 1-4 리뷰(사용자 요청 2026-09-28 "Phase1~4까지... 분석하고
개선해줘")도 이 파일에 담는다 — `delete_task()`가 이 파일 작성 당시엔
없던 `deliverables`/`agent_evaluations`/`agent_execution_jobs`(Phase
1/3/4)를 몰라 FK 위반을 냈던 갭을 재현·검증한다.
"""

from __future__ import annotations

import pytest

from hair_platform import data_admin, model_registry, pool_registry, teams


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
    approval 방지 때문에 호출 시점에 이미 admin1으로
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

    # 생성으로 jobs/job_items도 만들어 브리프 아래 전부 걸리게 한다.
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
    # 생성이 만든 agent_pool 행 자체는 살아있어야 한다(source_job_item_id만 NULL로).
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


def test_gc119b_cancel_assignment_endpoint_unblocks_brief_delete(client_and_conn):
    """사용자 보고(2026-10-19, "업무 요청 삭제가 확정된 배정 때문에
    막히는데 정리할 방법이 없다") — 에러 메시지가 안내하는 "먼저
    정리하세요"를 실제로 해낼 수 있는 `POST /api/assignments/{id}/cancel`
    이 confirmed 배정을 풀어주고, 그 뒤엔 같은 brief 삭제가 정상
    통과하는지 end-to-end로 확인한다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id, resource_id = _make_task_with_candidate(client)
    _confirm_assignment(client, task_id, "human", resource_id)

    assignment_id = conn.execute(
        "SELECT id FROM assignments WHERE task_id = ? AND status = 'confirmed'",
        (task_id,),
    ).fetchone()["id"]

    # member는 배정 취소 권한이 없다(admin/manager 전용 — 결재 결정과
    # 같은 권한 수위).
    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/assignments/{assignment_id}/cancel", json={})
    assert resp.status_code == 403, resp.text

    _login(client, "admin1", "adminpass")
    resp = client.delete(f"/api/briefs/{brief_id}")
    assert resp.status_code == 409, resp.text

    resp = client.post(
        f"/api/assignments/{assignment_id}/cancel", json={"reason": "정리 테스트"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert (
        conn.execute(
            "SELECT status FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()["status"]
        == "cancelled"
    )

    # 이미 취소된 배정을 다시 취소하면 막힌다.
    resp = client.post(f"/api/assignments/{assignment_id}/cancel", json={})
    assert resp.status_code == 400, resp.text

    # 존재하지 않는 배정은 404가 아니라 같은 400 경로(assignment_not_found).
    resp = client.post("/api/assignments/999999/cancel", json={})
    assert resp.status_code == 400, resp.text

    # 이제 confirmed 배정이 없으니 brief 삭제가 통과한다.
    resp = client.delete(f"/api/briefs/{brief_id}")
    assert resp.status_code == 200, resp.text
    assert (
        conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        is None
    )


def test_gc119c_cancel_assignment_while_requested_closes_pending_approval(
    client_and_conn,
):
    """`cancel_assignment()`가 `requested` 상태(아직 아무도 결정하지
    않은 결재 대기 중)도 취소할 수 있어야 하고, 그때 남아있는 결재
    단계(`pending`)를 같이 닫아야 한다 — 안 닫으면 승인권자가 뒤늦게
    그 결재를 승인했을 때 `resolve_assignment_decision()`이 이미
    취소된 배정을 다시 `confirmed`로 되돌려버리는 경합이 생긴다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id, resource_id = _make_task_with_candidate(client)

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    assignment_id = resp.json()["assignment_id"]
    approval_id = resp.json()["approval_id"]
    assert (
        conn.execute(
            "SELECT status FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()["status"]
        == "requested"
    )
    assert (
        conn.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()["status"]
        == "pending"
    )

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/assignments/{assignment_id}/cancel", json={})
    assert resp.status_code == 200, resp.text

    assert (
        conn.execute(
            "SELECT status FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()["status"]
        == "cancelled"
    )
    assert (
        conn.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()["status"]
        == "skipped"
    )

    # 결재가 이미 닫혔으니, 뒤늦게 승인하려 해도 "이미 처리된 승인"으로
    # 막혀 취소된 배정을 되살리지 못한다.
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 409, resp.text
    assert (
        conn.execute(
            "SELECT status FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()["status"]
        == "cancelled"
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


def test_gc153_delete_task_cascades_deliverables_after_assignment_superseded(
    client_and_conn,
):
    """실측으로 발견한 갭(사용자 요청, 2026-09-28, "Phase1~4까지...
    분석하고 개선해줘") — 이 함수는 Phase 15 때 작성돼 그 뒤에
    생긴 `deliverables`/`deliverable_comments`/`agent_evaluations`/
    `agent_execution_jobs`(Phase 1/3/4)를 몰랐다. Agent 배정이
    confirmed였다가 재배치로 superseded되면(더 이상 `confirmed`가
    아니므로 `_confirmed_assignment_exists`를 통과) task를 지울 때
    남아있는 deliverable이 FK 위반(`sqlite3.IntegrityError`)을 일으켰다
    — 이제는 설계 원칙 1번("완전한 연쇄 삭제")대로 전부 같이
    지워져야 한다."""
    from unittest.mock import patch

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_name = "겸용 업무"
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (task_name,),
    )
    conn.commit()
    agent_id = cur.lastrowid
    _confirm_assignment(client, task_id, "agent", agent_id)

    with patch("hair_platform.llm.call_llm", return_value="산출물"):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text
    deliverable_id = conn.execute(
        "SELECT id FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchone()["id"]
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM agent_evaluations WHERE deliverable_id = ?",
            (deliverable_id,),
        ).fetchone()["c"]
        == 1
    )

    # 재배치로 superseded — 더 이상 confirmed가 아니라 삭제 보호를 벗어난다.
    conn.execute(
        "UPDATE assignments SET status = 'superseded' WHERE task_id = ? "
        "AND resource_type = 'agent'",
        (task_id,),
    )
    conn.commit()

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    data_admin.delete_task(conn, admin_id, task_id)  # IntegrityError면 여기서 실패

    assert (
        conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() is None
    )
    for table, column in [
        ("deliverables", "task_id"),
        ("agent_execution_jobs", "task_id"),
    ]:
        assert (
            conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?", (task_id,)
            ).fetchone()["c"]
            == 0
        )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM agent_evaluations WHERE deliverable_id = ?",
            (deliverable_id,),
        ).fetchone()["c"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'deliverable' "
            "AND subject_id = ?",
            (deliverable_id,),
        ).fetchone()["c"]
        == 0
    )


def test_gc_delete_brief_cascades_pipeline_runs_and_stages(client_and_conn):
    """사용자 보고(2026-10-28, "업무 요청 목록에서 삭제 버튼을 클릭해도
    삭제가 안됨" — 대화 `@자율 개발 크루` 멘션이 자동 생성한 브리프를
    지우려다 실패) 재현/수정 — 이 모듈은 `pipeline_runtime.py`
    (2026-10-18 도입)의 `pipeline_runs`/`pipeline_run_stages`를 몰랐다.
    `pipeline_runs.task_id`가 `tasks(id)`를, `pipeline_run_stages.
    deliverable_id`가 `deliverables(id)`를 참조하는데 둘 다 안 지우고
    brief를 지우면 `sqlite3.IntegrityError`(FK 위반)가 그대로 사용자
    에게 노출됐다 — test_gc153과 같은 종류의 갭을 설계 원칙 1번대로
    메운다."""
    from unittest.mock import patch

    from hair_platform import pipeline_runtime

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_name = "파이프라인 연계 업무"
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "요구사항 분석가", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    template_id = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()["id"]

    def _fake_run_crew(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process="sequential",
        provider=None,
        model=None,
        manager_agent_id=None,
        expected_outputs=None,
        self_review=False,
        memory=True,
    ):
        return {
            "final_output": "x",
            "agents": [
                {"agent_id": a, "role_name": "r", "name": None, "output": "o"}
                for a in agent_ids
            ],
            "gate_scores": {"A": {"score": 0.9, "passed": True}},
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
        }

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_fake_run_crew):
        run_id = pipeline_runtime.start_pipeline_run(
            conn, admin_id, template_id, task_id
        )
        pipeline_runtime.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    # 1단계는 사람 승인 필수라 Gate 통과해도 여기서 멈춘다 — deliverable
    # 과 pipeline_run_stages가 둘 다 실제로 남아있는 상태에서 지워야
    # 재현/검증 의미가 있다.
    assert run["status"] == "paused_for_approval"
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM pipeline_run_stages WHERE pipeline_run_id = ?",
            (run_id,),
        ).fetchone()["c"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM deliverables WHERE task_id = ?", (task_id,)
        ).fetchone()["c"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'pipeline_run_stage'"
        ).fetchone()["c"]
        == 1
    )

    data_admin.delete_brief(conn, admin_id, brief_id)  # IntegrityError면 여기서 실패

    assert (
        conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        is None
    )
    for table, column in [
        ("pipeline_runs", "task_id"),
        ("deliverables", "task_id"),
    ]:
        assert (
            conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?", (task_id,)
            ).fetchone()["c"]
            == 0
        )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM pipeline_run_stages WHERE pipeline_run_id = ?",
            (run_id,),
        ).fetchone()["c"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'pipeline_run_stage'"
        ).fetchone()["c"]
        == 0
    )


# ---------- 사용자 요청(2026-10-27, "pip 설치나 패키지 배포시 에이전트와
# 도구/스킬 정보는 포함하고, 나머지 다른 정보들은 초기화 상태로 배포되도록")
# — reset_to_factory_state(). ----------

_REFERENCE_TABLES = (
    "agent_pool",
    "tool_pool",
    "skill_pool",
    "pipeline_templates",
    "pipeline_stages",
    "pipeline_stage_agents",
)
_WIPED_TABLES = (
    "users",
    "briefs",
    "tasks",
    "assignments",
    "approvals",
    "audit_log",
    "model_registry",
    "human_pool",
)


def _table_counts(conn, tables):
    return {
        t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        for t in tables
    }


def test_reset_to_factory_state_keeps_reference_tables_untouched(client_and_conn):
    _client, conn = client_and_conn
    before = _table_counts(conn, _REFERENCE_TABLES)
    assert all(
        before[t] > 0 for t in _REFERENCE_TABLES
    )  # 전부 미리 시드돼 있어야 의미 있는 테스트

    data_admin.reset_to_factory_state(conn)

    after = _table_counts(conn, _REFERENCE_TABLES)
    assert after == before


def test_reset_to_factory_state_keeps_teams_untouched(client_and_conn):
    """2026-11-02 — `reference_pipeline_seeds.seed()`가 더는 "자율 개발
    크루"를 자동 생성하지 않아(사용자 요청, "기존 대화, 업무 요청, 팀
    관리 내용을 모두 초기화해줘" — 그 팀도 포함) 위
    `_REFERENCE_TABLES`에서 `teams`/`team_members`를 뺐다 — 더는 미리
    시드돼 있지 않기 때문. 그래도 `reset_to_factory_state()`가 팀 데이터
    자체는 건드리지 않는 동작은 여전히 보장해야 하므로, 직접 만든 팀으로
    확인한다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    team_id = teams.create_team(conn, admin_id, "테스트 팀")
    agent_id = conn.execute("SELECT id FROM agent_pool LIMIT 1").fetchone()["id"]
    teams.add_team_member(conn, admin_id, team_id, "agent", agent_id)

    data_admin.reset_to_factory_state(conn)

    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM teams WHERE id = ?", (team_id,)
        ).fetchone()["c"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM team_members WHERE team_id = ?", (team_id,)
        ).fetchone()["c"]
        == 1
    )


def test_reset_to_factory_state_wipes_operational_tables(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id, human_id = _make_task_with_candidate(client)
    _confirm_assignment(client, task_id, "human", human_id)
    model_registry.create_model(
        conn,
        conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"],
        "openai",
        "gpt-4o-mini",
    )
    before = _table_counts(conn, _WIPED_TABLES)
    assert all(
        before[t] > 0 for t in _WIPED_TABLES
    )  # 전부 실제로 데이터가 쌓여 있었는지 먼저 확인

    counts = data_admin.reset_to_factory_state(conn)

    after = _table_counts(conn, _WIPED_TABLES)
    assert all(v == 0 for v in after.values())
    assert all(counts[t] == before[t] for t in _WIPED_TABLES)


def test_reset_to_factory_state_resets_agent_pool_volatile_columns_but_keeps_row(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_id = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = '백엔드 개발자'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE agent_pool SET llm_provider='openai', llm_model='gpt-4o-mini', "
        "cost_estimate=12.5, performance_score=0.9, last_output='이전 산출물', "
        "status='deprecated' WHERE id = ?",
        (agent_id,),
    )
    conn.commit()

    data_admin.reset_to_factory_state(conn)

    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    assert row is not None  # Agent 행 자체는 지워지지 않는다
    assert row["role_name"] == "백엔드 개발자"
    assert row["llm_provider"] is None
    assert row["llm_model"] is None
    assert row["last_output"] is None
    assert row["cost_estimate"] == 0.0
    assert row["performance_score"] is None
    assert row["status"] == "active"


def test_reset_to_factory_state_is_idempotent_and_restores_fk_enforcement(
    client_and_conn,
):
    _client, conn = client_and_conn
    data_admin.reset_to_factory_state(conn)
    counts_second = data_admin.reset_to_factory_state(
        conn
    )  # 두 번째 호출도 에러 없이 끝나야 함
    assert all(v == 0 for v in counts_second.values())

    # `PRAGMA foreign_keys=OFF`로 감쌌다가 반드시 다시 켜야 한다 — 꺼진 채로
    # 남으면 그 뒤 모든 작업에서 FK 위반이 조용히 허용돼 버린다(실측 검증).
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_reset_to_factory_state_audit_log_no_delete_trigger_still_active_afterward(
    client_and_conn,
):
    _client, conn = client_and_conn
    data_admin.reset_to_factory_state(conn)

    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()
    assert admin_id is None  # users도 초기화됐으니 재부트스트랩 전엔 없다

    from hair_platform import audit

    new_user_id = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('x','x','admin')"
    ).lastrowid
    conn.commit()
    audit.record(conn, new_user_id, "probe", "users", new_user_id)
    conn.commit()
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM audit_log")


def test_reset_to_factory_state_then_bootstrap_admin_works(client_and_conn):
    """리셋 직후 바로 `hair bootstrap-admin`을 다시 돌릴 수 있어야
    한다(CLI 안내 문구가 약속하는 실제 흐름)."""
    _client, conn = client_and_conn
    data_admin.reset_to_factory_state(conn)

    from hair_platform.auth import Role, create_user

    new_admin_id = create_user(conn, "fresh-admin", "freshpass", Role.ADMIN)
    assert new_admin_id is not None
    assert conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 1
    # 2026-11-08 "콘텐츠 카피라이팅" 추가(사업/기획 업무 처리 파이프라인
    # 신설, AGENT_SPECS 정규 목록에 포함)로 22 → 23.
    assert conn.execute("SELECT COUNT(*) AS c FROM agent_pool").fetchone()["c"] == 23
