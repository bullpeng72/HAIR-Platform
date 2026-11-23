"""파이프라인 품질 점검 후속(2026-11-21, "파이프라인 품질 점검해줘") — 실적 보고 파이프라인을
실제로 끝까지 돌려 찾은 문제들."""

from __future__ import annotations

import json
from types import SimpleNamespace

from hair_platform import db, llm, model_options, model_output, pipeline_runtime


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


# ---------- 1. 내부 추론·실행되지 않은 도구 호출이 산출물로 저장되던 문제 ----------

LEAKED_ONLY = (
    "The tool failed because my query was too complex.\n</think>\n\n"
    "Thought: Let me simplify.\n\n<tool_call>\n<function=custom_tool_2337>\n"
    "<parameter=query>매출 42, 48, 55</parameter>\n</function>\n</tool_call>"
)
LEAKED_WITH_BODY = (
    "Let me analyze this task carefully.\n- data...\n</think>\n\n"
    "Thought: I now can give a great answer\nFinal Answer: ## 실적 보고서\n\n3분기 매출 55억 원(목표 52억 원 대비 105.8%)."
)



def _id(cur) -> int:
    """INSERT 커서의 새 행 id — 없으면 바로 실패시킨다(None이 다음 호출로 흘러가지 않게)."""
    assert cur.lastrowid is not None
    return cur.lastrowid

def test_clean_strips_reasoning_and_tool_calls():
    assert model_output.clean(LEAKED_ONLY) == ""
    assert model_output.clean(LEAKED_WITH_BODY) == "## 실적 보고서\n\n3분기 매출 55억 원(목표 52억 원 대비 105.8%)."
    plain = "# 보고서\n\nLet me 강조: 본문 중간의 영어 문장은 그대로 둔다.\n\n본문"
    assert model_output.clean(plain) == plain


def test_guardrail_returns_cleaned_output_or_asks_retry():
    from hair_platform import crew_runtime

    ok, value = crew_runtime._min_length_guardrail(SimpleNamespace(raw=LEAKED_WITH_BODY))
    assert ok and value.startswith("## 실적 보고서")
    ok, message = crew_runtime._min_length_guardrail(SimpleNamespace(raw=LEAKED_ONLY))
    assert not ok and "결과물을 직접 완성" in message


# ---------- 2. 2단계 이후가 원 요청(Brief)을 못 보던 문제 ----------


def test_later_stages_receive_original_brief():
    from hair_platform.auth import Role, create_user

    conn = _conn()
    uid = create_user(conn, "pq_admin", "pw", Role.ADMIN)
    brief_id = _id(conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '3분기 목표 52억 원', '[]')", (uid,)
    ))
    task_id = _id(conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '실적 보고', '[]')", (brief_id,)
    ))
    out = pipeline_runtime.with_brief_reference(conn, task_id, "앞 단계 산출물")
    assert out.startswith("앞 단계 산출물") and "3분기 목표 52억 원" in out
    assert "원 업무 요청(Brief)" in out


# ---------- 3. 업무 문서 파이프라인의 Agent 배정 ----------


def _roles(conn, template_name, stage_seq):
    return [
        r[0]
        for r in conn.execute(
            "SELECT psa.role_name FROM pipeline_stage_agents psa JOIN pipeline_stages ps ON ps.id = psa.stage_id "
            "JOIN pipeline_templates t ON t.id = ps.pipeline_template_id WHERE t.name = ? AND ps.seq_order = ?",
            (template_name, stage_seq),
        )
    ]


def test_business_pipelines_use_document_agents_not_sw_agents():
    from hair_platform import reference_pipeline_seeds as seeds

    conn = _conn()
    for name in (seeds.BUSINESS_PIPELINE_NAME, seeds.PROPOSAL_PIPELINE_NAME, seeds.PERFORMANCE_REPORT_PIPELINE_NAME):
        assert _roles(conn, name, 1) == ["업무 요구사항 분석가"], name
    assert _roles(conn, seeds.BUSINESS_PIPELINE_NAME, 5) == ["문서 검토자"]
    assert _roles(conn, seeds.BUSINESS_PIPELINE_NAME, 6) == ["문서 교정자"]
    assert _roles(conn, seeds.PROPOSAL_PIPELINE_NAME, 4) == ["문서 검토자"]
    # SW 개발 파이프라인은 그대로.
    assert _roles(conn, seeds.DEV_PIPELINE_NAME, 1) == ["요구사항 분석가"]
    pm = json.loads(conn.execute("SELECT skill_tags FROM agent_pool WHERE role_name = 'PM'").fetchone()[0])
    assert "문서품질검토" in pm


# ---------- 4. 모델 설정이 없는 Agent가 API 키 없는 환경에서 즉시 실패하던 문제 ----------


def test_unset_provider_falls_back_to_platform_default_without_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HAIR_PLATFORM_LLM_PROVIDER", "HAIR_PLATFORM_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(model_options, "_PLATFORM_DEFAULT", {"provider": "ollama", "model": "local:latest"})
    assert llm.resolve_provider_and_model(None, None) == ("ollama", "local:latest")
    monkeypatch.setattr(model_options, "_PLATFORM_DEFAULT", {})
    monkeypatch.setattr(model_options, "_ACTIVE_MODELS", {"ollama": ["registry:latest"]})
    assert llm.resolve_provider_and_model(None, None) == ("ollama", "registry:latest")


