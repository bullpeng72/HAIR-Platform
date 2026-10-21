"""HAIR-Platform API — Phase 3 v0–v4 (전체 개발 버전 완료).

v0 범위: Auth 최소구현(세션 기반) + RBAC 4종 역할
+ 브리프 제출(규칙기반 스텁) + Human Pool 온보딩 + 최소 승인 워크플로우
(단일 승인자, self-approval 방지).

v1 범위 추가: Review UI(Task 분해 결과 검토, 후보 매칭 미포함) +
Pool 통합 검색 + Assignment Engine 매칭(점수 breakdown 포함, `assignment.py`).

v2 범위 추가: 배분 요청 → 승인 후 확정(`assignments`+`assignment_history`
트랜잭션 원자성) + 조직도 조회(confirmed 배정만 노출) + 재배치
(같은 task 재승인 시 이전 confirmed 배정을 superseded로 전환, `assignments.py`).

v3 범위 추가: 다단계 결재라인(조직도 매니저 체인 자동 라우팅 — `approvals.py`) +
Job Queue+Docker 샌드박스로 Agent 생성(실패 역할만 재시도 — `jobs.py`·
`sandbox.py`) + Agent 온보딩/오프보딩(이때 `pool.agent_pool_candidates()`가
처음으로 실제 데이터를 반환하게 된다) + 감사로그(append-only — `audit.py`).

v4(마지막 개발 버전) 범위 추가: 성과리뷰(사람 데이터는 사전 동의(opt-in) 없이는
절대 노출하지 않는다 — `performance.py`) + 비용 조회(1차는 수동 입력만
— `cost.py`). 이로써 핵심 기능이 전부 구현됐다.
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
    agent_optimizer,
    agent_runtime,
    assignments,
    audit,
    conversations,
    cost,
    crew_history,
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
    pipeline_runtime,
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
    create_session_token,
    create_user,
    get_current_user,
    hash_password,
    require_role,
)
from hair_platform.models import (
    AdhocCrewExecuteRequest,
    AgentCreateDirect,
    AgentExecuteRequest,
    AgentModelConfig,
    AgentNameUpdate,
    AgentPersonaConfig,
    AgentTuningConfig,
    ApprovalDecision,
    AssignmentCancelRequest,
    AssignmentRequest,
    AssistRequest,
    BriefSubmission,
    ConsentRequest,
    ConversationAutoReplyUpdate,
    ConversationCreate,
    ConversationMessageCreate,
    ConversationModelUpdate,
    ConversationParticipantInvite,
    CostUpdateRequest,
    CrewRunRequest,
    DelegateAgentRequest,
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
    PipelineExecuteRequest,
    PipelineStageCreate,
    PipelineStageReorder,
    PipelineStageUpdate,
    PipelineTemplateActiveUpdate,
    PipelineTemplateCreate,
    SkillPoolCreate,
    SkillPoolUpdate,
    TaskDecomposition,
    TaskDependencyCreate,
    TaskNotesUpdate,
    TaskParticipantInvite,
    TaskPriorityUpdate,
    TaskStatusUpdate,
    TeamActiveUpdate,
    TeamCreate,
    TeamCrewExecuteRequest,
    TeamDefaultsUpdate,
    TeamLeaderUpdate,
    TeamMemberAdd,
    TeamMemberReorder,
    ToolActiveUpdate,
    ToolPoolCreate,
    ToolPoolUpdate,
    UserCreate,
    UserPasswordReset,
    UserStatusUpdate,
)


def parse_brief_stub(raw_text: str) -> list[str]:
    """규칙기반 스텁 — 줄바꿈 단위로 역할 후보를 나눈다(실제 LLM 파싱은 이후 버전)."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines or [raw_text.strip()]


