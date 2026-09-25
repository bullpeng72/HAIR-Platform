"""HAIR-Platform API — Phase 3 v0–v4 (전체 개발 버전 완료).

v0 범위(SPEC.md §9, ADR.md): Auth 최소구현(세션 기반, 결정 6) + RBAC 4종 역할(결정 3)
+ F1(브리프 제출, 규칙기반 스텁) + F12(Human Pool 온보딩) + 최소 승인 워크플로우
(단일 승인자, self-approval 방지 — 결정 3-A).

v1 범위 추가: F2(Review UI — Task 분해 결과 검토, 후보 매칭 미포함 — ADR 결정 1-A) +
F3(Pool 통합 검색) + Assignment Engine 매칭(F14 점수 breakdown 포함, `assignment.py`).

v2 범위 추가: F6(배분 요청 → 승인 후 확정, `assignments`+`assignment_history`
트랜잭션 원자성 — ADR 결정 7) + F5(조직도 조회, confirmed 배정만 노출) + 재배치
(같은 task 재승인 시 이전 confirmed 배정을 superseded로 전환, `assignments.py`).

v3 범위 추가: F7(다단계 결재라인, 조직도 매니저 체인 자동 라우팅 — `approvals.py`) +
F4(Job Queue+Docker 샌드박스로 Agent 생성, 실패 역할만 재시도 — `jobs.py`·
`sandbox.py`) + F9(Agent 온보딩/오프보딩 — 이때 `pool.agent_pool_candidates()`가
처음으로 실제 데이터를 반환하게 된다) + F13(감사로그, append-only — `audit.py`).

v4(마지막 개발 버전) 범위 추가: F8(성과리뷰 — 사람 데이터는 사전 동의(opt-in) 없이는
절대 노출하지 않는다, NF9 — `performance.py`) + F10(비용 조회, 1차는 수동 입력만
— `cost.py`). 이로써 SPEC.md의 F1–F14가 전부 구현됐다.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from hair_platform import approvals as approvals_mod
from hair_platform import (
    assignments,
    audit,
    cost,
    db,
    fairness,
    jobs,
    performance,
    pool,
)
from hair_platform.assignment import match_candidates
from hair_platform.auth import (
    Role,
    authenticate,
    create_user,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    ApprovalDecision,
    AssignmentRequest,
    BriefSubmission,
    ConsentRequest,
    CostUpdateRequest,
    GenerateRequest,
    HumanPoolCreate,
    LoginRequest,
    ManagerUpdate,
    PerformanceScoreRequest,
    TaskDecomposition,
    UserCreate,
)


def parse_brief_stub(raw_text: str) -> list[str]:
    """F1 규칙기반 스텁 — 줄바꿈 단위로 역할 후보를 나눈다(실제 LLM 파싱은 이후 버전)."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines or [raw_text.strip()]


