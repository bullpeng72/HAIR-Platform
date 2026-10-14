"""Phase 16 — Agent-Evaluator/하니스 방법론 절차 스킬을 Skill Pool에 기본 노출
(사용자 요청, 2026-09-28, GitHub 저장소(bullpeng72/Agent-Evaluator) Skills
링크를 검토해 "도구/스킬 Pool에 기본 노출해줘").

HAIR-Platform이 모델링하는 SDLC 팀(백엔드 개발자·QA 등 역할의 AI 에이전트)이
실제로 하니스 방법론/agent-evaluator 생태계를 따르는 조직이라는 전제 하에,
그 생태계의 절차 스킬(SKILL.md)들을 `db._seed_default_pools()`에서 Skill
Pool에 기본 노출한다. 15개는 `agent-evaluator[sdk]` 기본 배포(Skills/)에
이미 포함된 것이고, 3개는 이 워크북 저장소에서 자체 발굴했지만 아직 그
기본 배포로 역이식되지 않은 것이다.

이 스킬들 대부분은 CrewAI Tool로 감쌀 수 있는 실행 가능한 함수가 아니라
Claude Code/OpenCode 세션이 참고하는 절차 문서(git/PR 판단, SDK 소스 읽기,
CI 신호 해석 같은 HITL 판단이 필요)라서, VectorDB/GraphDB처럼 자동으로
붙일 도구가 없다 — `augmentation_mode='none'`으로 skill_tag 표시만 남긴다.

예외가 하나 있다: `recommend-fix-workflow`는 사용자 요청(2026-09-28,
"CrewAI AI Agent가 개발 단계에 필요한 기능일지 확인하고, 필요한 경우 AI
Agent가 호출할 수 있도록 변형해줘")에 따라 실제로 검토한 결과,
`agent_evaluator.integrations.recommend_fix_mcp`가 이미 읽기 전용 stdio
MCP 서버로 구현돼 있고 `.aoo` 실행 이력 없이도 안전하게 동작해서, 이것만
mcp_stdio Tool Pool 항목으로 승격해 `augmentation_mode='tool'`로
연결했다.
"""

from __future__ import annotations

from hair_platform import db, pool_registry

_TOOLED_AGENT_EVAL_SKILL_NAME = "recommend-fix-workflow"

_UNTOOLED_AGENT_EVAL_SKILL_NAMES = {
    # agent-evaluator[sdk] 기본 배포(Skills/, 14개 — recommend-fix-workflow 제외)
    "abtest-decision-workflow",
    "checklist-confidence-audit",
    "claims-audit-ci",
    "config-silent-noop-check",
    "eval-run-directory-hygiene",
    "gate-improvement-loop",
    "harness-autopilot",
    "harness-gate-ci",
    "liveguardrail-standard-config",
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

_EXPECTED_AGENT_EVAL_SKILL_NAMES = _UNTOOLED_AGENT_EVAL_SKILL_NAMES | {
    _TOOLED_AGENT_EVAL_SKILL_NAME
}


def test_gc125_init_schema_seeds_agent_eval_procedural_skills():
    """스키마 초기화 시 18개 절차 스킬이 Skill Pool에 자동으로 채워져야
    한다. 17개는 실행 가능한 CrewAI Tool이 없어 `augmentation_mode='none'`·
    `default_tool_pool_ids=[]`여야 하고, `recommend-fix-workflow` 하나만
    실제 `recommend_fix` MCP 도구가 붙어 `augmentation_mode='tool'`이어야
    한다. `init_schema()`를 두 번 불러도 중복 생성되지 않아야 한다
    (멱등성)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 멱등성 확인

    skills = pool_registry.list_skills(conn)
    skill_names = {s["name"] for s in skills}
    assert _EXPECTED_AGENT_EVAL_SKILL_NAMES <= skill_names
    assert len(skills) == 4 + len(
        _EXPECTED_AGENT_EVAL_SKILL_NAMES
    ) + 25  # rag/graphdb 2(원래 vectordb/rag/graphdb/관계질의 4였으나 2026-10-06
    # 자동완성 중복 통합으로 2로 줄어듦) + 외부검색 1
    # (원래 websearch/외부검색 2였으나 역시 같은 이유로 1로 줄어듦) + 일반웹검색 1
    # + 절차 18
    # + 2026-10-14 "기본 배포에 Skill을 포함해줘"로 추가된 레퍼런스
    # 파이프라인 Skill 15개(Tool 5개를 감싼 'tool' 모드 5 + 지식 주입용
    # 'none' 모드 10 — adr-template/code-review-checklist/
    # commit-pr-message-rules/docs-style-guide/incident-postmortem/
    # human-approval-loop/3c-4p-swot-framework/business-plan-writing-guide/
    # business-strategy-formulation/executive-report-writing-style)
    # + 2026-10-14(PM/AA/DA 등 기획→개발 팀 구성)로 추가된 Skill 9개
    # (지식 8 + test-case-mechanical-derivation 'tool' 모드 1
    # = 15 + 9 = 24).
    # + 2026-10-14("자율 개발 크루" 10인 개발자 풀)로 추가된 Skill 1개
    # (data-infra-implementation-guide = 24 + 1 = 25).

    by_name = {s["name"]: s for s in skills}
    for name in _UNTOOLED_AGENT_EVAL_SKILL_NAMES:
        s = by_name[name]
        assert s["augmentation_mode"] == "none"
        assert s["default_tool_pool_ids"] == []
        assert s["description"]  # 빈 설명이 아니어야 한다

    tooled = by_name[_TOOLED_AGENT_EVAL_SKILL_NAME]
    assert tooled["augmentation_mode"] == "tool"
    assert len(tooled["default_tool_pool_ids"]) == 1

    tools_by_id = {t["id"]: t for t in pool_registry.list_tools(conn)}
    recommend_fix_tool = tools_by_id[tooled["default_tool_pool_ids"][0]]
    assert recommend_fix_tool["kind"] == "mcp_stdio"
    assert recommend_fix_tool["config"][
        "command"
    ]  # sys.executable, 빈 값이 아니어야 함
    assert recommend_fix_tool["config"]["args"] == [
        "-m",
        "agent_evaluator.integrations.recommend_fix_mcp",
    ]


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
        if s["name"] in _UNTOOLED_AGENT_EVAL_SKILL_NAMES:
            assert s["augmentation_mode"] == "none"
        elif s["name"] == _TOOLED_AGENT_EVAL_SKILL_NAME:
            assert s["augmentation_mode"] == "tool"
