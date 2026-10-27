"""Phase 38 — AI Agent에 이름 지정(사용자 요청, 2026-10-13, "AI Agent에도
이름을 지정하고, AI 에이전트 목록 포함 AI 에이전트 지정 항목이나
호출시(채팅 포함)에도 활용될 수 있도록 개선해줘").

지금까지 Agent는 `role_name`(생성 당시 역할, 예: "백엔드 개발자 Agent")이
식별자 역할까지 겸해 와서, 같은 역할의 Agent가 여럿이면 숫자 ID로만
구분해야 했다. `agent_pool.name`(선택 입력)을 추가해 지정하면 매칭·
검색·배정·조직도·결재·댓글/채팅 `@멘션` 전부에서 역할명보다 우선
쓰이게 한다 — 지정 안 하면 기존처럼 역할명 그대로(100% 하위 호환).

이 테스트는 신규 `PATCH /api/agent-pool/{id}/name` 엔드포인트와, 그
이름이 실제로 화면/멘션 경로 각각에 반영되는지를 확인한다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


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


def _confirm_assignment(client, task_id, resource_type, resource_id, requester):
    _login(client, requester[0], requester[1])
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": resource_type, "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text


def _create_active_agent(conn, role_name="백엔드 개발자", skill_tags="[]"):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        (role_name, skill_tags),
    )
    conn.commit()
    return cur.lastrowid


def test_set_agent_name_requires_admin_or_manager_and_persists(client_and_conn):
    client, conn = client_and_conn
    agent_id = _create_active_agent(conn, "백엔드 개발자")

    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "에이다"})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "에이다"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": agent_id, "name": "에이다"}

    resp = client.get("/api/agent-pool")
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["name"] == "에이다"
    assert row["role_name"] == "백엔드 개발자"  # role_name은 그대로 보존된다

    # 빈 문자열/공백으로 저장하면 이름을 지운다(다시 role_name 표시로 복귀).
    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "   "})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": agent_id, "name": None}
    resp = client.get("/api/agent-pool")
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["name"] is None


def test_set_agent_name_404_for_missing_agent(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.patch("/api/agent-pool/999999/name", json={"name": "유령"})
    assert resp.status_code == 404


def test_pool_search_and_match_surface_agent_name(client_and_conn):
    client, conn = client_and_conn
    agent_id = _create_active_agent(conn, "백엔드 개발자")
    _login(client, "admin1", "adminpass")
    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "에이다"})
    assert resp.status_code == 200, resp.text

    # "인력 검색" — 이름이 역할명과 별도로 노출된다.
    resp = client.get("/api/pool/search")
    item = next(
        i for i in resp.json()["items"] if i["id"] == agent_id and i["type"] == "agent"
    )
    assert item["name"] == "에이다"
    assert item["role"] == "백엔드 개발자"

    # 매칭 결과 — 같은 역할의 Task를 매칭하면 candidate.name이 그대로 온다.
    _login(client, "manager1", "managerpass")
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200, resp.text
    top = resp.json()["top"]
    match = next(c for c in top if c["candidate"]["id"] == agent_id)
    assert match["candidate"]["name"] == "에이다"


def test_assignment_and_org_chart_prefer_agent_name_over_role_name(client_and_conn):
    client, conn = client_and_conn
    agent_id = _create_active_agent(conn, "QA 엔지니어")
    _brief_id, task_id = _make_brief_and_task(client, "QA 엔지니어")
    _confirm_assignment(
        client, task_id, "agent", agent_id, requester=("manager1", "managerpass")
    )

    _login(client, "admin1", "adminpass")
    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "검증이"})
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/tasks/{task_id}/assignments")
    row = next(a for a in resp.json() if a["resource_id"] == agent_id)
    assert row["resource_name"] == "검증이"

    resp = client.get("/api/org-chart")
    reports = [r for bucket in resp.json() for r in bucket["reports"]]
    report = next(
        r for r in reports if r["task_id"] == task_id and r["resource_type"] == "agent"
    )
    # 조직도는 "Agent#N (이름)" 형식으로 감싸서 보여준다(app.py
    # get_org_chart) — 역할명 대신 지정한 이름이 그 안에 들어간다.
    assert report["resource_name"] == f"Agent#{agent_id} (검증이)"


def test_comment_mention_accepts_both_custom_name_and_role_name(client_and_conn):
    client, conn = client_and_conn
    agent_id = _create_active_agent(conn, "호출용 Agent")
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "담당자", "role": "백엔드 개발자", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )

    resp = client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "에이다"})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "초안입니다"}
    )
    deliverable_id = resp.json()["id"]

    # 1) 지정한 이름("@에이다")으로 멘션 — 새로 지정한 이름이 바로 먹혀야 한다.
    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="답변1"
    ) as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@에이다 검토해줘"},
        )
        assert resp.status_code == 201, resp.text
        assert [j["agent_id"] for j in resp.json()["triggered_jobs"]] == [agent_id]
        mock_exec.assert_called_once()

    # 2) 기존 방식("@역할명")도 하위 호환으로 계속 동작해야 한다.
    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="답변2"
    ) as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@호출용 Agent 다시 한번"},
        )
        assert resp.status_code == 201, resp.text
        assert [j["agent_id"] for j in resp.json()["triggered_jobs"]] == [agent_id]
        mock_exec.assert_called_once()

    # 참가자 목록의 표시 이름도 지정한 이름을 우선한다.
    resp = client.get(f"/api/tasks/{task_id}/participants")
    participant = next(p for p in resp.json() if p["participant_id"] == agent_id)
    assert participant["display_name"] == "에이다"


def test_conversation_search_mentionable_prefers_agent_name(client_and_conn):
    client, conn = client_and_conn
    agent_id = _create_active_agent(conn, "백엔드 개발자", '["python"]')
    _login(client, "admin1", "adminpass")
    actor_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    resp = conversations.search_mentionable(conn, "", actor_id)
    item = next(a for a in resp["agents"] if a["id"] == agent_id)
    assert item["role_name"] == "백엔드 개발자"  # 이름 지정 전엔 역할명 그대로

    client.patch(f"/api/agent-pool/{agent_id}/name", json={"name": "에이다"})

    resp = conversations.search_mentionable(conn, "", actor_id)
    item = next(a for a in resp["agents"] if a["id"] == agent_id)
    assert item["role_name"] == "에이다"

    # 지정한 이름으로도 검색이 된다(역할/스킬만 검색되던 것의 확장).
    resp = conversations.search_mentionable(conn, "에이다", actor_id)
    assert any(a["id"] == agent_id for a in resp["agents"])
