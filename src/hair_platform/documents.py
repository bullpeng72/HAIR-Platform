"""외부 자료(마크다운/텍스트/CSV/HTML/PDF/Excel/Word/PowerPoint) 업로드
→ VectorDB 색인.

사용자 요청(2026-10-26, "대화창 및 에이전트로 구성된 업무에서 외부
자료(마크다운, pdf, html) 등을 입력받을 수 있도록 개선해줘") — 당시엔
`knowledge_chunks` 색인이 `agent_runtime.index_knowledge()`(Agent의
`last_output`만 받음) 하나뿐이라 로컬 파일을 올리는 경로 자체가 없었다.

2026-11-20 지식베이스 등록 체계 개편 이후 입구는 둘뿐이다 — admin 직접
등록(이 모듈의 `ingest_*`, `documents.source_type='direct'`)과 제출 →
admin 결재(`knowledge_submissions.py`가 이 모듈의 `_ingest()`를 재사용,
`source_type='submission'`). `index_knowledge()`는 삭제됐다.

실측으로 확인한 버그 — `vectorstore.index_text()`는 청크 분할이 없다.
모델별 임베딩 글자수 한도(`vectorstore._MAX_EMBED_CHARS_BY_MODEL`, 예:
mxbai-embed-large는 350자)를 넘는 텍스트는 `_truncate_for_embedding()`이
뒤쪽 한도만 남기고 나머지를 통째로 버린다 — 여러 페이지짜리 PDF를 그대로
넣으면 대부분의 내용이 사라진다. 그래서 이 모듈이 업로드 텍스트를 모델
한도 이내 청크로 미리 쪼갠 뒤에야 `index_text()`를 호출한다.

VectorDB 검색(`vectorstore.search()`)은 전역이다(`source_agent_id`로
필터링하지 않는다) — 한 번 색인되면 `내부지식검색` 스킬이 있는 어떤 Agent든
대화창 단발 호출 경로(`agent_runtime.retrieve_context()`)와 실제 Crew
실행 경로(`vectordb_search` CrewAI Tool) 양쪽에서 그대로 찾아낸다. 이
모듈은 그 전역 색인기에 "파일에서 뽑은 텍스트"를 넣어주는 입구일
뿐이다 — 대화/Task별로 격리된 검색 범위를 새로 만들지 않는다.
"""

from __future__ import annotations

import re
import sqlite3

from hair_platform import audit, vectorstore

SUPPORTED_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".html", ".htm", ".pdf",
    ".csv", ".xlsx", ".docx", ".pptx",
}

# 사용자 요청(2026-11-18, "자료 업로드 형식으로 csv, excel, word,
# powerpoint를 포함하도록 개선 방안을 제안해줘" → "구현 시작") —
# 기본 상한은 기존 10MB를 그대로 유지한다(텍스트 위주 포맷은 이걸로
# 충분). PPTX만 예외 — 텍스트 비중은 작아도 슬라이드 이미지 때문에
# 파일 자체가 쉽게 커지므로 더 넉넉하게 둔다(그래도 이미지 자체는
# 추출하지 않는다 — 텍스트만).
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES_BY_EXTENSION = {
    ".pptx": 25 * 1024 * 1024,
}

# XLSX는 파일 바이트 크기와 추출된 텍스트 분량이 비례하지 않는다 —
# 작은 파일도 셀이 빽빽하면 텍스트로는 크게 불어날 수 있다. 바이트
# 상한과 별개로 시트당 행 수를 직접 제한한다. 초과분은 조용히
# 버리지 않고 "이하 N행 생략"을 명시한다(숫자를 지어내지 않는다는
# 이 코드베이스의 기존 원칙과 같은 이유 — 잘렸다는 사실 자체를
# 숨기면 안 된다).
_MAX_XLSX_ROWS_PER_SHEET = 5000


