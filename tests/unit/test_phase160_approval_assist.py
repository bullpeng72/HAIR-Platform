"""결재 도우미·위험 기반 결재(2026-11-27, AI Native 분석 3단계 — 9번·4번).

- 결재 한 건 요약: 점검 결과·이전 시도와의 차이·확인할 점·과거 반려 사유·위험도/추천
- 사람 결정과 추천을 함께 남겨 위험도별 승인율을 본다
- 검증 조건(낮음 추천 사람 결정 20건·95% 승인)을 채운 파이프라인만 낮은 위험 자동 승인을 켤 수 있고,
  켜면 두 실행 엔진 모두 사람 승인 단계를 자동 통과하되 5건마다 1건은 표본으로 사람에게 간다"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hair_platform import approval_assist, approvals, execution_graph
from hair_platform import pipeline_runtime as pr
from langgraph.checkpoint.sqlite import SqliteSaver


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _task(conn, judgment=False):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, '계약 검토')").lastrowid
    tid = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '검토')", (bid,)).lastrowid
    if judgment:
        conn.execute("UPDATE tasks SET requires_human_judgment = 1 WHERE id = ?", (tid,))
    return tid


@pytest.fixture
def tpl(client_and_conn):
    client, conn = client_and_conn
    admin = _uid(conn, "admin1")
    conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('검토자', '[]', 'active')")
    tid = pr.create_pipeline_template(conn, admin, "계약서 검토")
    sid = pr.add_stage(conn, admin, tid, "검토 의견", "single", [("검토자", "")], max_retry=2,
                       requires_human_approval=True, min_gate_pass_ratio=0.5)
    conn.commit()
    return client, conn, admin, tid, sid


def _stage_row(conn, tid, sid, requester, status, output, gate_scores, attempt=1, run_id=None, reason=None):
    if run_id is None:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
            "VALUES (?, ?, ?, 'paused_for_approval', 1)", (tid, _task(conn), requester)).lastrowid
    rs = conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output, gate_scores, decision_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (run_id, sid, attempt, status, output, json.dumps(gate_scores), reason)).lastrowid
    conn.commit()
    return run_id, rs


def test_brief_summarizes_checks_changes_and_risk(tpl):
    client, conn, _admin, tid, sid = tpl
    conn.execute("UPDATE pipeline_templates SET domain = 'legal' WHERE id = ?", (tid,))
    member = _uid(conn, "member1")
    run_id, _r1 = _stage_row(conn, tid, sid, member, "rejected", "1조 위약금 10%\n2조 해지", {}, reason="위약금 근거 조항을 적어 주세요")
    _r, rs = _stage_row(conn, tid, sid, member, "escalated", "1조 위약금 10%(민법 398조)\n2조 해지\n3조 분쟁",
                        {"A": {"score": 0.9, "passed": True},
                         "numeric_grounding": {"passed": False, "not_measured": False, "summary": "10%의 근거가 입력에 없음"},
                         "review_verdict": {"passed": True, "conditional": True, "note": "3조 관할 법원 명시 필요"}},
                        attempt=2, run_id=run_id)
    approvals.create_approval_chain(conn, member, "pipeline_run_stage", rs)
    conn.commit()
    aid = conn.execute("SELECT id FROM approvals WHERE subject_id = ? AND subject_type = 'pipeline_run_stage'", (rs,)).fetchone()[0]
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    b = client.get(f"/api/approvals/{aid}/brief").json()
    assert b["supported"] and b["context"]["attempt"] == 2 and b["title"].startswith("계약서 검토")
    assert b["checks"][0]["label"] == "숫자 근거" and b["checks"][0]["status"] == "fail"
    assert b["checks_summary"]["fail"] == 1
    assert b["changes"]["added"] >= 2 and b["changes"]["previous_rejection_reason"] == "위약금 근거 조항을 적어 주세요"
    assert sum("수용" in a or "관할" in a for a in b["attention"]) == 1  # 검토자 의견은 한 번만
    assert any("3조 관할" in a for a in b["attention"]) and any("자동 재시도" in a for a in b["attention"])
    assert b["risk"]["level"] == "high" and b["risk"]["recommended"] == "review"
    assert any("법무" in f for f in b["risk"]["factors"])
    # 요청자가 아닌 구성원은 못 본다
    client.post("/login", json={"username": "viewer1", "password": "viewerpass"})
    assert client.get(f"/api/approvals/{aid}/brief").status_code == 403


def test_clean_result_is_low_risk_and_decisions_are_tracked(tpl):
    client, conn, _admin, tid, sid = tpl
    member = _uid(conn, "member1")
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    for approve in (True, False):
        _run, rs = _stage_row(conn, tid, sid, member, "passed", "검토 의견 본문", {"A": {"score": 0.9, "passed": True}})
        approvals.create_approval_chain(conn, member, "pipeline_run_stage", rs)
        conn.commit()
        aid = conn.execute("SELECT id FROM approvals WHERE subject_id = ? AND subject_type = 'pipeline_run_stage'", (rs,)).fetchone()[0]
        b = client.get(f"/api/approvals/{aid}/brief").json()
        assert b["risk"]["level"] == "low" and b["risk"]["recommended"] == "approve"
        r = client.post(f"/api/approvals/{aid}/decide", json={"approve": approve, "reason": None if approve else "결론이 약합니다 보강"})
        assert r.status_code == 200
    ag = client.get(f"/api/process-metrics/templates/{tid}/approval-policy").json()
    assert ag["by_level"]["low"] == {"decisions": 2, "approved": 1, "approval_rate": 0.5}
    assert ag["eligible"] is False
    assert client.put(f"/api/process-metrics/templates/{tid}/approval-policy", json={"auto_approve_low_risk": True}).status_code == 400
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.put(f"/api/process-metrics/templates/{tid}/approval-policy", json={"auto_approve_low_risk": False}).status_code == 403


def _make_eligible(conn, tid, sid):
    for i in range(approval_assist.AUTO_MIN_DECISIONS):
        conn.execute("INSERT INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, "
                     "recommended, decided) VALUES (?, ?, ?, 'low', 0, 'approve', 'approved')", (900000 + i, tid, sid))
    conn.commit()


def _ok_crew(conn, agent_ids, task_descriptions, actor_id, **kwargs):
    return {"final_output": "검토 결과", "agents": [{"agent_id": a, "role_name": "r", "output": "o"} for a in agent_ids],
            "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}


def test_auto_approval_runs_through_with_sampling(tpl):
    client, conn, admin, tid, sid = tpl
    _make_eligible(conn, tid, sid)
    approval_assist.set_policy(conn, admin, tid, True)
    statuses = []
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_ok_crew):
        for _ in range(approval_assist.SAMPLE_EVERY):
            run = pr.start_pipeline_run(conn, admin, tid, _task(conn))
            pr.run_pipeline_stage_job(conn, run, admin)
            statuses.append(conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run,)).fetchone()[0])
    assert statuses == ["completed"] * (approval_assist.SAMPLE_EVERY - 1) + ["paused_for_approval"]  # 5번째는 표본
    autos = conn.execute("SELECT COUNT(*) FROM approval_recommendations WHERE auto = 1").fetchone()[0]
    assert autos == approval_assist.SAMPLE_EVERY - 1
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'pipeline_stage_auto_approved'").fetchone()[0] == autos
    # 자동 승인한 결과물은 승인 상태이고 결재 요청은 없다
    d = conn.execute("SELECT status FROM deliverables ORDER BY id LIMIT 1").fetchone()[0]
    assert d == "approved"
    sampled = conn.execute("SELECT sampled, decided FROM approval_recommendations WHERE sampled = 1").fetchone()
    assert tuple(sampled) == (1, None)


def test_auto_approval_respects_exclusions_and_graph_engine(tpl):
    _client, conn, admin, tid, sid = tpl
    _make_eligible(conn, tid, sid)
    approval_assist.set_policy(conn, admin, tid, True)
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_ok_crew):
        # 사람 판단 필수 업무는 항상 사람
        run = pr.start_pipeline_run(conn, admin, tid, _task(conn, judgment=True))
        pr.run_pipeline_stage_job(conn, run, admin)
        assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run,)).fetchone()[0] == "paused_for_approval"
        # 그래프 엔진도 같은 판정
        run2 = pr.start_pipeline_run(conn, admin, tid, _task(conn))
        with SqliteSaver.from_conn_string(":memory:") as saver:
            execution_graph.run_pipeline_graph(conn, run2, admin, saver)
        assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run2,)).fetchone()[0] == "completed"
    # 검증 조건이 깨지면(반려가 늘면) 켜 둬도 멈춘다
    for i in range(3):
        conn.execute("INSERT INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, "
                     "recommended, decided) VALUES (?, ?, ?, 'low', 0, 'approve', 'rejected')", (950000 + i, tid, sid))
    conn.commit()
    ag = approval_assist.agreement(conn, tid)
    assert ag["enabled"] and not ag["eligible"] and not ag["active"]
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_ok_crew):
        run3 = pr.start_pipeline_run(conn, admin, tid, _task(conn))
        pr.run_pipeline_stage_job(conn, run3, admin)
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run3,)).fetchone()[0] == "paused_for_approval"


def test_policy_change_is_recorded_and_revertible(tpl):
    client, conn, admin, tid, sid = tpl
    _make_eligible(conn, tid, sid)
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    r = client.put(f"/api/process-metrics/templates/{tid}/approval-policy", json={"auto_approve_low_risk": True})
    assert r.status_code == 200 and r.json()["active"] is True
    ch = [c for c in client.get(f"/api/process-metrics/templates/{tid}/changes").json() if c["kind"] == "approval_policy"]
    assert len(ch) == 1 and ch[0]["revertible"]
    assert client.post(f"/api/process-changes/{ch[0]['id']}/revert").status_code == 200
    assert approval_assist.agreement(conn, tid)["enabled"] is False


def test_deliverable_brief_and_unsupported_subject(client_and_conn):
    client, conn = client_and_conn
    member = _uid(conn, "member1")
    task = _task(conn)
    d1 = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, 'human', ?, 'a\nb', 'rejected')",
                      (task, member)).lastrowid
    d2 = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status, revises_id) "
                      "VALUES (?, 'human', ?, 'a\nb\nc', 'pending_review', ?)", (task, member, d1)).lastrowid
    approvals.create_approval_chain(conn, member, "deliverable", d2)
    bid = conn.execute("SELECT brief_id FROM tasks WHERE id = ?", (task,)).fetchone()[0]
    a2 = conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id) VALUES (?, 'brief', ?)", (member, bid)).lastrowid
    conn.commit()
    aid = conn.execute("SELECT id FROM approvals WHERE subject_type = 'deliverable'").fetchone()[0]
    client.post("/login", json={"username": "member1", "password": "memberpass"})  # 요청자 본인
    b = client.get(f"/api/approvals/{aid}/brief").json()
    assert b["kind"] == "deliverable" and b["changes"]["added"] == 1
    assert any("자동 품질 점검 기록이 없는" in a for a in b["attention"])
    assert client.get(f"/api/approvals/{a2}/brief").json()["supported"] is False
