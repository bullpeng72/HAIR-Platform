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

from hair_platform import audit, pool_registry

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


def get_platform_default_llm(conn: sqlite3.Connection) -> dict | None:
    """admin이 설정해둔 플랫폼 공통 기본 provider/모델 — 한 번도 설정한
    적 없으면(행이 없으면) `None`(호출부가 환경변수/자동판별로 폴백한다).
    `vectorstore.get_embedding_config()`와 같은 패턴."""
    row = conn.execute(
        "SELECT provider, model FROM platform_llm_config WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return {"provider": row["provider"], "model": row["model"]}


def set_platform_default_llm(
    conn: sqlite3.Connection, actor_id: int, provider: str, model: str
) -> None:
    """사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본
    동작을 위한 LLM을 지정할 수 있게") — `vectorstore.set_embedding_
    config()`와 같은 `id=1` 싱글턴 upsert + 감사로그 패턴.

    사용자 요청(2026-11-02, "모델 레지스트리에 등록된 모델에 한해
    선택할 수 있도록 개선") — 자유 텍스트 모델명을 더는 안 받는다.
    `model_registry`에 등록+활성화된 (provider, model) 조합만 허용한다
    (`agent_optimizer.refine_with_llm()`이 LLM 응답을 활성 레지스트리와
    대조 검증하는 것과 같은 원칙 — 여긴 LLM 환각 대신 사람의 직접
    오타/무효 입력을 막는다). `model_registry` 테이블을 직접 SELECT
    할 뿐 `model_registry.py`를 import하진 않는다(순환 import 방지
    목적은 아니고 — 그 모듈 함수들은 전부 `audit.record()`까지 겸하는
    무거운 API라, 여기선 단순 존재 확인만 필요하기 때문)."""
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"unsupported_provider:{provider!r}")
    model = (model or "").strip()
    if not model:
        raise ValueError("empty_model")
    registered = conn.execute(
        "SELECT 1 FROM model_registry WHERE provider = ? AND name = ? AND is_active = 1",
        (provider, model),
    ).fetchone()
    if registered is None:
        raise ValueError(f"model_not_registered:{provider}:{model}")
    before = get_platform_default_llm(conn)
    conn.execute(
        "INSERT INTO platform_llm_config (id, provider, model) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET provider = excluded.provider, "
        "model = excluded.model",
        (provider, model),
    )
    audit.record(
        conn,
        actor_id,
        "platform_llm_config_updated",
        "platform_llm_config",
        1,
        before=before,
        after={"provider": provider, "model": model},
    )
    conn.commit()


def resolve_provider_and_model(
    provider: str | None,
    model: str | None,
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, str]:
    """provider/model이 명시되지 않으면 (conn이 주어졌을 때) admin이
    설정해둔 플랫폼 공통 기본값 → 환경변수 → API 키 존재 여부 순으로
    판별한다.

    사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본 동작을
    위한 LLM을 지정할 수 있게") — `conn`은 키워드 전용 선택 인자라
    기존 호출부(전부 위치 인자 2개만 씀)는 전혀 안 바뀐다. "공통/기본
    동작"에 해당하는 호출부(`agent_optimizer.refine_with_llm()`,
    `conversations.py`의 "기본 AI"/요약)만 의도적으로 `conn=conn`을
    넘겨 이 폴백을 받는다 — Agent 실행(crew_runtime/agent_runtime/
    deliverables/jobs)은 Agent별 설정 우선순위를 그대로 지키기 위해
    건드리지 않는다."""
    if provider is None and conn is not None:
        configured = get_platform_default_llm(conn)
        if configured is not None:
            provider = configured["provider"]
            model = model or configured["model"]
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


def call_llm(
    provider: str,
    model: str,
    system: str,
    prompt: str,
    *,
    timeout_seconds: float | None = None,
) -> str:
    """실제 API 호출 지점 — 여기서만 비용/로컬 연산이 발생한다.

    `timeout_seconds`를 생략하면(기존 모든 호출부가 그렇다) provider별
    기본값(클라우드 900초/Ollama 1800초 — Ollama가 더 길다)이 그대로
    적용된다 — 하위 호환.
    `agent_runtime.execute_agent()`가 deep_reasoning tier Agent에는 더 긴
    값을 넘겨 쓴다(아래 `_call_anthropic`/`_call_openai` 참고)."""
    if provider == "anthropic":
        return _call_anthropic(model, system, prompt, timeout_seconds)
    if provider == "openai":
        return _call_openai(model, system, prompt, timeout_seconds)
    if provider == "ollama":
        return _call_ollama(model, system, prompt, timeout_seconds)
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

# 사용자 결정(2026-11-12, "전체적으로 타임아웃을 클라우드보다 Ollama가
# 더 길게, 작업 성격에 따라 차등하되 대화 및 업무에 같은 기준이
# 적용되도록") — 지금까지 Ollama는 단발 호출 기본값이 600초로,
# 클라우드(900초)보다 오히려 더 짧았다(원칙과 반대). 로컬 연산이라
# API 비용 걱정이 없고(README에 이미 기록된 실측 — 27B급 모델도
# 대화가 길어지면 600초를 넘기는 사례), 더 기다려도 손해볼 게 없으므로
# 클라우드보다 더 넉넉하게 잡는다. 다른 모듈의 "작업 성격별" 상위
# 타임아웃(`agent_runtime._DEEP_REASONING_LLM_TIMEOUT_SECONDS[_OLLAMA]`,
# `crew_runtime.CREWAI_LLM_TIMEOUT_SECONDS[_OLLAMA]`)도 전부 이
# "Ollama > 클라우드" 원칙을 따르도록 같이 맞췄다 — 이 상수는 그중
# "표준"(단발 호출, 별도 override 없음) 등급의 Ollama 값이다. 공개
# 이름으로 둬 `conversations.py`가 타임아웃 실패 메시지에 분을
# 정확히 표시할 때도 같은 값을 참조한다(이름을 아는 모듈이 둘로
# 늘어도 값의 정본은 여기 하나뿐).
OLLAMA_LLM_TIMEOUT_SECONDS = 1800.0


def _call_anthropic(
    model: str, system: str, prompt: str, timeout_seconds: float | None = None
) -> str:
    import anthropic  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = anthropic.Anthropic(
        timeout=timeout_seconds or _CLOUD_LLM_TIMEOUT_SECONDS
    )  # ANTHROPIC_API_KEY 환경변수를 사용한다.
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai(
    model: str, system: str, prompt: str, timeout_seconds: float | None = None
) -> str:
    import openai  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = openai.OpenAI(
        timeout=timeout_seconds or _CLOUD_LLM_TIMEOUT_SECONDS
    )  # OPENAI_API_KEY 환경변수를 사용한다.
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_ollama(
    model: str, system: str, prompt: str, timeout_seconds: float | None = None
) -> str:
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
        # `eval_duration`). 사용자 결정으로 600초로 다시 늘렸었고,
        # 그 뒤로도 재발(대화가 길어진 상태에서 27B급 모델이 600초를
        # 또 넘김)해 "클라우드보다 Ollama가 더 길어야 한다"는 원칙으로
        # `OLLAMA_LLM_TIMEOUT_SECONDS`(1800초)로 다시 올렸다 — 프런트
        # 폴링 포기 시한도 그보다 여유 있게 길게 유지한다(`webui.py`의
        # `pollConversationJob()`/`pollExecutionJob()` 참고).
        timeout=timeout_seconds or OLLAMA_LLM_TIMEOUT_SECONDS,
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
