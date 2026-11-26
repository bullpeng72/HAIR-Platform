"""사내 DB 조회 연동(2026-11-26, P1-9 — "외부 업무 시스템 연결 부족: DB 조회 커넥터 없음").

admin이 업무 DB 연결을 등록하면, "사내DB조회" 스킬을 가진 Agent가 `database_query` 도구로 **읽기 전용** 조회를 한다
(대화·파이프라인·Crew 모두 — 다른 네이티브 도구와 같은 경로). 코드 저장소 조회(`code_repos.py`)와 같은 형태다.

**지원 DB** — SQLite(파일, 추가 설치 없음) · PostgreSQL · MySQL(선택 설치 `pip install "hair-platform[dbconnect]"` —
psycopg·pymysql. 드라이버가 없으면 그 사실을 알린다).

**읽기 전용을 두 겹으로 지킨다**
1. SQL 검사: 문장 하나, `SELECT`·`WITH`·`EXPLAIN`으로 시작, 쓰기·DDL·권한·파일 접근 키워드 거부(주석 제거 후).
2. 연결 자체를 읽기 전용으로: SQLite `mode=ro` + `PRAGMA query_only`, PostgreSQL 읽기 전용 세션 + `statement_timeout`,
   MySQL `START TRANSACTION READ ONLY` + 실행 시간 제한. 검사가 뚫려도 DB가 쓰기를 거부한다.

**제한** — 행 수 상한(연결별, 기본 200 — 넘으면 잘렸다고 표시), 실행 시간 상한(기본 10초), 셀 200자.
**비밀번호는 저장하지 않는다** — 연결 정보에는 비밀번호를 담은 **환경변수 이름**만 둔다(`password_env`).
**기록** — 조회마다 요청자(작업 문맥 `knowledge_access.current_viewer()`)·연결·SQL·행 수를 활동 기록에 남긴다.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from typing import Any

from hair_platform import audit

NATIVE_ID = "database_query"
KINDS = ("sqlite", "postgresql", "mysql")
DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 2000
TIMEOUT_SECONDS = 10
MAX_CELL_CHARS = 200
ACTIONS = {
    "connections": "등록된 DB 연결 목록",
    "tables": "테이블 목록(connection 필요 — 하나뿐이면 생략 가능)",
    "describe": "테이블 열 구조(table 필수)",
    "query": "읽기 전용 SQL 실행(sql 필수 — SELECT/WITH/EXPLAIN 한 문장)",
}

_ALLOWED_START = re.compile(r"^\s*(select|with|explain)\b", re.I)
_FORBIDDEN = re.compile(
    # REPLACE()·SET 같은 이름은 함수·열 이름으로도 쓰여 막지 않는다(문장 시작은 SELECT/WITH/EXPLAIN만 허용하고 연결이
    # 읽기 전용이라 쓰기는 DB가 거부한다). 데이터 변경 CTE(WITH … DELETE)·SELECT INTO·파일 쓰기는 막는다.
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|attach|detach|pragma|"
    r"vacuum|reindex|copy|call|exec|execute|load_file|outfile|dumpfile|into)\b",
    re.I,
)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class ConnectorError(ValueError):
    """사람(또는 Agent)에게 그대로 보여 줄 수 있는 사유."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_connections ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL UNIQUE,"
        " kind TEXT NOT NULL,"
        " target TEXT NOT NULL,"           # sqlite: 파일 경로 / 그 외: host:port/dbname
        " username TEXT,"
        " password_env TEXT,"              # 비밀번호가 든 환경변수 이름(값은 저장하지 않음)
        " description TEXT NOT NULL DEFAULT '',"
        " row_limit INTEGER NOT NULL DEFAULT 200,"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


# ------------------------------------------------------------------ 등록·관리(admin)

