"""데이터 삭제/정리 — 사용자 요청(2026-09-28, "각 메뉴에서 데이터 삭제나,
초기화 기능 추가").

**범위(사용자가 명시적으로 선택)**: 도구/스킬 Pool + 사람/AI 에이전트 Pool +
업무 요청/할 일(brief/task)까지. **활동 기록(감사로그)은 제외** — NF10
(append-only)에 따라 `db.py`의 DB 트리거가 UPDATE/DELETE 자체를 막고
있어 애초에 삭제 API를 만들 수 없다(만들어도 트리거가 막는다).

**설계 원칙**:
1. **FK 순서를 지킨 완전한 연쇄 삭제** — `PRAGMA foreign_keys=ON`이 걸려
   있어(`db.get_connection`), 자식 행이 남아있는 채로 부모를 지우면
   `sqlite3.IntegrityError`가 난다. brief/task를 지우면 그 아래
   tasks·assignments·assignment_history·job_items·(subject로 참조하는)
   approvals까지 전부 같이 지운다(고아 행을 남기지 않는다). `agent_pool.
   source_job_item_id`처럼 지워질 행을 참조하는 컬럼은 먼저 NULL로
   비워서(그 자체 삭제 대상이 아니므로) FK 위반을 피한다.
2. **진행 중인 배정은 보호한다** — `status='confirmed'`인 배정이 걸려
   있는 task/brief/human/agent는 삭제를 막는다(먼저 F6/F7 화면에서
   배정을 정리하도록 유도) — 조직도가 조용히 깨지는 것을 막기 위한
   안전장치다. `status='requested'`(아직 승인 전)나 `'superseded'`
   (이미 지나간 이력)는 막지 않는다.
3. **감사로그에는 남긴다** — 실제 행은 지워도(append-only 원칙과 별개로)
   무엇을 지웠는지는 `audit_log`에 기록한다(삭제 자체가 되돌릴 수 없는
   행위이므로, 최소한 "언제 누가 무엇을 지웠는지"는 남아야 한다).
"""

from __future__ import annotations

import sqlite3

from hair_platform import audit


def _confirmed_assignment_exists(
    conn: sqlite3.Connection,
    *,
    task_ids: list[int] | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
) -> bool:
    if task_ids is not None:
        if not task_ids:
            return False
        placeholders = ",".join("?" * len(task_ids))
        row = conn.execute(
            f"SELECT 1 FROM assignments WHERE task_id IN ({placeholders}) "
            "AND status = 'confirmed' LIMIT 1",
            task_ids,
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM assignments WHERE resource_type = ? AND resource_id = ? "
        "AND status = 'confirmed' LIMIT 1",
        (resource_type, resource_id),
    ).fetchone()
    return row is not None


def _delete_assignments_for_tasks(conn: sqlite3.Connection, task_ids: list[int]) -> int:
    """주어진 task들의 assignments·assignment_history·(그 assignment를
    subject로 하는) approvals를 FK 순서를 지켜 지운다. 지운 assignment
    개수를 반환한다."""
    if not task_ids:
        return 0
    placeholders = ",".join("?" * len(task_ids))
    assignment_ids = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM assignments WHERE task_id IN ({placeholders})",
            task_ids,
        ).fetchall()
    ]
    if not assignment_ids:
        return 0
    a_placeholders = ",".join("?" * len(assignment_ids))
    conn.execute(
        f"DELETE FROM assignment_history WHERE assignment_id IN ({a_placeholders})",
        assignment_ids,
    )
    # 주의(실측으로 발견) — approvals를 assignments보다 먼저 지우면
    # `assignments.approval_id`가 그 approval을 여전히 참조하고 있어
    # FK 위반(`sqlite3.IntegrityError`)이 난다. assignments를 먼저 지워
    # 참조를 끊은 뒤에 approvals를 지워야 한다.
    approval_ids = [
        r["approval_id"]
        for r in conn.execute(
            f"SELECT approval_id FROM assignments WHERE id IN ({a_placeholders}) "
            "AND approval_id IS NOT NULL",
            assignment_ids,
        ).fetchall()
    ]
    conn.execute(
        f"DELETE FROM assignments WHERE id IN ({a_placeholders})", assignment_ids
    )
    if approval_ids:
        ap_placeholders = ",".join("?" * len(approval_ids))
        conn.execute(
            f"DELETE FROM approvals WHERE id IN ({ap_placeholders})", approval_ids
        )
    conn.execute(
        "DELETE FROM approvals WHERE subject_type = 'assignment' AND subject_id IN "
        f"({a_placeholders})",
        assignment_ids,
    )
    return len(assignment_ids)


