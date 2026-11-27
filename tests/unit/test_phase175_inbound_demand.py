"""역할별 수요 예측에 외부 입력 트리거(2026-11-28, Resource Management 2번 남은 부분)."""

from __future__ import annotations


def test_role_demand_includes_inbound_trigger_forecast(client_and_conn):
    """역할별 수요(2026-11-28) — 외부 입력 트리거의 최근 4주 받은 일을 앞으로 4주 예상으로(처음부터 있던 메일·꺼진 트리거·
    오래된 일은 빼고)."""
    from hair_platform import resource_planning

    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    tid = conn.execute("INSERT INTO pipeline_templates (name) VALUES ('문의')").lastrowid
    on = conn.execute("INSERT INTO inbound_triggers (name, source, action_template_id, role_name, brief_text, created_by) "
                      "VALUES ('문의 메일', 'mail', ?, '고객지원 수요테스트', 'b', ?)", (tid, admin)).lastrowid
    off = conn.execute("INSERT INTO inbound_triggers (name, source, action_template_id, role_name, brief_text, created_by, is_active) "
                       "VALUES ('꺼진 것', 'mail', ?, '고객지원 수요테스트', 'b', ?, 0)", (tid, admin)).lastrowid
    for i in range(8):
        conn.execute("INSERT INTO inbound_events (trigger_id, event_key, status, received_at) VALUES (?, ?, 'started', datetime('now', ?))",
                     (on, f"k{i}", f"-{i * 3} days"))
    conn.execute("INSERT INTO inbound_events (trigger_id, event_key, status) VALUES (?, 'seed', 'seeded')", (on,))
    conn.execute("INSERT INTO inbound_events (trigger_id, event_key, status, received_at) VALUES (?, 'old', 'started', datetime('now', '-60 days'))", (on,))
    for i in range(20):
        conn.execute("INSERT INTO inbound_events (trigger_id, event_key, status) VALUES (?, ?, 'started')", (off, f"o{i}"))
    conn.commit()
    row = next(r for r in resource_planning.demand_by_role(conn) if r["role"] == "고객지원 수요테스트")
    assert row["inbound_next"] == 8 and row["open"] == 0  # 열린 할 일이 없어도 다가올 수요로 줄이 생긴다
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    demand = client.get("/api/resource-planning").json()["demand"]
    assert any(r["role"] == "고객지원 수요테스트" and r["inbound_next"] == 8 for r in demand)
