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


def _make_agent(
    conn, role_name, skill_tags, tuning_config=None, provider=None, model=None
):
    import json

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, tuning_config, llm_provider, "
        "llm_model, status) VALUES (?, ?, ?, ?, ?, 'active')",
        (
            role_name,
            json.dumps(skill_tags),
            json.dumps(tuning_config or {}),
            provider,
            model,
        ),
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

    tier2, _tuning2, source2 = opt.classify_role(
        "신규 전략가", ["custom-strategy-framework"]
    )
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

    rec = opt.recommend_model(
        conn, opt.TIER_DEEP_REASONING, "anthropic", "claude-opus-4-1"
    )
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

    rec = opt.recommend_model(
        conn, opt.TIER_DEEP_REASONING, "anthropic", "claude-haiku-5-5"
    )
    assert rec["changed"] is True
    assert rec["model"] == "claude-opus-4-1"


def test_recommend_model_empty_registry_keeps_current_unchanged():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    rec = opt.recommend_model(conn, opt.TIER_STANDARD, "openai", "gpt-4o-mini")
    assert rec["changed"] is False
    assert rec["provider"] == "openai"
    assert rec["model"] == "gpt-4o-mini"
    assert rec["candidates"] == []


# ---------- 사용자 요청(2026-10-26, "모델 추천의 경우 모델 레지스트리에
# 등록된 모델을 선택할 수 있도록 해줘") — 1위 추천만이 아니라 레지스트리
# 활성 모델 전체를 이 tier 기준으로 순위 매긴 후보 목록도 같이 돌아오는지.


def test_recommend_model_returns_all_active_registry_models_as_ranked_candidates(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-haiku-5-5")
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")

    rec = opt.recommend_model(
        conn, opt.TIER_DEEP_REASONING, "anthropic", "claude-haiku-5-5"
    )

    # 1위(가장 적합)가 candidates[0]과 같고, 레지스트리에 있던 모델 둘
    # 다 — admin이 둘 중 뭘 고르든 등록된 모델이다.
    assert rec["candidates"][0]["provider"] == rec["provider"]
    assert rec["candidates"][0]["model"] == rec["model"]
    assert {c["model"] for c in rec["candidates"]} == {
        "claude-haiku-5-5",
        "claude-opus-4-1",
    }
    assert (
        rec["candidates"][0]["model"] == "claude-opus-4-1"
    )  # deep_reasoning엔 opus가 더 적합


def test_recommend_model_candidates_exclude_unavailable_provider(monkeypatch):
    # API 키가 없는 provider는 "선택해도 바로 못 쓰는" 모델이라 후보에서
    # 뺀다 — ollama는 API 키가 필요 없으니 항상 후보로 남는다.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "openai", "gpt-4o-mini")
    model_registry.create_model(conn, admin_id, "ollama", "llama3.1")

    rec = opt.recommend_model(conn, opt.TIER_STANDARD, None, None)
    assert {c["model"] for c in rec["candidates"]} == {"llama3.1"}


# ---------- 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정
# 최적화 각 tier에 적용되는 모델을 매핑하는 기능을 제공... 향후 새로운
# 모델 출시되거나 모델 프로바이더 변경시에도 각 Tier별 모델 맵핑 변경
# 하여 관리") — admin이 모델을 tier(deep_reasoning/precision_fast/
# standard)에 직접 매핑하면 이름 패턴 추정보다 그 매핑이 추천 랭킹에
# 항상 먼저 반영되는지, 매핑이 없는 tier는 기존처럼 패턴 추정으로
# 폴백하는지.


def test_recommend_model_falls_back_to_pattern_estimate_when_no_model_mapped_to_tier():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    # 둘 다 ollama라 이름 패턴만 보면 둘 다 economy로 동일 추정되고,
    # 둘 다 어떤 tier에도 매핑되지 않았다 — 기존(패턴 기반) 동작 그대로.
    model_registry.create_model(conn, admin_id, "ollama", "model-a:latest")
    model_registry.create_model(conn, admin_id, "ollama", "model-b:latest")

    rec = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None)
    assert {c["model"] for c in rec["candidates"]} == {
        "model-a:latest",
        "model-b:latest",
    }
    assert all(not c["tier_mapped"] for c in rec["candidates"])


