"""파이프라인 예상 남은 시간(2026-11-25, P0-3) — 같은 템플릿·단계의 최근 실행 평균으로 추정."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import db
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _setup():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "admin_eta", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "eta", "", "general")
    s1 = pr.add_stage(conn, admin, tid, "조사", "single", [("외부 자료 조사가", "")])
    s2 = pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    assert task is not None
    return conn, admin, tid, (s1, s2), task


def _fake_crew(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
    return {"final_output": "결과", "agents": [{"agent_id": agent_ids[0], "role_name": "x", "name": None, "output": "결과"}],
            "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}


def test_stage_records_get_start_time_and_eta_uses_history():
    conn, admin, tid, (s1, s2), task = _setup()
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_fake_crew):
        done = pr.start_pipeline_run(conn, admin, tid, task)
        pr.run_pipeline_stage_job(conn, done, admin)
    rows = conn.execute("SELECT started_at FROM pipeline_run_stages WHERE pipeline_run_id = ?", (done,)).fetchall()
    assert rows and all(r[0] for r in rows)  # 두 엔진 공통 경로가 시작 시각을 남긴다
    # 과거 소요 시간을 알려진 값으로 고정: 조사 10분, 작성 20분
    conn.execute("UPDATE pipeline_run_stages SET started_at = datetime(created_at, '-600 seconds') WHERE stage_id = ?", (s1,))
    conn.execute("UPDATE pipeline_run_stages SET started_at = datetime(created_at, '-1200 seconds') WHERE stage_id = ?", (s2,))
    # 지금 실행 중인 새 실행 — 1단계를 2분째 진행 중
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET status = 'running', current_stage_seq = 1, "
                 "stage_started_at = datetime('now', '-120 seconds') WHERE id = ?", (run_id,))
    conn.commit()
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    eta = pr.estimate_remaining(conn, run)
    assert eta is not None and eta["known_stages"] == 2 and eta["unknown_stages"] == 0
    assert abs(eta["remaining_seconds"] - (600 - 120 + 1200)) <= 5
    assert pr._run_progress_fields(conn, run)["eta"]["remaining_seconds"] == eta["remaining_seconds"]


def test_no_history_means_no_estimate_and_finished_run_has_none():
    conn, admin, tid, _stages, task = _setup()
    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    conn.execute("UPDATE pipeline_runs SET status = 'running', current_stage_seq = 1, stage_started_at = datetime('now') WHERE id = ?", (run_id,))
    conn.commit()
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert pr.estimate_remaining(conn, run) is None
    conn.execute("UPDATE pipeline_runs SET status = 'completed' WHERE id = ?", (run_id,))
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert pr.estimate_remaining(conn, run) is None
