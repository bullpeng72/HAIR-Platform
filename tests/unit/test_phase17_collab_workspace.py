"""Phase 17 — 협업 워크스페이스: 결과물 엔티티 + 내 업무 진입 흐름 + Skill/Tool
역할 재정의(사용자 요청, 2026-09-28, "HAIR의 핵심은 사람과 AI Agent가
협업하여 일하는 구조야..." → "팀이 승인되고... 내 업무 메뉴에서 확인되고...
사람-에이전트협업 프로세스로 펼쳐지고" → "사람이 하는 업무의 경우...
simple_llm 호출... skill을 사용할 수 있도록").

`docs/PHASE1_DELIVERABLE_SPEC.md`의 GC127-137(Phase 1)에 더해, Phase 2
(산출물 승인 게이트, 사용자 요청 2026-09-28 "다음 단계 진행해줘")의
GC138-142도 이 파일에 담는다.

**실측으로 발견한 진짜 선행 버그**: `human_pool.user_id`(F8
`review_for_member()`가 이미 참조하던 컬럼)를 채울 API가 이 작업 전까지
전혀 없었다 — `HumanPoolUpdate`에 `user_id` 필드를 추가해서 메웠다. 이게
없으면 이 파일의 어떤 "사람 당사자(assignee)" 테스트도 통과할 수 없다
(연결할 방법 자체가 없으므로).
"""

from __future__ import annotations

from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_and_task(client, task_name="백엔드 개발자", skills=None):
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": skills or []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def _confirm_assignment(client, task_id, resource_type, resource_id, requester):
    """요청자를 명시적으로 지정해 로그인한다 — self-approval 방지(ADR 3-A) 때문에
    반드시 admin1이 아닌 계정으로 요청해야 admin1이 승인할 수 있다."""
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
    assert resp.json()["assignment"]["status"] == "confirmed"


def _member1_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[
        "id"
    ]


def _link_human_to_member1(client, conn, human_id):
    """member1을 human_pool 레코드에 연결한다 — 관리자 전용 신규 필드."""
    _login(client, "admin1", "adminpass")
    member1_id = _member1_id(conn)
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    return member1_id


def _setup_human_assignee_task(client, conn, task_name="백엔드 개발자", skills=None):
    """member1이 사람 당사자로 confirmed 배정된 task를 만든다."""
    brief_id, task_id = _make_brief_and_task(client, task_name, skills)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": skills or []},
    )
    human_id = resp.json()["id"]
    _link_human_to_member1(client, conn, human_id)
    # member1 본인이 요청하면 self-approval에 걸리므로 manager1이 요청한다.
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id, human_id


def _setup_agent_requester_task(client, task_name="QA 엔지니어", skills=None):
    """manager1이 Agent 배정을 요청해 requester가 되는 task를 만든다."""
    brief_id, task_id = _make_brief_and_task(client, task_name, skills)
    _login(client, "admin1", "adminpass")
    cur = conn_from_client(client).execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (task_name,),
    )
    conn_from_client(client).commit()
    agent_id = cur.lastrowid
    _confirm_assignment(
        client, task_id, "agent", agent_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id, agent_id


def conn_from_client(client):
    return client.app.state.db


def test_gc127_my_work_returns_confirmed_human_assignee_task(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.get("/api/my-work")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["my_relationship"] == "assignee"
    assert rows[0]["resource_type"] == "human"
    assert rows[0]["deliverable_count"] == 0


def test_gc128_my_work_returns_confirmed_agent_requester_task(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/my-work")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["my_relationship"] == "requester"
    assert rows[0]["resource_type"] == "agent"
    assert rows[0]["resource_id"] == agent_id


def test_gc129_my_work_excludes_non_confirmed_assignments(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "디자이너")
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "미확정 후보", "role": "디자이너", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = _link_human_to_member1(client, conn, human_id)
    assert member1_id  # 연결 자체는 됨

    # 요청만 하고 승인하지 않는다 — status='requested'로 남는다.
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assert resp.status_code == 201

    _login(client, "member1", "memberpass")
    resp = client.get("/api/my-work")
    assert resp.status_code == 200
    assert not [r for r in resp.json() if r["task_id"] == task_id]


def test_gc130_post_and_list_deliverable_as_human(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "초안 작성했습니다"}
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["author_type"] == "human"
    assert rows[0]["status"] == "draft"
    assert rows[0]["content"] == "초안 작성했습니다"


def test_gc131_non_participant_member_forbidden(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "admin1", "adminpass")
    create_user_resp = client.post(
        "/api/users",
        json={"username": "outsider1", "password": "outsiderpass", "role": "member"},
    )
    assert create_user_resp.status_code == 201, create_user_resp.text

    _login(client, "outsider1", "outsiderpass")
    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    assert resp.status_code == 403
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "무단 작성"}
    )
    assert resp.status_code == 403


def test_gc132_admin_manager_can_view_but_not_write_deliverable(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "admin1", "adminpass")
    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    assert resp.status_code == 200  # 감독 목적 조회는 허용

    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "관리자 대리 작성"}
    )
    assert resp.status_code == 403  # 대리 작성은 금지


