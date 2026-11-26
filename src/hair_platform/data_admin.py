"""데이터 삭제/정리 — 사용자 요청(2026-09-28, "각 메뉴에서 데이터 삭제나,
초기화 기능 추가").

**범위(사용자가 명시적으로 선택)**: 도구/스킬 Pool + 사람/AI 에이전트 Pool +
업무 요청/할 일(brief/task)까지. **활동 기록(감사로그)은 제외** —
append-only 원칙에 따라 `db.py`의 DB 트리거가 UPDATE/DELETE 자체를 막고
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
   있는 task/brief/human/agent는 삭제를 막는다(먼저 배정/결재 화면에서
   배정을 정리하도록 유도) — 조직도가 조용히 깨지는 것을 막기 위한
   안전장치다. `status='requested'`(아직 승인 전)나 `'superseded'`
   (이미 지나간 이력)는 막지 않는다.
3. **감사로그에는 남긴다** — 실제 행은 지워도(append-only 원칙과 별개로)
   무엇을 지웠는지는 `audit_log`에 기록한다(삭제 자체가 되돌릴 수 없는
   행위이므로, 최소한 "언제 누가 무엇을 지웠는지"는 남아야 한다).

`reset_to_factory_state()`(사용자 요청, 2026-10-27, "pip 설치나 패키지
배포시 에이전트와 도구/스킬 정보는 포함하고, 나머지 다른 정보들은
초기화 상태로 배포되도록")만 예외적으로 **감사로그까지 지운다** — 위
3번 원칙과 달리, 이 함수는 "지운 사실을 남길 대상"인 사용자 계정 자체를
포함해 전부 지우는 전면 초기화라 `retention.purge_expired()`와 같은
방식(트리거를 잠깐 내렸다가 반드시 다시 세움)으로 그 한 함수 안에서만
통제된 채로 예외를 둔다 — 다른 삭제 함수들은 여전히 감사로그를 못 지운다.
"""

from __future__ import annotations

import functools
import sqlite3

from hair_platform import audit, id_scheme


