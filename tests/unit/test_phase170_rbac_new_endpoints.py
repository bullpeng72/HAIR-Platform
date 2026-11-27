"""RBAC 골든셋 — 2026-11-27~28에 추가한 엔드포인트 전부(rbac-permission-audit 스킬 절차).

엔드포인트마다 (1) 막혀야 하는 가장 높은 역할로 403, (2) 허용되는 가장 낮은 역할로 성공을 확인한다. 정본은 각 라우트의
`Depends(require_role(...))`다. "권한은 있지만 이 자원은 안 된다"(본인만·요청자만·참가자만·토큰)는 별도 케이스로 둔다.

역할 사다리: viewer < member < manager < admin.
- A·M(admin·manager): member 403 → manager 성공
- A(admin 전용): manager 403 → admin 성공
- A·M·Member(본인 것): viewer 403 → member(본인) 성공 + member(남의 것) 403
"""

from __future__ import annotations

from datetime import date

import pytest

from hair_platform import approvals, process_learning
from hair_platform import pipeline_runtime as pr

PW = {"admin1": "adminpass", "manager1": "managerpass", "member1": "memberpass", "viewer1": "viewerpass"}
DENY_ALLOW = {"AM": ("member1", "manager1"), "A": ("manager1", "admin1")}


def _login(client, name):
    assert client.post("/login", json={"username": name, "password": PW[name]}).status_code == 200


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _check(client, rule, method, url, body=None, ok=(200, 201, 202)):
    deny, allow = DENY_ALLOW[rule]
    _login(client, deny)
    r = client.request(method, url, json=body)
    assert r.status_code == 403, (deny, method, url, r.status_code, r.text[:200])
    _login(client, allow)
    r = client.request(method, url, json=body)
    assert r.status_code in ok, (allow, method, url, r.status_code, r.text[:300])
    return r


@pytest.fixture
def env(client_and_conn):
    client, conn = client_and_conn
    admin, member = _uid(conn, "admin1"), _uid(conn, "member1")
    conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('검토자', '[]', 'active')")
    tid = pr.create_pipeline_template(conn, admin, "RBAC 파이프라인")
    sid = pr.add_stage(conn, admin, tid, "검토", "single", [("검토자", "")], requires_human_approval=True)
    me = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('본인', 'QA', '[\"python\"]', ?)",
                      (member,)).lastrowid
    other = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('남', 'QA', '[\"python\"]')").lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'rbac')", (member,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'QA')", (bid,)).lastrowid
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
                       "VALUES (?, ?, ?, 'paused_for_approval', 1)", (tid, task, member)).lastrowid
    rs = conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, output, gate_scores) "
                      "VALUES (?, ?, 'passed', 'x', '{}')", (run, sid)).lastrowid
    approvals.create_approval_chain(conn, member, "pipeline_run_stage", rs)
    conn.commit()
    aid = conn.execute("SELECT id FROM approvals WHERE subject_id = ? AND subject_type = 'pipeline_run_stage'", (rs,)).fetchone()[0]
    return {"client": client, "conn": conn, "admin": admin, "member": member, "tid": tid, "sid": sid, "me": me,
            "other": other, "bid": bid, "task": task, "rs": rs, "aid": aid}


def _lesson(e, text="규칙을 지키세요 테스트"):
    process_learning.capture_rejection(e["conn"], e["rs"], text)
    e["conn"].commit()
    return e["conn"].execute("SELECT id FROM stage_lessons WHERE text = ?", (text,)).fetchone()[0]


# ------------------------------------------------------------------ 프로세스 성과·학습·결재 정책(A·M, 일부 A)

