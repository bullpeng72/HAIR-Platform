"""계획 분담 단계 모드(2026-11-23, "P2 진행해줘" — P2-9, 사용자 결정 "새 모드 추가")."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from hair_platform import agent_evaluation, db, pipeline_runtime, plan_merge


def _graph():
    return pytest.importorskip("hair_platform.execution_graph")  # langgraph 선택 설치


def _conn(path=":memory:"):
    c = db.get_connection(path)
    db.init_schema(c)
    return c


def _plain_score(role_name, call_fn, task_type=None, **_kwargs):
    out = call_fn()
    return {"output": out, "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0}


def _fake_agents(calls, plan_text):
    def fake(conn_, agent_id, actor_id, prompt, provider=None, model=None, **kw):
        calls.append((agent_id, prompt, conn_, threading.get_ident()))
        if "[분담 계획]" in prompt:
            return plan_text
        if "[병합]" in prompt:
            return "병합된 최종 보고서"
        return f"agent{agent_id} 결과"
    return fake


LEAD = {"agent_id": 1, "role_name": "편집장", "instructions": ""}
WORKERS = [
    {"agent_id": 2, "role_name": "시장 조사가", "instructions": "시장 규모 조사"},
    {"agent_id": 3, "role_name": "재무 분석가", "instructions": ""},
]


def test_parse_plan_keeps_only_known_roles_once():
    text = ('계획입니다:\n[{"role": "재무 분석가", "task": "손익 분석"}, {"role": "모르는 역할", "task": "x"},'
            ' {"role": "재무 분석가", "task": "중복"}, {"role": "시장 조사가", "task": ""}, "문자열"]')
    assert plan_merge.parse_plan(text, ["시장 조사가", "재무 분석가"]) == [{"role": "재무 분석가", "task": "손익 분석"}]
    assert plan_merge.parse_plan("계획 없음", ["a"]) == []
    assert plan_merge.parse_plan("[{깨진 json]", ["a"]) == []


def test_run_plans_executes_workers_and_merges():
    conn = _conn()
    calls = []
    plan = '[{"role": "재무 분석가", "task": "손익 분석"}, {"role": "시장 조사가", "task": "경쟁사 조사"}]'
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, plan)), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score):
        r = plan_merge.run(conn, LEAD, WORKERS, "3분기 보고서", 9, expected_output="표 포함")
    assert [c[0] for c in calls] == [1, 3, 2, 1]  # 계획 → 계획 순서대로 분담 → 병합
    assert "손익 분석" in calls[1][1] and "3분기 보고서" in calls[1][1]
    assert "agent3 결과" in calls[3][1] and "agent2 결과" in calls[3][1] and "표 포함" in calls[3][1]
    assert r["final_output"] == "병합된 최종 보고서" and r["agents"][-1]["agent_id"] == 1
    assert r["plan_source"] == "lead_plan" and r["parallel"] is False  # 메모리 DB·로컬 모델 → 순서대로
    assert [d["to"] for d in r["delegations"]] == ["재무 분석가", "시장 조사가"]
    assert set(r) >= {"agents", "final_output", "gate_scores", "tcr", "delegations"}


def test_unreadable_plan_falls_back_to_stage_instructions():
    conn = _conn()
    calls = []
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, "그냥 제가 다 할게요")), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score):
        r = plan_merge.run(conn, LEAD, WORKERS, "요청", 9)
    assert r["plan_source"] == "stage_instructions"
    assert [c[0] for c in calls] == [1, 2, 3, 1]
    assert "시장 규모 조사" in calls[1][1]


def test_cloud_models_run_workers_concurrently_on_own_connections(tmp_path):
    conn = _conn(str(tmp_path / "pm.db"))
    calls = []
    plan = '[{"role": "시장 조사가", "task": "a"}, {"role": "재무 분석가", "task": "b"}]'
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, plan)), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score), \
            patch.object(plan_merge, "_is_cloud", return_value=True):
        r = plan_merge.run(conn, LEAD, WORKERS, "요청", 9)
    assert r["parallel"] is True
    worker_calls = calls[1:3]
    assert all(c[2] is not conn for c in worker_calls)  # 스레드마다 별도 연결
    assert [a["role_name"] for a in r["agents"][:2]] == ["시장 조사가", "재무 분석가"]  # 결과는 계획 순서
    assert r["final_output"] == "병합된 최종 보고서"


def _template_with_plan_merge(conn, admin):
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "pm", "", "general")
    pipeline_runtime.add_stage(
        conn, admin, tid, "분담 작성", "plan_merge",
        [("기획서 작성자", ""), ("문서 교정자", "문장 다듬기")],
        lead_role_name="기획서 작성자", is_primary_deliverable=True,
    )
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    return tid, task


def test_add_stage_validates_plan_merge_lead():
    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "pm_admin0", "pw", Role.ADMIN)
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "v", "", "general")
    with pytest.raises(ValueError, match="plan_merge_requires_lead_role_name"):
        pipeline_runtime.add_stage(conn, admin, tid, "s", "plan_merge", [("기획서 작성자", ""), ("문서 교정자", "")])
    with pytest.raises(ValueError, match="plan_merge_requires_at_least_one_coworker"):
        pipeline_runtime.add_stage(conn, admin, tid, "s", "plan_merge", [("기획서 작성자", "")], lead_role_name="기획서 작성자")


@pytest.mark.parametrize("engine", ["classic", "graph"])
def test_pipeline_engines_run_plan_merge_stage(engine):
    from hair_platform.auth import Role, create_user

    if engine == "graph":
        _graph()
    conn = _conn()
    admin = create_user(conn, "pm_admin_" + engine, "pw", Role.ADMIN)
    tid, task = _template_with_plan_merge(conn, admin)
    calls = []
    plan = '[{"role": "문서 교정자", "task": "맞춤법 교정"}]'
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, plan)), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score), \
            patch("hair_platform.crew_runtime.run_crew", side_effect=AssertionError("CrewAI 경로를 타면 안 된다")):
        if engine == "classic":
            assert task is not None
            run_id = pipeline_runtime.start_pipeline_run(conn, admin, tid, task)
            pipeline_runtime.run_pipeline_stage_job(conn, run_id, admin)
        else:
            execution_graph = _graph()
            execution_graph.validate_template_supported(conn, tid)
            assert task is not None
            run_id = pipeline_runtime.start_pipeline_run(conn, admin, tid, task)
            execution_graph.run_pipeline_graph_job(conn, run_id, admin)
    assert len(calls) == 3
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "completed"
    content = conn.execute("SELECT content FROM deliverables WHERE task_id = ? ORDER BY id DESC", (task,)).fetchone()[0]
    assert "병합된 최종 보고서" in content


def test_graph_engine_accepts_plan_merge_template():
    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "pm_admin_v", "pw", Role.ADMIN)
    tid, _task = _template_with_plan_merge(conn, admin)
    _graph().validate_template_supported(conn, tid)  # 예외 없음


def test_migration_widens_mode_check_and_keeps_rows(tmp_path):
    path = str(tmp_path / "old.db")
    conn = _conn(path)
    # 옛 DB에는 계획 분담 단계가 없었다 — 2026-11-25부터 기본 구성에 생긴 계획 분담 단계를 옛 모드로 돌려 둔다.
    conn.execute("UPDATE pipeline_stages SET mode = 'sequential_relay', lead_role_name = NULL WHERE mode = 'plan_merge'")
    conn.commit()
    before = conn.execute("SELECT COUNT(*), SUM(id) FROM pipeline_stages").fetchone()
    agents_before = conn.execute("SELECT COUNT(*) FROM pipeline_stage_agents").fetchone()[0]
    # 옛 CHECK로 되돌린 DB를 만든다
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'pipeline_stages'").fetchone()[0]
    old_sql = sql.replace(db._NEW_STAGE_MODE_CHECK, db._OLD_STAGE_MODE_CHECK).replace(
        "pipeline_stages", "ps_old", 1)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(f"BEGIN; {old_sql}; INSERT INTO ps_old SELECT * FROM pipeline_stages; DROP TABLE pipeline_stages;"
                       " ALTER TABLE ps_old RENAME TO pipeline_stages; COMMIT;")
    conn.execute("PRAGMA foreign_keys=ON")
    assert db._OLD_STAGE_MODE_CHECK in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'pipeline_stages'").fetchone()[0]
    conn.close()

    conn = _conn(path)  # init_schema가 마이그레이션 실행
    assert tuple(conn.execute("SELECT COUNT(*), SUM(id) FROM pipeline_stages").fetchone()) == tuple(before)
    assert conn.execute("SELECT COUNT(*) FROM pipeline_stage_agents").fetchone()[0] == agents_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    tpl = conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode) VALUES (?, 999, 'x', 'plan_merge')", (tpl,))
    db._migrate_pipeline_stage_plan_merge_mode(conn)  # 두 번 돌려도 아무 일 없음


def test_worker_skipped_by_lead_gets_default_subtask_and_badge():
    """2026-11-25 P2-9 실측 — 리드가 팀원 한 명에게 일을 주지 않고 그 몫을 직접 채웠다. P1-11부터는 빠진 팀원이
    단계 지시로 기본 하위 작업을 받아 실제로 기여하고, 배지에 "자동 배정"으로 남는다."""
    conn = _conn()
    calls = []
    plan = '[{"role": "재무 분석가", "task": "손익 분석"}]'  # 시장 조사가는 빠짐
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, plan)), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score):
        r = plan_merge.run(conn, LEAD, WORKERS, "요청", 9)
    cov = r["gate_scores"]["plan_coverage"]
    worker_prompts = {aid: p for aid, p, *_ in calls if aid in (2, 3)}
    assert set(worker_prompts) == {2, 3}  # 두 팀원 모두 실행됨
    assert "시장 규모 조사" in worker_prompts[2]  # 빠진 팀원은 단계 지시로 기본 하위 작업
    assert r["plan_unassigned"] == [] and cov["unassigned"] == []
    assert cov["auto_assigned"] == ["시장 조사가"] and cov["assigned"] == ["재무 분석가"]
    assert cov["status"] == "warn" and cov["advisory"] and cov["passed"] is True and cov["score"] == 0.5
    assert pipeline_runtime._gate_pass_ratio(r["gate_scores"]) == 1.0  # 참고용 — 통과 판정은 그대로

    calls.clear()
    full = '[{"role": "재무 분석가", "task": "a"}, {"role": "시장 조사가", "task": "b"}]'
    with patch("hair_platform.agent_runtime.execute_agent", side_effect=_fake_agents(calls, full)), \
            patch.object(agent_evaluation, "run_and_score", side_effect=_plain_score):
        r = plan_merge.run(conn, LEAD, WORKERS, "요청", 9)
    assert r["gate_scores"]["plan_coverage"]["status"] == "pass" and r["plan_unassigned"] == []
    assert r["gate_scores"]["plan_coverage"]["auto_assigned"] == []