def max_upload_bytes(filename: str) -> int:
    """`app.py`의 세 업로드 경로(지식 베이스/대화 첨부/Task 실행
    참고자료)가 공유하는 확장자별 상한 조회."""
    return MAX_UPLOAD_BYTES_BY_EXTENSION.get(_extension(filename), DEFAULT_MAX_UPLOAD_BYTES)


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
    elif ext == ".csv":
        text = _extract_csv_text(raw_bytes)
    elif ext in (".html", ".htm"):
        text = _extract_html_text(raw_bytes)
    elif ext == ".pdf":
        text = _extract_pdf_text(raw_bytes)
    elif ext == ".xlsx":
        text = _extract_xlsx_text(raw_bytes)
    elif ext == ".docx":
        text = _extract_docx_text(raw_bytes)
    else:
        assert ext == ".pptx"
        text = _extract_pptx_text(raw_bytes)

    text = text.strip()
    if not text:
        raise ValueError("empty_document: 파일에서 텍스트를 추출하지 못했습니다")
    return text


def _extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx >= 0 else ""


# 사용자 요청(2026-11-18, 위와 동일) — 한국 사용자가 Excel에서
# "CSV(쉼표로 분리)"로 내보내면 기본 인코딩이 UTF-8이 아니라
# cp949(Windows) 또는 euc-kr인 경우가 흔하다. `.md`/`.txt`처럼
# UTF-8로만 디코드하면 한글이 깨진다 — 실패하면 조용히 깨진 채로
# 넘기지 않고 순서대로 재시도한다.
_CSV_ENCODING_CANDIDATES = ("utf-8", "cp949", "euc-kr")


def _extract_csv_text(raw_bytes: bytes) -> str:
    for encoding in _CSV_ENCODING_CANDIDATES:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 셋 다 실패하면 마지막엔 깨진 문자를 티 나게 바꿔서라도 보여준다
    # (완전히 포기하지 않음 — 나머지 포맷들과 같은 "실패를 숨기지
    # 않는다" 원칙).
    return raw_bytes.decode("utf-8", errors="replace")


_HTML_BLOCK_TAGS = (
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "blockquote", "section", "article", "header", "footer",
    "ul", "ol", "table",
)
_HTML_CELL_TAGS = ("td", "th")


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
    # 버그 수정(2026-11-12, 사용자 보고 — "자료 미리보기가 깨져 보인다",
    # 스크린샷으로 "HAIR(Human-AI Resource Management)" 다음 "는"이 한
    # 글자만 따로 줄바뀜된 걸 확인) — `get_text(separator="\n")`은
    # 블록/인라인 태그를 구분하지 않고 **모든** 태그 경계마다 줄바꿈을
    # 끼워 넣는다. `<p>...<b>는</b> 사람과...</p>`처럼 문장 중간에 강조
    # 인라인 태그(b/i/span/a 등)가 섞여 있으면, 원래 한 문장이던 글이
    # 그 태그 경계마다 쪼개져 단어 하나하나가 따로 줄이 된다(실측 재현
    # 완료, `soup.get_text(separator="\n")`로 직접 확인). 블록 레벨
    # 태그 뒤에만 명시적으로 줄바꿈 문자열을 끼워 넣고, 인라인 태그는
    # 건들지 않은 채 구분자 없는 `get_text()`로 이어 붙이면 원문의
    # 띄어쓰기가 그대로 보존된다 — 표 셀(td/th)만 공백으로 구분한다
    # (한 줄로 붙어버리면 "셀1셀2"처럼 읽기 힘들어지지만, 줄바꿈까지
    # 쓰면 표 한 줄이 여러 줄로 쪼개져 더 읽기 어려워진다).
    for tag in soup.find_all(_HTML_BLOCK_TAGS):
        tag.insert_after("\n")
    for tag in soup.find_all(_HTML_CELL_TAGS):
        tag.insert_after(" ")
    text = soup.get_text()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def _extract_xlsx_text(raw_bytes: bytes) -> str:
    try:
        from io import BytesIO

        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError(
            "documents_extra_not_installed: Excel 파일을 읽으려면 "
            '`pip install -e ".[documents]"`로 openpyxl을 설치하세요'
        ) from exc

    # read_only=True — 전체를 메모리에 안 올리고 행 단위로 스트리밍
    # (대용량 시트에도 안전). data_only=True — 수식이 아니라 마지막
    # 계산된 값을 읽는다(수식 문자열 자체는 텍스트로서 의미가 없음).
    workbook = load_workbook(BytesIO(raw_bytes), data_only=True, read_only=True)
    sections: list[str] = []
    for sheet in workbook.worksheets:
        lines: list[str] = [f"[시트: {sheet.title}]"]
        row_count = 0
        for row in sheet.iter_rows(values_only=True):
            if all(cell is None for cell in row):
                continue  # 완전히 빈 행은 건너뛴다(표 중간 여백 등).
            row_count += 1
            if row_count > _MAX_XLSX_ROWS_PER_SHEET:
                continue
            lines.append(" | ".join("" if c is None else str(c) for c in row))
        if row_count > _MAX_XLSX_ROWS_PER_SHEET:
            lines.append(f"...(이하 {row_count - _MAX_XLSX_ROWS_PER_SHEET}행 생략)")
        sections.append("\n".join(lines))
    workbook.close()
    return "\n\n".join(sections)


