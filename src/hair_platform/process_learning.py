"""프로세스 학습·변경 관리(2026-11-27, AI Native 분석 2단계 — 2번 "학습 루프", 10번 "변경 기록·효과 비교").

학습 루프 — 지금까지 사람이 적은 반려 사유는 **그 실행의 재시도**에만 들어가고, 다음 실행은 같은 실수를 되풀이했다.
여기서는 단계마다 "배운 점"을 모은다.
- 사람 반려 사유(결재함에서 파이프라인 단계를 반려하며 적은 글) → 그대로 개선 제안 후보. 같은 글이 또 나오면 횟수만 는다.
- 같은 품질 기준(Gate A-G)에서 최근 자동 재시도·사람 검토로 넘어간 실패가 3번 이상, 시도의 30% 이상이면 → 그 기준에
  맞춘 지시문 제안(되풀이되는 실패 패턴).
운영자(admin·manager)가 제안을 **적용**하면(글을 고쳐서 적용 가능) 그 뒤 모든 실행의 그 단계 지시문 끝에 붙는다. 자동으로
적용하지는 않는다 — 사람이 적은 한 번의 사유가 모든 실행에 퍼지기 전에 사람이 확인한다(HITL 원칙).

변경 관리 — 파이프라인 단계 설정을 바꾸거나 배운 점을 켜고 끄면 `process_changes`에 남는다. 변경마다 그 전 30일과 그 뒤
(다음 변경까지, 최대 30일) 실행을 같은 지표로 비교하고(`process_metrics.window_summary`), 단계 설정 변경과 배운 점 적용은
되돌릴 수 있다(그 뒤에 같은 값이 다시 바뀌었으면 되돌리지 않는다).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from hair_platform import audit, process_metrics

MAX_ACTIVE_PER_STAGE = 8
MAX_LESSON_CHARS = 300
PROMPT_BUDGET_CHARS = 1500
PATTERN_LOOKBACK_DAYS = 60
PATTERN_MIN_FAILURES = 3
PATTERN_MIN_SHARE = 0.3
EFFECT_WINDOW_DAYS = 30
_MIN_EFFECT_SAMPLE = 3

GATE_LESSONS = {
    "A": ("목표 달성", "작성을 마치기 전에 요청의 요구사항을 하나씩 대조해 빠진 항목이 없는지 확인하세요."),
    "B": ("행동 무결성", "지시된 범위·형식을 벗어나지 말고, 정해진 절차를 건너뛰지 마세요."),
    "C": ("신뢰성", "숫자·인용·사실은 근거가 있는 것만 쓰고, 확실하지 않으면 확실하지 않다고 밝히세요."),
    "D": ("응답 시간", "불필요하게 길게 쓰지 말고 핵심부터 간결하게 작성하세요."),
    "E": ("보안", "개인정보·비밀값·내부 식별 정보를 결과에 넣지 마세요."),
    "F": ("협업", "앞 단계 결과를 이어받아 쓰고, 다음 단계가 그대로 쓸 수 있게 정해진 구조를 지키세요."),
    "G": ("근거 제시", "주장마다 근거(출처·자료·계산 과정)를 함께 적으세요."),
}

# 되돌리기·비교를 위해 단계 설정 값을 API 입력 형태로 맞춘다(`pipeline_runtime.update_stage`가 받는 형태).
_BOOL_FIELDS = ("requires_human_approval", "is_primary_deliverable", "checks_requirements_traceability",
                "is_requirements_source", "strict_review")
FIELD_LABELS = {
    "name": "단계 이름", "instructions": "지시문", "gate_ids": "품질 기준", "min_gate_pass_ratio": "통과 비율",
    "max_retry": "최대 재시도", "requires_human_approval": "사람 승인", "is_primary_deliverable": "최종 결과물",
    "checks_requirements_traceability": "요구사항 추적 검사", "is_requirements_source": "요구사항 원본",
    "on_fail_return_stage_id": "실패 시 돌아갈 단계", "output_content_type": "결과 형식", "expected_output": "기대 결과",
    "strict_review": "엄격 검토", "required_tools": "필수 도구", "llm_provider": "모델 제공자", "llm_model": "모델",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL,  -- FK 없음: 기본 구성 동기화·단계 삭제가 이 표 때문에 막히지 않게(삭제 때 함께 정리)
    text TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('human_rejection', 'gate_pattern', 'manual')),
    pattern_key TEXT,
    evidence TEXT,
    hits INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'active', 'dismissed', 'retired')),
    source_run_stage_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage_lessons_stage ON stage_lessons(stage_id, status);
CREATE TABLE IF NOT EXISTS process_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    stage_id INTEGER,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    lesson_id INTEGER,
    actor_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    reverted_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_process_changes_template ON process_changes(template_id, created_at);
CREATE TABLE IF NOT EXISTS stage_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL,
    run_stage_id INTEGER NOT NULL UNIQUE,
    excerpt TEXT NOT NULL,
    full_chars INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'active', 'dismissed', 'retired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_at TEXT,
    decided_by INTEGER,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage_examples_stage ON stage_examples(stage_id, status);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _norm_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _stage(conn: sqlite3.Connection, stage_id: int):
    return conn.execute(
        "SELECT id, pipeline_template_id, name, seq_order FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()


# ---------------------------------------------------------------- 학습: 모으기

def capture_rejection(conn: sqlite3.Connection, run_stage_id: int, reason: str | None) -> int | None:
    """결재함에서 파이프라인 단계를 반려하며 적은 사유를 그 단계의 개선 제안 후보로 남긴다(커밋은 호출자)."""
    text = (reason or "").strip()
    if len(_norm_text(text)) < 4:  # "다시"·"별로" 같은 짧은 말은 다음 실행에 줄 지시가 못 된다
        return None
    row = conn.execute("SELECT stage_id FROM pipeline_run_stages WHERE id = ?", (run_stage_id,)).fetchone()
    if row is None:
        return None
    text = text[:MAX_LESSON_CHARS]
    key = "reject:" + _norm_text(text)[:120]
    existing = conn.execute(
        "SELECT id, status FROM stage_lessons WHERE stage_id = ? AND pattern_key = ?", (row["stage_id"], key)
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE stage_lessons SET hits = hits + 1, last_seen_at = datetime('now'), source_run_stage_id = ? WHERE id = ?",
            (run_stage_id, existing["id"]),
        )
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO stage_lessons (stage_id, text, source, pattern_key, evidence, source_run_stage_id) "
        "VALUES (?, ?, 'human_rejection', ?, ?, ?)",
        (row["stage_id"], text, key, json.dumps({"kind": "human_rejection"}, ensure_ascii=False), run_stage_id),
    )
    return cur.lastrowid


def refresh_gate_patterns(conn: sqlite3.Connection, template_id: int | None = None) -> None:
    """최근 실패 기록에서 되풀이되는 품질 기준 미달을 찾아 개선 제안으로 올린다(이미 무시·적용한 것은 다시 만들지 않는다)."""
    where = "rs.created_at >= datetime('now', ?)"
    params: list = [f"-{PATTERN_LOOKBACK_DAYS} days"]
    if template_id is not None:
        where += " AND s.pipeline_template_id = ?"
        params.append(template_id)
    rows = conn.execute(
        "SELECT rs.stage_id, rs.status, rs.gate_scores FROM pipeline_run_stages rs "
        f"JOIN pipeline_stages s ON s.id = rs.stage_id WHERE {where}",
        params,
    ).fetchall()
    attempts: dict[int, int] = {}
    fails: dict[tuple[int, str], int] = {}
    for r in rows:
        attempts[r["stage_id"]] = attempts.get(r["stage_id"], 0) + 1
        if r["status"] not in ("failed_retrying", "escalated") or not r["gate_scores"]:
            continue
        try:
            scores = json.loads(r["gate_scores"])
        except (TypeError, ValueError):
            continue
        for gid in GATE_LESSONS:
            g = scores.get(gid) if isinstance(scores, dict) else None
            if isinstance(g, dict) and not g.get("not_measured") and g.get("passed") is False:
                fails[(r["stage_id"], gid)] = fails.get((r["stage_id"], gid), 0) + 1
    changed = False
    for (stage_id, gid), n in fails.items():
        total = attempts.get(stage_id, 0)
        if n < PATTERN_MIN_FAILURES or not total or n / total < PATTERN_MIN_SHARE:
            continue
        name, advice = GATE_LESSONS[gid]
        key = f"gate:{gid}"
        evidence = json.dumps({"kind": "gate_pattern", "gate": gid, "gate_name": name, "failures": n,
                               "attempts": total, "days": PATTERN_LOOKBACK_DAYS}, ensure_ascii=False)
        existing = conn.execute(
            "SELECT id, status FROM stage_lessons WHERE stage_id = ? AND pattern_key = ?", (stage_id, key)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO stage_lessons (stage_id, text, source, pattern_key, evidence, hits) "
                "VALUES (?, ?, 'gate_pattern', ?, ?, ?)",
                (stage_id, f"[{name}] {advice}", key, evidence, n),
            )
            changed = True
        elif existing["status"] == "candidate":
            conn.execute("UPDATE stage_lessons SET hits = ?, evidence = ?, last_seen_at = datetime('now') WHERE id = ?",
                         (n, evidence, existing["id"]))
            changed = True
    if changed:
        conn.commit()


def add_manual_lesson(conn: sqlite3.Connection, actor_id: int, stage_id: int, text: str) -> dict:
    """운영자가 직접 적는 배운 점 — 바로 적용한다."""
    text = (text or "").strip()[:MAX_LESSON_CHARS]
    if len(_norm_text(text)) < 4:
        raise ValueError("lesson_text_too_short")
    if _stage(conn, stage_id) is None:
        raise LookupError("pipeline_stage_not_found")
    cur = conn.execute(
        "INSERT INTO stage_lessons (stage_id, text, source, evidence) VALUES (?, ?, 'manual', ?)",
        (stage_id, text, json.dumps({"kind": "manual"}, ensure_ascii=False)),
    )
    assert cur.lastrowid is not None
    return activate_lesson(conn, actor_id, cur.lastrowid)


# ---------------------------------------------------------------- 학습: 적용

def activate_lesson(conn: sqlite3.Connection, actor_id: int, lesson_id: int, text: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM stage_lessons WHERE id = ?", (lesson_id,)).fetchone()
    if row is None:
        raise LookupError("lesson_not_found")
    if row["status"] == "active":
        raise ValueError("lesson_already_active")
    new_text = (text if text is not None else row["text"]).strip()[:MAX_LESSON_CHARS]
    if len(_norm_text(new_text)) < 4:
        raise ValueError("lesson_text_too_short")
    active = conn.execute(
        "SELECT COUNT(*) FROM stage_lessons WHERE stage_id = ? AND status = 'active'", (row["stage_id"],)
    ).fetchone()[0]
    if active >= MAX_ACTIVE_PER_STAGE:
        raise ValueError("too_many_active_lessons")
    conn.execute(
        "UPDATE stage_lessons SET status = 'active', text = ?, decided_by = ?, decided_at = datetime('now') WHERE id = ?",
        (new_text, actor_id, lesson_id),
    )
    st = _stage(conn, row["stage_id"])
    record_change(conn, actor_id, st["pipeline_template_id"], st["id"], "lesson_on",
                  f"배운 점 적용 — {st['name']}: {new_text[:80]}", None, {"text": new_text}, lesson_id=lesson_id)
    audit.record(conn, actor_id, "stage_lesson_activated", "stage_lesson", lesson_id, after={"text": new_text})
    conn.commit()
    return {"id": lesson_id, "status": "active", "text": new_text}


def set_lesson_status(conn: sqlite3.Connection, actor_id: int, lesson_id: int, status: str) -> dict:
    """제안 무시(`dismissed`) 또는 적용 중인 배운 점 끄기(`retired`)."""
    if status not in ("dismissed", "retired"):
        raise ValueError("invalid_lesson_status")
    row = conn.execute("SELECT * FROM stage_lessons WHERE id = ?", (lesson_id,)).fetchone()
    if row is None:
        raise LookupError("lesson_not_found")
    if status == "dismissed" and row["status"] != "candidate":
        raise ValueError("lesson_not_candidate")
    if status == "retired" and row["status"] != "active":
        raise ValueError("lesson_not_active")
    conn.execute("UPDATE stage_lessons SET status = ?, decided_by = ?, decided_at = datetime('now') WHERE id = ?",
                 (status, actor_id, lesson_id))
    if status == "retired":
        st = _stage(conn, row["stage_id"])
        record_change(conn, actor_id, st["pipeline_template_id"], st["id"], "lesson_off",
                      f"배운 점 끄기 — {st['name']}: {row['text'][:80]}", {"text": row["text"]}, None, lesson_id=lesson_id)
    audit.record(conn, actor_id, f"stage_lesson_{status}", "stage_lesson", lesson_id)
    conn.commit()
    return {"id": lesson_id, "status": status}


def lessons_prompt(conn: sqlite3.Connection, stage_id: int) -> str:
    """실행 때 그 단계 지시문 끝에 붙일 글 — 적용된 배운 점과 좋은 결과 예시. 없으면 빈 문자열."""
    return _lessons_block(conn, stage_id) + examples_prompt(conn, stage_id)


def _lessons_block(conn: sqlite3.Connection, stage_id: int) -> str:
    try:
        rows = conn.execute(
            "SELECT text FROM stage_lessons WHERE stage_id = ? AND status = 'active' ORDER BY id", (stage_id,)
        ).fetchall()
    except sqlite3.OperationalError:  # 마이그레이션 전
        return ""
    if not rows:
        return ""
    lines, used = [], 0
    for r in rows:
        line = "- " + r["text"].strip()
        if used + len(line) > PROMPT_BUDGET_CHARS:
            break
        lines.append(line)
        used += len(line)
    return ("\n\n[이 단계의 이전 실행에서 배운 점 — 사람이 지적했거나 자주 미달했던 부분입니다. 반드시 지키세요]\n"
            + "\n".join(lines))


# ---------------------------------------------------------------- 좋은 결과 예시(2026-11-27, 5단계 — 2번 학습 루프 3단계)
# 사람이 승인한 결과는 그 실행에서 끝나고, 같은 단계의 다음 실행은 "어느 정도로, 어떤 형식으로" 써야 하는지 다시 짐작했다.
# 사람이 승인했고(자동 승인 제외), 한 번에 통과했고, 품질 점검 미달이 없는 결과를 그 단계의 "좋은 결과 예시" 후보로 올린다.
# 운영자가 고르면(단계마다 2개까지) 다음 실행부터 형식·수준 참고용으로 지시문에 붙는다.
# **외부 데이터를 읽은 실행(고객 메일·사내 DB 등)의 결과는 후보로 올리지 않는다** — 한 고객의 내용이 다른 실행에 섞이지 않게.

MAX_ACTIVE_EXAMPLES = 2
EXAMPLE_CHARS = 1200
EXAMPLE_LOOKBACK_DAYS = 90
EXAMPLE_CANDIDATES_PER_STAGE = 3


def _clean_gate(gate_scores: str | None) -> bool:
    try:
        scores = json.loads(gate_scores) if gate_scores else {}
    except ValueError:
        return False
    for k, v in (scores or {}).items():
        if k.startswith("_") or not isinstance(v, dict) or v.get("not_measured"):
            continue
        if v.get("passed") is False:
            return False
    return True


def refresh_example_candidates(conn: sqlite3.Connection, template_id: int | None = None) -> None:
    try:
        where = "rs.status = 'approved' AND rs.attempt = 1 AND rs.output IS NOT NULL AND rs.created_at >= datetime('now', ?)"
        params: list = [f"-{EXAMPLE_LOOKBACK_DAYS} days"]
        if template_id is not None:
            where += " AND s.pipeline_template_id = ?"
            params.append(template_id)
        rows = conn.execute(
            "SELECT rs.id, rs.stage_id, rs.pipeline_run_id, rs.output, rs.gate_scores, rs.created_at FROM pipeline_run_stages rs "
            f"JOIN pipeline_stages s ON s.id = rs.stage_id WHERE {where} "  # noqa: S608 — where는 내부 고정
            "AND NOT EXISTS (SELECT 1 FROM stage_examples e WHERE e.run_stage_id = rs.id) "
            "AND NOT EXISTS (SELECT 1 FROM tool_calls t WHERE t.pipeline_run_id = rs.pipeline_run_id AND t.external = 1) "
            "ORDER BY rs.id DESC", params).fetchall()
    except sqlite3.OperationalError:
        return
    per_stage: dict[int, int] = {}
    for r in rows:
        if not _clean_gate(r["gate_scores"]) or len((r["output"] or "").strip()) < 50:
            continue
        have = per_stage.get(r["stage_id"])
        if have is None:
            have = conn.execute("SELECT COUNT(*) FROM stage_examples WHERE stage_id = ? AND status = 'candidate'",
                                (r["stage_id"],)).fetchone()[0]
        if have >= EXAMPLE_CANDIDATES_PER_STAGE:
            continue
        out = r["output"].strip()
        conn.execute("INSERT OR IGNORE INTO stage_examples (stage_id, run_stage_id, excerpt, full_chars, source_at) "
                     "VALUES (?, ?, ?, ?, ?)", (r["stage_id"], r["id"], out[:EXAMPLE_CHARS], len(out), r["created_at"]))
        per_stage[r["stage_id"]] = have + 1
    conn.commit()


def list_examples(conn: sqlite3.Connection, template_id: int) -> dict:
    refresh_example_candidates(conn, template_id)
    try:
        rows = conn.execute(
            "SELECT e.*, s.name AS stage_name, s.seq_order, u.username AS decided_by_name FROM stage_examples e "
            "JOIN pipeline_stages s ON s.id = e.stage_id LEFT JOIN users u ON u.id = e.decided_by "
            "WHERE s.pipeline_template_id = ? AND e.status IN ('candidate', 'active') ORDER BY s.seq_order, e.id DESC",
            (template_id,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    items = [{k: r[k] for k in ("id", "stage_id", "stage_name", "seq_order", "run_stage_id", "excerpt", "full_chars", "status",
                                "source_at", "decided_at", "decided_by_name")} for r in rows]
    return {"candidates": [x for x in items if x["status"] == "candidate"],
            "active": [x for x in items if x["status"] == "active"], "max_active_per_stage": MAX_ACTIVE_EXAMPLES}


def set_example_status(conn: sqlite3.Connection, actor_id: int, example_id: int, status: str) -> dict:
    """후보 → active(예시로 쓰기)·dismissed(무시), active → retired(끄기)."""
    row = conn.execute("SELECT * FROM stage_examples WHERE id = ?", (example_id,)).fetchone()
    if row is None:
        raise LookupError("example_not_found")
    allowed = {"active": ("candidate",), "dismissed": ("candidate",), "retired": ("active",)}
    if status not in allowed or row["status"] not in allowed[status]:
        raise ValueError("invalid_example_transition")
    if status == "active":
        n = conn.execute("SELECT COUNT(*) FROM stage_examples WHERE stage_id = ? AND status = 'active'",
                         (row["stage_id"],)).fetchone()[0]
        if n >= MAX_ACTIVE_EXAMPLES:
            raise ValueError("too_many_active_examples")
    conn.execute("UPDATE stage_examples SET status = ?, decided_by = ?, decided_at = datetime('now') WHERE id = ?",
                 (status, actor_id, example_id))
    if status in ("active", "retired"):
        st = _stage(conn, row["stage_id"])
        record_change(conn, actor_id, st["pipeline_template_id"], st["id"],
                      "example_on" if status == "active" else "example_off",
                      ("좋은 결과 예시 적용 — " if status == "active" else "좋은 결과 예시 끄기 — ") + st["name"],
                      None, {"example_id": example_id, "run_stage_id": row["run_stage_id"]}, lesson_id=example_id)
    audit.record(conn, actor_id, f"stage_example_{status}", "stage_example", example_id)
    conn.commit()
    return {"id": example_id, "status": status}


def examples_prompt(conn: sqlite3.Connection, stage_id: int) -> str:
    try:
        rows = conn.execute("SELECT excerpt, full_chars FROM stage_examples WHERE stage_id = ? AND status = 'active' "
                            "ORDER BY id LIMIT ?", (stage_id, MAX_ACTIVE_EXAMPLES)).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""
    parts = []
    for i, r in enumerate(rows, start=1):
        cut = "\n…(예시 일부)" if r["full_chars"] > len(r["excerpt"]) else ""
        parts.append(f"--- 예시 {i} ---\n{r['excerpt']}{cut}")
    return ("\n\n[이 단계에서 사람이 승인한 좋은 결과 예시 — 형식·구성·깊이만 참고하세요. 내용은 이번 요청에 맞게 새로 쓰고, "
            "예시의 사실·숫자·이름을 옮겨 쓰지 마세요]\n" + "\n".join(parts))


def list_lessons(conn: sqlite3.Connection, template_id: int) -> dict:
    refresh_gate_patterns(conn, template_id)
    rows = conn.execute(
        "SELECT l.*, s.name AS stage_name, s.seq_order, u.username AS decided_by_name FROM stage_lessons l "
        "JOIN pipeline_stages s ON s.id = l.stage_id LEFT JOIN users u ON u.id = l.decided_by "
        "WHERE s.pipeline_template_id = ? AND l.status IN ('candidate', 'active') ORDER BY s.seq_order, l.hits DESC, l.id",
        (template_id,),
    ).fetchall()
    out = []
    for r in rows:
        item = {k: r[k] for k in ("id", "stage_id", "stage_name", "seq_order", "text", "source", "hits", "status",
                                  "created_at", "last_seen_at", "decided_at", "decided_by_name")}
        try:
            item["evidence"] = json.loads(r["evidence"]) if r["evidence"] else None
        except ValueError:
            item["evidence"] = None
        out.append(item)
    return {"candidates": [x for x in out if x["status"] == "candidate"],
            "active": [x for x in out if x["status"] == "active"],
            "max_active_per_stage": MAX_ACTIVE_PER_STAGE}


def candidate_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """파이프라인별 아직 결정하지 않은 개선 제안 수(프로세스 성과 표)."""
    try:
        refresh_gate_patterns(conn)
        rows = conn.execute(
            "SELECT s.pipeline_template_id AS tid, COUNT(*) AS n FROM stage_lessons l "
            "JOIN pipeline_stages s ON s.id = l.stage_id WHERE l.status = 'candidate' GROUP BY s.pipeline_template_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["tid"]: r["n"] for r in rows}


# ---------------------------------------------------------------- 변경 기록

def purge_stage(conn: sqlite3.Connection, stage_id: int) -> None:
    """단계를 지울 때 그 단계의 배운 점·예시를 함께 지운다(커밋은 호출자). 변경 기록은 이력이라 남긴다."""
    try:
        conn.execute("DE" "LETE FROM stage_examples WHERE stage_id = ?", (stage_id,))
        conn.execute("DELETE FROM stage_lessons WHERE stage_id = ?", (stage_id,))
    except sqlite3.OperationalError:
        pass


def purge_template(conn: sqlite3.Connection, template_id: int, stage_ids: list[int]) -> None:
    """파이프라인을 지울 때(실행 이력이 없을 때만 가능) 배운 점·변경 기록을 함께 지운다(커밋은 호출자)."""
    for sid in stage_ids:
        purge_stage(conn, sid)
    try:
        conn.execute("DELETE FROM process_changes WHERE template_id = ?", (template_id,))
    except sqlite3.OperationalError:
        pass


def stage_values(row) -> dict:
    """단계 행 → `update_stage`가 받는 형태의 값(비교·되돌리기용)."""
    out = {}
    keys = row.keys()
    for f in FIELD_LABELS:
        if f not in keys:
            continue
        v = row[f]
        if f == "gate_ids":
            v = json.loads(v) if v else []
        elif f == "required_tools":
            v = json.loads(v) if v else []
        elif f in _BOOL_FIELDS:
            v = bool(v)
        elif f == "on_fail_return_stage_id":
            v = v or None
        out[f] = v
    return out


def record_change(conn: sqlite3.Connection, actor_id: int, template_id: int, stage_id: int | None, kind: str,
                  summary: str, before: dict | None, after: dict | None, lesson_id: int | None = None) -> int | None:
    """변경 한 건(커밋은 호출자). 표가 아직 없으면(마이그레이션 전) 조용히 건너뛴다."""
    try:
        cur = conn.execute(
            "INSERT INTO process_changes (template_id, stage_id, kind, summary, before_value, after_value, lesson_id, "
            "actor_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (template_id, stage_id, kind, summary,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None, lesson_id, actor_id),
        )
    except sqlite3.OperationalError:
        return None
    return cur.lastrowid


def record_stage_update(conn: sqlite3.Connection, actor_id: int, before_row, after_row) -> int | None:
    """단계 설정 저장 직후 — 실제로 값이 바뀐 항목만 남긴다."""
    before, after = stage_values(before_row), stage_values(after_row)
    changed = [k for k in after if before.get(k) != after.get(k)]
    if not changed:
        return None
    summary = f"단계 설정 — {after_row['name']}: " + ", ".join(str(FIELD_LABELS.get(k) or k) for k in changed)
    return record_change(conn, actor_id, after_row["pipeline_template_id"], after_row["id"], "stage_update", summary,
                         {k: before[k] for k in changed}, {k: after[k] for k in changed})


def _ts(v) -> datetime | None:
    return process_metrics._ts(v)


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _verdict(before: dict, after: dict, stage_id: int | None) -> dict:
    """전·후 비교 판정 — 양쪽 모두 끝난 실행이 3건 이상일 때만. 기준: 한 번에 완료 비율(단계 변경이면 그 단계 재작업률도)."""
    nb, na = before["finished"], after["finished"]
    if nb < _MIN_EFFECT_SAMPLE or na < _MIN_EFFECT_SAMPLE:
        return {"label": "표본 부족", "level": "muted",
                "note": f"끝난 실행 전 {nb}건 · 후 {na}건(각 {_MIN_EFFECT_SAMPLE}건 이상 필요)"}
    score = 0.0
    parts = []
    if before["first_pass_rate"] is not None and after["first_pass_rate"] is not None:
        d = after["first_pass_rate"] - before["first_pass_rate"]
        score += d
        parts.append(f"한 번에 완료 {round(before['first_pass_rate'] * 100)}% → {round(after['first_pass_rate'] * 100)}%")
    if stage_id is not None and before.get("stage_rework_rate") is not None and after.get("stage_rework_rate") is not None:
        d = before["stage_rework_rate"] - after["stage_rework_rate"]
        score += d
        parts.append(f"이 단계 다시 작성 {round(before['stage_rework_rate'] * 100)}% → {round(after['stage_rework_rate'] * 100)}%")
    if score >= 0.1:
        return {"label": "좋아짐", "level": "ok", "note": " · ".join(parts)}
    if score <= -0.1:
        return {"label": "나빠짐", "level": "warn", "note": " · ".join(parts)}
    return {"label": "비슷함", "level": "muted", "note": " · ".join(parts)}


def list_changes(conn: sqlite3.Connection, template_id: int, limit: int = 30) -> list[dict]:
    """변경 기록 — 최신순, 변경마다 그 전 30일과 그 뒤(다음 변경까지, 최대 30일) 실행 지표."""
    try:
        rows = conn.execute(
            "SELECT c.*, u.username AS actor_name, s.name AS stage_name FROM process_changes c "
            "LEFT JOIN users u ON u.id = c.actor_id LEFT JOIN pipeline_stages s ON s.id = c.stage_id "
            "WHERE c.template_id = ? ORDER BY c.id DESC LIMIT ?",
            (template_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    now: datetime = process_metrics._now()
    ordered = sorted(rows, key=lambda r: r["id"])
    next_at: dict[int, datetime] = {}
    for i, r in enumerate(ordered):
        if i + 1 < len(ordered):
            nxt = _ts(ordered[i + 1]["created_at"])
            if nxt is not None:
                next_at[r["id"]] = nxt
    out = []
    for r in rows:
        at = _ts(r["created_at"]) or now
        b_start = at - timedelta(days=EFFECT_WINDOW_DAYS)
        a_end = min(next_at.get(r["id"], now), at + timedelta(days=EFFECT_WINDOW_DAYS), now)
        before = process_metrics.window_summary(conn, template_id, _fmt(b_start), _fmt(at), r["stage_id"])
        after = process_metrics.window_summary(conn, template_id, _fmt(at), _fmt(a_end), r["stage_id"])
        out.append({
            "id": r["id"], "kind": r["kind"], "summary": r["summary"], "stage_id": r["stage_id"],
            "stage_name": r["stage_name"], "actor_name": r["actor_name"], "created_at": r["created_at"],
            "before_value": json.loads(r["before_value"]) if r["before_value"] else None,
            "after_value": json.loads(r["after_value"]) if r["after_value"] else None,
            "reverted_by": r["reverted_by"],
            "revertible": r["reverted_by"] is None and r["kind"] in ("stage_update", "lesson_on", "approval_policy", "example_on"),
            "before": before, "after": after,
            "after_window_ends_at_next_change": r["id"] in next_at and next_at[r["id"]] < at + timedelta(days=EFFECT_WINDOW_DAYS),
            "verdict": _verdict(before, after, r["stage_id"]),
        })
    return out


def revert_change(conn: sqlite3.Connection, actor_id: int, change_id: int) -> dict:
    """단계 설정 변경 → 바꾸기 전 값으로, 배운 점 적용 → 끄기. 그 뒤 같은 값이 다시 바뀌었으면 거절한다."""
    from hair_platform import pipeline_runtime  # 순환 import 방지(pipeline_runtime이 이 모듈을 쓴다)

    ch = conn.execute("SELECT * FROM process_changes WHERE id = ?", (change_id,)).fetchone()
    if ch is None:
        raise LookupError("change_not_found")
    if ch["reverted_by"] is not None:
        raise ValueError("change_already_reverted")
    if ch["kind"] == "example_on":
        ex = conn.execute("SELECT status FROM stage_examples WHERE id = ?", (ch["lesson_id"],)).fetchone()
        if ex is None or ex["status"] != "active":
            raise ValueError("change_superseded")
        set_example_status(conn, actor_id, ch["lesson_id"], "retired")
    elif ch["kind"] == "lesson_on":
        lesson = conn.execute("SELECT status FROM stage_lessons WHERE id = ?", (ch["lesson_id"],)).fetchone()
        if lesson is None or lesson["status"] != "active":
            raise ValueError("change_superseded")
        set_lesson_status(conn, actor_id, ch["lesson_id"], "retired")
    elif ch["kind"] == "approval_policy":
        from hair_platform import approval_assist

        before = json.loads(ch["before_value"] or "{}").get("auto_approve_low_risk", False)
        cur = conn.execute("SELECT auto_approve_low_risk FROM pipeline_templates WHERE id = ?",
                           (ch["template_id"],)).fetchone()
        if cur is None or bool(cur[0]) == bool(before):
            raise ValueError("change_superseded")
        approval_assist.set_policy(conn, actor_id, ch["template_id"], bool(before))
    elif ch["kind"] == "stage_update":
        row = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (ch["stage_id"],)).fetchone()
        if row is None:
            raise ValueError("change_superseded")
        current = stage_values(row)
        after = json.loads(ch["after_value"] or "{}")
        if any(current.get(k) != v for k, v in after.items()):
            raise ValueError("change_superseded")
        pipeline_runtime.update_stage(conn, actor_id, ch["stage_id"], **json.loads(ch["before_value"] or "{}"))
    else:
        raise ValueError("change_not_revertible")
    new_id = conn.execute("SELECT MAX(id) FROM process_changes WHERE template_id = ?", (ch["template_id"],)).fetchone()[0]
    if new_id is not None and new_id != change_id:
        conn.execute("UPDATE process_changes SET kind = 'revert', summary = ? WHERE id = ?",
                     ("되돌리기 — " + ch["summary"], new_id))
    conn.execute("UPDATE process_changes SET reverted_by = ? WHERE id = ?", (new_id, change_id))
    audit.record(conn, actor_id, "process_change_reverted", "process_change", change_id)
    conn.commit()
    return {"id": change_id, "reverted_by": new_id}
