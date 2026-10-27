"""Resource Pool 조회 — Human Pool + Agent Pool → ResourceCandidate.

AgentContract·HumanProfile은 각자 `to_candidate()` 어댑터로 공통
인터페이스에 맞춘다. `agent_pool` 테이블은 v3(Job Queue+온보딩)에서 실제로
채워지기 시작한다 — `status='active'`(온보딩 승인 완료)인 항목만 후보로 노출한다.
실제 AgentForge Code Generator/Verifier 연동은 아직 vendor/import되지 않아
`agent_pool.skill_tags` 등은 `jobs.py`의 스텁 제너레이터 결과다.

v4부터 `performance_score`가 실제 값을 반환한다 — 사람은 `performance_consent`가
참일 때만, Agent는 동의 개념이 없어 그대로 노출한다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform.models import ResourceCandidate


def human_pool_candidates(
    conn: sqlite3.Connection,
    role: str | None = None,
    skill: str | None = None,
    availability: str | None = None,
) -> list[ResourceCandidate]:
    """Phase 5("검색/필터 스케일")에서
    `available_only: bool`을 `availability: 'available' | 'unavailable'`
    로 넓혔다 — 이전엔 "가용한 사람만" 걸러낼 순 있어도 "지금 바쁜
    사람만"(예: 언제 풀리는지 확인하려고) 찾아볼 방법이 없었다.
    `'offboarded'`는 여기 값으로 못 넣는다 — 아래 WHERE절이 항상 이미
    제외하는 별개 상태다(퇴사자는 애초에 검색 후보가 아니다)."""
    # 코드 재점검(R3)에서 추가한 오프보딩 상태 — 퇴사한 사람은 검색/매칭에서
    # 완전히 배제한다(단순 unavailable과 달리 아예 후보군에서 제외).
    query = "SELECT * FROM human_pool WHERE availability != 'offboarded'"
    params: list[object] = []
    if role:
        query += " AND role LIKE ?"
        params.append(f"%{role}%")
    if availability in ("available", "unavailable"):
        query += " AND availability = ?"
        params.append(availability)

    rows = conn.execute(query, params).fetchall()
    candidates = []
    for row in rows:
        skill_tags = json.loads(row["skill_tags"])
        if skill and skill not in skill_tags:
            continue
        candidates.append(
            ResourceCandidate(
                id=row["id"],
                type="human",
                role=row["role"],
                is_available=row["availability"] == "available",
                cost=row["cost_estimate"],
                skill_tags=skill_tags,
                # 동의(performance_consent) 없이는 절대 노출하지 않는다.
                performance_score=(
                    row["performance_score"] if row["performance_consent"] else None
                ),
                user_id=row["user_id"],
                name=row["name"],
                code=row["employee_id"],
            )
        )
    return candidates


def agent_pool_candidates(
    conn: sqlite3.Connection,
    role: str | None = None,
    skill: str | None = None,
) -> list[ResourceCandidate]:
    """온보딩(`status='active'`)된 Agent만 후보로 노출한다.

    검색 버그 수정(코드 재점검): `role`/`skill` 필터가 human_pool_candidates
    에만 있고 여기엔 없어서 검색 결과에 무관한 Agent가 항상 섞여 나왔다 —
    human_pool_candidates와 동일한 필터 규약을 맞춘다. `availability`는
    Agent에 해당 없다(온보딩된 Agent는 항상 available).
    """
    query = "SELECT * FROM agent_pool WHERE status = 'active'"
    params: list[object] = []
    if role:
        query += " AND role_name LIKE ?"
        params.append(f"%{role}%")

    rows = conn.execute(query, params).fetchall()
    candidates = []
    for row in rows:
        skill_tags = json.loads(row["skill_tags"])
        if skill and skill not in skill_tags:
            continue
        candidates.append(
            ResourceCandidate(
                id=row["id"],
                type="agent",
                role=row["role_name"],
                is_available=True,
                cost=row["cost_estimate"],
                skill_tags=skill_tags,
                performance_score=row["performance_score"],
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 지정 안 했으면 None(화면에서 role로 폴백, 데이터는
                # 솔직하게 비워둔다).
                name=row["name"],
                code=row["code"],
            )
        )
    return candidates
