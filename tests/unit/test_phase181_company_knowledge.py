"""회사 자료 질문은 지식 베이스에서(2026-11-28, 신입·일반 사용자 관점 Phase D) — 기본 AI가 스킬을 고르지 않으면 지식
베이스를 찾지 않던 문제. 회사 자료를 묻는 말이고 색인된 자료가 있으면 내부지식검색으로 답한다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversation_jobs, conversations


def _admin(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]


def _chunk(conn):
    conn.execute("INSERT INTO knowledge_chunks (text, embedding, embedding_provider, embedding_model) "
                 "VALUES ('국내 출장비 일비는 하루 3만 원', '[1,0]', 'openai', 'text-embedding-3-small')")
    conn.commit()


def test_auto_skill_only_for_company_questions_with_indexed_knowledge(client_and_conn):
    _, conn = client_and_conn
    q = "출장비 규정이 어떻게 돼?"
    assert conversation_jobs.auto_knowledge_skill(conn, q) is None  # 색인된 자료가 없으면 그대로
    _chunk(conn)
    assert conversation_jobs.auto_knowledge_skill(conn, q) == "내부지식검색"
    assert conversation_jobs.auto_knowledge_skill(conn, "주간보고 양식 있어?") == "내부지식검색"
    assert conversation_jobs.auto_knowledge_skill(conn, "엑셀 VLOOKUP 어떻게 써?") is None
    assert conversation_jobs.auto_knowledge_skill(conn, "") is None


def _run(conn, conv_id, admin):
    job_id = conversations.start_default_ai_job(conn, conv_id, admin)
    with patch("hair_platform.agent_runtime.retrieve_context", return_value="[근거1] 국내 출장비 일비는 하루 3만 원") as rc, \
            patch("hair_platform.llm.call_llm", return_value="하루 3만 원입니다 [근거1]") as plain, \
            patch("hair_platform.llm.call_llm_with_tools", return_value="하루 3만 원입니다 [근거1]") as tools:
        conversations.run_default_ai_job(conn, job_id, conv_id, admin)
    call = plain if plain.call_count else tools
    reply = conn.execute("SELECT used_skill FROM conversation_messages WHERE conversation_id = ? AND author_type = 'agent' "
                         "ORDER BY id DESC LIMIT 1", (conv_id,)).fetchone()
    return rc, call.call_args[0][2], reply["used_skill"]


def test_default_ai_answers_company_question_from_knowledge(client_and_conn):
    _, conn = client_and_conn
    admin = _admin(conn)
    _chunk(conn)
    conv_id = conversations.create_conversation(conn, admin, "신입 질문")["id"]
    conversations.create_message(conn, conv_id, admin, "출장비 규정이 어떻게 돼?")
    rc, system, used = _run(conn, conv_id, admin)
    assert rc.call_count == 1 and rc.call_args[0][1] == ["내부지식검색"]  # 지식 베이스를 찾았다
    assert "국내 출장비 일비" in system  # 찾은 근거가 지시문에 들어갔다
    assert used == "내부지식검색"  # 화면에 "스킬: 내부지식검색" 배지


def test_general_question_stays_plain(client_and_conn):
    _, conn = client_and_conn
    admin = _admin(conn)
    _chunk(conn)
    conv_id = conversations.create_conversation(conn, admin, "일반 질문")["id"]
    conversations.create_message(conn, conv_id, admin, "엑셀 VLOOKUP 어떻게 써?")
    rc, _system, used = _run(conn, conv_id, admin)
    assert rc.call_count == 0 and used is None


def test_getting_started_tracks_asking_and_delegating(client_and_conn):
    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/me/getting-started").json() == {"asked": False, "delegated": False}
    conv_id = conversations.create_conversation(conn, member, "첫 대화")["id"]
    conversations.create_message(conn, conv_id, member, "안녕하세요")
    assert client.get("/api/me/getting-started").json() == {"asked": True, "delegated": False}
    agent = conn.execute("SELECT id FROM agent_pool WHERE status = 'active' LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO conversation_participants (conversation_id, participant_type, participant_id, invited_by) "
                 "VALUES (?, 'agent', ?, ?)", (conv_id, agent, member))
    conn.commit()
    assert client.get("/api/me/getting-started").json() == {"asked": True, "delegated": True}  # Agent에게 맡겨 봄


def test_untitled_conversation_named_from_first_message(client_and_conn):
    _, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    cid = conversations.create_conversation(conn, member, "")["id"]
    conversations.create_message(conn, cid, member, "\n  @규정 요약가  출장비 규정이 어떻게 되는지 알려 주세요. 특히 해외 출장 일비가 궁금합니다\n둘째 줄")
    title = conn.execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()[0]
    assert title.startswith("출장비 규정이") and "@" not in title and title.endswith("…") and len(title) <= 41
    conversations.create_message(conn, cid, member, "다른 질문")
    assert conn.execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()[0] == title  # 첫 메시지로만
    named = conversations.create_conversation(conn, member, "내가 붙인 제목")["id"]
    conversations.create_message(conn, named, member, "안녕하세요")
    assert conn.execute("SELECT title FROM conversations WHERE id = ?", (named,)).fetchone()[0] == "내가 붙인 제목"
    only_mention = conversations.create_conversation(conn, member, "")["id"]
    conversations.create_message(conn, only_mention, member, "@규정 요약가")
    assert not conn.execute("SELECT title FROM conversations WHERE id = ?", (only_mention,)).fetchone()[0]  # 멘션뿐이면 그대로
