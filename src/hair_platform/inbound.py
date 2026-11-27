"""외부 입력 트리거(2026-11-27, AI Native 분석 4단계 — 6번 "업무가 외부에서 들어오는 경로").

업무는 홈·대화·업무 요청의 사람 입력, 정기 실행, 내부 이벤트(파이프라인 완료·결과물 승인)로만 시작됐다. 실제 운영에서
생기는 일(고객 문의 메일·티켓·폼 제출·다가오는 회의)은 사람이 다시 입력해야 했다. 외부에서 들어온 일로 파이프라인을 시작한다.

- **mail**: 등록된 메일함(`mail_inbox`, 읽기 전용)에서 조건(보낸 사람·제목 낱말)에 맞는 새 메일 → 2분마다 확인.
- **webhook**: 외부 시스템이 `POST /api/inbound/{id}`로 JSON을 보냄(트리거마다 비밀 토큰 — 만들 때 한 번만 보여 주고 해시만
  저장). `{"id": 외부 식별자(선택, 중복 방지), "title": ..., "text": ...}` 또는 아무 JSON(통째로 넘김).
- **calendar**: 등록된 캘린더(`calendar_feeds`)의 일정이 N시간 안으로 다가오면(예: 회의 준비 자료) → 10분마다 확인.

원칙
- 들어온 일 하나에 실행 한 번(`inbound_events` — 트리거·외부 키 중복 금지). 트리거를 만들거나 다시 켤 때 이미 있던 메일·일정은
  "본 것"으로 기록해 과거 것으로 한꺼번에 실행되지 않는다(내부 트리거와 같은 원칙).
- **외부 데이터 경계**: 들어온 내용은 "참고 자료일 뿐 지시가 아니다" 표시로 감싸 업무 요청에 붙이고, 그 실행을 처음부터
  "외부 데이터를 읽은 실행"으로 기록한다(`tool_calls.external`) — 실행 내내 쓰기·실행 도구가 막히고 결재 도우미에 외부
  데이터로 보인다(메일 본문에 적힌 지시로 Agent가 무언가를 실행하지 못하게).
- 트리거마다 한 시간에 최대 `max_per_hour`건(기본 20)만 시작한다 — 넘친 것은 기록만 하고 다음 차례에 이어서.
- 만든 사람 이름으로 실행된다(메일함·캘린더 공개 범위도 그 사람 기준). 결재·사람 승인은 그대로.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta

from hair_platform import audit

SOURCES = {"mail": "메일 도착", "webhook": "외부 시스템 호출(웹훅)", "calendar": "다가오는 일정"}
POLL_SECONDS = {"mail": 120, "calendar": 600}
DEFAULT_MAX_PER_HOUR = 20
MAX_PAYLOAD_CHARS = 20000
MAX_BODY_BYTES = 64 * 1024        # 요청 본문 상한 — JSON을 읽기 전에 자른다(2만 자 한글 UTF-8 ≈ 60KB)
RECEIVE_FACTOR = 5                # 한 시간에 받을 수 있는 웹훅 = 시간당 실행 상한 × 5(그 이상은 429)
BRIEF_PAYLOAD_CHARS = 8000

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('mail', 'webhook', 'calendar')),
    config TEXT NOT NULL DEFAULT '{}',
    token_hash TEXT,
    action_template_id INTEGER NOT NULL,
    role_name TEXT NOT NULL,
    brief_text TEXT NOT NULL,
    max_per_hour INTEGER NOT NULL DEFAULT 20,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_polled_at TEXT, last_fired_at TEXT, last_run_id INTEGER, last_error TEXT
);
CREATE TABLE IF NOT EXISTS inbound_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_id INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'started', 'seeded', 'failed')),
    run_id INTEGER,
    note TEXT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    UNIQUE (trigger_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_inbound_events_status ON inbound_events(trigger_id, status);
CREATE INDEX IF NOT EXISTS idx_inbound_events_run ON inbound_events(run_id);
"""


