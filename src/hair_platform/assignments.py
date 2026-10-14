"""배정 요청·승인 확정 처리(3중 기록 트랜잭션 원자성).

`assignments`·`assignment_history`·`approvals`(다단계 결재라인, `approvals.py`)는
이 모듈이 하나의 트랜잭션으로 묶는다(commit은 호출자가 한다 — decide_approval에서
audit_log 기록까지 같은 트랜잭션으로 커밋).

조직도·배분은 이 모듈이 공유하는 `assignments` 테이블 하나를 통해서만
데이터를 주고받는다(이중 쓰기 방지).
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
    team_leader_id: int | None = None,
    replaces_assignment_id: int | None = None,
) -> dict:
    """배정 요청(assignments.status='requested') + 결재라인(approvals) + 이력을 커밋한다.

    결재라인은 요청자의 조직도 매니저 체인을 따라 다단계로 구성된다.
    반환하는 `approval_id`는 체인의 1단계(즉시 결정 가능한 단계) id다 — 하위 호환용.

    사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) —
    `resource_type="team"`
    이면 `resource_id`는 `teams.id`이고, 결재라인은 **팀 리더**(`team_
    leader_id`, 비워두면 `requested_by`가 기본값)의 매니저 체인을 따른다
    — "팀의 승인 주체는 팀 리더"라는 사용자 결정을 그대로 반영한다
    (요청을 대신 제출한 관리자가 아니라 리더의 조직도 위치가 결재
    경로를 결정). `replaces_assignment_id`를 주면 그 배정이 같은
    `task_id`를 가리키는지 미리 검증한다 — 승인 시점에야 틀렸다는 걸
    알면 되돌리기 번거롭기 때문에 요청 시점에 막는다."""
    if resource_type == "team":
        team = conn.execute(
            "SELECT id FROM teams WHERE id = ?", (resource_id,)
        ).fetchone()
        if team is None:
            raise ValueError("team_not_found")
        if team_leader_id is None:
            team_leader_id = requested_by
    if replaces_assignment_id is not None:
        target = conn.execute(
            "SELECT task_id FROM assignments WHERE id = ?", (replaces_assignment_id,)
        ).fetchone()
        if target is None:
            raise ValueError("replaces_assignment_not_found")
        if target["task_id"] != task_id:
            raise ValueError("replaces_assignment_task_mismatch")

    approval_chain_subject = team_leader_id if resource_type == "team" else requested_by

    try:
        cur = conn.execute(
            "INSERT INTO assignments "
            "(task_id, resource_type, resource_id, requested_by, status, "
            "team_leader_id, replaces_assignment_id) "
            "VALUES (?, ?, ?, ?, 'requested', ?, ?)",
            (
                task_id,
                resource_type,
                resource_id,
                requested_by,
                team_leader_id,
                replaces_assignment_id,
            ),
        )
        assignment_id = cur.lastrowid
        assert assignment_id is not None

        approval_ids = approvals_mod.create_approval_chain(
            conn, approval_chain_subject, "assignment", assignment_id
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


def create_delegation(
    conn: sqlite3.Connection, task_id: int, agent_id: int, delegated_by: int
) -> dict:
    """사용자 요청(2026-10-12, "Task 카드에 위임/
    배정 버튼 분리") — Linear의 "위임(delegation)"/"배정(ownership)" 분리,
    Devin의 Ask/Agent 모드 착안. `create_assignment_request()`("배정",
    결재 체인 필수)와 달리 **결재 없이 즉시 `status='confirmed'`로
    만든다** — `approval_id`는 NULL로 남는다(결재 체인 자체가 없다).

    사용자 결정(2026-10-12, "위임은 Gate A-G 그대로 타고, audit_log에도
    남겨줘"): ① Gate A-G는 이 함수가 신경 쓸 필요가 없다 — `agent_
    evaluation.run_and_score()`가 Deliverable 생성 시점에 무조건
    적용되는 로직이라, `status='confirmed'`로만 만들어두면 이후
    `execute-agent`가 평소처럼 그 Deliverable을 만들고 자동으로 Gate가
    매겨진다(코드 변경 불필요 — 이미 그렇게 동작한다). ② `audit.record()`
    를 `"agent_delegated"`라는 별도 action으로 남겨 일반
    `"assignment_confirmed"`(결재를 거친 확정)와 구분한다 — 나중에
    "이 배정이 결재를 거쳤는지 위임으로 바로 됐는지"를 감사로그만 보고
    알 수 있어야 한다.

    agent 전용이다(resource_type='human'/'team'은 이 경량 경로를 쓸
    이유가 없다 — 사람/팀은 원래부터 배정·결재가 "무겁다"는
    불만의 대상이 아니었다, "Linear 위임/배정 분리"도 AI
    Agent 전용 개념이다). self-approval 방지는 적용하지
    않는다 — 승인 단계 자체가 없어 "누가 승인하는지"가 성립하지 않는다
    (위임은 요청자 본인이 즉시 확정한다는 게 설계 그 자체)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    agent = conn.execute(
        "SELECT id, status FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")
    if agent["status"] != "active":
        raise ValueError("agent_not_active")

    try:
        cur = conn.execute(
            "INSERT INTO assignments "
            "(task_id, resource_type, resource_id, requested_by, status) "
            "VALUES (?, 'agent', ?, ?, 'confirmed')",
            (task_id, agent_id, delegated_by),
        )
        assignment_id = cur.lastrowid
        assert assignment_id is not None
        _record_history(
            conn, assignment_id, None, "confirmed", delegated_by, reason="delegated"
        )
        result = {"assignment_id": assignment_id, "status": "confirmed"}
        audit.record(
            conn,
            delegated_by,
            "agent_delegated",
            "assignment",
            assignment_id,
            after={"task_id": task_id, "agent_id": agent_id, **result},
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

    **정정(2026-10-06, 사용자 요청 — "다중 confirmed 허용의 경우 개별
    배정에도")**: 2026-09-28 수정까지는 "같은 task·같은 resource_type에
    이미 confirmed인 배정이 있으면 자동으로 superseded"였다(재배치
    전제) — 다자간 팀 협업 기획 Phase 1+2로
    "복수의 사람 + 복수의 Agent가 한 task에 동시에 confirmed"를 정식
    지원하면서 이 자동 supersede를 **완전히 없앴다**. 이제 승인은
    기본적으로 아무것도 밀어내지 않고 기존 confirmed 배정들과 공존한다
    — "교체"하고 싶으면 요청 시점에 `AssignmentRequest.replaces_
    assignment_id`를 명시해야 한다(`create_assignment_request()`가 그
    값을 이 배정 행에 저장해두고, 여기서는 그 값이 가리키는 **그
    배정 하나만** confirmed였다면 superseded로 바꾼다 — "같은 task·
    같은 resource_type이면 전부"가 아니라 "명시적으로 지목한 것 하나만"
    이라 다른 resource_type을 건드리던 과거 버그(2026-09-28 수정 사유)
    가 애초에 재발할 수 없는 구조다)."""
    assignment = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if assignment is None:
        raise ValueError("assignment_not_found")

    if approve:
        if assignment["replaces_assignment_id"] is not None:
            prev = conn.execute(
                "SELECT id FROM assignments WHERE id = ? AND status = 'confirmed'",
                (assignment["replaces_assignment_id"],),
            ).fetchone()
            if prev is not None:
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


def update_team_leader(
    conn: sqlite3.Connection, actor_id: int, assignment_id: int, new_leader_id: int
) -> None:
    """사용자 결정(2026-10-06, "팀 리더는 배정 요청자가 기본값, 변경
    가능") — 팀 배정의 `team_leader_id`를 바꾼다. **`status='requested'`
    일 때만 허용한다**(`confirmed` 이후로는 이미 그 리더 기준으로 결재
    체인이 만들어져 있어, 바꾸면 "누가 승인해야 하는지"와 실제 결재
    체인이 어긋난다 — 재배정하려면 새로 배정 요청을 하는 게 더 정직한
    경로다)."""
    assignment = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if assignment is None:
        raise ValueError("assignment_not_found")
    if assignment["resource_type"] != "team":
        raise ValueError("not_a_team_assignment")
    if assignment["status"] != "requested":
        raise ValueError("team_leader_only_changeable_while_requested")
    new_leader = conn.execute(
        "SELECT id FROM users WHERE id = ?", (new_leader_id,)
    ).fetchone()
    if new_leader is None:
        raise ValueError("user_not_found")
    conn.execute(
        "UPDATE assignments SET team_leader_id = ? WHERE id = ?",
        (new_leader_id, assignment_id),
    )
    audit.record(
        conn,
        actor_id,
        "team_leader_changed",
        "assignment",
        assignment_id,
        before={"team_leader_id": assignment["team_leader_id"]},
        after={"team_leader_id": new_leader_id},
    )
    conn.commit()
