"""Phase 5 코드 재점검(2026-09-26) 골든셋 — 기획 문서가 아니라 실제 구현 코드를
대상으로 한 오류/Gray영역/누락 재검토에서 발견한 4건을 수정하고 회귀 테스트로
고정한다.

1. GC42: `POST /api/approvals`가 F7 결재라인을 우회하던 구멍(subject_type=assignment,
   role 제한 없음)을 닫았다 — brief 전용으로 제한.
2. GC43: F3 검색(`/api/pool/search`)의 role/skill 필터가 Agent Pool에는 적용 안
   되던 버그를 고쳤다.
3. GC44: 조직도(F5)가 Agent 배정을 전부 `manager_id=None` 버킷으로 뭉개던 것을
   고쳐 요청자(`requested_by`)를 관리자로 귀속시킨다.
4. GC45/GC46: `users.manager_id`를 Web UI/API로 설정할 방법이 전무해 F7이 항상
   flat(단일 단계, approver_id=NULL)로만 동작하던 누락을 메웠다(`POST /api/users`,
   `PATCH /api/users/{id}/manager`) — 실제로 approver_id가 채워지는지, 지정 안 된
   매니저는 거부되는지까지 검증한다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_with_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def test_gc42_generic_approval_endpoint_rejects_assignment_bypass(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _, task_id = _make_brief_with_task(client)

    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assignment_id = resp.json()["assignment_id"]

    # 진행 중인 정식 F7 체인과 무관한 rogue 승인을 만들려는 시도는 400으로 막힌다.
    resp = client.post(
        "/api/approvals",
        params={"subject_type": "assignment", "subject_id": assignment_id},
    )
    assert resp.status_code == 400

    # VIEWER는 brief 승인 요청조차 만들 수 없다(role 제한 신설).
    _login(client, "viewer1", "viewerpass")
    resp = client.post(
        "/api/approvals", params={"subject_type": "brief", "subject_id": 1}
    )
    assert resp.status_code == 403

    # brief 승인은 여전히 정상 동작한다(v0 회귀 방지).
    _login(client, "manager1", "managerpass")
    brief_id, _ = _make_brief_with_task(client, "QA 엔지니어")
    resp = client.post(
        "/api/approvals", params={"subject_type": "brief", "subject_id": brief_id}
    )
    assert resp.status_code == 201

    # 존재하지 않는 brief는 404.
    resp = client.post(
        "/api/approvals", params={"subject_type": "brief", "subject_id": 999999}
    )
    assert resp.status_code == 404


def test_gc43_pool_search_filters_apply_to_agents_too(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "디자이너")

    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    item_id = resp.json()["items"][0]["id"]
    agent_id = conn.execute(
        "SELECT id FROM agent_pool WHERE source_job_item_id = ?", (item_id,)
    ).fetchone()["id"]

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/onboard")
    assert resp.status_code == 200

    # role 필터에 안 맞으면 이 Agent가 결과에 나오면 안 된다(수정 전엔 버그로 나왔다).
    resp = client.get("/api/pool/search", params={"role": "백엔드"})
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()["items"] if c["type"] == "agent"]
    assert agent_id not in ids

    # role이 맞으면 나온다.
    resp = client.get("/api/pool/search", params={"role": "디자이너"})
    ids = [c["id"] for c in resp.json()["items"] if c["type"] == "agent"]
    assert agent_id in ids


def test_gc44_org_chart_attributes_agent_assignment_to_requester(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "디자이너")

    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    item_id = resp.json()["items"][0]["id"]
    agent_id = conn.execute(
        "SELECT id FROM agent_pool WHERE source_job_item_id = ?", (item_id,)
    ).fetchone()["id"]

    _login(client, "admin1", "adminpass")
    client.post(f"/api/agent-pool/{agent_id}/onboard")

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "agent", "resource_id": agent_id},
    )
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["status"] == "approved"

    resp = client.get("/api/org-chart")
    assert resp.status_code == 200
    chart = resp.json()

    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    bucket = next(b for b in chart if b["manager_id"] == manager1_id)
    report = next(r for r in bucket["reports"] if r["resource_type"] == "agent")
    assert report["resource_name"].startswith(f"Agent#{agent_id}")
    # 수정 전엔 이 배정이 manager_id=None 버킷에 뭉개져 있었다 — 이제 아니어야 한다.
    assert not any(b["manager_id"] is None and b["reports"] for b in chart)


def test_gc45_admin_only_user_creation(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/users",
        json={"username": "manager2", "password": "pw", "role": "manager"},
    )
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "manager2", "password": "pw", "role": "manager"},
    )
    assert resp.status_code == 201
    row = conn.execute("SELECT * FROM users WHERE username = 'manager2'").fetchone()
    assert row["role"] == "manager"

    # 중복 username은 409.
    resp = client.post(
        "/api/users",
        json={"username": "manager2", "password": "pw", "role": "manager"},
    )
    assert resp.status_code == 409


def test_gc46_manager_id_endpoint_makes_approval_chain_actually_route(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]

    resp = client.post(
        "/api/users",
        json={"username": "manager2", "password": "pw", "role": "manager"},
    )
    assert resp.status_code == 201

    # 수정 전엔 이걸 설정할 방법이 아예 없어서 결재라인이 항상 flat이었다.
    resp = client.patch(
        f"/api/users/{manager1_id}/manager", json={"manager_id": admin_id}
    )
    assert resp.status_code == 200
    assert resp.json()["manager_id"] == admin_id

    # 존재하지 않는 매니저 지정은 404, 본인을 상사로 지정은 400.
    resp = client.patch(
        f"/api/users/{manager1_id}/manager", json={"manager_id": 999999}
    )
    assert resp.status_code == 404
    resp = client.patch(
        f"/api/users/{manager1_id}/manager", json={"manager_id": manager1_id}
    )
    assert resp.status_code == 400

    _login(client, "manager1", "managerpass")
    _, task_id = _make_brief_with_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]

    approver_id = conn.execute(
        "SELECT approver_id FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()["approver_id"]
    assert approver_id == admin_id  # 이제 실제로 지정된 승인자가 채워진다.

    # 체인에 없는 다른 매니저는 거부된다(수정 전엔 approver_id가 항상 NULL이라
    # 이 체크가 한 번도 실제로 발동한 적이 없었다).
    _login(client, "manager2", "pw")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
