"""Phase 83 — 대화 컨텍스트 정책(사용자 요청, 2026-11-20, "이전 대화 10개 +
요약을 입력으로 사용하거나, 이전 대화 내용을 사용하지 않는 두가지 유형으로
구분 ... 각 대화마다 옵션을 두는게 좋을지 더 나은 방법이 있을지" → C안
"스킬별 기본값 + 메시지 단위 덮어쓰기" 승인).

`context_policy.py` + 응답 경로(기본 AI / 단일 Agent 멘션)가 정책대로
대화록·첨부 메모리·지시문·검색어를 조립하는지 검증한다.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import context_policy, conversation_memory, conversations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]


def _fake_embed(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [1.0, 0.0, 0.1])


def _conv_with_history(conn, admin_id, title="정책 테스트"):
    conv_id = conversations.create_conversation(conn, admin_id, title)["id"]
    conversations.create_message(conn, conv_id, admin_id, "예전 질문: 매출 분석해줘")
    conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) "
        "VALUES (?, 'agent', 0, '예전 답변: 매출은 10% 증가')",
        (conv_id,),
    )
    conn.commit()
    return conv_id


def _run_default_ai(conn, conv_id, admin_id, skill=None):
    job_id = conversations.start_default_ai_job(conn, conv_id, admin_id)
    # 도구가 연결된 스킬(예: 데이터분석)은 `call_llm_with_tools`를 탄다 —
    # 두 경로 모두 (provider, model, system, transcript, ...) 순서다.
    with patch("hair_platform.llm.call_llm", return_value="답변") as plain, patch(
        "hair_platform.llm.call_llm_with_tools", return_value="답변"
    ) as with_tools:
        conversations.run_default_ai_job(conn, job_id, conv_id, admin_id, skill=skill)
    assert plain.call_count + with_tools.call_count == 1
    mock_call = plain if plain.call_count else with_tools
    reply = conn.execute(
        "SELECT context_policy FROM conversation_messages WHERE conversation_id = ? "
        "AND author_type = 'agent' ORDER BY id DESC LIMIT 1",
        (conv_id,),
    ).fetchone()
    return mock_call.call_args[0][2], mock_call.call_args[0][3], json.loads(reply["context_policy"])


# ---------- 정책 판정 ----------


def test_seeded_skill_defaults(client_and_conn):
    _client, conn = client_and_conn
    rows = {
        r["name"]: (r["history_mode"], r["use_attachments"])
        for r in conn.execute("SELECT name, history_mode, use_attachments FROM skill_pool")
    }
    assert rows["내부지식검색"] == ("none", 0)
    assert rows["실시간웹검색"] == ("recent", 0)
    assert rows["일반웹검색"] == ("recent", 0)
    assert rows["데이터분석"] == ("full", 1)
    assert rows["제안서작성"] == ("full", 1)


def test_policy_merge_override_and_followup(client_and_conn):
    _client, conn = client_and_conn
    kb = context_policy.for_skills(conn, ["내부지식검색"])
    assert kb == {"history": "none", "attachments": False}
    # 여러 스킬이면 가장 포괄적인 값.
    assert context_policy.for_skills(conn, ["내부지식검색", "데이터분석"]) == {
        "history": "full",
        "attachments": True,
    }
    assert context_policy.for_skills(conn, []) == {"history": "full", "attachments": True}

    assert context_policy.finalize(kb, None, "HITL 개념 설명해줘")["history"] == "none"
    # 독립 요청에 흔한 표현은 후속 질문으로 오인하지 않는다.
    assert context_policy.finalize(kb, None, "HITL 개념 정리해줘")["history"] == "none"
    auto = context_policy.finalize(kb, None, "그거 더 자세히 설명해줘")
    assert (auto["history"], auto["source"]) == ("recent", "auto")
    forced = context_policy.finalize(kb, "full", "그거 더 자세히")
    assert (forced["history"], forced["source"]) == ("full", "override")


# ---------- 기본 AI 경로 ----------


def test_knowledge_search_uses_only_the_question(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "첨부.txt", "첨부 비밀 내용"
        )
        conversations.create_message(
            conn, conv_id, admin_id, "HITL 개념 설명해줘", skill="내부지식검색"
        )
        system, transcript, policy = _run_default_ai(conn, conv_id, admin_id, "내부지식검색")

    assert "HITL 개념 설명해줘" in transcript
    assert "예전 질문" not in transcript and "예전 답변" not in transcript
    assert "이전 대화는 의도적으로 제공하지 않았습니다" in transcript
    assert "완성된 산출물(코드 전체 등)" not in transcript  # 코드 생성용 지시 제외
    assert "첨부 비밀 내용" not in system  # 첨부 미사용
    assert policy == {
        "history": "none",
        "attachments": False,
        "source": "skill",
        "auto_upgraded": False,
    }


def test_data_analysis_keeps_full_history_and_attachments(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "매출.csv", "1월 매출 100"
        )
        conversations.create_message(
            conn, conv_id, admin_id, "이번엔 비용도 분석해줘", skill="데이터분석"
        )
        system, transcript, policy = _run_default_ai(conn, conv_id, admin_id, "데이터분석")

    assert "예전 질문" in transcript and "예전 답변" in transcript
    assert "1월 매출 100" in system
    assert policy["history"] == "full" and policy["attachments"] is True


def test_message_override_none_without_skill(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    conversations.create_message(
        conn, conv_id, admin_id, "새 주제로 질문할게", context_override="none"
    )
    _system, transcript, policy = _run_default_ai(conn, conv_id, admin_id)
    assert "예전 질문" not in transcript
    assert (policy["history"], policy["source"]) == ("none", "override")


def test_followup_upgrades_to_recent_and_expands_search_query(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    conversations.create_message(conn, conv_id, admin_id, "그거 더 자세히 설명해줘", skill="내부지식검색")
    with patch(
        "hair_platform.agent_runtime.retrieve_context", return_value=None
    ) as mock_retrieve:
        _system, transcript, policy = _run_default_ai(conn, conv_id, admin_id, "내부지식검색")

    assert (policy["history"], policy["source"]) == ("recent", "auto")
    assert "예전 답변" in transcript  # 직전 문답이 들어간다
    query = mock_retrieve.call_args[0][2]
    assert "매출 분석해줘" in query and "그거 더 자세히" in query


def test_invalid_override_rejected_by_api(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "API"}).json()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "q", "context_override": "everything"},
    )
    assert resp.status_code == 422
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "q", "context_override": "none"},
    )
    assert resp.status_code in (200, 201), resp.text
    stored = conn.execute(
        "SELECT context_override FROM conversation_messages WHERE conversation_id = ? "
        "AND author_type = 'human'",
        (conv_id,),
    ).fetchone()["context_override"]
    assert stored == "none"


# ---------- 단일 Agent 멘션 경로 ----------


def _agent(conn, skills):
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        ("정책 Agent", json.dumps(skills, ensure_ascii=False)),
    ).lastrowid
    conn.commit()
    return agent_id


def _run_agent_mention(conn, conv_id, agent_id, admin_id):
    conn.execute(
        "INSERT OR IGNORE INTO conversation_participants (conversation_id, "
        "participant_type, participant_id, invited_by) VALUES (?, 'agent', ?, ?)",
        (conv_id, agent_id, admin_id),
    )
    conn.commit()
    job_id = conversations.start_agent_mention_job(conn, conv_id, agent_id, admin_id)
    with patch("hair_platform.agent_runtime.execute_agent", return_value="답변") as mock_exec:
        conversations.run_agent_mention_job(conn, job_id, conv_id, agent_id, admin_id)
    mock_exec.assert_called_once()
    return mock_exec.call_args


def test_agent_mention_follows_agent_skills_and_passes_clean_search_query(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    agent_id = _agent(conn, ["내부지식검색"])
    conversations.create_message(conn, conv_id, admin_id, "HITL 개념 설명해줘")
    call = _run_agent_mention(conn, conv_id, agent_id, admin_id)

    prompt = call[0][3]
    assert "예전 질문" not in prompt
    # 검색어는 프롬프트 꼬리(지시문)가 아니라 질문 자체(2026-11-20 P0-1 수정).
    assert call.kwargs["retrieval_query"] == "HITL 개념 설명해줘"


def test_agent_override_beats_skill_default(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = _conv_with_history(conn, admin_id)
    agent_id = _agent(conn, ["내부지식검색"])

    _login(client, "member1", "memberpass")
    assert (
        client.patch(
            f"/api/agent-pool/{agent_id}/context-policy", json={"history_mode": "full"}
        ).status_code
        == 403
    )
    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/agent-pool/{agent_id}/context-policy",
        json={"history_mode": "full", "use_attachments": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["effective_context_policy"] == {"history": "full", "attachments": True}

    conversations.create_message(conn, conv_id, admin_id, "HITL 개념 설명해줘")
    prompt = _run_agent_mention(conn, conv_id, agent_id, admin_id)[0][3]
    assert "예전 질문" in prompt


def test_skill_policy_editable_and_exposed(client_and_conn):
    client, conn = client_and_conn
    skill_id = conn.execute("SELECT id FROM skill_pool WHERE name = '데이터분석'").fetchone()["id"]
    _login(client, "admin1", "adminpass")
    resp = client.patch(
        f"/api/skill-pool/{skill_id}", json={"history_mode": "recent", "use_attachments": False}
    )
    assert resp.status_code == 200, resp.text
    _login(client, "member1", "memberpass")
    policies = client.get("/api/skill-pool/context-policies").json()
    assert policies["데이터분석"] == {"history": "recent", "attachments": False}
    assert policies["내부지식검색"] == {"history": "none", "attachments": False}
