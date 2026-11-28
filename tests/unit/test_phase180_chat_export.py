"""대화 답변 Word·Excel로 받기(2026-11-28, 신입·일반 사용자 관점 Phase C) — 참가자만, Word는 본문, Excel은 표."""

from __future__ import annotations

import io

import pytest

from hair_platform import conversations, exports

_needs_office = pytest.mark.skipif(not (exports.available() and exports.xlsx_available()), reason="python-docx·openpyxl 없음")

ANSWER = "## 분기 매출 요약\n\n지난 분기 매출은 **12% 증가**했습니다.\n\n| 지역 | 매출 |\n|---|---|\n| 서울 | 1200 |\n| 부산 | 800 |\n"


def _setup(conn):
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    conv = conversations.create_conversation(conn, member, "매출 요약")
    mid = conn.execute("INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, 'agent', 0, ?)",
                       (conv["id"], ANSWER)).lastrowid
    conn.commit()
    return conv["id"], mid


@_needs_office
def test_member_downloads_answer_as_word_and_table_as_excel(client_and_conn):
    from docx import Document
    from openpyxl import load_workbook

    client, conn = client_and_conn
    cid, mid = _setup(conn)
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    r = client.get(f"/api/conversations/{cid}/messages/{mid}/export.docx")
    assert r.status_code == 200 and f"chat-{cid}-{mid}.docx" in r.headers["content-disposition"]
    text = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    assert "분기 매출 요약" in text and "12% 증가" in text and "**" not in text
    r = client.get(f"/api/conversations/{cid}/messages/{mid}/export.xlsx")
    assert r.status_code == 200
    values = [c for row in load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True) for c in row if c is not None]
    assert "서울" in values and 1200 in values


@_needs_office
def test_only_participants_and_known_formats(client_and_conn):
    client, conn = client_and_conn
    cid, mid = _setup(conn)
    client.post("/login", json={"username": "manager1", "password": "managerpass"})  # 참가자 아님
    assert client.get(f"/api/conversations/{cid}/messages/{mid}/export.docx").status_code == 403
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get(f"/api/conversations/{cid}/messages/{mid}/export.pdf").status_code == 404
    assert client.get(f"/api/conversations/{cid}/messages/999999/export.docx").status_code == 404
    other = conversations.create_conversation(conn, conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0], "다른")
    assert client.get(f"/api/conversations/{other['id']}/messages/{mid}/export.docx").status_code == 404  # 다른 대화의 메시지


def test_messages_etag_returns_304_until_something_changes(client_and_conn):
    """대화 새로고침(2026-11-28) — 바뀐 게 없으면 304(본문 없음). 메시지 수정처럼 내용이 바뀌면 새 ETag."""
    client, conn = client_and_conn
    cid, mid = _setup(conn)
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    r = client.get(f"/api/conversations/{cid}/messages")
    etag = r.headers["etag"]
    assert r.status_code == 200 and len(r.json()) == 1 and r.headers["cache-control"] == "private, no-cache"
    r2 = client.get(f"/api/conversations/{cid}/messages", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b""
    conn.execute("UPDATE conversation_messages SET content = content || '.' WHERE id = ?", (mid,))  # 같은 길이 변화도 잡혀야
    conn.commit()
    r3 = client.get(f"/api/conversations/{cid}/messages", headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["etag"] != etag
