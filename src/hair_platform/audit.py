"""F13 — 감사로그. append-only는 DB 트리거(db.py)로 강제한다(NF10).

Admin·매니저 role만 조회 가능(NF12) — RBAC 검사는 app.py의 라우트에서 수행한다.
"""

from __future__ import annotations

import json
import sqlite3


def record(
    conn: sqlite3.Connection,
    actor_id: int,
    action: str,
    subject_type: str,
    subject_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log "
        "(actor_id, action, subject_type, subject_id, before_value, after_value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            actor_id,
            action,
            subject_type,
            subject_id,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )
