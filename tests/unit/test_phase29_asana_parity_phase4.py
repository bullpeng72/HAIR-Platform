"""Phase 29 — ASANA 모방 기획 Phase 4: 작업 구조 심화(상태/우선순위/
의존관계) + 운영 가시성(사용자 요청, 2026-10-08,
"...기획 및 설계안을 제안해줘" → 승인 후 "Phase 1→5 순서로" 진행 지시,
열린 질문 3에 "Riido 수준의 그래프 시각화로" 답).

작업 구조 심화의 핵심 리스크는 순환 의존이다 — `task_dependencies`에 CHECK 제약으로
자기참조는 막지만 간접 순환(A→B→C→A)은 애플리케이션 레벨 DFS
(`_would_create_cycle()`)로만 막을 수 있다. 이 테스트의 1순위 목적은
그 DFS가 실제로 동작하는지 확인하는 것.

운영 가시성은 새 계측이 없으므로(설계 문서) 이미 있는 데이터(배정+상태,
결과물 반려 이력)만으로 workload/bottlenecks가 올바르게
집계되는지 확인한다."""

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


# ---------- status/priority ----------


def test_gc_participant_can_update_status_and_priority(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "in_progress"

    resp = client.patch(f"/api/tasks/{task_id}/priority", json={"priority": "urgent"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["priority"] == "urgent"

    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.json()["status"] == "in_progress"
    assert resp.json()["priority"] == "urgent"


def test_gc_non_participant_cannot_update_status(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _create_outsider(client)

    _login(client, "outsider1", "outsiderpass")
    resp = client.patch(f"/api/tasks/{task_id}/status", json={"status": "done"})
    assert resp.status_code == 403, resp.text


def test_gc_invalid_status_value_rejected(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/tasks/{task_id}/status", json={"status": "not_a_status"})
    assert resp.status_code == 422, resp.text


def test_gc_default_status_and_priority_on_new_task(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.json()["status"] == "queued"
    assert resp.json()["priority"] == "medium"


# ---------- dependencies ----------


def test_gc_add_and_list_dependency_graph(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, _human_id = _setup_human_assignee_task(client, conn, "작업 A")
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/briefs/{_brief_id}/tasks",
        json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
    )
    task_b = resp.json()["tasks"][0]["id"]

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/tasks/{task_a}/dependencies")
    assert resp.status_code == 200, resp.text
    graph = resp.json()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert {task_a, task_b} <= node_ids
    assert {"task_id": task_a, "depends_on_task_id": task_b} in graph["edges"]


def test_gc_self_dependency_rejected(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/dependencies", json={"depends_on_task_id": task_id}
    )
    assert resp.status_code == 400, resp.text


def test_gc_indirect_cycle_rejected(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, human_id = _setup_human_assignee_task(client, conn, "작업 A")
    _login(client, "manager1", "managerpass")
    task_b = client.post(
        f"/api/briefs/{_brief_id}/tasks",
        json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
    ).json()["tasks"][0]["id"]
    task_c = client.post(
        f"/api/briefs/{_brief_id}/tasks",
        json={"tasks": [{"role_name": "작업 C", "required_skills": []}]},
    ).json()["tasks"][0]["id"]
    # 의존관계 추가는 "그 task의 참가자"만 할 수 있다(참가자 전용 판정,
    # 그때 정한 관례) — member1을 B·C에도 confirmed 배정해 세 task
    # 모두의 참가자로 만든다.
    _confirm_assignment(
        client, task_b, "human", human_id, requester=("manager1", "managerpass")
    )
    _confirm_assignment(
        client, task_c, "human", human_id, requester=("manager1", "managerpass")
    )

    _login(client, "member1", "memberpass")
    # A depends on B, B depends on C — 정상
    resp = client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/tasks/{task_b}/dependencies", json={"depends_on_task_id": task_c}
    )
    assert resp.status_code == 201, resp.text

    # C가 A에 의존하면 A→B→C→A 순환 — 막혀야 한다
    resp = client.post(
        f"/api/tasks/{task_c}/dependencies", json={"depends_on_task_id": task_a}
    )
    assert resp.status_code == 409, resp.text


def test_gc_duplicate_dependency_rejected(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, _human_id = _setup_human_assignee_task(client, conn, "작업 A")
    task_b = (
        client.post(
            f"/api/briefs/{_brief_id}/tasks",
            json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
        )
    ).json()["tasks"][0]["id"]

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )
    assert resp.status_code == 409, resp.text


def test_gc_remove_dependency(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, _human_id = _setup_human_assignee_task(client, conn, "작업 A")
    task_b = (
        client.post(
            f"/api/briefs/{_brief_id}/tasks",
            json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
        )
    ).json()["tasks"][0]["id"]

    _login(client, "member1", "memberpass")
    client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )
    resp = client.delete(f"/api/tasks/{task_a}/dependencies/{task_b}")
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/tasks/{task_a}/dependencies")
    assert resp.json()["edges"] == []


def test_gc_dependency_activity_recorded_in_task_activity(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, _human_id = _setup_human_assignee_task(client, conn, "작업 A")
    task_b = (
        client.post(
            f"/api/briefs/{_brief_id}/tasks",
            json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
        )
    ).json()["tasks"][0]["id"]

    _login(client, "member1", "memberpass")
    client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )

    resp = client.get(f"/api/tasks/{task_a}/activity")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert "task_dependency_added" in actions


# ---------- 운영 가시성 ----------


def test_gc_workload_counts_in_progress_assignments(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _login(client, "member1", "memberpass")
    client.patch(f"/api/tasks/{task_id}/status", json={"status": "in_progress"})

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard/workload")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["resource_type"] == "human"]
    assert any(r["in_progress_count"] >= 1 for r in rows)


def test_gc_bottlenecks_detects_blocked_task(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_a, _human_id = _setup_human_assignee_task(client, conn, "작업 A")
    task_b = (
        client.post(
            f"/api/briefs/{_brief_id}/tasks",
            json={"tasks": [{"role_name": "작업 B", "required_skills": []}]},
        )
    ).json()["tasks"][0]["id"]

    _login(client, "member1", "memberpass")
    client.post(
        f"/api/tasks/{task_a}/dependencies", json={"depends_on_task_id": task_b}
    )

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard/bottlenecks")
    assert resp.status_code == 200, resp.text
    blocked_ids = [r["id"] for r in resp.json() if "blocked" in r["reasons"]]
    assert task_a in blocked_ids


def test_gc_bottlenecks_detects_repeated_rejection(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)

    for _ in range(2):
        _login(client, "member1", "memberpass")
        resp = client.post(
            f"/api/tasks/{task_id}/deliverables",
            json={"content": "초안", "content_type": "text"},
        )
        deliverable_id = resp.json()["id"]
        client.post(f"/api/deliverables/{deliverable_id}/submit")
        resp = client.get(f"/api/tasks/{task_id}/deliverables")
        approval_id = next(d for d in resp.json() if d["id"] == deliverable_id)[
            "approval_id"
        ]
        _login(client, "admin1", "adminpass")
        client.post(f"/api/approvals/{approval_id}/decide", json={"approve": False})

    resp = client.get("/api/dashboard/bottlenecks")
    assert resp.status_code == 200, resp.text
    rejected_ids = [r["id"] for r in resp.json() if "rejected_twice" in r["reasons"]]
    assert task_id in rejected_ids


def test_gc_dashboard_summary_includes_workload_and_bottlenecks(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "workload" in body
    assert "bottlenecks" in body
