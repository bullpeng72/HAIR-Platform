"""자동화를 만든 사람 점검(2026-11-28).

정기 실행(`pipeline_schedules`)·이벤트 트리거(`automation_triggers`)·외부 입력 트리거(`inbound_triggers`)는 만든 사람
(`created_by`)의 이름과 권한으로 업무 요청을 만들고 파이프라인을 시작한다. 예전엔 실행할 때 그 사람을 다시 보지 않아,
계정을 끄거나(퇴사) admin·manager에서 member·viewer로 바꿔도 자동화가 그 이름으로 계속 돌았다 — 만들 수 없는 사람이
만든 일이 계속 생긴다.

실행 직전에 만든 사람이 켜진 admin·manager인지 본다. 아니면 그 자동화를 끄고(`is_active = 0`) 이유를 `last_error`에
`OWNER_PAUSED_PREFIX`로 시작하게 남기며 활동 기록에 쓴다. 인계 점검 화면의 "넘기기"(`transfer`)가 다른 admin·manager
에게 넘기면서, 이 이유로 꺼진 것만 다시 켠다(사람이 직접 끈 것은 그대로).
"""

from __future__ import annotations

import sqlite3

from hair_platform import audit

TABLES = {"pipeline_schedules": "pipeline_schedule", "automation_triggers": "automation_trigger",
          "inbound_triggers": "inbound_trigger"}
OWNER_PAUSED_PREFIX = "[만든 사람]"
RUNNER_ROLES = ("admin", "manager")


def blocked_reason(conn: sqlite3.Connection, user_id: int) -> str | None:
    """만든 사람이 지금 자동화를 돌릴 수 있으면 None, 아니면 이유."""
    r = conn.execute("SELECT username, role, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
    if r is None:
        return "계정이 없습니다"
    if not r["is_active"]:
        return f"{r['username']} 계정이 꺼져 있습니다"
    if r["role"] not in RUNNER_ROLES:
        return f"{r['username']} 계정이 admin·manager가 아닙니다({r['role']})"
    return None


def check_or_pause(conn: sqlite3.Connection, table: str, row: sqlite3.Row | dict) -> bool:
    """돌려도 되면 True. 아니면 끄고 기록한 뒤 False."""
    reason = blocked_reason(conn, row["created_by"])
    if reason is None:
        return True
    msg = f"{OWNER_PAUSED_PREFIX} {reason} — 멈췄습니다. 인계 점검에서 다른 admin·manager에게 넘기면 다시 켜집니다."
    conn.execute(f"UPDATE {table} SET is_active = 0, last_error = ? WHERE id = ?", (msg, row["id"]))  # noqa: S608 — 고정 표
    audit.record(conn, row["created_by"], f"{TABLES[table]}_paused", table, row["id"], after={"reason": reason})
    conn.commit()
    return False


def owned(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """그 사람 이름의 자동화 — 켜진 것과, 만든 사람 때문에 꺼진 것(사람이 직접 끈 것은 뺀다)."""
    labels = {"pipeline_schedules": "정기 실행", "automation_triggers": "이벤트 트리거", "inbound_triggers": "외부 입력 트리거"}
    out = []
    for table, label in labels.items():
        try:
            rows = conn.execute(
                f"SELECT id, name, is_active, last_error FROM {table} WHERE created_by = ? "  # noqa: S608
                "AND (is_active = 1 OR last_error LIKE ?)", (user_id, OWNER_PAUSED_PREFIX + "%")).fetchall()
        except sqlite3.OperationalError:  # 마이그레이션 전
            continue
        for r in rows:
            out.append({"kind": label, "table": table, "id": r["id"], "name": r["name"],
                        "paused": not r["is_active"], "note": r["last_error"] if not r["is_active"] else None})
    return out


def transfer(conn: sqlite3.Connection, actor_id: int, from_user: int, to_user: int) -> int:
    """`from_user` 이름의 자동화(`owned`)를 `to_user`에게 넘긴다 — 켜진 admin·manager만. 만든 사람 때문에 꺼진 것은
    다시 켠다. 넘긴 수."""
    if blocked_reason(conn, to_user) is not None:
        raise ValueError("to_user_must_be_active_admin_or_manager")
    if to_user == from_user:
        raise ValueError("to_user_must_differ")
    n = 0
    for a in owned(conn, from_user):
        conn.execute(
            f"UPDATE {a['table']} SET created_by = ?, is_active = 1, "  # noqa: S608 — 고정 표
            "last_error = CASE WHEN last_error LIKE ? THEN NULL ELSE last_error END WHERE id = ?",
            (to_user, OWNER_PAUSED_PREFIX + "%", a["id"]))
        if a["paused"] and a["table"] == "pipeline_schedules":
            _reschedule(conn, a["id"])  # 멈춘 사이 지난 시각에 곧바로 돌지 않게 다음 시각을 지금부터 다시 센다
        audit.record(conn, actor_id, f"{TABLES[a['table']]}_transferred", a["table"], a["id"],
                     before={"created_by": from_user}, after={"created_by": to_user, "resumed": a["paused"]})
        n += 1
    conn.commit()
    return n


def _reschedule(conn: sqlite3.Connection, schedule_id: int) -> None:
    import datetime as dt

    from hair_platform import automation

    r = conn.execute("SELECT interval, time_hhmm, weekday FROM pipeline_schedules WHERE id = ?", (schedule_id,)).fetchone()
    nxt = automation.next_run_after(dt.datetime.now(), r["interval"], r["time_hhmm"], r["weekday"])
    conn.execute("UPDATE pipeline_schedules SET next_run_at = ? WHERE id = ?", (nxt.isoformat(timespec="minutes"), schedule_id))
