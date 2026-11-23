"""업무별 효율·비용(2026-11-23, "P2 진행해줘" — P2-11)."""

from __future__ import annotations

import pytest

from hair_platform import agent_runtime, db, llm_usage


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    llm_usage.configure(c)
    yield c
    llm_usage.configure(None)


def _task(conn):
    from hair_platform.auth import Role, create_user

    admin = create_user(conn, "wu_admin", "pw", Role.ADMIN)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '3분기 실적 보고서')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '실적 보고서 작성자')", (brief,)).lastrowid
    conn.commit()
    return admin, task


def test_scope_tags_records_and_nests(conn):
    llm_usage.record("ollama", "m", 10, 5)  # 맥락 없음
    with llm_usage.scope(task_id=7):
        llm_usage.record("ollama", "m", 100, 50)
        with llm_usage.scope(agent_id=3):
            llm_usage.record("ollama", "m", 1, 1)
    rows = conn.execute("SELECT task_id, agent_id FROM llm_usage ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(None, None), (7, None), (7, 3)]


def test_with_scope_reads_arguments_by_name_and_function(conn):
    @llm_usage.with_scope(task_id="task_id", agent_id=lambda a: a["agent"] * 10)
    def job(conn_, task_id, agent):
        llm_usage.record("ollama", "m", 1, 1)

    job(conn, 5, agent=2)
    assert tuple(conn.execute("SELECT task_id, agent_id FROM llm_usage").fetchone()) == (5, 20)

    @llm_usage.with_scope(task_id=lambda a: 1 / 0)  # 맥락 계산이 실패해도 실행은 된다
    def broken():
        llm_usage.record("ollama", "m", 1, 1)

    broken()
    assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2


def test_execute_agent_and_pipeline_jobs_are_attributed(conn, monkeypatch):
    admin, task = _task(conn)
    agent_id = conn.execute("SELECT id FROM agent_pool WHERE role_name = '문서 교정자'").fetchone()[0]
    monkeypatch.setattr(agent_runtime.llm, "resolve_provider_and_model", lambda p, m: ("ollama", "m"))

    def fake_call(*a, **k):
        llm_usage.record("ollama", "m", 30, 20)
        return "수정본"

    monkeypatch.setattr(agent_runtime.llm, "call_llm", fake_call)
    with llm_usage.scope(task_id=task):
        agent_runtime.execute_agent(conn, agent_id, admin, "교정해줘")
    row = conn.execute("SELECT task_id, agent_id FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(row) == (task, agent_id)
    tpl = conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()[0]
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id) VALUES (?, ?, ?)",
                       (tpl, task, admin)).lastrowid
    assert llm_usage.task_of_pipeline_run({"conn": conn, "run_id": run}) == task


def test_work_report_aggregates_cost_and_efficiency(conn):
    admin, task = _task(conn)
    conn.execute("INSERT INTO model_registry (provider, name, is_active, price_input_per_1m, price_output_per_1m) "
                 "VALUES ('openai', 'priced', 1, 1.0, 2.0)")
    conn.commit()
    with llm_usage.scope(task_id=task, agent_id=5):
        llm_usage.record("openai", "priced", 1_000_000, 500_000)  # 1.0 + 1.0
        llm_usage.record("ollama", "unpriced", 10, 10)
    llm_usage.record("ollama", "unpriced", 7, 3)
    did = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content) VALUES (?, 'agent', 5, 'x')", (task,)).lastrowid
    conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, status) VALUES (?, 'deliverable', ?, 'rejected')", (admin, did))
    conn.commit()
    w = llm_usage.work_report(conn, None)
    t = w["tasks"][0]
    assert t["task_id"] == task and t["calls"] == 2 and t["tokens"] == 1_500_020
    assert t["cost"] == pytest.approx(2.0) and t["cost_partial"] is True
    assert t["human_decisions"] == 1 and t["deliverables"] == 1 and t["brief"].startswith("3분기")
    assert w["agents"][0]["agent_id"] == 5
    assert w["unattributed"] == {"calls": 1, "tokens": 10}


def test_endpoint_permission(client_and_conn):
    client, _c = client_and_conn
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/llm-usage/work").status_code == 403
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    body = client.get("/api/llm-usage/work").json()
    assert set(body) == {"days", "tasks", "agents", "unattributed"}