def _validate(kind: str, name: str, target: str, row_limit: int) -> None:
    if kind not in KINDS:
        raise ConnectorError(f"지원하지 않는 DB 종류입니다(가능: {', '.join(KINDS)})")
    if not re.fullmatch(r"[\w가-힣\-]{1,60}", name or ""):
        raise ConnectorError("연결 이름은 1~60자 한글·영문·숫자·_-만 쓸 수 있습니다")
    if not (target or "").strip():
        raise ConnectorError("대상(SQLite 파일 경로 또는 host:port/DB이름)을 입력하세요")
    if kind == "sqlite" and not os.path.isfile(os.path.expanduser(target)):
        raise ConnectorError("SQLite 파일을 찾을 수 없습니다")
    if not 1 <= int(row_limit) <= MAX_ROW_LIMIT:
        raise ConnectorError(f"행 수 상한은 1~{MAX_ROW_LIMIT} 사이여야 합니다")


def register(conn: sqlite3.Connection, actor_id: int, name: str, kind: str, target: str, *,
             username: str | None = None, password_env: str | None = None, description: str = "",
             row_limit: int = DEFAULT_ROW_LIMIT) -> dict:
    _validate(kind, name, target, row_limit)
    if password_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", password_env):
        raise ConnectorError("비밀번호 환경변수 이름은 대문자·숫자·_ 형식이어야 합니다(예: HAIR_DB_SALES_PASSWORD)")
    try:
        cur = conn.execute(
            "INSERT INTO data_connections (name, kind, target, username, password_env, description, row_limit, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, kind, target.strip(), username or None, password_env or None, description or "", int(row_limit), actor_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ConnectorError("같은 이름의 연결이 이미 있습니다") from exc
    audit.record(conn, actor_id, "data_connection_registered", "data_connections", cur.lastrowid,
                 after={"name": name, "kind": kind, "target": target, "username": username,
                        "password_env": password_env, "row_limit": row_limit})
    conn.commit()
    return get(conn, cur.lastrowid or 0)


def get(conn: sqlite3.Connection, connection_id: int) -> dict:
    row = conn.execute("SELECT * FROM data_connections WHERE id = ?", (connection_id,)).fetchone()
    if row is None:
        raise ConnectorError("연결을 찾을 수 없습니다")
    return _public(row)


def _public(row) -> dict:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    d["password_set"] = bool(d.get("password_env") and os.environ.get(d["password_env"]))
    return d


def list_connections(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM data_connections" + ("" if include_inactive else " WHERE is_active = 1") + " ORDER BY name"
    return [_public(r) for r in conn.execute(sql)]


def set_active(conn: sqlite3.Connection, actor_id: int, connection_id: int, active: bool) -> dict:
    get(conn, connection_id)
    conn.execute("UPDATE data_connections SET is_active = ? WHERE id = ?", (1 if active else 0, connection_id))
    audit.record(conn, actor_id, "data_connection_toggled", "data_connections", connection_id,
                 after={"is_active": bool(active)})
    conn.commit()
    return get(conn, connection_id)


# ------------------------------------------------------------------ SQL 검사·실행

def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def _strip_literals(sql: str) -> str:
    """문자열 리터럴 안의 낱말(예: WHERE memo = 'delete me')로 잘못 막지 않게 리터럴을 비운다."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def check_sql(sql: str) -> str:
    """읽기 전용 한 문장인지 — 통과하면 실행할 SQL(끝 세미콜론 제거)."""
    body = _strip_comments(sql or "").strip().rstrip(";").strip()
    if not body:
        raise ConnectorError("SQL이 비어 있습니다")
    bare = _strip_literals(body)
    if ";" in bare:
        raise ConnectorError("SQL은 한 문장만 실행할 수 있습니다")
    if not _ALLOWED_START.match(bare):
        raise ConnectorError("읽기 전용 조회(SELECT·WITH·EXPLAIN)만 실행할 수 있습니다")
    bad = _FORBIDDEN.search(bare)
    if bad:
        raise ConnectorError(f"읽기 전용 조회에서는 '{bad.group(1).upper()}'를 쓸 수 없습니다")
    return body


def _row(conn: sqlite3.Connection, name: str | None) -> sqlite3.Row:
    rows = conn.execute("SELECT * FROM data_connections WHERE is_active = 1 ORDER BY name").fetchall()
    if not rows:
        raise ConnectorError("등록된 DB 연결이 없습니다 — admin이 '연동 설정'에서 등록해야 합니다")
    if not name:
        if len(rows) == 1:
            return rows[0]
        raise ConnectorError("연결이 여러 개입니다 — connection에 이름을 지정하세요: " + ", ".join(r["name"] for r in rows))
    for r in rows:
        if r["name"] == name:
            return r
    raise ConnectorError(f"'{name}' 연결이 없거나 꺼져 있습니다")


def _open(row) -> Any:
    """읽기 전용으로 연다(드라이버별)."""
    kind = row["kind"]
    if kind == "sqlite":
        path = os.path.expanduser(row["target"])
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=TIMEOUT_SECONDS, check_same_thread=False)
        c.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        return c
    host, _, dbname = row["target"].partition("/")
    host, _, port = host.partition(":")
    password = os.environ.get(row["password_env"] or "", "") if row["password_env"] else ""
    if kind == "postgresql":
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError('PostgreSQL 드라이버가 없습니다 — pip install "hair-platform[dbconnect]"') from exc
        c = psycopg.connect(host=host, port=int(port or 5432), dbname=dbname, user=row["username"],
                            password=password, connect_timeout=TIMEOUT_SECONDS,
                            options=f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_SECONDS * 1000}")
        c.read_only = True
        return c
    if kind == "mysql":
        try:
            import pymysql  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError('MySQL 드라이버가 없습니다 — pip install "hair-platform[dbconnect]"') from exc
        c = pymysql.connect(host=host, port=int(port or 3306), database=dbname, user=row["username"],
                            password=password, connect_timeout=TIMEOUT_SECONDS, read_timeout=TIMEOUT_SECONDS)
        with c.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("START TRANSACTION READ ONLY")
        return c
    raise ConnectorError("지원하지 않는 DB 종류입니다")


def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"


def _table(columns: list[str], rows: list[tuple]) -> str:
    head = "| " + " | ".join(columns) + " |\n|" + "---|" * len(columns)
    body = "\n".join("| " + " | ".join(_cell(v) for v in r) + " |" for r in rows)
    return head + ("\n" + body if body else "")


def run_query(conn: sqlite3.Connection, sql: str, connection: str | None = None) -> dict:
    """읽기 전용 조회 — {"columns", "rows", "truncated", "connection"}. 활동 기록을 남긴다."""
    from hair_platform import knowledge_access

    row = _row(conn, connection)
    body = check_sql(sql)
    limit = int(row["row_limit"] or DEFAULT_ROW_LIMIT)
    db = _open(row)
    started = time.monotonic()
    try:
        cur = db.cursor()
        try:
            cur.execute(body)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise ConnectorError(f"조회가 {TIMEOUT_SECONDS}초를 넘어 중단했습니다 — 조건을 좁히세요") from exc
            raise ConnectorError(f"SQL 오류: {exc}") from exc
        columns = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(limit + 1)
    except ConnectorError:
        raise
    except Exception as exc:  # noqa: BLE001 — 드라이버별 오류를 사람이 읽을 사유로
        raise ConnectorError(f"조회 실패: {str(exc)[:300]}") from exc
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001, S110
            pass
    truncated = len(fetched) > limit
    rows = [tuple(r) for r in fetched[:limit]]
    actor = knowledge_access.current_viewer()
    if actor is not None:
        audit.record(conn, actor, "data_query_executed", "data_connections", row["id"],
                     after={"connection": row["name"], "sql": body[:2000], "rows": len(rows), "truncated": truncated,
                            "ms": int((time.monotonic() - started) * 1000)})
        conn.commit()
    return {"connection": row["name"], "columns": columns, "rows": rows, "truncated": truncated, "limit": limit}


def _tables_sql(kind: str) -> str:
    if kind == "sqlite":
        return "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    if kind == "postgresql":
        return ("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1")
    return "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY 1"


def _describe_sql(kind: str, table: str) -> str:
    if not _IDENT.match(table or ""):
        raise ConnectorError("테이블 이름 형식이 올바르지 않습니다")
    if kind == "sqlite":
        return f"SELECT name, type, \"notnull\", pk FROM pragma_table_info('{table}')"
    schema, _, name = table.rpartition(".")
    if kind == "postgresql":
        return ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                f"WHERE table_name = '{name}'" + (f" AND table_schema = '{schema}'" if schema else "") + " ORDER BY ordinal_position")
    return ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            f"WHERE table_schema = DATABASE() AND table_name = '{name}' ORDER BY ordinal_position")


def _meta(conn: sqlite3.Connection, connection: str | None, sql_for) -> str:
    """메타데이터 조회는 내부 SQL이라 검사 대상(사용자 SQL)이 아니다 — 같은 읽기 전용 연결로 실행."""
    row = _row(conn, connection)
    db = _open(row)
    try:
        cur = db.cursor()
        cur.execute(sql_for(row["kind"]))
        columns = [d[0] for d in (cur.description or [])]
        return f"[{row['name']}]\n" + _table(columns, [tuple(r) for r in cur.fetchmany(500)])
    except ConnectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"조회 실패: {str(exc)[:300]}") from exc
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001, S110
            pass


def list_tables(conn: sqlite3.Connection, connection: str | None = None) -> str:
    """테이블 목록(실패하면 `ConnectorError` — 연결 확인용)."""
    return _meta(conn, connection, _tables_sql)


def describe_connections(conn: sqlite3.Connection) -> str:
    rows = list_connections(conn)
    if not rows:
        return "등록된 DB 연결이 없습니다 — admin이 '연동 설정'에서 등록해야 합니다."
    return "\n".join(f"- {r['name']} ({r['kind']}, 최대 {r['row_limit']}행): {r['description'] or '설명 없음'}" for r in rows)


def run_action(conn: sqlite3.Connection, action: str, connection: str = "", table: str = "", sql: str = "") -> str:
    """도구 진입점 — 오류도 문자열로 돌려준다(Agent가 다음 호출을 고칠 수 있게)."""
    act = (action or "").strip().lower()
    try:
        if act in ("connections", "list", ""):
            return describe_connections(conn)
        if act in ("tables", "schema"):
            return _meta(conn, connection or None, _tables_sql)
        if act in ("describe", "columns"):
            return _meta(conn, connection or None, lambda kind: _describe_sql(kind, table))
        if act in ("query", "select", "sql"):
            r = run_query(conn, sql, connection or None)
            note = f"\n(상한 {r['limit']}행에서 잘림 — 조건·집계로 좁히세요)" if r["truncated"] else ""
            return f"[{r['connection']}] {len(r['rows'])}행\n" + _table(r["columns"], r["rows"]) + note
        return "알 수 없는 action입니다. 가능한 값: " + "; ".join(f"{k}={v}" for k, v in ACTIONS.items())
    except ConnectorError as exc:
        return f"DB 조회 실패: {exc}"


def as_crewai_tool(conn: sqlite3.Connection):
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    @tool("database_query")
    def database_query(action: str = "connections", connection: str = "", table: str = "", sql: str = "") -> str:
        """관리자가 등록한 사내 업무 DB를 읽기 전용으로 조회한다.
        action: connections(연결 목록) | tables(테이블 목록) | describe(열 구조 — table 필수) |
        query(SQL 실행 — sql 필수, SELECT/WITH/EXPLAIN 한 문장, 결과는 행 수 상한까지).
        connection은 연결 이름(하나뿐이면 생략 가능). 먼저 tables/describe로 구조를 확인한 뒤 필요한 열·조건·집계만
        조회하라. 결과의 수치는 그대로 인용하고 추측으로 보태지 마라."""
        return run_action(conn, action, connection, table, sql)

    return database_query
