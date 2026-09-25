"""SQLite 연결 및 스키마 — v0 범위(users·human_pool·briefs·approvals)만 다룬다.

ADR 결정 5: 실시간 쓰기가 빈번한 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 쓴다.
assignments·assignment_history·audit_log·jobs 테이블은 v1 이후 버전에서 추가한다.
"""

from __future__ import annotations

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
    manager_id INTEGER REFERENCES users(id),
    user_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    raw_text TEXT NOT NULL,
    stub_roles TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'submitted'
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by INTEGER REFERENCES users(id)
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
