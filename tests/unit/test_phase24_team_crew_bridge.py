"""Phase 24 — Crew 실행을 팀(Team)·업무(Task) 파이프라인에 연결 — 사용자
요청(2026-10-07, "crew 실행에서 묶인 에이전트 그룹의 경우 하나의 그룹만
가능한 구조인데, 복수 그룹 가능하도록 개선, 이 그룹이 전체 업무에서
사람-에이전트 연동이 가능한 구조인지 확인해줘").

Phase 3 — 실측으로 확인한
전제: 기존 `run_crew()`는 `agent_ids`를 매번 직접 골라야 하는 "즉석
실행"이고(저장되는 그룹 개념 없음), `task_id`를 받지 않아 결과가
`assignments`/`deliverables`/결재 체인 어디에도 연결되지 않았다("알려진
갭"에서 이미 지적). "복수 그룹"은 팀(teams.py)을 재사용하는
방식으로 해결한다 — 한 할 일에 팀을 여러 개 배정하는 것 자체는
"다중 confirmed 허용"으로 이미 가능했고(팀도 resource_type별 제한
없음), 이번에 추가하는 것은 "그중 한 팀의 Agent 멤버를 실제 CrewAI
Crew로 실행해 그 결과를 업무 파이프라인에 연결하는 다리"다(사용자
결정 — 여러 팀을 동시 실행하지 않고 "팀 하나를 골라서 실행").

- `crew_runtime.run_crew_for_team()` — 검증(팀 존재, confirmed
배정, active Agent 멤버)과 task 맥락 기반 프롬프트 구성.
- `/api/tasks/{id}/execute-team-crew` 엔드포인트 — Agent별
deliverable+결재 체인 생성(사람-에이전트 연동 증명), RBAC, 복수 팀
독립 실행.

사용자 후속 피드백(2026-10-07, "현재 UI가 복수의 에이전트
그룹 생성 및 HAIR 관점의 연동에 적합한 경우는 아님... AI 에이전트
생성과 통합 또는 AI 에이전트 관리 노출 검토")으로 추가한 것 —
`teams.team_names_for_agent()`와 `GET /api/agent-pool`의 `team_names`
노출(관리 화면에서 Agent의 팀 소속을 바로 보여주기 위함).

사용자 요청(2026-10-07, "시나리오 내용도 crew 에이전트 그룹을
포함하도록 개선해줘")으로 시나리오 5를 "개별
Agent 배정 없이 팀 배정 하나만으로 팀 Crew 실행" 흐름으로 다시 쓰며
라이브 실행하다 발견한 실제 버그 — `deliverables.list_my_work()`
(`GET /api/my-work`)가 `resource_type='team'`을 전혀 몰랐다. 같은
파일의 `task_participant_relationship()`(워크스페이스 접근 권한
판정)은 Phase 1+2 때 이미 team_leader/team_member를 인식하도록
고쳤는데, "내 업무" **목록** 조회는 완전히 별개의 SQL이라 그때 같이
안 고쳐졌다 — 지금까지 모든 팀 배정 시나리오가 개별 사람/Agent
배정도 같이 썼던 우연 덕에 가려져 있었다."""

from __future__ import annotations

from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def _make_task(client, role_name="런칭 발표자료 준비"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def _make_team_confirmed_on_task(client, conn, task_id, agent_ids, team_name="팀"):
    """manager1이 팀을 만들고 Agent 멤버를 추가한 뒤, 그 task에 배정 요청해
    admin1이 승인(confirmed)까지 마친다. team_id를 반환한다."""
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/teams", json={"name": team_name})
    team_id = resp.json()["id"]
    for agent_id in agent_ids:
        resp = client.post(
            f"/api/teams/{team_id}/members",
            json={"member_type": "agent", "member_id": agent_id},
        )
        assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed", resp.text
    return team_id


def test_gc237_run_crew_for_team_rejects_unconfirmed_team(client_and_conn):
    from hair_platform import crew_runtime

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "런칭 발표자료 준비")

    resp = client.post("/api/teams", json={"name": "미배정팀"})
    team_id = resp.json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": agent_id},
    )
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    # 이 task에 배정·승인된 적 없는 팀 — 실행할 수 없어야 한다.
    import pytest

    with pytest.raises(ValueError, match="team_not_confirmed_for_task"):
        crew_runtime.run_crew_for_team(conn, team_id, task_id, admin_id)


