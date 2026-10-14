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
