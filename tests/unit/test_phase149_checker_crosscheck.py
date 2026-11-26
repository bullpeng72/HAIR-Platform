"""점검기와 AI 검토의 판정 대조(2026-11-27, 개선 계획 2-4 후반 — `checker_crosscheck.py`).

검토자가 "승인"했는데 최종본의 채용 공고를 점검기에 다시 넣으면 문제가 나오면, 어느 쪽이 맞는지 사람이 정한다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import checker_crosscheck, db
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user

CLEAN = ("## 채용 공고\n### 담당 업무\n백엔드 개발\n### 자격 요건\n경력 3년\n### 고용 형태\n정규직\n### 근무지\n서울\n"
         "### 근무 시간\n주 5일\n### 급여\n연봉 6천만 원\n### 전형 절차\n서류 → 면접\n### 지원 방법\n이메일 접수\n"
         "### 마감일\n12월 31일까지\n\n## 점검 결과\n| 고친 문구 | 원래 문구 |\n| 지원자 | 여성만 지원 가능 |")
BIASED = CLEAN.replace("경력 3년", "경력 3년, 30대 우대")


def test_section_is_extracted_without_the_check_table():
    section = checker_crosscheck.extract_section(CLEAN, "채용 공고")
    assert section is not None and "백엔드 개발" in section and "여성만" not in section  # 원래 문구 표는 빼고
    assert checker_crosscheck.extract_section("## 요약\n없음", "채용 공고") is None


def _run(posting: str, verdict_text: str):
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "admin_cross", "adminpass", Role.ADMIN)
    tid = pr.create_pipeline_template(conn, admin, "cross", "", "general")
    write = pr.add_stage(conn, admin, tid, "공고 작성", "single", [("채용 담당자", "")], is_primary_deliverable=True)
    pr.add_stage(conn, admin, tid, "최종 검토", "single", [("문서 검토자", "")],
                 gate_ids=["A"], min_gate_pass_ratio=1.0, max_retry=2, on_fail_return_stage_id=write)
    conn.execute("UPDATE pipeline_stages SET required_tools = ? WHERE id = ?", (json.dumps(["채용 공고 점검기"]), write))
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '공고', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    conn.commit()
    roles = {r[0]: r[1] for r in conn.execute("SELECT id, role_name FROM agent_pool")}

    def fake_run_crew(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        role = roles[agent_ids[0]]
        output = verdict_text if role == "문서 검토자" else posting
        return {"final_output": output,
                "agents": [{"agent_id": agent_ids[0], "role_name": role, "name": None, "output": output}],
                "gate_scores": {"A": {"score": 0.9, "passed": True}}, "tcr": 90.0, "delegations": []}

    not_measured = {"not_measured": True, "passed": True, "status": "not_measured"}
    with patch("hair_platform.crew_runtime.run_crew", side_effect=fake_run_crew), \
         patch("hair_platform.tool_audit.check_required", return_value=not_measured):
        assert task is not None
        run_id = pr.start_pipeline_run(conn, admin, tid, task)
        pr.run_pipeline_stage_job(conn, run_id, admin)
    row = conn.execute(
        "SELECT prs.status, prs.gate_scores FROM pipeline_run_stages prs JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? AND ps.name = '최종 검토' ORDER BY prs.id DESC LIMIT 1", (run_id,)).fetchone()
    run_status = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0]
    return row["status"], run_status, json.loads(row["gate_scores"]).get("checker_crosscheck")


def test_approved_review_but_checker_finds_issue_goes_to_a_person():
    status, run_status, cross = _run(BIASED, "## 판정\n승인 — 요구사항 모두 충족")
    assert status == "passed" and run_status == "paused_for_approval"  # 통과는 그대로, 사람 결재 대기
    assert cross["disagree"] is True and cross["advisory"] is True
    assert any("30대 우대" in f["text"] for f in cross["findings"])


def test_agreeing_checker_lets_the_run_finish():
    status, run_status, cross = _run(CLEAN, "## 판정\n승인 — 요구사항 모두 충족")
    assert status == "passed" and run_status == "completed" and cross["disagree"] is False
    assert checker_crosscheck.crosscheck(None, 1, {"on_fail_return_stage_id": 1},  # type: ignore[arg-type]
                                         {"passed": False, "not_measured": False}) is None  # 보완 요청이면 대조 안 함
    assert pr.needs_human_for_gates({"checker_crosscheck": {"disagree": True}}) is True
    assert pr.needs_human_for_gates({"checker_crosscheck": {"disagree": False}}) is False
