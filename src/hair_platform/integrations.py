"""외부 연동 — 개인 API 키(외부 시스템이 HAIR API를 호출).

사용자 요청(2026-11-23, "P1 진행해줘" — P1-5 사람이 누르지 않아도 도는 자동화). 지금까지 HAIR API는
로그인 세션(쿠키·탭별 토큰)으로만 부를 수 있어, cron·메일 규칙·지라 같은 외부 시스템이 업무 요청을
올리거나 파이프라인을 시작할 방법이 없었다.

**API 키**: `hair_` + 무작위 토큰. DB에는 SHA-256 해시만 저장하고 원문은 만들 때 한 번만 보여 준다
(토큰이 충분히 길고 무작위라 느린 해시가 필요 없다). `Authorization: Bearer hair_...`로 부르면 그 키
주인의 계정·권한 그대로 동작한다 — 키가 권한을 넓히지 않는다(RBAC 그대로). 주인이 비활성화되거나
키를 폐기하면 즉시 막힌다."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from hair_platform import audit

API_KEY_PREFIX = "hair_"
_MAX_KEYS_PER_USER = 10


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL REFERENCES users(id), "
        "name TEXT NOT NULL, "
        "key_prefix TEXT NOT NULL, "
        "key_hash TEXT NOT NULL UNIQUE, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "last_used_at TEXT, "
        "revoked_at TEXT)"
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_api_key(token: str) -> bool:
    return token.startswith(API_KEY_PREFIX)


def create_api_key(conn: sqlite3.Connection, user_id: int, name: str) -> dict:
    """새 키 — 반환값의 `token`은 이때 한 번만 알 수 있다."""
    name = (name or "").strip()
    if not name:
        raise ValueError("empty_name")
    active = conn.execute(
        "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND revoked_at IS NULL", (user_id,)
    ).fetchone()[0]
    if active >= _MAX_KEYS_PER_USER:
        raise ValueError("too_many_keys")
    token = API_KEY_PREFIX + secrets.token_urlsafe(32)
    cur = conn.execute(
        "INSERT INTO api_keys (user_id, name, key_prefix, key_hash) VALUES (?, ?, ?, ?)",
        (user_id, name[:80], token[:12], _hash(token)),
    )
    audit.record(conn, user_id, "api_key_created", "api_keys", cur.lastrowid, after={"name": name[:80]})
    conn.commit()
    return {"id": cur.lastrowid, "name": name[:80], "key_prefix": token[:12], "token": token}


def list_api_keys(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    """`user_id`를 주면 그 사용자 것만, `None`이면 전체(admin 화면)."""
    sql = (
        "SELECT k.id, k.user_id, u.username, k.name, k.key_prefix, k.created_at, k.last_used_at, "
        "k.revoked_at FROM api_keys k JOIN users u ON u.id = k.user_id"
    )
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE k.user_id = ?"
        params = (user_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY k.id DESC", params).fetchall()]


def revoke_api_key(conn: sqlite3.Connection, actor_id: int, key_id: int, is_admin: bool) -> None:
    row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    if row is None:
        raise ValueError("api_key_not_found")
    if row["user_id"] != actor_id and not is_admin:
        raise PermissionError("not_owner")
    if row["revoked_at"] is None:
        conn.execute("UPDATE api_keys SET revoked_at = datetime('now') WHERE id = ?", (key_id,))
        audit.record(conn, actor_id, "api_key_revoked", "api_keys", key_id, after={"owner_id": row["user_id"]})
        conn.commit()


def user_id_for_api_key(conn: sqlite3.Connection, token: str) -> int | None:
    """유효한 키면 주인 사용자 ID(마지막 사용 시각도 갱신), 아니면 None."""
    row = conn.execute(
        "SELECT id, user_id FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (_hash(token),)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    return row["user_id"]


# ---------- 외부 알림(2026-11-23, P1-5) ----------
# 결재 대기·파이프라인 완료/실패/승인 대기·Agent 실행 실패를 Slack·Teams 같은 수신 웹훅(incoming
# webhook) URL로 보낸다 — 사람이 인박스를 열어 봐야만 알던 일을 밀어서 알려 준다. 상태가 바뀌는
# 지점이 20곳이 넘어 곳곳에 훅을 거는 대신, 자동화 루프(`automation.py`)가 30초마다 새 이벤트를
# 찾아 한 번씩만 보낸다(`notification_log`의 고유 키로 중복 방지). 웹훅을 처음 설정할 때 이미 있던
# 이벤트는 "보낸 것"으로 기록해 과거 이력이 한꺼번에 쏟아지지 않게 한다.

NOTIFY_EVENTS: dict[str, str] = {
    "approval_pending": "결재 대기",
    "pipeline_paused": "파이프라인 사람 승인 대기",
    "pipeline_completed": "파이프라인 완료",
    "pipeline_failed": "파이프라인 실패",
    "agent_job_failed": "Agent 실행 실패",
}
_SUBJECT_LABELS = {
    "assignment": "배정 요청",
    "deliverable": "결과물 검토",
    "pipeline_run_stage": "파이프라인 단계 승인",
    "knowledge_submission": "지식 등록",
}
_MAX_SENDS_PER_TICK = 20


def ensure_notify_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notification_log ("
        "event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "ok INTEGER NOT NULL DEFAULT 1, error TEXT)"
    )
    for column in ("notify_webhook_url", "notify_events", "notify_last_error", "notify_last_sent_at",
                   "notify_emails", "notify_email_last_error"):
        if not any(r[1] == column for r in conn.execute("PRAGMA table_info(org_policy)")):
            conn.execute(f"ALTER TABLE org_policy ADD COLUMN {column} TEXT")


def get_notify_config(conn: sqlite3.Connection) -> dict:
    import json

    row = conn.execute(
        "SELECT notify_webhook_url, notify_events, notify_last_error, notify_last_sent_at, notify_emails, "
        "notify_email_last_error FROM org_policy WHERE id = 1"
    ).fetchone()
    emails = json.loads(row["notify_emails"]) if row is not None and row["notify_emails"] else []
    smtp = smtp_settings()
    base = {"emails": emails, "smtp_configured": smtp is not None, "smtp_host": smtp["host"] if smtp else None,
            "email_last_error": row["notify_email_last_error"] if row is not None else None}
    if row is None or not (row["notify_webhook_url"] or emails):
        return {"webhook_url": "", "events": list(NOTIFY_EVENTS), "last_error": None, "last_sent_at": None, **base}
    return {
        "webhook_url": row["notify_webhook_url"] or "",
        "events": json.loads(row["notify_events"] or "[]"),
        "last_error": row["notify_last_error"],
        "last_sent_at": row["notify_last_sent_at"],
        **base,
    }


MAX_NOTIFY_EMAILS = 20


def _valid_email(value: str) -> bool:
    import re

    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value) is not None


def set_notify_config(conn: sqlite3.Connection, actor_id: int, webhook_url: str, events: list[str],
                      emails: list[str] | None = None) -> dict:
    import json

    url = (webhook_url or "").strip()
    if url and not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("invalid_webhook_url")
    unknown = [e for e in events if e not in NOTIFY_EVENTS]
    if unknown:
        raise ValueError(f"unknown_events:{','.join(unknown)}")
    before = get_notify_config(conn)
    clean = before["emails"] if emails is None else list(dict.fromkeys(e.strip() for e in emails if e.strip()))
    bad = [e for e in clean if not _valid_email(e)]
    if bad:
        raise ValueError(f"invalid_emails:{','.join(bad)}")
    if len(clean) > MAX_NOTIFY_EMAILS:
        raise ValueError("too_many_emails")
    conn.execute("INSERT OR IGNORE INTO org_policy (id) VALUES (1)")
    conn.execute(
        "UPDATE org_policy SET notify_webhook_url = ?, notify_events = ?, notify_last_error = NULL, "
        "notify_emails = ?, notify_email_last_error = NULL WHERE id = 1",
        (url or None, json.dumps(events), json.dumps(clean) if clean else None),
    )
    if url and not before["webhook_url"]:
        seed_existing_events(conn)
    if clean and not before["emails"]:
        # 메일을 처음 켤 때도 이미 있던 이벤트는 보내지 않는다(웹훅과 같은 원칙, 채널별로 따로 기록).
        seed_existing_events(conn, channel=MAIL_CHANNEL)
    # URL은 비밀 토큰을 담는 경우가 많아 감사로그에는 호스트까지만, 메일 주소는 개인정보라 개수만 남긴다.
    host = url.split("/")[2] if url.count("/") >= 2 else ""
    audit.record(conn, actor_id, "notify_config_changed", "org_policy", 1,
                 after={"webhook_host": host, "events": events, "email_count": len(clean)})
    conn.commit()
    return get_notify_config(conn)


def collect_events(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(event_key, 이벤트 종류, 알림 문장) — 아직 보내지 않은 것만이 아니라 현재 조건에 맞는 전부."""
    from hair_platform import approvals

    events: list[tuple[str, str, str]] = []
    for a in conn.execute(
        "SELECT a.id, a.subject_type, a.subject_id, u.username AS approver "
        "FROM approvals a LEFT JOIN users u ON u.id = a.approver_id WHERE a.status = 'pending'"
    ).fetchall():
        try:
            desc = approvals.describe_subject(conn, a["subject_type"], a["subject_id"])
        except Exception:  # noqa: BLE001 — 설명을 못 만들어도 알림은 보낸다
            desc = {}
        what = _SUBJECT_LABELS.get(a["subject_type"], a["subject_type"])
        task = desc.get("task_role_name") or ""
        who = f" — 승인자 {a['approver']}" if a["approver"] else " — admin/manager 누구나"
        events.append((f"approval:{a['id']}", "approval_pending",
                       f"[HAIR] 결재 대기 #{a['id']}: {what}{f' ({task})' if task else ''}{who}"))
    for r in conn.execute(
        "SELECT r.id, r.status, r.current_stage_seq, r.task_id, t.name AS tpl FROM pipeline_runs r "
        "JOIN pipeline_templates t ON t.id = r.pipeline_template_id "
        "WHERE r.status IN ('paused_for_approval', 'completed', 'failed')"
    ).fetchall():
        kind = {"paused_for_approval": "pipeline_paused", "completed": "pipeline_completed",
                "failed": "pipeline_failed"}[r["status"]]
        stage = f" {r['current_stage_seq']}단계" if r["status"] == "paused_for_approval" else ""
        events.append((f"pipeline:{r['id']}:{r['status']}:{r['current_stage_seq']}", kind,
                       f"[HAIR] {NOTIFY_EVENTS[kind]}: 「{r['tpl']}」 실행 #{r['id']}(할 일 #{r['task_id']}){stage}"))
    for j in conn.execute(
        "SELECT id, task_id, error FROM agent_execution_jobs WHERE status = 'failed'"
    ).fetchall():
        events.append((f"agent_job:{j['id']}", "agent_job_failed",
                       f"[HAIR] Agent 실행 실패(할 일 #{j['task_id']}): {(j['error'] or '')[:160]}"))
    return events


