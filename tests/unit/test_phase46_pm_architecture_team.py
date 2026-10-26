"""Phase 46 — 기획→개발 실행 팀 구성, 사용자 요청(2026-10-14, "기획 내용을
받아 요구사항을 분석하고 스펙을 정리해 원하는 개발을 진행할 수 있도록...
PM과 AA, DA 등 아키텍터, 요구사항 분석, 설계, 개발 계획 수립(모듈을 나누고
개발자 Agent별 업무 분배), 복수의 개발자, UI기획, 테스트 계획 수립 및
테스트 케이스 작성, 기능 테스트, 품질 관리... 부족한 에이전트를
생성해줘").

요구사항 분석가·스펙 작성자·개발자·코드 검증가·QA 기능테스터(Phase 41/42의
Harness 파이프라인)는 이미 있어 재사용하고, 그 사이·앞뒤로 빠져 있던
PM/AA(애플리케이션 아키텍트)/DA(데이터 아키텍트)/개발 계획 수립가/
프론트엔드 개발자(복수 개발자 요건)/UI 기획자/테스트 계획 수립가/
품질 관리자 8개만 새로 만든다. `reference_pipeline_seeds.py`에
코드 레벨로 심어 기본 배포에 포함시킨다(2026-10-14, "기본 배포에 코드 및
Skill, Tool, Agent를 포함해줘" 선례를 그대로 따름)."""

from __future__ import annotations

import json

from hair_platform import db, pool_registry

_NEW_ROLE_NAMES = {
    "PM",
    "AA",
    "DA",
    "개발 계획 수립가",
    "프론트엔드 개발자",
    "UI 기획자",
    "테스트 계획 수립가",
    "품질 관리자",
}

_REUSED_ROLE_NAMES = {
    "요구사항 분석가",
    "스펙 작성자",
    "백엔드 개발자",
    "코드 검증가",
    "QA 기능테스터",
}


def test_gc_new_pm_architecture_team_agents_are_seeded():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    role_names = {
        r["role_name"]
        for r in conn.execute("SELECT role_name FROM agent_pool").fetchall()
    }
    assert _NEW_ROLE_NAMES <= role_names
    assert _REUSED_ROLE_NAMES <= role_names  # 기존 Harness 파이프라인 Agent 재사용 확인


def test_gc_new_agents_have_active_status_and_crewai_framework():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    for role_name in _NEW_ROLE_NAMES:
        row = conn.execute(
            "SELECT status, framework, skill_tags FROM agent_pool WHERE role_name = ?",
            (role_name,),
        ).fetchone()
        assert row is not None, f"{role_name} not seeded"
        assert row["status"] == "active"
        assert row["framework"] == "crewai"
        skills = json.loads(row["skill_tags"])
        assert len(skills) > 0


def test_gc_module_decomposition_skill_matches_dev_plan_agent():
    """개발 계획 수립가가 "설계에 맞춰 개발 모듈을 나누고 각 모듈별
    개발자 Agent에 업무 분배"하는 지식을 실제로 보유하는지 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '개발 계획 수립가'"
    ).fetchone()
    assert "module-decomposition-and-task-assignment" in json.loads(row["skill_tags"])

    skill = conn.execute(
        "SELECT description FROM skill_pool WHERE name = 'module-decomposition-and-task-assignment'"
    ).fetchone()
    assert "모듈" in skill["description"]
    assert "배분" in skill["description"] or "배정" in skill["description"]


def test_gc_two_developer_agents_exist_for_module_split_work():
    """사용자가 명시한 "복수의 개발자" 요건 — 기존 "개발자"(2026-10-26,
    역할이 드러나도록 "백엔드 개발자"로 개명) + 신규 "프론트엔드 개발자"로
    최소 2개 이상이어야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    dev_roles = [
        r["role_name"]
        for r in conn.execute(
            "SELECT role_name FROM agent_pool WHERE role_name LIKE '%개발자%'"
        ).fetchall()
    ]
    assert len(dev_roles) >= 2
    assert "백엔드 개발자" in dev_roles
    assert "프론트엔드 개발자" in dev_roles


def test_gc_test_case_mechanical_derivation_tool_executes_via_sandbox():
    """테스트 계획 수립가 전용 신규 Tool — EARS 요구사항에서 positive/
    negative/boundary 테스트 케이스 골격을 실제 샌드박스 경로로 기계적
    도출하는지 확인한다(DB에 저장된 소스를 그대로 실행)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    row = conn.execute(
        "SELECT * FROM tool_pool WHERE name = '테스트 케이스 기계적 도출'"
    ).fetchone()
    assert row is not None
    config = json.loads(row["config"])

    query = (
        'WHEN 사용자가 "시작" 버튼을 클릭할 때, THE SYSTEM SHALL 게임을 시작한다.\n'
        "일반 문장(요구사항 아님)\n"
    )
    out = pool_registry._run_custom_python_tool(
        config["function_source"], query, config["timeout_seconds"]
    )
    parsed = json.loads(out)
    assert parsed["derived_test_case_count"] == 1
    case = parsed["test_cases"][0]
    assert "positive_case" in case
    assert "negative_case" in case
    assert "boundary_case" in case


def test_gc_test_case_mechanical_derivation_tool_handles_no_requirements():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    row = conn.execute(
        "SELECT config FROM tool_pool WHERE name = '테스트 케이스 기계적 도출'"
    ).fetchone()
    config = json.loads(row["config"])
    out = pool_registry._run_custom_python_tool(
        config["function_source"], "요구사항이 전혀 없는 평문입니다.", config["timeout_seconds"]
    )
    assert "EARS" in out


def test_gc_pm_tracks_delivery_stages_not_hitl_skipping():
    """PM이 human-approval-loop를 보유해 단계 전환 시 HITL 승인을
    생략하지 않아야 한다는 사용 원칙을 그대로 따르는지 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    row = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = 'PM'"
    ).fetchone()
    assert "human-approval-loop" in json.loads(row["skill_tags"])


def test_gc_seeding_is_idempotent_for_new_team():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    before = conn.execute("SELECT COUNT(*) c FROM agent_pool").fetchone()["c"]
    db.init_schema(conn)
    after = conn.execute("SELECT COUNT(*) c FROM agent_pool").fetchone()["c"]
    assert before == after
