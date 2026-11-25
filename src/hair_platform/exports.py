"""결과물을 Word(.docx)로 내보내기.

사용자 요청(2026-11-23, "P1 진행해줘" — P1-7 결과물 형식의 제약). 지금까지 결과물은 원본 형식(텍스트·
마크다운·HTML·코드) 그대로만 내려받을 수 있었다 — 보고서·제안서를 결재 문서나 메일 첨부로 쓰려면
사람이 다시 Word로 옮겨야 했다. 마크다운의 제목·목록·표·코드 블록·굵게를 Word 서식으로 옮긴다.
HTML 결과물은 화면에 보이는 글자만 뽑아(문단 단위) 옮긴다 — 발표자료 덱 같은 디자인은 Word로 옮길
수 없다(그런 결과물은 원본 HTML 다운로드를 쓴다).

`python-docx`는 `documents` extra(업로드 문서 읽기와 같은 묶음)에 있다 — 없으면 설치 안내를 낸다."""

from __future__ import annotations

import io
import re

INSTALL_HINT = 'Word 내보내기에는 python-docx가 필요합니다 — pip install "hair-platform[documents]"'

_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BOLD_RE = re.compile(r"(\*\*[^*]+\*\*)")


def available() -> bool:
    try:
        import docx  # noqa: F401
    except ImportError:
        return False
    return True


