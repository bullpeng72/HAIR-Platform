"""Phase 31 — 대화 중심 작업공간(Conversational Workspace)(2026-10-08,
사용자 요청 "현재 방식의
업무 처리는... 유연성이나 Agility가 부족해. 사용자가 로그인하면
기본적으로 대화창이 주어지고, 다른 사용자나 에이전트(에이전트 그룹)을
호출하거나 skill을 사용하여 업무를 수행하는 방식으로... 기존 방식과의
공존(업무 성격에 따라 선택)도 좋아" → 뒤이어 "1인당 대화 여러 개,
Team은 워커별 메시지 각각, skill 조회해 모델에 일을 시킴, 멘션 검색은
조직 전체, ai_auto_reply 기본 켜짐, 로드맵은 전체"로 전부 결정).

`conversations`는 Task 존재 여부와 무관한 독립 엔티티다 —
`linked_task_id`가 NULL이면 로그인 직후 바로 쓰는 "내 대화", 채워지면
특정 Task에 종속된 대화(이전에 노리던 것을 흡수)다. 이 테스트는
세 가지 응답 경로(기본 AI 자동응답/특정 Agent 멘션/특정 Team 멘션)가
서로 독립적으로, 그리고 멘션이 있으면 기본 AI가 끼어들지 않는다는
핵심 규칙을 전부 검증한다. 실제 LLM/CrewAI 호출은 `unittest.mock.patch`
로 대체한다(다른 Phase 테스트 파일들과 같은 관례)."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations, db


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _create_outsider(client, username="outsider1"):
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": username, "password": "outsiderpass", "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_agent(conn, role_name="테스트 Agent"):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def _make_team_with_agent(conn, agent_id, team_name="테스트 팀"):
    cur = conn.execute("INSERT INTO teams (name) VALUES (?)", (team_name,))
    conn.commit()
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'agent', ?)",
        (team_id, agent_id),
    )
    conn.commit()
    return team_id


def _make_task(client, task_name="백엔드 개발자"):
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def _member1_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[
        "id"
    ]


def _make_task_with_member1_participant(client, conn, task_name="백엔드 개발자"):
    """`task_participant_relationship()`은 confirmed 배정 기준이라,
    브리프를 제출/분해한 것만으로는 참가자가 안 된다 — member1을
    실제로 confirmed human 배정해 진짜 참가자로 만든다(다른 Phase
    테스트 파일들과 같은 설정 패턴)."""
    task_id = _make_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = _member1_id(conn)
    client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    return task_id


# ---------- 생성/목록/권한 ----------


def test_gc_create_standalone_conversation_auto_adds_creator(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.post("/api/conversations", json={"title": "잡담방"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["linked_task_id"] is None
    assert body["ai_auto_reply"] == 1  # 사용자 결정: 기본 켜짐

    resp = client.get(f"/api/conversations/{body['id']}/participants")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    assert resp.json()[0]["participant_type"] == "human"


def test_gc_user_can_have_multiple_conversations(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    client.post("/api/conversations", json={"title": "방1"})
    client.post("/api/conversations", json={"title": "방2"})
    resp = client.get("/api/conversations")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2


def test_gc_non_participant_cannot_view_or_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "비공개"}).json()["id"]

    _create_outsider(client)
    _login(client, "outsider1", "outsiderpass")
    resp = client.get(f"/api/conversations/{conv_id}")
    assert resp.status_code == 403, resp.text
    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": "몰래"})
    assert resp.status_code == 403, resp.text


def test_gc_linked_conversation_requires_task_participation(client_and_conn):
    client, conn = client_and_conn
    task_id = _make_task(client)
    _create_outsider(client)
    _login(client, "outsider1", "outsiderpass")
    resp = client.post(
        "/api/conversations", json={"title": "업무 대화", "linked_task_id": task_id}
    )
    assert resp.status_code == 403, resp.text


# ---------- 기본 AI 자동응답 ----------


def test_gc_plain_message_triggers_default_ai_when_auto_reply_on(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "AI방"}).json()["id"]

    with patch("hair_platform.llm.call_llm", return_value="안녕하세요!") as mock_call:
        resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": "안녕"})
        assert resp.status_code == 201, resp.text
        assert len(resp.json()["triggered_jobs"]) == 1
        assert resp.json()["triggered_jobs"][0]["trigger_type"] == "auto_reply"
        mock_call.assert_called_once()

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(messages) == 2
    assert messages[1]["author_type"] == "agent"
    assert messages[1]["author_id"] == 0  # 예약 sentinel
    assert messages[1]["content"] == "안녕하세요!"


def test_gc_auto_reply_off_means_plain_message_gets_no_response(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "조용한방"}).json()["id"]
    resp = client.patch(
        f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ai_auto_reply"] == 0

    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": "사람끼리"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"] == []
        mock_call.assert_not_called()


def test_gc_model_settings_are_used_for_default_ai(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "모델방"}).json()["id"]
    resp = client.patch(
        f"/api/conversations/{conv_id}/model",
        json={"provider": "ollama", "model": "qwen3-coder:latest"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_provider"] == "ollama"

    with patch("hair_platform.llm.call_llm", return_value="응답") as mock_call:
        client.post(f"/api/conversations/{conv_id}/messages", json={"content": "안녕"})
        called_provider, called_model = mock_call.call_args[0][0], mock_call.call_args[0][1]
        assert called_provider == "ollama"
        assert called_model == "qwen3-coder:latest"


# ---------- Agent 멘션 ----------


def test_gc_agent_mention_suppresses_default_ai(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_id = _make_agent(conn)
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "멘션방"}).json()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )
    assert resp.status_code == 201, resp.text

    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="에이전트 답변"
    ) as mock_exec, patch("hair_platform.llm.call_llm") as mock_default:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@테스트 Agent 이거 봐줘"},
        )
        assert resp.status_code == 201, resp.text
        jobs = resp.json()["triggered_jobs"]
        assert len(jobs) == 1
        assert jobs[0]["trigger_type"] == "agent_mention"
        mock_exec.assert_called_once()
        mock_default.assert_not_called()  # 기본 AI는 끼어들지 않는다

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert messages[1]["author_type"] == "agent"
    assert messages[1]["author_id"] == agent_id
    assert messages[1]["content"] == "에이전트 답변"


def test_gc_uninvited_agent_mention_warns_in_default_ai_reply(client_and_conn):
    """사용자 보고(2026-10-19, "'기획서 작성자'로 설정했는데 기본 AI
    (openai/gpt-4o-mini)가 대신 응답했다") — 자동완성 드롭다운을 거치지
    않고 `@역할명`을 직접 타이핑하면, 그 Agent가 아직 이 대화의
    참가자가 아닌 한 멘션으로 인식되지 않아 기본 AI가 조용히 대신
    응답했다. 자동 초대는 여전히 하지 않되(기존 안전 설계 유지), 기본
    AI 응답 맨 앞에 "이 이름을 찾았지만 초대가 안 돼 있다"는 경고를
    붙인다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _make_agent(conn, role_name="기획서 작성자")  # 참가자로 초대하지 않음
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "미초대방"}).json()["id"]

    with patch("hair_platform.llm.call_llm", return_value="기본 AI 응답") as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@기획서 작성자 기획해줘"},
        )
        assert resp.status_code == 201, resp.text
        jobs = resp.json()["triggered_jobs"]
        assert len(jobs) == 1
        assert jobs[0]["trigger_type"] == "auto_reply"  # 멘션으로 인식되지 않음
        mock_call.assert_called_once()

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    reply = messages[1]
    assert reply["author_type"] == "agent"
    assert reply["author_id"] == 0  # 기본 AI sentinel — 실제 Agent 아님
    assert '"@기획서 작성자"' in reply["content"]
    assert "아직 이 대화에 초대되지 않아" in reply["content"]
    assert reply["content"].endswith("기본 AI 응답")


