"""Phase 3 v0 골든셋 4건 — 파이프라인 형태 증명."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc1_auth_and_rbac_boundary(client_and_conn):
    client, _ = client_and_conn

    resp = client.post("/login", json={"username": "admin1", "password": "wrong"})
    assert resp.status_code == 401

    # 뷰어는 브리프 제출 권한이 없다(RBAC 매트릭스)
    _login(client, "viewer1", "viewerpass")
    resp = client.post("/api/briefs", json={"raw_text": "요청"})
    assert resp.status_code == 403

    # 관리자는 통과한다
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    assert resp.status_code == 201


def test_gc2_f1_brief_submission_pipeline(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")

    resp = client.post(
        "/api/briefs", json={"raw_text": "백엔드 개발자 1명\n디자이너 1명"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["stub_roles"] == ["백엔드 개발자 1명", "디자이너 1명"]

    row = conn.execute("SELECT * FROM briefs WHERE id = ?", (body["id"],)).fetchone()
    assert row["status"] == "submitted"


def test_gc3_f12_human_pool_onboarding_pipeline(client_and_conn):
    client, conn = client_and_conn

    # 팀원은 권한이 없다(RBAC 매트릭스)
    _login(client, "member1", "memberpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    assert resp.status_code == 403

    # 매니저는 통과한다
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    assert resp.status_code == 201

    row = conn.execute(
        "SELECT * FROM human_pool WHERE id = ?", (resp.json()["id"],)
    ).fetchone()
    assert row["name"] == "김하니"


def test_gc4_minimal_approval_workflow_and_self_approval_block(client_and_conn):
    client, _ = client_and_conn

    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "QA 엔지니어 1명"})
    brief_id = resp.json()["id"]

    resp = client.post(
        "/api/approvals", params={"subject_type": "brief", "subject_id": brief_id}
    )
    assert resp.status_code == 201
    approval_id = resp.json()["id"]

    # 요청자 본인(manager1, 승인 권한은 있음)이 자기 건을 승인하려 하면 거부된다(self-approval 방지)
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403
    assert "본인" in resp.json()["detail"]

    # 다른 관리자(admin1)는 승인할 수 있다
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