def test_recommend_model_prioritizes_model_explicitly_mapped_to_tier_over_pattern_estimate():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    # 둘 다 ollama라 이름 패턴만 보면 둘 다 economy로 동일 추정된다.
    a_id = model_registry.create_model(conn, admin_id, "ollama", "model-a:latest")
    model_registry.create_model(conn, admin_id, "ollama", "model-b:latest")

    # admin이 "model-a는 심층추론 tier에 직접 쓰고 싶다"고 레지스트리에
    # 매핑 — 이름 패턴상으로는 둘 다 economy로 동일한데도 매핑된 쪽이
    # 항상 이긴다(새 모델 출시 시 코드 수정 없이 이 매핑만 바꾸면
    # 된다는 요구사항의 핵심).
    model_registry.set_optimization_tiers(
        conn, admin_id, a_id, [opt.TIER_DEEP_REASONING]
    )

    rec = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None)
    assert rec["model"] == "model-a:latest"
    assert rec["candidates"][0] == {
        "provider": "ollama",
        "model": "model-a:latest",
        "tier_mapped": True,
    }
    assert "직접 매핑" in rec["note"]

    # 다른 tier(precision_fast)엔 아직 아무도 매핑 안 됐으니 그 tier는
    # 패턴 추정으로 폴백한다 — 매핑은 tier별로 독립적이다.
    other = opt.recommend_model(conn, opt.TIER_PRECISION_FAST, None, None)
    assert all(not c["tier_mapped"] for c in other["candidates"])


def test_recommend_model_reset_and_reassign_tier_mapping():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    a_id = model_registry.create_model(conn, admin_id, "ollama", "model-a:latest")
    b_id = model_registry.create_model(conn, admin_id, "ollama", "model-b:latest")

    model_registry.set_optimization_tiers(
        conn, admin_id, a_id, [opt.TIER_DEEP_REASONING]
    )
    assert (
        opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None)["model"]
        == "model-a:latest"
    )

    # 재설정(provider 교체를 흉내) — model-a는 매핑 초기화([]), model-b를
    # 대신 매핑. "일단 설정된 후 변경할 수 있는 기능이 제공되지 않아"에
    # 대응: 몇 번이든 다시 바꿀 수 있어야 한다.
    model_registry.set_optimization_tiers(conn, admin_id, a_id, [])
    model_registry.set_optimization_tiers(
        conn, admin_id, b_id, [opt.TIER_DEEP_REASONING]
    )

    switched = opt.recommend_model(conn, opt.TIER_DEEP_REASONING, None, None)
    assert switched["model"] == "model-b:latest"


def test_set_optimization_tiers_rejects_invalid_tier_and_missing_model():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_id = model_registry.create_model(conn, admin_id, "ollama", "model-a:latest")

    with pytest.raises(ValueError):
        model_registry.set_optimization_tiers(conn, admin_id, model_id, ["엄청좋음"])
    with pytest.raises(ValueError):
        model_registry.set_optimization_tiers(
            conn, admin_id, 999999, [opt.TIER_STANDARD]
        )


# ---------- recommend_for_agent / recommend_all ----------


