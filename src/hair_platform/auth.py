"""Auth + RBAC — ADR 결정 3(권한 매트릭스)·3-A(self-approval 방지)·6(세션 기반 인증).

manager_id(직속상사)와 RBAC role은 별개 개념이다(SPEC.md §10) — 이 모듈은 role만
다루고, manager_id는 human_pool/users 테이블의 컬럼으로만 저장된다.
"""

from __future__ import annotations

import sqlite3
from enum import Enum

import bcrypt
from fastapi import Depends, HTTPException, Request


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: Role,
    manager_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, manager_id) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), role.value, manager_id),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def authenticate(
    conn: sqlite3.Connection, username: str, password: str
) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    if not row["is_active"]:
        # 사용자 요청(2026-09-29, "사용자 삭제 기능 필요") — 비활성화된
        # 계정은 비밀번호가 맞아도 로그인을 막는다. 계정 존재 여부를
        # 굳이 알려주지 않도록 "아이디/비밀번호 불일치"와 같은 취급(None).
        return None
    return row


def get_current_user(request: Request) -> sqlite3.Row:
    session_user = request.session.get("user")
    if session_user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    conn: sqlite3.Connection = request.app.state.db
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (session_user["id"],)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    if not row["is_active"]:
        # 로그인 시점만 막으면 이미 로그인한 세션이 비활성화 이후에도
        # 계속 유효해 버린다 — 매 요청마다 다시 확인해 즉시 차단한다.
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    return row


def require_role(*roles: Role):
    """지정된 role만 통과시키는 FastAPI 의존성 팩토리(ADR 결정 3 권한 매트릭스)."""
    allowed = {r.value for r in roles}

    def _dependency(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
        if user["role"] not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"{user['role']} role은 이 작업을 수행할 수 없습니다(ADR 결정 3)",
            )
        return user

    return _dependency
