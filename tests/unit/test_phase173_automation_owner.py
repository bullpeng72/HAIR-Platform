"""자동화를 만든 사람 점검(2026-11-28) — 만든 사람의 계정이 꺼지거나 admin·manager가 아니게 되면 정기 실행·이벤트 트리거·
외부 입력 트리거가 그 이름으로 계속 돌던 문제. 실행 직전에 멈추고, 인계 점검에서 넘기면 다시 켜진다."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from hair_platform import automation, automation_owner, inbound, triggers


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _tpl(conn):
    tid = conn.execute("INSERT INTO pipeline_templates (name) VALUES ('owner test')").lastrowid
    conn.execute("INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name) VALUES (?, 1, 's')", (tid,))
    return tid


def _schedule(conn, owner, tid):
    return conn.execute(
        "INSERT INTO pipeline_schedules (name, pipeline_template_id, brief_text, role_name, interval, time_hhmm, created_by, "
        "next_run_at) VALUES ('매일 보고', ?, 'b', 'r', 'daily', '09:00', ?, '2026-01-01T09:00')", (tid, owner)).lastrowid


def _audit_actions(conn, table, row_id):
    return [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE subject_type = ? AND subject_id = ?", (table, row_id))]


def test_schedule_pauses_when_owner_deactivated_and_resumes_on_transfer(client_and_conn):
    client, conn = client_and_conn
    mgr, admin = _uid(conn, "manager1"), _uid(conn, "admin1")
    tid = _tpl(conn)
    sid = _schedule(conn, mgr, tid)
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (mgr,))
    conn.commit()
    started = automation.run_due_schedules(conn, now=dt.datetime(2026, 1, 1, 10, 0), run_stages=lambda *a: None)
    assert started == []
    row = conn.execute("SELECT is_active, last_error FROM pipeline_schedules WHERE id = ?", (sid,)).fetchone()
    assert row["is_active"] == 0 and row["last_error"].startswith(automation_owner.OWNER_PAUSED_PREFIX)
    assert "pipeline_schedule_paused" in _audit_actions(conn, "pipeline_schedules", sid)
    assert conn.execute("SELECT COUNT(*) FROM briefs WHERE submitted_by = ?", (mgr,)).fetchone()[0] == 0
    # 인계 점검 목록에 멈춘 것으로 보인다
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('매니저', 'PM', '[]', ?)", (mgr,)).lastrowid
    conn.commit()
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    autos = client.get(f"/api/human-pool/{h}/handover").json()["automations"]
    assert [(a["name"], a["paused"]) for a in autos] == [("매일 보고", True)]
    # 넘겨받을 사람은 켜진 admin·manager여야 한다
    assert client.post(f"/api/users/{mgr}/transfer-automations", json={"to_user_id": _uid(conn, "viewer1")}).status_code == 400
    assert client.post(f"/api/users/{mgr}/transfer-automations", json={"to_user_id": mgr}).status_code == 400
    r = client.post(f"/api/users/{mgr}/transfer-automations", json={"to_user_id": admin})
    assert r.status_code == 200 and r.json() == {"transferred": 1}
    row = conn.execute("SELECT is_active, last_error, created_by, next_run_at FROM pipeline_schedules WHERE id = ?", (sid,)).fetchone()
    assert (row["is_active"], row["last_error"], row["created_by"]) == (1, None, admin)
    assert row["next_run_at"] > dt.datetime.now().isoformat(timespec="minutes")  # 지난 시각에 곧바로 돌지 않는다
    assert "pipeline_schedule_transferred" in _audit_actions(conn, "pipeline_schedules", sid)


def test_manually_turned_off_automation_is_not_resumed_by_transfer(client_and_conn):
    _, conn = client_and_conn
    mgr, admin = _uid(conn, "manager1"), _uid(conn, "admin1")
    sid = _schedule(conn, mgr, _tpl(conn))
    conn.execute("UPDATE pipeline_schedules SET is_active = 0 WHERE id = ?", (sid,))  # 사람이 직접 끔
    conn.commit()
    assert automation_owner.transfer(conn, admin, mgr, admin) == 0
    assert conn.execute("SELECT created_by, is_active FROM pipeline_schedules WHERE id = ?", (sid,)).fetchone()[:] == (mgr, 0)


def test_event_trigger_pauses_when_owner_demoted(client_and_conn):
    _, conn = client_and_conn
    mgr = _uid(conn, "manager1")
    tid = _tpl(conn)
    trig = conn.execute("INSERT INTO automation_triggers (name, event, action_template_id, role_name, brief_text, created_by) "
                        "VALUES ('완료 후', 'pipeline_completed', ?, 'r', 'b', ?)", (tid, mgr)).lastrowid
    conn.execute("UPDATE users SET role = 'member' WHERE id = ?", (mgr,))
    conn.commit()
    with patch.object(triggers, "_events", side_effect=AssertionError("이벤트를 읽으면 안 된다")):
        assert triggers.run_triggers(conn, run_stages=lambda *a: None) == []
    row = conn.execute("SELECT is_active, last_error FROM automation_triggers WHERE id = ?", (trig,)).fetchone()
    assert row["is_active"] == 0 and "admin·manager가 아닙니다" in row["last_error"]


def test_inbound_trigger_keeps_queue_while_paused_then_runs_for_new_owner(client_and_conn):
    _, conn = client_and_conn
    mgr, admin = _uid(conn, "manager1"), _uid(conn, "admin1")
    tid = _tpl(conn)
    t = conn.execute("INSERT INTO inbound_triggers (name, source, action_template_id, role_name, brief_text, created_by) "
                     "VALUES ('고객 문의', 'webhook', ?, 'r', 'b', ?)", (tid, mgr)).lastrowid
    conn.execute("INSERT INTO inbound_events (trigger_id, event_key, payload, status) VALUES (?, 'k', '{\"text\": \"문의\"}', 'queued')", (t,))
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (mgr,))
    conn.commit()
    assert inbound.fire(conn, trigger_id=t, run_stages=lambda *a: None) == []
    assert conn.execute("SELECT status FROM inbound_events WHERE trigger_id = ?", (t,)).fetchone()[0] == "queued"
    assert conn.execute("SELECT is_active FROM inbound_triggers WHERE id = ?", (t,)).fetchone()[0] == 0
    assert automation_owner.transfer(conn, admin, mgr, admin) == 1
    started = inbound.fire(conn, trigger_id=t, run_stages=lambda *a: None)
    assert len(started) == 1
    assert conn.execute("SELECT actor_id FROM pipeline_runs WHERE id = ?", (started[0],)).fetchone()[0] == admin


def test_transfer_automations_requires_admin_or_manager(client_and_conn):
    client, conn = client_and_conn
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    r = client.post(f"/api/users/{_uid(conn, 'manager1')}/transfer-automations", json={"to_user_id": _uid(conn, "admin1")})
    assert r.status_code == 403


def test_pause_sends_one_notification_and_again_only_if_new_owner_also_leaves(client_and_conn):
    from hair_platform import integrations

    _, conn = client_and_conn
    mgr, admin = _uid(conn, "manager1"), _uid(conn, "admin1")
    sid = _schedule(conn, mgr, _tpl(conn))
    conn.commit()
    integrations.set_notify_config(conn, admin, "https://hooks.example.com/x", ["automation_paused"], [])
    sent = []
    with patch.object(integrations, "send_webhook", side_effect=lambda url, text: sent.append(text) or (True, None)):
        assert integrations.run_notifications(conn) == 0
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (mgr,))
        conn.commit()
        automation.run_due_schedules(conn, now=dt.datetime(2026, 1, 1, 10, 0), run_stages=lambda *a: None)
        assert integrations.run_notifications(conn) == 1
        assert integrations.run_notifications(conn) == 0  # 같은 멈춤은 한 번만
        assert "정기 실행 「매일 보고」 멈춤 — manager1 계정이 꺼져 있습니다" in sent[0]
        automation_owner.transfer(conn, admin, mgr, admin)
        assert integrations.run_notifications(conn) == 0  # 다시 켜져서 알릴 것 없음
    assert conn.execute("SELECT is_active FROM pipeline_schedules WHERE id = ?", (sid,)).fetchone()[0] == 1
