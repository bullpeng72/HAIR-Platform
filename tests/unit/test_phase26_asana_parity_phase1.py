"""Phase 26 — ASANA_PARITY_DESIGN_PLAN.md Phase 1: F17(Task 메모) + F19(Task
단위 통합 활동 타임라인)(사용자 요청, 2026-10-08, "HAIR-Platform이 갖고 있는
장점을 살리면서 ASANA 모방 수준의 기능 및 UI 제공을 위한 기획 및 설계안을
제안해줘" → 설계안 승인 후 "Phase 1→5 순서로" 진행 지시).

F17: `GET /api/tasks/{task_id}`·`PATCH /api/tasks/{task_id}/notes` — 결과물이
아직 없는 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트 공간.
결재가 필요 없는 가벼운 메모라 `create_comment()`와 같은 참가자 판정만
재사용한다(대리 작성 금지, 승인 체인 없음).

F19: `GET /api/tasks/{task_id}/activity` — 배정·결과물·댓글·Agent 실행·결재·
Task 자체 변경을 `audit_log`에서 전부 모아 하나의 타임라인으로 합친다.
`audit_log`의 `subject_type`이 엔티티마다 제각각이라(`assignment`/
`deliverables`/`deliverable_comments`/`agent_execution_jobs`/`approval`/
`tasks`) task_id로 되짚는 경로도 제각각이다 — 이 테스트는 그 6개 경로가
전부 실제로 task_id에 닿는지, 그리고 서로 다른 task끼리 섞이지 않는지를
확인한다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _member1_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[
        "id"
    ]


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


def _link_human_to_member1(client, conn, human_id):
    _login(client, "admin1", "adminpass")
    member1_id = _member1_id(conn)
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    return member1_id


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
    return resp.json()["assignment"]["assignment_id"]


def _create_outsider(client, username="outsider1"):
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": username, "password": "outsiderpass", "role": "member"},
    )
    assert resp.status_code == 201, resp.text


def _setup_human_assignee_task(client, conn, task_name="백엔드 개발자"):
    brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    _link_human_to_member1(client, conn, human_id)
    assignment_id = _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id, human_id, assignment_id


def test_gc_task_detail_returns_404_for_missing_task(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/tasks/999999")
    assert resp.status_code == 404, resp.text


def test_gc_task_detail_includes_notes_default_empty(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == ""


def test_gc_participant_can_update_and_read_notes(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/tasks/{task_id}/notes", json={"notes": "진행 중"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "진행 중"

    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "진행 중"


def test_gc_non_participant_cannot_update_notes(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _create_outsider(client)
    _login(client, "outsider1", "outsiderpass")
    resp = client.patch(f"/api/tasks/{task_id}/notes", json={"notes": "몰래 수정"})
    assert resp.status_code == 403, resp.text


def test_gc_admin_can_read_but_role_is_still_supervisory(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "admin1", "adminpass")
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200, resp.text


def test_gc_brief_tasks_list_exposes_notes_field(client_and_conn):
    client, conn = client_and_conn
    brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    client.patch(f"/api/tasks/{task_id}/notes", json={"notes": "메모 본문"})

    _login(client, "manager1", "managerpass")
    resp = client.get(f"/api/briefs/{brief_id}/tasks")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["id"] == task_id)
    assert row["notes"] == "메모 본문"


def test_gc_activity_includes_assignment_and_approval_entries(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    resp = client.get(f"/api/tasks/{task_id}/activity")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    actions = [e["action"] for e in entries]
    assert "assignment_requested" in actions
    assert "approval_approved" in actions

    requested_entry = next(e for e in entries if e["action"] == "assignment_requested")
    assert requested_entry["subject_type"] == "assignment"
    assert requested_entry["subject_id"] == assignment_id


def test_gc_activity_includes_deliverable_and_comment_entries(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "작업 결과", "content_type": "text"},
    )
    assert resp.status_code == 201, resp.text
    deliverable_id = resp.json()["id"]

    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments", json={"content": "댓글"}
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/tasks/{task_id}/activity")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert "deliverable_created" in actions
    assert "deliverable_comment_created" in actions


def test_gc_activity_includes_task_notes_updated_entry(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _login(client, "member1", "memberpass")
    client.patch(f"/api/tasks/{task_id}/notes", json={"notes": "메모"})

    resp = client.get(f"/api/tasks/{task_id}/activity")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert "task_notes_updated" in actions


def test_gc_activity_does_not_leak_across_tasks(client_and_conn):
    client, conn = client_and_conn
    # human_pool은 계정 1:1 링크 제약(§73)이 있어 같은 member1을 두 번째
    # human_pool 레코드에 또 연결할 수 없다 — 하나의 human_pool 리소스를
    # task A·B 양쪽에 confirmed로 배정해 재사용한다(실제로 한 사람이
    # 여러 task를 동시에 맡는 흔한 경우와도 더 맞다).
    _brief_id_a, task_a, human_id, _assignment_a = _setup_human_assignee_task(
        client, conn, task_name="백엔드 개발자 A"
    )
    _brief_id_b, task_b = _make_brief_and_task(client, "백엔드 개발자 B")
    _confirm_assignment(
        client, task_b, "human", human_id, requester=("manager1", "managerpass")
    )
    _login(client, "member1", "memberpass")
    client.patch(f"/api/tasks/{task_a}/notes", json={"notes": "task A 전용 메모"})

    resp = client.get(f"/api/tasks/{task_b}/activity")
    assert resp.status_code == 200, resp.text
    after_values = [e["after_value"] for e in resp.json() if e["after_value"]]
    assert all("task A 전용 메모" not in str(v) for v in after_values)


def test_gc_non_participant_cannot_read_activity(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id, _assignment_id = _setup_human_assignee_task(
        client, conn
    )
    _create_outsider(client)
    _login(client, "outsider1", "outsiderpass")
    resp = client.get(f"/api/tasks/{task_id}/activity")
    assert resp.status_code == 403, resp.text
