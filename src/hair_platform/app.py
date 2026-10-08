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
import os
import sqlite3
from functools import partial

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from hair_platform import (
    agent_evaluation,
    agent_runtime,
    assignments,
    audit,
    cost,
    crew_runtime,
    dashboard,
    data_admin,
    db,
    deliverables,
    fairness,
    inbox,
    jobs,
    model_registry,
    performance,
    pool,
    pool_registry,
    retention,
    teams,
    webui,
)
from hair_platform import approvals as approvals_mod
from hair_platform.assignment import match_candidates
from hair_platform.auth import (
    Role,
    authenticate,
    create_user,
    get_current_user,
    hash_password,
    require_role,
)
from hair_platform.models import (
    AgentExecuteRequest,
    AgentModelConfig,
    ApprovalDecision,
    AssignmentRequest,
    AssistRequest,
    BriefSubmission,
    ConsentRequest,
    CostUpdateRequest,
    CrewRunRequest,
    DeliverableCommentCreate,
    DeliverableCreate,
    ExecuteAgentRequest,
    GenerateRequest,
    HumanPoolCreate,
    HumanPoolUpdate,
    KnowledgeIndexRequest,
    LoginRequest,
    ManagerUpdate,
    ModelActiveUpdate,
    ModelRegistryCreate,
    PerformanceScoreRequest,
    SkillPoolCreate,
    TaskDecomposition,
    TaskNotesUpdate,
    TeamActiveUpdate,
    TeamCreate,
    TeamCrewExecuteRequest,
    TeamLeaderUpdate,
    TeamMemberAdd,
    ToolActiveUpdate,
    ToolPoolCreate,
    UserCreate,
    UserPasswordReset,
    UserStatusUpdate,
)