class DeleteBlocked(Exception):
    """연결된 데이터가 남아 삭제할 수 없음 — `reason`은 화면에 그대로 보여 줄 한국어 설명."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ConfirmedAssignmentError(PermissionError):
    """확정 배정이 있어 막음 — `str()`은 예전과 같은 코드("confirmed_assignment_exists"), `detail`은 무엇이 막는지."""

    def __init__(self, detail: str):
        super().__init__("confirmed_assignment_exists")
        self.detail = detail


# 삭제를 막은 연결 데이터의 화면 이름(2026-11-25) — 모르는 테이블은 테이블 이름 그대로 보여 준다.
_TABLE_LABELS = {
    "tasks": "할 일", "briefs": "업무 요청", "deliverables": "결과물", "assignments": "배정", "approvals": "결재",
    "task_participants": "업무 참여자", "task_dependencies": "할 일 선행 관계", "conversations": "연결된 대화",
    "crew_runs": "Crew 실행 기록", "pipeline_runs": "파이프라인 실행", "pipeline_run_stages": "파이프라인 단계 기록",
    "agent_evaluations": "결과물 평가", "evaluation_reports": "평가 리포트", "agent_execution_jobs": "AI 실행 작업",
    "job_items": "Agent 생성 작업", "jobs": "Agent 생성 작업", "deliverable_comments": "결과물 댓글",
    "deliverable_revisions": "결과물 버전", "deliverable_edit_proposals": "부분 수정 제안",
    "deliverable_repo_commits": "저장소 커밋 기록", "assignment_history": "배정 이력", "agent_pool": "AI 에이전트",
    "team_members": "팀 멤버", "knowledge_chunks": "지식 색인", "agent_tool_links": "Agent 도구 연결",
}


def _fk_violations(conn: sqlite3.Connection) -> set[tuple[str, int, str]]:
    return {(r[0], r[1], r[2]) for r in conn.execute("PRAGMA foreign_key_check").fetchall()}


def _atomic_delete(fn):
    """삭제를 한 덩어리로(2026-11-25 — 업무 요청 삭제 500 수정). 예전엔 연쇄 삭제 도중 FK 위반이 나면 이미 지운 행이
    공유 연결에 커밋 안 된 채 남았다가 **다음 요청의 커밋에 함께 반영**돼, "삭제 실패"인데 일부만 지워진 상태가 될 수
    있었다. 이제 FK 검사를 커밋 직전으로 미루고(`defer_foreign_keys`), 삭제 전에 없던 위반이 생기면 전부 되돌리고
    무엇이 남아 막았는지 알려 준다(이 모듈이 아직 모르는 새 테이블이 생겨도 500 대신 이유가 보인다)."""

    @functools.wraps(fn)
    def wrapper(conn: sqlite3.Connection, *args, **kwargs):
        conn.commit()  # 앞선 미완료 작업과 섞이지 않게
        before = _fk_violations(conn)
        # 트랜잭션을 먼저 연다 — 트랜잭션 밖에서 켠 defer_foreign_keys는 다음 SELECT(자동 커밋 문장)가 끝나면 꺼진다(실측).
        conn.execute("BEGIN")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        try:
            result = fn(conn, *args, **kwargs)
            new = _fk_violations(conn) - before
            if new:
                tables = sorted({_TABLE_LABELS.get(t, t) for t, _rowid, _parent in new})
                raise DeleteBlocked(
                    "연결된 데이터가 남아 있어 삭제할 수 없습니다: " + ", ".join(tables)
                    + " — 그 데이터를 먼저 정리하거나 관리자에게 알려 주세요(삭제 기능이 이 연결을 아직 모릅니다)."
                )
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise

    return wrapper


def _confirmed_detail(conn: sqlite3.Connection, *, task_ids: list[int] | None = None,
                      resource_type: str | None = None, resource_id: int | None = None) -> str:
    """확정 배정이 무엇인지(할 일·리소스) 화면에 보일 설명."""
    if task_ids is not None:
        placeholders = ",".join("?" * len(task_ids)) or "NULL"
        rows = conn.execute(
            "SELECT t.role_name, a.resource_type, a.resource_id FROM assignments a JOIN tasks t ON t.id = a.task_id "
            f"WHERE a.task_id IN ({placeholders}) AND a.status = 'confirmed' ORDER BY a.id LIMIT 5", task_ids).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.role_name, a.resource_type, a.resource_id FROM assignments a JOIN tasks t ON t.id = a.task_id "
            "WHERE a.resource_type = ? AND a.resource_id = ? AND a.status = 'confirmed' ORDER BY a.id LIMIT 5",
            (resource_type, resource_id)).fetchall()
    kinds = {"human": "사람", "agent": "AI 에이전트", "team": "팀", "pipeline": "파이프라인"}
    items = [f"할 일 「{r[0]}」 ← {kinds.get(r[1], r[1])} #{r[2]}" for r in rows]
    return ("확정된 배정이 있어 삭제할 수 없습니다(" + "; ".join(items) + ") — 할 일 상세의 \"배정 취소\"로 먼저 정리하세요."
            if items else "확정된 배정이 있어 삭제할 수 없습니다 — 할 일 상세의 \"배정 취소\"로 먼저 정리하세요.")


def _delete_task_links(conn: sqlite3.Connection, task_ids: list[int]) -> None:
    """할 일을 가리키는 나머지 연결(2026-11-25 — 업무 요청 삭제 500의 원인: 할 일에 연결된 대화·참여자·선행 관계·Crew
    실행 기록을 몰라 FK 위반). 대화는 지우지 않고 연결만 끊어 독립 대화로 남긴다(대화 내용은 보존)."""
    if not task_ids:
        return
    ph = ",".join("?" * len(task_ids))
    conn.execute(f"UPDATE conversations SET linked_task_id = NULL WHERE linked_task_id IN ({ph})", task_ids)
    conn.execute(f"DELETE FROM task_participants WHERE task_id IN ({ph})", task_ids)
    conn.execute(f"DELETE FROM task_dependencies WHERE task_id IN ({ph}) OR depends_on_task_id IN ({ph})",
                 [*task_ids, *task_ids])
    conn.execute(f"DELETE FROM crew_runs WHERE task_id IN ({ph})", task_ids)


def confirmed_assignment_exists(
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


def _delete_pipeline_runs_for_tasks(
    conn: sqlite3.Connection, task_ids: list[int]
) -> int:
    """사용자 보고(2026-10-28, "업무 요청 목록에서 삭제 버튼을 클릭해도
    삭제가 안됨") 원인 수정 — 이 모듈이 처음 작성됐을 때는
    `pipeline_runs`/`pipeline_run_stages`(`pipeline_runtime.py`, 2026-10-18
    도입)를 몰랐다. `pipeline_runs.task_id`는 `tasks(id)`를, `pipeline_run_
    stages.deliverable_id`는 `deliverables(id)`를 참조하는데, 이 둘을
    먼저 안 지우고 `tasks`/`deliverables`를 지우면 `sqlite3.
    IntegrityError`(FK 위반)가 그대로 사용자에게 노출돼 "삭제가 안
    된다"로 보였다(`_delete_deliverables_for_tasks()`가 겪었던 것과
    같은 종류의 갭 — 이 모듈의 설계 원칙 1번을 그대로 따라 채운다).
    `_delete_deliverables_for_tasks()`보다 **먼저** 호출해야 한다 —
    `pipeline_run_stages.deliverable_id` 참조를 먼저 끊어야 그 함수가
    deliverables를 안전하게 지울 수 있다."""
    if not task_ids:
        return 0
    t_placeholders = ",".join("?" * len(task_ids))
    run_ids = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM pipeline_runs WHERE task_id IN ({t_placeholders})",
            task_ids,
        ).fetchall()
    ]
    if not run_ids:
        return 0
    r_placeholders = ",".join("?" * len(run_ids))
    stage_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM pipeline_run_stages WHERE pipeline_run_id IN "
            f"({r_placeholders})",
            run_ids,
        ).fetchall()
    ]
    if stage_ids:
        s_placeholders = ",".join("?" * len(stage_ids))
        conn.execute(
            "DELETE FROM approvals WHERE subject_type = 'pipeline_run_stage' "
            f"AND subject_id IN ({s_placeholders})",
            stage_ids,
        )
        conn.execute(
            f"DELETE FROM pipeline_run_stages WHERE id IN ({s_placeholders})",
            stage_ids,
        )
    conn.execute(f"DELETE FROM pipeline_runs WHERE id IN ({r_placeholders})", run_ids)
    return len(run_ids)


def _delete_deliverables_for_tasks(
    conn: sqlite3.Connection, task_ids: list[int]
) -> int:
    """**실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
    개선해줘" 리뷰 중)**: 이 함수가 처음 작성됐을 때는 `deliverables`
    테이블이 아직 없었다 — Phase 1-4가 그 뒤에 추가한 `deliverables`/
    `deliverable_comments`/`agent_evaluations`/`agent_execution_jobs`를
    이 모듈이 몰라서, "배정이 superseded된 뒤(더 이상 confirmed가 아님)
    deliverable은 남아있는" task를 지우면 `sqlite3.IntegrityError`(FK
    위반)가 그대로 사용자에게 500으로 노출되는 걸
    `test_probe_delete_task_after_supersede_with_existing_deliverable`로
    재현 확인했다. 이 모듈의 설계 원칙 1번("FK 순서를 지킨 완전한 연쇄
    삭제... 고아 행을 남기지 않는다")을 그대로 따라 여기도 연쇄
    삭제하도록 채워 넣었다 — job_items/assignments와 같은 패턴."""
    if not task_ids:
        return 0
    t_placeholders = ",".join("?" * len(task_ids))
    deliverable_ids = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM deliverables WHERE task_id IN ({t_placeholders})",
            task_ids,
        ).fetchall()
    ]
    conn.execute(
        f"DELETE FROM agent_execution_jobs WHERE task_id IN ({t_placeholders})",
        task_ids,
    )
    if deliverable_ids:
        d_placeholders = ",".join("?" * len(deliverable_ids))
        conn.execute(
            f"DELETE FROM deliverable_comments WHERE deliverable_id IN ({d_placeholders})",
            deliverable_ids,
        )
        # 결과물 수정 이력(2026-11-23, P1-6).
        conn.execute(
            f"DELETE FROM deliverable_revisions WHERE deliverable_id IN ({d_placeholders})",
            deliverable_ids,
        )
        # 결과물 → 저장소 커밋 기록(2026-11-24, P0-3 2단계 — 저장소 쪽 커밋은 그대로 남는다).
        conn.execute(
            f"DELETE FROM deliverable_repo_commits WHERE deliverable_id IN ({d_placeholders})",
            deliverable_ids,
        )
        # 부분 재작성 제안(2026-11-24, P1-6).
        conn.execute(
            f"DELETE FROM deliverable_edit_proposals WHERE deliverable_id IN ({d_placeholders})",
            deliverable_ids,
        )
        # 평가 HTML 리포트(2026-11-25) — 평가보다 먼저(FK).
        conn.execute(
            "DELETE FROM evaluation_reports WHERE evaluation_id IN "
            f"(SELECT id FROM agent_evaluations WHERE deliverable_id IN ({d_placeholders}))",
            deliverable_ids,
        )
        conn.execute(
            f"DELETE FROM agent_evaluations WHERE deliverable_id IN ({d_placeholders})",
            deliverable_ids,
        )
        conn.execute(
            "DELETE FROM approvals WHERE subject_type = 'deliverable' AND subject_id IN "
            f"({d_placeholders})",
            deliverable_ids,
        )
    conn.execute(
        f"DELETE FROM deliverables WHERE task_id IN ({t_placeholders})", task_ids
    )
    return len(deliverable_ids)


@_atomic_delete
def delete_task(conn: sqlite3.Connection, actor_id: int, task_id: int) -> None:
    """할 일(task) 하나를 그 아래 assignments·job_items·deliverables까지
    전부 지운다. `confirmed` 상태 배정이 있으면 막는다(먼저 정리하도록
    유도) — deliverable 존재 자체는 막지 않는다("완전한 연쇄 삭제" 설계
    원칙을 그대로 따른다)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if confirmed_assignment_exists(conn, task_ids=[task_id]):
        raise ConfirmedAssignmentError(_confirmed_detail(conn, task_ids=[task_id]))

    deleted_assignments = _delete_assignments_for_tasks(conn, [task_id])
    _delete_pipeline_runs_for_tasks(conn, [task_id])
    _delete_deliverables_for_tasks(conn, [task_id])
    _delete_task_links(conn, [task_id])
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


@_atomic_delete
def delete_brief(conn: sqlite3.Connection, actor_id: int, brief_id: int) -> None:
    """업무 요청(brief) 하나를 그 아래 tasks·jobs·assignments·deliverables까지
    전부 지운다. 아래 어떤 task에든 `confirmed` 배정이 있으면 막는다."""
    brief = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if brief is None:
        raise ValueError("brief_not_found")

    task_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM tasks WHERE brief_id = ?", (brief_id,)
        ).fetchall()
    ]
    if confirmed_assignment_exists(conn, task_ids=task_ids):
        raise ConfirmedAssignmentError(_confirmed_detail(conn, task_ids=task_ids))

    deleted_assignments = _delete_assignments_for_tasks(conn, task_ids)
    _delete_pipeline_runs_for_tasks(conn, task_ids)
    _delete_deliverables_for_tasks(conn, task_ids)
    _delete_task_links(conn, task_ids)
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


