"""단계 필수 도구(2026-11-26, 개선 계획 1-2 — `tool_audit.check_required`, `pipeline_runtime.score_stage_output`)."""

from __future__ import annotations

import json

from hair_platform import llm_usage, pipeline_runtime, tool_audit, tool_calling


class FakeTool:
    def __init__(self, result="ok"):
        self.result = result

    def run(self, **kwargs):
        return self.result


def _hr_stage(conn):
    return conn.execute(
        "SELECT s.* FROM pipeline_stages s JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "WHERE t.name = '채용 공고·면접 설계 파이프라인' AND s.name = '공고·면접 설계'").fetchone()


def _run(conn, stage):
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '요청')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '채용 담당자')", (brief,)).lastrowid
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) VALUES (?, ?, ?, 'running')",
                       (stage["pipeline_template_id"], task, admin)).lastrowid
    conn.commit()
    return run


def _score(conn, run, stage, attempt):
    result = {"gate_scores": {}, "agents": [], "final_output": "", "tcr": 1.0}
    return pipeline_runtime.score_stage_output(conn, run, stage, result, "## 채용 공고\n점검 결과: 0건", "", attempt=attempt)


def test_domain_stages_have_required_tools_seeded(client_and_conn):
    _client, conn = client_and_conn
    rows = {(r[0], r[1]): json.loads(r[2]) for r in conn.execute(
        "SELECT t.name, s.name, s.required_tools FROM pipeline_stages s JOIN pipeline_templates t "
        "ON t.id = s.pipeline_template_id WHERE s.required_tools IS NOT NULL")}
    assert rows == {
        ("재무 분석 보고 파이프라인", "재무 분석"): ["재무 지표 계산기"],
        ("계약서 1차 검토 파이프라인", "조항 검토"): ["계약 조항 점검기"],
        ("채용 공고·면접 설계 파이프라인", "공고·면접 설계"): ["채용 공고 점검기"],
    }
    assert tool_audit.stage_tool_names(conn, _hr_stage(conn)["id"]) == {"채용 공고 점검기"}


def test_claiming_a_check_without_calling_the_tool_fails_and_retry_feedback_says_so(client_and_conn):
    _client, conn = client_and_conn
    llm_usage.configure(conn)
    stage = _hr_stage(conn)
    run = _run(conn, stage)
    gates, passed = _score(conn, run, stage, 1)  # 결과물에 "점검 결과: 0건"만 적고 도구는 부르지 않음
    assert passed is False
    g = gates["required_tools"]
    assert g["passed"] is False and g["missing"] == ["채용 공고 점검기"] and g["called"] == []
    feedback = pipeline_runtime._gate_failure_summary(gates)
    assert "필수 도구를 실제로 부르지 않았습니다(채용 공고 점검기)" in feedback

    with llm_usage.scope(pipeline_run_id=run), tool_audit.stage(stage["id"], 2):
        from hair_platform import progress

        progress.TOOL_LABELS["custom_tool_x"] = "채용 공고 점검기"
        try:
            tool_calling.run_tool({"custom_tool_x": FakeTool('{"판정": "차별 소지 문구 없음"}')}, "custom_tool_x", {"query": "공고"})
        finally:
            progress.TOOL_LABELS.pop("custom_tool_x", None)
    gates2, _passed2 = _score(conn, run, stage, 2)
    assert gates2["required_tools"]["passed"] is True and gates2["required_tools"]["called"] == ["채용 공고 점검기"]


def test_failed_tool_call_does_not_count_and_unconnected_tool_is_not_blocking(client_and_conn):
    _client, conn = client_and_conn
    llm_usage.configure(conn)
    stage = _hr_stage(conn)
    run = _run(conn, stage)
    with llm_usage.scope(pipeline_run_id=run), tool_audit.stage(stage["id"], 1):
        tool_calling.run_tool({}, "채용 공고 점검기", {})  # 알 수 없는 도구 → 실패 기록
    assert _score(conn, run, stage, 1)[0]["required_tools"]["passed"] is False
    # 단계 Agent에 연결되지 않은 도구를 필수로 지정하면 — 다시 해도 부를 수 없으니 막지 않고 알린다
    conn.execute("UPDATE pipeline_stages SET required_tools = ? WHERE id = ?", (json.dumps(["사내 DB 조회"]), stage["id"]))
    conn.commit()
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (stage["id"],)).fetchone()
    g = _score(conn, run, stage, 1)[0]["required_tools"]
    assert g["not_measured"] is True and "연결되지 않은" in g["reason"]


def test_required_tools_api_validates_names_and_round_trips(client_and_conn):
    client, conn = client_and_conn
    stage = _hr_stage(conn)
    url = f"/api/pipeline-templates/{stage['pipeline_template_id']}/stages/{stage['id']}"
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    assert client.patch(url, json={"required_tools": ["없는 도구"]}).status_code in (400, 422)
    assert client.patch(url, json={"required_tools": ["채용 공고 점검기", "인사 지표 계산기"]}).status_code == 200
    detail = client.get(f"/api/pipeline-templates/{stage['pipeline_template_id']}").json()
    got = next(s for s in detail["stages"] if s["id"] == stage["id"])
    assert got["required_tools"] == ["채용 공고 점검기", "인사 지표 계산기"]
    assert client.patch(url, json={"required_tools": []}).status_code == 200
    assert conn.execute("SELECT required_tools FROM pipeline_stages WHERE id = ?", (stage["id"],)).fetchone()[0] is None
