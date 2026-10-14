"""Phase 10 — Ollama 추가 + Agent별 모델 설정 기능(사용자 요청, 2026-09-26).

1. llm.py에 Ollama provider 추가(로컬 HTTP, API 키 불필요, 자동판별
   대상 아님 — 명시적으로 지정해야 한다).
2. `PATCH /api/agent-pool/{id}/model`(신규) — Agent별 기본
   provider/model을 저장한다(비용 없음, LLM 호출 안 함).
3. `execute_agent`가 provider/model을 안 받으면 Agent에 저장된 모델
   설정을 우선 쓰고, 그것도 없으면 전역 기본값으로 판별한다.

**비용 발생 방지**: `hair_platform.llm.call_llm`/`httpx.post`를 전부 mock으로
대체한다 — 실제 Anthropic/OpenAI/Ollama를 호출하지 않는다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hair_platform import agent_runtime, llm


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc69_ollama_provider_supported_and_not_auto_detected(monkeypatch):
    assert "ollama" in llm.DEFAULT_MODELS

    # 명시적으로 지정하면 API 키 없이도 동작한다(로컬 서버라 키 개념이 없다).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider, model = llm.resolve_provider_and_model("ollama", None)
    assert provider == "ollama"
    assert model == llm.DEFAULT_MODELS["ollama"]

    # 자동 판별 대상은 아니다 — 키가 없으면 여전히 에러(ollama를 마음대로 고르지 않는다).
    with pytest.raises(RuntimeError):
        llm.resolve_provider_and_model(None, None)


def test_gc69b_call_llm_ollama_hits_local_http_api():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"message": {"content": "로컬 모델 응답"}}
    fake_resp.raise_for_status.return_value = None
    with patch("httpx.post", return_value=fake_resp) as m:
        output = llm.call_llm("ollama", "llama3.1", "system", "prompt")
    assert output == "로컬 모델 응답"
    m.assert_called_once()
    assert "localhost:11434" in m.call_args[0][0]


def test_gc70_set_model_config_persists_without_calling_llm(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, ?)",
        ("Backend 개발자", '["python"]', "active"),
    )
    agent_id = cur.lastrowid
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.patch(
        f"/api/agent-pool/{agent_id}/model", json={"provider": "ollama"}
    )
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.llm.call_llm") as m:
        resp = client.patch(
            f"/api/agent-pool/{agent_id}/model", json={"provider": "ollama"}
        )
    m.assert_not_called()  # 설정만 저장 — 절대 LLM을 호출하지 않는다.
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_provider"] == "ollama"
    assert body["llm_model"] == llm.DEFAULT_MODELS["ollama"]

    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    assert row["llm_provider"] == "ollama"
    assert row["llm_model"] == llm.DEFAULT_MODELS["ollama"]

    resp = client.patch("/api/agent-pool/999999/model", json={"provider": "openai"})
    assert resp.status_code == 404

    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "agent_model_config_updated" in actions


def test_gc71_set_model_config_custom_model_overrides_default(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, ?)",
        ("DB 개발자", '["sql"]', "active"),
    )
    agent_id = cur.lastrowid
    conn.commit()

    _login(client, "admin1", "adminpass")
    resp = client.patch(
        f"/api/agent-pool/{agent_id}/model",
        json={"provider": "openai", "model": "gpt-custom"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "id": agent_id,
        "llm_provider": "openai",
        "llm_model": "gpt-custom",
    }


def test_gc72_execute_uses_agent_stored_model_config_by_default(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    cur = conn.execute(
        "INSERT INTO agent_pool "
        "(role_name, skill_tags, status, llm_provider, llm_model) VALUES (?, ?, ?, ?, ?)",
        ("설계자", '["아키텍처설계"]', "active", "ollama", "llama3.1"),
    )
    agent_id = cur.lastrowid
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="설계 초안") as m:
        agent_runtime.execute_agent(conn, agent_id, admin_id, "설계해줘")

    m.assert_called_once()
    call_args = m.call_args[0]
    assert call_args[0] == "ollama"  # 저장된 provider를 그대로 썼다.
    assert call_args[1] == "llama3.1"  # 저장된 model을 그대로 썼다.
    assert call_args[3] == "설계해줘"