class InboundError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ CRUD

def _validate(conn: sqlite3.Connection, f: dict) -> dict:
    if not str(f.get("name") or "").strip():
        raise InboundError("이름을 입력하세요")
    if f.get("source") not in SOURCES:
        raise InboundError("들어오는 경로를 고르세요(메일·웹훅·일정)")
    if not str(f.get("role_name") or "").strip():
        raise InboundError("할 일 역할을 입력하세요")
    if not str(f.get("brief_text") or "").strip():
        raise InboundError("들어온 일로 할 업무 내용을 입력하세요")
    if not conn.execute("SELECT 1 FROM pipeline_templates WHERE id = ? AND is_active = 1",
                        (f.get("action_template_id"),)).fetchone():
        raise InboundError("파이프라인을 찾을 수 없습니다")
    raw_cfg = f.get("config")
    cfg: dict = raw_cfg if isinstance(raw_cfg, dict) else {}
    if f["source"] == "mail":
        if not conn.execute("SELECT 1 FROM mail_inboxes WHERE name = ? AND is_active = 1", (cfg.get("mailbox"),)).fetchone():
            raise InboundError("등록된 메일함을 고르세요")
        cfg = {"mailbox": cfg["mailbox"], "query": str(cfg.get("query") or "").strip()[:100]}
    elif f["source"] == "calendar":
        if not conn.execute("SELECT 1 FROM calendar_feeds WHERE name = ? AND is_active = 1", (cfg.get("calendar"),)).fetchone():
            raise InboundError("등록된 캘린더를 고르세요")
        hours = int(cfg.get("hours_before") or 24)
        if not 1 <= hours <= 168:
            raise InboundError("일정 몇 시간 전에 시작할지는 1~168시간입니다")
        cfg = {"calendar": cfg["calendar"], "hours_before": hours, "query": str(cfg.get("query") or "").strip()[:100]}
    else:
        cfg = {}
    mph = int(f.get("max_per_hour") or DEFAULT_MAX_PER_HOUR)
    if not 1 <= mph <= 200:
        raise InboundError("시간당 최대 실행 수는 1~200입니다")
    return {"cfg": cfg, "max_per_hour": mph}


def create(conn: sqlite3.Connection, actor_id: int, fields: dict) -> dict:
    """트리거를 만든다. 웹훅이면 응답에 토큰이 **한 번만** 들어 있다(저장은 해시만)."""
    v = _validate(conn, fields)
    token = secrets.token_urlsafe(24) if fields["source"] == "webhook" else None
    cur = conn.execute(
        "INSERT INTO inbound_triggers (name, source, config, token_hash, action_template_id, role_name, brief_text, "
        "max_per_hour, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fields["name"].strip(), fields["source"], json.dumps(v["cfg"], ensure_ascii=False),
         _hash(token) if token else None, fields["action_template_id"], fields["role_name"].strip(),
         fields["brief_text"].strip(), v["max_per_hour"], actor_id))
    tid = cur.lastrowid
    assert tid is not None
    audit.record(conn, actor_id, "inbound_trigger_created", "inbound_triggers", tid,
                 after={"source": fields["source"], "config": v["cfg"], "action_template_id": fields["action_template_id"]})
    conn.commit()
    out = get(conn, tid)
    if token:
        out["token"] = token
        out["webhook_path"] = f"/api/inbound/{tid}"
    return out


