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
