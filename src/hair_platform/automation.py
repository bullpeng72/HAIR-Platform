"""정기 실행과 자동화 루프 — 사람이 버튼을 누르지 않아도 파이프라인이 돌고 알림이 나간다.

사용자 요청(2026-11-23, "P1 진행해줘" — P1-5). 지금까지 모든 실행은 사람이 대화하거나 버튼을 눌러야
시작됐다 — 주간 실적 보고처럼 주기적으로 도는 업무도 매번 사람이 시작해야 했다.

**정기 실행**: "매일/평일/매주 ○요일 HH:MM"에 업무 요청 → 할 일 → 파이프라인 실행을 만든다. 만든
사람(`created_by`)의 이름으로 실행하므로 결재·권한은 사람이 직접 눌렀을 때와 같다(1단계 사람 승인
같은 HITL도 그대로 — 알림이 켜져 있으면 결재 대기가 웹훅으로 간다). 시간은 서버 PC의 현지 시각.

**자동화 루프**: `hair serve`로 띄운 서버에서만(`HAIR_AUTOMATION=1`) 30초마다 ① 때가 된 정기 실행
시작 ② 새 이벤트 알림(`integrations.run_notifications`)을 한다. 테스트·외부 uvicorn 실행에서는 돌지
않는다. 루프의 한 차례가 실패해도 다음 차례는 계속된다."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import threading

from hair_platform import audit

log = logging.getLogger(__name__)

INTERVALS = {"daily": "매일", "weekdays": "평일(월~금)", "weekly": "매주"}
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
LOOP_SECONDS = 30
_loop_started = False
_loop_lock = threading.Lock()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_schedules ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "pipeline_template_id INTEGER NOT NULL REFERENCES pipeline_templates(id), "
        "brief_text TEXT NOT NULL, "
        "role_name TEXT NOT NULL, "
        "interval TEXT NOT NULL CHECK (interval IN ('daily', 'weekdays', 'weekly')), "
        "weekday INTEGER CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6), "
        "time_hhmm TEXT NOT NULL, "
        "created_by INTEGER NOT NULL REFERENCES users(id), "
        "is_active INTEGER NOT NULL DEFAULT 1, "
        "next_run_at TEXT, last_run_at TEXT, last_task_id INTEGER, last_run_id INTEGER, last_error TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = (int(x) for x in value.strip().split(":"))
    except (ValueError, AttributeError):
        raise ValueError("invalid_time") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("invalid_time")
    return hh, mm


def next_run_after(now: dt.datetime, interval: str, time_hhmm: str, weekday: int | None) -> dt.datetime:
    """`now` 이후(같은 시각 제외) 첫 실행 시각 — 현지 시각 기준."""
    hh, mm = _parse_hhmm(time_hhmm)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for _ in range(8):
        if candidate > now:
            wd = candidate.weekday()
            if interval == "daily" or (interval == "weekdays" and wd < 5) or (
                interval == "weekly" and wd == weekday
            ):
                return candidate
        candidate += dt.timedelta(days=1)
        candidate = candidate.replace(hour=hh, minute=mm)
    raise ValueError("no_next_run")  # 도달하지 않는다(8일 안에 반드시 한 번 맞는다)


def describe(row: dict) -> str:
    when = INTERVALS[row["interval"]]
    if row["interval"] == "weekly" and row.get("weekday") is not None:
        when += f" {WEEKDAYS[row['weekday']]}요일"
    return f"{when} {row['time_hhmm']}"


def _validate(conn: sqlite3.Connection, fields: dict) -> None:
    if not (fields.get("name") or "").strip():
        raise ValueError("empty_name")
    if not (fields.get("brief_text") or "").strip():
        raise ValueError("empty_brief_text")
    if not (fields.get("role_name") or "").strip():
        raise ValueError("empty_role_name")
    if fields.get("interval") not in INTERVALS:
        raise ValueError("invalid_interval")
    if fields["interval"] == "weekly" and fields.get("weekday") not in range(7):
        raise ValueError("weekday_required")
    _parse_hhmm(fields.get("time_hhmm") or "")
    tpl = conn.execute(
        "SELECT is_active FROM pipeline_templates WHERE id = ?", (fields.get("pipeline_template_id"),)
    ).fetchone()
    if tpl is None:
        raise ValueError("pipeline_template_not_found")
    if not tpl["is_active"]:
        raise ValueError("pipeline_template_inactive")


def create_schedule(conn: sqlite3.Connection, actor_id: int, fields: dict, now: dt.datetime | None = None) -> dict:
    _validate(conn, fields)
    weekday = fields["weekday"] if fields["interval"] == "weekly" else None
    nxt = next_run_after(now or dt.datetime.now(), fields["interval"], fields["time_hhmm"], weekday)
    cur = conn.execute(
        "INSERT INTO pipeline_schedules (name, pipeline_template_id, brief_text, role_name, interval, weekday, "
        "time_hhmm, created_by, next_run_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fields["name"].strip(), fields["pipeline_template_id"], fields["brief_text"].strip(),
         fields["role_name"].strip(), fields["interval"], weekday, fields["time_hhmm"].strip(),
         actor_id, nxt.isoformat(timespec="minutes")),
    )
    schedule_id = cur.lastrowid
    assert schedule_id is not None
    audit.record(conn, actor_id, "pipeline_schedule_created", "pipeline_schedules", schedule_id,
                 after={"name": fields["name"], "when": describe({**fields, "weekday": weekday})})
    conn.commit()
    created = get_schedule(conn, schedule_id)
    assert created is not None
    return created


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> dict | None:
    row = conn.execute(
        "SELECT s.*, t.name AS template_name, u.username AS created_by_name FROM pipeline_schedules s "
        "JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "JOIN users u ON u.id = s.created_by WHERE s.id = ?",
        (schedule_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["when"] = describe(d)
    return d


def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    ids = [r[0] for r in conn.execute("SELECT id FROM pipeline_schedules ORDER BY id DESC")]
    return [s for s in (get_schedule(conn, i) for i in ids) if s is not None]


def set_active(conn: sqlite3.Connection, actor_id: int, schedule_id: int, active: bool) -> dict:
    row = get_schedule(conn, schedule_id)
    if row is None:
        raise ValueError("schedule_not_found")
    nxt = next_run_after(dt.datetime.now(), row["interval"], row["time_hhmm"], row["weekday"]) if active else None
    conn.execute(
        "UPDATE pipeline_schedules SET is_active = ?, next_run_at = ? WHERE id = ?",
        (int(active), nxt.isoformat(timespec="minutes") if nxt else None, schedule_id),
    )
    audit.record(conn, actor_id, "pipeline_schedule_toggled", "pipeline_schedules", schedule_id,
                 after={"active": active})
    conn.commit()
    updated = get_schedule(conn, schedule_id)
    assert updated is not None
    return updated


def delete_schedule(conn: sqlite3.Connection, actor_id: int, schedule_id: int) -> None:
    if get_schedule(conn, schedule_id) is None:
        raise ValueError("schedule_not_found")
    conn.execute("DELETE FROM pipeline_schedules WHERE id = ?", (schedule_id,))
    audit.record(conn, actor_id, "pipeline_schedule_deleted", "pipeline_schedules", schedule_id)
    conn.commit()


def start_scheduled_run(conn: sqlite3.Connection, schedule: dict, now: dt.datetime) -> tuple[int, int]:
    """업무 요청 → 할 일 → 파이프라인 실행 행을 만든다(실제 단계 실행은 호출자가 돌린다).
    (task_id, run_id)."""
    import json

    from hair_platform import pipeline_runtime

    stamp = now.strftime("%Y-%m-%d %H:%M")
    brief_text = f"{schedule['brief_text']}\n\n(정기 실행 「{schedule['name']}」 · {stamp})"
    brief_id = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, ?, ?)",
        (schedule["created_by"], brief_text, json.dumps([schedule["role_name"]], ensure_ascii=False)),
    ).lastrowid
    task_id = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, notes) VALUES (?, ?, ?)",
        (brief_id, schedule["role_name"], f"정기 실행 「{schedule['name']}」"),
    ).lastrowid
    assert task_id is not None
    conn.commit()
    run_id = pipeline_runtime.start_pipeline_run(
        conn, schedule["created_by"], schedule["pipeline_template_id"], task_id
    )
    return task_id, run_id


def run_due_schedules(conn: sqlite3.Connection, now: dt.datetime | None = None,
                      run_stages=None) -> list[int]:
    """때가 된 정기 실행을 시작하고 시작한 run_id 목록을 돌려준다. `run_stages(run_id, actor_id)`가
    실제 단계 실행(기본: 별도 스레드에서 `pipeline_runtime.run_pipeline_stage_job`)."""
    now = now or dt.datetime.now()
    started: list[int] = []
    due = conn.execute(
        "SELECT id FROM pipeline_schedules WHERE is_active = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?",
        (now.isoformat(timespec="minutes"),),
    ).fetchall()
    for (schedule_id,) in due:
        schedule = get_schedule(conn, schedule_id)
        if schedule is None:
            continue
        nxt = next_run_after(now, schedule["interval"], schedule["time_hhmm"], schedule["weekday"])
        # 다음 실행 시각부터 먼저 옮긴다 — 시작이 실패해도 같은 시각에 매 차례 다시 시도하지 않는다.
        conn.execute("UPDATE pipeline_schedules SET next_run_at = ?, last_run_at = ? WHERE id = ?",
                     (nxt.isoformat(timespec="minutes"), now.isoformat(timespec="minutes"), schedule_id))
        conn.commit()
        try:
            task_id, run_id = start_scheduled_run(conn, schedule, now)
        except Exception as exc:  # noqa: BLE001 — 한 정기 실행의 실패가 다른 것을 막지 않는다
            conn.execute("UPDATE pipeline_schedules SET last_error = ? WHERE id = ?",
                         (f"{type(exc).__name__}: {exc}"[:300], schedule_id))
            conn.commit()
            continue
        conn.execute(
            "UPDATE pipeline_schedules SET last_task_id = ?, last_run_id = ?, last_error = NULL WHERE id = ?",
            (task_id, run_id, schedule_id),
        )
        audit.record(conn, schedule["created_by"], "pipeline_schedule_fired", "pipeline_schedules",
                     schedule_id, after={"task_id": task_id, "run_id": run_id})
        conn.commit()
        if run_stages is None:
            from hair_platform import job_queue  # 작업 큐(2026-11-26, P1-6 2단계) — external이면 작업 프로세스가 실행

            job_queue.enqueue(conn, "pipeline.stage", run_id, schedule["created_by"])
        else:
            run_stages(run_id, schedule["created_by"])
        started.append(run_id)
    return started


def tick(conn: sqlite3.Connection) -> None:
    from hair_platform import backup, inbound, integrations, triggers

    # 이벤트 트리거(2026-11-24, P1-5)는 정기 실행 다음 — 같은 차례에 끝난 실행도 바로 이어 받는다.
    # 외부 입력 트리거(2026-11-27, 4단계 — 메일·일정 확인, 받은 웹훅 시작)는 그다음.
    # 자동 백업(2026-11-27, 개선 제안 5-1)은 마지막 — 하루에 한 번만 실제로 뜬다(`backup.run_scheduled_backup`).
    for step in (run_due_schedules, triggers.run_triggers, inbound.run_inbound, integrations.run_notifications,
                 backup.run_scheduled_backup):
        try:
            step(conn)
        except Exception:  # noqa: BLE001 — 한 단계의 실패가 루프를 멈추지 않는다
            log.exception("automation step failed: %s", getattr(step, "__name__", step))


def start_loop(conn: sqlite3.Connection) -> bool:
    """`HAIR_AUTOMATION=1`일 때만 데몬 스레드로 루프를 띄운다(프로세스당 한 번)."""
    global _loop_started
    if os.environ.get("HAIR_AUTOMATION") != "1":
        return False
    with _loop_lock:
        if _loop_started:
            return False
        _loop_started = True
    stop = threading.Event()

    def _run() -> None:
        while not stop.wait(LOOP_SECONDS):
            tick(conn)

    threading.Thread(target=_run, name="hair-automation", daemon=True).start()
    return True
