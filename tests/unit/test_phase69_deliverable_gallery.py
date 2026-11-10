"""Phase 69 — 결과물 단독 조회/단독 뷰 페이지/갤러리/삭제 + 파이프라인
출력 유형(output_content_type) 일반화(사용자 요청, 2026-11-10,
"claude의 artifact 처럼 최종 결과물을 전체 웹으로 확인하는 기능, 최종
결과물을 모아보는 갤러리 기능(결과물 삭제시 갤러리에서도 삭제), 코드성격의
결과물과, 리포트, 프레젠테이션 결과물 등 결과물 출력 유형 다양화").

설계 결정(사용자 확인) — 단독 뷰는 "내부 전용 새 탭"만: 비인증 공개
URL을 새로 만들지 않고 기존 `Depends(get_current_user)` 세션 인증을
그대로 유지한다. 갤러리는 캐시/스냅샷이 아니라 `deliverables` 테이블을
매번 실시간 조회하므로, 삭제하면 다음 조회부터 자동으로 빠진다는 설계를
`test_gallery_deleted_deliverable_disappears_without_extra_sync()`로
직접 검증한다 — 이게 이 라운드의 핵심 설계 주장이다.

비용/네트워크 발생 방지: `crew_runtime.run_crew` 그 자체만 patch한다
(`test_phase48_pipeline_runtime.py`와 같은 원칙)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, deliverables
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_and_task(client, task_name="백엔드 개발자", skills=None):
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": skills or []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def _confirm_assignment(client, task_id, resource_type, resource_id, requester):
    _login(client, requester[0], requester[1])
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": resource_type, "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text


def _setup_member1_task(client, conn, task_name="백엔드 개발자"):
    """member1이 사람 당사자로 confirmed 배정된 task를 만든다."""
    brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id


def _create_draft_deliverable(client, task_id, content="초안 내용"):
    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": content, "content_type": "text"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------- 단독 조회 GET /api/deliverables/{id} ----------


def test_get_deliverable_endpoint_404_for_missing(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/deliverables/999999")
    assert resp.status_code == 404


def test_get_deliverable_endpoint_403_for_non_participant(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "viewer1", "viewerpass")
    resp = client.get(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 403


def test_get_deliverable_endpoint_200_for_participant_and_admin(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id, content="내 초안")

    _login(client, "member1", "memberpass")
    resp = client.get(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "내 초안"

    _login(client, "admin1", "adminpass")
    resp = client.get(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 200


# ---------- 삭제 DELETE /api/deliverables/{id} ----------


def test_delete_deliverable_member_can_delete_own_draft(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.delete(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 200, resp.text

    row = conn.execute(
        "SELECT 1 FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    assert row is None


def test_delete_deliverable_non_author_member_forbidden(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "viewer1", "viewerpass")
    resp = client.delete(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 403

    row = conn.execute(
        "SELECT 1 FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    assert row is not None


def test_delete_deliverable_author_forbidden_after_submit(client_and_conn):
    """제출(pending_review) 후에는 본인도 못 지운다 — 결재 이력이라
    draft일 때만 본인 삭제를 허용하는 설계를 직접 확인."""
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text

    resp = client.delete(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 403


def test_delete_deliverable_admin_can_delete_any_status_and_cascades(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text
    client.post(
        f"/api/deliverables/{deliverable_id}/comments", json={"content": "댓글"}
    )

    # 이 deliverable을 가리키는 pipeline_run_stage를 하나 만들어(FK를
    # 만족시키려면 실제 템플릿/단계/실행이 필요) 삭제 후 deliverable_id가
    # NULL로 끊기는지(행 자체는 보존되는지) 확인.
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    tid = pr.create_pipeline_template(conn, admin_id, "p-cascade-test", "", "x")
    stage_id = pr.add_stage(
        conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")]
    )
    run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
    conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, "
        "deliverable_id) VALUES (?, ?, 'passed', ?)",
        (run_id, stage_id, deliverable_id),
    )
    conn.commit()

    _login(client, "admin1", "adminpass")
    resp = client.delete(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 200, resp.text

    assert (
        conn.execute(
            "SELECT 1 FROM deliverables WHERE id = ?", (deliverable_id,)
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM deliverable_comments WHERE deliverable_id = ?",
            (deliverable_id,),
        ).fetchone()
        is None
    )
    stage_row = conn.execute(
        "SELECT deliverable_id FROM pipeline_run_stages WHERE pipeline_run_id = ?",
        (run_id,),
    ).fetchone()
    assert stage_row is not None  # 단계 실행 이력 행 자체는 보존
    assert stage_row["deliverable_id"] is None  # 참조만 끊김

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'deliverable_deleted' "
        "AND subject_id = ?",
        (deliverable_id,),
    ).fetchone()
    assert audit_row is not None


# ---------- 갤러리 GET /api/deliverables ----------


def test_gallery_admin_sees_all_tasks_deliverables(client_and_conn):
    client, conn = client_and_conn
    _, task_a = _setup_member1_task(client, conn, "역할A")
    _create_draft_deliverable(client, task_a, "A의 내용")

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/deliverables")
    assert resp.status_code == 200, resp.text
    assert any(d["task_id"] == task_a for d in resp.json())


def test_gallery_member_sees_only_own_task_deliverables(client_and_conn):
    client, conn = client_and_conn
    _, task_mine = _setup_member1_task(client, conn, "내 역할")
    deliverable_mine = _create_draft_deliverable(client, task_mine, "내 것")

    # member1이 참가하지 않는 별개 task + deliverable(관리자가 직접 생성).
    _brief_id, task_other = _make_brief_and_task(client, "남의 역할")
    _login(client, "admin1", "adminpass")
    other_id = create_user(conn, "stranger1", "strangerpass", Role.MEMBER)
    conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, 'human', ?, '남의 것', 'text', 'draft')",
        (task_other, other_id),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.get("/api/deliverables")
    assert resp.status_code == 200, resp.text
    ids = {d["id"] for d in resp.json()}
    assert deliverable_mine in ids
    assert all(d["task_id"] != task_other for d in resp.json())


def test_gallery_content_type_filter(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    _login(client, "member1", "memberpass")
    client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "텍스트", "content_type": "text"},
    )
    client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "```python\nx=1\n```", "content_type": "code"},
    )
    resp = client.get("/api/deliverables?content_type=code")
    assert resp.status_code == 200, resp.text
    assert all(d["content_type"] == "code" for d in resp.json())
    assert len(resp.json()) == 1


def test_gallery_deleted_deliverable_disappears_without_extra_sync(client_and_conn):
    """핵심 설계 주장 — 갤러리는 실시간 조회라 별도 동기화 코드 없이도
    삭제 즉시 목록에서 빠진다."""
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.get("/api/deliverables")
    assert any(d["id"] == deliverable_id for d in resp.json())

    resp = client.delete(f"/api/deliverables/{deliverable_id}")
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/deliverables")
    assert resp.status_code == 200, resp.text
    assert all(d["id"] != deliverable_id for d in resp.json())


# ---------- 단독 뷰 페이지 GET /deliverables/{id}/view ----------


def test_view_page_200_renders_content_for_participant(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id, content="고유한 내용 XYZ")

    _login(client, "member1", "memberpass")
    resp = client.get(f"/deliverables/{deliverable_id}/view")
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    assert "고유한 내용 XYZ" in resp.text


def test_view_page_403_for_non_participant(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "viewer1", "viewerpass")
    resp = client.get(f"/deliverables/{deliverable_id}/view")
    assert resp.status_code == 403


def test_view_page_404_for_missing(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/deliverables/999999/view")
    assert resp.status_code == 404


def test_view_page_requires_login(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)
    client.cookies.clear()
    resp = client.get(f"/deliverables/{deliverable_id}/view")
    assert resp.status_code == 401


# ---------- deliverables.py 단위 테스트 (DB 직접) ----------


def test_get_deliverable_returns_none_for_missing():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    assert deliverables.get_deliverable(conn, 999999) is None


def test_list_deliverables_gallery_offset_and_limit():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')",
        (admin_id,),
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '역할', '[]')",
        (brief_id,),
    )
    task_id = cur.lastrowid
    for i in range(3):
        conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'human', ?, ?, 'text', 'draft')",
            (task_id, admin_id, f"content-{i}"),
        )
    conn.commit()
    all_rows = deliverables.list_deliverables_gallery(conn, admin_id, "admin")
    assert len(all_rows) == 3
    limited = deliverables.list_deliverables_gallery(conn, admin_id, "admin", limit=1)
    assert len(limited) == 1


# ---------- output_content_type (파이프라인 일반화) ----------


def test_add_stage_rejects_invalid_output_content_type():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    with pytest.raises(ValueError, match="invalid_output_content_type"):
        pr.add_stage(
            conn,
            admin_id,
            tid,
            "s1",
            "single",
            [("요구사항 분석가", "")],
            output_content_type="slideshow",
        )


def test_add_stage_persists_output_content_type():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    stage_id = pr.add_stage(
        conn,
        admin_id,
        tid,
        "s1",
        "single",
        [("요구사항 분석가", "")],
        output_content_type="code",
    )
    stage = conn.execute(
        "SELECT output_content_type FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert stage["output_content_type"] == "code"


def test_add_stage_defaults_output_content_type_to_text():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    stage_id = pr.add_stage(
        conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")]
    )
    stage = conn.execute(
        "SELECT output_content_type FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert stage["output_content_type"] == "text"


def test_update_stage_rejects_invalid_output_content_type():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    stage_id = pr.add_stage(
        conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")]
    )
    with pytest.raises(ValueError, match="invalid_output_content_type"):
        pr.update_stage(
            conn, admin_id, stage_id, output_content_type="not_a_real_type"
        )


def test_update_stage_accepts_valid_output_content_type():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    stage_id = pr.add_stage(
        conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")]
    )
    pr.update_stage(conn, admin_id, stage_id, output_content_type="html")
    stage = conn.execute(
        "SELECT output_content_type FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert stage["output_content_type"] == "html"


def _good_run_crew(gate_scores=None, tcr=90.0):
    gate_scores = gate_scores or {"A": {"score": 0.9, "passed": True}}

    def _fn(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process="sequential",
        provider=None,
        model=None,
        manager_agent_id=None,
        expected_outputs=None,
        self_review=False,
        memory=True,
    ):
        return {
            "final_output": "최종산출물",
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": f"output-{aid}",
                }
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": tcr,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
            "token_usage": {"total_tokens": 1},
        }

    return _fn


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '역할', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


def test_stage_execution_writes_configured_output_content_type_to_deliverable():
    """회귀 테스트의 핵심 — 예전엔 이 INSERT가 항상 'text'로 하드코딩돼
    있었다. 단계에 output_content_type='code'를 지정하면 실제로 그
    값으로 deliverables.content_type이 저장되는지 직접 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "s1",
        "single",
        [("요구사항 분석가", "")],
        output_content_type="code",
    )

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    deliverable = conn.execute(
        "SELECT content_type FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert deliverable is not None
    assert deliverable["content_type"] == "code"