def test_gc_invited_agent_mention_has_no_warning(client_and_conn):
    """반대 사례 — 정상적으로 초대된 뒤 멘션하면 경고 없이 그 Agent가
    직접 응답한다(기본 AI로 새지 않음)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_id = _make_agent(conn, role_name="기획서 작성자")
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "초대방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )

    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="기획서 작성자 응답"
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@기획서 작성자 기획해줘"},
        )
        assert resp.json()["triggered_jobs"][0]["trigger_type"] == "agent_mention"

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert messages[1]["author_id"] == agent_id
    assert messages[1]["content"] == "기획서 작성자 응답"  # 경고 없음


def test_gc_mentioning_human_participant_suppresses_default_ai(client_and_conn):
    """버그 재현(사용자 보고, 2026-10-14, "대화 중 Human을 호출했지만
    AI가 자동 응답을 했다") — 사람을 멘션하면 실행할 job은 없지만
    (사람은 트리거 대상이 아니다), 기본 AI 자동응답도 끼어들면 안
    된다. 수정 전엔 `_detect_mentions()`가 사람 참가자를 아예 조회
    대상에서 빼서 `has_mentions=False`로 잘못 판정됐었다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "사람호출방"}).json()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": manager_id},
    )
    assert resp.status_code == 201, resp.text

    with patch("hair_platform.llm.call_llm") as mock_default:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@manager1 이거 확인해주세요"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"] == []
        mock_default.assert_not_called()


