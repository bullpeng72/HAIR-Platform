"""스킬 숙련도(2026-11-28 — Resource Management 분석 3번 남은 항목).

- 근거 수준: 그 스킬을 요구한 할 일에서 사람이 결재로 승인한 결과물 수(1~2 실무, 3~9 숙련, 10+ 전문)
- 확인(admin·manager) vs 본인 기재(구성원, 검증 전 표시), 적용 수준 = max(확인, 근거), 없으면 본인 기재
- 매칭: 수준 정보가 있는 후보만 가점(최대 0.1), 없는 후보는 예전 점수
- 스킬 수요·공급에 숙련 이상 공급 수"""

from __future__ import annotations

import pytest

from hair_platform import pool, resource_insights, skill_levels
from hair_platform.assignment import match_candidates
from hair_platform.models import ResourceCandidate


def _task(conn, skills='["python"]', role="백엔드 개발자"):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'x')").lastrowid
    return conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, ?, ?)", (bid, role, skills)).lastrowid


def _approved(conn, human_id, n, skills='["python"]'):
    for _ in range(n):
        d = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, 'human', ?, 'x', 'approved')",
                         (_task(conn, skills), human_id)).lastrowid
        conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, status) VALUES (1, 'deliverable', ?, 'approved')", (d,))
    conn.commit()


@pytest.fixture
def people(client_and_conn):
    client, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    kim = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('김', '백엔드 개발자', '[\"python\", \"sql\"]', ?)",
                       (member,)).lastrowid
    lee = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('이', '백엔드 개발자', '[\"python\"]')").lastrowid
    conn.commit()
    return client, conn, kim, lee


def test_evidence_levels_and_sources(people):
    client, conn, kim, lee = people
    _approved(conn, kim, 4)
    _approved(conn, lee, 1)
    lv = skill_levels.Levels(conn)
    assert lv.detail("human", kim, "Python")["level"] == 3 and lv.detail("human", kim, "python")["source"] == "근거"
    assert lv.detail("human", lee, "python")["level"] == 2
    assert lv.detail("human", kim, "sql")["level"] is None
    # 본인 기재 — 근거가 없을 때만 적용, 검증 전 표시
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    r = client.put(f"/api/skill-levels/human/{kim}", json={"skill": "sql", "level": 4})
    assert r.status_code == 200 and r.json()["level"] == 4 and r.json()["unverified"] is True
    assert client.put(f"/api/skill-levels/human/{lee}", json={"skill": "python", "level": 4}).status_code == 403
    assert client.put(f"/api/skill-levels/human/{kim}", json={"skill": "rust", "level": 2}).status_code == 400  # 없는 스킬
    # 확인 수준이 근거보다 높으면 확인이 적용, 낮으면 근거가 적용
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.put(f"/api/skill-levels/human/{kim}", json={"skill": "python", "level": 4}).json()["source"] == "확인"
    client.put(f"/api/skill-levels/human/{kim}", json={"skill": "python", "level": 1})
    d = skill_levels.Levels(conn).detail("human", kim, "python")
    assert d["level"] == 3 and d["source"] == "근거" and d["verified"] == 1
    assert client.put(f"/api/skill-levels/human/{kim}", json={"skill": "python", "level": 5}).status_code == 400
    body = client.get(f"/api/skill-levels/human/{kim}").json()
    assert {s["skill"]: s["level"] for s in body["skills"]} == {"python": 3, "sql": 4}


def test_matching_bonus_only_with_levels():
    base = {"type": "human", "role": "백엔드 개발자", "is_available": True, "cost": 100.0, "skill_tags": ["python"]}
    deep = ResourceCandidate(id=1, skill_levels={"python": 4}, **base)
    flat = ResourceCandidate(id=2, **base)
    r = match_candidates("백엔드 개발자", ["python"], [flat, deep])
    assert r.top[0].candidate.id == 1
    assert r.top[0].breakdown["skill_depth"] == pytest.approx(0.1) and "skill_depth" not in r.top[1].breakdown
    assert r.top[1].score == pytest.approx(1.0)  # 수준 정보 없는 후보는 예전 점수


def test_pool_and_skill_gap_use_levels(people):
    _client, conn, kim, lee = people
    _approved(conn, kim, 3)
    for _ in range(3):
        _task(conn)
    conn.commit()
    c = next(x for x in pool.human_pool_candidates(conn) if x.id == kim)
    assert c.skill_levels == {"python": 3}
    assert next(x for x in pool.human_pool_candidates(conn) if x.id == lee).skill_levels is None
    g = next(x for x in resource_insights.skill_gaps(conn) if x["skill"].lower() == "python")
    assert g["supply"] == 2 and g["skilled"] == 1