# ---------- 6. 검증 단계가 내용과 무관한 Gate(C·D·F·G) 때문에 늘 사람 검토로 넘어가던 문제 ----------
# ("모든 파이프라인 점검해줘") — 단계 gate_ids에 든 점수만 통과 판정에 센다.

_ALWAYS_SAME = {
    "A": {"passed": True, "not_measured": False, "score": 0.8},
    "C": {"passed": False, "not_measured": False, "score": 0.4},
    "D": {"passed": False, "not_measured": False, "score": 0.0},
    "F": {"passed": False, "not_measured": False, "score": 0.687},
    "G": {"passed": False, "not_measured": False, "score": 0.0},
}


def test_gate_ratio_counts_only_stage_gate_ids():
    assert pipeline_runtime._gate_pass_ratio(_ALWAYS_SAME) == 0.2  # 예전 판정: 0.5 미달
    assert pipeline_runtime._gate_pass_ratio(_ALWAYS_SAME, ["A"]) == 1.0
    with_trace = dict(_ALWAYS_SAME, requirements_traceability={"passed": False, "not_measured": False})
    assert pipeline_runtime._gate_pass_ratio(with_trace, ["A", "requirements_traceability"]) == 0.5
    failing_a = dict(_ALWAYS_SAME, A={"passed": False, "not_measured": False})
    assert pipeline_runtime._gate_pass_ratio(failing_a, ["A"]) == 0.0


def test_traceability_counts_only_when_listed_in_stage_gates(monkeypatch):
    from hair_platform import requirements_traceability_llm

    monkeypatch.setattr(
        requirements_traceability_llm,
        "assess_traceability",
        lambda conn, req, out: {"passed": True, "not_measured": False, "score": 1.0},
    )
    conn = _conn()
    stage = {"checks_requirements_traceability": 1, "gate_ids": json.dumps(["A"]), "seq_order": 3}
    # 1단계 산출물이 없는 실행 → 잴 기준이 없다.
    assert pipeline_runtime.requirements_traceability_score(conn, -1, stage, "x") is None
    assert pipeline_runtime.requirements_traceability_score(
        conn, -1, {"checks_requirements_traceability": 0, "gate_ids": "[]", "seq_order": 3}, "x"
    ) is None


def test_reference_review_stages_gate_on_content_signals_only():
    conn = _conn()
    rows = conn.execute(
        "SELECT name, gate_ids, checks_requirements_traceability FROM pipeline_stages "
        "WHERE min_gate_pass_ratio > 0"
    ).fetchall()
    assert rows
    for row in rows:
        if row[0] == "구현":  # 2026-11-22 C안 — 구현은 실행 가능성 Gate(아래 15번 테스트)
            continue
        expected = ["A", "requirements_traceability"] if row[2] else ["A"]
        assert json.loads(row[1]) == expected, row[0]