@_atomic_delete
def delete_human(conn: sqlite3.Connection, actor_id: int, human_id: int) -> None:
    """사람 인력(human_pool) 레코드를 지운다. `confirmed` 배정이 있으면
    막는다."""
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if row is None:
        raise ValueError("human_not_found")
    if confirmed_assignment_exists(conn, resource_type="human", resource_id=human_id):
        raise ConfirmedAssignmentError(_confirmed_detail(conn, resource_type="human", resource_id=human_id))

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


@_atomic_delete
def delete_agent(conn: sqlite3.Connection, actor_id: int, agent_id: int) -> None:
    """AI 에이전트(agent_pool) 레코드를 그 아래 지식 색인(knowledge_chunks)·
    도구 연결(agent_tool_links)까지 전부 지운다. `confirmed` 배정이 있으면
    막는다."""
    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise ValueError("agent_not_found")
    if confirmed_assignment_exists(conn, resource_type="agent", resource_id=agent_id):
        raise ConfirmedAssignmentError(_confirmed_detail(conn, resource_type="agent", resource_id=agent_id))

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


# 사용자 요청(2026-10-27, "pip 설치나 패키지 배포시 에이전트와 도구/스킬
# 정보는 포함하고, 나머지 다른 정보들은 초기화 상태로 배포되도록") —
# `db.init_schema()`가 매 기동 때 `_seed_default_pools()` +
# `reference_pipeline_seeds.seed()`로 이미 채워 넣는 Agent(26종)/Tool
# Pool(12개)/Skill Pool(51개)/파이프라인 템플릿("소프트웨어 개발
# 파이프라인" 등 4종)은 그 두 함수가 `INSERT OR IGNORE`(또는 존재 확인 후
# INSERT)로 매번 알아서 채우므로, 이 함수는 그 테이블들에 **전혀 손대지
# 않는다**(관리자가 나중에 추가한 커스텀 Agent/Tool/Skill도 마찬가지로
# 그대로 둔다 — "정확히 레퍼런스 세트만 남긴다"가 아니라 "운영 중 쌓인
# 데이터만 지운다"는 보수적인 범위를 택했다).
#
# `teams`/`team_members`는 2026-11-10까지는 이 보존 목록에 있었지만
# 제외했다(사용자 요청, 2026-11-10, "팀 관리 데이터는 초기화 안되어
# 있음... 초기화시 삭제되고 이 후 자동 생성되지 않도록") — 조사 결과
# 팀은 Agent/Tool/Skill/파이프라인과 달리 **진짜로 재시드되지 않는다**:
# `reference_pipeline_seeds._sync_dev_pipeline_implementation_stage_
# agents()`(db.py)는 `WHERE name = '자율 개발 크루'`로 **이미 존재하는**
# 팀 행을 찾아 파이프라인 연결/멤버만 보강하는 UPDATE/INSERT OR
# IGNORE일 뿐, 팀 자체를 `INSERT`하는 코드는 이 코드베이스 어디에도
# 없다(전수 검색으로 `INSERT INTO teams`는 `teams.create_team()` 1곳뿐
# — 사용자가 직접 만들 때만 호출됨). 즉 "Agent 등은 재시드되니 지워도
# 된다"는 근거가 teams에는 애초에 성립하지 않았다 — 실측으로도 이름이
# 한 번 바뀐 팀("로드맵6 자율 개발 크루")이 멤버 0명인 빈 껍데기로
# 리셋을 여러 번 건너뛰며 누적돼 있었다. 지우면 다시 자동으로 안
# 생긴다(의도된 동작).
#
# 자식→부모 순서로 나열했지만 `PRAGMA foreign_keys=OFF`로 감싸 엄밀한
# 순서를 강제하지 않는다(`db.py`의 `_migrate_tool_pool_custom_python_
# kind()`와 같은 선례 — 대량 작업에서 FK를 한 번만 잠깐 끄는 쪽이 수십
# 개 테이블의 삭제 순서를 일일이 맞추는 것보다 훨씬 덜 깨지기 쉽다).
# 사용자 요청(2026-11-18, "데이터 초기화에 팀 삭제 되지 않도록 개선") —
# "데이터 분석·시각화 전담팀"은 바로 위 2026-11-10 결정("팀은 재시드
# 안 되니 지워도 된다")의 예외다 — 핵심 플랫폼 역량(실제 계산/차트
# 도구를 `@Team 멘션`으로 호출하는 유일한 경로, 지난 턴들에서 라이브로
# 검증)이라 "자율 개발 크루"가 한 번 삭제됐을 때처럼 또 수동으로
# 다시 만들어야 하는 상황을 피한다. 이름 기준으로 보호한다(팀은
# 코드/고유 식별자가 없어 이름이 유일한 식별 수단) — 다른 팀(관리자가
# 직접 만든 TF팀 등)은 여전히 2026-11-10 결정 그대로 초기화 대상이다.
# 보호 팀(2026-11-21 설정화) — 예전엔 팀 이름 목록(`_FACTORY_RESET_PROTECTED_TEAM_NAMES`,
# "데이터 분석·시각화 전담팀")으로 보호해 이름을 바꾸면 보호가 풀렸다. 이제 팀마다
# `teams.protect_on_reset` 표시(팀 관리 화면에서 admin이 켜고 끔)를 본다.

