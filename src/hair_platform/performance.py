"""F8 — 성과리뷰. 사람 성과데이터는 사전 동의(opt-in) 없이는 절대 수집·노출하지 않는다
(NF9, SPEC.md §10). Agent는 사람이 아니므로 동의 개념이 없다.

"Gate A–G" 실측 배선(`@agent_eval`)은 AgentForge에서 아직 vendor/import되지 않아
(SPEC.md §12 오픈 이슈) `performance_score`는 1차로 관리자가 수동 입력한다 —
비즈니스 KPI 축(확정 배정 건수)만 실제 데이터로 계산한다.
"""

from __future__ import annotations

import sqlite3


def set_human_consent(conn: sqlite3.Connection, human_id: int, consent: bool) -> None:
    """동의를 철회(consent=False)하면 기존 점수도 즉시 지운다(NF9)."""
    conn.execute(
        "UPDATE human_pool SET performance_consent = ?, "
        "performance_score = CASE WHEN ? THEN performance_score ELSE NULL END "
        "WHERE id = ?",
        (1 if consent else 0, 1 if consent else 0, human_id),
    )
    conn.commit()


def set_human_performance(
    conn: sqlite3.Connection, human_id: int, score: float
) -> None:
    row = conn.execute(
        "SELECT performance_consent FROM human_pool WHERE id = ?", (human_id,)
    ).fetchone()
    if row is None:
        raise ValueError("human_not_found")
    if not row["performance_consent"]:
        raise PermissionError("no_consent")
    conn.execute(
        "UPDATE human_pool SET performance_score = ? WHERE id = ?", (score, human_id)
    )
    conn.commit()


def set_agent_performance(
    conn: sqlite3.Connection, agent_id: int, score: float
) -> None:
    conn.execute(
        "UPDATE agent_pool SET performance_score = ? WHERE id = ?", (score, agent_id)
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


def full_review(conn: sqlite3.Connection) -> list[dict]:
    """Admin·매니저용 — 전체 사람+Agent 성과리뷰(F8)."""
    result = []
    for row in conn.execute("SELECT * FROM human_pool").fetchall():
        result.append(
            {
                "type": "human",
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "performance_score": (
                    row["performance_score"] if row["performance_consent"] else None
                ),
                "performance_consent": bool(row["performance_consent"]),
                "confirmed_assignments": _confirmed_count(conn, "human", row["id"]),
            }
        )
    for row in conn.execute(
        "SELECT * FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        result.append(
            {
                "type": "agent",
                "id": row["id"],
                "name": None,
                "role": row["role_name"],
                "performance_score": row["performance_score"],
                "performance_consent": None,
                "confirmed_assignments": _confirmed_count(conn, "agent", row["id"]),
            }
        )
    return result


def review_for_member(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """팀원용 — 본인과 연결된 Human Pool 레코드만(RBAC 매트릭스, F8)."""
    human_row = conn.execute(
        "SELECT id FROM human_pool WHERE user_id = ?", (user_id,)
    ).fetchone()
    if human_row is None:
        return []
    return [
        r
        for r in full_review(conn)
        if r["type"] == "human" and r["id"] == human_row["id"]
    ]
