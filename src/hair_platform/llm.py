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

from hair_platform import audit, model_options, tool_calling

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
    model_options.refresh(conn)  # 모델명 없는 호출의 기본 모델이 바로 따라오게


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
    # 모델명이 비었을 때(2026-11-21, 하드코딩 점검 1번): 환경변수 → 플랫폼 기본 LLM
    # (같은 provider) → 모델 레지스트리 활성 모델 → 마지막으로 `DEFAULT_MODELS` 고정값
    # (그 provider 모델이 레지스트리에 하나도 없을 때만 — 예: 레지스트리를 아직 안 채운
    # 새 설치). 예전엔 바로 고정값이 들어가 설치되지 않은 'llama3.1'로 404가 났다.
    model = (
        model
        or os.environ.get("HAIR_PLATFORM_LLM_MODEL")
        or model_options.default_model_for(provider)
        or DEFAULT_MODELS[provider]
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
    temperature: float | None = None,
) -> str:
    """실제 API 호출 지점 — 여기서만 비용/로컬 연산이 발생한다.

    `timeout_seconds`를 생략하면(기존 모든 호출부가 그렇다) provider별
    기본값(클라우드 900초/Ollama 1800초 — Ollama가 더 길다)이 그대로
    적용된다 — 하위 호환.
    `agent_runtime.execute_agent()`가 deep_reasoning tier Agent에는 더 긴
    값을 넘겨 쓴다(아래 `_call_anthropic`/`_call_openai` 참고)."""
    # `temperature`(2026-11-21) — 에이전트 파인튜닝 값. 지정됐을 때만 넘겨
    # 기존 호출 모양(테스트 대역 포함)을 그대로 둔다.
    extra = {"temperature": temperature} if temperature is not None else {}
    if provider == "anthropic":
        return _call_anthropic(model, system, prompt, timeout_seconds, **extra)
    if provider == "openai":
        return _call_openai(model, system, prompt, timeout_seconds, **extra)
    if provider == "ollama":
        return _call_ollama(model, system, prompt, timeout_seconds, **extra)
    raise ValueError(f"지원하지 않는 provider입니다: {provider!r}")


def call_llm_with_tools(
    provider: str,
    model: str,
    system: str,
    prompt: str,
    tools: list,
    *,
    timeout_seconds: float | None = None,
    max_iterations: int = tool_calling.MAX_TOOL_CALL_ITERATIONS,
    temperature: float | None = None,
) -> str:
    """사용자 요청(2026-11-18, "...Skill, tool 사용 제약을 없애고...
    엔터프라이즈 환경 적용이 가능한 수준의 확실한 개선 방안을 도출해줘"
    개선축 A, 적용 범위 사용자 확인 "Ollama 포함 전체 provider 적용") —
    CrewAI의 Agent/Task/Tool 루프 없이, provider 3종이 각자 지원하는
    네이티브 함수 호출(tool-calling) API로 같은 역량을 준다. `tools`는
    `tool_calling.resolve_callable_tools()`가 돌려준 CrewAI Tool
    객체 목록(실행은 `.run(**kwargs)`를 그대로 재사용 — 중복 구현 없음).

    `tools`가 비어 있으면 도구 호출 라운드트립 자체를 돌지 않고 바로
    `call_llm()`과 동일하게 1턴만 호출한다(불필요한 복잡도 없음,
    호출부가 매번 "도구가 있는지" 분기할 필요가 없도록 이 함수 안에서
    처리)."""
    extra = {"temperature": temperature} if temperature is not None else {}
    if not tools:
        return call_llm(provider, model, system, prompt, timeout_seconds=timeout_seconds, **extra)
    if provider == "anthropic":
        return _call_anthropic_with_tools(
            model, system, prompt, tools, timeout_seconds, max_iterations, **extra
        )
    if provider == "openai":
        return _call_openai_with_tools(
            model, system, prompt, tools, timeout_seconds, max_iterations, **extra
        )
    if provider == "ollama":
        return _call_ollama_with_tools(
            model, system, prompt, tools, timeout_seconds, max_iterations, **extra
        )
    raise ValueError(f"지원하지 않는 provider입니다: {provider!r}")


