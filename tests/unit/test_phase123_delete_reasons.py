"""업무 요청·할 일 삭제 500 수정(2026-11-25, 사용자 보고 "업무 요청에서 삭제 버튼 클릭시 500").

원인: 할 일에 연결된 대화(`conversations.linked_task_id`)·참여자·선행 관계·Crew 실행 기록을 삭제 연쇄가 몰라 FK 위반이
그대로 500으로 노출됐고, 실패 도중 이미 지운 행이 공유 연결에 남아 다음 요청의 커밋에 섞일 수 있었다."""

from __future__ import annotations

from hair_platform import data_admin


def _login_admin(client):
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200


def _brief_with_links(conn):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '삭제 시험', '[]')", (admin,)).lastrowid
    t1 = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '분석가', '[]')", (brief,)).lastrowid
    t2 = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '작성자', '[]')", (brief,)).lastrowid
    conv = conn.execute("INSERT INTO conversations (title, created_by, linked_task_id) VALUES ('업무 대화', ?, ?)",
                        (admin, t1)).lastrowid
    conn.execute("INSERT INTO task_participants (task_id, participant_type, participant_id, invited_by) VALUES (?, 'human', ?, ?)",
                 (t1, admin, admin))
    conn.execute("INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)", (t2, t1))
    conn.commit()
    return admin, brief, (t1, t2), conv


def test_brief_with_linked_conversation_and_participants_deletes_and_keeps_conversation(client_and_conn):
    client, conn = client_and_conn
    _admin, brief, (t1, _t2), conv = _brief_with_links(conn)
    _login_admin(client)
    r = client.delete(f"/api/briefs/{brief}")
    assert r.status_code == 200, r.text
    assert conn.execute("SELECT COUNT(*) FROM briefs WHERE id = ?", (brief,)).fetchone()[0] == 0
    row = conn.execute("SELECT linked_task_id FROM conversations WHERE id = ?", (conv,)).fetchone()
    assert row is not None and row[0] is None  # 대화는 남고 연결만 끊긴다
    assert conn.execute("SELECT COUNT(*) FROM task_participants WHERE task_id = ?", (t1,)).fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_task_delete_handles_dependencies_and_participants(client_and_conn):
    client, conn = client_and_conn
    _admin, brief, (t1, t2), _conv = _brief_with_links(conn)
    _login_admin(client)
    assert client.delete(f"/api/briefs/{brief}/tasks/{t1}").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM task_dependencies WHERE depends_on_task_id = ?", (t1,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE id = ?", (t2,)).fetchone()[0] == 1


def test_unknown_reference_is_reported_and_nothing_is_deleted(client_and_conn):
    """삭제 기능이 모르는 새 연결(테이블)이 있어도 500 대신 이유를 보이고 아무것도 지우지 않는다(원자적)."""
    client, conn = client_and_conn
    _admin, brief, (t1, _t2), _conv = _brief_with_links(conn)
    conn.execute("CREATE TABLE future_links (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id))")
    conn.execute("INSERT INTO future_links (task_id) VALUES (?)", (t1,))
    conn.commit()
    _login_admin(client)
    r = client.delete(f"/api/briefs/{brief}")
    assert r.status_code == 409 and "future_links" in r.json()["detail"] and "삭제할 수 없습니다" in r.json()["detail"]
    # 아무것도 지워지지 않았다 — 다음 요청의 커밋에도 섞이지 않는다
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM briefs WHERE id = ?", (brief,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE brief_id = ?", (brief,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM task_participants WHERE task_id = ?", (t1,)).fetchone()[0] == 1


def test_confirmed_assignment_message_names_task_and_resource(client_and_conn):
    client, conn = client_and_conn
    _admin, brief, (t1, _t2), _conv = _brief_with_links(conn)
    agent = conn.execute("SELECT id FROM agent_pool ORDER BY id LIMIT 1").fetchone()[0]
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, status, requested_by) "
                 "VALUES (?, 'agent', ?, 'confirmed', ?)", (t1, agent, admin))
    conn.commit()
    _login_admin(client)
    r = client.delete(f"/api/briefs/{brief}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "분석가" in detail and "AI 에이전트" in detail and "배정 취소" in detail


def test_pre_existing_orphans_do_not_block_unrelated_deletes(client_and_conn):
    """오래된 DB에 이미 고아 행이 있어도(삭제 전부터 있던 위반) 이번 삭제와 무관하면 막지 않는다."""
    _client, conn = client_and_conn
    admin, brief, _tasks, _conv = _brief_with_links(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO task_participants (task_id, participant_type, participant_id, invited_by) VALUES (999999, 'human', 1, 1)")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    data_admin.delete_brief(conn, admin, brief)
    assert conn.execute("SELECT COUNT(*) FROM briefs WHERE id = ?", (brief,)).fetchone()[0] == 0


def test_retention_purge_does_not_leave_write_transaction_open(tmp_path):
    """2026-11-25 — 지울 게 없을 때도 커밋해야 한다. 예전엔 `hair serve` 기동 뒤 쓰기 잠금이 계속 걸려 다른 프로세스가
    "database is locked"로 실패했다."""
    import sqlite3

    from hair_platform import db, retention

    path = str(tmp_path / "lock.db")
    conn = db.get_connection(path)
    db.init_schema(conn)
    retention.purge_expired(conn)
    assert conn.in_transaction is False
    other = sqlite3.connect(path, timeout=0.5)
    other.execute("BEGIN IMMEDIATE")  # 다른 프로세스가 쓰기 잠금을 얻을 수 있어야 한다
    other.rollback()
    other.close()
