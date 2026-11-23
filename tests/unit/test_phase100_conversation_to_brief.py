"""대화 → 업무 요청 전환(2026-11-23, "P1 진행해줘" — P1-4)."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations


def _login(client, username, password):
    assert client.post("/login", json={"username": username, "password": password}).status_code == 200


def _conversation_with_messages(client, conn):
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "3분기 실적 정리"}).json()["id"]
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    # 메시지는 AI 자동응답을 부르지 않도록 직접 넣는다.
    conn.executemany(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, ?, ?, ?)",
        [
            (conv_id, "human", member, "3분기 실적 보고서가 필요해요. 매출 55억(목표 52억), 영업이익 6.2억(목표 7억)."),
            (conv_id, "agent", 1, "보고서는 핵심 실적표 → 원인 분석 → 차기 계획 순서를 권합니다."),
            (conv_id, "human", member, "다음 주 월요일 임원회의 전까지 PDF 2쪽 이내로요."),
        ],
    )
    conn.commit()
    return conv_id


def test_rule_draft_collects_human_requests_and_last_answer(client_and_conn):
    client, conn = client_and_conn
    conv_id = _conversation_with_messages(client, conn)
    resp = client.post(f"/api/conversations/{conv_id}/brief-draft", json={})
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["source"] == "rule"
    text = draft["raw_text"]
    assert text.startswith("3분기 실적 정리")
    assert "매출 55억(목표 52억)" in text and "PDF 2쪽 이내" in text
    assert "핵심 실적표 → 원인 분석" in text
    assert f"대화 #{conv_id}" in text


def test_llm_draft_uses_conversation_model(client_and_conn):
    client, conn = client_and_conn
    conv_id = _conversation_with_messages(client, conn)
    with (
        patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")),
        patch("hair_platform.llm.call_llm", return_value="3분기 실적 보고\n## 목표\n임원 보고") as call,
    ):
        draft = client.post(f"/api/conversations/{conv_id}/brief-draft", json={"use_llm": True}).json()
    assert draft["source"] == "llm" and draft["raw_text"].startswith("3분기 실적 보고")
    prompt = call.call_args[0][3]
    assert "[사람] 3분기 실적 보고서가 필요해요" in prompt and "[AI]" in prompt


def test_submit_links_brief_and_notes_conversation(client_and_conn):
    client, conn = client_and_conn
    conv_id = _conversation_with_messages(client, conn)
    resp = client.post("/api/briefs", json={"raw_text": "3분기 실적 보고서 작성", "source_conversation_id": conv_id})
    assert resp.status_code == 201, resp.text
    brief_id = resp.json()["id"]
    row = conn.execute("SELECT source_conversation_id FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    assert row[0] == conv_id
    notes = [m for m in conversations.list_messages(conn, conv_id) if m["content"].startswith(conversations.TRANSFER_NOTE_PREFIX)]
    assert len(notes) == 1 and f"#{brief_id}" in notes[0]["content"]
    # 전환 메시지는 다음 초안에 다시 섞이지 않는다.
    draft = client.post(f"/api/conversations/{conv_id}/brief-draft", json={}).json()
    assert conversations.TRANSFER_NOTE_PREFIX not in draft["raw_text"]
    # AI 자동응답 작업을 만들지 않는다.
    assert conn.execute("SELECT COUNT(*) FROM conversation_jobs WHERE conversation_id = ?", (conv_id,)).fetchone()[0] == 0


def test_only_participants_can_transfer(client_and_conn):
    client, conn = client_and_conn
    conv_id = _conversation_with_messages(client, conn)
    _login(client, "manager1", "managerpass")
    assert client.post(f"/api/conversations/{conv_id}/brief-draft", json={}).status_code == 403
    resp = client.post("/api/briefs", json={"raw_text": "x", "source_conversation_id": conv_id})
    assert resp.status_code == 403
