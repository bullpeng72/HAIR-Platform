"""모델별 생성 옵션(샘플링) — 모델 레지스트리 값을 provider 호출 인자로 바꾼다.

사용자 요청(2026-11-21, "모델 레지스트리에서 ollama, openai, anthropic 등 다양한
provider의 모델을 상황에 따라 바꿔 사용하도록 되어 있어. 특정 모델만을 위한
하드코딩은 없도록 해줘") — 직전 개선에서 Qwen 계열 권장값(top_k=20,
top_p=0.8)을 **모든 Ollama 모델**에 기본으로 걸었던 것을 걷어 내고, 모델마다
admin이 모델 레지스트리(`model_registry.sampling_options`)에 적는 값으로 바꾼다.
비어 있으면 아무것도 보내지 않는다(각 모델의 기본값).

provider별 변환:
- ollama: `options`(top_k, top_p, num_predict ← max_output_tokens, 미등록 시 기본 한도)
- anthropic: Messages API 인자(top_k, top_p)
- openai: Chat Completions 인자(top_p, max_completion_tokens — top_k는 지원하지 않는다)
- CrewAI(litellm): 위와 같은 규칙의 키워드 인자

`llm.py`(호출 지점)는 DB 연결 없이 불리므로, 값은 기동 시(`db.init_schema`)와
변경 시(`model_registry.set_sampling_options`)에 이 모듈의 캐시로 올린다.
의존성이 없는 모듈이라 `llm`·`crew_runtime`·`model_registry` 어디서 import해도
순환이 생기지 않는다.
"""

from __future__ import annotations

import json
import os
import sqlite3

# 키 → (타입, 하한, 상한)
ALLOWED_KEYS: dict[str, tuple[type, float, float]] = {
    "top_k": (int, 1, 1000),
    "top_p": (float, 0.0, 1.0),
    # 한 번 응답의 최대 출력 토큰(2026-11-21, "모든 파이프라인 점검") — 로컬 모델이 반복
    # 루프에 빠지면 Ollama는 기본값(-1, 무제한)이라 호출 시간 제한(90분)까지 생성을 멈추지
    # 않았다(실측: 사업/기획 파이프라인 2단계가 25분 넘게 한 응답을 생성하며 멈춤).
    # 사고 토큰도 이 한도에 포함된다.
    "max_output_tokens": (int, 256, 1_000_000),
}
_PROVIDER_KEYS = {
    "ollama": ("top_k", "top_p", "max_output_tokens"),
    # Anthropic은 `llm._ANTHROPIC_MAX_OUTPUT_TOKENS`(API 필수 인자)가 따로 있다.
    "anthropic": ("top_k", "top_p"),
    "openai": ("top_p", "max_output_tokens"),
}
# 레지스트리에 값이 없을 때의 provider 기본 출력 한도 — Ollama만 둔다(기본이 무제한이라).
# 모델과 무관한 안전장치이고, 모델별 값은 레지스트리 생성 옵션으로 덮는다.
# 환경변수 HAIR_OLLAMA_MAX_OUTPUT_TOKENS로 바꿀 수 있다.
# 16384 → 32768(2026-11-22 실측): 사고(thinking) 토큰도 이 한도에 들어가, 사고를 길게 하는 모델은
# 사업/기획 덱이 4천 자에서 표 칸 중간에 잘렸다(한도 없던 첫 실행은 5만 자). 32768이면 폭주해도
# 로컬 생성 15분 안팎에서 멈추고, 긴 산출물(덱·단일 파일 앱)은 끝까지 쓸 수 있다.
DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS = 32768
# provider별 출력 한도 인자 이름 — 직접 호출(llm.py)과 CrewAI(litellm) 경로.
_OUTPUT_CAP_ARG = {"ollama": "num_predict", "openai": "max_completion_tokens"}
_CREWAI_OUTPUT_CAP_ARG = "max_tokens"  # litellm이 provider별 이름(ollama: num_predict)으로 바꾼다.

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
        if key == "max_output_tokens" and not (low <= value <= high):
            raise ValueError("max_output_tokens_out_of_range")
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


