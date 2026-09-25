"""Phase 8 코드 재점검(2026-09-26, R4) — F11 Web UI 셸을 실제로 채운다.

지금까지 v0-v4 개발 + 3라운드 코드 재점검을 거치는 동안 SPEC.md §1·F1-F14가
전부 "화면"·"Web UI 폼"을 명시적으로 요구했는데도 HTML/CSS/JS가 단 한 줄도
없었다(JSON REST API만 존재) — 이 갭을 `webui.py`(단일 페이지 바닐라 JS 앱)로
메운다. 여기서는:

1. 새 백엔드 지원 엔드포인트(GC56-60: /api/me, /logout, 브리프/Task/사람/
   Agent 목록 조회)가 올바르게 동작하는지.
2. `GET /`가 실제로 HTML 셸을 반환하는지(GC61).
3. 브라우저가 없으므로 JS를 직접 실행할 수는 없지만, JS가 호출하는 것과
   동일한 순서로 전체 API 플로우(브리프→분해→매칭→배정→승인→조직도 확인)가
   끝까지 동작하는지(GC62) — UI가 의존하는 계약이 깨지지 않았음을 검증한다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc56_me_and_logout(client_and_conn):
    client, _conn = client_and_conn
    resp = client.get("/api/me")
    assert resp.status_code == 401

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "manager1"

    resp = client.post("/logout")
    assert resp.status_code == 200
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_gc57_briefs_and_tasks_listing(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    brief_id = resp.json()["id"]
    client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": ["python"]}]},
    )

    resp = client.get("/api/briefs")
    assert resp.status_code == 200
    assert brief_id in {b["id"] for b in resp.json()}

    resp = client.get(f"/api/briefs/{brief_id}/tasks")
    assert resp.status_code == 200
    assert resp.json()[0]["role_name"] == "백엔드 개발자"


def test_gc58_human_pool_listing_exposes_raw_fields(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]

    resp = client.get("/api/human-pool")
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["id"] == human_id)
    assert row["availability"] == "available"
    assert row["performance_consent"] is False

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/human-pool")
    assert resp.status_code == 403


def test_gc59_agent_pool_listing_includes_pending_onboarding(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "디자이너 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "디자이너", "required_skills": ["figma"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})

    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200
    statuses = {a["status"] for a in resp.json()}
    assert "pending_onboarding" in statuses  # F3 검색(/api/pool/search)엔 안 보이던 것


def test_gc60_root_page_serves_html_shell(client_and_conn):
    client, _conn = client_and_conn
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    for marker in ["login-form", "tabs", "F1·F2 브리프", "F7 결재", "관리자(사용자)"]:
        assert marker in body


def test_gc61_full_ui_flow_end_to_end(client_and_conn):
    """JS가 호출하는 것과 동일한 순서로 전체 플로우를 끝까지 실행한다."""
    client, _conn = client_and_conn

    # 1) 로그인 확인(boot())
    _login(client, "manager1", "managerpass")
    assert client.get("/api/me").json()["role"] == "manager"

    # 2) F1 브리프 제출 + 목록
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    brief_id = resp.json()["id"]
    assert brief_id in {b["id"] for b in client.get("/api/briefs").json()}

    # 3) F2 Task 분해 + 목록
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    assert client.get(f"/api/briefs/{brief_id}/tasks").json()[0]["id"] == task_id

    # 4) F6 매칭 — 후보 없음 → F4 생성 제안
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.json()["generate_new"] is True

    # 5) F4 Agent 생성
    resp = client.post(f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]})
    assert resp.json()["status"] == "succeeded"
    agent_id = next(
        a["id"]
        for a in client.get("/api/agent-pool").json()
        if a["status"] == "pending_onboarding"
    )

    # 6) F9 온보딩(Admin)
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/onboard")
    assert resp.status_code == 200

    # 7) F6 재매칭 — 이제 후보가 보인다
    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.json()["generate_new"] is False
    assert resp.json()["top"][0]["candidate"]["id"] == agent_id

    # 8) F6 배정 요청
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "agent", "resource_id": agent_id},
    )
    approval_id = resp.json()["approval_id"]

    # 9) F7 승인함(inbox)에 보임 → 승인
    _login(client, "admin1", "adminpass")
    inbox = client.get("/api/approvals", params={"scope": "inbox"}).json()
    assert approval_id in {a["id"] for a in inbox}
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["status"] == "approved"

    # 10) F5 조직도에 반영
    chart = client.get("/api/org-chart").json()
    reports = [r for bucket in chart for r in bucket["reports"]]
    assert any(
        r["resource_type"] == "agent" and r["task_id"] == task_id for r in reports
    )

    # 11) F13 감사로그에 흔적이 남는다
    audit = client.get("/api/audit-log").json()
    actions = {row["action"] for row in audit["items"]}
    assert "assignment_requested" in actions
    assert "approval_approved" in actions
