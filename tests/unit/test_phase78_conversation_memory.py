"""Phase 78 — 대화 스코프 메모리(RAG) 개선축 C(사용자 요청, 2026-11-18,
"대화에서 업무처리시 Tool, skill 활용, 메시지 길이 관리에 제약사항이
많아... 메모리를 활용하는 방안 등을 고려해 엔터프라이즈 환경 적용이
가능한 수준의 확실한 개선 방안을 도출해줘").

실측 근거: 대화 #14/#20에서 엑셀 첨부파일 1개(19,321자)만으로 로컬
모델(32,768토큰 한도)이 15분 돌고도 빈 응답을 낸 사고(2026-11-18) —
개선축 B(`token_budget.py`)로 "안 넘치게 미리 자르기"는 고쳤지만, 잘려
나간 내용은 그냥 버려졌다. 이 파일은 그 내용을 대화 전용 임시 색인에
저장해뒀다가 관련 질문에 다시 꺼내 쓰는 메커니즘을 검증한다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import conversation_memory, conversations, data_admin, documents


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[
        "id"
    ]


def _fake_embed_constant(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [0.1, 0.2, 0.3])


# 키워드 기반 가짜 임베딩 — 검색 순위(관련성) 테스트 전용. 쿼리·청크
# 텍스트에 특정 토큰이 있는지로 축을 구성해, 진짜 임베딩 없이도
# "관련 있는 청크가 더 높은 코사인 유사도를 받는지"를 실측할 수 있다.
def _fake_embed_keyword(text, provider=None, model=None):
    vec = [
        1.0 if "사과" in text else 0.0,
        1.0 if "바나나" in text else 0.0,
        0.1,
    ]
    return (provider or "openai", model or "text-embedding-3-small", vec)


# ---------- conversation_memory.index_attachment() / search() ----------


def test_index_attachment_creates_chunks_scoped_to_conversation(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "메모리 테스트")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        result = conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "big.txt", "문단 하나.\n\n" * 500
        )

    assert result["chunk_count"] >= 1
    rows = conn.execute(
        "SELECT * FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conv_id,),
    ).fetchall()
    assert len(rows) == result["chunk_count"]


def test_search_only_returns_chunks_from_same_conversation(client_and_conn):
    """격리 보장 — 다른 대화에 색인된 청크는 절대 섞여 들어오지 않는다
    (2026-10-26 결정 "대화에 주입되는 문서는 저장되지 않고 휘발되는
    내용물"을 대화 간 격리까지 포함해 지킨다)."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_a = conversations.create_conversation(conn, admin_id, "A")["id"]
    conv_b = conversations.create_conversation(conn, admin_id, "B")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_keyword
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_a, None, "a.txt", "사과에 대한 내용입니다."
        )
        conversation_memory.index_attachment(
            conn, admin_id, conv_b, None, "b.txt", "바나나에 대한 내용입니다."
        )
        hits_a = conversation_memory.search(conn, conv_a, "사과 질문")

    assert len(hits_a) == 1
    assert hits_a[0]["filename"] == "a.txt"


def test_retrieve_context_for_query_skips_embedding_call_without_chunks(
    client_and_conn,
):
    """비용 회피 — 첨부파일을 안 올린 대화는 매 질문마다 임베딩 API를
    부르지 않는다(색인된 청크가 아예 없으면 즉시 `None`)."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "빈 대화")["id"]

    with patch("hair_platform.vectorstore.embed_text") as mock_embed:
        result = conversation_memory.retrieve_context_for_query(
            conn, conv_id, "아무 질문"
        )
    assert result is None
    mock_embed.assert_not_called()


def test_retrieve_context_for_query_returns_relevant_chunk(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "관련성 테스트")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_keyword
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "fruit.txt", "사과는 빨갛다."
        )
        result = conversation_memory.retrieve_context_for_query(
            conn, conv_id, "사과 색깔이 뭐야"
        )
    assert result is not None
    assert "사과는 빨갛다" in result
    assert "fruit.txt" in result


def test_retrieve_context_for_query_returns_none_when_embedding_fails(
    client_and_conn,
):
    """사용자 요청과 같은 원칙(검색 실패가 전체 응답을 막으면 안 됨,
    `agent_runtime.retrieve_context()`의 vectordb 조회와 동일) —
    임베딩 API가 실패해도 예외가 올라오지 않고 조용히 `None`."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "실패 테스트")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "a.txt", "내용"
        )
    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=RuntimeError("API 장애")
    ):
        result = conversation_memory.retrieve_context_for_query(conn, conv_id, "질문")
    assert result is None


