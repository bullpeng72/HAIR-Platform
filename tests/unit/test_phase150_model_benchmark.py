"""성능 테스트 정교화(2026-11-27 — `model_benchmark.py`): A 측정 정확도 → C 능력 점검 → B 실제 업무 시나리오.

실측 근거: 같은 Ornith 35B가 예전 방식으로 첫 호출 21.9자/초, 바로 다음 73.2자/초(9.7초 중 5.6초가 모델 로드)였고,
생각 토큰을 세지 않아 실제 생성(45 tok/s)이 exaone(28 tok/s)보다 빠른데도 더 느린 모델로 나왔다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import db, llm, llm_usage, model_benchmark
from hair_platform.auth import Role, create_user

NS = 1e9


def _ollama_reply(content, *, load=0.0, prompt_tokens=50, prompt_s=0.05, out_tokens=100, eval_s=2.0, thinking=""):
    data = {"message": {"content": content, "thinking": thinking}, "prompt_eval_count": prompt_tokens,
            "eval_count": out_tokens, "load_duration": load * NS, "prompt_eval_duration": prompt_s * NS,
            "eval_duration": eval_s * NS}
    return data


class FakeOllama:
    """프롬프트에 따라 답하고 Ollama 측정값을 `llm_usage`에 넘기는 대역. 첫 호출만 모델 로드 5초."""

    def __init__(self, *, salary="72500000", ignore_think_off=False, english=False):
        self.calls = 0
        self.salary = salary
        self.ignore_think_off = ignore_think_off
        self.english = english

    def __call__(self, provider, model, system, prompt, **kwargs):
        self.calls += 1
        think_off = llm._THINK.get() is False
        thinking = "" if think_off and not self.ignore_think_off else "먼저 생각해 보면..." * 10
        load = 5.0 if self.calls == 1 else 0.0
        if "JSON" in prompt:
            content = json.dumps({"salary": self.salary, "start_date": "2027-03-02", "years": 7})
        elif "계약 기간" in prompt:
            data = _ollama_reply("2027년 1월 1일부터 2027년 12월 31일까지", prompt_tokens=6400, prompt_s=8.0, out_tokens=30, eval_s=0.7)
            llm_usage.record_ollama(model, data)
            return data["message"]["content"]
        elif "회의록" in prompt:
            content = ("Let me think about this.\n" if self.english else "") + \
                "회의록 요약은 결정 사항을 먼저 쓰고, 담당자와 기한을 분명히 적으며, 논의 과정은 짧게 줄이는 것이 원칙이다. " * 2
        else:
            content = "좋은 제품 기획은 사용자의 문제를 정확히 정의하고 해결 방법과 성공 기준을 분명히 하는 일이다."
        data = _ollama_reply(content, load=load, out_tokens=90, eval_s=2.0, thinking=thinking)
        llm_usage.record_ollama(model, data)
        return content


def _setup():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "admin_bench", "adminpass", Role.ADMIN)
    mid = int(conn.execute("INSERT INTO model_registry (provider, name) VALUES ('ollama', 'fake-model')").lastrowid or 0)
    conn.commit()
    return conn, admin, mid


def _fake_tools(answer="E-1024 연봉은 65,000,000원입니다.", call_tool=True):
    def fake(provider, model, system, prompt, tools, **kwargs):
        if call_tool:
            tools[0].run(employee_id="E-1024")
        return answer
    return fake


def test_quick_excludes_model_load_and_uses_token_speed_including_thinking():
    conn, admin, mid = _setup()
    fake = FakeOllama()
    with patch.object(llm, "call_llm", side_effect=fake), \
         patch.object(model_benchmark, "_capabilities", return_value=["completion", "tools", "thinking"]):
        r = model_benchmark.run_benchmark(conn, mid, admin, mode="quick")
    d = r["details"]
    assert fake.calls == 1 + 3 + 3  # 예열 + 기본 3회 + 생각 끔 3회
    assert d["load_s"] == 5.0  # 첫 로드는 따로 기록하고
    assert d["default"]["first_token_s"] < 1.0  # 측정값에는 섞이지 않는다
    assert d["default"]["gen_tps"] == 45.0  # 90토큰 / 2초 — 생각 토큰까지 센 생성 속도(글자 수가 아님)
    assert d["default"]["thinking_chars"] > 0 and d["think_off"]["thinking_chars"] == 0
    assert d["estimated_stage_s"] == round(1500 / 45.0, 1)  # 출력 1,500토큰 생성 시간 ≈ 33초
    assert d["recommended_tiers"] == ["standard", "precision_fast"]  # 빠른 측정은 심층추론을 추천하지 않는다
    stored = conn.execute("SELECT mode, details FROM model_benchmarks WHERE id = ?", (r["id"],)).fetchone()
    assert stored["mode"] == "quick" and json.loads(stored["details"])["default"]["runs"] == 3


def test_slow_model_is_not_recommended_as_deep_reasoning_by_speed():
    assert model_benchmark.recommend_tiers_quick(5.0) == []  # 1,500토큰에 300초 — 예전엔 "심층추론"
    assert model_benchmark.recommend_tiers_quick(10.0) == ["precision_fast"]
    assert model_benchmark.recommend_tiers_quick(None) == []


def test_full_check_capable_model_gets_all_tiers():
    conn, admin, mid = _setup()
    with patch.object(llm, "call_llm", side_effect=FakeOllama()), \
         patch.object(llm, "call_llm_with_tools", side_effect=_fake_tools()), \
         patch.object(model_benchmark, "_capabilities", return_value=["completion", "tools", "thinking"]):
        r = model_benchmark.run_benchmark(conn, mid, admin, mode="full")
    d = r["details"]
    assert {k: v["passed"] for k, v in d["checks"].items()} == {
        "tools": True, "json": True, "numbers": True, "korean": True, "think_off": True, "long_doc": True}
    assert d["checks"]["long_doc"]["prefill_tps"] == 800.0  # 6,400토큰 / 8초
    assert d["estimated_stage_s"] == round(8000 / 800 + 1500 / 45, 1)  # 입력 읽기 + 출력 생성 ≈ 43초
    assert d["recommended_tiers"] == ["standard", "precision_fast", "deep_reasoning"] and d["warnings"] == []


def test_full_check_catches_failures_seen_in_real_models():
    """실측 실패 유형: 도구 미지원(exaone), 수치 변형(qwen3:4b-instruct), 생각 끄기 무시·영어 독백(qwen3:4b)."""
    conn, admin, mid = _setup()
    fake = FakeOllama(salary="7250000", ignore_think_off=True, english=True)
    with patch.object(llm, "call_llm", side_effect=fake), \
         patch.object(llm, "call_llm_with_tools", side_effect=AssertionError("부르면 안 됨")), \
         patch.object(model_benchmark, "_capabilities", return_value=["completion", "thinking"]):
        r = model_benchmark.run_benchmark(conn, mid, admin, mode="full")
    checks, d = r["details"]["checks"], r["details"]
    assert checks["tools"]["passed"] is False and "지원하지 않는다" in checks["tools"]["reason"]
    assert checks["numbers"]["passed"] is False and "salary" in checks["numbers"]["reason"]
    assert checks["json"]["passed"] is True  # 형식은 맞지만 값이 틀림 — 따로 잡는다
    assert checks["think_off"]["passed"] is False
    assert checks["korean"]["passed"] is False and "독백" in checks["korean"]["reason"]
    assert d["recommended_tiers"] == []
    assert any("도구" in w for w in d["warnings"]) and any("수치" in w for w in d["warnings"])


def test_tool_check_requires_actually_calling_the_tool():
    with patch.object(llm, "call_llm_with_tools", side_effect=_fake_tools(answer="65,000,000원", call_tool=False)):
        r = model_benchmark._check_tools("ollama", "m", ["tools"])
    assert r["passed"] is False and r["reason"] == "도구를 부르지 않음"  # 답을 지어내도 통과 아님


def test_long_doc_flags_truncated_context():
    def tiny_ctx(provider, model, system, prompt, **kwargs):
        llm_usage.record_ollama(model, _ollama_reply("2027년 1월 1일", prompt_tokens=2048, prompt_s=2.0, out_tokens=10, eval_s=0.2))
        return "2027년 1월 1일부터"
    with patch.object(llm, "call_llm", side_effect=tiny_ctx):
        r = model_benchmark._check_long_doc("ollama", "m")
    assert r["passed"] is False and "잘림" in r["reason"]


def test_api_mode_parameter_and_latest_full_kept(client_and_conn):
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    mid = client.post("/api/model-registry", json={"provider": "ollama", "name": "fake-api"}).json()["id"]
    with patch.object(llm, "call_llm", side_effect=FakeOllama()), \
         patch.object(llm, "call_llm_with_tools", side_effect=_fake_tools()), \
         patch.object(model_benchmark, "_capabilities", return_value=["tools", "thinking"]):
        assert client.post(f"/api/model-registry/{mid}/benchmark?mode=full").status_code == 201
        assert client.post(f"/api/model-registry/{mid}/benchmark?mode=quick").status_code == 201
        assert client.post(f"/api/model-registry/{mid}/benchmark?mode=bogus").status_code == 400
    row = next(r for r in client.get("/api/model-registry").json() if r["id"] == mid)
    assert row["latest_benchmark"]["details"]["mode"] == "quick"
    assert row["latest_benchmark"]["latest_full"]["details"]["mode"] == "full"  # 최근 전체 점검 결과도 함께
