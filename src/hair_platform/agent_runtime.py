"""배포된(active) Agent에게 실제로 일을 시키는 온디맨드 실행 — 사용자 요청(2026-09-26).

생성 시점의 LLM 호출(`jobs.llm_agent_generator`)은 "처음 합류했을 때의
산출물" 한 번만 만든다. 이 모듈은 그 이후 — 실제로 온보딩까지 끝나 조직에
배치된 Agent에게 구체적인 업무를 맡기고 결과를 받는 경로다. 이게 있어야
"생성만 되고 일은 안 하는 레코드"가 아니라 실제로 호출해 쓸 수 있는 Agent가 된다.

사용자 요청(2026-09-26, 3차) — `skill_tags`에 "내부지식검색"
또는 "조직배정관계조회"가 있으면 LLM을 부르기 전에 각각 `vectorstore.search()`(임베딩
검색, 추가 비용 발생)·`graphquery.context_for_prompt()`(SQL 조회, 비용 없음)로
검색 결과를 프롬프트에 덧붙인다(RAG) — 이게 "내부지식검색 Agent"·"관계분석
Agent"의 실제 동작이다. 관계분석 쪽은 경계를 지키기 위해 `graphquery.py`
자체가 성과 데이터를 절대 SELECT하지 않는다.

사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
API(가능한 무료)를... 추가해줘")로 "외부검색" skill_tags도
같은 방식으로 지원했다 — `websearch.context_for_prompt()`(Wikipedia
검색, API 키 불필요·무료)로 VectorDB/GraphDB가 못 다루는 "조직 밖
일반 지식"을 보강했다. ("websearch"/"vectordb"/"관계질의"는 각각
"외부검색"/"내부지식검색"/"조직배정관계조회"와 완전히 같은 뜻의 별칭이었으나 자동완성
중복 리포트로 2026-10-06에 한쪽으로 통합됨.) **이후(2026-10-26, "도구/
skill에서 동작하지 않는 검색은 삭제해줘")** Wikimedia가 이 배포 환경의
IP를 지속 차단해 "외부검색"은 시드에서 빠졌고, 같은 역할을 "실시간
웹검색"(Tavily)이 대신한다 — 아래 처리 방식도 동일한 패턴
(`pool_registry.resolve_static_augmentation_targets()`가 skill_pool
데이터로 판별)을 그대로 쓴다.
"""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import (
    audit,
    graphquery,
    id_scheme,
    llm,
    pool_catalog,
    pool_registry,
    tool_calling,
    vectorstore,
    websearch,
)
from hair_platform import llm_usage  # 업무 맥락별 사용량(2026-11-23, P2-11)

VECTORDB_SKILL_TAGS = {"내부지식검색"}
GRAPHDB_SKILL_TAGS = {"조직배정관계조회"}