def test_process_metrics_and_learning(env):
    c, tid, sid = env["client"], env["tid"], env["sid"]
    for url in ("/api/process-metrics", f"/api/process-metrics/templates/{tid}", f"/api/process-metrics/templates/{tid}/lessons",
                f"/api/process-metrics/templates/{tid}/examples", f"/api/process-metrics/templates/{tid}/changes",
                f"/api/process-metrics/templates/{tid}/approval-policy", "/api/capacity"):
        _check(c, "AM", "GET", url)
    _check(c, "AM", "PUT", f"/api/process-metrics/templates/{tid}/baseline", {"minutes": 60})
    _check(c, "A", "PUT", "/api/process-metrics/hourly-cost", {"hourly_cost": 40})
    _check(c, "A", "PUT", f"/api/process-metrics/templates/{tid}/approval-policy", {"auto_approve_low_risk": False})
    _check(c, "AM", "POST", f"/api/process-metrics/stages/{sid}/lessons", {"text": "결론을 먼저 쓰세요"}, ok=(201,))
    _check(c, "AM", "POST", f"/api/process-lessons/{_lesson(env, '표로 정리하세요 하나')}/activate", {})
    _check(c, "AM", "POST", f"/api/process-lessons/{_lesson(env, '표로 정리하세요 둘')}/dismiss")
    active = _lesson(env, "표로 정리하세요 셋")
    process_learning.activate_lesson(env["conn"], env["admin"], active)
    _check(c, "AM", "POST", f"/api/process-lessons/{active}/retire")
    env["conn"].execute("INSERT INTO stage_examples (stage_id, run_stage_id, excerpt) VALUES (?, 777001, 'x')", (sid,))
    env["conn"].commit()
    ex = env["conn"].execute("SELECT id FROM stage_examples WHERE run_stage_id = 777001").fetchone()[0]
    _check(c, "AM", "POST", f"/api/process-examples/{ex}/dismiss")
    _login(c, "admin1")
    c.patch(f"/api/pipeline-templates/{tid}/stages/{sid}", json={"max_retry": 4})
    ch = c.get(f"/api/process-metrics/templates/{tid}/changes").json()[0]["id"]
    _check(c, "AM", "POST", f"/api/process-changes/{ch}/revert")


def test_approval_brief_requester_or_decider(env):
    c, aid = env["client"], env["aid"]
    _login(c, "viewer1")  # 요청자도 결정자도 아님
    assert c.get(f"/api/approvals/{aid}/brief").status_code == 403
    _login(c, "member1")  # 요청자 본인
    assert c.get(f"/api/approvals/{aid}/brief").status_code == 200
    _login(c, "manager1")
    assert c.get(f"/api/approvals/{aid}/brief").status_code == 200


def test_pipeline_drafting(env):
    from unittest.mock import patch

    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("no llm")):
        _check(env["client"], "AM", "POST", "/api/pipeline-templates/draft", {"description": "계약서 위험 조항을 검토하는 업무입니다"})
    body = {"name": "RBAC 초안", "stages": [{"name": "a", "roles": ["검토자"]}, {"name": "b", "roles": ["검토자"]}]}
    _check(env["client"], "AM", "POST", "/api/pipeline-templates/from-draft", body, ok=(201,))


# ------------------------------------------------------------------ 외부 입력·실행 동작

def test_inbound_triggers_and_webhook_token(env):
    c, tid = env["client"], env["tid"]
    body = {"name": "rbac", "source": "webhook", "action_template_id": tid, "role_name": "QA", "brief_text": "회신"}
    t = _check(c, "AM", "POST", "/api/inbound-triggers", body, ok=(201,)).json()
    _check(c, "AM", "GET", "/api/inbound-triggers")
    _check(c, "AM", "GET", f"/api/inbound-triggers/{t['id']}/events")
    _check(c, "AM", "PATCH", f"/api/inbound-triggers/{t['id']}", {"active": True})
    # 웹훅 받기는 로그인 없이 토큰으로만
    c.post("/logout")
    c.cookies.clear()
    assert c.post(t["webhook_path"], json={"id": "x"}).status_code == 401
    assert c.post(t["webhook_path"], json={"id": "x"}, headers={"X-HAIR-Token": "wrong"}).status_code == 401
    _check(c, "AM", "DELETE", f"/api/inbound-triggers/{t['id']}")


def test_stage_actions_admin_only(env):
    c, sid = env["client"], env["sid"]
    _check(c, "A", "GET", "/api/stage-actions")
    _check(c, "A", "PUT", f"/api/pipeline-stages/{sid}/action", {"kind": "chat", "config": {"url": "https://hooks.example.com/x"}})
    _check(c, "A", "DELETE", f"/api/pipeline-stages/{sid}/action")


# ------------------------------------------------------------------ 자원(용량·운용·인계·비용·분석·만약에·가져오기)

