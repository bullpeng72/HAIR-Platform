"""Phase 61 — Gate 구문검사 + 요구사항 추적성 자동 채점(사용자 요청,
2026-11-05, "업무 배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너
수준의 게임... 개선 계획을 수립해줘" → 로드맵 4번 "Gate 구문검사 +
요구사항 추적성 자동 채점... 설계 들어가줘").

Gate A-G는 순수 휴리스틱이라 "구현" 산출물이 실제로 파싱 가능한 코드인지,
"코드 검증" 산출물이 1단계 요구사항을 실제로 커버했는지 기계적으로
확인하지 않았다. 이 파일은 두 신규 점수를 검증한다:
1. `agent_evaluation.compute_code_syntax_check()` — 결정론적, 비용 0.
2. `requirements_traceability_llm.assess_traceability()` — LLM judge,
   `test_phase59_brief_decompose_llm.py`와 같은 fail-safe 계약.
3. `pipeline_runtime._execute_stage_attempt()` 통합 — **핵심 회귀**:
   `_gate_pass_ratio()`는 `gate_scores.values()`를 제네릭하게 순회하므로
   새 키가 기존 "코드 검증" 단계(`min_gate_pass_ratio=0.5`, 실제 차단
   임계값)의 통과/차단 판정을 조용히 바꾸지 않아야 한다 — 그래서
   `requirements_traceability`는 항상 `not_measured`를 강제하고,
   `code_syntax`는 "구현" 단계(`min_gate_pass_ratio=0.0`)에만 둔다.
4. `deliverables.run_adhoc_crew_job()` — Agent별 산출물 내용이 다르므로
   `code_syntax`는 crew 전체가 공유하는 dict가 아니라 deliverable별
   복사본에 들어가야 한다.

**비용/네트워크 발생 방지**: `test_phase59_brief_decompose_llm.py`와
같은 원칙 — `hair_platform.llm.call_llm`을 patch한다. pipeline_runtime
쪽은 `test_phase48_pipeline_runtime.py`와 같은 원칙 —
`hair_platform.crew_runtime.run_crew` 그 자체를 patch한다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import agent_evaluation, db
from hair_platform import pipeline_runtime as pr
from hair_platform import requirements_traceability_llm as rtl
from hair_platform.auth import Role, create_user

# ---------- agent_evaluation.compute_code_syntax_check() ----------


def test_code_syntax_check_valid_python_passes():
    content = "설명\n```python\ndef f():\n    return 1\n```\n"
    result = agent_evaluation.compute_code_syntax_check(content)
    assert result["status"] == "pass"
    assert result["passed"] is True
    assert result["not_measured"] is False
    assert result["checked_blocks"] == 1
    assert result["score"] == 1.0


def test_code_syntax_check_broken_python_fails_with_error_detail():
    content = "```python\ndef f(:\n    return 1\n```\n"
    result = agent_evaluation.compute_code_syntax_check(content)
    assert result["status"] == "fail"
    assert result["passed"] is False
    assert result["score"] == 0.0
    assert result["errors"] and "python" in result["errors"][0]


def test_code_syntax_check_js_block_checked_when_node_available(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_evaluation.shutil, "which", lambda name: "/usr/bin/node")

    class _Proc:
        returncode = 0
        stderr = ""

    with patch.object(agent_evaluation.subprocess, "run", return_value=_Proc()):
        content = "```javascript\nfunction f() { return 1; }\n```\n"
        result = agent_evaluation.compute_code_syntax_check(content)
    assert result["status"] == "pass"
    assert result["checked_blocks"] == 1


def test_code_syntax_check_js_block_skipped_when_node_unavailable():
    with patch.object(agent_evaluation.shutil, "which", return_value=None):
        with patch.object(agent_evaluation.subprocess, "run") as mock_run:
            content = "```javascript\nfunction f( { broken\n```\n"
            result = agent_evaluation.compute_code_syntax_check(content)
    mock_run.assert_not_called()
    assert result["status"] == "not_measured"
    assert result["checked_blocks"] == 0


def test_code_syntax_check_no_code_blocks_is_not_measured():
    result = agent_evaluation.compute_code_syntax_check("그냥 설명 텍스트입니다.")
    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["checked_blocks"] == 0


def test_code_syntax_check_unknown_language_tag_is_skipped():
    content = "```sql\nSELECT * FROM x WHERE;;; broken\n```\n"
    result = agent_evaluation.compute_code_syntax_check(content)
    assert result["status"] == "not_measured"
    assert result["checked_blocks"] == 0


# ---------- requirements_traceability_llm.assess_traceability() ----------


def _conn():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    return conn


def test_assess_traceability_success_computes_score(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = _conn()
    raw = json.dumps({"total_requirements": 4, "covered": 3, "missing": ["알림 기능"]})
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = rtl.assess_traceability(conn, "요구사항 문서", "구현 결과물")
    assert result["status"] == "fail"  # 일부만 커버
    assert result["passed"] is False
    assert result["not_measured"] is False
    assert result["score"] == 0.75
    assert result["total_requirements"] == 4
    assert result["covered"] == 3
    assert result["missing"] == ["알림 기능"]


def test_assess_traceability_full_coverage_passes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = _conn()
    raw = json.dumps({"total_requirements": 2, "covered": 2, "missing": []})
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = rtl.assess_traceability(conn, "요구사항", "구현")
    assert result["status"] == "pass"
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_assess_traceability_llm_failure_falls_back_to_not_measured():
    conn = _conn()
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("boom")):
        result = rtl.assess_traceability(conn, "요구사항", "구현")
    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["not_measured"] is True


def test_assess_traceability_bad_json_falls_back_to_not_measured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = _conn()
    with patch("hair_platform.llm.call_llm", return_value="이건 JSON이 아닙니다"):
        result = rtl.assess_traceability(conn, "요구사항", "구현")
    assert result["status"] == "not_measured"


def test_assess_traceability_zero_total_requirements_is_not_measured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = _conn()
    raw = json.dumps({"total_requirements": 0, "covered": 0, "missing": []})
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = rtl.assess_traceability(conn, "요구사항 없음", "구현")
    assert result["status"] == "not_measured"
    assert result["score"] is None


# ---------- pipeline_runtime 통합 — 핵심 회귀 ----------


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '테스트', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


def _run_crew_with_output(output_text, gate_scores=None):
    gate_scores = (
        gate_scores
        if gate_scores is not None
        else {"A": {"score": 0.9, "passed": True, "not_measured": False}}
    )

    def _fn(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process="sequential",
        provider=None,
        model=None,
        manager_agent_id=None,
        expected_outputs=None,
        self_review=False,
        memory=True,
    ):
        return {
            "final_output": output_text,
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": output_text,
                }
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
            "token_usage": {"total_tokens": 1},
        }

    return _fn


def test_impl_stage_adds_code_syntax_without_blocking_on_failure():
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "single",
        [("백엔드 개발자", "")],
        is_primary_deliverable=True,  # 2026-11-08 — 단계명 대신 이 플래그로 code_syntax를 건다.
    )

    broken_code = "설명\n```python\ndef f(:\n```\n"
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_run_crew_with_output(broken_code),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    # min_gate_pass_ratio=0.0(기본값)이라 code_syntax가 fail이어도 그대로 통과한다.
    assert stage_result["status"] == "passed"
    gate_scores = json.loads(stage_result["gate_scores"])
    assert gate_scores["code_syntax"]["status"] == "fail"
    assert gate_scores["code_syntax"]["passed"] is False
    assert "requirements_traceability" not in gate_scores  # "구현" 단계엔 추가 안 함


def test_code_review_stage_requirements_traceability_forced_not_measured_does_not_change_pass_ratio():
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")])
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "코드 검증",
        "single",
        [("코드 검증가", "")],
        min_gate_pass_ratio=0.5,
        checks_requirements_traceability=True,  # 2026-11-08 — 단계명 대신 이 플래그로 건다.
    )

    gate_scores_stage2 = {"A": {"score": 0.9, "passed": True, "not_measured": False}}
    # 판정가가 "fail"을 반환해도(일반적으로는 ratio를 깎을 수 있는 값) —
    # 호출부가 반드시 not_measured를 강제해야 통과/차단이 안 바뀐다.
    failing_traceability = {
        "score": 0.2,
        "status": "fail",
        "not_measured": False,
        "passed": False,
        "total_requirements": 5,
        "covered": 1,
        "missing": ["많이 빠짐"],
    }
    with (
        patch(
            "hair_platform.crew_runtime.run_crew",
            side_effect=[
                _run_crew_with_output("요구사항 문서")(
                    conn, [1], ["x"], admin_id, "single", None, None, None, None
                ),
                _run_crew_with_output("코드 검증 결과", gate_scores_stage2)(
                    conn, [1], ["x"], admin_id, "single", None, None, None, None
                ),
            ],
        ),
        patch.object(
            rtl, "assess_traceability", return_value=dict(failing_traceability)
        ),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)  # stage 1
        pr.run_pipeline_stage_job(conn, run_id, admin_id)  # stage 2

    stage_results = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=? ORDER BY id",
        (run_id,),
    ).fetchall()
    assert len(stage_results) == 2
    stage2 = stage_results[1]
    # A만 측정 대상(1/1 통과)이므로 ratio=1.0 >= 0.5 → 통과해야 한다 —
    # judge가 "fail"을 말했어도 not_measured 강제 때문에 차단에 안 들어간다.
    assert stage2["status"] == "passed"
    gate_scores = json.loads(stage2["gate_scores"])
    assert gate_scores["requirements_traceability"]["not_measured"] is True
    assert gate_scores["requirements_traceability"]["score"] == 0.2  # 실제 점수는 보존
    assert "code_syntax" not in gate_scores  # "코드 검증" 단계엔 code_syntax 안 둠


def test_code_review_stage_without_seq1_prior_skips_requirements_traceability():
    """ "코드 검증"이 1단계 자체면(선행 단계 output 없음) 조회가 비어
    `requirements_traceability` 키 자체를 추가하지 않아야 한다."""
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "코드 검증",
        "single",
        [("코드 검증가", "")],
        min_gate_pass_ratio=0.5,
        checks_requirements_traceability=True,  # 2026-11-08 — 단계명 대신 이 플래그로 건다.
    )

    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_run_crew_with_output("코드 검증 결과"),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    gate_scores = json.loads(stage_result["gate_scores"])
    assert "requirements_traceability" not in gate_scores


# ---------- 2026-11-08: 단계명과 무관하게 플래그로 동작(일반화) ----------
# 사용자 요청("게임이 아닌 일상 사무 업무도 일반화") — 두 Gate 모두
# 예전엔 `stage["name"] == "구현"`/`"코드 검증"` 문자열 매칭이었다.
# 다른 도메인(사업기획서 등) 파이프라인이 전혀 다른 이름을 써도 똑같이
# 동작해야 한다는 걸 직접 증명한다 — "구현"/"코드 검증"이라는 이름을
# 의도적으로 피해서 짓는다.


def test_code_syntax_applies_to_any_primary_deliverable_stage_regardless_of_name():
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "기획서 초안 작성",  # "구현"이 아닌 이름 — SW 전용 가정이 없어야 함
        "single",
        [("기획서 작성자", "")],
        is_primary_deliverable=True,
    )

    broken_code = "설명\n```python\ndef f(:\n```\n"
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_run_crew_with_output(broken_code),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    gate_scores = json.loads(stage_result["gate_scores"])
    assert gate_scores["code_syntax"]["status"] == "fail"


def test_code_syntax_skipped_when_stage_not_flagged_even_if_named_구현():
    """반대 방향 회귀 — 이름이 "구현"이어도 `is_primary_deliverable`을
    안 켜면 더 이상 자동으로 code_syntax가 안 붙어야 한다(플래그가
    유일한 기준이라는 것 확인)."""
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(conn, admin_id, tid, "구현", "single", [("백엔드 개발자", "")])

    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_run_crew_with_output("```python\ndef f(:\n```\n"),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage_result = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=?", (run_id,)
    ).fetchone()
    gate_scores = json.loads(stage_result["gate_scores"])
    assert "code_syntax" not in gate_scores


def test_requirements_traceability_applies_to_any_flagged_stage_regardless_of_name():
    conn = _conn()
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")])
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "기획서 검토",  # "코드 검증"이 아닌 이름
        "single",
        [("품질 관리자", "")],
        min_gate_pass_ratio=0.5,
        checks_requirements_traceability=True,
    )

    gate_scores_stage2 = {"A": {"score": 0.9, "passed": True, "not_measured": False}}
    traceability = {
        "score": 0.8,
        "status": "pass",
        "not_measured": False,
        "passed": True,
        "total_requirements": 5,
        "covered": 4,
        "missing": ["항목 하나"],
    }
    with (
        patch(
            "hair_platform.crew_runtime.run_crew",
            side_effect=[
                _run_crew_with_output("요구사항 문서")(
                    conn, [1], ["x"], admin_id, "single", None, None, None, None
                ),
                _run_crew_with_output("검토 결과", gate_scores_stage2)(
                    conn, [1], ["x"], admin_id, "single", None, None, None, None
                ),
            ],
        ),
        patch.object(rtl, "assess_traceability", return_value=dict(traceability)),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage_results = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id=? ORDER BY id",
        (run_id,),
    ).fetchall()
    gate_scores = json.loads(stage_results[1]["gate_scores"])
    assert gate_scores["requirements_traceability"]["covered"] == 4
    assert gate_scores["requirements_traceability"]["not_measured"] is True


# ---------- deliverables.run_adhoc_crew_job() — 에이전트별 code_syntax ----------


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def test_run_adhoc_crew_job_adds_per_agent_code_syntax(client_and_conn):
    client, conn = client_and_conn
    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()[
        "id"
    ]

    resp = client.post("/api/briefs", json={"raw_text": "역할"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "역할", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")

    from hair_platform import deliverables

    job = deliverables.start_adhoc_crew_execution(
        conn, task_id, admin_id, [agent1, agent2], allow_supervisor_override=True
    )

    good_code = "```python\ndef f():\n    return 1\n```"
    broken_code = "```python\ndef g(:\n```"

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output("최종", [good_code, broken_code])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        deliverables.run_adhoc_crew_job(
            conn,
            job["job_by_agent_id"],
            task_id,
            admin_id,
            [agent1, agent2],
            ["작업1", "작업2"],
            "sequential",
            None,
            None,
            None,
        )

    rows = conn.execute(
        "SELECT d.author_id, ae.gate_scores FROM deliverables d "
        "JOIN agent_evaluations ae ON ae.deliverable_id = d.id "
        "WHERE d.task_id = ? ORDER BY d.author_id",
        (task_id,),
    ).fetchall()
    assert len(rows) == 2
    by_agent = {r["author_id"]: json.loads(r["gate_scores"]) for r in rows}
    assert by_agent[agent1]["code_syntax"]["status"] == "pass"
    assert by_agent[agent2]["code_syntax"]["status"] == "fail"