_FACTORY_RESET_WIPE_TABLES: tuple[str, ...] = (
    # 실제 LLM 토큰 사용량 기록(2026-11-22 `llm_usage.py`) — 실행 이력과 같은 운영 데이터.
    "llm_usage",
    "knowledge_chunks",
    "evaluation_reports",  # 평가 HTML 리포트(2026-11-25) — 평가와 함께 지운다
    "agent_evaluations",
    "deliverable_comments",
    "deliverable_revisions",
    # 결과물 → 저장소 커밋 기록·저장소 등록(2026-11-24, P0-3) — 등록은 이 서버의 경로라 모델
    # 레지스트리처럼 운영 설정으로 함께 지운다(저장소 자체·그 안의 커밋은 건드리지 않는다).
    "deliverable_repo_commits",
    "deliverable_edit_proposals",  # 부분 재작성 제안(2026-11-24, P1-6)
    "code_repositories",
    "connection_health",  # 연결 상태(2026-11-26, 개선 계획 2-3)
    "tool_calls",  # 도구 사용 기록(2026-11-26, 개선 계획 1-1) — 실행 이력과 같이 지운다
    "background_jobs",  # 작업 큐 기록(2026-11-26, P1-6 2단계) — 실행 이력과 같이 지운다
    "calendar_feeds",  # 캘린더(2026-11-26, P1-9 3단계)
    "mail_inboxes",  # 메일 수신함(2026-11-26, P1-9 2단계)
    "data_connections",  # 사내 DB 연결(2026-11-26, P1-9) — 코드 저장소 등록과 같이 지운다
    "pipeline_run_stages",
    "pipeline_runs",
    "crew_runs",
    "agent_execution_jobs",
    "job_items",
    "jobs",
    "deliverables",
    "conversation_jobs",
    "conversation_knowledge_chunks",
    "message_evaluation_reports",  # 대화 메시지 평가 리포트(2026-11-25) — 메시지보다 먼저
    "conversation_messages",
    "conversation_participants",
    "conversations",
    "knowledge_submissions",
    "documents",
    "task_dependencies",
    "task_participants",
    "assignment_history",
    "assignments",
    "approvals",
    "tasks",
    "briefs",
    "human_pool",
    "agent_tool_links",
    # 모델 성능 테스트 기록(2026-11-25 추가) — 모델 레지스트리를 지우면서 남겨 두면 없는 모델의 기록이 된다.
    "model_benchmarks",
    "model_registry",
    "embedding_config",
    "audit_log",
    # 개인 API 키·정기 실행·알림 기록(2026-11-23, P1-5) — 사용자와 함께 지운다(남기면 지워진
    # 계정의 키·일정이 된다). 알림 웹훅 설정 자체는 조직 정책(org_policy)이라 남는다.
    "api_keys",
    "pipeline_schedules",
    # 이벤트 트리거(2026-11-24, P1-5) — 정기 실행처럼 사용자와 함께 지운다(만든 사람 명의로 실행되므로).
    "trigger_log",
    "automation_triggers",
    "notification_log",
    "users",
)


