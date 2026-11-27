"""결재 API(승인 대기함·결재 처리) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
)

from hair_platform import job_queue  # 백그라운드 작업 큐(2026-11-26, P1-6 2단계)
from hair_platform import approvals as approvals_mod
from hair_platform import (
    assignments,
    audit,
    deliverables,
    approval_assist,
    knowledge_submissions,
    stage_actions,
    pipeline_runtime,
    process_learning,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    ApprovalDecision,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """결재 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.get("/api/approvals")
    def list_approvals(
        scope: str = "inbox",
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """코드 재점검(2026-09-26)에서 발견한 누락 보완 — 승인권자가 자신에게 무엇이
        결재 대기 중인지 조회할 API가 전무했다(결재 체인이 이론상으로만 동작). `decide_approval`
        의 권한 로직과 동일한 기준으로 "내가 지금 결정할 수 있는 건"만 보여준다.

        - `scope=mine`(누구나): 내가 요청한 승인 전체(상태 무관).
        - `scope=inbox`(Admin/매니저 전용, 기본값): 아직 `pending`이고, 내가
          요청자가 아니며(self-approval 제외), Admin이면 전부,
          매니저면 승인자 미지정이거나 나로 지정된 건만.

        사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
        에이전트명이 표시되지 않는 경우도 있어") — 응답의 `subject_type`/
        `subject_id`는 `assignments.id`/`deliverables.id`일 뿐이라 그
        자체로는 승인권자가 "무엇을 승인하는지" 알 길이 없었다.
        `approvals_mod.describe_subject()`로 업무 역할명·대상 이름을
        함께 내려준다(대상이 지워졌으면 전부 None — 에러로 막지 않음).
        """
        if scope == "mine":
            # 사용자 요청(2026-11-03, "내가 요청한 건의 경우 결재권자가
            # 표시되도록") — `list_inbox()`와 같은 3종 username 조인
            # (요청자는 자기 자신이라 의미 없지만, 응답 모양을
            # scope 둘 다 똑같이 맞춰두면 프런트가 고르기 편하다).
            rows = conn.execute(
                "SELECT a.*, "
                "ru.username AS requested_by_username, "
                "apv.username AS approver_username, "
                "adu.username AS approved_by_username "
                "FROM approvals a "
                "LEFT JOIN users ru ON ru.id = a.requested_by "
                "LEFT JOIN users apv ON apv.id = a.approver_id "
                "LEFT JOIN users adu ON adu.id = a.approved_by "
                "WHERE a.requested_by = ? ORDER BY a.id DESC",
                (user["id"],),
            ).fetchall()
            return [
                {
                    **dict(row),
                    **approvals_mod.describe_subject(
                        conn, row["subject_type"], row["subject_id"]
                    ),
                }
                for row in rows
            ]
        if scope != "inbox":
            raise HTTPException(
                status_code=400, detail="scope는 inbox 또는 mine이어야 합니다"
            )
        if user["role"] not in (Role.ADMIN.value, Role.MANAGER.value):
            raise HTTPException(
                status_code=403,
                detail="승인 대기함은 Admin/매니저만 조회할 수 있습니다",
            )
        return [
            {
                **row,
                **approvals_mod.describe_subject(
                    conn, row["subject_type"], row["subject_id"]
                ),
            }
            for row in approvals_mod.list_inbox(conn, user["id"], user["role"])
        ]

    @app.get("/api/approvals/{approval_id}/brief")
    def get_approval_brief(approval_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """결재 도우미(2026-11-27, AI Native 분석 3단계) — 품질 점검 요약·이전 판과의 차이·확인할 점·과거 반려 사유·
        위험도와 추천. 결정할 수 있는 Admin·매니저와 요청자 본인만."""
        row = conn.execute("SELECT requested_by FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="승인 요청을 찾을 수 없습니다")
        if user["role"] not in (Role.ADMIN.value, Role.MANAGER.value) and row["requested_by"] != user["id"]:
            raise HTTPException(status_code=403, detail="이 결재를 볼 권한이 없습니다")
        try:
            return approval_assist.brief_for_approval(conn, approval_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/approvals", status_code=201)
    def create_approval(
        subject_type: str,
        subject_id: int,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """브리프 최소 단일승인 워크플로우 전용(v0 범위).

        코드 재점검(2026-09-26)에서 발견한 심각한 우회 경로 수정: 이전엔 role
        제한이 전혀 없고(get_current_user만) `subject_type`도 검증하지 않아,
        VIEWER를 포함한 아무 로그인 사용자가 `subject_type=assignment`로 진행 중인
        다단계 결재라인과 무관한 rogue 승인 레코드(step=1, approver_id=NULL)를
        만들 수 있었다 — `decide_approval`의 "지정된 승인자만" 체크는
        approver_id가 NULL일 때 통과되므로, 체인 밖의 아무 Admin/Manager가 이
        rogue 레코드를 승인하면 `resolve_assignment_decision`이 subject_id만 보고
        배정을 조기 확정/반려시킬 수 있었다. 이 엔드포인트는 설계상 브리프
        단일승인 전용이었으므로 subject_type을 그 범위로 고정해 우회로를 닫는다.
        `assignment` 승인은 `POST /api/tasks/{task_id}/assignments`가 만드는
        정식 체인으로만 생성되게 한다.
        """
        if subject_type != "brief":
            raise HTTPException(
                status_code=400,
                detail="이 엔드포인트는 brief 승인 전용입니다(assignment는 배정 "
                "요청을 통해서만 결재라인이 생성됩니다)",
            )
        brief = conn.execute(
            "SELECT id FROM briefs WHERE id = ?", (subject_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")
        cur = conn.execute(
            "INSERT INTO approvals (requested_by, subject_type, subject_id) VALUES (?, ?, ?)",
            (user["id"], subject_type, subject_id),
        )
        conn.commit()
        return {"id": cur.lastrowid, "status": "pending"}

    @app.post("/api/approvals/{approval_id}/decide")
    def decide_approval(
        approval_id: int,
        payload: ApprovalDecision,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        approval = conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if approval is None:
            raise HTTPException(status_code=404, detail="승인 요청을 찾을 수 없습니다")
        if approval["status"] == "waiting":
            # 다단계 결재라인: 이전 단계가 아직 안 끝났다.
            raise HTTPException(status_code=409, detail="이전 단계 승인이 필요합니다")
        if approval["status"] != "pending":
            # 이미 처리된 승인을 재처리해 결재라인을 우회하지 못하게 막는다.
            raise HTTPException(status_code=409, detail="이미 처리된 승인입니다")
        if approval["requested_by"] == user["id"]:
            # self-approval 방지
            raise HTTPException(
                status_code=403,
                detail="본인이 요청한 건은 승인할 수 없습니다",
            )
        # 결재 위임(2026-11-27, Resource Management Phase C) — 지정 승인자가 오늘 나에게 맡겼으면 대신 결정한다.
        from hair_platform import resource_lifecycle

        decided_on_behalf_of = (
            approval["approver_id"]
            if approval["approver_id"] is not None and approval["approver_id"] != user["id"]
            and approval["approver_id"] in resource_lifecycle.delegators_for(conn, user["id"])
            else None
        )
        if (
            approval["approver_id"] is not None
            and approval["approver_id"] != user["id"]
            and user["role"] != Role.ADMIN.value
            and decided_on_behalf_of is None
        ):
            raise HTTPException(
                status_code=403, detail="지정된 승인자만 이 단계를 승인할 수 있습니다"
            )
        if approval["subject_type"] == knowledge_submissions.SUBJECT_TYPE:
            # 지식베이스 등록 결재(2026-11-20)는 admin 전용이다 — 매니저
            # 결재 체인이 아니라 approver_id가 비어 있으므로 위 "지정된
            # 승인자" 검사만으로는 매니저가 통과해 버린다.
            if user["role"] != Role.ADMIN.value:
                raise HTTPException(
                    status_code=403, detail="지식 등록 결재는 Admin만 처리할 수 있습니다"
                )
            if not payload.approve and not (payload.reason or "").strip():
                raise HTTPException(
                    status_code=400, detail="지식 등록을 반려할 때는 사유를 입력하세요"
                )

        new_status = "approved" if payload.approve else "rejected"
        subject_type = approval["subject_type"]
        subject_id = approval["subject_id"]
        try:
            conn.execute(
                "UPDATE approvals SET status = ?, approved_by = ? WHERE id = ?",
                (new_status, user["id"], approval_id),
            )
            assignment_result = None
            deliverable_result = None
            pipeline_run_id = None
            # 사용자 요청(2026-11-18, "langgraph + langgraph-checkpoint-
            # sqlite... 결재함과 연결") — `pipeline_run_stage` 결재 대상이
            # 기존 `pipeline_runtime` 상태 머신이 아니라 LangGraph 실행
            # 엔진(`execution_graph.py`)으로 진행 중인 run이면, 재개
            # 방식이 완전히 다르다(DB 컬럼 직접 조작 대신 `interrupt()`
            # 재개) — 아래서 `pipeline_run_stage` 분기 진입 시 확정된다.
            pipeline_run_is_graph_engine = False
            action_run_stage_id = None  # 결재 후 실행 동작(2026-11-27) — 최종 승인된 파이프라인 단계
            new_pipeline_run_id = None
            new_pipeline_run_actor_id = None
            next_step_pending = False
            knowledge_submission_to_register = None
            if payload.approve:
                next_step_pending = approvals_mod.activate_next_step(
                    conn, subject_type, subject_id, approval["step"]
                )
                if not next_step_pending and subject_type == "assignment":
                    # 체인의 마지막 단계 통과 시에만 최종 확정한다.
                    assignment_result = assignments.resolve_assignment_decision(
                        conn, subject_id, True, user["id"]
                    )
                    # 사용자 요청(2026-10-29, "파이프라인을 assignments.
                    # resource_type에 추가... 사람/Agent/Team 배정과 동일하게
                    # 결재를 거치게") — 지금까지 배정 승인은 레코드 상태만
                    # 바꿀 뿐 아무 실행도 트리거하지 않았다. resource_type이
                    # "pipeline"이면 확정되는 순간 실제로 실행을 시작한다.
                    if assignment_result["status"] == "confirmed":
                        arow = conn.execute(
                            "SELECT resource_type, resource_id, task_id, requested_by "
                            "FROM assignments WHERE id = ?",
                            (subject_id,),
                        ).fetchone()
                        if arow["resource_type"] == "pipeline":
                            try:
                                new_pipeline_run_id = (
                                    pipeline_runtime.start_pipeline_run(
                                        conn,
                                        arow["requested_by"],
                                        arow["resource_id"],
                                        arow["task_id"],
                                    )
                                )
                                new_pipeline_run_actor_id = arow["requested_by"]
                            except (LookupError, ValueError):
                                # 이미 같은 task에 실행 중인 run이 있거나
                                # 템플릿이 그 사이 비활성화됨 — 배정 자체는
                                # confirmed로 남기고 실행은 건너뛴다.
                                pass
                elif not next_step_pending and subject_type == "deliverable":
                    # Phase 2 — 체인의 마지막 단계 통과 시에만 산출물을 확정한다
                    # (assignment와 같은 원리 — deliverables.py docstring
                    # "Phase 2 설계" 참고).
                    deliverable_result = deliverables.resolve_deliverable_decision(
                        conn, subject_id, True
                    )
                elif (
                    not next_step_pending
                    and subject_type == knowledge_submissions.SUBJECT_TYPE
                ):
                    knowledge_submissions.decide(
                        conn, subject_id, user["id"], True, payload.reason
                    )
                    knowledge_submission_to_register = subject_id
                elif not next_step_pending and subject_type == "pipeline_run_stage":
                    # 추천 대 사람 결정(2026-11-27, 3단계 — 위험 기반 결재 검증용).
                    approval_assist.record_decision(conn, subject_id, True)
                    action_run_stage_id = subject_id
                    # 사용자 요청(2026-10-18, "최소 HITL/HOTL") — 계획
                    # 확정 체크포인트 또는 반복 실패 에스컬레이션을 사람이
                    # 승인하면, 파이프라인이 다음 단계로 이어서 진행된다
                    # (LLM 호출이 들어가므로 여기선 DB 상태만 바꾸고
                    # BackgroundTasks로 이어서 돌린다).
                    #
                    # 사용자 요청(2026-11-18, "결재함과 연결") — 이 단계가
                    # LangGraph 실행엔진으로 진행 중이면 `pipeline_runtime.
                    # resolve_stage_decision()`(DB 컬럼을 직접 조작하는
                    # 상태 머신 전제)을 호출하면 안 된다 — 그 엔진의
                    # "진실의 원천"은 LangGraph 체크포인트라 서로 충돌한다.
                    stage_run_row = conn.execute(
                        "SELECT pipeline_run_id FROM pipeline_run_stages WHERE id = ?",
                        (subject_id,),
                    ).fetchone()
                    engine_row = (
                        conn.execute(
                            "SELECT engine FROM pipeline_runs WHERE id = ?",
                            (stage_run_row["pipeline_run_id"],),
                        ).fetchone()
                        if stage_run_row is not None
                        else None
                    )
                    if engine_row is not None and engine_row["engine"] == "graph":
                        assert stage_run_row is not None
                        pipeline_run_is_graph_engine = True
                        pipeline_run_id = stage_run_row["pipeline_run_id"]
                    else:
                        pipeline_run_id = pipeline_runtime.resolve_stage_decision(
                            conn, user["id"], subject_id, True, payload.reason
                        )
            else:
                approvals_mod.skip_remaining_steps(
                    conn, subject_type, subject_id, approval["step"]
                )
                if subject_type == "assignment":
                    assignment_result = assignments.resolve_assignment_decision(
                        conn, subject_id, False, user["id"]
                    )
                elif subject_type == "deliverable":
                    deliverable_result = deliverables.resolve_deliverable_decision(
                        conn, subject_id, False
                    )
                elif subject_type == knowledge_submissions.SUBJECT_TYPE:
                    knowledge_submissions.decide(
                        conn, subject_id, user["id"], False, payload.reason
                    )
                elif subject_type == "pipeline_run_stage":
                    # 배운 점 후보(2026-11-27, AI Native 분석 2단계) — 반려 사유는 이 실행의 재시도뿐 아니라
                    # 운영자가 적용하면 같은 단계의 다음 실행들에도 들어간다(`process_learning`).
                    process_learning.capture_rejection(conn, subject_id, payload.reason)
                    approval_assist.record_decision(conn, subject_id, False)
                    # 반려 = "다시 해와"(승인루프 스킬 안내와
                    # 일치) — 재시도 여력이 남아 있으면 파이프라인은
                    # 'running'으로 돌아가 같은 단계를 재시도한다.
                    # 그래프 엔진 분기는 위 승인 branch와 동일한 이유.
                    stage_run_row = conn.execute(
                        "SELECT pipeline_run_id FROM pipeline_run_stages WHERE id = ?",
                        (subject_id,),
                    ).fetchone()
                    engine_row = (
                        conn.execute(
                            "SELECT engine FROM pipeline_runs WHERE id = ?",
                            (stage_run_row["pipeline_run_id"],),
                        ).fetchone()
                        if stage_run_row is not None
                        else None
                    )
                    if engine_row is not None and engine_row["engine"] == "graph":
                        assert stage_run_row is not None
                        pipeline_run_is_graph_engine = True
                        pipeline_run_id = stage_run_row["pipeline_run_id"]
                    else:
                        pipeline_run_id = pipeline_runtime.resolve_stage_decision(
                            conn, user["id"], subject_id, False, payload.reason
                        )
            audit.record(
                conn,
                user["id"],
                f"approval_{new_status}",
                "approval",
                approval_id,
                before={"step": approval["step"], "subject_type": subject_type},
                after={
                    "next_step_pending": next_step_pending,
                    "reason": payload.reason,
                    **({"on_behalf_of": decided_on_behalf_of} if decided_on_behalf_of else {}),
                },
            )
            # 2026-10-07, 실사용 리뷰로 발견한 갭 — 결재 사유를 그
            # 산출물의 댓글 타임라인에도 남긴다(참가자 여부와 무관하게,
            # deliverables.record_decision_comment() 참고). assignment
            # 결재는 아직 댓글 개념이 없어 감사로그에만 남는다.
            if payload.reason and subject_type == "deliverable":
                deliverables.record_decision_comment(
                    conn,
                    subject_id,
                    user["id"],
                    ("[승인 사유] " if payload.approve else "[반려 사유] ")
                    + payload.reason,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # 결재 후 실행 동작(2026-11-27, 4단계) — 커밋 뒤, 결재자가 "승인하고 실행"을 골랐을 때만 보낸다.
        action_result = (stage_actions.execute(conn, action_run_stage_id, user["id"], bool(payload.run_action))
                         if action_run_stage_id is not None else None)
        if knowledge_submission_to_register is not None:
            # 승인된 지식 등록 — 임베딩은 오래 걸릴 수 있어 커밋 뒤
            # 백그라운드에서 색인한다(`knowledge_submissions` 모듈 설명).
            job_queue.enqueue(
                conn, "knowledge.register_submission",
                knowledge_submission_to_register,
                background_tasks=background_tasks,
            )
        if pipeline_run_id is not None and pipeline_run_is_graph_engine:
            # 사용자 요청(2026-11-18, "결재함과 연결") — 이 run은 아직
            # `pipeline_runs.status`가 'paused_for_approval'인 채다(실제
            # 상태 전이는 `_node_human_approval()`이 재개될 때 일어난다
            # — 그래서 레거시 엔진의 `status == "running"` 체크가 여기선
            # 적용되지 않는다, 즉시 재개 작업을 건다). `conversations.
            # resume_pipeline_graph_from_approval_and_notify()`를 거쳐야
            # — 대화에서 트리거된 run이면 재개 후 진행 메시지도 올린다
            # (`run_pipeline_stage_job_and_notify()`와 같은 이유, 레거시
            # 엔진도 직접 `pipeline_runtime`을 안 부르고 그 래퍼를 씀).
            job_queue.enqueue(
                conn, "conversation.resume_graph_from_approval",
                pipeline_run_id,
                {
                    "decision": "approve" if payload.approve else "reject",
                    "reason": payload.reason,
                },
                background_tasks=background_tasks,
            )
        elif pipeline_run_id is not None:
            pipeline_run_row = conn.execute(
                "SELECT status, actor_id FROM pipeline_runs WHERE id = ?",
                (pipeline_run_id,),
            ).fetchone()
            if pipeline_run_row["status"] == "running":
                # 사용자 보고(2026-10-28, "코드 검증의 경우 요청자가
                # 대화중인 사람이 아닌 admin에 표기되며 어느 누구도
                # 승인할 수 있는 상황이 아님") 원인 수정 — 지금까지
                # 재개 시 넘기는 actor_id가 "지금 승인한 사람"(`user
                # ["id"]`)이었다. 다음 단계가 또 에스컬레이션되면
                # `approvals.create_approval_chain()`의 `requested_by`
                # 가 그 **승인자 자신**이 돼, 그 사람이 다시 승인하려
                # 하면 자기 승인 방지(`decide_approval()`의 self-
                # approval 차단)에 걸려 아무도 승인할 수 없는 교착
                # 상태가 됐다(실측 확인). 파이프라인을 일관되게
                # "원래 실행을 요청한 사람"(`pipeline_runs.actor_id`,
                # `start_pipeline_run()` 시점에 한 번 고정) 명의로
                # 계속 진행해야 한다 — 승인 행위 자체의 감사 기록은
                # `approvals.approved_by`에 이미 따로 남는다.
                # 대화 `@팀이름` 멘션이 띄운 run이면 다음 단계 진행
                # 후 그 대화에도 진행 상황을 올려야 하므로
                # `conversations.run_pipeline_stage_job_and_notify()`가
                # 감싼다(Task 기원 run은 평소와 동일하게 동작).
                job_queue.enqueue(
                    conn, "conversation.pipeline_stage_and_notify",
                    pipeline_run_id,
                    pipeline_run_row["actor_id"],
                    background_tasks=background_tasks,
                )
        if new_pipeline_run_id is not None:
            # 배정(assignment) 승인으로 새로 시작된 run — 연결된 대화가
            # 없으므로 `execute-pipeline` 엔드포인트와 동일하게 알림
            # 콜백 없는 plain 버전을 쓴다. actor_id는 승인자(`user["id"]`)
            # 가 아니라 배정을 원래 요청한 사람(`new_pipeline_run_actor_id`)
            # — 위 self-approval 교착과 똑같은 이유.
            assert new_pipeline_run_actor_id is not None
            job_queue.enqueue(
                conn, "pipeline.stage",
                new_pipeline_run_id,
                new_pipeline_run_actor_id,
                background_tasks=background_tasks,
            )
        return {
            "id": approval_id,
            "status": new_status,
            "next_step_pending": next_step_pending,
            "assignment": assignment_result,
            "deliverable": deliverable_result,
            "pipeline_run_id": pipeline_run_id,
            "new_pipeline_run_id": new_pipeline_run_id,
            "action": action_result,
        }