def augment_with_retrieval(
    conn: sqlite3.Connection,
    skills: list[str],
    prompt: str,
    query: str | None = None,
) -> tuple[str, str | None]:
    """skill_tags로 내부지식검색(VectorDB)/관계분석(GraphDB) Agent를 판별해
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
    발견한 갭)**: 실제로 내부지식검색 Agent를 끝까지 돌려보니, 무엇이
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
    # `query`(2026-11-20) — 검색어를 프롬프트 전체와 분리한다. 대화 멘션은
    # 프롬프트가 "대화록 + 첨부 + 지시문"으로 끝나는데, 임베딩 모델 한도
    # (mxbai 350자)로 검색어의 **마지막 350자**만 남아 실제로는 지시문
    # 꼬리로 검색되고 있었다(실측 — 질문의 "HITL"이 검색어에 없었음).
    context = retrieve_context(conn, skills, query or prompt)
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
    return). 실제로 skill_tags에 "내부지식검색"+"외부검색"을 같이 가진 "외부
    자료 조사가" Agent가 대화창 `@멘션`(도구 호출 루프가 없는
    `execute_agent()` 경로)에서 "내부지식검색"에 가려 Wikipedia 검색이 한 번도
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
        # 실측으로 발견한 버그(2026-10-26, 같은 조사) — vectordb 임베딩
        # 호출은 websearch.py와 달리 실패를 스스로 삼키지 않는다(임베딩
        # API가 키 누락·quota·일시 장애로 실패하면 `openai.OpenAIError`/
        # `httpx.HTTPError`가 그대로 올라온다) — 여기서 잡아두지 않으면
        # "있으면 좋은 보강"이어야 할 vectordb 검색 하나 때문에 Agent의
        # 응답 전체가 날아간다(실제로 "외부 자료 조사가" Agent가 이걸로
        # 전체 실행이 죽었었다). websearch.search()가 이미 지키는
        # "검색 실패가 Agent 실행 자체를 막으면 안 된다" 원칙을 여기도
        # 똑같이 적용한다.
        try:
            hits = vectorstore.search(conn, query)
        except Exception:  # noqa: BLE001 — 임베딩 API 실패를 보강 실패로만 취급
            hits = []
        # 요청과 단어가 거의 안 겹치는 청크는 강제 주입하지 않는다(2026-11-22 실측 —
        # `vectorstore.lexically_relevant_hits()` 참고). 다 빠지면 아래 "찾지 못했습니다"로
        # 검색을 시도했다는 사실은 그대로 알린다.
        hits = vectorstore.lexically_relevant_hits(query, hits)
        # 사용자 요청(2026-11-17, "Human Agency Scale... ReAct 루프가
        # 없는 단발 경로는 검색 결과가 부족해도 다시 찾아보지 못한다는
        # 한계를 최소한 투명하게라도 드러내야 한다" → "구현 시작") —
        # 지금까지 VectorDB만 결과가 0건이면 이 섹션 자체를 조용히
        # 생략했다(graphdb/Tavily는 이미 "결과를 찾지 못했습니다"류
        # 문구를 항상 돌려주는데 VectorDB만 비대칭이었다 — 아래 두
        # websearch 함수 docstring의 "실패≠0건" 원칙과 같은 이유:
        # 검색을 **시도했다는 사실 자체**를 LLM에게 숨기면, "검색해봤지만
        # 없었다"와 "애초에 검색할 필요가 없었다"를 LLM이 구분 못 해
        # 답을 지어낼 위험이 커진다).
        # 사용자 요청(2026-11-17, "웹검색, 딥리서치, RAG 등 조사 관련
        # skill의 경우 출처를 반드시 명시하도록 해줘") — 지금까지
        # 청크 텍스트만 넘기고 `h['id']`를 버려서, "출처를 밝히라"고
        # 스킬 제약사항에 적어도 LLM이 인용할 구체적인 식별자 자체가
        # 없었다(지어낼 수밖에 없는 구조). 청크 ID를 같이 노출해 실제
        # 인용이 가능하게 한다.
        sections.append(
            vectorstore.format_hits_with_sources(conn, hits)
            if hits
            else "VectorDB에서 관련된 과거 산출물을 찾지 못했습니다."
        )
    if "graphdb_query" in native_ids:
        sections.append(graphquery.context_for_prompt(conn))
    if "tavily_search" in native_ids:
        # 사용자 요청(2026-10-26, "대화에서 외부 검색이 가능한 방법을
        # 제안해줘") — Parallel Search(MCP)는 대화창에서 구조적으로
        # 호출 불가능했던 갭을 메운다. `tavily_context_for_prompt()`
        # 자체가 실패를 문자열로 삼켜 돌려주는 계약이라(websearch.
        # context_for_prompt()와 동일) 여기서 별도 예외 처리가 필요
        # 없다.
        sections.append(websearch.tavily_context_for_prompt(query))
    if "tavily_news_search" in native_ids:
        # 사용자 요청(2026-11-17, "딥리서치 skill과... 최신(1주일) 뉴스와
        # 인사이트를 제공해 주는 Skill 추가") — 도구 호출 루프가 없는
        # 이 경로(대화 @멘션·스킬 드롭다운)에서도 "최근 N일 뉴스" 스킬이
        # 동작하도록 같은 방식으로 정적 주입한다.
        sections.append(websearch.tavily_news_context_for_prompt(query))
    if pool_catalog.NATIVE_ID in native_ids:
        # 스킬·도구 목록 조회(2026-11-21) — "스킬생성"이 중복 확인·도구 참조에 쓴다.
        sections.append(pool_catalog.context_for_prompt(conn, query))
    if not sections:
        return None
    return "\n\n".join(sections)


# `index_knowledge()`(Agent `last_output` 즉시 색인)는 2026-11-20 지식베이스
# 등록 체계 개편으로 삭제했다 — `knowledge_submissions.py` 참고. 과거에
# 이 경로로 들어간 청크의 `knowledge_chunks.source_agent_id`는 하위 호환을
# 위해 컬럼·조회 코드를 그대로 둔다.


# 사용자 보고(2026-10-28)로 추가 — `llm.py`의 클라우드 기본 타임아웃(900초)
# 보다 넉넉하게, `tuning_config.reasoning=True`인(agent_optimizer.py의
# deep_reasoning tier) Agent의 단발 실행에만 적용한다. `execute_agent()`
# 아래 참고.
#
# 사용자 결정(2026-11-12, "클라우드보다 Ollama가 더 길게, 작업 성격에
# 따라 차등") — `llm.py`가 Ollama의 "표준" 단발 호출 기본값을 이미
# 1800초로 올려서, provider 구분 없이 이 상수 하나만 썼다면
# reasoning tier Ollama 호출이 표준 tier Ollama 호출과 같은 값이 돼
# "작업 성격별 차등"이 Ollama에서만 사라질 뻔했다. 클라우드와 똑같이
# "표준의 2배" 비율을 유지해 Ollama 전용 값을 따로 둔다(`crew_runtime
# .CREWAI_LLM_TIMEOUT_SECONDS_OLLAMA`와 같은, 이미 자리 잡은 "provider
# 별 쌍" 패턴).
_DEEP_REASONING_LLM_TIMEOUT_SECONDS = 1800.0
_DEEP_REASONING_LLM_TIMEOUT_SECONDS_OLLAMA = 3600.0

# 사용자 보고(2026-11-13, "프레젠테이션 자료 작성자 결과물이 끝까지
# 생성되지 않고 잘리는 문제") — 실측 확인: 같은 Agent·같은 시스템
# 프롬프트로 재현을 시도한 결과 한 번은 11,325토큰을 끝까지 정상
# 완료했고(done_reason: stop), 실제 장애 사례 3건은 각각 409자·
# 19,606자·30,476자에서 제각각 다르게 끊겼다 — 고정된 상한(컨텍스트
# 길이 등)에 걸리는 게 아니라 비결정적으로(로컬 모델 서빙 환경의
# 변동 때문으로 추정 — 타임아웃 예외는 전혀 없었다, 셋 다 "성공"으로
# 기록됨) 간헐적으로 발생한다. 원인을 하나로 특정해 막기보다, 끊긴
# 것으로 보이면 그 자리에서 이어서 완성하도록 자동 보강한다 — 어느
# 경로로 끊기든 결과적으로 사용자는 완성된 산출물을 받는다.
_MAX_CONTINUATION_ATTEMPTS = 2


def _looks_truncated(text: str) -> bool:
    """완벽한 판정은 불가능하다 — 가장 흔한 두 신호만 본다: (1) 코드펜스
    (```)를 열어놓고 안 닫음, (2) HTML 문서를 열어놓고 `</html>`로
    안 닫음(펜스 안쪽이든 바깥이든). 둘 다 "문서를 끝까지 못 쓰고
    멈췄다"는 정직한 신호일 뿐, 오탐(예: 설명 중 ```를 홀수 번 언급)
    가능성도 있어 과하게 민감하게 잡지 않는다 — 그 정도 오탐은 한 번
    더 이어쓰기를 시도해도 손해가 없다(기존 내용을 안 버리고 이어
    붙이기만 하므로)."""
    stripped = text.rstrip()
    if stripped.count("```") % 2 == 1:
        return True
    has_html_open = bool(
        re.search(r"<!DOCTYPE\s+html|<html[\s>]", stripped, re.IGNORECASE)
    )
    if has_html_open and "</html>" not in stripped.lower():
        return True
    return False


@llm_usage.with_scope(agent_id="agent_id")
def execute_agent(
    conn: sqlite3.Connection,
    agent_id: int,
    actor_id: int,
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
    *,
    retrieval_sink: dict | None = None,
    retrieval_query: str | None = None,
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
    # 스킬 활용·품질 개선(2026-11-21, `skill_prompting.py`) — 조회 결과와 실제로
    # 호출 가능한 도구를 먼저 정한 뒤, 그 조건에 맞는 시스템 프롬프트를 만든다(예전엔
    # 도구가 붙어 있어도 "도구를 호출할 수 없다"는 안내가 항상 들어갔다). 요청과
    # 관련 있는 스킬을 앞에 두려고 요청 문장도 넘긴다.
    from hair_platform import progress  # 실행 중 단계 표시(2026-11-23, P0-2)

    progress.report("자료 조회 중")
    augmented_prompt, retrieved_context = augment_with_retrieval(
        conn, skills, prompt, retrieval_query
    )
    callable_tools = tool_calling.resolve_callable_tools(
        conn, skills, agent_id=agent_id
    )
    request_text = retrieval_query or prompt
    system = llm.system_prompt_for_role(
        conn,
        agent["role_name"],
        skills,
        request=request_text,
        callable_tools=callable_tools,
        has_retrieval=retrieved_context is not None,
    )
    # 사용자 요청(2026-10-26, "프레젠테이션 자료 작성자의 최종 결과를
    # HTML 형태로 생성되도록 개선해줘") — `agent_pool.expected_output`
    # (`PATCH .../persona`로 admin이 저장한 Agent별 기본 산출물 형식
    # 지시)은 지금까지 `crew_runtime.py`의 실제 Crew 실행(Task.
    # expected_output)에서만 반영되고, 이 단발 실행 경로(대화창
    # `@멘션`·"LLM으로 도와줘"급 호출 포함)에서는 완전히 무시됐다 —
    # admin이 persona 화면에서 "HTML로 작성" 같은 지시를 저장해도
    # @멘션으로 부르면 그 지시가 전혀 안 먹혀서 Crew 실행과 결과 형식이
    # 달라지는 비일관이 있었다. 설정돼 있으면 여기서도 똑같이 반영한다.
    # 역할/인격(2026-11-21, "직접 호출 경로에서 적용되도록 개선") — 기대
    # 산출물과 같은 이유로 admin이 직접 적은 목표·배경도 넣는다. 비어 있으면
    # 넣지 않는다(자동 생성 값은 역할명·스킬로 만든 것이라 위 시스템
    # 프롬프트에 이미 들어 있다).
    if agent["goal"]:
        system = system + f"\n\n목표: {agent['goal']}"
    if agent["backstory"]:
        system = system + f"\n\n배경: {agent['backstory']}"
    if agent["expected_output"]:
        system = system + f"\n\n기대 산출물 형식: {agent['expected_output']}"
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
    # 사용자 보고(2026-10-28, "프레젠테이션 자료 작성자" 실패 — "AI 모델의
    # 응답 생성이 시간 제한(15분)을 넘겨 중단됐습니다") — `llm.py`의
    # `_CLOUD_LLM_TIMEOUT_SECONDS`(900초)는 tier와 무관하게 모든 Agent에
    # 똑같이 적용돼, `reasoning=True`로 튜닝된(agent_optimizer.py의
    # deep_reasoning tier) Agent — 긴 보고서/발표자료처럼 본래 느린
    # 작업을 하는 Agent — 도 "표준" Agent와 같은 15분 한도에 걸렸다.
    # tuning_config에 저장된 reasoning 플래그만 보고 이 호출에만 더 넉넉한
    # 한도를 준다(크루 실행 경로의 `max_execution_time`과는 별개 — 그쪽은
    # `crew_runtime.py`가 이미 따로 처리).
    tuning = json.loads(agent["tuning_config"] or "{}")
    if tuning.get("reasoning") is True:
        timeout_seconds = (
            _DEEP_REASONING_LLM_TIMEOUT_SECONDS_OLLAMA
            if resolved_provider == "ollama"
            else _DEEP_REASONING_LLM_TIMEOUT_SECONDS
        )
    else:
        timeout_seconds = None
    # 파인튜닝을 직접 호출에도 적용(2026-11-21) — 뜻이 같은 값만 옮긴다:
    # temperature → 생성 옵션, max_execution_time → 호출 시간 제한,
    # max_iter → 도구 호출 반복 상한. 나머지(max_rpm·max_retry_limit·
    # 코드 실행·컨텍스트 자동 축소·max_reasoning_attempts)는 CrewAI 실행 루프
    # 전용이라 Crew 실행에서만 쓰인다. 값이 없으면 호출 인자를 넘기지 않는다.
    llm_extra: dict = {}
    if tuning.get("temperature") is not None:
        llm_extra["temperature"] = tuning["temperature"]
    if tuning.get("max_execution_time"):
        timeout_seconds = float(tuning["max_execution_time"])
    tool_extra: dict = dict(llm_extra)
    if tuning.get("max_iter"):
        tool_extra["max_iterations"] = int(tuning["max_iter"])
    # Tool 호출 제약 해소(2026-11-18, 개선축 A, 사용자 요청 "...Skill,
    # tool 사용 제약을 없애고... 엔터프라이즈 환경 적용이 가능한 수준의
    # 확실한 개선 방안을 도출해줘", 적용 범위 "Ollama 포함 전체 provider
    # 적용") — 단일 Agent 멘션도 "기본 AI"(`conversations.
    # run_default_ai_job()`)와 똑같이 CrewAI 루프 없이 provider 네이티브
    # 함수 호출로 native/custom_python 도구를 실제로 호출할 수 있게
    # 한다(`agent_id`를 넘겨 `agent_tool_links`로 명시 연결된 도구까지
    # 포함 — skill 기반 매칭만 보는 "기본 AI"보다 범위가 넓다).
    tool_calls: list[tuple[str, str]] = []
    progress.report(f"응답 생성 중 — {resolved_model}")
    if callable_tools:
        with tool_calling.record_calls() as tool_calls:
            output = llm.call_llm_with_tools(
                resolved_provider,
                resolved_model,
                system,
                augmented_prompt,
                callable_tools,
                timeout_seconds=timeout_seconds,
                **tool_extra,
            )
        # 차트 생성기 결과를 답에서 빼먹으면 붙인다(`tool_calling.ensure_charts_included()`).
        output = tool_calling.ensure_charts_included(output, tool_calls)
    else:
        output = llm.call_llm(
            resolved_provider,
            resolved_model,
            system,
            augmented_prompt,
            timeout_seconds=timeout_seconds,
            **llm_extra,
        )
    # 중간에 끊긴 것으로 보이면 이어서 완성시킨다(`_looks_truncated()`
    # 모듈 docstring 참고) — 최대 `_MAX_CONTINUATION_ATTEMPTS`번까지만
    # 시도해, 같은 이유로 매번 또 끊기는 드문 경우에도 무한히 돌지
    # 않는다. 이전까지 쓴 내용은 절대 버리지 않고 이어 붙이기만 한다.
    for _ in range(_MAX_CONTINUATION_ATTEMPTS):
        if not _looks_truncated(output):
            break
        progress.report("끊긴 응답 이어 쓰는 중")
        continuation = llm.call_llm(
            resolved_provider,
            resolved_model,
            system,
            "직전 응답이 끝까지 작성되지 못하고 중간에 멈췄습니다. 아래는 "
            "지금까지 작성된 내용의 마지막 부분입니다 — 처음부터 다시 쓰지 "
            "말고, 끊긴 지점 바로 다음부터 자연스럽게 이어서 문서를 "
            "완성하세요(펜스나 닫는 태그가 빠졌다면 그것도 포함해서 "
            f"마무리하세요):\n\n...(생략)...\n{output[-1500:]}",
            timeout_seconds=timeout_seconds,
            **llm_extra,
        )
        output = output + continuation

    # 자체 검토(2026-11-21, 파인튜닝 "품질 검토(자체 개선)" 켜짐) — 초안을 요청·스킬
    # 기준·결과 품질 기준으로 한 번 점검해 고친 최종본으로 바꾼다. 실패하거나 결과가
    # 이상하면(비었음·크게 짧아짐·끊김) 초안을 그대로 쓴다 — 검토가 응답을 막지 않는다.
    if tuning.get("self_review") is True and output.strip():
        progress.report("자체 검토 중")
        output, review_status = _self_review(
            conn,
            agent,
            skills,
            request_text,
            output,
            resolved_provider,
            resolved_model,
            timeout_seconds,
            llm_extra,
            evidence=_review_evidence(retrieved_context, tool_calls),
        )
        if retrieval_sink is not None:
            retrieval_sink["self_review"] = review_status

    # 수치 근거 검사(2026-11-23, P0-1 — `numeric_check.py`) — 퍼센트 수치가 요청·조회·도구
    # 결과의 숫자로 재현되지 않으면 그 문장만 한 번 고친다(맞으면 계산식이 붙을 뿐).
    from hair_platform import numeric_check

    numeric_evidence = "\n\n".join(
        [augmented_prompt] + [result for _name, result in tool_calls]
    )
    output, numeric_report = numeric_check.repair_output(
        conn, output, numeric_evidence, resolved_provider, resolved_model, actor_id,
        "agent_execute",
    )
    if retrieval_sink is not None and numeric_report is not None:
        retrieval_sink["numeric_check"] = numeric_report

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


def _review_evidence(retrieved_context: str | None, tool_calls: list[tuple[str, str]]) -> str:
    """자체 검토자가 초안의 수치를 대조할 근거 — 이번 실행에서 받은 조회 결과와 도구 결과
    (2026-11-22, `skill_prompting.self_review_prompt()` 참고). 실패한 도구 호출(형식 안내 등)도
    그대로 넣는다 — 검토자가 "도구가 계산하지 못한 값"을 구분할 수 있어야 한다."""
    parts = [f"[도구 {name} 결과]\n{result}" for name, result in tool_calls]
    if retrieved_context:
        parts.append(f"[조회 결과]\n{retrieved_context}")
    return "\n\n".join(parts)


def _self_review(
    conn: sqlite3.Connection,
    agent: sqlite3.Row,
    skills: list[str],
    request: str,
    draft: str,
    provider: str,
    model: str,
    timeout_seconds: float | None,
    llm_extra: dict,
    evidence: str | None = None,
) -> tuple[str, str]:
    """(최종 출력, 상태) — 상태: applied(검토본 채택) / rejected(검토본이 기준
    미달이라 초안 유지) / failed(호출 실패, 초안 유지)."""
    from hair_platform import skill_prompting

    knowledge = skill_prompting.knowledge_block(conn, skills, request)
    if agent["expected_output"]:
        knowledge += f"\n기대 산출물 형식: {agent['expected_output']}"
    system = skill_prompting.self_review_system(
        agent["role_name"], knowledge or "(스킬 지침 없음)"
    )
    try:
        revised = llm.call_llm(
            provider,
            model,
            system,
            skill_prompting.self_review_prompt(request, draft, evidence),
            timeout_seconds=timeout_seconds,
            **llm_extra,
        )
    except Exception:  # noqa: BLE001 — 검토 실패가 응답을 막으면 안 된다
        return draft, "failed"
    if skill_prompting.accept_review(draft, revised) and not _looks_truncated(revised):
        return revised.strip(), "applied"
    return draft, "rejected"


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

    `model`을 안 주면 `llm.resolve_provider_and_model()`이 고른 기본 모델(플랫폼 기본 LLM →
    모델 레지스트리 활성 모델 → 레지스트리가 비었을 때만 `llm.DEFAULT_MODELS`)로 채운다.
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
    after: dict[str, object] = {
        "llm_provider": resolved_provider,
        "llm_model": resolved_model,
    }
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


def set_tuning_config(
    conn: sqlite3.Connection, agent_id: int, actor_id: int, tuning: dict
) -> dict:
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


def set_persona(
    conn: sqlite3.Connection, agent_id: int, actor_id: int, **fields
) -> dict:
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
        f"UPDATE agent_pool SET {', '.join(set_clauses)} WHERE id = ?",
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
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 레퍼런스 22종이 아닌 신규
    # 생성 Agent는 전부 CUSTOM.
    id_scheme.assign_code(conn, "agent_pool", agent_id, id_scheme.AGENT_CUSTOM_SEGMENT)
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
