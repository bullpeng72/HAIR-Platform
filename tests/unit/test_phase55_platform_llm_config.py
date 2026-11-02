"""Phase 55 — 플랫폼 공통 기본 LLM 설정(사용자 요청, 2026-11-02, "LLM
설정에 HAIR-Platform 공통/기본 동작을 위한 LLM을 지정할 수 있게").

`embedding_config`(`vectorstore.py`)와 같은 `id=1` 싱글턴 패턴 —
`llm.get_platform_default_llm()`/`llm.set_platform_default_llm()`이
CRUD를, `llm.resolve_provider_and_model()`의 새 키워드 전용 `conn`
인자가 "설정해두면 환경변수보다 먼저 적용"을 담당한다.

**정정(2026-11-02, 사용자 요청 "모델 레지스트리에 등록된 모델에 한해
선택할 수 있도록 개선")** — `set_platform_default_llm()`이 이제
`model_registry`에 등록+활성화(`is_active=1`)된 (provider, model)
조합만 허용한다. 이 파일의 테스트는 그래서 먼저
`model_registry.create_model()`로 대상 모델을 등록해둔다."""

from __future__ import annotations

import pytest

from hair_platform import db, llm, model_registry
from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


# ---------- CRUD ----------


def test_get_platform_default_llm_returns_none_when_unset():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    assert llm.get_platform_default_llm(conn) is None


def test_set_and_get_platform_default_llm_roundtrip():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "openai", "gpt-4o-mini")
    model_registry.create_model(conn, admin_id, "ollama", "llama3.1")
    llm.set_platform_default_llm(conn, admin_id, "openai", "gpt-4o-mini")
    assert llm.get_platform_default_llm(conn) == {
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    # 다시 설정하면 upsert(기존 행 교체) — 행이 여러 개 쌓이지 않는다.
    llm.set_platform_default_llm(conn, admin_id, "ollama", "llama3.1")
    assert llm.get_platform_default_llm(conn) == {
        "provider": "ollama",
        "model": "llama3.1",
    }
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM platform_llm_config").fetchone()["c"]
        == 1
    )


def test_set_platform_default_llm_rejects_unsupported_provider():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    with pytest.raises(ValueError):
        llm.set_platform_default_llm(conn, admin_id, "cohere", "some-model")


def test_set_platform_default_llm_rejects_empty_model():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    with pytest.raises(ValueError):
        llm.set_platform_default_llm(conn, admin_id, "anthropic", "   ")


def test_set_platform_default_llm_rejects_model_not_in_registry():
    """사용자 요청(2026-11-02, "모델 레지스트리에 등록된 모델에 한해
    선택할 수 있도록 개선") — 레지스트리에 전혀 없는 모델명은 거부."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    with pytest.raises(ValueError, match="model_not_registered"):
        llm.set_platform_default_llm(conn, admin_id, "anthropic", "claude-sonnet-5")


def test_set_platform_default_llm_rejects_deactivated_model():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_id = model_registry.create_model(conn, admin_id, "anthropic", "claude-sonnet-5")
    model_registry.set_model_active(conn, admin_id, model_id, False)
    with pytest.raises(ValueError, match="model_not_registered"):
        llm.set_platform_default_llm(conn, admin_id, "anthropic", "claude-sonnet-5")


def test_set_platform_default_llm_records_audit():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "anthropic", "claude-sonnet-5")
    llm.set_platform_default_llm(conn, admin_id, "anthropic", "claude-sonnet-5")
    row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'platform_llm_config_updated' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["actor_id"] == admin_id


# ---------- resolve_provider_and_model(conn=) ----------


def test_resolve_provider_and_model_without_conn_is_unchanged(monkeypatch):
    """`conn` 생략 시 기존 동작(환경변수→자동판별)이 전혀 안 바뀐다 —
    `test_gc62_resolve_provider_and_model`과 같은 전제."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HAIR_PLATFORM_LLM_PROVIDER", "anthropic")
    assert llm.resolve_provider_and_model(None, None) == (
        "anthropic",
        llm.DEFAULT_MODELS["anthropic"],
    )


def test_resolve_provider_and_model_prefers_platform_config_over_env(monkeypatch):
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "openai", "gpt-x")
    llm.set_platform_default_llm(conn, admin_id, "openai", "gpt-x")
    monkeypatch.setenv("HAIR_PLATFORM_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HAIR_PLATFORM_LLM_MODEL", "llama-env")

    assert llm.resolve_provider_and_model(None, None, conn=conn) == ("openai", "gpt-x")


def test_resolve_provider_and_model_explicit_arg_still_wins_over_platform_config():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin_id, "openai", "gpt-x")
    llm.set_platform_default_llm(conn, admin_id, "openai", "gpt-x")

    assert llm.resolve_provider_and_model("anthropic", "claude-y", conn=conn) == (
        "anthropic",
        "claude-y",
    )


def test_resolve_provider_and_model_falls_back_to_env_when_platform_config_unset(
    monkeypatch,
):
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    monkeypatch.setenv("HAIR_PLATFORM_LLM_PROVIDER", "ollama")
    assert llm.resolve_provider_and_model(None, None, conn=conn) == (
        "ollama",
        llm.DEFAULT_MODELS["ollama"],
    )


# ---------- API 엔드포인트 ----------


def test_platform_llm_config_endpoint_requires_admin(client_and_conn):
    """사용자 요청(2026-11-02, "모델 관리 메뉴 영역은 관리자(admin)
    전용으로 변경") — manager도 이제 403(원래는 admin+manager)."""
    client, _conn = client_and_conn
    for username, password in (
        ("manager1", "managerpass"),
        ("member1", "memberpass"),
        ("viewer1", "viewerpass"),
    ):
        _login(client, username, password)
        assert client.get("/api/platform-llm-config").status_code == 403, username
        assert (
            client.put(
                "/api/platform-llm-config", json={"provider": "openai", "model": "x"}
            ).status_code
            == 403
        ), username


def test_platform_llm_config_endpoint_shows_autodetect_when_unset(
    client_and_conn, monkeypatch
):
    client, _conn = client_and_conn
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/platform-llm-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_explicit"] is False
    assert body["provider"] == "openai"


def test_platform_llm_config_put_then_get_roundtrip(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    model_registry.create_model(conn, admin_id, "anthropic", "claude-sonnet-5")
    put_resp = client.put(
        "/api/platform-llm-config",
        json={"provider": "anthropic", "model": "claude-sonnet-5"},
    )
    assert put_resp.status_code == 200, put_resp.text

    get_resp = client.get("/api/platform-llm-config")
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "is_explicit": True,
    }


def test_platform_llm_config_put_rejects_invalid_provider(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.put(
        "/api/platform-llm-config", json={"provider": "cohere", "model": "x"}
    )
    # Pydantic Literal 검증에서 바로 422(요청 바디 단계) — 서비스 계층
    # ValueError(400)까지 갈 필요도 없다.
    assert resp.status_code == 422


def test_platform_llm_config_put_rejects_model_not_in_registry(client_and_conn):
    """사용자 요청(2026-11-02, "모델 레지스트리에 등록된 모델에 한해
    선택할 수 있도록 개선") — API를 직접 호출해도(UI 우회) 레지스트리에
    없는 모델은 400으로 막힌다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.put(
        "/api/platform-llm-config",
        json={"provider": "anthropic", "model": "claude-never-registered"},
    )
    assert resp.status_code == 400
