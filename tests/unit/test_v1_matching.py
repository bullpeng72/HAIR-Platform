"""Phase 3 v1 골든셋 4건(알고리즘) — 완전일치/스코어링/null예외/후보없음."""

from __future__ import annotations

from hair_platform.assignment import match_candidates
from hair_platform.models import ResourceCandidate


def _candidate(**kwargs) -> ResourceCandidate:
    base = {
        "id": 1,
        "type": "human",
        "role": "백엔드 개발자",
        "is_available": True,
        "cost": 100.0,
        "skill_tags": ["python"],
        "performance_score": None,
    }
    base.update(kwargs)
    return ResourceCandidate(**base)


def test_gc5_exact_match_wins_regardless_of_score():
    exact = _candidate(
        id=1, role="백엔드 개발자", skill_tags=["python", "fastapi"], cost=999.0
    )
    partial = _candidate(id=2, role="백엔드 개발자", skill_tags=["python"], cost=10.0)

    result = match_candidates("백엔드 개발자", ["python", "fastapi"], [exact, partial])

    assert result.generate_new is False
    assert result.top[0].candidate.id == 1
    assert result.top[0].exact_match is True


def test_gc6_weighted_scoring_with_performance_score():
    high_fit = _candidate(
        id=1, role="디자이너", skill_tags=["figma"], performance_score=0.9, cost=50.0
    )
    low_fit = _candidate(
        id=2, role="디자이너", skill_tags=["figma"], performance_score=0.3, cost=50.0
    )

    # 요구 스킬(illustrator)을 아무도 완전히 못 채우므로 완전일치 없음 → 가중치 스코어링
    result = match_candidates("디자이너", ["illustrator"], [high_fit, low_fit])

    assert result.generate_new is False
    assert result.top[0].candidate.id == 1  # 적합도가 높은 쪽이 1등
    assert "fit" in result.top[0].breakdown


def test_gc7_null_performance_score_reweights_availability_and_cost_only():
    candidate = _candidate(
        id=1, role="QA", performance_score=None, is_available=True, cost=0.0
    )

    result = match_candidates("QA", ["nonexistent-skill"], [candidate])

    breakdown = result.top[0].breakdown
    assert "fit" not in breakdown
    assert set(breakdown.keys()) == {"availability", "cost"}
    assert result.top[0].score == breakdown["availability"] + breakdown["cost"]


def test_gc8_no_candidates_suggests_generate_new():
    result = match_candidates("백엔드 개발자", ["python"], [])

    assert result.generate_new is True
    assert result.top == []


# ---------- 역할명 부분일치(사용자 요청, 2026-11-02, "찾기시에...
# 부분일치로 찾아지도록 개선") ----------


def test_gc9_role_name_substring_match_finds_candidate_with_longer_role():
    """사용자 보고("프론트엔드 개발" Task 찾기 안 됨) 재현 + 수정
    확인 — Task role_name이 Candidate role의 부분 문자열이면(또는
    반대로) 매칭돼야 한다."""
    frontend_dev = _candidate(
        id=1, role="프론트엔드 개발자", skill_tags=["html", "css"]
    )
    result = match_candidates("프론트엔드 개발", ["html"], [frontend_dev])

    assert result.generate_new is False
    assert result.top[0].candidate.id == 1


def test_gc10_role_name_substring_match_is_bidirectional():
    """반대 방향(후보 role이 Task role_name의 부분 문자열)도 매칭된다."""
    short_role = _candidate(id=1, role="개발", skill_tags=[])
    result = match_candidates("프론트엔드 개발", [], [short_role])

    assert result.generate_new is False
    assert result.top[0].candidate.id == 1


def test_gc11_completely_different_role_names_still_require_generate_new():
    """서로 포함 관계가 아닌 역할명(접두/접미만 다른 게 아니라 아예
    다른 단어 조합)은 부분일치로도 못 잡는다 — 이 경우는 여전히 LLM
    judge 폴백이 담당해야 한다(이 테스트는 그 경계를 고정한다)."""
    copywriter_agent = _candidate(id=1, role="카피라이팅 에이전트", skill_tags=[])
    result = match_candidates("콘텐츠 카피라이팅", [], [copywriter_agent])

    assert result.generate_new is True
    assert result.top == []
