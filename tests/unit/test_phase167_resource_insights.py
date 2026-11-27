"""자원 분석(2026-11-27, Resource Management 분석 Phase D).

- 역할별 사람 vs AI: 사람이 결재한 결과물만(자동 승인 제외), 승인율·한 번에 승인·첫 승인까지 시간, 추천(근거 부족이면 보류)
- 매칭 실패 기록 → 채워지지 않은 수요(같은 할 일은 하루 한 번)
- 스킬 수요·공급(공급 없음·얇음, 스킬 Pool 여부로 제안)
- 팀: 구성원 부하·겹침(3팀 이상 경고)·팀 할 일·Crew 실행
- 업무 분배: 지니·최다 몫·긴급 일 집중"""

from __future__ import annotations

import pytest

from hair_platform import resource_insights


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _task(conn, role="번역가", skills='[]', priority="medium", status="in_progress"):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'x')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills, priority, status) VALUES (?, ?, ?, ?, ?)",
                        (bid, role, skills, priority, status)).lastrowid


def _decided(conn, task, author_type, author_id, status, human=True):
    d = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, ?, ?, 'x', ?)",
                     (task, author_type, author_id, status)).lastrowid
    if human:
        conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, status) VALUES (1, 'deliverable', ?, ?)",
                     (d, status))
    return d


def test_human_vs_ai_by_role_counts_only_human_decisions(client_and_conn):
    _client, conn = client_and_conn
    h = conn.execute("INSERT INTO human_pool (name, role) VALUES ('박번역', '번역가')").lastrowid
    a = conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('번역가', '[]', 'active')").lastrowid
    for status in ("approved", "approved", "rejected", "approved"):
        _decided(conn, _task(conn), "human", h, status)
    for status in ("approved", "approved", "approved", "approved"):
        _decided(conn, _task(conn), "agent", a, status)
    _decided(conn, _task(conn), "agent", a, "rejected", human=False)  # 자동 처리 — 세지 않는다
    conn.commit()
    r = next(x for x in resource_insights.human_vs_ai(conn)["roles"] if x["role"] == "번역가")
    assert r["human"]["approval_rate"] == pytest.approx(0.75) and r["agent"]["approval_rate"] == 1.0
    assert r["agent"]["decided"] == 4 and r["agent"]["first_pass_rate"] == 1.0
    assert r["recommendation"]["label"] == "Agent 우선" and "승인율 사람 75%" in r["recommendation"]["why"]
    # 근거 부족
    _decided(conn, _task(conn, role="통역사"), "human", h, "approved")
    conn.commit()
    r2 = next(x for x in resource_insights.human_vs_ai(conn)["roles"] if x["role"] == "통역사")
    assert r2["recommendation"]["label"] == "데이터 부족" and "Agent" in r2["recommendation"]["why"]


def test_recommendation_people_first_when_ai_much_worse():
    h = {"decided": 5, "approval_rate": 0.9, "hours_to_approval": 5.0, "cost_per_task": 100.0}
    a = {"decided": 5, "approval_rate": 0.6, "hours_to_approval": 1.0, "cost_per_task": 1.0}
    assert resource_insights._recommend(h, a)["label"] == "사람 우선"
    a2 = dict(a, approval_rate=0.8)
    assert resource_insights._recommend(h, a2)["label"] == "AI 초안 + 사람 검토"


def test_match_miss_recorded_and_reported(client_and_conn):
    client, conn = client_and_conn
    t = _task(conn, role="양자컴퓨팅 연구원", skills='["qiskit"]')
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.post(f"/api/tasks/{t}/match").json()["generate_new"] is True
    client.post(f"/api/tasks/{t}/match")  # 같은 날 또 — 한 번만
    unmet = resource_insights.unmet_demand(conn)
    assert unmet[0]["role"] == "양자컴퓨팅 연구원" and unmet[0]["misses"] == 1 and unmet[0]["skills"] == ["qiskit"]
    assert client.get("/api/resource-insights").status_code == 200
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/resource-insights").status_code == 403


def test_skill_gaps(client_and_conn):
    _client, conn = client_and_conn
    conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('김', '개발', '[\"python\"]')")
    conn.execute("INSERT OR IGNORE INTO skill_pool (name) VALUES ('rust')")
    for _ in range(3):
        _task(conn, role="개발", skills='["python", "rust", "cobol"]')
    _task(conn, role="개발", skills='["python"]', status="done")  # 끝난 일은 수요 아님
    conn.commit()
    g = {x["skill"].lower(): x for x in resource_insights.skill_gaps(conn)}
    assert g["python"]["status"] == "thin" and g["python"]["open_tasks"] == 3 and g["python"]["people"] == ["김"]
    assert g["rust"]["status"] == "none" and "스킬 Pool에 있음" in g["rust"]["suggestion"]
    assert g["cobol"]["status"] == "none" and "스킬 Pool에 없음" in g["cobol"]["suggestion"]


def test_team_overlap_and_load(client_and_conn):
    _client, conn = client_and_conn
    p = conn.execute("INSERT INTO human_pool (name, role) VALUES ('최', '개발')").lastrowid
    teams = [conn.execute("INSERT INTO teams (name) VALUES (?)", (f"팀{i}",)).lastrowid for i in range(3)]
    for t in teams:
        conn.execute("INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'human', ?)", (t, p))
    task = _task(conn, role="개발")
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')", (task, p))
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'team', ?, 1, 'confirmed')", (task, teams[0]))
    conn.commit()
    t0 = next(x for x in resource_insights.team_overview(conn) if x["team_id"] == teams[0])
    assert t0["overlap_warning"] and t0["overlaps"][0]["teams"] == 3
    assert t0["member_active_tasks"] == 1 and t0["open_team_tasks"] == 1 and t0["utilization"] == pytest.approx(1 / 3)


def test_workload_distribution_flags_concentration(client_and_conn):
    _client, conn = client_and_conn
    ids = [conn.execute("INSERT INTO human_pool (name, role) VALUES (?, '상담원')", (n,)).lastrowid for n in ("가", "나", "다")]
    plan = {ids[0]: [("urgent", 3), ("medium", 2)], ids[1]: [("medium", 1)], ids[2]: []}
    for hid, items in plan.items():
        for pr, n in items:
            for _ in range(n):
                t = _task(conn, role="상담원", priority=pr)
                conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
                             "VALUES (?, 'human', ?, 1, 'confirmed')", (t, hid))
    conn.commit()
    g = next(x for x in resource_insights.workload_distribution(conn) if x["role"] == "상담원")
    assert g["active_total"] == 6 and g["top_share"] == pytest.approx(5 / 6) and g["gini"] > 0.4
    assert any("83%" in f for f in g["flags"]) and any("긴급" in f for f in g["flags"])
    assert resource_insights._gini([1, 1, 1]) == pytest.approx(0.0)


def test_unknown_cost_does_not_count_as_cheaper():
    h = {"decided": 5, "approval_rate": 0.8, "hours_to_approval": None, "cost_per_task": None}
    a = {"decided": 5, "approval_rate": 0.78, "hours_to_approval": None, "cost_per_task": None}
    assert resource_insights._recommend(h, a)["label"] == "AI 초안 + 사람 검토"
    assert resource_insights._recommend(h, dict(a, cost_per_task=1.0) | {"approval_rate": 0.78})["label"] == "AI 초안 + 사람 검토"
    assert resource_insights._recommend(dict(h, cost_per_task=50.0), dict(a, cost_per_task=1.0))["label"] == "Agent 우선"
