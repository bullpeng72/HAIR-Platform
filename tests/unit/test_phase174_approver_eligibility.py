"""결정할 수 없는 승인자(2026-11-28) — 상사 체인에 계정이 꺼졌거나 admin·manager가 아닌 사람이 있으면 그 단계가 아무도
처리하지 못하고 멈추던 문제. 새 체인은 그런 사람을 건너뛰고, 계정을 끄면 그 사람이 승인자인 대기 결재를 푼다."""

from __future__ import annotations

from hair_platform import approvals
from hair_platform.auth import Role, create_user


def _chain(conn, requester):
    ids = approvals.create_approval_chain(conn, requester, "brief", 999)
    conn.commit()
    return [tuple(conn.execute("SELECT step, approver_id, status FROM approvals WHERE id = ?", (i,)).fetchone()) for i in ids]


def test_chain_skips_inactive_and_non_deciding_managers(client_and_conn):
    _, conn = client_and_conn
    top = create_user(conn, "top_mgr", "top-pass-123", Role.MANAGER)
    lead = create_user(conn, "lead_member", "lead-pass-123", Role.MEMBER)  # 상사지만 결정 권한 없음
    gone = create_user(conn, "gone_mgr", "gone-pass-123", Role.MANAGER)
    worker = create_user(conn, "worker", "worker-pass-123", Role.MEMBER)
    for u, m in ((worker, gone), (gone, lead), (lead, top)):
        conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (m, u))
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (gone,))
    conn.commit()
    assert _chain(conn, worker) == [(1, top, "pending")]  # 꺼진 상사·member 상사를 건너뛰고 그 위로


def test_chain_with_no_eligible_manager_falls_back_to_anyone(client_and_conn):
    _, conn = client_and_conn
    gone = create_user(conn, "gone_mgr2", "gone-pass-123", Role.MANAGER)
    worker = create_user(conn, "worker2", "worker-pass-123", Role.MEMBER)
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (gone, worker))
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (gone,))
    conn.commit()
    assert _chain(conn, worker) == [(1, None, "pending")]


def test_deactivating_user_releases_their_pending_and_waiting_approvals(client_and_conn):
    client, conn = client_and_conn
    top = create_user(conn, "top_mgr3", "top-pass-123", Role.MANAGER)
    mid = create_user(conn, "mid_mgr3", "mid-pass-123", Role.MANAGER)
    worker = create_user(conn, "worker3", "worker-pass-123", Role.MEMBER)
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (mid, worker))
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (top, mid))
    conn.commit()
    before = _chain(conn, worker)
    assert before == [(1, mid, "pending"), (2, top, "waiting")]
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    r = client.patch(f"/api/users/{mid}/status", json={"is_active": False})
    assert r.status_code == 200 and r.json()["approvals_released"] == 1
    rows = conn.execute("SELECT step, approver_id, status FROM approvals WHERE subject_id = 999 ORDER BY step").fetchall()
    assert [tuple(x) for x in rows] == [(1, None, "pending"), (2, top, "waiting")]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'approval_transferred'").fetchone()[0] == 1
    # 다시 켜는 것은 아무것도 옮기지 않는다
    assert client.patch(f"/api/users/{mid}/status", json={"is_active": True}).json()["approvals_released"] == 0
    # 꺼진 승인자의 단계를 manager가 결정할 수 있다
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    aid = conn.execute("SELECT id FROM approvals WHERE subject_id = 999 AND step = 1").fetchone()[0]
    r = client.post(f"/api/approvals/{aid}/decide", json={"approve": True})
    assert r.status_code == 200, r.text


def test_human_with_deactivated_account_is_not_matched_and_adds_no_capacity(client_and_conn):
    from hair_platform import pool, resource_planning

    _, conn = client_and_conn
    gone = create_user(conn, "gone_dev", "gone-pass-123", Role.MEMBER)
    here = create_user(conn, "here_dev", "here-pass-123", Role.MEMBER)
    hg = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('떠난 개발자', '감사개발자', '[\"go\"]', ?)",
                      (gone,)).lastrowid
    hh = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('남은 개발자', '감사개발자', '[\"go\"]', ?)",
                      (here,)).lastrowid
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (gone,))
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (here,)).lastrowid
    conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '감사개발자')", (bid,))
    conn.commit()
    cands = {c.id: c for c in pool.human_pool_candidates(conn) if c.id in (hg, hh)}
    assert cands[hg].is_available is False and cands[hh].is_available is True  # 목록엔 남고 불가로
    util = resource_planning.utilization(conn)
    rows = {p["id"]: p for p in util["people"] if p["id"] in (hg, hh)}
    assert rows[hg]["weekly_hours"] == 0 and all(c["capacity_hours"] == 0 for c in rows[hg]["cells"])
    assert rows[hh]["weekly_hours"] == 40
    role = next(r for r in resource_planning.demand_by_role(conn) if r["role"] == "감사개발자")
    assert role["people"] == 1