def test_gc238_run_crew_for_team_shares_task_context_across_active_members_only(
    client_and_conn,
):
    """팀 멤버 중 온보딩(active) 안 된 Agent는 실행 대상에서 빠지고,
    task_descriptions는 할 일의 role_name + 최신 사람 작성 자료로
    구성된다(단일 Agent 실행과 같은 프롬프트 패턴)."""
    from hair_platform import crew_runtime

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "런칭 발표자료 준비")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "슬라이드 디자인 가이드")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'pending_onboarding')",
        ("오프보딩 예정",),
    )
    conn.commit()
    inactive_agent = cur.lastrowid

    team_id = _make_team_confirmed_on_task(
        client, conn, task_id, [agent1, agent2, inactive_agent]
    )

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "캠페인 목표: 전환율 15% 증가"},
    )
    assert resp.status_code == 201, resp.text

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종", ["카피 초안 결과", "디자인 가이드 결과"]),
    ) as m:
        result = crew_runtime.run_crew_for_team(
            conn, team_id, task_id, admin_id, process="sequential"
        )

    m.assert_called_once()
    assert {a["agent_id"] for a in result["agents"]} == {agent1, agent2}
    assert inactive_agent not in {a["agent_id"] for a in result["agents"]}


def test_gc239_execute_team_crew_endpoint_creates_deliverable_and_approval_per_agent(
    client_and_conn,
):
    """핵심 검증 — "사람-에이전트 연동 가능한 구조인지": 팀 Crew 실행
    결과가 각 Agent별 deliverable + 결재 체인으로 떨어져야, 그 순간
    부터 사람(팀 리더/팀원)이 협업 워크스페이스에서 보고 승인할 수
    있다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "런칭 발표자료 준비")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "슬라이드 디자인 가이드")
    team_id = _make_team_confirmed_on_task(client, conn, task_id, [agent1, agent2])

    _login(client, "manager1", "managerpass")
    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종", ["카피 초안 결과", "디자인 가이드 결과"]),
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew",
            json={"team_id": team_id, "process": "sequential"},
        )
    assert resp.status_code == 202, resp.text
    job_ids = resp.json()["job_ids"]
    assert len(job_ids) == 2

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    agent_delivs = [d for d in resp.json() if d["author_type"] == "agent"]
    assert len(agent_delivs) == 2
    assert {d["status"] for d in agent_delivs} == {"pending_review"}
    assert all(d["approval_id"] is not None for d in agent_delivs)
    assert all(d["evaluation"] is not None for d in agent_delivs)
    assert {d["content"] for d in agent_delivs} == {
        "카피 초안 결과",
        "디자인 가이드 결과",
    }

    # manager1(요청자)은 자기 요청 건을 self-approval 방지로 승인 못 하고,
    # admin1은 통과한다 — 단일 Agent 실행과 같은 결재 체인 재사용 확인.
    _login(client, "admin1", "adminpass")
    for d in agent_delivs:
        resp = client.post(
            f"/api/approvals/{d['approval_id']}/decide", json={"approve": True}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["deliverable"]["status"] == "approved"


def test_gc240_execute_team_crew_rejects_non_participant(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "런칭 발표자료 준비")
    team_id = _make_team_confirmed_on_task(client, conn, task_id, [agent_id])

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/execute-team-crew",
        json={"team_id": team_id},
    )
    assert resp.status_code == 403, resp.text


def test_gc241_two_teams_on_one_task_execute_independently(client_and_conn):
    """ "복수 그룹" 핵심 — 한 할 일에 팀 2개가 confirmed로 공존하고
    (다중 confirmed 허용), 각각 독립적으로 Crew 실행할 수 있다(동시
    아님 — 사용자 결정대로 한 번에 하나씩)."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "런칭 발표자료 준비")
    agent1 = _make_active_agent(conn, "카피팀 Agent")
    agent2 = _make_active_agent(conn, "디자인팀 Agent")
    team_a = _make_team_confirmed_on_task(client, conn, task_id, [agent1], "카피팀")
    team_b = _make_team_confirmed_on_task(client, conn, task_id, [agent2], "디자인팀")

    resp = client.get(f"/api/tasks/{task_id}/assignments")
    team_assignments = [a for a in resp.json() if a["resource_type"] == "team"]
    assert {a["status"] for a in team_assignments} == {"confirmed"}
    assert len(team_assignments) == 2

    _login(client, "manager1", "managerpass")
    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("A팀 결과", ["A팀 결과"])
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew", json={"team_id": team_a}
        )
    assert resp.status_code == 202, resp.text

    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("B팀 결과", ["B팀 결과"])
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew", json={"team_id": team_b}
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    contents = {d["content"] for d in resp.json() if d["author_type"] == "agent"}
    assert contents == {"A팀 결과", "B팀 결과"}


