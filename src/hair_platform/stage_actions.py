"""결재 후 실행 동작(2026-11-27, AI Native 분석 4단계 — 5번 "업무를 끝까지 완결하지 못함").

사내 DB·메일·캘린더는 읽기만 하고, 회신·공지·등록은 "결과물 초안"으로 남아 사람이 다른 시스템에 옮겨 적어야 했다. 파이프라인
단계에 **실행 동작**을 붙이면, 사람이 그 단계 결과를 결재로 승인하면서 **"승인하고 실행"을 고를 때만** 결과물을 실제로 내보낸다.

- 종류: `email`(서버 SMTP — `HAIR_SMTP_*`, 받는 사람은 정해 둔 주소 또는 이 실행을 시작한 메일의 보낸 사람에게 회신),
  `chat`(Slack·Teams 수신 웹훅 `{"text"}`), `json_webhook`(티켓 시스템 등 — 결과물과 맥락을 JSON으로 POST, 토큰은 환경변수).
- **admin만** 동작을 붙이고 바꾼다. 기본은 없음(파이프라인·단계마다 opt-in). 웹훅 주소·토큰은 환경변수 이름으로도 줄 수 있다
  (DB에 비밀을 남기지 않게) — 화면·API에는 주소를 가려 보여 준다.
- 결재자는 결재 도우미에서 **무엇을 어디로 보낼지 미리 보고**(받는 사람·제목·본문 앞부분), 표의 "승인"은 승인만 한다.
  "승인하고 실행"을 눌러야 보낸다. 실행은 단계 결과 하나당 한 번(`action_executions`), 결과는 성공·실패 모두 활동 기록에 남는다.
- 실행 동작이 붙은 단계는 **낮은 위험 자동 승인 대상에서 빠진다**(밖으로 나가는 일은 항상 사람이 승인).
- 외부 데이터 경계는 그대로다: 실행 중 Agent의 쓰기 도구는 막히고, 실행은 사람이 승인한 뒤 플랫폼이 한다.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from email.utils import parseaddr

from hair_platform import audit

KINDS = {"email": "메일 보내기", "chat": "Slack·Teams에 올리기", "json_webhook": "외부 시스템에 보내기(JSON 웹훅)"}
MAX_RECIPIENTS = 20
CHAT_CHARS = 3500
JSON_TIMEOUT = 10.0
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('email', 'chat', 'json_webhook')),
    config TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS action_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_action_id INTEGER NOT NULL,
    run_stage_id INTEGER NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    target TEXT,
    subject TEXT,
    status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
    error TEXT,
    decided_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ActionError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ------------------------------------------------------------------ 설정(admin)

def _clean_config(kind: str, cfg: dict) -> dict:
    if kind == "email":
        mode = cfg.get("to_mode") or "fixed"
        if mode not in ("fixed", "inbound_sender"):
            raise ActionError("받는 사람 방식은 fixed(정해 둔 주소) 또는 inbound_sender(시작한 메일의 보낸 사람)입니다")
        to = [str(a).strip() for a in (cfg.get("to") or []) if str(a).strip()]
        if mode == "fixed" and not to:
            raise ActionError("받는 사람 주소를 하나 이상 적으세요")
        if len(to) > MAX_RECIPIENTS or any(not _EMAIL_RE.match(a) for a in to):
            raise ActionError("메일 주소 형식이 올바르지 않습니다(최대 20개)")
        return {"to_mode": mode, "to": to, "cc": [], "subject": str(cfg.get("subject") or "").strip()[:200]}
    url = str(cfg.get("url") or "").strip()
    url_env = str(cfg.get("url_env") or "").strip()
    if bool(url) == bool(url_env):
        raise ActionError("주소(url) 또는 주소가 든 환경변수 이름(url_env) 중 하나만 적으세요")
    if url and not url.startswith("https://"):
        raise ActionError("주소는 https:// 로 시작해야 합니다")
    if url_env and not _ENV_RE.match(url_env):
        raise ActionError("환경변수 이름은 대문자·숫자·_ 만 씁니다(예: HAIR_TICKET_WEBHOOK)")
    out = {"url": url, "url_env": url_env}
    if kind == "json_webhook":
        token_env = str(cfg.get("token_env") or "").strip()
        if token_env and not _ENV_RE.match(token_env):
            raise ActionError("토큰 환경변수 이름은 대문자·숫자·_ 만 씁니다")
        out["token_env"] = token_env
    return out


def set_action(conn: sqlite3.Connection, actor_id: int, stage_id: int, kind: str, config: dict,
               is_active: bool = True) -> dict:
    from hair_platform import process_learning

    st = conn.execute("SELECT id, name, pipeline_template_id FROM pipeline_stages WHERE id = ?", (stage_id,)).fetchone()
    if st is None:
        raise LookupError("pipeline_stage_not_found")
    if kind not in KINDS:
        raise ActionError("동작 종류는 email·chat·json_webhook 중 하나입니다")
    cfg = _clean_config(kind, config if isinstance(config, dict) else {})
    before = conn.execute("SELECT kind, config, is_active FROM stage_actions WHERE stage_id = ?", (stage_id,)).fetchone()
    conn.execute(
        "INSERT INTO stage_actions (stage_id, kind, config, is_active, created_by) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(stage_id) DO UPDATE SET kind = excluded.kind, config = excluded.config, "
        "is_active = excluded.is_active, updated_at = datetime('now')",
        (stage_id, kind, json.dumps(cfg, ensure_ascii=False), 1 if is_active else 0, actor_id))
    after = {"kind": kind, "config": _mask(cfg), "is_active": is_active}
    process_learning.record_change(conn, actor_id, st["pipeline_template_id"], stage_id, "stage_action",
                                   f"실행 동작 — {st['name']}: {KINDS[kind]}" + ("" if is_active else "(꺼짐)"),
                                   ({"kind": before["kind"], "is_active": bool(before["is_active"])} if before else None), after)
    audit.record(conn, actor_id, "stage_action_set", "pipeline_stage", stage_id, after=after)
    conn.commit()
    return get_for_stage(conn, stage_id) or {}


def remove_action(conn: sqlite3.Connection, actor_id: int, stage_id: int) -> None:
    from hair_platform import process_learning

    st = conn.execute("SELECT id, name, pipeline_template_id FROM pipeline_stages WHERE id = ?", (stage_id,)).fetchone()
    row = conn.execute("SELECT kind FROM stage_actions WHERE stage_id = ?", (stage_id,)).fetchone()
    if st is None or row is None:
        raise LookupError("stage_action_not_found")
    conn.execute("DE" "LETE FROM stage_actions WHERE stage_id = ?", (stage_id,))
    process_learning.record_change(conn, actor_id, st["pipeline_template_id"], stage_id, "stage_action",
                                   f"실행 동작 삭제 — {st['name']}", {"kind": row["kind"]}, None)
    audit.record(conn, actor_id, "stage_action_removed", "pipeline_stage", stage_id)
    conn.commit()


def _mask(cfg: dict) -> dict:
    out = dict(cfg)
    if out.get("url"):
        u = out["url"]
        out["url"] = u[:24] + "…" if len(u) > 24 else u
    return out


def get_for_stage(conn: sqlite3.Connection, stage_id: int, active_only: bool = False) -> dict | None:
    try:
        row = conn.execute("SELECT * FROM stage_actions WHERE stage_id = ?" + (" AND is_active = 1" if active_only else ""),
                           (stage_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {"id": row["id"], "stage_id": row["stage_id"], "kind": row["kind"], "kind_label": KINDS[row["kind"]],
            "config": _mask(json.loads(row["config"] or "{}")), "is_active": bool(row["is_active"]),
            "updated_at": row["updated_at"]}


def list_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT a.stage_id, s.name AS stage_name, s.seq_order, t.id AS template_id, t.name AS template_name "
        "FROM stage_actions a JOIN pipeline_stages s ON s.id = a.stage_id JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "ORDER BY t.id, s.seq_order").fetchall()
    out = []
    for r in rows:
        a = get_for_stage(conn, r["stage_id"]) or {}
        out.append(a | {"stage_name": r["stage_name"], "seq_order": r["seq_order"], "template_id": r["template_id"],
                        "template_name": r["template_name"]})
    return out


def recent_executions(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT e.*, u.username AS decided_by_name, s.name AS stage_name FROM action_executions e "
            "LEFT JOIN users u ON u.id = e.decided_by LEFT JOIN stage_actions a ON a.id = e.stage_action_id "
            "LEFT JOIN pipeline_stages s ON s.id = a.stage_id ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 미리 보기·실행

def _content(conn: sqlite3.Connection, rs) -> str:
    if rs["deliverable_id"]:
        d = conn.execute("SELECT content FROM deliverables WHERE id = ?", (rs["deliverable_id"],)).fetchone()
        if d and d["content"]:
            return d["content"]
    return rs["output"] or ""


def _resolve_url(cfg: dict) -> str:
    return cfg.get("url") or os.environ.get(cfg.get("url_env") or "", "").strip()


def _plan(conn: sqlite3.Connection, run_stage_id: int) -> dict | None:
    """실행할 내용(마스킹 전) — 동작이 없으면 None."""
    from hair_platform import inbound, integrations

    rs = conn.execute("SELECT * FROM pipeline_run_stages WHERE id = ?", (run_stage_id,)).fetchone()
    if rs is None:
        return None
    row = conn.execute("SELECT * FROM stage_actions WHERE stage_id = ? AND is_active = 1", (rs["stage_id"],)).fetchone()
    if row is None:
        return None
    cfg = json.loads(row["config"] or "{}")
    ctx = conn.execute(
        "SELECT s.name AS stage_name, t.name AS template_name, r.task_id, r.id AS run_id FROM pipeline_runs r "
        "JOIN pipeline_stages s ON s.id = ? JOIN pipeline_templates t ON t.id = r.pipeline_template_id WHERE r.id = ?",
        (rs["stage_id"], rs["pipeline_run_id"])).fetchone()
    body = _content(conn, rs)
    problem = None
    plan = {"action_id": row["id"], "kind": row["kind"], "kind_label": KINDS[row["kind"]], "body": body,
            "run_stage_id": run_stage_id, "context": dict(ctx) if ctx else {}}
    if row["kind"] == "email":
        to = list(cfg.get("to") or [])
        origin = inbound.payload_for_run(conn, rs["pipeline_run_id"])
        if cfg.get("to_mode") == "inbound_sender":
            addr = parseaddr((origin or {}).get("from", ""))[1]
            to = [addr] if addr and _EMAIL_RE.match(addr) else []
            if not to:
                problem = "이 실행은 메일로 시작되지 않아 회신할 보낸 사람이 없습니다"
        subject = cfg.get("subject") or ""
        origin_subject = (origin or {}).get("subject") or (origin or {}).get("title")
        if not subject and origin_subject:
            subject = "Re: " + str(origin_subject)[:180]
        if not subject:
            subject = f"[{plan['context'].get('template_name', '')}] {plan['context'].get('stage_name', '')}"
        if integrations.smtp_settings() is None:
            problem = problem or "서버에 메일 발송 설정(HAIR_SMTP_HOST)이 없습니다"
        plan.update({"to": to, "subject": subject, "target": ", ".join(to)})
    else:
        url = _resolve_url(cfg)
        if not url:
            problem = "보낼 주소가 없습니다(" + (f"환경변수 {cfg.get('url_env')}가 비어 있음" if cfg.get("url_env") else "주소 미설정") + ")"
        elif not url.startswith("https://"):
            problem = "주소가 https:// 가 아닙니다"
        plan.update({"url": url, "target": (url[:24] + "…") if url else "", "subject": None,
                     "token_env": cfg.get("token_env") or ""})
    plan["problem"] = problem
    return plan


def preview(conn: sqlite3.Connection, run_stage_id: int) -> dict | None:
    """결재 도우미용 — 무엇을 어디로 보낼지(본문은 앞부분만), 이미 실행했는지."""
    plan = _plan(conn, run_stage_id)
    if plan is None:
        return None
    done = conn.execute("SELECT status, target, error, created_at FROM action_executions WHERE run_stage_id = ?",
                        (run_stage_id,)).fetchone()
    return {"kind": plan["kind"], "kind_label": plan["kind_label"], "target": plan["target"], "subject": plan.get("subject"),
            "body_excerpt": plan["body"][:600], "body_chars": len(plan["body"]), "ready": plan["problem"] is None,
            "problem": plan["problem"], "executed": dict(done) if done else None}


def _send(plan: dict) -> tuple[bool, str | None]:
    from hair_platform import integrations

    if plan["kind"] == "email":
        return integrations.send_email(plan["to"], plan["subject"], plan["body"])
    if plan["kind"] == "chat":
        c = plan["context"]
        text = f"*{c.get('template_name', '')} — {c.get('stage_name', '')}*\n\n" + plan["body"][:CHAT_CHARS]
        return integrations.send_webhook(plan["url"], text)
    import httpx

    headers = {"Content-Type": "application/json"}
    if plan.get("token_env"):
        tok = os.environ.get(plan["token_env"], "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    c = plan["context"]
    body = {"source": "hair-platform", "pipeline": c.get("template_name"), "stage": c.get("stage_name"),
            "run_id": c.get("run_id"), "task_id": c.get("task_id"), "content": plan["body"]}
    try:
        resp = httpx.post(plan["url"], json=body, headers=headers, timeout=JSON_TIMEOUT)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:160]}"
        return True, None
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def execute(conn: sqlite3.Connection, run_stage_id: int, actor_id: int, run: bool) -> dict | None:
    """결재 승인 직후(커밋 뒤) — `run`이 참이면 보내고, 아니면 '승인만'으로 기록. 동작이 없으면 None. 한 번만."""
    plan = _plan(conn, run_stage_id)
    if plan is None:
        return None
    if conn.execute("SELECT 1 FROM action_executions WHERE run_stage_id = ?", (run_stage_id,)).fetchone():
        return {"status": "already", "message": "이미 처리한 실행 동작입니다"}
    if not run:
        status, err = "skipped", "결재자가 '승인만'을 골랐습니다"
    elif plan["problem"]:
        status, err = "failed", plan["problem"]
    else:
        ok, err = _send(plan)
        status = "sent" if ok else "failed"
    conn.execute(
        "INSERT INTO action_executions (stage_action_id, run_stage_id, kind, target, subject, status, error, decided_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (plan["action_id"], run_stage_id, plan["kind"], plan["target"], plan.get("subject"), status, err, actor_id))
    audit.record(conn, actor_id, f"stage_action_{status}", "pipeline_run_stage", run_stage_id,
                 after={"kind": plan["kind"], "target": plan["target"], "subject": plan.get("subject"), "error": err})
    conn.commit()
    return {"status": status, "kind": plan["kind"], "target": plan["target"], "error": err}
