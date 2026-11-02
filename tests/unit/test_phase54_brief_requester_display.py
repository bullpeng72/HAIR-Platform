"""Phase 54 — 업무 요청 목록에 요청자 표시(사용자 요청, 2026-11-02, "업무
요청 목록에 요청자를 명시해줘").

`GET /api/briefs`는 원래도 `submitted_by`(숫자 user id)를 내려줬지만
화면에 표시할 이름이 없어 "업무 요청" 탭 목록에 요청자가 아예 안
보였다. `users`를 조인해 `submitted_by_username`도 같이 내려주고,
`webui.py`의 목록 표에 "요청자" 열을 추가했다(웹 UI 변경은 브라우저
없이 검증할 수 없으므로 소스에 마커가 있는지만 확인 — 나머지
테스트는 백엔드 응답 형태를 확인한다)."""

from __future__ import annotations

from hair_platform.webui import INDEX_HTML


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc_list_briefs_includes_submitted_by_username(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    assert resp.status_code == 201, resp.text
    brief_id = resp.json()["id"]

    rows = client.get("/api/briefs").json()
    row = next(r for r in rows if r["id"] == brief_id)
    assert row["submitted_by_username"] == "manager1"
    assert row["submitted_by"] == conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]


def test_gc_list_briefs_username_falls_back_to_none_for_deleted_user(client_and_conn):
    """계정이 지워져도(FK 제약상 실제로는 일어나지 않지만, LEFT JOIN이라
    혹시 `submitted_by`가 더 이상 `users`를 가리키지 못하는 상황이 와도)
    목록 조회 자체는 깨지지 않아야 한다 — `None`으로 내려온다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text) VALUES (?, ?)",
        (admin_id, "고아 submitted_by 테스트"),
    )
    conn.commit()
    # FK가 전역으로 켜져 있어(`db.py`) 존재하지 않는 user id를 그냥
    # UPDATE할 수 없다 — 이 시나리오(고아 참조)를 인위적으로 만들
    # 때만 잠깐 끈다(다른 곳의 같은 패턴과 동일).
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "UPDATE briefs SET submitted_by = 999999 WHERE raw_text = ?",
        ("고아 submitted_by 테스트",),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")

    _login(_client, "admin1", "adminpass")
    rows = _client.get("/api/briefs").json()
    row = next(r for r in rows if r["raw_text"] == "고아 submitted_by 테스트")
    assert row["submitted_by_username"] is None


def test_gc_briefs_list_html_renders_requester_column():
    assert "submitted_by_username" in INDEX_HTML
    assert "요청자" in INDEX_HTML


def test_gc_member_only_sees_own_submitted_briefs_not_others(client_and_conn):
    """사용자 보고(2026-11-02, "admin이 요청한 업무가 모든 멤버에게
    보이고 있어") 재현 + 수정 확인 — member1은 admin1이 제출한, 자신과
    무관한 브리프를 봐선 안 된다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_resp = client.post("/api/briefs", json={"raw_text": "admin이 제출한 브리프"})
    assert admin_resp.status_code == 201, admin_resp.text
    admin_brief_id = admin_resp.json()["id"]

    _login(client, "member1", "memberpass")
    member_resp = client.post("/api/briefs", json={"raw_text": "member1이 제출한 브리프"})
    assert member_resp.status_code == 201, member_resp.text
    member_brief_id = member_resp.json()["id"]

    rows = client.get("/api/briefs").json()
    ids = {r["id"] for r in rows}
    assert member_brief_id in ids
    assert admin_brief_id not in ids


def test_gc_member_sees_brief_via_confirmed_assignment_even_if_not_submitter(
    client_and_conn,
):
    """제출자가 아니어도, 그 브리프 아래 Task에 확정 배정된 당사자라면
    봐야 한다 — "내 업무"(`list_my_work`)와 같은 관계 판정 기준."""
    client, conn = client_and_conn
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]

    _login(client, "manager1", "managerpass")
    brief_resp = client.post("/api/briefs", json={"raw_text": "매니저가 제출한 브리프"})
    brief_id = brief_resp.json()["id"]
    task_resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": []}]},
    )
    task_id = task_resp.json()["tasks"][0]["id"]
    hp_resp = client.post(
        "/api/human-pool",
        json={"name": "member1", "role": "백엔드 개발자", "skill_tags": [], "user_id": member1_id},
    )
    resource_id = hp_resp.json()["id"]
    assign_resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": resource_id},
    )
    assert assign_resp.status_code == 201, assign_resp.text
    approval_id = assign_resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    decide_resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert decide_resp.status_code == 200, decide_resp.text

    _login(client, "member1", "memberpass")
    rows = client.get("/api/briefs").json()
    assert brief_id in {r["id"] for r in rows}


def test_gc_admin_and_manager_see_every_brief(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    member_brief_id = client.post(
        "/api/briefs", json={"raw_text": "member1 브리프"}
    ).json()["id"]

    for username, password in (("admin1", "adminpass"), ("manager1", "managerpass")):
        _login(client, username, password)
        rows = client.get("/api/briefs").json()
        assert member_brief_id in {r["id"] for r in rows}, username


def test_gc_viewer_with_no_relation_sees_no_briefs(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    client.post("/api/briefs", json={"raw_text": "admin 브리프"})
    _login(client, "manager1", "managerpass")
    client.post("/api/briefs", json={"raw_text": "manager 브리프"})

    _login(client, "viewer1", "viewerpass")
    rows = client.get("/api/briefs").json()
    assert rows == []
