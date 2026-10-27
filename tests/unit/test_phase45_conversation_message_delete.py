"""Phase 45 — 대화 내 단위 메시지 삭제, 사용자 요청(2026-10-14, "단위
메시지 삭제 기능 구현").

`conversations.delete_conversation()`(생성자 전용, phase34)과 별개로
메시지 한 건만 지울 때는 작성자 개념이 둘로 갈린다 — 사람 메시지는
사람이 직접 썼지만, AI/Agent 응답은 사람이 **트리거**했을 뿐 직접 쓴
게 아니다. 사용자 결정: 본인이 보낸 사람 메시지는 본인이, 그 외 모든
메시지(AI/Agent 응답 포함)는 이미 대화 전체 삭제 권한을 가진 생성자
(`created_by`)가 지울 수 있다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _insert_agent_message(conn, conversation_id, content="에이전트 응답"):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("테스트 에이전트",),
    )
    agent_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content) VALUES (?, 'agent', ?, ?)",
        (conversation_id, agent_id, content),
    )
    conn.commit()
    return cur.lastrowid


def test_gc_author_can_delete_own_message(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "안녕"}
    ).json()["id"]

    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": msg_id, "deleted": True}

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert all(m["id"] != msg_id for m in messages)


def test_gc_non_author_non_creator_cannot_delete_others_human_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "member1의 메시지"}
    ).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={
            "participant_type": "human",
            "participant_id": _user_id(conn, "manager1"),
        },
    )

    _login(client, "manager1", "managerpass")
    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 403, resp.text

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert any(m["id"] == msg_id for m in messages)


def test_gc_creator_can_delete_others_human_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={
            "participant_type": "human",
            "participant_id": _user_id(conn, "manager1"),
        },
    )

    _login(client, "manager1", "managerpass")
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "manager1의 메시지"}
    ).json()["id"]

    _login(client, "member1", "memberpass")  # member1 == 생성자(created_by)
    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 200, resp.text

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert all(m["id"] != msg_id for m in messages)


def test_gc_creator_can_delete_agent_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(conn, conv_id)

    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 200, resp.text

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert all(m["id"] != msg_id for m in messages)


def test_gc_non_creator_participant_cannot_delete_agent_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(conn, conv_id)
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={
            "participant_type": "human",
            "participant_id": _user_id(conn, "manager1"),
        },
    )

    _login(client, "manager1", "managerpass")  # 참가자지만 생성자가 아님
    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 403, resp.text


def test_gc_non_participant_cannot_delete_message(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "안녕"}
    ).json()["id"]

    _login(client, "viewer1", "viewerpass")  # 초대 안 된 사람
    resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
    assert resp.status_code == 403, resp.text


def test_gc_delete_nonexistent_message_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    resp = client.delete(f"/api/conversations/{conv_id}/messages/999999")
    assert resp.status_code == 404, resp.text


def _user_id(conn, username):
    return conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()[0]
