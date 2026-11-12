"""Phase 5 — 통합 인박스(사용자 요청, 2026-09-28, "Phase 5(기업용 UI/UX)에
포함할 항목을 고르세요" → "통합 인박스" 선택) — "결재 대기·내 업무
응답 대기·낮은 Gate 점수 경고를 한 곳에"를
`GET /api/inbox`로 구현한다 — 새 권한 규칙 없이 기존 `approvals.
list_inbox()`/`deliverables.list_my_work()`가 이미 강제하는 접근 범위를
그대로 재사용한다(`inbox.py` 참고).
"""

from __future__ import annotations

from unittest.mock import patch

from tests.unit.test_phase17_collab_workspace import (
    _execute_agent_and_get_deliverable,
    _login,
    _make_brief_and_task,
    _setup_agent_requester_task,
)


def test_gc157_admin_sees_pending_approval_in_inbox(client_and_conn):
    """결재 대기 항목 — 기존 scope=inbox와 동일한 데이터가 통합
    인박스에도 나와야 한다."""
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    _brief_id, task_id = _make_brief_and_task(client, "인박스 업무")
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "인박스 후보", "role": "인박스 업무", "skill_tags": []},
    )
    human_id = resp.json()["id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    assert resp.status_code == 201

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    matches = [i for i in resp.json() if i["type"] == "approval_pending"]
    assert len(matches) == 1
    assert matches[0]["subject_type"] == "assignment"


def test_gc158_execution_failed_appears_for_requester(client_and_conn):
    """Agent 실행 실패 — "내 업무 응답 대기"에 해당하는 항목. 요청자(=이
    task의 Agent 요청자)의 인박스에 나와야 한다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("모의 API 장애")):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text

    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    matches = [
        i
        for i in resp.json()
        if i["type"] == "execution_failed" and i["task_id"] == task_id
    ]
    assert len(matches) == 1
    assert "모의 API 장애" in matches[0]["error"]


def test_gc159_low_gate_score_warning_disappears_after_decision(client_and_conn):
    """낮은 Gate 점수 경고 — pending_review인 동안만 나오고, 승인/반려
    되면(조치가 끝난 일이라) 인박스에서 빠져야 한다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    assert row["evaluation"] is not None
    has_failing = any(
        g.get("passed") is False for g in row["evaluation"]["gate_scores"].values()
    )
    assert has_failing, "이 픽스처는 최소한 하나의 Gate가 낮게 나온다고 가정한다"

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/inbox")
    matches = [
        i
        for i in resp.json()
        if i["type"] == "low_gate_score" and i["deliverable_id"] == deliverable_id
    ]
    assert len(matches) == 1

    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{row['approval_id']}/decide", json={"approve": True}
    )
    assert resp.status_code == 200, resp.text

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/inbox")
    matches = [
        i
        for i in resp.json()
        if i["type"] == "low_gate_score" and i["deliverable_id"] == deliverable_id
    ]
    assert matches == []


def test_gc160_member_without_admin_role_gets_no_approval_items(client_and_conn):
    """결재 대기 항목은 Admin/매니저 전용 — 팀원 인박스엔 절대 안
    나와야 한다(자기 my-work 기반 항목만 나올 수 있음)."""
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    assert [i for i in resp.json() if i["type"] == "approval_pending"] == []


# ---------- completed(결재 불필요 성공 완료) — 사용자 요청(2026-11-12,
# "업무를 요청하고, 협업공간에서 진행과정과 결과물 확인이 어려워") ----------


def test_gc161_completed_appears_for_requester_after_approval(client_and_conn):
    """결재가 필요 없는 성공 완료도 요청한 사람이 알 수 있어야 한다 —
    지금까지 승인되면 조용히 묻혔다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{row['approval_id']}/decide", json={"approve": True}
    )
    assert resp.status_code == 200, resp.text

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    matches = [
        i for i in resp.json() if i["type"] == "completed" and i["task_id"] == task_id
    ]
    assert len(matches) == 1


def test_gc162_completed_excluded_once_older_than_24_hours(client_and_conn):
    """번복될 일이 없는 "승인됨" 상태를 시간 조건 없이 두면 한 번 끝난
    업무가 영원히 인박스에 남는다 — 24시간이 지나면 "새로 끝난 것"이
    아니라고 보고 빠져야 한다."""
    client, conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    deliverable_id, row = _execute_agent_and_get_deliverable(client, task_id)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/approvals/{row['approval_id']}/decide", json={"approve": True}
    )
    assert resp.status_code == 200, resp.text

    conn.execute(
        "UPDATE deliverables SET created_at = datetime('now', '-25 hours') "
        "WHERE id = ?",
        (deliverable_id,),
    )
    conn.commit()

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/inbox")
    matches = [
        i for i in resp.json() if i["type"] == "completed" and i["task_id"] == task_id
    ]
    assert matches == []
