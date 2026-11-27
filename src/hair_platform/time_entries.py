"""할 일 작업 시간 기록(2026-11-28, Resource Management 5번 남은 부분).

사람 인건비는 할 일 예상 시간 × 시간당 단가로만 추정했다 — 실제로 든 시간을 적을 곳이 없었다. 기록은 **선택**이다:
그 할 일에 시간이 기록된 사람은 기록한 시간 × 단가(`labor_logged`), 기록이 없는 사람은 예전처럼 자기 몫의 예상 시간 ×
단가(`labor_estimated`). 둘을 따로 보여 얼마가 실제인지 알 수 있다.

- 누가: 그 할 일에 확정 배정된 사람 본인(연결된 계정), 또는 admin·manager(배정된 누구의 시간이든). viewer는 못 한다.
- 값: 한 번에 0보다 크고 24시간 이하, 날짜는 오늘까지·1년 안. 같은 날 여러 번 적을 수 있다(합산).
- 지우기: 적은 사람 또는 admin·manager. 적기·지우기는 활동 기록에 남는다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from hair_platform import audit

MAX_HOURS_PER_ENTRY = 24.0
MAX_AGE_DAYS = 365
DECIDERS = ("admin", "manager")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_time_entries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL REFERENCES tasks(id), "
        "human_id INTEGER NOT NULL REFERENCES human_pool(id), "
        "hours REAL NOT NULL CHECK (hours > 0 AND hours <= 24), "
        "work_date TEXT NOT NULL, "
        "note TEXT, "
        "created_by INTEGER NOT NULL REFERENCES users(id), "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_time_entries_task ON task_time_entries(task_id, human_id)")


def _assigned_humans(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT h.id, h.name, h.user_id FROM assignments a JOIN human_pool h ON h.id = a.resource_id "
        "WHERE a.task_id = ? AND a.resource_type = 'human' AND a.status = 'confirmed' ORDER BY h.name", (task_id,)).fetchall()


def add_entry(conn: sqlite3.Connection, user: sqlite3.Row, task_id: int, hours: float, work_date: str,
              human_id: int | None = None, note: str = "") -> dict:
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise LookupError("task_not_found")
    if user["role"] == "viewer":
        raise PermissionError("viewer_cannot_log_time")
    try:
        hours = float(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("hours_must_be_number") from exc
    if not 0 < hours <= MAX_HOURS_PER_ENTRY:
        raise ValueError("hours_out_of_range")
    try:
        d = date.fromisoformat(work_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("work_date_must_be_yyyy_mm_dd") from exc
    if d > date.today() or d < date.today() - timedelta(days=MAX_AGE_DAYS):
        raise ValueError("work_date_out_of_range")
    assigned = {h["id"]: h for h in _assigned_humans(conn, task_id)}
    if human_id is None:  # 본인
        mine = [h for h in assigned.values() if h["user_id"] == user["id"]]
        if not mine:
            raise PermissionError("not_assigned_to_task")
        human_id = mine[0]["id"]
    if human_id not in assigned:
        raise ValueError("human_not_assigned_to_task")
    if assigned[human_id]["user_id"] != user["id"] and user["role"] not in DECIDERS:
        raise PermissionError("only_self_or_admin_manager")
    cur = conn.execute(
        "INSERT INTO task_time_entries (task_id, human_id, hours, work_date, note, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, human_id, hours, d.isoformat(), (note or "").strip()[:200] or None, user["id"]))
    audit.record(conn, user["id"], "task_time_logged", "tasks", task_id,
                 after={"entry_id": cur.lastrowid, "human_id": human_id, "hours": hours, "work_date": d.isoformat()})
    conn.commit()
    return {"id": cur.lastrowid, "task_id": task_id, "human_id": human_id, "hours": hours, "work_date": d.isoformat()}


def delete_entry(conn: sqlite3.Connection, user: sqlite3.Row, entry_id: int) -> dict:
    row = conn.execute("SELECT * FROM task_time_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise LookupError("entry_not_found")
    if row["created_by"] != user["id"] and user["role"] not in DECIDERS:
        raise PermissionError("only_author_or_admin_manager")
    conn.execute("DE" "LETE FROM task_time_entries WHERE id = ?", (entry_id,))
    audit.record(conn, user["id"], "task_time_entry_deleted", "tasks", row["task_id"],
                 before={"entry_id": entry_id, "human_id": row["human_id"], "hours": row["hours"], "work_date": row["work_date"]})
    conn.commit()
    return {"id": entry_id, "deleted": True}


def for_task(conn: sqlite3.Connection, task_id: int, user: sqlite3.Row) -> dict:
    """그 할 일의 기록(최신순)·사람별 합계·내가 적을 수 있는 사람."""
    entries = [dict(r) for r in conn.execute(
        "SELECT e.id, e.human_id, h.name AS human_name, e.hours, e.work_date, e.note, e.created_by, u.username AS created_by_name "
        "FROM task_time_entries e JOIN human_pool h ON h.id = e.human_id LEFT JOIN users u ON u.id = e.created_by "
        "WHERE e.task_id = ? ORDER BY e.work_date DESC, e.id DESC", (task_id,))]
    totals: dict[int, dict] = {}
    for e in entries:
        t = totals.setdefault(e["human_id"], {"human_id": e["human_id"], "name": e["human_name"], "hours": 0.0})
        t["hours"] = round(t["hours"] + e["hours"], 2)
        e["can_delete"] = e["created_by"] == user["id"] or user["role"] in DECIDERS
    assigned = _assigned_humans(conn, task_id)
    if user["role"] in DECIDERS:
        loggable = [{"id": h["id"], "name": h["name"]} for h in assigned]
    elif user["role"] == "viewer":
        loggable = []
    else:
        loggable = [{"id": h["id"], "name": h["name"]} for h in assigned if h["user_id"] == user["id"]]
    est = conn.execute("SELECT estimate_hours FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return {"entries": entries, "totals": list(totals.values()), "logged_hours": round(sum(e["hours"] for e in entries), 2),
            "estimate_hours": est["estimate_hours"] if est else None, "loggable": loggable}


def logged_by_task_human(conn: sqlite3.Connection) -> dict[tuple[int, int], float]:
    try:
        rows = conn.execute("SELECT task_id, human_id, SUM(hours) AS h FROM task_time_entries GROUP BY task_id, human_id").fetchall()
    except sqlite3.OperationalError:  # 마이그레이션 전
        return {}
    return {(r["task_id"], r["human_id"]): float(r["h"]) for r in rows}
