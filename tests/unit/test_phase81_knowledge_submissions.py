"""Phase 81 — 지식베이스 등록 체계 개편 1단계(백엔드).

사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
경우와 대화 및 업무배정을 통해 생성된 산출물 중 '지식베이스로 보내기'
및 admin 결재 후 등록되는 것으로 개선") — 경로 B "제출 → admin 결재 →
등록"(`knowledge_submissions.py`)을 검증한다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations, vectorstore


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _uid(conn, username):
    return conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[
        "id"
    ]


def _fake_embed(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [1.0, 0.0, 0.1])


def _member_message(conn, content, title="지식 제출방"):
    member_id = _uid(conn, "member1")
    conv_id = conversations.create_conversation(conn, member_id, title)["id"]
    msg = conversations.create_message(conn, conv_id, member_id, content)
    return conv_id, msg["id"]


def _submit(client, source_type, source_id, **extra):
    return client.post(
        "/api/knowledge-submissions",
        json={"source_type": source_type, "source_id": source_id, **extra},
    )


def _approval_id(conn, submission_id):
    return conn.execute(
        "SELECT id FROM approvals WHERE subject_type = 'knowledge_submission' "
        "AND subject_id = ?",
        (submission_id,),
    ).fetchone()["id"]


def _decide(client, approval_id, approve, reason=None):
    return client.post(
        f"/api/approvals/{approval_id}/decide",
        json={"approve": approve, "reason": reason},
    )


def test_member_submission_requires_admin_approval_then_registers(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(
        conn, "# HITL 개념\n\n사람이 업무 흐름 중간에서 승인·감독하는 구조."
    )

    _login(client, "member1", "memberpass")
    resp = _submit(client, "conversation_message", msg_id)
    assert resp.status_code == 201, resp.text
    sub = resp.json()
    assert sub["status"] == "pending"
    assert sub["title"] == "HITL 개념"  # 첫 제목 줄로 자동 채움
    assert sub["source_label"] == f"대화 '지식 제출방' · 메시지 #{msg_id}"
    # 결재 전에는 지식베이스에 아무것도 없다(임베딩 비용도 없음).
    assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()["c"] == 0

    approval_id = _approval_id(conn, sub["id"])

    # 매니저는 인박스에서 볼 수도, 결재할 수도 없다(admin 전용).
    _login(client, "manager1", "managerpass")
    inbox = client.get("/api/approvals?scope=inbox").json()
    assert all(a["subject_type"] != "knowledge_submission" for a in inbox)
    assert _decide(client, approval_id, True).status_code == 403

    _login(client, "admin1", "adminpass")
    inbox = client.get("/api/approvals?scope=inbox").json()
    item = next(a for a in inbox if a["id"] == approval_id)
    assert "HITL 개념" in item["resource_name"]
    assert "승인·감독" in item["content"]  # 결재 화면에서 등록될 본문을 바로 본다

    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = _decide(client, approval_id, True)  # BackgroundTasks는 응답 뒤 동기 실행
    assert resp.status_code == 200, resp.text

    row = conn.execute(
        "SELECT * FROM knowledge_submissions WHERE id = ?", (sub["id"],)
    ).fetchone()
    assert row["status"] == "registered"
    assert row["decided_by"] == _uid(conn, "admin1")
    doc = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (row["document_id"],)
    ).fetchone()
    assert doc["source_type"] == "submission"
    assert doc["submission_id"] == sub["id"]
    chunks = conn.execute(
        "SELECT text FROM knowledge_chunks WHERE source_document_id = ?", (doc["id"],)
    ).fetchall()
    assert chunks and "승인·감독" in chunks[0]["text"]


def test_snapshot_is_independent_of_later_source_edits(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "원래 내용입니다.")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()

    conn.execute(
        "UPDATE conversation_messages SET content = '나중에 바뀐 내용' WHERE id = ?",
        (msg_id,),
    )
    conn.commit()

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _decide(client, _approval_id(conn, sub["id"]), True)
    texts = [
        r["text"]
        for r in conn.execute(
            "SELECT kc.text FROM knowledge_chunks kc JOIN documents d "
            "ON d.id = kc.source_document_id WHERE d.submission_id = ?",
            (sub["id"],),
        )
    ]
    assert texts == ["원래 내용입니다."]


def test_html_message_is_reduced_to_body_text(client_and_conn):
    client, conn = client_and_conn
    html = (
        "```html\n<!DOCTYPE html><html><head><style>body{color:red}</style></head>"
        "<body><h1>HITL 가이드</h1><p>사람이 승인한다.</p><script>x()</script></body></html>\n```"
    )
    _conv_id, msg_id = _member_message(conn, html)
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()
    assert sub["content_type"] == "html"
    assert "HITL 가이드" in sub["content_snapshot"]
    assert "사람이 승인한다." in sub["content_snapshot"]
    for leaked in ("<", "color:red", "x()", "```"):
        assert leaked not in sub["content_snapshot"]


def test_admin_self_submission_registers_without_approval(client_and_conn):
    client, conn = client_and_conn
    admin_id = _uid(conn, "admin1")
    conv_id = conversations.create_conversation(conn, admin_id, "admin 방")["id"]
    msg = conversations.create_message(conn, conv_id, admin_id, "admin이 바로 등록")

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = _submit(client, "conversation_message", msg["id"])
    assert resp.status_code == 201, resp.text
    sub_id = resp.json()["id"]
    row = conn.execute(
        "SELECT status, decided_by FROM knowledge_submissions WHERE id = ?", (sub_id,)
    ).fetchone()
    assert row["status"] == "registered"
    assert row["decided_by"] == admin_id
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'knowledge_submission'"
        ).fetchone()["c"]
        == 0
    )


def test_reject_requires_reason_and_indexes_nothing(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "반려될 내용")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()
    approval_id = _approval_id(conn, sub["id"])

    _login(client, "admin1", "adminpass")
    assert _decide(client, approval_id, False).status_code == 400
    assert _decide(client, approval_id, False, "사내 기밀 포함").status_code == 200

    row = conn.execute(
        "SELECT status, reject_reason FROM knowledge_submissions WHERE id = ?", (sub["id"],)
    ).fetchone()
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "사내 기밀 포함"
    assert conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"] == 0


def test_duplicate_pending_blocked_and_resubmission_supersedes(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "첫 버전")
    _login(client, "member1", "memberpass")
    first = _submit(client, "conversation_message", msg_id).json()
    assert _submit(client, "conversation_message", msg_id).status_code == 409

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _decide(client, _approval_id(conn, first["id"]), True)
    old_doc_id = conn.execute(
        "SELECT document_id FROM knowledge_submissions WHERE id = ?", (first["id"],)
    ).fetchone()["document_id"]

    conn.execute(
        "UPDATE conversation_messages SET content = '두 번째 버전' WHERE id = ?", (msg_id,)
    )
    conn.commit()
    _login(client, "member1", "memberpass")
    second = _submit(client, "conversation_message", msg_id).json()
    assert second["supersedes_id"] == first["id"]

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _decide(client, _approval_id(conn, second["id"]), True)

    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM knowledge_submissions")
    }
    assert statuses == {first["id"]: "superseded", second["id"]: "registered"}
    assert (
        conn.execute("SELECT 1 FROM documents WHERE id = ?", (old_doc_id,)).fetchone()
        is None
    )
    texts = [r["text"] for r in conn.execute("SELECT text FROM knowledge_chunks")]
    assert texts == ["두 번째 버전"]


def test_deliverable_must_be_approved_and_code_needs_confirmation(client_and_conn):
    client, conn = client_and_conn
    conv_id, msg_id = _member_message(conn, "print('hi')")
    member_id = _uid(conn, "member1")
    deliverable_id = conversations.save_message_as_deliverable(
        conn, conv_id, msg_id, member_id
    )
    _login(client, "admin1", "adminpass")

    conn.execute("UPDATE deliverables SET status = 'draft' WHERE id = ?", (deliverable_id,))
    conn.commit()
    resp = _submit(client, "deliverable", deliverable_id)
    assert resp.status_code == 400
    assert "승인된 산출물" in resp.json()["detail"]

    conn.execute(
        "UPDATE deliverables SET status = 'approved', content_type = 'code' WHERE id = ?",
        (deliverable_id,),
    )
    conn.commit()
    assert _submit(client, "deliverable", deliverable_id).status_code == 400
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = _submit(client, "deliverable", deliverable_id, allow_code=True)
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_label"].startswith("업무 '")


def test_non_participant_and_viewer_cannot_submit(client_and_conn):
    client, conn = client_and_conn
    manager_id = _uid(conn, "manager1")
    conv_id = conversations.create_conversation(conn, manager_id, "매니저 방")["id"]
    msg = conversations.create_message(conn, conv_id, manager_id, "매니저만의 대화")

    _login(client, "member1", "memberpass")
    assert _submit(client, "conversation_message", msg["id"]).status_code == 403
    _login(client, "viewer1", "viewerpass")
    assert _submit(client, "conversation_message", msg["id"]).status_code == 403


def test_withdraw_by_submitter_only(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "회수할 내용")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()

    _login(client, "admin1", "adminpass")
    assert client.post(f"/api/knowledge-submissions/{sub['id']}/withdraw").status_code == 403

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/knowledge-submissions/{sub['id']}/withdraw")
    assert resp.status_code == 200
    assert resp.json()["status"] == "withdrawn"
    approval = conn.execute(
        "SELECT status FROM approvals WHERE id = ?", (_approval_id(conn, sub["id"]),)
    ).fetchone()
    assert approval["status"] == "skipped"
    # 회수 후엔 같은 원본을 다시 제출할 수 있다.
    assert _submit(client, "conversation_message", msg_id).status_code == 201


def test_embedding_failure_marks_failed_without_leftovers_then_retry(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "문단 하나\n\n" * 3 + "임베딩 실패 테스트")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=RuntimeError("ollama down")):
        _decide(client, _approval_id(conn, sub["id"]), True)
    row = conn.execute(
        "SELECT status, error FROM knowledge_submissions WHERE id = ?", (sub["id"],)
    ).fetchone()
    assert row["status"] == "failed"
    assert "ollama down" in row["error"]
    assert conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()["c"] == 0

    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        resp = client.post(f"/api/knowledge-submissions/{sub['id']}/retry")
    assert resp.status_code == 200, resp.text
    assert (
        conn.execute(
            "SELECT status FROM knowledge_submissions WHERE id = ?", (sub["id"],)
        ).fetchone()["status"]
        == "registered"
    )


def test_list_scope_and_citation_label(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "# 인용 테스트\n\nHITL 본문")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()

    # 매니저는 남의 제출 건을 못 본다.
    _login(client, "manager1", "managerpass")
    assert client.get("/api/knowledge-submissions").json() == []
    assert client.get(f"/api/knowledge-submissions/{sub['id']}").status_code == 404

    _login(client, "admin1", "adminpass")
    assert [s["id"] for s in client.get("/api/knowledge-submissions").json()] == [sub["id"]]
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _decide(client, _approval_id(conn, sub["id"]), True)
        hits = vectorstore.search(conn, "HITL", top_k=5, min_score=0.0)
    text = vectorstore.format_hits_with_sources(conn, hits)
    assert f"출처: 지식 '인용 테스트' — 대화 '지식 제출방' · 메시지 #{msg_id}" in text
    assert "승인" in text


def test_admin_deleting_registered_document_marks_submission_removed(client_and_conn):
    """지식 베이스 탭에서 제출로 등록된 문서를 지우면 FK 오류 없이 지워지고,
    제출 건은 'removed'가 되며 같은 원본을 다시 제출할 수 있다."""
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "지워질 지식")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _decide(client, _approval_id(conn, sub["id"]), True)
    doc_id = conn.execute(
        "SELECT document_id FROM knowledge_submissions WHERE id = ?", (sub["id"],)
    ).fetchone()["document_id"]
    docs = client.get("/api/documents").json()
    assert next(d for d in docs if d["id"] == doc_id)["source_type"] == "submission"

    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    row = conn.execute(
        "SELECT status, document_id FROM knowledge_submissions WHERE id = ?", (sub["id"],)
    ).fetchone()
    assert (row["status"], row["document_id"]) == ("removed", None)
    assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()["c"] == 0

    _login(client, "member1", "memberpass")
    resubmitted = _submit(client, "conversation_message", msg_id).json()
    assert resubmitted["supersedes_id"] is None


# ---------- 2단계(화면) 지원 API — 미리보기·배지 상태 ----------


def test_preview_returns_prepared_text_without_saving(client_and_conn):
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(
        conn, "```html\n<!DOCTYPE html><html><body><h1>정책</h1><p>본문</p></body></html>\n```"
    )
    _login(client, "member1", "memberpass")
    resp = client.post(
        "/api/knowledge-submissions/preview",
        json={"source_type": "conversation_message", "source_id": msg_id},
    )
    assert resp.status_code == 200, resp.text
    pv = resp.json()
    assert pv["title"] == "정책"
    assert "본문" in pv["content_snapshot"] and "<" not in pv["content_snapshot"]
    assert pv["requires_approval"] is True and pv["is_update"] is False
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM knowledge_submissions").fetchone()["c"] == 0
    )


def test_status_map_shows_latest_and_hides_others_reject_reason(client_and_conn):
    client, conn = client_and_conn
    conv_id, msg_id = _member_message(conn, "상태 확인용")
    other_msg = conversations.create_message(conn, conv_id, _uid(conn, "member1"), "미제출")
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()
    _login(client, "admin1", "adminpass")
    _decide(client, _approval_id(conn, sub["id"]), False, "근거 부족")

    url = (
        "/api/knowledge-submissions/status?source_type=conversation_message"
        f"&ids={msg_id},{other_msg['id']}"
    )
    _login(client, "member1", "memberpass")
    mine = client.get(url).json()
    assert mine[str(msg_id)]["status"] == "rejected"
    assert mine[str(msg_id)]["reject_reason"] == "근거 부족"
    assert str(other_msg["id"]) not in mine

    _login(client, "manager1", "managerpass")
    others = client.get(url).json()
    assert others[str(msg_id)]["status"] == "rejected"
    assert others[str(msg_id)]["reject_reason"] is None  # 남의 반려 사유는 안 보임


# ---------- 3단계 — 입구 정리(Agent 즉시 색인 삭제, 직접 등록 admin 전용) ----------


def test_agent_index_knowledge_endpoint_is_gone(client_and_conn):
    """결재 없이 Agent `last_output`을 색인하던 입구가 다시 생기면 안 된다."""
    client, conn = client_and_conn
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, last_output) "
        "VALUES ('기획자', '[]', 'active', '산출물')"
    ).lastrowid
    conn.commit()
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/index-knowledge", json={})
    assert resp.status_code in (404, 405)
    assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()["c"] == 0


def test_direct_registration_is_admin_only(client_and_conn):
    """경로 A(직접 등록·삭제)는 admin 전용 — manager는 조회만 한다."""
    client, conn = client_and_conn
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        _login(client, "manager1", "managerpass")
        assert client.post("/api/documents/text", json={"text": "매니저 메모"}).status_code == 403
        assert (
            client.post(
                "/api/documents", files={"file": ("a.md", b"# a", "text/markdown")}
            ).status_code
            == 403
        )
        assert client.get("/api/documents").status_code == 200  # 조회는 가능

        _login(client, "admin1", "adminpass")
        resp = client.post("/api/documents/text", json={"text": "admin 메모", "label": "메모"})
        assert resp.status_code == 201, resp.text
    doc_id = resp.json()["document_id"]
    chunk_id = conn.execute(
        "SELECT id FROM knowledge_chunks WHERE source_document_id = ?", (doc_id,)
    ).fetchone()["id"]

    _login(client, "manager1", "managerpass")
    assert client.delete(f"/api/knowledge-chunks/{chunk_id}").status_code == 403
    assert client.delete(f"/api/documents/{doc_id}").status_code == 403
    docs = client.get("/api/documents").json()
    assert next(d for d in docs if d["id"] == doc_id)["source_type"] == "direct"


def test_rbac_gaps_preview_retry_and_unauthenticated(client_and_conn):
    """rbac-permission-audit 절차로 찾은 빈칸 — 엔드포인트마다 "불허 최소
    role → 403/401"을 최소 1건씩 확보한다(허용 쪽은 위 테스트들이 검증)."""
    client, conn = client_and_conn
    _conv_id, msg_id = _member_message(conn, "RBAC 빈칸 확인")
    body = {"source_type": "conversation_message", "source_id": msg_id}

    # 미로그인 — get_current_user 계열 전부 401.
    client.cookies.clear()
    assert client.post("/api/knowledge-submissions", json=body).status_code == 401
    assert client.post("/api/knowledge-submissions/preview", json=body).status_code == 401
    assert client.get("/api/knowledge-submissions").status_code == 401
    assert (
        client.get(
            f"/api/knowledge-submissions/status?source_type=conversation_message&ids={msg_id}"
        ).status_code
        == 401
    )

    # preview도 열람 전용은 막힌다(submit과 같은 검증 공유).
    _login(client, "viewer1", "viewerpass")
    assert client.post("/api/knowledge-submissions/preview", json=body).status_code == 403

    # retry는 admin 전용 — 실패 건을 만들어 매니저로 시도.
    _login(client, "member1", "memberpass")
    sub = _submit(client, "conversation_message", msg_id).json()
    _login(client, "admin1", "adminpass")
    with patch("hair_platform.vectorstore.embed_text", side_effect=RuntimeError("down")):
        _decide(client, _approval_id(conn, sub["id"]), True)
    _login(client, "manager1", "managerpass")
    assert client.post(f"/api/knowledge-submissions/{sub['id']}/retry").status_code == 403
    assert (
        conn.execute(
            "SELECT status FROM knowledge_submissions WHERE id = ?", (sub["id"],)
        ).fetchone()["status"]
        == "failed"
    )
