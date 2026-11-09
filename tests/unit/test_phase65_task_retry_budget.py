"""Phase 65 — Task 레벨 비용/재시도 예산 추적(사용자 요청, 2026-11-05,
"업무 배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너 수준의
게임... 개선 계획을 수립해줘" → 로드맵 9번(마지막) "Task 레벨 비용/
재시도 예산 추적... 다음 개선 진행해줘").

비용·재시도 둘 다 지금까지 Task 단위로 합산해서 보는 방법이 없었고,
예산 상한을 걸 방법도 없었다. 이 파일은 ① `cost.task_report()`(Task
스코프 비용 + 실제 토큰 사용량 합산), ② `dashboard.task_retry_
summary()`(파이프라인 단계 시도 + Crew 재실행 합산), ③
`tasks.max_retry_budget`이 실제로 `pipeline_runtime.py`의 에스컬레이션
결정을 바꾸는지(단계 내 `max_retry`가 남아 있어도 Task 전체 예산을
넘으면 바로 에스컬레이션), ④ 설정 엔드포인트의 RBAC를 검증한다.

**비용/네트워크 발생 방지**: `crew_runtime.run_crew` 그 자체만
patch한다(`test_phase48_pipeline_runtime.py`/`test_phase63_staged_
escalation.py`와 같은 원칙)."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import cost, dashboard, db
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '테스트', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


# ---------- cost.task_report() ----------


def test_task_report_scopes_cost_to_one_task_and_sums_token_usage():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_a = _make_task(conn)
    task_b = _make_task(conn)

    agent_row = conn.execute(
        "SELECT id FROM agent_pool WHERE status='active' LIMIT 1"
    ).fetchone()
    agent_id = agent_row["id"]
    conn.execute("UPDATE agent_pool SET cost_estimate = 5.0 WHERE id = ?", (agent_id,))
    # task_a에만 확정 배정 — task_b는 비용 0이어야 한다.
    conn.execute(
        "INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
        "VALUES (?, 'agent', ?, ?, 'confirmed')",
        (task_a, agent_id, admin_id),
    )
    conn.execute(
        "INSERT INTO crew_runs (task_id, kind, requested_by, agent_ids, process, token_usage) "
        "VALUES (?, 'adhoc', ?, '[]', 'sequential', ?)",
        (
            task_a,
            admin_id,
            json.dumps(
                {
                    "total_tokens": 100,
                    "prompt_tokens": 60,
                    "completion_tokens": 40,
                    "successful_requests": 1,
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO crew_runs (task_id, kind, requested_by, agent_ids, process, token_usage) "
        "VALUES (?, 'adhoc', ?, '[]', 'sequential', ?)",
        (
            task_a,
            admin_id,
            json.dumps(
                {
                    "total_tokens": 50,
                    "prompt_tokens": 30,
                    "completion_tokens": 20,
                    "successful_requests": 1,
                }
            ),
        ),
    )
    conn.commit()

    report_a = cost.task_report(conn, task_a)
    assert report_a["estimated_cost"] == 5.0
    assert report_a["token_usage"]["total_tokens"] == 150
    assert report_a["token_usage"]["successful_requests"] == 2

    report_b = cost.task_report(conn, task_b)
    assert report_b["estimated_cost"] == 0.0
    assert report_b["token_usage"]["total_tokens"] == 0
    assert report_b["resources"] == []


# ---------- dashboard.task_retry_summary() ----------


def test_task_retry_summary_sums_pipeline_attempts_and_crew_runs():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)

    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES ('t', '', 'x')"
    )
    tpl_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode) "
        "VALUES (?, 1, 's', 'single')",
        (tpl_id,),
    )
    stage_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id) VALUES (?, ?, ?)",
        (tpl_id, task_id, admin_id),
    )
    run_id = cur.lastrowid
    for attempt in (1, 2):
        conn.execute(
            "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
            "VALUES (?, ?, ?, 'passed', 'x')",
            (run_id, stage_id, attempt),
        )
    for _ in range(3):
        conn.execute(
            "INSERT INTO crew_runs (task_id, kind, requested_by, agent_ids, process) "
            "VALUES (?, 'adhoc', ?, '[]', 'sequential')",
            (task_id, admin_id),
        )
    conn.commit()

    summary = dashboard.task_retry_summary(conn, task_id)
    assert summary == {
        "pipeline_stage_attempts": 2,
        "crew_run_count": 3,
        "total_retries": 5,
    }


def test_task_retry_summary_is_zero_for_untouched_task():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    assert dashboard.task_retry_summary(conn, task_id) == {
        "pipeline_stage_attempts": 0,
        "crew_run_count": 0,
        "total_retries": 0,
    }


# ---------- pipeline_runtime.py — 예산 집행 ----------


def _bad_run_crew_tracking(calls):
    bad_gate = {"A": {"score": 0.1, "passed": False}}

    def _fn(
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
        calls.append(1)
        return {
            "final_output": "산출물",
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": f"output-{aid}",
                }
                for aid in agent_ids
            ],
            "gate_scores": bad_gate,
            "tcr": 10.0,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
            "token_usage": {"total_tokens": 1},
        }

    return _fn


def _setup_gated_stage(conn, admin_id, task_id, max_retry=2):
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "코드검증",
        "single",
        [("코드 검증가", "")],
        max_retry=max_retry,
        min_gate_pass_ratio=0.9,
    )
    return tid


def test_default_no_budget_behaves_exactly_like_before():
    """회귀 가드 — `max_retry_budget`이 NULL(기본값)이면 기존 로드맵
    6번 동작과 100% 동일하게 max_retry만큼 재시도한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_gated_stage(conn, admin_id, task_id, max_retry=2)

    calls: list[int] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 2  # max_retry=2, 예산 없음 → 끝까지 재시도
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"


