"""Phase 17 — 협업 워크스페이스: 결과물 엔티티 + 내 업무 진입 흐름 + Skill/Tool
역할 재정의(사용자 요청, 2026-09-28, "HAIR의 핵심은 사람과 AI Agent가
협업하여 일하는 구조야..." → "팀이 승인되고... 내 업무 메뉴에서 확인되고...
사람-에이전트협업 프로세스로 펼쳐지고" → "사람이 하는 업무의 경우...
simple_llm 호출... skill을 사용할 수 있도록").

`docs/PHASE1_DELIVERABLE_SPEC.md`의 GC127-137(Phase 1)에 더해, Phase 2
(산출물 승인 게이트, 사용자 요청 2026-09-28 "다음 단계 진행해줘")의
GC138-142, Phase 3(Agent 평가 인라인 연결, 사용자 요청 2026-09-28
"Phase 3 진행해줘")의 GC143-145, Phase 4(execute-agent 비동기화, 사용자
요청 2026-09-28 "Phase 4 진행해줘")의 GC146-148, Phase 1-4 리뷰(사용자
요청 2026-09-28 "Phase1~4까지... 분석하고 개선해줘")의 GC149-152도 이
파일에 담는다(GC153은 `test_phase15_data_deletion.py`에 있다 — 그
파일의 `delete_task()`를 고치는 내용이라 그쪽에 뒀다).

**실측으로 발견한 진짜 크로스-Phase 버그(리뷰 중)**: `assignments.
resolve_assignment_decision()`은 원래 `resource_type` 구분 없이 같은
task의 confirmed 배정을 전부 supersede했다 — Phase 1 이전엔 "task 하나엔
활성 배정 하나"만 있던 시절이라 문제가 없었지만, 협업 워크스페이스가
"사람 당사자 + Agent 요청자가 같은 task에 함께 confirmed"를 전제로
하면서 먼저 확정된 쪽이 나중 확정에 조용히 superseded되는 버그가 됐다
(`docs/LIMITS.md` §26 참고). GC149로 재현·수정 확인.

**Phase 4 테스트 관례**: `TestClient`는 FastAPI `BackgroundTasks`를 응답
반환 *전에* 동기로 실행한다는 것을 별도 probe 스크립트로 실측 확인했다
(`docs/LIMITS.md` §25) — 그래서 `client.post(".../execute-agent")`가
돌아온 시점에 이미 job이 끝나 있고, 곧바로 `GET .../agent-execution-jobs`
로 최종 상태를 조회할 수 있다. 실제 배포 환경(uvicorn)에서는 HTTP
응답이 먼저 나가고 그 뒤에 백그라운드 작업이 실행되므로, 이 테스트들이
확인하는 건 "로직이 맞다"이지 "실제로 응답이 빨리 온다"가 아니다 —
후자는 라이브 서버 실측으로 별도 확인했다.

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
    # Phase 4 — 비동기화돼 202/job_id를 즉시 반환한다. TestClient는
    # BackgroundTasks를 응답 반환 전에 동기로 실행하므로(실측 확인) 이
    # 시점엔 이미 생성까지 끝나 있다.
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"

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
    """Phase 4 — execute-agent는 202+job_id만 즉시 돌려준다. TestClient는
    BackgroundTasks를 응답 반환 전에 동기로 실행하므로(실측 확인,
    docs/LIMITS.md §25) 이 시점엔 이미 job이 끝나 있다 — job을 조회해
    deliverable_id를 얻는다."""
    _login(client, *requester)
    with patch("hair_platform.llm.call_llm", return_value="Agent 산출물"):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["status"] == "succeeded", job
    deliverable_id = job["deliverable_id"]
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


# ---------- Phase 3 — Agent 평가 인라인 연결(사용자 요청, 2026-09-28, "Phase 3 진행해줘") ----------


def test_gc143_execute_agent_records_gate_scores_on_deliverable(client_and_conn):
    """Agent 산출물이 생기면 그 자리에서 Gate A-G가 측정돼 `evaluation`으로
    함께 노출돼야 한다 — 사람이 쓴 deliverable은 애초에 평가 대상이 아니라
    항상 evaluation=None이다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    assert row["evaluation"] is not None
    gate_scores = row["evaluation"]["gate_scores"]
    assert set(gate_scores.keys()) == {"A", "B", "C", "D", "E", "F", "G"}
    for gate in gate_scores.values():
        assert set(gate.keys()) >= {"score", "status", "passed"}
    assert row["evaluation"]["tcr"] is not None


