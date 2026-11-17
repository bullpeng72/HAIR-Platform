"""Phase 72 — 생성된 결과물(대화 메시지/Task 산출물) 내용 직접 수정,
사용자 요청(2026-11-16, "각 에이전트 및 스킬에 포함된 프레젠테이션
생성하는 스킬들을 확인해 개선점을 찾아줘(예: 폰트 색이 배경색과 같아
보이지 않는 경우 발생 등), PIP로 배포 가능한 환경에서 생성된 HTML을
수정할 수 있는 기능 추가 제안해줘" → "1단계부터 구현해줘" → "다음 개선
진행해줘" × 2 — 2단계는 순수 프런트엔드(CodeMirror CDN)라 이 파일엔
해당 테스트가 없다, 3단계가 이 파일 하단에 추가됨).

1단계: 수동 편집(PATCH) — 권한 경계는 의도적으로 기존 삭제 기능과
완전히 동일하게 재사용한다 — "지울 수 있는 사람이면 고칠 수도 있어야
자연스럽다"는 설계 결정(`conversations.edit_message()`/`deliverables.
update_deliverable_content()` docstring 참고). 그래서 이 파일의 권한
테스트는 `test_phase45_conversation_message_delete.py`/`test_phase69_
deliverable_gallery.py`의 대응 삭제 테스트와 1:1로 짝을 맞춘다(같은
시나리오, delete 대신 patch).

3단계: "AI에게 다시 요청"(suggest-fix) — 자연어 피드백으로 LLM 수정안을
받는다. `assist()`/`assist_task()`와 같은 설계(결과 미저장, 비용 발생
RBAC 동일) — 비용/네트워크 발생 방지를 위해 `hair_platform.llm.call_llm`
만 patch한다(`test_phase59_brief_decompose_llm.py`와 같은 원칙). 권한
경계는 1단계(PATCH)와 완전히 동일 — "수정안을 받아도 저장할 수 없는
사람은 요청할 이유가 없다"는 설계."""

from __future__ import annotations

from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _user_id(conn, username):
    return conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()[0]


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


# ---------- PATCH /api/conversations/{id}/messages/{id} ----------


def test_author_can_edit_own_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "안녕"}
    ).json()["id"]

    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/{msg_id}",
        json={"content": "고친 내용"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "고친 내용"

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert next(m for m in messages if m["id"] == msg_id)["content"] == "고친 내용"

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'conversation_message_edited' "
        "AND subject_id = ?",
        (msg_id,),
    ).fetchone()
    assert audit_row is not None


def test_non_author_non_creator_cannot_edit_others_human_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "member1의 메시지"}
    ).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": _user_id(conn, "manager1")},
    )

    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/{msg_id}",
        json={"content": "남의 메시지 고치기 시도"},
    )
    assert resp.status_code == 403, resp.text

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert next(m for m in messages if m["id"] == msg_id)["content"] == "member1의 메시지"


