"""스킬·도구·에이전트 실행 품질 점검 P1 개선(2026-11-22, "스킬, 툴, 에이전트 각각의 실행
품질을 점검해줘" → "P1부터 개선하고 같은 케이스로 다시 실행해줘") — 실제 Agent 9개를 끝까지
돌려 찾은 문제들."""

from __future__ import annotations

import json
from types import SimpleNamespace

from hair_platform import (
    agent_runtime,
    db,
    pool_registry,
    reference_pipeline_seeds as seeds,
    skill_prompting,
    tool_calling,
    vectorstore,
)


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _tool(conn, name):
    row = conn.execute("SELECT * FROM tool_pool WHERE name = ?", (name,)).fetchone()
    t = pool_registry._custom_python_crewai_tool(row)
    return lambda q: tool_calling.run_tool({t.name: t}, t.name, {"query": q})


# ---------- 1. JSON 입력 도구의 형식 안내·관용 입력 ----------


def test_json_tools_describe_their_input_format():
    conn = _conn()
    for name in ("골든셋 커버리지 체커", "분석 프레임워크 템플릿 생성기", "수치 데이터 분석기", "차트 생성기"):
        desc = conn.execute("SELECT description FROM tool_pool WHERE name = ?", (name,)).fetchone()[0]
        assert "입력(JSON 문자열)" in desc and "{" in desc, name
    lint = conn.execute("SELECT description FROM tool_pool WHERE name = 'Spec EARS 린터'").fetchone()[0]
    assert "전체" in lint


def test_tool_guidance_keeps_input_format():
    conn = _conn()
    row = conn.execute("SELECT * FROM tool_pool WHERE name = '수치 데이터 분석기'").fetchone()
    text = skill_prompting.tool_guidance([pool_registry._custom_python_crewai_tool(row)], False)
    assert '{"values": [42, 48, 55]}' in text
    assert "```chart" in text and "본문" in text


def test_data_analyzer_accepts_plain_number_lists_but_not_ambiguous_text():
    run = _tool(_conn(), "수치 데이터 분석기")
    for q in ("매출 42, 48, 55", "[42, 48, 55]", "42 48 55"):
        assert json.loads(run(q))["sum"] == 145.0, q
    # 기간 이름에 숫자가 섞이거나 천 단위 쉼표면 숫자를 잘못 읽을 수 있어 거절한다.
    for q in ("1분기 42억, 2분기 48억", "1,234, 5", '"x"'):
        out = run(q)
        assert out.startswith("입력은 JSON 문자열이어야 합니다"), (q, out)
    out = json.loads(run(json.dumps({"series": {"1분기": 42, "2분기": 48}})))
    assert out["growth_rates"][0]["growth_rate_pct"] == 14.29


def test_migration_updates_only_untouched_seed_rows():
    conn = _conn()
    old = seeds.TOOL_DESCRIPTIONS_V1
    conn.execute("UPDATE tool_pool SET description = ? WHERE name = '골든셋 커버리지 체커'", (old["골든셋 커버리지 체커"],))
    conn.execute("UPDATE tool_pool SET description = 'admin이 고친 설명' WHERE name = '차트 생성기'")
    row = conn.execute("SELECT id, config FROM tool_pool WHERE name = '수치 데이터 분석기'").fetchone()
    cfg = json.loads(row["config"])
    cfg["function_source"] = seeds.DATA_ANALYZER_SOURCE_V1
    conn.execute("UPDATE tool_pool SET config = ? WHERE id = ?", (json.dumps(cfg), row["id"]))
    db._add_tool_input_formats(conn)
    desc = dict(conn.execute("SELECT name, description FROM tool_pool").fetchall())
    assert "입력(JSON 문자열)" in desc["골든셋 커버리지 체커"]
    assert desc["차트 생성기"] == "admin이 고친 설명"
    src = json.loads(conn.execute("SELECT config FROM tool_pool WHERE id = ?", (row["id"],)).fetchone()[0])
    assert src["function_source"] == seeds.DATA_ANALYZER_SOURCE


# ---------- 2. 도구가 만든 차트를 답에서 빼먹는 문제 ----------

CHART = json.dumps({"type": "bar", "data": {"labels": ["a"], "datasets": [{"label": "x", "data": [1]}]}})


def test_ensure_charts_included_appends_missing_chart_once():
    calls = [("custom_tool_1", CHART), ("custom_tool_1", CHART), ("custom_tool_2", "type은 ... 중 하나")]
    out = tool_calling.ensure_charts_included("## 보고서\n본문", calls)
    assert out.count("```chart") == 1 and CHART in out
    already = "본문\n```chart\n{}\n```"
    assert tool_calling.ensure_charts_included(already, calls) == already
    assert tool_calling.ensure_charts_included("본문", [("t", '{"total": 2}')]) == "본문"