def test_pipeline_quality_settings_migration_keeps_admin_changes():
    conn = _conn()
    # 기존 DB 상태로 되돌린 뒤(시드 기본값) 마이그레이션을 다시 돌린다 — 관리자가 고친 단계는 그대로.
    conn.execute("UPDATE pipeline_stages SET gate_ids = ? WHERE min_gate_pass_ratio > 0",
                 (json.dumps(db._FULL_GATE_IDS),))
    conn.execute("UPDATE pipeline_stages SET gate_ids = ? WHERE name = '표현 검토'", (json.dumps(["A", "B"]),))
    db._review_pipeline_quality_settings(conn)
    gates = {r[0]: json.loads(r[1]) for r in conn.execute(
        "SELECT name, gate_ids FROM pipeline_stages WHERE min_gate_pass_ratio > 0")}
    assert gates["표현 검토"] == ["A", "B"]
    assert gates["코드 검증"] == ["A", "requirements_traceability"]

    def roles(stage):
        return [r[0] for r in conn.execute(
            "SELECT psa.role_name FROM pipeline_stage_agents psa JOIN pipeline_stages ps ON ps.id = psa.stage_id "
            "WHERE ps.name = ? ORDER BY psa.seq_order", (stage,))]

    assert roles("설계")[-1] == "UI 기획자" and "UI 기획자" not in roles("구현")
    assert conn.execute("SELECT output_content_type FROM pipeline_stages ps JOIN pipeline_templates pt "
                        "ON pt.id = ps.pipeline_template_id WHERE pt.name = '제안서 작성 파이프라인' "
                        "AND ps.name = '작성'").fetchone()[0] == "html"
    skills = json.loads(conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '프레젠테이션 자료 작성자'").fetchone()[0])
    assert "프레젠테이션작성" in skills


# ---------- 7. Ollama 응답이 반복 루프에 빠지면 호출 시간 제한(90분)까지 끝나지 않던 문제 ----------


def test_ollama_calls_have_default_output_cap(monkeypatch):
    monkeypatch.delenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr(model_options, "_CACHE", {})
    cap = model_options.DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS
    assert model_options.request_options("ollama", "m")["num_predict"] == cap
    assert model_options.for_model("ollama", "m")["max_tokens"] == cap  # CrewAI(litellm)
    # 다른 provider는 등록값이 없으면 보내지 않는다(provider 기본값).
    assert "max_completion_tokens" not in model_options.request_options("openai", "g")
    assert model_options.for_model("anthropic", "c") == {}


def test_registry_output_cap_overrides_default(monkeypatch):
    monkeypatch.setattr(model_options, "_CACHE", {})
    model_options.set_cached("ollama", "m", model_options.validate({"max_output_tokens": 4096}))
    model_options.set_cached("openai", "g", model_options.validate({"max_output_tokens": 2048}))
    assert model_options.request_options("ollama", "m")["num_predict"] == 4096
    assert model_options.request_options("openai", "g")["max_completion_tokens"] == 2048
    assert model_options.for_model("openai", "g") == {"max_tokens": 2048}
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")
    assert "num_predict" not in model_options.request_options("ollama", "other")  # 0 = 한도 없음
    import pytest

    with pytest.raises(ValueError):
        model_options.validate({"max_output_tokens": 10})


# ---------- 8. CrewAI 경로의 도구 호출이 litellm MCP 핸들러 import 오류로 전부 실패하던 문제 ----------


def test_crewai_llm_skips_litellm_mcp_handler():
    from hair_platform import crew_runtime

    llm_obj = crew_runtime._crewai_llm("ollama", "x")
    params = getattr(llm_obj, "additional_params", None) or {}
    assert params.get("_skip_mcp_handler") is True


# ---------- 9. 정해진 시작 문구 없이 붙은 영어 작업 메모(사업/기획 파이프라인 실측) ----------

_DECK_WITH_NOTES = (
    "The context provided includes:\n- A requirements-section mapping document\n\n"
    "The expected criteria: Create complete HTML presentation using HAIR deck design system "
    '(<main class="hair-deck">). Design (CSS) and chart drawing is done by platform.\n\n'
    "Let me plan the deck structure:\n1. 표지 (cover)\n2. 경영진 요약\n\n"
    '<main class="hair-deck">\n<section class="slide cover"><h1>사내 AI 교육</h1></section>\n</main>'
)
_KOREAN_WITH_NOTES = (
    "The user wants me to act as a document proofreader. The task is to review the deck.\n\n"
    "## 수정본\n\n300명 대상 교육에 1.2억 원을 투입한다. 수료율 85% 이상을 목표로 한다."
)


def test_clean_drops_english_notes_before_deck_markup():
    cleaned = model_output.clean(_DECK_WITH_NOTES)
    assert cleaned.startswith('<main class="hair-deck">')
    assert "사내 AI 교육" in cleaned


def test_clean_drops_english_preface_of_korean_deliverable_but_keeps_english_documents():
    assert model_output.clean(_KOREAN_WITH_NOTES).startswith("## 수정본")
    english = (
        "We propose a phased rollout for all staff.\n\n"
        "Phase 1 covers onboarding for 300 staff across three tracks.\n\n## Budget\n\n1.2 billion KRW."
    )
    assert model_output.clean(english) == english
    code = "```python\ndef f():\n    return 1\n```\n\n함수 f는 1을 돌려준다."
    assert model_output.clean(code) == code


# ---------- 10. 결과물 대신 작업 과정(영어 메모 + 초안 반복)을 출력한 경우 ----------

_WORK_IN_PROGRESS = (
    "```html\n<section class=\"slide\"><h2>경영진 요약</h2><p>300명 교육에 1.2억 원을 투입한다.</p></section>\n```\n\n"
    "Hmm, the metrics should come first. Let me reconsider the layout of this slide now.\n\n"
    "```html\n<section class=\"slide\"><h2>경영진 요약</h2><p>수료율 85% 이상을 목표로 한다.</p></section>\n```\n\n"
    "Wait, I need to be careful not to fabricate any numbers in the next slide either.\n\n"
    "Actually, let me combine both slides into one and use a decision component instead.\n\n"
    "```html\n<section class=\"slide\"><h2>결정 요청</h2><p>예산 승인을 요청한다.</p></section>\n```"
)


def test_work_in_progress_output_is_detected_and_retried():
    from hair_platform import crew_runtime

    assert model_output.work_in_progress(_WORK_IN_PROGRESS)
    ok, message = crew_runtime._min_length_guardrail(SimpleNamespace(raw=_WORK_IN_PROGRESS))
    assert ok is False and "작업 과정" in message
    # 한국어 결과물 속 영어 한두 문단(인용·용어 설명)이나 영어 결과물은 걸리지 않는다.
    korean = "## 보고\n\n매출은 55억 원이다.\n\nThe benchmark follows the McKinsey global survey of 2025 results.\n\n영업이익은 6.3억 원이다."
    assert not model_output.work_in_progress(korean)
    english = "\n\n".join(["This proposal describes the rollout plan for all three tracks in detail."] * 5)
    assert not model_output.work_in_progress(english)


# ---------- 11. 긴 표가 슬라이드를 넘쳐 제목·마지막 행이 잘리던 문제(제안서 덱 실측) ----------


def test_deck_css_keeps_long_tables_inside_slide():
    from hair_platform import presentation_deck

    css = presentation_deck.DECK_CSS
    assert "justify-content:safe center" in css  # 넘치면 위에서부터(제목이 잘리지 않게)
    assert ".slide:has(tr:nth-child(8))" in css and ".slide:has(tr:nth-child(12))" in css


# ---------- 12. 같은 추론 문단을 반복하다 출력 한도에서 끊긴 경우(제안서 최종 승인 판정 실측) ----------


def test_repetition_loop_is_detected_and_retried():
    from hair_platform import crew_runtime

    block = (
        "Let me think about which skill applies here. The relevant skill is the review skill.\n"
        "- ## 판정 (승인 / 보완 요청 + 한 줄 이유)\n- ## 요구사항 충족 (table)\n"
        "Wait, but the revised version already has these sections and the discussion.\n\n"
    )
    looped = "The user is asking me to act as PM.\n\n" + block * 12
    assert model_output.repetition_loop(looped)
    ok, message = crew_runtime._min_length_guardrail(SimpleNamespace(raw=looped))
    assert ok is False and "반복" in message
    # 표가 긴 정상 문서(행마다 내용이 다름)는 걸리지 않는다.
    table = "## 일정\n\n| 월 | 내용 |\n|---|---|\n" + "".join(
        f"| {i}월 | {i}차 운영: 과정 {i} 진행·만족도 조사·결과 보고서 작성 |\n" for i in range(1, 30)
    )
    assert not model_output.repetition_loop(table)


def test_traceability_stages_require_both_signals():
    """A안(2026-11-21) — 추적성 단계는 Gate A와 요구사항 추적성이 모두 통과해야 한다."""
    conn = _conn()
    rows = conn.execute(
        "SELECT name, gate_ids, min_gate_pass_ratio, checks_requirements_traceability FROM pipeline_stages "
        "WHERE min_gate_pass_ratio > 0"
    ).fetchall()
    for name, gate_ids, ratio, checks in rows:
        if name == "구현":  # 실행 가능성 Gate 단독(1.0) — 15번 테스트
            continue
        assert ratio == (1.0 if checks else 0.5), name
    partial = {
        "A": {"passed": True, "not_measured": False},
        "requirements_traceability": {"passed": False, "not_measured": False, "covered": 11, "total_requirements": 13},
    }
    assert pipeline_runtime._gate_pass_ratio(partial, ["A", "requirements_traceability"]) < 1.0


# ---------- 13. 품질 재시도를 다 쓰면 파이프라인 전체가 실패하던 문제(SW 개발 실측) ----------


def _looped_output():
    block = (
        "Let me think about which module applies here. The relevant module is the storage layer.\n"
        "Wait, but the previous developer already wrote the storage layer in the file above.\n\n"
    )
    return "시작합니다.\n\n" + block * 12


def test_output_guardrail_accepts_on_final_attempt(caplog):
    from hair_platform import crew_runtime

    guard = crew_runtime._output_guardrail(max_retries=2)
    looped = SimpleNamespace(raw=_looped_output())
    assert guard(looped)[0] is False  # 1회
    assert guard(looped)[0] is False  # 2회(재시도 1)
    ok, value = guard(looped)  # 마지막 — 정리본을 받는다(파이프라인 실패 대신 Gate·사람 검토로)
    assert ok is True and "storage layer" in value
    assert guard(SimpleNamespace(raw="  "))[0] is False  # 빈 출력은 마지막에도 실패
    assert any("rejected (repetition_loop)" in r.getMessage() for r in caplog.records)


def test_crewai_task_accepts_output_guardrail():
    from crewai import Agent, Task

    from hair_platform import crew_runtime

    agent = Agent(role="r", goal="g", backstory="b", llm=crew_runtime._crewai_llm("ollama", "x"))
    task = Task(
        description="d", expected_output="e", agent=agent,
        guardrail=crew_runtime._output_guardrail(),
        guardrail_max_retries=crew_runtime._GUARDRAIL_MAX_RETRIES,
    )
    assert task.guardrail_max_retries == 3


# ---------- 14. 코드 산출물에 섞인 혼잣말 작업 과정(SW 개발 구현 실측) ----------


def test_self_talk_work_in_progress_is_detected_regardless_of_language():
    code_with_self_talk = "\n\n".join(
        ["```javascript\nfunction add(a, b) { return a + b; }\n```",
         "Wait, there's an issue with the toggle handler when the list re-renders.",
         "```javascript\nfunction toggle(t) { return { ...t, done: !t.done }; }\n```",
         "Hmm, let me reconsider the whole flow once more.",
         "Actually, the store should own the validation logic instead.",
         "Let me write the complete HTML file now.",
         "Now, let me reconsider the initial render order before finishing."]
    )
    assert model_output.self_talk_paragraphs(code_with_self_talk) == 5
    assert model_output.work_in_progress(code_with_self_talk)
    # 정식 영어 문서·코드 주석의 "Let me"는 문단 머리가 아니거나 개수가 적어 걸리지 않는다.
    doc = "## Design\n\nThe store owns validation.\n\nLet me know if anything is unclear.\n\n```js\n// Let me explain\n```"
    assert not model_output.work_in_progress(doc)


# ---------- 15. C안 — 실행 가능성 Gate + 구현 단계 단일 작성(SW 개발 실측) ----------

_RUNNABLE_TODO = """```html
<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>할 일</title>
<style>body{font-family:sans-serif} .done{text-decoration:line-through}</style></head>
<body><form id="f"><input id="t" maxlength="100"><button>추가</button></form><ul id="list"></ul>
<script>
const KEY = "todos";
let todos = JSON.parse(localStorage.getItem(KEY) || "[]");
function save() {
  localStorage.setItem(KEY, JSON.stringify(todos));
}
function render() {
  const list = document.getElementById("list");
  list.innerHTML = "";
  todos.forEach((todo, i) => {
    const li = document.createElement("li");
    li.textContent = todo.title;
    if (todo.done) li.className = "done";
    li.onclick = () => { todo.done = !todo.done; save(); render(); };
    list.appendChild(li);
  });
}
document.getElementById("f").onsubmit = (e) => {
  e.preventDefault();
  const title = document.getElementById("t").value.trim();
  if (!title) return;
  todos.push({ title, done: false });
  save();
  render();
};
render();
</script></body></html>
```"""


def test_runnable_gate_passes_complete_app_and_fails_skeleton():
    from hair_platform import agent_evaluation

    assert agent_evaluation.compute_deliverable_runnable(_RUNNABLE_TODO)["passed"] is True
    skeleton = (
        '```html\n<!DOCTYPE html>\n<html><head><style>\n/* CSS here */\n</style></head>\n'
        "<body><ul class=\"todo-list\"></ul><script src=\"app.js\"></script></body></html>\n```"
    )
    result = agent_evaluation.compute_deliverable_runnable(skeleton)
    assert result["passed"] is False
    assert any("골격" in r for r in result["reasons"]) and any("자리표시자" in r for r in result["reasons"])
    assert agent_evaluation.compute_deliverable_runnable("설계 설명만 있다.")["passed"] is False


def test_implementation_stage_is_single_author_with_runnable_gate():
    conn = _conn()
    stage = conn.execute(
        "SELECT ps.id, ps.mode, ps.gate_ids, ps.min_gate_pass_ratio FROM pipeline_stages ps "
        "JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id "
        "WHERE pt.name = '소프트웨어 개발 파이프라인' AND ps.name = '구현'"
    ).fetchone()
    assert stage[1] == "single"
    assert json.loads(stage[2]) == ["deliverable_runnable"] and stage[3] == 1.0
    roles = [r[0] for r in conn.execute(
        "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ?", (stage[0],))]
    assert roles == ["풀스택 개발자"]


# ---------- 16. 출력 한도에 걸려 덱이 중간에 잘린 경우(사업/기획 재실행 실측) ----------


def test_truncated_deck_is_retried_but_mentions_are_not():
    from hair_platform import crew_runtime

    cut = (
        '<main class="hair-deck">\n<section class="slide cover"><h1>사내 AI 교육 도입</h1></section>\n'
        '<section class="slide compare">\n<h2>위험 요인</h2>\n<table class="num">\n'
        "<tr><td>직무별 과정 분류 미확정</td><td>고</td><td>20"
    )
    assert model_output.truncated_markup(cut)
    ok, message = crew_runtime._min_length_guardrail(SimpleNamespace(raw=cut))
    assert ok is False and "잘렸" in message
    whole = cut + "</td></tr>\n</table>\n</section>\n</main>"
    assert not model_output.truncated_markup(whole)
    review = '## 판정\n\n덱 전체를 <main class="hair-deck">로 감싸야 하는데 빠졌다. 보완 요청.'
    assert not model_output.truncated_markup(review)


def test_default_ollama_output_cap_leaves_room_for_thinking():
    assert model_options.DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS >= 32768


# ---------- 17. 결정 대상이 아닌 단계 행을 승인하면 사람 검토 단계를 건너뛰던 문제 ----------


def test_resolve_stage_decision_rejects_rows_not_awaiting_decision():
    import pytest

    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "admin_rs", "adminpass", Role.ADMIN)
    template = conn.execute("SELECT id FROM pipeline_templates LIMIT 1").fetchone()[0]
    s1, s2 = [r[0] for r in conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? ORDER BY seq_order LIMIT 2", (template,))]
    brief = _id(conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'b', '[]')", (admin,)))
    task = _id(conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)))
    run = _id(conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
        "VALUES (?, ?, ?, 'paused_for_approval', 2)", (template, task, admin)))
    done = _id(conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
        "VALUES (?, ?, 1, 'passed', 'x')", (run, s1)))
    escalated = _id(conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
        "VALUES (?, ?, 1, 'escalated', 'y')", (run, s2)))
    # 이미 지난 1단계 행으로는 실행을 진행시킬 수 없다(2단계가 사람 검토 대기 중).
    with pytest.raises(ValueError, match="not_awaiting_decision"):
        pipeline_runtime.resolve_stage_decision(conn, admin, done, True, None)
    assert pipeline_runtime.resolve_stage_decision(conn, admin, escalated, True, None) == run