def create_app(db_path: str | None = None) -> FastAPI:
    """`db_path`를 생략하면(uvicorn `--factory` 경로가 인자 없이 호출하는
    바로 그 경우) `HAIR_PLATFORM_DB_PATH` 환경변수를 본다 — 이전까지
    여기 아무 폴백이 없어 `uvicorn hair_platform.app:create_app --factory`
    로 띄우면 `--db-path`를 지정할 방법 자체가 없이 매 재시작마다
    조용히 ":memory:"(휘발성)로 뜨고 있었다(2026-10-10, `hair serve` CLI
    작업 중 발견). 명시적으로 인자를 넘기는
    기존 호출(테스트 등)은 전부 그대로 동작한다."""
    if db_path is None:
        db_path = os.environ.get("HAIR_PLATFORM_DB_PATH", ":memory:")
    # 사용자 요청(2026-09-26) — 프로젝트 루트 `.env`(gitignore됨)에서
    # ANTHROPIC_API_KEY/OPENAI_API_KEY/HAIR_PLATFORM_SECRET_KEY 등을 읽는다.
    # `.env`가 없으면 조용히 아무 일도 하지 않는다(기존 동작과 동일).
    # 이미 설정된 실제 환경변수를 덮어쓰지 않는다(override=False, 기본값).
    load_dotenv()

    app = FastAPI(title="HAIR-Platform")
    # 세션 기반 인증(SessionMiddleware).
    # 코드 재점검(2026-09-26)에서 발견: "배포 시 환경변수로 교체한다"는 주석만 있고
    # 실제로 환경변수를 읽는 코드가 없어 항상 소스에 공개된 고정 키를 쓰던 오류를
    # 수정 — HAIR_PLATFORM_SECRET_KEY가 없으면 개발용 기본값으로 폴백한다.
    secret_key = os.environ.get(
        "HAIR_PLATFORM_SECRET_KEY", "dev-secret-change-in-production"
    )
    app.add_middleware(SessionMiddleware, secret_key=secret_key)
    # get_current_user가 Bearer 토큰(탭별 다계정 로그인, auth.py 참고)을
    # 복호화할 때 같은 키를 쓴다 — 쿠키 서명과 토큰 서명이 어긋나면 안 된다.
    app.state.secret_key = secret_key

    conn = db.get_connection(db_path)
    db.init_schema(conn)
    # 실측으로 발견(2026-09-28, 리뷰) — 이전 프로세스가 실행 도중
    # 죽으면 agent_execution_jobs가 영원히 'running'으로 남아 해당
    # task의 실행 버튼이 복구 불가능하게 막힌다. 단일 프로세스 전제이므로
    # 기동 시점의 'running'은 전부 이전 프로세스의 잔재로 확정하고 정리한다.
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
        # 탭별 다계정 로그인(auth.py 주석 참고) — Web UI는 이 토큰을
        # sessionStorage(탭마다 독립)에 저장해 쿠키 세션과 별도로 쓴다.
        token = create_session_token(secret_key, user["id"])
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "token": token,
        }

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
        Web UI/API로 설정할 방법이 전무해 결재라인이 항상 flat(단일 단계)으로
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
        """결재라인 라우팅에 쓰는 `users.manager_id`를 갱신한다(ADMIN 전용)."""
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
        target = conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        if not payload.password:
            raise HTTPException(status_code=400, detail="비밀번호를 입력해야 합니다")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
        audit.record(conn, user["id"], "user_password_reset", "user", user_id)
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
        참조돼 하드 삭제가 FK 위반으로 불가능하다(감사 이력 보존과
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
        """Web UI가 브리프 목록을 브라우징할 수 있어야 Task 분해 화면으로
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
        `confirmed` 배정이 있으면 막는다(먼저 배정/결재에서 정리)."""
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

        # 사용자 결정(2026-09-29, "(b) Human Pool도 admin 전용으로
        # 좁히기") — Agent Pool의
        # cost_estimate/performance_score는 이미 admin 전용 전용
        # 엔드포인트로만 수정 가능한데, Human Pool은 이 일반
        # PATCH로 매니저도 자유롭게 고칠 수 있어 비대칭이었다. 다른
        # 필드(역할·스킬·가용성·상사 지정 등)는 매니저의 정상 인사
        # 업무라 그대로 두고, cost_estimate만 admin 전용으로 좁힌다.
        if payload.cost_estimate is not None and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="Human Pool 비용(cost_estimate) 수정은 admin 전용입니다",
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
                raise HTTPException(status_code=404, detail="매니저를 찾을 수 없습니다")
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
        """Task 분해 결과 등록(후보 매칭은 포함하지 않는다)."""
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
        """Web UI가 매칭/배분 화면에서 브리프별 Task 목록을 골라야 한다 —
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
                "status": row["status"],
                "priority": row["priority"],
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
        """Human Pool + Agent Pool(스텁) 통합 검색. 전원 조회 가능(RBAC 매트릭스).

        코드 재점검(2026-09-26)에서 발견: role/skill 필터가 human_pool에만 걸리고
        Agent Pool은 무조건 전부 반환되던 버그를 수정 — 양쪽에 동일 필터를 적용한다.

        Phase 5("검색/필터 스케일") — `available_only`(boolean)를
        `availability`(가능/불가 둘 다 명시적으로 찾을 수 있게)로 넓히고,
        감사로그와 같은 `limit`/`offset`+`total` 페이지네이션 규약을
        적용했다. Human+Agent를 하나로 합친 뒤 자르는 방식이라(두 풀을
        SQL에서 각각 페이지네이션하면 "한 페이지"가 한쪽 풀로만 채워질
        수 있어 부정확해진다) 큰 풀에서는 비효율적일 수 있다 — 이 저장소가
        전제하는 소규모 팀 규모에서는 허용 가능한 트레이드오프다.

        "부서" 필터(원안)는 넣지 않았다 — 이 스키마엔 애초에 "부서"라는
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
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 전엔 Agent는 항상 None으로 덮어썼다(이름 자체가 없던
                # 시절 코드). `pool.agent_pool_candidates()`가 이제
                # `candidate.name`을 채워 주므로 Agent는 그 값을 그대로
                # 쓴다(사람은 기존처럼 따로 조회한 값을 그대로 쓴다).
                "name": name_by_id.get(c.id) if c.type == "human" else c.name,
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
        """매칭 미리보기 + 점수 breakdown — 배정 요청(assignments)은 v2 범위.

        사용자 결정(2026-09-29, "매칭 점수 breakdown을 배정 후보
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
        """배정 요청. 승인 전까지는 `requested` 상태로만 남는다.

        사용자 결정(2026-09-29, "팀원 셀프서비스(본인이 직접 배정 요청)를
        허용") — MEMBER는 본인의 human_pool 레코드로만 요청할 수 있다
        (다른 사람·Agent를 대신 요청하는 건 여전히 admin/manager 전용).
        승인 게이트와 self-approval 방지는 그대로 적용되므로
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

    @app.post("/api/tasks/{task_id}/delegate", status_code=201)
    def delegate_agent(
        task_id: int,
        payload: DelegateAgentRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """위임 — 결재 없이 즉시 확정되는 경량 배정. `request_assignment`
        ("배정")와 같은 ADMIN/MANAGER 권한 경계를 쓴다(Agent 관련 배정
        행동이 지금까지 MEMBER 셀프서비스 밖이었던 것과 동일) — 결재가
        없어 더 즉각적인 만큼, 권한은 더 좁히지 않고 그대로 유지한다."""
        try:
            return assignments.create_delegation(
                conn, task_id, payload.agent_id, user["id"]
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
                   hp.name AS human_name,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name,
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
    # RBAC 원칙: 관리자/매니저는 감독 목적으로 조회는 전부 가능하지만,
    # 작성(deliverable/댓글)은 그 task의 사람 당사자/Agent 요청자 본인만
    # — 대리 작성 금지.

    @app.get("/api/my-work")
    def get_my_work(
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """확정된 배정 중 내가 당사자이거나(사람) 요청자인(Agent) task."""
        return deliverables.list_my_work(conn, user["id"], limit, offset)

    @app.get("/api/all-work")
    def get_all_work(
        limit: int = 200,
        offset: int = 0,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """할 일 보드의 "전체" 범위. `/api/my-work`와 같은 모양을
        반환하되 user_id로 안 좁힌다 — 조직 전체 현황이라 ADMIN/MANAGER
        전용."""
        return deliverables.list_all_work(conn, limit, offset)

    @app.get("/api/inbox")
    def get_inbox_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        """Phase 5 — 통합 인박스. 결재 대기(Admin/매니저)·Agent 실행
        실패·낮은 Gate 점수 경고를 한 목록으로 합쳐 반환한다. 새 권한
        규칙을 추가하지 않는다 — 각
        항목은 기존 조회 함수(`approvals.list_inbox`/`deliverables.
        list_my_work`)가 이미 강제하는 접근 범위를 그대로 물려받는다."""
        return inbox.get_inbox(conn, user["id"], user["role"])

    @app.get("/api/tasks/{task_id}")
    def get_task_endpoint(task_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """2026-10-08 — Task 단건 조회.
        지금까지 Task 단건 조회 엔드포인트가 없어 `notes` 등 새 필드를
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
        """Task 메모. 참가자 본인만 수정(결재 불필요한 가벼운
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

    @app.patch("/api/tasks/{task_id}/status")
    def update_task_status_endpoint(
        task_id: int,
        payload: TaskStatusUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 할 일 보드 칸반 상태 이동.
        `update_task_notes_endpoint()`와 같은 참가자 전용 판정."""
        try:
            return deliverables.update_task_status(
                conn, task_id, user["id"], payload.status
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

    @app.patch("/api/tasks/{task_id}/priority")
    def update_task_priority_endpoint(
        task_id: int,
        payload: TaskPriorityUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4."""
        try:
            return deliverables.update_task_priority(
                conn, task_id, user["id"], payload.priority
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

    @app.post("/api/tasks/{task_id}/dependencies", status_code=201)
    def add_task_dependency_endpoint(
        task_id: int,
        payload: TaskDependencyCreate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 선행 작업 추가. 자기참조·순환은
        `deliverables.add_task_dependency()`가 검사한다."""
        try:
            return deliverables.add_task_dependency(
                conn, task_id, user["id"], payload.depends_on_task_id
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except ValueError as exc:
            reason = str(exc)
            if reason == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            if reason == "depends_on_task_not_found":
                raise HTTPException(
                    status_code=404, detail="선행 작업을 찾을 수 없습니다"
                ) from None
            if reason == "self_dependency":
                raise HTTPException(
                    status_code=400,
                    detail="자기 자신을 선행 작업으로 지정할 수 없습니다",
                ) from None
            if reason == "cycle_detected":
                raise HTTPException(
                    status_code=409, detail="이 의존관계를 추가하면 순환이 생깁니다"
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 등록된 의존관계입니다"
            ) from None

    @app.delete("/api/tasks/{task_id}/dependencies/{depends_on_task_id}")
    def remove_task_dependency_endpoint(
        task_id: int,
        depends_on_task_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 설계 문서엔 없지만 POST의 자연스러운
        대칭으로 추가(`remove_task_dependency()` 독스트링 참고)."""
        try:
            deliverables.remove_task_dependency(
                conn, task_id, user["id"], depends_on_task_id
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=404, detail="등록된 의존관계를 찾을 수 없습니다"
            ) from None
        return {
            "task_id": task_id,
            "depends_on_task_id": depends_on_task_id,
            "deleted": True,
        }

    @app.get("/api/tasks/{task_id}/dependencies")
    def get_task_dependencies_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """2026-10-08, Phase 4 — **사용자 결정**: 1차 "선행 작업
        목록"이 아니라 Riido 수준의 그래프 시각화로 바로 간다. 이
        엔드포인트는 그 그래프가 그릴 노드/엣지(연결된 컴포넌트 전체)를
        반환한다(`deliverables.task_dependency_graph()`)."""
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            return deliverables.task_dependency_graph(conn, task_id)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.get("/api/tasks/{task_id}/activity")
    def get_task_activity_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """배정·결과물·댓글·Agent 실행·결재·Task 변경을 합친 통합
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

    @app.post("/api/tasks/{task_id}/participants", status_code=201)
    def invite_task_participant_endpoint(
        task_id: int,
        payload: TaskParticipantInvite,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 2 — 결재 불필요한 가벼운 초대.
        admin/manager는 항상 가능하고, 그 외엔
        이 task에 이미 댓글을 달 수 있는 사람(정식 참가자 또는 이미
        초대된 사람)만 — "초대의 초대"는 허용하되 완전한 외부인이
        마음대로 사람/Agent를 끌어들이진 못하게 한다."""
        if user["role"] not in ("admin", "manager") and not (
            deliverables.can_comment_on_task(conn, task_id, user["id"])
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무에 참가자를 초대할 권한이 없습니다",
            )
        try:
            return deliverables.invite_participant(
                conn,
                task_id,
                user["id"],
                payload.participant_type,
                payload.participant_id,
            )
        except ValueError as exc:
            if str(exc) == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            if str(exc) == "agent_not_found":
                raise HTTPException(
                    status_code=404, detail="Agent를 찾을 수 없습니다"
                ) from None
            if str(exc) == "user_not_found":
                raise HTTPException(
                    status_code=404, detail="사용자를 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 초대된 참가자입니다"
            ) from None

    @app.get("/api/tasks/{task_id}/participants")
    def list_task_participants_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Phase 2 — 초대된 참가자 목록."""
        if user["role"] not in ("admin", "manager") and not (
            deliverables.can_comment_on_task(conn, task_id, user["id"])
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_participants(conn, task_id)

    # ---------- 대화 중심 작업공간(Conversational Workspace,
    # 2026-10-08, 사용자 요청 "로그인하면 기본적으로 대화창이 주어지고
    # ... 사람이나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용
    # 하여 업무를 수행") ----------

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
        `run_team_mention_job`, (3) `skill`이 명시됐거나(명시적 요청이라
        항상 트리거) 멘션이 하나도 없고 `ai_auto_reply=1`이면
        `run_default_ai_job`(멘션이 있으면 기본 AI는 끼어들지 않는다 —
        응답 중복 방지)."""
        try:
            result = conversations.create_message(
                conn, conversation_id, user["id"], payload.content, payload.skill
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
            background_tasks.add_task(
                conversations.run_agent_mention_job,
                conn,
                job_id,
                conversation_id,
                agent["id"],
                user["id"],
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
            background_tasks.add_task(
                conversations.run_team_mention_job,
                conn,
                job_id,
                conversation_id,
                team["id"],
                user["id"],
            )
            triggered_jobs.append(
                {
                    "trigger_type": "team_mention",
                    "job_id": job_id,
                    "team_id": team["id"],
                }
            )
        if result["skill"] or (not result["has_mentions"] and result["ai_auto_reply"]):
            job_id = conversations.start_default_ai_job(
                conn, conversation_id, user["id"], result["skill"]
            )
            background_tasks.add_task(
                conversations.run_default_ai_job,
                conn,
                job_id,
                conversation_id,
                user["id"],
                result["skill"],
            )
            triggered_jobs.append(
                {
                    "trigger_type": "skill" if result["skill"] else "auto_reply",
                    "job_id": job_id,
                }
            )

        return {"id": result["id"], "triggered_jobs": triggered_jobs}

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

    @app.get("/api/tasks/{task_id}/deliverables")
    def list_task_deliverables(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """협업 워크스페이스 타임라인(시간순)."""
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
        """사람 결과물 작성. 대리 작성 금지(참가자 본인만)."""
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

    @app.post("/api/tasks/{task_id}/assist")
    def assist_task(
        task_id: int,
        payload: AssistRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """"LLM으로 도와줘". 실제 LLM API를 호출하므로 비용이
        발생한다. 결과는 저장하지 않는다 — 검토 후 별도로 제출해야 한다.
        팀원/뷰어는 provider/model을 선택할 수 없다(RBAC 결정, 비용
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
        """Phase 4로 비동기화 — task에 배정된 Agent 실행을 큐에
        올린다. 검증만 동기로 마치고 202로 즉시 응답하며, 실제 LLM 호출
        (비용 발생)은 응답 전송 후 백그라운드에서 진행된다 — Web UI는
        `GET .../agent-execution-jobs`로 폴링해 완료를 확인한다. 요청자
        본인 또는 매니저/관리자(감독 대리 실행 허용 — "작성"과
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
        개선... 사람-에이전트 연동이 가능한 구조인지 확인") — Phase 3.
        이 할 일에 confirmed로 배정된 팀 하나의 Agent 멤버 전원을 CrewAI Crew로
        실행하고, 결과를 이 할 일의 deliverables/결재 체인에 연결한다 —
        단일 Agent 실행(`execute-agent`)과 같은 비동기 패턴."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider
            or payload.model
            or payload.planning
            or payload.manager_provider
            or payload.manager_model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model/매니저 LLM/planning을 직접 선택할 수 없습니다",
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
        # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 호출자가
        # process/manager_agent_id를 명시하지 않았으면(기존처럼 매번
        # 새로 고르지 않아도 되도록) "팀 관리"에서 저장해둔 팀 기본값을
        # 쓴다 — 그마저 없으면 기존처럼 "sequential"/None(자동 추정)으로
        # 떨어진다(하위 호환).
        team_defaults = teams.get_team(conn, payload.team_id)
        effective_process = (
            payload.process
            or (team_defaults["default_process"] if team_defaults else None)
            or "sequential"
        )
        effective_manager_agent_id = payload.manager_agent_id or (
            team_defaults["default_manager_agent_id"] if team_defaults else None
        )
        background_tasks.add_task(
            deliverables.run_team_crew_job,
            conn,
            job["job_by_agent_id"],
            task_id,
            payload.team_id,
            user["id"],
            effective_process,
            payload.provider,
            payload.model,
            effective_manager_agent_id,
            payload.content_type,
            memory=payload.memory,
            planning=payload.planning,
            manager_provider=payload.manager_provider,
            manager_model=payload.manager_model,
        )
        return {"job_ids": list(job["job_by_agent_id"].values()), "status": "running"}

    @app.post("/api/tasks/{task_id}/execute-adhoc-crew", status_code=202)
    def trigger_adhoc_crew_execution(
        task_id: int,
        payload: AdhocCrewExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 업무에 바로 실행") —
        팀 사전 등록·배정·승인 없이, 이 Task의 참가자가 Agent를 즉석으로
        골라 바로 CrewAI Crew로 실행하고 결과를 이 Task의 deliverable+
        결재 체인에 연결한다(`execute-team-crew`와 같은 비동기 패턴,
        `deliverables.start_adhoc_crew_execution()` 참고)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider
            or payload.model
            or payload.planning
            or payload.manager_provider
            or payload.manager_model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model/매니저 LLM/planning을 직접 선택할 수 없습니다",
            )
        if len(payload.agent_ids) != len(payload.task_descriptions):
            raise HTTPException(
                status_code=400,
                detail="agent_ids와 task_descriptions의 개수가 같아야 합니다",
            )
        # 사용자 요청(2026-10-20, "독립 작업 병렬 실행") — 이 검증은
        # `crew_runtime.run_crew()` 안에서도 하지만, 거기서 걸리면
        # BackgroundTasks 안에서 조용히 'failed' job으로만 남는다(길이
        # 불일치 검증을 여기서 미리 막는 것과 같은 이유 — 사용자가 즉시
        # 알 수 있게 요청 시점에 400으로 막는다).
        if payload.parallel_agent_ids and payload.process != "sequential":
            raise HTTPException(
                status_code=400,
                detail="병렬 실행(parallel_agent_ids)은 process='sequential'에서만 지정할 수 있습니다",
            )
        try:
            job = deliverables.start_adhoc_crew_execution(
                conn,
                task_id,
                user["id"],
                payload.agent_ids,
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
                "no_agents_selected": "참여할 Agent를 1명 이상 선택하세요",
                "some_agents_not_active": "선택한 Agent 중 온보딩(active) 상태가 아닌 Agent가 있습니다",
                "manager_agent_not_in_agent_ids": "매니저로 지정한 Agent가 참여 Agent 목록에 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        background_tasks.add_task(
            deliverables.run_adhoc_crew_job,
            conn,
            job["job_by_agent_id"],
            task_id,
            user["id"],
            payload.agent_ids,
            payload.task_descriptions,
            payload.process,
            payload.provider,
            payload.model,
            payload.manager_agent_id,
            payload.content_type,
            memory=payload.memory,
            planning=payload.planning,
            manager_provider=payload.manager_provider,
            manager_model=payload.manager_model,
            parallel_agent_ids=payload.parallel_agent_ids,
        )
        return {"job_ids": list(job["job_by_agent_id"].values()), "status": "running"}

    @app.get("/api/tasks/{task_id}/suggested-agents")
    def suggest_agents_for_task_endpoint(
        task_id: int, limit: int = 5, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움" — Crew
        빌더 로드맵 5단계) — "업무에 바로 실행" 모드가 Task 선택 시
        호출해 Agent 선택 행을 자동으로 채운다. 읽기 전용·비용 없음이라
        `execute-adhoc-crew`와 같은 기준(이 Task의 참가자 또는 admin/
        manager)으로 넓게 허용한다."""
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
        return pool_registry.suggest_agents_for_task(conn, task_id, limit=limit)

    @app.get("/api/tasks/{task_id}/agent-execution-jobs")
    def list_task_agent_execution_jobs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Phase 4 — Web UI가 폴링해 "생성 중…"/실패 상태를 협업
        워크스페이스 타임라인에 얹는다. 접근 권한은 결과물 타임라인
        조회와 동일하다."""
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

    @app.post("/api/tasks/{task_id}/execute-pipeline", status_code=202)
    def trigger_pipeline_execution(
        task_id: int,
        payload: PipelineExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
        개발 Agent 팀을 구성... 각 단계별 단독 또는 복수의 에이전트가
        순차/병행 처리") — 할 일 하나에 단계형 파이프라인을 실행한다.
        `execute-team-crew`와 같은 비동기 2단계 패턴(동기로 run 행만
        만들고, 실제 단계 실행은 BackgroundTasks)."""
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
        try:
            run_id = pipeline_runtime.start_pipeline_run(
                conn, user["id"], payload.pipeline_template_id, task_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            detail = {
                "pipeline_template_not_found": "파이프라인 템플릿을 찾을 수 없습니다",
                "pipeline_template_inactive": "비활성화된 파이프라인 템플릿입니다",
                "task_not_found": "할 일을 찾을 수 없습니다",
                "pipeline_template_has_no_stages": "단계가 없는 파이프라인 템플릿입니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        background_tasks.add_task(
            pipeline_runtime.run_pipeline_stage_job, conn, run_id, user["id"]
        )
        return {"run_id": run_id, "status": "running"}

    @app.get("/api/tasks/{task_id}/pipeline-runs")
    def list_task_pipeline_runs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """파이프라인 진행상황 카드가 폴링한다 — 권한은 위 Agent 실행 잡
        목록 조회와 동일하다."""
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
        return pipeline_runtime.list_pipeline_runs_for_task(conn, task_id)

    @app.get("/api/tasks/{task_id}/crew-runs")
    def list_task_crew_runs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행
        가시성") — 이 Task의 팀/즉석 Crew 실행 이력(진행 중인 것
        포함 — `progress` 필드로 "N명 중 M명 완료"를 보여줄 수 있다).
        권한은 위 파이프라인 실행 이력 조회와 동일하다."""
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
        return crew_history.list_runs_for_task(conn, task_id)

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
        """생성 버튼. Job Queue+샌드박스로 부족한 역할을 배치 생성한다.

        `framework="crewai"`면 실제 CrewAI Agent+Task+Crew를
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

    @app.post("/api/agent-pool", status_code=201)
    def create_agent_direct(
        payload: AgentCreateDirect,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
        참조) — Brief/Task 분해 없이 역할명·goal·backstory·
        expected_output·skill_tags를 한 번에 입력해 즉시 Agent를
        등록한다("AI 에이전트 생성" 탭의 두 번째 모드). 기존 Task 기반
        생성과 동일하게 `pending_onboarding`으로 시작 — 온보딩 승인
        (`POST /api/agent-pool/{id}/onboard`)은 그대로 admin 전용."""
        try:
            agent_id = agent_runtime.create_agent_directly(
                conn,
                user["id"],
                payload.role_name,
                payload.skill_tags,
                goal=payload.goal,
                backstory=payload.backstory,
                expected_output=payload.expected_output,
            )
        except ValueError as exc:
            detail = {
                "empty_role_name": "역할명을 입력하세요",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": agent_id, "role_name": payload.role_name, "status": "pending_onboarding"}

    @app.post("/api/jobs/{job_id}/items/{item_id}/retry")
    def retry_job_item(
        job_id: int,
        item_id: int,
        use_llm: bool = False,
        provider: str | None = None,
        model: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """실패 역할만 재시도."""
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
        """Web UI가 온보딩/오프보딩 화면에서 status 무관 전체 목록을 봐야
        한다 — 검색(`/api/pool/search`)은 `status='active'`만 노출해서
        온보딩 대기(pending_onboarding) 항목을 찾을 방법이 없었다.

        **실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
        개선해줘" 리뷰 중)**: Phase 3가 만든 `agent_evaluation.
        aggregate_agent_score()`("자동값과 수동값을 구분해서
        보여준다")가 실제로는 어떤 API/화면에도 연결돼 있지 않아 테스트
        말고는 아무도 못 보고 있었다 — `auto_evaluation`으로 여기 얹어서
        `performance_score`(관리자 수동)와 나란히 노출한다."""
        rows = conn.execute("SELECT * FROM agent_pool ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                "role_name": row["role_name"],
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 선택 입력이라 비어 있을 수 있다(비어 있으면 화면이
                # role_name으로 폴백한다).
                "name": row["name"],
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
                # 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해
                # Finetuning") — 지정 안 한 값은 전부 키 자체가 없다(={}),
                # 화면이 그걸 "CrewAI 기본값 사용 중"으로 보여준다.
                "tuning_config": json.loads(row["tuning_config"]),
                "linked_tool_ids": pool_registry.list_agent_tool_ids(conn, row["id"]),
                # 사용자 요청(2026-10-07, "AI 에이전트 관리 노출 검토") —
                # 이 Agent가 어느 팀 소속인지 여기서 바로 보여준다(팀은
                # "팀 관리" 탭에서 정의하지만, 관리 화면에도 연결 관계가
                # 보여야 "복수 에이전트 그룹"이 업무와 어떻게 이어지는지
                # 알 수 있다).
                "team_names": teams.team_names_for_agent(conn, row["id"]),
                # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 저장된
                # 오버라이드(없으면 빈 문자열/None)와, 실제 Crew 실행에
                # "지금 적용되는" 값을 구분해 둘 다 보여준다 — 후자는
                # `crew_runtime.py`의 자동 생성 로직을 그대로 호출해
                # 미리보기로 쓴다(화면이 그 로직을 중복 구현하지 않도록).
                "goal": row["goal"],
                "backstory": row["backstory"],
                "expected_output": row["expected_output"],
                "effective_goal": row["goal"]
                or crew_runtime._agent_goal(
                    row["role_name"], json.loads(row["skill_tags"])
                ),
                "effective_backstory": row["backstory"]
                or crew_runtime._agent_backstory(
                    conn, row["role_name"], json.loads(row["skill_tags"])
                ),
                "effective_expected_output": row["expected_output"]
                or crew_runtime.DEFAULT_EXPECTED_OUTPUT,
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

    @app.patch("/api/agent-pool/{agent_id}/tuning")
    def set_agent_tuning_config(
        agent_id: int,
        payload: AgentTuningConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해
        Finetuning 하는 기능") — CrewAI Agent/LLM 동작 파라미터 저장.
        `set_agent_model_config`와 같은 권한(admin+manager, 비용 없는
        설정 전용 엔드포인트)."""
        try:
            saved = agent_runtime.set_tuning_config(
                conn, agent_id, user["id"], payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        return {"id": agent_id, "tuning_config": saved}

    @app.patch("/api/agent-pool/{agent_id}/persona")
    def set_agent_persona(
        agent_id: int,
        payload: AgentPersonaConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "CrewAI 에이전트... goal, backstory...
        고급 에이전트 관리 기능") — `set_agent_tuning_config`와 같은
        권한(admin+manager, 비용 없는 설정 전용 엔드포인트).
        `agent_runtime.set_persona()` 참고."""
        try:
            saved = agent_runtime.set_persona(
                conn, agent_id, user["id"], **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "agent_not_found":
                raise HTTPException(
                    status_code=404, detail="Agent를 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": agent_id, **saved}

    @app.get("/api/agent-pool/{agent_id}/optimize-recommendation")
    def get_agent_optimize_recommendation(
        agent_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-18, "Agent의 목표, 기능, 입출력 등을 고려해
        가용한 LLM 모델에서 종류 및 설정 등을 최적화 setting 하는 admin
        기능") — 규칙 기반 1차 추천(비용 없음, `agent_optimizer.py`).
        저장은 하지 않는다 — admin이 검토 후 기존 모델/파인튜닝 저장
        버튼으로 직접 적용한다."""
        try:
            return agent_optimizer.recommend_for_agent(conn, agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc

    @app.post("/api/agent-pool/{agent_id}/optimize-recommendation/refine")
    def refine_agent_optimize_recommendation(
        agent_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """규칙 기반 추천을 LLM에게 검토·보정받는다 — **실제 LLM API를
        호출해 비용이 발생한다**(명시적으로 이 엔드포인트를 호출했을 때만).
        LLM이 레지스트리에 없는 모델을 환각하거나 잘못된 JSON을 내면
        규칙 기반 추천으로 안전하게 되돌아간다(`source` 필드로 구분)."""
        try:
            return agent_optimizer.refine_with_llm(conn, agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc

    @app.get("/api/agent-pool-optimization/recommendations")
    def list_agent_optimize_recommendations(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """"LLM 설정 최적화" 일괄 화면용 — 전체 Agent(deprecated 제외)의
        규칙 기반 추천을 한 번에 반환한다(비용 없음)."""
        return agent_optimizer.recommend_all(conn)

    @app.post("/api/agent-pool/{agent_id}/index-knowledge")
    def index_agent_knowledge(
        agent_id: int,
        payload: KnowledgeIndexRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """이 Agent의 산출물(또는 지정 텍스트)을 임베딩해
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
        """재사용 가능한 도구(MCP 서버 또는 native Tool)
        레지스트리 조회."""
        return pool_registry.list_tools(conn)

    @app.post("/api/tool-pool", status_code=201)
    def create_tool_pool_entry(
        payload: ToolPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        # 사용자 결정(2026-09-29, "(a) MCP 종류만 admin 전용으로
        # 좁히기") — mcp_stdio/mcp_remote는
        # 등록되는 순간이 아니라 "그 스킬을 가진 Agent가 실행될 때" 조직
        # 전체 Agent가 실제 외부 프로세스/네트워크에 접속하게 만드는
        # 조직 차원 정책이라(GitHub Copilot 원칙 1), native(웹서치 도구
        # 등)와 달리 admin 전용으로 좁힌다. 엔드포인트 전체를 admin
        # 전용으로 만들지 않고 이 두 kind만 막는 이유는 native 도구
        # 등록(운영 업무 성격)까지 매니저에게서 뺏을 이유가 없어서다.
        #
        # 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는
        # 기능") — `custom_python`은 등록된 Python 소스가 실행되는 순간
        # 서버 프로세스와 같은 신뢰 수준으로 동작한다(`pool_registry.
        # _run_custom_python_tool()` 참고) — mcp_stdio와 같은 등급의
        # 위험이라 같은 이유로 admin 전용으로 묶는다.
        if (
            payload.kind in ("mcp_stdio", "mcp_remote", "custom_python")
            and user["role"] != Role.ADMIN.value
        ):
            raise HTTPException(
                status_code=403,
                detail="MCP·커스텀 Python 도구(mcp_stdio/mcp_remote/custom_python) 등록은 admin 전용입니다",
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

    @app.patch("/api/tool-pool/{tool_id}")
    def update_tool_pool_entry(
        tool_id: int,
        payload: ToolPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을
        추가해줘") — 지금까지 등록 뒤엔 삭제·재등록만 가능했다. `kind`·
        `name`은 바꿀 수 없다(`pool_registry.update_tool()` 참고 — name은
        Agent/Skill 연결이 참조하는 문자열이라 바꾸면 그 연결이 조용히
        끊어진다). 등록(POST)과 같은 이유로 mcp_stdio/mcp_remote/
        custom_python 종류는 admin 전용으로 좁힌다 — 수정 경로로 그
        제한을 우회할 수 없어야 한다."""
        row = conn.execute(
            "SELECT kind FROM tool_pool WHERE id = ?", (tool_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다")
        if (
            row["kind"] in ("mcp_stdio", "mcp_remote", "custom_python")
            and user["role"] != Role.ADMIN.value
        ):
            raise HTTPException(
                status_code=403,
                detail="MCP·커스텀 Python 도구(mcp_stdio/mcp_remote/custom_python) 수정은 admin 전용입니다",
            )
        try:
            pool_registry.update_tool(
                conn, user["id"], tool_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            detail = {"tool_not_found": "도구를 찾을 수 없습니다"}.get(
                str(exc), str(exc)
            )
            raise HTTPException(status_code=400, detail=detail) from exc
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
        """`skill_tags`를 정식 엔티티로 승격한 Skill Pool
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

    @app.patch("/api/skill-pool/{skill_id}")
    def update_skill_pool_entry(
        skill_id: int,
        payload: SkillPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을
        추가해줘") — `name`은 바꿀 수 없다(`pool_registry.update_skill()`
        참고)."""
        try:
            pool_registry.update_skill(
                conn, user["id"], skill_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "skill_not_found":
                raise HTTPException(
                    status_code=404, detail="스킬을 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    # (skill_pool/tool_pool과 같은 "등록→선택" 패턴).

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

    @app.patch("/api/teams/{team_id}")
    def update_team_defaults_endpoint(
        team_id: int,
        payload: TeamDefaultsUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "고급 에이전트 관리") — 이 팀의
        기본 process/매니저를 저장한다. `teams.update_team_defaults()`
        참고."""
        try:
            saved = teams.update_team_defaults(
                conn, user["id"], team_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from exc
            detail = {
                "manager_must_be_active_agent_member_of_team": "지정한 매니저가 이 팀의 온보딩(active)된 Agent 멤버가 아닙니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": team_id, **saved}

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

    @app.patch("/api/teams/{team_id}/members/order")
    def reorder_team_members_endpoint(
        team_id: int,
        payload: TeamMemberReorder,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 팀
        관리 화면의 Drawflow 캔버스에서 노드를 드래그해 순서를 바꾸면
        호출된다. `teams.reorder_team_members()` 참고."""
        try:
            teams.reorder_team_members(
                conn, user["id"], team_id, payload.member_row_ids
            )
        except ValueError as exc:
            if str(exc) == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from exc
            detail = {
                "member_row_ids_mismatch": "이 팀의 현재 Agent 멤버 전체와 정확히 일치하지 않습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return teams.get_team(conn, team_id)

    # ---------- 파이프라인 빌더(사용자 요청 2026-10-18) ----------

    @app.get("/api/pipeline-templates")
    def list_pipeline_templates_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return pipeline_runtime.list_pipeline_templates(conn)

    @app.get("/api/pipeline-templates/{template_id}")
    def get_pipeline_template_endpoint(
        template_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        template = pipeline_runtime.get_pipeline_template(conn, template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="파이프라인 템플릿을 찾을 수 없습니다")
        return template

    @app.post("/api/pipeline-templates", status_code=201)
    def create_pipeline_template_endpoint(
        payload: PipelineTemplateCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            template_id = pipeline_runtime.create_pipeline_template(
                conn, user["id"], payload.name, payload.description, payload.domain
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": template_id, "name": payload.name}

    @app.patch("/api/pipeline-templates/{template_id}/active")
    def set_pipeline_template_active_endpoint(
        template_id: int,
        payload: PipelineTemplateActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.set_pipeline_template_active(
                conn, user["id"], template_id, payload.is_active
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="파이프라인 템플릿을 찾을 수 없습니다"
            ) from exc
        return {"id": template_id, "is_active": payload.is_active}

    @app.delete("/api/pipeline-templates/{template_id}")
    def delete_pipeline_template_endpoint(
        template_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            pipeline_runtime.delete_pipeline_template(conn, user["id"], template_id)
        except ValueError as exc:
            detail = (
                "실행 이력이 있는 파이프라인 템플릿은 삭제할 수 없습니다"
                if str(exc) == "pipeline_template_has_run_history"
                else "파이프라인 템플릿을 찾을 수 없습니다"
            )
            status_code = 409 if str(exc) == "pipeline_template_has_run_history" else 404
            raise HTTPException(status_code=status_code, detail=detail) from exc
        return {"id": template_id, "deleted": True}

    @app.post("/api/pipeline-templates/{template_id}/stages", status_code=201)
    def add_pipeline_stage_endpoint(
        template_id: int,
        payload: PipelineStageCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            stage_id = pipeline_runtime.add_stage(
                conn,
                user["id"],
                template_id,
                payload.name,
                payload.mode,
                [(a.role_name, a.instructions) for a in payload.agents],
                lead_role_name=payload.lead_role_name,
                instructions=payload.instructions,
                gate_ids=payload.gate_ids,
                min_gate_pass_ratio=payload.min_gate_pass_ratio,
                max_retry=payload.max_retry,
                requires_human_approval=payload.requires_human_approval,
                is_primary_deliverable=payload.is_primary_deliverable,
                expected_output=payload.expected_output,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

    @app.patch("/api/pipeline-templates/{template_id}/stages/{stage_id}")
    def update_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        payload: PipelineStageUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.update_stage(
                conn, user["id"], stage_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

    @app.delete("/api/pipeline-templates/{template_id}/stages/{stage_id}")
    def delete_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.delete_stage(conn, user["id"], stage_id)
        except ValueError as exc:
            detail = (
                "실행 이력이 있는 단계는 삭제할 수 없습니다"
                if str(exc) == "pipeline_stage_has_run_history"
                else "단계를 찾을 수 없습니다"
            )
            status_code = 409 if str(exc) == "pipeline_stage_has_run_history" else 404
            raise HTTPException(status_code=status_code, detail=detail) from exc
        return {"id": stage_id, "deleted": True}

    @app.post("/api/pipeline-templates/{template_id}/stages/{stage_id}/reorder")
    def reorder_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        payload: PipelineStageReorder,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.reorder_stage(conn, user["id"], stage_id, payload.direction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

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

    @app.post("/api/assignments/{assignment_id}/cancel")
    def cancel_assignment_endpoint(
        assignment_id: int,
        payload: AssignmentCancelRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 보고(2026-10-19, "업무 요청 삭제가 확정된 배정 때문에
        막히는데 정리할 방법이 없다") — 결재 체인 없이(이미 확정됐거나,
        아직 결재 중인 배정 자체를 되돌리는 것뿐이라 새 승인이 필요
        없다) 즉시 취소한다. `assignments.cancel_assignment()` 참고."""
        try:
            result = assignments.cancel_assignment(
                conn, user["id"], assignment_id, payload.reason
            )
        except ValueError as exc:
            detail = {
                "assignment_not_found": "배정을 찾을 수 없습니다",
                "assignment_not_cancellable": "이미 종료된 배정(반려/대체/취소됨)은 다시 취소할 수 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return result

    @app.post("/api/agent-pool/{agent_id}/tools/{tool_pool_id}", status_code=201)
    def link_agent_tool_endpoint(
        agent_id: int,
        tool_pool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Agent별로 skill 매칭과 무관하게 Tool Pool 항목을 하나 더 붙인다
        — 다음 `run_crew()`/`crewai_agent_generator()` 실행
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
        """여러 온보딩된 Agent를 CrewAI Crew로 묶어 실제 협업
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
                memory=payload.memory,
                planning=payload.planning,
                manager_provider=payload.manager_provider,
                manager_model=payload.manager_model,
                parallel_agent_ids=payload.parallel_agent_ids,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"온보딩(active)된 Agent만 참여할 수 있습니다: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.get("/api/crew-runs")
    def list_crew_runs(
        limit: int = 50,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 실행 이력") — "Crew
        빌더"의 "실행 이력" 탭이 Task를 가로질러 조회한다(admin/manager
        전용 — 이 목록이 모든 Task의 실행을 한 번에 보여주므로 개별
        Task 참가자 권한 범위를 넘는다)."""
        return crew_history.list_recent_runs(conn, limit=limit)

    @app.get("/api/crew-runs/{run_id}")
    def get_crew_run(
        run_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """단건 상세 — 협업 다이어그램·토큰 사용량·Gate 점수까지 포함된
        전체 데이터(`crewDiagramSvg()`/`renderCrewResult()`가 그대로
        쓸 수 있는 모양). 권한은 그 Crew 실행이 속한 Task의 참가자
        판정과 동일(파이프라인 실행 이력 조회와 같은 기준)."""
        run = crew_history.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Crew 실행 이력을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(
                conn, run["task_id"], user["id"]
            )
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return run

    @app.post("/api/crew-runs/{run_id}/retry-remaining", status_code=202)
    def retry_crew_run_remaining(
        run_id: int,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-20, "실패 시 일부 재시도" — Crew 빌더
        로드맵 5단계) — 실패한 Crew 실행에서 이미 완료된 Agent는
        제외하고 남은 Agent만으로 새 즉석 Crew 실행을 시작한다. 권한은
        `get_crew_run`과 동일(참가자 또는 admin/manager)."""
        run = crew_history.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Crew 실행 이력을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(
                conn, run["task_id"], user["id"]
            )
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            retry = deliverables.start_crew_run_retry(
                conn,
                run_id,
                user["id"],
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
                "run_not_found": "Crew 실행 이력을 찾을 수 없습니다",
                "run_not_failed": "실패한 실행만 재시도할 수 있습니다",
                "nothing_to_retry": "재시도할 Agent가 남아있지 않습니다(이미 전원 완료됨)",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        background_tasks.add_task(
            deliverables.run_adhoc_crew_job,
            conn,
            retry["job_by_agent_id"],
            retry["task_id"],
            user["id"],
            retry["agent_ids"],
            retry["task_descriptions"],
            "sequential",
            None,
            None,
            None,
        )
        return {"job_ids": list(retry["job_by_agent_id"].values()), "status": "running"}

    @app.post("/api/agent-pool/{agent_id}/onboard")
    def onboard_agent(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """Agent 온보딩 승인(Admin 전용). 승인 후 검색에 노출된다."""
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
        """Agent 오프보딩(Admin 전용). 이후 검색에서 제외된다."""
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

    @app.post("/api/demo/seed", status_code=201)
    def seed_demo_data_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """2026-10-08, Phase 5 — "샘플로
        체험하기". 기존 데이터 삭제 기능과 대칭되는 admin 전용 생성
        기능이다. 몇 번을 눌러도 매번 새 샘플 Brief/Task/Human/Agent가
        추가로 생긴다(중복 방지 로직 없음 — "초기화"가 아니라 "체험용
        데이터 추가"가 목적이라 멱등일 필요가 없다)."""
        return data_admin.seed_demo_data(conn, user["id"])

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
        """사전 동의(opt-in). 본인 또는 Admin만 설정할 수 있다."""
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
                detail="본인 또는 Admin만 동의를 설정할 수 있습니다",
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
        """성과 점수 입력. 동의 없이는 거부된다.

        사용자 결정(2026-09-29,
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
                detail="동의 없이는 성과데이터를 수집할 수 없습니다",
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
        """Agent 성과 점수(사람이 아니므로 동의 불필요)."""
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
        """성과리뷰 조회. Admin·매니저는 전체, 팀원은 본인 관련만(RBAC 매트릭스)."""
        if user["role"] == Role.MEMBER.value:
            return performance.review_for_member(conn, user["id"])
        return performance.full_review(conn)

    @app.post("/api/agent-pool/{agent_id}/cost")
    def set_agent_cost(
        agent_id: int,
        payload: CostUpdateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """Agent 비용 수동 입력(1차 범위)."""
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

    @app.patch("/api/agent-pool/{agent_id}/name")
    def set_agent_name(
        agent_id: int,
        payload: AgentNameUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
        (채팅 포함)에도 활용될 수 있도록 개선") — `role_name`은 생성
        당시 역할이라 같은 역할의 Agent가 여럿이면 구분이 안 됐다.
        비용/성과점수(admin 전용)와 달리 이건 human_pool의 역할·스킬
        수정과 같은 "정상 운영 업무"라 admin+manager 둘 다 허용한다
        (사람 인력 쪽과의 권한 비대칭 방지). 빈 문자열/공백은 이름
        지우기(다시 role_name 표시로 복귀)로 정규화한다."""
        row = conn.execute(
            "SELECT id FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        name = (payload.name.strip() if payload.name else "") or None
        conn.execute(
            "UPDATE agent_pool SET name = ? WHERE id = ?", (name, agent_id)
        )
        audit.record(
            conn,
            user["id"],
            "agent_name_updated",
            "agent_pool",
            agent_id,
            after={"name": name},
        )
        conn.commit()
        return {"id": agent_id, "name": name}

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
        """
        rows = conn.execute("""
            SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
                   a.requested_by, t.role_name,
                   hp.name AS human_name, hp.manager_id AS human_manager_id,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name
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
            rows = conn.execute(
                "SELECT * FROM approvals WHERE requested_by = ? ORDER BY id DESC",
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
            pipeline_run_id = None
            next_step_pending = False
            if payload.approve:
                next_step_pending = approvals_mod.activate_next_step(
                    conn, subject_type, subject_id, approval["step"]
                )
                if not next_step_pending and subject_type == "assignment":
                    # 체인의 마지막 단계 통과 시에만 최종 확정한다.
                    assignment_result = assignments.resolve_assignment_decision(
                        conn, subject_id, True, user["id"]
                    )
                elif not next_step_pending and subject_type == "deliverable":
                    # Phase 2 — 체인의 마지막 단계 통과 시에만 산출물을 확정한다
                    # (assignment와 같은 원리 — deliverables.py docstring
                    # "Phase 2 설계" 참고).
                    deliverable_result = deliverables.resolve_deliverable_decision(
                        conn, subject_id, True
                    )
                elif not next_step_pending and subject_type == "pipeline_run_stage":
                    # 사용자 요청(2026-10-18, "최소 HITL/HOTL") — 계획
                    # 확정 체크포인트 또는 반복 실패 에스컬레이션을 사람이
                    # 승인하면, 파이프라인이 다음 단계로 이어서 진행된다
                    # (LLM 호출이 들어가므로 여기선 DB 상태만 바꾸고
                    # BackgroundTasks로 이어서 돌린다).
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
                elif subject_type == "pipeline_run_stage":
                    # 반려 = "다시 해와"(human-approval-loop 스킬 안내와
                    # 일치) — 재시도 여력이 남아 있으면 파이프라인은
                    # 'running'으로 돌아가 같은 단계를 재시도한다.
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
        if pipeline_run_id is not None:
            run_status = conn.execute(
                "SELECT status FROM pipeline_runs WHERE id = ?", (pipeline_run_id,)
            ).fetchone()["status"]
            if run_status == "running":
                background_tasks.add_task(
                    pipeline_runtime.run_pipeline_stage_job,
                    conn,
                    pipeline_run_id,
                    user["id"],
                )
        return {
            "id": approval_id,
            "status": new_status,
            "next_step_pending": next_step_pending,
            "assignment": assignment_result,
            "deliverable": deliverable_result,
            "pipeline_run_id": pipeline_run_id,
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        """Web UI 셸(코드 재점검 R4). 위 JSON API를 그대로 호출하는 단일
        페이지 바닐라 JS 앱이다 — 새 비즈니스 로직은 없다(webui.py)."""
        return webui.INDEX_HTML

    return app
