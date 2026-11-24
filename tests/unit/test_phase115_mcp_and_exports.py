"""P1-7(2026-11-24 — "go ahead"): 대화·단일 Agent 실행의 MCP 도구 + Excel·PowerPoint 내보내기."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from hair_platform import db, exports, tool_calling


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _mcp_skill(conn):
    tid = conn.execute(
        "INSERT INTO tool_pool (name, description, kind, config) VALUES ('사내 검색 MCP', 'd', 'mcp_remote', ?)",
        (json.dumps({"url": "http://localhost:9/mcp", "transport": "streamable-http"}),),
    ).lastrowid
    conn.execute(
        "INSERT INTO skill_pool (name, description, default_tool_pool_ids, augmentation_mode) VALUES ('사내검색', 'd', ?, 'tool')",
        (json.dumps([tid]),),
    )
    conn.commit()
    return tid


class _FakeTool:
    name = "intranet_search"


def test_mcp_tools_only_inside_session(conn):
    _mcp_skill(conn)
    with patch("hair_platform.pool_registry.connect_mcp_tools", return_value=[_FakeTool()]) as connect:
        assert tool_calling.resolve_callable_tools(conn, ["사내검색"]) == []  # 세션 밖: 예전처럼 제외
        assert tool_calling.unsupported_skill_names(conn, ["사내검색"]) == ["사내검색"]
        with tool_calling.mcp_session():
            tools = tool_calling.resolve_callable_tools(conn, ["사내검색"])
            assert [t.name for t in tools] == ["intranet_search"]
            assert tool_calling.unsupported_skill_names(conn, ["사내검색"]) == []
    assert connect.call_count == 1


def test_failed_mcp_connection_is_skipped_and_reported(conn):
    _mcp_skill(conn)
    with patch("hair_platform.pool_registry.connect_mcp_tools", side_effect=ConnectionError("down")), \
            tool_calling.mcp_session():
        assert tool_calling.resolve_callable_tools(conn, ["사내검색"]) == []
        assert tool_calling.unsupported_skill_names(conn, ["사내검색"]) == ["사내검색"]


def test_session_closes_connections():
    closed = []

    class _Adapter:
        def __enter__(self):
            return [_FakeTool()]

        def __exit__(self, *a):
            closed.append(True)
            return False

    with tool_calling.mcp_session():
        tool_calling._MCP_STACK.get().enter_context(_Adapter())
        assert closed == []
    assert closed == [True] and tool_calling._MCP_STACK.get() is None


REPORT = (
    "# 3분기 실적\n\n## 예산 대비 실적\n| 항목 | 예산 | 실적 | 차이율 |\n|---|---:|---:|---:|\n"
    "| 인건비 | 1,200 | 1,180 | −1.67% |\n| 마케팅 | 400 | 520 | 30.0% |\n\n"
    "## 권고\n- 마케팅 초과분 확인\n- 교육 계획 수립\n\n### 일정\n| 주 | 할 일 |\n|---|---|\n| 1주 | 원인 확인 |\n"
)


def test_xlsx_one_sheet_per_table_with_numbers():
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(exports.to_xlsx(REPORT, "markdown", "실적")))
    assert [ws.title for ws in wb.worksheets] == ["예산 대비 실적", "일정"]
    ws = wb["예산 대비 실적"]
    assert [c.value for c in ws[1]] == ["항목", "예산", "실적", "차이율"]
    assert ws["B2"].value == 1200 and ws["C3"].value == 520
    assert ws["D2"].value == pytest.approx(-0.0167) and "%" in ws["D2"].number_format


def test_xlsx_without_tables_keeps_text():
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(exports.to_xlsx("## 제목\n- 항목 하나\n본문", "markdown")))
    assert wb.sheetnames == ["내용"] and [r[0].value for r in wb["내용"].iter_rows()] == ["제목", "• 항목 하나", "본문"]


def test_pptx_slides_per_heading_with_tables():
    pytest.importorskip("pptx")
    from pptx import Presentation

    prs = Presentation(io.BytesIO(exports.to_pptx(REPORT, "markdown", "실적")))
    titles = [s.shapes.title.text for s in prs.slides]
    assert titles == ["3분기 실적", "예산 대비 실적", "권고", "권고 — 일정"]  # 맨 앞 # 제목은 표지 슬라이드
    tables = [sh for sh in prs.slides[1].shapes if sh.has_table]
    assert len(tables) == 1 and tables[0].table.cell(2, 0).text == "마케팅"


def test_pptx_from_html_deck_sections():
    pytest.importorskip("pptx")
    pytest.importorskip("bs4")
    from pptx import Presentation

    html = ('<main class="hair-deck"><section><h1>표지</h1><p>3분기 보고</p></section>'
            '<section><h2>요약</h2><ul><li>마케팅 초과</li><li>교육 미집행</li></ul></section></main>')
    prs = Presentation(io.BytesIO(exports.to_pptx(html, "html")))
    assert [s.shapes.title.text for s in prs.slides] == ["표지", "요약"]


def test_export_endpoints(client_and_conn):
    pytest.importorskip("openpyxl")
    pytest.importorskip("pptx")
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
        "VALUES (?, 'agent', 1, ?, 'markdown', 'approved')", (task, REPORT),
    ).lastrowid
    conn.commit()
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    x = client.get(f"/api/deliverables/{did}/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK" and "spreadsheetml" in x.headers["content-type"]
    p = client.get(f"/api/deliverables/{did}/export.pptx")
    assert p.status_code == 200 and "presentationml" in p.headers["content-type"]
    assert client.get(f"/api/deliverables/{did}/export.pdf").status_code == 404
    assert client.get(f"/api/deliverables/{did}/export.docx").status_code in (200, 400)  # 기존 Word 경로 유지
