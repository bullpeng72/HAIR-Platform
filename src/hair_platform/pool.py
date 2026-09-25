"""Resource Pool 조회 — Human Pool(F12) + Agent Pool(F4/F9로 채워짐) → ResourceCandidate.

ADR 결정 4: AgentContract·HumanProfile은 각자 `to_candidate()` 어댑터로 공통
인터페이스에 맞춘다. `agent_pool` 테이블은 v3(Job Queue+F9 온보딩)에서 실제로
채워지기 시작한다 — `status='active'`(F9 온보딩 승인 완료)인 항목만 후보로 노출한다.
실제 AgentForge Code Generator/Verifier 연동은 아직 vendor/import되지 않아
`agent_pool.skill_tags` 등은 `jobs.py`의 스텁 제너레이터 결과다(SPEC.md §12 오픈 이슈).

v4부터 `performance_score`가 실제 값을 반환한다 — 사람은 `performance_consent`가
참일 때만(NF9, F8), Agent는 동의 개념이 없어 그대로 노출한다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform.models import ResourceCandidate


def human_pool_candidates(
    conn: sqlite3.Connection,
    role: str | None = None,
    skill: str | None = None,
    available_only: bool = False,
) -> list[ResourceCandidate]:
    query = "SELECT * FROM human_pool WHERE 1=1"
    params: list[object] = []
    if role:
        query += " AND role LIKE ?"
        params.append(f"%{role}%")
    if available_only:
        query += " AND availability = 'available'"

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
                # NF9: 동의(performance_consent) 없이는 절대 노출하지 않는다.
                performance_score=(
                    row["performance_score"] if row["performance_consent"] else None
                ),
            )
        )
    return candidates


def agent_pool_candidates(
    conn: sqlite3.Connection,
    role: str | None = None,
    skill: str | None = None,
) -> list[ResourceCandidate]:
    """F9에서 온보딩(`status='active'`)된 Agent만 후보로 노출한다.

    F3 검색 버그 수정(코드 재점검): `role`/`skill` 필터가 human_pool_candidates
    에만 있고 여기엔 없어서 검색 결과에 무관한 Agent가 항상 섞여 나왔다 —
    human_pool_candidates와 동일한 필터 규약을 맞춘다. `available_only`는
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
            )
        )
    return candidates
