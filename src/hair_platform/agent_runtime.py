"""배포된(active) Agent에게 실제로 일을 시키는 온디맨드 실행 — 사용자 요청(2026-09-26).

F4(생성) 시점의 LLM 호출(`jobs.llm_agent_generator`)은 "처음 합류했을 때의
산출물" 한 번만 만든다. 이 모듈은 그 이후 — 실제로 온보딩(F9)까지 끝나 조직에
배치된 Agent에게 구체적인 업무를 맡기고 결과를 받는 경로다. 이게 있어야
"생성만 되고 일은 안 하는 레코드"가 아니라 실제로 호출해 쓸 수 있는 Agent가 된다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit, llm


def execute_agent(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """온보딩된(status='active') Agent에게 프롬프트를 넘겨 실제로 일을 시킨다.

    `provider`/`model`을 안 넘기면 이 Agent에 저장된 모델 설정(`PATCH
    /api/agent-pool/{id}/model`로 지정, 또는 생성 시 `use_llm=True`로 이미
    쓰인 provider/model)을 우선 쓴다 — 그것도 없으면 `llm.py`의 전역 기본값
    판별로 넘어간다. 이게 "Agent별 모델 설정" 기능의 핵심이다(사용자 요청,
    2026-09-26).

    결과는 `agent_pool.last_output`에 누적 없이 최신값으로 저장한다(감사로그에
    이전 값이 남으므로 이력 자체는 audit_log로 추적 가능하다).
    """
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")
    if agent["status"] != "active":
        raise PermissionError("agent_not_active")

    skills = json.loads(agent["skill_tags"])
    effective_provider = provider or agent["llm_provider"]
    effective_model = model or agent["llm_model"]
    resolved_provider, resolved_model = llm.resolve_provider_and_model(
        effective_provider, effective_model
    )
    system = llm.system_prompt_for_role(agent["role_name"], skills)
    output = llm.call_llm(resolved_provider, resolved_model, system, prompt)

    conn.execute(
        "UPDATE agent_pool SET last_output = ?, llm_provider = ?, llm_model = ? WHERE id = ?",
        (output, resolved_provider, resolved_model, agent_id),
    )
    audit.record(
        conn,
        actor_id,
        "agent_executed",
        "agent_pool",
        agent_id,
        before={"prompt": prompt},
        after={"llm_provider": resolved_provider, "llm_model": resolved_model},
    )
    conn.commit()
    return output


def set_model_config(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    provider: str,
    model: str | None,
) -> tuple[str, str]:
    """Agent별 기본 LLM provider/model을 저장한다(비용 없음 — 설정만 한다).

    `model`을 안 주면 해당 provider의 기본 모델로 채운다(`llm.DEFAULT_MODELS`).
    """
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    conn.execute(
        "UPDATE agent_pool SET llm_provider = ?, llm_model = ? WHERE id = ?",
        (resolved_provider, resolved_model, agent_id),
    )
    audit.record(
        conn,
        actor_id,
        "agent_model_config_updated",
        "agent_pool",
        agent_id,
        before={"llm_provider": agent["llm_provider"], "llm_model": agent["llm_model"]},
        after={"llm_provider": resolved_provider, "llm_model": resolved_model},
    )
    conn.commit()
    return resolved_provider, resolved_model
