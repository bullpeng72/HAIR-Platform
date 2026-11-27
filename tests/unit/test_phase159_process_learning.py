"""배운 점·변경 기록(2026-11-27, AI Native 분석 2단계 — 2번 학습 루프·10번 변경 효과 비교/되돌리기).

- 결재함 반려 사유 → 그 단계의 개선 제안 후보(같은 글은 횟수만 증가, 짧은 말은 무시)
- 되풀이되는 품질 기준 미달 → 그 기준에 맞춘 제안
- 적용한 배운 점은 두 실행 엔진 모두에서 다음 실행의 단계 지시문에 들어간다
- 단계 설정 변경은 실제로 바뀐 값만 기록되고, 전·후 지표 비교와 되돌리기가 된다"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from hair_platform import approvals, execution_graph, process_learning
from hair_platform import pipeline_runtime as pr
from langgraph.checkpoint.sqlite import SqliteSaver


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _t(minutes_ago: float) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _task(conn):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, '분기 보고서')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '작성')", (bid,)).lastrowid


@pytest.fixture
def tpl(client_and_conn):
    client, conn = client_and_conn
    admin = _uid(conn, "admin1")
    conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('작성자', '[]', 'active')")
    tid = pr.create_pipeline_template(conn, admin, "보고서 작성")
    sid = pr.add_stage(conn, admin, tid, "초안", "single", [("작성자", "")], max_retry=1,
                       requires_human_approval=True, min_gate_pass_ratio=0.5)
    conn.commit()
    return client, conn, admin, tid, sid


def _paused_run(conn, tid, sid, requester):
    run_id = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
        "VALUES (?, ?, ?, 'paused_for_approval', 1)", (tid, _task(conn), requester)).lastrowid
    rs = conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status) VALUES (?, ?, 'passed')",
                      (run_id, sid)).lastrowid
    approvals.create_approval_chain(conn, requester, "pipeline_run_stage", rs)
    conn.commit()
    return run_id, rs


def _reject(client, conn, rs, reason):
    aid = conn.execute("SELECT id FROM approvals WHERE subject_type = 'pipeline_run_stage' AND subject_id = ? "
                       "AND status = 'pending'", (rs,)).fetchone()[0]
    r = client.post(f"/api/approvals/{aid}/decide", json={"approve": False, "reason": reason})
    assert r.status_code == 200, r.text


def test_rejection_reason_becomes_candidate_and_repeats_count(tpl):
    client, conn, _admin, tid, sid = tpl
    member = _uid(conn, "member1")
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    for reason in ("표의 합계 숫자를 원자료와 맞춰 주세요", "표의 합계 숫자를 원자료와 맞춰 주세요.", "다시"):
        _run, rs = _paused_run(conn, tid, sid, member)
        _reject(client, conn, rs, reason)
    data = client.get(f"/api/process-metrics/templates/{tid}/lessons").json()
    cands = [c for c in data["candidates"] if c["source"] == "human_rejection"]
    assert len(cands) == 1 and cands[0]["hits"] == 2 and "합계" in cands[0]["text"]  # "다시"는 지시가 못 돼 버림
    assert data["active"] == []
    # 제안 수가 프로세스 성과 표에 나온다
    row = next(t for t in client.get("/api/process-metrics").json()["templates"] if t["template_id"] == tid)
    assert row["lesson_candidates"] == 1


def test_repeated_gate_failures_become_a_proposal(tpl):
    _client, conn, admin, tid, sid = tpl
    run_id = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) "
                          "VALUES (?, ?, ?, 'failed')", (tid, _task(conn), admin)).lastrowid
    bad = json.dumps({"C": {"score": 0.2, "passed": False}, "A": {"score": 0.9, "passed": True}})
    for _ in range(3):
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, gate_scores) "
                     "VALUES (?, ?, 'failed_retrying', ?)", (run_id, sid, bad))
    conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, gate_scores) VALUES (?, ?, 'passed', '{}')",
                 (run_id, sid))
    conn.commit()
    cands = process_learning.list_lessons(conn, tid)["candidates"]
    gate = [c for c in cands if c["source"] == "gate_pattern"]
    assert len(gate) == 1 and gate[0]["evidence"]["gate"] == "C" and gate[0]["hits"] == 3
    assert "신뢰성" in gate[0]["text"]
    # 무시하면 다시 만들지 않는다
    process_learning.set_lesson_status(conn, admin, gate[0]["id"], "dismissed")
    assert [c for c in process_learning.list_lessons(conn, tid)["candidates"] if c["source"] == "gate_pattern"] == []


def test_active_lesson_reaches_both_engines(tpl):
    _client, conn, admin, tid, sid = tpl
    lesson = process_learning.add_manual_lesson(conn, admin, sid, "금액은 천 원 단위로 반올림해 표기하세요")
    assert lesson["status"] == "active"
    seen = []

    def _capture(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        seen.append(task_descriptions[0])
        return {"final_output": "x", "agents": [{"agent_id": a, "role_name": "r", "output": "o"} for a in agent_ids],
                "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_capture):
        run1 = pr.start_pipeline_run(conn, admin, tid, _task(conn))
        pr.run_pipeline_stage_job(conn, run1, admin)
        run2 = pr.start_pipeline_run(conn, admin, tid, _task(conn))
        with SqliteSaver.from_conn_string(":memory:") as saver:
            execution_graph.run_pipeline_graph(conn, run2, admin, saver)
    assert len(seen) == 2
    assert all("이전 실행에서 배운 점" in s and "천 원 단위" in s for s in seen)
    # 끄면 더 들어가지 않는다
    process_learning.set_lesson_status(conn, admin, lesson["id"], "retired")
    assert process_learning.lessons_prompt(conn, sid) == ""


def test_activation_edits_text_and_limits(tpl):
    client, conn, admin, tid, sid = tpl
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    _run, rs = _paused_run(conn, tid, sid, _uid(conn, "member1"))
    process_learning.capture_rejection(conn, rs, "근거 자료 출처를 각 문단 끝에 적어 주세요")
    conn.commit()
    cid = process_learning.list_lessons(conn, tid)["candidates"][0]["id"]
    r = client.post(f"/api/process-lessons/{cid}/activate", json={"text": "문단마다 근거 자료 출처를 적으세요"})
    assert r.status_code == 200 and r.json()["text"].startswith("문단마다")
    assert client.post(f"/api/process-lessons/{cid}/activate", json={}).status_code == 409
    assert client.post(f"/api/process-metrics/stages/{sid}/lessons", json={"text": "짧"}).status_code == 400
    for i in range(process_learning.MAX_ACTIVE_PER_STAGE - 1):
        assert client.post(f"/api/process-metrics/stages/{sid}/lessons", json={"text": f"규칙 번호 {i} 를 지키세요"}).status_code == 201
    assert client.post(f"/api/process-metrics/stages/{sid}/lessons", json={"text": "하나 더 넣어 보는 규칙"}).status_code == 400
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get(f"/api/process-metrics/templates/{tid}/lessons").status_code == 403


def test_stage_update_records_only_real_changes_and_reverts(tpl):
    client, conn, admin, tid, sid = tpl
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    url = f"/api/pipeline-templates/{tid}/stages/{sid}"
    assert client.patch(url, json={"max_retry": 1}).status_code == 200  # 같은 값 → 기록 안 함
    assert client.patch(url, json={"max_retry": 3, "instructions": "표를 먼저 쓰세요", "gate_ids": ["A", "C"]}).status_code == 200
    changes = client.get(f"/api/process-metrics/templates/{tid}/changes").json()
    upd = [c for c in changes if c["kind"] == "stage_update"]
    assert len(upd) == 1
    assert upd[0]["before_value"] == {"max_retry": 1, "instructions": "", "gate_ids": ["A", "B", "C", "D", "E", "F", "G"]}
    assert upd[0]["after_value"]["gate_ids"] == ["A", "C"] and upd[0]["revertible"] is True
    assert upd[0]["verdict"]["label"] == "표본 부족"
    r = client.post(f"/api/process-changes/{upd[0]['id']}/revert")
    assert r.status_code == 200
    row = conn.execute("SELECT max_retry, instructions, gate_ids FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    assert (row["max_retry"], row["instructions"], json.loads(row["gate_ids"])) == (1, "", ["A", "B", "C", "D", "E", "F", "G"])
    after = client.get(f"/api/process-metrics/templates/{tid}/changes").json()
    assert after[0]["kind"] == "revert" and after[0]["summary"].startswith("되돌리기")
    assert client.post(f"/api/process-changes/{upd[0]['id']}/revert").status_code == 409  # 이미 되돌림


def test_revert_refuses_when_value_changed_again(tpl):
    client, conn, _admin, tid, sid = tpl
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    url = f"/api/pipeline-templates/{tid}/stages/{sid}"
    client.patch(url, json={"max_retry": 3})
    first = client.get(f"/api/process-metrics/templates/{tid}/changes").json()[0]["id"]
    client.patch(url, json={"max_retry": 4})
    assert client.post(f"/api/process-changes/{first}/revert").status_code == 409


def test_effect_comparison_before_and_after(tpl):
    _client, conn, admin, tid, sid = tpl
    # 변경 전: 4건 모두 다시 작성 / 변경 후: 4건 모두 한 번에
    for i in range(4):
        r = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, created_at, finished_at) "
                         "VALUES (?, ?, ?, 'completed', ?, ?)", (tid, _task(conn), admin, _t(600 + i), _t(590 + i))).lastrowid
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, created_at) VALUES (?, ?, 'failed_retrying', ?)", (r, sid, _t(598)))
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, created_at) VALUES (?, ?, 2, 'passed', ?)", (r, sid, _t(595)))
    cid = process_learning.record_change(conn, admin, tid, sid, "lesson_on", "배운 점 적용", None, {"text": "x"})
    conn.execute("UPDATE process_changes SET created_at = ? WHERE id = ?", (_t(300), cid))
    for i in range(4):
        r = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, created_at, finished_at) "
                         "VALUES (?, ?, ?, 'completed', ?, ?)", (tid, _task(conn), admin, _t(200 + i), _t(190 + i))).lastrowid
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, created_at) VALUES (?, ?, 'passed', ?)", (r, sid, _t(195)))
    conn.commit()
    ch = next(c for c in process_learning.list_changes(conn, tid) if c["id"] == cid)
    assert ch["before"]["first_pass_rate"] == 0.0 and ch["after"]["first_pass_rate"] == 1.0
    assert ch["before"]["stage_rework_rate"] == 0.5 and ch["after"]["stage_rework_rate"] == 0.0
    assert ch["verdict"]["label"] == "좋아짐"


def test_deleting_stage_and_template_cleans_learning_rows(tpl):
    _client, conn, admin, tid, sid = tpl
    process_learning.add_manual_lesson(conn, admin, sid, "결론을 맨 앞에 쓰세요")
    pr.delete_stage(conn, admin, sid)  # 실행 이력이 없는 단계
    assert conn.execute("SELECT COUNT(*) FROM stage_lessons WHERE stage_id = ?", (sid,)).fetchone()[0] == 0
    pr.delete_pipeline_template(conn, admin, tid)
    assert conn.execute("SELECT COUNT(*) FROM process_changes WHERE template_id = ?", (tid,)).fetchone()[0] == 0
