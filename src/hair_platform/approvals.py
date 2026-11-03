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


def describe_subject(
    conn: sqlite3.Connection, subject_type: str, subject_id: int
) -> dict:
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
                   tm.name AS team_name,
                   pt.name AS pipeline_name
            FROM assignments a
            JOIN tasks t ON t.id = a.task_id
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            LEFT JOIN teams tm ON tm.id = a.resource_id AND a.resource_type = 'team'
            LEFT JOIN pipeline_templates pt ON pt.id = a.resource_id AND a.resource_type = 'pipeline'
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
            "pipeline": row["pipeline_name"],
        }
        return {
            "task_id": row["task_id"],
            "task_role_name": row["role_name"],
            "resource_type": row["resource_type"],
            "resource_name": resource_names.get(row["resource_type"]),
        }
    if subject_type == "deliverable":
        # `content`/`content_type`(2026-10-28, 사용자 보고 "결재에서
        # 승인을 요청하고 있으나, 해당 컨텐츠를 검토할 수 있는 UI가
        # 없어") — 지금까지 결재자는 "누가 어느 업무에 낸 산출물인지"
        # 만 알고, 실제 내용은 다른 탭(Task 협업 워크스페이스)을 또
        # 열어야만 볼 수 있었다. 결재함 화면이 바로 보여줄 수 있게
        # 내용 자체도 같이 내려준다.
        row = conn.execute(
            """
            SELECT d.task_id, d.author_type, d.author_id, d.content, d.content_type,
                   t.role_name, u.username AS human_username,
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
            "content": row["content"],
            "content_type": row["content_type"],
        }
    if subject_type == "pipeline_run_stage":
        # 사용자 요청(2026-10-18, "최소 HITL/HOTL" 파이프라인) — 결재함에
        # "어느 할 일의 몇 번째 단계(파이프라인 이름)"가 떠야 결재자가
        # 무엇을 보는지 알 수 있다(assignment/deliverable과 같은 원칙).
        #
        # `content`(2026-10-28, 사용자 보고 "결재에서 승인을 요청하고
        # 있으나, 해당 컨텐츠를 검토할 수 있는 UI가 없어") — 이
        # 단계의 실제 산출물은 `pipeline_run_stages.output`에 그대로
        # 있는데 결재함은 그걸 전혀 안 보여줬다. `content_type`은
        # 고정 "markdown"(파이프라인 엔진이 만드는 산출물은 LLM이
        # 쓴 마크다운 텍스트이기 때문 — `_execute_stage_attempt()`가
        # `deliverables.content_type`에 쓰는 'text'보다 실제 내용에
        # 더 맞게 렌더링된다).
        row = conn.execute(
            """
            SELECT pr.task_id, t.role_name, pt.name AS pipeline_name,
                   ps.name AS stage_name, ps.seq_order AS stage_seq_order,
                   prs.output
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
            "content": row["output"],
            "content_type": "markdown",
        }
    return empty


def list_inbox(conn: sqlite3.Connection, user_id: int, role: str) -> list[dict]:
    """Phase 5(통합 인박스)를 위해
    `app.py`의 `GET /api/approvals?scope=inbox`에 있던 로직을 재사용
    가능하게 뽑아냈다(동작 변경 없음, 순수 추출) — "내가 지금 결정할 수
    있는 건"만 보여준다: 아직 `pending`이고, 내가 요청자가 아니며
    (self-approval 제외), Admin이면 전부, 매니저면 승인자
    미지정이거나 나로 지정된 건만. Admin/매니저가 아니면 빈 리스트.

    사용자 요청(2026-11-03, "결재 내 승인 대기의 경우 요청자가, 내가
    요청한 건의 경우 결재권자가 표시되도록") — `requested_by`/
    `approver_id`/`approved_by`는 전부 숫자 user id뿐이라 화면에
    이름을 못 보여줬다. `users`를 세 번 조인해 세 이름을 전부
    내려준다(어느 화면이 어떤 열을 쓸지는 `webui.py`가 고른다 —
    `scope=mine`도 같은 모양으로 맞춘다, `app.py` `list_approvals`
    참고)."""
    base_select = (
        "SELECT a.*, "
        "ru.username AS requested_by_username, "
        "apv.username AS approver_username, "
        "adu.username AS approved_by_username "
        "FROM approvals a "
        "LEFT JOIN users ru ON ru.id = a.requested_by "
        "LEFT JOIN users apv ON apv.id = a.approver_id "
        "LEFT JOIN users adu ON adu.id = a.approved_by "
    )
    if role == "admin":
        rows = conn.execute(
            base_select
            + "WHERE a.status = 'pending' AND a.requested_by != ? ORDER BY a.id ASC",
            (user_id,),
        ).fetchall()
    elif role == "manager":
        rows = conn.execute(
            base_select
            + "WHERE a.status = 'pending' AND a.requested_by != ? "
            "AND (a.approver_id IS NULL OR a.approver_id = ?) ORDER BY a.id ASC",
            (user_id, user_id),
        ).fetchall()
    else:
        return []
    return [dict(row) for row in rows]
