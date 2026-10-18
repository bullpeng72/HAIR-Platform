"""Phase 49 — Agent별 LLM 모델/설정 최적화 추천(사용자 요청, 2026-10-18,
"Agent의 목표, 기능, 입출력 등을 고려해 가용한 LLM 모델에서 종류 및 설정
등을 최적화 setting 하는 admin 기능을 추가해줘").

**비용/네트워크 발생 방지** — `recommend_for_agent`/`recommend_all`/
`recommend_model`/`classify_role`은 전부 읽기 전용·규칙 기반이라 애초에
LLM을 호출하지 않는다. `refine_with_llm`만 실제로 `llm.call_llm`을
호출하므로, 이 파일은 그 함수 자체를 patch한다(진짜 API 호출 없음 —
test_phase48의 `crew_runtime.run_crew` patch와 같은 원칙).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import agent_optimizer as opt
from hair_platform import db, model_registry
from hair_platform.auth import Role, create_user


def _make_agent(conn, role_name, skill_tags, tuning_config=None, provider=None, model=None):
    import json

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, tuning_config, llm_provider, "
        "llm_model, status) VALUES (?, ?, ?, ?, ?, 'active')",
        (role_name, json.dumps(skill_tags), json.dumps(tuning_config or {}), provider, model),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 역할 분류 ----------


def test_known_role_uses_agent_specs_tuning_verbatim():
    tier, tuning, source = opt.classify_role("코드 검증가", ["harness-gate-ci"])
    assert source == "known_role"
    assert tier == opt.TIER_PRECISION_FAST
    assert tuning == {"temperature": 0.2, "max_retry_limit": 3}


def test_known_deep_reasoning_role():
    tier, _tuning, source = opt.classify_role("DA", ["data-model-design-guide"])
    assert source == "known_role"
    assert tier == opt.TIER_DEEP_REASONING


def test_unknown_role_falls_back_to_skill_heuristic():
    tier, _tuning, source = opt.classify_role("신규 QA 담당", ["custom-qa-checklist"])
    assert source == "skill_heuristic"
    assert tier == opt.TIER_PRECISION_FAST

    tier2, _tuning2, source2 = opt.classify_role("신규 전략가", ["custom-strategy-framework"])
    assert source2 == "skill_heuristic"
    assert tier2 == opt.TIER_DEEP_REASONING


def test_unknown_role_with_no_keyword_match_uses_default_fallback():
    tier, tuning, source = opt.classify_role("미지의 역할", ["완전히-무관한-태그"])
    assert source == "default_fallback"
    assert tier == opt.TIER_STANDARD
    assert tuning == opt._TIER_DEFAULT_TUNING[opt.TIER_STANDARD]


# ---------- 모델 성능 등급 추정 ----------


@pytest.mark.parametrize(
    "provider,name,expected_tier",
    [
        ("anthropic", "claude-opus-4-1", "premium"),
        ("anthropic", "claude-sonnet-5", "balanced"),
        ("anthropic", "claude-haiku-5-5", "economy"),
        ("openai", "gpt-4o-mini", "economy"),
        ("openai", "gpt-4o", "premium"),
        ("openai", "o3-mini", "premium"),
        ("ollama", "llama3.1", "economy"),
    ],
)
def test_model_capability_pattern_matching(provider, name, expected_tier):
    cap, _tags = opt.model_capability(provider, name)
    assert cap == expected_tier


def test_model_capability_tags_code_for_coder_models():
    _cap, tags = opt.model_capability("ollama", "qwen3-coder:latest")
    assert "code" in tags


# ---------- recommend_model ----------


def test_recommend_model_keeps_current_when_already_adequate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")

    rec = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, "anthropic", "claude-opus-4-1")
    assert rec["changed"] is False
    assert rec["provider"] == "anthropic"
    assert rec["model"] == "claude-opus-4-1"


def test_recommend_model_switches_to_better_active_match(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-haiku-5-5")
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")

    rec = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, "anthropic", "claude-haiku-5-5")
    assert rec["changed"] is True
    assert rec["model"] == "claude-opus-4-1"


def test_recommend_model_empty_registry_keeps_current_unchanged():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    rec = opt.recommend_model(conn, opt.TIER_STANDARD, "openai", "gpt-4o-mini")
    assert rec["changed"] is False
    assert rec["provider"] == "openai"
    assert rec["model"] == "gpt-4o-mini"


# ---------- recommend_for_agent / recommend_all ----------


def test_recommend_for_agent_end_to_end(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    model_registry.create_model(conn, admin_id, "anthropic", "claude-haiku-5-5")

    agent_id = _make_agent(conn, "개발자", ["tdd-ai-workflow-checklist"], provider="anthropic", model="claude-opus-4-1")
    rec = opt.recommend_for_agent(conn, agent_id)

    assert rec["role_name"] == "개발자"
    assert rec["tier"] == opt.TIER_PRECISION_FAST
    assert rec["classification_source"] == "known_role"
    # 추천 모델은 항상 활성 레지스트리 안에 있어야 한다.
    active_names = {m["name"] for m in model_registry.list_models(conn) if m["is_active"]}
    assert rec["recommended"]["model"] in active_names
    assert "temperature" in rec["tuning_diff"] or rec["tuning_diff"] == {}


def test_recommend_for_agent_missing_agent_raises():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    with pytest.raises(ValueError):
        opt.recommend_for_agent(conn, 999999)


def test_recommend_all_excludes_deprecated():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    active_id = _make_agent(conn, "개발자", [])
    deprecated_id = _make_agent(conn, "QA 기능테스터", [])
    conn.execute("UPDATE agent_pool SET status='deprecated' WHERE id=?", (deprecated_id,))
    conn.commit()

    recs = opt.recommend_all(conn)
    ids = {r["agent_id"] for r in recs}
    assert active_id in ids
    assert deprecated_id not in ids


# ---------- refine_with_llm (LLM 호출을 patch — 실제 API 호출 없음) ----------


def test_refine_with_llm_success_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    agent_id = _make_agent(conn, "DA", ["data-model-design-guide"], provider="anthropic", model="claude-opus-4-1")

    fake_response = (
        '{"provider": "anthropic", "model": "claude-opus-4-1", '
        '"tuning_config": {"temperature": 0.25}, "rationale": "데이터 모델 설계는 정밀해야 함"}'
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = opt.refine_with_llm(conn, agent_id)

    assert result["source"] == "llm_refined"
    assert result["llm_refined"]["provider"] == "anthropic"
    assert result["llm_refined"]["tuning_config"]["temperature"] == 0.25


def test_refine_with_llm_falls_back_on_malformed_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    agent_id = _make_agent(conn, "DA", ["data-model-design-guide"], provider="anthropic", model="claude-opus-4-1")

    with patch("hair_platform.llm.call_llm", return_value="이건 JSON이 아닙니다"):
        result = opt.refine_with_llm(conn, agent_id)

    assert result["source"] == "rule_based_fallback"
    assert "llm_error" in result


def test_refine_with_llm_rejects_hallucinated_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    agent_id = _make_agent(conn, "DA", ["data-model-design-guide"], provider="anthropic", model="claude-opus-4-1")

    fake_response = (
        '{"provider": "anthropic", "model": "claude-made-up-9000", '
        '"tuning_config": {}, "rationale": "환각"}'
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = opt.refine_with_llm(conn, agent_id)

    assert result["source"] == "rule_based_fallback"


# ---------- API 엔드포인트 ----------


def test_optimize_recommendation_endpoint_permissions_and_shape(client_and_conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    agent_id = _make_agent(conn, "개발자", ["tdd-ai-workflow-checklist"])

    resp = client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert resp.status_code == 200
    resp = client.get(f"/api/agent-pool/{agent_id}/optimize-recommendation")
    assert resp.status_code == 403

    resp = client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert resp.status_code == 200
    resp = client.get(f"/api/agent-pool/{agent_id}/optimize-recommendation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == agent_id
    assert body["tier"] in (opt.TIER_DEEP_REASONING, opt.TIER_PRECISION_FAST, opt.TIER_STANDARD)

    resp = client.get("/api/agent-pool/999999/optimize-recommendation")
    assert resp.status_code == 404


def test_optimize_recommendation_bulk_endpoint(client_and_conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    _make_agent(conn, "개발자", [])
    _make_agent(conn, "DA", [])

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    resp = client.get("/api/agent-pool-optimization/recommendations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) >= 2


def test_optimize_recommendation_refine_endpoint_uses_patched_llm(client_and_conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    admin_id = create_user(conn, "admin2", "adminpass2", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    agent_id = _make_agent(conn, "DA", ["data-model-design-guide"], provider="anthropic", model="claude-opus-4-1")

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    fake_response = (
        '{"provider": "anthropic", "model": "claude-opus-4-1", '
        '"tuning_config": {"temperature": 0.25}, "rationale": "ok"}'
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        resp = client.post(f"/api/agent-pool/{agent_id}/optimize-recommendation/refine")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "llm_refined"