def test_record_calls_only_inside_context():
    t = SimpleNamespace(run=lambda **kw: "ok")
    tool_calling.run_tool({"t": t}, "t", {})  # 기록 컨텍스트 밖 — 아무 데도 안 쌓인다
    with tool_calling.record_calls() as calls:
        tool_calling.run_tool({"t": t}, "t", {})
        tool_calling.run_tool({}, "missing", {})
    assert [n for n, _ in calls] == ["t", "missing"]


def test_execute_agent_adds_chart_the_model_left_out(monkeypatch):
    conn = _conn()
    from hair_platform.auth import Role, create_user

    agent_id = conn.execute("SELECT id FROM agent_pool WHERE role_name = '실적 보고서 작성자'").fetchone()[0]
    actor = create_user(conn, "eq_admin", "pw", Role.ADMIN)

    def fake_with_tools(provider, model, system, prompt, tools, **kw):
        tool_calling.run_tool({"c": SimpleNamespace(run=lambda **a: CHART)}, "c", {"query": "{}"})
        return "# 3분기 실적 보고\n\n매출 55억 원."

    monkeypatch.setattr(agent_runtime.llm, "call_llm_with_tools", fake_with_tools)
    monkeypatch.setattr(agent_runtime.llm, "resolve_provider_and_model", lambda p, m: ("ollama", "m"))
    out = agent_runtime.execute_agent(conn, agent_id, actor, "3분기 실적 보고서")
    assert "```chart" in out and CHART in out


# ---------- 4. 요청과 무관한 지식 청크 강제 주입 ----------

KB = "# Human-AI Agent 협업: 기술·시장·솔루션·방법론 조사 — 사람과 에이전트의 역할 분담, HITL 승인 관문"


def test_lexical_gate_drops_unrelated_chunks():
    # 실측 mxbai 점수대(관련·무관 모두 0.7~0.88)의 청크는 단어 겹침으로 가른다.
    hits = [{"id": 1, "text": KB, "score": 0.85}]
    assert vectorstore.lexically_relevant_hits("국내 전기차 충전 서비스 사업을 SWOT으로 분석해줘", hits) == []
    assert vectorstore.lexically_relevant_hits("2026년 글로벌 반도체 시장 규모 전망", hits) == []
    assert vectorstore.lexically_relevant_hits("Human-AI 에이전트 협업 방식", hits) == hits
    assert vectorstore.lexically_relevant_hits("HITL 승인 관문이 필요한 이유", hits) == hits
    # 코사인이 아주 높으면 단어가 안 겹쳐도(업무 제목이 검색어인 Task 실행) 남긴다.
    confident = [{"id": 2, "text": "원격근무는 수습 기간 중 사용할 수 없다", "score": 0.95}]
    assert vectorstore.lexically_relevant_hits("'정책 문의 응답' 업무를 수행하세요", confident) == confident


def test_retrieve_context_reports_no_hit_when_all_filtered(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(vectorstore, "search", lambda c, q: [{"id": 1, "text": KB, "score": 0.85}])
    ctx = agent_runtime.retrieve_context(conn, ["내부지식검색"], "반도체 수출 규모 전망을 알려줘")
    assert ctx == "VectorDB에서 관련된 과거 산출물을 찾지 못했습니다."


# ---------- 2-b. 도구 호출 턴에 쓴 본문이 최종 답에서 사라지는 문제 ----------

SPEC_TURN = (
    "로그인 기능 Spec을 작성하겠습니다. 먼저 초안을 쓴 뒤 도구로 검증하겠습니다.\n\n"
    "## Spec\n\nSPEC-001: WHEN 사용자가 로그인을 요청하면, THE SYSTEM SHALL 세션 토큰을 발급한다.\n"
    "SPEC-002: IF 인증이 5회 실패하면, THE SYSTEM SHALL 계정을 15분간 잠근다."
)


def test_merge_interim_keeps_body_drops_narration_and_duplicates():
    final = "## EARS 검증 결과\n통과"
    out = tool_calling.merge_interim_text([SPEC_TURN], final)
    assert out.startswith("## Spec") and out.endswith(final)
    assert "작성하겠습니다" not in out
    # 짧은 진행 안내만 있던 턴은 버린다.
    assert tool_calling.merge_interim_text(["도구를 호출하겠습니다."], final) == final
    # 최종 답이 본문을 다시 실었으면 붙이지 않는다.
    restated = SPEC_TURN.split("\n\n", 1)[1] + "\n\n" + final
    assert tool_calling.merge_interim_text([SPEC_TURN], restated) == restated
    assert tool_calling.merge_interim_text([], final) == final


def test_ollama_tool_loop_returns_text_written_alongside_tool_calls(monkeypatch):
    from hair_platform import llm

    replies = iter(
        [
            {"message": {"role": "assistant", "content": SPEC_TURN,
                         "tool_calls": [{"function": {"name": "t", "arguments": {"query": "x"}}}]}},
            {"message": {"role": "assistant", "content": "## EARS 검증 결과\n통과"}},
        ]
    )

    class _Resp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(next(replies)))
    tool = SimpleNamespace(
        name="t", run=lambda **kw: "ok", func=SimpleNamespace(__doc__="d"),
        args_schema=SimpleNamespace(model_json_schema=lambda: {"properties": {"query": {"type": "string"}}}),
    )
    out = llm.call_llm_with_tools("ollama", "m", "sys", "Spec 써줘", [tool])
    assert "SPEC-001" in out and "## EARS 검증 결과" in out


