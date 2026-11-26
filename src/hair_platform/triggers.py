"""이벤트 트리거 — "이 일이 생기면 저 파이프라인을 시작"(2026-11-24, P1-5 남은 항목 — "이벤트 트리거").

정기 실행(`automation.py`)은 시각이 기준이었다. 트리거는 일이 생기는 것이 기준이다.
- `pipeline_completed`: 파이프라인 실행이 끝나면(원하면 특정 템플릿만) → 다음 파이프라인 시작. 앞 실행의 최종
  결과물을 새 업무 요청 본문에 붙여 넘기므로 파이프라인을 이어 붙일 수 있다(예: 재무 분석 보고 → 발표자료).
- `deliverable_approved`: 결과물이 결재로 승인되면 → 파이프라인 시작(그 결과물을 넘김).

원칙
- 자동화 루프(30초, `automation.tick`)가 새 이벤트를 찾아 트리거마다 한 번씩만 실행한다(`trigger_log`).
- 트리거를 만들 때 이미 있던 이벤트는 "처리한 것"으로 기록한다 — 과거 이력으로 한꺼번에 실행되지 않게
  (외부 알림 웹훅과 같은 원칙).
- 만든 사람 이름으로 실행된다(결재·사람 승인은 그대로). 트리거가 시작한 실행이 끝나 또 트리거가 걸리는 연쇄는
  `MAX_CHAIN_DEPTH`단계까지만 — 서로를 부르는 트리거가 끝없이 돌지 않게 한다. 자기 자신(같은 템플릿 완료 →
  같은 템플릿 시작)은 만들 때 거부한다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit

EVENTS = {"pipeline_completed": "파이프라인 완료", "deliverable_approved": "결과물 승인"}
MAX_CHAIN_DEPTH = 3
UPSTREAM_CHARS = 8000


class TriggerError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS automation_triggers ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " event TEXT NOT NULL CHECK (event IN ('pipeline_completed', 'deliverable_approved')),"
        " source_template_id INTEGER REFERENCES pipeline_templates(id),"
        " action_template_id INTEGER NOT NULL REFERENCES pipeline_templates(id),"
        " role_name TEXT NOT NULL,"
        " brief_text TEXT NOT NULL,"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by INTEGER NOT NULL REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " last_fired_at TEXT, last_run_id INTEGER, last_error TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trigger_log ("
        " trigger_id INTEGER NOT NULL REFERENCES automation_triggers(id) ON DELETE CASCADE,"
        " event_key TEXT NOT NULL,"
        " run_id INTEGER,"
        " depth INTEGER NOT NULL DEFAULT 0,"
        " fired_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " note TEXT,"
        " PRIMARY KEY (trigger_id, event_key))"
    )


# ------------------------------------------------------------------- events


def _events(conn: sqlite3.Connection, trigger: sqlite3.Row) -> list[dict]:
    """(event_key, task_id, 넘길 결과물, 원인 실행 run_id) — 지금 조건에 맞는 전부(처리 여부와 무관)."""
    out: list[dict] = []
    if trigger["event"] == "pipeline_completed":
        sql = "SELECT id, task_id, pipeline_template_id FROM pipeline_runs WHERE status = 'completed'"
        args: tuple = ()
        if trigger["source_template_id"]:
            sql += " AND pipeline_template_id = ?"
            args = (trigger["source_template_id"],)
        for r in conn.execute(sql, args).fetchall():
            out.append({"key": f"run:{r['id']}", "task_id": r["task_id"], "source_run_id": r["id"]})
    else:
        for d in conn.execute("SELECT id, task_id FROM deliverables WHERE status = 'approved'").fetchall():
            out.append({"key": f"deliverable:{d['id']}", "task_id": d["task_id"], "deliverable_id": d["id"],
                        "source_run_id": None})
    return out


def _upstream(conn: sqlite3.Connection, ev: dict) -> str:
    if ev.get("deliverable_id"):
        row = conn.execute("SELECT content FROM deliverables WHERE id = ?", (ev["deliverable_id"],)).fetchone()
    else:
        # 앞 실행의 최종 결과물 — 그 할 일의 가장 최근 결과물(파이프라인 핵심 산출물이 마지막에 저장된다).
        row = conn.execute(
            "SELECT content FROM deliverables WHERE task_id = ? ORDER BY id DESC LIMIT 1", (ev["task_id"],)
        ).fetchone()
    text = (row["content"] if row else "") or ""
    return text if len(text) <= UPSTREAM_CHARS else text[:UPSTREAM_CHARS] + "\n…(이하 생략 — 원본은 이전 할 일에서 확인)"


def _depth_of_run(conn: sqlite3.Connection, run_id: int | None) -> int:
    if not run_id:
        return 0
    row = conn.execute("SELECT MAX(depth) AS d FROM trigger_log WHERE run_id = ?", (run_id,)).fetchone()
    return int(row["d"]) if row and row["d"] is not None else 0


def _seed(conn: sqlite3.Connection, trigger_id: int) -> int:
    trigger = conn.execute("SELECT * FROM automation_triggers WHERE id = ?", (trigger_id,)).fetchone()
    keys = [(trigger_id, ev["key"]) for ev in _events(conn, trigger)]
    conn.executemany("INSERT OR IGNORE INTO trigger_log (trigger_id, event_key, note) VALUES (?, ?, 'seeded')", keys)
    return len(keys)


# ---------------------------------------------------------------------- CRUD


def _validate(conn: sqlite3.Connection, f: dict) -> None:
    if not (f.get("name") or "").strip():
        raise TriggerError("이름을 입력하세요")
    if f.get("event") not in EVENTS:
        raise TriggerError("이벤트를 고르세요")
    if not (f.get("role_name") or "").strip():
        raise TriggerError("할 일 역할을 입력하세요")
    if not (f.get("brief_text") or "").strip():
        raise TriggerError("새 업무 요청 내용을 입력하세요")
    for key in ("action_template_id", "source_template_id"):
        tid = f.get(key)
        if tid is None and key == "source_template_id":
            continue
        if not conn.execute("SELECT 1 FROM pipeline_templates WHERE id = ? AND is_active = 1", (tid,)).fetchone():
            raise TriggerError("파이프라인 템플릿을 찾을 수 없습니다")
    if f["event"] == "pipeline_completed" and f.get("source_template_id") == f["action_template_id"]:
        raise TriggerError("같은 파이프라인이 끝나면 같은 파이프라인을 다시 시작하는 트리거는 만들 수 없습니다(무한 반복)")
    if f["event"] == "pipeline_completed" and f.get("source_template_id") is None:
        raise TriggerError("'파이프라인 완료' 트리거는 어느 파이프라인이 끝났을 때인지 골라야 합니다")


def create(conn: sqlite3.Connection, actor_id: int, fields: dict) -> dict:
    _validate(conn, fields)
    cur = conn.execute(
        "INSERT INTO automation_triggers (name, event, source_template_id, action_template_id, role_name, brief_text, "
        "created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fields["name"].strip(), fields["event"], fields.get("source_template_id"), fields["action_template_id"],
         fields["role_name"].strip(), fields["brief_text"].strip(), actor_id),
    )
    tid = cur.lastrowid
    assert tid is not None
    seeded = _seed(conn, tid)
    audit.record(conn, actor_id, "automation_trigger_created", "automation_triggers", tid,
                 after={"event": fields["event"], "action_template_id": fields["action_template_id"],
                        "existing_events_skipped": seeded})
    conn.commit()
    return get(conn, tid)


def get(conn: sqlite3.Connection, trigger_id: int) -> dict:
    row = conn.execute(
        "SELECT t.*, s.name AS source_template_name, a.name AS action_template_name, u.username AS created_by_name "
        "FROM automation_triggers t LEFT JOIN pipeline_templates s ON s.id = t.source_template_id "
        "JOIN pipeline_templates a ON a.id = t.action_template_id LEFT JOIN users u ON u.id = t.created_by "
        "WHERE t.id = ?", (trigger_id,),
    ).fetchone()
    if row is None:
        raise LookupError("trigger_not_found")
    out = dict(row)
    out["event_label"] = EVENTS[out["event"]]
    out["fired_count"] = conn.execute(
        "SELECT COUNT(*) FROM trigger_log WHERE trigger_id = ? AND run_id IS NOT NULL", (trigger_id,)
    ).fetchone()[0]
    return out


def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [get(conn, r[0]) for r in conn.execute("SELECT id FROM automation_triggers ORDER BY id DESC")]


def set_active(conn: sqlite3.Connection, actor_id: int, trigger_id: int, active: bool) -> dict:
    get(conn, trigger_id)
    conn.execute("UPDATE automation_triggers SET is_active = ? WHERE id = ?", (1 if active else 0, trigger_id))
    if active:
        _seed(conn, trigger_id)  # 꺼져 있던 동안의 이벤트로 한꺼번에 실행되지 않게
    audit.record(conn, actor_id, "automation_trigger_toggled", "automation_triggers", trigger_id,
                 after={"is_active": active})
    conn.commit()
    return get(conn, trigger_id)


def delete(conn: sqlite3.Connection, actor_id: int, trigger_id: int) -> None:
    get(conn, trigger_id)
    conn.execute("DELETE FROM trigger_log WHERE trigger_id = ?", (trigger_id,))
    conn.execute("DELETE FROM automation_triggers WHERE id = ?", (trigger_id,))
    audit.record(conn, actor_id, "automation_trigger_deleted", "automation_triggers", trigger_id)
    conn.commit()


# ----------------------------------------------------------------------- fire


def _start(conn: sqlite3.Connection, trigger: sqlite3.Row, ev: dict) -> tuple[int, int]:
    from hair_platform import pipeline_runtime

    upstream = _upstream(conn, ev)
    source = f"할 일 #{ev['task_id']}" + (f"의 결과물 #{ev['deliverable_id']}" if ev.get("deliverable_id") else "")
    brief_text = (
        f"{trigger['brief_text']}\n\n(트리거 「{trigger['name']}」 — {EVENTS[trigger['event']]}: {source})"
        + (f"\n\n[이전 결과물]\n{upstream}" if upstream.strip() else "")
    )
    brief_id = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, ?, ?)",
        (trigger["created_by"], brief_text, json.dumps([trigger["role_name"]], ensure_ascii=False)),
    ).lastrowid
    task_id = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, notes) VALUES (?, ?, ?)",
        (brief_id, trigger["role_name"], f"트리거 「{trigger['name']}」"),
    ).lastrowid
    assert task_id is not None
    conn.commit()
    run_id = pipeline_runtime.start_pipeline_run(conn, trigger["created_by"], trigger["action_template_id"], task_id)
    return task_id, run_id


def run_triggers(conn: sqlite3.Connection, run_stages=None) -> list[int]:
    """새 이벤트마다 트리거를 한 번씩 실행하고 시작한 run_id 목록을 돌려준다. `run_stages(run_id, actor_id)`가
    실제 단계 실행(기본: 별도 스레드에서 `pipeline_runtime.run_pipeline_stage_job`)."""
    started: list[int] = []
    for trigger in conn.execute("SELECT * FROM automation_triggers WHERE is_active = 1").fetchall():
        for ev in _events(conn, trigger):
            if conn.execute("SELECT 1 FROM trigger_log WHERE trigger_id = ? AND event_key = ?",
                            (trigger["id"], ev["key"])).fetchone():
                continue
            depth = _depth_of_run(conn, ev.get("source_run_id")) + 1
            if depth > MAX_CHAIN_DEPTH:
                conn.execute("INSERT OR IGNORE INTO trigger_log (trigger_id, event_key, depth, note) VALUES (?, ?, ?, ?)",
                             (trigger["id"], ev["key"], depth, f"연쇄 {MAX_CHAIN_DEPTH}단계 초과 — 실행 안 함"))
                conn.execute("UPDATE automation_triggers SET last_error = ? WHERE id = ?",
                             (f"트리거 연쇄가 {MAX_CHAIN_DEPTH}단계를 넘어 멈췄습니다({ev['key']})", trigger["id"]))
                conn.commit()
                continue
            # 먼저 기록한다 — 시작이 실패해도 같은 이벤트로 매 차례 다시 시도하지 않는다.
            conn.execute("INSERT OR IGNORE INTO trigger_log (trigger_id, event_key, depth) VALUES (?, ?, ?)",
                         (trigger["id"], ev["key"], depth))
            conn.commit()
            try:
                task_id, run_id = _start(conn, trigger, ev)
            except Exception as exc:  # noqa: BLE001 — 한 트리거의 실패가 다른 것을 막지 않는다
                conn.execute("UPDATE automation_triggers SET last_error = ? WHERE id = ?",
                             (f"{type(exc).__name__}: {exc}"[:300], trigger["id"]))
                conn.execute("UPDATE trigger_log SET note = ? WHERE trigger_id = ? AND event_key = ?",
                             (f"시작 실패: {exc}"[:300], trigger["id"], ev["key"]))
                conn.commit()
                continue
            conn.execute("UPDATE trigger_log SET run_id = ? WHERE trigger_id = ? AND event_key = ?",
                         (run_id, trigger["id"], ev["key"]))
            conn.execute(
                "UPDATE automation_triggers SET last_fired_at = datetime('now'), last_run_id = ?, last_error = NULL "
                "WHERE id = ?", (run_id, trigger["id"]),
            )
            audit.record(conn, trigger["created_by"], "automation_trigger_fired", "automation_triggers",
                         trigger["id"], after={"event": ev["key"], "task_id": task_id, "run_id": run_id, "depth": depth})
            conn.commit()
            if run_stages is None:
                from hair_platform import job_queue  # 작업 큐(2026-11-26, P1-6 2단계) — external이면 작업 프로세스가 실행

                job_queue.enqueue(conn, "pipeline.stage", run_id, trigger["created_by"])
            else:
                run_stages(run_id, trigger["created_by"])
            started.append(run_id)
    return started
