"""Phase 23 — 실사용 리뷰(2026-10-07, 사용자 요청 "HAIR-Platform 위 시나리오를
하나씩 실행하며... 실용성 및 품질 향상이 되도록 개선해줘")로 발견·수정한
갭 검증. 5개 시나리오를 실제 서버(uvicorn, Playwright로
화면까지 확인)에 라이브로 실행하며 찾았다.

결재 사유(`ApprovalDecision.reason`) — 결재 체인은 조직도
매니저 체인 기준이라 결재자가 그 task의 참가자가 아닌 경우가 흔한데
(예: 시나리오 3에서 다른 task 담당 매니저가 CS 응답 초안을 결재),
참가자 전용 댓글 기능으로는 반려 사유를 시스템에 전혀 남길 수 없었다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task_with_candidate(client, task_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return task_id


def test_gc235_non_participant_decider_can_attach_reason_as_deliverable_comment(
    client_and_conn,
):
    """시나리오 3 실행 중 실측으로 발견 — 결재 체인은 조직도 매니저
    체인 기준이지 task 참가 여부와 무관하다. 그래서 그 deliverable이
    속한 task의 참가자가 전혀 아닌 다른 매니저가 결재하는 일이 흔한데,
    `POST /api/deliverables/{id}/comments`는 참가자 전용이라 그 매니저는
    반려해도 이유를 시스템 어디에도 남길 수 없었다(실측: 403 확인).
    `ApprovalDecision.reason`을 주면 참가자 여부와 무관하게 결재자
    자격으로 댓글 타임라인에 남는다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "manager2", "password": "managerpass", "role": "manager"},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "manager1", "managerpass")
    task_id = _make_task_with_candidate(client, "CS 응답 초안")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("CS 응답 초안",),
    )
    conn.commit()
    agent_id = cur.lastrowid

    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "agent", "resource_id": agent_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed"

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "초안입니다"}
    )
    deliverable_id = resp.json()["id"]
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit", json={})
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/approvals", params={"scope": "inbox"})
    _login(client, "manager2", "managerpass")
    resp = client.get("/api/approvals", params={"scope": "inbox"})
    deliverable_approval_id = next(
        a["id"] for a in resp.json() if a["subject_id"] == deliverable_id
    )

    # manager2는 이 task의 참가자가 아니므로 직접 댓글은 여전히 403이다
    # (회귀 확인 — 이 보호 자체는 그대로 유지돼야 한다).
    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments",
        json={"content": "참가자 아닌데 직접 댓글"},
    )
    assert resp.status_code == 403, resp.text

    # reason을 실어 반려하면 참가자가 아니어도 통과하고, 댓글로 남는다.
    resp = client.post(
        f"/api/approvals/{deliverable_approval_id}/decide",
        json={"approve": False, "reason": "에스컬레이션 문구가 빠졌습니다"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deliverable"]["status"] == "rejected"

    _login(client, "manager1", "managerpass")
    resp = client.get(f"/api/deliverables/{deliverable_id}/comments")
    assert resp.status_code == 200, resp.text
    contents = [c["content"] for c in resp.json()]
    assert any("[반려 사유] 에스컬레이션 문구가 빠졌습니다" in c for c in contents)


def test_gc236_approval_reason_is_optional_and_recorded_in_audit_for_assignments(
    client_and_conn,
):
    """assignment 결재는 댓글 개념이 없으므로 reason은 감사로그에만
    남는다 — reason 없이 호출하는 기존 흐름(회귀)도 그대로 통과해야
    한다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task_with_candidate(client, "QA")
    resp = client.post(
        "/api/human-pool", json={"name": "후보A", "role": "QA", "skill_tags": []}
    )
    resource_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    approval_id = resp.json()["approval_id"]

    _login(client, "admin1", "adminpass")
    # reason 없이(기존 계약) — 회귀 없어야 한다.
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/api/human-pool", json={"name": "후보B", "role": "QA", "skill_tags": []}
    )
    resource_b = resp.json()["id"]
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_b},
    )
    approval_b = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{approval_b}/decide",
        json={"approve": False, "reason": "이미 QA 인원이 배정돼 있습니다"},
    )
    assert resp.status_code == 200, resp.text

    row = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'approval_rejected' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert "이미 QA 인원이 배정돼 있습니다" in row["after_value"]