_TOOL_CALL_LIMIT_EXCEEDED_MESSAGE = (
    "도구 호출을 반복하다 최대 횟수에 도달해 중단했습니다 — 지금까지의 "
    "도구 호출 결과를 참고해 이어서 직접 답변해주세요."
)

# 반복 상한에 걸렸을 때의 마무리(2026-11-21) — 예전엔 위 안내문을 **결과물로 그대로**
# 돌려줬다(실측: 프레젠테이션 Agent가 차트 도구를 13번 부르다 상한에 걸려, 발표자료
# 대신 이 한 줄이 저장됐다). 이제 도구를 끈 채 한 번 더 불러 지금까지의 도구 결과로
# 최종 답을 받는다. 그 호출마저 실패·빈 응답일 때만 안내문을 돌려준다.
_FINAL_ANSWER_INSTRUCTION = (
    "도구 호출을 더 하지 마세요. 지금까지의 도구 결과와 대화 내용만으로 처음 요청에 대한 "
    "최종 결과물을 완성해 출력하세요."
)


def _call_anthropic_with_tools(
    model: str,
    system: str,
    prompt: str,
    tools: list,
    timeout_seconds: float | None,
    max_iterations: int,
    temperature: float | None = None,
) -> str:
    import anthropic

    client = anthropic.Anthropic(timeout=timeout_seconds or _CLOUD_LLM_TIMEOUT_SECONDS)
    tool_schemas = [tool_calling.tool_schema_anthropic(t) for t in tools]
    tools_by_name = {t.name: t for t in tools}
    messages: list = [{"role": "user", "content": prompt}]
    for _ in range(max_iterations):
        resp = client.messages.create(
            model=model,
            max_tokens=_ANTHROPIC_MAX_OUTPUT_TOKENS,
            system=system,
            messages=messages,
            tools=tool_schemas,
            **model_options.request_options("anthropic", model, temperature),
        )
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = tool_calling.run_tool(tools_by_name, block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )
        messages.append({"role": "user", "content": tool_results})
    # 상한 도달 — 도구를 끄고(tool_choice none) 최종 답을 받는다.
    messages[-1]["content"].append({"type": "text", "text": _FINAL_ANSWER_INSTRUCTION})
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=_ANTHROPIC_MAX_OUTPUT_TOKENS,
            system=system,
            messages=messages,
            tools=tool_schemas,
            tool_choice={"type": "none"},
            **model_options.request_options("anthropic", model, temperature),
        )
        final = "".join(b.text for b in resp.content if b.type == "text")
    except Exception:  # noqa: BLE001 — 마무리 실패 시 기존 안내문으로
        final = ""
    return final or _TOOL_CALL_LIMIT_EXCEEDED_MESSAGE


def _call_openai_with_tools(
    model: str,
    system: str,
    prompt: str,
    tools: list,
    timeout_seconds: float | None,
    max_iterations: int,
    temperature: float | None = None,
) -> str:
    import json

    import openai

    client = openai.OpenAI(timeout=timeout_seconds or _CLOUD_LLM_TIMEOUT_SECONDS)
    tool_schemas = [tool_calling.tool_schema_openai(t) for t in tools]
    tools_by_name = {t.name: t for t in tools}
    messages: list = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    for _ in range(max_iterations):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas,
            **model_options.request_options("openai", model, temperature),
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append(msg.model_dump(exclude_unset=True))
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                # 모델이 인자를 유효한 JSON으로 못 만든 경우 — 도구
                # 실행을 아예 건너뛰지 않고, 그 사실을 결과로 돌려줘
                # 모델이 다음 턴에 스스로 고쳐 재시도할 기회를 준다.
                args = None
            result = (
                tool_calling.run_tool(tools_by_name, tc.function.name, args)
                if args is not None
                else "도구 호출 인자가 올바른 JSON이 아닙니다 — 다시 시도하세요."
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
    # 상한 도달 — 도구를 끄고(tool_choice none) 최종 답을 받는다.
    messages.append({"role": "user", "content": _FINAL_ANSWER_INSTRUCTION})
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas,
            tool_choice="none",
            **model_options.request_options("openai", model, temperature),
        )
        final = resp.choices[0].message.content or ""
    except Exception:  # noqa: BLE001 — 마무리 실패 시 기존 안내문으로
        final = ""
    return final or _TOOL_CALL_LIMIT_EXCEEDED_MESSAGE