def platform_default() -> tuple[str, str] | None:
    """admin이 정한 플랫폼 기본 LLM(provider, model) — 없으면 None."""
    if _PLATFORM_DEFAULT.get("provider") and _PLATFORM_DEFAULT.get("model"):
        return _PLATFORM_DEFAULT["provider"], _PLATFORM_DEFAULT["model"]
    return None


def first_active_model() -> tuple[str, str] | None:
    """레지스트리 활성 모델 중 첫 번째('표준' tier 매핑 우선) — 없으면 None."""
    for provider, models in _ACTIVE_MODELS.items():
        if models:
            return provider, models[0]
    return None


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


def _registered(provider: str, model: str) -> dict:
    """그 provider가 받는 키만 남긴 레지스트리 값(없으면 빈 dict)."""
    options = _CACHE.get((provider, model)) or {}
    return {k: v for k, v in options.items() if k in _PROVIDER_KEYS.get(provider, ())}


def max_output_tokens(provider: str, model: str) -> int | None:
    """이 모델 호출의 출력 한도 — 레지스트리 값 > provider 기본값(Ollama만) > None."""
    registered = _registered(provider, model).get("max_output_tokens")
    if registered is not None:
        return int(registered)
    if provider == "ollama":
        env = os.environ.get("HAIR_OLLAMA_MAX_OUTPUT_TOKENS")
        if env and env.strip().lstrip("-").isdigit():
            value = int(env)
            return value if value > 0 else None  # 0·음수 = 한도 없음(예전 동작)
        return DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS
    return None


def ollama_context_length(provider: str, model: str) -> int | None:
    """Ollama 컨텍스트 창(`num_ctx`) — admin이 모델 레지스트리에 적은 컨텍스트 한도(2026-11-22,
    컨텍스트 창 A안). 예전엔 이 값을 프롬프트 자르기에만 쓰고 Ollama에는 보내지 않아, Ollama가
    자기 기본값(이 환경 32,768)으로 돌았다 — 모델은 262,144까지 지원하는데 릴레이 마지막 Agent가
    앞 산출물 + 사고로 창을 다 채워 덱이 잘렸다(사업/기획 실측). 등록값이 없으면 보내지 않는다."""
    if provider != "ollama":
        return None
    return context_window_for(model)


def for_model(provider: str, model: str) -> dict:
    """CrewAI(litellm) `LLM` 키워드 인자 — 샘플링 값 + 출력 한도(`max_tokens`) + Ollama `num_ctx`."""
    options = {k: v for k, v in _registered(provider, model).items() if k != "max_output_tokens"}
    cap = max_output_tokens(provider, model)
    if cap is not None:
        options[_CREWAI_OUTPUT_CAP_ARG] = cap
    num_ctx = ollama_context_length(provider, model)
    if num_ctx:
        options["num_ctx"] = num_ctx
    return options


# provider별 temperature 허용 상한 — Anthropic Messages API는 0~1만 받는다(초과 시
# 400). 에이전트 튜닝 UI는 0~2를 허용하므로(OpenAI·Ollama 기준) 여기서 맞춘다.
_TEMPERATURE_MAX = {"anthropic": 1.0, "openai": 2.0, "ollama": 2.0}


def request_options(provider: str, model: str, temperature: float | None = None) -> dict:
    """실제 호출에 실을 생성 옵션 — 모델 레지스트리 값(모델 공통) 위에 에이전트
    튜닝 값(에이전트별, 현재 temperature)을 덮는다. 우선순위: 에이전트 > 모델 >
    provider 기본값(아무것도 안 보냄)."""
    options = {k: v for k, v in _registered(provider, model).items() if k != "max_output_tokens"}
    cap = max_output_tokens(provider, model)
    if cap is not None and provider in _OUTPUT_CAP_ARG:
        options[_OUTPUT_CAP_ARG[provider]] = cap
    num_ctx = ollama_context_length(provider, model)
    if num_ctx:
        options["num_ctx"] = num_ctx
    if temperature is not None:
        options["temperature"] = max(0.0, min(float(temperature), _TEMPERATURE_MAX.get(provider, 2.0)))
    return options
