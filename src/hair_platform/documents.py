"""외부 자료(마크다운/PDF/HTML) 업로드 → VectorDB 색인.

사용자 요청(2026-10-26, "대화창 및 에이전트로 구성된 업무에서 외부
자료(마크다운, pdf, html) 등을 입력받을 수 있도록 개선해줘") — 지금까지
`knowledge_chunks` 색인은 `agent_runtime.index_knowledge()` 하나뿐이고,
그 함수는 이미 존재하는 Agent의 `last_output`(또는 명시한 텍스트)만
받는다. 로컬 파일을 올리는 경로 자체가 없었다.

실측으로 확인한 버그 — `vectorstore.index_text()`는 청크 분할이 없다.
모델별 임베딩 글자수 한도(`vectorstore._MAX_EMBED_CHARS_BY_MODEL`, 예:
mxbai-embed-large는 350자)를 넘는 텍스트는 `_truncate_for_embedding()`이
뒤쪽 한도만 남기고 나머지를 통째로 버린다 — 여러 페이지짜리 PDF를 그대로
넣으면 대부분의 내용이 사라진다. 그래서 이 모듈이 업로드 텍스트를 모델
한도 이내 청크로 미리 쪼갠 뒤에야 `index_text()`를 호출한다.

VectorDB 검색(`vectorstore.search()`)은 전역이다(`source_agent_id`로
필터링하지 않는다) — 한 번 색인되면 `rag` 스킬이 있는 어떤 Agent든
대화창 단발 호출 경로(`agent_runtime.retrieve_context()`)와 실제 Crew
실행 경로(`vectordb_search` CrewAI Tool) 양쪽에서 그대로 찾아낸다. 이
모듈은 그 전역 색인기에 "파일에서 뽑은 텍스트"를 넣어주는 입구일
뿐이다 — 대화/Task별로 격리된 검색 범위를 새로 만들지 않는다.
"""

from __future__ import annotations

import sqlite3

from hair_platform import audit, vectorstore

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}


def extract_text(filename: str, raw_bytes: bytes) -> str:
    """파일 확장자에 맞는 방식으로 텍스트만 뽑아낸다.

    `.md`/`.markdown`/`.txt`는 마크다운 자체가 이미 임베딩에 적합한
    사람이 읽는 텍스트라 추가 파싱 없이 그대로 디코드한다. 지원하지 않는
    확장자거나 추출 결과가 비어 있으면 `ValueError`."""
    ext = _extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"unsupported_extension: {ext or '(없음)'} "
            f"— 지원 형식: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext in (".md", ".markdown", ".txt"):
        text = raw_bytes.decode("utf-8", errors="replace")
    elif ext in (".html", ".htm"):
        text = _extract_html_text(raw_bytes)
    else:
        assert ext == ".pdf"
        text = _extract_pdf_text(raw_bytes)

    text = text.strip()
    if not text:
        raise ValueError("empty_document: 파일에서 텍스트를 추출하지 못했습니다")
    return text


def _extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx >= 0 else ""


def _extract_html_text(raw_bytes: bytes) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ValueError(
            "documents_extra_not_installed: HTML 파일을 읽으려면 "
            '`pip install -e ".[documents]"`로 beautifulsoup4를 설치하세요'
        ) from exc

    soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _extract_pdf_text(raw_bytes: bytes) -> str:
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "documents_extra_not_installed: PDF 파일을 읽으려면 "
            '`pip install -e ".[documents]"`로 pypdf를 설치하세요'
        ) from exc

    reader = PdfReader(BytesIO(raw_bytes))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def chunk_text(text: str, max_chars: int) -> list[str]:
    """빈 줄 기준 문단 단위로 묶어 `max_chars` 이내 청크로 패킹한다
    (오버랩 없는 단순 구현). 문단 하나가 `max_chars`보다 길면 그 문단만
    강제로 잘라 나눈다."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > max_chars:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def ingest_document(
    conn: sqlite3.Connection,
    actor_id: int,
    filename: str,
    raw_bytes: bytes,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """파일을 추출→청크→색인까지 한 번에 처리한다(비용 발생 — 청크마다
    임베딩 API를 호출한다). "지식 베이스"(admin/manager 전용 공통·영구
    지식) 전용 경로다 — 대화/업무 1회성 첨부는 저장을 거치지 않는
    별도 경로(`conversations.create_attachment_message()`,
    `deliverables.start_agent_execution(attachment_text=...)`)를 쓴다
    (2026-10-26, "A안 + B안" 결정)."""
    text = extract_text(filename, raw_bytes)
    return _ingest(conn, actor_id, filename, text, provider, model)


def ingest_text(
    conn: sqlite3.Connection,
    actor_id: int,
    text: str,
    label: str = "직접 입력",
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """사용자 요청(2026-10-26, "Admin에 VectorDB... 데이터 입력") — 파일
    없이 "지식 베이스" 탭에서 텍스트를 바로 붙여넣어 색인하는 길.
    파일 추출 단계(`extract_text()`)만 없을 뿐, 그 다음부터는
    `ingest_document()`와 완전히 같은 `_ingest()`를 공유한다."""
    text = text.strip()
    if not text:
        raise ValueError("empty_document: 입력한 텍스트가 비어 있습니다")
    return _ingest(conn, actor_id, label, text, provider, model)


def _ingest(
    conn: sqlite3.Connection,
    actor_id: int,
    filename: str,
    text: str,
    provider: str | None,
    model: str | None,
) -> dict:
    if provider is None or model is None:
        default_provider, default_model = vectorstore.resolve_default_provider_and_model(
            conn
        )
        provider = provider or default_provider
        model = model or default_model

    chunks = chunk_text(text, vectorstore.max_embed_chars(model))

    cur = conn.execute(
        "INSERT INTO documents (filename, uploaded_by, char_count, chunk_count) "
        "VALUES (?, ?, ?, ?)",
        (filename, actor_id, len(text), len(chunks)),
    )
    document_id = cur.lastrowid
    assert document_id is not None

    for chunk in chunks:
        chunk_id = vectorstore.index_text(conn, None, chunk, provider, model)
        conn.execute(
            "UPDATE knowledge_chunks SET source_document_id = ? WHERE id = ?",
            (document_id, chunk_id),
        )

    audit.record(
        conn,
        actor_id,
        "document_ingested",
        "documents",
        document_id,
        after={
            "filename": filename,
            "char_count": len(text),
            "chunk_count": len(chunks),
        },
    )
    conn.commit()
    return {
        "document_id": document_id,
        "filename": filename,
        "char_count": len(text),
        "chunk_count": len(chunks),
    }


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT d.id, d.filename, d.mime_type, d.uploaded_by, u.username AS uploaded_by_username, "
        "d.conversation_id, d.char_count, d.chunk_count, d.created_at "
        "FROM documents d JOIN users u ON u.id = d.uploaded_by "
        "ORDER BY d.id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_document(conn: sqlite3.Connection, actor_id: int, document_id: int) -> None:
    row = conn.execute("SELECT id FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise ValueError("document_not_found")
    conn.execute("DELETE FROM knowledge_chunks WHERE source_document_id = ?", (document_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    audit.record(conn, actor_id, "document_deleted", "documents", document_id)
    conn.commit()
