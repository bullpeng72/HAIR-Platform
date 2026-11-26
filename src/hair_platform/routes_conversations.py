"""대화·지식 API(대화·지식 등록 결재·지식 조각·문서 색인·조직 관계 그래프) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    HTTPException,
    UploadFile,
)

from hair_platform import job_queue  # 백그라운드 작업 큐(2026-11-26, P1-6 2단계)
from hair_platform import (
    conversations,
    documents,
    graphquery,
    knowledge_access,
    knowledge_submissions,
    vectorstore,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    BriefDraftRequest,
    ContentFixSuggestRequest,
    ConversationAutoReplyUpdate,
    ConversationCreate,
    ConversationMessageContentUpdate,
    ConversationMessageCreate,
    ConversationModelUpdate,
    ConversationParticipantInvite,
    KnowledgeSubmissionCreate,
    ManualKnowledgeTextCreate,
)
from hair_platform.routes_common import (
    _knowledge_submission_http_error,
    _reject_if_too_large,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """대화·지식 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.post("/api/conversations/{conversation_id}/brief-draft")
    def conversation_brief_draft(
        conversation_id: int,
        payload: BriefDraftRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        """대화 → 업무 요청 초안(2026-11-23, P1-4) — 저장하지 않는다. 사람이 다듬어
        `POST /api/briefs`(source_conversation_id 포함)로 제출한다."""
        try:
            return conversations.brief_draft_from_conversation(
                conn, conversation_id, user["id"], payload.message_ids, payload.use_llm
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="이 대화의 참가자만 전환할 수 있습니다") from exc
        except ValueError as exc:
            detail = {"conversation_not_found": "대화를 찾을 수 없습니다",
                      "no_messages": "전환할 메시지가 없습니다"}.get(str(exc), str(exc))
            raise HTTPException(status_code=404 if "not_found" in str(exc) else 400, detail=detail) from exc

    @app.post("/api/conversations", status_code=201)
    def create_conversation_endpoint(
        payload: ConversationCreate, user: sqlite3.Row = Depends(get_current_user)
    ):
        try:
            return conversations.create_conversation(
                conn, user["id"], payload.title, payload.linked_task_id
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="연결할 업무를 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 참가자만 이 업무에 대화를 연결할 수 있습니다",
            ) from None

    @app.get("/api/conversations")
    def list_conversations_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        return conversations.list_conversations_for_user(conn, user["id"])

    @app.get("/api/conversations/mentionable")
    def search_mentionable_endpoint(
        q: str = "", user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 결정(4) — 미초대 대상 검색 범위는 조직 전체. 누구나
        접근 가능(초대 자체가 admin/manager 전용이 아니므로 검색도
        마찬가지). `actor_id`는 역할/스킬 매칭 자체엔 안 쓰이고 "이
        사람이 최근 초대한 대상"을 앞세우는 정렬에만 쓰인다(검색
        결과를 사용자별로 숨기지 않음 — 여전히 조직 전체 검색)."""
        return conversations.search_mentionable(conn, q, user["id"])

    @app.get("/api/conversations/my-mentions")
    def list_my_mentions_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        """사용자 요청(2026-10-14, "호출 받은 사람에게 브라우저 알림...")
        — 프런트엔드가 전역 폴링으로 호출해 브라우저 알림을 띄울지
        판단한다. 참가 중인 특정 대화 화면이 열려 있지 않아도(다른
        탭을 보고 있어도) 동작해야 하므로, 특정 `conversation_id`가
        아니라 `/api/conversations` 바로 아래(로그인한 사용자 전역)에
        둔다. "안 읽음" 판정은 서버가 `conversation_participants.
        last_read_message_id`로 들고 있다(클라이언트는 커서를 안 들고
        있어도 된다) — `mark_conversation_read()`/`GET .../messages` 참고."""
        return conversations.list_my_recent_mentions(conn, user["id"])

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        conv = conversations.get_conversation(conn, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            )
        return conv

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-10, "대화 세션 삭제/나가기 기능을
        구현해줘") — 만든 사람만 대화를 통째로 지울 수 있다. 다른
        참가자는 `/leave`로 자기 참가만 뺀다."""
        try:
            conversations.delete_conversation(conn, conversation_id, user["id"])
        except ValueError:
            raise HTTPException(
                status_code=404, detail="대화를 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화를 만든 사람만 삭제할 수 있습니다"
            ) from None
        return {"id": conversation_id, "deleted": True}

    @app.post("/api/conversations/{conversation_id}/leave")
    def leave_conversation_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-10) — 참가자가 자기 자신을 대화에서
        뺀다. 마지막 사람이 나가면 대화 자체가 삭제된다(응답의
        `conversation_deleted`로 알려준다)."""
        try:
            return conversations.leave_conversation(conn, conversation_id, user["id"])
        except ValueError:
            raise HTTPException(
                status_code=404, detail="대화를 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 나갈 수 있습니다"
            ) from None

    @app.patch("/api/conversations/{conversation_id}/model")
    def update_conversation_model_endpoint(
        conversation_id: int,
        payload: ConversationModelUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            return conversations.update_model_settings(
                conn, conversation_id, user["id"], payload.provider, payload.model
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="대화를 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            ) from None

    @app.patch("/api/conversations/{conversation_id}/ai-auto-reply")
    def update_conversation_auto_reply_endpoint(
        conversation_id: int,
        payload: ConversationAutoReplyUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            return conversations.update_auto_reply(
                conn, conversation_id, user["id"], payload.enabled
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="대화를 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            ) from None

    @app.post("/api/conversations/{conversation_id}/participants", status_code=201)
    def invite_conversation_participant_endpoint(
        conversation_id: int,
        payload: ConversationParticipantInvite,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            return conversations.invite_participant(
                conn,
                conversation_id,
                user["id"],
                payload.participant_type,
                payload.participant_id,
            )
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 초대할 수 있습니다"
            ) from None
        except ValueError as exc:
            reason = str(exc)
            if reason == "conversation_not_found":
                raise HTTPException(
                    status_code=404, detail="대화를 찾을 수 없습니다"
                ) from None
            if reason == "agent_not_found":
                raise HTTPException(
                    status_code=404, detail="Agent를 찾을 수 없습니다"
                ) from None
            if reason == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from None
            if reason == "user_not_found":
                raise HTTPException(
                    status_code=404, detail="사용자를 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 초대된 참가자입니다"
            ) from None

    @app.get("/api/conversations/{conversation_id}/participants")
    def list_conversation_participants_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            )
        return conversations.list_participants(conn, conversation_id)

    @app.get("/api/conversations/{conversation_id}/jobs")
    def list_conversation_jobs_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            )
        return conversations.list_jobs(conn, conversation_id)

    @app.get("/api/conversations/{conversation_id}/messages")
    def list_conversation_messages_endpoint(
        conversation_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 볼 수 있습니다"
            )
        # 버그 수정(2026-10-14, "호출이 안 되는 거 같아") — 이 대화를
        # 실제로 보고 있다는 신호라 "읽음"으로 표시한다(`GET /my-mentions`
        # 폴링의 안 읽음 판정 기준). 조회수 집계가 아니라 사람이 직접
        # 연 화면이므로 매번 갱신해도 괜찮다.
        conversations.mark_conversation_read(conn, conversation_id, user["id"])
        return conversations.list_messages(conn, conversation_id)

    @app.get("/api/conversations/{conversation_id}/messages/{message_id}/evaluation-report")
    def conversation_message_evaluation_report(
        conversation_id: int, message_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """대화 AI 응답의 Agent-Evaluator HTML 리포트(2026-11-25). 대화 참가자만, 격리된 출처(CSP sandbox)로 연다."""
        from fastapi.responses import Response

        from hair_platform import evaluation_reports

        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(status_code=403, detail="이 대화의 참가자만 볼 수 있습니다")
        row = conn.execute("SELECT 1 FROM conversation_messages WHERE id = ? AND conversation_id = ?",
                           (message_id, conversation_id)).fetchone()
        html = evaluation_reports.html_for_message(conn, message_id) if row else None
        if html is None:
            raise HTTPException(status_code=404, detail="이 메시지에는 평가 리포트가 없습니다")
        return Response(
            content=html, media_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": "sandbox allow-scripts allow-popups", "X-Content-Type-Options": "nosniff",
                     "Cache-Control": "private, max-age=300"},
        )

    @app.post("/api/conversations/{conversation_id}/messages", status_code=201)
    def create_conversation_message_endpoint(
        conversation_id: int,
        payload: ConversationMessageCreate,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """세 가지 트리거를 전부 여기서 오케스트레이션한다(각자 독립
        job이라 "이미 실행 중" 가드 없이 동시에 돌 수 있다 — 실제 그룹
        채팅에서 여러 봇이 동시에 응답하는 것과 같은 모델): (1) 멘션된
        Agent마다 `run_agent_mention_job`, (2) 멘션된 Team마다
        `run_team_mention_job`, (3) 멘션이 하나도 없을 때만 — `skill`이
        명시됐거나(명시적 요청이라 항상 트리거) `ai_auto_reply=1`이면
        `run_default_ai_job`.

        사용자 보고(2026-10-22, 스크린샷 — 스킬을 고른 채로 "@기획서
        작성자"를 멘션해 보냈더니 Agent 응답과 별개로 "기본 AI" job이
        하나 더 돌았다) — 이전엔 `skill`이 있으면 멘션 여부와 무관하게
        무조건 기본 AI도 같이 트리거했다(`result["skill"] or (not
        has_mentions and ai_auto_reply)` — `or`라 `skill`만으로 조건이
        참이 됨). 호출 대상(Human/Agent/Team 멘션)이 있으면 그 호출이
        우선이고, 스킬 선택은 "멘션이 없을 때(= 기본 AI가 응답할 때)
        어떤 지식으로 그라운딩할지"를 고르는 용도이므로, 멘션이 있으면
        스킬이 선택돼 있어도 기본 AI를 더 띄우지 않는다(응답 중복
        방지 — Human 멘션만 있고 Agent/Team 멘션은 없는 경우도
        `has_mentions=True`라 똑같이 막힌다)."""
        try:
            result = conversations.create_message(
                conn,
                conversation_id,
                user["id"],
                payload.content,
                payload.skill,
                context_override=payload.context_override,
            )
        except ValueError as exc:
            if str(exc) == "conversation_not_found":
                raise HTTPException(
                    status_code=404, detail="대화를 찾을 수 없습니다"
                ) from None
            if str(exc) == "skill_not_found":
                raise HTTPException(
                    status_code=404, detail="스킬을 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=400, detail="내용을 입력해야 합니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 메시지를 보낼 수 있습니다"
            ) from None

        triggered_jobs = []
        for agent in result["mentioned_agents"]:
            job_id = conversations.start_agent_mention_job(
                conn, conversation_id, agent["id"], user["id"]
            )
            job_queue.enqueue(
                conn, "conversation.agent_mention",
                job_id,
                conversation_id,
                agent["id"],
                user["id"],
                background_tasks=background_tasks,
            )
            triggered_jobs.append(
                {
                    "trigger_type": "agent_mention",
                    "job_id": job_id,
                    "agent_id": agent["id"],
                }
            )
        for team in result["mentioned_teams"]:
            job_id = conversations.start_team_mention_job(
                conn, conversation_id, team["id"], user["id"]
            )
            job_queue.enqueue(
                conn, "conversation.team_mention",
                job_id,
                conversation_id,
                team["id"],
                user["id"],
                background_tasks=background_tasks,
            )
            triggered_jobs.append(
                {
                    "trigger_type": "team_mention",
                    "job_id": job_id,
                    "team_id": team["id"],
                }
            )
        if not result["has_mentions"] and (result["skill"] or result["ai_auto_reply"]):
            job_id = conversations.start_default_ai_job(
                conn, conversation_id, user["id"], result["skill"]
            )
            job_queue.enqueue(
                conn, "conversation.default_ai",
                job_id,
                conversation_id,
                user["id"],
                result["skill"],
                background_tasks=background_tasks,
            )
            triggered_jobs.append(
                {
                    "trigger_type": "skill" if result["skill"] else "auto_reply",
                    "job_id": job_id,
                }
            )

        return {"id": result["id"], "triggered_jobs": triggered_jobs}

    @app.post(
        "/api/conversations/{conversation_id}/messages/{message_id}/save-as-deliverable",
        status_code=201,
    )
    def save_conversation_message_as_deliverable_endpoint(
        conversation_id: int,
        message_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물
        (필요시 지정한 결과물)이 갤러리에 가도록") — 대화 메시지(사람
        또는 Agent) 하나를 명시적으로 결과물로 승격해 갤러리에 보낸다.
        대화의 모든 메시지를 자동으로 결과물화하지 않는 이유는
        `conversations.save_message_as_deliverable()` 참고."""
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 저장할 수 있습니다"
            )
        try:
            deliverable_id = conversations.save_message_as_deliverable(
                conn, conversation_id, message_id, user["id"]
            )
        except ValueError as exc:
            detail = {
                "conversation_not_found": "대화를 찾을 수 없습니다",
                "message_not_found": "메시지를 찾을 수 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=404, detail=detail) from None
        return {"deliverable_id": deliverable_id}

    @app.post(
        "/api/conversations/{conversation_id}/messages/{message_id}/save-as-knowledge",
        status_code=201,
    )
    def save_conversation_message_as_knowledge_endpoint(
        conversation_id: int,
        message_id: int,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """대화 메시지를 지식베이스로 **제출**한다(2026-11-20 개편 — 이전엔
        admin/manager가 누르면 결재 없이 즉시 색인했다). `POST /api/
        knowledge-submissions`(source_type='conversation_message')와 같은
        동작의 호환용 경로 — 참여자 누구나 제출하고 admin 결재 후 등록된다
        (admin 본인 제출은 즉시 등록). 응답은 제출 건(`status` 포함)."""
        try:
            result = conversations.save_message_as_knowledge(
                conn, conversation_id, message_id, user["id"], user["role"]
            )
        except ValueError as exc:
            if str(exc) == "message_not_found":
                raise HTTPException(
                    status_code=404, detail="메시지를 찾을 수 없습니다"
                ) from None
            raise _knowledge_submission_http_error(exc) from None
        except PermissionError as exc:
            raise _knowledge_submission_http_error(exc) from None
        if result.pop("needs_registration"):
            job_queue.enqueue(
                conn, "knowledge.register_submission",
                result["id"],
                background_tasks=background_tasks,
            )
        return result

    @app.patch("/api/conversations/{conversation_id}/messages/{message_id}")
    def edit_conversation_message_endpoint(
        conversation_id: int,
        message_id: int,
        payload: ConversationMessageContentUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된
        HTML을 수정할 수 있는 기능") 1단계 — Agent가 만든 발표자료
        HTML에 색 대비 등의 버그가 있을 때 소스를 직접 고쳐 저장한다.
        권한은 삭제와 동일(`conversations.edit_message()` 참고)."""
        try:
            updated = conversations.edit_message(
                conn, conversation_id, message_id, user["id"], payload.content
            )
        except ValueError as exc:
            detail = (
                "대화를 찾을 수 없습니다"
                if str(exc) == "conversation_not_found"
                else "메시지를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from None
        except PermissionError as exc:
            detail = (
                "이 대화의 참가자만 메시지를 고칠 수 있습니다"
                if str(exc) == "not_a_participant"
                else "본인이 보낸 메시지이거나 대화를 만든 사람만 고칠 수 있습니다"
            )
            raise HTTPException(status_code=403, detail=detail) from None
        return updated

    @app.post("/api/conversations/{conversation_id}/messages/{message_id}/suggest-fix")
    def suggest_message_fix_endpoint(
        conversation_id: int,
        message_id: int,
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
            suggestion = conversations.suggest_message_fix(
                conn,
                conversation_id,
                message_id,
                user["id"],
                payload.feedback,
                payload.provider,
                payload.model,
            )
        except ValueError as exc:
            detail = (
                "대화를 찾을 수 없습니다"
                if str(exc) == "conversation_not_found"
                else "메시지를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from None
        except PermissionError as exc:
            detail = (
                "이 대화의 참가자만 수정을 요청할 수 있습니다"
                if str(exc) == "not_a_participant"
                else "본인이 보낸 메시지이거나 대화를 만든 사람만 수정을 요청할 수 있습니다"
            )
            raise HTTPException(status_code=403, detail=detail) from None
        return {"suggestion": suggestion}

    @app.delete("/api/conversations/{conversation_id}/messages/{message_id}")
    def delete_conversation_message_endpoint(
        conversation_id: int,
        message_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 본인이
        보낸 사람 메시지는 본인이, 그 외(AI/Agent 응답 포함)는 대화
        생성자가 지운다(`conversations.delete_message()` 참고)."""
        try:
            conversations.delete_message(conn, conversation_id, message_id, user["id"])
        except ValueError as exc:
            detail = (
                "대화를 찾을 수 없습니다"
                if str(exc) == "conversation_not_found"
                else "메시지를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from None
        except PermissionError as exc:
            detail = (
                "이 대화의 참가자만 메시지를 지울 수 있습니다"
                if str(exc) == "not_a_participant"
                else "본인이 보낸 메시지이거나 대화를 만든 사람만 지울 수 있습니다"
            )
            raise HTTPException(status_code=403, detail=detail) from None
        return {"id": message_id, "deleted": True}

    @app.post("/api/documents", status_code=201)
    async def upload_document(
        file: UploadFile,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        raw_bytes = await file.read()
        _reject_if_too_large(file.filename or "", raw_bytes)

        try:
            return documents.ingest_document(
                conn, user["id"], file.filename or "업로드 파일", raw_bytes
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/conversations/{conversation_id}/attachments", status_code=201)
    async def upload_conversation_attachment(
        conversation_id: int,
        file: UploadFile,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """대화의 참가자만(역할 무관) 올릴 수 있다. VectorDB/문서 저장소를
        전혀 거치지 않고 추출한 텍스트를 그대로 대화 메시지로 남긴다 —
        `conversations.create_attachment_message()` 참고(휘발성 설계)."""
        if not conversations.is_participant(conn, conversation_id, user["id"]):
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 파일을 올릴 수 있습니다"
            )
        raw_bytes = await file.read()
        _reject_if_too_large(file.filename or "", raw_bytes)

        filename = file.filename or "첨부 파일"
        try:
            text = documents.extract_text(filename, raw_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            message_id = conversations.create_attachment_message(
                conn, conversation_id, user["id"], filename, text, raw_bytes=raw_bytes
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="대화를 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403, detail="이 대화의 참가자만 파일을 올릴 수 있습니다"
            ) from exc

        return {
            "conversation_message_id": message_id,
            "filename": filename,
            "char_count": len(text),
        }

    @app.get("/api/documents")
    def list_documents_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return documents.list_documents(conn)

    @app.patch("/api/documents/{document_id}/visibility")
    def set_document_visibility_endpoint(
        document_id: int,
        payload: dict = Body(...),
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """문서 공개 범위(2026-11-25, P1-5) — all·manager·admin·team:<팀 id>. manager는 자기가 못 보게 되는
        "admin만"으로 바꾸거나 이미 "admin만"인 문서를 바꾸지 못한다(admin 전용 자료 보호)."""
        visibility = str(payload.get("visibility") or "")
        if user["role"] != Role.ADMIN.value:
            current = conn.execute("SELECT visibility FROM documents WHERE id = ?", (document_id,)).fetchone()
            if visibility == "admin" or (current is not None and current[0] == "admin"):
                raise HTTPException(status_code=403, detail="'admin만' 공개 범위는 admin만 정하거나 바꿀 수 있습니다")
        try:
            return knowledge_access.set_visibility(conn, user["id"], document_id, visibility)
        except ValueError as exc:
            status = 404 if "찾을 수 없습니다" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/documents/{document_id}")
    def delete_document_endpoint(
        document_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            documents.delete_document(conn, user["id"], document_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="문서를 찾을 수 없습니다"
            ) from exc
        return {"id": document_id, "deleted": True}

    @app.post("/api/documents/text", status_code=201)
    def upload_document_text(
        payload: ManualKnowledgeTextCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """파일 없이 텍스트를 바로 붙여넣어 색인한다(`지식 자료실`의
        업로드와 같은 색인기, 추출 단계만 없음)."""
        try:
            return documents.ingest_text(conn, user["id"], payload.text, payload.label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/knowledge-chunks")
    def list_knowledge_chunks_endpoint(
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return vectorstore.list_chunks(conn, limit, offset)

    @app.get("/api/knowledge-chunks/stats")
    def get_knowledge_chunks_stats(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return vectorstore.stats(conn)

    @app.delete("/api/knowledge-chunks/{chunk_id}")
    def delete_knowledge_chunk_endpoint(
        chunk_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            vectorstore.delete_chunk(conn, user["id"], chunk_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="조각을 찾을 수 없습니다"
            ) from exc
        return {"id": chunk_id, "deleted": True}

    @app.post("/api/knowledge-submissions", status_code=201)
    def create_knowledge_submission(
        payload: KnowledgeSubmissionCreate,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            result = knowledge_submissions.submit(
                conn,
                user["id"],
                user["role"],
                payload.source_type,
                payload.source_id,
                title=payload.title,
                note=payload.note,
                allow_code=payload.allow_code,
            )
        except (ValueError, PermissionError) as exc:
            raise _knowledge_submission_http_error(exc) from None
        if result.pop("needs_registration"):
            # admin 본인 제출 — 결재 없이 바로 색인(경로 A와 같은 권한).
            job_queue.enqueue(
                conn, "knowledge.register_submission",
                result["id"],
                background_tasks=background_tasks,
            )
        return result

    @app.get("/api/knowledge-submissions")
    def list_knowledge_submissions(
        status: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        return knowledge_submissions.list_submissions(
            conn, user["id"], user["role"], status, source_type, source_id
        )

    @app.post("/api/knowledge-submissions/preview")
    def preview_knowledge_submission(
        payload: KnowledgeSubmissionCreate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """제출 모달 — 저장 없이 등록될 텍스트(전처리 결과)를 미리 보여준다."""
        try:
            return knowledge_submissions.preview(
                conn,
                user["id"],
                user["role"],
                payload.source_type,
                payload.source_id,
                allow_code=payload.allow_code,
            )
        except (ValueError, PermissionError) as exc:
            raise _knowledge_submission_http_error(exc) from None

    # `/{submission_id}`보다 먼저 선언해야 "status"가 정수 경로로 잡히지 않는다.
    @app.get("/api/knowledge-submissions/status")
    def knowledge_submission_status(
        source_type: str,
        ids: str = "",
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """배지용 — `ids`는 쉼표 구분 원본 id 목록. `{원본id: 최신 상태}`."""
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids는 쉼표로 구분한 정수여야 합니다") from None
        return knowledge_submissions.status_map(
            conn, user["id"], user["role"], source_type, id_list
        )

    @app.get("/api/knowledge-submissions/{submission_id}")
    def get_knowledge_submission(
        submission_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            row = knowledge_submissions.get_submission(conn, submission_id)
        except ValueError as exc:
            raise _knowledge_submission_http_error(exc) from None
        if user["role"] != Role.ADMIN.value and row["submitted_by"] != user["id"]:
            raise HTTPException(status_code=404, detail="지식 등록 요청을 찾을 수 없습니다")
        return row

    @app.post("/api/knowledge-submissions/{submission_id}/withdraw")
    def withdraw_knowledge_submission(
        submission_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        try:
            return knowledge_submissions.withdraw(conn, submission_id, user["id"])
        except (ValueError, PermissionError) as exc:
            raise _knowledge_submission_http_error(exc) from None

    @app.post("/api/knowledge-submissions/{submission_id}/retry")
    def retry_knowledge_submission(
        submission_id: int,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """임베딩 실패(`failed`)한 승인 건을 다시 색인한다(admin 전용 —
        이미 결재를 통과한 건이므로 결재를 다시 거치지 않는다)."""
        try:
            row = knowledge_submissions.get_submission(conn, submission_id)
        except ValueError as exc:
            raise _knowledge_submission_http_error(exc) from None
        if row["status"] != "failed":
            raise HTTPException(status_code=409, detail="실패한 요청만 재시도할 수 있습니다")
        conn.execute(
            "UPDATE knowledge_submissions SET status = 'registering', error = NULL "
            "WHERE id = ?",
            (submission_id,),
        )
        conn.commit()
        job_queue.enqueue(
            conn, "knowledge.register_submission",
            submission_id,
            background_tasks=background_tasks,
        )
        return {**row, "status": "registering", "error": None}

    @app.get("/api/graph/relationships")
    def list_graph_relationships(
        limit: int = 100,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """GraphDB는 별도 저장소가 없어(`graphquery.py` 참고) 조회만
        제공한다 — 관계 자체를 바꾸려면 "사람 인력 관리"에서 상사를
        지정하거나 배정을 승인/반려해야 한다."""
        return graphquery.org_chart_summary(conn, limit=max(1, min(limit, 200)))

    @app.get("/api/graph/stats")
    def get_graph_stats(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return graphquery.stats(conn)
