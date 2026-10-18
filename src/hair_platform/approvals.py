"""다단계 결재라인(조직도 매니저 관계 기반 자동 라우팅).

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


def describe_subject(conn: sqlite3.Connection, subject_type: str, subject_id: int) -> dict:
    """결재 대상을 사람이 알아볼 수 있는 정보로 풀어준다.

    사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
    에이전트명이 표시되지 않는 경우도 있어... 사람이 활용하는 관점으로
    개선") — `approvals.subject_type`/`subject_id`는 `assignments.id`
    또는 `deliverables.id`일 뿐이라(그 자체로는 사람이 읽을 수 없는
    내부 FK), 승인권자가 "결재 대기 #12"만 보고는 무엇을 승인하는지
    전혀 알 수 없었다 — 다른 탭을 또 열어 역추적해야 했다. `assignments`/
    `deliverables`를 각각 `tasks`+사람/Agent 이름까지 조인해, "무슨
    업무에 누구를/누가 쓴 결과물을" 즉시 보여준다.

    대상이 이미 지워졌으면(브리프 삭제 등) 전부 `None`으로 돌아간다 —
    존재하지 않는 레코드를 에러로 막지 않고 화면에서 "정보 없음"으로
    자연스럽게 떨어지게 한다."""
    empty = {
        "task_id": None,
        "task_role_name": None,
        "resource_type": None,
        "resource_name": None,
    }
    if subject_type == "assignment":
        row = conn.execute(
            """
            SELECT a.task_id, a.resource_type, a.resource_id, t.role_name,
                   hp.name AS human_name,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name,
                   tm.name AS team_name
            FROM assignments a
            JOIN tasks t ON t.id = a.task_id
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            LEFT JOIN teams tm ON tm.id = a.resource_id AND a.resource_type = 'team'
            WHERE a.id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return empty
        resource_names = {
            "human": row["human_name"],
            "agent": row["agent_role_name"],
            "team": row["team_name"],
        }
        return {
            "task_id": row["task_id"],
            "task_role_name": row["role_name"],
            "resource_type": row["resource_type"],
            "resource_name": resource_names.get(row["resource_type"]),
        }
    if subject_type == "deliverable":
        row = conn.execute(
            """
            SELECT d.task_id, d.author_type, d.author_id, t.role_name,
                   u.username AS human_username,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name
            FROM deliverables d
            JOIN tasks t ON t.id = d.task_id
            LEFT JOIN users u ON u.id = d.author_id AND d.author_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = d.author_id AND d.author_type = 'agent'
            WHERE d.id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return empty
        resource_names = {
            "human": row["human_username"],
            "agent": row["agent_role_name"],
        }
        return {
            "task_id": row["task_id"],
            "task_role_name": row["role_name"],
            "resource_type": row["author_type"],
            "resource_name": resource_names.get(row["author_type"]),
        }
    if subject_type == "pipeline_run_stage":
        # 사용자 요청(2026-10-18, "최소 HITL/HOTL" 파이프라인) — 결재함에
        # "어느 할 일의 몇 번째 단계(파이프라인 이름)"가 떠야 결재자가
        # 무엇을 보는지 알 수 있다(assignment/deliverable과 같은 원칙).
        row = conn.execute(
            """
            SELECT pr.task_id, t.role_name, pt.name AS pipeline_name,
                   ps.name AS stage_name, ps.seq_order AS stage_seq_order
            FROM pipeline_run_stages prs
            JOIN pipeline_runs pr ON pr.id = prs.pipeline_run_id
            JOIN pipeline_templates pt ON pt.id = pr.pipeline_template_id
            JOIN pipeline_stages ps ON ps.id = prs.stage_id
            JOIN tasks t ON t.id = pr.task_id
            WHERE prs.id = ?
            """,
            (subject_id,),
        ).fetchone()
        if row is None:
            return empty
        return {
            "task_id": row["task_id"],
            "task_role_name": row["role_name"],
            "resource_type": "pipeline_run_stage",
            "resource_name": f"{row['pipeline_name']} — {row['stage_seq_order']}단계 "
            f"({row['stage_name']})",
        }
    return empty


def list_inbox(conn: sqlite3.Connection, user_id: int, role: str) -> list[dict]:
    """Phase 5(통합 인박스)를 위해
    `app.py`의 `GET /api/approvals?scope=inbox`에 있던 로직을 재사용
    가능하게 뽑아냈다(동작 변경 없음, 순수 추출) — "내가 지금 결정할 수
    있는 건"만 보여준다: 아직 `pending`이고, 내가 요청자가 아니며
    (self-approval 제외), Admin이면 전부, 매니저면 승인자
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
