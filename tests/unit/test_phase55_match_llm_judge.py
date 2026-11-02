"""Phase 55 — LLM judge 기반 인력 매칭 폴백(사용자 요청, 2026-11-02,
"LLM judge로 추천 인력(사람/에이전트)를 찾도록 개선").

`assignment.match_candidates()`(역할명 완전일치만 보는 결정론적 매칭)가
`generate_new=True`를 반환했을 때만 호출되는 2차 경로 — 콘텐츠
카피라이팅 Task에 "카피라이팅 에이전트"처럼 역할명 문자열이 달라도
의미상 맞는 후보를 LLM이 찾게 한다.

**비용/네트워크 발생 방지** — `agent_optimizer.refine_with_llm()` 테스트
(`test_phase49_agent_optimizer.py`)와 같은 원칙: `hair_platform.llm.
call_llm` 자체를 patch한다(진짜 API 호출 없음)."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import db, match_llm_judge
from hair_platform.auth import Role, create_user
from hair_platform.models import ResourceCandidate


def _make_task(conn, role_name="콘텐츠 카피라이팅", required_skills=None, notes=""):
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text) VALUES (?, ?)",
        (admin_id, "테스트 브리프"),
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills, notes) "
        "VALUES (?, ?, ?, ?)",
        (brief_id, role_name, json.dumps(required_skills or []), notes),
    )
    conn.commit()
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()


def _human_candidate(id_=1, role="카피라이터"):
    return ResourceCandidate(
        id=id_,
        type="human",
        role=role,
        is_available=True,
        cost=100.0,
        skill_tags=["copywriting"],
        performance_score=None,
        user_id=None,
        name="후보A",
        code=None,
    )


def _agent_candidate(id_=1, role="카피라이팅 에이전트"):
    return ResourceCandidate(
        id=id_,
        type="agent",
        role=role,
        is_available=True,
        cost=0.0,
        skill_tags=["copywriting"],
        performance_score=None,
        user_id=None,
        name=None,
        code=None,
    )


def test_judge_candidates_empty_pool_short_circuits_without_llm_call():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)
    with patch("hair_platform.llm.call_llm") as mock_call:
        result = match_llm_judge.judge_candidates(conn, task, [])
    assert result.generate_new is True
    assert result.top == []
    mock_call.assert_not_called()


def test_judge_candidates_returns_matches_from_valid_llm_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn, role_name="콘텐츠 카피라이팅")
    agent = _agent_candidate(id_=7)

    fake_response = json.dumps(
        {
            "recommendations": [
                {
                    "candidate_id": 7,
                    "candidate_type": "agent",
                    "score": 0.9,
                    "rationale": "카피라이팅 업무와 역량이 일치합니다",
                }
            ]
        }
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = match_llm_judge.judge_candidates(conn, task, [agent])

    assert result.generate_new is False
    assert len(result.top) == 1
    sc = result.top[0]
    assert sc.candidate.id == 7
    assert sc.candidate.type == "agent"
    assert sc.exact_match is False
    assert sc.rationale == "카피라이팅 업무와 역량이 일치합니다"
    assert sc.score == 0.9


def test_judge_candidates_drops_hallucinated_candidate_id(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)
    real_agent = _agent_candidate(id_=1)

    fake_response = json.dumps(
        {
            "recommendations": [
                {
                    "candidate_id": 999,  # 실제 후보 풀에 없는 id — 환각
                    "candidate_type": "agent",
                    "score": 0.9,
                    "rationale": "지어낸 후보",
                }
            ]
        }
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = match_llm_judge.judge_candidates(conn, task, [real_agent])

    assert result.generate_new is True
    assert result.top == []


def test_judge_candidates_falls_back_on_malformed_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)
    with patch(
        "hair_platform.llm.call_llm", return_value="이건 JSON이 아닙니다"
    ):
        result = match_llm_judge.judge_candidates(conn, task, [_human_candidate()])

    assert result.generate_new is True
    assert result.top == []


def test_judge_candidates_falls_back_on_llm_call_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("boom")):
        result = match_llm_judge.judge_candidates(conn, task, [_human_candidate()])

    assert result.generate_new is True
    assert result.top == []


def test_judge_candidates_empty_recommendations_list_means_generate_new(monkeypatch):
    """LLM이 "적합한 후보가 없습니다"라고 정상적으로 빈 배열을 돌려줘도
    (JSON 자체는 유효) 안전하게 generate_new=True로 처리된다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)
    fake_response = json.dumps({"recommendations": []})
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = match_llm_judge.judge_candidates(conn, task, [_human_candidate()])

    assert result.generate_new is True
    assert result.top == []


def test_judge_candidates_uses_platform_default_llm_when_configured(monkeypatch):
    """`conn=`을 넘긴 `resolve_provider_and_model()`이 관리자가 설정한
    플랫폼 공통 기본 LLM을 쓰는지 — `llm.call_llm`에 실제로 넘어가는
    provider/model을 추적해 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    task = _make_task(conn)  # "admin1" 계정은 _make_task()가 이미 만든다.
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    from hair_platform import llm, model_registry

    model_registry.create_model(conn, admin_id, "openai", "gpt-platform-default")
    llm.set_platform_default_llm(conn, admin_id, "openai", "gpt-platform-default")
    monkeypatch.delenv("HAIR_PLATFORM_LLM_PROVIDER", raising=False)

    captured = {}

    def _fake_call_llm(provider, model, system, prompt, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return json.dumps({"recommendations": []})

    with patch("hair_platform.llm.call_llm", side_effect=_fake_call_llm):
        match_llm_judge.judge_candidates(conn, task, [_human_candidate()])

    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-platform-default"