def get(conn: sqlite3.Connection, trigger_id: int) -> dict:
    row = conn.execute(
        "SELECT t.*, p.name AS action_template_name, u.username AS created_by_name FROM inbound_triggers t "
        "LEFT JOIN pipeline_templates p ON p.id = t.action_template_id LEFT JOIN users u ON u.id = t.created_by "
        "WHERE t.id = ?", (trigger_id,)).fetchone()
    if row is None:
        raise LookupError("inbound_trigger_not_found")
    out = {k: row[k] for k in row.keys() if k != "token_hash"}
    out["config"] = json.loads(row["config"] or "{}")
    out["source_label"] = SOURCES[row["source"]]
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM inbound_events WHERE trigger_id = ? GROUP BY status", (trigger_id,))}
    out["started_count"] = counts.get("started", 0)
    out["queued_count"] = counts.get("queued", 0)
    out["failed_count"] = counts.get("failed", 0)
    return out


def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [get(conn, r[0]) for r in conn.execute("SELECT id FROM inbound_triggers ORDER BY id DESC")]


def recent_events(conn: sqlite3.Connection, trigger_id: int, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, event_key, status, run_id, note, received_at, started_at, payload FROM inbound_events "
        "WHERE trigger_id = ? AND status != 'seeded' ORDER BY id DESC LIMIT ?", (trigger_id, limit)).fetchall()
    out = []
    for r in rows:
        p = json.loads(r["payload"] or "{}")
        out.append({k: r[k] for k in ("id", "event_key", "status", "run_id", "note", "received_at", "started_at")}
                   | {"title": str(p.get("title") or p.get("subject") or p.get("summary") or "")[:120]})
    return out


def set_active(conn: sqlite3.Connection, actor_id: int, trigger_id: int, active: bool) -> dict:
    get(conn, trigger_id)
    conn.execute("UPDATE inbound_triggers SET is_active = ?, last_polled_at = CASE WHEN ? THEN NULL ELSE last_polled_at END "
                 "WHERE id = ?", (1 if active else 0, 1 if active else 0, trigger_id))
    if active:
        # 꺼져 있던 동안 쌓인 웹훅은 실행하지 않는다(다시 켜면 그 뒤에 들어온 것부터).
        conn.execute("UPDATE inbound_events SET status = 'seeded', note = '꺼져 있던 동안 들어옴 — 실행 안 함' "
                     "WHERE trigger_id = ? AND status = 'queued'", (trigger_id,))
    audit.record(conn, actor_id, "inbound_trigger_toggled", "inbound_triggers", trigger_id, after={"is_active": active})
    conn.commit()
    return get(conn, trigger_id)


def delete(conn: sqlite3.Connection, actor_id: int, trigger_id: int) -> None:
    get(conn, trigger_id)
    conn.execute("DE" "LETE FROM inbound_events WHERE trigger_id = ?", (trigger_id,))
    conn.execute("DE" "LETE FROM inbound_triggers WHERE id = ?", (trigger_id,))
    audit.record(conn, actor_id, "inbound_trigger_deleted", "inbound_triggers", trigger_id)
    conn.commit()


# ------------------------------------------------------------------ 받기