def _call_ollama_with_tools(
    model: str,
    system: str,
    prompt: str,
    tools: list,
    timeout_seconds: float | None,
    max_iterations: int,
    temperature: float | None = None,
) -> str:
    """Ollama는 OpenAI 호환 `tools` 스키마를 `/api/chat`이 받는다(설치된
    모델이 함수 호출을 지원해야 함 — 지원 안 하면 `tool_calls` 없이
    그냥 일반 답변만 오므로, 아래 로직이 자연스럽게 1턴 응답으로
    끝난다 — 별도 감지 코드가 필요 없다)."""
    import httpx

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    tool_schemas = [tool_calling.tool_schema_openai(t) for t in tools]
    tools_by_name = {t.name: t for t in tools}
    messages: list = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    for _ in range(max_iterations):
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": tool_schemas,
                "stream": False,
                "options": model_options.request_options("ollama", model, temperature),
            },
            timeout=timeout_seconds or OLLAMA_LLM_TIMEOUT_SECONDS,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_ollama_error_with_detail(resp, exc)
        data = resp.json()
        msg = data["message"]
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content", "")
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments")
            args = raw_args if isinstance(raw_args, dict) else {}
            result = tool_calling.run_tool(tools_by_name, fn.get("name", ""), args)
            messages.append({"role": "tool", "content": result})
    # 상한 도달 — tools 없이 한 번 더 불러 최종 답을 받는다.
    messages.append({"role": "user", "content": _FINAL_ANSWER_INSTRUCTION})
    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": model_options.request_options("ollama", model, temperature),
            },
            timeout=timeout_seconds or OLLAMA_LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        final = (resp.json().get("message") or {}).get("content", "")
    except Exception:  # noqa: BLE001 — 마무리 실패 시 기존 안내문으로
        final = ""
    return final or _TOOL_CALL_LIMIT_EXCEEDED_MESSAGE


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


_ANTHROPIC_MAX_OUTPUT_TOKENS = 8192