# ---------- P2 #9. Ollama가 도구 인자를 JSON 문자열로 줄 때 ----------


def _ollama_replies(monkeypatch, replies):
    import httpx

    seq = iter(replies)

    class _Resp:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(next(seq)))


def _echo_tool(seen):
    return SimpleNamespace(
        name="t", run=lambda **kw: seen.append(kw) or "ok", func=SimpleNamespace(__doc__="d"),
        args_schema=SimpleNamespace(model_json_schema=lambda: {"properties": {"query": {"type": "string"}}}),
    )


def test_ollama_string_arguments_are_parsed(monkeypatch):
    from hair_platform import llm

    seen: list = []
    call = {"function": {"name": "t", "arguments": '{"query": "42, 48"}'}}
    _ollama_replies(monkeypatch, [
        {"message": {"role": "assistant", "content": "", "tool_calls": [call]}},
        {"message": {"role": "assistant", "content": "끝"}},
    ])
    assert llm.call_llm_with_tools("ollama", "m", "s", "p", [_echo_tool(seen)]) == "끝"
    assert seen == [{"query": "42, 48"}]


def test_ollama_unparseable_arguments_tell_the_model(monkeypatch):
    from hair_platform import llm

    seen: list = []
    sent: list = []
    import httpx

    replies = iter([
        {"message": {"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "t", "arguments": "{query: 42"}}]}},
        {"message": {"role": "assistant", "content": "끝"}},
    ])

    class _Resp:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    def fake_post(*a, **k):
        sent.append(k["json"]["messages"])
        return _Resp(next(replies))

    monkeypatch.setattr(httpx, "post", fake_post)
    llm.call_llm_with_tools("ollama", "m", "s", "p", [_echo_tool(seen)])
    assert seen == []
    assert "올바른 JSON이 아닙니다" in sent[-1][-1]["content"]


# ---------- P2 #5. Tavily 자동 요약을 쓰지 않는다 ----------
# (tests/unit/test_phase22_websearch_tool.py의 두 Tavily 테스트가 요청 페이로드·결과 텍스트를 검사)


# ---------- P2 #6. 자체 검토에 근거 자료와 수치 점검 ----------


def test_self_review_prompt_carries_evidence_and_number_checks():
    p = skill_prompting.self_review_prompt("요청", "초안", '[도구 custom_tool_1 결과]\n{"sum": 206.0}')
    assert '{"sum": 206.0}' in p and p.index("[근거 자료") < p.index("[초안]")
    assert "%p" in p and "비교 방향" in p
    assert "[근거 자료" not in skill_prompting.self_review_prompt("요청", "초안")
    long = "x" * (skill_prompting.SELF_REVIEW_EVIDENCE_MAX_CHARS + 500)
    assert len(skill_prompting.self_review_prompt("r", "d", long)) < len(long)


def test_execute_agent_gives_reviewer_the_tool_results(monkeypatch):
    from hair_platform.auth import Role, create_user

    conn = _conn()
    actor = create_user(conn, "rv_admin", "pw", Role.ADMIN)
    agent_id = conn.execute("SELECT id FROM agent_pool WHERE role_name = '데이터 분석가'").fetchone()[0]
    conn.execute("UPDATE agent_pool SET tuning_config = ? WHERE id = ?", (json.dumps({"self_review": True}), agent_id))
    prompts: list = []

    def fake_with_tools(*a, **kw):
        tool_calling.run_tool({"c": SimpleNamespace(run=lambda **x: '{"sum": 206.0}')}, "c", {"query": "{}"})
        return "## 분석\n합계 206억 원. " + "본문 " * 20

    def fake_call(provider, model, system, prompt, **kw):
        prompts.append(prompt)
        return "## 분석\n합계 206억 원(검토). " + "본문 " * 20

    monkeypatch.setattr(agent_runtime.llm, "call_llm_with_tools", fake_with_tools)
    monkeypatch.setattr(agent_runtime.llm, "call_llm", fake_call)
    monkeypatch.setattr(agent_runtime.llm, "resolve_provider_and_model", lambda p, m: ("ollama", "m"))
    out = agent_runtime.execute_agent(conn, agent_id, actor, "매출 합계")
    assert "(검토)" in out
    assert any('{"sum": 206.0}' in p for p in prompts)