# ---------- Crew 빌더(2026-10-20) — 팀 사전 등록 없는 즉석 Crew 실행 ----------


def test_adhoc_crew_execution_available_to_any_task_participant_not_just_team(
    client_and_conn,
):
    """핵심 검증 — 팀을 전혀 만들지 않고도(위임만으로 참가자가 된 사람)
    즉석으로 Agent를 골라 바로 실행할 수 있어야 한다. 기존 "팀 Agent
    실행"은 팀 리더/팀원으로 좁히지만, 즉석 실행은 이 Task의 confirmed
    배정에 걸린 사람이면 누구나(위임 요청자 포함) 쓸 수 있다."""
    from hair_platform import assignments

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "즉석 실행 업무")
    agent_id = _make_active_agent(conn, "카피 초안")
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    # 팀 없이, 위임(delegation)만으로 member1을 이 task의 참가자("requester")
    # 로 만든다 — create_delegation()은 즉시 confirmed된다.
    assignments.create_delegation(conn, task_id, agent_id, member_id)

    _login(client, "member1", "memberpass")
    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("즉석 결과", ["즉석 결과"])
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-adhoc-crew",
            json={"agent_ids": [agent_id], "task_descriptions": ["카피를 작성하세요"]},
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    agent_delivs = [d for d in resp.json() if d["author_type"] == "agent"]
    assert len(agent_delivs) == 1
    assert agent_delivs[0]["content"] == "즉석 결과"
    assert agent_delivs[0]["status"] == "pending_review"


def test_adhoc_crew_execution_rejects_non_participant(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "즉석 실행 거부 업무")
    agent_id = _make_active_agent(conn, "카피 초안")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/execute-adhoc-crew",
        json={"agent_ids": [agent_id], "task_descriptions": ["아무거나"]},
    )
    assert resp.status_code == 403, resp.text


def test_adhoc_crew_execution_lets_each_agent_have_a_different_task_description(
    client_and_conn,
):
    """즉석 실행만의 장점 — "팀 Agent 실행"은 전원에게 같은 설명을
    주지만, 즉석 실행은 Agent별로 서로 다른 지시를 줄 수 있다(실제
    CrewAI Task 객체까지 inspect — `_kickoff_capture` 패턴)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client, "역할 분담 업무")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "디자인 가이드")

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("최종", ["카피 결과", "디자인 결과"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-adhoc-crew",
            json={
                "agent_ids": [agent1, agent2],
                "task_descriptions": ["카피를 작성하세요", "디자인 가이드를 작성하세요"],
            },
        )
    assert resp.status_code == 202, resp.text

    crew = captured["crew"]
    assert "카피를 작성하세요" in crew.tasks[0].description
    assert "디자인 가이드를 작성하세요" in crew.tasks[1].description

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    contents = {d["content"] for d in resp.json() if d["author_type"] == "agent"}
    assert contents == {"카피 결과", "디자인 결과"}


def test_adhoc_crew_execution_rejects_inactive_agent(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client, "비활성 Agent 거부 업무")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('아직 온보딩 안 됨', '[]', 'pending_onboarding')"
    )
    inactive_agent_id = cur.lastrowid
    conn.commit()

    resp = client.post(
        f"/api/tasks/{task_id}/execute-adhoc-crew",
        json={"agent_ids": [inactive_agent_id], "task_descriptions": ["아무거나"]},
    )
    assert resp.status_code == 400, resp.text


def test_adhoc_crew_execution_rejects_mismatched_agent_and_description_counts(
    client_and_conn,
):
    """사용자 요청(2026-10-20) — 길이가 안 맞으면 job 행을 만들기 전에
    즉시 400으로 막아야 한다(먼저 job을 만들고 뒤늦게 막으면 그
    job들이 영원히 'running'에 갇힌다)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client, "개수 불일치 거부 업무")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "디자인 가이드")

    resp = client.post(
        f"/api/tasks/{task_id}/execute-adhoc-crew",
        json={"agent_ids": [agent1, agent2], "task_descriptions": ["설명 하나뿐"]},
    )
    assert resp.status_code == 400, resp.text
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM agent_execution_jobs WHERE task_id = ?",
            (task_id,),
        ).fetchone()["c"]
        == 0
    )