def test_resource_admin_manager_endpoints(env):
    c, me = env["client"], env["me"]
    for url in ("/api/resource-load", "/api/resource-planning", "/api/resource-costs", "/api/resource-insights",
                f"/api/human-pool/{me}/handover", "/api/approval-delegations"):
        _check(c, "AM", "GET", url)
    _check(c, "AM", "PUT", f"/api/human-pool/{me}/capacity", {"max_concurrent_tasks": 4})
    _check(c, "AM", "PUT", f"/api/human-pool/{me}/weekly-hours", {"weekly_hours": 32})
    _check(c, "AM", "PUT", f"/api/briefs/{env['bid']}/budget", {"budget_usd": 100})
    _check(c, "AM", "POST", "/api/resource-what-if", {"add_people": [{"role": "QA", "count": 1}]})
    _check(c, "AM", "POST", f"/api/users/{env['member']}/transfer-approvals", {"to_user_id": None})
    d = _check(c, "AM", "POST", "/api/approval-delegations",
               {"delegate_id": env["admin"], "starts_on": date.today().isoformat(), "ends_on": date.today().isoformat()},
               ok=(201,)).json()
    _check(c, "AM", "DELETE", f"/api/approval-delegations/{d['id']}")


def test_resource_admin_only_endpoints(env):
    c, me = env["client"], env["me"]
    _check(c, "A", "PUT", f"/api/human-pool/{me}/hourly-rate", {"hourly_rate": 40})
    _check(c, "A", "PUT", "/api/approval-escalation", {"days": None})
    _check(c, "A", "POST", "/api/human-pool/import", {"kind": "people", "csv": "employee_id,name,role\n20260001,a,b\n"})


def test_member_self_service_absences_and_skills(env):
    c, me, other = env["client"], env["me"], env["other"]
    today = date.today().isoformat()
    body = {"starts_on": today, "ends_on": today}
    _login(c, "viewer1")
    assert c.post(f"/api/human-pool/{me}/absences", json=body).status_code == 403
    assert c.get("/api/resource-absences").status_code == 403
    assert c.put(f"/api/skill-levels/human/{me}", json={"skill": "python", "level": 2}).status_code == 403
    _login(c, "member1")
    assert c.post(f"/api/human-pool/{me}/absences", json=body).status_code == 201  # 본인
    assert c.post(f"/api/human-pool/{other}/absences", json=body).status_code == 403  # 남의 것
    mine = c.get("/api/resource-absences").json()
    assert len(mine) == 1 and c.delete(f"/api/resource-absences/{mine[0]['id']}").status_code == 200
    assert c.put(f"/api/skill-levels/human/{me}", json={"skill": "python", "level": 2}).status_code == 200
    assert c.put(f"/api/skill-levels/human/{other}", json={"skill": "python", "level": 2}).status_code == 403
    assert c.get(f"/api/skill-levels/human/{me}").status_code == 200
    assert c.get(f"/api/skill-levels/human/{other}").status_code == 403
    assert c.get("/api/human-pool/me").json()["id"] == me
    _login(c, "manager1")  # 남의 부재 지우기는 admin·manager
    c.post(f"/api/human-pool/{other}/absences", json=body)
    oid = c.get("/api/resource-absences").json()[0]["id"]
    _login(c, "member1")
    assert c.delete(f"/api/resource-absences/{oid}").status_code == 403


def test_task_schedule_participant_rule(env):
    c, conn, task = env["client"], env["conn"], env["task"]
    _login(c, "member1")  # 아직 참가자 아님
    assert c.patch(f"/api/tasks/{task}/schedule", json={"estimate_hours": 3}).status_code == 403
    assert c.get(f"/api/tasks/{task}/schedule").status_code == 403
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, 1, 'confirmed')",
                 (task, env["me"]))
    conn.commit()
    assert c.patch(f"/api/tasks/{task}/schedule", json={"estimate_hours": 3}).status_code == 200
    _login(c, "viewer1")
    assert c.patch(f"/api/tasks/{task}/schedule", json={"estimate_hours": 3}).status_code == 403
    _login(c, "manager1")
    assert c.patch(f"/api/tasks/{task}/schedule", json={"due_date": None}).status_code == 200