def delete_task(conn: sqlite3.Connection, actor_id: int, task_id: int) -> None:
    """할 일(task) 하나를 그 아래 assignments·job_items까지 전부 지운다.
    `confirmed` 상태 배정이 있으면 막는다(먼저 정리하도록 유도)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if _confirmed_assignment_exists(conn, task_ids=[task_id]):
        raise PermissionError("confirmed_assignment_exists")

    deleted_assignments = _delete_assignments_for_tasks(conn, [task_id])
    conn.execute(
        "UPDATE agent_pool SET source_job_item_id = NULL WHERE source_job_item_id IN "
        "(SELECT id FROM job_items WHERE task_id = ?)",
        (task_id,),
    )
    conn.execute("DELETE FROM job_items WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    audit.record(
        conn,
        actor_id,
        "task_deleted",
        "tasks",
        task_id,
        before={"role_name": task["role_name"], "brief_id": task["brief_id"]},
        after={"deleted_assignments": deleted_assignments},
    )
    conn.commit()


def delete_brief(conn: sqlite3.Connection, actor_id: int, brief_id: int) -> None:
    """업무 요청(brief) 하나를 그 아래 tasks·jobs·assignments까지 전부
    지운다. 아래 어떤 task에든 `confirmed` 배정이 있으면 막는다."""
    brief = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if brief is None:
        raise ValueError("brief_not_found")

    task_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM tasks WHERE brief_id = ?", (brief_id,)
        ).fetchall()
    ]
    if _confirmed_assignment_exists(conn, task_ids=task_ids):
        raise PermissionError("confirmed_assignment_exists")

    deleted_assignments = _delete_assignments_for_tasks(conn, task_ids)
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        conn.execute(
            "UPDATE agent_pool SET source_job_item_id = NULL WHERE source_job_item_id IN "
            f"(SELECT id FROM job_items WHERE task_id IN ({placeholders}))",
            task_ids,
        )
        conn.execute(
            f"DELETE FROM job_items WHERE task_id IN ({placeholders})", task_ids
        )
    conn.execute("DELETE FROM jobs WHERE brief_id = ?", (brief_id,))
    conn.execute("DELETE FROM tasks WHERE brief_id = ?", (brief_id,))
    conn.execute(
        "DELETE FROM approvals WHERE subject_type = 'brief' AND subject_id = ?",
        (brief_id,),
    )
    conn.execute("DELETE FROM briefs WHERE id = ?", (brief_id,))
    audit.record(
        conn,
        actor_id,
        "brief_deleted",
        "briefs",
        brief_id,
        before={"raw_text": brief["raw_text"]},
        after={
            "deleted_tasks": len(task_ids),
            "deleted_assignments": deleted_assignments,
        },
    )
    conn.commit()


def delete_human(conn: sqlite3.Connection, actor_id: int, human_id: int) -> None:
    """사람 인력(human_pool) 레코드를 지운다. `confirmed` 배정이 있으면
    막는다."""
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if row is None:
        raise ValueError("human_not_found")
    if _confirmed_assignment_exists(conn, resource_type="human", resource_id=human_id):
        raise PermissionError("confirmed_assignment_exists")

    conn.execute("DELETE FROM human_pool WHERE id = ?", (human_id,))
    audit.record(
        conn,
        actor_id,
        "human_deleted",
        "human_pool",
        human_id,
        before={"name": row["name"], "role": row["role"]},
        after=None,
    )
    conn.commit()


def delete_agent(conn: sqlite3.Connection, actor_id: int, agent_id: int) -> None:
    """AI 에이전트(agent_pool) 레코드를 그 아래 지식 색인(knowledge_chunks)·
    도구 연결(agent_tool_links)까지 전부 지운다. `confirmed` 배정이 있으면
    막는다."""
    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise ValueError("agent_not_found")
    if _confirmed_assignment_exists(conn, resource_type="agent", resource_id=agent_id):
        raise PermissionError("confirmed_assignment_exists")

    conn.execute("DELETE FROM knowledge_chunks WHERE source_agent_id = ?", (agent_id,))
    conn.execute("DELETE FROM agent_tool_links WHERE agent_id = ?", (agent_id,))
    conn.execute("DELETE FROM agent_pool WHERE id = ?", (agent_id,))
    audit.record(
        conn,
        actor_id,
        "agent_deleted",
        "agent_pool",
        agent_id,
        before={"role_name": row["role_name"], "status": row["status"]},
        after=None,
    )
    conn.commit()
