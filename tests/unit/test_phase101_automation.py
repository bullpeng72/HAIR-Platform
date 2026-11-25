"""사람이 누르지 않아도 도는 자동화(2026-11-23, "P1 진행해줘" — P1-5): 개인 API 키·외부 알림 웹훅·
정기 실행."""

from __future__ import annotations

import datetime as dt

import pytest

from hair_platform import automation, integrations


def _login(client, username, password):
    assert client.post("/login", json={"username": username, "password": password}).status_code == 200


# ---------- 개인 API 키 ----------


def test_api_key_authenticates_as_owner_and_can_be_revoked(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    created = client.post("/api/api-keys", json={"name": "주간 실적 cron"}).json()
    token = created["token"]
    assert token.startswith("hair_") and created["key_prefix"] == token[:12]
    stored = conn.execute("SELECT key_hash FROM api_keys WHERE id = ?", (created["id"],)).fetchone()[0]
    assert token not in stored  # 원문은 저장하지 않는다
    client.post("/logout")

    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post("/api/briefs", json={"raw_text": "외부 시스템이 올린 업무"}, headers=headers)
    assert resp.status_code == 201, resp.text
    submitter = conn.execute("SELECT u.username FROM briefs b JOIN users u ON u.id = b.submitted_by "
                             "WHERE b.id = ?", (resp.json()["id"],)).fetchone()[0]
    assert submitter == "member1"
    # 키가 권한을 넓히지 않는다 — member는 admin 화면을 못 본다.
    assert client.get("/api/integrations/notify", headers=headers).status_code == 403
    assert conn.execute("SELECT last_used_at FROM api_keys WHERE id = ?", (created["id"],)).fetchone()[0]

    _login(client, "member1", "memberpass")
    assert client.delete(f"/api/api-keys/{created['id']}").status_code == 200
    client.post("/logout")
    assert client.post("/api/briefs", json={"raw_text": "x"}, headers=headers).status_code == 401


def test_other_users_keys_and_inactive_owner(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    key = client.post("/api/api-keys", json={"name": "k"}).json()
    _login(client, "manager1", "managerpass")
    assert client.delete(f"/api/api-keys/{key['id']}").status_code == 403
    assert client.get("/api/api-keys?all_users=true").status_code == 403
    _login(client, "admin1", "adminpass")
    assert any(k["id"] == key["id"] for k in client.get("/api/api-keys?all_users=true").json())
    client.post("/logout")
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'member1'")
    conn.commit()
    assert client.get("/api/briefs", headers={"Authorization": f"Bearer {key['token']}"}).status_code == 401


# ---------- 외부 알림 ----------


def test_notifications_seed_history_then_send_new_events_once(client_and_conn, monkeypatch):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id) VALUES (?, 'assignment', 999)", (admin,))
    conn.commit()
    sent: list[str] = []
    monkeypatch.setattr(integrations, "send_webhook", lambda url, text: (sent.append(text) or (True, None)))

    _login(client, "admin1", "adminpass")
    resp = client.put("/api/integrations/notify", json={"webhook_url": "https://hooks.example/abc", "events": ["approval_pending"]})
    assert resp.status_code == 200, resp.text
    assert integrations.run_notifications(conn) == 0  # 설정 전부터 있던 결재 대기는 보내지 않는다

    new_id = conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id) VALUES (?, 'deliverable', 1)", (admin,)).lastrowid
    conn.commit()
    assert integrations.run_notifications(conn) == 1
    assert f"결재 대기 #{new_id}" in sent[0] and "결과물 검토" in sent[0]
    assert integrations.run_notifications(conn) == 0  # 같은 이벤트는 한 번만
    # 끈 종류는 보내지 않는다.
    conn.execute("INSERT INTO agent_execution_jobs (task_id, requested_by, status, error) VALUES (1, ?, 'failed', 'x')", (admin,)) if conn.execute("SELECT 1 FROM tasks WHERE id = 1").fetchone() else None
    assert integrations.run_notifications(conn) == 0
    # URL 검증·감사로그에는 호스트만.
    assert client.put("/api/integrations/notify", json={"webhook_url": "ftp://x", "events": []}).status_code == 400
    after = conn.execute("SELECT after_value FROM audit_log WHERE action = 'notify_config_changed' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "hooks.example" in after and "/abc" not in after


def test_failed_send_is_recorded(client_and_conn, monkeypatch):
    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    integrations.set_notify_config(conn, admin, "https://hooks.example/x", ["approval_pending"])
    conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id) VALUES (?, 'assignment', 5)", (admin,))
    conn.commit()
    monkeypatch.setattr(integrations, "send_webhook", lambda url, text: (False, "HTTP 404: no_service"))
    integrations.run_notifications(conn)
    assert integrations.get_notify_config(conn)["last_error"] == "HTTP 404: no_service"