# 사용자 요청(2026-11-18, "...메모리를 활용하는 방안 등을 고려해
# 엔터프라이즈 환경 적용이 가능한 수준의 확실한 개선 방안을 도출해줘"
# 개선축 C, "업로드 시 숫자 열 자동 감지해 사전 계산") — 숫자 열이
# 있는 표(CSV/XLSX)는 `conversation_memory.search()`의 청크 검색만으론
# "합계가 얼마야" 같은 질문에 약하다(관련 청크를 찾아도 그 안의 일부
# 행만 보임 — 전체 합계는 전체 행을 다 봐야 계산 가능). 업로드 시점에
# 열별로 합계·평균 등을 실제로 계산해두면, 이후 질문에서 LLM이 암산할
# 필요 없이 이미 계산된 값을 그대로 인용할 수 있다 — "수치 데이터
# 분석기" 도구와 같은 계산(`statistics` 모듈)이지만, Tool 호출 루프가
# 없는 경로(기본 AI 등)에서도 항상 쓸 수 있도록 업로드 시점에 미리
# 계산해 저장해두는 방식이다.
_MIN_NUMERIC_VALUES_FOR_SUMMARY = 2

# 실측 버그(2026-11-18, "용돈 관리.xlsx"의 "날짜" 열이 "10.12"/"10.13"
# 형태로 저장돼 있어 `float("10.12")`가 그대로 성공해 날짜를 합계·평균
# 내는 무의미한 요약("날짜 합계=30.37")이 나왔다) — ① 열 이름에 날짜/
# 시간을 뜻하는 단어가 있으면 숫자로 파싱되더라도 제외하고, ② 열 이름이
# 애매해도 값 대부분이 "10.12"/"2026-01-01" 같은 날짜형 패턴이면 역시
# 제외한다(둘 중 하나만 걸려도 제외 — 날짜를 숫자로 잘못 집계하는 쪽이
# 숫자 열 하나를 놓치는 쪽보다 훨씬 나쁘다).
_DATE_LIKE_COLUMN_NAME_RE = re.compile(
    r"(날짜|일자|연월일|년월일|date|time|시간|timestamp)", re.IGNORECASE
)
_DATE_LIKE_VALUE_RE = re.compile(
    r"^\d{1,4}[.\-/]\d{1,2}([.\-/]\d{1,4})?$"
)


def _looks_like_date_value(cell) -> bool:
    import datetime

    if isinstance(cell, (datetime.date, datetime.datetime)):
        return True
    return isinstance(cell, str) and bool(_DATE_LIKE_VALUE_RE.match(cell.strip()))