MAIL_CHANNEL = "|mail"  # 메일 채널의 중복 방지 키 접미사(웹훅은 접미사 없음 — 예전 기록과 호환)


def seed_existing_events(conn: sqlite3.Connection, channel: str = "") -> int:
    keys = [(k + channel,) for k, _kind, _text in collect_events(conn)]
    conn.executemany("INSERT OR IGNORE INTO notification_log (event_key, ok, error) VALUES (?, 1, 'seeded')", keys)
    return len(keys)


def send_webhook(url: str, text: str) -> tuple[bool, str | None]:
    """Slack·Teams 수신 웹훅이 공통으로 받는 `{"text": ...}` 형식으로 보낸다."""
    import httpx

    try:
        resp = httpx.post(url, json={"text": text}, timeout=5.0)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:160]}"
        return True, None
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


# ---------- 메일 알림(2026-11-24, P1-5 남은 항목 — "Slack·메일 알림") ----------
# 웹훅과 같은 이벤트를 admin이 정한 수신자에게 메일로도 보낸다. SMTP 접속 정보(로그인 비밀 포함)는 DB가
# 아니라 서버 환경변수에서만 읽는다 — HAIR_SMTP_HOST(필수)·HAIR_SMTP_PORT(기본 587, SSL이면 465)·
# HAIR_SMTP_USER·HAIR_SMTP_PASSWORD·HAIR_SMTP_FROM(기본 USER)·HAIR_SMTP_SSL=1(SMTPS). 그 밖에는 STARTTLS
# 를 시도하고, 지원하지 않는 사내 릴레이면 그대로 보낸다.