# ---------- 18. 하드코딩·스텁 점검(2026-11-22) 1순위 ----------


def test_session_secret_is_random_and_persisted_not_source_constant(tmp_path, monkeypatch):
    from hair_platform import app as app_module

    db_file = str(tmp_path / "s.db")
    first_conn = db.get_connection(db_file)
    first = db.session_secret(first_conn)
    first_conn.close()
    second_conn = db.get_connection(db_file)
    assert db.session_secret(second_conn) == first  # 재시작해도 같은 키(로그인 유지)
    second_conn.close()
    assert len(first) >= 48 and first != "dev-secret-change-in-production"
    assert db.session_secret(_conn()) != first  # 설치마다 다른 키
    monkeypatch.delenv("HAIR_PLATFORM_SECRET_KEY", raising=False)
    assert app_module.create_app(":memory:").state.secret_key != "dev-secret-change-in-production"
    monkeypatch.setenv("HAIR_PLATFORM_SECRET_KEY", "from-env")
    assert app_module.create_app(":memory:").state.secret_key == "from-env"


def test_generate_request_defaults_to_real_agent_not_stub():
    from hair_platform.models import GenerateRequest

    assert GenerateRequest(task_ids=[1]).framework is None  # app에서 crewai로 해석
    assert GenerateRequest(task_ids=[1], framework="stub").framework == "stub"


