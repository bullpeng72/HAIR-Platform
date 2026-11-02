"""Task에 역할명이 정확히 일치하는 후보가 없을 때(결정론적 매칭
`assignment.match_candidates()`가 `generate_new=True`를 반환한 경우)
LLM에게 전체 Human/Agent Pool을 보여주고 의미상 맞는 후보를 찾게 하는
선택적 2차 경로 — 사용자 요청(2026-11-02, "LLM judge로 추천 인력(사람/
에이전트)를 찾도록 개선").

`assignment.py`는 "결정론적(LLM 미사용)"을 모듈 설계 원칙으로 못박아
두고 있어, 이 LLM 기반 로직은 그 계약을 지키기 위해 별도 파일로
분리했다 — `ScoredCandidate`/`MatchResult`는 그대로 재사용한다.

`agent_optimizer.refine_with_llm()`과 같은 fail-safe 계약: 호출부
(`app.py`)가 `use_llm_judge=True`를 명시적으로 줬을 때만 호출되고
(비용 발생 지점을 상위에서 게이팅하는 `llm.py`의 기존 원칙과 동일),
API 에러·JSON 파싱 실패·존재하지 않는 후보를 추천(환각)하는 등 어떤
이유로든 실패하면 절대 예외를 올리지 않고 조용히
`MatchResult(top=[], generate_new=True)`로 되돌아간다 — 매칭 화면이
LLM 장애로 깨지는 일은 없다.
"""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import llm
from hair_platform.assignment import MatchResult, ScoredCandidate
from hair_platform.models import ResourceCandidate

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "당신은 업무 배정 보좌관입니다. 주어진 Task와 전체 후보 목록을 보고, "
    "역할명 문자열이 정확히 같지 않아도 실제로 그 업무를 수행할 수 있는 "
    "후보를 최대 3명까지 추천하세요. 적합한 후보가 전혀 없으면 빈 배열을 "
    "반환하세요. 반드시 JSON 객체 하나만 출력하세요: "
    '{"recommendations": [{"candidate_id": int, "candidate_type": '
    '"human"|"agent", "score": 0.0~1.0, "rationale": "한 문장 근거"}]}. '
    "아래 '전체 후보' 목록에 실제로 있는 candidate_id/candidate_type 조합만 "
    "추천할 수 있습니다 — 목록에 없는 후보는 절대 지어내지 마세요."
)


def _candidate_summary(c: ResourceCandidate) -> dict:
    return {
        "candidate_id": c.id,
        "candidate_type": c.type,
        "role": c.role,
        "skill_tags": c.skill_tags,
        "name": c.name,
        "is_available": c.is_available,
        "cost": c.cost,
    }


def judge_candidates(
    conn: sqlite3.Connection,
    task: sqlite3.Row,
    candidates: list[ResourceCandidate],
) -> MatchResult:
    """결정론적 매칭이 실패했을 때만 호출되는 폴백 — 호출 자체가
    실제 LLM 비용을 발생시키므로, 비어 있는 `candidates`에 대해서는
    LLM을 아예 호출하지 않는다(그래도 매칭될 게 없으므로)."""
    if not candidates:
        return MatchResult(top=[], generate_new=True)

    by_key = {(c.type, c.id): c for c in candidates}
    required_skills = json.loads(task["required_skills"])
    prompt = (
        f"Task 역할명: {task['role_name']}\n"
        f"필요 스킬: {json.dumps(required_skills, ensure_ascii=False)}\n"
        f"비고: {task['notes'] or '(없음)'}\n\n"
        "전체 후보:\n"
        + json.dumps(
            [_candidate_summary(c) for c in candidates], ensure_ascii=False
        )
    )

    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
        raw = llm.call_llm(provider, model, _SYSTEM_PROMPT, prompt)
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("no_json_block")
        parsed = json.loads(match.group(0))

        scored: list[ScoredCandidate] = []
        for rec in parsed.get("recommendations", []):
            key = (rec.get("candidate_type"), rec.get("candidate_id"))
            candidate = by_key.get(key)
            if candidate is None:
                continue  # 환각 후보 — 후보 풀에 없으면 조용히 제외
            score = float(rec.get("score", 0.0))
            scored.append(
                ScoredCandidate(
                    candidate=candidate,
                    score=score,
                    breakdown={"llm_judge": score},
                    exact_match=False,
                    rationale=rec.get("rationale"),
                )
            )
        if not scored:
            return MatchResult(top=[], generate_new=True)
        scored.sort(key=lambda s: s.score, reverse=True)
        return MatchResult(top=scored[:3], generate_new=False)
    except Exception:  # noqa: BLE001 — 절대 raise하지 않는다, 항상 안전 폴백
        return MatchResult(top=[], generate_new=True)
