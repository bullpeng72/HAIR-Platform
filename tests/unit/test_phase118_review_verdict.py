"""2026-11-25 실측(계약서·영업 파이프라인): 검토 단계의 '## 판정 — 보완 요청'이 무시되고 그대로 완료되던 문제."""

from __future__ import annotations

from hair_platform import db, skill_catalog
from hair_platform import pipeline_runtime as pr


def test_review_verdict_reads_only_the_verdict_section():
    rework = pr.review_verdict("## 판정\n\n**보완 요청** — 제목이 확약처럼 보임\n\n## 요구사항 충족\n| R1 | O |")
    assert rework["passed"] is False and rework["verdict"] == "보완 요청" and not rework["not_measured"]
    ok = pr.review_verdict("## 판정\n승인 — 요구사항 6개 모두 충족\n\n## 보완 사항\n보완 요청할 것 없음")
    assert ok["passed"] is True and ok["verdict"] == "승인"
    assert pr.review_verdict("## 검토 요약\n보완 요청")["not_measured"] is True  # 판정 절 없음 → 막지 않음
    assert pr.review_verdict("### 최종 판정\n보완 요청(승인 불가)")["verdict"] == "보완 요청"


def test_stage_with_return_target_fails_on_rework_verdict_and_goes_back():
    """Gate가 모두 통과해도 '## 판정'이 보완 요청이면 미통과 → 지정된 작성 단계로 되돌아가 다시 쓴다."""
    from unittest.mock import patch

    from hair_platform.auth import Role, create_user

    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "admin_verdict", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "verdict", "", "general")
    write = pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    pr.add_stage(conn, admin, tid, "검토", "single", [("문서 검토자", "")],
                 gate_ids=["A"], min_gate_pass_ratio=1.0, max_retry=2, on_fail_return_stage_id=write)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    roles = {r[0]: r[1] for r in conn.execute("SELECT id, role_name FROM agent_pool")}
    calls = []

    def fake_run_crew(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        role = roles[agent_ids[0]]
        calls.append((role, task_descriptions[0]))
        review_round = sum(1 for r, _ in calls if r == "문서 검토자")
        if role == "문서 검토자":
            output = ("## 판정\n**보완 요청** — 제목이 근거 없는 확약처럼 보임\n\n## 보완 사항\n제목 순화" if review_round == 1
                      else "## 판정\n승인 — 요구사항 모두 충족")
        else:
            output = f"{role} 산출물 {len(calls)}"
        return {"final_output": output,
                "agents": [{"agent_id": agent_ids[0], "role_name": role, "name": None, "output": output}],
                "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}

    with patch("hair_platform.crew_runtime.run_crew", side_effect=fake_run_crew):
        run_id = pr.start_pipeline_run(conn, admin, tid, task)
        pr.run_pipeline_stage_job(conn, run_id, admin)

    assert [r for r, _ in calls] == ["기획서 작성자", "문서 검토자", "기획서 작성자", "문서 검토자"]
    assert "제목이 근거 없는 확약처럼 보임" in calls[2][1]  # 검토 의견이 다시 쓰기에 전달됨
    rows = [tuple(r) for r in conn.execute(
        "SELECT ps.name, prs.attempt, prs.status FROM pipeline_run_stages prs JOIN pipeline_stages ps "
        "ON ps.id = prs.stage_id WHERE prs.pipeline_run_id = ? ORDER BY prs.id", (run_id,))]
    assert rows == [("작성", 1, "passed"), ("검토", 1, "failed_retrying"), ("작성", 2, "passed"), ("검토", 2, "passed")]
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "completed"


def test_reviewer_guide_says_verdict_is_reviewers_job_and_old_text_is_upgraded():
    guide = skill_catalog.SKILL_GUIDES["문서품질검토"]
    assert "판정은 검토자인 당신이 쓰는" in guide.constraints
    old = guide.constraints[: guide.constraints.index(" ⑤")]
    assert skill_catalog.is_replaceable("문서품질검토", "constraints", old)  # 고치지 않은 기존 DB 문구는 새 문구로 교체


def test_graph_engine_also_reworks_on_rework_verdict():
    """그래프 엔진도 같은 판정 — Gate가 모두 통과해도 '## 판정'이 보완 요청이면 작성 단계로 되돌아간다."""
    from unittest.mock import patch

    from langgraph.checkpoint.sqlite import SqliteSaver

    from hair_platform import execution_graph
    from hair_platform.auth import Role, create_user

    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "admin_verdict_g", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "verdict-graph", "", "general")
    write = pr.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    pr.add_stage(conn, admin, tid, "검토", "single", [("문서 검토자", "")],
                 gate_ids=["A"], min_gate_pass_ratio=1.0, max_retry=2, on_fail_return_stage_id=write)
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    roles = {r[0]: r[1] for r in conn.execute("SELECT id, role_name FROM agent_pool")}
    calls = []

    def fake_run_crew(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        role = roles[agent_ids[0]]
        calls.append(role)
        rounds = calls.count("문서 검토자")
        if role == "문서 검토자":
            output = "## 판정\n보완 요청 — 근거 부족" if rounds == 1 else "## 판정\n승인 — 모두 충족"
        else:
            output = f"초안 {len(calls)}"
        return {"final_output": output,
                "agents": [{"agent_id": agent_ids[0], "role_name": role, "name": None, "output": output}],
                "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}

    run_id = pr.start_pipeline_run(conn, admin, tid, task)
    with SqliteSaver.from_conn_string(":memory:") as saver, \
            patch("hair_platform.crew_runtime.run_crew", side_effect=fake_run_crew):
        execution_graph.run_pipeline_graph(conn, run_id, admin, saver)

    assert calls == ["기획서 작성자", "문서 검토자", "기획서 작성자", "문서 검토자"]
    rows = [tuple(r) for r in conn.execute(
        "SELECT ps.name, prs.attempt, prs.status FROM pipeline_run_stages prs JOIN pipeline_stages ps "
        "ON ps.id = prs.stage_id WHERE prs.pipeline_run_id = ? ORDER BY prs.id", (run_id,))]
    assert rows == [("작성", 1, "passed"), ("검토", 1, "failed_retrying"), ("작성", 2, "passed"), ("검토", 2, "passed")]