# ---------- 19. 하드코딩·스텁 점검 2순위 — 업무 요청 역할 후보 ----------


def test_brief_role_suggestions_come_from_pool_with_line_fallback():
    from hair_platform import pool_catalog

    conn = _conn()
    roles = pool_catalog.suggest_roles(conn, "브라우저에서 동작하는 할 일 목록 웹앱을 만들어 주세요. localStorage 저장.")
    assert roles and any("개발자" in r for r in roles)
    report = pool_catalog.suggest_roles(conn, "3분기 영업 실적 보고서: 매출 목표 대비 분석과 지역별 비중 차트")
    assert "실적 보고서 작성자" in report
    assert pool_catalog.suggest_roles(conn, "zzz qqq") == []  # 맞는 역할 없음 → 호출자가 줄 단위 폴백


# ---------- 20. 하드코딩·스텁 점검 3순위 — 실제 토큰 사용량·단가·자동 적합도 ----------


def test_direct_and_crewai_calls_record_usage_and_price_only_when_entered(monkeypatch):
    from hair_platform import llm_usage, model_registry
    from hair_platform.auth import Role, create_user

    conn = _conn()
    llm_usage.configure(conn)
    try:
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": "답"}, "prompt_eval_count": 1200, "eval_count": 300}

        monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
        assert llm.call_llm("ollama", "local-m", "s", "p") == "답"
        # CrewAI(litellm) 경로 콜백
        llm_usage._litellm_success(
            {"model": "openai/gpt-x"}, SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=500_000)), 0, 0
        )
        report = llm_usage.report(conn)
        by = {(m["provider"], m["model"]): m for m in report["models"]}
        assert by[("ollama", "local-m")]["prompt_tokens"] == 1200 and by[("ollama", "local-m")]["cost"] is None
        assert by[("openai", "gpt-x")]["cost"] is None and report["unpriced_models"] == 2  # 단가 미입력 → 금액 없음
        admin = create_user(conn, "admin_price", "adminpass", Role.ADMIN)
        model_id = model_registry.create_model(conn, admin, "openai", "gpt-x")
        model_registry.set_pricing(conn, admin, model_id, 2.0, 8.0)  # 100만 토큰당
        priced = {m["model"]: m for m in llm_usage.report(conn)["models"]}
        assert priced["gpt-x"]["cost"] == 2.0 + 0.5 * 8.0
    finally:
        llm_usage.configure(None)


