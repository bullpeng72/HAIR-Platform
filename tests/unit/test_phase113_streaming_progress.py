"""응답 스트리밍·파이프라인 진행률(2026-11-24, P0-2 — "go ahead").

- 작업(대화·워크스페이스) 안의 주 응답 생성은 provider 스트리밍으로 받아 지금까지의 글을 작업 행
  `partial_output`에 남기고(화면은 기존 폴링으로 미리보기), 끝나면 비운다.
- 교정 같은 보조 호출은 스트리밍하지 않는다(미리보기가 교정 JSON으로 덮이지 않게).
- 파이프라인 단계는 실행 행에 단계 시작 시각·지금 하는 일(CrewAI 작업·도구 이벤트)을 남긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from hair_platform import db, llm, pipeline_runtime, progress


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _conversation_job(conn):
    from hair_platform.auth import Role, create_user

    uid = create_user(conn, "stream_user", "pw", Role.ADMIN)
    conv = conn.execute("INSERT INTO conversations (created_by, title) VALUES (?, 't')", (uid,)).lastrowid
    job = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type, status) VALUES (?, 'auto_reply', 'running')",
        (conv,),
    ).lastrowid
    conn.commit()
    return job


def test_partial_sink_only_inside_job_and_cleared_after(conn, monkeypatch):
    monkeypatch.setattr(progress, "PARTIAL_WRITE_INTERVAL_SECONDS", 0)
    job = _conversation_job(conn)
    assert progress.partial_sink() is None
    with progress.reporting(conn, "conversation_jobs", job):
        sink = progress.partial_sink()
        sink("1. 분기별 목표")
        row = conn.execute("SELECT partial_output FROM conversation_jobs WHERE id = ?", (job,)).fetchone()
        assert row["partial_output"] == "1. 분기별 목표"
    assert conn.execute("SELECT partial_output FROM conversation_jobs WHERE id = ?", (job,)).fetchone()[0] is None
    assert progress.partial_sink() is None


def test_partial_writes_are_throttled(conn):
    job = _conversation_job(conn)
    writes = []
    with progress.reporting(conn, "conversation_jobs", job):
        sink = progress.partial_sink()
        for i in range(50):
            sink("x" * i)
        writes.append(conn.execute("SELECT partial_output FROM conversation_jobs WHERE id = ?", (job,)).fetchone()[0])
    assert writes[0] in ("", None) or len(writes[0]) < 50  # 간격 안의 연속 호출은 대부분 건너뛴다


class _FakeStream:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self._lines


def test_ollama_streaming_collects_content_and_reports_thinking(monkeypatch):
    lines = [json.dumps({"message": {"thinking": "음, 먼저"}}),
             json.dumps({"message": {"content": "1. 목표"}}),
             json.dumps({"message": {"content": "를 세운다."}}),
             json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 10, "eval_count": 5})]
    monkeypatch.setattr("httpx.stream", lambda *a, **k: _FakeStream(lines))
    reports, partials = [], []
    token = progress._REPORTER.set(reports.append)
    try:
        out = llm.call_llm("ollama", "m", "s", "p", on_partial=partials.append)
    finally:
        progress._REPORTER.reset(token)
    assert out == "1. 목표를 세운다."
    assert partials == ["1. 목표", "1. 목표를 세운다."]
    assert any("생각 중" in r for r in reports)


def test_without_on_partial_call_is_unchanged(monkeypatch):
    called = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "답"}}

    def fake_post(*a, **k):
        called["stream"] = k["json"]["stream"]
        return _Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    assert llm.call_llm("ollama", "m", "s", "p") == "답" and called["stream"] is False


def test_agent_main_call_streams_only_inside_a_job(conn, monkeypatch):
    from hair_platform import agent_runtime
    from hair_platform.auth import Role, create_user

    monkeypatch.setattr(progress, "PARTIAL_WRITE_INTERVAL_SECONDS", 0)
    uid = create_user(conn, "stream_admin", "pw", Role.ADMIN)
    aid = conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('보고서 Agent', '[]', 'active')").lastrowid
    conn.commit()
    seen = []

    def fake_call(provider, model, system, prompt, **kw):
        seen.append("on_partial" in kw)
        if "on_partial" in kw:
            kw["on_partial"]("작성 중인 글")
        return "완성된 보고서 본문이다."

    with patch("hair_platform.llm.call_llm", side_effect=fake_call):
        agent_runtime.execute_agent(conn, aid, uid, "보고서", provider="ollama", model="m")
        job = _conversation_job_for(conn, uid)
        with progress.reporting(conn, "conversation_jobs", job):
            agent_runtime.execute_agent(conn, aid, uid, "보고서", provider="ollama", model="m")
            mid = conn.execute("SELECT partial_output FROM conversation_jobs WHERE id = ?", (job,)).fetchone()[0]
    assert seen == [False, True]
    assert mid == "작성 중인 글"


def _conversation_job_for(conn, uid):
    conv = conn.execute("INSERT INTO conversations (created_by, title) VALUES (?, 't2')", (uid,)).lastrowid
    job = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type, status) VALUES (?, 'agent_mention', 'running')",
        (conv,),
    ).lastrowid
    conn.commit()
    return job


def _pipeline(conn):
    from hair_platform.auth import Role, create_user

    admin = create_user(conn, "pp_admin", "pw", Role.ADMIN)
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "진행률 템플릿", "", "general")
    pipeline_runtime.add_stage(conn, admin, tid, "자료 조사", "single", [("업무 요구사항 분석가", "")])
    pipeline_runtime.add_stage(conn, admin, tid, "작성", "single", [("문서 교정자", "")])
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    run_id = pipeline_runtime.start_pipeline_run(conn, admin, tid, task)
    return admin, task, run_id


def test_stage_progress_and_crew_events_are_recorded(conn):
    pytest.importorskip("crewai")
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.task_events import TaskStartedEvent
    from crewai.events.types.tool_usage_events import ToolUsageStartedEvent

    from hair_platform import crew_runtime

    admin, task, run_id = _pipeline(conn)
    stage = conn.execute("SELECT * FROM pipeline_stages ORDER BY id DESC LIMIT 1").fetchone()
    agent_id = conn.execute("SELECT id FROM agent_pool WHERE role_name = '문서 교정자'").fetchone()[0]
    snapshots = []

    def _kickoff(self, *args, **kwargs):
        from crewai.crews.crew_output import CrewOutput
        from crewai.tasks.task_output import TaskOutput

        crewai_event_bus.emit(object(), TaskStartedEvent(context="", task=None, agent_role="문서 교정자"))
        crewai_event_bus.flush()  # 처리기는 실행기 스레드에서 돈다 — 끝날 때까지 기다린다
        snapshots.append(conn.execute("SELECT progress FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0])
        crewai_event_bus.emit(object(), ToolUsageStartedEvent(tool_name="재무 지표 계산기", tool_args={}, agent_role="문서 교정자"))
        crewai_event_bus.flush()
        snapshots.append(conn.execute("SELECT progress FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0])
        return CrewOutput(raw="결과", tasks_output=[TaskOutput(description="d", agent="문서 교정자", raw="결과")])

    with patch("crewai.Crew.kickoff", new=_kickoff), pipeline_runtime.stage_progress(conn, run_id, stage):
        started = conn.execute("SELECT stage_started_at FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0]
        crew_runtime.run_crew(conn, [agent_id], ["교정"], admin, provider="openai")
    assert started is not None
    assert snapshots[0] == "문서 교정자 작업 중"
    assert "재무 지표 계산기" in snapshots[1]


def test_run_listing_exposes_progress_fields(conn):
    admin, task, run_id = _pipeline(conn)
    conn.execute("UPDATE pipeline_runs SET progress = '문서 교정자 작업 중', stage_started_at = ? WHERE id = ?",
                 (datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), run_id))
    conn.commit()
    run = pipeline_runtime.list_pipeline_runs_for_task(conn, task)[0]
    assert run["total_stages"] == 2 and run["current_stage_index"] == 1
    assert run["current_stage_name"] == "자료 조사" and run["progress"] == "문서 교정자 작업 중"
    assert run["stage_started_at"] is not None and "partial_output" in run
