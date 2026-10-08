"""Phase 28 — ASANA_PARITY_DESIGN_PLAN.md Phase 3: F15 댓글 `@멘션` 트리거
(사용자 요청, 2026-10-08, "...기획 및 설계안을 제안해줘" → 승인 후 "Phase
1→5 순서로" 진행 지시, 열린 질문 1에 "(a) 댓글 트리거 결과는 항상 댓글로만
남기고 별도 '이걸 결과물로 제출' 버튼을 눌러야 deliverable이 되도록"로 답).

핵심 리스크(설계 문서 §5): "댓글 대화가 너무 쉽게 deliverable을 만들어버리면
F7 결재 체인을 우회하는 뒷문이 될 수 있다." 이 테스트의 1순위 목적은 그
뒷문이 실제로 막혀 있는지 확인하는 것 — 댓글 트리거로 Agent를 불러도
`deliverables` 테이블에는 아무 행도 생기지 않고, 응답은 `deliverable_
comments`에 `author_type='agent'`로만 남아야 한다.

`agent_runtime.execute_agent()`는 실제 LLM을 호출하므로(비용 발생)
`unittest.mock.patch`로 대체한다 — 다른 Phase 테스트 파일들(`test_
phase17_collab_workspace.py` 등)과 같은 관례."""

from __future__ import annotations

from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _member1_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[
        "id"
    ]


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


def _link_human_to_member1(client, conn, human_id):
    _login(client, "admin1", "adminpass")
    member1_id = _member1_id(conn)
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": member1_id})
    assert resp.status_code == 200, resp.text
    return member1_id


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


def _setup_human_assignee_task(client, conn, task_name="백엔드 개발자"):
    brief_id, task_id = _make_brief_and_task(client, task_name)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "member1의 사람", "role": task_name, "skill_tags": []},
    )
    human_id = resp.json()["id"]
    _link_human_to_member1(client, conn, human_id)
    _confirm_assignment(
        client, task_id, "human", human_id, requester=("manager1", "managerpass")
    )
    return brief_id, task_id, human_id


def _invite_new_agent(client, conn, task_id, role_name="호출용 Agent"):
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    agent_id = cur.lastrowid
    resp = client.post(
        f"/api/tasks/{task_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_id},
    )
    assert resp.status_code == 201, resp.text
    return agent_id


def test_gc_mention_triggers_agent_and_posts_comment_only(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    agent_id = _invite_new_agent(client, conn, task_id, "호출용 Agent")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물 본문", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="호출에 대한 답변입니다"
    ) as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@호출용 Agent 이거 어떻게 생각해?"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert len(body["triggered_jobs"]) == 1
        assert body["triggered_jobs"][0]["agent_id"] == agent_id
        mock_exec.assert_called_once()

    # 핵심 검증: deliverable이 새로 생기지 않았어야 한다(뒷문 차단).
    deliverables_count = conn.execute(
        "SELECT COUNT(*) AS c FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchone()["c"]
    assert deliverables_count == 1  # 사람이 작성한 것 하나뿐

    comments = conn.execute(
        "SELECT * FROM deliverable_comments WHERE deliverable_id = ? ORDER BY id",
        (deliverable_id,),
    ).fetchall()
    assert len(comments) == 2
    assert comments[0]["author_type"] == "human"
    assert comments[1]["author_type"] == "agent"
    assert comments[1]["author_id"] == agent_id
    assert comments[1]["content"] == "호출에 대한 답변입니다"

    job = conn.execute(
        "SELECT * FROM agent_execution_jobs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert job["status"] == "succeeded"
    assert job["source_comment_id"] == comments[0]["id"]
    assert job["deliverable_id"] is None  # 결과물과 연결되지 않는다(사용자 결정 a)


def test_gc_comment_without_mention_does_not_trigger(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _invite_new_agent(client, conn, task_id, "호출용 Agent")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    with patch("hair_platform.agent_runtime.execute_agent") as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "그냥 평범한 댓글입니다"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"] == []
        mock_exec.assert_not_called()


def test_gc_mention_of_uninvited_agent_does_not_trigger(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("초대 안 된 Agent",),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    with patch("hair_platform.agent_runtime.execute_agent") as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@초대 안 된 Agent 도와줘"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["triggered_jobs"] == []
        mock_exec.assert_not_called()


def test_gc_mention_skipped_when_another_job_already_running(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    agent_id = _invite_new_agent(client, conn, task_id, "호출용 Agent")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    conn.execute(
        "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
        "VALUES (?, ?, 'running')",
        (task_id, _member1_id(conn)),
    )
    conn.commit()

    with patch("hair_platform.agent_runtime.execute_agent") as mock_exec:
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@호출용 Agent 바쁠 때 테스트"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["triggered_jobs"] == []
        assert body["skipped_agent_ids"] == [agent_id]
        mock_exec.assert_not_called()

    # 댓글 자체는 트리거 성패와 무관하게 저장돼야 한다.
    comments = conn.execute(
        "SELECT COUNT(*) AS c FROM deliverable_comments WHERE deliverable_id = ?",
        (deliverable_id,),
    ).fetchone()["c"]
    assert comments == 1


def test_gc_failed_comment_triggered_execution_recorded_as_failed_job(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _invite_new_agent(client, conn, task_id, "호출용 Agent")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    with patch(
        "hair_platform.agent_runtime.execute_agent",
        side_effect=RuntimeError("boom"),
    ):
        resp = client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@호출용 Agent 실패 테스트"},
        )
        assert resp.status_code == 201, resp.text
        assert len(resp.json()["triggered_jobs"]) == 1

    job = conn.execute(
        "SELECT * FROM agent_execution_jobs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert job["status"] == "failed"
    assert job["error"]

    # 실패해도 deliverable_comments에 가짜 응답이 남으면 안 된다.
    comments_count = conn.execute(
        "SELECT COUNT(*) AS c FROM deliverable_comments WHERE deliverable_id = ?",
        (deliverable_id,),
    ).fetchone()["c"]
    assert comments_count == 1  # 사람 댓글 하나뿐


def test_gc_activity_includes_comment_triggered_execution(client_and_conn):
    client, conn = client_and_conn
    _brief_id, task_id, _human_id = _setup_human_assignee_task(client, conn)
    _invite_new_agent(client, conn, task_id, "호출용 Agent")

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/deliverables",
        json={"content": "결과물", "content_type": "text"},
    )
    deliverable_id = resp.json()["id"]

    with patch(
        "hair_platform.agent_runtime.execute_agent", return_value="응답"
    ):
        client.post(
            f"/api/deliverables/{deliverable_id}/comments",
            json={"content": "@호출용 Agent 활동 기록 확인"},
        )

    resp = client.get(f"/api/tasks/{task_id}/activity")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert "comment_triggered_execution_started" in actions
    # Agent의 응답도 deliverable_comment_created로 두 번(사람 1 + Agent 1) 남는다.
    assert actions.count("deliverable_comment_created") == 2