def test_gc_self_mention_does_not_suppress_default_ai(client_and_conn):
    """버그 재현(사용자 보고, 2026-10-14, "본인이 본인에게 멘션을 보낼
    수 있는데, 이 기능이 맞는지/필요한지 확인해줘" — 직접 재현해 확인한
    버그) — "@내아이디"가 메시지에 들어가도 실제로 부른 대상은 아무도
    없다. 수정 전엔 이걸 "사람을 호출함"으로 오판정해 기본 AI 자동응답
    까지 조용히 꺼졌다 — 아무도 안 부른 평범한 메시지와 똑같이 취급돼야
    한다(브라우저 알림 경로 `list_my_recent_mentions()`는 처음부터
    자기 자신을 제외하고 있었는데, 이 경로만 빠져 있었다)."""
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "메모방"}).json()["id"]

    with patch(
        "hair_platform.llm.call_llm", return_value="기본 AI 응답"
    ) as mock_default:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@member1 나한테 메모"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"][0]["trigger_type"] == "auto_reply"
        mock_default.assert_called_once()


def test_gc_mentioning_uninvited_agent_name_does_not_trigger(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _make_agent(conn, "초대 안 된 Agent")
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    with patch("hair_platform.agent_runtime.execute_agent") as mock_exec, patch(
        "hair_platform.llm.call_llm", return_value="기본 응답"
    ) as mock_default:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@초대 안 된 Agent 도와줘"},
        )
        assert resp.status_code == 201, resp.text
        # 멘션 감지가 안 되므로(초대 안 됨) 평범한 메시지로 취급 — 기본 AI가 응답
        assert resp.json()["triggered_jobs"][0]["trigger_type"] == "auto_reply"
        mock_exec.assert_not_called()
        mock_default.assert_called_once()


def test_gc_agent_mention_job_failure_recorded(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_id = _make_agent(conn)
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )

    with patch(
        "hair_platform.agent_runtime.execute_agent", side_effect=RuntimeError("boom")
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@테스트 Agent 실패 테스트"},
        )
        assert resp.status_code == 201, resp.text

    jobs = client.get(f"/api/conversations/{conv_id}/jobs").json()
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error"]


# ---------- Team(에이전트 그룹) 멘션 ----------


def test_gc_team_mention_posts_one_message_per_worker_agent(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent1 = _make_agent(conn, "워커1")
    agent2 = _make_agent(conn, "워커2")
    cur = conn.execute("INSERT INTO teams (name) VALUES ('협업팀')")
    conn.commit()
    team_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'agent', ?)",
        [(team_id, agent1), (team_id, agent2)],
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "팀방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    crew_result = {
        "agents": [
            {"agent_id": agent1, "role_name": "워커1", "output": "워커1의 답변"},
            {"agent_id": agent2, "role_name": "워커2", "output": "워커2의 답변"},
        ],
        "token_usage": {},
        "delegation_count": 0,
    }
    with patch(
        "hair_platform.crew_runtime.run_crew", return_value=crew_result
    ) as mock_crew:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages", json={"content": "@협업팀 검토해줘"}
        )
        assert resp.status_code == 201, resp.text
        mock_crew.assert_called_once()
        call_args = mock_crew.call_args[0]
        assert sorted(call_args[1]) == sorted([agent1, agent2])  # agent_ids

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert len(agent_messages) == 2
    assert {m["content"] for m in agent_messages} == {"워커1의 답변", "워커2의 답변"}