def _call_anthropic(
    model: str,
    system: str,
    prompt: str,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
) -> str:
    import anthropic  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = anthropic.Anthropic(
        timeout=timeout_seconds or _CLOUD_LLM_TIMEOUT_SECONDS
    )  # ANTHROPIC_API_KEY 환경변수를 사용한다.
    # 버그 수정(2026-11-13, 사용자 보고 — "결과물이 끝까지 생성되지
    # 않고 잘리는 문제" 조사 중 발견) — Anthropic SDK는 `max_tokens`가
    # 필수 인자라 반드시 어떤 값을 줘야 하는데, 지금까지 1024(약
    # 700~800 한글 단어 분량)로 고정돼 있었다 — "20장짜리 발표자료"
    # 같은 긴 산출물은 provider·모델 선택과 무관하게 이 값 하나 때문에
    # 항상 잘렸을 것이다(Ollama 쪽과 달리 이쪽은 재현 없이도 코드만
    # 봐도 확정적인 버그). Claude 최신 모델이 공통으로 지원하는
    # 8192토큰으로 올린다.
    resp = client.messages.create(
        model=model,
        max_tokens=_ANTHROPIC_MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        **model_options.request_options("anthropic", model, temperature),
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai(
    model: str,
    system: str,
    prompt: str,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
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
        **model_options.request_options("openai", model, temperature),
    )
    return resp.choices[0].message.content or ""


def _call_ollama(
    model: str,
    system: str,
    prompt: str,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
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
            "options": model_options.request_options("ollama", model, temperature),
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
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_ollama_error_with_detail(resp, exc)
    return resp.json()["message"]["content"]


def _raise_ollama_error_with_detail(resp, exc: Exception) -> None:
    """사용자 보고(2026-11-18, "데이터분석가이드 스킬 사용시 '400
    Bad Request' 오류") — 실측 재현 결과 스킬 자체와는 무관했다
    (Ollama 서버 로그: "request (33806 tokens) exceeds the available
    context size (32768 tokens)") — 대화가 길어져 로드된 모델의
    컨텍스트 한도(llama-server `-c` 옵션)를 넘기면 어떤 스킬/멘션을
    썼든 똑같이 터진다. 그런데 `raise_for_status()`가 올리는 예외는
    "Client error '400 Bad Request' for url '...'" 뿐이고, Ollama가
    응답 바디에 실어 보낸 진짜 원인(`error` 필드)은 읽지도 않고
    버려지고 있었다 — 사용자에게는 원인을 전혀 알 수 없는 메시지만
    남았다. 이 필드를 읽어 예외 메시지에 포함시킨다(호출부 전부
    `except Exception`으로 받아 `str(exc)`를 그대로 사용자에게
    보여주므로, 여기서 고치면 전부 혜택을 본다).

    버그 수정(2026-11-19, 사용자 보고 — 같은 바닥 메시지 재발) — 이
    함수는 원래 `_call_ollama()` 안에 인라인으로만 있었다. 그 뒤
    추가된 `_call_ollama_with_tools()`(개선축 A, Tool 호출 루프)는
    자기만의 `resp.raise_for_status()`를 별도로 호출하면서 이 보강을
    안 가져가, 같은 컨텍스트 초과 상황에서 다시 맨 "Client error '400
    Bad Request'"만 떴다 — 도구를 쓰는 스킬("데이터분석가이드" 등)
    일수록 요청 토큰 수가 더 커 더 쉽게 재현된다. 공용 함수로 빼서
    두 호출부가 똑같은 보강을 받게 한다."""
    detail = None
    try:
        detail = resp.json().get("error")
    except Exception:  # noqa: BLE001 — 응답 바디가 JSON이 아닐 수도 있음
        pass
    if detail:
        hint = (
            " — 대화가 길어져 이 모델의 컨텍스트 한도를 넘었습니다. "
            "대화를 요약해 새로 시작하거나, 첨부 자료를 줄이거나, "
            "더 넓은 컨텍스트를 지원하는 모델로 바꿔보세요."
            if "context size" in detail
            else ""
        )
        raise RuntimeError(
            f"Ollama 요청 실패(HTTP {resp.status_code}): {detail}{hint}"
        ) from exc
    raise exc


def system_prompt_for_role(
    conn: sqlite3.Connection,
    role_name: str,
    skills: list[str],
    *,
    request: str | None = None,
    callable_tools: list | None = None,
    has_retrieval: bool = False,
) -> str:
    """`conn`(2026-10-13, "Harness Methodology 기준 개발 단계별 에이전트
    생성... skill을 적극 활용") — 전까지는 `skills`의 스킬 "이름"만
    나열해 전달했다. `skill_pool.description`(예: "코드를 짜기 전에
    Spec·실패 모드 카탈로그·최소 골든셋을 만든다")은 어디에도 전달되지
    않아, 절차형 스킬을 Agent에 달아둬도 실행에 아무 영향이 없었다
    (`pool_registry.skill_knowledge_text()`가 이제 그 설명을 실제로
    끌어와 붙인다 — `deliverables.skill_grounded_system_prompt()`가
    쓰던 것과 같은 조회)."""
    # 스킬 활용·품질 개선(2026-11-21, `skill_prompting.py`) — 역할 소개에서
    # "소프트웨어 개발팀"을 뺐고(전략·보고·카피 Agent에도 똑같이 붙었다), 도구 안내는
    # 실제 실행 조건(`callable_tools`/`has_retrieval`)에 맞춰 고른다. `request`를
    # 주면 요청과 관련 있는 스킬을 앞에 두고 상위 몇 개만 상세 지침을 싣는다.
    from hair_platform import skill_prompting

    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    base = (
        f"당신은 '{role_name}' 역할을 맡은 AI 에이전트입니다. "
        f"보유 역량: {skill_text}. 이 역할의 숙련자 관점에서 실무에 바로 쓸 수 있는 "
        "구체적이고 실질적인 산출물을 작성하세요. 불필요한 설명 없이 산출물 자체에 집중하세요."
    )
    return skill_prompting.compose(
        base,
        conn,
        skills,
        request=request,
        callable_tools=callable_tools,
        has_retrieval=has_retrieval,
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
