"""LLM 설정 최적화가 성능 테스트 결과를 반영(2026-11-27 사용자 요청 — "도구 호출 안 되는 모델 적용 로직" 등).

실측: exaone 7.8B는 도구 호출을 지원하지 않는다(전체 점검 ✗). 채용 공고 점검기를 반드시 불러야 하는 채용 담당자에게
그 모델을 추천하면 단계가 매번 "필수 도구 미호출"로 실패한다."""

from __future__ import annotations

import json

from hair_platform import agent_optimizer as opt
from hair_platform import db

CHECKS_OK = {k: {"passed": True} for k in ("tools", "json", "numbers", "korean", "think_off", "long_doc")}


def _conn():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if admin is None:
        from hair_platform.auth import Role, create_user

        create_user(conn, "admin_opt", "adminpass", Role.ADMIN)
    return conn


def _model(conn, name, checks, *, tiers=(), est=50.0, mapped=()):
    actor = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    mid = conn.execute("INSERT INTO model_registry (provider, name, optimization_tiers) VALUES ('ollama', ?, ?)",
                       (name, json.dumps(list(mapped)))).lastrowid
    details = {"mode": "full", "checks": checks, "estimated_stage_s": est, "recommended_tiers": list(tiers),
               "capabilities": ["completion"] + (["tools"] if checks.get("tools", {}).get("passed") else [])}
    conn.execute("INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, mode, details) "
                 "VALUES (?, ?, 'success', 2.0, 'full', ?)", (mid, actor, json.dumps(details)))
    conn.commit()
    return mid


def _agent(conn, role, provider="ollama", model=None):
    row = conn.execute("SELECT id FROM agent_pool WHERE role_name = ?", (role,)).fetchone()
    conn.execute("UPDATE agent_pool SET llm_provider = ?, llm_model = ? WHERE id = ?", (provider, model, row[0]))
    conn.commit()
    return row[0]


def test_model_that_cannot_call_tools_is_excluded_for_tool_using_agent_even_if_mapped():
    conn = _conn()
    # 관리자가 모든 tier에 매핑해 둔 exaone — 그래도 도구를 쓰는 Agent에는 추천하지 않는다
    _model(conn, "exaone3.5:7.8b", {**CHECKS_OK, "tools": {"passed": False}}, tiers=["precision_fast"], est=76,
           mapped=("deep_reasoning", "precision_fast", "standard"))
    _model(conn, "ornith-35b", CHECKS_OK, tiers=["standard", "precision_fast", "deep_reasoning"], est=46)
    agent = _agent(conn, "채용 담당자", model="exaone3.5:7.8b")
    rec = opt.recommend_for_agent(conn, agent)
    assert rec["needs"]["tools"] == ["채용 공고 점검기"]
    assert rec["recommended"]["model"] == "ornith-35b"
    cands = {c["model"]: c for c in rec["recommended"]["candidates"]}
    assert cands["exaone3.5:7.8b"]["excluded"] is True and "도구 호출 불가" in cands["exaone3.5:7.8b"]["excluded_reason"]
    assert rec["recommended"]["candidates"][-1]["model"] == "exaone3.5:7.8b"  # 맨 뒤
    assert rec["current_fitness"]["excluded"] is True and rec["changed"] is True
    assert "쓸 수 없어" in rec["rationale"] and "채용 공고 점검기" in rec["rationale"]


def test_same_model_is_fine_for_agent_without_tools():
    conn = _conn()
    _model(conn, "exaone3.5:7.8b", {**CHECKS_OK, "tools": {"passed": False}}, tiers=["precision_fast"],
           mapped=("deep_reasoning", "precision_fast", "standard"))
    _model(conn, "ornith-35b", CHECKS_OK, tiers=["standard", "precision_fast", "deep_reasoning"])
    rec = opt.recommend_for_agent(conn, _agent(conn, "업무 요구사항 분석가"))
    assert rec["needs"]["tools"] == []
    assert rec["recommended"]["model"] == "exaone3.5:7.8b"  # 관리자 매핑 그대로 — 도구를 안 쓰니 제외 사유 없음
    assert not any(c["excluded"] for c in rec["recommended"]["candidates"])


def test_soft_issues_rank_lower_and_think_off_matters_only_when_reasoning_is_off():
    conn = _conn()
    _model(conn, "ignores-think-off", {**CHECKS_OK, "think_off": {"passed": False}}, tiers=["deep_reasoning"])
    _model(conn, "clean", CHECKS_OK, tiers=["deep_reasoning"])
    off = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None, {"tools": [], "reasoning_off": True})
    assert off["model"] == "clean"
    flagged = next(c for c in off["candidates"] if c["model"] == "ignores-think-off")
    assert flagged["issues"] == ["추론 끔 설정을 무시함"] and flagged["excluded"] is False
    on = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None, {"tools": [], "reasoning_off": False})
    assert all(not c["issues"] for c in on["candidates"])  # 추론을 켜는 Agent에는 문제 아님


def test_capability_from_quick_check_also_excludes():
    """전체 점검 전이라도 Ollama 모델 정보에 tools가 없으면(빠른 측정도 기록) 도구 쓰는 Agent에는 제외."""
    m = {"provider": "ollama", "latest_benchmark": {"details": {"mode": "quick", "capabilities": ["completion"]}}}
    assert opt.model_fitness(m, {"tools": ["x"]})["excluded"] is True
    assert opt.model_fitness(m, {"tools": []})["excluded"] is False
    untested = {"provider": "ollama", "latest_benchmark": None}
    fit = opt.model_fitness(untested, {"tools": ["x"]})
    assert fit["excluded"] is False and fit["checked"] is False  # 측정 안 한 모델은 판단하지 않음


def test_all_models_unusable_keeps_current_with_guidance():
    conn = _conn()
    _model(conn, "no-tools", {**CHECKS_OK, "tools": {"passed": False}})
    rec = opt.recommend_model(conn, opt.TIER_STANDARD, "ollama", "no-tools", {"tools": ["채용 공고 점검기"], "reasoning_off": False})
    assert rec["changed"] is False and "모두 이 Agent에 맞지 않습니다" in rec["note"]


def test_llm_refinement_never_sees_excluded_models(monkeypatch):
    conn = _conn()
    _model(conn, "exaone3.5:7.8b", {**CHECKS_OK, "tools": {"passed": False}})
    _model(conn, "ornith-35b", CHECKS_OK)
    agent = _agent(conn, "채용 담당자")
    seen = {}

    def fake_call(provider, model, system, prompt, **kwargs):
        seen["prompt"] = prompt
        return json.dumps({"provider": "ollama", "model": "exaone3.5:7.8b", "tuning_config": {}})

    monkeypatch.setattr(opt.llm, "resolve_provider_and_model", lambda *a, **k: ("ollama", "judge"))
    monkeypatch.setattr(opt.llm, "call_llm", fake_call)
    out = opt.refine_with_llm(conn, agent)
    assert "exaone" not in seen["prompt"].split("사용 가능한 활성 모델")[1]
    assert out["source"] == "rule_based_fallback"  # AI가 제외된 모델을 골라도 받지 않는다