def test_gc_team_mention_skips_manager_with_no_output(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent1 = _make_agent(conn, "매니저")
    team_id = _make_team_with_agent(conn, agent1, "매니저팀")
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    crew_result = {
        "agents": [{"agent_id": agent1, "role_name": "매니저", "output": None}],
        "token_usage": {},
        "delegation_count": 0,
    }
    with patch("hair_platform.crew_runtime.run_crew", return_value=crew_result):
        client.post(
            f"/api/conversations/{conv_id}/messages", json={"content": "@매니저팀 안녕"}
        )

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert len(agent_messages) == 0  # 매니저는 산출물이 없어 메시지가 안 생긴다


def test_gc_team_mention_routes_hierarchical_when_team_has_manager_role_label(
    client_and_conn,
):
    """사용자 보고(2026-10-15, "@자율 개발 크루... 일부 응답이 실패했습니다:
    Expecting value: line 1 column 1 (char 0)") 재발 방지 — "자율 개발
    크루" 같은 매니저+워커 hierarchical 팀(`team_members.role_label`에
    "매니저"가 들어감, `test_phase47_autonomous_dev_crew.py` 참고)이
    Team 멘션으로 실행될 때도 설계된 대로 `process="hierarchical"` +
    `manager_agent_id`로 라우팅되는지 확인한다 — 이전에는 이 구분 없이
    항상 sequential 기본값으로 넘어가 매니저까지 포함한 전원이 동일한
    작업을 중복 수행하며 서로 자유롭게 위임했다(위임/체이닝 트래픽
    과다가 CrewAI/litellm의 JSON 파싱 실패 확률을 키운 근본 원인)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    manager_id = _make_agent(conn, "매니저")
    worker_id = _make_agent(conn, "워커")
    # 사용자 보고(2026-10-19, "매니저의 저장된 설정을 쓰도록") — 매니저
    # 자신에게 저장된 provider/model이 실제 run_crew() 호출에 그대로
    # 전달되는지 함께 검증한다(이전엔 자동판별로 떨어져 무시됐었다).
    conn.execute(
        "UPDATE agent_pool SET llm_provider = 'ollama', llm_model = 'qwen3-coder:latest' "
        "WHERE id = ?",
        (manager_id,),
    )
    conn.commit()
    cur = conn.execute("INSERT INTO teams (name) VALUES ('자율 개발 크루 테스트')")
    conn.commit()
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '매니저(조율자)')",
        (team_id, manager_id),
    )
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '워커')",
        (team_id, worker_id),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    crew_result = {
        "agents": [
            {"agent_id": manager_id, "role_name": "매니저", "output": None},
            {"agent_id": worker_id, "role_name": "워커", "output": "워커의 답변"},
        ],
        "token_usage": {},
        "delegation_count": 0,
    }
    with patch(
        "hair_platform.crew_runtime.run_crew", return_value=crew_result
    ) as mock_crew:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 테스트 테트리스 게임을 작성해줘"},
        )
        assert resp.status_code == 201, resp.text
        mock_crew.assert_called_once()
        _, kwargs = mock_crew.call_args
        assert kwargs["process"] == "hierarchical"
        assert kwargs["manager_agent_id"] == manager_id
        assert kwargs["provider"] == "ollama"
        assert kwargs["model"] == "qwen3-coder:latest"


def test_gc_team_mention_persists_gate_scores_and_delegation_count_and_warns_against_copying_old_output(
    client_and_conn,
):
    """사용자 요청(2026-10-18, "@자율 개발 크루" 결과물 품질 분석 후속) —
    실측(대화 7의 "위 spec을 준수해 게임을 개발해줘" 실행)으로 확인한
    두 문제의 회귀 테스트:
    1) Gate A-G가 fail해도 이 경로는 측정만 하고 버려졌다 — 메시지에
       gate_scores/delegation_count가 저장되고 API로 그대로 내려오는지.
    2) 매니저가 과거 거대한 코드 산출물에 "앵커링"돼 새 지시를 무시하고
       그대로 베끼는 문제 — `run_crew()`에 넘기는 task_description에
       "그대로 복사하지 말라"는 명시적 경고가 포함되는지."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    manager_id = _make_agent(conn, "매니저")
    worker_id = _make_agent(conn, "워커")
    team_id = _make_team_with_agent(conn, manager_id, "Gate테스트팀")
    conn.execute(
        "UPDATE team_members SET role_label = '매니저' WHERE team_id = ? AND member_id = ?",
        (team_id, manager_id),
    )
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '워커')",
        (team_id, worker_id),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "Gate 테스트방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    bad_gate_scores = {
        "A": {"score": 0.8, "passed": True},
        "C": {"score": 0.4, "passed": False},
        "B": {"not_measured": True},
    }
    crew_result = {
        "agents": [{"agent_id": manager_id, "role_name": "매니저", "output": "통합 결과물"}],
        "token_usage": {},
        "gate_scores": bad_gate_scores,
        "delegations": [{"from_agent_role": "매니저", "to_agent_role": "워커", "tool": "Delegate work to coworker"}],
    }
    with patch(
        "hair_platform.crew_runtime.run_crew", return_value=crew_result
    ) as mock_crew:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@Gate테스트팀 새 spec을 준수해 다시 작성해줘"},
        )
        assert resp.status_code == 201, resp.text
        task_descriptions = mock_crew.call_args[0][2]
        assert "그대로 복사하거나 재사용하지 마세요" in task_descriptions[0]
        assert "구현하지 않은 기능을 구현했다고 설명하지 마세요" in task_descriptions[0]
        # 사용자 보고(2026-10-19, "(제목 없음) 대화 분석") 재발 방지 —
        # "복사 금지"만으로는 "위에 이미 있다"며 빈손으로 응답하는 걸
        # 못 막았다. "이번 응답에 직접 완성된 산출물을 포함하라"는
        # 명시적 요구가 들어가는지 확인한다.
        assert "이번 응답 자체에 실제로" in task_descriptions[0]

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_msg = next(m for m in messages if m["author_type"] == "agent")
    assert agent_msg["gate_scores"] == bad_gate_scores
    assert agent_msg["delegation_count"] == 1


