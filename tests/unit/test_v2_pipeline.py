"""Phase 3 v2 골든셋 8건 — F5/F6 assignments 공유, assignment_history, 재배치(SPEC.md §9)."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task_with_candidate(client, task_name="백엔드 개발자"):
    """매니저로 로그인된 상태를 전제 — 브리프+Task+Human Pool 후보 1명을 만든다."""
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    resp = client.post(
        "/api/human-pool", json={"name": "후보A", "role": task_name, "skill_tags": []}
    )
    resource_id = resp.json()["id"]
    return task_id, resource_id


def test_gc11_assignment_request_creates_pending_approval_and_history(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client)

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "requested"

    row = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (body["assignment_id"],)
    ).fetchone()
    assert row["status"] == "requested"

    history = conn.execute(
        "SELECT * FROM assignment_history WHERE assignment_id = ?",
        (body["assignment_id"],),
    ).fetchall()
    assert len(history) == 1
    assert history[0]["from_status"] is None
    assert history[0]["to_status"] == "requested"


def test_gc12_member_cannot_request_assignment(client_and_conn):
    """사용자 결정(2026-09-29, "팀원 셀프서비스(본인이 직접 배정 요청)를
    허용")으로 MEMBER 자체는 이 엔드포인트를 더 이상 role 자체로 차단
    당하지 않는다 — 대신 "본인이 아닌 자원"을 요청하면 여전히 403이다
    (이름은 유지하되 검증 대상이 role 차단 → 본인 확인으로 바뀌었다).
    본인 자원 요청이 실제로 성공하는 경로는 GC200이 검증한다."""
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert resp.status_code == 403


def test_gc13_approval_confirms_assignment_and_records_history(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client)
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assignment_id = resp.json()["assignment_id"]
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "confirmed"

    row = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    assert row["status"] == "confirmed"

    history = conn.execute(
        "SELECT to_status FROM assignment_history WHERE assignment_id = ? ORDER BY id",
        (assignment_id,),
    ).fetchall()
    assert [h["to_status"] for h in history] == ["requested", "confirmed"]


def test_gc14_self_approval_blocked_for_assignment_subject(client_and_conn):
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client)
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    approval_id = resp.json()["approval_id"]

    # manager1 본인이 자기 배정 요청을 승인하려 하면 거부된다(ADR 결정 3-A)
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403


def test_gc15_rejected_assignment_never_confirms(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client)
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assignment_id = resp.json()["assignment_id"]
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": False})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "rejected"

    # 이미 처리된 승인을 다시 결정하려 하면 거부된다(NF3: 결재라인 우회 방지)
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 409

    row = conn.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    assert row["status"] == "rejected"


def test_gc16_org_chart_shows_only_confirmed_assignments(client_and_conn):
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_id = _make_task_with_candidate(client, "디자이너")
    client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/org-chart")
    assert resp.status_code == 200
    # 아직 requested 상태라 조직도에 노출되지 않는다
    all_reports = [r for group in resp.json() for r in group["reports"]]
    assert all_reports == []


def test_gc17_reassignment_supersedes_previous_confirmed_assignment(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_a = _make_task_with_candidate(client, "QA 엔지니어")

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_a},
    )
    approval_a = resp.json()["approval_id"]
    assignment_a = resp.json()["assignment_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_a}/decide", json={"approve": True})

    # 같은 task에 새 후보(B)로 재배치 요청
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "후보B", "role": "QA 엔지니어", "skill_tags": []},
    )
    resource_b = resp.json()["id"]
    # 2026-10-06 정정(다자간 팀 협업 기획 Phase 1+2) — "다중 confirmed
    # 허용"으로 바뀌어 더 이상 같은 resource_type이라고 자동으로
    # supersede되지 않는다. 명시적으로 교체 의도를 표현해야 한다.
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={
            "resource_type": "human",
            "resource_id": resource_b,
            "replaces_assignment_id": assignment_a,
        },
    )
    approval_b = resp.json()["approval_id"]
    assignment_b = resp.json()["assignment_id"]

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_b}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed"

    row_a = conn.execute(
        "SELECT status FROM assignments WHERE id = ?", (assignment_a,)
    ).fetchone()
    row_b = conn.execute(
        "SELECT status FROM assignments WHERE id = ?", (assignment_b,)
    ).fetchone()
    assert row_a["status"] == "superseded"
    assert row_b["status"] == "confirmed"

    resp = client.get("/api/org-chart")
    names = [r["resource_name"] for group in resp.json() for r in group["reports"]]
    assert names == ["후보B"]


def test_gc18_assignment_history_records_full_reassignment_sequence(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id, resource_a = _make_task_with_candidate(client, "PM")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_a},
    )
    approval_a = resp.json()["approval_id"]
    assignment_a = resp.json()["assignment_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_a}/decide", json={"approve": True})

    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool", json={"name": "후보C", "role": "PM", "skill_tags": []}
    )
    resource_c = resp.json()["id"]
    # 2026-10-06 정정(다자간 팀 협업 기획 Phase 1+2) — 위 gc17과 같은 이유로
    # 명시적 replaces_assignment_id가 필요해졌다.
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={
            "resource_type": "human",
            "resource_id": resource_c,
            "replaces_assignment_id": assignment_a,
        },
    )
    approval_c = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_c}/decide", json={"approve": True})

    history_a = conn.execute(
        "SELECT from_status, to_status FROM assignment_history "
        "WHERE assignment_id = ? ORDER BY id",
        (assignment_a,),
    ).fetchall()
    assert [(h["from_status"], h["to_status"]) for h in history_a] == [
        (None, "requested"),
        ("requested", "confirmed"),
        ("confirmed", "superseded"),
    ]
