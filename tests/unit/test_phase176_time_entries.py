"""할 일 작업 시간 기록(2026-11-28, Resource Management 5번) — 적을 수 있는 사람, 값 검사, 인건비(기록 우선), 정리."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from hair_platform import data_admin, resource_lifecycle

PW = {"admin1": "adminpass", "manager1": "managerpass", "member1": "memberpass", "viewer1": "viewerpass"}


def _login(c, name):
    assert c.post("/login", json={"username": name, "password": PW[name]}).status_code == 200


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


@pytest.fixture
def env(client_and_conn):
    client, conn = client_and_conn
    member, admin = _uid(conn, "member1"), _uid(conn, "admin1")
    me = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id, hourly_rate) VALUES ('본인', 'QA', '[]', ?, 50)",
                      (member,)).lastrowid
    other = conn.execute("INSERT INTO human_pool (name, role, skill_tags, hourly_rate) VALUES ('동료', 'QA', '[]', 30)").lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '시간 테스트')", (member,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, estimate_hours) VALUES (?, 'QA', 10)", (bid,)).lastrowid
    for h in (me, other):
        conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                     (task, h, admin))
    conn.commit()
    return {"c": client, "conn": conn, "me": me, "other": other, "task": task, "bid": bid}


def test_who_can_log_and_value_checks(env):
    c, task, me, other = env["c"], env["task"], env["me"], env["other"]
    today = date.today().isoformat()
    _login(c, "viewer1")
    assert c.post(f"/api/tasks/{task}/time", json={"hours": 1, "work_date": today}).status_code == 403
    _login(c, "member1")
    r = c.post(f"/api/tasks/{task}/time", json={"hours": 2.5, "work_date": today, "note": "검증"})
    assert r.status_code == 201 and r.json()["human_id"] == me  # 비우면 본인
    assert c.post(f"/api/tasks/{task}/time", json={"hours": 1, "work_date": today, "human_id": other}).status_code == 403
    for bad in ({"hours": 0, "work_date": today}, {"hours": 25, "work_date": today}, {"hours": True, "work_date": today},
                {"hours": 1, "work_date": (date.today() + timedelta(days=1)).isoformat()}, {"hours": 1, "work_date": "어제"}):
        assert c.post(f"/api/tasks/{task}/time", json=bad).status_code == 400, bad
    _login(c, "manager1")  # 배정된 누구의 시간이든
    assert c.post(f"/api/tasks/{task}/time", json={"hours": 4, "work_date": today, "human_id": other}).status_code == 201
    stranger = env["conn"].execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('배정 안 됨', 'QA', '[]')").lastrowid
    env["conn"].commit()
    assert c.post(f"/api/tasks/{task}/time", json={"hours": 1, "work_date": today, "human_id": stranger}).status_code == 400
    d = c.get(f"/api/tasks/{task}/time").json()
    assert d["logged_hours"] == 6.5 and d["estimate_hours"] == 10 and {h["id"] for h in d["loggable"]} == {me, other}


def test_participant_rule_on_read_and_delete(env):
    c, conn, task = env["c"], env["conn"], env["task"]
    _login(c, "manager1")
    eid = c.post(f"/api/tasks/{task}/time", json={"hours": 1, "work_date": date.today().isoformat(), "human_id": env["me"]}).json()["id"]
    _login(c, "viewer1")
    assert c.get(f"/api/tasks/{task}/time").status_code == 403  # 참가자 아님
    _login(c, "member1")
    d = c.get(f"/api/tasks/{task}/time").json()
    assert d["entries"][0]["can_delete"] is False and [h["id"] for h in d["loggable"]] == [env["me"]]
    assert c.delete(f"/api/time-entries/{eid}").status_code == 403  # 적은 사람·admin·manager만
    _login(c, "manager1")
    assert c.delete(f"/api/time-entries/{eid}").status_code == 200
    acts = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE subject_type = 'tasks' AND subject_id = ?", (task,))]
    assert "task_time_logged" in acts and "task_time_entry_deleted" in acts


def test_labor_cost_uses_logged_hours_where_present(env):
    c, conn, task = env["c"], env["conn"], env["task"]
    before = resource_lifecycle.task_costs(conn)[task]
    assert before["labor_estimated"] == pytest.approx(50 * 5 + 30 * 5) and before["labor_logged"] == 0
    _login(c, "member1")
    c.post(f"/api/tasks/{task}/time", json={"hours": 3, "work_date": date.today().isoformat()})
    c.post(f"/api/tasks/{task}/time", json={"hours": 1, "work_date": date.today().isoformat()})
    after = resource_lifecycle.task_costs(conn)[task]
    # 본인(50/h)은 기록 4시간, 동료(30/h)는 그대로 예상 몫 5시간
    assert after["labor_logged"] == pytest.approx(200) and after["labor_estimated"] == pytest.approx(150)
    assert after["logged_hours"] == 4 and after["total"] == pytest.approx(350 + after["ai_cost"])
    brief = next(b for b in resource_lifecycle.cost_overview(conn)["briefs"] if b["brief_id"] == env["bid"])
    assert brief["labor_logged"] == 200 and brief["logged_hours"] == 4 and brief["total"] == pytest.approx(350)
    # 예상 시간이 없어도 기록한 사람은 "예상 시간 없음"으로 치지 않는다
    conn.execute("UPDATE tasks SET estimate_hours = NULL WHERE id = ?", (task,))
    conn.commit()
    no_est = resource_lifecycle.task_costs(conn)[task]
    assert no_est["labor_logged"] == pytest.approx(200) and no_est["labor_missing_estimate"] is True  # 동료 몫만 비어 있음


def test_entries_cleaned_with_task_person_and_reset(env):
    c, conn = env["c"], env["conn"]
    _login(c, "manager1")
    today = date.today().isoformat()
    c.post(f"/api/tasks/{env['task']}/time", json={"hours": 2, "work_date": today, "human_id": env["other"]})
    conn.execute("UPDATE assignments SET status = 'cancelled' WHERE resource_id = ? AND resource_type = 'human'", (env["other"],))
    conn.commit()
    data_admin.delete_human(conn, _uid(conn, "admin1"), env["other"])
    assert conn.execute("SELECT COUNT(*) FROM task_time_entries").fetchone()[0] == 0
    c.post(f"/api/tasks/{env['task']}/time", json={"hours": 2, "work_date": today, "human_id": env["me"]})
    conn.execute("UPDATE assignments SET status = 'cancelled' WHERE task_id = ?", (env["task"],))
    conn.commit()
    data_admin.delete_brief(conn, _uid(conn, "admin1"), env["bid"])
    assert conn.execute("SELECT COUNT(*) FROM task_time_entries").fetchone()[0] == 0
    assert "task_time_entries" in data_admin._FACTORY_RESET_WIPE_TABLES
