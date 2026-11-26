"""연결 상태 한눈에(2026-11-26, 개선 계획 2-3 — `connection_health.py`)."""

from __future__ import annotations

import os
import sqlite3

from hair_platform import calendar_feeds, connection_health, data_admin, data_connectors, knowledge_access, mail_inbox


def _admin(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]


def _status(conn, kind, name):
    return next(i for i in connection_health.summary(conn) if i["kind"] == kind and i["name"] == name)


def test_db_connection_records_ok_on_use_and_failure_when_file_disappears(client_and_conn, tmp_path):
    _client, conn = client_and_conn
    path = tmp_path / "sales.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t (x INTEGER)")
    c.commit()
    c.close()
    data_connectors.register(conn, _admin(conn), "sales_db", "sqlite", str(path))
    assert _status(conn, "data_connections", "sales_db")["status"] == "unchecked"
    assert "확인하지 않은 연동" in " ".join(connection_health.warnings(conn))
    data_connectors.run_action(conn, "query", sql="SELECT COUNT(*) FROM t")  # Agent의 실제 조회도 기록
    assert _status(conn, "data_connections", "sales_db")["status"] == "ok"
    # SQL을 틀린 것은 연결 문제가 아니다 — 상태를 바꾸지 않는다
    data_connectors.run_action(conn, "query", sql="SELECT * FROM 없는_테이블")
    assert _status(conn, "data_connections", "sales_db")["status"] == "ok"
    os.remove(path)
    assert "DB 조회 실패" in data_connectors.run_action(conn, "tables")
    st = _status(conn, "data_connections", "sales_db")
    assert st["status"] == "failed" and st["error"]
    assert any("사내 DB 「sales_db」 연결 실패" in w for w in connection_health.warnings(conn))


def test_mail_and_calendar_failures_are_recorded(client_and_conn, tmp_path, monkeypatch):
    _client, conn = client_and_conn
    admin = _admin(conn)
    monkeypatch.delenv("HAIR_MAIL_X_PASSWORD", raising=False)
    mail_inbox.register(conn, admin, "support", "imap.example.com", "s@example.com", "HAIR_MAIL_X_PASSWORD", visibility="all")
    ics = tmp_path / "team.ics"
    ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    calendar_feeds.register(conn, admin, "team", "file", str(ics), visibility="all")
    calendar_feeds._CACHE.clear()
    with knowledge_access.as_viewer(admin):
        assert "환경변수" in mail_inbox.run_action(conn, "search")
        calendar_feeds.run_action(conn, "events")
        assert _status(conn, "calendar_feeds", "team")["status"] == "ok"
        os.remove(ics)
        calendar_feeds._CACHE.clear()
        calendar_feeds.run_action(conn, "events")
    assert _status(conn, "mail_inboxes", "support")["status"] == "failed"
    assert _status(conn, "calendar_feeds", "team")["status"] == "failed"


def test_api_is_admin_only_and_reset_clears_health(client_and_conn, tmp_path):
    client, conn = client_and_conn
    path = tmp_path / "a.db"
    sqlite3.connect(path).close()
    data_connectors.register(conn, _admin(conn), "a_db", "sqlite", str(path))
    data_connectors.run_action(conn, "tables")
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/admin/connection-status").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    body = client.get("/api/admin/connection-status").json()
    assert [(i["name"], i["status"]) for i in body["items"]] == [("a_db", "ok")] and body["warnings"] == []
    data_admin.reset_to_factory_state(conn)
    assert conn.execute("SELECT COUNT(*) FROM connection_health").fetchone()[0] == 0