# ---------- Crew 빌더(2026-10-20) — 실행 이력(`crew_history`) ----------


def test_adhoc_crew_run_is_recorded_in_crew_history_with_progress(client_and_conn):
    """즉석 Crew 실행이 `crew_runs`에 영구 기록되는지 — `on_task_
    complete` 콜백이 실제로 Task 완료마다 호출돼 `progress`가 쌓이고,
    끝나면 gate_scores/token_usage/agents(diagram에 쓰일 데이터)까지
    남는지 확인한다. `self.task_callback(output)`을 직접 호출하는
    기존 패턴(`test_gc101_run_crew_records_audit_log_per_task_via_
    task_callback`, test_phase12)을 그대로 재사용 — `crewai.Crew.
    kickoff`를 통째로 mock하면 CrewAI 내부 루프가 안 돌아 콜백이
    저절로는 안 불리므로, 콜백 호출 자체를 이 mock이 흉내 낸다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client, "이력 기록 업무")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "디자인 가이드")

    def _kickoff_with_task_callback(self, *args, **kwargs):
        from crewai.tasks.task_output import TaskOutput

        outputs = [
            TaskOutput(description="task-0", agent="agent", raw="카피 결과"),
            TaskOutput(description="task-1", agent="agent", raw="디자인 결과"),
        ]
        for output in outputs:
            self.task_callback(output)
        return _crew_output("최종", ["카피 결과", "디자인 결과"])

    with patch("crewai.Crew.kickoff", new=_kickoff_with_task_callback):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-adhoc-crew",
            json={
                "agent_ids": [agent1, agent2],
                "task_descriptions": ["카피를 작성하세요", "디자인 가이드를 작성하세요"],
            },
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/crew-runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert len(runs) == 1
    run = runs[0]
    assert run["kind"] == "adhoc"
    assert run["status"] == "completed"
    assert run["team_id"] is None
    assert set(run["agent_ids"]) == {agent1, agent2}
    assert len(run["progress"]) == 2
    assert run["progress"][0]["output_preview"] == "카피 결과"
    assert run["gate_scores"] is not None
    assert run["token_usage"] is not None
    assert run["agents"] is not None
    assert run["final_output"] == "최종"

    resp = client.get(f"/api/crew-runs/{run['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["final_output"] == "최종"

    resp = client.get("/api/crew-runs")
    assert resp.status_code == 200, resp.text
    assert any(r["id"] == run["id"] for r in resp.json())


def test_team_crew_run_is_recorded_in_crew_history_with_team_id(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "팀 이력 기록 업무")
    agent1 = _make_active_agent(conn, "카피 초안")
    team_id = _make_team_confirmed_on_task(client, conn, task_id, [agent1])

    _login(client, "manager1", "managerpass")
    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("팀 결과", ["팀 결과"])
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew", json={"team_id": team_id}
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/crew-runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["kind"] == "team"
    assert runs[0]["team_id"] == team_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["agent_ids"] == [agent1]


def test_crew_run_history_records_failure_without_leaving_it_running(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client, "실패 이력 업무")
    agent_id = _make_active_agent(conn, "카피 초안")

    with patch("crewai.Crew.kickoff", side_effect=RuntimeError("모델 호출 실패")):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-adhoc-crew",
            json={"agent_ids": [agent_id], "task_descriptions": ["아무거나"]},
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/crew-runs")
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"]
    assert runs[0]["gate_scores"] is None


def test_list_crew_runs_requires_admin_or_manager(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/crew-runs")
    assert resp.status_code == 403, resp.text


def test_get_crew_run_404_for_missing_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/crew-runs/999999")
    assert resp.status_code == 404, resp.text


def test_gc242_team_names_for_agent_reflects_membership(client_and_conn):
    from hair_platform import teams

    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent_id = _make_active_agent(conn, "카피 초안")

    assert teams.team_names_for_agent(conn, agent_id) == []

    team_a = teams.create_team(conn, admin_id, "A팀")
    team_b = teams.create_team(conn, admin_id, "B팀")
    teams.add_team_member(conn, admin_id, team_a, "agent", agent_id)
    teams.add_team_member(conn, admin_id, team_b, "agent", agent_id)

    # 한 Agent가 여러 팀에 동시에 속할 수 있다 — team_members의 UNIQUE
    # 제약은 (team_id, member_type, member_id)뿐, Agent당 팀 1개 제한은
    # 없다.
    assert teams.team_names_for_agent(conn, agent_id) == ["A팀", "B팀"]


def test_gc243_agent_pool_endpoint_exposes_team_names(client_and_conn):
    """ "AI 에이전트 관리" 화면이 Agent의 팀 소속을 바로 보여줄 수 있도록
    `GET /api/agent-pool`이 `team_names`를 노출해야 한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_in_team = _make_active_agent(conn, "카피 초안")
    agent_without_team = _make_active_agent(conn, "소속 없음")

    resp = client.post("/api/teams", json={"name": "런칭 캠페인팀"})
    team_id = resp.json()["id"]
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": agent_in_team},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200, resp.text
    by_id = {a["id"]: a for a in resp.json()}
    assert by_id[agent_in_team]["team_names"] == ["런칭 캠페인팀"]
    assert by_id[agent_without_team]["team_names"] == []