def test_gc144_human_deliverable_has_no_evaluation(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "사람이 쓴 초안"}
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    row = next(d for d in resp.json() if d["author_type"] == "human")
    assert row["evaluation"] is None


def test_gc145_aggregate_agent_score_averages_across_evaluations(client_and_conn):
    """`agent_pool.performance_score`(관리자 수동)와 별도로, 자동 측정치는
    그 Agent가 만든 모든 deliverable의 평가를 평균 낸다 — 평가 이력이
    없으면 None(수동 값만 있다는 뜻)."""
    from hair_platform import agent_evaluation

    client, conn = client_and_conn
    _brief_id, task_id, agent_id = _setup_agent_requester_task(client)

    assert agent_evaluation.aggregate_agent_score(conn, agent_id) is None

    _execute_agent_and_get_deliverable(client, task_id)
    _execute_agent_and_get_deliverable(client, task_id)

    agg = agent_evaluation.aggregate_agent_score(conn, agent_id)
    assert agg is not None
    assert agg["sample_count"] == 2
    assert set(agg["gate_averages"].keys()) == {"A", "B", "C", "D", "E", "F", "G"}


# ---------- Phase 4 — execute-agent 비동기화(사용자 요청, 2026-09-28, "Phase 4 진행해줘") ----------


def test_gc146_execute_agent_returns_202_then_job_succeeds(client_and_conn):
    """202로 즉시 응답하고, job을 폴링하면 완료 후 deliverable_id가 채워진
    'succeeded' 상태를 볼 수 있어야 한다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", return_value="비동기 산출물"):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] == "running"

    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    assert resp.status_code == 200
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["status"] == "succeeded"
    assert job["error"] is None
    assert job["deliverable_id"] is not None

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    row = next(d for d in resp.json() if d["id"] == job["deliverable_id"])
    assert row["content"] == "비동기 산출물"
    assert row["status"] == "pending_review"


def test_gc147_failed_execution_records_error_and_leaves_no_deliverable(
    client_and_conn,
):
    """LLM 호출이 실패하면 job이 'failed'+에러 메시지로 남아야 하고,
    deliverable도 결재 체인도 생기지 않아야 한다(성공 시 부분 상태가
    남지 않는다는 기존 불변식이 비동기 경로에서도 지켜져야 함)."""
    client, conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("모의 API 장애")):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["status"] == "failed"
    assert "모의 API 장애" in job["error"]
    assert job["deliverable_id"] is None

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    assert [d for d in resp.json() if d["author_type"] == "agent"] == []
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'deliverable'"
        ).fetchone()["c"]
        == 0
    )


def test_gc148_execution_jobs_endpoint_respects_participant_rbac(client_and_conn):
    """결과물 타임라인 조회와 같은 권한 규칙 — 참가자/매니저/관리자만
    job 목록을 볼 수 있고, 무관한 사용자는 403."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "outsider3", "password": "outsiderpass", "role": "member"},
    )
    assert resp.status_code == 201
    _login(client, "outsider3", "outsiderpass")
    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------- Phase 1-4 리뷰(사용자 요청, 2026-09-28, "Phase1~4까지... 분석하고 개선해줘") ----------