def receive_webhook(conn: sqlite3.Connection, trigger_id: int, token: str, payload) -> dict:
    """외부 시스템 호출을 받는다 — 토큰이 맞고 켜져 있을 때만. 같은 `id`는 한 번만."""
    row = conn.execute("SELECT * FROM inbound_triggers WHERE id = ? AND source = 'webhook'", (trigger_id,)).fetchone()
    if row is None or not token or not row["token_hash"] or not secrets.compare_digest(row["token_hash"], _hash(token)):
        raise PermissionError("invalid_token")
    if not row["is_active"]:
        raise InboundError("trigger_inactive")
    # 받기 상한(2026-11-28 — RBAC 감사 후속): 실행 시작은 시간당 상한이 있었지만 받기는 무제한이라, 토큰을 가진 쪽이 대기열을
    # 끝없이 쌓을 수 있었다. 토큰이 맞은 뒤에만 센다(틀린 토큰으로는 트리거가 있는지·바쁜지 알 수 없다).
    received = conn.execute("SELECT COUNT(*) FROM inbound_events WHERE trigger_id = ? AND status != 'seeded' "
                            "AND received_at >= datetime('now', '-1 hour')", (trigger_id,)).fetchone()[0]
    if received >= row["max_per_hour"] * RECEIVE_FACTOR:
        raise InboundError("rate_limited")
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    if len(text) > MAX_PAYLOAD_CHARS:
        raise InboundError("payload_too_large")
    data = payload if isinstance(payload, dict) else {"text": text}
    ext_id = str(data.get("id") or "").strip()[:120]
    key = "webhook:" + (ext_id or hashlib.sha256(text.encode()).hexdigest()[:32])
    cur = conn.execute("INSERT OR IGNORE INTO inbound_events (trigger_id, event_key, payload) VALUES (?, ?, ?)",
                       (trigger_id, key, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    return {"accepted": cur.rowcount == 1, "duplicate": cur.rowcount == 0, "event_key": key}


def _poll_mail(conn: sqlite3.Connection, t: sqlite3.Row, cfg: dict) -> list[tuple[str, dict]]:
    from hair_platform import knowledge_access, mail_inbox

    with knowledge_access.as_viewer(t["created_by"]):
        found = mail_inbox.search(conn, cfg["mailbox"], cfg.get("query", ""), days=2, limit=20)
        out = []
        for m in found:
            key = f"mail:{cfg['mailbox']}:{m['uid']}"
            if conn.execute("SELECT 1 FROM inbound_events WHERE trigger_id = ? AND event_key = ?", (t["id"], key)).fetchone():
                continue
            if t["last_polled_at"] is None:  # 첫 확인 — 이미 있던 메일은 본 것으로
                out.append((key, {"seed": True, "subject": m["subject"]}))
                continue
            full = mail_inbox.read(conn, m["uid"], cfg["mailbox"])
            out.append((key, {"from": full["from"], "to": full["to"], "subject": full["subject"], "date": full["date"],
                              "text": full["body"], "attachments": full["attachments"]}))
    return out


def _poll_calendar(conn: sqlite3.Connection, t: sqlite3.Row, cfg: dict) -> list[tuple[str, dict]]:
    from hair_platform import calendar_feeds, knowledge_access

    with knowledge_access.as_viewer(t["created_by"]):
        res = calendar_feeds.events_between(conn, cfg["calendar"], days=max(2, cfg["hours_before"] // 24 + 2),
                                            query=cfg.get("query", ""))
    tz = calendar_feeds.local_tz()
    now = datetime.now(tz)
    horizon = now + timedelta(hours=cfg["hours_before"])
    out = []
    for e in res["events"]:
        start = e["start"]
        if not isinstance(start, datetime) or start.tzinfo is None:
            continue
        if not (now <= start <= horizon):
            continue
        key = f"calendar:{cfg['calendar']}:{e.get('uid') or e.get('summary', '')}:{start.isoformat()}"
        if conn.execute("SELECT 1 FROM inbound_events WHERE trigger_id = ? AND event_key = ?", (t["id"], key)).fetchone():
            continue
        payload = {"summary": e.get("summary", ""), "start": start.isoformat(),
                   "end": e["end"].isoformat() if isinstance(e.get("end"), datetime) else "",
                   "location": e.get("location", ""), "organizer": e.get("organizer", ""),
                   "attendees": e.get("attendees", 0), "text": e.get("description", "")}
        out.append((key, ({"seed": True} | payload) if t["last_polled_at"] is None else payload))
    return out


def poll(conn: sqlite3.Connection, force: bool = False) -> int:
    """메일·일정 트리거를 확인해 새로 들어온 일을 쌓는다(자동화 루프). 쌓은 수."""
    added = 0
    from hair_platform import process_metrics

    now = process_metrics._now()
    from hair_platform import automation_owner

    for t in conn.execute("SELECT * FROM inbound_triggers WHERE is_active = 1 AND source IN ('mail', 'calendar')").fetchall():
        if not automation_owner.check_or_pause(conn, "inbound_triggers", t):
            continue
        last = datetime.fromisoformat(t["last_polled_at"]) if t["last_polled_at"] else None
        if not force and last is not None and (now - last).total_seconds() < POLL_SECONDS[t["source"]]:
            continue
        cfg = json.loads(t["config"] or "{}")
        try:
            items = _poll_mail(conn, t, cfg) if t["source"] == "mail" else _poll_calendar(conn, t, cfg)
            error = None
        except Exception as exc:  # noqa: BLE001 — 한 트리거의 실패가 다른 것을 막지 않는다
            items, error = [], f"{type(exc).__name__}: {exc}"[:300]
        for key, payload in items:
            seed = bool(payload.pop("seed", False))
            cur = conn.execute(
                "INSERT OR IGNORE INTO inbound_events (trigger_id, event_key, payload, status, note) VALUES (?, ?, ?, ?, ?)",
                (t["id"], key, json.dumps(payload, ensure_ascii=False), "seeded" if seed else "queued",
                 "트리거를 만들 때 이미 있던 것 — 실행 안 함" if seed else None))
            added += 0 if seed else cur.rowcount
        conn.execute("UPDATE inbound_triggers SET last_polled_at = ?, last_error = ? WHERE id = ?",
                     (now.strftime("%Y-%m-%d %H:%M:%S"), error, t["id"]))
        conn.commit()
    return added


# ------------------------------------------------------------------ 시작

def _brief_text(t: sqlite3.Row, payload: dict) -> str:
    from hair_platform import tool_audit

    p = dict(payload)
    lines = []
    for k, label in (("from", "보낸 사람"), ("to", "받는 사람"), ("subject", "제목"), ("title", "제목"), ("summary", "일정"),
                     ("start", "시작"), ("end", "끝"), ("location", "장소"), ("organizer", "주최"), ("date", "날짜")):
        if p.get(k):
            lines.append(f"{label}: {p.pop(k)}")
    body = str(p.pop("text", "") or "")
    rest = {k: v for k, v in p.items() if k not in ("id", "attachments", "attendees") and v not in (None, "", [])}
    if p.get("attachments"):
        lines.append("첨부: " + ", ".join(str(a) for a in p["attachments"][:10]))
    content = "\n".join(lines) + ("\n\n" + body if body else "") + (
        "\n\n" + json.dumps(rest, ensure_ascii=False, indent=1) if rest else "")
    if len(content) > BRIEF_PAYLOAD_CHARS:
        content = content[:BRIEF_PAYLOAD_CHARS] + "\n…(이하 생략)"
    return (f"{t['brief_text']}\n\n(외부 입력 트리거 「{t['name']}」 — {SOURCES[t['source']]})\n\n"
            + tool_audit.wrap_external(content, SOURCES[t["source"]]))


def _taint(conn: sqlite3.Connection, run_id: int, task_id: int, t: sqlite3.Row) -> None:
    """이 실행을 처음부터 '외부 데이터를 읽은 실행'으로 기록 — 쓰기·실행 도구가 막히고 결재 도우미에 보인다."""
    first = conn.execute(
        "SELECT s.id FROM pipeline_runs r JOIN pipeline_stages s ON s.pipeline_template_id = r.pipeline_template_id "
        "WHERE r.id = ? ORDER BY s.seq_order LIMIT 1", (run_id,)).fetchone()
    conn.execute(
        "INSERT INTO tool_calls (tool_name, tool_label, ok, args_preview, result_preview, task_id, pipeline_run_id, "
        "stage_id, stage_attempt, user_id, external) VALUES ('inbound_trigger', ?, 1, ?, '', ?, ?, ?, 1, ?, 1)",
        (f"외부 입력({SOURCES[t['source']]})", f"트리거 #{t['id']} {t['name']}"[:200], task_id, run_id,
         first["id"] if first else None, t["created_by"]))


def fire(conn: sqlite3.Connection, run_stages=None, trigger_id: int | None = None) -> list[int]:
    """쌓인 일마다 파이프라인을 시작한다(시간당 상한 안에서). 시작한 run_id 목록."""
    from hair_platform import pipeline_runtime

    started: list[int] = []
    sql = "SELECT * FROM inbound_triggers WHERE is_active = 1" + (" AND id = ?" if trigger_id else "")
    from hair_platform import automation_owner

    for t in conn.execute(sql, (trigger_id,) if trigger_id else ()).fetchall():
        if not automation_owner.check_or_pause(conn, "inbound_triggers", t):  # 만든 사람 점검(2026-11-28)
            continue
        used = conn.execute("SELECT COUNT(*) FROM inbound_events WHERE trigger_id = ? AND status = 'started' "
                            "AND started_at >= datetime('now', '-1 hour')", (t["id"],)).fetchone()[0]
        room = max(0, t["max_per_hour"] - used)
        for ev in conn.execute("SELECT * FROM inbound_events WHERE trigger_id = ? AND status = 'queued' ORDER BY id LIMIT ?",
                               (t["id"], room)).fetchall():
            # 먼저 표시 — 시작이 실패해도 같은 일로 매 차례 다시 시도하지 않는다.
            conn.execute("UPDATE inbound_events SET status = 'started', started_at = datetime('now') WHERE id = ?", (ev["id"],))
            conn.commit()
            try:
                payload = json.loads(ev["payload"] or "{}")
                brief_id = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, ?, ?)",
                                        (t["created_by"], _brief_text(t, payload),
                                         json.dumps([t["role_name"]], ensure_ascii=False))).lastrowid
                task_id = conn.execute("INSERT INTO tasks (brief_id, role_name, notes) VALUES (?, ?, ?)",
                                       (brief_id, t["role_name"], f"외부 입력 트리거 「{t['name']}」")).lastrowid
                assert task_id is not None
                conn.commit()
                run_id = pipeline_runtime.start_pipeline_run(conn, t["created_by"], t["action_template_id"], task_id)
                _taint(conn, run_id, task_id, t)
            except Exception as exc:  # noqa: BLE001
                conn.execute("UPDATE inbound_events SET status = 'failed', note = ? WHERE id = ?",
                             (f"시작 실패: {exc}"[:300], ev["id"]))
                conn.execute("UPDATE inbound_triggers SET last_error = ? WHERE id = ?", (f"{type(exc).__name__}: {exc}"[:300], t["id"]))
                conn.commit()
                continue
            conn.execute("UPDATE inbound_events SET run_id = ? WHERE id = ?", (run_id, ev["id"]))
            conn.execute("UPDATE inbound_triggers SET last_fired_at = datetime('now'), last_run_id = ?, last_error = NULL "
                         "WHERE id = ?", (run_id, t["id"]))
            audit.record(conn, t["created_by"], "inbound_trigger_fired", "inbound_triggers", t["id"],
                         after={"event": ev["event_key"], "task_id": task_id, "run_id": run_id})
            conn.commit()
            if run_stages is None:
                from hair_platform import job_queue

                job_queue.enqueue(conn, "pipeline.stage", run_id, t["created_by"])
            else:
                run_stages(run_id, t["created_by"])
            started.append(run_id)
    return started


def run_inbound(conn: sqlite3.Connection) -> list[int]:
    """자동화 루프 한 차례 — 확인하고 시작한다."""
    poll(conn)
    return fire(conn)


def payload_for_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """이 실행을 시작한 외부 입력(실행 동작의 '보낸 사람에게 회신'이 쓴다)."""
    try:
        row = conn.execute("SELECT e.payload, t.source FROM inbound_events e JOIN inbound_triggers t ON t.id = e.trigger_id "
                           "WHERE e.run_id = ?", (run_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {"source": row["source"], **json.loads(row["payload"] or "{}")}