def smtp_settings() -> dict | None:
    import os

    host = os.environ.get("HAIR_SMTP_HOST", "").strip()
    if not host:
        return None
    use_ssl = os.environ.get("HAIR_SMTP_SSL") == "1"
    user = os.environ.get("HAIR_SMTP_USER", "").strip()
    return {
        "host": host,
        "port": int(os.environ.get("HAIR_SMTP_PORT") or (465 if use_ssl else 587)),
        "user": user,
        "secret": os.environ.get("HAIR_SMTP_PASSWORD", ""),
        "sender": os.environ.get("HAIR_SMTP_FROM", "").strip() or user or f"hair@{host}",
        "ssl": use_ssl,
    }


def send_email(recipients: list[str], subject: str, text: str) -> tuple[bool, str | None]:
    import smtplib
    from email.message import EmailMessage

    cfg = smtp_settings()
    if cfg is None:
        return False, "SMTP 설정 없음(HAIR_SMTP_HOST)"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    from hair_platform import tls  # 서버 인증서 검증(2026-11-26 — 기본값은 검증하지 않았다)

    try:
        ctx = tls.client_context()
        server = (smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10, context=ctx) if cfg["ssl"]
                  else smtplib.SMTP(cfg["host"], cfg["port"], timeout=10))
        with server:
            encrypted = bool(cfg["ssl"])
            if not cfg["ssl"]:
                try:
                    server.starttls(context=ctx)
                    encrypted = True
                except smtplib.SMTPNotSupportedError:
                    pass  # TLS 없는 사내 릴레이 — 인증 없이 보낼 때만 허용(아래)
            if cfg["user"]:
                if not encrypted:
                    # 예전엔 STARTTLS를 지원하지 않는 서버에도 비밀번호를 평문으로 보냈다(2026-11-26 수정).
                    return False, "TLS를 지원하지 않는 서버에는 비밀번호를 보내지 않습니다(HAIR_SMTP_SSL=1로 465 포트를 쓰거나, 인증 없는 사내 릴레이면 HAIR_SMTP_USER를 비우세요)"
                server.login(cfg["user"], cfg["secret"])
            server.send_message(msg)
        return True, None
    except Exception as exc:  # noqa: BLE001 — 메일 실패가 자동화 루프를 멈추지 않는다
        return False, (tls.explain(exc) or f"{type(exc).__name__}: {exc}")[:200]


