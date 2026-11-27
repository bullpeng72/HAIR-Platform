"""스킬 숙련도(2026-11-28, Resource Management 분석 3번 남은 항목 — "스킬이 깊이 없이 나열만").

`skill_tags`는 수준·근거 없는 목록이라 "적어 둔 스킬"과 "검증된 스킬"을 구별할 수 없었다. 스킬마다 수준(1 입문 · 2 실무 ·
3 숙련 · 4 전문)을 둔다.

- **근거 수준**(자동): 그 사람·Agent가 그 스킬을 요구한 할 일에서 낸 결과물 중 **사람이 결재로 승인한** 것의 수(최근 180일) —
  1~2건 실무, 3~9건 숙련, 10건 이상 전문.
- **확인 수준**: admin·manager가 정한 값(검증). **본인 기재**: 구성원이 자기 스킬에 적은 값(검증 전 — 화면에 표시).
- **적용 수준** = max(확인, 근거). 둘 다 없으면 본인 기재. 매칭은 적용 수준이 있는 후보에만 작은 가점(요구 스킬 평균 수준 ÷ 4
  × 0.1, `assignment`)을 준다 — 수준 정보가 없는 후보는 예전 점수 그대로.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit

LEVELS = {1: "입문", 2: "실무", 3: "숙련", 4: "전문"}
EVIDENCE_DAYS = 180
DEPTH_WEIGHT = 0.1

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_skill_levels (
    resource_type TEXT NOT NULL CHECK (resource_type IN ('human', 'agent')),
    resource_id INTEGER NOT NULL,
    skill TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    source TEXT NOT NULL CHECK (source IN ('self', 'verified')),
    set_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resource_type, resource_id, skill, source)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _norm(skill: str) -> str:
    return (skill or "").strip().lower()


def _evidence_level(n: int) -> int | None:
    if n >= 10:
        return 4
    if n >= 3:
        return 3
    if n >= 1:
        return 2
    return None


def evidence_counts(conn: sqlite3.Connection) -> dict[tuple[str, int, str], int]:
    """(resource_type, id, 스킬 소문자) → 사람이 승인한 결과물 수(최근 180일, 그 스킬을 요구한 할 일)."""
    out: dict[tuple[str, int, str], int] = {}
    try:
        rows = conn.execute(
            "SELECT d.author_type, d.author_id, t.required_skills FROM deliverables d JOIN tasks t ON t.id = d.task_id "
            "WHERE d.status = 'approved' AND d.created_at >= datetime('now', ?) AND ("
            " EXISTS (SELECT 1 FROM approvals a WHERE a.subject_type = 'deliverable' AND a.subject_id = d.id AND a.status = 'approved')"
            " OR EXISTS (SELECT 1 FROM pipeline_run_stages rs JOIN approvals a ON a.subject_type = 'pipeline_run_stage' "
            "   AND a.subject_id = rs.id AND a.status = 'approved' WHERE rs.deliverable_id = d.id))",
            (f"-{EVIDENCE_DAYS} days",)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        rtype = "human" if r["author_type"] == "human" else "agent"
        try:
            skills = json.loads(r["required_skills"] or "[]")
        except ValueError:
            continue
        for s in skills:
            key = (rtype, r["author_id"], _norm(str(s)))
            out[key] = out.get(key, 0) + 1
    return out


def _stored(conn: sqlite3.Connection) -> dict[tuple[str, int, str], dict[str, int]]:
    out: dict[tuple[str, int, str], dict[str, int]] = {}
    try:
        for r in conn.execute("SELECT resource_type, resource_id, skill, level, source FROM resource_skill_levels"):
            out.setdefault((r["resource_type"], r["resource_id"], _norm(r["skill"])), {})[r["source"]] = r["level"]
    except sqlite3.OperationalError:
        pass
    return out


class Levels:
    """매칭·화면에서 한 번 읽어 여러 후보에 쓴다."""

    def __init__(self, conn: sqlite3.Connection):
        self.evidence = evidence_counts(conn)
        self.stored = _stored(conn)

    def detail(self, rtype: str, rid: int, skill: str) -> dict:
        key = (rtype, rid, _norm(skill))
        st = self.stored.get(key, {})
        n = self.evidence.get(key, 0)
        ev = _evidence_level(n)
        verified = st.get("verified")
        self_lv = st.get("self")
        candidates = [v for v in (verified, ev) if v]
        level = max(candidates) if candidates else self_lv
        source = ("확인" if verified and verified == level else "근거" if ev and ev == level else "본인 기재" if self_lv else None)
        return {"skill": skill, "level": level, "label": LEVELS.get(level) if level else None, "source": source,
                "evidence": n, "evidence_level": ev, "verified": verified, "self": self_lv,
                "unverified": source == "본인 기재"}

    def for_resource(self, rtype: str, rid: int, skills: list[str]) -> dict[str, int]:
        out = {}
        for s in skills:
            d = self.detail(rtype, rid, s)
            if d["level"]:
                out[s] = d["level"]
        return out


def set_level(conn: sqlite3.Connection, actor_id: int, rtype: str, rid: int, skill: str, level: int | None,
              source: str) -> dict:
    skill = (skill or "").strip()
    if rtype not in ("human", "agent") or source not in ("self", "verified") or not skill:
        raise ValueError("invalid_skill_level_request")
    table, col = ("human_pool", "skill_tags") if rtype == "human" else ("agent_pool", "skill_tags")
    row = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (rid,)).fetchone()  # noqa: S608 — 고정 이름
    if row is None:
        raise LookupError("resource_not_found")
    if _norm(skill) not in {_norm(s) for s in json.loads(row[col] or "[]")}:
        raise ValueError("skill_not_in_resource_tags")
    before = conn.execute("SELECT level FROM resource_skill_levels WHERE resource_type = ? AND resource_id = ? AND skill = ? "
                          "AND source = ?", (rtype, rid, skill, source)).fetchone()
    if level is None:
        conn.execute("DE" "LETE FROM resource_skill_levels WHERE resource_type = ? AND resource_id = ? AND skill = ? AND source = ?",
                     (rtype, rid, skill, source))
    else:
        if level not in LEVELS:
            raise ValueError("level_must_be_1_to_4")
        conn.execute("INSERT INTO resource_skill_levels (resource_type, resource_id, skill, level, source, set_by) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(resource_type, resource_id, skill, source) DO UPDATE SET "
                     "level = excluded.level, set_by = excluded.set_by, updated_at = datetime('now')",
                     (rtype, rid, skill, level, source, actor_id))
    audit.record(conn, actor_id, "skill_level_set", f"{rtype}_pool" if rtype == "agent" else "human_pool", rid,
                 before={"skill": skill, "source": source, "level": before["level"] if before else None},
                 after={"skill": skill, "source": source, "level": level})
    conn.commit()
    return Levels(conn).detail(rtype, rid, skill)


def resource_levels(conn: sqlite3.Connection, rtype: str, rid: int) -> list[dict]:
    table = "human_pool" if rtype == "human" else "agent_pool"
    row = conn.execute(f"SELECT skill_tags FROM {table} WHERE id = ?", (rid,)).fetchone()  # noqa: S608 — 고정 이름
    if row is None:
        raise LookupError("resource_not_found")
    lv = Levels(conn)
    return [lv.detail(rtype, rid, s) for s in json.loads(row["skill_tags"] or "[]")]


def depth_score(levels: dict[str, int] | None, required: list[str]) -> float | None:
    """요구 스킬의 평균 수준 ÷ 4(0~1). 요구 스킬 중 수준 정보가 하나도 없으면 None."""
    if not levels or not required:
        return None
    norm = {_norm(k): v for k, v in levels.items()}
    vals = [norm[_norm(s)] for s in required if _norm(s) in norm]
    if not vals:
        return None
    return sum(vals) / len(vals) / 4
