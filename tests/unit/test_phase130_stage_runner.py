"""파이프라인 단계 실행기(2026-11-25, P1-7 1단계 — `stage_runner.py`)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, stage_runner
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _ids(conn, *roles):
    return [conn.execute("SELECT id FROM agent_pool WHERE role_name = ?", (r,)).fetchone()[0] for r in roles]


def test_default_runtime_is_crewai_with_same_arguments(conn, monkeypatch):
    monkeypatch.delenv("HAIR_STAGE_RUNTIME", raising=False)
    stage = {"mode": "single", "name": "작성"}
    with patch("hair_platform.crew_runtime.run_crew", return_value={"agents": []}) as crew:
        stage_runner.run_stage_agents(conn, stage, [1], ["요청"], 7, provider="ollama", model="m",
                                      expected_outputs=["보고서"], self_review=True)
    args, kwargs = crew.call_args
    assert args[1:] == ([1], ["요청"], 7)
    assert kwargs["memory"] is False and kwargs["provider"] == "ollama" and kwargs["expected_outputs"] == ["보고서"]
    assert kwargs["self_review"] is True


def test_direct_runtime_only_for_single_and_relay(monkeypatch):
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    assert stage_runner.runtime_for({"mode": "single"}) == "direct"
    assert stage_runner.runtime_for({"mode": "sequential_relay"}) == "direct"
    assert stage_runner.runtime_for({"mode": "parallel_delegated"}) == "crewai"  # hierarchical은 CrewAI가 필요
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "이상한값")
    assert stage_runner.runtime_for({"mode": "single"}) == "crewai"


def test_direct_relay_passes_previous_output_expected_format_and_tool_evidence(conn, monkeypatch):
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    a, b = _ids(conn, "외부 자료 조사가", "기획서 작성자")
    prompts = []

    def fake_execute(conn_, agent_id, actor_id, prompt, provider=None, model=None, *, retrieval_sink=None, **kw):
        prompts.append((agent_id, prompt))
        if retrieval_sink is not None and agent_id == a:
            retrieval_sink["tool_results"] = ['{"매출": 55}']
        return f"{agent_id}의 결과"

    with patch("hair_platform.agent_runtime.execute_agent", side_effect=fake_execute):
        result = stage_runner.run_stage_agents(
            conn, {"mode": "sequential_relay", "name": "조사·작성"}, [a, b], ["요청 A", "요청 B"], 7,
            expected_outputs=["표 1개 포함", "표 1개 포함"],
        )
    assert [p[0] for p in prompts] == [a, b]
    assert "[기대 산출물 형식] 표 1개 포함" in prompts[0][1]
    assert f"{a}의 결과" in prompts[1][1] and "[앞 참여자 결과" in prompts[1][1]  # 앞사람 결과를 이어받음
    assert [x["agent_id"] for x in result["agents"]] == [a, b]
    assert result["final_output"] == f"{b}의 결과" and result["runtime"] == "direct"
    assert '{"매출": 55}' in result["tool_evidence"]
    assert set(result["gate_scores"]) >= {"A", "G"}
    # 단계 산출물 조립(두 엔진 공통 함수)이 그대로 읽는다 — 순차 릴레이는 참여자 결과를 이어 붙인다
    stage_output, author = pr.assemble_stage_output({"mode": "sequential_relay", "is_primary_deliverable": 0},
                                                    result, b)
    assert f"{a}의 결과" in stage_output and f"{b}의 결과" in stage_output


def test_direct_retries_once_on_empty_output(conn, monkeypatch):
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    (a,) = _ids(conn, "기획서 작성자")
    outputs = iter(["", "다시 쓴 결과"])
    calls = []

    def fake_execute(conn_, agent_id, actor_id, prompt, provider=None, model=None, **kw):
        calls.append(prompt)
        return next(outputs)

    with patch("hair_platform.agent_runtime.execute_agent", side_effect=fake_execute):
        result = stage_runner.run_stage_agents(conn, {"mode": "single", "name": "작성"}, [a], ["요청"], 7)
    assert len(calls) == 2 and "[다시 작성]" in calls[1]
    assert result["final_output"] == "다시 쓴 결과"


def test_pipeline_runs_end_to_end_with_direct_runtime(conn, monkeypatch):
    """두 엔진 공통 실행기를 거쳐 기본 엔진 파이프라인이 직접 실행으로 끝까지 돈다(CrewAI를 부르지 않음)."""
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    admin = create_user(conn, "sr_admin", "pw", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "direct", "", "general")
    pr.add_stage(conn, admin, tid, "조사", "single", [("외부 자료 조사가", "")])
    pr.add_stage(conn, admin, tid, "작성", "sequential_relay", [("기획서 작성자", ""), ("문서 검토자", "")],
                 is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    with patch("hair_platform.agent_runtime.execute_agent", return_value="결과 본문"), \
            patch("hair_platform.crew_runtime.run_crew", side_effect=AssertionError("CrewAI를 부르면 안 됨")):
        run_id = pr.start_pipeline_run(conn, admin, tid, task)
        pr.run_pipeline_stage_job(conn, run_id, admin)
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "completed"
    assert conn.execute("SELECT COUNT(*) FROM pipeline_run_stages WHERE pipeline_run_id = ?", (run_id,)).fetchone()[0] == 2


def test_graph_engine_with_direct_runtime_via_api(client_and_conn, monkeypatch):
    """LangGraph 엔진도 같은 공통 실행기를 쓴다 — 실제 API(실행 시작 → 결재 승인으로 그래프 재개)로 끝까지."""
    pytest.importorskip("langgraph")
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    tid = pr.create_pipeline_template(conn, admin, "graph-direct", "", "general")
    pr.add_stage(conn, admin, tid, "정리", "single", [("업무 요구사항 분석가", "")], requires_human_approval=True)
    pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    with patch("hair_platform.agent_runtime.execute_agent", return_value="결과 본문"), \
            patch("hair_platform.crew_runtime.run_crew", side_effect=AssertionError("CrewAI를 부르면 안 됨")):
        r = client.post(f"/api/tasks/{task}/execute-pipeline", json={"pipeline_template_id": tid, "use_graph_engine": True})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        ap = conn.execute("SELECT a.id FROM approvals a JOIN pipeline_run_stages prs ON prs.id = a.subject_id "
                          "WHERE a.subject_type = 'pipeline_run_stage' AND a.status = 'pending' AND prs.pipeline_run_id = ?",
                          (run_id,)).fetchone()
        assert ap is not None  # 1단계 사람 승인에서 멈춤
        # 실행한 사람은 자기 결재를 승인할 수 없다(자기 승인 금지) — 다른 결재권자(manager)가 승인
        assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
        assert client.post(f"/api/approvals/{ap[0]}/decide", json={"approve": True}).status_code == 200
    row = conn.execute("SELECT status, engine FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert tuple(row) == ("completed", "graph")
