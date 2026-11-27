"""캘린더에서 부재 가져오기(2026-11-28, Resource Management 12번) — 휴가 일정만, 사람 찾기(제목 이름·주최자), 반차·모호·겹침은
넣지 않음, 미리 보기 → 적용."""

from __future__ import annotations

from datetime import date, timedelta

from hair_platform import calendar_feeds


def _d(n: int) -> str:
    return (date.today() + timedelta(days=n)).strftime("%Y%m%d")


def _event(uid, summary, start, end, organizer=""):
    org = f"ORGANIZER;CN={organizer}:mailto:x@example.com\r\n" if organizer else ""
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART;VALUE=DATE:{start}\r\n"
            f"DTEND;VALUE=DATE:{end}\r\n{org}END:VEVENT\r\n")


def _setup(client_and_conn, tmp_path):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    kim = conn.execute("INSERT INTO human_pool (name, role, skill_tags, employee_id) VALUES ('김하나', 'QA', '[]', '20260001')").lastrowid
    lee = conn.execute("INSERT INTO human_pool (name, role, skill_tags, employee_id) VALUES ('이둘', 'QA', '[]', '20260002')").lastrowid
    park = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('박셋', 'QA', '[]', ?)", (member,)).lastrowid
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
           + _event("a", "김하나 연차", _d(2), _d(4))                    # 2일(끝 날짜는 포함 안 함)
           + _event("b", "이둘 반차", _d(3), _d(4))                      # 반나절 → 건너뜀
           + _event("c", "주간 팀 회의", _d(1), _d(2))                    # 휴가 아님 → 줄에도 없음
           + _event("d", "김하나·이둘 휴가", _d(5), _d(6))                 # 두 사람 → 확인 필요
           + _event("e", "crypto 세미나", _d(6), _d(7))                   # pto 오탐 없음
           + _event("f", "Vacation", _d(8), _d(11), organizer="박셋")     # 주최자 이름
           + _event("g", "외부인 휴가", _d(9), _d(10))                     # 사람 없음
           + _event("h", "이둘 병가", _d(20), _d(21))
           + "END:VCALENDAR\r\n")
    path = tmp_path / "team.ics"
    path.write_text(ics, encoding="utf-8")
    feed = calendar_feeds.register(conn, admin, "팀일정", "file", str(path))
    conn.execute("INSERT INTO resource_absences (resource_type, resource_id, starts_on, ends_on, created_by) VALUES ('human', ?, ?, ?, ?)",
                 (lee, (date.today() + timedelta(days=20)).isoformat(), (date.today() + timedelta(days=22)).isoformat(), admin))
    conn.commit()
    return client, conn, feed["id"], kim, lee, park


def test_preview_then_apply(client_and_conn, tmp_path):
    client, conn, fid, kim, _lee, park = _setup(client_and_conn, tmp_path)
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.post("/api/human-pool/import-calendar", json={"feed_id": fid}).status_code == 403  # admin 전용
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    r = client.post("/api/human-pool/import-calendar", json={"feed_id": fid, "days": 60}).json()
    by = {x["event"].split(" ", 1)[1]: x for x in r["rows"]}
    assert "주간 팀 회의" not in by and "crypto 세미나" not in by
    assert by["김하나 연차"]["action"] == "create" and by["김하나 연차"]["employee_id"] == "20260001"
    assert by["이둘 반차"]["action"] == "skip"
    assert by["김하나·이둘 휴가"]["action"] == "error" and "여러 사람" in by["김하나·이둘 휴가"]["detail"]
    assert by["Vacation"]["action"] == "create" and "주최자" in by["Vacation"]["detail"]
    assert by["외부인 휴가"]["action"] == "error"
    assert by["이둘 병가"]["action"] == "skip"  # 이미 겹치는 부재
    assert r["created"] == 2 and r["applied"] is False
    assert conn.execute("SELECT COUNT(*) FROM resource_absences").fetchone()[0] == 1  # 미리 보기는 저장 안 함
    r = client.post("/api/human-pool/import-calendar", json={"feed_id": fid, "days": 60, "apply": True}).json()
    assert r["created"] == 2
    got = {row[0]: (row[1], row[2]) for row in conn.execute(
        "SELECT resource_id, starts_on, ends_on FROM resource_absences WHERE resource_id IN (?, ?)", (kim, park))}
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731
    assert got == {kim: (d(2), d(3)), park: (d(8), d(10))}
    assert conn.execute("SELECT reason FROM resource_absences WHERE resource_id = ?", (kim,)).fetchone()[0] == "캘린더 팀일정: 김하나 연차"
    # 다시 적용하면 겹쳐서 건너뛴다
    again = client.post("/api/human-pool/import-calendar", json={"feed_id": fid, "days": 60, "apply": True}).json()
    assert again["created"] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'resource_absences_imported'").fetchone()[0] == 1


def test_days_window_and_bad_input(client_and_conn, tmp_path):
    client, _, fid, *_ = _setup(client_and_conn, tmp_path)
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    r = client.post("/api/human-pool/import-calendar", json={"feed_id": fid, "days": 4}).json()
    assert [x["event"].split(" ", 1)[1] for x in r["rows"]] == ["김하나 연차", "이둘 반차"]  # 4일 안만
    assert client.post("/api/human-pool/import-calendar", json={"feed_id": 999}).status_code == 400
    assert client.post("/api/human-pool/import-calendar", json={"feed_id": "1"}).status_code == 400
