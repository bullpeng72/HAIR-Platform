"""실행 중 단계 표시(2026-11-23, "P0부터 개선 진행해줘" — P0-2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hair_platform import agent_runtime, db, progress, tool_calling


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def test_report_outside_a_job_does_nothing():
    progress.report("아무 데도 기록되지 않음")  # 예외 없이 지나간다


def test_reporting_updates_the_job_row():
    from hair_platform.auth import Role, create_user

    conn = _conn()
    user = create_user(conn, "pg_user", "pw", Role.MEMBER)
    conv = conn.execute("INSERT INTO conversations (title, created_by) VALUES ('t', ?)", (user,)).lastrowid
    job = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type) VALUES (?, 'auto_reply')", (conv,)
    ).lastrowid
    with progress.reporting(conn, "conversation_jobs", job):
        progress.report("자료 조회 중")
    row = conn.execute("SELECT progress, progress_at FROM conversation_jobs WHERE id = ?", (job,)).fetchone()
    assert row["progress"] == "자료 조회 중" and row["progress_at"]
    with pytest.raises(ValueError):
        with progress.reporting(conn, "users", 1):
            pass


def test_tool_calls_report_the_registered_tool_name():
    seen: list[str] = []
    token = progress._REPORTER.set(seen.append)
    try:
        progress.TOOL_LABELS["custom_tool_9"] = "수치 데이터 분석기"
        tool_calling.run_tool({"custom_tool_9": SimpleNamespace(run=lambda **k: "ok")}, "custom_tool_9", {})
    finally:
        progress._REPORTER.reset(token)
    assert seen == ["도구 실행 중: 수치 데이터 분석기"]


def test_execute_agent_reports_its_steps(monkeypatch):
    from hair_platform.auth import Role, create_user

    conn = _conn()
    actor = create_user(conn, "pg_admin", "pw", Role.ADMIN)
    agent_id = conn.execute("SELECT id FROM agent_pool WHERE role_name = '문서 교정자'").fetchone()[0]
    monkeypatch.setattr(agent_runtime.llm, "resolve_provider_and_model", lambda p, m: ("ollama", "small-model"))
    monkeypatch.setattr(agent_runtime.llm, "call_llm", lambda *a, **k: "## 수정본\n완료했습니다.")
    seen: list[str] = []
    token = progress._REPORTER.set(seen.append)
    try:
        agent_runtime.execute_agent(conn, agent_id, actor, "이 문장을 교정해줘")
    finally:
        progress._REPORTER.reset(token)
    assert seen[:2] == ["자료 조회 중", "응답 생성 중 — small-model"]
