"""Phase 25 — 결과물(Deliverable) 형식(content_type) Tier 2 — 사용자 요청
(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT, HTML 등 다양할
수 있을텐데 현재 지원 가능한 범위와, 확장할 수 있는 범위는 어디까지일지...
확인해줘" → "Tier 2까지 진행해줘").

분석 결과 기존 `deliverables.content`는 순수
TEXT였다(업로드/다운로드/mime 처리 전혀 없음). Tier 2 범위: `content_type`
컬럼(`'text'|'markdown'|'html'|'code'`, DB CHECK 제약) + 사람이 직접
고르는 값(`DeliverableCreate.content_type`) + Agent/LLM에게 그 형식으로
써 달라고 지시하는 프롬프트 보강(`assist()`/`start_agent_execution()`/
`crew_runtime.run_crew_for_team()`). 실제 파일(PPT 등) 생성·저장은
Tier 3로 범위 밖.

- 사람 작성 결과물 — content_type 기본값('text')과 명시값 저장·조회.
- Pydantic Literal 검증 — 허용 안 된 값은 422.
- assist() — content_type별 프롬프트 보강(지시문 추가/미추가).
- execute-agent — 프롬프트 보강 + 완성된 deliverable에 content_type 저장.
- execute-team-crew — 팀 Crew 산출물 각각에 content_type 저장.
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


def _setup_human_assignee_task(client, conn, task_name="백엔드 개발자"):
    """member1이 사람 당사자로 confirmed 배정된 task를 만든다."""
    _brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
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
    return task_id


def _setup_agent_requester_task(client, conn, task_name="QA 엔지니어"):
    """manager1이 Agent 배정을 요청해 requester가 되는 task를 만든다."""
    _brief_id, task_id = _make_brief_and_task(client, task_name)
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (task_name,),
    )
    conn.commit()
    agent_id = cur.lastrowid
    _confirm_assignment(
        client, task_id, "agent", agent_id, requester=("manager1", "managerpass")
    )
    return task_id, agent_id


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_team_confirmed_on_task(client, conn, task_id, agent_ids, team_name="팀"):
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


def test_gc244_deliverable_content_type_defaults_and_is_stored(client_and_conn):
    client, conn = client_and_conn
    task_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "형식 기본값 테스트"}
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "# 제목\n내용", "content_type": "markdown"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    by_content = {d["content"]: d["content_type"] for d in resp.json()}
    assert by_content["형식 기본값 테스트"] == "text"
    assert by_content["# 제목\n내용"] == "markdown"


def test_gc245_invalid_content_type_rejected_with_422(client_and_conn):
    client, conn = client_and_conn
    task_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "지원 안 하는 형식", "content_type": "pdf"},
    )
    assert resp.status_code == 422, resp.text


def test_gc246_assist_appends_format_instruction_per_content_type(client_and_conn):
    client, conn = client_and_conn
    task_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    with patch("hair_platform.llm.call_llm", return_value="초안") as mock_call:
        resp = client.post(
            f"/api/tasks/{task_id}/assist",
            json={"draft_context": "초안 맥락", "content_type": "markdown"},
        )
    assert resp.status_code == 200, resp.text
    prompt_arg = mock_call.call_args.args[3]
    assert "Markdown 형식" in prompt_arg

    with patch("hair_platform.llm.call_llm", return_value="초안") as mock_call:
        resp = client.post(
            f"/api/tasks/{task_id}/assist",
            json={"draft_context": "초안 맥락", "content_type": "text"},
        )
    assert resp.status_code == 200, resp.text
    prompt_arg = mock_call.call_args.args[3]
    assert prompt_arg == "초안 맥락"  # text는 지시문 없이 그대로


def test_gc247_execute_agent_prompt_and_deliverable_use_content_type(client_and_conn):
    client, conn = client_and_conn
    task_id, _agent_id = _setup_agent_requester_task(client, conn)

    _login(client, "manager1", "managerpass")
    with patch(
        "hair_platform.llm.call_llm", return_value="```python\nprint(1)\n```"
    ) as mock_call:
        # 사용자 요청(2026-10-26, "A안 + B안")으로 이 엔드포인트가
        # 멀티파트(Form)로 바뀌었다 — JSON 바디는 파싱되지 않고 조용히
        # 기본값으로 떨어지므로 `data=`로 보내야 한다.
        resp = client.post(
            f"/api/tasks/{task_id}/execute-agent", data={"content_type": "code"}
        )
    assert resp.status_code == 202, resp.text

    prompt_arg = mock_call.call_args.args[3]
    assert "코드 블록" in prompt_arg

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    agent_rows = [r for r in resp.json() if r["author_type"] == "agent"]
    assert len(agent_rows) == 1
    assert agent_rows[0]["content_type"] == "code"


def test_gc248_team_crew_execution_stores_content_type_on_each_deliverable(
    client_and_conn,
):
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "런칭 발표자료 준비")
    agent1 = _make_active_agent(conn, "카피 초안")
    agent2 = _make_active_agent(conn, "슬라이드 디자인 가이드")
    team_id = _make_team_confirmed_on_task(client, conn, task_id, [agent1, agent2])

    _login(client, "manager1", "managerpass")
    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종", ["카피 결과", "디자인 결과"]),
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-team-crew",
            json={"team_id": team_id, "content_type": "html"},
        )
    assert resp.status_code == 202, resp.text

    resp = client.get(f"/api/tasks/{task_id}/deliverables")
    agent_rows = [r for r in resp.json() if r["author_type"] == "agent"]
    assert len(agent_rows) == 2
    assert all(r["content_type"] == "html" for r in agent_rows)
