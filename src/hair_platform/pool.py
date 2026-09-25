"""Resource Pool 조회 — Human Pool(F12에서 등록) + Agent Pool(스텁) → ResourceCandidate.

ADR 결정 4: AgentContract·HumanProfile은 각자 `to_candidate()` 어댑터로 공통
인터페이스에 맞춘다. Agent Pool은 AgentForge 컴포넌트 추출 전까지(v3 Job Queue 이후)
빈 리스트를 반환하는 스텁이다 — v1 시점엔 실제로 생성된 Agent가 하나도 없으므로
빈 리스트가 정확한 현재 상태다.
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
                performance_score=None,  # F8 성과리뷰(v4) 전까지는 항상 null(동의 기반 수집 전)
            )
        )
    return candidates


def agent_pool_candidates() -> list[ResourceCandidate]:
    """AgentForge Pool 추출 전 스텁 — v3(Job Queue) 이후 실제 Agent가 생기면 채워진다."""
    return []
