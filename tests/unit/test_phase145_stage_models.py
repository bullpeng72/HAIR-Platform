"""단계별 모델·채점은 보조 작업 모델로(2026-11-26, 개선 계획 3-2)."""

from __future__ import annotations

from hair_platform import llm, model_options, pipeline_runtime, requirements_traceability_llm


def _req_stage(conn):
    return conn.execute(
        "SELECT s.* FROM pipeline_stages s JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "WHERE t.name = '채용 공고·면접 설계 파이프라인' AND s.seq_order = 1").fetchone()


def test_stage_model_overrides_agent_model_and_can_be_cleared(client_and_conn):
    client, conn = client_and_conn
    stage = _req_stage(conn)
    conn.execute("UPDATE agent_pool SET llm_provider = 'ollama', llm_model = 'big-35b' WHERE role_name = '업무 요구사항 분석가'")
    conn.commit()
    assert pipeline_runtime._resolve_stage_llm(conn, stage) == ("ollama", "big-35b")  # 지정 없음 → 대표 Agent
    url = f"/api/pipeline-templates/{stage['pipeline_template_id']}/stages/{stage['id']}"
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    assert client.patch(url, json={"llm_provider": "ollama", "llm_model": "qwen3:4b"}).status_code == 200
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (stage["id"],)).fetchone()
    assert pipeline_runtime._resolve_stage_llm(conn, stage) == ("ollama", "qwen3:4b")
    # Agent 설정은 그대로 — 같은 Agent를 쓰는 다른 파이프라인은 영향 없음
    assert conn.execute("SELECT llm_model FROM agent_pool WHERE role_name = '업무 요구사항 분석가'").fetchone()[0] == "big-35b"
    detail = client.get(f"/api/pipeline-templates/{stage['pipeline_template_id']}").json()
    got = next(s for s in detail["stages"] if s["id"] == stage["id"])
    assert (got["llm_provider"], got["llm_model"]) == ("ollama", "qwen3:4b")
    assert client.patch(url, json={"llm_provider": "", "llm_model": ""}).status_code == 200
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (stage["id"],)).fetchone()
    assert pipeline_runtime._resolve_stage_llm(conn, stage) == ("ollama", "big-35b")
    assert client.patch(url, json={"llm_provider": "gemini", "llm_model": "x"}).status_code in (400, 422)


def test_traceability_judge_uses_auxiliary_model(client_and_conn, monkeypatch):
    _client, conn = client_and_conn
    seen = []
    monkeypatch.setattr(llm, "resolve_provider_and_model", lambda *a, **k: ("ollama", "big-35b"))
    monkeypatch.setattr(llm, "call_llm", lambda provider, model, *a, **k: seen.append((provider, model))
                        or '{"total_requirements": 2, "covered": 2, "missing": []}')
    monkeypatch.setattr(model_options, "auxiliary_model", lambda: ("ollama", "qwen3:4b"))
    out = requirements_traceability_llm.assess_traceability(conn, "1. A\n2. B", "A와 B를 했다")
    assert seen == [("ollama", "qwen3:4b")] and out["covered"] == 2
    monkeypatch.setattr(model_options, "auxiliary_model", lambda: None)  # 보조 모델이 없으면 본 모델
    seen.clear()
    requirements_traceability_llm.assess_traceability(conn, "1. A", "A")
    assert seen == [("ollama", "big-35b")]
