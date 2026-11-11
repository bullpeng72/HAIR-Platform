"""Phase 70 — "제안서 작성 파이프라인"/"실적 보고 파이프라인" 신설 +
"수치 데이터 분석기" Tool(사용자 요청, 2026-11-11, "기획, 전략, 자료조사,
보고서 작성, 데이터 분석, 제안서 작성, 실적서 작성 관련 내용들을
중심으로... 대화창 밑 업무 요청으로 처리할 수 있도록 개선 방안을
기획해줘" → "바로 구현해줘").

조사 결과 데이터 분석/제안서 작성/실적서 작성 3개는 Skill/Agent/Tool
어디에도 전혀 없었다(전수 검색 0건, pandas/numpy 등 수치 계산 라이브러리
자체도 안 쓰임). 제안서/실적서는 기존 "사업/기획 업무 처리 파이프라인"의
"작성" 단계(이미 4명)에 끼워 넣지 않고(브리프마다 필요 없는 문서까지
매번 생기는 낭비 방지, 사용자 확인) 독립 파이프라인으로 분리했다.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import db, pool_registry, reference_pipeline_seeds
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _make_task(conn, role_name="제안서"):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, ?, '[]')",
        (brief_id, role_name),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 수치 데이터 분석기(Tool) ----------


def test_data_analyzer_tool_computes_sum_average_stdev_for_simple_values():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '수치 데이터 분석기'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"], json.dumps({"values": [10, 20, 30]}), config["timeout_seconds"]
    )
    parsed = json.loads(out)
    assert parsed["sum"] == 60.0
    assert parsed["average"] == 20.0
    assert parsed["min"] == 10.0
    assert parsed["max"] == 30.0
    assert parsed["stdev"] is not None


def test_data_analyzer_tool_stdev_is_none_without_enough_data():
    """실측 설계 원칙 확인 — 값이 1개뿐이면 표준편차를 지어내지 않고
    명시적으로 None을 돌려준다(Gate A-G의 "정직한 not_measured" 원칙과
    동일)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '수치 데이터 분석기'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"], json.dumps({"values": [42]}), config["timeout_seconds"]
    )
    parsed = json.loads(out)
    assert parsed["stdev"] is None


def test_data_analyzer_tool_computes_growth_rate_for_series():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '수치 데이터 분석기'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps({"series": {"2024": 100, "2025": 130}}),
        config["timeout_seconds"],
    )
    parsed = json.loads(out)
    assert parsed["growth_rates"] == [
        {"from": "2024", "to": "2025", "growth_rate_pct": 30.0}
    ]


