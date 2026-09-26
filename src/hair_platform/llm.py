"""실제 LLM 연동 — Anthropic·OpenAI 중 선택 가능(사용자 요청, 2026-09-26).

이전까지 F4(Agent 생성)는 `jobs.default_stub_generator`(역할명·스킬을 그대로
되돌려주는 규칙 기반 함수)만 썼다 — `grep -rniE "anthropic|openai|llm"
src/`로 확인한 결과 실행 코드에 LLM 호출이 전무했다(ADR 결정 2: "결정론적,
LLM 미사용"으로 의도적 설계). 이 모듈은 그 한계를 실제로 메운다.

**비용 발생 지점**: `call_llm()`을 호출하면 실제 Anthropic/OpenAI API가
호출되어 비용이 발생한다 — 이 모듈을 import/정의하는 것 자체는 비용이 없다
(지연 import + 함수 호출 시점에만 SDK를 가져온다). 항상 `use_llm=True`를
명시적으로 요청했을 때만 호출 경로를 타도록 상위(`app.py`)에서 게이팅한다
(Gate A-G 배선 때와 같은 원칙 — 기본값은 무료 스텁 유지).

프로바이더/모델은 요청 파라미터로 명시하거나, 없으면 환경변수
(`HAIR_PLATFORM_LLM_PROVIDER`, `HAIR_PLATFORM_LLM_MODEL`)로, 그것도 없으면
설정된 API 키로 자동 판별한다(Anthropic 우선).
"""

from __future__ import annotations

import os

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
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
            f"지원하지 않는 provider입니다: {provider!r} (anthropic 또는 openai만 가능)"
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
        "LLM 연동을 쓰려면 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 환경변수가 "
        "필요합니다(둘 다 없으면 provider를 자동 판별할 수 없습니다)."
    )


def call_llm(provider: str, model: str, system: str, prompt: str) -> str:
    """실제 API 호출 지점 — 여기서만 비용이 발생한다."""
    if provider == "anthropic":
        return _call_anthropic(model, system, prompt)
    if provider == "openai":
        return _call_openai(model, system, prompt)
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


def system_prompt_for_role(role_name: str, skills: list[str]) -> str:
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    return (
        f"당신은 소프트웨어 개발팀에서 '{role_name}' 역할을 맡은 AI 에이전트입니다. "
        f"보유 역량: {skill_text}. 이 역할의 관점에서 실무에 바로 쓸 수 있는 "
        "구체적이고 실질적인 산출물을 작성하세요. 불필요한 설명 없이 산출물 자체에 집중하세요."
    )