def test_gc133_assist_injects_matching_skill_pool_description(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(
        client, conn, skills=["graphdb"]
    )

    _login(client, "member1", "memberpass")
    with patch("hair_platform.llm.call_llm", return_value="LLM 초안") as mock_call:
        resp = client.post(f"/api/tasks/{task_id}/assist", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft"] == "LLM 초안"
    system_arg = mock_call.call_args.args[2]
    assert "GraphDB" in system_arg or "관계분석" in system_arg


def test_gc134_member_cannot_choose_provider_manager_can(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/assist", json={"provider": "openai"})
    assert resp.status_code == 400

    # 매니저는 참가자가 아니라도 provider 지정 시도 자체는 400이 아니어야
    # 하지만(감독 관점), 참가자 판정에는 걸리지 않으므로 403이 정상이다.
    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", return_value="매니저 초안"):
        resp = client.post(f"/api/tasks/{task_id}/assist", json={"provider": "openai"})
    assert resp.status_code == 403  # manager1은 이 task의 참가자가 아님(400 아님)


def test_gc135_execute_agent_includes_latest_human_deliverable(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "요구사항 메모"}
    )
    # manager1은 requester지 assignee가 아니라 사람 작성 deliverable은 못 올린다
    # (참가자 관계는 있지만 §2-3은 human 작성만 허용 — requester도 참가자이므로
    # 실제로는 허용된다).
    assert resp.status_code == 201, resp.text

    with patch(
        "hair_platform.llm.call_llm", return_value="Agent가 만든 산출물"
    ) as mock_call:
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 201, resp.text

    prompt_arg = mock_call.call_args.args[3]
    assert "요구사항 메모" in prompt_arg

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    agent_rows = [r for r in resp.json() if r["author_type"] == "agent"]
    assert len(agent_rows) == 1
    assert agent_rows[0]["content"] == "Agent가 만든 산출물"
    assert agent_rows[0]["status"] == "pending_review"


def test_gc136_execute_agent_without_confirmed_agent_assignment_conflicts(
    client_and_conn,
):
    """member1은 이 task의 human assignee일 뿐 agent 배정의 requester가
    아니므로(그런 배정 자체가 없다) execute-agent를 시도하면 403이 먼저
    난다(실측으로 발견 — 참가자 판정이 "assignee"/"requester"를 구분하기
    때문에 당연한 결과다). 409("확정 Agent 배정 없음")에 실제로 도달하려면
    참가자 판정을 우회할 수 있는 관리자/매니저(supervisor override)로
    호출해야 한다."""
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 409


def test_gc137_deliverable_comments_use_parent_task_participant_check(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/deliverables", json={"content": "초안"})
    deliverable_id = resp.json()["id"]

    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments",
        json={"content": "여기 참고하세요"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/deliverables/{deliverable_id}/comments")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["content"] == "여기 참고하세요"

    _login(client, "admin1", "adminpass")
    create_user_resp = client.post(
        "/api/users",
        json={"username": "outsider2", "password": "outsiderpass", "role": "member"},
    )
    assert create_user_resp.status_code == 201
    _login(client, "outsider2", "outsiderpass")
    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments", json={"content": "몰래"}
    )
    assert resp.status_code == 403


# ---------- Phase 2 — 산출물 승인 게이트(사용자 요청, 2026-09-28, "다음 단계 진행해줘") ----------


def _execute_agent_and_get_deliverable(
    client, task_id, requester=("manager1", "managerpass")
):
    _login(client, *requester)
    with patch("hair_platform.llm.call_llm", return_value="Agent 산출물"):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 201, resp.text
    deliverable_id = resp.json()["id"]
    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    assert resp.status_code == 200
    row = next(d for d in resp.json() if d["id"] == deliverable_id)
    return deliverable_id, row


def test_gc138_execute_agent_creates_approval_chain_for_deliverable(client_and_conn):
    """Agent 산출물이 생기면 자동으로 결재 체인이 만들어지고, 그 결재 id가
    결과물 목록의 approval_id로 노출돼야 워크스페이스에서 바로 처리할 수
    있다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    assert row["status"] == "pending_review"
    assert row["approval_id"] is not None

    resp = client.get("/api/approvals", params={"scope": "mine"})
    assert resp.status_code == 200
    matches = [a for a in resp.json() if a["id"] == row["approval_id"]]
    assert len(matches) == 1
    assert matches[0]["subject_type"] == "deliverable"


def test_gc139_self_approval_blocked_then_admin_approves(client_and_conn):
    """manager1이 execute-agent를 트리거했으므로(requested_by=manager1)
    본인은 그 산출물을 승인할 수 없다(ADR 3-A 재사용) — 다른 승인자(admin1)
    는 승인할 수 있고, 승인되면 deliverable.status='approved'로 바뀌며
    approval_id는 다시 null이 된다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)
    deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    approval_id = row["approval_id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deliverable"] == {
        "deliverable_id": deliverable_id,
        "status": "approved",
    }

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    row = next(d for d in resp.json() if d["id"] == deliverable_id)
    assert row["status"] == "approved"
    assert row["approval_id"] is None


def test_gc140_reject_sets_deliverable_status_rejected(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)
    deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{row['approval_id']}/decide", json={"approve": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deliverable"]["status"] == "rejected"

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    row = next(d for d in resp.json() if d["id"] == deliverable_id)
    assert row["status"] == "rejected"
    assert row["approval_id"] is None


def test_gc141_deliverable_approval_appears_in_admin_inbox(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)
    _deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert resp.status_code == 200
    matches = [a for a in resp.json() if a["id"] == row["approval_id"]]
    assert len(matches) == 1
    assert matches[0]["subject_type"] == "deliverable"


def test_gc142_human_draft_deliverable_never_has_approval_id(client_and_conn):
    """사람이 작성한 deliverable은 애초에 승인 게이트 대상이 아니다 —
    approval_id가 항상 null이어야 한다."""
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "사람이 쓴 초안"}
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    row = next(d for d in resp.json() if d["author_type"] == "human")
    assert row["status"] == "draft"
    assert row["approval_id"] is None