def test_data_analyzer_tool_growth_rate_none_when_baseline_is_zero():
    """기준값이 0이면 증감률을 계산하지 않고(ZeroDivisionError도 아니고
    추측값도 아닌) 명시적으로 None+사유를 돌려준다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '수치 데이터 분석기'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"],
        json.dumps({"series": {"2024": 0, "2025": 50}}),
        config["timeout_seconds"],
    )
    parsed = json.loads(out)
    assert parsed["growth_rates"][0]["growth_rate_pct"] is None
    assert "note" in parsed["growth_rates"][0]


def test_data_analyzer_tool_rejects_invalid_json():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '수치 데이터 분석기'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"], "이건 JSON이 아닙니다", config["timeout_seconds"]
    )
    assert "JSON" in out


# ---------- 신규 Skill/Agent 등록 확인 ----------


def test_new_business_document_agents_are_seeded():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    for role_name in ("데이터 분석가", "제안서 작성자", "실적 보고서 작성자"):
        row = conn.execute(
            "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
        ).fetchone()
        assert row is not None, f"{role_name} 미생성"


def test_data_analysis_guide_skill_is_tool_mode_with_resolvable_tool():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT augmentation_mode, default_tool_pool_ids FROM skill_pool "
        "WHERE name = '데이터분석가이드'"
    ).fetchone()
    assert row["augmentation_mode"] == "tool"
    tool_ids = json.loads(row["default_tool_pool_ids"])
    assert len(tool_ids) == 1
    tool_row = conn.execute(
        "SELECT name, kind FROM tool_pool WHERE id = ?", (tool_ids[0],)
    ).fetchone()
    assert tool_row["name"] == "수치 데이터 분석기"
    assert tool_row["kind"] == "custom_python"


# ---------- 제안서 작성 파이프라인 ----------


def test_proposal_pipeline_is_seeded_idempotently():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 재시드해도 중복 안 됨
    templates = [
        t for t in pr.list_pipeline_templates(conn) if t["name"] == "제안서 작성 파이프라인"
    ]
    assert len(templates) == 1


def test_proposal_pipeline_all_stages_have_at_least_one_resolvable_agent():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '제안서 작성 파이프라인'"
    ).fetchone()
    full = pr.get_pipeline_template(conn, template["id"])
    assert len(full["stages"]) == 6
    for stage in full["stages"]:
        assert len(stage["agents"]) >= 1, f"{stage['name']} 단계에 Agent가 없음"


def test_proposal_pipeline_agents_are_all_pre_existing():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    expected_roles = {
        role_name
        for _, _, _, _, _, _, agents, _, _ in reference_pipeline_seeds.PROPOSAL_PIPELINE_STAGES
        for role_name, _ in agents
    }
    existing_roles = {
        r["role_name"] for r in conn.execute("SELECT role_name FROM agent_pool").fetchall()
    }
    assert expected_roles <= existing_roles


def test_proposal_pipeline_runs_end_to_end():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '제안서 작성 파이프라인'"
    ).fetchone()

    def _dispatch(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        roles = []
        for aid in agent_ids:
            row = conn.execute(
                "SELECT role_name FROM agent_pool WHERE id = ?", (aid,)
            ).fetchone()
            roles.append(row["role_name"] if row else str(aid))
        single_agent = [
            {
                "agent_id": agent_ids[0],
                "role_name": roles[0],
                "name": None,
                "output": f"{roles[0]} 산출물",
            }
        ]
        return {
            "final_output": "fallback",
            "agents": single_agent,
            "gate_scores": {"A": {"score": 0.9, "passed": True}},
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "ollama",
            "resolved_model": "x",
            "token_usage": {"total_tokens": 1},
        }

    traceability_result = {
        "score": 0.9,
        "status": "pass",
        "not_measured": False,
        "passed": True,
        "total_requirements": 2,
        "covered": 2,
        "missing": [],
    }

    with (
        patch("hair_platform.crew_runtime.run_crew", side_effect=_dispatch),
        patch(
            "hair_platform.requirements_traceability_llm.assess_traceability",
            return_value=dict(traceability_result),
        ),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, template["id"], task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)  # 1단계 실행 → 승인 대기
        pending_approval = conn.execute(
            "SELECT subject_id FROM approvals WHERE subject_type = 'pipeline_run_stage' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        pr.resolve_stage_decision(conn, admin_id, pending_approval["subject_id"], True)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"

    stages = conn.execute(
        "SELECT ps.name FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? ORDER BY prs.id",
        (run_id,),
    ).fetchall()
    assert [s["name"] for s in stages] == [
        "요구사항 분석/계획 확정",
        "자료 조사·분석",
        "작성",
        "내용 검증",
        "표현 검토",
        "최종 승인 판정",
    ]


# ---------- 실적 보고 파이프라인 ----------


def test_performance_report_pipeline_is_seeded_idempotently():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)
    templates = [
        t for t in pr.list_pipeline_templates(conn) if t["name"] == "실적 보고 파이프라인"
    ]
    assert len(templates) == 1


def test_performance_report_pipeline_all_stages_have_at_least_one_resolvable_agent():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '실적 보고 파이프라인'"
    ).fetchone()
    full = pr.get_pipeline_template(conn, template["id"])
    assert len(full["stages"]) == 4
    for stage in full["stages"]:
        assert len(stage["agents"]) >= 1, f"{stage['name']} 단계에 Agent가 없음"


def test_performance_report_pipeline_final_stage_checks_traceability():
    """표현 검토 단계가 없는 더 짧은 파이프라인이라 "최종 승인 판정"
    단계 하나에 검증+추적성 플래그가 함께 있어야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '실적 보고 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT checks_requirements_traceability, min_gate_pass_ratio FROM pipeline_stages "
        "WHERE pipeline_template_id = ? AND name = '최종 승인 판정'",
        (template["id"],),
    ).fetchone()
    assert bool(stage["checks_requirements_traceability"]) is True
    assert stage["min_gate_pass_ratio"] == 0.5


def test_performance_report_pipeline_runs_end_to_end():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn, role_name="실적서")
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '실적 보고 파이프라인'"
    ).fetchone()

    def _dispatch(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        row = conn.execute(
            "SELECT role_name FROM agent_pool WHERE id = ?", (agent_ids[0],)
        ).fetchone()
        role_name = row["role_name"] if row else str(agent_ids[0])
        return {
            "final_output": "fallback",
            "agents": [
                {
                    "agent_id": agent_ids[0],
                    "role_name": role_name,
                    "name": None,
                    "output": f"{role_name} 산출물",
                }
            ],
            "gate_scores": {"A": {"score": 0.9, "passed": True}},
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "ollama",
            "resolved_model": "x",
            "token_usage": {"total_tokens": 1},
        }

    traceability_result = {
        "score": 0.9,
        "status": "pass",
        "not_measured": False,
        "passed": True,
        "total_requirements": 1,
        "covered": 1,
        "missing": [],
    }

    with (
        patch("hair_platform.crew_runtime.run_crew", side_effect=_dispatch),
        patch(
            "hair_platform.requirements_traceability_llm.assess_traceability",
            return_value=dict(traceability_result),
        ),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, template["id"], task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)
        pending_approval = conn.execute(
            "SELECT subject_id FROM approvals WHERE subject_type = 'pipeline_run_stage' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        pr.resolve_stage_decision(conn, admin_id, pending_approval["subject_id"], True)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"

    stages = conn.execute(
        "SELECT ps.name FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? ORDER BY prs.id",
        (run_id,),
    ).fetchall()
    assert [s["name"] for s in stages] == [
        "요구사항 분석/계획 확정",
        "데이터 분석",
        "작성",
        "최종 승인 판정",
    ]
