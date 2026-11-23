"""Phase 74 — Task 레벨 "사람 판단 필수" 플래그
(사용자 요청, 2026-11-17, "Human Agency Scale 등 개념과 비교해 HAIR-
Platform 개선이 필요한 Insight 정리" → "구현 시작").

지금까지 사람 에스컬레이션은 전부 Gate 통과율 기반이었다 — 업무
'종류'(협상·민감한 커뮤니케이션 등) 자체로 항상 사람이 봐야 하는
Task를 지정할 방법이 없었다. `tasks.requires_human_judgment`를 켜면
그 Task가 타는 모든 파이프라인 단계가, 그 단계 템플릿 자체의
`requires_human_approval` 설정과 무관하게 Gate 통과를 자동 승인으로
치지 않고 항상 결재를 거친다(`pipeline_runtime._execute_stage_
attempt()`)."""

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


def _setup_passing_stage(conn, admin_id, task_id, requires_human_approval=False):
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "single",
        [("코드 검증가", "")],
        max_retry=2,
        min_gate_pass_ratio=0.5,
        requires_human_approval=requires_human_approval,
    )
    return tid


def test_requires_human_judgment_default_false_behaves_exactly_like_before():
    """회귀 가드 — `requires_human_judgment`이 기본값(False)이고 단계
    템플릿도 승인 불필요면, Gate 통과 즉시 'approved'로 자동 전진한다
    (기존 "최소 HITL" 동작 100% 동일)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_passing_stage(conn, admin_id, task_id, requires_human_approval=False)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] != "paused_for_approval"
    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    deliverable = conn.execute(
        "SELECT status FROM deliverables WHERE id=?", (stage_result["deliverable_id"],)
    ).fetchone()
    assert deliverable["status"] == "approved"


def test_requires_human_judgment_forces_approval_even_when_stage_does_not():
    """핵심 테스트 — 단계 템플릿은 승인 불필요(`requires_human_approval=
    False`)인데 Task에 `requires_human_judgment=True`를 켜면, Gate가
    통과해도 자동 승인 대신 항상 결재를 거친다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = _setup_passing_stage(conn, admin_id, task_id, requires_human_approval=False)
    conn.execute(
        "UPDATE tasks SET requires_human_judgment = 1 WHERE id = ?", (task_id,)
    )
    conn.commit()

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"
    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    deliverable = conn.execute(
        "SELECT status FROM deliverables WHERE id=?", (stage_result["deliverable_id"],)
    ).fetchone()
    assert deliverable["status"] == "pending_review"
    approval = conn.execute(
        "SELECT * FROM approvals WHERE subject_type='pipeline_run_stage' "
        "AND subject_id=?",
        (stage_result["id"],),
    ).fetchone()
    assert approval is not None


def test_update_task_human_judgment_endpoint_requires_admin_or_manager(client_and_conn):
    client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()["id"]
    task_id = _make_task(conn)

    client.post("/login", json={"username": "member1", "password": "memberpass"})
    resp = client.patch(
        f"/api/tasks/{task_id}/human-judgment", json={"requires_human_judgment": True}
    )
    assert resp.status_code == 403

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    resp = client.patch(
        f"/api/tasks/{task_id}/human-judgment", json={"requires_human_judgment": True}
    )
    assert resp.status_code == 200
    assert resp.json()["requires_human_judgment"] is True

    row = conn.execute(
        "SELECT requires_human_judgment FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    assert row["requires_human_judgment"] == 1
    assert admin_id is not None
