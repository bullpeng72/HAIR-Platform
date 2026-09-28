"""F7 — 다단계 결재라인(조직도 매니저 관계 기반 자동 라우팅, ADR 결정 3·SPEC.md §10).

`users.manager_id` 체인을 따라 승인 단계를 구성한다. 매니저 체인이 없으면(예: Admin
본인 요청) 단일 단계로 폴백하고 그 단계는 특정 승인자를 지정하지 않는다
(`approver_id=NULL`) — v0/v2의 단일 승인자 동작과 동일하게 유지된다.
"""

from __future__ import annotations

import sqlite3


def build_manager_chain(
    conn: sqlite3.Connection, user_id: int, max_depth: int = 10
) -> list[int]:
    """user_id의 상사 → 그 상사의 상사 … 순서로 매니저 id 체인을 반환한다(순환 방지)."""
    chain: list[int] = []
    seen = {user_id}
    row = conn.execute(
        "SELECT manager_id FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    depth = 0
    while row and row["manager_id"] is not None and depth < max_depth:
        manager_id = row["manager_id"]
        if manager_id in seen:
            break
        chain.append(manager_id)
        seen.add(manager_id)
        row = conn.execute(
            "SELECT manager_id FROM users WHERE id = ?", (manager_id,)
        ).fetchone()
        depth += 1
    return chain


def create_approval_chain(
    conn: sqlite3.Connection, requested_by: int, subject_type: str, subject_id: int
) -> list[int]:
    """승인 단계 행을 생성한다. 1단계만 즉시 `pending`, 나머지는 `waiting`."""
    chain = build_manager_chain(conn, requested_by)
    approver_ids: list[int | None] = list(chain) if chain else [None]

    approval_ids: list[int] = []
    for step, approver_id in enumerate(approver_ids, start=1):
        cur = conn.execute(
            "INSERT INTO approvals "
            "(requested_by, subject_type, subject_id, step, approver_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                requested_by,
                subject_type,
                subject_id,
                step,
                approver_id,
                "pending" if step == 1 else "waiting",
            ),
        )
        assert cur.lastrowid is not None
        approval_ids.append(cur.lastrowid)
    return approval_ids


def activate_next_step(
    conn: sqlite3.Connection, subject_type: str, subject_id: int, current_step: int
) -> bool:
    """다음 단계를 `pending`으로 활성화한다. 다음 단계가 없으면 False(체인 종료)."""
    next_row = conn.execute(
        "SELECT id FROM approvals WHERE subject_type = ? AND subject_id = ? AND step = ?",
        (subject_type, subject_id, current_step + 1),
    ).fetchone()
    if next_row is None:
        return False
    conn.execute(
        "UPDATE approvals SET status = 'pending' WHERE id = ?", (next_row["id"],)
    )
    return True


def skip_remaining_steps(
    conn: sqlite3.Connection, subject_type: str, subject_id: int, from_step: int
) -> None:
    """반려 시 아직 결정되지 않은 나머지 단계를 전부 `skipped`로 닫는다."""
    conn.execute(
        "UPDATE approvals SET status = 'skipped' "
        "WHERE subject_type = ? AND subject_id = ? AND step > ? "
        "AND status IN ('pending', 'waiting')",
        (subject_type, subject_id, from_step),
    )


def list_inbox(conn: sqlite3.Connection, user_id: int, role: str) -> list[dict]:
    """Phase 5(통합 인박스, `docs/HAIR_MASTER_PLAN.md` §6)를 위해
    `app.py`의 `GET /api/approvals?scope=inbox`에 있던 로직을 재사용
    가능하게 뽑아냈다(동작 변경 없음, 순수 추출) — "내가 지금 결정할 수
    있는 건"만 보여준다: 아직 `pending`이고, 내가 요청자가 아니며
    (self-approval 제외, ADR 결정 3-A), Admin이면 전부, 매니저면 승인자
    미지정이거나 나로 지정된 건만. Admin/매니저가 아니면 빈 리스트."""
    if role == "admin":
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' AND requested_by != ? "
            "ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    elif role == "manager":
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' AND requested_by != ? "
            "AND (approver_id IS NULL OR approver_id = ?) ORDER BY id ASC",
            (user_id, user_id),
        ).fetchall()
    else:
        return []
    return [dict(row) for row in rows]
