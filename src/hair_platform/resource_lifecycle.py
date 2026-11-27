"""인계·결재 위임·결재 에스컬레이션·시간 기반 비용(2026-11-27, Resource Management 분석 Phase C — 4번·5번).

4번 — 퇴사·부재가 상태값 하나로 끝났다. 오프보딩은 `availability='offboarded'`만 바꿔, 확정 배정·진행 중 할 일·팀 소속·
그 사람이 상사로 걸린 결재·그 이름으로 도는 정기 실행·트리거가 그대로 남았다. 부재 중 대결자가 없어 상사가 휴가면 그 아래
결재가 모두 멈췄다.
- **인계 점검**(`handover`): 그 사람에게 남은 일을 한 화면에 모으고, 끝나지 않은 배정마다 그 사람을 뺀 매칭 1순위를 대신
  맡을 후보로 제안한다. 교체는 기존 배정 요청(`replaces_assignment_id`)으로 — 결재를 그대로 거친다. 결재는 다른 사람에게
  한 번에 넘긴다(`transfer_approvals`).
- **결재 위임**(`approval_delegations`): 기간 동안 대결자(admin·manager)가 위임한 사람 몫의 결재를 볼 수 있고 처리한다
  (결재함·결정 권한, 활동 기록에 "위임으로 결정").
- **에스컬레이션**: admin이 N일을 정하면(기본 끔) 지정 승인자에게 N일 넘게 머문 결재를 그 승인자의 상사에게(없으면 admin·
  manager 누구나) 넘긴다(자동화 루프, 활동 기록 `approval_escalated`). 단계에 들어선 시각은 `approvals.pending_since`.

5번 — 비용은 "단가 × 확정 배정 건수"였다(시간·작업량 무관, Agent 실제 사용량과 분리).
- 사람: `human_pool.hourly_rate`(USD/시간) × 그 사람 몫의 예상 시간(할 일 `estimate_hours` ÷ 배정된 사람 수) — **추정**.
- AI: 그 할 일로 기록된 실제 LLM 사용 금액(`llm_usage`, 모델 레지스트리 단가 — 단가 없는 모델이 섞이면 표시) — **실측**.
- 업무 요청 예산(`briefs.budget_usd`): 80% 이상 주의, 100% 넘으면 초과. 팀별 비용(팀에 배정된 할 일 합계).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from hair_platform import audit

BUDGET_WARN = 0.8

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    delegate_id INTEGER NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (ends_on >= starts_on), CHECK (user_id != delegate_id)
);
CREATE INDEX IF NOT EXISTS idx_approval_delegations ON approval_delegations(delegate_id, ends_on);
CREATE TABLE IF NOT EXISTS resource_settings (key TEXT PRIMARY KEY, value TEXT, updated_by INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')));
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from hair_platform.db_migrations import _ensure_column

    conn.executescript(SCHEMA)
    _ensure_column(conn, "approvals", "pending_since", "TEXT")
    _ensure_column(conn, "approvals", "escalation_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "human_pool", "hourly_rate", "REAL")
    _ensure_column(conn, "briefs", "budget_usd", "REAL")


def _today() -> str:
    return date.today().isoformat()


# ================================================================== 인계 점검

def handover(conn: sqlite3.Connection, human_id: int) -> dict:
    from hair_platform import pool
    from hair_platform.assignment import match_candidates

    h = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if h is None:
        raise LookupError("human_not_found")
    rows = conn.execute(
        "SELECT a.id AS assignment_id, a.status, t.id AS task_id, t.role_name, t.status AS task_status, t.due_date, "
        "t.required_skills FROM assignments a JOIN tasks t ON t.id = a.task_id "
        "WHERE a.resource_type = 'human' AND a.resource_id = ? AND a.status IN ('confirmed', 'requested') "
        "AND t.status != 'done' ORDER BY t.due_date IS NULL, t.due_date, t.id", (human_id,)).fetchall()

    candidates = [c for c in pool.human_pool_candidates(conn) + pool.agent_pool_candidates(conn)
                  if not (c.type == "human" and c.id == human_id)]
    work = []
    for r in rows:
        item = dict(r)
        item.pop("required_skills", None)
        if r["status"] == "confirmed":
            m = match_candidates(r["role_name"], json.loads(r["required_skills"] or "[]"), candidates)
            best = m.top[0] if m.top else None
            item["suggestion"] = ({"type": best.candidate.type, "id": best.candidate.id,
                                   "name": best.candidate.name or best.candidate.role, "score": round(best.score, 3),
                                   "notes": best.notes} if best else None)
        work.append(item)
    teams = [dict(r) for r in conn.execute(
        "SELECT t.id, t.name, m.role_label FROM team_members m JOIN teams t ON t.id = m.team_id "
        "WHERE m.member_type = 'human' AND m.member_id = ? AND t.is_active = 1", (human_id,))]
    out = {"human": {"id": h["id"], "name": h["name"], "role": h["role"], "user_id": h["user_id"],
                     "availability": h["availability"]},
           "work": work, "teams": teams, "approvals": [], "reports": [], "automations": []}
    uid = h["user_id"]
    if uid:
        out["approvals"] = [dict(r) for r in conn.execute(
            "SELECT a.id, a.subject_type, a.subject_id, a.step, a.status, a.pending_since FROM approvals a "
            "WHERE a.approver_id = ? AND a.status IN ('pending', 'waiting') ORDER BY a.id", (uid,))]
        out["reports"] = [dict(r) for r in conn.execute(
            "SELECT id, username AS name, 'user' AS kind FROM users WHERE manager_id = ? AND is_active = 1 "
            "UNION ALL SELECT id, name, 'human' AS kind FROM human_pool WHERE manager_id = ? AND availability != 'offboarded'",
            (uid, uid))]
        from hair_platform import automation_owner

        out["automations"] = automation_owner.owned(conn, uid)  # 켜진 것 + 만든 사람 때문에 멈춘 것(2026-11-28)
    out["open_count"] = (len(work) + len(teams) + len(out["approvals"]) + len(out["reports"]) + len(out["automations"]))
    return out


def transfer_approvals(conn: sqlite3.Connection, actor_id: int, from_user: int, to_user: int | None) -> int:
    """`from_user`가 지정 승인자인 대기 결재를 `to_user`(None이면 admin·manager 누구나)에게 넘긴다."""
    if to_user is not None:
        r = conn.execute("SELECT role, is_active FROM users WHERE id = ?", (to_user,)).fetchone()
        if r is None or not r["is_active"] or r["role"] not in ("admin", "manager"):
            raise ValueError("delegate_must_be_active_admin_or_manager")
    rows = conn.execute("SELECT id, requested_by FROM approvals WHERE approver_id = ? AND status IN ('pending', 'waiting')",
                        (from_user,)).fetchall()
    n = 0
    for r in rows:
        target = None if to_user == r["requested_by"] else to_user  # 자기 요청을 자기가 승인하게 되면 누구나로
        conn.execute("UPDATE approvals SET approver_id = ?, pending_since = CASE WHEN status = 'pending' "
                     "THEN datetime('now') ELSE pending_since END WHERE id = ?", (target, r["id"]))
        audit.record(conn, actor_id, "approval_transferred", "approval", r["id"],
                     before={"approver_id": from_user}, after={"approver_id": target})
        n += 1
    conn.commit()
    return n


# ================================================================== 결재 위임

def add_delegation(conn: sqlite3.Connection, actor_id: int, user_id: int, delegate_id: int, starts_on: str, ends_on: str,
                   reason: str = "") -> dict:
    try:
        s, e = date.fromisoformat(starts_on), date.fromisoformat(ends_on)
    except (TypeError, ValueError) as exc:
        raise ValueError("date_format_YYYY-MM-DD") from exc
    if e < s:
        raise ValueError("ends_before_starts")
    if user_id == delegate_id:
        raise ValueError("cannot_delegate_to_self")
    d = conn.execute("SELECT role, is_active FROM users WHERE id = ?", (delegate_id,)).fetchone()
    if d is None or not d["is_active"] or d["role"] not in ("admin", "manager"):
        raise ValueError("delegate_must_be_active_admin_or_manager")
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise LookupError("user_not_found")
    cur = conn.execute("INSERT INTO approval_delegations (user_id, delegate_id, starts_on, ends_on, reason, created_by) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (user_id, delegate_id, s.isoformat(), e.isoformat(),
                                                    (reason or "").strip()[:200], actor_id))
    audit.record(conn, actor_id, "approval_delegation_added", "users", user_id,
                 after={"delegate_id": delegate_id, "starts_on": s.isoformat(), "ends_on": e.isoformat()})
    conn.commit()
    return {"id": cur.lastrowid, "user_id": user_id, "delegate_id": delegate_id, "starts_on": s.isoformat(),
            "ends_on": e.isoformat()}


def delete_delegation(conn: sqlite3.Connection, actor_id: int, delegation_id: int) -> dict:
    row = conn.execute("SELECT * FROM approval_delegations WHERE id = ?", (delegation_id,)).fetchone()
    if row is None:
        raise LookupError("delegation_not_found")
    conn.execute("DE" "LETE FROM approval_delegations WHERE id = ?", (delegation_id,))
    audit.record(conn, actor_id, "approval_delegation_removed", "users", row["user_id"],
                 before={"delegate_id": row["delegate_id"], "starts_on": row["starts_on"], "ends_on": row["ends_on"]})
    conn.commit()
    return dict(row)


def list_delegations(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    sql = ("SELECT d.*, u.username AS user_name, g.username AS delegate_name FROM approval_delegations d "
           "JOIN users u ON u.id = d.user_id JOIN users g ON g.id = d.delegate_id WHERE d.ends_on >= ?")
    params: list = [_today()]
    if user_id is not None:
        sql += " AND (d.user_id = ? OR d.delegate_id = ?)"
        params += [user_id, user_id]
    return [dict(r) for r in conn.execute(sql + " ORDER BY d.starts_on", params)]


def delegators_for(conn: sqlite3.Connection, delegate_id: int) -> list[int]:
    """오늘 `delegate_id`에게 결재를 맡긴 사람들."""
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM approval_delegations WHERE delegate_id = ? AND starts_on <= ? AND ends_on >= ?",
            (delegate_id, _today(), _today()))]
    except sqlite3.OperationalError:
        return []


# ================================================================== 에스컬레이션

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        r = conn.execute("SELECT value FROM resource_settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def escalation_days(conn: sqlite3.Connection) -> int | None:
    v = get_setting(conn, "approval_escalation_days")
    return int(v) if v else None


def set_escalation_days(conn: sqlite3.Connection, actor_id: int, days: int | None) -> dict:
    if days is not None and not 1 <= int(days) <= 60:
        raise ValueError("escalation_days_out_of_range")
    before = escalation_days(conn)
    conn.execute("INSERT INTO resource_settings (key, value, updated_by) VALUES ('approval_escalation_days', ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
                 "updated_at = datetime('now')", (str(int(days)) if days else None, actor_id))
    audit.record(conn, actor_id, "approval_escalation_policy_set", "resource_settings", None,
                 before={"days": before}, after={"days": days})
    conn.commit()
    return {"approval_escalation_days": days}


def escalate_overdue(conn: sqlite3.Connection) -> int:
    """지정 승인자에게 N일 넘게 머문 결재를 상사(없으면 누구나)에게 넘긴다(자동화 루프). 넘긴 수."""
    days = escalation_days(conn)
    if not days:
        return 0
    rows = conn.execute(
        "SELECT a.id, a.approver_id, a.requested_by, u.manager_id FROM approvals a LEFT JOIN users u ON u.id = a.approver_id "
        "WHERE a.status = 'pending' AND a.approver_id IS NOT NULL AND a.pending_since IS NOT NULL "
        "AND a.pending_since <= datetime('now', ?)", (f"-{days} days",)).fetchall()
    n = 0
    for r in rows:
        target = r["manager_id"]
        if target is not None:
            m = conn.execute("SELECT is_active FROM users WHERE id = ?", (target,)).fetchone()
            if m is None or not m["is_active"] or target == r["requested_by"]:
                target = None
        conn.execute("UPDATE approvals SET approver_id = ?, pending_since = datetime('now'), "
                     "escalation_count = escalation_count + 1 WHERE id = ?", (target, r["id"]))
        audit.record(conn, r["requested_by"], "approval_escalated", "approval", r["id"],
                     before={"approver_id": r["approver_id"]}, after={"approver_id": target, "after_days": days})
        n += 1
    if n:
        conn.commit()
    return n


# ================================================================== 시간 기반 비용

def _ai_cost_by_task(conn: sqlite3.Connection) -> dict[int, dict]:
    out: dict[int, dict] = {}
    try:
        rows = conn.execute(
            "SELECT u.task_id, u.prompt_tokens, u.completion_tokens, m.price_input_per_1m, m.price_output_per_1m "
            "FROM llm_usage u LEFT JOIN model_registry m ON m.provider = u.provider AND m.name = u.model "
            "WHERE u.task_id IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        b = out.setdefault(r["task_id"], {"ai_cost": 0.0, "ai_partial": False, "tokens": 0})
        b["tokens"] += r["prompt_tokens"] + r["completion_tokens"]
        if r["price_input_per_1m"] is None or r["price_output_per_1m"] is None:
            b["ai_partial"] = True
        else:
            b["ai_cost"] += (r["prompt_tokens"] / 1e6 * r["price_input_per_1m"]
                             + r["completion_tokens"] / 1e6 * r["price_output_per_1m"])
    return out


def task_costs(conn: sqlite3.Connection) -> dict[int, dict]:
    """할 일별 {labor_estimated, labor_missing_rate, labor_missing_estimate, ai_cost, ai_partial, total}."""
    ai = _ai_cost_by_task(conn)
    out: dict[int, dict] = {}
    rows = conn.execute(
        "SELECT t.id AS task_id, t.estimate_hours, h.hourly_rate, "
        "(SELECT COUNT(*) FROM assignments a2 WHERE a2.task_id = t.id AND a2.status = 'confirmed' AND a2.resource_type = 'human') AS people "
        "FROM tasks t LEFT JOIN assignments a ON a.task_id = t.id AND a.status = 'confirmed' AND a.resource_type = 'human' "
        "LEFT JOIN human_pool h ON h.id = a.resource_id").fetchall()
    for r in rows:
        b = out.setdefault(r["task_id"], {"labor_estimated": 0.0, "labor_missing_rate": 0, "labor_missing_estimate": False})
        if not r["people"]:
            continue
        if r["estimate_hours"] is None:
            b["labor_missing_estimate"] = True
            continue
        if r["hourly_rate"] is None:
            b["labor_missing_rate"] += 1
            continue
        b["labor_estimated"] += float(r["hourly_rate"]) * float(r["estimate_hours"]) / r["people"]
    for tid, b in out.items():
        a = ai.get(tid, {"ai_cost": 0.0, "ai_partial": False, "tokens": 0})
        b.update(a)
        b["total"] = b["labor_estimated"] + b["ai_cost"]
    return out


def set_hourly_rate(conn: sqlite3.Connection, actor_id: int, human_id: int, rate: float | None) -> dict:
    row = conn.execute("SELECT hourly_rate FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if row is None:
        raise LookupError("human_not_found")
    if rate is not None and not 0 <= float(rate) <= 10000:
        raise ValueError("hourly_rate_out_of_range")
    conn.execute("UPDATE human_pool SET hourly_rate = ? WHERE id = ?", (rate, human_id))
    audit.record(conn, actor_id, "human_hourly_rate_set", "human_pool", human_id,
                 before={"hourly_rate": row["hourly_rate"]}, after={"hourly_rate": rate})
    conn.commit()
    return {"id": human_id, "hourly_rate": rate}


def set_brief_budget(conn: sqlite3.Connection, actor_id: int, brief_id: int, budget: float | None) -> dict:
    row = conn.execute("SELECT budget_usd FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if row is None:
        raise LookupError("brief_not_found")
    if budget is not None and not 0 < float(budget) <= 10_000_000:
        raise ValueError("budget_out_of_range")
    conn.execute("UPDATE briefs SET budget_usd = ? WHERE id = ?", (budget, brief_id))
    audit.record(conn, actor_id, "brief_budget_set", "briefs", brief_id, before={"budget_usd": row["budget_usd"]},
                 after={"budget_usd": budget})
    conn.commit()
    return {"brief_id": brief_id, "budget_usd": budget}


def cost_overview(conn: sqlite3.Connection) -> dict:
    costs = task_costs(conn)
    briefs = []
    for b in conn.execute("SELECT id, substr(raw_text, 1, 60) AS title, budget_usd FROM briefs ORDER BY id DESC").fetchall():
        tids = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE brief_id = ?", (b["id"],))]
        cs = [costs[t] for t in tids if t in costs]
        labor = sum(c["labor_estimated"] for c in cs)
        ai = sum(c["ai_cost"] for c in cs)
        total = labor + ai
        if not tids or (total == 0 and b["budget_usd"] is None):
            continue
        ratio = (total / b["budget_usd"]) if b["budget_usd"] else None
        briefs.append({"brief_id": b["id"], "title": b["title"], "tasks": len(tids), "labor_estimated": round(labor, 2),
                       "ai_cost": round(ai, 4), "total": round(total, 2), "budget_usd": b["budget_usd"], "ratio": ratio,
                       "status": None if ratio is None else ("over" if ratio > 1 else "warn" if ratio >= BUDGET_WARN else "ok"),
                       "ai_partial": any(c["ai_partial"] for c in cs),
                       "missing_rate": sum(c["labor_missing_rate"] for c in cs),
                       "missing_estimate": sum(1 for c in cs if c["labor_missing_estimate"])})
    briefs.sort(key=lambda x: (x["status"] != "over", x["status"] != "warn", -x["total"]))
    teams = []
    for t in conn.execute("SELECT id, name FROM teams WHERE is_active = 1").fetchall():
        tids = [r[0] for r in conn.execute("SELECT DISTINCT task_id FROM assignments WHERE resource_type = 'team' "
                                           "AND resource_id = ? AND status = 'confirmed'", (t["id"],))]
        cs = [costs[x] for x in tids if x in costs]
        if not tids:
            continue
        teams.append({"team_id": t["id"], "name": t["name"], "tasks": len(tids),
                      "ai_cost": round(sum(c["ai_cost"] for c in cs), 4),
                      "labor_estimated": round(sum(c["labor_estimated"] for c in cs), 2)})
    people_missing_rate = conn.execute("SELECT COUNT(*) FROM human_pool WHERE availability != 'offboarded' "
                                       "AND hourly_rate IS NULL").fetchone()[0]
    return {"briefs": briefs, "teams": teams, "people_missing_rate": people_missing_rate, "budget_warn": BUDGET_WARN}
