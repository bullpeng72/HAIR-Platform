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


def test_default_runtime_is_direct_and_crewai_keeps_same_arguments(conn, monkeypatch):
    """운영 기본은 direct(2026-11-26 실측으로 결정), crewai를 고르면 예전과 같은 인자로 run_crew를 부른다."""
    monkeypatch.delenv("HAIR_STAGE_RUNTIME", raising=False)
    assert stage_runner.runtime_for({"mode": "single"}) == "direct"
    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "crewai")
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
    assert stage_runner.runtime_for({"mode": "single"}) == "direct"  # 모르는 값이면 기본(direct)


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


def test_stall_watchdog_abandons_stuck_stage_and_allows_resume(conn, monkeypatch):
    """진척 없는 단계(실측: 도구 호출 뒤 잠금 대기로 21분 멈춤)를 정리한다 — 스택을 활동 기록에 남기고 "재시작으로
    중단됨"으로 표시(이어서 실행 가능), 뒤늦게 깨어난 시도는 결과를 쓰지 않는다."""
    import threading

    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    monkeypatch.setenv("HAIR_STAGE_STALL_SECONDS", "0.5")
    monkeypatch.setenv("HAIR_STAGE_WATCHDOG_POLL_SECONDS", "0.1")
    admin = create_user(conn, "wd_admin", "pw", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "stall", "", "general")
    pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    release = threading.Event()

    def stuck_agent(*a, **k):
        import time

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:  # 멈춘 척 — 감시가 정리한 뒤 풀어 준다
            row = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
            if row[0] != "running":
                break
            time.sleep(0.05)
        release.set()
        return "뒤늦은 결과"

    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=stuck_agent):
        pr.run_pipeline_stage_job(conn, run_id, admin)
    assert release.is_set()
    row = conn.execute("SELECT status, interrupted_at, progress FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed" and row["interrupted_at"] and "멈춤 감지" in row["progress"]
    assert conn.execute("SELECT COUNT(*) FROM pipeline_run_stages WHERE pipeline_run_id = ?", (run_id,)).fetchone()[0] == 0
    audit = conn.execute("SELECT after_value FROM audit_log WHERE action = 'pipeline_stage_stalled'").fetchone()
    assert audit is not None and "Thread" in audit[0]  # 모든 스레드 스택이 남는다
    # 이어서 실행 가능(P1-6과 같은 경로)
    assert pr.resume_interrupted_run(conn, admin, run_id) == "legacy"


def test_watchdog_does_not_fire_while_progress_keeps_coming(conn, monkeypatch):
    from hair_platform import progress

    monkeypatch.setenv("HAIR_STAGE_STALL_SECONDS", "0.4")
    monkeypatch.setenv("HAIR_STAGE_WATCHDOG_POLL_SECONDS", "0.05")
    admin = create_user(conn, "wd2_admin", "pw", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "busy", "", "general")
    pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")])
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE pipeline_template_id = ?", (tid,)).fetchone()
    import time

    with pr.stage_progress(conn, run_id, stage):  # 1초 동안 진행 문구가 계속 들어온다 — 멈춤 아님
        for _ in range(10):
            progress.report("생성 중")
            time.sleep(0.1)
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "running"


def test_graph_engine_job_marks_run_failed_on_error(conn, monkeypatch):
    """그래프 엔진 노드에서 예외가 나도 실행이 'running'으로 남지 않는다(예전 공백)."""
    pytest.importorskip("langgraph")
    from hair_platform import execution_graph

    monkeypatch.setenv("HAIR_STAGE_RUNTIME", "direct")
    admin = create_user(conn, "ge_admin", "pw", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "graph-fail", "", "general")
    pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=RuntimeError("모델 연결 실패")):
        assert execution_graph.run_pipeline_graph_job(conn, run_id, admin) is None
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "failed"
    err = conn.execute("SELECT after_value FROM audit_log WHERE action = 'pipeline_run_failed' ORDER BY id DESC").fetchone()
    assert "모델 연결 실패" in err[0]


def test_conditional_approval_catches_phrases_missed_in_measurement():
    t1 = "## 판정\n\n**승인(조건부)** — 실무 발송 직전 상태입니다. 단, 수신자 확인 후 발송하세요.\n"
    t2 = "## 판정\n**승인** — 요구사항을 충족합니다. 3개 개선사항은 반영하면 좋습니다.\n"
    assert pr.review_verdict(t1)["verdict"] == pr.review_verdict(t2)["verdict"] == "조건부 승인"


def test_plan_merge_stage_can_be_created_via_api_and_parallel_delegated_cannot(client_and_conn):
    """2026-11-26 — 예전엔 API 모델에 plan_merge가 빠져 화면에서 계획 분담 단계를 만들면 422였다(선택지는 있었음)."""
    client, _conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    tpl = client.post("/api/pipeline-templates", json={"name": "api-plan", "description": "", "category": "general"}).json()
    tid = tpl["id"] if isinstance(tpl, dict) else tpl
    body = {"name": "분담", "mode": "plan_merge", "agents": [{"role_name": "PM"}, {"role_name": "데이터 분석가"}],
            "lead_role_name": "PM"}
    r = client.post(f"/api/pipeline-templates/{tid}/stages", json=body)
    assert r.status_code in (200, 201), r.text
    r2 = client.post(f"/api/pipeline-templates/{tid}/stages", json={**body, "name": "위임", "mode": "parallel_delegated"})
    assert r2.status_code == 422
