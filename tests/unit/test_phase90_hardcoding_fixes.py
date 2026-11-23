"""하드코딩 점검 후속 수정(2026-11-21, "제안한 우선순위대로 1번부터 진행해줘")."""

from __future__ import annotations

import pytest

from hair_platform import db, llm, model_options, model_registry


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.delenv("HAIR_PLATFORM_LLM_MODEL", raising=False)
    c = db.get_connection(":memory:")
    db.init_schema(c)
    yield c
    model_options.refresh(db_conn_empty())


def db_conn_empty():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _admin(conn):
    from hair_platform.auth import Role, create_user

    return create_user(conn, "hc_admin", "pw", Role.ADMIN)


# ---------- 1. 모델명이 비었을 때 고정 모델(llama3.1 등) 대신 레지스트리/플랫폼 기본값 ----------


def test_empty_registry_falls_back_to_builtin_default(conn):
    assert llm.resolve_provider_and_model("ollama", None) == ("ollama", llm.DEFAULT_MODELS["ollama"])


def test_registry_active_model_beats_builtin_default(conn):
    admin = _admin(conn)
    model_registry.create_model(conn, admin, "ollama", "local-a:latest")
    mid = model_registry.create_model(conn, admin, "ollama", "local-b:latest")
    model_registry.set_optimization_tiers(conn, admin, mid, ["standard"])
    # "표준" tier에 매핑된 모델이 먼저, 레지스트리 변경은 바로 반영된다.
    assert llm.resolve_provider_and_model("ollama", None)[1] == "local-b:latest"
    model_registry.set_model_active(conn, admin, mid, False)
    assert llm.resolve_provider_and_model("ollama", None)[1] == "local-a:latest"
    # 명시한 모델은 그대로.
    assert llm.resolve_provider_and_model("ollama", "explicit")[1] == "explicit"


def test_platform_default_llm_wins_for_same_provider(conn):
    admin = _admin(conn)
    model_registry.create_model(conn, admin, "ollama", "local-a:latest")
    model_registry.create_model(conn, admin, "ollama", "local-plat:latest")
    llm.set_platform_default_llm(conn, admin, "ollama", "local-plat:latest")
    assert llm.resolve_provider_and_model("ollama", None)[1] == "local-plat:latest"
    # 다른 provider에는 영향 없음(그 provider 레지스트리가 비었으면 고정값).
    assert llm.resolve_provider_and_model("openai", None)[1] == llm.DEFAULT_MODELS["openai"]


def test_agent_model_config_default_uses_registry_model(conn):
    """에이전트 설정에서 모델을 '기본값 사용'으로 저장하면 예전엔 설치되지 않은
    'llama3.1'이 저장돼 실행 시 404가 났다."""
    from hair_platform import agent_runtime

    admin = _admin(conn)
    model_registry.create_model(conn, admin, "ollama", "installed:latest")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('분석가', '[]', 'active')"
    )
    conn.commit()
    assert agent_runtime.set_model_config(conn, cur.lastrowid, admin, "ollama", None) == (
        "ollama",
        "installed:latest",
    )


def test_env_model_still_has_priority(conn, monkeypatch):
    admin = _admin(conn)
    model_registry.create_model(conn, admin, "ollama", "local-a:latest")
    monkeypatch.setenv("HAIR_PLATFORM_LLM_MODEL", "from-env")
    assert llm.resolve_provider_and_model("ollama", None)[1] == "from-env"


# ---------- 3. 임베딩 글자수 한도 — 레지스트리 컨텍스트 한도 우선 ----------


def test_embedding_limit_uses_registered_context_window(conn):
    from hair_platform import vectorstore

    admin = _admin(conn)
    # 표에 없는 긴 문맥 임베딩 모델 — 예전엔 무조건 350자로 잘렸다.
    assert vectorstore.max_embed_chars("bge-m3:latest") == 350
    mid = model_registry.create_model(conn, admin, "ollama", "bge-m3:latest")
    model_registry.set_context_window(conn, admin, mid, 8192)
    assert vectorstore.max_embed_chars("bge-m3:latest") == int(8192 * 0.68)
    long_text = "가" * 10000
    assert len(vectorstore._truncate_for_embedding(long_text, "bge-m3:latest")) == int(8192 * 0.68)
    # 표에 있는 모델도 등록값이 있으면 등록값이 우선.
    mid2 = model_registry.create_model(conn, admin, "ollama", "mxbai-embed-large:latest")
    assert vectorstore.max_embed_chars("mxbai-embed-large:latest") == 350
    model_registry.set_context_window(conn, admin, mid2, 1024)
    assert vectorstore.max_embed_chars("mxbai-embed-large:latest") == int(1024 * 0.68)
    model_registry.set_context_window(conn, admin, mid2, None)  # 초기화 → 표 값
    assert vectorstore.max_embed_chars("mxbai-embed-large:latest") == 350


