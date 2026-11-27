"""만약에·인사 자료 가져오기(2026-11-28 — Resource Management 분석 11번·12번).

- 만약에: 역할에 사람 N명(주당 H시간)을 더하면 4주 활용도·초과 주 수가 줄어든다, 모델 동시 처리 k개 → 파이프라인 대기
- 가져오기: 미리 보기(아무것도 안 씀) → 적용, 사번으로 만들기·고치기·건너뜀·오류, 부재는 사번으로 사람을 찾는다, admin 전용"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from hair_platform import resource_planning, resource_scenarios

MON = date(2026, 11, 30)


def test_what_if_adding_people_lowers_utilization(client_and_conn):
    _client, conn = client_and_conn
    kim = conn.execute("INSERT INTO human_pool (name, role) VALUES ('김', '백엔드 개발자')").lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (1, 'x')").lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name, status, due_date, estimate_hours) VALUES (?, '백엔드 개발자', 'in_progress', ?, 60)",
                     (bid, (MON + timedelta(days=4)).isoformat())).lastrowid
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')", (t, kim))
    conn.commit()
    with patch.object(resource_planning, "_today", return_value=MON):
        r = resource_scenarios.what_if(conn, [{"role": "백엔드", "count": 1, "weekly_hours": 40}])
    role = r["roles"][0]
    assert role["role"] == "백엔드 개발자" and role["before"]["weekly"][0] == pytest.approx(1.5)
    assert role["after"]["weekly"][0] == pytest.approx(0.75) and role["before"]["weeks_over"] == 1 and role["after"]["weeks_over"] == 0
    with pytest.raises(ValueError):
        resource_scenarios.what_if(conn, [{"role": "x", "count": 0}])
    # 새 역할(지금 사람 없음)
    with patch.object(resource_planning, "_today", return_value=MON):
        assert resource_scenarios.what_if(conn, [{"role": "디자이너", "count": 2}])["roles"][0]["new_role"] is True


def test_what_if_model_slots(client_and_conn):
    client, conn = client_and_conn
    fake = {"slots": 1, "pipeline_jobs_ahead": 3, "pipeline_queue_wait_seconds": 1800}
    with patch("hair_platform.capacity.report", return_value=fake):
        q = resource_scenarios.what_if(conn, [], 2)["queue"]
    assert q["wait_before"] == 1800 and q["wait_after"] == pytest.approx(600) and "OLLAMA_NUM_PARALLEL" in q["note"]
    with patch("hair_platform.capacity.report", return_value={**fake, "slots": None}):
        q2 = resource_scenarios.what_if(conn, [], 2)["queue"]
    assert q2["wait_after"] == 1800 and "클라우드" in q2["note"]
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.post("/api/resource-what-if", json={"add_people": [], "extra_model_slots": 99}).status_code == 400


PEOPLE = """employee_id,name,role,skills,weekly_hours,hourly_rate,max_concurrent_tasks,availability
20260001,김신입,데이터 분석가,python;sql,32,45,2,available
20260002,이경력,,,,,,
abc,박오류,QA,,,,,
20260003,최변경,QA,,50,,,
"""


def test_import_people_preview_then_apply(client_and_conn):
    client, conn = client_and_conn
    conn.execute("INSERT INTO human_pool (name, role, employee_id, weekly_hours) VALUES ('최변경', 'QA', '20260003', 40)")
    conn.commit()
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.post("/api/human-pool/import", json={"kind": "people", "csv": PEOPLE}).status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    prev = client.post("/api/human-pool/import", json={"kind": "people", "csv": PEOPLE}).json()
    acts = {r["employee_id"]: r["action"] for r in prev["rows"]}
    assert acts == {"20260001": "create", "20260002": "error", "abc": "error", "20260003": "update"}
    assert prev["applied"] is False and conn.execute("SELECT COUNT(*) FROM human_pool WHERE employee_id = '20260001'").fetchone()[0] == 0
    done = client.post("/api/human-pool/import", json={"kind": "people", "csv": PEOPLE, "apply": True}).json()
    assert (done["created"], done["updated"], done["errors"]) == (1, 1, 2)
    row = conn.execute("SELECT * FROM human_pool WHERE employee_id = '20260001'").fetchone()
    assert (row["weekly_hours"], row["hourly_rate"], row["max_concurrent_tasks"], row["skill_tags"]) == (32.0, 45.0, 2, '["python", "sql"]')
    assert conn.execute("SELECT weekly_hours FROM human_pool WHERE employee_id = '20260003'").fetchone()[0] == 50
    again = client.post("/api/human-pool/import", json={"kind": "people", "csv": PEOPLE}).json()
    assert {r["employee_id"]: r["action"] for r in again["rows"]}["20260001"] == "skip"  # 바뀐 값 없음
    assert client.post("/api/human-pool/import", json={"kind": "people", "csv": "name,role\na,b"}).status_code == 400


def test_import_absences(client_and_conn):
    client, conn = client_and_conn
    conn.execute("INSERT INTO human_pool (name, role, employee_id) VALUES ('김', 'QA', '20269999')")
    conn.commit()
    csv_text = "employee_id,starts_on,ends_on,reason\n20269999,2026-12-21,2026-12-24,연차\n20260000,2026-12-01,2026-12-02,\n20269999,2026-12-05,2026-12-01,\n"
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    prev = client.post("/api/human-pool/import", json={"kind": "absences", "csv": csv_text}).json()
    assert [r["action"] for r in prev["rows"]] == ["create", "error", "error"]
    client.post("/api/human-pool/import", json={"kind": "absences", "csv": csv_text, "apply": True})
    assert conn.execute("SELECT reason FROM resource_absences").fetchone()[0] == "연차"
    assert client.post("/api/human-pool/import", json={"kind": "absences", "csv": csv_text}).json()["rows"][0]["action"] == "skip"
