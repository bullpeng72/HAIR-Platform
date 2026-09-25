"""Phase 3 v4 골든셋 10건 — F8 성과리뷰(동의기반), F10 비용 조회(SPEC.md §9)."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_human(client, name="후보A", role="백엔드 개발자", cost=100.0):
    resp = client.post(
        "/api/human-pool",
        json={"name": name, "role": role, "skill_tags": [], "cost_estimate": cost},
    )
    return resp.json()["id"]


# ---- F8: 성과리뷰(동의 기반) ------------------------------------------------


def test_gc27_performance_score_rejected_without_consent(client_and_conn):
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    human_id = _make_human(client)

    resp = client.post(f"/api/human-pool/{human_id}/performance", json={"score": 0.9})
    assert resp.status_code == 403


def test_gc28_consent_then_manager_can_record_score(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    human_id = _make_human(client, "후보B")

    # human_pool 레코드를 member1 본인과 연결한 뒤 본인이 동의한다
    member_row = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_row["id"], human_id)
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/human-pool/{human_id}/consent", json={"consent": True})
    assert resp.status_code == 200

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/human-pool/{human_id}/performance", json={"score": 0.8})
    assert resp.status_code == 200

    resp = client.get("/api/performance-review")
    row = next(r for r in resp.json() if r["id"] == human_id and r["type"] == "human")
    assert row["performance_score"] == 0.8


def test_gc29_revoking_consent_immediately_clears_score(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    human_id = _make_human(client, "후보C")
    member_row = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_row["id"], human_id)
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    client.post(f"/api/human-pool/{human_id}/consent", json={"consent": True})
    _login(client, "manager1", "managerpass")
    client.post(f"/api/human-pool/{human_id}/performance", json={"score": 0.7})

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/human-pool/{human_id}/consent", json={"consent": False})
    assert resp.status_code == 200

    row = conn.execute(
        "SELECT performance_score, performance_consent FROM human_pool WHERE id = ?",
        (human_id,),
    ).fetchone()
    assert row["performance_consent"] == 0
    assert row["performance_score"] is None


def test_gc30_only_self_or_admin_can_set_consent(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    human_id = _make_human(client, "후보D")
    member_row = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_row["id"], human_id)
    )
    conn.commit()

    # manager1은 본인도 Admin도 아니므로 남의 동의를 대신 설정할 수 없다
    resp = client.post(f"/api/human-pool/{human_id}/consent", json={"consent": True})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/human-pool/{human_id}/consent", json={"consent": True})
    assert resp.status_code == 200


def test_gc31_agent_performance_needs_no_consent(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) "
        "VALUES ('백엔드 개발자', '[]', 'active')"
    )
    conn.commit()
    agent_id = cur.lastrowid

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/performance", json={"score": 0.85})
    assert resp.status_code == 200

    resp = client.get("/api/performance-review")
    row = next(r for r in resp.json() if r["id"] == agent_id and r["type"] == "agent")
    assert row["performance_score"] == 0.85


def test_gc32_performance_review_rbac_admin_full_member_own_viewer_denied(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    human_id = _make_human(client, "후보E")
    member_row = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member_row["id"], human_id)
    )
    conn.commit()
    other_human_id = _make_human(client, "후보F")

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/performance-review")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json() if r["type"] == "human"}
    assert {human_id, other_human_id} <= ids

    _login(client, "member1", "memberpass")
    resp = client.get("/api/performance-review")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [human_id]

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/performance-review")
    assert resp.status_code == 403


def test_gc33_review_includes_confirmed_assignment_count(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "디자이너 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "디자이너", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    human_id = _make_human(client, "후보G", "디자이너")

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})

    resp = client.get("/api/performance-review")
    row = next(r for r in resp.json() if r["id"] == human_id and r["type"] == "human")
    assert row["confirmed_assignments"] == 1


# ---- F10: 비용 조회 ----------------------------------------------------------


def test_gc34_cost_report_rbac_admin_manager_only(client_and_conn):
    client, _ = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.get("/api/cost-report")
    assert resp.status_code == 200

    _login(client, "member1", "memberpass")
    resp = client.get("/api/cost-report")
    assert resp.status_code == 403

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/cost-report")
    assert resp.status_code == 403


def test_gc35_agent_cost_manual_update_reflected_in_report(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) "
        "VALUES ('QA 엔지니어', '[]', 'active')"
    )
    conn.commit()
    agent_id = cur.lastrowid

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/cost", json={"cost_estimate": 5.0})
    assert resp.status_code == 403  # 비용 수동 입력은 Admin 전용

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/cost", json={"cost_estimate": 5.0})
    assert resp.status_code == 200

    resp = client.get("/api/cost-report")
    row = next(
        r
        for r in resp.json()["resources"]
        if r["id"] == agent_id and r["type"] == "agent"
    )
    assert row["cost_estimate"] == 5.0


def test_gc36_cost_report_aggregates_total_cost_by_confirmed_assignments(
    client_and_conn,
):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "PM 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "PM", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    human_id = _make_human(client, "후보H", "PM", cost=50.0)

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})

    resp = client.get("/api/cost-report")
    body = resp.json()
    row = next(
        r for r in body["resources"] if r["id"] == human_id and r["type"] == "human"
    )
    assert row["confirmed_assignments"] == 1
    assert row["total_cost"] == 50.0
    assert body["total_cost"] >= 50.0
