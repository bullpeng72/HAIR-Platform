"""스킬 활용·결과 품질(2026-11-21, "에이전트들이 스킬을 충분히 활용하도록 하고,
스킬 이상의 결과품질을 생성해 내도록 개선") — `skill_prompting.py`."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import agent_runtime, db, llm, pool_registry, skill_prompting


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _admin(conn):
    from hair_platform.auth import Role, create_user

    row = conn.execute("SELECT id FROM users WHERE username = 'sp_admin'").fetchone()
    return row[0] if row else create_user(conn, "sp_admin", "pw", Role.ADMIN)


def _agent(conn, skills, tuning=None, role="분석가") -> int:
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, tuning_config) VALUES (?, ?, 'active', ?)",
        (role, json.dumps(skills, ensure_ascii=False), json.dumps(tuning or {})),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


class _Tool:
    name = "custom_tool_x"
    description = "숫자 목록의 합계·평균·증감률을 계산한다"


def test_tool_guidance_matches_real_conditions():
    assert skill_prompting.tool_guidance(None, False) == pool_registry.NO_TOOL_LOOP_GUARD
    assert skill_prompting.tool_guidance(None, True) == skill_prompting.RETRIEVAL_ONLY_GUIDANCE
    text = skill_prompting.tool_guidance([_Tool()], False)
    assert "custom_tool_x" in text and "도구를 호출해" in text
    assert "호출할 수 없습니다" not in text


def test_system_prompt_with_tools_does_not_forbid_tools():
    """버그 수정 — 실제 도구가 붙은 실행에 "도구를 호출할 수 없다"가 들어갔었다."""
    conn = _conn()
    system = llm.system_prompt_for_role(conn, "데이터 분석가", ["데이터분석"], callable_tools=[_Tool()])
    assert "호출할 수 없습니다" not in system
    assert "[도구]" in system
    assert skill_prompting.SKILL_USAGE_PRINCIPLES in system
    assert skill_prompting.QUALITY_BAR in system
    assert "소프트웨어 개발팀" not in system


def test_relevant_skill_comes_first_and_rest_are_brief():
    conn = _conn()
    skills = ["커밋메시지작성", "장애회고", "tdd-ai-workflow-checklist", "백엔드구현", "API설계"]
    block = skill_prompting.knowledge_block(conn, skills, "주문 조회 REST API 엔드포인트 명세를 정의해줘")
    assert block.startswith("이번 요청의 주 스킬(판단 참고): API설계")
    detail, brief = block.split("그 밖의 보유 스킬")
    assert "- [API설계]" in detail and "출력 형식:" in detail
    # 상세 지침은 상위 FULL_DETAIL_LIMIT개만, 나머지는 한 줄 요약.
    assert detail.count("\n- [") + 1 - detail.count("(함께 적용") <= skill_prompting.FULL_DETAIL_LIMIT + 1
    assert "출력 형식:" not in brief


def test_no_signal_keeps_all_skills_in_full():
    conn = _conn()
    skills = ["커밋메시지작성", "장애회고", "tdd-ai-workflow-checklist", "백엔드구현"]
    block = skill_prompting.knowledge_block(conn, skills, "안녕")
    assert "그 밖의 보유 스킬" not in block
    assert block.count("출력 형식:") == 4


def test_execute_agent_passes_tools_and_request_to_prompt():
    conn = _conn()
    aid = _agent(conn, ["데이터분석"])
    with (
        patch("hair_platform.tool_calling.resolve_callable_tools", return_value=[_Tool()]),
        patch("hair_platform.llm.call_llm_with_tools", return_value="결과") as m,
    ):
        agent_runtime.execute_agent(conn, aid, _admin(conn), "월별 매출 증감률을 계산해줘", provider="ollama", model="m")
    system = m.call_args.args[2]
    assert "custom_tool_x" in system and "호출할 수 없습니다" not in system


def test_self_review_applies_improved_version():
    conn = _conn()
    aid = _agent(conn, ["기획서작성"], {"self_review": True}, role="기획서 작성자")
    draft = "# 기획서\n목표: 효율화"
    improved = "# 기획서\n## 요약\n목표: 보고서 작성 시간을 건당 4시간 → 2시간으로 단축(2027-03)"
    with patch("hair_platform.llm.call_llm", side_effect=[draft, improved]) as m:
        out = agent_runtime.execute_agent(conn, aid, _admin(conn), "업무 자동화 기획서 써줘", provider="ollama", model="m")
    assert out == improved
    review_system, review_prompt = m.call_args_list[1].args[2], m.call_args_list[1].args[3]
    assert "최종 검토·개선 담당" in review_system and "[기획서작성]" in review_system
    assert draft in review_prompt and "업무 자동화 기획서 써줘" in review_prompt


def test_self_review_keeps_draft_when_review_is_bad_or_fails():
    conn = _conn()
    aid = _agent(conn, ["기획서작성"], {"self_review": True})
    draft = "충분히 긴 초안 " * 20
    sink: dict = {}
    with patch("hair_platform.llm.call_llm", side_effect=[draft, "짧음"]):
        out = agent_runtime.execute_agent(conn, aid, _admin(conn), "요청", retrieval_sink=sink, provider="ollama", model="m")
    # 단일 Agent 출력은 `model_output.clean()`을 거쳐 앞뒤 공백이 정리된다(2026-11-24).
    assert out == draft.strip() and sink["self_review"] == "rejected"
    with patch("hair_platform.llm.call_llm", side_effect=[draft, RuntimeError("timeout")]):
        out = agent_runtime.execute_agent(conn, aid, _admin(conn), "요청", retrieval_sink=sink, provider="ollama", model="m")
    assert out == draft.strip() and sink["self_review"] == "failed"


def test_self_review_off_by_default_makes_single_call():
    conn = _conn()
    aid = _agent(conn, ["기획서작성"])
    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, aid, _admin(conn), "요청", provider="ollama", model="m")
    assert m.call_count == 1


def test_crew_backstory_carries_principles_without_dev_team_framing():
    from hair_platform import crew_runtime

    conn = _conn()
    text = crew_runtime._agent_backstory(conn, "사업전략 수립가", ["사업전략수립"])
    assert "소프트웨어 개발팀" not in text
    assert skill_prompting.QUALITY_BAR in text