def test_auto_fit_uses_gate_a_only_with_enough_samples(monkeypatch):
    from hair_platform import agent_evaluation

    summary = {"sample_count": 5, "gate_averages": {"A": 0.82, "D": 0.0, "C": 0.4}}
    monkeypatch.setattr(agent_evaluation, "aggregate_agent_score", lambda conn, aid: summary)
    assert agent_evaluation.auto_fit_score(_conn(), 1) == 0.82  # D·C(내용과 무관한 고정값)는 쓰지 않는다
    monkeypatch.setattr(agent_evaluation, "aggregate_agent_score", lambda conn, aid: dict(summary, sample_count=2))
    assert agent_evaluation.auto_fit_score(_conn(), 1) is None  # 근거 부족 — 지어내지 않는다


# ---------- 21. 하드코딩·스텁 점검 4순위 — 요구사항 정의 단계 지정·Gate 목록 API ----------


def test_traceability_uses_designated_requirements_stage(monkeypatch):
    from hair_platform import requirements_traceability_llm
    from hair_platform.auth import Role, create_user

    seen = {}

    def fake_assess(conn, requirements, output):
        seen["requirements"] = requirements
        return {"passed": True, "not_measured": False, "score": 1.0}

    monkeypatch.setattr(requirements_traceability_llm, "assess_traceability", fake_assess)
    conn = _conn()
    admin = create_user(conn, "admin_req", "adminpass", Role.ADMIN)
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "req-src", "", "general")
    s1 = pipeline_runtime.add_stage(conn, admin, tid, "자료 수집", "single", [("PM", "")])
    s2 = pipeline_runtime.add_stage(conn, admin, tid, "요구 정리", "single", [("PM", "")], is_requirements_source=True)
    s3 = pipeline_runtime.add_stage(conn, admin, tid, "검증", "single", [("PM", "")], checks_requirements_traceability=True)
    brief = _id(conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'b', '[]')", (admin,)))
    task = _id(conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)))
    run = _id(conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
                       "VALUES (?, ?, ?, 'running', 3)", (tid, task, admin)))
    for sid, out in ((s1, "1단계 자료"), (s2, "R1. 요구사항 문서")):
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output) "
                     "VALUES (?, ?, 1, 'passed', ?)", (run, sid, out))
    stage3 = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (s3,)).fetchone()
    pipeline_runtime.requirements_traceability_score(conn, run, stage3, "산출물")
    assert seen["requirements"] == "R1. 요구사항 문서"  # 1단계가 아니라 지정한 2단계


