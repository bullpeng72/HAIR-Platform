"""Phase 64 — 셀프배정 결재 단순화(사용자 요청, 2026-11-05, "업무 배정,
파이프라인, crew 팀 구성 등을 이용해 로드러너 수준의 게임... 개선
계획을 수립해줘" → 로드맵 7번 "셀프배정 결재 단순화... 다음 개선
진행해줘").

조사 결과 — 멤버가 본인 자신을 Task에 배정 요청하는 "셀프배정"은
지금까지 다른 사람·Agent를 배정하는 일반 배정과 완전히 동일한 다단계
조직도 결재 절차를 거쳤다. 본인 시간만 쓰는 일이라 타인 자원을
커밋하는 일반 배정과 위험 성격이 다르므로, `assignments.
create_delegation()`(Agent 위임 전용, 결재 없이 즉시 confirmed)과
같은 논리로 셀프배정도 즉시 확정되게 바꿨다 — 역할과 무관하게
"요청자==배정 대상 본인" 관계로만 판단한다.

비용/네트워크 발생 없음(순수 DB 로직)."""

from __future__ import annotations

from hair_platform import assignments, deliverables


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def _link_human_pool_to_user(conn, human_id, user_id):
    conn.execute("UPDATE human_pool SET user_id = ? WHERE id = ?", (user_id, human_id))
    conn.commit()


def test_member_self_assignment_auto_confirms_without_approval_chain(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, human_id, member_id)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["approval_id"] is None
    assert body["approval_ids"] == []

    # 결재 체인 자체가 생성되지 않았어야 한다.
    approval_count = conn.execute(
        "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'assignment' "
        "AND subject_id = ?",
        (body["assignment_id"],),
    ).fetchone()["c"]
    assert approval_count == 0


def test_self_assignment_unlocks_deliverable_creation_immediately(client_and_conn):
    """이번 기능의 실질적 목적 — 결재를 기다리지 않고 바로 작업을
    시작할 수 있는지(task_participant_relationship이 즉시 "assignee"를
    반환해 create_deliverable()이 통과하는지)."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, human_id, member_id)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assert resp.json()["status"] == "confirmed"

    assert (
        deliverables.task_participant_relationship(conn, task_id, member_id)
        == "assignee"
    )
    deliverable_id = deliverables.create_deliverable(
        conn, task_id, member_id, "바로 작성한 결과물"
    )
    assert deliverable_id is not None


def test_admin_and_manager_self_assignment_also_auto_confirms(client_and_conn):
    """역할 무관 — 자기 자신 여부로만 판단한다는 설계 확인."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    resp = client.post(
        "/api/human-pool", json={"name": "매니저본인", "role": "PM", "skill_tags": []}
    )
    human_id = resp.json()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, human_id, manager_id)

    result = assignments.create_assignment_request(
        conn, task_id, "human", human_id, manager_id
    )
    assert result["status"] == "confirmed"
    assert result["approval_id"] is None


def test_manager_assigning_a_different_human_keeps_full_approval_chain(
    client_and_conn,
):
    """회귀 가드 — 셀프배정이 아닌 경로는 무변화."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "타인", "role": "백엔드 개발자", "skill_tags": []},
    )
    other_human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, other_human_id, member_id)
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]

    result = assignments.create_assignment_request(
        conn, task_id, "human", other_human_id, manager_id
    )
    assert result["status"] == "requested"
    assert result["approval_id"] is not None
    approval_count = conn.execute(
        "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'assignment' "
        "AND subject_id = ?",
        (result["assignment_id"],),
    ).fetchone()["c"]
    assert approval_count >= 1


def test_assigning_an_agent_keeps_full_approval_chain(client_and_conn):
    """회귀 가드 — Agent는 "본인"이 될 수 없으므로 셀프배정 대상이 아님."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client)
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('백엔드 개발자', '[]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    result = assignments.create_assignment_request(
        conn, task_id, "agent", agent_id, admin_id
    )
    assert result["status"] == "requested"
    assert result["approval_id"] is not None


def test_self_assignment_replaces_existing_confirmed_assignment(client_and_conn):
    """replaces_assignment_id가 있는 셀프 재배정도 resolve_assignment_
    decision()의 supersede 로직을 그대로 받는지 확인."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, human_id, member_id)

    first = assignments.create_assignment_request(
        conn, task_id, "human", human_id, member_id
    )
    assert first["status"] == "confirmed"

    second = assignments.create_assignment_request(
        conn,
        task_id,
        "human",
        human_id,
        member_id,
        replaces_assignment_id=first["assignment_id"],
    )
    assert second["status"] == "confirmed"

    prev = conn.execute(
        "SELECT status FROM assignments WHERE id = ?", (first["assignment_id"],)
    ).fetchone()
    assert prev["status"] == "superseded"


def test_audit_log_distinguishes_self_confirmed_from_requested(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)

    resp = client.post(
        "/api/human-pool",
        json={"name": "본인", "role": "백엔드 개발자", "skill_tags": []},
    )
    self_human_id = resp.json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    _link_human_pool_to_user(conn, self_human_id, member_id)

    resp = client.post(
        "/api/human-pool",
        json={"name": "타인", "role": "백엔드 개발자", "skill_tags": []},
    )
    other_human_id = resp.json()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]

    self_result = assignments.create_assignment_request(
        conn, task_id, "human", self_human_id, member_id
    )
    other_result = assignments.create_assignment_request(
        conn, task_id, "human", other_human_id, manager_id
    )

    self_audit = conn.execute(
        "SELECT action FROM audit_log WHERE subject_type = 'assignment' "
        "AND subject_id = ?",
        (self_result["assignment_id"],),
    ).fetchone()
    other_audit = conn.execute(
        "SELECT action FROM audit_log WHERE subject_type = 'assignment' "
        "AND subject_id = ?",
        (other_result["assignment_id"],),
    ).fetchone()
    assert self_audit["action"] == "assignment_self_confirmed"
    assert other_audit["action"] == "assignment_requested"
