"""Gate C·D·F·G가 내용과 무관한 상수로 나오던 문제(2026-11-24, "Gate C, D, F, G가 항상 같은 값인
원인을 분석해줘" → "1~4 진행해줘").

- C(0.4×TCR + 0.6×(1−SLA 위반율))·D(1 − p95/30초): 여러 LLM 호출로 몇 분씩 걸리는 크루·단계
  실행은 단일 호출 기준 30초 SLA를 늘 넘겨 0.4·0.0 → "측정 안 됨".
- F(크루 구조 점수): Agent 1명이면 늘 0.687 → 1명일 때 "측정 안 됨".
- G(추론 표지 키워드): 한국어 표지가 "왜냐하면"·"따라서"뿐 → "근거"·"므로"·"때문" 등 추가.
"""

from __future__ import annotations

import pytest

from hair_platform import agent_evaluation as ae

REPORT = (
    "## 결론\n마케팅 예산을 4분기에 재배정할 것을 권고한다.\n\n"
    "## 근거\n마케팅 실적이 520백만 원으로 예산 400백만 원을 30% 초과했으므로 재배정이 필요하다."
)


def _crew_output(agents, text=REPORT):
    pytest.importorskip("crewai")
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    return CrewOutput(raw=text, tasks_output=[TaskOutput(description="d", agent=a, raw=text) for a in agents])


def test_korean_reasoning_markers_count_for_gate_g():
    assert {"근거", "때문", "므로"} <= set(ae._EXPLAINABILITY.reasoning_markers)
    assert "따라서" in ae._EXPLAINABILITY.reasoning_markers  # SDK 기본 표지는 그대로 유지
    result = ae.run_and_score("재무 분석가", lambda: REPORT)
    assert result["gate_scores"]["G"]["score"] == 1.0


def test_single_call_keeps_latency_gates():
    # 단일 LLM 호출은 30초 SLA가 맞는 대상이라 SDK 판정을 그대로 둔다(우리 "측정 안 됨" 표시 없음).
    # 스텁은 즉시 반환해 지연 0이면 SDK가 D를 스스로 못 잴 수 있으니 점수 대신 표시 여부를 본다.
    gates = ae.run_and_score("재무 분석가", lambda: REPORT)["gate_scores"]
    assert gates["C"]["score"] is not None
    assert "reason" not in gates["C"] and "reason" not in gates["D"]


def test_long_running_single_scoring_marks_latency_gates_not_measured():
    gates = ae.run_and_score("리드", lambda: REPORT, long_running=True)["gate_scores"]
    for gid in ("C", "D"):
        assert gates[gid]["score"] is None and gates[gid]["not_measured"] is True
        assert gates[gid]["status"] == "not_measured" and "SLA" in gates[gid]["reason"]


def test_crew_scoring_marks_c_d_and_single_agent_f_not_measured():
    gates = ae.run_and_score_crew("재무", lambda: _crew_output(["재무 분석가"]))["gate_scores"]
    assert all(gates[g]["not_measured"] for g in ("C", "D", "F"))
    assert "1명" in gates["F"]["reason"]
    assert gates["A"]["score"] is not None and gates["G"]["score"] == 1.0


def test_crew_scoring_measures_f_with_two_or_more_agents():
    gates = ae.run_and_score_crew("재무", lambda: _crew_output(["재무 분석가", "재무 검토자"]))["gate_scores"]
    assert gates["F"]["score"] is not None and not gates["F"].get("not_measured")
    assert gates["C"]["not_measured"] and gates["D"]["not_measured"]


def test_not_measured_gates_drop_out_of_pass_ratio():
    from hair_platform import pipeline_runtime

    gates = ae.run_and_score_crew("재무", lambda: _crew_output(["재무 분석가"]))["gate_scores"]
    measured = [g for g in gates.values() if not g.get("not_measured")]
    expected = sum(1 for g in measured if g.get("passed")) / len(measured)
    assert pipeline_runtime._gate_pass_ratio(gates) == expected


# ---------- 수치 근거 — 도구 결과도 근거로 인정(2026-11-24 실측 오탐: 계산기의 IRR 12.92%) ----------


def test_run_crew_collects_tool_outputs_as_evidence():
    pytest.importorskip("crewai")
    import sqlite3
    from datetime import UTC, datetime
    from unittest.mock import patch

    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

    from hair_platform import crew_runtime, db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    conn.execute("INSERT INTO users (id, username, password_hash, role) VALUES (1, 'a', 'x', 'admin')")
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('재무 분석가', '[]', 'active')"
    ).lastrowid
    conn.commit()

    def _kickoff(self, *args, **kwargs):
        for name, output in (("재무 지표 계산기", '{"IRR(%)": 12.92}'), ("Delegate work to coworker", "위임 결과")):
            crewai_event_bus.emit(object(), ToolUsageFinishedEvent(
                tool_name=name, tool_args={}, started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
                output=output, agent_role="재무 분석가",
            ))
        return _crew_output(["재무 분석가"], "IRR 12.92%")

    with patch("crewai.Crew.kickoff", new=_kickoff):
        result = crew_runtime.run_crew(conn, [agent_id], ["분석"], 1, provider="openai")
    assert "[도구 재무 지표 계산기 결과]" in result["tool_evidence"]
    assert "12.92" in result["tool_evidence"]
    assert "위임 결과" not in result["tool_evidence"]  # 위임은 작업 결과로 따로 다룬다


def test_numeric_check_accepts_tool_result_numbers():
    from hair_platform import numeric_check

    text = "- **4분기 투자안**: NPV +104.18백만 원, IRR **12.92%**, 회수기간 2.94년으로 타당하다."
    brief = "초기 투자 900백만 원, 4년간 순현금흐름 250, 320, 350, 300백만 원, 할인율 8%"
    tool = '[도구 재무 지표 계산기 결과]\n{"투자_타당성": {"NPV": 104.18, "IRR(%)": 12.92}}'
    assert numeric_check.gate_score(text, brief)["status"] != "pass"
    assert numeric_check.gate_score(text, brief + "\n\n" + tool)["status"] == "pass"
