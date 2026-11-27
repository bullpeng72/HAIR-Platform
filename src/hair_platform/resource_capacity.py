"""자원 용량·부재 기간·부하(2026-11-27, Resource Management 분석 Phase A — 1번 "용량이 켬/끔 한 칸, 매칭이 지금
부하를 보지 않음").

지금까지 사람의 가용성은 `available`·`unavailable`·`offboarded` 한 칸뿐이었고, 매칭은 그 값을 1·0으로만 썼다. 진행 중인
일이 10건인 사람과 0건인 사람이 같은 점수였고(대시보드의 부하 집계는 매칭에 들어가지 않았다), 휴가·출장 같은 기간 부재는
적을 곳이 없었으며, Agent는 늘 '가능'으로 보였다(실제로는 로컬 모델이 요청을 하나씩 처리해 줄을 선다).

- **사람 용량**: `human_pool.max_concurrent_tasks` — 동시에 맡을 수 있는 할 일 수. 비우면 `DEFAULT_HUMAN_CAPACITY`(3).
- **부하**: 확정 배정 중 아직 끝나지 않은(`tasks.status != 'done'`) 할 일 수. 사람·Agent 모두.
- **부재 기간**: `resource_absences`(사람, 시작일~종료일, 사유). 오늘이 기간 안이면 매칭에서 '불가', 2주 안에 시작하면 알림.
- **Agent 대기**: 그 Agent가 쓰는 모델이 로컬(Ollama)이면 지금 맡길 때 파이프라인·긴 작업 줄의 예상 대기(`capacity`).
- 매칭은 이 값으로 가용성 항목을 **남은 용량 비율**로 바꾼다(`assignment.match_candidates`) — 가중치(가용성·적합도·비용)는
  그대로이고, 용량을 다 쓴 사람은 후보에 남되 가용성 0으로 내려가며 '용량 초과'로 표시된다. 배정 요청은 막지 않고 경고를
  돌려주고 활동 기록에 남긴다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from hair_platform import audit

DEFAULT_HUMAN_CAPACITY = 3
MAX_CAPACITY = 50
UPCOMING_ABSENCE_DAYS = 14
AGENT_WAIT_FLOOR = 0.2           # 대기가 아무리 길어도 가용성은 이 아래로 내리지 않는다(대기는 결국 끝난다)
AGENT_WAIT_FULL_PENALTY = 7200   # 2시간 대기면 바닥까지

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('human')),
    resource_id INTEGER NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (ends_on >= starts_on)
);
CREATE INDEX IF NOT EXISTS idx_resource_absences ON resource_absences(resource_type, resource_id, ends_on);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _today() -> date:
    return date.today()


# ------------------------------------------------------------------ 부하

def active_tasks(conn: sqlite3.Connection) -> dict[tuple[str, int], int]:
    """(resource_type, resource_id) → 확정 배정 중 끝나지 않은 할 일 수."""
    rows = conn.execute(
        "SELECT a.resource_type, a.resource_id, COUNT(DISTINCT a.task_id) AS n FROM assignments a "
        "JOIN tasks t ON t.id = a.task_id WHERE a.status = 'confirmed' AND t.status != 'done' "
        "AND a.resource_type IN ('human', 'agent') GROUP BY a.resource_type, a.resource_id").fetchall()
    return {(r["resource_type"], r["resource_id"]): r["n"] for r in rows}


def human_capacity(row) -> int:
    keys = row.keys()
    v = row["max_concurrent_tasks"] if "max_concurrent_tasks" in keys else None
    return int(v) if v else DEFAULT_HUMAN_CAPACITY


# ------------------------------------------------------------------ 부재 기간

def _absence_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            "SELECT * FROM resource_absences WHERE resource_type = 'human' AND ends_on >= ? ORDER BY starts_on",
            (_today().isoformat(),)).fetchall()
    except sqlite3.OperationalError:
        return []


def absence_status(conn: sqlite3.Connection) -> dict[int, dict]:
    """사람 id → {"absent_until": 오늘 부재면 종료일, "absent_from": 2주 안에 시작하는 부재의 시작일, "reason"}."""
    today = _today()
    horizon = today + timedelta(days=UPCOMING_ABSENCE_DAYS)
    out: dict[int, dict] = {}
    for r in _absence_rows(conn):
        s, e = date.fromisoformat(r["starts_on"]), date.fromisoformat(r["ends_on"])
        cur = out.setdefault(r["resource_id"], {"absent_until": None, "absent_from": None, "reason": ""})
        if s <= today <= e:
            if cur["absent_until"] is None or r["ends_on"] > cur["absent_until"]:
                cur["absent_until"] = r["ends_on"]
                cur["reason"] = r["reason"]
        elif today < s <= horizon and (cur["absent_from"] is None or r["starts_on"] < cur["absent_from"]):
            cur["absent_from"] = r["starts_on"]
            cur["reason"] = cur["reason"] or r["reason"]
    return out


def list_absences(conn: sqlite3.Connection, human_id: int | None = None, include_past: bool = False) -> list[dict]:
    sql = ("SELECT a.*, h.name AS human_name FROM resource_absences a JOIN human_pool h ON h.id = a.resource_id "
           "WHERE a.resource_type = 'human'")
    params: list = []
    if human_id is not None:
        sql += " AND a.resource_id = ?"
        params.append(human_id)
    if not include_past:
        sql += " AND a.ends_on >= ?"
        params.append(_today().isoformat())
    return [dict(r) for r in conn.execute(sql + " ORDER BY a.starts_on", params)]


def add_absence(conn: sqlite3.Connection, actor_id: int, human_id: int, starts_on: str, ends_on: str, reason: str = "") -> dict:
    if conn.execute("SELECT 1 FROM human_pool WHERE id = ?", (human_id,)).fetchone() is None:
        raise LookupError("human_not_found")
    try:
        s, e = date.fromisoformat(starts_on), date.fromisoformat(ends_on)
    except (TypeError, ValueError) as exc:
        raise ValueError("date_format_YYYY-MM-DD") from exc
    if e < s:
        raise ValueError("ends_before_starts")
    if (e - s).days > 366:
        raise ValueError("absence_longer_than_a_year")
    cur = conn.execute(
        "INSERT INTO resource_absences (resource_type, resource_id, starts_on, ends_on, reason, created_by) "
        "VALUES ('human', ?, ?, ?, ?, ?)", (human_id, s.isoformat(), e.isoformat(), (reason or "").strip()[:200], actor_id))
    aid = cur.lastrowid
    audit.record(conn, actor_id, "resource_absence_added", "human_pool", human_id,
                 after={"absence_id": aid, "starts_on": s.isoformat(), "ends_on": e.isoformat(), "reason": reason})
    conn.commit()
    return {"id": aid, "resource_id": human_id, "starts_on": s.isoformat(), "ends_on": e.isoformat(), "reason": reason}


def delete_absence(conn: sqlite3.Connection, actor_id: int, absence_id: int) -> int:
    row = conn.execute("SELECT * FROM resource_absences WHERE id = ?", (absence_id,)).fetchone()
    if row is None:
        raise LookupError("absence_not_found")
    conn.execute("DE" "LETE FROM resource_absences WHERE id = ?", (absence_id,))
    audit.record(conn, actor_id, "resource_absence_removed", "human_pool", row["resource_id"],
                 before={"starts_on": row["starts_on"], "ends_on": row["ends_on"], "reason": row["reason"]})
    conn.commit()
    return row["resource_id"]


def set_capacity(conn: sqlite3.Connection, actor_id: int, human_id: int, value: int | None) -> dict:
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    if row is None:
        raise LookupError("human_not_found")
    if value is not None and not (1 <= int(value) <= MAX_CAPACITY):
        raise ValueError("capacity_out_of_range")
    conn.execute("UPDATE human_pool SET max_concurrent_tasks = ? WHERE id = ?", (value, human_id))
    audit.record(conn, actor_id, "human_capacity_set", "human_pool", human_id,
                 before={"max_concurrent_tasks": row["max_concurrent_tasks"]}, after={"max_concurrent_tasks": value})
    conn.commit()
    return {"id": human_id, "max_concurrent_tasks": value, "effective_capacity": value or DEFAULT_HUMAN_CAPACITY}


# ------------------------------------------------------------------ Agent 대기

class AgentWait:
    """매칭 한 번에 한 번만 계산 — 로컬 모델(Ollama)을 쓰는 Agent의 지금 예상 대기(초)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._wait: float | None | bool = False  # False = 아직 계산 안 함
        self._default_provider: str | None = None

    def _platform_provider(self) -> str | None:
        if self._default_provider is None:
            from hair_platform import llm

            try:
                self._default_provider = llm.resolve_provider_and_model(None, None, conn=self.conn)[0]
            except Exception:  # noqa: BLE001 — 모델 설정이 없어도 매칭은 된다
                self._default_provider = ""
        return self._default_provider or None

    def for_agent(self, row) -> float | None:
        provider = (row["llm_provider"] if "llm_provider" in row.keys() else None) or self._platform_provider()
        if provider != "ollama":
            return None
        if self._wait is False:
            from hair_platform import capacity

            try:
                self._wait = capacity.report(self.conn)["pipeline_queue_wait_seconds"]
            except Exception:  # noqa: BLE001
                self._wait = None
        return self._wait if isinstance(self._wait, (int, float)) else None