# ---------- 정기 실행 ----------


@pytest.mark.parametrize(
    "now, interval, weekday, expected",
    [
        (dt.datetime(2026, 11, 23, 8, 0), "daily", None, dt.datetime(2026, 11, 23, 9, 0)),  # 월 08:00 → 오늘 09:00
        (dt.datetime(2026, 11, 23, 9, 0), "daily", None, dt.datetime(2026, 11, 24, 9, 0)),  # 같은 시각은 제외
        (dt.datetime(2026, 11, 28, 10, 0), "weekdays", None, dt.datetime(2026, 11, 30, 9, 0)),  # 토 → 다음 월
        (dt.datetime(2026, 11, 23, 10, 0), "weekly", 0, dt.datetime(2026, 11, 30, 9, 0)),  # 월 10:00 → 다음 주 월
        (dt.datetime(2026, 11, 23, 10, 0), "weekly", 4, dt.datetime(2026, 11, 27, 9, 0)),  # → 이번 주 금
    ],
)
def test_next_run_after(now, interval, weekday, expected):
    assert automation.next_run_after(now, interval, "09:00", weekday) == expected


def test_schedule_fires_brief_task_and_pipeline_run(client_and_conn):
    client, conn = client_and_conn
    tpl = conn.execute("SELECT id FROM pipeline_templates WHERE name = '실적 보고 파이프라인'").fetchone()[0]
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/pipeline-schedules", json={
        "name": "주간 실적", "pipeline_template_id": tpl, "brief_text": "지난주 실적 보고서를 써줘",
        "role_name": "실적 보고서 작성자", "interval": "weekly", "weekday": 0, "time_hhmm": "09:00"})
    assert resp.status_code == 201, resp.text
    sched = resp.json()
    assert sched["when"] == "매주 월요일 09:00" and sched["next_run_at"]

    ran: list[tuple[int, int]] = []
    due = dt.datetime.fromisoformat(sched["next_run_at"])
    assert automation.run_due_schedules(conn, due - dt.timedelta(minutes=1), lambda r, a: ran.append((r, a))) == []
    started = automation.run_due_schedules(conn, due, lambda r, a: ran.append((r, a)))
    assert len(started) == 1 and ran[0][0] == started[0]
    manager = conn.execute("SELECT id FROM users WHERE username = 'manager1'").fetchone()[0]
    assert ran[0][1] == manager  # 만든 사람 이름으로 실행
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (started[0],)).fetchone()
    assert run["pipeline_template_id"] == tpl
    brief = conn.execute("SELECT b.raw_text FROM tasks t JOIN briefs b ON b.id = t.brief_id WHERE t.id = ?", (run["task_id"],)).fetchone()[0]
    assert "정기 실행 「주간 실적」" in brief
    after = automation.get_schedule(conn, sched["id"])
    assert after is not None
    assert after["next_run_at"] == (due + dt.timedelta(days=7)).isoformat(timespec="minutes")
    assert automation.run_due_schedules(conn, due, lambda r, a: None) == []  # 같은 시각에 두 번 돌지 않는다


def test_schedule_validation_and_permissions(client_and_conn):
    client, conn = client_and_conn
    tpl = conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()[0]
    body = {"name": "n", "pipeline_template_id": tpl, "brief_text": "b", "role_name": "r",
            "interval": "weekly", "time_hhmm": "25:00"}
    _login(client, "member1", "memberpass")
    assert client.post("/api/pipeline-schedules", json=body).status_code == 403
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/pipeline-schedules", json=body)
    assert resp.status_code == 400 and "요일" in resp.json()["detail"]
    body["weekday"] = 2
    assert "HH:MM" in client.post("/api/pipeline-schedules", json=body).json()["detail"]
    body["time_hhmm"] = "07:30"
    created = client.post("/api/pipeline-schedules", json=body).json()
    off = client.patch(f"/api/pipeline-schedules/{created['id']}", json={"active": False}).json()
    assert off["is_active"] == 0 and off["next_run_at"] is None
    assert client.delete(f"/api/pipeline-schedules/{created['id']}").status_code == 200


def test_loop_only_starts_under_hair_serve(monkeypatch):
    monkeypatch.delenv("HAIR_AUTOMATION", raising=False)
    assert automation.start_loop(None) is False  # pyright: ignore[reportArgumentType]
