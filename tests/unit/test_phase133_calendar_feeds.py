"""캘린더 읽기 연동(2026-11-26, P1-9 3단계 — `calendar_feeds.py`). ICS는 파일·가짜 HTTP로 대신한다(hermetic)."""

from __future__ import annotations

import pytest

from hair_platform import calendar_feeds, knowledge_access

ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//KO",
    # 매주 월요일 10시(서울) 주간 회의 — 12/7은 빠지고(EXDATE), 12/14는 11시로 옮김(RECURRENCE-ID)
    "BEGIN:VEVENT", "UID:weekly-1", "SUMMARY:주간 영업 회의", "LOCATION:3층 회의실",
    "DTSTART;TZID=Asia/Seoul:20261123T100000", "DTEND;TZID=Asia/Seoul:20261123T110000",
    "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6", "EXDATE;TZID=Asia/Seoul:20261207T100000",
    "ORGANIZER;CN=김팀장:mailto:lead@example.com", "ATTENDEE:mailto:a@example.com", "ATTENDEE:mailto:b@example.com",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:weekly-1", "RECURRENCE-ID;TZID=Asia/Seoul:20261214T100000", "SUMMARY:주간 영업 회의(시간 변경)",
    "DTSTART;TZID=Asia/Seoul:20261214T110000", "DTEND;TZID=Asia/Seoul:20261214T120000", "END:VEVENT",
    # UTC로 적힌 고객 미팅(01:00Z = 서울 10:00), 설명은 접힌 줄
    "BEGIN:VEVENT", "UID:cust-1", "SUMMARY:A사 고객 미팅", "DTSTART:20261126T010000Z", "DTEND:20261126T020000Z",
    "DESCRIPTION:견적서 v2 지참\\, 계약 조건", "  확인", "END:VEVENT",
    # 종일 2일(12/1~12/2)
    "BEGIN:VEVENT", "UID:allday-1", "SUMMARY:워크숍", "DTSTART;VALUE=DATE:20261201", "DTEND;VALUE=DATE:20261203", "END:VEVENT",
    # 취소된 일정은 보이지 않는다
    "BEGIN:VEVENT", "UID:cancel-1", "SUMMARY:취소된 회의", "STATUS:CANCELLED",
    "DTSTART:20261127T010000Z", "DTEND:20261127T020000Z", "END:VEVENT",
    "END:VCALENDAR", "",
])


@pytest.fixture
def ics_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HAIR_TIMEZONE", "Asia/Seoul")
    calendar_feeds._CACHE.clear()
    p = tmp_path / "team.ics"
    p.write_text(ICS, encoding="utf-8")
    return str(p)


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _register(conn, src, visibility="all", kind="file", name="team"):
    calendar_feeds.ensure_schema(conn)
    return calendar_feeds.register(conn, _uid(conn, "admin1"), name, kind, src, visibility=visibility,
                                   description="영업팀 공용 캘린더")


def test_recurrence_exdate_override_utc_allday_and_cancelled(client_and_conn, ics_file):
    _client, conn = client_and_conn
    _register(conn, ics_file)
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        r = calendar_feeds.events_between(conn, start="2026-11-23", days=28)
        text = calendar_feeds.run_action(conn, "events", start="2026-11-23", days=28)
    titles = [(e["start"].strftime("%m/%d %H:%M"), e["summary"]) for e in r["events"]]
    assert ("11/23 10:00", "주간 영업 회의") in titles and ("11/30 10:00", "주간 영업 회의") in titles
    assert not any(t.startswith("12/07") for t, _ in titles)  # EXDATE
    assert ("12/14 11:00", "주간 영업 회의(시간 변경)") in titles
    assert not any(t == "12/14 10:00" for t, _ in titles)  # 옮겨진 원래 회차는 빠진다
    assert ("11/26 10:00", "A사 고객 미팅") in titles  # UTC → 서울
    assert "취소된 회의" not in text
    assert "12/01(화)~12/02 종일 | 워크숍" in text
    assert "11/23(월) 10:00–11:00 | 주간 영업 회의 | 장소 3층 회의실 | 주최 김팀장 | 참석 2명" in text
    assert "메모: 견적서 v2 지참, 계약 조건 확인" in text  # 접힌 줄·이스케이프
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'calendar_read'").fetchone()[0] == 2


