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


# ---------- 2026-11-19, 사용자 보고 "대화 삭제 시 500 Error 발생" ----------


def test_gc_delete_conversation_with_knowledge_chunks_does_not_500(client_and_conn):
    """`conversation_knowledge_chunks.source_message_id`가 메시지를 FK로
    참조하는데, `_cascade_delete_conversation()`이 메시지를 먼저 지우면
    `sqlite3.IntegrityError: FOREIGN KEY constraint failed`로 500이
    났다 — 첨부파일을 올려 색인된 대화를 삭제하면 실제로 재현됨."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "첨부방"}).json()["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"content": "자료 첨부"})
    # 메시지 id는 목록 조회로 확정한다(응답 shape이 job 목록이라).
    message_row = conn.execute(
        "SELECT id FROM conversation_messages WHERE conversation_id = ? "
        "ORDER BY id LIMIT 1",
        (conv_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO conversation_knowledge_chunks (conversation_id, "
        "source_message_id, filename, chunk_index, text, embedding, "
        "embedding_provider, embedding_model) VALUES (?, ?, 'a.txt', 0, "
        "'내용', '[0.1]', 'openai', 'text-embedding-3-small')",
        (conv_id, message_row["id"]),
    )
    conn.commit()

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conv_id,),
    ).fetchone()["c"] == 0


def test_gc_delete_conversation_detaches_source_pipeline_run_instead_of_blocking(
    client_and_conn,
):
    """`pipeline_runs.source_conversation_id`도 같은 이유로 대화 삭제를
    막을 수 있다 — 파이프라인 run 자체(산출물·감사기록이 있는 실제
    업무 이력)는 지우지 않고 참조만 끊는다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "파이프라인 출처방"}).json()[
        "id"
    ]
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '역할', '[]')",
        (brief_id,),
    )
    task_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name) VALUES ('테스트 템플릿')"
    )
    template_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, "
        "source_conversation_id) VALUES (?, ?, 1, ?)",
        (template_id, task_id, conv_id),
    )
    run_id = cur.lastrowid
    conn.commit()

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run is not None  # run 자체는 보존됨
    assert run["source_conversation_id"] is None  # 참조만 끊김


def test_gc_delete_message_with_knowledge_chunk_does_not_500(client_and_conn):
    """`delete_message()`도 같은 FK 문제의 쌍둥이 버그였다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "메시지삭제방"}).json()[
        "id"
    ]
    client.post(f"/api/conversations/{conv_id}/messages", json={"content": "자료"})
    message_row = conn.execute(
        "SELECT id FROM conversation_messages WHERE conversation_id = ? "
        "ORDER BY id LIMIT 1",
        (conv_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO conversation_knowledge_chunks (conversation_id, "
        "source_message_id, filename, chunk_index, text, embedding, "
        "embedding_provider, embedding_model) VALUES (?, ?, 'a.txt', 0, "
        "'내용', '[0.1]', 'openai', 'text-embedding-3-small')",
        (conv_id, message_row["id"]),
    )
    conn.commit()

    resp = client.delete(f"/api/conversations/{conv_id}/messages/{message_row['id']}")
    assert resp.status_code == 200, resp.text
