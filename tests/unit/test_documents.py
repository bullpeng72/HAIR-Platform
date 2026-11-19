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

import itertools
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


def test_extract_text_html_keeps_inline_tagged_sentence_intact():
    """사용자 보고(2026-11-12, 스크린샷) — 문장 중간에 `<b>`/`<i>` 같은
    인라인 태그가 섞이면 그 경계마다 줄바꿈이 끼어들어 "HAIR(...)"
    다음에 와야 할 "는"이 따로 떨어진 줄로 깨져 보였다. 인라인 태그는
    줄바꿈 없이 그대로 이어 붙어야 한다."""
    html = (
        "<html><body><p>HAIR(Human-AI Resource Management)"
        "<b>는</b> 사람과 <i>AI Agent</i>를 같이 배치합니다.</p>"
        "</body></html>"
    ).encode()
    text = documents.extract_text("report.html", html)
    assert "HAIR(Human-AI Resource Management)는 사람과 AI Agent를 같이 배치합니다." in text


def test_extract_text_html_separates_block_level_paragraphs():
    html = (
        "<html><body><p>첫 번째 문단</p><p>두 번째 문단</p></body></html>"
    ).encode()
    text = documents.extract_text("multi.html", html)
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines == ["첫 번째 문단", "두 번째 문단"]


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


# ---------- extract_text() — csv/xlsx/docx/pptx(사용자 요청, 2026-11-18,
# "자료 업로드 형식으로 csv, excel, word, powerpoint를 포함하도록
# 개선 방안을 제안해줘" → "구현 시작") ----------


def test_extract_text_csv_decoded_as_is():
    csv_bytes = "이름,나이\n홍길동,30\n".encode("utf-8")
    assert documents.extract_text("list.csv", csv_bytes) == "이름,나이\n홍길동,30"


def test_extract_text_csv_falls_back_to_cp949_encoding():
    """한국 사용자가 Excel에서 "CSV(쉼표로 분리)"로 내보내면 흔히
    cp949로 저장된다 — UTF-8로만 디코드하면 깨진다."""
    csv_bytes = "이름,나이\n박영희,40\n".encode("cp949")
    text = documents.extract_text("list_cp949.csv", csv_bytes)
    assert "박영희" in text
    assert "�" not in text


def test_extract_text_xlsx_reads_multiple_sheets():
    openpyxl = pytest.importorskip("openpyxl")
    from io import BytesIO

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "매출"
    ws1.append(["월", "매출액"])
    ws1.append(["1월", 1000])
    ws2 = wb.create_sheet("비고")
    ws2.append(["특이사항 없음"])
    buf = BytesIO()
    wb.save(buf)

    text = documents.extract_text("report.xlsx", buf.getvalue())
    assert "[시트: 매출]" in text
    assert "월 | 매출액" in text
    assert "1월 | 1000" in text
    assert "[시트: 비고]" in text
    assert "특이사항 없음" in text


def test_extract_text_xlsx_truncates_rows_over_cap(monkeypatch):
    """바이트 크기와 무관하게 셀이 빽빽하면 텍스트가 크게 불어날 수
    있다 — 시트당 행 수 상한을 두되, 잘렸다는 사실 자체는 숨기지
    않는다."""
    openpyxl = pytest.importorskip("openpyxl")
    from io import BytesIO

    monkeypatch.setattr(documents, "_MAX_XLSX_ROWS_PER_SHEET", 3)
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(5):
        ws.append([i])
    buf = BytesIO()
    wb.save(buf)

    text = documents.extract_text("big.xlsx", buf.getvalue())
    lines = text.splitlines()
    assert lines[1:4] == ["0", "1", "2"]
    assert lines[-1] == "...(이하 2행 생략)"


def test_extract_text_xlsx_skips_fully_empty_rows():
    openpyxl = pytest.importorskip("openpyxl")
    from io import BytesIO

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["a"])
    ws.append([None])
    ws.append(["b"])
    buf = BytesIO()
    wb.save(buf)

    text = documents.extract_text("gaps.xlsx", buf.getvalue())
    assert text.splitlines()[1:] == ["a", "b"]


