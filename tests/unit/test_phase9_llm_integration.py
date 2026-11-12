"""Phase 9 — 실제 LLM(Anthropic/OpenAI) 연동(사용자 요청, 2026-09-26).

이전까지 Agent 생성은 `default_stub_generator`(역할명·스킬을 그대로
되돌려주는 규칙 기반 함수)만 썼고, 실행 코드 전체에 LLM 호출이 전무했다
(`grep -rniE "anthropic|openai|llm" src/`로 확인). 이번에:

1. `llm.py` — Anthropic/OpenAI 중 선택 가능한 프로바이더/모델 추상화.
2. `jobs.llm_agent_generator` — 생성 시점에 실제 LLM을 호출하는 옵션(`use_llm=True`,
   기본값은 여전히 무료 스텁).
3. `agent_runtime.execute_agent` — 온보딩된 Agent에게 실제로 업무를 맡기는
   온디맨드 실행 경로(신규).

**비용 발생 방지**: 이 테스트 파일은 `hair_platform.llm.call_llm`을 전부
mock으로 대체한다 — 실제 Anthropic/OpenAI API를 호출하지 않는다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import agent_runtime, db, llm
from hair_platform.jobs import llm_agent_generator


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_with_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def test_gc62_resolve_provider_and_model(monkeypatch):
    # 명시적으로 지정하면 그대로 쓴다.
    assert llm.resolve_provider_and_model("openai", "gpt-x") == ("openai", "gpt-x")

    # 환경변수로 provider만 지정하면 기본 모델을 쓴다.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HAIR_PLATFORM_LLM_PROVIDER", "anthropic")
    assert llm.resolve_provider_and_model(None, None) == (
        "anthropic",
        llm.DEFAULT_MODELS["anthropic"],
    )
    monkeypatch.delenv("HAIR_PLATFORM_LLM_PROVIDER")

    # API 키만 있으면 자동 판별한다.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.resolve_provider_and_model(None, None)[0] == "openai"
    monkeypatch.delenv("OPENAI_API_KEY")

    # 둘 다 없으면 명확한 에러를 낸다(조용히 아무 provider나 고르지 않는다).
    with pytest.raises(RuntimeError):
        llm.resolve_provider_and_model(None, None)

    # 지원하지 않는 provider는 거부한다.
    with pytest.raises(ValueError):
        llm.resolve_provider_and_model("cohere", None)


def test_gc63_llm_agent_generator_calls_call_llm_and_shapes_result():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    with patch("hair_platform.llm.call_llm", return_value="생성된 산출물 텍스트") as m:
        result = llm_agent_generator(
            conn,
            "백엔드 개발자",
            ["python", "fastapi"],
            provider="anthropic",
            model="claude-x",
        )
    m.assert_called_once()
    assert result["role_name"] == "백엔드 개발자"
    assert result["llm_provider"] == "anthropic"
    assert result["llm_model"] == "claude-x"
    assert result["generated_output"] == "생성된 산출물 텍스트"
    assert "agents_yaml_snippet" in result  # 샌드박스 smoke-test가 이 키를 본다


def test_gc64_generate_with_use_llm_populates_agent_pool_llm_fields(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "디자이너")

    with patch("hair_platform.llm.call_llm", return_value="디자이너 첫 산출물"):
        resp = client.post(
            f"/api/briefs/{brief_id}/generate",
            json={
                "task_ids": [task_id],
                "use_llm": True,
                "provider": "openai",
                "model": "gpt-test",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["status"] == "succeeded"

    row = conn.execute(
        "SELECT * FROM agent_pool WHERE role_name = '디자이너'"
    ).fetchone()
    assert row["llm_provider"] == "openai"
    assert row["llm_model"] == "gpt-test"
    assert row["last_output"] == "디자이너 첫 산출물"


def test_gc65_generate_without_use_llm_still_uses_free_stub(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client, "QA")

    with patch("hair_platform.llm.call_llm") as m:
        resp = client.post(
            f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
        )
    m.assert_not_called()  # 기본값은 여전히 비용 없는 스텁이어야 한다.
    assert resp.status_code == 201
    row = conn.execute("SELECT * FROM agent_pool WHERE role_name = 'QA'").fetchone()
    assert row["llm_provider"] is None
    assert row["last_output"] is None


def test_gc66_generate_with_use_llm_but_no_provider_fails_clearly(
    client_and_conn, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HAIR_PLATFORM_LLM_PROVIDER", raising=False)
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    # 2026-10-14 "기본 배포에 Agent를 포함해줘"로 `agent_pool`이 더는
    # 빈 상태로 시작하지 않는다 — "0건"이 아니라 "이 실패한 생성 시도로
    # 늘지 않았는지"를 기준으로 삼는다.
    baseline_count = conn.execute("SELECT COUNT(*) AS c FROM agent_pool").fetchone()[
        "c"
    ]
    brief_id, task_id = _make_brief_with_task(client, "PM")

    resp = client.post(
        f"/api/briefs/{brief_id}/generate",
        json={"task_ids": [task_id], "use_llm": True},
    )
    assert resp.status_code == 201  # job 자체는 만들어지지만
    body = resp.json()
    assert body["status"] == "failed"  # 항목은 실패로 정직하게 기록된다(가짜 성공 없음)
    assert "API_KEY" in body["items"][0]["error"]
    count = conn.execute("SELECT COUNT(*) AS c FROM agent_pool").fetchone()["c"]
    assert count == baseline_count  # 실패했으므로 agent_pool이 늘지 않는다


def test_gc67_execute_agent_updates_last_output_and_requires_active(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, ?)",
        ("백엔드 개발자", '["python"]', "pending_onboarding"),
    )
    agent_id = cur.lastrowid
    conn.commit()

    with pytest.raises(PermissionError):
        agent_runtime.execute_agent(conn, agent_id, admin_id, "API 스펙 초안 작성해줘")

    conn.execute("UPDATE agent_pool SET status = 'active' WHERE id = ?", (agent_id,))
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="API 스펙 초안") as m:
        output = agent_runtime.execute_agent(
            conn, agent_id, admin_id, "API 스펙 초안 작성해줘", provider="anthropic"
        )
    m.assert_called_once()
    assert output == "API 스펙 초안"
    row = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    assert row["last_output"] == "API 스펙 초안"
    assert row["llm_provider"] == "anthropic"

    with pytest.raises(ValueError):
        agent_runtime.execute_agent(conn, 999999, admin_id, "존재 안 함")

    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "agent_executed" in actions


def test_gc69_execute_agent_gives_deep_reasoning_agents_longer_llm_timeout(
    client_and_conn,
):
    """사용자 보고(2026-10-28, "프레젠테이션 자료 작성자" — "AI 모델의
    응답 생성이 시간 제한(15분)을 넘겨 중단됐습니다") — `llm.call_llm()`
    은 모든 Agent에 똑같이 15분(900초) 한도를 썼다. `tuning_config.
    reasoning=True`(agent_optimizer.py의 deep_reasoning tier)인 Agent의
    단발 실행(`execute_agent()`, 대화 `@멘션`이 쓰는 경로)에는 30분
    (1800초)을 넘겨야 한다 — `reasoning`이 없거나 False인 평범한 Agent는
    기존 그대로(호출부에 timeout_seconds를 안 넘김, 즉 None)여야 한다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, tuning_config) "
        "VALUES (?, ?, 'active', ?)",
        ("프레젠테이션 자료 작성자", "[]", '{"reasoning": true}'),
    )
    deep_agent_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        ("백엔드 개발자", "[]"),
    )
    plain_agent_id = cur.lastrowid
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, deep_agent_id, admin_id, "발표자료 만들어줘")
    assert m.call_args.kwargs["timeout_seconds"] == 1800.0

    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, plain_agent_id, admin_id, "API 스펙 초안")
    assert m.call_args.kwargs["timeout_seconds"] is None