def _summarize_numeric_columns(header: list, data_rows: list[list]) -> str | None:
    import statistics

    parts: list[str] = []
    for col_idx, raw_name in enumerate(header):
        col_name = str(raw_name).strip() if raw_name not in (None, "") else f"{col_idx + 1}열"
        if _DATE_LIKE_COLUMN_NAME_RE.search(col_name):
            continue
        values: list[float] = []
        is_numeric_column = True
        date_like_count = 0
        cell_count = 0
        for row in data_rows:
            if col_idx >= len(row):
                continue
            cell = row[col_idx]
            if cell is None or (isinstance(cell, str) and not cell.strip()):
                continue
            cell_count += 1
            if _looks_like_date_value(cell):
                date_like_count += 1
            try:
                values.append(float(cell))
            except (TypeError, ValueError):
                is_numeric_column = False
                break
        if (
            not is_numeric_column
            or len(values) < _MIN_NUMERIC_VALUES_FOR_SUMMARY
            or (cell_count > 0 and date_like_count / cell_count > 0.5)
        ):
            continue
        stdev_part = (
            f", 표준편차={round(statistics.stdev(values), 2)}" if len(values) >= 2 else ""
        )
        parts.append(
            f"{col_name}(n={len(values)}): 합계={round(sum(values), 2)}, "
            f"평균={round(statistics.mean(values), 2)}, 최소={min(values)}, "
            f"최대={max(values)}{stdev_part}"
        )
    if not parts:
        return None
    return "자동 계산된 열별 요약(실제 계산값 — 추측 아님) — " + " / ".join(parts)


def _csv_numeric_summary(raw_bytes: bytes) -> str | None:
    import csv
    import io

    text = _extract_csv_text(raw_bytes)
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < _MIN_NUMERIC_VALUES_FOR_SUMMARY + 1:
        return None
    header, *data_rows = rows
    return _summarize_numeric_columns(header, data_rows)


def _xlsx_numeric_summary(raw_bytes: bytes) -> str | None:
    from io import BytesIO

    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(raw_bytes), data_only=True, read_only=True)
    sections: list[str] = []
    for sheet in workbook.worksheets:
        rows = [
            list(row)
            for row in sheet.iter_rows(values_only=True)
            if not all(cell is None for cell in row)
        ]
        if len(rows) < _MIN_NUMERIC_VALUES_FOR_SUMMARY + 1:
            continue
        header, *data_rows = rows
        summary = _summarize_numeric_columns(list(header), data_rows)
        if summary:
            sections.append(f"[시트: {sheet.title}] {summary}")
    workbook.close()
    return "\n\n".join(sections) if sections else None


def extract_numeric_summary(filename: str, raw_bytes: bytes) -> str | None:
    """CSV/XLSX에만 적용 — 그 외 포맷이거나 숫자 열이 하나도 없으면
    `None`(근거 없이 요약을 지어내지 않는다, Gate A-G의 "정직한
    not_measured" 원칙과 동일)."""
    ext = _extension(filename)
    if ext == ".csv":
        return _csv_numeric_summary(raw_bytes)
    if ext == ".xlsx":
        return _xlsx_numeric_summary(raw_bytes)
    return None


