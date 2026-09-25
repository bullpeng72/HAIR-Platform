"""F10 — 비용 조회. 1차 범위: 사람·Agent 비용 모두 수동 입력이다(HR 연동·실 LLM
과금 연동은 SPEC.md §12 오픈 이슈로 이월). Admin·매니저만 조회 가능(RBAC 매트릭스).
"""

from __future__ import annotations

import sqlite3


def set_agent_cost(
    conn: sqlite3.Connection, agent_id: int, cost_estimate: float
) -> None:
    conn.execute(
        "UPDATE agent_pool SET cost_estimate = ? WHERE id = ?",
        (cost_estimate, agent_id),
    )
    conn.commit()


def _confirmed_count(
    conn: sqlite3.Connection, resource_type: str, resource_id: int
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM assignments "
        "WHERE resource_type = ? AND resource_id = ? AND status = 'confirmed'",
        (resource_type, resource_id),
    ).fetchone()
    return row["c"]


def report(conn: sqlite3.Connection) -> dict:
    resources = []
    total_cost = 0.0
    for row in conn.execute("SELECT * FROM human_pool").fetchall():
        count = _confirmed_count(conn, "human", row["id"])
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "human",
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )
    for row in conn.execute(
        "SELECT * FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        count = _confirmed_count(conn, "agent", row["id"])
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "agent",
                "id": row["id"],
                "name": None,
                "role": row["role_name"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )
    return {"resources": resources, "total_cost": total_cost}
