"""Phase 22 — 다자간 팀 협업(사용자 요청, 2026-10-06, "복수의 사람이 복수의
Agent와 또는 CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록
... 다시 기획"). Phase 1+2 구현
검증: `teams.py`(등록→재사용 CRUD), `assignments.resource_type='team'`(
결재 주체는 팀 리더, 기본값은 요청자·변경 가능), "다중 confirmed 허용"(같은
task·같은 resource_type이어도 명시적 `replaces_assignment_id` 없이는 더 이상
자동 supersede되지 않음)을 다룬다.

- `teams.py` CRUD + RBAC + 409/404 에러 경로.
- 팀 배정 요청 → 리더 기본값/변경 → self-approval.
- 팀 리더/팀 멤버가 `task_participant_relationship()`에서 참가자로
인정되어 결과물을 쓸 수 있는지(그 외엔 403).
- `GET /api/tasks/{id}/assignments`가 team 자원의 이름(`teams.name`)과
`team_leader_id`를 노출하는지 — 이 테스트를 작성하며 기존 쿼리가 teams를
조인하지 않아 `resource_name`이 항상 null로 나오는 실제 버그를 발견해
`app.py::list_task_assignments`에 `LEFT JOIN teams` + `team_leader_id`
컬럼을 추가해 고쳤다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


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


def _create_team(client, name="백엔드팀", requester=("admin1", "adminpass")):
    _login(client, *requester)
    resp = client.post("/api/teams", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _user_id(conn, username):
    return conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()["id"]


def test_gc226_create_team_and_add_members_via_api(client_and_conn):
    client, conn = client_and_conn
    team_id = _create_team(client, "백엔드팀")

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool", json={"name": "김과장", "role": "백엔드 개발자"}
    )
    human_id = resp.json()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("코드리뷰 Agent",),
    )
    conn.commit()
    agent_id = cur.lastrowid

    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": human_id, "role_label": "팀장"},
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": agent_id},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(f"/api/teams/{team_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "백엔드팀"
    assert body["is_active"] is True
    members = {(m["member_type"], m["member_name"]) for m in body["members"]}
    assert members == {("human", "김과장"), ("agent", "코드리뷰 Agent")}
    role_labels = {m["member_name"]: m["role_label"] for m in body["members"]}
    assert role_labels["김과장"] == "팀장"

    resp = client.get("/api/teams")
    assert resp.status_code == 200
    assert any(t["id"] == team_id for t in resp.json())

    # member/viewer는 팀 생성이 금지된다(admin+manager 전용 — pool_registry와 동일 관례).
    _login(client, "member1", "memberpass")
    resp = client.post("/api/teams", json={"name": "member가 만든 팀"})
    assert resp.status_code == 403
    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/teams")
    assert resp.status_code == 403


def test_gc227_add_team_member_rejects_unknown_team_member_and_duplicate(
    client_and_conn,
):
    client, _conn = client_and_conn
    team_id = _create_team(client)

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/teams/999999/members",
        json={"member_type": "human", "member_id": 1},
    )
    assert resp.status_code == 404

    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": 999999},
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/human-pool", json={"name": "이대리", "role": "백엔드 개발자"}
    )
    human_id = resp.json()["id"]
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": human_id},
    )
    assert resp.status_code == 201
    member_row_id = resp.json()["id"]

    # 같은 멤버를 다시 추가하면 충돌(UNIQUE 제약).
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": human_id},
    )
    assert resp.status_code == 409

    resp = client.delete(f"/api/teams/{team_id}/members/{member_row_id}")
    assert resp.status_code == 200
    resp = client.delete(f"/api/teams/{team_id}/members/{member_row_id}")
    assert resp.status_code == 404


def test_gc228_delete_team_requires_admin_manager_forbidden(client_and_conn):
    client, _conn = client_and_conn
    team_id = _create_team(client)

    _login(client, "manager1", "managerpass")
    resp = client.delete(f"/api/teams/{team_id}")
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.delete(f"/api/teams/{team_id}")
    assert resp.status_code == 200
    resp = client.get(f"/api/teams/{team_id}")
    assert resp.status_code == 404


def test_gc229_set_team_active_toggle(client_and_conn):
    client, _conn = client_and_conn
    team_id = _create_team(client)

    _login(client, "manager1", "managerpass")
    resp = client.patch(f"/api/teams/{team_id}/active", json={"is_active": False})
    assert resp.status_code == 200
    resp = client.get(f"/api/teams/{team_id}")
    assert resp.json()["is_active"] is False

    resp = client.patch(f"/api/teams/{team_id}/active", json={"is_active": True})
    assert resp.status_code == 200
    assert client.get(f"/api/teams/{team_id}").json()["is_active"] is True

    _login(client, "viewer1", "viewerpass")
    resp = client.patch(f"/api/teams/{team_id}/active", json={"is_active": False})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.patch("/api/teams/999999/active", json={"is_active": False})
    assert resp.status_code == 404


def test_gc230_request_team_assignment_unknown_team_404(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client)

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": 999999},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "team_not_found"


def test_gc231_team_leader_defaults_to_requester_and_self_approval_blocked(
    client_and_conn,
):
    client, conn = client_and_conn
    team_id = _create_team(client, "QA팀")
    _brief_id, task_id = _make_brief_and_task(client, "QA 업무")
    manager1_id = _user_id(conn, "manager1")

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    assignment_id = resp.json()["assignment_id"]

    resp = client.get(f"/api/tasks/{task_id}/assignments")
    row = next(r for r in resp.json() if r["id"] == assignment_id)
    assert row["resource_type"] == "team"
    assert row["resource_name"] == "QA팀"
    assert row["team_leader_id"] == manager1_id

    # 팀 리더(=기본값인 요청자 본인)는 self-approval 방지에 걸린다.
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "confirmed"


def test_gc232_team_leader_changeable_only_while_requested(client_and_conn):
    client, conn = client_and_conn
    team_id = _create_team(client, "디자인팀")
    _brief_id, task_id = _make_brief_and_task(client, "디자인 업무")
    admin1_id = _user_id(conn, "admin1")

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    assignment_id = resp.json()["assignment_id"]

    resp = client.patch(
        f"/api/assignments/{assignment_id}/team-leader",
        json={"team_leader_id": admin1_id},
    )
    assert resp.status_code == 200, resp.text
    resp = client.get(f"/api/tasks/{task_id}/assignments")
    row = next(r for r in resp.json() if r["id"] == assignment_id)
    assert row["team_leader_id"] == admin1_id

    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["status"] == "confirmed"

    # confirmed 이후엔 결재라인과 어긋나므로 더 이상 바꿀 수 없다.
    resp = client.patch(
        f"/api/assignments/{assignment_id}/team-leader",
        json={"team_leader_id": admin1_id},
    )
    assert resp.status_code == 400
    assert "requested" in resp.json()["detail"]


def test_gc233_team_member_and_leader_are_deliverable_participants(client_and_conn):
    client, conn = client_and_conn
    team_id = _create_team(client, "백엔드팀")
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 업무")

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool", json={"name": "member1의 사람", "role": "백엔드 업무"}
    )
    human_id = resp.json()["id"]
    member1_id = _user_id(conn, "member1")
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "human", "member_id": human_id},
    )
    assert resp.status_code == 201

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "team", "resource_id": team_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.json()["assignment"]["status"] == "confirmed"

    # 팀의 사람 멤버(member1)는 결과물을 쓸 수 있다("team_member").
    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "팀 멤버 초안"}
    )
    assert resp.status_code == 201, resp.text

    # 팀 리더(기본값 manager1)도 결과물을 쓸 수 있다("team_leader").
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "팀 리더 작성"}
    )
    assert resp.status_code == 201, resp.text

    # 팀과 무관한 사람은 여전히 403.
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "outsider2", "password": "outsiderpass", "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    _login(client, "outsider2", "outsiderpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables", json={"content": "무단 작성"}
    )
    assert resp.status_code == 403


def test_gc234_multiple_confirmed_assignments_coexist_without_explicit_replace(
    client_and_conn,
):
    """2026-10-06 정정 — "다중 confirmed 허용"은 팀뿐 아니라 개별(사람/사람,
    Agent/Agent) 배정에도 적용된다(사용자 결정, "다중 confirmed 허용의
    경우 개별 배정에도"). 같은 task·같은 resource_type(human)에 두 번째
    사람을 `replaces_assignment_id` 없이 confirmed해도 첫 번째가
    superseded되지 않고 둘 다 confirmed로 공존해야 한다."""
    client, conn = client_and_conn
    _brief_id, task_id = _make_brief_and_task(client, "페어 프로그래밍")

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool", json={"name": "후보A", "role": "페어 프로그래밍"}
    )
    human_a = resp.json()["id"]
    resp = client.post(
        "/api/human-pool", json={"name": "후보B", "role": "페어 프로그래밍"}
    )
    human_b = resp.json()["id"]

    def _confirm(resource_id):
        _login(client, "manager1", "managerpass")
        resp = client.post(
            f"/api/tasks/{task_id}/assignments",
            json={"resource_type": "human", "resource_id": resource_id},
        )
        approval_id = resp.json()["approval_id"]
        _login(client, "admin1", "adminpass")
        resp = client.post(
            f"/api/approvals/{approval_id}/decide", json={"approve": True}
        )
        assert resp.json()["assignment"]["status"] == "confirmed"

    _confirm(human_a)
    _confirm(human_b)

    rows = conn.execute(
        "SELECT resource_id, status FROM assignments WHERE task_id = ?", (task_id,)
    ).fetchall()
    by_resource = {r["resource_id"]: r["status"] for r in rows}
    assert by_resource[human_a] == "confirmed"
    assert by_resource[human_b] == "confirmed"