def test_stage_gate_list_accepts_platform_gates():
    from hair_platform.models import PipelineStageUpdate

    update = PipelineStageUpdate(gate_ids=["A", "requirements_traceability", "deliverable_runnable"])
    assert update.gate_ids == ["A", "requirements_traceability", "deliverable_runnable"]


# ---------- 22. 하드코딩·스텁 점검 4순위 — 조직 결과물 언어 ----------


def test_output_language_instructs_agents_and_disables_korean_only_repair():
    from hair_platform import language_qa, org_policy, skill_prompting
    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "admin_lang", "adminpass", Role.ADMIN)
    try:
        assert org_policy.output_language_instruction() == ""  # 기본(한국어) — 기존 프롬프트 그대로
        prompt_ko = skill_prompting.compose("기본 역할", conn, [], request="요청")
        org_policy.set_output_language(conn, admin, "ja")
        assert "Japanese" in org_policy.output_language_instruction()
        assert "Japanese" in skill_prompting.compose("기본 역할", conn, [], request="요청")
        assert "Japanese" not in prompt_ko
        assert language_qa.enabled() is False  # 일본어 결과물을 한국어로 바꾸면 안 된다
        import pytest

        with pytest.raises(ValueError):
            org_policy.set_output_language(conn, admin, "xx")
    finally:
        org_policy.set_output_language(conn, admin, "ko")
    assert org_policy.output_language() == "ko"


# ---------- 23. 하드코딩·스텁 점검 4순위 — 검토 실패 시 작성 단계로 되돌림 ----------


def test_failed_review_returns_to_writing_stage_with_review_feedback():
    from unittest.mock import patch

    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "admin_rw", "adminpass", Role.ADMIN)
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "rework", "", "general")
    write = pipeline_runtime.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    pipeline_runtime.add_stage(
        conn, admin, tid, "검토", "single", [("문서 검토자", "")],
        gate_ids=["A"], min_gate_pass_ratio=1.0, max_retry=2, on_fail_return_stage_id=write,
    )
    brief = _id(conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)))
    task = _id(conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)))
    conn.commit()
    roles = {r[0]: r[1] for r in conn.execute("SELECT id, role_name FROM agent_pool")}
    calls = []

    def fake_run_crew(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        role = roles[agent_ids[0]]
        calls.append((role, task_descriptions[0]))
        review_round = sum(1 for r, _ in calls if r == "문서 검토자")
        passed = not (role == "문서 검토자" and review_round == 1)
        output = "위험 요인 슬라이드가 중간에 끊겼다 — 보완 요청" if role == "문서 검토자" and not passed else f"{role} 산출물 {len(calls)}"
        return {
            "final_output": output,
            "agents": [{"agent_id": agent_ids[0], "role_name": role, "name": None, "output": output}],
            "gate_scores": {"A": {"score": 0.9 if passed else 0.3, "passed": passed}},
            "tcr": 90.0, "delegations": [],
        }

    with patch("hair_platform.crew_runtime.run_crew", side_effect=fake_run_crew):
        run_id = pipeline_runtime.start_pipeline_run(conn, admin, tid, task)
        pipeline_runtime.run_pipeline_stage_job(conn, run_id, admin)

    assert [r for r, _ in calls] == ["기획서 작성자", "문서 검토자", "기획서 작성자", "문서 검토자"]
    rewrite_prompt = calls[2][1]
    assert "위험 요인 슬라이드가 중간에 끊겼다" in rewrite_prompt  # 검토 의견이 다시 쓰기에 전달됨
    assert "기획서 작성자 산출물 1" in rewrite_prompt  # 직전 초안을 고친다
    rows = [tuple(r) for r in conn.execute(
        "SELECT ps.name, prs.attempt, prs.status FROM pipeline_run_stages prs JOIN pipeline_stages ps "
        "ON ps.id = prs.stage_id WHERE prs.pipeline_run_id = ? ORDER BY prs.id", (run_id,))]
    assert rows == [("작성", 1, "passed"), ("검토", 1, "failed_retrying"), ("작성", 2, "passed"), ("검토", 2, "passed")]
    assert conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()[0] == "completed"


def test_graph_engine_returns_to_writing_stage_and_keeps_counting_review_attempts():
    from unittest.mock import patch

    from langgraph.checkpoint.sqlite import SqliteSaver

    from hair_platform import execution_graph
    from hair_platform.auth import Role, create_user

    conn = _conn()
    admin = create_user(conn, "admin_gr", "adminpass", Role.ADMIN)
    tid = pipeline_runtime.create_pipeline_template(conn, admin, "rework-graph", "", "general")
    write = pipeline_runtime.add_stage(conn, admin, tid, "작성", "single", [("기획서 작성자", "")], is_primary_deliverable=True)
    pipeline_runtime.add_stage(
        conn, admin, tid, "검토", "single", [("문서 검토자", "")],
        gate_ids=["A"], min_gate_pass_ratio=1.0, max_retry=2, on_fail_return_stage_id=write,
    )
    brief = _id(conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '기획서', '[]')", (admin,)))
    task = _id(conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)))
    conn.commit()
    roles = {r[0]: r[1] for r in conn.execute("SELECT id, role_name FROM agent_pool")}
    calls = []

    def always_failing_review(conn_, agent_ids, task_descriptions, actor_id, **kwargs):
        role = roles[agent_ids[0]]
        calls.append((role, task_descriptions[0]))
        passed = role != "문서 검토자"  # 검토는 계속 미달 — 무한 반복 없이 사람 검토로 끝나야 한다
        output = "보완 요청: 결론 근거 부족" if not passed else f"{role} 초안 {len(calls)}"
        return {
            "final_output": output,
            "agents": [{"agent_id": agent_ids[0], "role_name": role, "name": None, "output": output}],
            "gate_scores": {"A": {"score": 0.9 if passed else 0.2, "passed": passed}},
            "tcr": 90.0, "delegations": [],
        }

    run_id = pipeline_runtime.start_pipeline_run(conn, admin, tid, task)
    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=always_failing_review
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin, saver)

    assert [r for r, _ in calls] == ["기획서 작성자", "문서 검토자", "기획서 작성자", "문서 검토자"]
    assert "보완 요청: 결론 근거 부족" in calls[2][1]
    rows = [tuple(r) for r in conn.execute(
        "SELECT ps.name, prs.attempt, prs.status FROM pipeline_run_stages prs JOIN pipeline_stages ps "
        "ON ps.id = prs.stage_id WHERE prs.pipeline_run_id = ? ORDER BY prs.id", (run_id,))]
    assert rows[:3] == [("작성", 1, "passed"), ("검토", 1, "failed_retrying"), ("작성", 2, "passed")]
    assert rows[3][:2] == ("검토", 2) and rows[3][2] in ("escalated", "failed")  # 한도 소진 → 사람 검토


