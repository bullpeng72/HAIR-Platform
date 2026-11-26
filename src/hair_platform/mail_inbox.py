"""메일 수신함 읽기 연동(2026-11-26, P1-9 2단계 — "외부 업무 시스템 연결 부족: 메일 수신 없음").

admin이 IMAP 메일함(공용 고객지원 메일함·팀 메일함 등)을 등록하면, "메일수신함조회" 스킬을 가진 Agent가 `mail_inbox` 도구로
최근 메일을 검색하고 읽는다(문의 요약·회신 초안·업무 요청 정리 등). 표준 IMAP(파이썬 표준 라이브러리 `imaplib`, 추가 설치
없음) — Gmail·Microsoft 365·사내 메일 서버 모두.

**읽기 전용** — 메일함을 `EXAMINE`(읽기 전용 선택)으로 열고 본문은 `BODY.PEEK[]`로 가져와 **읽음 표시도 바꾸지 않는다**.
보내기·지우기·이동·플래그 변경은 하지 않는다. 첨부는 이름·크기만 보여 준다(내용 열지 않음).

**누가 읽을 수 있나(메일은 민감하다)** — 메일함마다 공개 범위(`visibility`)를 둔다. 지식 베이스 문서와 같은 규칙
(`knowledge_access`): all(모든 사람) · manager(manager 이상) · admin(admin만, 기본값) · team:<id>(그 팀의 사람 구성원).
요청자는 작업 입구에서 정한 문맥(`knowledge_access.current_viewer()`)이며, **요청자를 모르면 거부**한다(지식 베이스는 공개
문서만 돌려주지만 메일은 공개 범위가 all이어도 요청자를 확인한다).

**비밀번호는 저장하지 않는다** — 앱 비밀번호가 든 환경변수 이름만(`password_env`). 조회마다 활동 기록(`mail_inbox_read`).
"""

from __future__ import annotations

import email
import email.header
import email.message
import imaplib
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta

from hair_platform import audit, knowledge_access

NATIVE_ID = "mail_inbox_read"
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_BODY_CHARS = 8000
TIMEOUT_SECONDS = 20
ACTIONS = {
    "mailboxes": "등록된 메일함 목록",
    "search": "최근 메일 검색(mailbox, query=보낸 사람·제목 낱말, days=최근 N일, 기본 7일)",
    "read": "메일 한 통 읽기(mailbox, uid 필수)",
}


