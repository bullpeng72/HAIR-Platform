"""F5/F6 — 배정 요청·승인 확정 처리(ADR 결정 7: 3중 기록 트랜잭션 원자성).

`assignments`·`assignment_history`는 이 모듈이 하나의 트랜잭션으로 묶는다(commit은
호출자가 한다 — decide_approval에서 approvals 상태 변경까지 같은 트랜잭션으로 커밋).
audit_log 합류는 v3(Job Queue+F13)에서 완성된다.

F5(조직도)·F6(배분)은 이 모듈이 공유하는 `assignments` 테이블 하나를 통해서만
데이터를 주고받는다(SPEC.md §10 — 이중 쓰기 방지).
"""

from __future__ import annotations

import sqlite3


def _record_history(
    conn: sqlite3.Connection,
    assignment_id: int,
    from_status: str | None,
    to_status: str,
    changed_by: int,
    reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO assignment_history "
        "(assignment_id, from_status, to_status, changed_by, reason) VALUES (?, ?, ?, ?, ?)",
        (assignment_id, from_status, to_status, changed_by, reason),
    )


def create_assignment_request(
    conn: sqlite3.Connection,
    task_id: int,
    resource_type: str,
    resource_id: int,
    requested_by: int,
) -> dict:
    """배정 요청(assignments.status='requested') + 승인요청(approvals) + 이력을 커밋한다."""
    try:
        cur = conn.execute(
            "INSERT INTO assignments "
            "(task_id, resource_type, resource_id, requested_by, status) "
            "VALUES (?, ?, ?, ?, 'requested')",
            (task_id, resource_type, resource_id, requested_by),
        )
        assignment_id = cur.lastrowid
        assert assignment_id is not None

        approval_cur = conn.execute(
            "INSERT INTO approvals (requested_by, subject_type, subject_id) "
            "VALUES (?, 'assignment', ?)",
            (requested_by, assignment_id),
        )
        approval_id = approval_cur.lastrowid

        conn.execute(
            "UPDATE assignments SET approval_id = ? WHERE id = ?",
            (approval_id, assignment_id),
        )
        _record_history(conn, assignment_id, None, "requested", requested_by)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "assignment_id": assignment_id,
        "approval_id": approval_id,
        "status": "requested",
    }


def resolve_assignment_decision(
    conn: sqlite3.Connection, assignment_id: int, approve: bool, decided_by: int
) -> dict:
    """승인/반려 결과를 assignments·assignment_history에 반영한다(커밋은 호출자 책임).

    승인 시 같은 task에 이미 confirmed인 배정이 있으면 재배치로 간주해
    superseded로 전환한다(F5/F6 assignments 테이블 공유 원칙).
    """
    assignment = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if assignment is None:
        raise ValueError("assignment_not_found")

    if approve:
        previous = conn.execute(
            "SELECT id FROM assignments WHERE task_id = ? AND status = 'confirmed' AND id != ?",
            (assignment["task_id"], assignment_id),
        ).fetchall()
        for prev in previous:
            conn.execute(
                "UPDATE assignments SET status = 'superseded' WHERE id = ?",
                (prev["id"],),
            )
            _record_history(
                conn,
                prev["id"],
                "confirmed",
                "superseded",
                decided_by,
                reason="reassigned",
            )
        new_status = "confirmed"
    else:
        new_status = "rejected"

    conn.execute(
        "UPDATE assignments SET status = ? WHERE id = ?", (new_status, assignment_id)
    )
    _record_history(conn, assignment_id, assignment["status"], new_status, decided_by)
    return {"assignment_id": assignment_id, "status": new_status}
