"""Assignment Engine 매칭 알고리즘 — 결정론적(LLM 미사용).

역할명 일치(부분일치 포함) 우선 → 없으면 가중치 스코어링(가용성40·적합도35·비용25) →
후보 전혀 없으면 generate_new=True(생성 제안)로 알린다. 사람 후보의 performance_score가
null이면 적합도 항목을 제외하고 가용성60·비용40으로 재조정한다(성과리뷰가 아직 없는
v1에서는 Human Pool 후보 전원이 이 경로를 탄다 — 의도된 동작).

**역할명 부분일치(2026-11-02, 사용자 요청 "찾기시에... 부분일치로
찾아지도록 개선")** — 원래는 역할명이 대소문자/공백만 무시하고 글자
그대로 완전히 같아야만 후보로 잡혔다(예: Task "콘텐츠 카피라이팅"과
Agent "카피라이팅 에이전트"가 의미상 같은 일인데도 0건). "직접 검색해서
배정"(`/api/pool/search`)이 이미 쓰던 `role LIKE '%...%'` 부분일치
방식을 이 매칭에도 적용해 `_role_matches()`로 뺐다 — 한쪽 문자열이
다른 쪽을 포함하면 일치로 본다. 양쪽 다 서로를 포함하지 않는 역할명
(위 "카피라이팅" 예시처럼 접두/접미만 다른 게 아니라 아예 다른 단어
조합인 경우)까지는 여전히 못 잡는다 — 그런 경우는 `match_llm_judge.
judge_candidates()`(의미 기반 LLM 판단) 폴백이 계속 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hair_platform.models import ResourceCandidate


@dataclass
class ScoredCandidate:
    candidate: ResourceCandidate
    score: float
    breakdown: dict[str, float]  # 점수 breakdown
    exact_match: bool
    # 사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선") —
    # 이 모듈 자체는 여전히 결정론적(LLM 미사용)이라 이 필드는 항상
    # None이다. `match_llm_judge.judge_candidates()`가 이 dataclass를
    # 재사용하면서 LLM의 판단 근거를 채워 넣는다(기본값 있어 하위호환).
    rationale: str | None = None


@dataclass
class MatchResult:
    top: list[ScoredCandidate] = field(default_factory=list)
    generate_new: bool = False  # 후보가 전혀 없으면 True(생성 제안)


def _cost_score(cost: float, candidates: list[ResourceCandidate]) -> float:
    """비용이 낮을수록 높은 점수(0~1) — 후보군 내 상대 정규화(min-max)."""
    costs = [c.cost for c in candidates]
    lo, hi = min(costs), max(costs)
    if hi == lo:
        return 1.0
    return 1.0 - (cost - lo) / (hi - lo)


def _role_matches(candidate_role: str, required_role: str) -> bool:
    """역할명 부분일치 — 한쪽 문자열이 다른 쪽에 포함되면 일치로 본다
    (대소문자/양끝 공백 무시). `/api/pool/search`의 `role LIKE '%...%'`
    와 같은 느슨함을 이 매칭 알고리즘에도 적용한다."""
    a = candidate_role.strip().lower()
    b = required_role.strip().lower()
    if not a or not b:
        return False
    return a in b or b in a


def match_candidates(
    required_role: str,
    required_skills: list[str],
    candidates: list[ResourceCandidate],
) -> MatchResult:
    if not candidates:
        return MatchResult(top=[], generate_new=True)

    required_skill_set = set(required_skills)

    exact = [
        c
        for c in candidates
        if _role_matches(c.role, required_role)
        and required_skill_set.issubset(set(c.skill_tags))
        and c.is_available
    ]
    if exact:
        ranked = sorted(exact, key=lambda c: c.cost)
        scored = [
            ScoredCandidate(
                candidate=c, score=1.0, breakdown={"exact_match": 1.0}, exact_match=True
            )
            for c in ranked
        ]
        return MatchResult(top=scored[:3], generate_new=False)

    # 코드 재점검(2026-09-26, R3)에서 발견한 버그 수정: 완전일치가 없으면 이전엔
    # role이 완전히 다른 후보까지 전부 점수 매겨 "매칭 결과"로 추천했다(예: 백엔드
    # 개발자를 찾는데 Pool에 디자이너만 있어도 top 3로 제시되고 generate_new=False가
    # 나왔다) — 가중치 스코어링은 role이 일치하는 후보로만 좁힌다. role 일치 후보가
    # 아예 없으면 완전히 없는 것과 동일하게 생성을 제안한다.
    role_relevant = [c for c in candidates if _role_matches(c.role, required_role)]
    if not role_relevant:
        return MatchResult(top=[], generate_new=True)

    scored: list[ScoredCandidate] = []
    for c in role_relevant:
        availability = 1.0 if c.is_available else 0.0
        cost_score = _cost_score(c.cost, role_relevant)
        if c.performance_score is None:
            # null 예외: 적합도 제외, 가용성60·비용40으로 재조정
            score = availability * 0.60 + cost_score * 0.40
            breakdown = {"availability": availability * 0.60, "cost": cost_score * 0.40}
        else:
            score = availability * 0.40 + c.performance_score * 0.35 + cost_score * 0.25
            breakdown = {
                "availability": availability * 0.40,
                "fit": c.performance_score * 0.35,
                "cost": cost_score * 0.25,
            }
        scored.append(
            ScoredCandidate(
                candidate=c, score=score, breakdown=breakdown, exact_match=False
            )
        )

    scored.sort(key=lambda s: s.score, reverse=True)
    return MatchResult(top=scored[:3], generate_new=False)
