"""Phase 53 — TF팀(teams 확장) + 파이프라인 assignment 통합 + Team 온보딩
라이프사이클(사용자 요청, 2026-10-29).

검증 대상:
- `teams.create_team()`의 `task_id`/`brief_id` 분기(TF팀은 `pending_
  onboarding`으로 시작, 일반 팀은 `active`로 시작).
- `onboard_team`/`deprecate_team`/`complete_team_mission` 엔드포인트.
- `delete_team()`의 confirmed assignment 보호.
- `assignments.resource_type == "pipeline"` — 결재 체인 생성, 승인
  확정 시 `pipeline_runtime.start_pipeline_run()` 트리거(`actor_id`는
  요청자, 승인자가 아님), 중복 실행 흡수, 템플릿 미존재/비활성 거부.
- 조직도(`GET /api/org-chart`)의 team/pipeline 라벨링.

**비용/네트워크 발생 방지**(test_phase48/test_phase52와 같은 원칙):
실제로 파이프라인 실행을 트리거하는 테스트는 `crew_runtime.run_crew`를
patch한다 — TestClient는 `BackgroundTasks`를 응답 생성과 동기적으로
실행하므로, patch 없이는 실제 LLM API를 호출하려 든다.
"""

from __future__ import annotations

from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _good_run_crew(gate_scores=None, tcr=90.0):
    """test_phase52의 `_good_run_crew()`와 동일 패턴 — `pipeline_runtime.
    _execute_stage_attempt()`가 쓰는 호출 모양에 맞춘 side_effect."""
    gate_scores = gate_scores or {
        "A": {"score": 0.9, "passed": True},
        "B": {"not_measured": True},
    }

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
    ):
        return {
            "final_output": "단계 산출물",
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": f"output-{aid}",
                }
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": tcr,
            "delegations": [],
            "resolved_provider": "anthropic",
            "resolved_model": "claude-sonnet-5",
        }

    return _fn


def _make_brief_and_task(client, task_name="백엔드 개발자"):
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def _pipeline_template_id(conn):
    return conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()["id"]


def test_gc_create_team_without_task_starts_active(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/teams", json={"name": "범용팀"})
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["id"]
    resp = client.get(f"/api/teams/{team_id}")
    body = resp.json()
    assert body["status"] == "active"
    assert body["is_active"] is True
    assert body["task_id"] is None


def test_gc_create_team_with_task_id_starts_pending_onboarding(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "TF팀 업무")
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/teams", json={"name": "TF팀", "task_id": task_id})
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["id"]
    resp = client.get(f"/api/teams/{team_id}")
    body = resp.json()
    assert body["status"] == "pending_onboarding"
    assert body["is_active"] is False
    assert body["task_id"] == task_id

    # pending_onboarding인 동안은 Task 배정 드롭다운(활성 팀만)에서 제외된다
    # — is_active 거울 덕분에 /api/teams의 is_active 필터만으로 자동 반영.
    resp = client.get("/api/teams")
    active_names = {t["name"] for t in resp.json() if t["is_active"]}
    assert "TF팀" not in active_names

    # 온보딩 후에는 노출된다.
    resp = client.post(f"/api/teams/{team_id}/onboard")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    resp = client.get("/api/teams")
    active_names = {t["name"] for t in resp.json() if t["is_active"]}
    assert "TF팀" in active_names


def test_gc_complete_team_mission_requires_active_and_records_completed_at(
    client_and_conn,
):
    client, _conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "미션 완료 업무")
    _login(client, "admin1", "adminpass")
    team_id = client.post(
        "/api/teams", json={"name": "미션팀", "task_id": task_id}
    ).json()["id"]

    # pending_onboarding 상태에서는 미션 완료 불가.
    resp = client.post(f"/api/teams/{team_id}/complete-mission")
    assert resp.status_code == 400
    assert "활성" in resp.json()["detail"]

    client.post(f"/api/teams/{team_id}/onboard")
    resp = client.post(f"/api/teams/{team_id}/complete-mission")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "deprecated"
    assert body["completed_at"] is not None

    resp = client.get(f"/api/teams/{team_id}")
    assert resp.json()["is_active"] is False


def test_gc_delete_team_blocked_by_confirmed_assignment(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "삭제 보호 업무")
    _login(client, "manager1", "managerpass")
    team_id = client.post("/api/teams", json={"name": "보호팀"}).json()["id"]

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "confirmed"

    resp = client.delete(f"/api/teams/{team_id}")
    assert resp.status_code == 409, resp.text
    assert "삭제할 수 없습니다" in resp.json()["detail"]


