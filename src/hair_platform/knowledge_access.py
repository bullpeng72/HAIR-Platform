"""지식 베이스 문서별 공개 범위(2026-11-25, P1-5 — "지식 베이스에 문서별 접근 권한이 없음").

예전에는 지식 베이스에 등록된 자료가 누구의 Agent 실행에서든 검색됐다 — 부서 기밀 자료를 올릴 수 없었다.
문서(`documents.visibility`)마다 공개 범위를 두고, 검색할 때 **요청한 사람** 기준으로 거른다.

공개 범위(문자열):
- `all` — 모든 사람(기본값. 예전 데이터·Agent 결과물에서 나온 청크도 모두 이것 — 동작이 바뀌지 않는다)
- `manager` — manager 이상(manager·admin)
- `admin` — admin만
- `team:<팀 id>` — 그 팀의 사람 구성원(로그인 계정이 연결된 사람 인력)과 admin

**요청한 사람** — 검색 함수 4곳(단일 Agent·대화 기본 AI·Crew 보강·도구 호출)은 사용자 정보를 받지 않는다. 대신 작업
입구(`agent_runtime.execute_agent`·`crew_runtime.run_crew`·`conversations.run_default_ai_job`)에서 `as_viewer(user_id)`로
요청 문맥에 한 번 남긴다(파이프라인도 이 경로를 탄다). **모르면(문맥 밖 — 다른 스레드의 도구 실행 등) 전체 공개
자료만** 돌려준다(닫힌 쪽으로 실패). admin은 늘 전부 본다.
"""

from __future__ import annotations

import contextlib
import contextvars
import sqlite3
from collections.abc import Iterator

ALL = "all"
LEVELS = ("all", "manager", "admin")  # + "team:<id>"
_ROLE_RANK = {"viewer": 0, "member": 1, "manager": 2, "admin": 3}

_VIEWER: contextvars.ContextVar[int | None] = contextvars.ContextVar("knowledge_viewer", default=None)


def ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    if "visibility" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN visibility TEXT NOT NULL DEFAULT 'all'")


@contextlib.contextmanager
def as_viewer(user_id: int | None) -> Iterator[None]:
    """이 블록 안의 지식 검색은 `user_id` 기준으로 공개 범위를 거른다. 이미 정해져 있으면(바깥 작업) 그대로 둔다."""
    if user_id is None or _VIEWER.get() is not None:
        yield
        return
    token = _VIEWER.set(user_id)
    try:
        yield
    finally:
        _VIEWER.reset(token)


def current_viewer() -> int | None:
    return _VIEWER.get()


def validate(visibility: str) -> str:
    v = (visibility or "").strip()
    if v in LEVELS:
        return v
    if v.startswith("team:") and v[5:].isdigit():
        return v
    raise ValueError("공개 범위는 all·manager·admin·team:<팀 id> 중 하나여야 합니다")


def _viewer_profile(conn: sqlite3.Connection, user_id: int | None) -> tuple[int, set[int]]:
    """(역할 등급, 소속 팀 id들). 모르는 사용자면 (-1, 빈 집합) — 전체 공개만."""
    if user_id is None:
        return -1, set()
    row = conn.execute("SELECT role FROM users WHERE id = ? AND COALESCE(is_active, 1) = 1", (user_id,)).fetchone()
    if row is None:
        return -1, set()
    teams = {
        r[0] for r in conn.execute(
            "SELECT tm.team_id FROM team_members tm JOIN human_pool hp ON hp.id = tm.member_id "
            "WHERE tm.member_type = 'human' AND hp.user_id = ?",
            (user_id,),
        )
    }
    return _ROLE_RANK.get(row[0], -1), teams


def can_see(visibility: str | None, rank: int, teams: set[int]) -> bool:
    v = visibility or ALL
    if v == ALL:
        return True
    if rank >= _ROLE_RANK["admin"]:
        return True
    if v == "manager":
        return rank >= _ROLE_RANK["manager"]
    if v.startswith("team:"):
        try:
            return int(v[5:]) in teams
        except ValueError:
            return False
    return False  # "admin" 또는 알 수 없는 값 — 닫힌 쪽


def filter_rows(conn: sqlite3.Connection, rows: list, user_id: int | None = None) -> list:
    """`knowledge_chunks` 행(`source_document_id` 포함) 중 요청한 사람이 볼 수 있는 것만. 문서 없는 청크(Agent 결과물)는 전체 공개."""
    doc_ids = sorted({r["source_document_id"] for r in rows if r["source_document_id"] is not None})
    if not doc_ids:
        return list(rows)
    placeholders = ",".join("?" * len(doc_ids))
    visibility = {
        r[0]: r[1] for r in conn.execute(
            f"SELECT id, visibility FROM documents WHERE id IN ({placeholders})", doc_ids
        )
    }
    if all((visibility.get(d) or ALL) == ALL for d in doc_ids):
        return list(rows)
    rank, teams = _viewer_profile(conn, user_id if user_id is not None else current_viewer())
    return [
        r for r in rows
        if r["source_document_id"] is None or can_see(visibility.get(r["source_document_id"]), rank, teams)
    ]


def set_visibility(conn: sqlite3.Connection, actor_id: int, document_id: int, visibility: str) -> dict:
    """문서 공개 범위를 바꾼다(감사 기록). 팀 범위면 팀이 있는지 확인한다."""
    from hair_platform import audit

    v = validate(visibility)
    row = conn.execute("SELECT id, visibility, filename FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise ValueError("문서를 찾을 수 없습니다")
    if v.startswith("team:") and conn.execute("SELECT 1 FROM teams WHERE id = ?", (int(v[5:]),)).fetchone() is None:
        raise ValueError("없는 팀입니다")
    conn.execute("UPDATE documents SET visibility = ? WHERE id = ?", (v, document_id))
    audit.record(conn, actor_id, "knowledge_document_visibility_changed", "documents", document_id,
                 before={"visibility": row["visibility"]}, after={"visibility": v})
    conn.commit()
    return {"id": document_id, "filename": row["filename"], "visibility": v}


def viewer_from_actor(func):
    """작업 입구 함수용 데코레이터 — 인자 `actor_id`(요청한 사람)를 이 호출 동안의 지식 검색 요청자로 정한다.
    (`execute_agent`·`run_crew`·`run_default_ai_job` — 함수 본문은 건드리지 않는다.)"""
    import functools
    import inspect

    signature = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            actor_id = signature.bind_partial(*args, **kwargs).arguments.get("actor_id")
        except TypeError:
            actor_id = None
        with as_viewer(actor_id if isinstance(actor_id, int) else None):
            return func(*args, **kwargs)

    return wrapper
