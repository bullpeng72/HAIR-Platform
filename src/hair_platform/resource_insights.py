"""자원 분석(2026-11-27, Resource Management 분석 Phase D — 6번 사람/AI 배분, 8번 스킬 갭, 9번 팀, 10번 업무 분배 공정성).

6번 — 같은 역할을 사람에게 맡겼을 때와 Agent에게 맡겼을 때의 결과를 나란히 본다(역할별): 사람이 결재한 결과물의 승인율,
   처음 결정된 결과물이 승인된 비율(한 번에 승인), 배정 확정부터 첫 승인까지 시간, 할 일당 비용(사람 추정 인건비 / AI 실측).
   양쪽 모두 결재 3건 이상이면 추천(Agent 우선 · 사람 우선 · AI 초안 + 사람 검토)을 근거와 함께 낸다. 자동 승인된 결과물은
   빼고 **사람이 결재한 것만** 센다(공정한 비교). 매칭이 후보를 못 찾은 기록(`match_misses`)을 모아 "채워지지 않은 수요"로.
8번 — 열린 할 일의 필요 스킬(수요) ↔ 가용 사람·활성 Agent의 스킬(공급). 공급 없음·얇음(1곳)·충분. 스킬 Pool에 있는 스킬이면
   "Agent에 붙이기", 없으면 "등록·채용·교육"을 제안한다.
9번 — 팀마다 구성원(사람·Agent), 다른 활성 팀과 겹치는 사람(3팀 이상이면 경고), 구성원 부하(끝나지 않은 할 일/용량), 팀에
   배정된 열린 할 일, 팀 Crew 실행 성공·실패.
10번 — 같은 역할의 사람들 사이에 일이 어떻게 나뉘는지: 끝나지 않은 할 일 수의 지니 계수, 가장 많이 맡은 사람의 몫, 긴급·높음
   우선순위 일의 집중(3명 이상인 역할에서 한 사람이 절반 이상), 기한 지난 일의 분포. 사람의 성과 점수는 쓰지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
import statistics

from hair_platform import process_metrics

MIN_DECISIONS = 3
OVERLAP_WARN_TEAMS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS match_misses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    role_name TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_match_misses_role ON match_misses(role_name, created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def record_match_miss(conn: sqlite3.Connection, task_id: int, role_name: str, required_skills: list[str]) -> None:
    """매칭이 후보를 못 찾았을 때(같은 할 일은 하루 한 번만)."""
    try:
        if conn.execute("SELECT 1 FROM match_misses WHERE task_id = ? AND created_at >= datetime('now', '-1 day')",
                        (task_id,)).fetchone():
            return
        conn.execute("INSERT INTO match_misses (task_id, role_name, required_skills) VALUES (?, ?, ?)",
                     (task_id, role_name, json.dumps(required_skills, ensure_ascii=False)))
        conn.commit()
    except sqlite3.OperationalError:
        pass


# ------------------------------------------------------------------ 6번 사람 vs AI

_HUMAN_DECIDED = (
    "(EXISTS (SELECT 1 FROM approvals a WHERE a.subject_type = 'deliverable' AND a.subject_id = d.id "
    "AND a.status IN ('approved', 'rejected')) OR EXISTS (SELECT 1 FROM pipeline_run_stages rs JOIN approvals a "
    "ON a.subject_type = 'pipeline_run_stage' AND a.subject_id = rs.id AND a.status IN ('approved', 'rejected') "
    "WHERE rs.deliverable_id = d.id))"
)


def human_vs_ai(conn: sqlite3.Connection) -> dict:
    from hair_platform import resource_lifecycle

    costs = resource_lifecycle.task_costs(conn)
    rows = conn.execute(
        "SELECT t.id AS task_id, t.role_name, d.author_type, d.status, d.created_at, d.id FROM deliverables d "
        f"JOIN tasks t ON t.id = d.task_id WHERE d.status IN ('approved', 'rejected') AND {_HUMAN_DECIDED} "  # noqa: S608 — 고정 조각
        "ORDER BY d.id").fetchall()
    confirmed = {}
    for r in conn.execute(
            "SELECT a.task_id, a.resource_type, MIN(h.changed_at) AS at FROM assignments a JOIN assignment_history h "
            "ON h.assignment_id = a.id AND h.to_status = 'confirmed' WHERE a.resource_type IN ('human', 'agent') "
            "GROUP BY a.task_id, a.resource_type").fetchall():
        confirmed[(r["task_id"], r["resource_type"])] = r["at"]
    roles: dict[str, dict] = {}
    seen_first: set[tuple[int, str]] = set()
    first_approved: dict[tuple[int, str], str] = {}
    for r in rows:
        side = "human" if r["author_type"] == "human" else "agent"
        b = roles.setdefault(r["role_name"], {"role": r["role_name"]}).setdefault(
            side, {"decided": 0, "approved": 0, "first_pass": 0, "tasks": set(), "hours": [], "cost": []})
        b["decided"] += 1
        b["approved"] += r["status"] == "approved"
        b["tasks"].add(r["task_id"])
        key = (r["task_id"], side)
        if key not in seen_first:
            seen_first.add(key)
            b["first_pass"] += r["status"] == "approved"
        if r["status"] == "approved" and key not in first_approved:
            first_approved[key] = r["created_at"]
            start = process_metrics._ts(confirmed.get(key))
            end = process_metrics._ts(r["created_at"])
            if start and end and end >= start:
                b["hours"].append((end - start).total_seconds() / 3600)
    out = []
    for role, v in roles.items():
        item: dict = {"role": role}
        for side in ("human", "agent"):
            b = v.get(side)
            if not b:
                item[side] = None
                continue
            # 비용이 기록되지 않은 할 일(단가·예상 시간·AI 사용 없음)은 0이 아니라 '모름'이다 — 빼고 평균.
            task_costs = [cost_v for t in b["tasks"] if t in costs
                          and (cost_v := costs[t]["labor_estimated" if side == "human" else "ai_cost"]) > 0]
            item[side] = {"decided": b["decided"], "tasks": len(b["tasks"]),
                          "approval_rate": b["approved"] / b["decided"],
                          "first_pass_rate": b["first_pass"] / len(b["tasks"]) if b["tasks"] else None,
                          "hours_to_approval": statistics.median(b["hours"]) if b["hours"] else None,
                          "cost_per_task": (sum(task_costs) / len(task_costs)) if task_costs else None}
        item["recommendation"] = _recommend(item["human"], item["agent"])
        out.append(item)
    out.sort(key=lambda x: -((x["human"] or {}).get("decided", 0) + (x["agent"] or {}).get("decided", 0)))
    return {"roles": out, "min_decisions": MIN_DECISIONS, "unmet": unmet_demand(conn)}


def _recommend(h: dict | None, a: dict | None) -> dict:
    if not h or not a or h["decided"] < MIN_DECISIONS or a["decided"] < MIN_DECISIONS:
        missing = []
        if not h or h["decided"] < MIN_DECISIONS:
            missing.append("사람")
        if not a or a["decided"] < MIN_DECISIONS:
            missing.append("Agent")
        return {"label": "데이터 부족", "level": "muted",
                "why": " · ".join(missing) + f" 쪽 결재가 {MIN_DECISIONS}건 미만"}
    gap = a["approval_rate"] - h["approval_rate"]
    why = f"승인율 사람 {round(h['approval_rate'] * 100)}% · Agent {round(a['approval_rate'] * 100)}%"
    faster = (a["hours_to_approval"] is not None and h["hours_to_approval"] is not None
              and a["hours_to_approval"] < h["hours_to_approval"])
    if faster:
        why += f" · 첫 승인까지 Agent {a['hours_to_approval']:.1f}h / 사람 {h['hours_to_approval']:.1f}h"
    cheaper = (a["cost_per_task"] is not None and h["cost_per_task"] is not None
               and a["cost_per_task"] <= h["cost_per_task"])
    if cheaper:
        why += f" · 할 일당 Agent ${a['cost_per_task']:.2f} / 사람 ${h['cost_per_task']:.2f}"
    if gap <= -0.15:
        return {"label": "사람 우선", "level": "warn", "why": why}
    if gap >= 0.05 or (gap >= -0.05 and (faster or cheaper)):
        return {"label": "Agent 우선", "level": "ok", "why": why}
    return {"label": "AI 초안 + 사람 검토", "level": "info", "why": why}


def unmet_demand(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT role_name, COUNT(*) AS misses, COUNT(DISTINCT task_id) AS tasks, MAX(created_at) AS last_at, "
            "GROUP_CONCAT(required_skills, '|') AS skills FROM match_misses WHERE created_at >= datetime('now', ?) "
            "GROUP BY role_name ORDER BY misses DESC", (f"-{days} days",)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        skills: dict[str, int] = {}
        for part in (r["skills"] or "").split("|"):
            try:
                for s in json.loads(part or "[]"):
                    skills[s] = skills.get(s, 0) + 1
            except ValueError:
                continue
        out.append({"role": r["role_name"], "misses": r["misses"], "tasks": r["tasks"], "last_at": r["last_at"],
                    "skills": sorted(skills, key=lambda k: -skills[k])[:6],
                    "suggestion": "Agent 만들기(AI 에이전트 생성) 또는 사람 등록·채용"})
    return out


# ------------------------------------------------------------------ 8번 스킬 수요·공급

def skill_gaps(conn: sqlite3.Connection) -> list[dict]:
    demand: dict[str, dict] = {}
    for r in conn.execute("SELECT required_skills, estimate_hours FROM tasks WHERE status != 'done'").fetchall():
        try:
            skills = json.loads(r["required_skills"] or "[]")
        except ValueError:
            continue
        for s in skills:
            key = str(s).strip().lower()
            if not key:
                continue
            d = demand.setdefault(key, {"skill": str(s).strip(), "open_tasks": 0, "hours": 0.0})
            d["open_tasks"] += 1
            d["hours"] += float(r["estimate_hours"] or 0)
    people: dict[str, list[str]] = {}
    for h in conn.execute("SELECT name, skill_tags FROM human_pool WHERE availability = 'available'").fetchall():
        for s in json.loads(h["skill_tags"] or "[]"):
            people.setdefault(str(s).strip().lower(), []).append(h["name"])
    agents: dict[str, list[str]] = {}
    for a in conn.execute("SELECT COALESCE(name, role_name) AS n, skill_tags FROM agent_pool WHERE status = 'active'").fetchall():
        try:
            tags = json.loads(a["skill_tags"] or "[]")
        except ValueError:
            tags = []
        for s in tags if isinstance(tags, list) else []:
            agents.setdefault(str(s).strip().lower(), []).append(a["n"])
    in_pool = {r[0].strip().lower() for r in conn.execute("SELECT name FROM skill_pool")}
    # 숙련 이상(수준 3+) 공급(2026-11-28, `skill_levels`)
    from hair_platform import skill_levels

    lv = skill_levels.Levels(conn)
    skilled: dict[str, int] = {}
    for h in conn.execute("SELECT id, skill_tags FROM human_pool WHERE availability = 'available'").fetchall():
        for s, v in lv.for_resource("human", h["id"], json.loads(h["skill_tags"] or "[]")).items():
            if v >= 3:
                skilled[str(s).strip().lower()] = skilled.get(str(s).strip().lower(), 0) + 1
    for a in conn.execute("SELECT id, skill_tags FROM agent_pool WHERE status = 'active'").fetchall():
        try:
            tags = json.loads(a["skill_tags"] or "[]")
        except ValueError:
            tags = []
        for s, v in lv.for_resource("agent", a["id"], tags if isinstance(tags, list) else []).items():
            if v >= 3:
                skilled[str(s).strip().lower()] = skilled.get(str(s).strip().lower(), 0) + 1
    out = []
    for key, d in demand.items():
        supply = len(people.get(key, [])) + len(agents.get(key, []))
        status = "none" if supply == 0 else ("thin" if supply == 1 and d["open_tasks"] >= 3 else "ok")
        suggestion = None
        if status != "ok":
            suggestion = ("스킬 Pool에 있음 — 맞는 Agent에 이 스킬을 붙이거나 새 Agent를 만드세요" if key in in_pool
                          else "스킬 Pool에 없음 — 사람 교육·채용, 또는 스킬 등록 후 Agent에 붙이기")
        out.append({**d, "hours": round(d["hours"], 1), "people": people.get(key, [])[:5], "agents": agents.get(key, [])[:5],
                    "supply": supply, "skilled": skilled.get(key, 0), "status": status, "suggestion": suggestion})
    order = {"none": 0, "thin": 1, "ok": 2}
    out.sort(key=lambda x: (order[x["status"]], -x["open_tasks"]))
    return out


# ------------------------------------------------------------------ 9번 팀

def team_overview(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform import resource_capacity

    load = resource_capacity.active_tasks(conn)
    caps = {r["id"]: resource_capacity.human_capacity(r) for r in conn.execute("SELECT * FROM human_pool").fetchall()}
    membership: dict[int, list[int]] = {}
    teams = conn.execute("SELECT id, name, status FROM teams WHERE is_active = 1 ORDER BY id").fetchall()
    members_by_team: dict[int, list[sqlite3.Row]] = {}
    for t in teams:
        ms = conn.execute(
            "SELECT m.member_type, m.member_id, m.role_label, COALESCE(h.name, a.name, a.role_name) AS name FROM team_members m "
            "LEFT JOIN human_pool h ON m.member_type = 'human' AND h.id = m.member_id "
            "LEFT JOIN agent_pool a ON m.member_type = 'agent' AND a.id = m.member_id WHERE m.team_id = ?", (t["id"],)).fetchall()
        members_by_team[t["id"]] = ms
        for m in ms:
            if m["member_type"] == "human":
                membership.setdefault(m["member_id"], []).append(t["id"])
    out = []
    for t in teams:
        ms = members_by_team[t["id"]]
        humans = [m for m in ms if m["member_type"] == "human"]
        active = sum(load.get(("human", m["member_id"]), 0) for m in humans)
        cap = sum(caps.get(m["member_id"], resource_capacity.DEFAULT_HUMAN_CAPACITY) for m in humans)
        overlaps = [{"name": m["name"], "teams": len(membership.get(m["member_id"], []))} for m in humans
                    if len(membership.get(m["member_id"], [])) > 1]
        open_tasks = conn.execute(
            "SELECT COUNT(DISTINCT a.task_id) FROM assignments a JOIN tasks k ON k.id = a.task_id WHERE a.resource_type = 'team' "
            "AND a.resource_id = ? AND a.status = 'confirmed' AND k.status != 'done'", (t["id"],)).fetchone()[0]
        try:
            runs = conn.execute("SELECT SUM(status = 'completed') AS ok, SUM(status = 'failed') AS bad, COUNT(*) AS n "
                                "FROM crew_runs WHERE team_id = ?", (t["id"],)).fetchone()
        except sqlite3.OperationalError:
            runs = None
        out.append({"team_id": t["id"], "name": t["name"], "humans": len(humans), "agents": len(ms) - len(humans),
                    "member_names": [m["name"] for m in ms][:8], "member_active_tasks": active, "member_capacity": cap,
                    "utilization": (active / cap) if cap else None, "open_team_tasks": open_tasks,
                    "crew_runs": (runs["n"] or 0) if runs else 0, "crew_completed": (runs["ok"] or 0) if runs else 0,
                    "crew_failed": (runs["bad"] or 0) if runs else 0, "overlaps": overlaps,
                    "overlap_warning": any(o["teams"] >= OVERLAP_WARN_TEAMS for o in overlaps)})
    return out


# ------------------------------------------------------------------ 10번 업무 분배

def _gini(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v >= 0)
    n = len(vals)
    total = sum(vals)
    if n < 2 or total == 0:
        return None
    cum = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * cum) / (n * total) - (n + 1) / n


def workload_distribution(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform.assignment import _role_matches

    people = conn.execute("SELECT id, name, role FROM human_pool WHERE availability != 'offboarded'").fetchall()
    stats: dict[int, dict] = {p["id"]: {"name": p["name"], "active": 0, "urgent": 0, "overdue": 0} for p in people}
    for r in conn.execute(
            "SELECT a.resource_id, k.priority, (k.due_date IS NOT NULL AND k.due_date < date('now', 'localtime')) AS late "
            "FROM assignments a JOIN tasks k ON k.id = a.task_id WHERE a.resource_type = 'human' AND a.status = 'confirmed' "
            "AND k.status != 'done'").fetchall():
        s = stats.get(r["resource_id"])
        if s is None:
            continue
        s["active"] += 1
        s["urgent"] += r["priority"] in ("urgent", "high")
        s["overdue"] += bool(r["late"])
    groups: dict[str, list[int]] = {}
    for p in people:
        key = next((g for g in groups if _role_matches(p["role"], g)), p["role"])
        groups.setdefault(key, []).append(p["id"])
    out = []
    for role, ids in groups.items():
        if len(ids) < 2:
            continue
        members = sorted((stats[i] for i in ids), key=lambda s: -s["active"])
        total = sum(m["active"] for m in members)
        urgent_total = sum(m["urgent"] for m in members)
        top = members[0]
        flags = []
        gini = _gini([m["active"] for m in members])
        if total >= 4 and top["active"] / total >= 0.5:
            flags.append(f"{top['name']}님이 이 역할 일의 {round(top['active'] / total * 100)}%를 맡고 있습니다")
        if len(ids) >= 3 and urgent_total >= 2:
            ut = max(members, key=lambda m: m["urgent"])
            if ut["urgent"] / urgent_total >= 0.5:
                flags.append(f"긴급·높음 우선순위 일의 {round(ut['urgent'] / urgent_total * 100)}%가 {ut['name']}님에게 몰려 있습니다")
        out.append({"role": role, "people": len(ids), "active_total": total, "gini": gini,
                    "top_share": (top["active"] / total) if total else None, "members": members, "flags": flags})
    out.sort(key=lambda g: (-(len(g["flags"])), -(g["gini"] or 0)))
    return out


def overview(conn: sqlite3.Connection) -> dict:
    return {"human_vs_ai": human_vs_ai(conn), "skills": skill_gaps(conn), "teams": team_overview(conn),
            "distribution": workload_distribution(conn)}
