"""도구 사용 기록(2026-11-26, 개선 계획 1-1 — `tool_audit.py`)."""

from __future__ import annotations

import threading

from hair_platform import llm_usage, pipeline_runtime, progress, tool_audit, tool_calling


class FakeTool:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.result


def _seed_run(conn):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '요청')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '채용 담당자')", (brief,)).lastrowid
    tpl = conn.execute("SELECT id FROM pipeline_templates WHERE name = '채용 공고·면접 설계 파이프라인'").fetchone()[0]
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = 2", (tpl,)).fetchone()
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) VALUES (?, ?, ?, 'running')",
                       (tpl, task, admin)).lastrowid
    conn.commit()
    return admin, task, run, stage


def test_run_tool_records_call_with_run_stage_attempt_and_job_context(client_and_conn):
    _client, conn = client_and_conn
    llm_usage.configure(conn)
    admin, task, run, stage = _seed_run(conn)
    tools = {"posting_check": FakeTool(result='{"판정": "차별 소지 문구 없음"}' + "x" * 2000)}
    with llm_usage.scope(task_id=task, pipeline_run_id=run, agent_id=7), \
            pipeline_runtime.stage_progress(conn, run, stage, 2):
        assert progress.current_job() == ("pipeline_runs", run)
        tool_calling.run_tool(tools, "posting_check", {"query": "공고 본문"})
        tool_calling.run_tool(tools, "missing_tool", {})
    rows = tool_audit.for_stage_attempt(conn, run, stage["id"], 2)
    assert [r["tool_name"] for r in rows] == ["posting_check", "missing_tool"]
    assert rows[0]["ok"] == 1 and rows[1]["ok"] == 0
    assert rows[0]["args_preview"] == '{"query": "공고 본문"}'
    assert len(rows[0]["result_preview"]) == tool_audit.PREVIEW_CHARS + 1  # 앞부분만 + …
    full = conn.execute("SELECT task_id, agent_id, job_table, job_id FROM tool_calls WHERE id = ?", (rows[0]["id"],)).fetchone()
    assert tuple(full) == (task, 7, "pipeline_runs", run)
    assert tool_audit.summary(rows) == [{"tool": "posting_check", "calls": 1, "failed": 0},
                                        {"tool": "missing_tool", "calls": 1, "failed": 1}]
    assert tool_audit.for_stage_attempt(conn, run, stage["id"], 1) == []  # 다른 시도와 섞이지 않는다


def test_tool_exception_is_recorded_as_failure_and_context_crosses_threads(client_and_conn):
    _client, conn = client_and_conn
    llm_usage.configure(conn)
    _admin, task, run, stage = _seed_run(conn)
    with llm_usage.scope(task_id=task, pipeline_run_id=run), tool_audit.stage(stage["id"], 1):
        tool_calling.run_tool({"boom": FakeTool(exc=RuntimeError("시간 초과"))}, "boom", {})
        captured = tool_audit.capture()
    # CrewAI 이벤트 처리기처럼 다른 스레드에서 — 잡아 둔 문맥으로 기록
    t = threading.Thread(target=tool_audit.record, args=("crew_tool", "계약 조항 점검기", {"text": "…"}, "결과", 15),
                         kwargs={"ok": True, "ctx": captured})
    t.start()
    t.join()
    rows = tool_audit.for_stage_attempt(conn, run, stage["id"], 1)
    assert [(r["tool_name"], r["ok"]) for r in rows] == [("boom", 0), ("crew_tool", 1)]
    assert "시간 초과" in rows[0]["result_preview"] and rows[1]["tool_label"] == "계약 조항 점검기"


def test_stage_results_show_tools_and_api_respects_workspace_permission(client_and_conn):
    client, conn = client_and_conn
    llm_usage.configure(conn)
    _admin, task, run, stage = _seed_run(conn)
    with llm_usage.scope(pipeline_run_id=run), tool_audit.stage(stage["id"], 1):
        tool_calling.run_tool({"t": FakeTool(result="ok")}, "t", {})
        tool_calling.run_tool({"t": FakeTool(result="ok")}, "t", {})
    conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
                 "VALUES (?, ?, 1, 'passed', '산출물')", (run, stage["id"]))
    conn.commit()
    runs = pipeline_runtime.list_pipeline_runs_for_task(conn, task)
    assert runs[0]["stage_results"][0]["tools"] == [{"tool": "t", "calls": 2, "failed": 0}]
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.get(f"/api/pipeline-runs/{run}/tool-calls").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    body = client.get(f"/api/pipeline-runs/{run}/tool-calls").json()
    assert len(body) == 2 and body[0]["stage_attempt"] == 1
    assert client.get("/api/pipeline-runs/999999/tool-calls").status_code == 404


def test_no_configured_connection_means_no_record_and_no_error():
    previous = llm_usage._CONN
    llm_usage.configure(None)
    try:
        assert tool_calling.run_tool({"t": FakeTool(result="ok")}, "t", {}) == "ok"
    finally:
        llm_usage.configure(previous)