def _add_inline(paragraph, text: str) -> None:
    """`**굵게**`만 서식으로 옮기고, 인라인 코드 표시(`)는 떼어 낸다."""
    for part in _BOLD_RE.split(text.replace("`", "")):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        else:
            paragraph.add_run(part)


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _html_to_lines(html: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
        text = " ".join(el.get_text(" ").split())
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append("#" * int(el.name[1]) + " " + text)
        elif el.name == "li":
            lines.append("- " + text)
        else:
            lines.append(text)
    return lines or [" ".join(soup.get_text(" ").split())]


def to_docx(content: str, content_type: str = "markdown", title: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    if title:
        doc.core_properties.title = title
    if content_type == "code":
        para = doc.add_paragraph()
        run = para.add_run(content)
        run.font.name = "Consolas"
        run.font.size = Pt(9)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    lines = _html_to_lines(content) if content_type == "html" else (content or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip().lower()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            if lang in ("chart", "mermaid"):
                # 차트·다이어그램은 Word로 그릴 수 없다 — 원본 JSON 대신 자리 안내만 남긴다.
                found = re.search(r'"title"\s*:\s*"([^"]*)"', "\n".join(block))
                name = f" 「{found.group(1)}」" if found and found.group(1) else ""
                kind = "차트" if lang == "chart" else "다이어그램"
                doc.add_paragraph().add_run(f"({kind}{name} — HAIR 화면에서 확인)").italic = True
                continue
            run = doc.add_paragraph().add_run("\n".join(block))
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _row_cells(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_row_cells(lines[i]))
                i += 1
            table = doc.add_table(rows=1 + len(rows), cols=len(header))
            table.style = "Table Grid"
            for r, cells in enumerate([header, *rows]):
                for c in range(len(header)):
                    cell = table.cell(r, c)
                    cell.text = ""
                    _add_inline(cell.paragraphs[0], cells[c] if c < len(cells) else "")
                    if r == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            continue
        heading = _HEADING_RE.match(line)
        bullet = _BULLET_RE.match(line)
        numbered = _NUMBERED_RE.match(line)
        if heading:
            doc.add_heading(heading.group(2).replace("**", "").strip(), level=min(len(heading.group(1)), 4))
        elif bullet:
            _add_inline(doc.add_paragraph(style="List Bullet"), bullet.group(1))
        elif numbered:
            _add_inline(doc.add_paragraph(style="List Number"), numbered.group(1))
        elif line.strip().startswith(">"):
            _add_inline(doc.add_paragraph(style="Quote"), line.strip().lstrip("> "))
        elif line.strip() in ("---", "***"):
            pass
        elif line.strip():
            _add_inline(doc.add_paragraph(), line.strip())
        i += 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- Excel·PowerPoint 내보내기(2026-11-24, P1-7 — "xlsx·pptx로 내보낼 수 없다") ----------
# 실적 보고·재무 분석처럼 표가 핵심인 결과물은 엑셀로, 발표자료는 파워포인트로 바로 받는다. 둘 다
# `documents` extra의 openpyxl·python-pptx를 쓴다(업로드 문서 읽기와 같은 묶음).

XLSX_INSTALL_HINT = 'Excel 내보내기에는 openpyxl이 필요합니다 — pip install "hair-platform[documents]"'
PPTX_INSTALL_HINT = 'PowerPoint 내보내기에는 python-pptx가 필요합니다 — pip install "hair-platform[documents]"'


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def pptx_available() -> bool:
    try:
        import pptx  # noqa: F401
    except ImportError:
        return False
    return True


def _blocks(lines: list[str]) -> list[dict]:
    """마크다운 줄 → 블록(heading/table/bullet/para/code). 내보내기 형식별로 다시 쓴다."""
    out: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip().lower()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append({"type": "code", "lang": lang, "text": "\n".join(block)})
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _row_cells(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_row_cells(lines[i]))
                i += 1
            out.append({"type": "table", "header": header, "rows": rows})
            continue
        heading = _HEADING_RE.match(line)
        bullet = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
        if heading:
            out.append({"type": "heading", "level": len(heading.group(1)), "text": _plain(heading.group(2))})
        elif bullet:
            out.append({"type": "bullet", "text": _plain(bullet.group(1))})
        elif line.strip() and line.strip() not in ("---", "***"):
            out.append({"type": "para", "text": _plain(line.strip().lstrip("> "))})
        i += 1
    return out


def _plain(text: str) -> str:
    return text.replace("**", "").replace("`", "").strip()


_NUMBER_RE = re.compile(r"^[+\-−]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^[+\-−]?\d+(?:\.\d+)?$")
_PERCENT_RE = re.compile(r"^([+\-−]?\d+(?:\.\d+)?)\s*%$")


def _cell_value(text: str):
    """표 칸 글 → 숫자면 숫자(엑셀에서 바로 계산되게), 퍼센트는 비율 + 표시 형식, 나머지는 글 그대로."""
    t = _plain(text)
    if _NUMBER_RE.match(t):
        return float(t.replace(",", "").replace("−", "-")), None
    m = _PERCENT_RE.match(t)
    if m:
        return float(m.group(1).replace("−", "-")) / 100, "0.0#%"
    return t, None


def to_xlsx(content: str, content_type: str = "markdown", title: str | None = None) -> bytes:
    """표마다 시트 하나(시트 이름은 바로 위 제목). 표가 없으면 '내용' 시트에 줄 단위로."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    lines = _html_to_lines(content) if content_type == "html" else (content or "").splitlines()
    blocks = _blocks(lines)
    wb = Workbook()
    default_sheet = wb.active  # 새 통합 문서의 빈 기본 시트(타입상 None일 수 있어 확인 후 지움)
    if default_sheet is not None:
        wb.remove(default_sheet)
    used: set[str] = set()

    def sheet_name(base: str) -> str:
        name = re.sub(r"[\[\]:*?/\\]", " ", base).strip()[:28] or "표"
        candidate, n = name, 2
        while candidate in used:
            candidate, n = f"{name[:25]} {n}", n + 1
        used.add(candidate)
        return candidate

    last_heading = None
    for b in blocks:
        if b["type"] == "heading":
            last_heading = b["text"]
        elif b["type"] == "table":
            ws = wb.create_sheet(sheet_name(last_heading or f"표 {len(used) + 1}"))
            ws.append(b["header"])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in b["rows"]:
                values = [_cell_value(row[c] if c < len(row) else "") for c in range(len(b["header"]))]
                ws.append([v for v, _fmt in values])
                for c, (_v, fmt) in enumerate(values, start=1):
                    if fmt:
                        ws.cell(row=ws.max_row, column=c).number_format = fmt
            for c, head in enumerate(b["header"], start=1):
                width = max([len(str(head))] + [len(str(r[c - 1])) if c - 1 < len(r) else 0 for r in b["rows"]])
                ws.column_dimensions[get_column_letter(c)].width = min(60, max(10, width * 1.6))
            ws.freeze_panes = "A2"
    if not wb.worksheets:
        ws = wb.create_sheet("내용")
        for b in blocks:
            text = b.get("text", "")
            if b["type"] == "heading":
                ws.append([text])
                ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            elif text:
                ws.append([("• " if b["type"] == "bullet" else "") + text])
        ws.column_dimensions["A"].width = 100
    if title:
        wb.properties.title = title
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _deck_slides_from_html(html: str) -> list[dict] | None:
    """HAIR 발표자료 덱(`<section>` 슬라이드) → 슬라이드 목록. 덱이 아니면 None."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    sections = soup.find_all("section")
    if len(sections) < 2:
        return None
    slides = []
    for sec in sections:
        for tag in sec(["script", "style"]):
            tag.decompose()
        head = sec.find(["h1", "h2", "h3"])
        title = " ".join(head.get_text(" ").split()) if head else ""
        body = []
        for el in sec.find_all(["li", "p", "td"]):
            text = " ".join(el.get_text(" ").split())
            if text and text != title:
                body.append(text)
        slides.append({"title": title, "body": body[:12], "table": None})
    return slides


def _slides_from_markdown(content: str) -> list[dict]:
    slides: list[dict] = []
    current: dict | None = None
    parent = ""  # 마지막 #·## 제목 — ### 소제목 슬라이드에 "상위 — 소제목"으로 붙인다
    for b in _blocks((content or "").splitlines()):
        if b["type"] == "heading" and b["level"] <= 3:
            empty_prev = current is not None and not current["body"] and current["table"] is None
            if b["level"] <= 2:
                parent = b["text"]
                title = b["text"]
            else:
                title = f"{parent} — {b['text']}" if parent else b["text"]
            if empty_prev and current is not None and b["level"] == 3:
                current["title"] = title  # 내용 없는 상위 제목 슬라이드는 소제목 슬라이드로 합친다
            else:
                current = {"title": title, "body": [], "table": None, "base": title}
                slides.append(current)
            continue
        if current is None:
            current = {"title": "", "body": [], "table": None, "base": ""}
            slides.append(current)
        if b["type"] == "table":
            if current["table"] is not None:  # 한 슬라이드에 표 하나 — 다음 표는 새 슬라이드
                base = current.get("base") or current["title"]
                current = {"title": base + " (계속)", "body": [], "table": None, "base": base}
                slides.append(current)
            current["table"] = b
        elif b["type"] == "heading" or b["type"] in ("bullet", "para"):
            current["body"].append(b["text"])
        elif b["type"] == "code" and b["lang"] in ("chart", "mermaid"):
            current["body"].append(("(차트" if b["lang"] == "chart" else "(다이어그램") + " — HAIR 화면에서 확인)")
    return [s for s in slides if s["title"] or s["body"] or s["table"]]


def to_pptx(content: str, content_type: str = "markdown", title: str | None = None) -> bytes:
    """제목(#·##)마다 슬라이드 하나 — 목록·문단은 본문, 표는 표로. HTML 덱은 `<section>`마다 한 장."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    slides = None
    if content_type == "html":
        slides = _deck_slides_from_html(content)
        if slides is None:
            slides = _slides_from_markdown("\n".join(_html_to_lines(content)))
    else:
        slides = _slides_from_markdown(content)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    if title:
        prs.core_properties.title = title
    for s in slides or [{"title": title or "결과물", "body": [], "table": None}]:
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # 제목만
        title_shape = slide.shapes.title  # "제목만" 레이아웃이라 늘 있지만 타입상 None일 수 있다
        if title_shape is not None:
            title_shape.text = s["title"][:120]
        top = Inches(1.5)
        if s["body"]:
            box = slide.shapes.add_textbox(Inches(0.6), top, Inches(12.1), Inches(2.6 if s["table"] else 5.5))
            tf = box.text_frame
            tf.word_wrap = True
            for n, text in enumerate(s["body"][:12]):
                para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
                para.text = "• " + text[:300]
                para.font.size = Pt(18 if len(s["body"]) <= 6 else 14)
            top = Inches(4.2) if s["table"] else top
        if s["table"]:
            header, rows = s["table"]["header"], s["table"]["rows"][:14]
            shape = slide.shapes.add_table(1 + len(rows), len(header), Inches(0.6), top, Inches(12.1),
                                           Inches(0.4 * (1 + len(rows))))  # 행당 0.4인치(Length 타입 유지)
            for r, cells in enumerate([header, *rows]):
                for c in range(len(header)):
                    cell = shape.table.cell(r, c)
                    cell.text = _plain(cells[c]) if c < len(cells) else ""
                    for para in cell.text_frame.paragraphs:
                        para.font.size = Pt(12)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
