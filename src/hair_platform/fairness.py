"""Phase 4 검증 — 배정 공정성 지표(NF4, SPEC.md DoD).

사람·Agent가 같은 기준(Assignment Engine 스코어링)으로 배정되는지, 반려율에
체계적인 차이가 없는지를 `assignments` 실제 데이터로 계산한다. 표본이 작을 때는
비율 해석에 주의해야 하므로 `sample_size`를 항상 함께 반환한다.
"""

from __future__ import annotations

import sqlite3


def _stats_for(conn: sqlite3.Connection, resource_type: str) -> dict:
    row = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed, "
        "  SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected, "
        "  SUM(CASE WHEN status IN ('confirmed', 'rejected') THEN 1 ELSE 0 END) AS decided "
        "FROM assignments WHERE resource_type = ?",
        (resource_type,),
    ).fetchone()
    confirmed = row["confirmed"] or 0
    rejected = row["rejected"] or 0
    decided = row["decided"] or 0
    return {
        "resource_type": resource_type,
        "confirmed": confirmed,
        "rejected": rejected,
        "decided": decided,
        "rejection_rate": (rejected / decided) if decided else None,
    }


def report(conn: sqlite3.Connection) -> dict:
    human = _stats_for(conn, "human")
    agent = _stats_for(conn, "agent")

    gap = None
    if human["rejection_rate"] is not None and agent["rejection_rate"] is not None:
        gap = abs(human["rejection_rate"] - agent["rejection_rate"])

    min_sample = min(human["decided"], agent["decided"])
    return {
        "human": human,
        "agent": agent,
        "rejection_rate_gap": gap,
        # 표본이 작으면(예: 각 유형 10건 미만) 비율 차이가 통계적으로 의미 없을 수 있다
        # — Phase 4 threshold_review에서 사람이 함께 해석해야 한다(자동 판정 아님).
        "low_sample_warning": min_sample < 10,
    }
