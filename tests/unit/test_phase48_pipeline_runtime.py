"""Phase 48 — 단계형 개발 파이프라인(사용자 요청, 2026-10-18, "BMAD-METHOD
(https://github.com/bmad-code-org/bmad-method)와 하네스 방법론을 참조해,
최소의 HITL, HOTL으로 수준높은 개발 결과물 생성 가능한 개발 Agent 팀을
구성(기존의 개발팀 갱신)하고... 각 단계별 단독 또는 복수의 에이전트가
순차/병행 처리하며 개발 진행... 다른 업무에도 적용될 수 있도록 일반화").

**비용/네트워크 발생 방지(중요, test_phase12와 같은 원칙)**: `pipeline_
runtime`은 LLM을 직접 호출하지 않고 `crew_runtime.run_crew()`를 통해서만
호출한다 — 이 파일은 `crew_runtime.run_crew` 그 자체를 patch한다(그 아래
CrewAI/LLM은 건드리지 않는다, 애초에 호출되지 않는다).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, pipeline_runtime as pr
from hair_platform.app import create_app
from hair_platform.auth import Role, create_user


def _login(client, username="admin1", password="adminpass"):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '테트리스', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


def _good_run_crew(gate_scores=None, tcr=90.0):
    gate_scores = gate_scores or {"A": {"score": 0.9, "passed": True}, "B": {"not_measured": True}}

    def _fn(conn, agent_ids, task_descriptions, actor_id, process="sequential",
            provider=None, model=None, manager_agent_id=None, expected_outputs=None):
        return {
            "final_output": "최종산출물",
            "agents": [
                {"agent_id": aid, "role_name": "role", "name": None, "output": f"output-{aid}"}
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": tcr,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
            "token_usage": {"total_tokens": 1},
        }

    return _fn


# ---------- 템플릿/단계 CRUD ----------


def test_create_template_and_add_stages_round_trip():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)

    tid = pr.create_pipeline_template(conn, admin_id, "테스트 파이프라인", "설명", "software_dev")
    s1 = pr.add_stage(conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")],
                       requires_human_approval=True)
    s2 = pr.add_stage(conn, admin_id, tid, "설계", "sequential_relay",
                       [("AA", "AA 역할"), ("DA", "DA 역할")])
    s3 = pr.add_stage(conn, admin_id, tid, "구현", "parallel_delegated",
                       [("개발 계획 수립가", ""), ("개발자", "백엔드")],
                       lead_role_name="개발 계획 수립가")

    tpl = pr.get_pipeline_template(conn, tid)
    assert [s["seq_order"] for s in tpl["stages"]] == [1, 2, 3]
    assert tpl["stages"][0]["requires_human_approval"] is True
    assert [a["role_name"] for a in tpl["stages"][1]["agents"]] == ["AA", "DA"]
    assert tpl["stages"][2]["lead_role_name"] == "개발 계획 수립가"

    # 역할 1개 제약(단독), 리드 포함 2개 이상 제약(병행 위임)
    with pytest.raises(ValueError, match="single_mode_requires_exactly_one_agent"):
        pr.add_stage(conn, admin_id, tid, "x", "single", [("a", ""), ("b", "")])
    with pytest.raises(ValueError, match="parallel_delegated_requires_at_least_one_coworker"):
        pr.add_stage(conn, admin_id, tid, "y", "parallel_delegated", [("a", "")], lead_role_name="a")

    pr.reorder_stage(conn, admin_id, s2, "down")
    tpl2 = pr.get_pipeline_template(conn, tid)
    assert [s["name"] for s in tpl2["stages"]] == ["요구분석", "구현", "설계"]
    pr.reorder_stage(conn, admin_id, s2, "up")
    tpl3 = pr.get_pipeline_template(conn, tid)
    assert [s["name"] for s in tpl3["stages"]] == ["요구분석", "설계", "구현"]

    pr.update_stage(conn, admin_id, s1, instructions="갱신된 지시문")
    assert pr.get_pipeline_template(conn, tid)["stages"][0]["instructions"] == "갱신된 지시문"

    pr.delete_stage(conn, admin_id, s3)
    assert len(pr.get_pipeline_template(conn, tid)["stages"]) == 2

    pr.delete_pipeline_template(conn, admin_id, tid)
    assert pr.get_pipeline_template(conn, tid) is None


def test_default_software_dev_pipeline_is_seeded_idempotently():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 재시드해도 중복 안 됨
    templates = [t for t in pr.list_pipeline_templates(conn) if t["name"] == "소프트웨어 개발 파이프라인"]
    assert len(templates) == 1
    tpl = pr.get_pipeline_template(conn, templates[0]["id"])
    assert len(tpl["stages"]) == 7
    modes = [s["mode"] for s in tpl["stages"]]
    assert modes == [
        "single", "sequential_relay", "single", "parallel_delegated",
        "single", "sequential_relay", "single",
    ]
    assert tpl["stages"][0]["requires_human_approval"] is True
    assert all(not s["requires_human_approval"] for s in tpl["stages"][1:])
    impl_stage = tpl["stages"][3]
    assert impl_stage["lead_role_name"] == "개발 계획 수립가"
    assert len(impl_stage["agents"]) == 12  # 리드 1 + 개발자 10 + UI 기획자 1


# ---------- 실행 엔진: 3가지 mode ----------


def test_single_mode_stage_requiring_approval_pauses_run():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p1", "", "x")
    pr.add_stage(conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")],
                 requires_human_approval=True)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"
    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    assert stage_result["status"] == "passed"
    deliverable = conn.execute(
        "SELECT * FROM deliverables WHERE id=?", (stage_result["deliverable_id"],)
    ).fetchone()
    assert deliverable["status"] == "pending_review"
    approval = conn.execute(
        "SELECT * FROM approvals WHERE subject_type='pipeline_run_stage'"
    ).fetchone()
    assert approval is not None and approval["subject_id"] == stage_result["id"]


def test_sequential_relay_then_parallel_delegated_auto_advance_without_approval():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p2", "", "x")
    pr.add_stage(conn, admin_id, tid, "설계", "sequential_relay",
                 [("AA", "AA 역할"), ("DA", "DA 역할")])
    pr.add_stage(conn, admin_id, tid, "구현", "parallel_delegated",
                 [("개발 계획 수립가", ""), ("개발자", "백엔드")],
                 lead_role_name="개발 계획 수립가")

    calls = []

    def _tracking_run_crew(conn, agent_ids, task_descriptions, actor_id, process="sequential",
                            provider=None, model=None, manager_agent_id=None, expected_outputs=None):
        calls.append({"agent_ids": agent_ids, "process": process, "manager_agent_id": manager_agent_id,
                      "descriptions": task_descriptions, "expected_outputs": expected_outputs})
        return _good_run_crew()(conn, agent_ids, task_descriptions, actor_id, process, provider,
                                 model, manager_agent_id, expected_outputs)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_tracking_run_crew):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    assert len(calls) == 2
    assert calls[0]["process"] == "sequential"
    assert calls[0]["manager_agent_id"] is None
    assert calls[1]["process"] == "hierarchical"
    assert calls[1]["manager_agent_id"] == calls[1]["agent_ids"][0]  # 리드가 매니저로 지정됨

    deliverables_rows = conn.execute(
        "SELECT * FROM deliverables WHERE task_id=?", (task_id,)
    ).fetchall()
    assert len(deliverables_rows) == 2
    assert all(d["status"] == "approved" for d in deliverables_rows)  # 승인 불필요 단계는 자동 승인


def test_is_primary_deliverable_round_trips_through_add_stage_and_template():
    """사용자 보고(2026-10-19, "최종 결과물에 코드가 아니라 품질 리포트만
    있다") — `is_primary_deliverable`이 `add_stage()` → `get_pipeline_
    template()` → `update_stage()`까지 제대로 저장/반영되는지 확인."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)

    tid = pr.create_pipeline_template(conn, admin_id, "p-primary", "", "x")
    s1 = pr.add_stage(conn, admin_id, tid, "설계", "single", [("AA", "")])
    s2 = pr.add_stage(conn, admin_id, tid, "구현", "single", [("개발자", "")],
                       is_primary_deliverable=True)

    tpl = pr.get_pipeline_template(conn, tid)
    assert tpl["stages"][0]["is_primary_deliverable"] is False
    assert tpl["stages"][1]["is_primary_deliverable"] is True

    pr.update_stage(conn, admin_id, s1, is_primary_deliverable=True)
    pr.update_stage(conn, admin_id, s2, is_primary_deliverable=False)
    tpl2 = pr.get_pipeline_template(conn, tid)
    assert tpl2["stages"][0]["is_primary_deliverable"] is True
    assert tpl2["stages"][1]["is_primary_deliverable"] is False


def test_primary_deliverable_stage_output_identifiable_via_list_pipeline_runs_for_task():
    """위와 같은 사용자 보고의 핵심 — Task 워크스페이스가 폴링하는
    `list_pipeline_runs_for_task()`의 `stage_results`에서 "구현" 단계의
    deliverable만 `is_primary_deliverable=True`로 식별 가능해야, 맨 나중에
    도는 검증/판정 단계의 결과물이 아니라 실제 구현 결과물을 "최종
    결과물"로 고를 수 있다(webui.py `renderCollabWorkspace`가 바로 이
    필드로 고른다)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p3", "", "x")
    pr.add_stage(conn, admin_id, tid, "구현", "single", [("개발자", "")],
                 is_primary_deliverable=True)
    pr.add_stage(conn, admin_id, tid, "품질 관리 최종 판정", "single", [("코드 검증가", "")])

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    runs = pr.list_pipeline_runs_for_task(conn, task_id)
    assert len(runs) == 1
    stage_results = runs[0]["stage_results"]
    assert len(stage_results) == 2
    primary = [sr for sr in stage_results if sr["is_primary_deliverable"]]
    assert len(primary) == 1
    assert primary[0]["stage_name"] == "구현"
    assert primary[0]["deliverable_id"] is not None
    non_primary = [sr for sr in stage_results if not sr["is_primary_deliverable"]]
    assert non_primary[0]["stage_name"] == "품질 관리 최종 판정"
    # 품질 판정 단계가 시간상 나중이라도(created_at 비교로는 걸러지지 않음)
    # is_primary_deliverable 플래그로 구현 단계의 deliverable_id를 구분해낼 수 있다.
    assert primary[0]["deliverable_id"] != non_primary[0]["deliverable_id"]


def test_expected_output_round_trips_through_add_stage_and_update_stage():
    """사용자 요청(2026-10-20, "고급 에이전트 관리") —
    `pipeline_stages.expected_output`이 `add_stage()`→`get_pipeline_
    template()`→`update_stage()`까지 제대로 저장/반영되는지 확인
    (`is_primary_deliverable`과 같은 패턴)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)

    tid = pr.create_pipeline_template(conn, admin_id, "p-expected-output", "", "x")
    s1 = pr.add_stage(conn, admin_id, tid, "설계", "single", [("AA", "")])
    s2 = pr.add_stage(
        conn, admin_id, tid, "QA 기능테스트", "single", [("QA", "")],
        expected_output="실제로 실행 가능한 테스트 코드와 통과/실패 리포트",
    )

    tpl = pr.get_pipeline_template(conn, tid)
    assert tpl["stages"][0]["expected_output"] == ""  # 기본값
    assert tpl["stages"][1]["expected_output"] == "실제로 실행 가능한 테스트 코드와 통과/실패 리포트"

    pr.update_stage(conn, admin_id, s1, expected_output="설계 문서(ERD/API 스펙)")
    tpl2 = pr.get_pipeline_template(conn, tid)
    assert tpl2["stages"][0]["expected_output"] == "설계 문서(ERD/API 스펙)"

    # s2를 빈 문자열로 되돌리면 플랫폼 공통 기본값으로 폴백(crew_runtime.
    # run_crew()가 falsy 체크로 처리 — 여기선 저장값만 확인).
    pr.update_stage(conn, admin_id, s2, expected_output="")
    tpl3 = pr.get_pipeline_template(conn, tid)
    assert tpl3["stages"][1]["expected_output"] == ""


def test_execute_stage_attempt_passes_stage_expected_output_to_run_crew():
    """단계에 저장한 `expected_output`이 실제로 `crew_runtime.run_crew()`
    호출의 `expected_outputs` 인자로 넘어가는지 — 참여 Agent 수만큼
    같은 값이 반복돼야 한다(`_execute_stage_attempt()`의 `task_
    descriptions`와 같은 모양)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p-eo-passthrough", "", "x")
    pr.add_stage(
        conn, admin_id, tid, "구현", "sequential_relay",
        [("AA", ""), ("개발자", "")],
        expected_output="동작하는 코드 한 벌",
    )

    calls = []

    def _tracking(conn, agent_ids, task_descriptions, actor_id, process="sequential",
                  provider=None, model=None, manager_agent_id=None, expected_outputs=None):
        calls.append(expected_outputs)
        return _good_run_crew()(conn, agent_ids, task_descriptions, actor_id, process,
                                 provider, model, manager_agent_id, expected_outputs)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_tracking):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 1
    assert calls[0] == ["동작하는 코드 한 벌", "동작하는 코드 한 벌"]


def test_gate_failure_retries_with_feedback_then_escalates():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p3", "", "x")
    pr.add_stage(conn, admin_id, tid, "코드검증", "single", [("코드 검증가", "")],
                 max_retry=2, min_gate_pass_ratio=0.9)

    bad_gate = {"A": {"score": 0.1, "passed": False}}
    descriptions_seen = []

    def _bad_run_crew(conn, agent_ids, task_descriptions, actor_id, process="sequential",
                       provider=None, model=None, manager_agent_id=None, expected_outputs=None):
        descriptions_seen.append(task_descriptions[0])
        return _good_run_crew(gate_scores=bad_gate, tcr=10.0)(
            conn, agent_ids, task_descriptions, actor_id, process, provider, model,
            manager_agent_id, expected_outputs,
        )

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_bad_run_crew):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(descriptions_seen) == 2  # max_retry=2
    assert "이전 시도" in descriptions_seen[1] and "A(0.1)" in descriptions_seen[1]

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"
    stages = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=? ORDER BY attempt", (run_id,)
    ).fetchall()
    assert [s["status"] for s in stages] == ["failed_retrying", "escalated"]
    approval = conn.execute("SELECT * FROM approvals WHERE subject_type='pipeline_run_stage'").fetchone()
    assert approval["subject_id"] == stages[-1]["id"]

    # 반려(재시도 소진) → run 완전 실패
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_bad_run_crew):
        pr.resolve_stage_decision(conn, admin_id, approval["subject_id"], approved=False)
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "failed"


def test_resolve_stage_decision_approve_advances_to_next_stage():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p4", "", "x")
    pr.add_stage(conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")],
                 requires_human_approval=True)
    pr.add_stage(conn, admin_id, tid, "s2", "single", [("AA", "")])

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)
        approval = conn.execute(
            "SELECT * FROM approvals WHERE subject_type='pipeline_run_stage'"
        ).fetchone()
        approved_run_stage = conn.execute(
            "SELECT * FROM pipeline_run_stages WHERE id=?", (approval["subject_id"],)
        ).fetchone()
        returned_run_id = pr.resolve_stage_decision(
            conn, admin_id, approval["subject_id"], approved=True
        )
        assert returned_run_id == run_id
        run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        assert run["status"] == "running"
        assert run["current_stage_seq"] == 2
        # 실측 버그(2026-10-19, "내게 필요한 조치"에 같은 업무가 중복
        # 표시됨) — 사람이 파이프라인 단계를 승인했으면 그 단계의
        # deliverable도 'pending_review'에 머물지 않고 함께 'approved'로
        # 바뀌어야 한다(안 바뀌면 inbox의 low_gate_score 항목이 계속
        # "아직 조치 필요"로 집어옴).
        d = conn.execute(
            "SELECT status FROM deliverables WHERE id=?",
            (approved_run_stage["deliverable_id"],),
        ).fetchone()
        assert d["status"] == "approved"
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "completed"


def test_resolve_stage_decision_reject_also_rejects_the_deliverable():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p5", "", "x")
    pr.add_stage(conn, admin_id, tid, "s1", "single", [("요구사항 분석가", "")],
                 requires_human_approval=True, max_retry=0)

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)
        approval = conn.execute(
            "SELECT * FROM approvals WHERE subject_type='pipeline_run_stage'"
        ).fetchone()
        rejected_run_stage = conn.execute(
            "SELECT * FROM pipeline_run_stages WHERE id=?", (approval["subject_id"],)
        ).fetchone()
        pr.resolve_stage_decision(conn, admin_id, approval["subject_id"], approved=False)

    d = conn.execute(
        "SELECT status FROM deliverables WHERE id=?",
        (rejected_run_stage["deliverable_id"],),
    ).fetchone()
    assert d["status"] == "rejected"


# ---------- HTTP 엔드포인트(사용자 요청, "화면에서 단계를 직접 구성하는
# 빌더까지") ----------


def test_pipeline_builder_http_endpoints_round_trip():
    app = create_app(":memory:")
    conn = app.state.db
    create_user(conn, "admin1", "adminpass", Role.ADMIN)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        _login(client)
        r = client.post("/api/pipeline-templates", json={
            "name": "HTTP 테스트 파이프라인", "description": "d", "domain": "software_dev",
        })
        assert r.status_code == 201, r.text
        template_id = r.json()["id"]

        r = client.post(f"/api/pipeline-templates/{template_id}/stages", json={
            "name": "요구분석", "mode": "single",
            "agents": [{"role_name": "요구사항 분석가", "instructions": ""}],
            "requires_human_approval": True,
        })
        assert r.status_code == 201, r.text
        stage1_id = r.json()["id"]

        r = client.post(f"/api/pipeline-templates/{template_id}/stages", json={
            "name": "설계", "mode": "sequential_relay",
            "agents": [{"role_name": "AA", "instructions": "a"}, {"role_name": "DA", "instructions": "b"}],
        })
        assert r.status_code == 201, r.text

        r = client.get(f"/api/pipeline-templates/{template_id}")
        assert r.status_code == 200
        detail = r.json()
        assert len(detail["stages"]) == 2

        r = client.post(
            f"/api/pipeline-templates/{template_id}/stages/{stage1_id}/reorder",
            json={"direction": "down"},
        )
        assert r.status_code == 200
        detail = client.get(f"/api/pipeline-templates/{template_id}").json()
        assert detail["stages"][0]["name"] == "설계"

        task_id = _make_task(conn)
        with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
            r = client.post(f"/api/tasks/{task_id}/execute-pipeline",
                             json={"pipeline_template_id": template_id})
            assert r.status_code == 202, r.text
            run_id = r.json()["run_id"]

        r = client.get(f"/api/tasks/{task_id}/pipeline-runs")
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) == 1
        assert runs[0]["id"] == run_id
        # 1단계(설계, 승인 불필요)는 자동 통과하고 2단계(요구분석, 승인
        # 필요)에서 멈춰 있어야 한다 — TestClient는 BackgroundTasks를
        # 응답과 같은 요청 안에서 동기적으로 실행한다.
        assert runs[0]["status"] == "paused_for_approval"
        statuses = [sr["status"] for sr in runs[0]["stage_results"]]
        assert statuses == ["passed", "passed"]  # 1단계 통과 + 2단계(승인 대상) 통과 기록

        # 자기 승인 방지(decide_approval의 self-approval 차단과 같은
        # 원칙) — 파이프라인을 실행시킨 admin1 본인은 이 결재함에서
        # 제외된다, 다른 관리자(reviewer1)가 대신 확인·승인한다.
        create_user(conn, "reviewer1", "reviewpass", Role.ADMIN)
        _login(client, "reviewer1", "reviewpass")
        r = client.get("/api/approvals")
        assert r.status_code == 200
        inbox = r.json()
        pipeline_approval = next(
            a for a in inbox if a["subject_type"] == "pipeline_run_stage"
        )
        assert "요구분석" in pipeline_approval.get("resource_name", "")

        with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
            r = client.post(
                f"/api/approvals/{pipeline_approval['id']}/decide",
                json={"approve": True},
            )
            assert r.status_code == 200, r.text

        r = client.get(f"/api/tasks/{task_id}/pipeline-runs")
        runs = r.json()
        assert runs[0]["status"] == "completed"
