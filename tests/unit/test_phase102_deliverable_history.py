"""결과물 버전 이력(2026-11-23, "P1 진행해줘" — P1-6)."""

from __future__ import annotations

from hair_platform import data_admin, deliverables


def _login(client, username, password):
    assert client.post("/login", json={"username": username, "password": password}).status_code == 200


def _task(conn):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '실적 보고')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '실적 보고서 작성자')", (brief,)).lastrowid
    conn.commit()
    return task


def _deliverable(conn, task, author_type, author_id, content, status="pending_review"):
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, ?, ?, ?, ?)",
        (task, author_type, author_id, content, status),
    ).lastrowid
    conn.commit()
    return did


def test_versions_group_by_task_and_author_and_edits_keep_old_content(client_and_conn):
    client, conn = client_and_conn
    task = _task(conn)
    agent = conn.execute("SELECT id FROM agent_pool WHERE role_name = '실적 보고서 작성자'").fetchone()[0]
    v1 = _deliverable(conn, task, "agent", agent, "매출 55억, 영업이익 13.8% 미달", "rejected")
    v2 = _deliverable(conn, task, "agent", agent, "매출 55억, 영업이익 11.4% 미달")
    other = _deliverable(conn, task, "agent", agent + 1, "다른 Agent 결과물")

    _login(client, "admin1", "adminpass")
    assert client.patch(f"/api/deliverables/{v2}", json={"content": "매출 55억(목표 대비 105.8%), 영업이익 11.4% 미달"}).status_code == 200
    h = client.get(f"/api/deliverables/{v2}/history").json()
    assert [v["id"] for v in h["versions"]] == [v1, v2]
    assert [v["version"] for v in h["versions"]] == [1, 2] and h["versions"][1]["is_current"]
    assert h["versions"][0]["status"] == "rejected"
    assert other not in [v["id"] for v in h["versions"]]
    assert len(h["edits"]) == 1 and h["edits"][0]["content"] == "매출 55억, 영업이익 11.4% 미달"
    assert h["edits"][0]["edited_by"] == "admin1"
    assert h["current_content"].startswith("매출 55억(목표 대비 105.8%)")
    # 같은 내용으로 저장하면 기록을 늘리지 않는다.
    client.patch(f"/api/deliverables/{v2}", json={"content": h["current_content"]})
    assert len(client.get(f"/api/deliverables/{v2}/history").json()["edits"]) == 1


def test_history_permission_matches_view(client_and_conn):
    client, conn = client_and_conn
    task = _task(conn)
    did = _deliverable(conn, task, "agent", 1, "x")
    _login(client, "member1", "memberpass")
    assert client.get(f"/api/deliverables/{did}/history").status_code == 403
    _login(client, "manager1", "managerpass")
    assert client.get(f"/api/deliverables/{did}/history").status_code == 200
    assert client.get("/api/deliverables/999999/history").status_code == 404


def test_deleting_removes_revisions(client_and_conn):
    client, conn = client_and_conn
    task = _task(conn)
    did = _deliverable(conn, task, "agent", 1, "원본")
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    deliverables.update_deliverable_content(conn, did, admin, "admin", "고친 본")
    assert conn.execute("SELECT COUNT(*) FROM deliverable_revisions WHERE deliverable_id = ?", (did,)).fetchone()[0] == 1
    deliverables.delete_deliverable(conn, did, admin, "admin")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM deliverable_revisions WHERE deliverable_id = ?", (did,)).fetchone()[0] == 0
    assert "deliverable_revisions" in data_admin._FACTORY_RESET_WIPE_TABLES
