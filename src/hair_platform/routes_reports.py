"""운영 리포트 API(대시보드·비용·공정성·성과 리뷰·활동 기록·조직도) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    Depends,
    FastAPI,
)

from hair_platform import (
    cost,
    dashboard,
    fairness,
    performance,
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

    @app.get("/api/cost-report")
    def get_cost_report(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """비용 조회(Admin·매니저 전용, RBAC 매트릭스)."""
        return cost.report(conn)

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
