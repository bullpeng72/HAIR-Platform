"""Auth + RBAC — 권한 매트릭스·self-approval 방지·세션 기반 인증.

manager_id(직속상사)와 RBAC role은 별개 개념이다 — 이 모듈은 role만
다루고, manager_id는 human_pool/users 테이블의 컬럼으로만 저장된다.
"""

from __future__ import annotations

import hashlib
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


def password_version(password_hash: str) -> str:
    """비밀번호가 바뀌면 달라지는 짧은 표지(2026-11-27, 사용성 평가 13번·개선 제안 5-3). 로그인 토큰·세션에 넣어 두고
    요청마다 비교한다 — 비밀번호를 바꾸거나 관리자가 재설정하면 그 전에 발급된 로그인이 모두 끊긴다(예전엔 14일 동안 유효)."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def create_session_token(secret_key: str, user_id: int, pv: str | None = None) -> str:
    payload: dict = {"id": user_id}
    if pv:
        payload["pv"] = pv
    return _serializer(secret_key).dumps(payload)


def _decode_session_payload(secret_key: str, token: str) -> dict | None:
    try:
        payload = _serializer(secret_key).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload if isinstance(payload, dict) else None


def decode_session_token(secret_key: str, token: str) -> int | None:
    payload = _decode_session_payload(secret_key, token)
    return payload.get("id") if payload else None


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
    employee_id: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, manager_id, employee_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, hash_password(password), role.value, manager_id, employee_id),
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
    pv: str | None = None  # 비밀번호 표지(API 키는 비밀번호와 무관 — 키를 폐기해 끊는다)
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[len("Bearer ") :].strip()
        from hair_platform import integrations  # 지연 import — 순환 방지

        if integrations.is_api_key(token):
            # 개인 API 키(2026-11-23, P1-5 — `integrations.py`) — 외부 시스템 호출용.
            # 키 주인의 계정·권한 그대로 동작한다(아래 is_active 확인도 똑같이 적용).
            user_id = integrations.user_id_for_api_key(request.app.state.db, token)
            if user_id is None:
                raise HTTPException(status_code=401, detail="API 키가 유효하지 않거나 폐기됐습니다")
        else:
            # 탭별 토큰(위 주석) — 있으면 쿠키 세션보다 우선한다. 같은 브라우저의
            # 다른 탭이 로그인/로그아웃해도 이 탭의 계정에는 영향이 없어야 한다.
            payload = _decode_session_payload(request.app.state.secret_key, token)
            if payload is None or payload.get("id") is None:
                raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
            user_id, pv = payload["id"], payload.get("pv")
    else:
        session_user = request.session.get("user")
        if session_user is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        user_id, pv = session_user["id"], session_user.get("pv")
    conn: sqlite3.Connection = request.app.state.db
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다")
    if pv is not None and pv != password_version(row["password_hash"]):
        # 비밀번호가 바뀐 뒤의 옛 로그인(2026-11-27). 표지가 없는 옛 토큰(이 기능 전 발급)은 만료(14일)까지 그대로 둔다.
        raise HTTPException(status_code=401, detail="비밀번호가 바뀌어 다시 로그인해야 합니다")
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
