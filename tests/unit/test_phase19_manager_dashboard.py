"""Phase 5 — 매니저 대시보드(사용자 요청, 2026-09-28, "Phase 5(기업용
UI/UX)에 포함할 항목을 고르세요" → "매니저 대시보드" 선택).
`docs/HAIR_MASTER_PLAN.md` §6 "팀 Agent 평균 Gate 점수, 대기 승인 건수,
비용 추이를 한 화면에"를 `GET /api/dashboard`로 구현한다.

**"비용 추이" 실측 확인**: 새 계측을 추가하지 않고 이미 있던
`agent_cost_updated`/`human_pool_updated` 감사로그 액션(각각
`after_value.cost_estimate` + `audit_log.created_at`)을 그대로
재사용한다(`dashboard.py` 참고).
"""

from __future__ import annotations

from unittest.mock import patch

from tests.unit.test_phase17_collab_workspace import (
    _execute_agent_and_get_deliverable,
    _login,
    _setup_agent_requester_task,
)


def test_gc161_team_gate_scores_aggregate_across_agents(client_and_conn):
    """팀 Agent 평균 Gate 점수 — 평가 이력이 없으면 sample_count=0, 실행
    후엔 실제 측정된 게이트만 평균에 반영된다."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    assert resp.json()["team_gate_scores"]["sample_count"] == 0

    _execute_agent_and_get_deliverable(client, task_id)

    resp = client.get("/api/dashboard")
    body = resp.json()
    assert body["team_gate_scores"]["sample_count"] == 1
    assert set(body["team_gate_scores"]["gate_averages"].keys()) == {
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
    }


def test_gc162_pending_approval_count_matches_inbox(client_and_conn):
    """대기 승인 건수 — `approvals.list_inbox()`와 같은 값이어야 한다
    (동일 로직 재사용, 이중 구현 방지)."""
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", return_value="산출물"):
        client.post(f"/api/tasks/{task_id}/execute-agent", json={})

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard")
    dashboard_count = resp.json()["pending_approval_count"]

    resp = client.get("/api/approvals", params={"scope": "inbox"})
    assert dashboard_count == len(resp.json())
    assert dashboard_count >= 1


def test_gc163_cost_trend_reflects_real_cost_updates(client_and_conn):
    """비용 추이 — Agent 비용을 실제로 바꾸면 그 이력이 최신순으로
    나와야 한다(새 계측이 아니라 기존 agent_cost_updated 감사로그 재사용)."""
    client, _conn = client_and_conn
    _brief_id, _task_id, agent_id = _setup_agent_requester_task(client)

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/dashboard")
    before_count = len(resp.json()["cost_trend"])

    resp = client.post(f"/api/agent-pool/{agent_id}/cost", json={"cost_estimate": 12.5})
    assert resp.status_code == 200

    resp = client.get("/api/dashboard")
    trend = resp.json()["cost_trend"]
    assert len(trend) == before_count + 1
    assert trend[0]["cost_estimate"] == 12.5
    assert trend[0]["subject_type"] == "agent_pool"
    assert trend[0]["subject_id"] == agent_id


def test_gc164_dashboard_forbidden_for_member(client_and_conn):
    """F10/F13과 같은 RBAC — 팀원은 대시보드를 볼 수 없다."""
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/dashboard")
    assert resp.status_code == 403
