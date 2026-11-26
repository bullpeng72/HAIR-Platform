"""모델 레지스트리 — 컨텍스트 한도·생성 옵션 추천(2026-11-26 사용자 요청, `model_recommend.py`).

Ollama 응답 모양은 이 PC의 실제 `/api/show` 출력(2026-11-26)에서 옮겼다 — 네트워크 없이 계산만 확인한다."""

from __future__ import annotations

import json

from hair_platform import model_recommend

GiB = 2**30

ORNITH = {  # Ornith 35B MoE — 4층마다 1층만 전체 주의
    "model_info": {"qwen35moe.context_length": 262144, "qwen35moe.block_count": 41,
                   "qwen35moe.attention.head_count": 16, "qwen35moe.attention.head_count_kv": 2,
                   "qwen35moe.attention.key_length": 256, "qwen35moe.full_attention_interval": 4,
                   "general.parameter_count": 35505251456},
    "capabilities": ["tools", "thinking", "completion", "vision"],
    "parameters": 'stop "<|im_start|>"',
}
QWEN_INSTRUCT = {
    "model_info": {"qwen3.context_length": 262144, "qwen3.block_count": 36, "qwen3.attention.head_count": 32,
                   "qwen3.attention.head_count_kv": 8, "qwen3.attention.key_length": 128},
    "capabilities": ["tools", "thinking", "completion"],
    "parameters": "repeat_penalty 1\ntemperature 0.7\ntop_k 20\ntop_p 0.8",
}
EXAONE = {  # 학습 최대 32K, key_length 없음(임베딩/헤드로 계산), 생각 기능 없음
    "model_info": {"exaone.context_length": 32768, "exaone.block_count": 32, "exaone.attention.head_count": 32,
                   "exaone.attention.head_count_kv": 8, "exaone.embedding_length": 4096},
    "capabilities": ["completion"],
    "parameters": "repeat_penalty 1\ntemperature 1",
}


def test_ollama_recommendation_uses_model_info_memory_and_modelfile():
    r = model_recommend.recommend_ollama("ornith", show=ORNITH, memory=32 * GiB, size=int(21.1 * GiB))
    assert r["context_window"] == 65536 and r["max_output_tokens"] == 32768  # 실용 상한, 생각 모델
    assert r["top_k"] is None and any("KV 캐시" in x for x in r["reasons"])
    q = model_recommend.recommend_ollama("qwen3:4b-instruct", show=QWEN_INSTRUCT, memory=32 * GiB, size=int(2.3 * GiB))
    assert (q["top_k"], q["top_p"]) == (20, 0.8)  # 제작자 권장값
    e = model_recommend.recommend_ollama("exaone", show=EXAONE, memory=32 * GiB, size=int(4.5 * GiB))
    assert e["context_window"] == 32768 and e["max_output_tokens"] == 16384  # 학습 최대치에서 멈춤, 생각 없음


def test_memory_limits_the_context_size():
    # 메모리가 빠듯하면 더 작은 컨텍스트 — 16GB PC에서 9GB 모델(토큰당 KV 144KB): 12GB-9GB=3GB → 16K(2.25GB)
    r = model_recommend.recommend_ollama("x", show=QWEN_INSTRUCT, memory=16 * GiB, size=9 * GiB)
    assert r["context_window"] == 16384
    tight = model_recommend.recommend_ollama("x", show=QWEN_INSTRUCT, memory=16 * GiB, size=12 * GiB)
    assert tight["context_window"] == 8192 and any("빠듯" in x for x in tight["reasons"])
    assert model_recommend.kv_bytes_per_token(ORNITH["model_info"]) == 2 * 11 * 2 * 256 * 2


def test_unknown_or_unreachable_models_have_no_recommendation(monkeypatch):
    monkeypatch.setattr(model_recommend, "_ollama_show", lambda name: None)
    assert model_recommend.recommend("ollama", "없는모델")["available"] is False
    assert model_recommend.recommend("openai", "custom-finetune")["available"] is False
    c = model_recommend.recommend("anthropic", "claude-sonnet-5-5")
    assert c["context_window"] == 200_000 and c["max_output_tokens"] is None
    assert model_recommend.recommend("openai", "gpt-4o-mini")["max_output_tokens"] == 16_384


def test_api_recommend_and_apply_merges_options(client_and_conn, monkeypatch):
    client, conn = client_and_conn
    monkeypatch.setattr(model_recommend, "_ollama_show", lambda name: QWEN_INSTRUCT)
    monkeypatch.setattr(model_recommend, "_ollama_size", lambda name: int(2.3 * GiB))
    monkeypatch.setattr(model_recommend, "system_memory_bytes", lambda: 32 * GiB)
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    mid = client.post("/api/model-registry", json={"provider": "ollama", "name": "qwen3:4b-instruct"}).json()["id"]
    conn.execute("UPDATE model_registry SET sampling_options = ? WHERE id = ?", (json.dumps({"top_p": 0.5}), mid))
    conn.commit()
    rec = client.get(f"/api/model-registry/{mid}/recommendation").json()
    assert rec["context_window"] == 65536
    row = conn.execute("SELECT context_window FROM model_registry WHERE id = ?", (mid,)).fetchone()
    assert row[0] is None  # 조회는 저장하지 않는다
    applied = client.post(f"/api/model-registry/{mid}/apply-recommendation").json()
    assert applied["context_window"] == 65536
    saved = conn.execute("SELECT context_window, sampling_options FROM model_registry WHERE id = ?", (mid,)).fetchone()
    assert saved[0] == 65536 and json.loads(saved[1]) == {"top_k": 20, "top_p": 0.8, "max_output_tokens": 32768}
    cloud = client.post("/api/model-registry", json={"provider": "openai", "name": "custom-x"}).json()["id"]
    assert client.post(f"/api/model-registry/{cloud}/apply-recommendation").status_code == 409
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get(f"/api/model-registry/{mid}/recommendation").status_code == 403