def test_gc149_human_assignee_and_agent_requester_coexist_confirmed(client_and_conn):
    """실측으로 발견한 크로스-Phase 버그 — 사람 당사자를 먼저 confirmed한
    뒤 같은 task에 Agent 배정도 confirmed하면(협업 워크스페이스의 핵심
    시나리오), 예전 F5/F6 "재배치" 로직이 resource_type을 구분하지 않아
    먼저 확정된 사람 배정이 조용히 superseded 됐었다 — 이제는 서로 다른
    resource_type끼리는 supersede하지 않아야 한다."""
    client, conn = client_and_conn
    brief_id, task_id = _make_brief_and_task(client, "겸용 업무")
    assert brief_id

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "겸용 사람", "role": "겸용 업무", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    _link_human_to_member1(client, conn, human_id)
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )

    cur = conn_from_client(client).execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("겸용 업무",),
    )
    conn_from_client(client).commit()
    agent_id = cur.lastrowid
    _confirm_assignment(
        client, task_id, "agent", agent_id, requester=("manager1", "managerpass")
    )

    rows = conn.execute(
        "SELECT resource_type, status FROM assignments WHERE task_id = ?", (task_id,)
    ).fetchall()
    statuses = {r["resource_type"]: r["status"] for r in rows}
    assert statuses == {"human": "confirmed", "agent": "confirmed"}

    # task_participant_relationship()도 둘 다 정상적으로 인식해야 한다.
    _login(client, "member1", "memberpass")
    resp = client.get("/api/my-work")
    rel = next(r for r in resp.json() if r["task_id"] == task_id)
    assert rel["my_relationship"] == "assignee"

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/my-work")
    rel = next(r for r in resp.json() if r["task_id"] == task_id)
    assert rel["my_relationship"] == "requester"


def test_gc150_reassignment_within_same_resource_type_still_supersedes(
    client_and_conn,
):
    """GC149 수정이 기존 "같은 타입 재배치" 동작(`test_v2_pipeline.py`)을
    깨지 않았는지 이 파일의 헬퍼로도 재확인 — 같은 resource_type끼리는
    여전히 supersede돼야 한다."""
    client, conn = client_and_conn
    brief_id, task_id = _make_brief_and_task(client, "재배치 업무")
    assert brief_id

    _login(client, "admin1", "adminpass")
    resp_a = client.post(
        "/api/human-pool",
        json={"name": "후보A", "role": "재배치 업무", "skill_tags": []},
    )
    human_a = resp_a.json()["id"]
    _confirm_assignment(
        client, task_id, "human", human_a, requester=("manager1", "managerpass")
    )

    resp_b = client.post(
        "/api/human-pool",
        json={"name": "후보B", "role": "재배치 업무", "skill_tags": []},
    )
    human_b = resp_b.json()["id"]
    _confirm_assignment(
        client, task_id, "human", human_b, requester=("manager1", "managerpass")
    )

    rows = {
        r["resource_id"]: r["status"]
        for r in conn.execute(
            "SELECT resource_id, status FROM assignments WHERE task_id = ? "
            "AND resource_type = 'human'",
            (task_id,),
        ).fetchall()
    }
    assert rows[human_a] == "superseded"
    assert rows[human_b] == "confirmed"


def test_gc151_orphaned_running_jobs_are_reaped_on_startup(client_and_conn):
    """실측으로 발견한 갭 — 서버 프로세스가 실행 도중 죽으면 `running`
    job이 영원히 남아 해당 task의 실행 버튼이 복구 불가능하게 막힌다.
    `create_app()`이 기동 시점에 `reap_orphaned_execution_jobs()`로
    이걸 정리해야 한다."""
    from hair_platform import deliverables
    from hair_platform.app import create_app

    client, conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    # 프로세스가 죽어 'running'으로 멈춘 상황을 흉내낸다.
    conn.execute(
        "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
        "VALUES (?, 1, 'running')",
        (task_id,),
    )
    conn.commit()

    # 직접 호출로도 확인.
    reaped = deliverables.reap_orphaned_execution_jobs(conn)
    assert reaped == 1
    row = conn.execute(
        "SELECT status, error FROM agent_execution_jobs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"]

    # create_app() 기동 경로에도 실제로 연결돼 있는지 별도 DB 파일로 확인.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        from hair_platform import db as db_mod

        setup_conn = db_mod.get_connection(tmp.name)
        db_mod.init_schema(setup_conn)
        setup_conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('u', 'h', 'admin')"
        )
        setup_conn.execute(
            "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 't', '[]')"
        )
        setup_conn.execute(
            "INSERT INTO tasks (brief_id, role_name, required_skills) "
            "VALUES (1, 't', '[]')"
        )
        setup_conn.execute(
            "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
            "VALUES (1, 1, 'running')"
        )
        setup_conn.commit()
        setup_conn.close()

        app = create_app(tmp.name)
        row = app.state.db.execute("SELECT status FROM agent_execution_jobs").fetchone()
        assert row["status"] == "failed"


