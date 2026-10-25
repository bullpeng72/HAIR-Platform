"""배포된(active) Agent에게 실제로 일을 시키는 온디맨드 실행 — 사용자 요청(2026-09-26).

생성 시점의 LLM 호출(`jobs.llm_agent_generator`)은 "처음 합류했을 때의
산출물" 한 번만 만든다. 이 모듈은 그 이후 — 실제로 온보딩까지 끝나 조직에
배치된 Agent에게 구체적인 업무를 맡기고 결과를 받는 경로다. 이게 있어야
"생성만 되고 일은 안 하는 레코드"가 아니라 실제로 호출해 쓸 수 있는 Agent가 된다.

사용자 요청(2026-09-26, 3차) — `skill_tags`에 "rag"
또는 "graphdb"가 있으면 LLM을 부르기 전에 각각 `vectorstore.search()`(임베딩
검색, 추가 비용 발생)·`graphquery.context_for_prompt()`(SQL 조회, 비용 없음)로
검색 결과를 프롬프트에 덧붙인다(RAG) — 이게 "지식검색 Agent"·"관계분석
Agent"의 실제 동작이다. 관계분석 쪽은 경계를 지키기 위해 `graphquery.py`
자체가 성과 데이터를 절대 SELECT하지 않는다.

사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
API(가능한 무료)를... 추가해줘")로 "외부검색" skill_tags도
같은 방식으로 지원한다 — `websearch.context_for_prompt()`(Wikipedia
검색, API 키 불필요·무료)로 VectorDB/GraphDB가 못 다루는 "조직 밖
일반 지식"을 보강한다. ("websearch"/"vectordb"/"관계질의"는 각각
"외부검색"/"rag"/"graphdb"와 완전히 같은 뜻의 별칭이었으나 자동완성
중복 리포트로 2026-10-06에 한쪽으로 통합됨.)
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit, graphquery, llm, pool_registry, vectorstore, websearch

VECTORDB_SKILL_TAGS = {"rag"}
GRAPHDB_SKILL_TAGS = {"graphdb"}
WEBSEARCH_SKILL_TAGS = {"외부검색"}


def augment_with_retrieval(
    conn: sqlite3.Connection, skills: list[str], prompt: str
) -> tuple[str, str | None]:
    """skill_tags로 지식검색(VectorDB)/관계분석(GraphDB) Agent를 판별해
    검색 결과를 프롬프트에 덧붙인다. 해당 없으면 원본 그대로.

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
    결과가 0건이면 `None`.

    **매칭 방식 정정(2026-09-29, "skill_pool.augmentation_mode의 'always'
    값 실제 구현")**: 이전엔 `VECTORDB_SKILL_TAGS` 등 하드코딩된 집합과의
    교집합으로 판별했다. 이제 `pool_registry.
    resolve_static_augmentation_targets()`로 `skill_pool`을 직접 조회한다
    — `modes=("always", "tool")` 둘 다 정적 주입 대상으로 삼는데, 이
    경로는 도구 호출 루프가 없어 Agent가 스스로 도구를 호출할 방법이
    없으므로(`'tool'`이든 `'always'`든 결과는 같다: 정적 주입이 유일한
    선택지) 굳이 구분하지 않는다. `'tool'` vs `'always'`가 실제로 다르게
    동작하는 지점은 `crew_runtime.py`(진짜 Tool 호출 루프가 있는 경로) —
    `'always'`만 Task description에 강제로 끼워 넣고, `'tool'`은 여전히
    Agent 판단에 맡긴다."""
    context = retrieve_context(conn, skills, prompt)
    if context is None:
        return prompt, None
    return f"{context}\n\n요청: {prompt}", context


def retrieve_context(
    conn: sqlite3.Connection, skills: list[str], query: str
) -> str | None:
    """`augment_with_retrieval()`의 실제 조회 부분만 떼어낸 것 — 사용자
    요청(2026-10-22, "대화창 기본 AI 경로에 실제 검색 연결")으로
    `conversations.run_default_ai_job()`(멀티턴 transcript를 그대로
    프롬프트로 써야 해서 `augment_with_retrieval()`의 "요청: {prompt}"
    포맷을 그대로 못 쓰는 호출부)도 같은 조회 로직을 쓸 수 있도록
    공용화했다. 매칭되는 native 도구가 하나도 없거나, 전부 결과가
    0건이면 `None`.

    실측으로 발견한 버그(2026-10-26, "외부 자료 조사가 에이전트...
    외부 검색 기능이 있는지 확인해줘") — 이전엔 `if vectordb_search:
    ...return` → `if graphdb_query: ...return` → `if web_search:
    ...return` 순서의 if-체인이라, native 스킬을 2개 이상 가진 Agent는
    먼저 매칭되는 **하나만** 쓰고 나머지는 전혀 조회하지 않았다
    (vectordb 결과가 있든 없든 동일 — 매칭되면 그 분기에서 무조건
    return). 실제로 skill_tags에 "rag"+"외부검색"을 같이 가진 "외부
    자료 조사가" Agent가 대화창 `@멘션`(도구 호출 루프가 없는
    `execute_agent()` 경로)에서 "rag"에 가려 Wikipedia 검색이 한 번도
    안 불리는 걸 재현 확인했다. CrewAI 실제 Tool 루프(`resolve_pool_
    tools()`)는 애초에 매칭되는 도구를 전부 리스트로 붙여 Agent가
    스스로 고르므로 이 문제가 없다 — 이 단발 호출 경로만 if-체인
    때문에 뒤처져 있었다. 셋 다 해당하면 셋 다 조회해 하나의 텍스트로
    이어붙인다."""
    native_ids = pool_registry.resolve_static_augmentation_targets(
        conn, skills, modes=("always", "tool")
    )
    sections: list[str] = []
    if "vectordb_search" in native_ids:
        hits = vectorstore.search(conn, query, top_k=3)
        if hits:
            sections.append(
                "참고할 과거 산출물:\n" + "\n".join(f"- {h['text']}" for h in hits)
            )
    if "graphdb_query" in native_ids:
        sections.append(graphquery.context_for_prompt(conn))
    if "web_search" in native_ids:
        sections.append(websearch.context_for_prompt(query))
    if not sections:
        return None
    return "\n\n".join(sections)


def index_knowledge(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    text: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """이 Agent의 산출물(또는 지정한 text)을 임베딩해 지식검색 Agent가 찾을
    수 있게 색인한다(비용 발생 — 항상 명시적 호출로만 일어난다)."""
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
    system = llm.system_prompt_for_role(conn, agent["role_name"], skills)
    augmented_prompt, retrieved_context = augment_with_retrieval(conn, skills, prompt)
    if retrieval_sink is not None:
        retrieval_sink["retrieved_context"] = retrieved_context
        # 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현
        # 가능한 감사로그) — 실제로 LLM에 들어간 전체 프롬프트(시스템+
        # 증강된 사용자 프롬프트)와 그 시점에 쓴 provider/model을 호출자
        # (`run_agent_execution_job()`)가 `agent_execution_jobs.
        # prompt_snapshot`에 영구 저장할 수 있게 같은 side-channel로
        # 함께 넘긴다 — LLM 호출(실패할 수도 있는 지점) *전에* 채워서,
        # 실패해도 "무엇을 시도했는지"는 남긴다.
        retrieval_sink["system_prompt"] = system
        retrieval_sink["augmented_prompt"] = augmented_prompt
        retrieval_sink["llm_provider"] = resolved_provider
        retrieval_sink["llm_model"] = resolved_model
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
    주면(빈 리스트 포함) 그 값으로 덮어쓴다(2026-09-27).
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


def set_tuning_config(conn: sqlite3.Connection, agent_id: int, actor_id: int, tuning: dict) -> dict:
    """사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    하는 기능") — CrewAI `Agent`/`LLM`의 동작 파라미터(max_iter/max_rpm/
    max_execution_time/max_retry_limit/reasoning/allow_code_execution/
    respect_context_window/temperature)를 Agent별로 저장한다. 실제 LLM
    가중치 재학습이 아니다(사용자 확인) — `crew_runtime.py`가 CrewAI
    Agent/LLM을 만들 때 이 값을 읽어 반영한다(`stub`/`simple_llm`
    프레임워크로 만든 Agent에는 적용되지 않음 — 그 경로엔 CrewAI Agent
    루프 자체가 없다).

    `tuning`에서 값이 `None`인 키는 저장에서 빼 "CrewAI 기본값 사용"으로
    되돌린다 — 지정 안 한 설정까지 `null`로 채워 저장하면 나중에 "이게
    명시적으로 비워진 건지 원래 기본값인지" 구분이 안 된다."""
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")

    cleaned = {k: v for k, v in tuning.items() if v is not None}
    conn.execute(
        "UPDATE agent_pool SET tuning_config = ? WHERE id = ?",
        (json.dumps(cleaned, ensure_ascii=False), agent_id),
    )
    audit.record(
        conn,
        actor_id,
        "agent_tuning_config_updated",
        "agent_pool",
        agent_id,
        before={"tuning_config": json.loads(agent["tuning_config"])},
        after={"tuning_config": cleaned},
    )
    conn.commit()
    return cleaned


def set_persona(conn: sqlite3.Connection, agent_id: int, actor_id: int, **fields) -> dict:
    """사용자 요청(2026-10-20, "CrewAI 에이전트... goal, backstory...
    상세조정하고 수정하기 위한 고급 에이전트 관리 기능") — 지금까지
    `Agent.goal`/`backstory`/`Task.expected_output`은 전부
    `crew_runtime.py`가 role_name+skill_tags로 매번 자동 생성하거나
    (goal/backstory) 플랫폼 전역 하드코딩 문구 하나뿐이었다
    (expected_output) — 관리자가 덮어쓸 방법이 없었다.

    `fields`에서 빈 문자열(`""`)을 주면 그 필드를 **명시적으로 비워**
    "자동 생성으로 되돌린다"는 뜻이다(`crew_runtime.py`가 소비할 때
    `row["goal"] or _agent_goal(...)`처럼 falsy 체크 하나로 폴백한다 —
    `None`/빈 문자열 모두 "오버라이드 없음"으로 취급). 필드를 아예 안
    보내면(`exclude_unset`) 기존 값을 그대로 둔다 — `set_tuning_config()`
    와 달리 부분 필드만 바꿀 수 있어야 하므로(goal만 바꾸고 backstory는
    유지하는 등) `tuning_config`처럼 JSON 통째로 덮어쓰지 않고 컬럼별로
    갱신한다."""
    allowed = {"goal", "backstory", "expected_output"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported_fields:{sorted(unknown)}")
    agent = conn.execute(
        "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("agent_not_found")
    if not fields:
        return {
            "goal": agent["goal"],
            "backstory": agent["backstory"],
            "expected_output": agent["expected_output"],
        }

    set_clauses = [f"{key} = ?" for key in fields]
    values = list(fields.values()) + [agent_id]
    conn.execute(
        f"UPDATE agent_pool SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608
        values,
    )
    audit.record(
        conn,
        actor_id,
        "agent_persona_updated",
        "agent_pool",
        agent_id,
        before={k: agent[k] for k in fields},
        after=fields,
    )
    conn.commit()
    updated = conn.execute(
        "SELECT goal, backstory, expected_output FROM agent_pool WHERE id = ?",
        (agent_id,),
    ).fetchone()
    return dict(updated)


def create_agent_directly(
    conn: sqlite3.Connection,
    actor_id: int,
    role_name: str,
    skill_tags: list[str],
    goal: str = "",
    backstory: str = "",
    expected_output: str = "",
) -> int:
    """사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
    참조) — 지금까지 Agent 생성은 Brief→Task 분해를 거쳐야만 했고
    (`jobs._run_item()`), role_name/skill_tags가 전부 선택한 Task에서
    그대로 왔다. 이 함수는 Brief/Task 없이 관리자가 역할명·스킬·
    goal/backstory/expected_output을 직접 입력해 즉시 `agent_pool`에
    등록한다 — `status`는 INSERT에 안 넣어 기존 생성 경로와 똑같이
    DB 기본값(`pending_onboarding`)에 맡긴다(기존 온보딩 승인 버튼이
    그대로 작동). goal/backstory/expected_output이 빈 문자열이면
    `set_persona()`와 같은 폴백 규칙(`crew_runtime.py`가 소비할 때
    `row["goal"] or _agent_goal(...)`)으로 자동 생성된다 — 새 규칙을
    만들지 않는다. `framework='manual'`로 저장해 Task 기반 생성
    (stub/simple_llm/crewai)과 구분한다(순수 표시용 — 다른 분기
    로직 없음, webui.py의 생성방식 열에만 쓰임)."""
    role_name = role_name.strip()
    if not role_name:
        raise ValueError("empty_role_name")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, goal, backstory, "
        "expected_output, framework) VALUES (?, ?, ?, ?, ?, 'manual')",
        (
            role_name,
            json.dumps(skill_tags, ensure_ascii=False),
            goal,
            backstory,
            expected_output,
        ),
    )
    agent_id = cur.lastrowid
    assert agent_id is not None
    audit.record(
        conn,
        actor_id,
        "agent_created_directly",
        "agent_pool",
        agent_id,
        after={"role_name": role_name, "skill_tags": skill_tags},
    )
    conn.commit()
    return agent_id