def availability_factor(c) -> tuple[float, list[str], bool]:
    """후보 하나의 가용성 값(0~1)·설명·용량 초과 여부. 부하 정보가 없으면 예전처럼 1·0."""
    notes: list[str] = []
    if getattr(c, "absent_until", None):
        notes.append(f"부재 중(~{c.absent_until})")
    if getattr(c, "absent_from", None):
        notes.append(f"{c.absent_from}부터 부재")
    if not c.is_available:
        return 0.0, notes or ["가용 아님"], False
    over = False
    factor = 1.0
    if c.type == "human" and c.capacity:
        active = c.active_tasks or 0
        remaining = max(0, c.capacity - active)
        factor = remaining / c.capacity
        over = active >= c.capacity
        notes.append(f"진행 중 {active}/{c.capacity}" + (" — 용량 초과" if active > c.capacity else (" — 꽉 참" if over else "")))
    elif c.type == "agent":
        if c.active_tasks:
            notes.append(f"맡은 할 일 {c.active_tasks}건")
        wait = getattr(c, "queue_wait_seconds", None)
        if wait:
            factor = max(AGENT_WAIT_FLOOR, 1.0 - wait / AGENT_WAIT_FULL_PENALTY)
            notes.append(f"모델 대기 약 {max(1, round(wait / 60))}분")
    return factor, notes, over


def assignment_warnings(conn: sqlite3.Connection, resource_type: str, resource_id: int) -> list[str]:
    """배정 요청 때 돌려줄 경고(막지는 않는다)."""
    if resource_type != "human":
        return []
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (resource_id,)).fetchone()
    if row is None:
        return []
    out = []
    cap = human_capacity(row)
    active = active_tasks(conn).get(("human", resource_id), 0)
    if active >= cap:
        out.append(f"{row['name']}님은 이미 끝나지 않은 할 일 {active}건을 맡고 있습니다(동시 용량 {cap}건) — 확정되면 용량을 넘깁니다.")
    st = absence_status(conn).get(resource_id)
    if st and st["absent_until"]:
        out.append(f"{row['name']}님은 {st['absent_until']}까지 부재입니다" + (f"({st['reason']})" if st["reason"] else "") + ".")
    elif st and st["absent_from"]:
        out.append(f"{row['name']}님은 {st['absent_from']}부터 부재 예정입니다" + (f"({st['reason']})" if st["reason"] else "") + ".")
    if row["availability"] != "available":
        out.append(f"{row['name']}님은 '가용 아님'으로 표시돼 있습니다.")
    return out
