"""신입·일반 사용자 관점 Phase A(2026-11-28) — "내 업무" API가 기한·예상 시간·할 일 상태를 돌려준다(예전엔 쿼리에서 읽고
응답에서 빠뜨려 "내 업무"의 기한 칸이 늘 "-"였다)."""

from __future__ import annotations


def test_my_work_returns_due_date_estimate_and_status(client_and_conn):
    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('본인', 'QA', '[]', ?)", (member,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (member,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name, due_date, estimate_hours, status) VALUES (?, 'QA', '2026-12-05', 6, 'in_progress')",
                     (bid,)).lastrowid
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                 (t, h, member))
    conn.commit()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    row = next(r for r in client.get("/api/my-work").json() if r["task_id"] == t)
    assert (row["due_date"], row["estimate_hours"], row["task_status"]) == ("2026-12-05", 6, "in_progress")


def test_task_detail_includes_request_text_and_who_asked(client_and_conn):
    """워크스페이스 요청 내용(2026-11-28) — 맡은 사람이 무엇을·누가 요청했는지 본다. 참가자 아닌 사람은 여전히 403."""
    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    manager = conn.execute("SELECT id FROM users WHERE username = 'manager1'").fetchone()[0]
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('본인', 'QA', '[]', ?)", (member,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '거래처 A사 견적 비교표를 만들어 주세요')", (manager,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '견적 비교표 작성')", (bid,)).lastrowid
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                 (t, h, manager))
    conn.commit()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    d = client.get(f"/api/tasks/{t}").json()
    assert d["brief_text"] == "거래처 A사 견적 비교표를 만들어 주세요"
    assert d["requested_by_name"] == "manager1" and d["assigned_by_name"] == "manager1"
    client.post("/login", json={"username": "viewer1", "password": "viewerpass"})
    assert client.get(f"/api/tasks/{t}").status_code == 403


def test_briefs_mine_and_limit_for_home(client_and_conn):
    """홈 "내가 낸 업무 요청"(2026-11-28) — admin도 자기 것만, limit만큼. 필터 없이 부르면 예전처럼 전체."""
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    for i in range(6):
        conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, ?)", (admin, f"관리자 요청 {i}"))
    conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '구성원 요청')", (member,))
    conn.commit()
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    mine = client.get("/api/briefs", params={"mine": "true", "limit": 4}).json()
    assert [b["raw_text"] for b in mine] == ["관리자 요청 5", "관리자 요청 4", "관리자 요청 3", "관리자 요청 2"]
    assert len(client.get("/api/briefs").json()) == 7  # 필터 없으면 전체(예전 동작)


def test_my_approval_requests_are_bounded_and_content_loads_on_demand(client_and_conn):
    """내가 요청한 건(2026-11-28) — 열린 건은 오래돼도 항상, 나머지는 최근 limit개 대상만, 한 대상의 단계는 함께. 본문은 목록에
    없고 /content로(요청자·admin·manager만)."""
    from hair_platform import approvals

    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (member,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'r')", (bid,)).lastrowid
    def deliverable(text):
        return conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) "
                            "VALUES (?, 'human', ?, ?, 'pending_review')", (t, member, text)).lastrowid
    old_open = deliverable("가장 오래된 열린 건")
    approvals.create_approval_chain(conn, member, "deliverable", old_open)
    for i in range(5):  # 결정이 끝난 건 5개
        d = deliverable(f"끝난 건 {i}")
        conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, step, status) VALUES (?, 'deliverable', ?, 1, 'approved')",
                     (member, d))
    conn.commit()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    rows = client.get("/api/approvals", params={"scope": "mine", "limit": 3}).json()
    subjects = {r["subject_id"] for r in rows}
    assert len(subjects) == 3 and old_open in subjects  # 열린 건 + 최근 결정 2개
    assert all("content" not in r for r in rows) and all(r["has_content"] for r in rows)
    aid = next(r["id"] for r in rows if r["subject_id"] == old_open)
    assert client.get(f"/api/approvals/{aid}/content").json()["content"] == "가장 오래된 열린 건"
    client.post("/login", json={"username": "viewer1", "password": "viewerpass"})
    assert client.get(f"/api/approvals/{aid}/content").status_code == 403
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.get(f"/api/approvals/{aid}/content").status_code == 200
