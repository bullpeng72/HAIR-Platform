"""실제 LLM 연동 — Anthropic·OpenAI·Ollama 중 선택 가능(사용자 요청, 2026-09-26,
Ollama는 2차 요청으로 추가).

이전까지 F4(Agent 생성)는 `jobs.default_stub_generator`(역할명·스킬을 그대로
되돌려주는 규칙 기반 함수)만 썼다 — `grep -rniE "anthropic|openai|llm"
src/`로 확인한 결과 실행 코드에 LLM 호출이 전무했다(ADR 결정 2: "결정론적,
LLM 미사용"으로 의도적 설계). 이 모듈은 그 한계를 실제로 메운다.

**비용 발생 지점**: `call_llm()`을 호출하면 실제 API가 호출된다(Anthropic/
OpenAI는 과금, Ollama는 로컬 실행이라 API 비용은 없지만 로컬 컴퓨팅 자원을
쓴다) — 이 모듈을 import/정의하는 것 자체는 비용이 없다(지연 import + 함수
호출 시점에만 SDK를 가져온다). 항상 `use_llm=True`를 명시적으로 요청했을
때만 호출 경로를 타도록 상위(`app.py`)에서 게이팅한다(Gate A-G 배선 때와
같은 원칙 — 기본값은 무료 스텁 유지).

프로바이더/모델은 요청 파라미터로 명시하거나, 없으면 환경변수
(`HAIR_PLATFORM_LLM_PROVIDER`, `HAIR_PLATFORM_LLM_MODEL`)로, 그것도 없으면
설정된 API 키로 자동 판별한다(Anthropic 우선). Ollama는 API 키 개념이 없어
자동 판별 대상이 아니다 — 명시적으로 지정해야 한다(요청 파라미터, 환경변수,
또는 Agent별로 저장된 모델 설정을 통해 — `agent_runtime.py` 참고).
"""

from __future__ import annotations

import os

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


def resolve_provider_and_model(
    provider: str | None, model: str | None
) -> tuple[str, str]:
    """provider/model이 명시되지 않으면 환경변수 → API 키 존재 여부 순으로 판별한다."""
    provider = (
        provider or os.environ.get("HAIR_PLATFORM_LLM_PROVIDER") or _auto_detect()
    )
    if provider not in DEFAULT_MODELS:
        raise ValueError(
            f"지원하지 않는 provider입니다: {provider!r} "
            f"({', '.join(DEFAULT_MODELS)} 중 하나만 가능)"
        )
    model = (
        model or os.environ.get("HAIR_PLATFORM_LLM_MODEL") or DEFAULT_MODELS[provider]
    )
    return provider, model


def _auto_detect() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "LLM 연동을 쓰려면 provider를 명시하거나 ANTHROPIC_API_KEY 또는 "
        "OPENAI_API_KEY 환경변수가 필요합니다(Ollama는 API 키가 없어 자동 "
        "판별 대상이 아닙니다 — provider='ollama'를 직접 지정하세요)."
    )


def call_llm(provider: str, model: str, system: str, prompt: str) -> str:
    """실제 API 호출 지점 — 여기서만 비용/로컬 연산이 발생한다."""
    if provider == "anthropic":
        return _call_anthropic(model, system, prompt)
    if provider == "openai":
        return _call_openai(model, system, prompt)
    if provider == "ollama":
        return _call_ollama(model, system, prompt)
    raise ValueError(f"지원하지 않는 provider입니다: {provider!r}")


def _call_anthropic(model: str, system: str, prompt: str) -> str:
    import anthropic  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수를 사용한다.
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai(model: str, system: str, prompt: str) -> str:
    import openai  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = openai.OpenAI()  # OPENAI_API_KEY 환경변수를 사용한다.
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_ollama(model: str, system: str, prompt: str) -> str:
    import httpx  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    resp = httpx.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def system_prompt_for_role(role_name: str, skills: list[str]) -> str:
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    return (
        f"당신은 소프트웨어 개발팀에서 '{role_name}' 역할을 맡은 AI 에이전트입니다. "
        f"보유 역량: {skill_text}. 이 역할의 관점에서 실무에 바로 쓸 수 있는 "
        "구체적이고 실질적인 산출물을 작성하세요. 불필요한 설명 없이 산출물 자체에 집중하세요."
    )