def test_strip_large_code_fences_replaces_long_blocks_only():
    """사용자 보고(2026-10-19, "(제목 없음) 대화 분석") — 실측 재현:
    기획서 작성자가 §5에 265줄짜리 완성된 게임 코드를 포함한 기획서를
    쓰면, `_build_transcript()`가 그 코드를 글자 수 제한 없이 그대로
    다음 실행(자율 개발 크루)에 넘겼고, 매니저는 그 코드를 복사도
    설명도 안 한 채 "위 코드는 이미 완성돼 있다"는 한 줄만 쓰고
    끝냈다(실제 산출물 0건). 긴 코드 블록(15줄 초과)만 생략 표시로
    바꾸고, 맥락상 유용할 짧은 예시(15줄 이하)는 그대로 둔다."""
    long_code = "\n".join(f"line{i}" for i in range(20))
    content = f"설명 문단입니다.\n\n```html\n{long_code}\n```\n\n끝."
    result = conversations._strip_large_code_fences(content)
    assert "line0" not in result
    assert "line19" not in result
    assert "[코드 20줄 생략" in result
    assert "```html" in result  # 펜스 마커/언어 태그는 유지
    assert "설명 문단입니다." in result
    assert "끝." in result

    short_code = "\n".join(f"line{i}" for i in range(5))
    short_content = f"```js\n{short_code}\n```"
    assert conversations._strip_large_code_fences(short_content) == short_content  # 15줄 이하는 그대로