# ---------- 4. 개발 크루 모델 전환은 처음 연결될 때 한 번만 ----------


def test_dev_crew_cloud_switch_happens_only_on_first_integration(conn):
    from hair_platform import reference_pipeline_seeds as seeds

    team_id = conn.execute(
        "INSERT INTO teams (name) VALUES (?)", (seeds.DEV_CREW_TEAM_NAME,)
    ).lastrowid
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, llm_provider, llm_model) "
        "VALUES ('크루원', '[]', 'active', 'ollama', 'local:latest')"
    ).lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'agent', ?)",
        (team_id, agent_id),
    )
    conn.commit()

    def provider():
        return conn.execute("SELECT llm_provider FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()[0]

    db.init_schema(conn)  # 처음 파이프라인에 연결되는 기동 — 한 번 전환(2026-10-28 결정)
    assert provider() == "openai"
    conn.execute("UPDATE agent_pool SET llm_provider = 'ollama' WHERE id = ?", (agent_id,))
    conn.commit()
    db.init_schema(conn)  # 이후 기동 — admin이 고른 Ollama를 덮어쓰지 않는다
    assert provider() == "ollama"


# ---------- 2. 최적화 추천 — 한글 스킬명 분류 + 성능 테스트 결과 반영 ----------


@pytest.mark.parametrize(
    ("role", "skills", "tier"),
    [
        ("시장조사 담당", ["딥리서치", "실시간웹검색"], "deep_reasoning"),
        ("기획 담당", ["기획서작성", "사업성분석"], "deep_reasoning"),
        ("보안 담당", ["보안점검"], "precision_fast"),
        ("개발자", ["백엔드구현", "API설계"], "precision_fast"),
        ("카피 담당", ["카피라이팅"], "standard"),
    ],
)
def test_unknown_korean_roles_are_classified_by_skill_keywords(role, skills, tier):
    """이름표에 없는 새 역할도 한글 스킬명으로 분류된다(예전엔 전부 '표준')."""
    from hair_platform import agent_optimizer

    got_tier, _tuning, source = agent_optimizer.classify_role(role, skills)
    assert (got_tier, source) == (tier, "skill_heuristic")


def test_benchmark_recommended_tier_ranks_before_name_pattern(conn):
    from hair_platform import agent_optimizer

    admin = _admin(conn)
    model_registry.create_model(conn, admin, "ollama", "aaa-model:latest")
    fast = model_registry.create_model(conn, admin, "ollama", "zzz-model:latest")
    conn.execute(
        "INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, "
        "output_chars, chars_per_second, recommended_tier) "
        "VALUES (?, ?, 'success', 1.0, 100, 100.0, 'precision_fast')",
        (fast, admin),
    )
    conn.commit()
    rec = agent_optimizer.recommend_model(conn, "precision_fast", None, None)
    assert rec["candidates"][0]["model"] == "zzz-model:latest"  # 이름순이면 aaa가 먼저였다


# ---------- 5. stub 생성 — smoke 테스트가 실제로 한 일을 결과에 남긴다 ----------


@pytest.mark.parametrize(
    ("skipped", "expected"),
    [
        (True, "skipped: Docker가 없어 샌드박스 확인을 건너뜀"),
        (False, "format_only: 생성 결과 형식만 확인(동작 검증 아님)"),
    ],
)
def test_job_result_records_what_smoke_test_actually_did(client_and_conn, monkeypatch, skipped, expected):
    """예전엔 Docker가 없어 확인을 건너뛰어도 아무 표시 없이 'succeeded'만 남았다."""
    import json

    from hair_platform import jobs
    from hair_platform.sandbox import SandboxResult

    monkeypatch.setattr(
        jobs, "run_smoke_test_in_sandbox", lambda generated: SandboxResult(ok=True, skipped=skipped)
    )
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    brief_id = client.post("/api/briefs", json={"raw_text": "기록 확인"}).json()["id"]
    task_id = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "기록 확인", "required_skills": ["python"]}]},
    ).json()["tasks"][0]["id"]
    body = client.post(f"/api/briefs/{brief_id}/generate", json={"framework": "stub", "task_ids": [task_id]}).json()
    assert body["items"][0]["status"] == "succeeded"
    stored = conn.execute("SELECT result FROM job_items WHERE id = ?", (body["items"][0]["id"],)).fetchone()[0]
    assert json.loads(stored)["smoke_test"] == expected


def test_agent_creation_form_defaults_to_crewai():
    from hair_platform import webui

    assert '<option value="crewai" selected>' in webui.INDEX_HTML
    assert '<option value="stub">stub(등록만 — LLM 호출·검증 없음)</option>' in webui.INDEX_HTML
