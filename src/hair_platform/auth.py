"""Auth + RBAC — 권한 매트릭스·self-approval 방지·세션 기반 인증.

manager_id(직속상사)와 RBAC role은 별개 개념이다 — 이 모듈은 role만
다루고, manager_id는 human_pool/users 테이블의 컬럼으로만 저장된다.
"""

from __future__ import annotations

import sqlite3
from enum import Enum

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# 사용자 요청(2026-10-12, "하나의 브라우저에서 두 개의 계정을 동시에 로그인") —
# 기존 SessionMiddleware 쿠키는 브라우저 전체(모든 탭)가 공유하므로 탭 A에서
# 로그인한 뒤 탭 B에서 다른 계정으로 로그인하면 탭 A도 그 계정으로 바뀐다.
# 탭마다 독립된 sessionStorage에 담을 수 있는 토큰을 추가로 발급해
# "Authorization: Bearer <token>" 헤더로 보내면 탭별로 다른 계정을 유지할
# 수 있다. 쿠키 기반 경로는 기존 테스트/API 클라이언트 호환을 위해 그대로 둔다.
TOKEN_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # SessionMiddleware 기본 max_age(14일)와 동일


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="hair-platform-session-token")


def create_session_token(secret_key: str, user_id: int) -> str:
    return _serializer(secret_key).dumps({"id": user_id})


def decode_session_token(secret_key: str, token: str) -> int | None:
    try:
        payload = _serializer(secret_key).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload.get("id")


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
    user_id = None
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        # 탭별 토큰(위 주석) — 있으면 쿠키 세션보다 우선한다. 같은 브라우저의
        # 다른 탭이 로그인/로그아웃해도 이 탭의 계정에는 영향이 없어야 한다.
        user_id = decode_session_token(
            request.app.state.secret_key, auth_header[len("Bearer ") :]
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    else:
        session_user = request.session.get("user")
        if session_user is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        user_id = session_user["id"]
    conn: sqlite3.Connection = request.app.state.db
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    if not row["is_active"]:
        # 로그인 시점만 막으면 이미 로그인한 세션이 비활성화 이후에도
        # 계속 유효해 버린다 — 매 요청마다 다시 확인해 즉시 차단한다.
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    return row


def require_role(*roles: Role):
    """지정된 role만 통과시키는 FastAPI 의존성 팩토리(권한 매트릭스)."""
    allowed = {r.value for r in roles}

    def _dependency(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
        if user["role"] not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"{user['role']} role은 이 작업을 수행할 수 없습니다",
            )
        return user

    return _dependency
