"""Phase 75 — 파이프라인 실행 취소 요청(코파일럿 — 실행 중 조향의
최소 첫 단계)
(사용자 요청, 2026-11-17, "Human Agency Scale 등 개념과 비교해 HAIR-
Platform 개선이 필요한 Insight 정리" → "구현 시작").

`pipeline_runs.status` CHECK 제약은 이미 'cancelled' 값을 허용해뒀지만
실제로 그 상태로 전이시키는 코드가 전혀 없었다 — 사람이 실행 중인
파이프라인에 개입할 방법이 전혀 없었던 갭. `cancel_requested` 플래그는
"지금 당장"이 아니라 "다음 안전한 지점(다음 시도/단계 시작 직전,
비용이 드는 Crew 호출 전)에서 멈춰라"는 요청이다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import db
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user
from tests.unit.test_phase48_pipeline_runtime import _good_run_crew


def _make_task(conn) -> int:
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '테스트', '[]')",
        (brief_id,),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _setup_two_stage_template(conn, admin_id):
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn, admin_id, tid, "1단계", "single", [("코드 검증가", "")],
        max_retry=2, min_gate_pass_ratio=0.5, requires_human_approval=False,
    )
    pr.add_stage(
        conn, admin_id, tid, "2단계", "single", [("코드 검증가", "")],
        max_retry=2, min_gate_pass_ratio=0.5, requires_human_approval=False,
    )
    return tid


def test_cancel_requested_stops_run_before_next_stage_starts():
    """핵심 테스트 — 1단계가 통과해 2단계로 넘어가기 직전에
    `cancel_requested`가 세팅돼 있으면, 2단계는 아예 시작되지(= Crew가
    호출되지) 않고 실행이 'cancelled'로 끝난다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_two_stage_template(conn, admin_id)

    calls: list[int] = []

    def _tracking_run_crew(*args, **kwargs):
        calls.append(1)
        # 1단계가 끝난 직후부터는 취소가 요청된 것처럼 만든다.
        if len(calls) == 1:
            conn.execute(
                "UPDATE pipeline_runs SET cancel_requested = 1 "
                "WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        return _good_run_crew()(*args, **kwargs)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_tracking_run_crew):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 1  # 2단계의 Crew 호출은 아예 일어나지 않았다
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "cancelled"
    assert run["finished_at"] is not None

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'pipeline_run_cancelled' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit_row is not None


def test_request_cancel_on_running_run_only_sets_flag_does_not_stop_immediately():
    """`status='running'`인 실행에 취소를 요청하면, 그 순간 바로
    'cancelled'가 되는 게 아니라 플래그만 세워진다(다음 안전한 지점에서
    멈추도록) — 이미 시작된 단계 실행을 강제로 끊지 않는다는 설계."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_two_stage_template(conn, admin_id)
    run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)

    result = pr.request_pipeline_run_cancel(conn, admin_id, run_id)
    assert result["status"] == "running"
    assert result["cancel_requested"] == 1


def test_request_cancel_on_paused_for_approval_cancels_immediately():
    """결재 대기 중(= 아무 배경 작업도 돌고 있지 않음)인 실행은 즉시
    'cancelled'로 전이한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn, admin_id, tid, "1단계", "single", [("코드 검증가", "")],
        max_retry=2, min_gate_pass_ratio=0.5, requires_human_approval=True,
    )

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"

    result = pr.request_pipeline_run_cancel(conn, admin_id, run_id)
    assert result["status"] == "cancelled"
    assert result["finished_at"] is not None


def test_request_cancel_on_already_finished_run_rejected():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_two_stage_template(conn, admin_id)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "completed"  # 2단계 모두 승인 불필요라 끝까지 자동 완료

    try:
        pr.request_pipeline_run_cancel(conn, admin_id, run_id)
        assert False, "should have raised"
    except ValueError as exc:
        assert str(exc) == "pipeline_run_already_finished"


def test_cancel_endpoint_requires_participant_or_supervisor(client_and_conn):
    client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    task_id = _make_task(conn)
    tid = _setup_two_stage_template(conn, admin_id)
    run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)

    client.post("/login", json={"username": "member1", "password": "memberpass"})
    resp = client.post(f"/api/pipeline-runs/{run_id}/cancel")
    assert resp.status_code == 403

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    resp = client.post(f"/api/pipeline-runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] == 1


def test_cancel_endpoint_404_for_unknown_run(client_and_conn):
    client, _conn = client_and_conn
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    resp = client.post("/api/pipeline-runs/999999/cancel")
    assert resp.status_code == 404


def test_list_pipeline_runs_for_task_exposes_cancel_requested():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_two_stage_template(conn, admin_id)
    run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)

    runs = pr.list_pipeline_runs_for_task(conn, task_id)
    assert runs[0]["cancel_requested"] is False

    pr.request_pipeline_run_cancel(conn, admin_id, run_id)
    runs = pr.list_pipeline_runs_for_task(conn, task_id)
    assert runs[0]["cancel_requested"] is True
