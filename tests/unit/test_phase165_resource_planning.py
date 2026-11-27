"""기한·예상 시간·인원·활용도·역할별 수요(2026-11-27, Resource Management 분석 Phase B).

- 할 일 기한·예상 시간(참가자·admin·manager), 만들 때도 지정 가능, 인원 채움 상태
- 배분: 예상 시간 ÷ 배정 인원, 오늘~기한 평일에 고르게, 지난 기한은 이번 주에 몰고 지연으로, 기한·추정 없는 일은 따로
- 용량: 주당 시간(기본 40) − 부재일, 활용도·여유 인력
- 지연·임박 목록, 인원 부족, 역할별 수요(공급 없는 역할 먼저), 대시보드 "주의 필요"에 기한 지남"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from hair_platform import resource_planning

MON = date(2026, 11, 30)  # 월요일로 고정해 주 경계를 손으로 검산한다


def _task(conn, role="백엔드 개발자", status="in_progress", due=None, est=None, headcount=1):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'x')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name, status, due_date, estimate_hours, headcount) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (bid, role, status, due, est, headcount)).lastrowid


def _assign(conn, task, rid, rtype="human"):
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
                 "VALUES (?, ?, ?, 1, 'confirmed')", (task, rtype, rid))


@pytest.fixture
def env(client_and_conn):
    client, conn = client_and_conn
    kim = conn.execute("INSERT INTO human_pool (name, role) VALUES ('김개발', '백엔드 개발자')").lastrowid
    lee = conn.execute("INSERT INTO human_pool (name, role, weekly_hours) VALUES ('이개발', '백엔드 개발자', 20)").lastrowid
    conn.commit()
    return client, conn, kim, lee


def test_allocation_spreads_over_workdays_and_weeks(env):
    _client, conn, kim, lee = env
    # 김: 20시간, 기한 다음 주 금요일(평일 10일) → 주마다 10시간
    t1 = _task(conn, due=(MON + timedelta(days=11)).isoformat(), est=20)
    _assign(conn, t1, kim)
    # 김+이 공동: 8시간, 이번 주 금요일 → 1인 4시간씩 이번 주에
    t2 = _task(conn, due=(MON + timedelta(days=4)).isoformat(), est=8)
    _assign(conn, t2, kim)
    _assign(conn, t2, lee)
    # 이: 지난 기한 6시간 → 이번 주에 몰고 지연
    t3 = _task(conn, due=(MON - timedelta(days=3)).isoformat(), est=6)
    _assign(conn, t3, lee)
    # 김: 기한 없음 → 따로
    _assign(conn, _task(conn, est=5), kim)
    # 끝난 일은 세지 않는다
    _assign(conn, _task(conn, status="done", due=MON.isoformat(), est=50), kim)
    conn.commit()
    with patch.object(resource_planning, "_today", return_value=MON):
        u = resource_planning.utilization(conn)
    k = next(p for p in u["people"] if p["id"] == kim)
    l_ = next(p for p in u["people"] if p["id"] == lee)
    assert [c["allocated_hours"] for c in k["cells"]][:3] == [14.0, 10.0, 0.0]
    assert k["cells"][0]["ratio"] == pytest.approx(14 / 40) and k["unscheduled_tasks"] == 1
    assert l_["cells"][0]["allocated_hours"] == 10.0 and l_["cells"][0]["capacity_hours"] == 20.0
    assert l_["overdue_tasks"] == 1 and u["summary"]["overdue_assignments"] == 1
    assert u["weeks"][0] == MON.isoformat()


def test_absence_reduces_capacity_and_bench(env):
    _client, conn, kim, lee = env
    conn.execute("INSERT INTO resource_absences (resource_type, resource_id, starts_on, ends_on, created_by) "
                 "VALUES ('human', ?, ?, ?, 1)", (kim, MON.isoformat(), (MON + timedelta(days=1)).isoformat()))
    t = _task(conn, due=(MON + timedelta(days=4)).isoformat(), est=30)
    _assign(conn, t, lee)
    conn.commit()
    with patch.object(resource_planning, "_today", return_value=MON):
        u = resource_planning.utilization(conn)
    k = next(p for p in u["people"] if p["id"] == kim)
    assert k["cells"][0]["capacity_hours"] == 24.0 and k["cells"][0]["absent_days"] == 2
    l_ = next(p for p in u["people"] if p["id"] == lee)
    assert l_["cells"][0]["ratio"] == pytest.approx(1.5)  # 20시간 주에 30시간
    assert u["summary"]["over_allocated_cells"] == 1
    assert "김개발" in u["summary"]["bench"] and "이개발" not in u["summary"]["bench"]


def test_deadlines_headcount_and_demand(env):
    client, conn, kim, _lee = env
    today = date.today()
    late = _task(conn, due=(today - timedelta(days=2)).isoformat())
    soon = _task(conn, due=(today + timedelta(days=3)).isoformat(), headcount=2)
    _assign(conn, soon, kim)
    _task(conn, due=(today + timedelta(days=30)).isoformat())  # 창 밖
    _task(conn, role="법무 검토자")  # 공급 없는 역할
    conn.commit()
    d = resource_planning.deadlines(conn)
    assert [x["id"] for x in d["overdue"]] == [late] and [x["id"] for x in d["due_soon"]] == [soon]
    assert d["due_soon"][0]["assignees"] == "김개발" and d["overdue"][0]["days_left"] == -2
    piped = _task(conn)  # 파이프라인이 처리하는 할 일 — 배정 대상이 아니다
    conn.execute("INSERT INTO pipeline_templates (name) VALUES ('p')")
    conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id) VALUES ((SELECT MAX(id) FROM pipeline_templates), ?, 1)", (piped,))
    conn.commit()
    gaps = {g["id"]: g for g in resource_planning.headcount_gaps(conn)}
    assert gaps[soon]["short"] == 1 and gaps[late]["short"] == 1 and piped not in gaps
    demand = resource_planning.demand_by_role(conn)
    assert demand[0]["role"] == "법무 검토자" and demand[0]["no_supply"]
    be = next(x for x in demand if x["role"] == "백엔드 개발자")
    assert be["open"] == 4 and be["unassigned"] == 2 and be["people"] == 2
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    b = {x["id"]: x for x in client.get("/api/dashboard/bottlenecks").json()}
    assert "overdue" in b[late]["reasons"] and soon not in b
    assert client.get("/api/resource-planning").status_code == 200
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/resource-planning").status_code == 403


def test_schedule_api_permissions_and_validation(env):
    client, conn, kim, _lee = env
    t = _task(conn, headcount=2)
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    r = client.patch(f"/api/tasks/{t}/schedule", json={"due_date": "2026-12-24", "estimate_hours": 6})
    assert r.status_code == 200 and r.json()["due_date"] == "2026-12-24" and r.json()["short"] == 2
    assert client.patch(f"/api/tasks/{t}/schedule", json={"due_date": "24/12"}).status_code == 400
    assert client.patch(f"/api/tasks/{t}/schedule", json={"estimate_hours": -1}).status_code == 400
    assert client.patch(f"/api/tasks/{t}/schedule", json={"estimate_hours": "x"}).status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'task_schedule_updated'").fetchone()[0] == 1
    client.post("/login", json={"username": "member1", "password": "memberpass"})  # 참가자 아님
    assert client.patch(f"/api/tasks/{t}/schedule", json={"due_date": None}).status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert client.put(f"/api/human-pool/{kim}/weekly-hours", json={"weekly_hours": 100}).status_code == 400
    assert client.put(f"/api/human-pool/{kim}/weekly-hours", json={"weekly_hours": 32}).json()["effective_weekly_hours"] == 32
    # 만들 때 지정
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'y')").lastrowid
    conn.commit()
    r = client.post(f"/api/briefs/{bid}/tasks", json={"tasks": [{"role_name": "QA", "due_date": "2026-12-01", "estimate_hours": 3}]})
    assert r.status_code in (200, 201), r.text
    row = conn.execute("SELECT due_date, estimate_hours FROM tasks WHERE brief_id = ?", (bid,)).fetchone()
    assert tuple(row) == ("2026-12-01", 3.0)
    assert client.post(f"/api/briefs/{bid}/tasks", json={"tasks": [{"role_name": "QA", "due_date": "내일"}]}).status_code == 400
