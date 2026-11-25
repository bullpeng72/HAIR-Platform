"""결과물 API(결과물·댓글·버전·내보내기·부분 수정 제안) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

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
from fastapi.responses import HTMLResponse

from hair_platform import (
    deliverables,
    webui,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    ContentFixSuggestRequest,
    DeliverableCommentCreate,
    DeliverableContentUpdate,
    EditProposalCreate,
    RepoCommitRequest,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """결과물 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.get("/api/deliverables")
    def gallery_deliverables(
        content_type: str | None = None,
        task_id: int | None = None,
        status: str | None = None,
        sort: str = "created_desc",
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """갤러리(사용자 요청, 2026-11-10, "최종 결과물을 모아보는 갤러리
        기능" → "필터링, 소팅 기능 등을 좀 더 다양하게 강화") —
        admin/manager는 조직 전체, member는 본인이 참가한 Task의
        결과물만. 캐시가 아니라 매번 실시간 조회라 결과물이 삭제되면
        다음 호출부터 자동으로 빠진다(별도 동기화 불필요)."""
        try:
            return deliverables.list_deliverables_gallery(
                conn,
                user["id"],
                user["role"],
                content_type,
                task_id,
                status,
                sort,
                limit,
                offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/deliverables/{deliverable_id}")
    def get_deliverable_endpoint(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """단독 조회 — 갤러리 카드 클릭, 단독 뷰 페이지 양쪽에서 쓴다.
        RBAC은 `list_task_deliverables`와 동일(admin/manager 또는 그
        Task의 참가자)."""
        item = deliverables.get_deliverable(conn, deliverable_id)
        if item is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(
                conn, item["task_id"], user["id"]
            )
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return item

    @app.patch("/api/deliverables/{deliverable_id}")
    def update_deliverable_content_endpoint(
        deliverable_id: int,
        payload: DeliverableContentUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된
        HTML을 수정할 수 있는 기능") 1단계 — Agent가 만든 HTML 산출물에
        색 대비 등의 버그가 있을 때 소스를 직접 고쳐 저장한다. 권한은
        삭제와 동일(`deliverables.update_deliverable_content()` 참고)."""
        try:
            updated = deliverables.update_deliverable_content(
                conn, deliverable_id, user["id"], user["role"], payload.content
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="결과물을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 결과물을 수정할 권한이 없습니다"
            ) from None
        return updated

    @app.post("/api/deliverables/{deliverable_id}/suggest-fix")
    def suggest_deliverable_fix_endpoint(
        deliverable_id: int,
        payload: ContentFixSuggestRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된
        HTML을 수정할 수 있는 기능") 3단계 — "이 부분 색이 안 보여" 같은
        자연어 피드백으로 LLM 수정안을 받는다. 결과는 저장하지 않는다
        (`assist_task()`와 같은 설계 — 검토 후 위 PATCH로 직접 커밋).
        실제 LLM API를 호출하므로 비용이 발생한다. 팀원/뷰어는
        provider/model을 직접 선택할 수 없다(RBAC 결정, 비용 통제 —
        `assist_task()`와 동일)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider or payload.model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )
        try:
            suggestion = deliverables.suggest_content_fix(
                conn,
                deliverable_id,
                user["id"],
                user["role"],
                payload.feedback,
                payload.provider,
                payload.model,
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="결과물을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 결과물의 수정을 요청할 권한이 없습니다"
            ) from None
        return {"suggestion": suggestion}

    @app.delete("/api/deliverables/{deliverable_id}")
    def delete_deliverable_endpoint(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """admin/manager는 항상, 그 외엔 본인이 작성한 draft 상태 결과물만
        삭제 가능(`deliverables.delete_deliverable()` 참고)."""
        try:
            deliverables.delete_deliverable(
                conn, deliverable_id, user["id"], user["role"]
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="결과물을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 결과물을 삭제할 권한이 없습니다"
            ) from None
        conn.commit()
        return {"status": "deleted"}

    @app.get("/api/deliverables/{deliverable_id}/history")
    def get_deliverable_history(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """결과물 버전 이력(2026-11-23, P1-6) — 같은 할 일·같은 작성자가 다시 낸 버전과, 사람이 직접
        고친 기록. 권한은 결과물 조회와 같다(admin/manager 또는 그 할 일의 참가자)."""
        row = conn.execute("SELECT task_id FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, row["task_id"], user["id"]) is None
        ):
            raise HTTPException(status_code=403, detail="이 업무의 결과물을 볼 권한이 없습니다")
        return deliverables.deliverable_history(conn, deliverable_id)

    # 결과물 부분 재작성 제안(2026-11-24, P1-6 — `edit_proposals.py`). 만들기·수락·거절은 결과물 편집과
    # 같은 권한, 목록·섹션 조회는 결과물 조회와 같은 권한. provider/model 직접 선택은 suggest-fix처럼
    # admin/manager만.
    def _deliverable_viewable(deliverable_id: int, user: sqlite3.Row) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, row["task_id"], user["id"]) is None
        ):
            raise HTTPException(status_code=403, detail="이 업무의 결과물을 볼 권한이 없습니다")
        return row

    @app.get("/api/deliverables/{deliverable_id}/sections")
    def deliverable_sections(deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)):
        from hair_platform import edit_proposals

        row = _deliverable_viewable(deliverable_id, user)
        return [{k: s[k] for k in ("index", "title", "chars")} for s in edit_proposals.sections(row["content"])]

    @app.get("/api/deliverables/{deliverable_id}/edit-proposals")
    def list_edit_proposals(deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)):
        from hair_platform import edit_proposals

        _deliverable_viewable(deliverable_id, user)
        return edit_proposals.list_for(conn, deliverable_id)

    @app.post("/api/deliverables/{deliverable_id}/edit-proposals", status_code=201)
    def create_edit_proposal(
        deliverable_id: int, payload: EditProposalCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        from hair_platform import edit_proposals

        if user["role"] not in ("admin", "manager") and (payload.provider or payload.model):
            raise HTTPException(status_code=403, detail="팀원은 provider/model을 직접 선택할 수 없습니다")
        try:
            return edit_proposals.propose(
                conn, deliverable_id, user["id"], user["role"], payload.instruction,
                payload.section_index, payload.selected_text, payload.provider, payload.model,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="이 결과물을 고칠 권한이 없습니다") from exc
        except edit_proposals.ProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/edit-proposals/{proposal_id}/{decision}")
    def decide_edit_proposal(
        proposal_id: int, decision: str,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        from hair_platform import edit_proposals

        if decision not in ("accept", "reject"):
            raise HTTPException(status_code=404, detail="accept 또는 reject만 가능합니다")
        try:
            return edit_proposals.decide(conn, proposal_id, user["id"], user["role"], decision == "accept")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="수정안을 찾을 수 없습니다") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="이 결과물을 고칠 권한이 없습니다") from exc
        except edit_proposals.ProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 결과물 → 코드 저장소 커밋(2026-11-24, P0-3 2단계 — `repo_commit.py`). 저장소 등록과 같은 admin
    # 전용 — 미리보기(아무것도 쓰지 않음)와 커밋(점검 재실행 후 통과 시 main에 로컬 커밋, push 없음).
    @app.post("/api/deliverables/{deliverable_id}/repo-commit/preview")
    def preview_deliverable_repo_commit(
        deliverable_id: int, payload: RepoCommitRequest, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import repo_commit

        try:
            return repo_commit.plan(conn, deliverable_id, payload.repository)
        except repo_commit.CommitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/deliverables/{deliverable_id}/repo-commit")
    def commit_deliverable_to_repository(
        deliverable_id: int, payload: RepoCommitRequest, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import repo_commit

        try:
            return repo_commit.commit(conn, deliverable_id, payload.repository, user, payload.message, payload.mode)
        except repo_commit.CommitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/deliverables/{deliverable_id}/repo-commits")
    def list_deliverable_repo_commits(
        deliverable_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import repo_commit

        return repo_commit.history(conn, deliverable_id)

    @app.get("/api/deliverables/{deliverable_id}/export.docx")
    def export_deliverable_docx(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """결과물 Word 내보내기(2026-11-23, P1-7 — `exports.py`). 권한은 결과물 조회와 같다."""
        from fastapi.responses import Response

        from hair_platform import exports

        row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, row["task_id"], user["id"]) is None
        ):
            raise HTTPException(status_code=403, detail="이 업무의 결과물을 볼 권한이 없습니다")
        if not exports.available():
            raise HTTPException(status_code=400, detail=exports.INSTALL_HINT)
        data = exports.to_docx(row["content"], row["content_type"], title=f"결과물 #{deliverable_id}")
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="deliverable-{deliverable_id}.docx"'},
        )

    # Excel·PowerPoint 내보내기(2026-11-24, P1-7 — `exports.to_xlsx`/`to_pptx`). 권한은 Word와 같다.
    _EXPORTERS = {
        "xlsx": ("to_xlsx", "xlsx_available", "XLSX_INSTALL_HINT",
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "pptx": ("to_pptx", "pptx_available", "PPTX_INSTALL_HINT",
                 "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    }

    @app.get("/api/deliverables/{deliverable_id}/export.{fmt}")
    def export_deliverable_office(
        deliverable_id: int, fmt: str, user: sqlite3.Row = Depends(get_current_user)
    ):
        from fastapi.responses import Response

        from hair_platform import exports

        if fmt not in _EXPORTERS:
            raise HTTPException(status_code=404, detail="지원하지 않는 형식입니다(docx·xlsx·pptx)")
        row = _deliverable_viewable(deliverable_id, user)
        fn, check, hint, media = _EXPORTERS[fmt]
        if not getattr(exports, check)():
            raise HTTPException(status_code=400, detail=getattr(exports, hint))
        data = getattr(exports, fn)(row["content"], row["content_type"], title=f"결과물 #{deliverable_id}")
        return Response(
            content=data, media_type=media,
            headers={"Content-Disposition": f'attachment; filename="deliverable-{deliverable_id}.{fmt}"'},
        )

    @app.get("/api/deliverables/{deliverable_id}/evaluation-report")
    def deliverable_evaluation_report(deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """결과물의 최신 평가에 딸린 Agent-Evaluator HTML 리포트(2026-11-25 — `evaluation_reports.py`). 권한은
        결과물 조회와 같다. 리포트에는 SDK가 만든 스크립트(차트 등)가 들어 있어 CSP `sandbox`로 격리된 출처에서
        열리게 한다 — 이 페이지의 스크립트는 HAIR 로그인 세션·API에 접근할 수 없다."""
        from fastapi.responses import Response

        from hair_platform import evaluation_reports

        _deliverable_viewable(deliverable_id, user)
        html = evaluation_reports.html_for_deliverable(conn, deliverable_id)
        if html is None:
            raise HTTPException(status_code=404, detail="이 결과물에는 평가 리포트가 없습니다(리포트 기능 도입 전 평가이거나 꺼져 있음)")
        return Response(
            content=html, media_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": "sandbox allow-scripts allow-popups", "X-Content-Type-Options": "nosniff",
                     "Cache-Control": "private, max-age=300"},
        )

    @app.post("/api/deliverables/{deliverable_id}/submit")
    def submit_task_deliverable(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """후속(라이브 시나리오 실행에서 발견한 갭 보완) — 사람이
        작성한 결과물을 명시적으로 검토 대상으로 제출한다. Agent
        산출물과 같은 결재 체인을 재사용한다."""
        try:
            result = deliverables.submit_deliverable(conn, deliverable_id, user["id"])
        except ValueError as exc:
            if str(exc) == "deliverable_not_found":
                raise HTTPException(
                    status_code=404, detail="결과물을 찾을 수 없습니다"
                ) from None
            if str(exc) == "not_human_deliverable":
                raise HTTPException(
                    status_code=400,
                    detail="Agent 산출물은 생성 즉시 자동으로 제출됩니다",
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 제출되었거나 처리된 결과물입니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="본인이 작성한 결과물만 제출할 수 있습니다"
            ) from None
        return result

    @app.get("/api/deliverables/{deliverable_id}/comments")
    def get_deliverable_comments(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """2026-10-08, Phase 2 — 초대만 된 사람도 자신이
        초대된 댓글 스레드는 읽을 수 있어야 하므로
        `can_comment_on_task()`(쓰기 판정과 동일)로 열람 권한도 넓힌다."""
        d = conn.execute(
            "SELECT task_id FROM deliverables WHERE id = ?", (deliverable_id,)
        ).fetchone()
        if d is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and not (
            deliverables.can_comment_on_task(conn, d["task_id"], user["id"])
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_comments(conn, deliverable_id)

    @app.post("/api/deliverables/{deliverable_id}/comments", status_code=201)
    def create_deliverable_comment(
        deliverable_id: int,
        payload: DeliverableCommentCreate,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 3 — 댓글에 `@에이전트이름`이
        있고 그 Agent가 이 task에 초대돼 있으면,
        댓글 저장 직후 가볍게 그 Agent를 실행한다(결재·Gate 없음 —
        사용자 결정 a, 결과는 항상 댓글로만 남는다). 이미 다른 실행이
        진행 중이면(버튼이든 다른 멘션이든) 조용히 건너뛰고
        `triggered: false`로 알린다 — 댓글 자체는 멘션 트리거 성패와
        무관하게 항상 저장돼야 한다."""
        try:
            result = deliverables.create_comment(
                conn, deliverable_id, user["id"], payload.content
            )
        except ValueError as exc:
            if str(exc) == "deliverable_not_found":
                raise HTTPException(
                    status_code=404, detail="결과물을 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=400, detail="내용을 입력해야 합니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None

        triggered_jobs = []
        skipped_agent_ids = []
        for agent in result["mentioned_agents"]:
            try:
                job_id = deliverables.start_comment_triggered_execution(
                    conn,
                    result["task_id"],
                    result["id"],
                    agent["id"],
                    user["id"],
                    payload.content,
                )
            except LookupError:
                skipped_agent_ids.append(agent["id"])
                continue
            background_tasks.add_task(
                deliverables.run_comment_triggered_execution_job,
                conn,
                job_id,
                deliverable_id,
                agent["id"],
                user["id"],
                payload.content,
            )
            triggered_jobs.append({"agent_id": agent["id"], "job_id": job_id})

        return {
            "id": result["id"],
            "triggered_jobs": triggered_jobs,
            "skipped_agent_ids": skipped_agent_ids,
        }

    @app.get("/deliverables/{deliverable_id}/view", response_class=HTMLResponse)
    def view_deliverable_page(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """결과물 단독 뷰(사용자 요청, 2026-11-10, "claude의 artifact
        처럼 최종 결과물을 전체 웹으로 확인") — SPA 밖, 새 탭에서 열리는
        독립 페이지지만 로그인 세션은 그대로 필요하다(비인증 공개 URL이
        아님, `Depends(get_current_user)` 유지). RBAC은 단독 조회
        엔드포인트와 동일."""
        item = deliverables.get_deliverable(conn, deliverable_id)
        if item is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(
                conn, item["task_id"], user["id"]
            )
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        task = conn.execute(
            "SELECT role_name FROM tasks WHERE id = ?", (item["task_id"],)
        ).fetchone()
        return webui.render_deliverable_view_page(
            item, task["role_name"] if task is not None else None
        )