def test_gc249_my_work_lists_team_only_assignment_for_leader_and_member(
    client_and_conn,
):
    """핵심 버그 재현 — 개별 사람/Agent 배정 없이 **팀 배정 하나만** 있는
    task도 팀 리더·팀원 둘 다 "내 업무"에서 봐야 한다(`task_participant_
    relationship()`은 이미 인식하는데 `list_my_work()`는 몰랐다)."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "팀 전용 배정 업무")

    resp = client.post("/api/teams", json={"name": "팀전용배정팀"})
    team_id = resp.json()["id"]

    _login(client, "admin1", "adminpass")
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": "팀원", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": human_id},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed", resp.text

    # 팀 리더(manager1, 기본값 = 요청자) — 개별 배정이 하나도 없어도
    # "내 업무"에 떠야 한다.
    _login(client, "manager1", "managerpass")
    resp = client.get("/api/my-work")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["task_id"] == task_id]
    assert len(rows) == 1, resp.json()
    assert rows[0]["my_relationship"] == "team_leader"
    assert rows[0]["resource_type"] == "team"

    # 팀원(member1) — 역시 개별 배정 없이도 떠야 한다.
    _login(client, "member1", "memberpass")
    resp = client.get("/api/my-work")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["task_id"] == task_id]
    assert len(rows) == 1, resp.json()
    assert rows[0]["my_relationship"] == "team_member"

    # 이 팀과 무관한 admin1은 안 떠야 한다.
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/my-work")
    assert task_id not in [r["task_id"] for r in resp.json()]


def test_gc250_run_crew_for_team_auto_detects_manager_when_hierarchical_and_unset(
    client_and_conn,
):
    """사용자 요청(2026-10-18, "기존 개발자 크루 팀을... 업무 할당시
    배정하면 위 변경 내용을 모두 적용할 수 있는지 확인해줘") 후속 — 실측
    으로 확인한 gap: 이 함수는 `manager_agent_id`를 호출자가 명시적으로
    넘겨야만 썼는데, Task 협업 워크스페이스의 "팀 Agent 실행(Crew)"
    화면에는 매니저를 고르는 UI가 없어 항상 `None`으로 호출돼
    `conversations.run_team_mention_job()` 쪽에서 이미 고친 "매니저가
    실제로 역할별 하위 과업을 쪼개 위임"하는 경로를 전혀 못 탔다.
    `teams.manager_agent_id_for_team()`으로 자동 탐지하도록 고친 뒤의
    동작을 확인한다."""
    from hair_platform import crew_runtime

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "자동 매니저 탐지 업무")
    manager_agent = _make_active_agent(conn, "개발 계획 수립가")
    worker_agent = _make_active_agent(conn, "개발자")

    resp = client.post("/api/teams", json={"name": "자동탐지팀"})
    team_id = resp.json()["id"]
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": manager_agent, "role_label": "매니저"},
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": worker_agent, "role_label": "워커"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed", resp.text

    captured = {}

    def _capture_run_crew(conn, agent_ids, task_descriptions, actor_id, process="sequential",
                           provider=None, model=None, manager_agent_id=None,
                           on_task_complete=None, memory=True, planning=False,
                           manager_provider=None, manager_model=None):
        captured["process"] = process
        captured["manager_agent_id"] = manager_agent_id
        return _crew_output("최종", ["x"] * len(agent_ids))

    # hierarchical을 골랐는데 manager_agent_id는 안 넘김(UI에 선택
    # 수단이 없는 실제 상황과 동일) — 팀의 "매니저" 라벨 멤버가 자동으로
    # 채워져야 한다.
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_capture_run_crew):
        crew_runtime.run_crew_for_team(
            conn, team_id, task_id, admin_id, process="hierarchical"
        )
    assert captured["manager_agent_id"] == manager_agent

    # sequential을 고른 경우는 건드리지 않는다(매니저가 있어도 그대로
    # None) — 사용자가 릴레이를 의도적으로 고른 선택을 존중한다.
    captured.clear()
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_capture_run_crew):
        crew_runtime.run_crew_for_team(
            conn, team_id, task_id, admin_id, process="sequential"
        )
    assert captured["manager_agent_id"] is None

    # 호출자가 이미 명시적으로 manager_agent_id를 지정했으면 자동
    # 탐지로 덮어쓰지 않는다.
    captured.clear()
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_capture_run_crew):
        crew_runtime.run_crew_for_team(
            conn, team_id, task_id, admin_id, process="hierarchical",
            manager_agent_id=worker_agent,
        )
    assert captured["manager_agent_id"] == worker_agent


# ---------- 고급 에이전트 관리(2026-10-20) — 팀 기본 process/매니저 ----------


def test_update_team_defaults_requires_admin_or_manager_and_validates(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    manager_agent = _make_active_agent(conn, "개발 계획 수립가")
    other_agent = _make_active_agent(conn, "다른 팀 Agent")
    resp = client.post("/api/teams", json={"name": "기본값팀"})
    team_id = resp.json()["id"]
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": manager_agent},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/teams/{team_id}", json={"default_process": "hierarchical"})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/teams/{team_id}",
        json={"default_process": "hierarchical", "default_manager_agent_id": manager_agent},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": team_id,
        "default_process": "hierarchical",
        "default_manager_agent_id": manager_agent,
    }

    resp = client.get(f"/api/teams/{team_id}")
    assert resp.json()["default_process"] == "hierarchical"
    assert resp.json()["default_manager_agent_id"] == manager_agent

    # 이 팀의 멤버가 아닌 Agent는 매니저로 지정할 수 없다.
    resp = client.patch(
        f"/api/teams/{team_id}", json={"default_manager_agent_id": other_agent}
    )
    assert resp.status_code == 400, resp.text

    # 허용 안 된 process 값은 거부된다(Pydantic Literal 검증, 422).
    resp = client.patch(f"/api/teams/{team_id}", json={"default_process": "parallel"})
    assert resp.status_code == 422, resp.text

    # None을 명시적으로 보내면 기본값을 지운다.
    resp = client.patch(
        f"/api/teams/{team_id}",
        json={"default_process": None, "default_manager_agent_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_process"] is None
    assert resp.json()["default_manager_agent_id"] is None

    resp = client.patch("/api/teams/999999", json={"default_process": "sequential"})
    assert resp.status_code == 404


def test_manager_agent_id_for_team_prefers_explicit_default_over_role_label_heuristic(
    client_and_conn,
):
    """사용자 요청(2026-10-20, "고급 에이전트 관리") — `teams.
    default_manager_agent_id`를 명시적으로 지정해뒀으면 `role_label`
    문자열 추정보다 우선해야 한다. 탈퇴/비활성화되면 조용히 추정으로
    폴백해야 전체 실행이 깨지지 않는다."""
    from hair_platform import teams

    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    labeled_manager = _make_active_agent(conn, "기획자")
    explicit_manager = _make_active_agent(conn, "PM")
    resp = client.post("/api/teams", json={"name": "우선순위팀"})
    team_id = resp.json()["id"]
    # role_label="매니저"인 멤버와, 그와 별개로 명시적 기본값으로 지정할
    # 또 다른 Agent 둘 다 팀에 넣는다.
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": labeled_manager, "role_label": "매니저"},
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": explicit_manager},
    )
    assert resp.status_code == 201, resp.text

    # 아직 default_manager_agent_id가 없으면 기존처럼 role_label 추정.
    assert teams.manager_agent_id_for_team(conn, team_id) == labeled_manager

    resp = client.patch(
        f"/api/teams/{team_id}", json={"default_manager_agent_id": explicit_manager}
    )
    assert resp.status_code == 200, resp.text
    # 명시적 기본값이 role_label 추정보다 우선한다.
    assert teams.manager_agent_id_for_team(conn, team_id) == explicit_manager

    # 지정된 매니저가 더 이상 active가 아니면 조용히 추정으로 폴백한다.
    conn.execute(
        "UPDATE agent_pool SET status = 'deprecated' WHERE id = ?", (explicit_manager,)
    )
    conn.commit()
    assert teams.manager_agent_id_for_team(conn, team_id) == labeled_manager


def test_execute_team_crew_endpoint_falls_back_to_team_default_process_and_manager(
    client_and_conn,
):
    """사용자 요청(2026-10-20, "고급 에이전트 관리") — "팀 Agent 실행"
    요청에 `process`/`manager_agent_id`를 안 보내면(기존 UI 기본값
    그대로) 팀에 저장된 기본값을 쓴다. 명시적으로 보내면 여전히 그
    값이 우선한다(기존 동작 유지)."""
    client, conn = client_and_conn
    manager_agent = _make_active_agent(conn, "개발 계획 수립가")
    worker_agent = _make_active_agent(conn, "개발자")
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client, "팀 기본값 실행 업무")
    team_id = _make_team_confirmed_on_task(
        client, conn, task_id, [manager_agent, worker_agent], team_name="실행기본값팀"
    )
    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/teams/{team_id}",
        json={"default_process": "hierarchical", "default_manager_agent_id": manager_agent},
    )
    assert resp.status_code == 200, resp.text

    captured = {}

    def _capture_run_team_crew_job(conn, job_by_agent_id, task_id, team_id, actor_id,
                                    process, provider, model, manager_agent_id,
                                    content_type="text", memory=True, planning=False,
                                    manager_provider=None, manager_model=None):
        captured["process"] = process
        captured["manager_agent_id"] = manager_agent_id
        # 실제 run_team_crew_job()을 안 태우므로, 그게 하던 "job을 종결
        # 상태로 바꾸는" 역할을 대신한다 — 안 하면 다음 호출이 "이미
        # 실행 중"(409)으로 막힌다.
        for job_id in job_by_agent_id.values():
            conn.execute(
                "UPDATE agent_execution_jobs SET status = 'succeeded' WHERE id = ?",
                (job_id,),
            )
        conn.commit()

    with patch(
        "hair_platform.deliverables.run_team_crew_job",
        side_effect=_capture_run_team_crew_job,
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew", json={"team_id": team_id}
        )
        assert resp.status_code == 202, resp.text

    assert captured["process"] == "hierarchical"
    assert captured["manager_agent_id"] == manager_agent

    # 명시적으로 보내면 팀 기본값보다 우선한다.
    with patch(
        "hair_platform.deliverables.run_team_crew_job",
        side_effect=_capture_run_team_crew_job,
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew",
            json={"team_id": team_id, "process": "sequential"},
        )
        assert resp.status_code == 202, resp.text
    assert captured["process"] == "sequential"


# ---------- Crew 빌더(2026-10-20) — Crew 레벨 고급 옵션(로드맵 4단계):
# memory/planning 토글 + 매니저 전용 경량 LLM ----------


def test_run_crew_memory_and_planning_toggle_pass_through_to_crew(client_and_conn):
    """`memory`/`planning`이 실제 `Crew(...)` 생성자에 그대로 전달되는지
    확인한다. `crewai.Crew.kickoff`를 통째로 mock해도 `self`는 이미 정상
    생성된 실제 Crew 인스턴스이므로, 그 속성을 직접 읽어 검증한다(추측
    없이 CrewAI 자신이 들고 있는 값). 기본값(둘 다 생략)이 기존 동작
    (memory=True, planning=False)과 같은지도 같이 확인한다(하위 호환)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "작가")
    agent2 = _make_active_agent(conn, "편집자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["memory"] = self.memory
        captured["planning"] = self.planning
        return _crew_output("최종", ["x", "y"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["글을 쓰세요", "교정하세요"], admin_id,
            memory=False, planning=True,
        )
    assert captured == {"memory": False, "planning": True}

    captured.clear()
    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["글을 쓰세요", "교정하세요"], admin_id,
        )
    assert captured == {"memory": True, "planning": False}


def test_run_crew_manager_llm_uses_separate_manager_provider_when_auto_manager(client_and_conn):
    """매니저를 자동 배정(`manager_agent_id=None`)한 hierarchical 실행에서
    `manager_provider`/`manager_model`을 지정하면, 워커 전체가 쓰는 LLM과는
    분리된 매니저 전용 LLM이 구성되는지 확인한다 — Agent 전체를
    `agent_pool`에 등록하지 않고도 매니저만 더 가볍거나 다른 모델로 돌릴
    수 있게 하는 것이 이 기능의 목적(사용자 요청, 2026-10-20, "Crew
    레벨 고급 옵션")."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "작가")
    agent2 = _make_active_agent(conn, "편집자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["manager_llm_model"] = self.manager_llm.model if self.manager_llm else None
        captured["worker_llm_model"] = self.agents[0].llm.model
        return _crew_output("최종", ["x", "y"])

    # provider="openai"(테스트 환경의 `.env`에 OPENAI_API_KEY가 있어
    # 실제 LLM 생성이 가능), manager_provider="ollama"(API 키가
    # 필요 없는 로컬 provider)로 서로 다른 provider임을 확인한다 —
    # anthropic은 이 테스트 환경에 키가 없어 LLM 생성 자체가 실패한다.
    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["글을 쓰세요", "교정하세요"], admin_id,
            process="hierarchical", provider="openai", model="gpt-4o-mini",
            manager_provider="ollama", manager_model="llama3.1",
        )
    # openai는 crewai가 "네이티브 provider"로 다뤄 `self.model`에서
    # provider 접두어를 벗겨내고(`"gpt-4o-mini"`), ollama는 네이티브가
    # 아니라 LiteLLM로 폴백해 접두어를 그대로 유지한다(`"ollama/..."`)
    # — 둘 다 crewai 자신의 실제 동작이라 그대로 검증한다.
    assert captured["manager_llm_model"] == "ollama/llama3.1"
    assert captured["worker_llm_model"] == "gpt-4o-mini"
    assert captured["manager_llm_model"] != captured["worker_llm_model"]


def test_run_crew_manager_llm_falls_back_to_worker_llm_when_manager_provider_not_set(
    client_and_conn,
):
    """`manager_provider`/`manager_model`을 안 주면(기본값) 기존처럼
    워커와 같은 LLM을 매니저에도 그대로 쓴다(하위 호환 — 이전엔 이게
    유일한 동작이었다)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "작가")
    agent2 = _make_active_agent(conn, "편집자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["manager_llm_model"] = self.manager_llm.model if self.manager_llm else None
        captured["worker_llm_model"] = self.agents[0].llm.model
        return _crew_output("최종", ["x", "y"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["글을 쓰세요", "교정하세요"], admin_id,
            process="hierarchical", provider="openai", model="gpt-4o-mini",
        )
    assert captured["manager_llm_model"] == captured["worker_llm_model"] == "gpt-4o-mini"


def test_execute_team_crew_rejects_member_setting_planning_or_manager_llm(client_and_conn):
    """`planning`/`manager_provider`/`manager_model`은 provider/model과
    같은 이유(비용에 직접 영향)로 팀원은 설정할 수 없다(기존 provider/
    model 제한을 그대로 확장). `memory`는 비용을 늘리는 방향이 아니라
    (OPENAI_API_KEY 없이 쓰기 위한 방어적 toggle) 팀원도 설정할 수
    있게 구분해서 둔다 — 마지막 요청은 400이 아니라 403(참가자가 아님)
    이어야 그 구분이 실제로 적용됐다는 뜻이다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "작가")
    team_id = _make_team_confirmed_on_task(client, conn, task_id, [agent_id])

    _login(client, "member1", "memberpass")
    for extra in (
        {"planning": True},
        {"manager_provider": "openai"},
        {"manager_model": "gpt-4o-mini"},
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew",
            json={"team_id": team_id, **extra},
        )
        assert resp.status_code == 400, resp.text

    resp = client.post(
        f"/api/tasks/{task_id}/execute-team-crew",
        json={"team_id": team_id, "memory": False},
    )
    assert resp.status_code == 403, resp.text


def test_execute_adhoc_crew_rejects_member_setting_planning_or_manager_llm(client_and_conn):
    """같은 제한을 "업무에 바로 실행"(즉석 Crew) 경로에도 똑같이
    확인한다 — 두 엔드포인트가 같은 게이팅 로직을 각자 복제해 두고
    있어 하나만 고치고 잊어버릴 위험이 있다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "작가")

    _login(client, "member1", "memberpass")
    for extra in (
        {"planning": True},
        {"manager_provider": "openai"},
        {"manager_model": "gpt-4o-mini"},
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-adhoc-crew",
            json={
                "agent_ids": [agent_id],
                "task_descriptions": ["글을 쓰세요"],
                **extra,
            },
        )
        assert resp.status_code == 400, resp.text