def test_extract_text_docx_reads_paragraphs_and_tables():
    docx = pytest.importorskip("docx")
    from io import BytesIO

    doc = docx.Document()
    doc.add_paragraph("본문 문단입니다.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "항목"
    table.cell(0, 1).text = "값"
    buf = BytesIO()
    doc.save(buf)

    text = documents.extract_text("report.docx", buf.getvalue())
    assert "본문 문단입니다." in text
    assert "[표 1]" in text
    assert "항목 | 값" in text


def test_extract_text_pptx_reads_slide_text_and_notes():
    pptx = pytest.importorskip("pptx")
    from io import BytesIO

    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "첫 번째 슬라이드"
    slide.placeholders[1].text = "핵심 내용"
    slide.notes_slide.notes_text_frame.text = "발표 포인트"
    buf = BytesIO()
    prs.save(buf)

    text = documents.extract_text("deck.pptx", buf.getvalue())
    assert "[1번 슬라이드]" in text
    assert "첫 번째 슬라이드" in text
    assert "핵심 내용" in text
    assert "[발표자 노트] 발표 포인트" in text


def test_extract_text_pptx_skips_slide_with_no_text():
    pptx = pytest.importorskip("pptx")
    from io import BytesIO

    prs = pptx.Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])  # 빈 레이아웃 — 텍스트 없음
    slide2 = prs.slides.add_slide(prs.slide_layouts[6])
    tb = slide2.shapes.add_textbox(0, 0, 100, 100)
    tb.text_frame.text = "두 번째엔 내용 있음"
    buf = BytesIO()
    prs.save(buf)

    text = documents.extract_text("deck2.pptx", buf.getvalue())
    assert "[1번 슬라이드]" not in text  # 빈 슬라이드는 섹션 자체가 생략됨
    assert "[2번 슬라이드]" in text


# ---------- max_upload_bytes() ----------


def test_max_upload_bytes_default_and_pptx_override():
    assert documents.max_upload_bytes("note.md") == 10 * 1024 * 1024
    assert documents.max_upload_bytes("deck.pptx") == 25 * 1024 * 1024
    assert documents.max_upload_bytes("NO_EXTENSION") == 10 * 1024 * 1024


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
    for prev, nxt in itertools.pairwise(chunks):
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


def test_upload_document_accepts_csv(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = client.post(
            "/api/documents",
            files={"file": ("list.csv", "이름,나이\n홍길동,30\n".encode(), "text/csv")},
        )
    assert resp.status_code == 201, resp.text


def test_upload_document_enforces_per_extension_size_limit(client_and_conn):
    """사용자 요청(2026-11-18, "10MB 상한을 포맷별로 다르게") — .md는
    여전히 10MB가 상한이라 10MB를 살짝 넘기면 400이어야 한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    too_big = b"a" * (10 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/documents", files={"file": ("huge.md", too_big, "text/markdown")}
    )
    assert resp.status_code == 400, resp.text
    assert "10MB" in resp.json()["detail"]


def test_upload_conversation_attachment_accepts_pptx_up_to_25mb(client_and_conn):
    """PPTX는 .md와 달리 25MB까지 허용된다 — 10MB를 넘겨도(.md였다면
    거부됐을 크기) 25MB 이하면 통과해야 한다."""
    pptx = pytest.importorskip("pptx")
    from io import BytesIO

    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "테스트"}).json()["id"]

    import secrets

    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # 단순 반복 문자는 zip 압축(pptx 포맷의 실제 저장 방식)으로 원본
    # 바이트 수와 무관하게 쪼그라든다 — 압축에 강한 무작위 16진수
    # 문자열을 써야 실제 업로드 바이트 수가 의도한 만큼 커진다. 텍스트
    # 노드 하나에 몰아넣으면 lxml 자체의 "텍스트 노드가 너무 길다"는
    # 안전장치에 걸리므로, 여러 텍스트 상자로 나눠 담는다.
    for _ in range(14):  # 실측(2026-11-18) — 약 16MB로, 10MB~25MB 사이
        tb = slide.shapes.add_textbox(0, 0, 100, 100)
        tb.text_frame.text = secrets.token_hex(1024 * 1024)  # 상자당 2MB
    buf = BytesIO()
    prs.save(buf)
    raw = buf.getvalue()
    assert len(raw) > 10 * 1024 * 1024  # 실제로 10MB를 넘는지 확인

    # 사용자 요청(2026-11-18, "...메모리를 활용하는 방안... 개선 방안을
    # 도출해줘" 개선축 C) — 이 정도로 큰 첨부는 추출된 텍스트가 문턱
    # (`conversations._LARGE_ATTACHMENT_CHAR_THRESHOLD`)을 넘어 대화
    # 메모리에 색인되므로(실제 임베딩 API 호출), 다른 임베딩 테스트와
    # 같은 관례로 모킹한다 — 안 하면 실제 네트워크 호출을 반복하다
    # 멈추지 않는다(실측: 11분 넘게 걸려 강제 종료해야 했던 회귀).
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = client.post(
            f"/api/conversations/{conv_id}/attachments",
            files={
                "file": (
                    "deck.pptx",
                    raw,
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            },
        )
    assert resp.status_code == 201, resp.text


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
