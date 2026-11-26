"""연동 설정 API(2026-11-25 — `create_app()` 분리 1단계, 기술 부채 정리).

`create_app()`이 5,900줄을 넘는 한 함수라(모든 라우트가 그 안의 클로저) 읽고 고치기 어려웠다. 자기완결적인 라우트
묶음부터 모듈로 옮긴다 — 이 파일은 "연동 설정" 화면이 쓰는 API(개인 API 키·코드 저장소 등록·기본 구성 점검·외부
알림·정기 실행·이벤트 트리거)이고, 본문은 옮기기 전과 한 글자도 다르지 않다(같은 경로·권한·응답).

다음 묶음도 같은 방식으로 옮긴다: `register(app, conn)` 함수 하나에 라우트를 정의하고 `create_app()`에서 부른다.
라우트가 `create_app()` 안의 다른 지역 함수에 기대면 그 함수를 먼저 모듈 수준으로 옮긴다.
"""

from __future__ import annotations

import sqlite3

from fastapi import Body, Depends, FastAPI, HTTPException

from hair_platform.auth import Role, require_role
from hair_platform.models import (
    ActiveToggle,
    ApiKeyCreate,
    AutomationTriggerCreate,
    CodeRepositoryActive,
    CodeRepositoryCreate,
    ConfigCheckApply,
    NotifyConfigUpdate,
    PipelineScheduleCreate,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """연동 설정 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    # ---------- 개인 API 키(2026-11-23, P1-5 — `integrations.py`) ----------
    @app.get("/api/api-keys")
    def list_api_keys_endpoint(
        all_users: bool = False,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        """내 키 목록(원문 토큰은 다시 볼 수 없다). admin은 `all_users=true`로 전체를 본다."""
        from hair_platform import integrations

        if all_users and user["role"] != Role.ADMIN.value:
            raise HTTPException(status_code=403, detail="전체 키 목록은 admin만 볼 수 있습니다")
        return integrations.list_api_keys(conn, None if all_users else user["id"])

    @app.post("/api/api-keys", status_code=201)
    def create_api_key_endpoint(
        payload: ApiKeyCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        from hair_platform import integrations

        try:
            return integrations.create_api_key(conn, user["id"], payload.name)
        except ValueError as exc:
            detail = {"empty_name": "키 이름을 입력하세요",
                      "too_many_keys": "사용 중인 키가 너무 많습니다(최대 10개) — 쓰지 않는 키를 폐기하세요"}.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.delete("/api/api-keys/{key_id}")
    def revoke_api_key_endpoint(
        key_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)),
    ):
        from hair_platform import integrations

        try:
            integrations.revoke_api_key(conn, user["id"], key_id, user["role"] == Role.ADMIN.value)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="API 키를 찾을 수 없습니다") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="본인 키만 폐기할 수 있습니다") from exc
        return {"id": key_id, "revoked": True}

    # ---------- 코드 저장소 읽기 연동(2026-11-24, P0-3 1단계 — `code_repos.py`, admin) ----------
    _REPO_ERROR_KO = {
        "invalid_name": "저장소 이름은 글자·숫자·._- 60자 이내로 입력하세요(Agent가 이 이름으로 부릅니다)",
        "path_not_found": "서버에 그 경로가 없습니다(이 서버 PC 안의 폴더 경로를 입력하세요)",
        "not_a_git_repository": "Git 저장소가 아닙니다",
        "name_already_exists": "같은 이름의 저장소가 이미 있습니다",
        "repository_not_found": "저장소를 찾을 수 없습니다",
        "git_not_installed": "서버에 git이 설치돼 있지 않습니다",
    }

    @app.get("/api/code-repositories")
    def list_code_repositories_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import code_repos

        return code_repos.list_repositories(conn, include_inactive=True)

    @app.post("/api/code-repositories", status_code=201)
    def register_code_repository_endpoint(
        payload: CodeRepositoryCreate, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import code_repos

        try:
            return code_repos.register_repository(conn, user["id"], payload.name, payload.path, payload.description)
        except code_repos.RepoError as exc:
            # 커밋이 하나도 없는 저장소 등 git 자체 메시지는 그대로 보여 준다.
            raise HTTPException(status_code=400, detail=_REPO_ERROR_KO.get(str(exc), str(exc))) from exc

    @app.patch("/api/code-repositories/{repo_id}")
    def set_code_repository_active_endpoint(
        repo_id: int, payload: CodeRepositoryActive, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import code_repos

        try:
            code_repos.set_repository_active(conn, user["id"], repo_id, payload.is_active)
        except code_repos.RepoError as exc:
            raise HTTPException(status_code=404, detail=_REPO_ERROR_KO.get(str(exc), str(exc))) from exc
        return {"id": repo_id, "is_active": payload.is_active}

    # ---------- 사내 DB 조회 연동(2026-11-26, P1-9 — `data_connectors.py`, admin) ----------
    @app.get("/api/data-connections")
    def list_data_connections_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import data_connectors

        return data_connectors.list_connections(conn, include_inactive=True)

    @app.post("/api/data-connections", status_code=201)
    def register_data_connection_endpoint(
        payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import data_connectors

        try:
            return data_connectors.register(
                conn, user["id"], str(payload.get("name") or ""), str(payload.get("kind") or ""),
                str(payload.get("target") or ""), username=payload.get("username") or None,
                password_env=payload.get("password_env") or None, description=str(payload.get("description") or ""),
                row_limit=int(payload.get("row_limit") or data_connectors.DEFAULT_ROW_LIMIT),
            )
        except (data_connectors.ConnectorError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/data-connections/{connection_id}")
    def set_data_connection_active_endpoint(
        connection_id: int, payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import data_connectors

        try:
            return data_connectors.set_active(conn, user["id"], connection_id, bool(payload.get("is_active")))
        except data_connectors.ConnectorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/data-connections/{connection_id}/test")
    def test_data_connection_endpoint(connection_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """연결 확인 — 테이블 목록을 읽어 본다(쓰기 없음)."""
        from hair_platform import data_connectors

        try:
            row = data_connectors.get(conn, connection_id)
            return {"ok": True, "tables": data_connectors.list_tables(conn, row["name"])}
        except data_connectors.ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- 메일 수신함 읽기(2026-11-26, P1-9 2단계 — `mail_inbox.py`, admin) ----------
    @app.get("/api/mail-inboxes")
    def list_mail_inboxes_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import mail_inbox

        return mail_inbox.list_inboxes(conn, include_inactive=True)

    @app.post("/api/mail-inboxes", status_code=201)
    def register_mail_inbox_endpoint(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import mail_inbox

        try:
            return mail_inbox.register(
                conn, user["id"], str(payload.get("name") or ""), str(payload.get("host") or ""),
                str(payload.get("username") or ""), str(payload.get("password_env") or ""),
                port=int(payload.get("port") or 993), folder=str(payload.get("folder") or "INBOX"),
                visibility=str(payload.get("visibility") or "admin"), description=str(payload.get("description") or ""),
            )
        except (mail_inbox.MailError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/mail-inboxes/{inbox_id}")
    def update_mail_inbox_endpoint(inbox_id: int, payload: dict = Body(...),
                                   user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import mail_inbox

        try:
            return mail_inbox.update(
                conn, user["id"], inbox_id,
                is_active=bool(payload["is_active"]) if "is_active" in payload else None,
                visibility=str(payload["visibility"]) if payload.get("visibility") else None,
            )
        except mail_inbox.MailError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/mail-inboxes/{inbox_id}/test")
    def test_mail_inbox_endpoint(inbox_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """연결 확인 — admin 본인 이름으로 최근 1일 메일을 검색해 본다(읽기 전용)."""
        from hair_platform import knowledge_access, mail_inbox

        try:
            box = mail_inbox.get(conn, inbox_id)
            if not box["is_active"]:
                raise mail_inbox.MailError("꺼진 메일함입니다")
            with knowledge_access.as_viewer(user["id"]):
                found = mail_inbox.search(conn, box["name"], days=1, limit=5)
            return {"ok": True, "recent": len(found)}
        except mail_inbox.MailError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- 캘린더 읽기(2026-11-26, P1-9 3단계 — `calendar_feeds.py`, admin) ----------
    @app.get("/api/calendar-feeds")
    def list_calendar_feeds_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import calendar_feeds

        return calendar_feeds.list_feeds(conn, include_inactive=True)

    @app.post("/api/calendar-feeds", status_code=201)
    def register_calendar_feed_endpoint(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import calendar_feeds

        try:
            return calendar_feeds.register(
                conn, user["id"], str(payload.get("name") or ""), str(payload.get("source_kind") or ""),
                str(payload.get("source") or ""), visibility=str(payload.get("visibility") or "admin"),
                description=str(payload.get("description") or ""),
            )
        except (calendar_feeds.CalendarError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/calendar-feeds/{feed_id}")
    def update_calendar_feed_endpoint(feed_id: int, payload: dict = Body(...),
                                      user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import calendar_feeds

        try:
            return calendar_feeds.update(
                conn, user["id"], feed_id,
                is_active=bool(payload["is_active"]) if "is_active" in payload else None,
                visibility=str(payload["visibility"]) if payload.get("visibility") else None,
            )
        except calendar_feeds.CalendarError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calendar-feeds/{feed_id}/test")
    def test_calendar_feed_endpoint(feed_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """연결 확인 — admin 본인 이름으로 오늘부터 30일 일정을 읽어 본다(읽기 전용)."""
        from hair_platform import calendar_feeds, knowledge_access

        try:
            feed = calendar_feeds.get(conn, feed_id)
            if not feed["is_active"]:
                raise calendar_feeds.CalendarError("꺼진 캘린더입니다")
            calendar_feeds._CACHE.pop(feed_id, None)
            with knowledge_access.as_viewer(user["id"]):
                r = calendar_feeds.events_between(conn, feed["name"], days=30)
            # 테스트용 ICS는 과거 날짜일 수 있어, 30일 안이 비면 전체 VEVENT 수도 알려 준다
            total = len(calendar_feeds.parse_events(calendar_feeds._fetch(conn.execute(
                "SELECT * FROM calendar_feeds WHERE id = ?", (feed_id,)).fetchone()), calendar_feeds.local_tz()))
            return {"ok": True, "upcoming": len(r["events"]), "events": total, "tz": r["tz"]}
        except calendar_feeds.CalendarError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- 기본 구성 점검(2026-11-23, P2-10 — `config_check.py`, admin) ----------
    @app.get("/api/admin/config-check")
    def config_check_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import config_check

        return config_check.check(conn)

    @app.post("/api/admin/config-check/apply")
    def config_check_apply_endpoint(
        payload: ConfigCheckApply, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import config_check

        return config_check.apply(conn, user["id"], payload.item_ids)

    # ---------- 외부 알림 웹훅(2026-11-23, P1-5 — `integrations.py`, admin) ----------
    @app.get("/api/integrations/notify")
    def get_notify_config_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import integrations

        return {**integrations.get_notify_config(conn), "available_events": integrations.NOTIFY_EVENTS}

    @app.put("/api/integrations/notify")
    def set_notify_config_endpoint(
        payload: NotifyConfigUpdate, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        from hair_platform import integrations

        try:
            return integrations.set_notify_config(conn, user["id"], payload.webhook_url, payload.events,
                                                  payload.emails)
        except ValueError as exc:
            msg = str(exc)
            detail = ("웹훅 URL은 http:// 또는 https://로 시작해야 합니다" if msg == "invalid_webhook_url"
                      else "메일 주소 형식이 맞지 않습니다: " + msg.split(":", 1)[1] if msg.startswith("invalid_emails")
                      else f"메일 수신자는 {integrations.MAX_NOTIFY_EMAILS}명까지입니다" if msg == "too_many_emails"
                      else msg)
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.post("/api/integrations/notify/test")
    def test_notify_endpoint(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """웹훅과 메일(설정된 것만)에 테스트를 보낸다(2026-11-24 — 메일 채널 추가)."""
        from hair_platform import integrations

        cfg = integrations.get_notify_config(conn)
        if not cfg["webhook_url"] and not cfg["emails"]:
            raise HTTPException(status_code=400, detail="먼저 웹훅 URL이나 메일 수신자를 저장하세요")
        results = {}
        if cfg["webhook_url"]:
            results["webhook"] = integrations.send_webhook(cfg["webhook_url"], "[HAIR] 알림 연결 테스트입니다.")
        if cfg["emails"]:
            results["email"] = integrations.send_email(cfg["emails"], "[HAIR] 알림 연결 테스트",
                                                       "[HAIR] 메일 알림 연결 테스트입니다.")
        errors = [f"{'웹훅' if k == 'webhook' else '메일'}: {err}" for k, (ok, err) in results.items() if not ok]
        return {"ok": not errors, "error": " / ".join(errors) or None,
                "channels": {k: ok for k, (ok, _err) in results.items()}}

    # ---------- 정기 실행(2026-11-23, P1-5 — `automation.py`, admin/manager) ----------
    _SCHEDULE_ERRORS = {
        "empty_name": "이름을 입력하세요",
        "empty_brief_text": "업무 요청 내용을 입력하세요",
        "empty_role_name": "할 일 역할을 입력하세요",
        "invalid_interval": "반복 주기를 고르세요",
        "weekday_required": "매주 실행은 요일을 골라야 합니다",
        "invalid_time": "시각은 HH:MM(00:00~23:59) 형식이어야 합니다",
        "pipeline_template_not_found": "파이프라인 템플릿을 찾을 수 없습니다",
        "pipeline_template_inactive": "비활성화된 파이프라인 템플릿입니다",
        "schedule_not_found": "정기 실행을 찾을 수 없습니다",
    }

    @app.get("/api/pipeline-schedules")
    def list_pipeline_schedules(user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        from hair_platform import automation

        return automation.list_schedules(conn)

    @app.post("/api/pipeline-schedules", status_code=201)
    def create_pipeline_schedule(
        payload: PipelineScheduleCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        from hair_platform import automation

        try:
            return automation.create_schedule(conn, user["id"], payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_SCHEDULE_ERRORS.get(str(exc), str(exc))) from exc

    @app.patch("/api/pipeline-schedules/{schedule_id}")
    def toggle_pipeline_schedule(
        schedule_id: int, payload: ActiveToggle,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        from hair_platform import automation

        try:
            return automation.set_active(conn, user["id"], schedule_id, payload.active)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_SCHEDULE_ERRORS.get(str(exc), str(exc))) from exc

    @app.delete("/api/pipeline-schedules/{schedule_id}")
    def delete_pipeline_schedule(
        schedule_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))
    ):
        from hair_platform import automation

        try:
            automation.delete_schedule(conn, user["id"], schedule_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_SCHEDULE_ERRORS.get(str(exc), str(exc))) from exc
        return {"id": schedule_id, "deleted": True}

    # ---------- 이벤트 트리거(2026-11-24, P1-5 — `triggers.py`, admin/manager — 정기 실행과 같은 권한) ----------
    @app.get("/api/automation-triggers")
    def list_automation_triggers(user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        from hair_platform import triggers

        return {"events": triggers.EVENTS, "max_chain_depth": triggers.MAX_CHAIN_DEPTH,
                "triggers": triggers.list_all(conn)}

    @app.post("/api/automation-triggers", status_code=201)
    def create_automation_trigger(
        payload: AutomationTriggerCreate, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))
    ):
        from hair_platform import triggers

        try:
            return triggers.create(conn, user["id"], payload.model_dump())
        except triggers.TriggerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/automation-triggers/{trigger_id}")
    def toggle_automation_trigger(
        trigger_id: int, payload: ActiveToggle,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        from hair_platform import triggers

        try:
            return triggers.set_active(conn, user["id"], trigger_id, payload.active)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="트리거를 찾을 수 없습니다") from exc

    @app.delete("/api/automation-triggers/{trigger_id}")
    def delete_automation_trigger(
        trigger_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))
    ):
        from hair_platform import triggers

        try:
            triggers.delete(conn, user["id"], trigger_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="트리거를 찾을 수 없습니다") from exc
        return {"id": trigger_id, "deleted": True}
