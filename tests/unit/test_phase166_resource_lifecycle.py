"""인계·결재 위임·에스컬레이션·시간 기반 비용(2026-11-27, Resource Management 분석 Phase C).

- 인계 점검: 끝나지 않은 배정(대신 맡을 후보 — 본인 제외), 요청 중 배정, 팀, 대기 결재, 보고 대상, 이름으로 도는 자동화
- 결재 넘기기: 대상은 활성 admin·manager, 자기 요청을 자기가 승인하게 되면 누구나로
- 위임: 기간 안에만 대결자 결재함에 보이고 결정 가능, 활동 기록에 on_behalf_of
- 에스컬레이션: 기본 끔, N일 넘게 머문 지정 결재 → 승인자의 상사(없으면 누구나)
- 비용: 사람 시간당 단가 × 몫(예상 ÷ 인원) + AI 실측, 예산 80% 주의·100% 초과, 팀별"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from hair_platform import approvals, resource_lifecycle


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _task(conn, role="백엔드 개발자", est=None, status="in_progress"):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, '주문 API 개선')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name, status, estimate_hours, required_skills) VALUES (?, ?, ?, ?, '[]')",
                        (bid, role, status, est)).lastrowid


@pytest.fixture
def env(client_and_conn):
    client, conn = client_and_conn
    mgr = _uid(conn, "manager1")
    kim = conn.execute("INSERT INTO human_pool (name, role, user_id) VALUES ('김매니저', '백엔드 개발자', ?)", (mgr,)).lastrowid
    lee = conn.execute("INSERT INTO human_pool (name, role) VALUES ('이개발', '백엔드 개발자')").lastrowid
    conn.commit()
    return client, conn, mgr, kim, lee


def test_handover_lists_everything_and_suggests_someone_else(env):
    client, conn, mgr, kim, lee = env
    t = _task(conn)
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')", (t, kim))
    team = conn.execute("INSERT INTO teams (name) VALUES ('플랫폼팀')").lastrowid
    conn.execute("INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'human', ?)", (team, kim))
    member = _uid(conn, "member1")
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (mgr, member))
    conn.commit()
    approvals.create_approval_chain(conn, member, "deliverable", 999)  # 상사(김매니저)가 승인자
    conn.commit()
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    h = client.get(f"/api/human-pool/{kim}/handover").json()
    assert h["work"][0]["task_id"] == t and h["work"][0]["suggestion"]["id"] == lee  # 본인 제외 1순위
    assert h["teams"][0]["name"] == "플랫폼팀" and len(h["approvals"]) == 1
    assert any(r["name"] == "member1" for r in h["reports"]) and h["open_count"] >= 4
    # 결재 넘기기 — 대상 검증, 자기 요청이면 누구나로
    admin = _uid(conn, "admin1")
    assert client.post(f"/api/users/{mgr}/transfer-approvals", json={"to_user_id": member}).status_code == 400
    assert client.post(f"/api/users/{mgr}/transfer-approvals", json={"to_user_id": admin}).json()["transferred"] == 1
    assert conn.execute("SELECT approver_id FROM approvals WHERE subject_id = 999").fetchone()[0] == admin


def test_delegation_window_inbox_and_decision(env):
    client, conn, mgr, _kim, _lee = env
    member = _uid(conn, "member1")
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (mgr, member))
    admin = _uid(conn, "admin1")
    from hair_platform.auth import Role, create_user

    deputy = create_user(conn, "deputy1", "deputypass", Role.MANAGER)
    conn.commit()
    d = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, 'human', ?, 'x', 'pending_review')",
                     (_task(conn), member)).lastrowid
    approvals.create_approval_chain(conn, member, "deliverable", d)
    conn.commit()
    aid = conn.execute("SELECT id FROM approvals WHERE subject_id = ?", (d,)).fetchone()[0]
    client.post("/login", json={"username": "deputy1", "password": "deputypass"})
    assert [a for a in client.get("/api/approvals?scope=inbox").json() if a["id"] == aid] == []
    assert client.post(f"/api/approvals/{aid}/decide", json={"approve": True}).status_code == 403
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    today = date.today()
    bad = client.post("/api/approval-delegations", json={"delegate_id": member, "starts_on": today.isoformat(), "ends_on": today.isoformat()})
    assert bad.status_code == 400  # 대결자는 admin·manager
    r = client.post("/api/approval-delegations", json={"delegate_id": deputy, "starts_on": today.isoformat(),
                                                       "ends_on": (today + timedelta(days=3)).isoformat(), "reason": "휴가"})
    assert r.status_code == 201
    client.post("/login", json={"username": "deputy1", "password": "deputypass"})
    item = [a for a in client.get("/api/approvals?scope=inbox").json() if a["id"] == aid]
    assert item and item[0]["on_behalf_of"] == "manager1"
    assert client.post(f"/api/approvals/{aid}/decide", json={"approve": True}).status_code == 200
    assert '"on_behalf_of"' in conn.execute("SELECT after_value FROM audit_log WHERE action = 'approval_approved'").fetchone()[0]
    # 지난 위임은 효력 없음
    conn.execute("UPDATE approval_delegations SET starts_on = '2020-01-01', ends_on = '2020-01-02'")
    conn.commit()
    assert resource_lifecycle.delegators_for(conn, deputy) == []
    assert admin  # (admin 존재 확인 — 다른 테스트와 같은 픽스처)


def test_escalation_off_by_default_then_moves_to_manager(env):
    client, conn, mgr, _kim, _lee = env
    member = _uid(conn, "member1")
    boss = _uid(conn, "admin1")
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (mgr, member))
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (boss, mgr))
    conn.commit()
    approvals.create_approval_chain(conn, member, "deliverable", 4242)
    conn.execute("UPDATE approvals SET pending_since = datetime('now', '-5 days') WHERE subject_id = 4242 AND step = 1")
    conn.commit()
    assert resource_lifecycle.escalate_overdue(conn) == 0  # 기본 끔
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.put("/api/approval-escalation", json={"days": 3}).status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert client.put("/api/approval-escalation", json={"days": 0}).status_code == 400
    assert client.put("/api/approval-escalation", json={"days": 3}).status_code == 200
    assert resource_lifecycle.escalate_overdue(conn) == 1
    row = conn.execute("SELECT approver_id, escalation_count FROM approvals WHERE subject_id = 4242 AND step = 1").fetchone()
    assert tuple(row) == (boss, 1)
    assert resource_lifecycle.escalate_overdue(conn) == 0  # 방금 넘겨 시각이 새로 찍혔다


def test_costs_labor_estimate_ai_actual_and_budget(env):
    client, conn, _mgr, kim, lee = env
    t = _task(conn, est=10)
    for h in (kim, lee):
        conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')", (t, h))
    conn.execute("INSERT OR IGNORE INTO model_registry (provider, name, price_input_per_1m, price_output_per_1m) VALUES ('anthropic', 'm2', 3, 15)")
    conn.execute("UPDATE model_registry SET price_input_per_1m = 3, price_output_per_1m = 15 WHERE provider = 'anthropic' AND name = 'm2'")
    conn.execute("INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, task_id) VALUES ('anthropic', 'm2', 1000000, 100000, ?)", (t,))
    conn.commit()
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert client.put(f"/api/human-pool/{kim}/hourly-rate", json={"hourly_rate": 50}).status_code == 200
    c = resource_lifecycle.task_costs(conn)[t]
    assert c["labor_estimated"] == pytest.approx(250.0) and c["labor_missing_rate"] == 1  # 이개발 단가 없음
    assert c["ai_cost"] == pytest.approx(4.5) and c["total"] == pytest.approx(254.5)
    bid = conn.execute("SELECT brief_id FROM tasks WHERE id = ?", (t,)).fetchone()[0]
    client.put(f"/api/human-pool/{lee}/hourly-rate", json={"hourly_rate": 30})
    assert client.put(f"/api/briefs/{bid}/budget", json={"budget_usd": 450}).status_code == 200
    b = next(x for x in client.get("/api/resource-costs").json()["briefs"] if x["brief_id"] == bid)
    assert b["total"] == pytest.approx(404.5) and b["status"] == "warn" and b["missing_rate"] == 0
    client.put(f"/api/briefs/{bid}/budget", json={"budget_usd": 300})
    b = next(x for x in client.get("/api/resource-costs").json()["briefs"] if x["brief_id"] == bid)
    assert b["status"] == "over" and client.get("/api/resource-costs").json()["briefs"][0]["brief_id"] == bid
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.put(f"/api/human-pool/{kim}/hourly-rate", json={"hourly_rate": 1}).status_code == 403
