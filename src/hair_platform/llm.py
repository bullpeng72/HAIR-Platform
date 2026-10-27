"""실제 LLM 연동 — Anthropic·OpenAI·Ollama 중 선택 가능(사용자 요청, 2026-09-26,
Ollama는 2차 요청으로 추가).

이전까지 Agent 생성은 `jobs.default_stub_generator`(역할명·스킬을 그대로
되돌려주는 규칙 기반 함수)만 썼다 — `grep -rniE "anthropic|openai|llm"
src/`로 확인한 결과 실행 코드에 LLM 호출이 전무했다("결정론적,
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
import sqlite3

from hair_platform import pool_registry

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


# 사용자 보고(2026-10-18, "@자율 개발 크루" 품질 분석 중 실측) — QA
# 기능테스터/품질 관리자처럼 긴 대화 맥락을 받아 표가 많은 긴 응답을
# 만드는 Agent가 OpenAI SDK **기본** 타임아웃(600초)에 걸려 "timed out"
# 으로 조용히 실패했다. 이 파일엔 Ollama용 `timeout=600`만 명시돼 있고
# OpenAI/Anthropic 호출에는 타임아웃을 전혀 지정하지 않아 SDK 기본값에
# 그대로 노출돼 있었다 — 아래처럼 명시적으로 넉넉하게(900초) 잡아
# 같은 급으로 느린 정상 응답을 더 받아줄 여지를 둔다(Ollama 타임아웃을
# 120→300→600초로 올린 것과 같은 원칙 — "멈춘 게 아니라 느린 것"일
# 때까지는 끊지 않는다).
_CLOUD_LLM_TIMEOUT_SECONDS = 900.0


def _call_anthropic(model: str, system: str, prompt: str) -> str:
    import anthropic  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = anthropic.Anthropic(
        timeout=_CLOUD_LLM_TIMEOUT_SECONDS
    )  # ANTHROPIC_API_KEY 환경변수를 사용한다.
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai(model: str, system: str, prompt: str) -> str:
    import openai  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = openai.OpenAI(
        timeout=_CLOUD_LLM_TIMEOUT_SECONDS
    )  # OPENAI_API_KEY 환경변수를 사용한다.
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
        # 사용자 보고(2026-10-14, "@개발자... -> 일부 응답이 실패했습니다:
        # timed out") — 실측으로 원인 확인: `conversation_jobs`에 정확히
        # 120.0초만에 "timed out"으로 기록돼 있었다(이전 값과 정확히
        # 일치). 로컬 대형 모델(실측 사례: 35B)은 120초를 넘기기 쉽고,
        # `reasoning=True` 튜닝을 쓰면 답변 전 계획 수립용 LLM 호출이
        # 하나 더 끼어 더 오래 걸린다 — 클라우드 provider(Anthropic/
        # OpenAI)는 SDK 기본 타임아웃이 이미 넉넉해(수백 초) 이 문제가
        # 없다. 1차로 300초로 늘렸는데, 재발 보고 후 타임아웃 없이 직접
        # 측정해보니(2026-10-14, 2차) 같은 Agent(ornith-1.5:35b, 입력
        # 7,959토큰·출력 7,729토큰짜리 긴 보고서)가 실제로는 337.7초
        # 만에 **정상 완료**됐다 — 멈춘 게 아니라 초당 ~23토큰의 순수
        # 생성 속도가 느린 것뿐이다(`total_duration`의 99.96%가
        # `eval_duration`). 사용자 결정으로 600초로 다시 늘리고,
        # 프런트엔드 `pollConversationJob()`의 폴링 포기 시한도 700초로
        # 같이 올렸다(아래 timeout보다 여유 있게 길게 유지 — 백엔드가
        # 결론을 내기 전에 프런트가 먼저 포기하지 않도록).
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def system_prompt_for_role(
    conn: sqlite3.Connection, role_name: str, skills: list[str]
) -> str:
    """`conn`(2026-10-13, "Harness Methodology 기준 개발 단계별 에이전트
    생성... skill을 적극 활용") — 전까지는 `skills`의 스킬 "이름"만
    나열해 전달했다. `skill_pool.description`(예: "코드를 짜기 전에
    Spec·실패 모드 카탈로그·최소 골든셋을 만든다")은 어디에도 전달되지
    않아, 절차형 스킬을 Agent에 달아둬도 실행에 아무 영향이 없었다
    (`pool_registry.skill_knowledge_text()`가 이제 그 설명을 실제로
    끌어와 붙인다 — `deliverables.skill_grounded_system_prompt()`가
    쓰던 것과 같은 조회)."""
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    base = (
        f"당신은 소프트웨어 개발팀에서 '{role_name}' 역할을 맡은 AI 에이전트입니다. "
        f"보유 역량: {skill_text}. 이 역할의 관점에서 실무에 바로 쓸 수 있는 "
        "구체적이고 실질적인 산출물을 작성하세요. 불필요한 설명 없이 산출물 자체에 집중하세요."
    )
    knowledge = pool_registry.skill_knowledge_text(conn, skills)
    if not knowledge:
        return base
    return (
        f"{base}\n\n참고 지식({pool_registry.SKILL_KNOWLEDGE_GUIDANCE})\n{knowledge}"
        f"\n\n{pool_registry.NO_TOOL_LOOP_GUARD}"
    )


def system_prompt_for_human_assist(role_name: str, skills: list[str]) -> str:
    """협업 워크스페이스 "LLM으로 도와줘"(사용자 요청, 2026-09-28) 전용 —
    `system_prompt_for_role()`과 달리 LLM이 Agent 역할을 연기하는 게 아니라
    그 역할을 맡은 **사람**의 초안 작성을 돕는다는 걸 명시한다(그대로
    재사용하면 "당신은 AI 에이전트입니다"라는 문장이 사람의 업무 보조
    맥락과 안 맞는다)."""
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    return (
        f"당신은 '{role_name}' 역할을 맡은 사람의 업무를 돕는 보조자입니다. "
        f"관련 역량: {skill_text}. 사람이 검토·수정할 초안을 작성하세요 — "
        "최종 결정과 책임은 사람에게 있습니다. 불필요한 설명 없이 산출물 자체에 집중하세요."
    )