def test_gc70_execute_agent_ollama_deep_reasoning_gets_even_longer_timeout(
    client_and_conn,
):
    """사용자 결정(2026-11-12, "클라우드보다 Ollama가 더 길게, 작업
    성격에 따라 차등하되 대화 및 업무에 같은 기준이 적용되도록") —
    `llm.py`가 Ollama의 "표준" 단발 호출 기본값 자체를 1800초로
    올렸으므로, reasoning tier가 그와 **똑같은** 1800초를 쓰면 Ollama
    안에서는 "작업 성격별 차등"이 사라진다. reasoning tier Ollama는
    표준 Ollama(1800초)보다도 길어야 하고(3600초), 클라우드 reasoning
    tier(1800초)보다도 길어야 한다(일반 원칙 — Ollama가 항상 더 김)."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, tuning_config) "
        "VALUES (?, ?, 'active', ?)",
        ("심층추론 Agent", "[]", '{"reasoning": true}'),
    )
    deep_agent_id = cur.lastrowid
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(
            conn, deep_agent_id, admin_id, "보고서 작성", provider="ollama"
        )
    assert m.call_args.kwargs["timeout_seconds"] == 3600.0


def test_gc71_ollama_standard_timeout_is_longer_than_cloud():
    """같은 결정의 "표준"(reasoning 미설정) tier 확인 — Ollama 표준
    호출도 클라우드 표준(900초)보다 길어야 한다(1800초). `execute_agent
    ()`는 표준 tier에 `timeout_seconds=None`을 넘겨(위 테스트에서 이미
    확인) `llm.call_llm()`의 provider별 기본값에 맡기므로, 여기서는
    그 기본값 자체를 `llm._call_ollama()` 레벨에서 직접 확인한다."""
    from unittest.mock import MagicMock

    from hair_platform import llm

    assert llm.OLLAMA_LLM_TIMEOUT_SECONDS > llm._CLOUD_LLM_TIMEOUT_SECONDS

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"message": {"content": "결과"}}
    with patch("httpx.post", return_value=fake_resp) as mock_post:
        llm.call_llm("ollama", "아무모델", "system", "prompt")
    assert mock_post.call_args.kwargs["timeout"] == llm.OLLAMA_LLM_TIMEOUT_SECONDS


def test_gc68_execute_endpoint_rbac_and_status_codes(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, ?)",
        ("Backend 개발자", '["python"]', "active"),
    )
    agent_id = cur.lastrowid
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/execute", json={"prompt": "일해줘"})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.post("/api/agent-pool/999999/execute", json={"prompt": "일해줘"})
    assert resp.status_code == 404

    with patch("hair_platform.llm.call_llm", return_value="완료된 작업 결과"):
        resp = client.post(
            f"/api/agent-pool/{agent_id}/execute",
            json={"prompt": "일해줘", "provider": "anthropic"},
        )
    assert resp.status_code == 200
    assert resp.json()["output"] == "완료된 작업 결과"