def test_creator_can_edit_agent_message(client_and_conn):
    """이번 기능의 핵심 시나리오 — 프레젠테이션 자료 작성자 같은 Agent가
    만든 HTML에 색 대비 버그가 있을 때, 그 Agent를 호출한(대화 생성자인)
    사람이 직접 고칠 수 있어야 한다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(
        conn, conv_id, content="<style>.t{color:#fff;background:#fff}</style>"
    )

    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/{msg_id}",
        json={"content": "<style>.t{color:#000;background:#fff}</style>"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "<style>.t{color:#000;background:#fff}</style>"


def test_non_creator_participant_cannot_edit_agent_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(conn, conv_id)
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": _user_id(conn, "manager1")},
    )

    _login(client, "manager1", "managerpass")  # 참가자지만 생성자가 아님
    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": "수정 시도"}
    )
    assert resp.status_code == 403, resp.text


def test_non_participant_cannot_edit_message(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "안녕"}
    ).json()["id"]

    _login(client, "viewer1", "viewerpass")  # 초대 안 된 사람
    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": "수정 시도"}
    )
    assert resp.status_code == 403, resp.text


def test_edit_nonexistent_message_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    resp = client.patch(
        f"/api/conversations/{conv_id}/messages/999999", json={"content": "x"}
    )
    assert resp.status_code == 404, resp.text


# ---------- PATCH /api/deliverables/{id} ----------


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


def _confirm_assignment(client, task_id, resource_type, resource_id, requester):
    _login(client, requester[0], requester[1])
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": resource_type, "resource_id": resource_id},
    )
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    resp = client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text


def _setup_member1_task(client, conn, task_name="백엔드 개발자"):
    brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id


def _create_draft_deliverable(client, task_id, content="초안 내용"):
    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": content, "content_type": "html"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_member_can_edit_own_draft_deliverable(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.patch(
        f"/api/deliverables/{deliverable_id}", json={"content": "고친 HTML"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "고친 HTML"

    row = conn.execute(
        "SELECT content FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    assert row["content"] == "고친 HTML"

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'deliverable_content_edited' "
        "AND subject_id = ?",
        (deliverable_id,),
    ).fetchone()
    assert audit_row is not None


def test_non_author_member_cannot_edit_deliverable(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "viewer1", "viewerpass")
    resp = client.patch(
        f"/api/deliverables/{deliverable_id}", json={"content": "남의 산출물 고치기"}
    )
    assert resp.status_code == 403, resp.text

    row = conn.execute(
        "SELECT content FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    assert row["content"] == "초안 내용"


def test_author_cannot_edit_deliverable_after_submit(client_and_conn):
    """제출(pending_review) 후에는 본인도 못 고친다 — delete와 같은
    경계(결재 이력은 draft일 때만 본인이 손댈 수 있다)."""
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text

    resp = client.patch(
        f"/api/deliverables/{deliverable_id}", json={"content": "제출 후 수정 시도"}
    )
    assert resp.status_code == 403, resp.text


def test_admin_can_edit_deliverable_any_status(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text

    _login(client, "admin1", "adminpass")
    resp = client.patch(
        f"/api/deliverables/{deliverable_id}", json={"content": "admin이 고친 HTML"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "admin이 고친 HTML"


def test_edit_nonexistent_deliverable_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.patch("/api/deliverables/999999", json={"content": "x"})
    assert resp.status_code == 404, resp.text


# ---------- 3단계 POST /api/conversations/{id}/messages/{id}/suggest-fix ----------


def test_creator_can_suggest_fix_for_agent_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(
        conn, conv_id, content="<style>.t{color:#fff;background:#fff}</style>"
    )

    with patch(
        "hair_platform.llm.call_llm",
        return_value="<style>.t{color:#000;background:#fff}</style>",
    ) as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages/{msg_id}/suggest-fix",
            json={"feedback": "이 부분 색이 안 보여"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"suggestion": "<style>.t{color:#000;background:#fff}</style>"}
    mock_call.assert_called_once()

    # 결과는 저장되지 않는다 — 원본 메시지는 그대로.
    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert next(m for m in messages if m["id"] == msg_id)["content"] == (
        "<style>.t{color:#fff;background:#fff}</style>"
    )


def test_suggest_fix_uses_html_instruction_only_for_full_html_document(client_and_conn):
    """사용자 요청(2026-11-17, "모든 대화 결과물에서도 수정이
    가능하도록") 이후 회귀 가드 — 일반 텍스트 메시지의 "AI에게 요청"이
    엉뚱하게 "완전한 HTML 문서로 작성하라"는 지시를 받지 않는지,
    완전한 HTML 문서 메시지는 여전히 그 지시를 받는지 둘 다 확인한다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    text_msg_id = _insert_agent_message(conn, conv_id, content="일반 텍스트 응답입니다")
    with patch(
        "hair_platform.llm.call_llm", return_value="수정된 텍스트"
    ) as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages/{text_msg_id}/suggest-fix",
            json={"feedback": "더 짧게 줄여줘"},
        )
    assert resp.status_code == 200, resp.text
    text_system_prompt = mock_call.call_args.args[2]
    assert "완전한 HTML 문서" not in text_system_prompt

    html_msg_id = _insert_agent_message(
        conn, conv_id, content="<!DOCTYPE html><html><body>hi</body></html>"
    )
    with patch(
        "hair_platform.llm.call_llm", return_value="<!DOCTYPE html><html><body>bye</body></html>"
    ) as mock_call2:
        resp2 = client.post(
            f"/api/conversations/{conv_id}/messages/{html_msg_id}/suggest-fix",
            json={"feedback": "색이 안 보여"},
        )
    assert resp2.status_code == 200, resp2.text
    html_system_prompt = mock_call2.call_args.args[2]
    assert "완전한 HTML 문서" in html_system_prompt