def test_query_and_empty_range(client_and_conn, ics_file):
    _client, conn = client_and_conn
    _register(conn, ics_file)
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        only = calendar_feeds.run_action(conn, "events", start="2026-11-23", days=28, query="고객")
        none = calendar_feeds.run_action(conn, "events", start="2027-03-01", days=7)
        bad = calendar_feeds.run_action(conn, "events", start="11/23")
    assert "A사 고객 미팅" in only and "주간 영업 회의" not in only
    assert "일정이 없습니다" in none
    assert "YYYY-MM-DD" in bad


def test_visibility_and_unknown_requester(client_and_conn, ics_file):
    _client, conn = client_and_conn
    _register(conn, ics_file, visibility="manager")
    assert "확인할 수 없어" in calendar_feeds.run_action(conn, "events")
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        assert "읽을 수 있는 캘린더가 없습니다" in calendar_feeds.run_action(conn, "events")
    with knowledge_access.as_viewer(_uid(conn, "manager1")):
        assert "team" in calendar_feeds.run_action(conn, "calendars")


def test_url_source_from_env_https_only_and_secret_not_leaked(client_and_conn, ics_file, monkeypatch):
    _client, conn = client_and_conn
    secret = "https://calendar.example.com/private-abc123/basic.ics"
    seen = []

    class FakeResp:
        status_code = 200

        def iter_bytes(self):
            yield ICS.encode()

    class FakeStream:
        def __init__(self, method, url, **kw):
            seen.append(url)

        def __enter__(self):
            return FakeResp()

        def __exit__(self, *a):
            return False

    import httpx

    monkeypatch.setattr(httpx, "stream", FakeStream)
    box = _register(conn, "HAIR_CAL_TEAM_URL", kind="url")
    assert box["source_ready"] is False
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        assert "환경변수 HAIR_CAL_TEAM_URL" in calendar_feeds.run_action(conn, "events")
        monkeypatch.setenv("HAIR_CAL_TEAM_URL", "http://calendar.example.com/x.ics")
        assert "https만" in calendar_feeds.run_action(conn, "events")
        monkeypatch.setenv("HAIR_CAL_TEAM_URL", secret.replace("https://", "webcal://"))
        out = calendar_feeds.run_action(conn, "events", start="2026-11-23", days=7)
    assert "주간 영업 회의" in out and seen == [secret]
    stored = conn.execute("SELECT source FROM calendar_feeds").fetchone()[0]
    assert stored == "HAIR_CAL_TEAM_URL"
    assert "private-abc123" not in str(conn.execute("SELECT after_value FROM audit_log").fetchall())


def test_register_validation_api_rbac_and_reset(client_and_conn, ics_file):
    from hair_platform import data_admin

    client, conn = client_and_conn
    admin = _uid(conn, "admin1")
    calendar_feeds.ensure_schema(conn)
    with pytest.raises(calendar_feeds.CalendarError):
        calendar_feeds.register(conn, admin, "x", "url", "https://calendar.example.com/secret.ics")  # 주소를 직접 넣지 못함
    with pytest.raises(calendar_feeds.CalendarError):
        calendar_feeds.register(conn, admin, "x", "file", "/없는/경로.ics")
    with pytest.raises(calendar_feeds.CalendarError):
        calendar_feeds.register(conn, admin, "x", "caldav", "X")
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/calendar-feeds").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.post("/api/calendar-feeds", json={"name": "team", "source_kind": "file", "source": ics_file, "visibility": "all"})
    assert r.status_code == 201 and r.json()["source_ready"] is True
    fid = r.json()["id"]
    t = client.post(f"/api/calendar-feeds/{fid}/test")
    assert t.status_code == 200 and t.json()["events"] >= 1
    assert client.patch(f"/api/calendar-feeds/{fid}", json={"is_active": False}).json()["is_active"] is False
    assert client.post(f"/api/calendar-feeds/{fid}/test").status_code == 400
    holders = {row[0] for row in conn.execute("SELECT role_name FROM agent_pool WHERE skill_tags LIKE '%캘린더조회%'")}
    assert holders == {"PM", "실적 보고서 작성자", "개발 계획 수립가"}
    data_admin.reset_to_factory_state(conn)
    assert conn.execute("SELECT COUNT(*) FROM calendar_feeds").fetchone()[0] == 0


def test_quoted_tzid_param_with_colon_is_parsed():
    name, params, value = calendar_feeds._split('DTSTART;TZID="GMT+09:00 Seoul":20261126T100000')
    assert name == "DTSTART" and params["TZID"] == "GMT+09:00 Seoul" and value == "20261126T100000"