def test_business_plan_writing_guide_forbids_full_source_code():
    """사용자 보고(2026-10-19, "(제목 없음) 대화 분석") — "기획서 작성자"
    (스킬: business-plan-writing-guide)가 사업계획서 역할을 벗어나
    265줄짜리 완성된 게임 코드를 §5에 통째로 넣은 사례가 실측됐다.
    이 스킬 설명(Agent 시스템 프롬프트에 그대로 들어감, `llm.
    system_prompt_for_role()` 참고)에 "전체 소스 코드를 작성하지
    않는다"는 경계가 명시됐는지 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    desc = conn.execute(
        "SELECT description FROM skill_pool WHERE name = 'business-plan-writing-guide'"
    ).fetchone()["description"]
    assert "전체 소스 코드를 작성하지 않는다" in desc
    assert "구현은 개발 역할의 몫" in desc


def test_build_transcript_omits_large_past_code_blocks(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "코드방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    long_code = "\n".join(f"console.log({i})" for i in range(30))
    conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) "
        "VALUES (?, 'agent', 0, ?)",
        (conv_id, f"# 기획서\n\n## 산출물\n\n```js\n{long_code}\n```"),
    )
    conn.commit()

    transcript = conversations._build_transcript(conn, conv_id)
    assert "console.log(0)" not in transcript
    assert "[코드 30줄 생략" in transcript
    assert "# 기획서" in transcript  # 코드 밖 본문은 그대로 유지


def test_gc_team_mention_translates_json_decode_error_for_user(client_and_conn):
    """사용자 보고(2026-10-15) 재발 방지 — CrewAI/litellm 내부에서 새어나온
    raw `json.JSONDecodeError`("Expecting value: line 1 column 1 (char 0)")가
    그대로 화면에 노출되지 않고, 원인 유형과 행동 지침이 담긴 메시지로
    바뀌어야 한다. 원본 문자열은 진단을 위해 `audit_log`에는 그대로
    남아야 한다."""
    import json as _json

    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_id = _make_agent(conn)
    team_id = _make_team_with_agent(conn, agent_id, "실패팀")
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    broken_json_error = _json.JSONDecodeError("Expecting value", "", 0)
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=broken_json_error
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@실패팀 테트리스 게임을 작성해줘"},
        )
        assert resp.status_code == 201, resp.text

    jobs = client.get(f"/api/conversations/{conv_id}/jobs").json()
    assert jobs[0]["status"] == "failed"
    assert "Expecting value" not in jobs[0]["error"]
    assert "다시 시도" in jobs[0]["error"]

    audit_rows = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'conversation_job_failed' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchall()
    assert audit_rows
    assert "Expecting value" in audit_rows[0]["after_value"]


# ---------- skill 사용 ----------


def test_gc_skill_param_grounds_response_regardless_of_auto_reply(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conn.execute(
        "INSERT INTO skill_pool (name, description) VALUES ('RAG', '지식베이스 검색')"
    )
    conn.commit()
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})

    with patch(
        "hair_platform.llm.call_llm", return_value="RAG 기반 답변"
    ) as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "질문있어요", "skill": "RAG"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"][0]["trigger_type"] == "skill"
        mock_call.assert_called_once()
        system_prompt = mock_call.call_args[0][2]
        assert "RAG" in system_prompt

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert messages[1]["used_skill"] == "RAG"


def test_gc_unknown_skill_rejected(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "질문", "skill": "존재안함"},
    )
    assert resp.status_code == 404, resp.text


# ---------- 참가자 초대(조직 전체 검색) ----------


def test_gc_mentionable_search_is_organization_wide(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _make_agent(conn, "검색용 Agent")
    cur = conn.execute("INSERT INTO teams (name) VALUES ('검색용 팀')")
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/mentionable", params={"q": "검색용"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["agents"]) == 1
    assert len(body["teams"]) == 1


def test_gc_any_participant_can_invite_more(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    outsider_id = _create_outsider(client)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "outsider1", "outsiderpass")
    resp = client.get(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text


def test_gc_non_participant_cannot_invite(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    outsider_id = _create_outsider(client, "outsider1")
    another_id = _create_outsider(client, "outsider2")

    _login(client, "outsider1", "outsiderpass")
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": another_id},
    )
    assert resp.status_code == 403, resp.text


# ---------- Task 종속 대화 ----------


def test_gc_linked_conversation_uses_task_skills_for_default_ai(client_and_conn):
    client, conn = client_and_conn
    task_id = _make_task_with_member1_participant(client, conn, "데이터 분석")
    _login(client, "member1", "memberpass")
    resp = client.post(
        "/api/conversations", json={"title": "", "linked_task_id": task_id}
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["id"]

    with patch("hair_platform.llm.call_llm", return_value="분석 결과입니다") as mock_call:
        client.post(f"/api/conversations/{conv_id}/messages", json={"content": "시작해줘"})
        system_prompt = mock_call.call_args[0][2]
        assert "데이터 분석" in system_prompt


# ---------- 대화 맥락(멀티턴) ----------


def test_gc_default_ai_sees_recent_conversation_history(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    with patch("hair_platform.llm.call_llm", return_value="첫 응답"):
        client.post(f"/api/conversations/{conv_id}/messages", json={"content": "첫 질문"})

    with patch("hair_platform.llm.call_llm", return_value="두번째 응답") as mock_call:
        client.post(f"/api/conversations/{conv_id}/messages", json={"content": "두번째 질문"})
        prompt = mock_call.call_args[0][3]
        assert "첫 질문" in prompt
        assert "첫 응답" in prompt
        assert "두번째 질문" in prompt