def test_retry_budget_escalates_before_stage_max_retry_exhausted():
    """핵심 테스트 — `max_retry_budget=1`이면 단계 내 `max_retry=2`가
    남아 있어도 1번째 시도 직후 바로 에스컬레이션된다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_gated_stage(conn, admin_id, task_id, max_retry=2)
    conn.execute("UPDATE tasks SET max_retry_budget = 1 WHERE id = ?", (task_id,))
    conn.commit()

    calls: list[int] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 1  # 예산(1) 때문에 2번째 시도 전에 멈춤
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"
    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    assert stage_result["status"] == "escalated"
    assert stage_result["attempt"] == 1

    audit_row = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'pipeline_stage_escalated' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    after = json.loads(audit_row["after_value"])
    assert after["retry_budget_exceeded"] is True


def test_retry_budget_larger_than_max_retry_has_no_effect():
    """예산이 단계 내 max_retry보다 넉넉하면 기존 동작 그대로."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_gated_stage(conn, admin_id, task_id, max_retry=2)
    conn.execute("UPDATE tasks SET max_retry_budget = 100 WHERE id = ?", (task_id,))
    conn.commit()

    calls: list[int] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 2


# ---------- RBAC + API ----------


def test_retry_budget_endpoint_requires_admin_or_manager(client_and_conn):
    client, conn = client_and_conn
    task_id = _make_task(conn)

    _login(client, "member1", "memberpass")
    resp = client.patch(
        f"/api/tasks/{task_id}/retry-budget", json={"max_retry_budget": 3}
    )
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/tasks/{task_id}/retry-budget", json={"max_retry_budget": 3}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_retry_budget"] == 3

    # None으로 다시 보내면 예산 해제.
    resp = client.patch(
        f"/api/tasks/{task_id}/retry-budget", json={"max_retry_budget": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_retry_budget"] is None

    audit_action = conn.execute(
        "SELECT action FROM audit_log WHERE action = 'task_retry_budget_updated'"
    ).fetchall()
    assert len(audit_action) == 2


def test_get_task_budget_endpoint_returns_cost_and_retries(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(conn)

    resp = client.get(f"/api/tasks/{task_id}/budget")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "cost" in body and "retries" in body
    assert body["retries"]["total_retries"] == 0
    assert body["cost"]["estimated_cost"] == 0.0
