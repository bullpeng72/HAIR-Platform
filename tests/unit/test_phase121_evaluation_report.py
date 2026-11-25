"""Agent-Evaluator HTML 리포트(2026-11-25, `evaluation_reports.py`) — 평가마다 SDK 리포트를 만들어 결과물에 붙인다."""

from __future__ import annotations

from hair_platform import agent_evaluation, evaluation_reports, pipeline_runtime


def _deliverable(conn):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    return conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
        "VALUES (?, 'agent', 1, '보고서', 'markdown', 'pending_review')", (task,)).lastrowid


def test_report_is_generated_stored_and_served_sandboxed(client_and_conn):
    client, conn = client_and_conn
    scored = agent_evaluation.run_and_score("재무 분석가", lambda: "3분기 매출은 55억 원으로 목표 대비 105.8%이므로 달성했다.")
    entry = scored["gate_scores"][evaluation_reports.REPORT_KEY]
    assert entry["not_measured"] and entry["advisory"] and entry["token"]
    # 토큰 항목은 통과 판정·실패 판단에 영향이 없다
    assert agent_evaluation.has_failing_gate({evaluation_reports.REPORT_KEY: entry}) is False
    assert pipeline_runtime._gate_pass_ratio({evaluation_reports.REPORT_KEY: entry}) == 1.0

    did = _deliverable(conn)
    agent_evaluation.record_evaluation(conn, did, scored["gate_scores"], scored["tcr"])
    conn.commit()
    latest = agent_evaluation.latest_evaluation(conn, did)
    assert latest["has_report"] is True
    assert evaluation_reports.REPORT_KEY not in latest["gate_scores"]  # 저장된 Gate 점수에는 토큰이 없다
    assert evaluation_reports.REPORT_KEY in scored["gate_scores"]  # 호출자 dict는 건드리지 않는다

    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.get(f"/api/deliverables/{did}/evaluation-report")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "sandbox" in r.headers["content-security-policy"]
    assert "Gate" in r.text and "<html" in r.text.lower()


def test_no_report_when_disabled_and_404_without_report(client_and_conn, monkeypatch):
    client, conn = client_and_conn
    monkeypatch.setenv("HAIR_EVAL_HTML_REPORT", "0")
    scored = agent_evaluation.run_and_score("재무 분석가", lambda: "보고서 본문")
    assert evaluation_reports.REPORT_KEY not in scored["gate_scores"]
    did = _deliverable(conn)
    agent_evaluation.record_evaluation(conn, did, scored["gate_scores"], scored["tcr"])
    conn.commit()
    assert agent_evaluation.latest_evaluation(conn, did)["has_report"] is False
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    assert client.get(f"/api/deliverables/{did}/evaluation-report").status_code == 404


def test_member_without_participation_cannot_open_report(client_and_conn):
    client, conn = client_and_conn
    scored = agent_evaluation.run_and_score("재무 분석가", lambda: "보고서 본문")
    did = _deliverable(conn)
    agent_evaluation.record_evaluation(conn, did, scored["gate_scores"], scored["tcr"])
    conn.commit()
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.get(f"/api/deliverables/{did}/evaluation-report").status_code == 403


def test_reset_wipes_reports_with_evaluations(client_and_conn):
    from hair_platform import data_admin

    _client, conn = client_and_conn
    scored = agent_evaluation.run_and_score("재무 분석가", lambda: "보고서 본문")
    agent_evaluation.record_evaluation(conn, _deliverable(conn), scored["gate_scores"], scored["tcr"])
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM evaluation_reports").fetchone()[0] == 1
    data_admin.reset_to_factory_state(conn)
    assert conn.execute("SELECT COUNT(*) FROM evaluation_reports").fetchone()[0] == 0


def test_conversation_default_ai_reply_gets_report_and_clean_gate_scores(client_and_conn):
    """2026-11-25(사용자 확인 요청 "대화에서도 리포트가 생성되는지") — 대화 응답은 `agent_evaluations`를 거치지 않고
    메시지에 Gate 점수를 바로 저장해, 예전 구현은 리포트를 버리고 토큰만 메시지 점수에 남겼다."""
    from unittest.mock import patch

    from hair_platform import conversations

    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    conv = conversations.create_conversation(conn, admin, "리포트 확인")["id"]
    conversations.create_message(conn, conv, admin, "3분기 실적을 요약해줘")
    job = conversations.start_default_ai_job(conn, conv, admin)
    with patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")), \
            patch("hair_platform.llm.call_llm", return_value="3분기 매출은 55억 원으로 목표 대비 105.8%이므로 달성했다."):
        conversations.run_default_ai_job(conn, job, conv, admin)
    msg = [m for m in conversations.list_messages(conn, conv) if m["author_type"] == "agent"][-1]
    assert msg["has_report"] is True
    assert evaluation_reports.REPORT_KEY not in (msg["gate_scores"] or {})  # 토큰은 메시지 점수에 안 남는다
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.get(f"/api/conversations/{conv}/messages/{msg['id']}/evaluation-report")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"] and "Gate" in r.text
    # 대화 참가자가 아니면 못 연다
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.get(f"/api/conversations/{conv}/messages/{msg['id']}/evaluation-report").status_code == 403
