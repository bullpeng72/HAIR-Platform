"""배포된(active) Agent에게 실제로 일을 시키는 온디맨드 실행 — 사용자 요청(2026-09-26).

F4(생성) 시점의 LLM 호출(`jobs.llm_agent_generator`)은 "처음 합류했을 때의
산출물" 한 번만 만든다. 이 모듈은 그 이후 — 실제로 온보딩(F9)까지 끝나 조직에
배치된 Agent에게 구체적인 업무를 맡기고 결과를 받는 경로다. 이게 있어야
"생성만 되고 일은 안 하는 레코드"가 아니라 실제로 호출해 쓸 수 있는 Agent가 된다.

ADR 결정 11(사용자 요청, 2026-09-26, 3차) — `skill_tags`에 "vectordb"/"rag"
또는 "graphdb"가 있으면 LLM을 부르기 전에 각각 `vectorstore.search()`(임베딩
검색, 추가 비용 발생)·`graphquery.context_for_prompt()`(SQL 조회, 비용 없음)로
검색 결과를 프롬프트에 덧붙인다(RAG) — 이게 "지식검색 Agent"·"관계분석
Agent"의 실제 동작이다. 관계분석 쪽은 NF9 경계를 지키기 위해 `graphquery.py`
자체가 성과 데이터를 절대 SELECT하지 않는다.

사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
API(가능한 무료)를... 추가해줘")로 "websearch"/"외부검색" skill_tags도
같은 방식으로 지원한다 — `websearch.context_for_prompt()`(Wikipedia
검색, API 키 불필요·무료)로 VectorDB/GraphDB가 못 다루는 "조직 밖
일반 지식"을 보강한다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit, graphquery, llm, vectorstore, websearch

VECTORDB_SKILL_TAGS = {"vectordb", "rag"}
GRAPHDB_SKILL_TAGS = {"graphdb", "관계질의"}
WEBSEARCH_SKILL_TAGS = {"websearch", "외부검색"}


def augment_with_retrieval(
    conn: sqlite3.Connection, skills: list[str], prompt: str
) -> tuple[str, str | None]:
    """skill_tags로 지식검색(VectorDB)/관계분석(GraphDB) Agent를 판별해
    검색 결과를 프롬프트에 덧붙인다(ADR 결정 11). 해당 없으면 원본 그대로.

    **정정(2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록
    개선해줘")**: 이 정적 주입 방식은 이제 `execute_agent()`(도구 호출
    루프가 없는 단순 1회 LLM 호출)에만 남아 있다 — 그 경로는 Agent가 스스로
    도구를 호출할 수 없어(agent loop 자체가 없음) 이 방식이 유일한
    선택지다. `crew_runtime.run_crew()`/`crewai_agent_generator()`(실제
    CrewAI Agent loop가 있는 경로)는 대신 `vectorstore.as_crewai_tool()`/
    `graphquery.as_crewai_tool()`로 만든 진짜 CrewAI Tool을 Agent에 붙여서
    Agent가 호출 여부를 스스로 판단하게 한다(`crew_runtime._skill_based_
    tools()`) — 한때 이 함수를 `run_crew()`에도 그대로 연결했었지만
    (2026-09-27, "CrewAI 개념을 제대로 활용하는지 확인해줘"), 이번 요청으로
    Tool 방식으로 대체했다.

    **반환값 확장(2026-09-29, Tool/Skill 활용 시나리오 라이브 실행에서
    발견한 갭)**: 실제로 지식검색 Agent를 끝까지 돌려보니, 무엇이
    검색·주입됐는지 사후에 확인할 방법이 어디에도 없었다 — 결재자가
    "이 답변이 실제 회사 규정을 근거로 했는지, 그냥 지어낸 건지"를
    검증할 수가 없는 데이터 흐름 공백이었다. `(augmented_prompt,
    retrieved_context)`로 바꿔 호출자가 근거를 별도로 저장할 수 있게
    한다 — `retrieved_context`는 매칭되는 스킬이 없거나 VectorDB 검색
    결과가 0건이면 `None`."""
    skill_set = set(skills)
    if skill_set & VECTORDB_SKILL_TAGS:
        hits = vectorstore.search(conn, prompt, top_k=3)
        if hits:
            context = "\n".join(f"- {h['text']}" for h in hits)
            return f"참고할 과거 산출물:\n{context}\n\n요청: {prompt}", context
        return prompt, None
    if skill_set & GRAPHDB_SKILL_TAGS:
        context = graphquery.context_for_prompt(conn)
        return f"{context}\n\n요청: {prompt}", context
    if skill_set & WEBSEARCH_SKILL_TAGS:
        context = websearch.context_for_prompt(prompt)
        return f"{context}\n\n요청: {prompt}", context
    return prompt, None


def index_knowledge(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    text: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """이 Agent의 산출물(또는 지정한 text)을 임베딩해 지식검색 Agent가 찾을
    수 있게 색인한다(ADR 결정 11, 비용 발생 — 항상 명시적 호출로만 일어난다)."""
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")
    text_to_index = text or agent["last_output"]
    if not text_to_index:
        raise ValueError("no_text_to_index")

    chunk_id = vectorstore.index_text(conn, agent_id, text_to_index, provider, model)
    audit.record(
        conn,
        actor_id,
        "agent_knowledge_indexed",
        "knowledge_chunks",
        chunk_id,
        after={"source_agent_id": agent_id},
    )
    conn.commit()
    return chunk_id


def execute_agent(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
    *,
    retrieval_sink: dict | None = None,
) -> str:
    """온보딩된(status='active') Agent에게 프롬프트를 넘겨 실제로 일을 시킨다.

    `provider`/`model`을 안 넘기면 이 Agent에 저장된 모델 설정(`PATCH
    /api/agent-pool/{id}/model`로 지정, 또는 생성 시 `use_llm=True`로 이미
    쓰인 provider/model)을 우선 쓴다 — 그것도 없으면 `llm.py`의 전역 기본값
    판별로 넘어간다. 이게 "Agent별 모델 설정" 기능의 핵심이다(사용자 요청,
    2026-09-26).

    결과는 `agent_pool.last_output`에 누적 없이 최신값으로 저장한다(감사로그에
    이전 값이 남으므로 이력 자체는 audit_log로 추적 가능하다).

    `retrieval_sink`(2026-09-29, Tool/Skill 활용 시나리오 라이브 실행에서
    발견한 갭 보완): 반환 타입을 그대로 `str`로 유지해야 한다 — 이 함수는
    `agent_evaluation.run_and_score()`의 `Callable[[], str]` 계약과
    `/api/agent-pool/{id}/execute` 두 호출부 모두 문자열 반환을 전제로
    한다. 그래서 검색 근거는 반환값이 아니라 **선택적 side-channel**로만
    전달한다 — 호출자가 dict를 넘기면 `retrieved_context` 키에 채워
    넣는다(넘기지 않으면 기존 동작과 100% 동일)."""
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
    augmented_prompt, retrieved_context = augment_with_retrieval(conn, skills, prompt)
    if retrieval_sink is not None:
        retrieval_sink["retrieved_context"] = retrieved_context
    output = llm.call_llm(resolved_provider, resolved_model, system, augmented_prompt)

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
    mcp_servers: list[dict] | None = None,
) -> tuple[str, str]:
    """Agent별 기본 LLM provider/model(+ MCP 서버 설정)을 저장한다(비용
    없음 — 설정만 한다).

    `model`을 안 주면 해당 provider의 기본 모델로 채운다(`llm.DEFAULT_MODELS`).
    `mcp_servers`는 `None`이면 기존 설정을 그대로 두고(부분 갱신), 리스트를
    주면(빈 리스트 포함) 그 값으로 덮어쓴다(ADR 결정 10, 2026-09-27).
    """
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    if mcp_servers is not None:
        conn.execute(
            "UPDATE agent_pool SET llm_provider = ?, llm_model = ?, mcp_servers = ? "
            "WHERE id = ?",
            (
                resolved_provider,
                resolved_model,
                json.dumps(mcp_servers, ensure_ascii=False),
                agent_id,
            ),
        )
    else:
        conn.execute(
            "UPDATE agent_pool SET llm_provider = ?, llm_model = ? WHERE id = ?",
            (resolved_provider, resolved_model, agent_id),
        )
    after = {"llm_provider": resolved_provider, "llm_model": resolved_model}
    if mcp_servers is not None:
        after["mcp_servers"] = mcp_servers
    audit.record(
        conn,
        actor_id,
        "agent_model_config_updated",
        "agent_pool",
        agent_id,
        before={"llm_provider": agent["llm_provider"], "llm_model": agent["llm_model"]},
        after=after,
    )
    conn.commit()
    return resolved_provider, resolved_model