def test_non_creator_participant_cannot_suggest_fix_for_agent_message(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(conn, conv_id)
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": _user_id(conn, "manager1")},
    )

    _login(client, "manager1", "managerpass")  # 참가자지만 생성자가 아님
    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages/{msg_id}/suggest-fix",
            json={"feedback": "고쳐줘"},
        )
    assert resp.status_code == 403, resp.text
    mock_call.assert_not_called()


def test_member_cannot_choose_provider_for_message_suggest_fix(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    msg_id = _insert_agent_message(conn, conv_id)

    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages/{msg_id}/suggest-fix",
            json={"feedback": "고쳐줘", "provider": "openai"},
        )
    assert resp.status_code == 400, resp.text
    mock_call.assert_not_called()


def test_suggest_fix_for_nonexistent_message_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]

    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages/999999/suggest-fix",
            json={"feedback": "x"},
        )
    assert resp.status_code == 404, resp.text
    mock_call.assert_not_called()


# ---------- 3단계 POST /api/deliverables/{id}/suggest-fix ----------


def test_member_can_suggest_fix_for_own_draft_deliverable(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(
        client, task_id, content="<style>.t{color:#fff;background:#fff}</style>"
    )

    _login(client, "member1", "memberpass")
    with patch(
        "hair_platform.llm.call_llm",
        return_value="<style>.t{color:#000;background:#fff}</style>",
    ) as mock_call:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/suggest-fix",
            json={"feedback": "이 부분 색이 안 보여"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"suggestion": "<style>.t{color:#000;background:#fff}</style>"}
    mock_call.assert_called_once()

    row = conn.execute(
        "SELECT content FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    assert row["content"] == "<style>.t{color:#fff;background:#fff}</style>"  # 미저장


def test_non_author_member_cannot_suggest_fix_for_deliverable(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "viewer1", "viewerpass")
    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/suggest-fix",
            json={"feedback": "고쳐줘"},
        )
    assert resp.status_code == 403, resp.text
    mock_call.assert_not_called()


def test_author_cannot_suggest_fix_for_deliverable_after_submit(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text

    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/suggest-fix",
            json={"feedback": "고쳐줘"},
        )
    assert resp.status_code == 403, resp.text
    mock_call.assert_not_called()


def test_admin_can_suggest_fix_for_deliverable_any_status(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/deliverables/{deliverable_id}/submit")
    assert resp.status_code == 200, resp.text

    _login(client, "admin1", "adminpass")
    with patch("hair_platform.llm.call_llm", return_value="고친 내용") as mock_call:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/suggest-fix",
            json={"feedback": "고쳐줘"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"suggestion": "고친 내용"}
    mock_call.assert_called_once()


def test_member_cannot_choose_provider_for_deliverable_suggest_fix(client_and_conn):
    client, conn = client_and_conn
    _, task_id = _setup_member1_task(client, conn)
    deliverable_id = _create_draft_deliverable(client, task_id)

    _login(client, "member1", "memberpass")
    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/suggest-fix",
            json={"feedback": "고쳐줘", "model": "gpt-4o"},
        )
    assert resp.status_code == 400, resp.text
    mock_call.assert_not_called()


def test_suggest_fix_for_nonexistent_deliverable_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            "/api/deliverables/999999/suggest-fix", json={"feedback": "x"}
        )
    assert resp.status_code == 404, resp.text
    mock_call.assert_not_called()
