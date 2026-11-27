"""2026-11-27~28에 추가한 표의 정리(2026-11-28) — 공장 초기화·삭제·보존 기간이 새 표를 몰라 고아 기록과 고객 메일 본문이
남던 문제.

- 초기화: 배운 점·예시·변경 기록·추천·실행 동작·외부 입력·위임·부재·숙련도·매칭 실패를 지운다(조직 설정은 남긴다)
- 업무 요청/할 일 삭제: 그 단계 기록에 딸린 예시·추천·실행 기록을 지우고, 외부 입력은 키만 남기고 본문을 비운다
- 사람·Agent 삭제: 부재·숙련도
- 보존 기간: 30일 지난 외부 입력 본문(대기 중 제외), 1년 지난 매칭 실패"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hair_platform import data_admin, retention


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _seed(conn):
    admin = _uid(conn, "admin1")
    tid = conn.execute("INSERT INTO pipeline_templates (name) VALUES ('정리 테스트')").lastrowid
    sid = conn.execute("INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name) VALUES (?, 1, 's')", (tid,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'r')", (bid,)).lastrowid
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id) VALUES (?, ?, ?)", (tid, task, admin)).lastrowid
    rs = conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status) VALUES (?, ?, 'approved')", (run, sid)).lastrowid
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('사람', 'r', '[\"python\"]')").lastrowid
    trig = conn.execute("INSERT INTO inbound_triggers (name, source, action_template_id, role_name, brief_text, created_by) "
                        "VALUES ('t', 'webhook', ?, 'r', 'b', ?)", (tid, admin)).lastrowid
    rows = [
        ("INSERT INTO stage_examples (stage_id, run_stage_id, excerpt) VALUES (?, ?, '고객 계약 발췌')", (sid, rs)),
        ("INSERT INTO stage_lessons (stage_id, text, source) VALUES (?, '규칙', 'manual')", (sid,)),
        ("INSERT INTO process_changes (template_id, kind, summary, actor_id) VALUES (?, 'stage_update', 's', ?)", (tid, admin)),
        ("INSERT INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, recommended) "
         "VALUES (?, ?, ?, 'low', 0, 'approve')", (rs, tid, sid)),
        ("INSERT INTO stage_actions (stage_id, kind, config, created_by) VALUES (?, 'chat', '{}', ?)", (sid, admin)),
        ("INSERT INTO action_executions (stage_action_id, run_stage_id, kind, status) VALUES (1, ?, 'chat', 'sent')", (rs,)),
        ("INSERT INTO inbound_events (trigger_id, event_key, payload, status, run_id) VALUES (?, 'k1', '{\"text\":\"고객 메일\"}', 'started', ?)",
         (trig, run)),
        ("INSERT INTO approval_delegations (user_id, delegate_id, starts_on, ends_on, created_by) VALUES (?, ?, '2026-01-01', '2026-01-02', ?)",
         (admin, _uid(conn, "manager1"), admin)),
        ("INSERT INTO resource_absences (resource_type, resource_id, starts_on, ends_on, created_by) VALUES ('human', ?, '2026-01-01', '2026-01-02', ?)",
         (h, admin)),
        ("INSERT INTO resource_skill_levels (resource_type, resource_id, skill, level, source, set_by) VALUES ('human', ?, 'python', 3, 'verified', ?)",
         (h, admin)),
        ("INSERT INTO match_misses (task_id, role_name) VALUES (?, 'r')", (task,)),
    ]
    for sql, args in rows:
        conn.execute(sql, args)
    conn.execute("INSERT INTO process_value_settings (id, hourly_cost) VALUES (1, 40)")
    conn.commit()
    return {"bid": bid, "task": task, "rs": rs, "h": h, "trig": trig, "run": run}


NEW_TABLES = ("stage_examples", "stage_lessons", "process_changes", "approval_recommendations", "stage_actions",
              "action_executions", "inbound_events", "inbound_triggers", "approval_delegations", "resource_absences",
              "resource_skill_levels", "match_misses")


def test_factory_reset_wipes_new_tables_but_keeps_org_settings(client_and_conn):
    _, conn = client_and_conn
    _seed(conn)
    counts = data_admin.reset_to_factory_state(conn)
    for t in NEW_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t  # noqa: S608
        assert t in counts
    assert conn.execute("SELECT hourly_cost FROM process_value_settings").fetchone()[0] == 40


def test_deleting_brief_cleans_stage_linked_rows_and_inbound_body(client_and_conn):
    _, conn = client_and_conn
    s = _seed(conn)
    data_admin.delete_brief(conn, _uid(conn, "admin1"), s["bid"])
    for t in ("stage_examples", "approval_recommendations", "action_executions", "match_misses"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t  # noqa: S608
    ev = conn.execute("SELECT payload, run_id, event_key FROM inbound_events").fetchone()
    assert tuple(ev) == (None, None, "k1")  # 중복 방지 키만 남는다
    assert conn.execute("SELECT COUNT(*) FROM stage_lessons").fetchone()[0] == 1  # 단계(템플릿) 설정은 그대로


def test_deleting_human_and_agent_cleans_levels_and_absences(client_and_conn):
    _, conn = client_and_conn
    s = _seed(conn)
    data_admin.delete_human(conn, _uid(conn, "admin1"), s["h"])
    assert conn.execute("SELECT COUNT(*) FROM resource_absences").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM resource_skill_levels").fetchone()[0] == 0
    aid = conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('a', '[\"go\"]', 'active')").lastrowid
    conn.execute("INSERT INTO resource_skill_levels (resource_type, resource_id, skill, level, source, set_by) VALUES ('agent', ?, 'go', 2, 'verified', 1)", (aid,))
    conn.commit()
    data_admin.delete_agent(conn, _uid(conn, "admin1"), aid)
    assert conn.execute("SELECT COUNT(*) FROM resource_skill_levels").fetchone()[0] == 0


def test_retention_clears_old_inbound_bodies_and_misses(client_and_conn):
    _, conn = client_and_conn
    s = _seed(conn)
    conn.execute("UPDATE inbound_events SET received_at = datetime('now', '-40 days')")
    conn.execute("INSERT INTO inbound_events (trigger_id, event_key, payload, status, received_at) "
                 "VALUES (?, 'k2', '{\"text\":\"대기\"}', 'queued', datetime('now', '-40 days'))", (s["trig"],))
    conn.execute("INSERT INTO inbound_events (trigger_id, event_key, payload, status) VALUES (?, 'k3', '{\"text\":\"최근\"}', 'started')",
                 (s["trig"],))
    conn.execute("UPDATE match_misses SET created_at = datetime('now', '-400 days')")
    conn.commit()
    r = retention.purge_expired(conn, now=datetime.now(UTC) + timedelta(seconds=1))
    assert r["inbound_payloads_cleared"] == 1 and r["match_misses_deleted"] == 1
    left = dict(conn.execute("SELECT event_key, payload IS NOT NULL FROM inbound_events").fetchall())
    assert left == {"k1": 0, "k2": 1, "k3": 1}  # 오래되고 시작된 것만 비움, 대기 중·최근은 그대로
