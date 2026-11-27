"""수요·기한·활용도(2026-11-27, Resource Management 분석 Phase B — 2번 "일에 크기·기한이 없다", 7번 "활용도 화면이 없다").

할 일에는 기한·예상 작업량이 없어 일정 계획·지연 감지·수요 예측·"이 일을 받을 수 있나" 판단이 불가능했다(대시보드의
"마감 임박"도 데이터가 없어 빠져 있었다). `tasks.headcount`는 저장만 되고 실제로 몇 명이 배정됐는지 보지 않았다.

- 할 일: `due_date`(YYYY-MM-DD), `estimate_hours`(예상 작업 시간) — 둘 다 선택.
- 사람: `weekly_hours`(주당 일할 수 있는 시간, 비우면 40). 부재일(`resource_capacity`)만큼 그 주 용량이 줄어든다(평일 하루 = 1/5).
- **배분**: 끝나지 않은 할 일의 예상 시간을 확정 배정된 사람 수로 나누고, 오늘부터 기한까지의 평일에 고르게 편다. 기한이 지난
  일은 이번 주에 모두 올리고 '지연'으로 센다. 기한이 없거나 예상 시간이 없는 일은 달력에 놓지 않고 따로 센다(지어내지 않는다).
- **활용도** = 배분된 시간 ÷ 그 주 용량. 0.85 이상이면 높음, 1을 넘으면 초과. 앞으로 2주 평균 30% 미만이면 여유 인력.
- **인원 부족**: `headcount`보다 확정 배정이 적은 끝나지 않은 할 일.
- **역할별 수요**: 열린 할 일·배정 안 된 할 일·최근 4주 주당 새 할 일·앞으로 4주 정기 실행 예정 수 ↔ 그 역할을 맡을 수 있는 사람·Agent.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from hair_platform import audit

DEFAULT_WEEKLY_HOURS = 40.0
WEEKS = 4
HIGH_UTIL = 0.85
BENCH_UTIL = 0.3
DUE_SOON_DAYS = 7
MAX_ESTIMATE_HOURS = 2000


def ensure_schema(conn: sqlite3.Connection) -> None:
    from hair_platform.db_migrations import _ensure_column

    _ensure_column(conn, "tasks", "due_date", "TEXT")
    _ensure_column(conn, "tasks", "estimate_hours", "REAL")
    _ensure_column(conn, "human_pool", "weekly_hours", "REAL")


def _today() -> date:
    return date.today()


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _workdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ 설정

def set_task_schedule(conn: sqlite3.Connection, actor_id: int, task_id: int, due_date: str | None,
                      estimate_hours: float | None) -> dict:
    row = conn.execute("SELECT due_date, estimate_hours FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise LookupError("task_not_found")
    if due_date:
        try:
            due_date = date.fromisoformat(due_date).isoformat()
        except ValueError as exc:
            raise ValueError("due_date_format_YYYY-MM-DD") from exc
    else:
        due_date = None
    if estimate_hours is not None:
        estimate_hours = float(estimate_hours)
        if not 0 < estimate_hours <= MAX_ESTIMATE_HOURS:
            raise ValueError("estimate_hours_out_of_range")
    conn.execute("UPDATE tasks SET due_date = ?, estimate_hours = ? WHERE id = ?", (due_date, estimate_hours, task_id))
    audit.record(conn, actor_id, "task_schedule_updated", "tasks", task_id,
                 before={"due_date": row["due_date"], "estimate_hours": row["estimate_hours"]},
                 after={"due_date": due_date, "estimate_hours": estimate_hours})
    conn.commit()
    return {"task_id": task_id, "due_date": due_date, "estimate_hours": estimate_hours, **headcount_fill(conn, task_id)}


def set_weekly_hours(conn: sqlite3.Connection, actor_id: int, human_id: int, hours: float | None) -> dict:
    row = conn.execute("SELECT weekly_hours FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if row is None:
        raise LookupError("human_not_found")
    if hours is not None:
        hours = float(hours)
        if not 0 < hours <= 80:
            raise ValueError("weekly_hours_out_of_range")
    conn.execute("UPDATE human_pool SET weekly_hours = ? WHERE id = ?", (hours, human_id))
    audit.record(conn, actor_id, "human_weekly_hours_set", "human_pool", human_id,
                 before={"weekly_hours": row["weekly_hours"]}, after={"weekly_hours": hours})
    conn.commit()
    return {"id": human_id, "weekly_hours": hours, "effective_weekly_hours": hours or DEFAULT_WEEKLY_HOURS}


def headcount_fill(conn: sqlite3.Connection, task_id: int) -> dict:
    t = conn.execute("SELECT headcount FROM tasks WHERE id = ?", (task_id,)).fetchone()
    n = conn.execute("SELECT COUNT(*) FROM assignments WHERE task_id = ? AND status = 'confirmed'", (task_id,)).fetchone()[0]
    need = int(t["headcount"] or 1) if t else 1
    return {"headcount": need, "assigned": n, "short": max(0, need - n)}


# ------------------------------------------------------------------ 활용도

def _open_human_assignments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.task_id, a.resource_id, t.role_name, t.status, t.due_date, t.estimate_hours, t.priority, "
        "(SELECT COUNT(*) FROM assignments a2 WHERE a2.task_id = a.task_id AND a2.status = 'confirmed' "
        " AND a2.resource_type = 'human') AS people "
        "FROM assignments a JOIN tasks t ON t.id = a.task_id "
        "WHERE a.status = 'confirmed' AND a.resource_type = 'human' AND t.status != 'done'").fetchall()


def _absent_days(conn: sqlite3.Connection, start: date, end: date) -> dict[int, set[date]]:
    out: dict[int, set[date]] = {}
    try:
        rows = conn.execute("SELECT resource_id, starts_on, ends_on FROM resource_absences WHERE resource_type = 'human' "
                            "AND ends_on >= ? AND starts_on <= ?", (start.isoformat(), end.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        s, e = max(start, date.fromisoformat(r["starts_on"])), min(end, date.fromisoformat(r["ends_on"]))
        out.setdefault(r["resource_id"], set()).update(_workdays(s, e))
    return out


def utilization(conn: sqlite3.Connection, weeks: int = WEEKS) -> dict:
    today = _today()
    week_starts = [_monday(today) + timedelta(weeks=i) for i in range(weeks)]
    horizon_end = week_starts[-1] + timedelta(days=6)
    absent = _absent_days(conn, week_starts[0], horizon_end)
    people_rows = conn.execute("SELECT id, name, role, weekly_hours FROM human_pool WHERE availability != 'offboarded' "
                               "ORDER BY name").fetchall()
    people = {}
    for p in people_rows:
        wh = float(p["weekly_hours"] or DEFAULT_WEEKLY_HOURS)
        cells = []
        for ws in week_starts:
            off = len([d for d in _workdays(ws, ws + timedelta(days=4)) if d in absent.get(p["id"], set())])
            cells.append({"week_start": ws.isoformat(), "allocated_hours": 0.0,
                          "capacity_hours": max(0.0, wh * (5 - off) / 5), "absent_days": off, "tasks": set()})
        people[p["id"]] = {"id": p["id"], "name": p["name"], "role": p["role"], "weekly_hours": wh, "cells": cells,
                           "unscheduled_tasks": 0, "unscheduled_hours": 0.0, "overdue_tasks": 0}
    for a in _open_human_assignments(conn):
        person = people.get(a["resource_id"])
        if person is None:
            continue
        if a["estimate_hours"] is None or not a["due_date"]:
            person["unscheduled_tasks"] += 1
            person["unscheduled_hours"] += float(a["estimate_hours"] or 0) / max(1, a["people"])
            continue
        share = float(a["estimate_hours"]) / max(1, a["people"])
        due = date.fromisoformat(a["due_date"])
        if due < today:
            person["overdue_tasks"] += 1
            spread = [person["cells"][0]]
            per = [share]
        else:
            days = [d for d in _workdays(today, min(due, horizon_end))]
            beyond = len(_workdays(max(today, horizon_end + timedelta(days=1)), due)) if due > horizon_end else 0
            total_days = len(days) + beyond
            if total_days == 0:  # 주말이 기한 — 이번 주에
                days = [today]
                total_days = 1
            per_day = share / total_days
            spread, per = [], []
            for d in days:
                idx = (_monday(d) - week_starts[0]).days // 7
                if 0 <= idx < weeks:
                    spread.append(person["cells"][idx])
                    per.append(per_day)
        for cell, h in zip(spread, per, strict=True):
            cell["allocated_hours"] += h
            cell["tasks"].add(a["task_id"])
    out_people = []
    for p in people.values():
        for c in p["cells"]:
            c["tasks"] = len(c["tasks"])
            c["allocated_hours"] = round(c["allocated_hours"], 1)
            c["ratio"] = (c["allocated_hours"] / c["capacity_hours"]) if c["capacity_hours"] else (1.0 if c["allocated_hours"] else None)
        first2 = [c["ratio"] or 0 for c in p["cells"][:2]]
        p["next2_avg"] = sum(first2) / len(first2) if first2 else 0
        p["unscheduled_hours"] = round(p["unscheduled_hours"], 1)
        out_people.append(p)
    over = [(p["name"], c["week_start"]) for p in out_people for c in p["cells"] if c["ratio"] is not None and c["ratio"] > 1]
    all_ratios = [c["ratio"] for p in out_people for c in p["cells"] if c["ratio"] is not None and c["capacity_hours"]]
    return {
        "today": today.isoformat(),
        "weeks": [w.isoformat() for w in week_starts],
        "people": out_people,
        "summary": {
            "people": len(out_people),
            "avg_utilization": (sum(all_ratios) / len(all_ratios)) if all_ratios else None,
            "over_allocated_cells": len(over),
            "bench": [p["name"] for p in out_people if p["next2_avg"] < BENCH_UTIL and not p["unscheduled_tasks"]],
            "unscheduled_tasks": sum(p["unscheduled_tasks"] for p in out_people),
            "overdue_assignments": sum(p["overdue_tasks"] for p in out_people),
        },
        "thresholds": {"high": HIGH_UTIL, "bench": BENCH_UTIL},
    }


# ------------------------------------------------------------------ 기한·인원·수요

def deadlines(conn: sqlite3.Connection, days: int = DUE_SOON_DAYS) -> dict:
    today = _today()
    rows = conn.execute(
        "SELECT t.id, t.role_name, t.status, t.priority, t.due_date, t.estimate_hours, t.brief_id, "
        "(SELECT GROUP_CONCAT(COALESCE(h.name, ag.name, ag.role_name, tm.name), ', ') FROM assignments a "
        " LEFT JOIN human_pool h ON a.resource_type = 'human' AND h.id = a.resource_id "
        " LEFT JOIN agent_pool ag ON a.resource_type = 'agent' AND ag.id = a.resource_id "
        " LEFT JOIN teams tm ON a.resource_type = 'team' AND tm.id = a.resource_id "
        " WHERE a.task_id = t.id AND a.status = 'confirmed') AS assignees "
        "FROM tasks t WHERE t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date <= ? ORDER BY t.due_date",
        ((today + timedelta(days=days)).isoformat(),)).fetchall()
    overdue, soon = [], []
    for r in rows:
        item = dict(r)
        item["days_left"] = (date.fromisoformat(r["due_date"]) - today).days
        (overdue if item["days_left"] < 0 else soon).append(item)
    return {"overdue": overdue, "due_soon": soon, "window_days": days}


def headcount_gaps(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT t.id, t.role_name, t.status, t.headcount, t.due_date, "
        "(SELECT COUNT(*) FROM assignments a WHERE a.task_id = t.id AND a.status = 'confirmed') AS assigned, "
        "(SELECT COUNT(*) FROM assignments a WHERE a.task_id = t.id AND a.status = 'requested') AS requested "
        "FROM tasks t WHERE t.status != 'done' "
        # 파이프라인이 처리하는 할 일은 사람·Agent 배정 없이 돈다 — 인원 부족으로 세지 않는다.
        "AND NOT EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.task_id = t.id) AND COALESCE(t.headcount, 1) > "
        "(SELECT COUNT(*) FROM assignments a WHERE a.task_id = t.id AND a.status = 'confirmed') "
        "ORDER BY t.due_date IS NULL, t.due_date, t.id").fetchall()
    return [dict(r) | {"short": int(r["headcount"] or 1) - r["assigned"]} for r in rows]


def _scheduled_runs(conn: sqlite3.Connection, weeks: int) -> dict[str, int]:
    """앞으로 몇 주 동안 정기 실행이 만들 할 일 수(역할별)."""
    per = {"daily": 7, "weekdays": 5, "weekly": 1}
    try:
        rows = conn.execute("SELECT role_name, interval FROM pipeline_schedules WHERE is_active = 1").fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, int] = {}
    for r in rows:
        out[r["role_name"]] = out.get(r["role_name"], 0) + per.get(r["interval"], 0) * weeks
    return out


def demand_by_role(conn: sqlite3.Connection, weeks: int = WEEKS) -> list[dict]:
    from hair_platform.assignment import _role_matches

    roles: dict[str, dict] = {}

    def bucket(role: str) -> dict:
        return roles.setdefault(role, {"role": role, "open": 0, "unassigned": 0, "open_hours": 0.0, "recent_per_week": 0.0,
                                       "scheduled_next": 0})
    for r in conn.execute(
            "SELECT t.role_name, t.estimate_hours, (SELECT COUNT(*) FROM assignments a WHERE a.task_id = t.id "
            "AND a.status = 'confirmed') AS assigned, EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.task_id = t.id) AS by_pipeline "
            "FROM tasks t WHERE t.status != 'done'").fetchall():
        b = bucket(r["role_name"])
        b["open"] += 1
        b["unassigned"] += 1 if r["assigned"] == 0 and not r["by_pipeline"] else 0  # 파이프라인이 처리하는 일은 배정 대상 아님
        b["open_hours"] += float(r["estimate_hours"] or 0)
    # 최근 4주 시작된 할 일 — 할 일·업무 요청에는 생성 시각이 없어, 처음 배정 요청(assignment_history) 또는 처음 파이프라인
    # 실행(pipeline_runs.created_at)이 4주 안인 할 일을 센다(그 전에 만들어 두고 이제 시작한 일도 포함되는 근삿값).
    recent = conn.execute(
        "SELECT t.role_name, COUNT(*) AS n FROM tasks t WHERE MIN("
        " COALESCE((SELECT MIN(h.changed_at) FROM assignment_history h JOIN assignments a ON a.id = h.assignment_id "
        "           WHERE a.task_id = t.id), '9999'),"
        " COALESCE((SELECT MIN(r.created_at) FROM pipeline_runs r WHERE r.task_id = t.id), '9999')"
        ") >= datetime('now', '-28 days') GROUP BY t.role_name").fetchall()
    for r in recent:
        bucket(r["role_name"])["recent_per_week"] = r["n"] / 4
    for role, n in _scheduled_runs(conn, weeks).items():
        bucket(role)["scheduled_next"] += n
    humans = conn.execute("SELECT role, availability FROM human_pool WHERE availability != 'offboarded'").fetchall()
    agents = conn.execute("SELECT role_name FROM agent_pool WHERE status = 'active'").fetchall()
    out = []
    for b in roles.values():
        b["people"] = sum(1 for h in humans if _role_matches(h["role"], b["role"]) and h["availability"] == "available")
        b["agents"] = sum(1 for a in agents if _role_matches(a["role_name"], b["role"]))
        b["open_hours"] = round(b["open_hours"], 1)
        b["no_supply"] = b["people"] == 0 and b["agents"] == 0
        out.append(b)
    out.sort(key=lambda x: (not x["no_supply"], -x["unassigned"], -x["open"]))
    return out


def overview(conn: sqlite3.Connection) -> dict:
    return {"utilization": utilization(conn), "deadlines": deadlines(conn), "headcount_gaps": headcount_gaps(conn),
            "demand": demand_by_role(conn), "default_weekly_hours": DEFAULT_WEEKLY_HOURS}


def task_schedule_info(conn: sqlite3.Connection, task_id: int) -> dict:
    t = conn.execute("SELECT due_date, estimate_hours, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if t is None:
        raise LookupError("task_not_found")
    days_left = (date.fromisoformat(t["due_date"]) - _today()).days if t["due_date"] else None
    return {"due_date": t["due_date"], "estimate_hours": t["estimate_hours"], "days_left": days_left,
            "overdue": days_left is not None and days_left < 0 and t["status"] != "done", **headcount_fill(conn, task_id)}