def test_gc152_agent_pool_endpoint_exposes_auto_evaluation(client_and_conn):
    """실측으로 발견한 갭 — Phase 3가 만든 `aggregate_agent_score()`가
    `GET /api/agent-pool` 응답의 `auto_evaluation`으로 실제 노출되는지
    확인한다(이전까지는 어떤 API에도 연결돼 있지 않아 테스트 말고는
    아무도 못 보는 죽은 코드였다)."""
    client, _conn = client_and_conn
    _brief_id, task_id, agent_id = _setup_agent_requester_task(client)

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["auto_evaluation"] is None  # 아직 평가 이력 없음

    _execute_agent_and_get_deliverable(client, task_id)

    resp = client.get("/api/agent-pool")
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["auto_evaluation"]["sample_count"] == 1
    assert set(row["auto_evaluation"]["gate_averages"].keys()) == {
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
    }


def test_gc154_execute_agent_rejects_duplicate_trigger_while_running(client_and_conn):
    """실측으로 발견한 갭(리뷰 2회차) — `start_agent_execution()`은 같은
    task에 이미 'running' job이 있어도 확인 없이 새 job을 또 만들었다.
    Web UI 버튼 비활성화는 클라이언트 쪽 가드일 뿐이라 빠른 연속
    클릭·다른 탭·직접 API 호출로는 우회된다 — 같은 트리거로 실제 LLM
    호출이 두 번 나가는(비용 이중 지출) 걸 서버 쪽에서도 막아야 한다."""
    client, conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    # 다른 요청이 이미 실행 중인 상황을 흉내낸다(TestClient는
    # BackgroundTasks를 동기 실행해 정상 경로로는 이 상태를 관찰할 수
    # 없으므로 직접 만든다).
    conn.execute(
        "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
        "VALUES (?, 1, 'running')",
        (task_id,),
    )
    conn.commit()

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 409, resp.text
    assert "실행 중" in resp.json()["detail"]

    # 여전히 job이 하나뿐이어야 한다(두 번째가 실제로 안 생겼는지).
    jobs = conn.execute(
        "SELECT COUNT(*) AS c FROM agent_execution_jobs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert jobs["c"] == 1


def test_gc155_failed_job_shows_friendly_message_for_known_errors(client_and_conn):
    """실측으로 발견한 갭(리뷰 2회차) — Agent가 트리거 시점 이후, 실제
    실행 전에 오프보딩되면 `agent_runtime.execute_agent()`가 내부 코드
    문자열("agent_not_active")을 그대로 던진다. 그게 그대로 Web UI에
    노출되지 않고 사람이 읽을 수 있는 문구로 바뀌어야 한다."""
    from hair_platform import deliverables

    client, conn = client_and_conn
    _brief_id, task_id, agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    job = deliverables.start_agent_execution(
        conn, task_id, manager1_id, allow_supervisor_override=True
    )

    # 트리거 이후, 실제 실행 전에 오프보딩됐다고 가정.
    conn.execute(
        "UPDATE agent_pool SET status = 'deprecated' WHERE id = ?", (agent_id,)
    )
    conn.commit()

    deliverables.run_agent_execution_job(
        conn,
        job["job_id"],
        task_id,
        job["agent_id"],
        manager1_id,
        job["role_name"],
        job["prompt"],
        None,
        None,
    )

    row = conn.execute(
        "SELECT status, error FROM agent_execution_jobs WHERE id = ?", (job["job_id"],)
    ).fetchone()
    assert row["status"] == "failed"
    assert (
        row["error"] == "Agent가 더 이상 활성 상태가 아닙니다(오프보딩됐을 수 있습니다)"
    )