def create_app(db_path: str = ":memory:") -> FastAPI:
    app = FastAPI(title="HAIR-Platform")
    # ADR 결정 6: 세션 기반 인증(SessionMiddleware). secret_key는 배포 시 환경변수로 교체한다.
    app.add_middleware(SessionMiddleware, secret_key="dev-secret-change-in-production")

    conn = db.get_connection(db_path)
    db.init_schema(conn)
    app.state.db = conn

    @app.post("/login")
    def login(payload: LoginRequest, request: Request):
        user = authenticate(conn, payload.username, payload.password)
        if user is None:
            raise HTTPException(
                status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다"
            )
        request.session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        }
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    @app.post("/api/users", status_code=201)
    def create_user_account(
        payload: UserCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """코드 재점검(2026-09-26)에서 발견한 누락 보완 — `users`·`manager_id`를
        Web UI/API로 설정할 방법이 전무해 F7 결재라인이 항상 flat(단일 단계)으로
        축소되던 갭을 닫는다. ADMIN 전용(사용자 계정 생성은 조직 관리 권한)."""
        try:
            new_id = create_user(
                conn,
                payload.username,
                payload.password,
                Role(payload.role),
                payload.manager_id,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="이미 존재하는 username입니다"
            ) from exc
        audit.record(
            conn,
            user["id"],
            "user_created",
            "user",
            new_id,
            after={
                "username": payload.username,
                "role": payload.role,
                "manager_id": payload.manager_id,
            },
        )
        conn.commit()
        return {"id": new_id, "username": payload.username, "role": payload.role}

    @app.patch("/api/users/{user_id}/manager")
    def set_user_manager(
        user_id: int,
        payload: ManagerUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """F7이 라우팅에 쓰는 `users.manager_id`를 갱신한다(ADMIN 전용)."""
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        if payload.manager_id is not None:
            manager = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.manager_id,)
            ).fetchone()
            if manager is None:
                raise HTTPException(status_code=404, detail="매니저를 찾을 수 없습니다")
            if payload.manager_id == user_id:
                raise HTTPException(
                    status_code=400, detail="본인을 상사로 지정할 수 없습니다"
                )
        conn.execute(
            "UPDATE users SET manager_id = ? WHERE id = ?",
            (payload.manager_id, user_id),
        )
        audit.record(
            conn,
            user["id"],
            "user_manager_updated",
            "user",
            user_id,
            before={"manager_id": target["manager_id"]},
            after={"manager_id": payload.manager_id},
        )
        conn.commit()
        return {"id": user_id, "manager_id": payload.manager_id}

    @app.post("/api/briefs", status_code=201)
    def submit_brief(
        payload: BriefSubmission,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        stub_roles = parse_brief_stub(payload.raw_text)
        cur = conn.execute(
            "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, ?, ?)",
            (user["id"], payload.raw_text, json.dumps(stub_roles, ensure_ascii=False)),
        )
        conn.commit()
        return {"id": cur.lastrowid, "stub_roles": stub_roles, "status": "submitted"}

    @app.post("/api/human-pool", status_code=201)
    def create_human_pool_entry(
        payload: HumanPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        cur = conn.execute(
            "INSERT INTO human_pool "
            "(name, role, skill_tags, availability, cost_estimate, manager_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload.name,
                payload.role,
                json.dumps(payload.skill_tags, ensure_ascii=False),
                payload.availability,
                payload.cost_estimate,
                payload.manager_id,
            ),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": payload.name}

    @app.post("/api/briefs/{brief_id}/tasks", status_code=201)
    def decompose_brief_into_tasks(
        brief_id: int,
        payload: TaskDecomposition,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F2 — Task 분해 결과 등록(후보 매칭은 포함하지 않는다, ADR 결정 1-A)."""
        brief = conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")

        created = []
        for item in payload.tasks:
            cur = conn.execute(
                "INSERT INTO tasks (brief_id, role_name, required_skills, headcount) "
                "VALUES (?, ?, ?, ?)",
                (
                    brief_id,
                    item.role_name,
                    json.dumps(item.required_skills, ensure_ascii=False),
                    item.headcount,
                ),
            )
            created.append({"id": cur.lastrowid, "role_name": item.role_name})
        conn.execute("UPDATE briefs SET status = 'reviewed' WHERE id = ?", (brief_id,))
        conn.commit()
        return {"brief_id": brief_id, "tasks": created}

    @app.get("/api/pool/search")
    def search_pool(
        role: str | None = None,
        skill: str | None = None,
        available_only: bool = False,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """F3 — Human Pool + Agent Pool(스텁) 통합 검색. 전원 조회 가능(RBAC 매트릭스).

        코드 재점검(2026-09-26)에서 발견: role/skill 필터가 human_pool에만 걸리고
        Agent Pool은 무조건 전부 반환되던 버그를 수정 — 양쪽에 동일 필터를 적용한다.
        """
        candidates = pool.human_pool_candidates(
            conn, role=role, skill=skill, available_only=available_only
        ) + pool.agent_pool_candidates(conn, role=role, skill=skill)
        rows = conn.execute("SELECT id, name FROM human_pool").fetchall()
        name_by_id = {row["id"]: row["name"] for row in rows}
        return [
            {
                **c.model_dump(),
                "name": name_by_id.get(c.id) if c.type == "human" else None,
            }
            for c in candidates
        ]

    @app.post("/api/tasks/{task_id}/match")
    def match_task(
        task_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F6 매칭 미리보기 + F14(점수 breakdown) — 배정 요청(assignments)은 v2 범위."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")

        required_skills = json.loads(task["required_skills"])
        candidates = pool.human_pool_candidates(conn) + pool.agent_pool_candidates(conn)
        result = match_candidates(task["role_name"], required_skills, candidates)

        return {
            "task_id": task_id,
            "generate_new": result.generate_new,
            "top": [
                {
                    "candidate": sc.candidate.model_dump(),
                    "score": sc.score,
                    "breakdown": sc.breakdown,
                    "exact_match": sc.exact_match,
                }
                for sc in result.top
            ],
        }

    @app.post("/api/tasks/{task_id}/assignments", status_code=201)
    def request_assignment(
        task_id: int,
        payload: AssignmentRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F6 — 배정 요청. 승인(F7) 전까지는 `requested` 상태로만 남는다(NF3)."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")
        return assignments.create_assignment_request(
            conn, task_id, payload.resource_type, payload.resource_id, user["id"]
        )

    @app.post("/api/briefs/{brief_id}/generate", status_code=201)
    def generate_agents(
        brief_id: int,
        payload: GenerateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F4 — 생성 버튼. Job Queue+샌드박스(ADR 결정 8)로 부족한 역할을 배치 생성한다."""
        brief = conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")
        return jobs.create_and_run_job(conn, brief_id, payload.task_ids, user["id"])

    @app.post("/api/jobs/{job_id}/items/{item_id}/retry")
    def retry_job_item(
        job_id: int,
        item_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """NF6 — 실패 역할만 재시도."""
        try:
            return jobs.retry_failed_item(conn, job_id, item_id, user["id"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agent-pool/{agent_id}/onboard")
    def onboard_agent(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """F9 — Agent 온보딩 승인(Admin 전용). 승인 후 F3 검색에 노출된다."""
        agent = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        conn.execute(
            "UPDATE agent_pool SET status = 'active' WHERE id = ?", (agent_id,)
        )
        audit.record(
            conn,
            user["id"],
            "agent_onboarded",
            "agent_pool",
            agent_id,
            before={"status": agent["status"]},
            after={"status": "active"},
        )
        conn.commit()
        return {"id": agent_id, "status": "active"}

    @app.post("/api/agent-pool/{agent_id}/deprecate")
    def deprecate_agent(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """F9 — Agent 오프보딩(Admin 전용). 이후 F3 검색에서 제외된다."""
        agent = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        conn.execute(
            "UPDATE agent_pool SET status = 'deprecated' WHERE id = ?", (agent_id,)
        )
        audit.record(
            conn,
            user["id"],
            "agent_deprecated",
            "agent_pool",
            agent_id,
            before={"status": agent["status"]},
            after={"status": "deprecated"},
        )
        conn.commit()
        return {"id": agent_id, "status": "deprecated"}

    @app.get("/api/audit-log")
    def get_audit_log(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F13 — 감사로그 조회(Admin·매니저 전용, NF12). append-only는 DB 트리거가 강제."""
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    @app.post("/api/human-pool/{human_id}/consent")
    def set_performance_consent(
        human_id: int,
        payload: ConsentRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """F8 — 사전 동의(opt-in, NF9). 본인 또는 Admin만 설정할 수 있다."""
        human = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if human is None:
            raise HTTPException(
                status_code=404, detail="Human Pool 레코드를 찾을 수 없습니다"
            )
        if human["user_id"] != user["id"] and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="본인 또는 Admin만 동의를 설정할 수 있습니다(NF9)",
            )
        performance.set_human_consent(conn, human_id, payload.consent)
        audit.record(
            conn,
            user["id"],
            "performance_consent_set",
            "human_pool",
            human_id,
            after={"consent": payload.consent},
        )
        conn.commit()
        return {"id": human_id, "performance_consent": payload.consent}

    @app.post("/api/human-pool/{human_id}/performance")
    def set_human_performance_score(
        human_id: int,
        payload: PerformanceScoreRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F8 — 성과 점수 입력. 동의 없이는 거부된다(NF9)."""
        try:
            performance.set_human_performance(conn, human_id, payload.score)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="사람을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="동의 없이는 성과데이터를 수집할 수 없습니다(NF9)",
            ) from exc
        audit.record(
            conn,
            user["id"],
            "human_performance_recorded",
            "human_pool",
            human_id,
            after={"score": payload.score},
        )
        conn.commit()
        return {"id": human_id, "performance_score": payload.score}

    @app.post("/api/agent-pool/{agent_id}/performance")
    def set_agent_performance_score(
        agent_id: int,
        payload: PerformanceScoreRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """F8 — Agent 성과 점수(사람이 아니므로 동의 불필요)."""
        performance.set_agent_performance(conn, agent_id, payload.score)
        audit.record(
            conn,
            user["id"],
            "agent_performance_recorded",
            "agent_pool",
            agent_id,
            after={"score": payload.score},
        )
        conn.commit()
        return {"id": agent_id, "performance_score": payload.score}

    @app.get("/api/performance-review")
    def get_performance_review(
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """F8 — 성과리뷰 조회. Admin·매니저는 전체, 팀원은 본인 관련만(RBAC 매트릭스)."""
        if user["role"] == Role.MEMBER.value:
            return performance.review_for_member(conn, user["id"])
        return performance.full_review(conn)

    @app.post("/api/agent-pool/{agent_id}/cost")
    def set_agent_cost(
        agent_id: int,
        payload: CostUpdateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """F10 — Agent 비용 수동 입력(1차 범위)."""
        cost.set_agent_cost(conn, agent_id, payload.cost_estimate)
        audit.record(
            conn,
            user["id"],
            "agent_cost_updated",
            "agent_pool",
            agent_id,
            after={"cost_estimate": payload.cost_estimate},
        )
        conn.commit()
        return {"id": agent_id, "cost_estimate": payload.cost_estimate}

    @app.get("/api/cost-report")
    def get_cost_report(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F10 — 비용 조회(Admin·매니저 전용, RBAC 매트릭스)."""
        return cost.report(conn)

    @app.get("/api/fairness-report")
    def get_fairness_report(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Phase 4 검증 — 배정 공정성 지표(NF4). 사람·Agent 반려율 격차를 계산한다."""
        return fairness.report(conn)

    @app.get("/api/org-chart")
    def get_org_chart(user: sqlite3.Row = Depends(get_current_user)):
        """F5 — 조직도 조회. `confirmed` 배정만 노출한다(F5·F6 공유 assignments 테이블).

        코드 재점검(2026-09-26)에서 발견: Agent 배정은 `human_pool` LEFT JOIN이
        전부 NULL이라 `manager_id=None` 버킷에 뭉뚱그려져 "사람과 Agent를 하나의
        조직도 위에"라는 핵심 가치와 어긋났다 — Agent는 고용관계상 상사가 없으므로
        그 업무를 실제로 요청·소유한 사람(`assignments.requested_by`)을 관리자로
        귀속시킨다.
        """
        rows = conn.execute("""
            SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
                   a.requested_by, t.role_name,
                   hp.name AS human_name, hp.manager_id AS human_manager_id,
                   ap.role_name AS agent_role_name
            FROM assignments a
            JOIN tasks t ON t.id = a.task_id
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            WHERE a.status = 'confirmed'
            """).fetchall()

        chart: dict[int | None, dict] = {}
        for row in rows:
            if row["resource_type"] == "human":
                manager_id = row["human_manager_id"]
                resource_name = row["human_name"]
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

    @app.post("/api/approvals", status_code=201)
    def create_approval(
        subject_type: str,
        subject_id: int,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """F1(브리프) 최소 단일승인 워크플로우 전용(ADR.md v0 범위, ADR 결정 3-A).

        코드 재점검(2026-09-26)에서 발견한 심각한 우회 경로 수정: 이전엔 role
        제한이 전혀 없고(get_current_user만) `subject_type`도 검증하지 않아,
        VIEWER를 포함한 아무 로그인 사용자가 `subject_type=assignment`로 진행 중인
        F7 다단계 결재라인과 무관한 rogue 승인 레코드(step=1, approver_id=NULL)를
        만들 수 있었다 — `decide_approval`의 "지정된 승인자만" 체크는
        approver_id가 NULL일 때 통과되므로, 체인 밖의 아무 Admin/Manager가 이
        rogue 레코드를 승인하면 `resolve_assignment_decision`이 subject_id만 보고
        배정을 조기 확정/반려시킬 수 있었다. 이 엔드포인트는 설계상 F1(브리프)
        단일승인 전용이었으므로 subject_type을 그 범위로 고정해 우회로를 닫는다.
        `assignment` 승인은 `POST /api/tasks/{task_id}/assignments`(F6)가 만드는
        정식 체인으로만 생성되게 한다.
        """
        if subject_type != "brief":
            raise HTTPException(
                status_code=400,
                detail="이 엔드포인트는 brief 승인 전용입니다(assignment는 F6 배정 "
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
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        approval = conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if approval is None:
            raise HTTPException(status_code=404, detail="승인 요청을 찾을 수 없습니다")
        if approval["status"] == "waiting":
            # F7 다단계 결재라인: 이전 단계가 아직 안 끝났다(ADR 결정 3).
            raise HTTPException(status_code=409, detail="이전 단계 승인이 필요합니다")
        if approval["status"] != "pending":
            # NF3: 이미 처리된 승인을 재처리해 결재라인을 우회하지 못하게 막는다.
            raise HTTPException(status_code=409, detail="이미 처리된 승인입니다")
        if approval["requested_by"] == user["id"]:
            # ADR 결정 3-A: self-approval 방지
            raise HTTPException(
                status_code=403,
                detail="본인이 요청한 건은 승인할 수 없습니다(ADR 결정 3-A)",
            )
        if (
            approval["approver_id"] is not None
            and approval["approver_id"] != user["id"]
            and user["role"] != Role.ADMIN.value
        ):
            raise HTTPException(
                status_code=403, detail="지정된 승인자만 이 단계를 승인할 수 있습니다"
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
            next_step_pending = False
            if payload.approve:
                next_step_pending = approvals_mod.activate_next_step(
                    conn, subject_type, subject_id, approval["step"]
                )
                if not next_step_pending and subject_type == "assignment":
                    # ADR 결정 7: 체인의 마지막 단계 통과 시에만 최종 확정한다.
                    assignment_result = assignments.resolve_assignment_decision(
                        conn, subject_id, True, user["id"]
                    )
            else:
                approvals_mod.skip_remaining_steps(
                    conn, subject_type, subject_id, approval["step"]
                )
                if subject_type == "assignment":
                    assignment_result = assignments.resolve_assignment_decision(
                        conn, subject_id, False, user["id"]
                    )
            audit.record(
                conn,
                user["id"],
                f"approval_{new_status}",
                "approval",
                approval_id,
                before={"step": approval["step"], "subject_type": subject_type},
                after={"next_step_pending": next_step_pending},
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "id": approval_id,
            "status": new_status,
            "next_step_pending": next_step_pending,
            "assignment": assignment_result,
        }

    return app
