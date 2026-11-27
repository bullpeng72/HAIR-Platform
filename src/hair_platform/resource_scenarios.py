"""만약에(what-if)·인사 자료 가져오기(2026-11-28, Resource Management 분석 11번·12번).

11번 — "사람을 몇 명 더 두면 / 모델 동시 처리를 늘리면 어떻게 되나"를 지금 기록으로 계산한다.
- 역할별 앞으로 4주: 그 역할 사람들의 배분 시간 합 ÷ 용량 합(`resource_planning.utilization`과 같은 배분), 용량을 넘는 주 수.
  사람 N명(주당 시간 H)을 더하면 매주 용량에 N×H를 더해 다시 계산한다(새 사람이 넘친 일을 나눠 맡는다는 가정).
- 파이프라인 시작까지 대기: 앞에 선 긴 작업 × 최근 평균 실행 시간 ÷ 동시 처리 수(`capacity`). 동시 처리를 k개 늘린 값과 비교.
  클라우드 기본 모델이면(상한 없음) 동시 처리로는 줄지 않는다고 알린다.
- 추정치다 — 실제로는 일의 성격·숙련도에 따라 다르다.

12번 — 인사 시스템에서 내보낸 CSV로 사람·부재를 한꺼번에 넣는다(admin). 항상 **미리 보기**(줄마다 만들기·고치기·건너뜀·오류)를
먼저 돌려주고, `apply`일 때만 쓴다. 사람은 사번(`employee_id`)으로 찾아 있으면 고치고 없으면 만든다. 부재는 사번으로 사람을 찾는다.
- 사람 열: employee_id, name, role, skills(;로 구분), weekly_hours, hourly_rate, max_concurrent_tasks, availability
- 부재 열: employee_id, starts_on, ends_on, reason
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3

from hair_platform import audit

PEOPLE_COLUMNS = ["employee_id", "name", "role", "skills", "weekly_hours", "hourly_rate", "max_concurrent_tasks", "availability"]
ABSENCE_COLUMNS = ["employee_id", "starts_on", "ends_on", "reason"]
MAX_ROWS = 2000


# ================================================================== 만약에

def _role_weeks(conn: sqlite3.Connection) -> tuple[list[str], dict[str, dict]]:
    from hair_platform import resource_planning
    from hair_platform.assignment import _role_matches

    u = resource_planning.utilization(conn)
    groups: dict[str, dict] = {}
    for p in u["people"]:
        key = next((g for g in groups if _role_matches(p["role"], g)), p["role"])
        g = groups.setdefault(key, {"role": key, "people": 0, "allocated": [0.0] * len(u["weeks"]),
                                    "capacity": [0.0] * len(u["weeks"]), "unscheduled": 0})
        g["people"] += 1
        g["unscheduled"] += p["unscheduled_tasks"]
        for i, c in enumerate(p["cells"]):
            g["allocated"][i] += c["allocated_hours"]
            g["capacity"][i] += c["capacity_hours"]
    return u["weeks"], groups


def _summ(allocated: list[float], capacity: list[float]) -> dict:
    ratios = [(a / c) if c else (None if not a else float("inf")) for a, c in zip(allocated, capacity, strict=True)]
    finite = [r for r in ratios if r is not None and r != float("inf")]
    return {"avg_utilization": (sum(allocated) / sum(capacity)) if sum(capacity) else None,
            "weeks_over": sum(1 for r in ratios if r is not None and r > 1),
            "peak": max(finite) if finite else None,
            "weekly": [None if r in (None, float("inf")) else round(r, 3) for r in ratios]}


def what_if(conn: sqlite3.Connection, add_people: list[dict] | None = None, extra_model_slots: int = 0) -> dict:
    from hair_platform import capacity
    from hair_platform.assignment import _role_matches

    weeks, groups = _role_weeks(conn)
    adds = []
    for a in add_people or []:
        role = str(a.get("role") or "").strip()
        n = int(a.get("count") or 0)
        h = float(a.get("weekly_hours") or 40)
        if not role or not 1 <= n <= 100 or not 0 < h <= 80:
            raise ValueError("add_people_needs_role_count_1_100_weekly_hours_1_80")
        adds.append((role, n, h))
    roles = []
    touched = set()
    for role, n, h in adds:
        key = next((g for g in groups if _role_matches(role, g)), None)
        g = groups.get(key) if key else None
        alloc = g["allocated"] if g else [0.0] * len(weeks)
        cap = g["capacity"] if g else [0.0] * len(weeks)
        before = _summ(alloc, cap)
        after = _summ(alloc, [c + n * h for c in cap])
        roles.append({"role": key or role, "added": n, "weekly_hours": h, "people_before": g["people"] if g else 0,
                      "unscheduled_tasks": g["unscheduled"] if g else 0, "before": before, "after": after,
                      "new_role": g is None})
        if key:
            touched.add(key)
    cur = capacity.report(conn)
    q = {"slots": cur["slots"], "jobs_ahead": cur["pipeline_jobs_ahead"], "wait_before": cur["pipeline_queue_wait_seconds"],
         "wait_after": cur["pipeline_queue_wait_seconds"], "extra_slots": int(extra_model_slots or 0), "note": None}
    if q["extra_slots"]:
        if not 0 < q["extra_slots"] <= 16:
            raise ValueError("extra_model_slots_1_16")
        if cur["slots"] is None:
            q["note"] = "기본 모델이 클라우드라 동시 처리 상한이 없습니다 — 슬롯을 늘려도 대기는 줄지 않습니다."
        elif cur["pipeline_queue_wait_seconds"] is not None:
            q["wait_after"] = cur["pipeline_queue_wait_seconds"] * cur["slots"] / (cur["slots"] + q["extra_slots"])
            q["note"] = ("Ollama 동시 처리를 늘리려면 서버 환경변수 OLLAMA_NUM_PARALLEL과 모델 레지스트리의 '동시 실행 상한'을 함께 "
                         "올려야 합니다(메모리가 더 듭니다).")
        else:
            q["note"] = "최근 긴 작업 실행 기록이 없어 대기를 계산할 수 없습니다."
    return {"weeks": weeks, "roles": roles, "queue": q,
            "current_roles": [{"role": g["role"], "people": g["people"], **_summ(g["allocated"], g["capacity"])}
                              for g in groups.values()]}


# ================================================================== 인사 자료 가져오기

def _rows(text: str, columns: list[str]) -> list[dict]:
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    if not reader.fieldnames or "employee_id" not in [f.strip() for f in reader.fieldnames]:
        raise ValueError("첫 줄에 열 이름이 있어야 하고 employee_id 열이 필요합니다(열: " + ", ".join(columns) + ")")
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"한 번에 {MAX_ROWS}줄까지 가져올 수 있습니다")
    return rows


def _num(v: str, lo: float, hi: float, kind=float):
    if v == "":
        return None
    x = kind(v)
    if not lo <= x <= hi:
        raise ValueError(f"{v}는 {lo}~{hi} 범위가 아닙니다")
    return x


def import_people(conn: sqlite3.Connection, actor_id: int, text: str, apply: bool) -> dict:
    from hair_platform import org_policy

    out, created, updated = [], 0, 0
    seen: set[str] = set()
    for i, r in enumerate(_rows(text, PEOPLE_COLUMNS), start=2):
        emp = r.get("employee_id", "")
        try:
            if not emp:
                raise ValueError("employee_id가 비어 있습니다")
            org_policy.validate_employee_id(emp)
            if emp in seen:
                raise ValueError("같은 사번이 파일에 두 번 있습니다")
            seen.add(emp)
            fields: dict = {}
            if r.get("name"):
                fields["name"] = r["name"][:100]
            if r.get("role"):
                fields["role"] = r["role"][:100]
            if r.get("skills"):
                fields["skill_tags"] = json.dumps([s.strip() for s in r["skills"].split(";") if s.strip()][:50], ensure_ascii=False)
            for col, lo, hi, kind in (("weekly_hours", 1, 80, float), ("hourly_rate", 0, 10000, float),
                                      ("max_concurrent_tasks", 1, 50, int)):
                v = _num(r.get(col, ""), lo, hi, kind)
                if v is not None:
                    fields[col] = v
            if r.get("availability"):
                if r["availability"] not in ("available", "unavailable"):
                    raise ValueError("availability는 available 또는 unavailable")
                fields["availability"] = r["availability"]
            row = conn.execute("SELECT * FROM human_pool WHERE employee_id = ?", (emp,)).fetchone()
            if row is None:
                if not fields.get("name") or not fields.get("role"):
                    raise ValueError("새 사람은 name과 role이 필요합니다")
                action = "create"
                if apply:
                    cols = ["employee_id", *fields]
                    conn.execute(f"INSERT INTO human_pool ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608 — 고정 열 이름
                                 [emp, *fields.values()])
                created += 1
            else:
                if row["availability"] == "offboarded":
                    out.append({"line": i, "employee_id": emp, "action": "skip", "detail": "오프보딩된 사람 — 건너뜀"})
                    continue
                changed = {k: v for k, v in fields.items() if row[k] != v}
                if not changed:
                    out.append({"line": i, "employee_id": emp, "action": "skip", "detail": "바뀐 값 없음"})
                    continue
                action = "update"
                if apply:
                    conn.execute(f"UPDATE human_pool SET {', '.join(k + ' = ?' for k in changed)} WHERE id = ?",  # noqa: S608
                                 [*changed.values(), row["id"]])
                fields = changed
                updated += 1
            out.append({"line": i, "employee_id": emp, "action": action, "detail": ", ".join(sorted(fields))})
        except ValueError as exc:
            out.append({"line": i, "employee_id": emp, "action": "error", "detail": str(exc)})
    if apply and (created or updated):
        audit.record(conn, actor_id, "human_pool_imported", "human_pool", None,
                     after={"created": created, "updated": updated, "errors": sum(1 for o in out if o["action"] == "error")})
        conn.commit()
    return {"applied": apply, "created": created, "updated": updated, "rows": out,
            "errors": sum(1 for o in out if o["action"] == "error")}


def import_absences(conn: sqlite3.Connection, actor_id: int, text: str, apply: bool) -> dict:
    from datetime import date

    out, added = [], 0
    for i, r in enumerate(_rows(text, ABSENCE_COLUMNS), start=2):
        emp = r.get("employee_id", "")
        try:
            h = conn.execute("SELECT id, name FROM human_pool WHERE employee_id = ? AND availability != 'offboarded'",
                             (emp,)).fetchone()
            if h is None:
                raise ValueError("그 사번의 사람이 없습니다")
            s, e = date.fromisoformat(r.get("starts_on", "")), date.fromisoformat(r.get("ends_on", ""))
            if e < s or (e - s).days > 366:
                raise ValueError("기간이 올바르지 않습니다(끝 ≥ 시작, 1년 이내)")
            if conn.execute("SELECT 1 FROM resource_absences WHERE resource_type = 'human' AND resource_id = ? "
                            "AND starts_on = ? AND ends_on = ?", (h["id"], s.isoformat(), e.isoformat())).fetchone():
                out.append({"line": i, "employee_id": emp, "action": "skip", "detail": "같은 부재가 이미 있음"})
                continue
            if apply:
                conn.execute("INSERT INTO resource_absences (resource_type, resource_id, starts_on, ends_on, reason, created_by) "
                             "VALUES ('human', ?, ?, ?, ?, ?)", (h["id"], s.isoformat(), e.isoformat(),
                                                                 r.get("reason", "")[:200], actor_id))
            added += 1
            out.append({"line": i, "employee_id": emp, "action": "create",
                        "detail": f"{h['name']} {s.isoformat()}~{e.isoformat()} {r.get('reason', '')}".strip()})
        except ValueError as exc:
            out.append({"line": i, "employee_id": emp, "action": "error", "detail": str(exc)})
    if apply and added:
        audit.record(conn, actor_id, "resource_absences_imported", "human_pool", None, after={"added": added})
        conn.commit()
    return {"applied": apply, "created": added, "updated": 0, "rows": out,
            "errors": sum(1 for o in out if o["action"] == "error")}
