"""SQLite 연결 및 스키마 — v4(전체) 범위까지 다룬다.

ADR 결정 5: 실시간 쓰기가 빈번한 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 쓴다.
ADR 결정 3: `approvals.step`·`approver_id`로 F7 다단계 결재라인(조직도 매니저 관계
기반 자동 라우팅)을 표현한다. NF10: `audit_log`는 트리거로 UPDATE/DELETE를 막아
append-only를 DB 레벨에서 강제한다. NF9: `human_pool.performance_consent`가
False이면 `performance_score`를 절대 채우지 않는다(F8, 사전 동의 기반 최소수집).

ADR 결정 10/11(2026-09-26): `agent_pool.framework`가 어떤 생성 경로(stub/
simple_llm/crewai)로 만들어졌는지 기록한다. `knowledge_chunks`는 지식검색
Agent(VectorDB)의 임베딩 저장소다 — 결정 11에서 정한 대로 별도 벡터DB
서버 없이 SQLite에 임베딩을 JSON으로 저장하고, 검색은 애플리케이션에서
코사인 유사도로 계산한다(로컬/온프레미스 우선 원칙). 관계분석 Agent
(GraphDB)는 새 테이블이 필요 없다 — 이미 있는 `users.manager_id`·
`assignments`·`human_pool`을 그래프로 취급해 조회한다(`graphquery.py`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def get_connection(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    manager_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS human_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    skill_tags TEXT NOT NULL DEFAULT '[]',
    availability TEXT NOT NULL DEFAULT 'available',
    cost_estimate REAL NOT NULL DEFAULT 0.0,
    manager_id INTEGER REFERENCES users(id),
    user_id INTEGER REFERENCES users(id),
    performance_consent INTEGER NOT NULL DEFAULT 0,
    performance_score REAL
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    raw_text TEXT NOT NULL,
    stub_roles TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'submitted'
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    role_name TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    headcount INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    step INTEGER NOT NULL DEFAULT 1,
    approver_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    resource_type TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'requested',
    approval_id INTEGER REFERENCES approvals(id)
);

CREATE TABLE IF NOT EXISTS assignment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_by INTEGER NOT NULL REFERENCES users(id),
    reason TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_by INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name TEXT NOT NULL,
    skill_tags TEXT NOT NULL DEFAULT '[]',
    cost_estimate REAL NOT NULL DEFAULT 0.0,
    performance_score REAL,
    status TEXT NOT NULL DEFAULT 'pending_onboarding',
    source_job_item_id INTEGER REFERENCES job_items(id),
    llm_provider TEXT,
    llm_model TEXT,
    last_output TEXT,
    framework TEXT NOT NULL DEFAULT 'stub',
    mcp_servers TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id INTEGER REFERENCES agent_pool(id),
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native')),
    config TEXT NOT NULL DEFAULT '{}',
    tool_names TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skill_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    default_tool_pool_ids TEXT NOT NULL DEFAULT '[]',
    augmentation_mode TEXT NOT NULL DEFAULT 'tool'
        CHECK (augmentation_mode IN ('tool', 'always', 'none'))
);

CREATE TABLE IF NOT EXISTS agent_tool_links (
    agent_id INTEGER NOT NULL REFERENCES agent_pool(id),
    tool_pool_id INTEGER NOT NULL REFERENCES tool_pool(id),
    PRIMARY KEY (agent_id, tool_pool_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id INTEGER,
    before_value TEXT,
    after_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (NF10)');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (NF10)');
END;
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, coltype_with_default: str
) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_with_default}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS`는 이미 존재하는 테이블에 새 컬럼을 추가하지
    못한다 — 이 저장소가 커오면서 `agent_pool`에 여러 번 컬럼을 추가했는데,
    이미 만들어진 DB 파일을 쓰는 배포(이 세션의 데모 서버 포함)에서는 이
    마이그레이션 없이 컬럼이 영영 안 생겨 `SELECT *` 결과에 없는 키에 접근할
    때 500 에러가 실제로 발생했다("AI 에이전트 관리"·"Crew 실행" 메뉴)."""
    _ensure_column(conn, "agent_pool", "llm_provider", "TEXT")
    _ensure_column(conn, "agent_pool", "llm_model", "TEXT")
    _ensure_column(conn, "agent_pool", "last_output", "TEXT")
    _ensure_column(conn, "agent_pool", "framework", "TEXT NOT NULL DEFAULT 'stub'")
    _ensure_column(conn, "agent_pool", "mcp_servers", "TEXT NOT NULL DEFAULT '[]'")


def _seed_default_pools(conn: sqlite3.Connection) -> None:
    """ADR 결정 12(사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘") — VectorDB/GraphDB를 감싼 native Tool과, 이걸 쓰는
    skill_pool 항목을 기본 제공한다. 이전까지 `agent_runtime.py`의
    `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 하드코딩이 하던 "스킬→도구"
    매핑을 데이터로 옮긴 것 — 이 시드가 없으면 기존 vectordb/graphdb
    skill_tags를 가진 Agent가 갑자기 도구를 못 받게 되므로 반드시 필요하다.
    `name` UNIQUE 제약으로 `INSERT OR IGNORE`가 매 기동마다 안전하게
    멱등이다(이미 있으면 아무 것도 안 함 — 관리자가 이후 수정해도 안
    덮어씀)."""
    native_tools = [
        (
            "VectorDB 지식검색",
            "vectorstore.search()를 감싼 네이티브 CrewAI Tool",
            "vectordb_search",
        ),
        (
            "GraphDB 관계조회",
            "graphquery.context_for_prompt()를 감싼 네이티브 CrewAI Tool",
            "graphdb_query",
        ),
    ]
    tool_ids: dict[str, int] = {}
    for name, description, native_id in native_tools:
        conn.execute(
            "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
            "VALUES (?, ?, 'native', ?)",
            (name, description, json.dumps({"native_id": native_id})),
        )
        row = conn.execute(
            "SELECT id FROM tool_pool WHERE name = ?", (name,)
        ).fetchone()
        tool_ids[native_id] = row["id"]

    skills = [
        ("vectordb", "VectorDB 기반 지식검색 역량", "vectordb_search"),
        ("rag", "VectorDB 기반 지식검색 역량(별칭)", "vectordb_search"),
        ("graphdb", "GraphDB 기반 관계분석 역량", "graphdb_query"),
        ("관계질의", "GraphDB 기반 관계분석 역량(별칭)", "graphdb_query"),
    ]
    for name, description, native_id in skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, ?, 'tool')",
            (name, description, json.dumps([tool_ids[native_id]])),
        )


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    _seed_default_pools(conn)
    conn.commit()