def test_reference_review_stages_return_to_primary_stage():
    conn = _conn()
    rows = conn.execute(
        "SELECT s.name, r.name FROM pipeline_stages s JOIN pipeline_stages r ON r.id = s.on_fail_return_stage_id"
    ).fetchall()
    assert {tuple(r) for r in rows} >= {("코드 검증", "구현"), ("내용 검증", "작성"), ("최종 승인 판정", "작성")}


# ---------- 24. 컨텍스트 창 A안 — 레지스트리 컨텍스트 한도를 Ollama num_ctx로 보낸다 ----------


def test_registry_context_window_is_sent_to_ollama_as_num_ctx(monkeypatch):
    monkeypatch.setattr(model_options, "_CACHE", {})
    monkeypatch.setattr(model_options, "_CONTEXT_WINDOWS", {"local-m": 49152})
    assert model_options.request_options("ollama", "local-m")["num_ctx"] == 49152  # 직접 호출
    assert model_options.for_model("ollama", "local-m")["num_ctx"] == 49152  # CrewAI(litellm)
    assert "num_ctx" not in model_options.request_options("ollama", "unregistered")  # 미등록 → Ollama 기본값
    assert "num_ctx" not in model_options.request_options("openai", "local-m")  # Ollama 전용 옵션


# ---------- 25. 웹 UI 상단 버전 표기 = hair --version ----------


def test_web_ui_header_shows_same_version_as_cli():
    from hair_platform import cli, get_version, webui

    assert cli._get_version() == get_version()
    assert f">v{get_version()}</span></h1>" in webui.INDEX_HTML
    assert "__APP_VERSION__" not in webui.INDEX_HTML


# ---------- 26. pip 기본 설치(선택 extra 없음) — 그래프 엔진·CrewAI 미설치 안내 ----------


def test_graph_engine_and_crewai_missing_give_install_hint(client_and_conn, monkeypatch):
    import hair_platform

    client, conn = client_and_conn
    monkeypatch.setattr(hair_platform, "graph_engine_available", lambda: False)
    monkeypatch.setattr(hair_platform, "crewai_available", lambda: False)
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    template_id = client.get("/api/pipeline-templates").json()[0]["id"]
    team_id = client.post("/api/teams", json={"name": "그래프팀", "description": ""}).json()["id"]
    r = client.patch(f"/api/teams/{team_id}", json={"pipeline_template_id": template_id, "use_graph_engine": True})
    assert r.status_code == 400 and "hair-platform[graph]" in r.json()["detail"]
    brief = client.post("/api/briefs", json={"raw_text": "t"}).json()["id"]
    task_id = client.post(f"/api/briefs/{brief}/tasks", json={"tasks": [{"role_name": "x", "required_skills": []}]}).json()["tasks"][0]["id"]
    runs_before = conn.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0]
    r2 = client.post(f"/api/tasks/{task_id}/execute-pipeline", json={"pipeline_template_id": template_id, "use_graph_engine": True})
    assert r2.status_code == 400 and "hair-platform[graph]" in r2.json()["detail"]
    assert conn.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0] == runs_before  # 고아 실행 없음
    from hair_platform import crew_runtime
    import pytest

    with pytest.raises(RuntimeError, match=r"hair-platform\[crewai\]"):
        crew_runtime.crewai_agent_generator(conn, "x", [])
