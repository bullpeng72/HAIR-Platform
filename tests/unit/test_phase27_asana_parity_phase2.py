"""Phase 27 — ASANA 모방 기획 Phase 2: Task 참가자 초대
(사용자 요청, 2026-10-08, "...기획 및 설계안을 제안해줘" → 승인 후 "Phase
1→5 순서로" 진행 지시).

핵심: 지금까지 Agent/사람이 Task에 관여하려면 반드시 배정 요청→
결재 승인이라는 무거운 절차를 거쳐야 했다. "초대"는 결재가 필요 없는
가벼운 멤버십이다(`task_participants` 테이블, `assignments`와 분리) —
초대된 사람/Agent는 **댓글만** 읽고 쓸 수 있고, 결과물 작성·AI 실행
트리거 같은 "작성자 자격"은 여전히 기존의 엄격한 참가자 판정
(`task_participant_relationship()`)만 통과시킨다(설계 원칙).

이 테스트는 그 경계가 실제로 지켜지는지 확인한다: 초대된 사람은
댓글은 달 수 있지만 결과물은 못 쓴다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _member1_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[
        "id"
    ]


def _make_brief_and_task(client, task_name="백엔드 개발자"):
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": task_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": task_name, "required_skills": []}]},
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


def _setup_human_assignee_task(client, conn, task_name="백엔드 개발자"):
    brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    _link_human_to_member1(client, conn, human_id)
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id, human_id


def _create_outsider(client, username="outsider1"):
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": username, "password": "outsiderpass", "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _outsider_id(conn, username="outsider1"):
    return conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()["id"]


def test_gc_participant_can_invite_outsider(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    outsider_id = _create_outsider(client)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["participant_id"] == outsider_id

    resp = client.get(f"/api/tasks/{task_id}/participants")
    assert resp.status_code == 200, resp.text
    assert any(p["participant_id"] == outsider_id for p in resp.json())


def test_gc_non_participant_cannot_invite(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _create_outsider(client, "outsider1")
    another_id = _create_outsider(client, "outsider2")

    _login(client, "outsider1", "outsiderpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": another_id},
    )
    assert resp.status_code == 403, resp.text


def test_gc_admin_can_invite_even_without_participation(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    outsider_id = _create_outsider(client)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 201, resp.text


def test_gc_invited_human_can_comment_but_not_author_deliverable(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    outsider_id = _create_outsider(client)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "정식 참가자 결과물", "content_type": "text"},
    )
    assert resp.status_code == 201, resp.text
    deliverable_id = resp.json()["id"]

    _login(client, "outsider1", "outsiderpass")
    # 초대된 사람은 댓글은 가능해야 한다.
    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments",
        json={"content": "초대받은 사람의 질문"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/deliverables/{deliverable_id}/comments")
    assert resp.status_code == 200, resp.text

    # 하지만 결과물 작성(= 작성자 자격)은 여전히 금지돼야 한다.
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "초대받은 사람의 무단 결과물", "content_type": "text"},
    )
    assert resp.status_code == 403, resp.text


def test_gc_uninvited_outsider_still_cannot_comment(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _create_outsider(client, "outsider1")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    _login(client, "outsider1", "outsiderpass")
    resp = client.post(
        f"/api/deliverables/{deliverable_id}/comments",
        json={"content": "초대 안 받았는데 댓글"},
    )
    assert resp.status_code == 403, resp.text


def test_gc_invite_nonexistent_user_returns_404(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": 999999},
    )
    assert resp.status_code == 404, resp.text


def test_gc_duplicate_invite_returns_409(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    outsider_id = _create_outsider(client)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "human", "participant_id": outsider_id},
    )
    assert resp.status_code == 409, resp.text


def test_gc_invite_agent_participant(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("초대용 Agent",),
    )
    conn.commit()
    agent_id = cur.lastrowid

    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/tasks/{task_id}/participants")
    assert resp.status_code == 200, resp.text
    rows = [p for p in resp.json() if p["participant_type"] == "agent"]
    assert any(p["participant_id"] == agent_id for p in rows)
