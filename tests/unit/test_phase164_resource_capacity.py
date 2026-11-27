"""자원 용량·부재·부하 기반 매칭(2026-11-27, Resource Management 분석 Phase A).

- 사람 용량(기본 3)·끝나지 않은 확정 배정 수 → 매칭의 가용성이 남은 용량 비율, 꽉 찬 사람은 뒤로·'용량 초과' 표시
- 역할·스킬이 모두 맞는 후보끼리도 비용만이 아니라 같은 점수로 줄 세움
- 부재 기간: 오늘이면 불가, 2주 안이면 예고, 본인도 적을 수 있음
- Agent: 로컬 모델 대기 반영, 자동 적합도 = Gate A + 사람 승인율(근거 표시)
- 배정 요청은 막지 않고 경고·활동 기록"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from hair_platform import agent_evaluation, pool, resource_capacity
from hair_platform.assignment import match_candidates
from hair_platform.models import ResourceCandidate


def _cand(**kw):
    base = {"id": 1, "type": "human", "role": "백엔드 개발자", "is_available": True, "cost": 100.0,
            "skill_tags": ["python"], "performance_score": None}
    base.update(kw)
    return ResourceCandidate(**base)


def test_load_aware_score_and_exact_ordering():
    busy = _cand(id=1, cost=10.0, active_tasks=3, capacity=3)   # 꽉 참, 가장 쌈
    half = _cand(id=2, cost=50.0, active_tasks=1, capacity=2)
    free = _cand(id=3, cost=90.0, active_tasks=0, capacity=3)
    r = match_candidates("백엔드 개발자", ["python"], [busy, half, free])
    ids = [s.candidate.id for s in r.top]
    assert ids[-1] == 1 and r.top[-1].over_capacity is True  # 예전엔 비용만 봐 1등이었다
    assert any("진행 중 3/3" in n for n in r.top[-1].notes)
    assert ids[0] == 3  # 남은 용량이 가장 많다(비용 차이를 넘는다)
    # 부하 정보가 없는 후보는 예전 점수 그대로
    legacy = match_candidates("디자이너", [], [_cand(id=9, role="디자이너", skill_tags=[])])
    assert legacy.top[0].score == pytest.approx(1.0)


def test_agent_queue_wait_lowers_but_does_not_zero_availability():
    a = _cand(id=5, type="agent", queue_wait_seconds=3600)
    f, notes, over = resource_capacity.availability_factor(a)
    assert f == pytest.approx(0.5) and "모델 대기 약 60분" in notes and not over
    f2, _n, _o = resource_capacity.availability_factor(_cand(id=6, type="agent", queue_wait_seconds=99999))
    assert f2 == resource_capacity.AGENT_WAIT_FLOOR


@pytest.fixture
def people(client_and_conn):
    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    kim = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('김개발', '백엔드 개발자', '[\"python\"]', ?)",
                       (member,)).lastrowid
    lee = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('이개발', '백엔드 개발자', '[\"python\"]')").lastrowid
    conn.commit()
    return client, conn, kim, lee


def _task(conn, status="in_progress", role="백엔드 개발자"):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'x')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills, status) VALUES (?, ?, '[\"python\"]', ?)",
                        (bid, role, status)).lastrowid


def _assign(conn, task_id, human_id):
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')",
                 (task_id, human_id))


def test_active_tasks_capacity_and_match_route(people):
    client, conn, kim, lee = people
    for _ in range(3):
        _assign(conn, _task(conn), kim)
    _assign(conn, _task(conn, status="done"), lee)  # 끝난 일은 세지 않는다
    conn.commit()
    load = resource_capacity.active_tasks(conn)
    assert load[("human", kim)] == 3 and ("human", lee) not in load
    new_task = _task(conn, status="queued")
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    top = client.post(f"/api/tasks/{new_task}/match").json()["top"]
    humans = [t for t in top if t["candidate"]["type"] == "human"]
    assert humans[0]["candidate"]["id"] == lee and humans[-1]["over_capacity"] is True
    # 용량을 늘리면 다시 여유가 생긴다
    assert client.put(f"/api/human-pool/{kim}/capacity", json={"max_concurrent_tasks": 6}).json()["effective_capacity"] == 6
    assert client.put(f"/api/human-pool/{kim}/capacity", json={"max_concurrent_tasks": 0}).status_code == 400
    kim_c = next(c for c in pool.human_pool_candidates(conn) if c.id == kim)
    assert (kim_c.active_tasks, kim_c.capacity) == (3, 6)
    load_view = client.get("/api/resource-load").json()
    row = next(r for r in load_view["resources"] if r["type"] == "human" and r["id"] == kim)
    assert row["utilization"] == pytest.approx(0.5)


def test_absence_today_makes_unavailable_and_member_can_add_own(people):
    client, conn, kim, lee = people
    today = date.today()
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    r = client.post(f"/api/human-pool/{kim}/absences", json={"starts_on": today.isoformat(),
                                                             "ends_on": (today + timedelta(days=2)).isoformat(), "reason": "휴가"})
    assert r.status_code == 201
    assert client.post(f"/api/human-pool/{lee}/absences", json={"starts_on": today.isoformat(), "ends_on": today.isoformat()}).status_code == 403
    assert len(client.get("/api/resource-absences").json()) == 1  # 본인 것만
    kim_c = next(c for c in pool.human_pool_candidates(conn) if c.id == kim)
    assert kim_c.is_available is False and kim_c.absent_until == (today + timedelta(days=2)).isoformat()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    soon = (today + timedelta(days=5)).isoformat()
    client.post(f"/api/human-pool/{lee}/absences", json={"starts_on": soon, "ends_on": soon, "reason": "출장"})
    lee_c = next(c for c in pool.human_pool_candidates(conn) if c.id == lee)
    assert lee_c.is_available is True and lee_c.absent_from == soon
    bad = client.post(f"/api/human-pool/{lee}/absences", json={"starts_on": soon, "ends_on": today.isoformat()})
    assert bad.status_code == 400
    aid = client.get("/api/resource-absences").json()[0]["id"]
    assert client.delete(f"/api/resource-absences/{aid}").status_code == 200


def test_assignment_request_warns_but_succeeds(people):
    client, conn, kim, _lee = people
    for _ in range(3):
        _assign(conn, _task(conn), kim)
    t = _task(conn, status="queued")
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    r = client.post(f"/api/tasks/{t}/assignments", json={"resource_type": "human", "resource_id": kim})
    assert r.status_code == 201 and any("동시 용량 3건" in w for w in r.json()["warnings"])
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'assignment_requested_with_warning'").fetchone()[0] == 1


def test_agent_auto_fit_combines_gate_and_human_approvals(client_and_conn):
    _client, conn = client_and_conn
    aid = conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('작성자', '[]', 'active')").lastrowid
    task = _task(conn, role="작성자")
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    for i, status in enumerate(("approved", "approved", "rejected", "approved")):
        d = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, 'agent', ?, 'x', ?)",
                         (task, aid, status)).lastrowid
        conn.execute("INSERT INTO agent_evaluations (deliverable_id, gate_scores) VALUES (?, ?)",
                     (d, json.dumps({"A": {"score": 0.8, "passed": True}})))
        conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, status) VALUES (?, 'deliverable', ?, ?)",
                     (member, d, status))
    conn.commit()
    with patch.object(agent_evaluation, "auto_fit_score", return_value=0.8):
        det = agent_evaluation.auto_fit_detail(conn, aid)
    assert det is not None
    assert det["human_approval_rate"] == pytest.approx(0.75) and det["human_decisions"] == 4
    assert det["score"] == pytest.approx(0.6 * 0.8 + 0.4 * 0.75)
    assert "사람 승인율 75%(4건)" in det["source"]
    with patch.object(agent_evaluation, "auto_fit_score", return_value=None):
        only_human = agent_evaluation.auto_fit_detail(conn, aid)
        assert only_human is not None and only_human["score"] == pytest.approx(0.75)
    c = next(x for x in pool.agent_pool_candidates(conn) if x.id == aid)
    assert c.performance_source and c.performance_source.startswith("자동")


def test_my_human_record_endpoint(people):
    client, _conn, kim, _lee = people
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.get("/api/human-pool/me").json()["id"] == kim
    client.post("/login", json={"username": "viewer1", "password": "viewerpass"})
    assert client.get("/api/human-pool/me").json() == {"id": None}
