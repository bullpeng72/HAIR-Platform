"""외부 자료(마크다운/PDF/HTML) 업로드 → VectorDB 색인(2026-10-26, 사용자
요청 "대화창 및 에이전트로 구성된 업무에서 외부 자료(마크다운, pdf,
html) 등을 입력받을 수 있도록 개선해줘 — 대화에 바로 로컬에 있는 파일을
업로드, RAG 및 vector DB에 파일 업로드 처리").

실제 임베딩 API는 `patch("hair_platform.vectorstore.embed_text", ...)`로
대체한다(다른 Phase 테스트 파일들과 같은 관례) — provider/model 해석과
청크 분할·저장 로직만 검증한다.

**2차 결정(2026-10-26, "A안 + B안")** — `documents.ingest_document()`
(이 파일 위쪽 테스트들)는 이제 "지식 베이스"(admin/manager 전용 공통·
영구 지식) 하나만 다룬다. 대화/업무 1회성 첨부는 저장을 안 하는 별도
경로(`conversations.create_attachment_message()`,
`deliverables.start_agent_execution(attachment_text=...)`)를 쓴다 —
아래쪽 테스트들이 검증한다."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import conversations, deliverables, documents, vectorstore


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[
        "id"
    ]


def _fake_embed(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [0.1, 0.2, 0.3])


# ---------- extract_text() ----------


def test_extract_text_markdown_and_txt_are_decoded_as_is():
    assert (
        documents.extract_text("note.md", "# 제목\n\n본문".encode()) == "# 제목\n\n본문"
    )
    assert documents.extract_text("note.txt", "그냥 텍스트".encode()) == "그냥 텍스트"


def test_extract_text_html_strips_tags_and_script():
    html = (
        b"<html><head><style>body{color:red}</style></head>"
        b"<body><script>alert(1)</script><h1>\xec\xa0\x9c\xeb\xaa\xa9</h1>"
        b"<p>\xeb\xb3\xb8\xeb\xac\xb8</p></body></html>"
    )
    text = documents.extract_text("page.html", html)
    assert "제목" in text
    assert "본문" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_extract_text_pdf_reads_real_pdf_bytes():
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    # 빈 페이지는 텍스트가 없어 `extract_text()`가 `empty_document`로
    # 거부해야 한다(라이브러리 연결 자체는 확인됐으므로 충분).
    with pytest.raises(ValueError, match="empty_document"):
        documents.extract_text("blank.pdf", buf.getvalue())


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="unsupported_extension"):
        documents.extract_text("archive.zip", b"PK\x03\x04")


def test_extract_text_rejects_empty_result():
    with pytest.raises(ValueError, match="empty_document"):
        documents.extract_text("empty.txt", b"   \n\n  ")


# ---------- chunk_text() ----------


def test_chunk_text_short_text_is_single_chunk():
    assert documents.chunk_text("짧은 텍스트", max_chars=100) == ["짧은 텍스트"]


def test_chunk_text_packs_paragraphs_within_limit():
    paras = ["a" * 30, "b" * 30, "c" * 30]
    text = "\n\n".join(paras)
    chunks = documents.chunk_text(text, max_chars=50)
    assert len(chunks) == 3
    assert all(len(c) <= 50 for c in chunks)


def test_chunk_text_splits_a_single_huge_paragraph():
    """오버랩이 있으므로(사용자 요청 2026-11-03, "RAG 검색 품질 향상")
    인접 청크는 끝/시작이 겹친다 — 단순 `"".join()` 재조립이 아니라
    겹치는 부분을 확인하고 그만큼을 걷어내야 원문과 일치한다."""
    text = "x" * 1000
    chunks = documents.chunk_text(text, max_chars=300)
    assert len(chunks) == 4
    assert all(len(c) <= 300 for c in chunks)
    overlap = documents._CHUNK_OVERLAP_CHARS
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev[-overlap:] == nxt[:overlap]
    rebuilt = chunks[0] + "".join(c[overlap:] for c in chunks[1:])
    assert rebuilt == text


# ---------- ingest_document() ----------


def test_ingest_document_creates_document_row_and_chunks(client_and_conn):
    _, conn = client_and_conn
    admin_id = _admin_id(conn)
    text = ("문단 하나.\n\n" * 5).strip()

    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        result = documents.ingest_document(
            conn, admin_id, "note.md", text.encode("utf-8")
        )

    doc_row = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc_row["filename"] == "note.md"
    assert doc_row["char_count"] == len(text)
    assert doc_row["chunk_count"] == result["chunk_count"]

    chunk_rows = conn.execute(
        "SELECT * FROM knowledge_chunks WHERE source_document_id = ?",
        (result["document_id"],),
    ).fetchall()
    assert len(chunk_rows) == result["chunk_count"]
    assert result["chunk_count"] >= 1


# ---------- POST /api/documents("지식 베이스" 전용 — admin/manager만) ----------


def test_upload_document_requires_admin_or_manager(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = client.post(
            "/api/documents", files={"file": ("note.txt", b"hello", "text/plain")}
        )
    assert resp.status_code == 403, resp.text

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = client.post(
            "/api/documents", files={"file": ("note.txt", b"hello", "text/plain")}
        )
    assert resp.status_code == 201, resp.text


def test_upload_document_rejects_unsupported_extension(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/documents",
        files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
    )
    assert resp.status_code == 400, resp.text


# ---------- GET/DELETE /api/documents ----------


def test_list_and_delete_document(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = _admin_id(conn)
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        result = documents.ingest_document(conn, admin_id, "note.md", b"hello world")

    listed = client.get("/api/documents").json()
    assert any(d["id"] == result["document_id"] for d in listed)

    resp = client.delete(f"/api/documents/{result['document_id']}")
    assert resp.status_code == 200, resp.text

    assert (
        conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (result["document_id"],)
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM knowledge_chunks WHERE source_document_id = ?",
            (result["document_id"],),
        ).fetchone()
        is None
    )


def test_delete_document_requires_admin_or_manager(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = _admin_id(conn)
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        result = documents.ingest_document(conn, admin_id, "note.md", b"hello world")

    _login(client, "member1", "memberpass")
    resp = client.delete(f"/api/documents/{result['document_id']}")
    assert resp.status_code == 403, resp.text


# ---------- 회귀: 업로드된 자료를 VectorDB 검색이 실제로 찾아내는지 ----------


def test_uploaded_chunk_is_found_by_vectorstore_search(client_and_conn):
    _, conn = client_and_conn
    admin_id = _admin_id(conn)

    def fake_embed(text, provider=None, model=None):
        # 쿼리와 색인된 청크가 같은 벡터를 받도록 고정 — 코사인 유사도가
        # 항상 1.0이 되어 검색에 반드시 걸린다.
        return (
            provider or "openai",
            model or "text-embedding-3-small",
            [1.0, 0.0, 0.0],
        )

    with patch("hair_platform.vectorstore.embed_text", side_effect=fake_embed):
        documents.ingest_document(conn, admin_id, "note.md", b"HAIR-Platform RAG note")
        hits = vectorstore.search(conn, "RAG", top_k=3)

    assert any("HAIR-Platform RAG note" in h["text"] for h in hits)


# ---------- 대화 첨부 — 저장 없이 메시지로 직접 주입("A안", 2026-10-26 2차) ----------


def test_create_attachment_message_inserts_message_and_touches_no_storage(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = _admin_id(conn)
    conv_id = client.post("/api/conversations", json={"title": "테스트"}).json()["id"]

    message_id = conversations.create_attachment_message(
        conn, conv_id, admin_id, "note.md", "첨부된 본문 전체"
    )

    row = conn.execute(
        "SELECT content FROM conversation_messages WHERE id = ?", (message_id,)
    ).fetchone()
    assert "note.md" in row["content"]
    assert "첨부된 본문 전체" in row["content"]

    # VectorDB/문서 저장소는 전혀 거치지 않는다(휘발성 설계의 핵심 검증).
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM knowledge_chunks").fetchone()["c"] == 0


def test_create_attachment_message_rejects_non_participant(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "테스트"}).json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]

    with pytest.raises(PermissionError):
        conversations.create_attachment_message(
            conn, conv_id, member_id, "note.md", "본문"
        )


def test_upload_conversation_attachment_endpoint(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "테스트"}).json()["id"]

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/conversations/{conv_id}/attachments",
        files={"file": ("note.md", "회의록 전체 내용".encode(), "text/markdown")},
    )
    assert resp.status_code == 403, resp.text  # 아직 참가자 아님

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/conversations/{conv_id}/attachments",
        files={"file": ("note.md", "회의록 전체 내용".encode(), "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "note.md"
    assert body["char_count"] == len("회의록 전체 내용")

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    attached = [m for m in messages if m["id"] == body["conversation_message_id"]]
    assert len(attached) == 1
    assert "회의록 전체 내용" in attached[0]["content"]

    # 임베딩 API를 patch하지 않았는데도(=호출 자체가 없었다는 뜻) 성공했다는
    # 것 자체가 VectorDB를 거치지 않았다는 증거다.
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0


# ---------- 업무 실행 첨부 — 저장 없이 그 1회 프롬프트에만 주입 ----------


def _make_confirmed_agent_task(conn, admin_id, role_name="백엔드 개발자"):
    brief_cur = conn.execute(
        "INSERT INTO briefs (raw_text, submitted_by) VALUES ('테스트', ?)", (admin_id,)
    )
    task_cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, status) VALUES (?, ?, 'queued')",
        (brief_cur.lastrowid, role_name),
    )
    task_id = task_cur.lastrowid
    agent_cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    agent_id = agent_cur.lastrowid
    conn.execute(
        "INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
        "VALUES (?, 'agent', ?, ?, 'confirmed')",
        (task_id, agent_id, admin_id),
    )
    conn.commit()
    return task_id, agent_id


def test_start_agent_execution_includes_attachment_text_in_prompt_but_not_in_db(
    client_and_conn,
):
    _, conn = client_and_conn
    admin_id = _admin_id(conn)
    task_id, _agent_id = _make_confirmed_agent_task(conn, admin_id)

    job = deliverables.start_agent_execution(
        conn,
        task_id,
        admin_id,
        allow_supervisor_override=True,
        attachment_text="이번 실행에만 쓸 참고자료 원문",
    )

    assert "이번 실행에만 쓸 참고자료 원문" in job["prompt"]

    row = conn.execute(
        "SELECT * FROM agent_execution_jobs WHERE id = ?", (job["job_id"],)
    ).fetchone()
    assert "참고자료 원문" not in str(dict(row))  # 어떤 컬럼에도 원문이 안 남음
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0


def test_execute_agent_endpoint_accepts_multipart_attachment(client_and_conn):
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    task_id, _agent_id = _make_confirmed_agent_task(conn, admin_id)

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.llm.call_llm", return_value="완료") as mock_call:
        resp = client.post(
            f"/api/tasks/{task_id}/execute-agent",
            data={"content_type": "text"},
            files={"attachment": ("note.txt", b"first-run-only-note", "text/plain")},
        )
    assert resp.status_code == 202, resp.text

    prompt_arg = mock_call.call_args.args[3]
    assert "first-run-only-note" in prompt_arg

    # 감사 로그엔 파일명·글자수만 남고 본문은 안 남는다.
    audit_row = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'agent_execution_attachment_used' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert "note.txt" in audit_row["after_value"]
    assert "first-run-only-note" not in audit_row["after_value"]
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 0
