"""통합 인박스 — "내게 필요한 조치"를 한 화면에(사용자 요청, 2026-09-28,
Phase 5 — "결재 대기·내 업무
응답 대기·낮은 Gate 점수 경고를 한 곳에"). 이미 Phase 1-4가 만든 데이터
(`agent_execution_jobs`, `agent_evaluations`, `approvals`)를 그대로
조회해 조합할 뿐, 새 상태를 만들지 않는다.

**3종 항목**:
- `approval_pending` — `approvals.list_inbox()` 그대로(Admin/매니저 전용,
  자기승인 방지 포함).
- `execution_failed` — 내 "내 업무" task 중 가장 최근 Agent 실행이
  실패로 끝난 것(`agent_execution_jobs`/친절한 에러
  메시지를 그대로 재사용).
- `low_gate_score` — 내 "내 업무" task 안에서 아직 `pending_review`인
  deliverable 중 Gate A-G 하나라도 `passed: false`인 것(`agent_evaluations`를
  그대로 재사용). 이미 승인/반려된 건 "조치가 끝난
  일"이라 제외한다.

**정직한 한계**: `approvals` 테이블엔 `created_at`이 없어(설계 당시
부터) 3종을 하나의 타임스탬프로 완전히 통합 정렬할 수 없다 — 타입별로
묶어서 반환하고(결재 → 실행 실패 → 낮은 Gate 순), 각 타입 내부는 최신
순으로 정렬한다. 완전한 시간순 통합이 필요해지면 `approvals`에
`created_at` 컬럼을 추가하는 마이그레이션이 먼저 필요하다."""

from __future__ import annotations

import sqlite3

from hair_platform import approvals as approvals_mod
from hair_platform import deliverables


def _has_failing_gate(evaluation: dict) -> bool:
    return any(
        gate.get("passed") is False for gate in evaluation["gate_scores"].values()
    )


def get_inbox(conn: sqlite3.Connection, user_id: int, role: str) -> list[dict]:
    """`GET /api/inbox` — 로그인한 사용자 기준으로 조치가 필요한 항목을
    모아 반환한다. Admin/매니저는 결재 대기함도 함께 보고, 그 외 role은
    자신이 참가자인 "내 업무" 기반 항목만 본다(둘 다 해당하는 계정은
    둘 다 나온다)."""
    items: list[dict] = []

    for a in approvals_mod.list_inbox(conn, user_id, role):
        items.append(
            {
                "type": "approval_pending",
                "approval_id": a["id"],
                "subject_type": a["subject_type"],
                "subject_id": a["subject_id"],
                # 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
                # 에이전트명이 표시되지 않는 경우도 있어") — approvals_mod.
                # describe_subject()와 같은 이유로 추가(결재 화면과 동일한
                # 보강을 인박스에도 적용).
                **approvals_mod.describe_subject(
                    conn, a["subject_type"], a["subject_id"]
                ),
            }
        )

    my_work = deliverables.list_my_work(conn, user_id, limit=200)
    execution_failed: list[dict] = []
    low_gate_score: list[dict] = []
    for w in my_work:
        task_id = w["task_id"]
        jobs = deliverables.list_execution_jobs(conn, task_id)
        if jobs and jobs[0]["status"] == "failed":
            execution_failed.append(
                {
                    "type": "execution_failed",
                    "task_id": task_id,
                    "role_name": w["role_name"],
                    "job_id": jobs[0]["id"],
                    "error": jobs[0]["error"],
                }
            )
        for d in deliverables.list_deliverables(conn, task_id):
            if (
                d["status"] == "pending_review"
                and d["evaluation"] is not None
                and _has_failing_gate(d["evaluation"])
            ):
                low_gate_score.append(
                    {
                        "type": "low_gate_score",
                        "task_id": task_id,
                        "role_name": w["role_name"],
                        "deliverable_id": d["id"],
                    }
                )

    # list_my_work()가 이미 최근 배정순(id DESC)으로 주므로 그 순서를
    # 그대로 따른다 — 뒤집지 않는다(실측으로 발견한 버그: 처음엔
    # reversed()를 잘못 넣어 오래된 task가 먼저 나왔었다).
    items.extend(execution_failed)
    items.extend(low_gate_score)
    return items
