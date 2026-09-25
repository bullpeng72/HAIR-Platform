"""HAIR-Platform API — Phase 3 v0–v2.

v0 범위(SPEC.md §9, ADR.md): Auth 최소구현(세션 기반, 결정 6) + RBAC 4종 역할(결정 3)
+ F1(브리프 제출, 규칙기반 스텁) + F12(Human Pool 온보딩) + 최소 승인 워크플로우
(단일 승인자, self-approval 방지 — 결정 3-A).

v1 범위 추가: F2(Review UI — Task 분해 결과 검토, 후보 매칭 미포함 — ADR 결정 1-A) +
F3(Pool 통합 검색) + Assignment Engine 매칭(F14 점수 breakdown 포함, `assignment.py`).

v2 범위 추가: F6(배분 요청 → 승인 후 확정, `assignments`+`assignment_history`
트랜잭션 원자성 — ADR 결정 7) + F5(조직도 조회, confirmed 배정만 노출) + 재배치
(같은 task 재승인 시 이전 confirmed 배정을 superseded로 전환, `assignments.py`).

F4(Agent 생성)·F7(다단계 결재라인)·F13(감사로그)는 v3 이후 범위다.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from hair_platform import assignments, db, pool
from hair_platform.assignment import match_candidates
from hair_platform.auth import Role, authenticate, get_current_user, require_role
from hair_platform.models import (
    ApprovalDecision,
    AssignmentRequest,
    BriefSubmission,
    HumanPoolCreate,
    LoginRequest,
    TaskDecomposition,
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
        """F3 — Human Pool + Agent Pool(스텁) 통합 검색. 전원 조회 가능(RBAC 매트릭스)."""
        candidates = (
            pool.human_pool_candidates(
                conn, role=role, skill=skill, available_only=available_only
            )
            + pool.agent_pool_candidates()
        )
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
        candidates = pool.human_pool_candidates(conn) + pool.agent_pool_candidates()
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

    @app.get("/api/org-chart")
    def get_org_chart(user: sqlite3.Row = Depends(get_current_user)):
        """F5 — 조직도 조회. `confirmed` 배정만 노출한다(F5·F6 공유 assignments 테이블)."""
        rows = conn.execute("""
            SELECT a.id AS assignment_id, a.task_id, a.resource_type,
                   t.role_name, hp.name AS resource_name, hp.manager_id
            FROM assignments a
            JOIN tasks t ON t.id = a.task_id
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            WHERE a.status = 'confirmed'
            """).fetchall()

        chart: dict[int | None, dict] = {}
        for row in rows:
            manager_id = row["manager_id"]
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
                    "resource_name": row["resource_name"],
                }
            )
        return list(chart.values())

    @app.post("/api/approvals", status_code=201)
    def create_approval(
        subject_type: str,
        subject_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
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
        if approval["status"] != "pending":
            # NF3: 이미 처리된 승인을 재처리해 결재라인을 우회하지 못하게 막는다.
            raise HTTPException(status_code=409, detail="이미 처리된 승인입니다")
        if approval["requested_by"] == user["id"]:
            # ADR 결정 3-A: self-approval 방지
            raise HTTPException(
                status_code=403,
                detail="본인이 요청한 건은 승인할 수 없습니다(ADR 결정 3-A)",
            )
        new_status = "approved" if payload.approve else "rejected"
        try:
            conn.execute(
                "UPDATE approvals SET status = ?, approved_by = ? WHERE id = ?",
                (new_status, user["id"], approval_id),
            )
            assignment_result = None
            if approval["subject_type"] == "assignment":
                # ADR 결정 7: approvals·assignments·assignment_history를 한 트랜잭션으로 커밋.
                assignment_result = assignments.resolve_assignment_decision(
                    conn, approval["subject_id"], payload.approve, user["id"]
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "id": approval_id,
            "status": new_status,
            "assignment": assignment_result,
        }

    return app
