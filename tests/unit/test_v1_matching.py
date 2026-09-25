"""Phase 3 v1 골든셋 4건(알고리즘) — 완전일치/스코어링/null예외/후보없음(SPEC.md §9)."""

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
