"""캘린더 읽기 연동(2026-11-26, P1-9 3단계 — "외부 업무 시스템 연결 부족: 일정 정보 없음").

admin이 업무 캘린더의 **iCal(ICS) 주소**(Google 캘린더 "비공개 iCal 주소", Outlook/Microsoft 365 "게시된 캘린더 ICS",
사내 그룹웨어 ICS 내보내기) 또는 서버의 .ics 파일을 등록하면, "캘린더조회" 스킬을 가진 Agent가 `calendar_events` 도구로
기간 안의 일정을 읽는다(회의 준비·주간 보고·일정 충돌 확인·마감 정리 등). 추가 설치 없음(httpx·표준 라이브러리,
반복 일정 펼치기는 설치돼 있으면 python-dateutil).

**읽기 전용** — ICS를 받아 읽기만 한다(일정 만들기·수정·초대 응답 없음).
**ICS 주소는 저장하지 않는다** — 비공개 iCal 주소는 그 자체가 비밀 키라, 주소가 든 **환경변수 이름**만 둔다(`url_env`).
주소는 https만, 응답 5MB·15초 상한, 5분 캐시.
**누가 읽을 수 있나** — 메일 수신함과 같다: 캘린더마다 공개 범위(`knowledge_access` 규칙 — all·manager·admin·team:<id>,
기본 admin), 요청자를 모르면 거부, 조회마다 활동 기록(`calendar_read`).
**시간대** — `HAIR_TIMEZONE`(예: Asia/Seoul), 없으면 서버 PC의 시간대로 보여 준다.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from hair_platform import audit, knowledge_access

NATIVE_ID = "calendar_read"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 15
CACHE_SECONDS = 300
MAX_EVENTS = 100
MAX_DAYS = 92
ACTIONS = {
    "calendars": "등록된 캘린더 목록",
    "events": "기간 안의 일정(start=YYYY-MM-DD, 기본 오늘 · days=기간, 기본 7일 · query=제목·장소 낱말)",
}

_CACHE: dict[int, tuple[float, str]] = {}


class CalendarError(ValueError):
    """사람(또는 Agent)에게 그대로 보여 줄 수 있는 사유."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS calendar_feeds ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL UNIQUE,"
        " source_kind TEXT NOT NULL,"       # url(환경변수에 든 https ICS 주소) | file(서버의 .ics 파일)
        " source TEXT NOT NULL,"            # url이면 환경변수 이름, file이면 경로
        " visibility TEXT NOT NULL DEFAULT 'admin',"
        " description TEXT NOT NULL DEFAULT '',"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def local_tz() -> tzinfo:
    name = os.environ.get("HAIR_TIMEZONE", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — 잘못된 이름이면 서버 시간대로
            pass
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


# ------------------------------------------------------------------ 등록·관리(admin)

def register(conn: sqlite3.Connection, actor_id: int, name: str, source_kind: str, source: str, *,
             visibility: str = "admin", description: str = "") -> dict:
    if not re.fullmatch(r"[\w가-힣\-]{1,60}", name or ""):
        raise CalendarError("캘린더 이름은 1~60자 한글·영문·숫자·_-만 쓸 수 있습니다")
    source = (source or "").strip()
    if source_kind == "url":
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", source):
            raise CalendarError("iCal 주소가 든 환경변수 이름을 대문자·숫자·_로 적으세요(예: HAIR_CAL_TEAM_URL) — 주소 자체는 저장하지 않습니다")
    elif source_kind == "file":
        if not source.lower().endswith(".ics") or not os.path.isfile(os.path.expanduser(source)):
            raise CalendarError("서버에 그 .ics 파일이 없습니다")
    else:
        raise CalendarError("source_kind는 url 또는 file입니다")
    visibility = knowledge_access.validate(visibility)
    try:
        cur = conn.execute(
            "INSERT INTO calendar_feeds (name, source_kind, source, visibility, description, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (name, source_kind, source, visibility, description or "", actor_id),
        )
    except sqlite3.IntegrityError as exc:
        raise CalendarError("같은 이름의 캘린더가 이미 있습니다") from exc
    audit.record(conn, actor_id, "calendar_feed_registered", "calendar_feeds", cur.lastrowid,
                 after={"name": name, "source_kind": source_kind, "source": source, "visibility": visibility})
    conn.commit()
    return get(conn, cur.lastrowid or 0)


def _public(row) -> dict:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    d["source_ready"] = bool(os.environ.get(d["source"])) if d["source_kind"] == "url" else os.path.isfile(
        os.path.expanduser(d["source"]))
    return d


def get(conn: sqlite3.Connection, feed_id: int) -> dict:
    row = conn.execute("SELECT * FROM calendar_feeds WHERE id = ?", (feed_id,)).fetchone()
    if row is None:
        raise CalendarError("캘린더를 찾을 수 없습니다")
    return _public(row)


def list_feeds(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM calendar_feeds" + ("" if include_inactive else " WHERE is_active = 1") + " ORDER BY name"
    return [_public(r) for r in conn.execute(sql)]


def update(conn: sqlite3.Connection, actor_id: int, feed_id: int, *, is_active: bool | None = None,
           visibility: str | None = None) -> dict:
    before = get(conn, feed_id)
    if is_active is not None:
        conn.execute("UPDATE calendar_feeds SET is_active = ? WHERE id = ?", (1 if is_active else 0, feed_id))
    if visibility is not None:
        conn.execute("UPDATE calendar_feeds SET visibility = ? WHERE id = ?", (knowledge_access.validate(visibility), feed_id))
    _CACHE.pop(feed_id, None)
    audit.record(conn, actor_id, "calendar_feed_updated", "calendar_feeds", feed_id,
                 before={"is_active": before["is_active"], "visibility": before["visibility"]},
                 after={"is_active": is_active, "visibility": visibility})
    conn.commit()
    return get(conn, feed_id)


# ------------------------------------------------------------------ ICS 받기·해석

def _fetch(row) -> str:
    cached = _CACHE.get(row["id"])
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    if row["source_kind"] == "file":
        path = os.path.expanduser(row["source"])
        if not os.path.isfile(path):
            raise CalendarError("서버에 그 .ics 파일이 없습니다")
        if os.path.getsize(path) > MAX_BYTES:
            raise CalendarError("ICS 파일이 5MB를 넘습니다")
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        url = os.environ.get(row["source"], "").strip()
        if not url:
            raise CalendarError(f"서버 환경변수 {row['source']}에 iCal 주소가 없습니다")
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if not url.startswith("https://"):
            raise CalendarError("iCal 주소는 https만 쓸 수 있습니다")
        import httpx

        try:
            with httpx.stream("GET", url, timeout=TIMEOUT_SECONDS, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise CalendarError(f"캘린더를 받지 못했습니다(HTTP {resp.status_code})")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise CalendarError("캘린더 응답이 5MB를 넘습니다")
                    chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
        except CalendarError:
            raise
        except httpx.HTTPError as exc:
            raise CalendarError(f"캘린더 접속 실패: {type(exc).__name__}") from exc  # 주소(비밀)가 담길 수 있어 메시지는 빼고
    if "BEGIN:VCALENDAR" not in text:
        raise CalendarError("ICS(iCalendar) 형식이 아닙니다")
    _CACHE[row["id"]] = (time.monotonic(), text)
    return text


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    # 매개변수 값이 따옴표 안에 ':'를 가질 수 있다(TZID="…") — 간단히 따옴표 밖 첫 ':'로 나눈다
    if head.count('"') % 2:
        inq, cut = False, len(line)
        for pos, ch in enumerate(line):
            if ch == '"':
                inq = not inq
            elif ch == ":" and not inq:
                cut = pos
                break
        head, value = line[:cut], line[cut + 1:]
    name, *params = head.split(";")
    pmap = {}
    for p in params:
        k, _, v = p.partition("=")
        pmap[k.upper()] = v.strip('"')
    return name.upper(), pmap, value


def _text(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _dt(value: str, params: dict[str, str], tz: tzinfo) -> tuple[datetime, bool]:
    """(지역 시각 datetime, 종일 여부)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=tz), True
    utc = value.endswith("Z")
    d = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return d.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz), False
    src: tzinfo = tz
    if params.get("TZID"):
        try:
            src = ZoneInfo(params["TZID"])
        except Exception:  # noqa: BLE001 — Windows 시간대 이름 등은 지역 시간대로 본다
            src = tz
    return d.replace(tzinfo=src).astimezone(tz), False


def _duration(v: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_events(text: str, tz: tzinfo) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": [], "attendees": 0}
            continue
        if line == "END:VEVENT":
            if cur and "start" in cur:
                events.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        name, params, value = _split(line)
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _dt(value, params, tz)
        elif name == "DTEND":
            cur["end"], _ = _dt(value, params, tz)
        elif name == "DURATION":
            cur["duration"] = _duration(value)
        elif name in ("SUMMARY", "LOCATION", "DESCRIPTION", "UID", "STATUS"):
            cur[name.lower()] = _text(value)
        elif name == "ORGANIZER":
            cur["organizer"] = params.get("CN") or value.replace("mailto:", "")
        elif name == "ATTENDEE":
            cur["attendees"] += 1
        elif name == "RRULE":
            cur["rrule"] = value
        elif name == "EXDATE":
            for v in value.split(","):
                cur["exdates"].append(_dt(v, params, tz)[0])
        elif name == "RECURRENCE-ID":
            cur["recurrence_id"], _ = _dt(value, params, tz)
    for e in events:
        if "end" not in e:
            e["end"] = e["start"] + e.pop("duration", timedelta(days=1) if e["all_day"] else timedelta(0))
    return events


def expand(events: list[dict], start: datetime, end: datetime) -> tuple[list[dict], bool]:
    """기간 [start, end)와 겹치는 일정(반복 일정 펼침). 두 번째 값은 반복 펼치기를 못 했는지."""
    overrides = {(e.get("uid"), e["recurrence_id"]) for e in events if e.get("recurrence_id")}
    out, no_rrule = [], False
    try:
        from dateutil.rrule import rrulestr  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — 기본 환경에는 있다(pandas·crewai 의존)
        rrulestr = None
    for e in events:
        if (e.get("status") or "").upper() == "CANCELLED":
            continue
        length = e["end"] - e["start"]
        starts = [e["start"]]
        if e.get("rrule") and not e.get("recurrence_id"):
            if rrulestr is None:
                no_rrule = True
            else:
                try:
                    rule = rrulestr(e["rrule"], dtstart=e["start"], ignoretz=False)
                    starts = list(rule.between(start - length, end, inc=True))[:MAX_EVENTS * 2]  # type: ignore[union-attr]
                except (ValueError, TypeError):
                    starts = [e["start"]]
        for s in starts:
            if s in e["exdates"] or (not e.get("recurrence_id") and (e.get("uid"), s) in overrides):
                continue
            if s < end and s + length > start:
                out.append({**e, "start": s, "end": s + length})
    out.sort(key=lambda x: x["start"])
    return out, no_rrule


# ------------------------------------------------------------------ 조회(도구)

def _accessible(conn: sqlite3.Connection, name: str | None, user_id: int | None) -> sqlite3.Row:
    if user_id is None:
        raise CalendarError("요청한 사람을 확인할 수 없어 일정을 읽지 않았습니다")
    rank, teams = knowledge_access._viewer_profile(conn, user_id)
    rows = [r for r in conn.execute("SELECT * FROM calendar_feeds WHERE is_active = 1 ORDER BY name")
            if knowledge_access.can_see(r["visibility"], rank, teams)]
    if not rows:
        raise CalendarError("읽을 수 있는 캘린더가 없습니다 — admin이 '연동 설정'에서 등록하고 공개 범위를 정해야 합니다")
    if not name:
        if len(rows) == 1:
            return rows[0]
        raise CalendarError("캘린더가 여러 개입니다 — calendar에 이름을 지정하세요: " + ", ".join(r["name"] for r in rows))
    for r in rows:
        if r["name"] == name:
            return r
    raise CalendarError(f"'{name}' 캘린더가 없거나 읽을 권한이 없습니다")


def events_between(conn: sqlite3.Connection, calendar: str | None = None, start: str = "", days: int = 7,
                   query: str = "") -> dict[str, Any]:
    user_id = knowledge_access.current_viewer()
    row = _accessible(conn, calendar, user_id)
    assert user_id is not None  # _accessible이 None을 거부한다
    tz = local_tz()
    try:
        d0 = date.fromisoformat(start) if start else datetime.now(tz).date()
    except ValueError as exc:
        raise CalendarError("start는 YYYY-MM-DD 형식입니다") from exc
    days = max(1, min(int(days or 7), MAX_DAYS))
    t0 = datetime(d0.year, d0.month, d0.day, tzinfo=tz)
    t1 = t0 + timedelta(days=days)
    items, no_rrule = expand(parse_events(_fetch(row), tz), t0, t1)
    q = (query or "").strip().lower()
    if q:
        items = [e for e in items if q in (e.get("summary", "") + " " + e.get("location", "")).lower()]
    truncated = len(items) > MAX_EVENTS
    items = items[:MAX_EVENTS]
    audit.record(conn, user_id, "calendar_read", "calendar_feeds", row["id"],
                 after={"calendar": row["name"], "start": d0.isoformat(), "days": days, "query": q, "events": len(items)})
    conn.commit()
    return {"calendar": row["name"], "start": t0, "end": t1, "events": items, "truncated": truncated,
            "no_rrule": no_rrule, "tz": str(tz)}


_WEEKDAY = "월화수목금토일"


def _fmt(e: dict) -> str:
    s, en = e["start"], e["end"]
    day = f"{s:%m/%d}({_WEEKDAY[s.weekday()]})"
    if e["all_day"]:
        last = en - timedelta(days=1)
        when = day + (" 종일" if last.date() <= s.date() else f"~{last:%m/%d} 종일")
    else:
        when = f"{day} {s:%H:%M}–" + (f"{en:%H:%M}" if en.date() == s.date() else f"{en:%m/%d %H:%M}")
    bits = [when, e.get("summary") or "(제목 없음)"]
    if e.get("location"):
        bits.append("장소 " + e["location"])
    if e.get("organizer"):
        bits.append("주최 " + e["organizer"])
    if e.get("attendees"):
        bits.append(f"참석 {e['attendees']}명")
    if (e.get("status") or "").upper() == "TENTATIVE":
        bits.append("미정")
    line = " | ".join(bits)
    desc = (e.get("description") or "").replace("\n", " ")
    return line + (f"\n  메모: {desc[:200]}" + ("…" if len(desc) > 200 else "") if desc else "")


def describe_feeds(conn: sqlite3.Connection) -> str:
    user_id = knowledge_access.current_viewer()
    if user_id is None:
        return "요청한 사람을 확인할 수 없어 캘린더 목록을 보여 주지 않습니다."
    rank, teams = knowledge_access._viewer_profile(conn, user_id)
    rows = [r for r in list_feeds(conn) if knowledge_access.can_see(r["visibility"], rank, teams)]
    if not rows:
        return "읽을 수 있는 캘린더가 없습니다."
    return "\n".join(f"- {r['name']}: {r['description'] or '설명 없음'}" for r in rows)


def run_action(conn: sqlite3.Connection, action: str, calendar: str = "", start: str = "", days: int = 7,
               query: str = "") -> str:
    act = (action or "").strip().lower()
    try:
        if act in ("calendars", "list", ""):
            return describe_feeds(conn)
        if act in ("events", "schedule", "agenda"):
            r = events_between(conn, calendar or None, start, days, query)
            head = (f"[{r['calendar']}] {r['start']:%Y-%m-%d} ~ {(r['end'] - timedelta(days=1)):%Y-%m-%d} "
                    f"({r['tz']}) 일정 {len(r['events'])}건")
            if not r["events"]:
                return head + " — 이 기간에 일정이 없습니다."
            notes = []
            if r["truncated"]:
                notes.append(f"(앞 {MAX_EVENTS}건만 — 기간을 줄이세요)")
            if r["no_rrule"]:
                notes.append("(반복 일정은 첫 회만 보입니다 — 서버에 python-dateutil이 없습니다)")
            return head + "\n" + "\n".join("- " + _fmt(e) for e in r["events"]) + ("\n" + " ".join(notes) if notes else "")
        return "알 수 없는 action입니다. 가능한 값: " + "; ".join(f"{k}={v}" for k, v in ACTIONS.items())
    except CalendarError as exc:
        return f"캘린더 조회 실패: {exc}"


def as_crewai_tool(conn: sqlite3.Connection):
    from crewai.tools import tool  # 지연 import

    @tool("calendar_events")
    def calendar_events(action: str = "calendars", calendar: str = "", start: str = "", days: int = 7, query: str = "") -> str:
        """관리자가 등록한 업무 캘린더의 일정을 읽기 전용으로 조회한다(일정 만들기·수정 없음).
        action: calendars(읽을 수 있는 캘린더) | events(기간 안의 일정 — start=YYYY-MM-DD(기본 오늘), days=기간(기본 7일),
        query=제목·장소 낱말). calendar는 캘린더 이름(하나뿐이면 생략 가능). 반복 일정은 펼쳐서 보여 준다.
        날짜·시각은 결과 그대로 옮기고, 캘린더에 없는 일정을 지어내지 마라."""
        from hair_platform import tool_audit  # 외부 데이터 경계(2026-11-26, 개선 계획 1-3) — CrewAI 경로도

        return tool_audit.wrap_external(run_action(conn, action, calendar, start, days, query), "캘린더")

    return calendar_events
