"""모델별 생성 옵션(샘플링) — 모델 레지스트리 값을 provider 호출 인자로 바꾼다.

사용자 요청(2026-11-21, "모델 레지스트리에서 ollama, openai, anthropic 등 다양한
provider의 모델을 상황에 따라 바꿔 사용하도록 되어 있어. 특정 모델만을 위한
하드코딩은 없도록 해줘") — 직전 개선에서 Qwen 계열 권장값(top_k=20,
top_p=0.8)을 **모든 Ollama 모델**에 기본으로 걸었던 것을 걷어 내고, 모델마다
admin이 모델 레지스트리(`model_registry.sampling_options`)에 적는 값으로 바꾼다.
비어 있으면 아무것도 보내지 않는다(각 모델의 기본값).

provider별 변환:
- ollama: `options`(top_k, top_p)
- anthropic: Messages API 인자(top_k, top_p)
- openai: Chat Completions 인자(top_p만 — top_k는 지원하지 않아 보내지 않는다)
- CrewAI(litellm): 위와 같은 규칙의 키워드 인자

`llm.py`(호출 지점)는 DB 연결 없이 불리므로, 값은 기동 시(`db.init_schema`)와
변경 시(`model_registry.set_sampling_options`)에 이 모듈의 캐시로 올린다.
의존성이 없는 모듈이라 `llm`·`crew_runtime`·`model_registry` 어디서 import해도
순환이 생기지 않는다.
"""

from __future__ import annotations

import json
import sqlite3

# 키 → (타입, 하한, 상한)
ALLOWED_KEYS: dict[str, tuple[type, float, float]] = {
    "top_k": (int, 1, 1000),
    "top_p": (float, 0.0, 1.0),
}
_PROVIDER_KEYS = {
    "ollama": ("top_k", "top_p"),
    "anthropic": ("top_k", "top_p"),
    "openai": ("top_p",),
}

_CACHE: dict[tuple[str, str], dict] = {}


def validate(options: dict | None) -> dict:
    """허용 키·범위 검사 후 정규화한 dict(빈 값·None인 키는 뺀다)."""
    cleaned: dict = {}
    for key, value in (options or {}).items():
        if value is None or value == "":
            continue
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported_sampling_option:{key}")
        typ, low, high = ALLOWED_KEYS[key]
        try:
            value = typ(value)
        except (TypeError, ValueError):
            raise ValueError(f"invalid_sampling_option:{key}") from None
        if key == "top_p" and not (low < value <= high):
            raise ValueError("top_p_out_of_range")
        if key == "top_k" and not (low <= value <= high):
            raise ValueError("top_k_out_of_range")
        cleaned[key] = value
    return cleaned


# 모델명이 비었을 때 쓸 기본 모델(2026-11-21, 하드코딩 점검 1번) — 예전엔
# `llm.DEFAULT_MODELS`(ollama='llama3.1' 등) 고정값이 들어가, 그 모델이 설치·등록돼
# 있지 않으면 실행 시 404가 났다. 이제 플랫폼 기본 LLM → 레지스트리 활성 모델 순으로
# 고르고, 레지스트리에 그 provider 모델이 하나도 없을 때만 고정값으로 떨어진다.
# `llm.resolve_provider_and_model()`은 DB 연결 없이 불리는 곳이 많아 캐시로 둔다.
_ACTIVE_MODELS: dict[str, list[str]] = {}
_PLATFORM_DEFAULT: dict[str, str] = {}
# 모델명 → admin이 등록한 컨텍스트 한도(토큰). 임베딩 글자수 한도 계산에 쓴다
# (`vectorstore.max_embed_chars`, 하드코딩 점검 3번).
_CONTEXT_WINDOWS: dict[str, int] = {}


def refresh(conn: sqlite3.Connection) -> None:
    """레지스트리·플랫폼 기본 LLM을 캐시로 다시 읽는다(기동 시, 레지스트리 변경 시)."""
    _CACHE.clear()
    _ACTIVE_MODELS.clear()
    _PLATFORM_DEFAULT.clear()
    _CONTEXT_WINDOWS.clear()
    try:
        rows = conn.execute(
            "SELECT provider, name, sampling_options, is_active, optimization_tiers, "
            "context_window FROM model_registry"
        ).fetchall()
    except sqlite3.OperationalError:  # 컬럼이 생기기 전(마이그레이션 도중)
        return
    ranked: list[tuple[int, str, str]] = []
    for provider, name, raw, is_active, tiers_raw, context_window in rows:
        options = json.loads(raw or "{}")
        if options:
            _CACHE[(provider, name)] = options
        if context_window:
            _CONTEXT_WINDOWS[name] = int(context_window)
        if is_active:
            # "표준" tier에 매핑된 모델을 먼저(일반 업무 기본값으로 가장 알맞다).
            ranked.append((0 if "standard" in json.loads(tiers_raw or "[]") else 1, name, provider))
    for _, name, provider in sorted(ranked):
        _ACTIVE_MODELS.setdefault(provider, []).append(name)
    try:
        row = conn.execute(
            "SELECT provider, model FROM platform_llm_config WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None and row[0] and row[1]:
        _PLATFORM_DEFAULT.update(provider=row[0], model=row[1])


def context_window_for(model: str) -> int | None:
    """admin이 모델 레지스트리에 등록한 컨텍스트 한도(토큰). 없으면 None."""
    return _CONTEXT_WINDOWS.get(model)


def default_model_for(provider: str) -> str | None:
    """모델명이 비었을 때의 기본 모델 — 플랫폼 기본 LLM(같은 provider일 때) →
    레지스트리 활성 모델. 둘 다 없으면 None(호출부가 마지막 고정값으로 폴백)."""
    if _PLATFORM_DEFAULT.get("provider") == provider:
        return _PLATFORM_DEFAULT["model"]
    models = _ACTIVE_MODELS.get(provider)
    return models[0] if models else None


def set_cached(provider: str, name: str, options: dict) -> None:
    if options:
        _CACHE[(provider, name)] = dict(options)
    else:
        _CACHE.pop((provider, name), None)


def for_model(provider: str, model: str) -> dict:
    """그 provider가 받는 키만 남긴 옵션(없으면 빈 dict)."""
    options = _CACHE.get((provider, model)) or {}
    return {k: v for k, v in options.items() if k in _PROVIDER_KEYS.get(provider, ())}


# provider별 temperature 허용 상한 — Anthropic Messages API는 0~1만 받는다(초과 시
# 400). 에이전트 튜닝 UI는 0~2를 허용하므로(OpenAI·Ollama 기준) 여기서 맞춘다.
_TEMPERATURE_MAX = {"anthropic": 1.0, "openai": 2.0, "ollama": 2.0}


def request_options(provider: str, model: str, temperature: float | None = None) -> dict:
    """실제 호출에 실을 생성 옵션 — 모델 레지스트리 값(모델 공통) 위에 에이전트
    튜닝 값(에이전트별, 현재 temperature)을 덮는다. 우선순위: 에이전트 > 모델 >
    provider 기본값(아무것도 안 보냄)."""
    options = for_model(provider, model)
    if temperature is not None:
        options["temperature"] = max(0.0, min(float(temperature), _TEMPERATURE_MAX.get(provider, 2.0)))
    return options
