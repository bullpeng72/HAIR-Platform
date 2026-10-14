"""Phase 41 — "Harness Methodology 기준 Full Stack 개발 단계별 Agent 생성...
skill, Tool을 적극 활용"(2026-10-13) 작업 중 발견한 실제 갭을 고친다:

`skill_pool`에 등록된 절차/방법론 스킬(`tdd-ai-workflow-checklist`,
`spec-driven-artifacts` 등)은 Agent의 `backstory`/시스템 프롬프트에
**이름만** 나열될 뿐, 그 스킬이 실제로 무엇을 하라는 것인지
(`skill_pool.description`)는 어디에도 전달되지 않았다 —
`deliverables.skill_grounded_system_prompt()`(사람의 "LLM으로 도와줘"
경로)만 이 지식 주입 로직을 갖고 있었다. 이 테스트는 그 로직을
공용화한 `pool_registry.skill_knowledge_text()`가 Agent 자율 실행
경로(`llm.system_prompt_for_role()`, `crew_runtime._agent_backstory()`)
에도 똑같이 적용됐는지 검증한다."""

from __future__ import annotations

from hair_platform import crew_runtime, llm, pool_registry


def _make_skill(conn, name, description, mode="none"):
    pool_registry.create_skill(conn, 1, name, description, [], mode)


def test_skill_knowledge_text_collects_matching_descriptions(client_and_conn):
    _client, conn = client_and_conn
    _make_skill(conn, "스킬A", "스킬A에 대한 절차 설명입니다.")
    _make_skill(conn, "스킬B", "스킬B에 대한 절차 설명입니다.")
    _make_skill(conn, "스킬C", "쓰이지 않을 스킬입니다.")

    text = pool_registry.skill_knowledge_text(conn, ["스킬A", "스킬B"])
    assert "스킬A에 대한 절차 설명입니다." in text
    assert "스킬B에 대한 절차 설명입니다." in text
    assert "쓰이지 않을 스킬입니다." not in text


def test_skill_knowledge_text_empty_for_no_skills(client_and_conn):
    _client, conn = client_and_conn
    assert pool_registry.skill_knowledge_text(conn, []) == ""
    assert pool_registry.skill_knowledge_text(conn, ["존재하지 않는 스킬"]) == ""


def test_system_prompt_for_role_includes_skill_descriptions(client_and_conn):
    _client, conn = client_and_conn
    _make_skill(conn, "절차스킬", "이 역할은 작업 전 체크리스트를 먼저 확인해야 한다.")

    system = llm.system_prompt_for_role(conn, "개발자", ["절차스킬"])
    assert "절차스킬" in system  # 이름도 여전히 나열된다(기존 동작 유지)
    assert "이 역할은 작업 전 체크리스트를 먼저 확인해야 한다." in system


def test_system_prompt_for_role_unchanged_when_no_matching_skill(client_and_conn):
    _client, conn = client_and_conn
    system = llm.system_prompt_for_role(conn, "개발자", [])
    assert "참고 지식" not in system


def test_agent_backstory_includes_skill_descriptions(client_and_conn):
    _client, conn = client_and_conn
    _make_skill(conn, "절차스킬", "Spec을 작성하기 전 실패 모드를 먼저 도출한다.")

    backstory = crew_runtime._agent_backstory(conn, "스펙 작성자", ["절차스킬"])
    assert "절차스킬" in backstory
    assert "Spec을 작성하기 전 실패 모드를 먼저 도출한다." in backstory


def test_agent_backstory_unchanged_when_no_matching_skill(client_and_conn):
    _client, conn = client_and_conn
    backstory = crew_runtime._agent_backstory(conn, "개발자", [])
    assert "보유 역량 상세" not in backstory
