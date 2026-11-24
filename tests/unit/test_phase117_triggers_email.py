"""P1-5 남은 항목(2026-11-24 — "go ahead"): 이벤트 트리거 + 메일 알림."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, integrations, pipeline_runtime, triggers
from hair_platform.auth import Role, create_user


@pytest.fixture
def env():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "trig_admin", "pw", Role.ADMIN)
    tpl = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pipeline_templates")}
    return conn, admin, tpl


def _completed_run(conn, admin, template_id, output="## 재무 결과\nNPV +104.18백만 원"):
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    run = pipeline_runtime.start_pipeline_run(conn, admin, template_id, task)
    conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
                 "VALUES (?, 'agent', 1, ?, 'markdown', 'approved')", (task, output))
    conn.execute("UPDATE pipeline_runs SET status = 'completed' WHERE id = ?", (run,))
    conn.commit()
    return task, run


def _trigger(conn, admin, tpl, **over):
    fields = {"name": "재무 → 제안서", "event": "pipeline_completed",
              "source_template_id": tpl["재무 분석 보고 파이프라인"], "action_template_id": tpl["제안서 작성 파이프라인"],
              "role_name": "제안서 작성", "brief_text": "아래 이전 결과물로 제안서를 써 줘"}
    fields.update(over)
    return triggers.create(conn, admin, fields)


# ---------------------------------------------------------------- triggers


def test_validation_blocks_self_loop_and_missing_source(env):
    conn, admin, tpl = env
    with pytest.raises(triggers.TriggerError, match="무한 반복"):
        _trigger(conn, admin, tpl, action_template_id=tpl["재무 분석 보고 파이프라인"])
    with pytest.raises(triggers.TriggerError, match="어느 파이프라인"):
        _trigger(conn, admin, tpl, source_template_id=None)
    with pytest.raises(triggers.TriggerError):
        _trigger(conn, admin, tpl, event="nope")


def test_existing_events_are_not_replayed_and_new_completion_fires_with_upstream(env):
    conn, admin, tpl = env
    _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"], "## 예전 결과")  # 트리거 전
    t = _trigger(conn, admin, tpl)
    started_stages = []
    assert triggers.run_triggers(conn, run_stages=lambda rid, actor: started_stages.append(rid)) == []
    _task, _run = _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"])
    started = triggers.run_triggers(conn, run_stages=lambda rid, actor: started_stages.append(rid))
    assert len(started) == 1 and started_stages == started
    new_run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (started[0],)).fetchone()
    assert new_run["pipeline_template_id"] == tpl["제안서 작성 파이프라인"]
    brief = conn.execute("SELECT b.raw_text FROM tasks t JOIN briefs b ON b.id = t.brief_id WHERE t.id = ?",
                         (new_run["task_id"],)).fetchone()[0]
    assert "아래 이전 결과물로 제안서를 써 줘" in brief and "NPV +104.18백만 원" in brief and "예전 결과" not in brief
    assert triggers.run_triggers(conn, run_stages=lambda *a: None) == []  # 같은 이벤트로 두 번 안 함
    assert triggers.get(conn, t["id"])["fired_count"] == 1


def test_other_template_completion_does_not_fire(env):
    conn, admin, tpl = env
    _trigger(conn, admin, tpl)
    _completed_run(conn, admin, tpl["실적 보고 파이프라인"])
    assert triggers.run_triggers(conn, run_stages=lambda *a: None) == []


def test_chain_depth_is_limited(env):
    conn, admin, tpl = env
    a, b = tpl["재무 분석 보고 파이프라인"], tpl["제안서 작성 파이프라인"]
    _trigger(conn, admin, tpl, name="A→B", source_template_id=a, action_template_id=b)
    _trigger(conn, admin, tpl, name="B→A", source_template_id=b, action_template_id=a)
    _completed_run(conn, admin, a)
    fired = 0
    for _ in range(8):  # 시작된 실행을 곧바로 완료시켜 연쇄를 흉내 낸다
        started = triggers.run_triggers(conn, run_stages=lambda *a: None)
        for rid in started:
            conn.execute("UPDATE pipeline_runs SET status = 'completed' WHERE id = ?", (rid,))
        conn.commit()
        fired += len(started)
        if not started:
            break
    assert fired == triggers.MAX_CHAIN_DEPTH
    errors = [r[0] for r in conn.execute("SELECT last_error FROM automation_triggers WHERE last_error IS NOT NULL")]
    assert errors and "연쇄" in errors[0]


def test_deliverable_approved_trigger_and_reactivation_does_not_replay(env):
    conn, admin, tpl = env
    t = _trigger(conn, admin, tpl, name="승인 → 실적", event="deliverable_approved", source_template_id=None,
                 action_template_id=tpl["실적 보고 파이프라인"])
    triggers.set_active(conn, admin, t["id"], False)
    _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"], "꺼져 있던 동안 승인된 결과물")
    triggers.set_active(conn, admin, t["id"], True)
    assert triggers.run_triggers(conn, run_stages=lambda *a: None) == []
    _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"], "새로 승인된 결과물")
    started = triggers.run_triggers(conn, run_stages=lambda *a: None)
    assert len(started) == 1


def test_trigger_api_permissions(client_and_conn):
    client, conn = client_and_conn
    tpl = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pipeline_templates")}
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.get("/api/automation-triggers").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    body = {"name": "재무 → 제안서", "event": "pipeline_completed", "source_template_id": tpl["재무 분석 보고 파이프라인"],
            "action_template_id": tpl["제안서 작성 파이프라인"], "role_name": "제안서 작성", "brief_text": "이어서"}
    r = client.post("/api/automation-triggers", json=body)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert client.patch(f"/api/automation-triggers/{tid}", json={"active": False}).json()["is_active"] == 0
    assert client.get("/api/automation-triggers").json()["triggers"][0]["id"] == tid
    assert client.post("/api/automation-triggers", json={**body, "action_template_id": body["source_template_id"]}).status_code == 400
    assert client.delete(f"/api/automation-triggers/{tid}").status_code == 200


# -------------------------------------------------------------------- email


def test_email_recipients_validated_and_existing_events_not_mailed(env, monkeypatch):
    conn, admin, tpl = env
    monkeypatch.setenv("HAIR_SMTP_HOST", "smtp.example.com")
    with pytest.raises(ValueError, match="invalid_emails"):
        integrations.set_notify_config(conn, admin, "", ["pipeline_completed"], ["not-an-email"])
    _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"])  # 메일을 켜기 전 이벤트
    cfg = integrations.set_notify_config(conn, admin, "", ["pipeline_completed"], ["ops@example.com", "ops@example.com"])
    assert cfg["emails"] == ["ops@example.com"] and cfg["smtp_configured"] and cfg["smtp_host"] == "smtp.example.com"
    sent = []
    with patch.object(integrations, "send_email", side_effect=lambda to, subj, text: sent.append((to, subj, text)) or (True, None)):
        assert integrations.run_notifications(conn) == 0  # 켜기 전 이벤트는 보내지 않는다
        _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"])
        assert integrations.run_notifications(conn) == 1
        assert integrations.run_notifications(conn) == 0  # 같은 이벤트 두 번 안 보냄
    assert sent[0][0] == ["ops@example.com"] and "파이프라인 완료" in sent[0][1]


def test_without_smtp_mail_channel_is_skipped_and_webhook_still_works(env, monkeypatch):
    conn, admin, tpl = env
    monkeypatch.delenv("HAIR_SMTP_HOST", raising=False)
    integrations.set_notify_config(conn, admin, "https://hooks.example.com/x", ["pipeline_completed"], ["ops@example.com"])
    _completed_run(conn, admin, tpl["재무 분석 보고 파이프라인"])
    with patch.object(integrations, "send_webhook", return_value=(True, None)) as hook, \
            patch.object(integrations, "send_email", side_effect=AssertionError("SMTP 없으면 메일 안 보냄")):
        assert integrations.run_notifications(conn) == 1
    assert hook.call_count == 1


def test_send_email_uses_starttls_and_login(monkeypatch):
    monkeypatch.setenv("HAIR_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("HAIR_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("HAIR_SMTP_PASSWORD", "app-password")
    calls = []

    class _SMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, secret):
            calls.append(("login", user))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr("smtplib.SMTP", _SMTP)
    ok, err = integrations.send_email(["a@example.com", "b@example.com"], "[HAIR] 제목", "본문")
    assert ok and err is None
    assert calls == [("connect", "smtp.example.com", 587), ("starttls",), ("login", "bot@example.com"),
                     ("send", "a@example.com, b@example.com", "[HAIR] 제목")]
