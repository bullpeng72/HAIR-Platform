"""Phase 68 — "사업/기획 업무 처리 파이프라인" 신설(사용자 요청, 2026-11-08,
"게임 개발이 아닌 일상 사무 업무를 포함한 다른 복잡한 업무 처리를 위해
일반화").

로드맵 ① 일반화(단계명과 무관한 `is_primary_deliverable`/`checks_
requirements_traceability` 플래그)를 실제로 비SW 도메인에 적용해본다 —
"소프트웨어 개발 파이프라인"과 완전히 같은 엔진·같은 구조(sequential_
relay 우선, 요구사항-섹션 매핑 계획, 참여자 전원 산출물 보존)를 재사용
하면서 "구현"/"코드 검증"이라는 이름을 전혀 안 쓴다. 새 Agent는 전혀
만들지 않는다 — `agent_pool`에 이미 있었지만 자동 파이프라인에 연결
안 돼 있던 역할(기획서 작성자/사업전략 수립가/C-level 보고서 작성자/
외부 자료 조사가/3C4P SWOT 분석가/프레젠테이션 자료 작성자/콘텐츠
카피라이팅/PM)만 재사용한다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import db, reference_pipeline_seeds
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '사업계획서', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 시드 구조 자체 확인 ----------


def test_business_planning_pipeline_is_seeded_alongside_dev_pipeline():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    templates = [t["name"] for t in pr.list_pipeline_templates(conn)]
    assert "소프트웨어 개발 파이프라인" in templates
    assert "사업/기획 업무 처리 파이프라인" in templates


def test_business_planning_pipeline_seeded_idempotently():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 재시드해도 중복 안 됨
    templates = [
        t
        for t in pr.list_pipeline_templates(conn)
        if t["name"] == "사업/기획 업무 처리 파이프라인"
    ]
    assert len(templates) == 1


def test_business_planning_pipeline_all_stages_have_at_least_one_resolvable_agent():
    """실측으로 발견한 버그 재발 방지 — "콘텐츠 카피라이팅"이 AGENT_SPECS
    에 없어 신규 설치 시 "표현 검토" 단계가 Agent 0명으로 생성됐었다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '사업/기획 업무 처리 파이프라인'"
    ).fetchone()
    full = pr.get_pipeline_template(conn, template["id"])
    assert len(full["stages"]) == 7
    for stage in full["stages"]:
        assert len(stage["agents"]) >= 1, f"{stage['name']} 단계에 Agent가 없음"


def test_business_planning_pipeline_research_stage_includes_data_analyst():
    """사용자 요청(2026-11-11, "데이터 분석... 개선 방안을 기획해줘" →
    "바로 구현해줘") — "자료 조사·분석" 단계가 정성 분석(3C4P/SWOT)만
    하고 실제 숫자 계산이 전혀 없던 공백을 "데이터 분석가" 추가로
    메운다. 외부 자료 조사가 → 데이터 분석가 → 3C4P SWOT 분석가 순서를
    확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '사업/기획 업무 처리 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND name = '자료 조사·분석'",
        (template["id"],),
    ).fetchone()
    agents = conn.execute(
        "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order",
        (stage["id"],),
    ).fetchall()
    assert [a["role_name"] for a in agents] == [
        "외부 자료 조사가",
        "데이터 분석가",
        "3C4P SWOT 분석가",
    ]


def test_business_planning_pipeline_uses_sequential_relay_for_writing_stage():
    """"구현" 단계와 같은 이유로 — hierarchical 위임의 신뢰성 문제를
    피하려고 "작성" 단계도 sequential_relay를 쓴다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '사업/기획 업무 처리 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT mode, is_primary_deliverable FROM pipeline_stages "
        "WHERE pipeline_template_id = ? AND name = '작성'",
        (template["id"],),
    ).fetchone()
    assert stage["mode"] == "sequential_relay"
    assert bool(stage["is_primary_deliverable"]) is True


def test_business_planning_pipeline_content_verification_flag_set_without_sw_names():
    """단계명이 "코드 검증"이 아니라 "내용 검증"인데도 요구사항 추적성
    플래그가 켜져 있어야 한다 — 로드맵 ① 일반화의 핵심 증명."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '사업/기획 업무 처리 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT checks_requirements_traceability FROM pipeline_stages "
        "WHERE pipeline_template_id = ? AND name = '내용 검증'",
        (template["id"],),
    ).fetchone()
    assert bool(stage["checks_requirements_traceability"]) is True


def test_business_planning_agents_are_all_pre_existing_no_new_agents_created():
    """새 Agent를 전혀 만들지 않는다는 설계 원칙 확인 — 파이프라인이
    참조하는 모든 role_name이 이미 agent_pool에 있어야 한다(방어적
    SELECT 건너뛰기로 조용히 빠지는 role이 없는지)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    expected_roles = {
        role_name
        for _, _, _, _, _, _, agents, _, _ in reference_pipeline_seeds.BUSINESS_PIPELINE_STAGES
        for role_name, _ in agents
    }
    existing_roles = {
        r["role_name"]
        for r in conn.execute("SELECT role_name FROM agent_pool").fetchall()
    }
    assert expected_roles <= existing_roles


# ---------- 전체 실행 통합(모킹) ----------


def test_business_planning_pipeline_runs_end_to_end_with_requirement_coverage():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '사업/기획 업무 처리 파이프라인'"
    ).fetchone()

    writing_agents = [
        {"agent_id": 1, "role_name": "기획서 작성자", "name": None, "output": "배경/목표 섹션"},
        {"agent_id": 2, "role_name": "사업전략 수립가", "name": None, "output": "사업전략 섹션"},
        {"agent_id": 3, "role_name": "C-level 보고서 작성자", "name": None, "output": "경영진 요약"},
        {"agent_id": 4, "role_name": "프레젠테이션 자료 작성자", "name": None, "output": "통합 최종본"},
    ]

    call_log: list[str] = []

    def _dispatch(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        # 단계별로 다른 결과를 주기 위해 참여 Agent 역할로 어느 단계인지
        # 구분한다(실제 run_crew를 안 타므로 process/agent_ids로 분기).
        roles = []
        for aid in agent_ids:
            row = conn.execute(
                "SELECT role_name FROM agent_pool WHERE id = ?", (aid,)
            ).fetchone()
            roles.append(row["role_name"] if row else str(aid))
        call_log.append(",".join(roles))
        if "기획서 작성자" in roles:
            return {
                "final_output": "fallback",
                "agents": writing_agents,
                "gate_scores": {"A": {"score": 0.9, "passed": True}},
                "tcr": 90.0,
                "delegations": [],
                "resolved_provider": "ollama",
                "resolved_model": "x",
                "token_usage": {"total_tokens": 1},
            }
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
        "total_requirements": 3,
        "covered": 3,
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
        "SELECT ps.name, prs.gate_scores, prs.output FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? ORDER BY prs.id",
        (run_id,),
    ).fetchall()
    assert [s["name"] for s in stages] == [
        "요구사항 분석/계획 확정",
        "자료 조사·분석",
        "기획 계획 수립",
        "작성",
        "내용 검증",
        "표현 검토",
        "최종 승인 판정",
    ]

    writing_stage = stages[3]
    assert "배경/목표 섹션" in writing_stage["output"]
    assert "사업전략 섹션" in writing_stage["output"]
    assert "경영진 요약" in writing_stage["output"]
    assert "통합 최종본" in writing_stage["output"]

    import json

    verification_gate = json.loads(stages[4]["gate_scores"])
    assert verification_gate["requirements_traceability"]["covered"] == 3
    assert verification_gate["requirements_traceability"]["not_measured"] is True
