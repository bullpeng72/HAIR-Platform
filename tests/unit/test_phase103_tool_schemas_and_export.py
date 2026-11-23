"""도구 입력 스키마·Word 내보내기(2026-11-23, "P1 진행해줘" — P1-7)."""

from __future__ import annotations

import io
import json

import pytest

from hair_platform import db, exports, pool_registry, reference_pipeline_seeds as seeds, tool_calling


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _tool(conn, name):
    return pool_registry._custom_python_crewai_tool(
        conn.execute("SELECT * FROM tool_pool WHERE name = ?", (name,)).fetchone()
    )


# ---------- (a) 도구 입력 스키마 ----------


def test_json_tools_expose_structured_parameters():
    conn = _conn()
    t = _tool(conn, "수치 데이터 분석기")
    params = tool_calling.tool_schema_openai(t)["function"]["parameters"]
    assert set(params["properties"]) == {"series", "values"}
    chart = tool_calling.tool_schema_anthropic(_tool(conn, "차트 생성기"))["input_schema"]
    assert chart["required"] == ["type", "labels", "series"]
    # 스키마가 없는 도구는 예전처럼 query 하나.
    lint = tool_calling.tool_schema_openai(_tool(conn, "Spec EARS 린터"))["function"]["parameters"]
    assert list(lint["properties"]) == ["query"]


def test_structured_arguments_reach_the_tool_as_json(monkeypatch):
    conn = _conn()
    t = _tool(conn, "수치 데이터 분석기")
    seen = {}
    monkeypatch.setattr(pool_registry, "_run_custom_python_tool",
                        lambda source, query, timeout: seen.setdefault("q", query) or "ok")
    tool_calling.run_tool({t.name: t}, t.name, {"series": {"1분기": 42, "2분기": 48}})
    assert json.loads(seen["q"]) == {"series": {"1분기": 42, "2분기": 48}}
    seen.clear()
    tool_calling.run_tool({t.name: t}, t.name, {"query": "42, 48"})  # 예전 형식도 그대로 통과
    assert seen["q"] == "42, 48"


def test_real_tool_runs_with_structured_arguments():
    conn = _conn()
    t = _tool(conn, "수치 데이터 분석기")
    out = tool_calling.run_tool({t.name: t}, t.name, {"series": {"1분기": 42, "2분기": 48}})
    assert '"growth_rate_pct": 14.29' in out


def test_migration_adds_schema_only_to_untouched_seed_sources():
    conn = _conn()
    for name in ("수치 데이터 분석기", "차트 생성기"):
        row = conn.execute("SELECT id, config FROM tool_pool WHERE name = ?", (name,)).fetchone()
        cfg = json.loads(row["config"])
        cfg.pop("input_schema", None)
        if name == "차트 생성기":
            cfg["function_source"] += "\n# admin이 고친 코드\n"
        conn.execute("UPDATE tool_pool SET config = ? WHERE id = ?", (json.dumps(cfg), row["id"]))
    db._add_tool_input_schemas(conn)
    cfg = {n: json.loads(c) for n, c in conn.execute("SELECT name, config FROM tool_pool")}
    assert cfg["수치 데이터 분석기"]["input_schema"] == seeds.TOOL_INPUT_SCHEMAS["수치 데이터 분석기"]
    assert "input_schema" not in cfg["차트 생성기"]


# ---------- (b) Word 내보내기 ----------

MD = """# 3분기 실적 보고
## 핵심 실적
- 매출 **55억** 달성
1. 영업이익 6.2억

| 지표 | 목표 | 실적 |
|---|---|---|
| 매출 | 52억 | 55억 |

```chart
{"type": "bar", "title": "분기별 매출", "labels": ["a"], "series": {"x": [1]}}
```
> 전기 실적은 확인 필요
"""


@pytest.mark.skipif(not exports.available(), reason="python-docx 미설치")
def test_markdown_to_docx_keeps_structure():
    from docx import Document

    doc = Document(io.BytesIO(exports.to_docx(MD, "markdown")))
    texts = [p.text for p in doc.paragraphs]
    styles = {p.text: p.style.name for p in doc.paragraphs}
    assert styles["3분기 실적 보고"].startswith("Heading 1") and styles["핵심 실적"].startswith("Heading 2")
    assert styles["매출 55억 달성"] == "List Bullet"
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "55억" in bold
    assert any("차트 「분기별 매출」" in t for t in texts) and not any('"type"' in t for t in texts)
    table = doc.tables[0]
    assert [c.text for c in table.rows[1].cells] == ["매출", "52억", "55억"]


@pytest.mark.skipif(not exports.available(), reason="python-docx 미설치")
def test_export_endpoint_permission_and_html(client_and_conn):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'r')", (brief,)).lastrowid
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type) VALUES (?, 'agent', 1, ?, 'html')",
        (task, "<html><body><h1>제안서</h1><p>핵심 제안</p><script>alert(1)</script></body></html>"),
    ).lastrowid
    conn.commit()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get(f"/api/deliverables/{did}/export.docx").status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    resp = client.get(f"/api/deliverables/{did}/export.docx")
    assert resp.status_code == 200 and resp.headers["content-type"].endswith("wordprocessingml.document")
    from docx import Document

    texts = [p.text for p in Document(io.BytesIO(resp.content)).paragraphs]
    assert "제안서" in texts and "핵심 제안" in texts and not any("alert" in t for t in texts)
