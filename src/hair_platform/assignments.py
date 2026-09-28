"""F5/F6 — 배정 요청·승인 확정 처리(ADR 결정 7: 3중 기록 트랜잭션 원자성).

`assignments`·`assignment_history`·`approvals`(F7 다단계 결재라인, `approvals.py`)는
이 모듈이 하나의 트랜잭션으로 묶는다(commit은 호출자가 한다 — decide_approval에서
audit_log 기록까지 같은 트랜잭션으로 커밋).

F5(조직도)·F6(배분)은 이 모듈이 공유하는 `assignments` 테이블 하나를 통해서만
데이터를 주고받는다(SPEC.md §10 — 이중 쓰기 방지).
"""

from __future__ import annotations

import sqlite3

from hair_platform import approvals as approvals_mod
from hair_platform import audit


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
    """배정 요청(assignments.status='requested') + 결재라인(approvals) + 이력을 커밋한다.

    결재라인은 요청자의 조직도 매니저 체인을 따라 다단계로 구성된다(F7, ADR 결정 3).
    반환하는 `approval_id`는 체인의 1단계(즉시 결정 가능한 단계) id다 — 하위 호환용.
    """
    try:
        cur = conn.execute(
            "INSERT INTO assignments "
            "(task_id, resource_type, resource_id, requested_by, status) "
            "VALUES (?, ?, ?, ?, 'requested')",
            (task_id, resource_type, resource_id, requested_by),
        )
        assignment_id = cur.lastrowid
        assert assignment_id is not None

        approval_ids = approvals_mod.create_approval_chain(
            conn, requested_by, "assignment", assignment_id
        )

        conn.execute(
            "UPDATE assignments SET approval_id = ? WHERE id = ?",
            (approval_ids[0], assignment_id),
        )
        _record_history(conn, assignment_id, None, "requested", requested_by)
        result = {
            "assignment_id": assignment_id,
            "approval_id": approval_ids[0],
            "approval_ids": approval_ids,
            "status": "requested",
        }
        audit.record(
            conn,
            requested_by,
            "assignment_requested",
            "assignment",
            assignment_id,
            after=result,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def resolve_assignment_decision(
    conn: sqlite3.Connection, assignment_id: int, approve: bool, decided_by: int
) -> dict:
    """승인/반려 결과를 assignments·assignment_history에 반영한다(커밋은 호출자 책임).

    승인 시 같은 task·같은 resource_type에 이미 confirmed인 배정이 있으면
    재배치로 간주해 superseded로 전환한다(F5/F6 assignments 테이블 공유
    원칙).

    **실측으로 발견한 버그(2026-09-28, "Phase1~4까지... 분석하고
    개선해줘" 리뷰 중)**: 원래는 `resource_type` 구분 없이 같은 task의
    confirmed 배정을 전부 supersede했다 — "사람 배정을 재배치"만 있던
    시절(F5/F6)엔 문제가 없었지만, 협업 워크스페이스(Phase 1-4)가
    "사람 당사자 + Agent 요청자가 같은 task에 함께 confirmed"인 걸
    전제로 하면서(`deliverables.task_participant_relationship()`) 사람
    배정을 확정한 뒤 Agent 배정을 확정하면(또는 반대 순서) 먼저 확정된
    쪽이 조용히 superseded돼 협업 자체가 깨지는 실제 버그가 됐다 —
    `test_probe_both_human_and_agent_confirmed_on_same_task`로 재현
    확인. `resource_type`이 같을 때만 supersede하도록 좁혀서 고쳤다
    (기존 "같은 역할 재배치" 테스트 동작은 그대로 유지됨 —
    `test_v2_pipeline.py`는 항상 human→human만 테스트했었다)."""
    assignment = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if assignment is None:
        raise ValueError("assignment_not_found")

    if approve:
        previous = conn.execute(
            "SELECT id FROM assignments WHERE task_id = ? AND resource_type = ? "
            "AND status = 'confirmed' AND id != ?",
            (assignment["task_id"], assignment["resource_type"], assignment_id),
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
