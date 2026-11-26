"""모델 레지스트리 — 컨텍스트 한도·생성 옵션 추천(2026-11-26 사용자 요청).

등록 화면의 "컨텍스트 한도"·"생성 옵션(top_k·top_p·최대 출력)"은 비워 두면 보수적 기본값(Ollama 8K 등)이라, 긴 문서가
잘리거나 반대로 학습 최대치(26만 토큰)를 그대로 넣어 메모리가 모자라는 일이 생긴다. 모델마다 근거와 함께 추천한다.
추천은 **채워 주기만** 하고 저장은 admin이 한다(등록할 때 "추천 기본값 함께 저장"을 켜면 그때 저장).

- **Ollama** — 모델이 스스로 알려 주는 정보(`/api/show`): 학습된 최대 컨텍스트, 층 수·KV 헤드·헤드 크기(→ 토큰당 KV 캐시
  메모리), 생각 기능, Modelfile의 제작자 권장 샘플링 값. 이 PC 메모리의 75%에서 모델 가중치를 뺀 만큼에 KV 캐시가
  들어가는 가장 큰 크기를 8K·16K·32K·64K 중에서 고른다(64K는 업무 문서 기준 실용 상한 — 더 크게 잡으면 첫 응답이 느려진다).
- **Anthropic·OpenAI** — API가 한도를 알려 주지 않아 모델 계열별 공개 한도 표로 추천한다(표에 없으면 추천 없음).
  "공개 문서로 확인" 안내를 붙인다.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess

CANDIDATES = (8192, 16384, 32768, 65536)
MEMORY_SHARE = 0.75
THINKING_MAX_OUTPUT = 32768
PLAIN_MAX_OUTPUT = 16384

# (모델 이름 패턴, 컨텍스트, 최대 출력) — 공개 문서 기준 계열 단위 추천값. 위에서부터 처음 맞는 것.
CLOUD_TABLE: tuple[tuple[str, str, int, int | None], ...] = (
    ("anthropic", r"\[1m\]|-1m\b", 1_000_000, None),
    ("anthropic", r"^claude-", 200_000, None),
    ("openai", r"^gpt-5", 400_000, 128_000),
    ("openai", r"^gpt-4\.1", 1_047_576, 32_768),
    ("openai", r"^(o1|o3|o4)", 200_000, 100_000),
    ("openai", r"^gpt-4o", 128_000, 16_384),
)


def system_memory_bytes() -> int | None:
    try:
        if platform.system() == "Darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _ollama_show(name: str) -> dict | None:
    import httpx

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        r = httpx.post(f"{base}/api/show", json={"model": name}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001 — 서버 없음·모델 없음은 추천 없음으로
        return None


def _ollama_size(name: str) -> int | None:
    """받아 둔 모델 파일 크기(바이트, /api/tags) — 가중치 메모리 계산용."""
    import httpx

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=10)
        r.raise_for_status()
        for m in r.json().get("models") or []:
            if m.get("name") == name or m.get("model") == name:
                return int(m.get("size") or 0) or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _modelfile_params(text: str) -> dict:
    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("top_k", "top_p"):
            try:
                out[parts[0]] = int(parts[1]) if parts[0] == "top_k" else float(parts[1])
            except ValueError:
                pass
    return out


def kv_bytes_per_token(info: dict) -> int | None:
    """토큰 하나가 차지하는 KV 캐시(f16) — 2(K·V) × 전체 주의 층 수 × KV 헤드 × 헤드 크기 × 2바이트.
    일부 층만 전체 주의를 쓰는 모델(full_attention_interval)은 그 층만 센다."""
    def get(suffix):
        return next((v for k, v in info.items() if k.endswith(suffix)), None)

    layers, kv_heads = get(".block_count"), get(".attention.head_count_kv")
    key_len = get(".attention.key_length")
    embedding, heads = get(".embedding_length"), get(".attention.head_count")
    if key_len is None and embedding and heads:
        key_len = int(embedding) // int(heads)
    if not (layers and kv_heads and key_len):
        return None
    interval = get(".full_attention_interval") or 1
    full_layers = max(1, -(-int(layers) // int(interval)))
    return 2 * full_layers * int(kv_heads) * int(key_len) * 2


def recommend_ollama(name: str, show: dict | None = None, memory: int | None = None, size: int | None = None) -> dict:
    show = show if show is not None else _ollama_show(name)
    if not show:
        return {"available": False, "reasons": ["Ollama 서버에서 이 모델 정보를 읽지 못했습니다(서버가 꺼졌거나 모델을 받지 않음)"]}
    info = show.get("model_info") or {}
    trained = next((v for k, v in info.items() if k.endswith(".context_length")), None)
    size = size or show.get("size") or _ollama_size(name)
    params_count = info.get("general.parameter_count")
    weights = size or (int(params_count * 0.6) if params_count else None)  # 4비트 양자화 근사(크기 정보가 없을 때)
    memory = memory if memory is not None else system_memory_bytes()
    per_token = kv_bytes_per_token(info)
    reasons: list[str] = []
    chosen = None
    for ctx in CANDIDATES:
        if trained and ctx > trained:
            break
        if memory and weights and per_token:
            if weights + per_token * ctx > memory * MEMORY_SHARE:
                break
        chosen = ctx
    if chosen is None:
        chosen = CANDIDATES[0]
        reasons.append("메모리가 빠듯해 가장 작은 8K를 권합니다 — 더 큰 문서는 잘릴 수 있습니다")
    if trained:
        reasons.append(f"학습 최대 컨텍스트 {int(trained):,}토큰")
    if memory and weights and per_token:
        reasons.append(f"이 PC 메모리 {memory / 2**30:.0f}GB의 {int(MEMORY_SHARE * 100)}%에서 모델 약 {weights / 2**30:.1f}GB를 빼고, "
                       f"토큰당 KV 캐시 약 {per_token / 1024:.0f}KB로 계산 — {chosen:,}토큰이면 KV 캐시 약 "
                       f"{per_token * chosen / 2**30:.1f}GB")
    elif not per_token:
        reasons.append("모델 구조 정보가 없어 메모리 계산 없이 학습 최대치·실용 상한으로만 골랐습니다")
    if chosen == CANDIDATES[-1] and (not trained or trained > chosen):
        reasons.append(f"{CANDIDATES[-1]:,}토큰은 업무 문서 기준 실용 상한(더 크게 잡으면 첫 응답이 느려지고 메모리를 더 씀)")
    thinking = "thinking" in (show.get("capabilities") or [])
    max_out = THINKING_MAX_OUTPUT if thinking else PLAIN_MAX_OUTPUT
    reasons.append(f"최대 출력 {max_out:,} — " + ("생각(추론) 토큰이 출력에 포함되는 모델" if thinking else "생각 기능이 없는 모델"))
    sampling = _modelfile_params(show.get("parameters") or "")
    if sampling:
        reasons.append("top_k·top_p는 모델 제작자가 Modelfile에 정한 권장값: " + ", ".join(f"{k}={v}" for k, v in sampling.items()))
    else:
        reasons.append("top_k·top_p는 제작자 권장값이 없어 비워 둡니다(모델 기본값)")
    return {"available": True, "context_window": chosen, "max_output_tokens": max_out, "top_k": sampling.get("top_k"),
            "top_p": sampling.get("top_p"), "source": "Ollama 모델 정보(/api/show)", "reasons": reasons}


def recommend_cloud(provider: str, name: str) -> dict:
    for prov, pattern, ctx, max_out in CLOUD_TABLE:
        if prov == provider and re.search(pattern, name):
            reasons = [f"{provider} 모델 계열 공개 한도 기준 컨텍스트 {ctx:,}토큰 — 공개 문서로 한 번 확인하세요"]
            if max_out:
                reasons.append(f"최대 출력 {max_out:,}토큰(계열 기준)")
            reasons.append("top_k·top_p는 비워 둡니다(provider 기본값)")
            return {"available": True, "context_window": ctx,
                    "max_output_tokens": None if provider == "anthropic" else max_out,
                    "top_k": None, "top_p": None, "source": "모델 계열별 공개 한도 표", "reasons": reasons}
    return {"available": False, "reasons": [f"{provider}의 '{name}'은 추천 표에 없는 모델입니다 — 공개 문서의 한도를 직접 입력하세요"]}


def recommend(provider: str, name: str) -> dict:
    return recommend_ollama(name) if provider == "ollama" else recommend_cloud(provider, name)