class MailError(ValueError):
    """사람(또는 Agent)에게 그대로 보여 줄 수 있는 사유."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mail_inboxes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL UNIQUE,"
        " host TEXT NOT NULL,"
        " port INTEGER NOT NULL DEFAULT 993,"
        " username TEXT NOT NULL,"
        " password_env TEXT NOT NULL,"
        " folder TEXT NOT NULL DEFAULT 'INBOX',"
        " visibility TEXT NOT NULL DEFAULT 'admin',"
        " description TEXT NOT NULL DEFAULT '',"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


# ------------------------------------------------------------------ 등록·관리(admin)

def register(conn: sqlite3.Connection, actor_id: int, name: str, host: str, username: str, password_env: str, *,
             port: int = 993, folder: str = "INBOX", visibility: str = "admin", description: str = "") -> dict:
    if not re.fullmatch(r"[\w가-힣\-]{1,60}", name or ""):
        raise MailError("메일함 이름은 1~60자 한글·영문·숫자·_-만 쓸 수 있습니다")
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host or ""):
        raise MailError("IMAP 서버 주소가 올바르지 않습니다(예: imap.gmail.com)")
    if not (username or "").strip():
        raise MailError("메일 계정을 입력하세요")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", password_env or ""):
        raise MailError("비밀번호 환경변수 이름은 대문자·숫자·_ 형식이어야 합니다(예: HAIR_MAIL_SUPPORT_PASSWORD)")
    if not 1 <= int(port) <= 65535:
        raise MailError("포트가 올바르지 않습니다")
    visibility = knowledge_access.validate(visibility)
    try:
        cur = conn.execute(
            "INSERT INTO mail_inboxes (name, host, port, username, password_env, folder, visibility, description, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, host, int(port), username.strip(), password_env, folder or "INBOX", visibility, description or "", actor_id),
        )
    except sqlite3.IntegrityError as exc:
        raise MailError("같은 이름의 메일함이 이미 있습니다") from exc
    audit.record(conn, actor_id, "mail_inbox_registered", "mail_inboxes", cur.lastrowid,
                 after={"name": name, "host": host, "username": username, "folder": folder, "visibility": visibility})
    conn.commit()
    return get(conn, cur.lastrowid or 0)


def _public(row) -> dict:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    d["password_set"] = bool(os.environ.get(d["password_env"] or ""))
    return d


def get(conn: sqlite3.Connection, inbox_id: int) -> dict:
    row = conn.execute("SELECT * FROM mail_inboxes WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        raise MailError("메일함을 찾을 수 없습니다")
    return _public(row)


def list_inboxes(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM mail_inboxes" + ("" if include_inactive else " WHERE is_active = 1") + " ORDER BY name"
    return [_public(r) for r in conn.execute(sql)]


def update(conn: sqlite3.Connection, actor_id: int, inbox_id: int, *, is_active: bool | None = None,
           visibility: str | None = None) -> dict:
    before = get(conn, inbox_id)
    if is_active is not None:
        conn.execute("UPDATE mail_inboxes SET is_active = ? WHERE id = ?", (1 if is_active else 0, inbox_id))
    if visibility is not None:
        conn.execute("UPDATE mail_inboxes SET visibility = ? WHERE id = ?", (knowledge_access.validate(visibility), inbox_id))
    audit.record(conn, actor_id, "mail_inbox_updated", "mail_inboxes", inbox_id,
                 before={"is_active": before["is_active"], "visibility": before["visibility"]},
                 after={"is_active": is_active, "visibility": visibility})
    conn.commit()
    return get(conn, inbox_id)


# ------------------------------------------------------------------ 읽기

def _accessible(conn: sqlite3.Connection, name: str | None, user_id: int | None) -> sqlite3.Row:
    if user_id is None:
        raise MailError("요청한 사람을 확인할 수 없어 메일을 읽지 않았습니다")
    rank, teams = knowledge_access._viewer_profile(conn, user_id)
    rows = [r for r in conn.execute("SELECT * FROM mail_inboxes WHERE is_active = 1 ORDER BY name")
            if knowledge_access.can_see(r["visibility"], rank, teams)]
    if not rows:
        raise MailError("읽을 수 있는 메일함이 없습니다 — admin이 '연동 설정'에서 등록하고 공개 범위를 정해야 합니다")
    if not name:
        if len(rows) == 1:
            return rows[0]
        raise MailError("메일함이 여러 개입니다 — mailbox에 이름을 지정하세요: " + ", ".join(r["name"] for r in rows))
    for r in rows:
        if r["name"] == name:
            return r
    raise MailError(f"'{name}' 메일함이 없거나 읽을 권한이 없습니다")


def _open(row) -> imaplib.IMAP4:
    password = os.environ.get(row["password_env"] or "", "")
    if not password:
        raise MailError(f"서버 환경변수 {row['password_env']}에 메일 비밀번호(앱 비밀번호)가 없습니다")
    try:
        from hair_platform import tls  # 서버 인증서 검증(2026-11-26 — 기본값은 검증하지 않았다)

        client = imaplib.IMAP4_SSL(row["host"], int(row["port"]), timeout=TIMEOUT_SECONDS, ssl_context=tls.client_context())
        client.login(row["username"], password)
        typ, _ = client.select(_quote(row["folder"]), readonly=True)  # EXAMINE — 읽기 전용
        if typ != "OK":
            raise MailError(f"메일함 폴더 '{row['folder']}'를 열 수 없습니다")
        return client
    except MailError:
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        from hair_platform import tls

        raise MailError(tls.explain(exc) or f"메일 서버 접속 실패: {str(exc)[:200]}") from exc


def _open_checked(conn: sqlite3.Connection, row) -> imaplib.IMAP4:
    """IMAP 접속·로그인 결과를 연결 상태로 남긴다(2026-11-26, 개선 계획 2-3 — `connection_health`)."""
    from hair_platform import connection_health

    try:
        client = _open(row)
    except MailError as exc:
        connection_health.record(conn, "mail_inboxes", row["id"], False, str(exc))
        raise
    connection_health.record(conn, "mail_inboxes", row["id"], True)
    return client


def _quote(folder: str) -> str:
    return '"' + (folder or "INBOX").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for text, charset in email.header.decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _body_text(msg: email.message.Message) -> tuple[str, list[str]]:
    """본문(텍스트 우선, 없으면 HTML에서 태그 제거)과 첨부 이름·크기."""
    text, html, attachments = "", "", []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        raw = part.get_payload(decode=True)
        payload = raw if isinstance(raw, bytes) else b""
        if filename or "attachment" in disposition:
            attachments.append(f"{_decode(filename) or '(이름 없음)'} ({len(payload):,}바이트)")
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        if part.get_content_type() == "text/plain" and not text:
            text = decoded
        elif part.get_content_type() == "text/html" and not html:
            html = decoded
    if not text and html:
        text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return text, attachments


def _record(conn, user_id, row, action: str, detail: dict) -> None:
    audit.record(conn, user_id, "mail_inbox_read", "mail_inboxes", row["id"],
                 after={"mailbox": row["name"], "action": action, **detail})
    conn.commit()


def search(conn: sqlite3.Connection, mailbox: str | None = None, query: str = "", days: int = 7,
           limit: int = DEFAULT_LIMIT) -> list[dict]:
    user_id = knowledge_access.current_viewer()
    row = _accessible(conn, mailbox, user_id)
    days = max(1, min(int(days or 7), 365))
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")
    criteria = ["SINCE", since]
    q = (query or "").strip()
    client = _open_checked(conn, row)
    try:
        if q and not q.isascii():
            # 한글 등 ASCII 밖 낱말(2026-11-26 실제 메일 서버 통합 테스트에서 발견): imaplib는 명령 인자를 ASCII로만 보내
            # 한글 검색어가 UnicodeEncodeError로 실패했다. 낱말은 IMAP 리터럴(UTF-8 바이트)로 보내야 하는데, imaplib는
            # 명령 하나에 리터럴 하나만 허용하므로 보낸 사람·제목을 따로 찾아 합친다.
            found: set[bytes] = set()
            for field in ("FROM", "SUBJECT"):
                setattr(client, "literal", q.encode("utf-8"))  # noqa: B010 — imaplib 속성이 타입 정의(typeshed)에 없다
                typ, data = client.uid("SEARCH", "CHARSET", "UTF-8", *criteria, field)
                if typ != "OK":
                    raise MailError("메일 검색에 실패했습니다")
                found.update((data[0] or b"").split())
            ordered = sorted(found, key=lambda u: int(u))
        else:
            if q:
                # 보낸 사람 또는 제목에 낱말(IMAP OR)
                typ, data = client.uid("SEARCH", *criteria, "OR", "FROM", f'"{q}"', "SUBJECT", f'"{q}"')
            else:
                typ, data = client.uid("SEARCH", *criteria)
            if typ != "OK":
                raise MailError("메일 검색에 실패했습니다")
            ordered = (data[0] or b"").split()
        uids = ordered[-limit:][::-1]  # 최신부터
        results = []
        for uid in uids:
            uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
            typ, fetched = client.uid("FETCH", uid_s, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            header = email.message_from_bytes(fetched[0][1])
            results.append({"uid": uid_s, "from": _decode(header.get("From")),
                            "subject": _decode(header.get("Subject")), "date": header.get("Date") or ""})
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001, S110
            pass
    _record(conn, user_id, row, "search", {"query": q, "days": days, "results": len(results)})
    return results


def read(conn: sqlite3.Connection, uid: str, mailbox: str | None = None) -> dict:
    user_id = knowledge_access.current_viewer()
    row = _accessible(conn, mailbox, user_id)
    if not re.fullmatch(r"\d{1,12}", str(uid or "")):
        raise MailError("uid는 search 결과의 숫자입니다")
    client = _open_checked(conn, row)
    try:
        typ, fetched = client.uid("FETCH", str(uid), "(BODY.PEEK[])")  # PEEK — 읽음 표시를 바꾸지 않음
        if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
            raise MailError("그 메일을 찾을 수 없습니다")
        msg = email.message_from_bytes(fetched[0][1])
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001, S110
            pass
    body, attachments = _body_text(msg)
    truncated = len(body) > MAX_BODY_CHARS
    _record(conn, user_id, row, "read", {"uid": str(uid), "subject": _decode(msg.get("Subject"))[:200]})
    return {"uid": str(uid), "from": _decode(msg.get("From")), "to": _decode(msg.get("To")),
            "subject": _decode(msg.get("Subject")), "date": msg.get("Date") or "",
            "body": body[:MAX_BODY_CHARS], "truncated": truncated, "attachments": attachments}


def describe_inboxes(conn: sqlite3.Connection) -> str:
    user_id = knowledge_access.current_viewer()
    if user_id is None:
        return "요청한 사람을 확인할 수 없어 메일함 목록을 보여 주지 않습니다."
    rank, teams = knowledge_access._viewer_profile(conn, user_id)
    rows = [r for r in list_inboxes(conn) if knowledge_access.can_see(r["visibility"], rank, teams)]
    if not rows:
        return "읽을 수 있는 메일함이 없습니다."
    return "\n".join(f"- {r['name']} ({r['username']}, 폴더 {r['folder']}): {r['description'] or '설명 없음'}" for r in rows)


def run_action(conn: sqlite3.Connection, action: str, mailbox: str = "", query: str = "", days: int = 7,
               uid: str = "") -> str:
    act = (action or "").strip().lower()
    try:
        if act in ("mailboxes", "list", ""):
            return describe_inboxes(conn)
        if act in ("search", "recent", "find"):
            items = search(conn, mailbox or None, query, days)
            if not items:
                return f"최근 {days}일 안에 조건에 맞는 메일이 없습니다."
            return "\n".join(f"- uid={m['uid']} | {m['date']} | {m['from']} | {m['subject']}" for m in items)
        if act in ("read", "open", "get"):
            m = read(conn, uid, mailbox or None)
            att = ("\n첨부: " + ", ".join(m["attachments"])) if m["attachments"] else ""
            more = "\n(본문이 길어 앞부분만 보여 줍니다)" if m["truncated"] else ""
            return (f"보낸 사람: {m['from']}\n받는 사람: {m['to']}\n날짜: {m['date']}\n제목: {m['subject']}{att}\n\n"
                    f"{m['body']}{more}")
        return "알 수 없는 action입니다. 가능한 값: " + "; ".join(f"{k}={v}" for k, v in ACTIONS.items())
    except MailError as exc:
        return f"메일 조회 실패: {exc}"


def as_crewai_tool(conn: sqlite3.Connection):
    from crewai.tools import tool  # 지연 import

    @tool("mail_inbox")
    def mail_inbox(action: str = "mailboxes", mailbox: str = "", query: str = "", days: int = 7, uid: str = "") -> str:
        """관리자가 등록한 업무 메일함을 읽기 전용으로 조회한다(보내기·지우기·읽음 표시 변경 없음).
        action: mailboxes(읽을 수 있는 메일함) | search(최근 메일 — query=보낸 사람·제목 낱말, days=최근 N일) |
        read(메일 한 통 — uid는 search 결과의 숫자). mailbox는 메일함 이름(하나뿐이면 생략 가능).
        먼저 search로 대상 메일을 찾은 뒤 read로 본문을 확인하라. 메일에 없는 내용을 지어내지 말고, 개인정보는 결과물에서 가려라."""
        from hair_platform import tool_audit  # 외부 데이터 경계(2026-11-26, 개선 계획 1-3) — CrewAI 경로도

        return tool_audit.wrap_external(run_action(conn, action, mailbox, query, days, uid), "메일 수신함")

    return mail_inbox