def reset_to_factory_state(conn: sqlite3.Connection) -> dict[str, int]:
    """ "공장 초기화" — 운영 중 쌓인 데이터를 전부 지우고, Agent/Tool/
    Skill/파이프라인 템플릿은 그대로 둔다(위 모듈 독스트링 참고). 팀
    (teams/team_members)은 2026-11-10부터 삭제 대상이다(재시드되지
    않는 데이터라 Agent 등과 같은 보존 근거가 없음, 바로 위 주석 참고)
    — 단 "초기화 시 보호" 표시(`teams.protect_on_reset`)가 켜진 팀은 예외다
    (2026-11-18 결정, 2026-11-21 이름 목록에서 팀별 표시로 설정화).
    Agent 자체는 남기되 실행하면서 쌓인 휘발성 필드(비용·성과점수·
    provider/model·마지막 산출물)는 초기값으로 되돌린다 — `source_
    job_item_id`는 `job_items`를 지우므로 먼저 NULL로 비운다(이 파일
    서두 "설계 원칙 1"과 같은 이유).

    되돌릴 수 없다 — 호출자(CLI)가 확인을 받은 뒤에만 불러야 한다.
    반환값은 테이블별 삭제 건수(로그/확인용)."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    counts: dict[str, int] = {}
    try:
        conn.execute(
            "UPDATE agent_pool SET source_job_item_id = NULL, "
            "llm_provider = NULL, llm_model = NULL, last_output = NULL, "
            "cost_estimate = 0.0, performance_score = NULL"
        )
        # 상태(2026-11-25 수정) — 예전엔 모든 Agent를 'active'로 바꿔, 온보딩 승인 전("온보딩 대기")인
        # 관리자 생성 Agent가 승인 없이 활성이 되고 관리자가 꺼 둔 Agent도 다시 켜졌다(온보딩 절차 우회).
        # 기본 Agent(`AGT-REF-…`, 플랫폼 기본 구성)만 활성으로 되돌리고, 관리자가 만든 Agent
        # (`AGT-CUSTOM-…`)는 지금 상태를 그대로 둔다.
        conn.execute(
            "UPDATE agent_pool SET status = 'active' WHERE code LIKE ?",
            (f"AGT-{id_scheme.AGENT_REF_SEGMENT}-%",),
        )
        # 평가 기준 설정(2026-11-25)은 조직 설정이라 남긴다 — 지우는 사용자를 가리키지 않게 수정자만 비운다.
        conn.execute("UPDATE evaluation_config SET updated_by = NULL")
        conn.execute("UPDATE ai_budget_config SET updated_by = NULL")  # AI 사용 한도는 남김(2026-11-25, P2-14)
        for table in _FACTORY_RESET_WIPE_TABLES:
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
        counts["team_members"] = conn.execute(
            "DELETE FROM team_members WHERE team_id NOT IN "
            "(SELECT id FROM teams WHERE protect_on_reset = 1)"
        ).rowcount
        counts["teams"] = conn.execute(
            "DELETE FROM teams WHERE protect_on_reset = 0"
        ).rowcount
        conn.commit()
    finally:
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete "
            "BEFORE DELETE ON audit_log BEGIN "
            "SELECT RAISE(ABORT, 'audit_log is append-only'); END"
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    # 지식 베이스 벡터 DB(2026-11-25, `vector_backends.py`)도 비운다 — 실패해도 다음 검색의 `sync()`가 원본(빈
    # `knowledge_chunks`)에 맞춰 지우므로 초기화 자체는 막지 않는다.
    try:
        from hair_platform import vector_backends

        vector_backends.get_backend(conn).reset(conn)
    except Exception:  # noqa: BLE001
        pass
    return counts
