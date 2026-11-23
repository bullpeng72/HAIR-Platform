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
