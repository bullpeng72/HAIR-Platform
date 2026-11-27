"""운영 리포트 API(대시보드·비용·공정성·성과 리뷰·활동 기록·조직도) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
)

from hair_platform import (
    cost,
    dashboard,
    fairness,
    performance,
    process_learning,
    process_metrics,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """운영 리포트 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.get("/api/audit-log")
    def get_audit_log(
        limit: int = 50,
        offset: int = 0,
        actor_id: int | None = None,
        subject_type: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """감사로그 조회(Admin·매니저 전용). append-only는 DB 트리거가 강제.

        코드 재점검(2026-09-26)에서 발견: 페이지네이션/필터 없이 전체 테이블을
        반환하던 것을 고쳤다 — append-only라 무한정 커지는 테이블을 무제한
        반환하면 응답 크기·지연 문제로 이어진다.
        """
        limit = max(1, min(limit, 200))
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list[object] = []
        if actor_id is not None:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if subject_type is not None:
            query += " AND subject_type = ?"
            params.append(subject_type)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        # 사용자 요청(2026-10-27, "활동 기록의 경우 처리자 ID, 대상 ID
        # 표기됨 처리자 ID는 아이디로 대상 ID는 agent code로 변경") —
        # 조회 전용·admin/manager만 보는 최대 200행 페이지라 행마다
        # 조회 한두 번 더 하는 비용은 감수한다(핫 패스 아님).
        items = []
        for row in rows:
            item = dict(row)
            actor = conn.execute(
                "SELECT username FROM users WHERE id = ?", (row["actor_id"],)
            ).fetchone()
            item["actor_username"] = actor["username"] if actor else None
            item["subject_code"] = None
            if row["subject_type"] == "agent_pool" and row["subject_id"] is not None:
                agent = conn.execute(
                    "SELECT code FROM agent_pool WHERE id = ?", (row["subject_id"],)
                ).fetchone()
                item["subject_code"] = agent["code"] if agent else None
            items.append(item)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/performance-review")
    def get_performance_review(
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """성과리뷰 조회. Admin·매니저는 전체, 팀원은 본인 관련만(RBAC 매트릭스)."""
        if user["role"] == Role.MEMBER.value:
            return performance.review_for_member(conn, user["id"])
        return performance.full_review(conn)

    # AI 사용 한도(2026-11-25, P2-14 — `ai_budget.py`). 조회는 admin·manager(비용 화면), 변경은 admin.
    @app.get("/api/ai-budget")
    def get_ai_budget(user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        from hair_platform import ai_budget

        return ai_budget.status(conn)

    @app.put("/api/ai-budget")
    def put_ai_budget(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import ai_budget

        try:
            ai_budget.save(conn, user["id"], payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ai_budget.status(conn)

    @app.get("/api/cost-report")
    def get_cost_report(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """비용 조회(Admin·매니저 전용, RBAC 매트릭스)."""
        return cost.report(conn)

    @app.get("/api/process-metrics")
    def get_process_metrics(
        days: int = process_metrics.DEFAULT_PERIOD_DAYS,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """프로세스 성과(2026-11-27) — 파이프라인별 리드타임(AI 작업·사람 대기·그 밖)·한 번에 완료 비율·재작업·
        자동 처리율·건당 비용·기준 시간 대비, 지금 결재를 기다리는 실행, 주별 추이. `days`: 7·30·90·0(전체)."""
        try:
            result = process_metrics.overview(conn, days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        counts = process_learning.candidate_counts(conn)  # 아직 결정 안 한 개선 제안 수(2026-11-27, 2단계)
        # 처리 용량·지금 맡기면(2026-11-27, 4단계 — `capacity`)
        from hair_platform import capacity

        cap = capacity.report(conn)
        est = capacity.estimates(conn, [t["template_id"] for t in result["templates"] if t["is_active"]], cap)
        for t in result["templates"]:
            t["lesson_candidates"] = counts.get(t["template_id"], 0)
            t["estimate"] = est.get(t["template_id"])
        result["capacity"] = cap
        return result

    @app.put("/api/process-metrics/hourly-cost")
    def put_process_hourly_cost(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """사람 1시간 비용(USD, admin) — 기준 시간과 곱해 건당 가치를 계산한다. `{"hourly_cost": 40}` / null이면 지움."""
        v = payload.get("hourly_cost")
        if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)):
            raise HTTPException(status_code=400, detail="hourly_cost_must_be_number")
        try:
            return process_metrics.set_hourly_cost(conn, user["id"], v)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/process-metrics/templates/{template_id}")
    def get_process_metrics_template(
        template_id: int,
        days: int = process_metrics.DEFAULT_PERIOD_DAYS,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """파이프라인 하나의 성과 + 단계별 흐름(어디서 시간을 쓰고 되돌아가는지·지금 몇 건이 걸려 있는지)."""
        try:
            return process_metrics.template_detail(conn, template_id, days)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/process-metrics/templates/{template_id}/baseline")
    def put_process_baseline(
        template_id: int,
        payload: dict = Body(...),
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """예전 방식으로 이 업무 한 건에 걸리던 시간(분) — `{"minutes": 120}`, 지우려면 `{"minutes": null}`."""
        minutes = payload.get("minutes")
        if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int)):
            raise HTTPException(status_code=400, detail="baseline_minutes_must_be_integer")
        try:
            return process_metrics.set_baseline(conn, user["id"], template_id, minutes)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 배운 점·변경 기록(2026-11-27, AI Native 분석 2단계 — `process_learning`). Admin·매니저.
    def _learning_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409 if str(exc) in (
                "change_superseded", "change_already_reverted", "lesson_already_active") else 400,
                detail=str(exc)) from exc

    @app.get("/api/process-metrics/templates/{template_id}/lessons")
    def get_process_lessons(template_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """단계별 개선 제안(사람 반려 사유·되풀이되는 품질 기준 미달)과 적용 중인 배운 점."""
        return process_learning.list_lessons(conn, template_id)

    @app.post("/api/process-metrics/stages/{stage_id}/lessons", status_code=201)
    def post_process_lesson(stage_id: int, payload: dict = Body(...),
                            user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """운영자가 직접 적는 배운 점 — 바로 적용된다. `{"text": "..."}`"""
        return _learning_call(process_learning.add_manual_lesson, conn, user["id"], stage_id, str(payload.get("text") or ""))

    @app.get("/api/process-metrics/templates/{template_id}/examples")
    def get_process_examples(template_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """좋은 결과 예시(2026-11-27, 5단계) — 사람이 승인·한 번에 통과·점검 미달 없음·외부 데이터 없음 후보와 적용 중인 예시."""
        return process_learning.list_examples(conn, template_id)

    @app.post("/api/process-examples/{example_id}/{action}")
    def decide_process_example(example_id: int, action: str,
                               user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """action: use(예시로 쓰기) · dismiss(무시) · retire(끄기)"""
        status = {"use": "active", "dismiss": "dismissed", "retire": "retired"}.get(action)
        if status is None:
            raise HTTPException(status_code=404, detail="unknown_action")
        return _learning_call(process_learning.set_example_status, conn, user["id"], example_id, status)

    @app.post("/api/process-lessons/{lesson_id}/activate")
    def activate_process_lesson(lesson_id: int, payload: dict = Body(default={}),
                                user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """개선 제안 적용(글을 고쳐서 적용하려면 `{"text": "..."}`) — 다음 실행부터 그 단계 지시문에 들어간다."""
        text = payload.get("text") if isinstance(payload, dict) else None
        return _learning_call(process_learning.activate_lesson, conn, user["id"], lesson_id,
                              str(text) if text is not None else None)

    @app.post("/api/process-lessons/{lesson_id}/dismiss")
    def dismiss_process_lesson(lesson_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        return _learning_call(process_learning.set_lesson_status, conn, user["id"], lesson_id, "dismissed")

    @app.post("/api/process-lessons/{lesson_id}/retire")
    def retire_process_lesson(lesson_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        return _learning_call(process_learning.set_lesson_status, conn, user["id"], lesson_id, "retired")

    @app.get("/api/process-metrics/templates/{template_id}/changes")
    def get_process_changes(template_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """변경 기록과 변경마다 전(30일)·후(다음 변경까지, 최대 30일) 지표 비교."""
        return process_learning.list_changes(conn, template_id)

    @app.post("/api/process-changes/{change_id}/revert")
    def revert_process_change(change_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """단계 설정 변경 → 바꾸기 전 값, 배운 점 적용 → 끄기. 그 뒤 다시 바뀐 값이면 409."""
        return _learning_call(process_learning.revert_change, conn, user["id"], change_id)

    # 위험 기반 결재(2026-11-27, AI Native 분석 3단계 — `approval_assist`).
    @app.get("/api/process-metrics/templates/{template_id}/approval-policy")
    def get_approval_policy(template_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """추천 대 사람 결정(위험도별 건수·승인율)·자동 승인 수·낮은 위험 자동 승인을 켤 수 있는지."""
        from hair_platform import approval_assist

        return approval_assist.agreement(conn, template_id)

    @app.put("/api/process-metrics/templates/{template_id}/approval-policy")
    def put_approval_policy(template_id: int, payload: dict = Body(...),
                            user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """낮은 위험 자동 승인 켜기·끄기(admin). 검증 조건을 채우지 못했으면 켜지 않는다(400)."""
        from hair_platform import approval_assist

        enabled = payload.get("auto_approve_low_risk")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="auto_approve_low_risk_must_be_boolean")
        return _learning_call(approval_assist.set_policy, conn, user["id"], template_id, enabled)

    @app.get("/api/capacity")
    def get_capacity(days: int = 7, user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """처리 용량(2026-11-27, 4단계) — 줄별 대기·실행 시간, 바쁜 정도, 지금 대기·실행 중, 파이프라인 시작까지 예상."""
        from hair_platform import capacity

        if days not in (1, 7, 30):
            raise HTTPException(status_code=400, detail="days_must_be_1_7_30")
        return capacity.report(conn, days)

    @app.get("/api/resource-insights")
    def get_resource_insights(user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER))):
        """자원 분석(2026-11-27, Resource Management Phase D) — 역할별 사람 vs AI, 채워지지 않은 수요, 스킬 수요·공급, 팀, 업무 분배."""
        from hair_platform import resource_insights

        return resource_insights.overview(conn)

    @app.get("/api/dashboard")
    def get_dashboard(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Phase 5 — 매니저 대시보드. 팀
        Agent 평균 Gate 점수·대기 승인 건수·비용 추이를 한 번에 담는다.
        비용·감사로그와 같은 이유로 Admin·매니저 전용이다."""
        return dashboard.summary(conn, user["id"], user["role"])

    @app.get("/api/dashboard/workload")
    def get_dashboard_workload(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """2026-10-08, Phase 4 — 사람/
        Agent별 진행 중 Task 수. `GET /api/dashboard`에도 포함돼 있지만
        (요약 화면용), 설계 문서가 별도 엔드포인트로 명시해 그대로 둔다."""
        return dashboard.workload(conn)

    @app.get("/api/dashboard/bottlenecks")
    def get_dashboard_bottlenecks(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """2026-10-08, Phase 4 — "주의 필요" Task 목록(막힌 작업 +
        2회 이상 반려된 작업)."""
        return dashboard.bottlenecks(conn)

    @app.get("/api/dashboard/agent-bottlenecks")
    def get_dashboard_agent_bottlenecks(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 1번)
        — 최근 Gate 평가가 연속으로 실패한 Agent 목록("주의 필요"의
        Agent 버전). `GET /api/dashboard`에도 `agent_bottlenecks`로
        포함돼 있지만(요약 화면용), `bottlenecks`/`workload`가 이미
        각자 독립 엔드포인트를 갖는 것과 같은 이유로 그대로 둔다."""
        return dashboard.agents_with_repeated_gate_failures(conn)

    @app.get("/api/fairness-report")
    def get_fairness_report(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Phase 4 검증 — 배정 공정성 지표. 사람·Agent 반려율 격차를 계산한다."""
        return fairness.report(conn)

    @app.get("/api/org-chart")
    def get_org_chart(user: sqlite3.Row = Depends(get_current_user)):
        """조직도 조회. `confirmed` 배정만 노출한다(조직도·배정이 공유하는 assignments 테이블).

        코드 재점검(2026-09-26)에서 발견: Agent 배정은 `human_pool` LEFT JOIN이
        전부 NULL이라 `manager_id=None` 버킷에 뭉뚱그려져 "사람과 Agent를 하나의
        조직도 위에"라는 핵심 가치와 어긋났다 — Agent는 고용관계상 상사가 없으므로
        그 업무를 실제로 요청·소유한 사람(`assignments.requested_by`)을 관리자로
        귀속시킨다.

        **정정(2026-10-29)**: `team`/`pipeline` 배정도 지금까지 `teams`/
        `pipeline_templates`를 JOIN하지 않아 agent 분기로 떨어져
        "Agent#N (None)"으로 잘못 표시되고 있었다 — 이번에 리소스 타입
        4가지 모두를 올바르게 분기한다. `team`은 팀의 결재 주체
        (`team_leader_id`, 비었으면 요청자)를 관리자로 삼고, `pipeline`은
        소유자 개념이 없어 agent와 같이 요청자를 관리자로 삼는다.
        """
        rows = conn.execute("""
            SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
                   a.requested_by, a.team_leader_id, t.role_name,
                   hp.name AS human_name, hp.manager_id AS human_manager_id,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name,
                   tm.name AS team_name,
                   pt.name AS pipeline_name
            FROM assignments a
            JOIN tasks t ON t.id = a.task_id
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            LEFT JOIN teams tm ON tm.id = a.resource_id AND a.resource_type = 'team'
            LEFT JOIN pipeline_templates pt ON pt.id = a.resource_id AND a.resource_type = 'pipeline'
            WHERE a.status = 'confirmed'
            """).fetchall()

        chart: dict[int | None, dict] = {}
        for row in rows:
            if row["resource_type"] == "human":
                manager_id = row["human_manager_id"]
                resource_name = row["human_name"]
            elif row["resource_type"] == "team":
                manager_id = row["team_leader_id"] or row["requested_by"]
                resource_name = row["team_name"]
            elif row["resource_type"] == "pipeline":
                manager_id = row["requested_by"]
                resource_name = row["pipeline_name"]
            else:
                # Agent는 상사가 없으니 이 배정을 요청/소유한 사람을 관리자로 삼는다.
                manager_id = row["requested_by"]
                resource_name = f"Agent#{row['resource_id']} ({row['agent_role_name']})"
            if manager_id not in chart:
                manager_row = conn.execute(
                    "SELECT username FROM users WHERE id = ?", (manager_id,)
                ).fetchone()
                chart[manager_id] = {
                    "manager_id": manager_id,
                    "manager_username": (
                        manager_row["username"] if manager_row else None
                    ),
                    "reports": [],
                }
            chart[manager_id]["reports"].append(
                {
                    "assignment_id": row["assignment_id"],
                    "task_id": row["task_id"],
                    "role_name": row["role_name"],
                    "resource_type": row["resource_type"],
                    "resource_name": resource_name,
                }
            )
        return list(chart.values())