def _extract_docx_text(raw_bytes: bytes) -> str:
    try:
        from io import BytesIO

        from docx import Document
    except ImportError as exc:
        raise ValueError(
            "documents_extra_not_installed: Word 파일을 읽으려면 "
            '`pip install -e ".[documents]"`로 python-docx를 설치하세요'
        ) from exc

    doc = Document(BytesIO(raw_bytes))
    sections: list[str] = []
    paragraphs = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if paragraphs:
        sections.append(paragraphs)
    # 표는 본문과 순서가 뒤섞이지 않고 별도 블록으로 뒤에 모은다
    # (python-docx의 기본 API는 본문 흐름 속 표의 정확한 삽입 위치를
    # 안 주므로, 순서를 억지로 재구성하기보다 명확히 구분해 보여준다).
    for i, table in enumerate(doc.tables, 1):
        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in table.rows
        ]
        if any(rows):
            sections.append(f"[표 {i}]\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _extract_pptx_text(raw_bytes: bytes) -> str:
    try:
        from io import BytesIO

        from pptx import Presentation
    except ImportError as exc:
        raise ValueError(
            "documents_extra_not_installed: PowerPoint 파일을 읽으려면 "
            '`pip install -e ".[documents]"`로 python-pptx를 설치하세요'
        ) from exc

    prs = Presentation(BytesIO(raw_bytes))
    sections: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        slide_lines: list[str] = []
        for shape in slide.shapes:
            # `has_text_frame`이 참인 도형만 `text_frame`이 있다 — BaseShape 타입에는 없어
            # getattr로 읽는다(Pylance reportAttributeAccessIssue, 2026-11-23).
            text_frame = getattr(shape, "text_frame", None) if shape.has_text_frame else None
            if text_frame is not None and text_frame.text.strip():
                slide_lines.append(text_frame.text.strip())
        if slide.has_notes_slide:
            # 노트 슬라이드에 본문 자리표시자가 없으면 `notes_text_frame`이 None이다 — 예전엔
            # 그대로 `.text`를 읽어 AttributeError로 업로드 전체가 실패했다(2026-11-23).
            notes_frame = slide.notes_slide.notes_text_frame
            notes = notes_frame.text.strip() if notes_frame is not None else ""
            if notes:
                slide_lines.append(f"[발표자 노트] {notes}")
        if slide_lines:
            sections.append(f"[{i}번 슬라이드]\n" + "\n".join(slide_lines))
    return "\n\n".join(sections)


# 사용자 요청(2026-11-03, "RAG 검색 품질 향상... 우선순위를 정해
# 진행해줘") — 강제분할(hard-split) 시 오버랩 없이 자르면 경계에서
# 문맥이 끊긴다(한 문장이 두 청크로 쪼개지면 양쪽 다 불완전한 문맥만
# 갖게 됨). 문단 패킹(문단 경계 유지) 브랜치는 이미 의미 경계를
# 지키고 있어 오버랩 이득이 없으므로 건드리지 않는다 — 강제분할
# 브랜치에만 적용한다.
_CHUNK_OVERLAP_CHARS = 50


def chunk_text(
    text: str, max_chars: int, overlap_chars: int = _CHUNK_OVERLAP_CHARS
) -> list[str]:
    """빈 줄 기준 문단 단위로 묶어 `max_chars` 이내 청크로 패킹한다.
    문단 하나가 `max_chars`보다 길면 그 문단만 강제로 잘라 나누는데,
    이때는 `overlap_chars`만큼 겹쳐서 잘라 경계의 문맥 손실을 줄인다
    (문단 패킹 쪽은 이미 문단 경계를 지키므로 오버랩을 적용하지
    않는다)."""
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
            step = max(1, max_chars - min(overlap_chars, max_chars // 3))
            i = 0
            while i < len(para):
                chunks.append(para[i : i + max_chars])
                if i + max_chars >= len(para):
                    break
                i += step
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
    *,
    source_type: str = "direct",
    submission_id: int | None = None,
) -> dict:
    if provider is None or model is None:
        default_provider, default_model = (
            vectorstore.resolve_default_provider_and_model(conn)
        )
        provider = provider or default_provider
        model = model or default_model

    chunks = chunk_text(text, vectorstore.max_embed_chars(model))

    cur = conn.execute(
        "INSERT INTO documents (filename, uploaded_by, char_count, chunk_count, "
        "source_type, submission_id) VALUES (?, ?, ?, ?, ?, ?)",
        (filename, actor_id, len(text), len(chunks), source_type, submission_id),
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
        "d.conversation_id, d.char_count, d.chunk_count, d.created_at, "
        "d.source_type, d.submission_id "
        "FROM documents d JOIN users u ON u.id = d.uploaded_by "
        "ORDER BY d.id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_document(conn: sqlite3.Connection, actor_id: int, document_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        raise ValueError("document_not_found")
    # 제출 → 결재로 등록된 문서(2026-11-20)면 그 제출 건을 'removed'로
    # 돌리고 FK를 끊는다(안 끊으면 FOREIGN KEY constraint failed). 같은
    # 원본은 이후 새 제출로 다시 등록할 수 있다.
    conn.execute(
        "UPDATE knowledge_submissions SET status = 'removed', document_id = NULL "
        "WHERE document_id = ?",
        (document_id,),
    )
    conn.execute(
        "DELETE FROM knowledge_chunks WHERE source_document_id = ?", (document_id,)
    )
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    audit.record(conn, actor_id, "document_deleted", "documents", document_id)
    conn.commit()
