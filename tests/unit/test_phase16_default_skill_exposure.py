"""Phase 16 — Agent-Evaluator/하니스 방법론 절차 스킬을 Skill Pool에 기본 노출
(사용자 요청, 2026-09-28, GitHub 저장소(bullpeng72/Agent-Evaluator) Skills
링크를 검토해 "도구/스킬 Pool에 기본 노출해줘").

HAIR-Platform이 모델링하는 SDLC 팀(백엔드 개발자·QA 등 역할의 AI 에이전트)이
실제로 하니스 방법론/agent-evaluator 생태계를 따르는 조직이라는 전제 하에,
그 생태계의 절차 스킬(SKILL.md)들을 `db._seed_default_pools()`에서 Skill
Pool에 기본 노출한다. 15개는 `agent-evaluator[sdk]` 기본 배포(Skills/)에
이미 포함된 것이고, 3개는 이 워크북 저장소에서 자체 발굴했지만 아직 그
기본 배포로 역이식되지 않은 것이다.

이 스킬들은 CrewAI Tool로 감쌀 수 있는 실행 가능한 함수가 아니라 Claude
Code/OpenCode 세션이 참고하는 절차 문서라서, VectorDB/GraphDB처럼 자동으로
붙일 도구가 없다 — `augmentation_mode='none'`으로 skill_tag 표시만 남긴다.
"""

from __future__ import annotations

from hair_platform import db, pool_registry

_EXPECTED_AGENT_EVAL_SKILL_NAMES = {
    # agent-evaluator[sdk] 기본 배포(Skills/, 15개)
    "abtest-decision-workflow",
    "checklist-confidence-audit",
    "claims-audit-ci",
    "config-silent-noop-check",
    "eval-run-directory-hygiene",
    "gate-improvement-loop",
    "harness-autopilot",
    "harness-gate-ci",
    "liveguardrail-standard-config",
    "recommend-fix-workflow",
    "spec-driven-artifacts",
    "sync-drift-check",
    "tdd-ai-workflow-checklist",
    "threshold-realism-review",
    "unattended-session-recovery",
    # 이 워크북에서 자체 발굴했지만 아직 기본 배포로 역이식되지 않은 3개
    "background-real-verification",
    "evidence-based-gate-fix",
    "honest-refuted-experiment-logging",
}


def test_gc125_init_schema_seeds_agent_eval_procedural_skills_as_untooled_skill_tags():
    """스키마 초기화 시 18개 절차 스킬이 Skill Pool에 자동으로 채워지고,
    실행 가능한 CrewAI Tool이 없으므로 전부 `augmentation_mode='none'`·
    `default_tool_pool_ids=[]`여야 한다. `init_schema()`를 두 번 불러도
    중복 생성되지 않아야 한다(멱등성)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 멱등성 확인

    skills = pool_registry.list_skills(conn)
    skill_names = {s["name"] for s in skills}
    assert _EXPECTED_AGENT_EVAL_SKILL_NAMES <= skill_names
    assert len(skills) == 4 + len(
        _EXPECTED_AGENT_EVAL_SKILL_NAMES
    )  # vectordb/graphdb 4 + 절차 18

    by_name = {s["name"]: s for s in skills}
    for name in _EXPECTED_AGENT_EVAL_SKILL_NAMES:
        s = by_name[name]
        assert s["augmentation_mode"] == "none"
        assert s["default_tool_pool_ids"] == []
        assert s["description"]  # 빈 설명이 아니어야 한다


def test_gc126_skill_pool_endpoint_exposes_procedural_skills_to_manager(
    client_and_conn,
):
    """관리자뿐 아니라 매니저도 "도구/스킬 Pool" 탭(`GET /api/skill-pool`)에서
    이 절차 스킬들을 볼 수 있어야 한다 — 등록 전용이 아니라 "노출"이 목적."""
    client, _conn = client_and_conn
    resp = client.post(
        "/login", json={"username": "manager1", "password": "managerpass"}
    )
    assert resp.status_code == 200

    resp = client.get("/api/skill-pool")
    assert resp.status_code == 200
    body = resp.json()
    names = {s["name"] for s in body}
    assert _EXPECTED_AGENT_EVAL_SKILL_NAMES <= names
    for s in body:
        if s["name"] in _EXPECTED_AGENT_EVAL_SKILL_NAMES:
            assert s["augmentation_mode"] == "none"