def test_index_attachment_includes_numeric_summary_when_raw_bytes_given(
    client_and_conn,
):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "수치 테스트")["id"]

    csv_bytes = ("항목,금액\n김밥,2500\n라면,3500\n우유,2000\n" * 1).encode()
    text = documents.extract_text("spend.csv", csv_bytes)

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        result = conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "spend.csv", text, raw_bytes=csv_bytes
        )
    assert result["has_numeric_summary"] is True
    rows = conn.execute(
        "SELECT text FROM conversation_knowledge_chunks WHERE conversation_id = ? "
        "ORDER BY chunk_index",
        (conv_id,),
    ).fetchall()
    assert "자동 계산된 열별 요약" in rows[0]["text"]
    assert "합계=8000" in rows[0]["text"]


# ---------- create_attachment_message() — 문턱 기준 분기 ----------


def test_create_attachment_message_small_text_unchanged_behavior(client_and_conn):
    """회귀 가드 — 문턱(3000자) 아래면 지금까지와 100% 동일하게 전문이
    그대로 메시지에 들어가고, 메모리 색인은 전혀 안 생긴다."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "짧은 첨부")["id"]

    message_id = conversations.create_attachment_message(
        conn, conv_id, admin_id, "short.txt", "짧은 내용입니다."
    )
    message = conn.execute(
        "SELECT content FROM conversation_messages WHERE id = ?", (message_id,)
    ).fetchone()
    assert "짧은 내용입니다." in message["content"]
    assert "이하 생략" not in message["content"]
    chunk_count = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conv_id,),
    ).fetchone()["c"]
    assert chunk_count == 0


def test_create_attachment_message_large_text_indexes_and_shows_preview(
    client_and_conn,
):
    """핵심 회귀 테스트 — 2026-11-18 사고(19KB 첨부파일 1개로 컨텍스트
    한도 초과)의 직접 재현+수정 확인: 문턱을 넘는 첨부는 전문 대신
    미리보기만 메시지에 남고, 전체는 대화 전용 메모리에 색인된다."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "큰 첨부")["id"]

    huge_text = "행 데이터 " * 2000  # 3000자 문턱을 확실히 넘김
    assert len(huge_text) > conversations._LARGE_ATTACHMENT_CHAR_THRESHOLD

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        message_id = conversations.create_attachment_message(
            conn, conv_id, admin_id, "huge.xlsx", huge_text
        )

    message = conn.execute(
        "SELECT content FROM conversation_messages WHERE id = ?", (message_id,)
    ).fetchone()
    assert "이하 생략" in message["content"]
    assert len(message["content"]) < len(huge_text)
    chunk_count = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conv_id,),
    ).fetchone()["c"]
    assert chunk_count >= 1


# ---------- 삭제/초기화 연동 ----------


def test_conversation_deletion_cascades_to_knowledge_chunks(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "삭제 테스트")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "a.txt", "내용"
        )
    conversations.delete_conversation(conn, conv_id, admin_id)

    remaining = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conv_id,),
    ).fetchone()["c"]
    assert remaining == 0


def test_factory_reset_wipes_conversation_knowledge_chunks(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "초기화 테스트")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_constant
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "a.txt", "내용"
        )
    data_admin.reset_to_factory_state(conn)

    remaining = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_knowledge_chunks"
    ).fetchone()["c"]
    assert remaining == 0


# ---------- 응답 경로 통합 — 기본 AI/단일 Agent 멘션/Team 멘션 ----------


def test_default_ai_job_injects_memory_context_into_system_prompt(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "기본 AI 메모리")["id"]

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_keyword
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "fruit.txt", "사과는 빨갛다."
        )
        conversations.create_message(conn, conv_id, admin_id, "사과 색깔이 뭐야")
        job_id = conversations.start_default_ai_job(conn, conv_id, admin_id)
        with patch(
            "hair_platform.llm.call_llm", return_value="빨간색입니다"
        ) as mock_call:
            conversations.run_default_ai_job(conn, job_id, conv_id, admin_id)

    mock_call.assert_called_once()
    system_arg = mock_call.call_args[0][2]
    assert "사과는 빨갛다" in system_arg