def test_recommend_for_agent_end_to_end(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    model_registry.create_model(conn, admin_id, "anthropic", "claude-haiku-5-5")

    agent_id = _make_agent(
        conn,
        "백엔드 개발자",
        ["tdd-ai-workflow-checklist"],
        provider="anthropic",
        model="claude-opus-4-1",
    )
    rec = opt.recommend_for_agent(conn, agent_id)

    assert rec["role_name"] == "백엔드 개발자"
    assert rec["tier"] == opt.TIER_PRECISION_FAST
    assert rec["classification_source"] == "known_role"
    # 추천 모델은 항상 활성 레지스트리 안에 있어야 한다.
    active_names = {
        m["name"] for m in model_registry.list_models(conn) if m["is_active"]
    }
    assert rec["recommended"]["model"] in active_names
    assert "temperature" in rec["tuning_diff"] or rec["tuning_diff"] == {}
    # admin이 1위 추천 대신 고를 수 있는 후보 목록도 전부 등록된 모델이다.
    assert active_names >= {c["model"] for c in rec["recommended"]["candidates"]}
    assert rec["recommended"]["candidates"]


def test_recommend_for_agent_missing_agent_raises():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    with pytest.raises(ValueError):
        opt.recommend_for_agent(conn, 999999)


def test_recommend_all_excludes_deprecated():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    active_id = _make_agent(conn, "백엔드 개발자", [])
    deprecated_id = _make_agent(conn, "QA 기능테스터", [])
    conn.execute(
        "UPDATE agent_pool SET status='deprecated' WHERE id=?", (deprecated_id,)
    )
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
    agent_id = _make_agent(
        conn,
        "DA",
        ["data-model-design-guide"],
        provider="anthropic",
        model="claude-opus-4-1",
    )

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
    agent_id = _make_agent(
        conn,
        "DA",
        ["data-model-design-guide"],
        provider="anthropic",
        model="claude-opus-4-1",
    )

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
    agent_id = _make_agent(
        conn,
        "DA",
        ["data-model-design-guide"],
        provider="anthropic",
        model="claude-opus-4-1",
    )

    fake_response = (
        '{"provider": "anthropic", "model": "claude-made-up-9000", '
        '"tuning_config": {}, "rationale": "환각"}'
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        result = opt.refine_with_llm(conn, agent_id)

    assert result["source"] == "rule_based_fallback"


# ---------- API 엔드포인트 ----------


def test_optimize_recommendation_endpoint_permissions_and_shape(
    client_and_conn, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    agent_id = _make_agent(conn, "백엔드 개발자", ["tdd-ai-workflow-checklist"])

    resp = client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert resp.status_code == 200
    resp = client.get(f"/api/agent-pool/{agent_id}/optimize-recommendation")
    assert resp.status_code == 403

    resp = client.post(
        "/login", json={"username": "manager1", "password": "managerpass"}
    )
    assert resp.status_code == 200
    resp = client.get(f"/api/agent-pool/{agent_id}/optimize-recommendation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == agent_id
    assert body["tier"] in (
        opt.TIER_DEEP_REASONING,
        opt.TIER_PRECISION_FAST,
        opt.TIER_STANDARD,
    )

    resp = client.get("/api/agent-pool/999999/optimize-recommendation")
    assert resp.status_code == 404


def test_optimize_recommendation_bulk_endpoint(client_and_conn, monkeypatch):
    """사용자 요청(2026-11-02, "모델 관리 메뉴 영역은 관리자(admin)
    전용으로 변경") — "LLM 설정 최적화" 일괄 화면 전용인 이 엔드포인트는
    이제 admin 전용이다(Agent별 "⚡ 최적화 추천"은 별개 엔드포인트라
    manager도 그대로 쓸 수 있음, 위 refine 테스트가 그걸 확인한다)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    _make_agent(conn, "백엔드 개발자", [])
    _make_agent(conn, "DA", [])

    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    resp = client.get("/api/agent-pool-optimization/recommendations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) >= 2

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    resp = client.get("/api/agent-pool-optimization/recommendations")
    assert resp.status_code == 403, resp.text


def test_optimize_recommendation_refine_endpoint_uses_patched_llm(
    client_and_conn, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    admin_id = create_user(conn, "admin2", "adminpass2", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-opus-4-1")
    agent_id = _make_agent(
        conn,
        "DA",
        ["data-model-design-guide"],
        provider="anthropic",
        model="claude-opus-4-1",
    )

    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    fake_response = (
        '{"provider": "anthropic", "model": "claude-opus-4-1", '
        '"tuning_config": {"temperature": 0.25}, "rationale": "ok"}'
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        resp = client.post(f"/api/agent-pool/{agent_id}/optimize-recommendation/refine")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "llm_refined"