def parse_brief_stub(raw_text: str) -> list[str]:
    """F1 규칙기반 스텁 — 줄바꿈 단위로 역할 후보를 나눈다(실제 LLM 파싱은 이후 버전)."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines or [raw_text.strip()]


def create_app(db_path: str = ":memory:") -> FastAPI:
    # 사용자 요청(2026-09-26) — 프로젝트 루트 `.env`(gitignore됨)에서
    # ANTHROPIC_API_KEY/OPENAI_API_KEY/HAIR_PLATFORM_SECRET_KEY 등을 읽는다.
    # `.env`가 없으면 조용히 아무 일도 하지 않는다(기존 동작과 동일).
    # 이미 설정된 실제 환경변수를 덮어쓰지 않는다(override=False, 기본값).
    load_dotenv()

    app = FastAPI(title="HAIR-Platform")
    # ADR 결정 6: 세션 기반 인증(SessionMiddleware).
    # 코드 재점검(2026-09-26)에서 발견: "배포 시 환경변수로 교체한다"는 주석만 있고
    # 실제로 환경변수를 읽는 코드가 없어 항상 소스에 공개된 고정 키를 쓰던 오류를
    # 수정 — HAIR_PLATFORM_SECRET_KEY가 없으면 개발용 기본값으로 폴백한다.
    secret_key = os.environ.get(
        "HAIR_PLATFORM_SECRET_KEY", "dev-secret-change-in-production"
    )
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    conn = db.get_connection(db_path)
    db.init_schema(conn)
    # 실측으로 발견(2026-09-28, 리뷰) — 이전 프로세스가 실행 도중
    # 죽으면 agent_execution_jobs가 영원히 'running'으로 남아 해당
    # task의 실행 버튼이 복구 불가능하게 막힌다. 단일 프로세스 전제
    # (docs/LIMITS.md §3)이므로 기동 시점의 'running'은 전부 이전
    # 프로세스의 잔재로 확정하고 정리한다.
    deliverables.reap_orphaned_execution_jobs(conn)
    # 사용자 결정(2026-09-29, "assignment_history/audit_log 보관기간 1년") —
    # 오래 켜진 배포에서 기동 때마다 자연스럽게 정리되도록 여기서도 실행한다
    # (retention.py 참고, 지울 게 없으면 DB에 손도 대지 않는다).
    retention.purge_expired(conn)
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

    @app.post("/logout")
    def logout(request: Request):
        """Web UI 세션 종료 — 로그인은 있었는데 로그아웃이 없던 것을 보완."""
        request.session.clear()
        return {"status": "logged_out"}

    @app.get("/api/me")
    def get_me(user: sqlite3.Row = Depends(get_current_user)):
        """Web UI 셸이 현재 로그인 사용자·role을 알아야 RBAC에 맞게 화면을 그린다."""
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

    @app.patch("/api/users/{user_id}/password")
    def reset_user_password(
        user_id: int,
        payload: UserPasswordReset,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-29, "비밀번호 조회/초기화 기능 추가 필요") —
        조회는 bcrypt 해시라 원리상 불가능하고, 초기화만 제공한다(ADMIN
        전용). 감사로그엔 비밀번호 값 자체를 남기지 않는다."""
        target = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        if not payload.password:
            raise HTTPException(status_code=400, detail="비밀번호를 입력해야 합니다")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
        audit.record(
            conn, user["id"], "user_password_reset", "user", user_id
        )
        conn.commit()
        return {"id": user_id, "status": "password_reset"}

    @app.patch("/api/users/{user_id}/status")
    def set_user_status(
        user_id: int,
        payload: UserStatusUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-29, "사용자 삭제 기능 필요") — `users.id`는
        audit_log/assignments/approvals/human_pool.user_id에서 NOT NULL로
        참조돼 하드 삭제가 FK 위반으로 불가능하다(NF10 감사 이력 보존과
        정면 충돌). Human/Agent Pool의 오프보딩과 같은 "비활성화" 방식을
        쓴다 — 비활성화된 계정은 로그인이 막히고(`auth.authenticate()`/
        `get_current_user()`), 이력은 그대로 남는다."""
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        if user_id == user["id"]:
            raise HTTPException(
                status_code=400, detail="본인 계정은 비활성화할 수 없습니다"
            )
        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (int(payload.is_active), user_id),
        )
        audit.record(
            conn,
            user["id"],
            "user_status_updated",
            "user",
            user_id,
            before={"is_active": bool(target["is_active"])},
            after={"is_active": payload.is_active},
        )
        conn.commit()
        return {"id": user_id, "is_active": payload.is_active}

    @app.get("/api/users")
    def list_users(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """코드 재점검(2026-09-26)에서 발견한 누락 보완 — PATCH .../manager를
        쓰려면 상사로 지정할 user_id를 조회할 방법이 있어야 하는데 없었다."""
        rows = conn.execute(
            "SELECT id, username, role, manager_id, is_active FROM users ORDER BY id"
        ).fetchall()
        return [{**dict(row), "is_active": bool(row["is_active"])} for row in rows]

    @app.get("/api/users/names")
    def list_user_names(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 보고(2026-10-06, "사람 인력으로 등록되어 있지만 사용자
        관리에 등록되지 않은 경우 로그인도 안 돼 일을 할 수 없다")로 추가
        — "사람 인력 등록" 화면(ADMIN/MANAGER 둘 다 접근 가능)이 "이미
        만들어진 계정 중에서 골라 연결"하려면 계정 목록을 읽을 수 있어야
        하는데, `GET /api/users`는 ADMIN 전용이라 MANAGER는 못 쓴다.
        `/api/skill-pool/names`와 같은 이유로 — 관리 목적의 전체 레코드
        (manager_id·is_active 등)가 아니라 연결 용도로 꼭 필요한 최소
        정보(id/username/role)만, 그 화면과 같은 권한 범위로 노출한다.
        비활성 계정은 연결 대상에서 의미가 없어 제외한다."""
        rows = conn.execute(
            "SELECT id, username, role FROM users WHERE is_active = 1 ORDER BY username"
        ).fetchall()
        return [dict(row) for row in rows]

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

    @app.get("/api/briefs")
    def list_briefs(user: sqlite3.Row = Depends(get_current_user)):
        """Web UI가 브리프 목록을 브라우징할 수 있어야 F2(Task 분해) 화면으로
        이어갈 수 있다 — 지금까지는 생성 응답의 id를 기억해야만 했다."""
        rows = conn.execute("SELECT * FROM briefs ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                "raw_text": row["raw_text"],
                "stub_roles": json.loads(row["stub_roles"]),
                "status": row["status"],
                "submitted_by": row["submitted_by"],
            }
            for row in rows
        ]

    @app.delete("/api/briefs/{brief_id}")
    def delete_brief_endpoint(
        brief_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28, "각 메뉴에서 데이터 삭제나, 초기화 기능
        추가") — brief와 그 아래 tasks·jobs·assignments를 전부 지운다.
        `confirmed` 배정이 있으면 막는다(먼저 F6/F7에서 정리)."""
        try:
            data_admin.delete_brief(conn, user["id"], brief_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="업무 요청을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        return {"id": brief_id, "deleted": True}

    @app.post("/api/human-pool", status_code=201)
    def create_human_pool_entry(
        payload: HumanPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        if payload.user_id is not None:
            target_user = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.user_id,)
            ).fetchone()
            if target_user is None:
                raise HTTPException(
                    status_code=404, detail="연결할 사용자를 찾을 수 없습니다"
                )
            already_linked = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (payload.user_id,)
            ).fetchone()
            if already_linked is not None:
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 사람 인력(id="
                    f"{already_linked['id']})에 연결된 계정입니다",
                )
        cur = conn.execute(
            "INSERT INTO human_pool "
            "(name, role, skill_tags, availability, cost_estimate, manager_id, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload.name,
                payload.role,
                json.dumps(payload.skill_tags, ensure_ascii=False),
                payload.availability,
                payload.cost_estimate,
                payload.manager_id,
                payload.user_id,
            ),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": payload.name}

    @app.get("/api/human-pool")
    def list_human_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Web UI가 Human Pool 관리 화면(수정/오프보딩/동의)에서 원본 레코드가
        필요하다 — `/api/pool/search`는 공통 ResourceCandidate 어댑터라 availability
        원문·manager_id·performance_consent가 빠져 있어 이 목적에 못 쓴다."""
        rows = conn.execute("SELECT * FROM human_pool ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "skill_tags": json.loads(row["skill_tags"]),
                "availability": row["availability"],
                "cost_estimate": row["cost_estimate"],
                "manager_id": row["manager_id"],
                "user_id": row["user_id"],
                "performance_consent": bool(row["performance_consent"]),
                "performance_score": row["performance_score"],
            }
            for row in rows
        ]

    @app.patch("/api/human-pool/{human_id}")
    def update_human_pool_entry(
        human_id: int,
        payload: HumanPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """코드 재점검(R3)에서 발견한 비대칭 보완 — Agent Pool엔 onboard/deprecate가
        있는데 Human Pool은 생성 후 수정할 방법이 전혀 없었다."""
        row = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="사람을 찾을 수 없습니다")

        # 사용자 결정(2026-09-29, `docs/ROLE_TASK_CLASSIFICATION.md` 갭 2,
        # "(b) Human Pool도 admin 전용으로 좁히기") — Agent Pool의
        # cost_estimate/performance_score는 이미 admin 전용 전용
        # 엔드포인트로만 수정 가능한데(§2-1), Human Pool은 이 일반
        # PATCH로 매니저도 자유롭게 고칠 수 있어 비대칭이었다. 다른
        # 필드(역할·스킬·가용성·상사 지정 등)는 매니저의 정상 인사
        # 업무라 그대로 두고, cost_estimate만 admin 전용으로 좁힌다.
        if payload.cost_estimate is not None and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="Human Pool 비용(cost_estimate) 수정은 admin 전용입니다"
                "(docs/ROLE_TASK_CLASSIFICATION.md 갭 2)",
            )

        updates: dict[str, object] = {}
        if payload.role is not None:
            updates["role"] = payload.role
        if payload.skill_tags is not None:
            updates["skill_tags"] = json.dumps(payload.skill_tags, ensure_ascii=False)
        if payload.availability is not None:
            updates["availability"] = payload.availability
        if payload.cost_estimate is not None:
            updates["cost_estimate"] = payload.cost_estimate
        if payload.user_id is not None:
            target_user = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.user_id,)
            ).fetchone()
            if target_user is None:
                raise HTTPException(
                    status_code=404, detail="연결할 사용자를 찾을 수 없습니다"
                )
            # 사용자 보고(2026-10-06)로 추가 — 두 Human Pool 레코드가 같은
            # 계정에 연결되면 `WHERE user_id = ?` 기반 조회(`/api/my-work`
            # 등)가 둘 중 하나만 조용히 골라 다른 쪽은 영원히 안 보이게
            # 된다. 자기 자신에게 다시 연결하는 건 허용(멱등).
            already_linked = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ? AND id != ?",
                (payload.user_id, human_id),
            ).fetchone()
            if already_linked is not None:
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 사람 인력(id="
                    f"{already_linked['id']})에 연결된 계정입니다",
                )
            updates["user_id"] = payload.user_id
        if payload.manager_id is not None:
            manager = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.manager_id,)
            ).fetchone()
            if manager is None:
                raise HTTPException(
                    status_code=404, detail="매니저를 찾을 수 없습니다"
                )
            updates["manager_id"] = payload.manager_id
        if not updates:
            raise HTTPException(status_code=400, detail="수정할 필드가 없습니다")

        set_clause = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE human_pool SET {set_clause} WHERE id = ?",
            (*updates.values(), human_id),
        )
        audit.record(
            conn,
            user["id"],
            "human_pool_updated",
            "human_pool",
            human_id,
            before={k: row[k] for k in updates},
            after=updates,
        )
        conn.commit()
        return {"id": human_id, **updates}

    @app.post("/api/human-pool/{human_id}/offboard")
    def offboard_human(
        human_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """코드 재점검(R3)에서 발견한 누락 보완 — 퇴사한 사람을 검색/매칭에서
        완전히 배제할 방법이 없었다(Agent의 `/deprecate`에 대응)."""
        row = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="사람을 찾을 수 없습니다")
        conn.execute(
            "UPDATE human_pool SET availability = 'offboarded' WHERE id = ?",
            (human_id,),
        )
        audit.record(
            conn,
            user["id"],
            "human_offboarded",
            "human_pool",
            human_id,
            before={"availability": row["availability"]},
            after={"availability": "offboarded"},
        )
        conn.commit()
        return {"id": human_id, "availability": "offboarded"}

    @app.delete("/api/human-pool/{human_id}")
    def delete_human_endpoint(
        human_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28) — 사람 인력 레코드를 완전히 지운다
        (오프보딩은 상태만 바꿀 뿐 지우지는 않는다 — 이건 그 위의 진짜
        삭제). `confirmed` 배정이 있으면 막는다."""
        try:
            data_admin.delete_human(conn, user["id"], human_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="사람을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        return {"id": human_id, "deleted": True}

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

    @app.get("/api/briefs/{brief_id}/tasks")
    def list_tasks_for_brief(
        brief_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Web UI가 F6(매칭/배분) 화면에서 브리프별 Task 목록을 골라야 한다 —
        지금까지는 분해 응답의 id를 기억해야만 했다.

        사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
        분석하고 부족한 기능을 개선해줘")에 따라 `deliverables.
        task_completion_summary()`(`내 업무` 목록과 공용)를 붙여, 이
        화면만 보고도 어떤 할 일이 최종 결과물(승인됨)까지 나왔는지
        알 수 있게 했다 — 이전엔 할 일마다 워크스페이스를 직접 열어야
        했다."""
        rows = conn.execute(
            "SELECT * FROM tasks WHERE brief_id = ? ORDER BY id", (brief_id,)
        ).fetchall()
        return [
            {
                "id": row["id"],
                "role_name": row["role_name"],
                "required_skills": json.loads(row["required_skills"]),
                "headcount": row["headcount"],
                "notes": row["notes"],
                **deliverables.task_completion_summary(conn, row["id"]),
            }
            for row in rows
        ]

    @app.delete("/api/briefs/{brief_id}/tasks/{task_id}")
    def delete_task_endpoint(
        brief_id: int,
        task_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — 할 일(task) 하나를 그 아래
        assignments·job_items까지 지운다. `confirmed` 배정이 있으면
        막는다. `brief_id`는 URL 일관성(중첩 리소스)을 위해 받지만 실제
        조회·삭제는 `task_id`만으로 한다."""
        del brief_id
        try:
            data_admin.delete_task(conn, user["id"], task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        return {"id": task_id, "deleted": True}

    @app.get("/api/pool/search")
    def search_pool(
        role: str | None = None,
        skill: str | None = None,
        availability: str | None = None,
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """F3 — Human Pool + Agent Pool(스텁) 통합 검색. 전원 조회 가능(RBAC 매트릭스).

        코드 재점검(2026-09-26)에서 발견: role/skill 필터가 human_pool에만 걸리고
        Agent Pool은 무조건 전부 반환되던 버그를 수정 — 양쪽에 동일 필터를 적용한다.

        Phase 5(`docs/HAIR_MASTER_PLAN.md` §6 "검색/필터 스케일") — `available_only`
        (boolean)를 `availability`(가능/불가 둘 다 명시적으로 찾을 수 있게)로
        넓히고, 감사로그(§13)와 같은 `limit`/`offset`+`total` 페이지네이션
        규약을 적용했다. Human+Agent를 하나로 합친 뒤 자르는 방식이라(두
        풀을 SQL에서 각각 페이지네이션하면 "한 페이지"가 한쪽 풀로만 채워질
        수 있어 부정확해진다) 큰 풀에서는 비효율적일 수 있다 — 이 저장소가
        전제하는 소규모 팀 규모에서는 허용 가능한 트레이드오프다.

        "부서" 필터(§6 원안)는 넣지 않았다 — 이 스키마엔 애초에 "부서"라는
        필드가 없다(조직 구조는 `manager_id` 상사 체인/조직도로만 표현된다).
        존재하지 않는 데이터를 필터로 꾸며내지 않고, 이미 있는 `role`이
        사실상 같은 역할(직군 단위 검색)을 한다는 점을 그대로 문서화한다.

        **실측으로 발견한 버그(리뷰, "Phase1~5까지... 분석하고 개선해줘")**:
        `availability` 필터가 `human_pool_candidates()` 안에만 있어서,
        `availability='unavailable'`로 검색해도 Agent(항상 `is_available
        =True`로 고정)는 이 필터를 아예 안 받아 그대로 결과에 섞여
        나왔다 — "불가능한 후보만" 찾는 필터가 사실상 안 지켜졌다.
        Agent는 애초에 "불가" 상태가 없다는 걸 반영해, `availability=
        'unavailable'`일 땐 Agent 후보 자체를 통째로 뺀다.
        """
        limit = max(1, min(limit, 200))
        agent_candidates = (
            []
            if availability == "unavailable"
            else pool.agent_pool_candidates(conn, role=role, skill=skill)
        )
        candidates = (
            pool.human_pool_candidates(
                conn, role=role, skill=skill, availability=availability
            )
            + agent_candidates
        )
        rows = conn.execute("SELECT id, name FROM human_pool").fetchall()
        name_by_id = {row["id"]: row["name"] for row in rows}
        # 사용자 보고(2026-10-06)로 `ResourceCandidate.user_id`가 추가됐다
        # — 이 검색은 viewer까지 전원 조회 가능(RBAC 매트릭스)이라, 계정
        # 미연결 경고는 그보다 좁은 매칭/배정 화면(admin/manager/member)
        # 응답에만 노출하고 여긴 그대로 뺀다(불필요한 노출 확대 방지).
        items = [
            {
                **c.model_dump(exclude={"user_id"}),
                "name": name_by_id.get(c.id) if c.type == "human" else None,
            }
            for c in candidates
        ]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/tasks/{task_id}/match")
    def match_task(
        task_id: int,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """F6 매칭 미리보기 + F14(점수 breakdown) — 배정 요청(assignments)은 v2 범위.

        사용자 결정(2026-09-29, "F14 매칭 점수 breakdown을 배정 후보
        본인에게 공개") — MEMBER도 호출할 수 있게 열되, 다른 후보의
        breakdown까지 보이면 프라이버시 문제라 자기 자신(human_pool.
        user_id로 연결된 항목)에 해당하는 한 건만 필터링해 돌려준다."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")

        required_skills = json.loads(task["required_skills"])
        candidates = pool.human_pool_candidates(conn) + pool.agent_pool_candidates(conn)
        result = match_candidates(task["role_name"], required_skills, candidates)

        top = [
            {
                "candidate": sc.candidate.model_dump(),
                "score": sc.score,
                "breakdown": sc.breakdown,
                "exact_match": sc.exact_match,
            }
            for sc in result.top
        ]
        if user["role"] == Role.MEMBER.value:
            my_human = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (user["id"],)
            ).fetchone()
            my_human_id = my_human["id"] if my_human else None
            top = [
                t
                for t in top
                if t["candidate"]["type"] == "human"
                and t["candidate"]["id"] == my_human_id
            ]

        return {
            "task_id": task_id,
            "generate_new": result.generate_new,
            "top": top,
        }

    @app.post("/api/tasks/{task_id}/assignments", status_code=201)
    def request_assignment(
        task_id: int,
        payload: AssignmentRequest,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """F6 — 배정 요청. 승인(F7) 전까지는 `requested` 상태로만 남는다(NF3).

        사용자 결정(2026-09-29, "팀원 셀프서비스(본인이 직접 배정 요청)를
        허용") — MEMBER는 본인의 human_pool 레코드로만 요청할 수 있다
        (다른 사람·Agent를 대신 요청하는 건 여전히 admin/manager 전용).
        승인 게이트(F7)와 self-approval 방지(ADR 3-A)는 그대로 적용되므로
        셀프서비스는 "요청 권한"만 넓힐 뿐 확정 권한은 늘리지 않는다."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")
        if user["role"] == Role.MEMBER.value:
            my_human = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (user["id"],)
            ).fetchone()
            if (
                payload.resource_type != "human"
                or my_human is None
                or payload.resource_id != my_human["id"]
            ):
                raise HTTPException(
                    status_code=403, detail="본인 배정 요청만 가능합니다"
                )
        try:
            return assignments.create_assignment_request(
                conn,
                task_id,
                payload.resource_type,
                payload.resource_id,
                user["id"],
                team_leader_id=payload.team_leader_id,
                replaces_assignment_id=payload.replaces_assignment_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/assignments")
    def list_task_assignments(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의
        화면으로 합치는 방안" → 옵션 C 채택) — "업무 요청" 탭에서 할 일
        하나의 배정 현황(요청됨/확정됨/반려됨/대체됨)을 바로 보여주려면
        이 할 일에 걸린 `assignments` 전체를 조회할 방법이 있어야 하는데
        지금까지 없었다(`/api/org-chart`는 confirmed만, 그마저 task
        단위 조회가 아니다). 조직도(`GET /api/org-chart`)가 이미 전원
        공개하는 것과 같은 수준(이름+상태, 비용·성과점수는 제외)이라
        `get_current_user`로 연다 — status 제한 없이 이력 전체를
        최신순으로 돌려준다(화면에서 현재 상태/과거 이력을 구분해
        보여줄 수 있도록)."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        rows = conn.execute(
            """
            SELECT a.id, a.resource_type, a.resource_id, a.status,
                   a.team_leader_id,
                   hp.name AS human_name, ap.role_name AS agent_role_name,
                   t.name AS team_name
            FROM assignments a
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            LEFT JOIN teams t ON t.id = a.resource_id AND a.resource_type = 'team'
            WHERE a.task_id = ?
            ORDER BY a.id DESC
            """,
            (task_id,),
        ).fetchall()
        resource_names = {
            "human": lambda row: row["human_name"],
            "agent": lambda row: row["agent_role_name"],
            "team": lambda row: row["team_name"],
        }
        return [
            {
                "id": row["id"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "resource_name": resource_names[row["resource_type"]](row),
                "status": row["status"],
                "team_leader_id": row["team_leader_id"],
            }
            for row in rows
        ]

    # ---------- 협업 워크스페이스(사용자 요청, 2026-09-28) ----------
    # `docs/PHASE1_DELIVERABLE_SPEC.md` 참고. RBAC 원칙: 관리자/매니저는
    # 감독 목적으로 조회는 전부 가능하지만, 작성(deliverable/댓글)은 그
    # task의 사람 당사자/Agent 요청자 본인만 — 대리 작성 금지.

    @app.get("/api/my-work")
    def get_my_work(
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """§2-1 — 확정된 배정 중 내가 당사자이거나(사람) 요청자인(Agent) task."""
        return deliverables.list_my_work(conn, user["id"], limit, offset)

    @app.get("/api/inbox")
    def get_inbox_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        """Phase 5 — 통합 인박스(`docs/HAIR_MASTER_PLAN.md` §6). 결재
        대기(Admin/매니저)·Agent 실행 실패·낮은 Gate 점수 경고를 한
        목록으로 합쳐 반환한다. 새 권한 규칙을 추가하지 않는다 — 각
        항목은 기존 조회 함수(`approvals.list_inbox`/`deliverables.
        list_my_work`)가 이미 강제하는 접근 범위를 그대로 물려받는다."""
        return inbox.get_inbox(conn, user["id"], user["role"])

    @app.get("/api/tasks/{task_id}")
    def get_task_endpoint(task_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """ASANA_PARITY_DESIGN_PLAN.md F17/F19(2026-10-08) — Task 단건 조회.
        지금까지 Task 단건 조회 엔드포인트가 없어 `notes`(F17) 등 새 필드를
        확인하려면 `GET /api/briefs/{brief_id}/tasks` 전체를 받아 걸러내야
        했다."""
        detail = deliverables.get_task_detail(conn, task_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return detail

    @app.patch("/api/tasks/{task_id}/notes")
    def update_task_notes_endpoint(
        task_id: int,
        payload: TaskNotesUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """F17 — Task 메모. 참가자 본인만 수정(결재 불필요한 가벼운
        메모라 대리 작성 제한만 `create_comment()`와 동일하게 건다)."""
        try:
            return deliverables.update_task_notes(
                conn, task_id, user["id"], payload.notes
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None

    @app.get("/api/tasks/{task_id}/activity")
    def get_task_activity_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """F19 — 배정·결과물·댓글·Agent 실행·결재·Task 변경을 합친 통합
        타임라인."""
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            return deliverables.task_activity(conn, task_id)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.get("/api/tasks/{task_id}/deliverables")
    def list_task_deliverables(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """§2-2 — 협업 워크스페이스 타임라인(시간순)."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_deliverables(conn, task_id)

    @app.post("/api/tasks/{task_id}/deliverables", status_code=201)
    def create_task_deliverable(
        task_id: int,
        payload: DeliverableCreate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """§2-3 — 사람 결과물 작성. 대리 작성 금지(참가자 본인만)."""
        try:
            deliverable_id = deliverables.create_deliverable(
                conn, task_id, user["id"], payload.content, payload.content_type
            )
        except ValueError as exc:
            if str(exc) == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=400, detail="내용을 입력해야 합니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        return {"id": deliverable_id}

    @app.post("/api/deliverables/{deliverable_id}/submit")
    def submit_task_deliverable(
        deliverable_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """§2-3 후속(라이브 시나리오 실행에서 발견한 갭 보완, `docs/LIMITS.md`
        §33-34) — 사람이 작성한 결과물을 명시적으로 검토 대상으로 제출한다.
        Agent 산출물과 같은 결재 체인을 재사용한다."""
        try:
            result = deliverables.submit_deliverable(conn, deliverable_id, user["id"])
        except ValueError as exc:
            if str(exc) == "deliverable_not_found":
                raise HTTPException(
                    status_code=404, detail="결과물을 찾을 수 없습니다"
                ) from None
            if str(exc) == "not_human_deliverable":
                raise HTTPException(
                    status_code=400, detail="Agent 산출물은 생성 즉시 자동으로 제출됩니다"
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
        """§2-4."""
        d = conn.execute(
            "SELECT task_id FROM deliverables WHERE id = ?", (deliverable_id,)
        ).fetchone()
        if d is None:
            raise HTTPException(status_code=404, detail="결과물을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, d["task_id"], user["id"])
            is None
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
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """§2-4."""
        try:
            comment_id = deliverables.create_comment(
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
        return {"id": comment_id}

    @app.post("/api/tasks/{task_id}/assist")
    def assist_task(
        task_id: int,
        payload: AssistRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """§2-5 — "LLM으로 도와줘". 실제 LLM API를 호출하므로 비용이
        발생한다. 결과는 저장하지 않는다 — 검토 후 별도로 제출해야 한다.
        팀원/뷰어는 provider/model을 선택할 수 없다(§3 RBAC 결정, 비용
        통제)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider or payload.model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )
        try:
            draft = deliverables.assist(
                conn,
                task_id,
                user["id"],
                payload.draft_context,
                payload.provider,
                payload.model,
                payload.content_type,
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        return {"draft": draft}

    @app.post("/api/tasks/{task_id}/execute-agent", status_code=202)
    def trigger_agent_execution(
        task_id: int,
        payload: ExecuteAgentRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """§2-6(Phase 4로 비동기화) — task에 배정된 Agent 실행을 큐에
        올린다. 검증만 동기로 마치고 202로 즉시 응답하며, 실제 LLM 호출
        (비용 발생)은 응답 전송 후 백그라운드에서 진행된다 — Web UI는
        `GET .../agent-execution-jobs`로 폴링해 완료를 확인한다. 요청자
        본인 또는 매니저/관리자(감독 대리 실행 허용 — §2-3의 "작성"과
        달리 "지금 실행시켜라"는 대리해도 저자성이 안 흐려짐)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider or payload.model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )
        try:
            job = deliverables.start_agent_execution(
                conn,
                task_id,
                user["id"],
                allow_supervisor_override=user["role"] in ("admin", "manager"),
                agent_id=payload.agent_id,
                content_type=payload.content_type,
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except LookupError as exc:
            if str(exc) == "agent_already_running":
                raise HTTPException(
                    status_code=409,
                    detail="이미 실행 중인 작업이 있습니다 — 완료를 기다려 주세요",
                ) from None
            raise HTTPException(
                status_code=409,
                detail="이 업무에 확정된 AI 에이전트 배정이 없습니다",
            ) from None
        background_tasks.add_task(
            deliverables.run_agent_execution_job,
            conn,
            job["job_id"],
            task_id,
            job["agent_id"],
            user["id"],
            job["role_name"],
            job["prompt"],
            payload.provider,
            payload.model,
            payload.content_type,
        )
        return {"job_id": job["job_id"], "status": "running"}

    @app.post("/api/tasks/{task_id}/execute-team-crew", status_code=202)
    def trigger_team_crew_execution(
        task_id: int,
        payload: TeamCrewExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
        개선... 사람-에이전트 연동이 가능한 구조인지 확인") —
        `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 3. 이 할 일에
        confirmed로 배정된 팀 하나의 Agent 멤버 전원을 CrewAI Crew로
        실행하고, 결과를 이 할 일의 deliverables/결재 체인에 연결한다 —
        단일 Agent 실행(`execute-agent`)과 같은 비동기 패턴."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider or payload.model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )
        try:
            job = deliverables.start_team_crew_execution(
                conn,
                task_id,
                payload.team_id,
                user["id"],
                manager_agent_id=payload.manager_agent_id,
                allow_supervisor_override=user["role"] in ("admin", "manager"),
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except LookupError as exc:
            if str(exc) == "agent_already_running":
                raise HTTPException(
                    status_code=409,
                    detail="이미 실행 중인 작업이 있습니다 — 완료를 기다려 주세요",
                ) from None
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            detail = {
                "team_not_found": "팀을 찾을 수 없습니다",
                "team_not_confirmed_for_task": "이 할 일에 확정 배정된 팀이 아닙니다",
                "team_has_no_active_agents": "이 팀에 온보딩(active)된 Agent 멤버가 없습니다",
                "manager_agent_not_in_team": "매니저로 지정한 Agent가 이 팀의 멤버가 아닙니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        background_tasks.add_task(
            deliverables.run_team_crew_job,
            conn,
            job["job_by_agent_id"],
            task_id,
            payload.team_id,
            user["id"],
            payload.process,
            payload.provider,
            payload.model,
            payload.manager_agent_id,
            payload.content_type,
        )
        return {"job_ids": list(job["job_by_agent_id"].values()), "status": "running"}

    @app.get("/api/tasks/{task_id}/agent-execution-jobs")
    def list_task_agent_execution_jobs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Phase 4 — Web UI가 폴링해 "생성 중…"/실패 상태를 협업
        워크스페이스 타임라인에 얹는다. 접근 권한은 결과물 타임라인
        조회(§2-2)와 동일하다."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_execution_jobs(conn, task_id)

    @app.get("/api/tasks/{task_id}/execution-permission")
    def get_execution_permission(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자가 스크린샷으로 보고한 갭(2026-09-29, "이 업무의 협업
        워크스페이스에 접근할 권한이 없습니다") 보완 — 웹 UI가 "이 업무의
        AI 에이전트 실행" 버튼을 실제로 보여주기 전에 먼저 확인한다."""
        try:
            return deliverables.execution_eligibility(
                conn, task_id, user["id"], user["role"] in ("admin", "manager")
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.post("/api/briefs/{brief_id}/generate", status_code=201)
    def generate_agents(
        brief_id: int,
        payload: GenerateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F4 — 생성 버튼. Job Queue+샌드박스(ADR 결정 8)로 부족한 역할을 배치 생성한다.

        `framework="crewai"`(ADR 결정 10)면 실제 CrewAI Agent+Task+Crew를
        구성해 kickoff한다. `use_llm=True`+`framework` 미지정(또는
        `"simple_llm"`)이면 기존 1회성 LLM 호출이다. 둘 다 명시적으로
        요청했을 때만 실제 API를 호출한다(사용자 요청, 2026-09-26) —
        기본값은 여전히 무료 규칙기반 스텁이다.
        """
        brief = conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")
        generator = None
        if payload.framework == "crewai":
            generator = partial(
                crew_runtime.crewai_agent_generator,
                provider=payload.provider,
                model=payload.model,
            )
        elif payload.use_llm:
            generator = partial(
                jobs.llm_agent_generator, provider=payload.provider, model=payload.model
            )
        return jobs.create_and_run_job(
            conn, brief_id, payload.task_ids, user["id"], generator=generator
        )

    @app.post("/api/jobs/{job_id}/items/{item_id}/retry")
    def retry_job_item(
        job_id: int,
        item_id: int,
        use_llm: bool = False,
        provider: str | None = None,
        model: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """NF6 — 실패 역할만 재시도."""
        generator = None
        if use_llm:
            generator = partial(
                jobs.llm_agent_generator, provider=provider, model=model
            )
        try:
            return jobs.retry_failed_item(
                conn, job_id, item_id, user["id"], generator=generator
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/agent-pool")
    def list_agent_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Web UI가 F9(온보딩/오프보딩) 화면에서 status 무관 전체 목록을 봐야
        한다 — F3 검색(`/api/pool/search`)은 `status='active'`만 노출해서
        온보딩 대기(pending_onboarding) 항목을 찾을 방법이 없었다.

        **실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
        개선해줘" 리뷰 중)**: Phase 3가 만든 `agent_evaluation.
        aggregate_agent_score()`(§5-2 "자동값과 수동값을 구분해서
        보여준다")가 실제로는 어떤 API/화면에도 연결돼 있지 않아 테스트
        말고는 아무도 못 보고 있었다 — `auto_evaluation`으로 여기 얹어서
        `performance_score`(관리자 수동)와 나란히 노출한다."""
        rows = conn.execute("SELECT * FROM agent_pool ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                "role_name": row["role_name"],
                "skill_tags": json.loads(row["skill_tags"]),
                "cost_estimate": row["cost_estimate"],
                "performance_score": row["performance_score"],
                "auto_evaluation": agent_evaluation.aggregate_agent_score(
                    conn, row["id"]
                ),
                "status": row["status"],
                "llm_provider": row["llm_provider"],
                "llm_model": row["llm_model"],
                "last_output": row["last_output"],
                "framework": row["framework"],
                "mcp_servers": json.loads(row["mcp_servers"]),
                "linked_tool_ids": pool_registry.list_agent_tool_ids(conn, row["id"]),
                # 사용자 요청(2026-10-07, "AI 에이전트 관리 노출 검토") —
                # 이 Agent가 어느 팀 소속인지 여기서 바로 보여준다(팀은
                # "팀 관리" 탭에서 정의하지만, 관리 화면에도 연결 관계가
                # 보여야 "복수 에이전트 그룹"이 업무와 어떻게 이어지는지
                # 알 수 있다).
                "team_names": teams.team_names_for_agent(conn, row["id"]),
            }
            for row in rows
        ]

    @app.post("/api/agent-pool/{agent_id}/execute")
    def execute_agent_endpoint(
        agent_id: int,
        payload: AgentExecuteRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """온보딩된 Agent에게 실제 업무를 맡긴다(사용자 요청, 2026-09-26) —
        호출하면 실제 Anthropic/OpenAI API가 불려 비용이 발생한다."""
        retrieval_sink: dict = {}
        try:
            output = agent_runtime.execute_agent(
                conn,
                agent_id,
                user["id"],
                payload.prompt,
                payload.provider,
                payload.model,
                retrieval_sink=retrieval_sink,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409, detail="온보딩(active)된 Agent만 실행할 수 있습니다"
            ) from exc
        return {
            "id": agent_id,
            "output": output,
            "retrieved_context": retrieval_sink.get("retrieved_context"),
        }

    @app.patch("/api/agent-pool/{agent_id}/model")
    def set_agent_model_config(
        agent_id: int,
        payload: AgentModelConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Agent별 기본 LLM provider/model(+ MCP 서버 설정)을 저장한다(사용자
        요청, 2026-09-26, 2차 — Anthropic/OpenAI/Ollama 중 선택; MCP는
        2026-09-27 추가). 비용이 발생하지 않는다 — LLM/MCP를 호출하지 않고
        설정만 저장한다. 이후 `/execute`가 provider/model을 안 받으면 여기서
        저장한 값을 기본으로 쓰고, `POST /api/crew/run`이 여기서 저장한
        `mcp_servers`로 그 Agent에 실제 MCP 도구를 붙인다."""
        try:
            resolved_provider, resolved_model = agent_runtime.set_model_config(
                conn,
                agent_id,
                user["id"],
                payload.provider,
                payload.model,
                mcp_servers=(
                    [s.model_dump() for s in payload.mcp_servers]
                    if payload.mcp_servers is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        return {
            "id": agent_id,
            "llm_provider": resolved_provider,
            "llm_model": resolved_model,
        }

    @app.post("/api/agent-pool/{agent_id}/index-knowledge")
    def index_agent_knowledge(
        agent_id: int,
        payload: KnowledgeIndexRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """ADR 결정 11 — 이 Agent의 산출물(또는 지정 텍스트)을 임베딩해
        지식검색 Agent(VectorDB)가 찾을 수 있게 색인한다. 실제 임베딩 API를
        호출하므로 비용이 발생한다(opt-in — 명시적으로 호출했을 때만)."""
        try:
            chunk_id = agent_runtime.index_knowledge(
                conn,
                agent_id,
                user["id"],
                payload.text,
                payload.provider,
                payload.model,
            )
        except ValueError as exc:
            detail = (
                "Agent를 찾을 수 없습니다"
                if str(exc) == "agent_not_found"
                else "색인할 텍스트가 없습니다(text를 지정하거나 먼저 실행/생성하세요)"
            )
            status_code = 404 if str(exc) == "agent_not_found" else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
        return {"id": agent_id, "chunk_id": chunk_id}

    @app.get("/api/tool-pool")
    def list_tool_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """ADR 결정 12 — 재사용 가능한 도구(MCP 서버 또는 native Tool)
        레지스트리 조회."""
        return pool_registry.list_tools(conn)

    @app.post("/api/tool-pool", status_code=201)
    def create_tool_pool_entry(
        payload: ToolPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        # 사용자 결정(2026-09-29, `docs/ROLE_TASK_CLASSIFICATION.md` 갭 1,
        # "(a) MCP 종류만 admin 전용으로 좁히기") — mcp_stdio/mcp_remote는
        # 등록되는 순간이 아니라 "그 스킬을 가진 Agent가 실행될 때" 조직
        # 전체 Agent가 실제 외부 프로세스/네트워크에 접속하게 만드는
        # 조직 차원 정책이라(GitHub Copilot 원칙 1), native(웹서치 도구
        # 등)와 달리 admin 전용으로 좁힌다. 엔드포인트 전체를 admin
        # 전용으로 만들지 않고 이 두 kind만 막는 이유는 native 도구
        # 등록(운영 업무 성격)까지 매니저에게서 뺏을 이유가 없어서다.
        if payload.kind in ("mcp_stdio", "mcp_remote") and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="MCP 도구(mcp_stdio/mcp_remote) 등록은 admin 전용입니다"
                "(docs/ROLE_TASK_CLASSIFICATION.md 갭 1)",
            )
        try:
            tool_id = pool_registry.create_tool(
                conn,
                user["id"],
                payload.name,
                payload.description,
                payload.kind,
                payload.config,
                payload.tool_names,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"이미 있는 이름입니다: {payload.name}"
            ) from exc
        return {"id": tool_id}

    @app.patch("/api/tool-pool/{tool_id}/active")
    def set_tool_pool_active(
        tool_id: int,
        payload: ToolActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pool_registry.set_tool_active(conn, user["id"], tool_id, payload.is_active)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="도구를 찾을 수 없습니다"
            ) from exc
        return {"id": tool_id, "is_active": payload.is_active}

    @app.delete("/api/tool-pool/{tool_id}")
    def delete_tool_pool_entry(
        tool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — Tool Pool 항목을 완전히 지운다(비활성화가
        아니라 진짜 삭제). 참조하던 `agent_tool_links`도 같이 지운다."""
        try:
            pool_registry.delete_tool(conn, user["id"], tool_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="도구를 찾을 수 없습니다"
            ) from exc
        return {"id": tool_id, "deleted": True}

    @app.get("/api/skill-pool")
    def list_skill_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """ADR 결정 12 — `skill_tags`를 정식 엔티티로 승격한 Skill Pool
        조회. `augmentation_mode="tool"`인 스킬은 `default_tool_pool_ids`가
        가리키는 도구를 이 스킬을 가진 모든 Agent에 자동으로 붙인다."""
        return pool_registry.list_skills(conn)

    @app.get("/api/skill-pool/names")
    def list_skill_pool_names(user: sqlite3.Row = Depends(get_current_user)):
        """Phase 5 — Skill 자동완성(사용자 요청, "협업 워크스페이스 UX
        개선"). `/api/skill-pool`은 도구 연결 상세(augmentation_mode 등)
        까지 담아 Admin/매니저 전용이지만, 스킬 "이름"만 필요한 자동완성은
        `required_skills`/`skill_tags`를 입력하는 모든 role(팀원 포함)이
        써야 한다 — 이름만 떼어 노출하는 별도 엔드포인트를 둔다."""
        return [s["name"] for s in pool_registry.list_skills(conn)]

    @app.post("/api/skill-pool", status_code=201)
    def create_skill_pool_entry(
        payload: SkillPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            skill_id = pool_registry.create_skill(
                conn,
                user["id"],
                payload.name,
                payload.description,
                payload.default_tool_pool_ids,
                payload.augmentation_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"이미 있는 이름입니다: {payload.name}"
            ) from exc
        return {"id": skill_id}

    @app.delete("/api/skill-pool/{skill_id}")
    def delete_skill_pool_entry(
        skill_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — Skill Pool 항목을 완전히 지운다.
        같은 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터
        이 스킬 기반 자동 도구 연결만 사라진다."""
        try:
            pool_registry.delete_skill(conn, user["id"], skill_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="스킬을 찾을 수 없습니다"
            ) from exc
        return {"id": skill_id, "deleted": True}

    # ---------- 모델 레지스트리(사용자 요청, 2026-10-06) ----------
    # "운영 관리"에 provider별 사용 가능한 모델을 등록해두고, "AI 에이전트
    # 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"의 자유 텍스트
    # 모델명 입력을 여기서 등록한 목록 중에서 고르는 방식으로 바꾼다
    # (skill_pool/tool_pool과 같은 "등록→선택" 패턴, docs/LIMITS.md §59).

    @app.get("/api/model-registry")
    def list_model_registry(
        provider: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return model_registry.list_models(conn, provider=provider)

    @app.get("/api/model-registry/fetch")
    def fetch_model_registry_candidates(
        provider: str,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """provider가 실제로 제공/보유한 모델 후보를 라이브로 조회한다 —
        조회만으로는 아무것도 저장되지 않는다(등록은 별도
        `POST /api/model-registry`). ollama는 로컬 서버 접속 실패,
        openai/anthropic은 API 키 미설정·네트워크 오류가 흔한 실패
        모드라 그대로 500을 내지 않고 사람이 읽을 수 있는 메시지로
        바꿔 400으로 돌려준다."""
        if provider not in model_registry.PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"지원하지 않는 provider입니다: {provider}"
            )
        try:
            models = model_registry.fetch_available_models(provider)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{provider} 모델 목록을 불러오지 못했습니다: {exc}",
            ) from exc
        return {"provider": provider, "models": models}

    @app.post("/api/model-registry", status_code=201)
    def create_model_registry_entry(
        payload: ModelRegistryCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            model_id = model_registry.create_model(
                conn, user["id"], payload.provider, payload.name
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="이미 등록된 모델입니다"
            ) from exc
        return {"id": model_id, "provider": payload.provider, "name": payload.name}

    @app.patch("/api/model-registry/{model_id}/active")
    def set_model_registry_active(
        model_id: int,
        payload: ModelActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            model_registry.set_model_active(
                conn, user["id"], model_id, payload.is_active
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="모델을 찾을 수 없습니다"
            ) from exc
        return {"id": model_id, "is_active": payload.is_active}

    @app.delete("/api/model-registry/{model_id}")
    def delete_model_registry_entry(
        model_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            model_registry.delete_model(conn, user["id"], model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="모델을 찾을 수 없습니다"
            ) from exc
        return {"id": model_id, "deleted": True}

    # ---------- 팀(사용자 요청, 2026-10-06) ----------
    # "복수의 사람이 복수의 Agent와 또는 CrewAI 장점인 역할에 따른 Agent를
    # 묶어 생성된 그룹과 협업" — `docs/MULTI_PARTY_TEAM_COLLABORATION_
    # PLAN.md` Phase 1+2. skill_pool/tool_pool/model_registry와 같은
    # "등록→재사용" 패턴(`teams.py`)을 사람+Agent 혼합 그룹에 적용한다.

    @app.get("/api/teams")
    def list_teams_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return teams.list_teams(conn)

    @app.get("/api/teams/{team_id}")
    def get_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        team = teams.get_team(conn, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다")
        return team

    @app.post("/api/teams", status_code=201)
    def create_team_endpoint(
        payload: TeamCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            team_id = teams.create_team(
                conn, user["id"], payload.name, payload.description
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": team_id, "name": payload.name}

    @app.patch("/api/teams/{team_id}/active")
    def set_team_active_endpoint(
        team_id: int,
        payload: TeamActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            teams.set_team_active(conn, user["id"], team_id, payload.is_active)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc
        return {"id": team_id, "is_active": payload.is_active}

    @app.delete("/api/teams/{team_id}")
    def delete_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            teams.delete_team(conn, user["id"], team_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc
        return {"id": team_id, "deleted": True}

    @app.post("/api/teams/{team_id}/members", status_code=201)
    def add_team_member_endpoint(
        team_id: int,
        payload: TeamMemberAdd,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            member_row_id = teams.add_team_member(
                conn,
                user["id"],
                team_id,
                payload.member_type,
                payload.member_id,
                payload.role_label,
            )
        except ValueError as exc:
            detail = (
                "팀을 찾을 수 없습니다"
                if str(exc) == "team_not_found"
                else "멤버를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="이미 이 팀의 멤버입니다"
            ) from exc
        return {"id": member_row_id}

    @app.delete("/api/teams/{team_id}/members/{member_row_id}")
    def remove_team_member_endpoint(
        team_id: int,
        member_row_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            teams.remove_team_member(conn, user["id"], team_id, member_row_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀 멤버를 찾을 수 없습니다"
            ) from exc
        return {"id": member_row_id, "deleted": True}

    @app.patch("/api/assignments/{assignment_id}/team-leader")
    def update_team_leader_endpoint(
        assignment_id: int,
        payload: TeamLeaderUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 결정(2026-10-06, "팀 리더는 배정 요청자가 기본값,
        변경 가능") — 팀 배정이 아직 `requested` 상태일 때만 바꿀 수
        있다(`assignments.update_team_leader()` 참고)."""
        try:
            assignments.update_team_leader(
                conn, user["id"], assignment_id, payload.team_leader_id
            )
        except ValueError as exc:
            detail = {
                "assignment_not_found": "배정을 찾을 수 없습니다",
                "not_a_team_assignment": "팀 배정이 아닙니다",
                "team_leader_only_changeable_while_requested": "결재 대기 중(requested)일 때만 팀 리더를 바꿀 수 있습니다",
                "user_not_found": "사용자를 찾을 수 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": assignment_id, "team_leader_id": payload.team_leader_id}

    @app.post("/api/agent-pool/{agent_id}/tools/{tool_pool_id}", status_code=201)
    def link_agent_tool_endpoint(
        agent_id: int,
        tool_pool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Agent별로 skill 매칭과 무관하게 Tool Pool 항목을 하나 더 붙인다
        (ADR 결정 12) — 다음 `run_crew()`/`crewai_agent_generator()` 실행
        부터 반영된다."""
        try:
            pool_registry.link_agent_tool(conn, user["id"], agent_id, tool_pool_id)
        except ValueError as exc:
            detail = (
                "Agent를 찾을 수 없습니다"
                if str(exc) == "agent_not_found"
                else "도구를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from exc
        return {"agent_id": agent_id, "tool_pool_id": tool_pool_id}

    @app.delete("/api/agent-pool/{agent_id}/tools/{tool_pool_id}")
    def unlink_agent_tool_endpoint(
        agent_id: int,
        tool_pool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        pool_registry.unlink_agent_tool(conn, user["id"], agent_id, tool_pool_id)
        return {"agent_id": agent_id, "tool_pool_id": tool_pool_id}

    @app.post("/api/crew/run", status_code=201)
    def run_crew_endpoint(
        payload: CrewRunRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """ADR 결정 11 — 여러 온보딩된 Agent를 CrewAI Crew로 묶어 실제 협업
        실행한다(Task.context로 산출물 전달 + allow_delegation으로 실제 동료
        위임/질의). 항상 실제 LLM API를 호출하므로 비용이 발생한다."""
        try:
            result = crew_runtime.run_crew(
                conn,
                payload.agent_ids,
                payload.task_descriptions,
                user["id"],
                process=payload.process,
                provider=payload.provider,
                model=payload.model,
                manager_agent_id=payload.manager_agent_id,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"온보딩(active)된 Agent만 참여할 수 있습니다: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

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

    @app.delete("/api/agent-pool/{agent_id}")
    def delete_agent_endpoint(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28) — AI 에이전트 레코드를 그 아래 지식
        색인·도구 연결까지 전부 지운다(오프보딩은 상태만 바꿀 뿐 지우지는
        않는다 — 이건 진짜 삭제). `confirmed` 배정이 있으면 막는다."""
        try:
            data_admin.delete_agent(conn, user["id"], agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        return {"id": agent_id, "deleted": True}

    @app.get("/api/audit-log")
    def get_audit_log(
        limit: int = 50,
        offset: int = 0,
        actor_id: int | None = None,
        subject_type: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """F13 — 감사로그 조회(Admin·매니저 전용, NF12). append-only는 DB 트리거가 강제.

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
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

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
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """F8 — 성과 점수 입력. 동의 없이는 거부된다(NF9).

        사용자 결정(2026-09-29, `docs/ROLE_TASK_CLASSIFICATION.md` 갭 2,
        "(b) Human Pool도 admin 전용으로 좁히기") — Agent Pool의
        `/performance`(admin 전용)와 대칭을 맞췄다. 이전엔 admin+manager
        공용이었다."""
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

    @app.get("/api/dashboard")
    def get_dashboard(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Phase 5 — 매니저 대시보드(`docs/HAIR_MASTER_PLAN.md` §6). 팀
        Agent 평균 Gate 점수·대기 승인 건수·비용 추이를 한 번에 담는다.
        F10/F13과 같은 이유로 Admin·매니저 전용이다."""
        return dashboard.summary(conn, user["id"], user["role"])

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

    @app.get("/api/approvals")
    def list_approvals(
        scope: str = "inbox",
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """코드 재점검(2026-09-26)에서 발견한 누락 보완 — 승인권자가 자신에게 무엇이
        결재 대기 중인지 조회할 API가 전무했다(F7이 이론상으로만 동작). `decide_approval`
        의 권한 로직과 동일한 기준으로 "내가 지금 결정할 수 있는 건"만 보여준다.

        - `scope=mine`(누구나): 내가 요청한 승인 전체(상태 무관).
        - `scope=inbox`(Admin/매니저 전용, 기본값): 아직 `pending`이고, 내가
          요청자가 아니며(self-approval 제외, ADR 결정 3-A), Admin이면 전부,
          매니저면 승인자 미지정이거나 나로 지정된 건만.
        """
        if scope == "mine":
            rows = conn.execute(
                "SELECT * FROM approvals WHERE requested_by = ? ORDER BY id DESC",
                (user["id"],),
            ).fetchall()
            return [dict(row) for row in rows]
        if scope != "inbox":
            raise HTTPException(
                status_code=400, detail="scope는 inbox 또는 mine이어야 합니다"
            )
        if user["role"] not in (Role.ADMIN.value, Role.MANAGER.value):
            raise HTTPException(
                status_code=403,
                detail="승인 대기함은 Admin/매니저만 조회할 수 있습니다",
            )
        return approvals_mod.list_inbox(conn, user["id"], user["role"])

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
            deliverable_result = None
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
                elif not next_step_pending and subject_type == "deliverable":
                    # Phase 2 — 체인의 마지막 단계 통과 시에만 산출물을 확정한다
                    # (assignment와 같은 원리, docs/PHASE1_DELIVERABLE_SPEC.md
                    # 후속 — deliverables.py docstring "Phase 2 설계" 참고).
                    deliverable_result = deliverables.resolve_deliverable_decision(
                        conn, subject_id, True
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
        return {
            "id": approval_id,
            "status": new_status,
            "next_step_pending": next_step_pending,
            "assignment": assignment_result,
            "deliverable": deliverable_result,
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        """F11 — Web UI 셸(코드 재점검 R4). 위 JSON API를 그대로 호출하는 단일
        페이지 바닐라 JS 앱이다 — 새 비즈니스 로직은 없다(webui.py)."""
        return webui.INDEX_HTML

    return app
