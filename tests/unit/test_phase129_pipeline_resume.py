"""서버 재시작으로 중단된 파이프라인 실행 이어서 실행(2026-11-25, P1-6)."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import pipeline_runtime as pr


def _setup(conn):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    tid = pr.create_pipeline_template(conn, admin, "resume", "", "general")
    pr.add_stage(conn, admin, tid, "조사", "single", [("외부 자료 조사가", "")])
    pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    return admin, tid, task


def _fake_crew(calls):
    def fake(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        calls.append(agent_ids[0])
        return {"final_output": "결과", "agents": [{"agent_id": agent_ids[0], "role_name": "x", "name": None, "output": "결과"}],
                "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}
    return fake


def test_restart_marks_interrupted_and_resume_continues_from_current_stage(client_and_conn):
    client, conn = client_and_conn
    admin, tid, task = _setup(conn)
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    # 1단계는 끝났고 2단계 진행 중에 서버가 죽었다고 가정
    calls: list[int] = []
    conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
                 "SELECT ?, id, 1, 'passed', '조사 결과' FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = 1",
                 (run_id, tid))
    conn.execute("UPDATE pipeline_runs SET current_stage_seq = 2, status = 'running' WHERE id = ?", (run_id,))
    conn.commit()
    assert pr.reap_orphaned_pipeline_runs(conn) == 1  # 재시작
    row = conn.execute("SELECT status, interrupted_at FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed" and row["interrupted_at"]
    listed = pr.list_pipeline_runs_for_task(conn, task)[0]
    assert listed["interrupted_at"]

    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_fake_crew(calls)):
        r = client.post(f"/api/pipeline-runs/{run_id}/resume")
    assert r.status_code == 200 and r.json()["engine"] == "legacy"
    final = conn.execute("SELECT status, interrupted_at FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert final["status"] == "completed" and final["interrupted_at"] is None
    writer = conn.execute("SELECT id FROM agent_pool WHERE role_name = '기획서 작성자'").fetchone()[0]
    assert calls == [writer]  # 끝난 1단계(조사)는 다시 하지 않았다
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'pipeline_run_resumed'").fetchone()[0] == 1


def test_resume_rejects_runs_not_interrupted_and_requires_manager(client_and_conn):
    client, conn = client_and_conn
    admin, tid, task = _setup(conn)
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET status = 'failed', finished_at = datetime('now') WHERE id = ?", (run_id,))
    conn.commit()
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    assert client.post(f"/api/pipeline-runs/{run_id}/resume").status_code == 409  # 일반 실패는 이어서 실행 대상 아님
    assert client.post("/api/pipeline-runs/999999/resume").status_code == 404
    conn.execute("UPDATE pipeline_runs SET interrupted_at = datetime('now') WHERE id = ?", (run_id,))
    conn.commit()
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.post(f"/api/pipeline-runs/{run_id}/resume").status_code == 403


def test_graph_engine_run_resumes_from_checkpoint_path(client_and_conn):
    """그래프 엔진 실행은 마지막 체크포인트부터 이어 가는 경로로 보낸다."""
    client, conn = client_and_conn
    admin, tid, task = _setup(conn)
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET engine = 'graph', status = 'failed', interrupted_at = datetime('now') "
                 "WHERE id = ?", (run_id,))
    conn.commit()
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    with patch("hair_platform.execution_graph.resume_interrupted_graph_job") as resumed:
        r = client.post(f"/api/pipeline-runs/{run_id}/resume")
    assert r.status_code == 200 and r.json()["engine"] == "graph"
    assert resumed.call_args.args[1] == run_id
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "running"


def test_inbox_lists_failed_and_interrupted_pipeline_runs(client_and_conn):
    """인박스에 파이프라인 실패·재시작 중단이 보인다(2026-11-25, P2-15) — 그 업무의 가장 최근 실행만."""
    from hair_platform import inbox

    _client, conn = client_and_conn
    admin, tid, task = _setup(conn)
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET status = 'failed', interrupted_at = datetime('now'), finished_at = datetime('now') "
                 "WHERE id = ?", (run_id,))
    conn.commit()
    with patch("hair_platform.deliverables.list_my_work",
               return_value=[{"task_id": task, "role_name": "x", "has_final_result": False, "latest_deliverable_at": None}]):
        items = [i for i in inbox.get_inbox(conn, admin, "admin") if i["type"] == "pipeline_failed"]
    assert len(items) == 1 and items[0]["run_id"] == run_id and items[0]["interrupted"] is True
    assert items[0]["template_name"] == "resume"
    # 다시 실행해 성공한 최신 실행이 있으면 사라진다
    newer = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET status = 'completed' WHERE id = ?", (newer,))
    conn.commit()
    with patch("hair_platform.deliverables.list_my_work",
               return_value=[{"task_id": task, "role_name": "x", "has_final_result": False, "latest_deliverable_at": None}]):
        assert not [i for i in inbox.get_inbox(conn, admin, "admin") if i["type"] == "pipeline_failed"]