def test_agent_mention_job_injects_memory_context_into_prompt(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('테스트 Agent', '[]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()
    conv_id = conversations.create_conversation(conn, admin_id, "Agent 메모리")["id"]
    conn.execute(
        "INSERT INTO conversation_participants (conversation_id, participant_type, "
        "participant_id, invited_by) VALUES (?, 'agent', ?, ?)",
        (conv_id, agent_id, admin_id),
    )
    conn.commit()

    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_keyword
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "fruit.txt", "사과는 빨갛다."
        )
        conversations.create_message(conn, conv_id, admin_id, "사과 색깔이 뭐야")
        job_id = conversations.start_agent_mention_job(
            conn, conv_id, agent_id, admin_id
        )
        with patch(
            "hair_platform.agent_runtime.execute_agent", return_value="답변"
        ) as mock_exec:
            conversations.run_agent_mention_job(
                conn, job_id, conv_id, agent_id, admin_id
            )

    mock_exec.assert_called_once()
    prompt_arg = mock_exec.call_args[0][3]
    assert "사과는 빨갛다" in prompt_arg


def test_team_mention_job_injects_memory_context_into_task_description(
    client_and_conn,
):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('테스트 Agent', '[]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()
    cur = conn.execute("INSERT INTO teams (name) VALUES ('메모리 팀')")
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'agent', ?)",
        (team_id, agent_id),
    )
    conn.commit()
    conv_id = conversations.create_conversation(conn, admin_id, "Team 메모리")["id"]

    crew_result = {
        "agents": [{"agent_id": agent_id, "role_name": "테스트 Agent", "output": "답변"}],
        "token_usage": {},
        "delegation_count": 0,
    }
    with patch(
        "hair_platform.vectorstore.embed_text", side_effect=_fake_embed_keyword
    ):
        conversation_memory.index_attachment(
            conn, admin_id, conv_id, None, "fruit.txt", "사과는 빨갛다."
        )
        conversations.create_message(conn, conv_id, admin_id, "사과 색깔이 뭐야")
        job_id = conversations.start_team_mention_job(conn, conv_id, team_id, admin_id)
        with patch(
            "hair_platform.crew_runtime.run_crew", return_value=crew_result
        ) as mock_crew:
            conversations.run_team_mention_job(conn, job_id, conv_id, team_id, admin_id)

    mock_crew.assert_called_once()
    task_descriptions_arg = mock_crew.call_args[0][2]
    assert all("사과는 빨갛다" in desc for desc in task_descriptions_arg)


# ---------- 2026-11-19, 사용자 보고 "자료 파일의 일부만 전달되며,
# 자료파일의 중요 정보가 모두 손실" ----------


def _insert_chunk(conn, conv_id, filename, chunk_index, text, embedding):
    conn.execute(
        "INSERT INTO conversation_knowledge_chunks (conversation_id, "
        "source_message_id, filename, chunk_index, text, embedding, "
        "embedding_provider, embedding_model) VALUES (?, NULL, ?, ?, ?, ?, "
        "'openai', 'text-embedding-3-small')",
        (conv_id, filename, chunk_index, text, json.dumps(embedding)),
    )


def test_retrieve_context_for_query_includes_more_than_five_chunks_when_budget_allows(
    client_and_conn,
):
    """회귀 가드 — 예전엔 `top_k=5`로 청크 개수와 무관하게 무조건 5개만
    포함했다. 토큰 예산에 넉넉히 들어가는 작은 파일(이 테스트는 10개
    청크, 전부 짧은 텍스트)이라면 5개 넘게도 전부 포함돼야 한다."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "청크 10개 테스트")["id"]

    for i in range(10):
        _insert_chunk(
            conn, conv_id, "거래내역.csv", i, f"거래 {i}: 금액 {i}000원", [1.0, 0.0]
        )
    conn.commit()

    with patch("hair_platform.vectorstore.embed_text", return_value=(
        "openai", "text-embedding-3-small", [1.0, 0.0]
    )):
        result = conversation_memory.retrieve_context_for_query(
            conn, conv_id, "전체 거래 분석해줘", provider="anthropic", model="claude-sonnet-5"
        )
    assert result is not None
    for i in range(10):
        assert f"거래 {i}: 금액 {i}000원" in result


def test_retrieve_context_for_query_stays_within_tight_budget(client_and_conn):
    """예산이 빠듯하면(작은 provider/model 폴백) 전부가 아니라 예산 안
    에서 상위 몇 개만 포함돼야 한다 — 그래도 최소 1개는 반드시 포함
    (완전히 빈 손으로 돌아가지 않게)."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "예산 빠듯 테스트")["id"]

    # 각 청크를 길게 만들어 예산을 빨리 소진시킨다.
    long_text = "거래 내역 " * 2000
    for i in range(10):
        _insert_chunk(conn, conv_id, "큰파일.csv", i, f"[{i}] {long_text}", [1.0, 0.0])
    conn.commit()

    with patch("hair_platform.vectorstore.embed_text", return_value=(
        "openai", "text-embedding-3-small", [1.0, 0.0]
    )):
        result = conversation_memory.retrieve_context_for_query(
            conn, conv_id, "분석해줘", provider="ollama", model="unregistered-tiny-model"
        )
    assert result is not None
    included_count = sum(1 for i in range(10) if f"[{i}] " in result)
    assert 0 < included_count < 10