def run_notifications(conn: sqlite3.Connection) -> int:
    """새 이벤트를 채널(웹훅·메일)마다 한 번씩 보내고 보낸 건수를 돌려준다. 둘 다 없으면 아무것도 안 한다."""
    cfg = get_notify_config(conn)
    channels: list[tuple[str, str]] = []
    if cfg["webhook_url"]:
        channels.append(("", "notify_last_error"))
    if cfg["emails"] and cfg["smtp_configured"]:
        channels.append((MAIL_CHANNEL, "notify_email_last_error"))
    if not channels:
        return 0
    enabled = set(cfg["events"])
    events = [e for e in collect_events(conn) if e[1] in enabled]
    sent = 0
    for suffix, error_column in channels:
        for key, _kind, text in events:
            if sent >= _MAX_SENDS_PER_TICK:
                break
            if conn.execute("SELECT 1 FROM notification_log WHERE event_key = ?", (key + suffix,)).fetchone():
                continue
            if suffix:
                ok, err = send_email(cfg["emails"], text.split(":")[0][:120], text)
            else:
                ok, err = send_webhook(cfg["webhook_url"], text)
            conn.execute("INSERT OR IGNORE INTO notification_log (event_key, ok, error) VALUES (?, ?, ?)",
                         (key + suffix, int(ok), err))
            conn.execute(
                f"UPDATE org_policy SET {error_column} = ?, notify_last_sent_at = datetime('now') WHERE id = 1",
                (err,),
            )
            conn.commit()
            sent += 1
            if not ok:
                break  # 이 채널이 실패하면 이번 차례는 멈춘다(실패한 키는 다시 보내지 않는다)
    return sent
