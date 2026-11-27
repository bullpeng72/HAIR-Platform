"""사용자 관리 API(계정 생성·권한·상사 지정·비밀번호·사번·활성화) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)

from hair_platform import (
    audit,
)
from hair_platform.auth import (
    Role,
    create_session_token,
    create_user,
    get_current_user,
    hash_password,
    password_version,
    require_role,
    verify_password,
)
from hair_platform.models import (
    ManagerUpdate,
    UserCreate,
    UserEmployeeIdUpdate,
    MyPasswordChange,
    UserPasswordReset,
    UserStatusUpdate,
)

MIN_PASSWORD_LENGTH = 8  # 내 비밀번호 바꾸기(2026-11-27)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """사용자 관리 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
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
                payload.employee_id,
            )
        except sqlite3.IntegrityError as exc:
            # 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및
            # 표시되도록") — username UNIQUE 외에 employee_id UNIQUE도
            # 이제 이 INSERT에서 걸릴 수 있어, 실제로 어떤 제약이
            # 깨졌는지 구분해 메시지를 보여준다.
            if "employee_id" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail=f"이미 등록된 사번입니다: {payload.employee_id}",
                ) from exc
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

    @app.patch("/api/users/{user_id}/employee-id")
    def set_user_employee_id(
        user_id: int,
        payload: UserEmployeeIdUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및
        표시되도록 UI개선") — 등록 시점에 비워뒀거나 잘못 입력한 사번을
        나중에 바로잡는 전용 엔드포인트(ADMIN 전용, `.../manager`·
        `.../password`와 같은 "필드 하나당 PATCH 하나" 관례)."""
        target = conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        try:
            conn.execute(
                "UPDATE users SET employee_id = ? WHERE id = ?",
                (payload.employee_id, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"이미 등록된 사번입니다: {payload.employee_id}",
            ) from exc
        audit.record(conn, user["id"], "user_employee_id_updated", "user", user_id)
        conn.commit()
        return {"id": user_id, "employee_id": payload.employee_id}

    @app.post("/api/me/password")
    def change_my_password(
        payload: MyPasswordChange,
        request: Request,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """내 비밀번호 바꾸기(2026-11-27, 사용성 평가 13번) — 예전엔 관리자 재설정밖에 없었다. 바꾸면 다른 곳(다른 브라우저·
        탭)의 로그인은 모두 끊기고, 지금 이 탭은 새 토큰으로 이어진다(`auth.password_version`)."""
        if not verify_password(payload.current_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="지금 비밀번호가 맞지 않습니다")
        new = payload.new_password or ""
        if len(new) < MIN_PASSWORD_LENGTH:
            raise HTTPException(status_code=400, detail=f"새 비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")
        if new == payload.current_password:
            raise HTTPException(status_code=400, detail="지금 비밀번호와 다른 비밀번호를 쓰세요")
        new_hash = hash_password(new)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
        audit.record(conn, user["id"], "user_password_changed", "user", user["id"])
        conn.commit()
        pv = password_version(new_hash)
        if request.session.get("user"):
            request.session["user"] = {**request.session["user"], "pv": pv}
        return {"status": "password_changed", "token": create_session_token(request.app.state.secret_key, user["id"], pv)}

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
        from hair_platform import login_guard  # 재설정하면 그 계정의 로그인 잠금도 푼다(2026-11-27, 5-2)

        name = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if name is not None:
            login_guard.unlock(name["username"])
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
            "SELECT id, username, role, manager_id, is_active, employee_id "
            "FROM users ORDER BY id"
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
