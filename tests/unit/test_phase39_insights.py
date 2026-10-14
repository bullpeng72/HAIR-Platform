"""Phase 39 — 외부 설계 문서("HAIR-Platform 설계서", 다른 시스템 기획안)
에서 가져올 만한 인사이트 중 사용자가 고른 2개를 구현한다(사용자 요청,
2026-10-13, "문서 내용에서 얻을 인사이트가 있는지 확인해줘" → "1,2번
구현을 진행해줘").

1. **Gate 점수 기반 자동 경고** — Agent 하나의 가장 최근 Gate 평가
   3건이 전부 하나 이상의 Gate에서 실패했으면 매니저 대시보드의
   "Agent 주의 필요"에 띄운다(`dashboard.agents_with_repeated_gate_
   failures()`). 완전 자동 오프보딩은 아니다 — 신호만 준다.
2. **재현 가능한 감사로그** — 지금까지 `audit_log`의 `agent_executed`
   행은 증강 전 원본 프롬프트만 남겼고, 실제 LLM에 들어간 system
   prompt·RAG/GraphDB로 증강된 최종 프롬프트·그 시점 provider/model은
   어디에도 안 남았다. `agent_execution_jobs.prompt_snapshot`(JSON)에
   성공/실패 둘 다 남긴다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import dashboard
from tests.unit.test_phase17_collab_workspace import (
    _execute_agent_and_get_deliverable,
    _login,
    _setup_agent_requester_task,
)
from tests.unit.test_phase27_asana_parity_phase2 import _make_brief_and_task


def _insert_agent_evaluation(conn, agent_id, task_id, *, passed):
    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, status) "
        "VALUES (?, 'agent', ?, '산출물', 'pending_review')",
        (task_id, agent_id),
    )
    deliverable_id = cur.lastrowid
    gate_scores = {"A": {"score": 0.9 if passed else 0.1, "passed": passed}}
    conn.execute(
        "INSERT INTO agent_evaluations (deliverable_id, gate_scores, tcr) VALUES (?, ?, ?)",
        (deliverable_id, json.dumps(gate_scores), 90.0 if passed else 10.0),
    )
    conn.commit()
    return deliverable_id


# ---------- 1) Gate 점수 기반 자동 경고 ----------


def test_agent_with_3_consecutive_failures_is_flagged(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("연속실패 Agent",),
    )
    agent_id = cur.lastrowid
    conn.commit()
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    for _ in range(3):
        _insert_agent_evaluation(conn, agent_id, task_id, passed=False)

    flagged = dashboard.agents_with_repeated_gate_failures(conn)
    assert any(a["agent_id"] == agent_id for a in flagged)
    entry = next(a for a in flagged if a["agent_id"] == agent_id)
    assert entry["recent_evaluation_count"] == 3


def test_agent_with_only_2_failures_not_flagged_small_sample(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("샘플부족 Agent",),
    )
    agent_id = cur.lastrowid
    conn.commit()
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    for _ in range(2):
        _insert_agent_evaluation(conn, agent_id, task_id, passed=False)

    flagged = dashboard.agents_with_repeated_gate_failures(conn)
    assert not any(a["agent_id"] == agent_id for a in flagged)


def test_agent_with_one_recent_pass_not_flagged(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("섞인결과 Agent",),
    )
    agent_id = cur.lastrowid
    conn.commit()
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    _insert_agent_evaluation(conn, agent_id, task_id, passed=False)
    _insert_agent_evaluation(conn, agent_id, task_id, passed=True)  # 최근 것 하나는 통과
    _insert_agent_evaluation(conn, agent_id, task_id, passed=False)

    flagged = dashboard.agents_with_repeated_gate_failures(conn)
    assert not any(a["agent_id"] == agent_id for a in flagged)


def test_inactive_agent_excluded_even_with_3_failures(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'pending_onboarding')",
        ("미온보딩 Agent",),
    )
    agent_id = cur.lastrowid
    conn.commit()
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    for _ in range(3):
        _insert_agent_evaluation(conn, agent_id, task_id, passed=False)

    flagged = dashboard.agents_with_repeated_gate_failures(conn)
    assert not any(a["agent_id"] == agent_id for a in flagged)


def test_dashboard_api_exposes_agent_bottlenecks_with_rbac(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("API로확인 Agent",),
    )
    agent_id = cur.lastrowid
    conn.commit()
    _brief_id, task_id = _make_brief_and_task(client, "백엔드 개발자")
    for _ in range(3):
        _insert_agent_evaluation(conn, agent_id, task_id, passed=False)

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    assert any(a["agent_id"] == agent_id for a in resp.json()["agent_bottlenecks"])

    resp = client.get("/api/dashboard/agent-bottlenecks")
    assert resp.status_code == 200, resp.text
    assert any(a["agent_id"] == agent_id for a in resp.json())

    _login(client, "member1", "memberpass")
    resp = client.get("/api/dashboard/agent-bottlenecks")
    assert resp.status_code == 403


# ---------- 2) 재현 가능한 감사로그(prompt_snapshot) ----------


def test_successful_execution_persists_prompt_snapshot(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)
    _deliverable_id, _row = _execute_agent_and_get_deliverable(client, task_id)

    _login(client, "manager1", "managerpass")
    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    assert resp.status_code == 200, resp.text
    job = next(j for j in resp.json() if j["deliverable_id"] == _deliverable_id)
    snapshot = job["prompt_snapshot"]
    assert snapshot is not None
    assert snapshot["llm_provider"]
    assert snapshot["llm_model"]
    assert snapshot["system_prompt"]
    assert snapshot["augmented_prompt"]
    # 역할명이 system prompt에 실제로 반영돼 있어야 "재현 가능"하다고 할 수 있다.
    assert "QA 엔지니어" in snapshot["system_prompt"]


def test_failed_execution_still_persists_prompt_snapshot(client_and_conn):
    client, _conn = client_and_conn
    _brief_id, task_id, _agent_id = _setup_agent_requester_task(client)

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("LLM 호출 실패")):
        resp = client.post(f"/api/tasks/{task_id}/execute-agent", json={})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    resp = client.get(f"/api/tasks/{task_id}/agent-execution-jobs")
    job = next(j for j in resp.json() if j["id"] == job_id)
    assert job["status"] == "failed"
    # 실패해도 "무엇을 시도했는지"는 남아야 사후 재현/디버깅이 된다.
    assert job["prompt_snapshot"] is not None
    assert job["prompt_snapshot"]["system_prompt"]
