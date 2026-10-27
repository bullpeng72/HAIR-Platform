"""Phase 34 — 대화 세션 삭제/나가기, 사용자 요청(2026-10-10, "대화
세션 삭제/나가기 기능을 구현해줘").

`conversations.py`의 대화 중심 작업공간은 초대를 참가자 누구나
할 수 있는 가벼운 레이어로 설계했다(`invite_participant()` 참고) —
삭제/나가기도 같은 신뢰 전제 위에서, 다만 "삭제"(되돌릴 수 없는 연쇄
삭제)만은 만든 사람(`created_by`)으로 좁힌다. "나가기"는 참가자 누구나
자기 자신만 뺄 수 있고, 마지막 사람이 나가면 유령 대화가 남지 않도록
대화 자체가 함께 삭제된다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _create_second_member(client, username="member2"):
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": username, "password": "member2pass", "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------- 삭제 ----------


def test_gc_creator_can_delete_conversation(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "지울 방"}).json()["id"]

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": conv_id, "deleted": True}

    assert client.get(f"/api/conversations/{conv_id}").status_code == 404


def test_gc_deleting_cascades_messages_participants_jobs(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"content": "안녕"})

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text

    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM conversation_messages WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()["c"]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM conversation_participants WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()["c"]
        == 0
    )


def test_gc_non_creator_participant_cannot_delete(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member2_id = _create_second_member(client)
    _login(client, "member1", "memberpass")
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member2_id},
    )

    _login(client, "member2", "member2pass")
    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 403, resp.text

    assert client.get(f"/api/conversations/{conv_id}").status_code == 200


def test_gc_non_participant_cannot_delete(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    _create_second_member(client)
    _login(client, "member2", "member2pass")
    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 403, resp.text


def test_gc_delete_nonexistent_conversation_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.delete("/api/conversations/999999")
    assert resp.status_code == 404, resp.text


def test_gc_creator_who_already_left_cannot_delete_from_outside(client_and_conn):
    """나간 뒤에는 "원 제작자"라는 이유로 바깥에서 남은 사람들의 대화를
    지울 수 없다 — `leave_conversation()`의 참가자 제거가 삭제 권한도
    함께 거둬간다."""
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member2_id = _create_second_member(client)
    _login(client, "member1", "memberpass")
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member2_id},
    )

    resp = client.post(f"/api/conversations/{conv_id}/leave")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "conversation_id": conv_id,
        "left": True,
        "conversation_deleted": False,
    }

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 403, resp.text
    assert (
        client.get(f"/api/conversations/{conv_id}").status_code == 403
    )  # 더 이상 참가자가 아님


# ---------- 나가기 ----------


def test_gc_leaving_with_others_remaining_keeps_conversation(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member2_id = _create_second_member(client)
    _login(client, "member1", "memberpass")
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member2_id},
    )

    _login(client, "member2", "member2pass")
    resp = client.post(f"/api/conversations/{conv_id}/leave")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "conversation_id": conv_id,
        "left": True,
        "conversation_deleted": False,
    }

    _login(client, "member1", "memberpass")
    participants = client.get(f"/api/conversations/{conv_id}/participants").json()
    assert [p["participant_id"] for p in participants] == [
        conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()["id"]
    ]


def test_gc_last_participant_leaving_deletes_conversation(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    resp = client.post(f"/api/conversations/{conv_id}/leave")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "conversation_id": conv_id,
        "left": True,
        "conversation_deleted": True,
    }

    assert client.get(f"/api/conversations/{conv_id}").status_code == 404


def test_gc_non_participant_cannot_leave(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    _create_second_member(client)
    _login(client, "member2", "member2pass")
    resp = client.post(f"/api/conversations/{conv_id}/leave")
    assert resp.status_code == 403, resp.text


def test_gc_leave_nonexistent_conversation_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.post("/api/conversations/999999/leave")
    assert resp.status_code == 404, resp.text