def test_gc_pipeline_assignment_request_rejects_unknown_or_inactive_template(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "파이프라인 배정 업무")
    _login(client, "manager1", "managerpass")

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "pipeline", "resource_id": 99999},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "pipeline_template_not_found"

    template_id = _pipeline_template_id(conn)
    conn.execute(
        "UPDATE pipeline_templates SET is_active = 0 WHERE id = ?", (template_id,)
    )
    conn.commit()
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "pipeline", "resource_id": template_id},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "pipeline_template_inactive"


def test_gc_pipeline_assignment_approval_triggers_pipeline_run_with_requester_as_actor(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "실행 트리거 업무")
    template_id = _pipeline_template_id(conn)
    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "pipeline", "resource_id": template_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    assignment_id = resp.json()["assignment_id"]

    # describe_subject가 결재함에서 파이프라인 이름을 보여주는지.
    resp = client.get("/api/approvals", params={"scope": "mine"})
    mine = next(a for a in resp.json() if a["id"] == approval_id)
    assert mine["resource_type"] == "pipeline"
    assert mine["resource_name"] == "소프트웨어 개발 파이프라인"

    # 요청자 본인은 self-approval에 막힌다.
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/approvals/{approval_id}/decide", json={"approve": True}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignment"]["status"] == "confirmed"
    assert body["new_pipeline_run_id"] is not None
    run_id = body["new_pipeline_run_id"]

    run = conn.execute(
        "SELECT actor_id, task_id, pipeline_template_id FROM pipeline_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert (
        run["actor_id"] == manager1_id
    ), "actor_id는 승인자가 아니라 원래 요청자여야 한다"
    assert run["task_id"] == task_id
    assert run["pipeline_template_id"] == template_id

    # 배정 조회에 파이프라인 이름이 노출되는지.
    resp = client.get(f"/api/tasks/{task_id}/assignments")
    row = next(r for r in resp.json() if r["id"] == assignment_id)
    assert row["resource_type"] == "pipeline"
    assert row["resource_name"] == "소프트웨어 개발 파이프라인"


def test_gc_pipeline_assignment_approval_does_not_duplicate_run_if_already_running(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "중복 방지 업무")
    template_id = _pipeline_template_id(conn)

    _login(client, "manager1", "managerpass")
    # 먼저 execute-pipeline으로 직접 run을 하나 띄워 'running' 상태로 만든다.
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-pipeline",
            json={"pipeline_template_id": template_id},
        )
    assert resp.status_code == 202, resp.text

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "pipeline", "resource_id": template_id},
    )
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/approvals/{approval_id}/decide", json={"approve": True}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 배정 자체는 confirmed로 남지만, 이미 실행 중이라 새 run은 안 생긴다.
    assert body["assignment"]["status"] == "confirmed"
    assert body["new_pipeline_run_id"] is None

    runs = conn.execute(
        "SELECT COUNT(*) AS c FROM pipeline_runs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert runs["c"] == 1


def test_gc_org_chart_shows_correct_names_for_team_and_pipeline_resources(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "조직도 표시 업무")
    _brief_id2, task_id2 = _make_brief_and_task(client, "조직도 표시 업무2")
    template_id = _pipeline_template_id(conn)

    _login(client, "admin1", "adminpass")
    team_id = client.post("/api/teams", json={"name": "조직도팀"}).json()["id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    team_approval_id = resp.json()["approval_id"]
    resp = client.post(
        f"/api/tasks/{task_id2}/assignments",
        json={"resource_type": "pipeline", "resource_id": template_id},
    )
    pipeline_approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{team_approval_id}/decide", json={"approve": True})
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        client.post(
            f"/api/approvals/{pipeline_approval_id}/decide", json={"approve": True}
        )

    resp = client.get("/api/org-chart")
    assert resp.status_code == 200
    all_reports = [r for bucket in resp.json() for r in bucket["reports"]]
    team_reports = [r for r in all_reports if r["resource_type"] == "team"]
    pipeline_reports = [r for r in all_reports if r["resource_type"] == "pipeline"]
    assert any(r["resource_name"] == "조직도팀" for r in team_reports)
    assert any(
        r["resource_name"] == "소프트웨어 개발 파이프라인" for r in pipeline_reports
    )
    # 과거 버그: team/pipeline이 agent 분기로 떨어져 "Agent#N (None)"으로
    # 잘못 표시됐다 — 회귀 확인.
    assert not any(str(r["resource_name"]).startswith("Agent#") for r in team_reports)
    assert not any(
        str(r["resource_name"]).startswith("Agent#") for r in pipeline_reports
    )
