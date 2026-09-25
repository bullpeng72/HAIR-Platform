"""HAIR-Platform API — Phase 3 v0.

범위(SPEC.md §9 v0, ADR.md): Auth 최소구현(세션 기반, 결정 6) + RBAC 4종 역할(결정 3)
+ F1(브리프 제출, 규칙기반 스텁) + F12(Human Pool 온보딩) + 최소 승인 워크플로우
(단일 승인자, self-approval 방지 — 결정 3-A).

F1의 실제 Spec Interpreter(LLM) 연동, F2(Review UI)·F3(Pool 검색)·F4(Agent 생성)·
F5/F6(조직도·배분)·F7(다단계 결재라인)·F13/F14(감사로그·매칭근거)는 v1 이후 버전 범위다.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from hair_platform import db
from hair_platform.auth import Role, authenticate, get_current_user, require_role
from hair_platform.models import (
    ApprovalDecision,
    BriefSubmission,
    HumanPoolCreate,
    LoginRequest,
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
            "INSERT INTO human_pool (name, role, skill_tags, availability, manager_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                payload.name,
                payload.role,
                json.dumps(payload.skill_tags, ensure_ascii=False),
                payload.availability,
                payload.manager_id,
            ),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": payload.name}

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
        if approval["requested_by"] == user["id"]:
            # ADR 결정 3-A: self-approval 방지
            raise HTTPException(
                status_code=403,
                detail="본인이 요청한 건은 승인할 수 없습니다(ADR 결정 3-A)",
            )
        new_status = "approved" if payload.approve else "rejected"
        conn.execute(
            "UPDATE approvals SET status = ?, approved_by = ? WHERE id = ?",
            (new_status, user["id"], approval_id),
        )
        conn.commit()
        return {"id": approval_id, "status": new_status}

    return app
