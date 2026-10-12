"""실제 CrewAI 연동 — ADR.md 결정 10·11(사용자 요청, 2026-09-26, 2~3차).

이전까지 F4(Agent 생성)는 stub(무료)와 simple_llm(1회성 LLM 호출)만 있었다.
`grep -rniE "crewai" src/`로 확인한 결과 실행 코드에 CrewAI가 실제로 들어간
적이 없었다(`agents_yaml_snippet` 필드는 CrewAI 흉내만 낸 문자열). 이 모듈이
그 갭을 실제로 메운다.

**두 가지 경로**:
1. `crewai_agent_generator()` — F4 생성 시점에 단일 Agent+Task+Crew(sequential)
   를 실제로 구성해 kickoff한다(결정 10). `jobs.py`의 기존 `GeneratorFn`
   계약(role_name, required_skills) -> dict)을 그대로 따르므로 stub/simple_llm과
   동일한 방식으로 갈아끼울 수 있다.
2. `run_crew()` — 여러 개의 이미 온보딩된 Agent를 하나의 Crew로 묶어 실제로
   협업 실행한다(결정 11) — `Task.context`로 앞 Agent의 산출물을 다음 Agent에게
   전달하고(sequential), `allow_delegation=True`로 Agent가 동료에게 실제로
   위임/질문할 수 있게 한다. 위임이 실제로 일어났는지는 CrewAI의 이벤트버스에서
   "Delegate work to coworker"/"Ask question to coworker" 도구 사용 이벤트를
   실시간으로 캡처해서 판단한다(추측이 아니라 실제 발동 여부를 기록 —
   LIMITS L7 교훈과 같은 정신).

**비용 발생 지점**: `crewai_agent_generator()`/`run_crew()`를 호출하면 실제
LLM API가 호출된다(CrewAI가 내부적으로 처리) — `use_llm`/`framework` 게이팅은
호출부(app.py)에서 한다(Gate A-G 때부터의 opt-in 원칙 재적용).

**주의(중요, 실제로 겪은 사고)**: `crewai`(그리고 그게 지연 import하는
litellm)는 **import되는 순간 자체 dotenv.load_dotenv()를 실행한다** — 이
모듈을 그냥 import하기만 해도 `.env`의 API 키가 프로세스 환경변수에 로드된다.
테스트에서는 반드시 `crewai.Crew.kickoff`(가장 바깥쪽 호출 경계)를 직접
mock해야 한다 — 그보다 안쪽(`LLM.call` 등)을 mock하면 CrewAI 내부 재시도
로직이 그걸 우회해 실제 네트워크 호출로 새어나갈 수 있다(이 세션에서 실제로
한 번 발생시켜 확인함).

**MCP(외부 도구) 연동(ADR 결정 10, 사용자 요청 2026-09-27)**: `run_crew()`에서
각 Agent의 `agent_pool.mcp_servers`(`AgentModelConfig.mcp_servers`로 설정)에
따라 `crewai-tools`의 `MCPServerAdapter`로 실제 MCP 서버에 접속해 도구를
붙인다(`_mcp_tools()`). `crewai_agent_generator()`(F4 단일 생성)에는 아직
연결하지 않았다 — 그 시점엔 아직 `agent_pool` row가 없어 MCP 설정을 읽어올
곳이 없기 때문이다(온보딩 이후 `run_crew()`에서만 유효, 오픈 이슈로 명시).
`MCPServerAdapter`는 생성 시점에 실제로 네트워크/프로세스 접속을 시도하므로,
이 환경처럼 접속 가능한 MCP 서버가 없으면 mock 경계(`crewai_tools.
MCPServerAdapter` 클래스 자체)로만 검증할 수 있다 — `docs/LIMITS.md` 참고.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import ExitStack

from hair_platform import (
    agent_evaluation,
    audit,
    graphquery,
    llm,
    pool_registry,
    teams,
    vectorstore,
    websearch,
)

DELEGATION_TOOL_NAMES = {"Delegate work to coworker", "Ask question to coworker"}

# 사용자 결정(2026-09-29, "CrewAI 위임 캡처가 CrewAI 내부 문자열에
# 하드코딩돼 있는 리스크 — 지금 방어 로직을 추가") — 위 정확한 문자열이
# CrewAI 버전업으로 바뀌면 위임이 실제로 발생해도 `delegations`가 계속
# 비어 있는 "조용한 실패"가 된다(docs/LIMITS.md §12). 정확한 이름 매칭을
# 우선하되, 대소문자·표기 변형(예: "co-worker")까지 놓치지 않도록 키워드
# 기반 완화 매칭을 fallback으로 둔다 — 이 기능의 목적(위임 발생 여부의
# 정직한 기록) 상 오탐보다 미탐이 훨씬 치명적이라는 판단.
_DELEGATION_KEYWORDS = ("delegate", "coworker", "co-worker", "ask question")


def _looks_like_delegation(tool_name: str) -> bool:
    if tool_name in DELEGATION_TOOL_NAMES:
        return True
    lowered = tool_name.lower()
    return any(keyword in lowered for keyword in _DELEGATION_KEYWORDS)


def _sanitize_role(name: str) -> str:
    """CrewAI 자체의 `BaseAgentTool.sanitize_agent_name()`과 같은 정규화
    (공백 정리+소문자화) — 아래 `_resolve_delegation_target()`이 LLM이
    자유 텍스트로 써낸 `coworker` 값을 실제 Agent role과 매칭할 때,
    CrewAI가 내부적으로 쓰는 것과 동일한 기준으로 비교해야 "매칭 안 됨"을
    우리가 따로 오탐하지 않는다."""
    return " ".join(name.split()).replace('"', "").casefold()


def _resolve_delegation_target(tool_args, known_roles: list[str]) -> str | None:
    """사용자 요청(2026-10-06, "묶인 Agent간 협업 관계를 다이어그램으로
    표시") — 위임 다이어그램을 그리려면 "누가" 뿐 아니라 "누구에게"가
    필요한데, 기존 `_capture_delegation`은 `event.tool_args`를 아예 안 읽어
    대상을 버리고 있었다. CrewAI의 위임 Tool(`DelegateWorkTool`/
    `AskQuestionTool`)은 둘 다 `coworker` 인자로 대상 role을 받는다
    (crewai 소스 확인: `delegate_work_tool.py`/`ask_question_tool.py`) —
    `ToolUsageEvent.tool_args`에 그 값이 그대로 실려 있어 꺼낼 수 있다.
    LLM이 대소문자·공백을 자유롭게 써내므로, CrewAI 자신의 매칭 방식과
    동일하게 정규화해 실제 Agent role과 맞춰본다 — 못 찾으면(예: 존재
    하지 않는 동료를 호출해 CrewAI가 에러를 반환한 경우) 원문 그대로
    돌려줘 정보를 버리지 않는다."""
    if not isinstance(tool_args, dict):
        return None
    raw = tool_args.get("coworker") or tool_args.get("co_worker")
    if not raw or not isinstance(raw, str):
        return None
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].split(",")[0]
    sanitized = _sanitize_role(raw)
    for role in known_roles:
        if _sanitize_role(role) == sanitized:
            return role
    return raw.strip()


# ADR 결정 11 한계 섹션이 "반드시 걸어야 한다"고 명시했던 상한(사용자 요청,
# 2026-09-27, "crewai 기능 적절히 적용됐는지 분석" 리뷰에서 실제로 미반영 상태를
# 발견해 반영함). CrewAI Agent 기본값(25)보다 보수적으로 잡는다 — 우리는 Crew
# 전체 Agent에 allow_delegation=True를 켜서 위임 왕복이 겹칠 수 있기 때문이다.
DELEGATION_MAX_ITER = 10


def _mcp_tools(stack: ExitStack, mcp_servers: list[dict]) -> list:
    """`AgentModelConfig.mcp_servers`(Agent별 개별 MCP 설정)에 실제로
    접속해 CrewAI 도구 목록을 모은다 — 접속 로직 자체는
    `pool_registry.connect_mcp_tools()`에 있다(ADR 결정 12 이후 Tool Pool
    경로와 공유). `mcp_servers`가 비어 있으면 `crewai_tools`/`mcp`를 아예
    import하지 않는다(설치 안 해도 CrewAI의 다른 경로는 그대로 동작해야
    하므로 — 지연 import 원칙)."""
    if not mcp_servers:
        return []
    return [
        tool
        for cfg in mcp_servers
        for tool in pool_registry.connect_mcp_tools(stack, cfg)
    ]


def _skill_based_tools(
    conn: sqlite3.Connection,
    stack: ExitStack,
    skills: list[str],
    agent_id: int | None = None,
) -> list:
    """`skill_pool`/`agent_tool_links`(ADR 결정 12, 사용자 요청 2026-09-27
    — "tool_pool·skill_pool 테이블도 실제로 구현해줘")를 조회해 실제
    CrewAI Tool을 붙인다 — `pool_registry.resolve_pool_tools()`에 위임한다.

    **정정 이력**: 처음엔 `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS`를
    직접 하드코딩해서 판별했었는데(2026-09-27, "MCP Tool처럼... 동작하도록
    개선해줘"), 이번 요청으로 그 매핑 자체를 `skill_pool` 데이터로
    옮겼다 — 새 스킬을 추가할 때 코드를 안 고치고 `skill_pool`에 레코드만
    추가하면 된다."""
    return pool_registry.resolve_pool_tools(conn, stack, skills, agent_id=agent_id)


def _static_augmentation(
    conn: sqlite3.Connection, skills: list[str], description: str
) -> str:
    """사용자 결정(2026-09-29, "skill_pool.augmentation_mode의 'always'
    값 실제 구현") — `'tool'` 모드는 이미 위 `_skill_based_tools()`로 진짜
    CrewAI Tool을 붙여 Agent가 스스로 호출 여부를 판단하게 한다. `'always'`
    모드는 그와 달리 **Agent의 판단을 거치지 않고 Task description에
    검색 결과를 강제로 끼워 넣는다** — `agent_runtime.
    augment_with_retrieval()`이 도구 호출 루프가 없는 단일 실행 경로에서
    하던 정적 주입과 같은 성격이지만, 여기서는 `'tool'`과 구분되는
    별도 옵션으로만 켜진다(기본 시드 스킬은 전부 `'tool'`이라 이 함수는
    기본 동작을 바꾸지 않는다 — 관리자가 skill_pool에 `augmentation_mode
    ='always'`인 항목을 만들어야 실제 효과가 생긴다)."""
    native_ids = pool_registry.resolve_static_augmentation_targets(
        conn, skills, modes=("always",)
    )
    if not native_ids:
        return description
    parts = []
    if "vectordb_search" in native_ids:
        hits = vectorstore.search(conn, description, top_k=3)
        if hits:
            parts.append(
                "참고할 과거 산출물:\n" + "\n".join(f"- {h['text']}" for h in hits)
            )
    if "graphdb_query" in native_ids:
        parts.append(graphquery.context_for_prompt(conn))
    if "web_search" in native_ids:
        parts.append(websearch.context_for_prompt(description))
    if not parts:
        return description
    return "\n\n".join(parts) + f"\n\n요청: {description}"


def _crewai_llm(provider: str, model: str):
    from crewai import LLM  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return LLM(model=f"ollama/{model}", base_url=base_url)
    return LLM(model=f"{provider}/{model}")


def _agent_goal(role_name: str) -> str:
    return f"'{role_name}' 역할로서 주어진 업무를 실무에 바로 쓸 수 있는 수준으로 완수한다."


def _agent_backstory(role_name: str, skills: list[str]) -> str:
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    return f"소프트웨어 개발팀의 '{role_name}'로서 보유 역량은 {skill_text}이다."


def crewai_agent_generator(
    conn: sqlite3.Connection,
    role_name: str,
    required_skills: list[str],
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """F4 생성 시점에 실제 CrewAI Agent+Task+Crew(단일, sequential)를 구성해
    kickoff한다. `jobs.GeneratorFn`과 같은 시그니처라 그대로 갈아끼울 수 있다.

    `conn`은 이 Agent가 vectordb/graphdb 스킬을 가졌을 때 실제 CrewAI
    Tool(`_skill_based_tools()`)을 붙이기 위해 필요하다(사용자 요청,
    2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘" —
    `GeneratorFn` 계약 자체에 conn을 추가해 F4 생성 시점부터 이 Agent가
    쓸 도구를 가지고 태어나게 했다)."""
    from crewai import Agent, Crew, Process, Task

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    crew_llm = _crewai_llm(resolved_provider, resolved_model)

    # Tool Pool 항목 중 mcp_stdio/mcp_remote 종류는 접속을 유지했다가 정리해야
    # 해서 ExitStack이 필요하다(ADR 결정 12) — native(VectorDB/GraphDB)는
    # 필요 없지만 같은 해석 경로(_skill_based_tools)를 쓰므로 통일한다.
    with ExitStack() as pool_stack:
        agent = Agent(
            role=role_name,
            goal=_agent_goal(role_name),
            backstory=_agent_backstory(role_name, required_skills),
            llm=crew_llm,
            allow_delegation=False,  # 단일 Agent Crew라 위임할 동료가 없다.
            tools=_skill_based_tools(conn, pool_stack, required_skills),
            verbose=False,
        )
        task = Task(
            description=_static_augmentation(
                conn,
                required_skills,
                f"'{role_name}' 역할로서 지금 막 팀에 합류했습니다. 이 역할이 "
                "프로젝트에서 처음 맡을 만한 구체적인 산출물을 하나 작성하세요.",
            ),
            expected_output="실무에 바로 쓸 수 있는 구체적인 산출물",
            agent=agent,
        )
        crew = Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=False
        )
        result = crew.kickoff()

    return {
        "role_name": role_name,
        "skill_tags": required_skills,
        "agents_yaml_snippet": f"role: {role_name}, skills: {required_skills}",
        "llm_provider": resolved_provider,
        "llm_model": resolved_model,
        "generated_output": result.raw,
        "framework": "crewai",
    }


# `deliverables.py`의 `_CONTENT_TYPE_INSTRUCTIONS`와 내용이 같다(의도적
# 중복 — 2026-10-07, "Tier 2까지 진행해줘"). 이 모듈은 `deliverables.py`를
# import할 수 없다(순환 참조 — `deliverables.py`가 이미 이 모듈을 쓴다).
_CONTENT_TYPE_INSTRUCTIONS = {
    "text": "",
    "markdown": "결과물은 Markdown 형식으로 작성하세요(제목은 #, 목록은 -, 강조는 **, "
    "코드는 ``` 블록을 쓰세요).",
    "html": "결과물은 완전한 HTML 문서로 작성하세요(<!DOCTYPE html>부터 </html>까지, "
    "스타일은 <style> 태그 안에 인라인으로만 넣고 외부 리소스는 참조하지 마세요).",
    "code": "결과물은 코드로 작성하세요. 전체를 ``` 코드 블록 안에 넣고, 필요하면 "
    "블록 앞뒤에 짧은 설명만 덧붙이세요.",
}


def run_crew(
    conn: sqlite3.Connection,
    agent_ids: list[int],
    task_descriptions: list[str],
    actor_id: int,
    process: str = "sequential",
    provider: str | None = None,
    model: str | None = None,
    manager_agent_id: int | None = None,
) -> dict:
    """ADR 결정 11 — 여러 온보딩된 Agent를 CrewAI Crew로 묶어 실제 협업
    실행한다. N번째 `agent_ids`가 N번째 `task_descriptions`를 맡는다.

    `process="sequential"`이면 Task.context로 앞 Agent의 산출물이 다음
    Agent에게 자동 전달된다. `"hierarchical"`이면 매니저(지정한
    `manager_agent_id` 또는 매니저 LLM)가 작업을 조율한다 — CrewAI의
    hierarchical 모델은 매니저가 작업을 직접 수행하지 않고 조율·위임만
    하므로, `manager_agent_id`로 지정된 Agent에 대응하는
    `task_descriptions` 항목은 실행되지 않는다(그 Agent는 매니저 역할만
    맡는다). 이 때문에 Task.context 체이닝도 hierarchical에서는 걸지
    않는다 — 매니저가 매 순간 누구에게 무엇을 넘길지 동적으로 결정하므로
    sequential처럼 "바로 앞 Task"만 미리 고정해 넘기는 게 의미가 없다.
    모든 Agent에 `allow_delegation=True`를 켜서 실제 동료 위임/질의가
    가능하게 한다 — 실제로 위임이 발동했는지는 CrewAI 이벤트버스에서
    캡처해 정직하게 기록한다(추측 아님).

    `memory=True`(ADR 결정 10에서 약속했으나 누락됐던 부분, 2026-09-27
    리뷰에서 발견해 반영함)로 Crew 전체가 이전 실행 맥락을 기억한다 —
    **주의**: CrewAI의 기본 메모리 임베더는 provider와 무관하게 OpenAI
    임베딩 API를 쓴다(Anthropic은 임베딩 API 자체가 없다). 즉
    provider="anthropic"/"ollama"로 이 함수를 호출해도 `OPENAI_API_KEY`가
    별도로 필요하다 — 없으면 메모리 저장 시점에 CrewAI가 에러를 낸다.
    """
    if len(agent_ids) != len(task_descriptions):
        raise ValueError("agent_ids와 task_descriptions의 개수가 같아야 합니다")
    if not agent_ids:
        raise ValueError("agent_ids가 비어 있습니다")
    if manager_agent_id is not None:
        if process != "hierarchical":
            raise ValueError(
                "manager_agent_id는 process='hierarchical'에서만 사용합니다"
            )
        if manager_agent_id not in agent_ids:
            raise ValueError(
                f"manager_agent_id가 agent_ids에 없습니다: {manager_agent_id}"
            )

    from crewai import Agent, Crew, Process, Task
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

    rows = []
    for agent_id in agent_ids:
        row = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"agent_not_found:{agent_id}")
        if row["status"] != "active":
            raise PermissionError(f"agent_not_active:{agent_id}")
        rows.append(row)

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    crew_llm = _crewai_llm(resolved_provider, resolved_model)

    # ADR 결정 10(MCP, 2026-09-27) — MCPServerAdapter는 생성 시점에 실제로
    # 접속을 시도하므로, Crew 실행이 끝날 때까지 연결을 유지했다가 정리하기
    # 위해 ExitStack으로 감싼다.
    with ExitStack() as mcp_stack:
        crewai_agents = [
            Agent(
                role=row["role_name"],
                goal=_agent_goal(row["role_name"]),
                backstory=_agent_backstory(
                    row["role_name"], json.loads(row["skill_tags"])
                ),
                llm=crew_llm,
                allow_delegation=True,  # ADR 결정 11 — 정적 릴레이가 아니라 실제 협업
                max_iter=DELEGATION_MAX_ITER,
                # AgentModelConfig.mcp_servers(Agent별 개별 MCP 설정, ADR
                # 결정 10) + skill_pool/agent_tool_links(Tool Pool, ADR 결정
                # 12) — 둘 다 실제 CrewAI Tool이라 같은 리스트에 합친다.
                tools=(
                    _mcp_tools(mcp_stack, json.loads(row["mcp_servers"]))
                    + _skill_based_tools(
                        conn,
                        mcp_stack,
                        json.loads(row["skill_tags"]),
                        agent_id=row["id"],
                    )
                ),
                verbose=False,
            )
            for row in rows
        ]

        # 정정(2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘"):
        # 예전에는 여기서 `agent_runtime.augment_with_retrieval()`로 검색
        # 결과를 Task description에 무조건 끼워 넣었다(정적 주입). 이제는
        # 위에서 vectordb/graphdb 스킬을 가진 Agent에게 진짜 CrewAI Tool을
        # 붙였으므로(augmentation_mode='tool'), description은 원문 그대로
        # 두고 **Agent가 스스로 도구 호출 여부를 판단**하게 한다 — 정적
        # 주입과 Tool 호출을 동시에 하면 검색 결과가 중복으로 들어가거나
        # Agent 판단이 무의미해진다.
        #
        # 재정정(2026-09-29, "skill_pool.augmentation_mode의 'always' 값
        # 실제 구현"): 위 원칙은 `'tool'` 모드에만 적용된다. `'always'`
        # 모드로 등록된 스킬은 이 원칙의 예외로 두기로 결정했다 — Agent
        # 판단에 맡기지 않고 강제로 컨텍스트를 주입하는 것 자체가
        # `'always'`의 존재 이유이므로, `_static_augmentation()`이
        # `'always'` 매칭분만 여기서 description 앞에 붙인다(기본 시드
        # 스킬은 전부 `'tool'`이라 이 변경은 기존 동작을 바꾸지 않는다).
        tasks: list[Task] = []
        for i, description in enumerate(task_descriptions):
            context = [tasks[i - 1]] if i > 0 and process == "sequential" else None
            tasks.append(
                Task(
                    description=_static_augmentation(
                        conn, json.loads(rows[i]["skill_tags"]), description
                    ),
                    expected_output="실무에 바로 쓸 수 있는 구체적인 산출물",
                    agent=crewai_agents[i],
                    context=context,
                )
            )

        # CrewAI는 manager_agent가 agents 목록에 함께 있으면
        # ValidationError(manager_agent_in_agents)를 던진다 — 매니저는 작업을
        # 직접 수행하지 않는다는 hierarchical 모델과 일치한다(2026-09-27 리뷰에서
        # 실측으로 확인한 실사용 크래시 버그를 여기서 고친다).
        manager_index = (
            agent_ids.index(manager_agent_id) if manager_agent_id is not None else None
        )
        worker_agents = [a for i, a in enumerate(crewai_agents) if i != manager_index]
        worker_tasks = [t for i, t in enumerate(tasks) if i != manager_index]
        worker_rows = [r for i, r in enumerate(rows) if i != manager_index]

        task_completions: list[dict] = []

        def _capture_task_completion(task_output) -> None:
            # 주의(실사용 중 발견, 2026-09-27): `task_output.agent`를 그대로
            # 믿으면 안 된다 — CrewAI 소스(`crew.py`의 `_get_agent_to_use`)를
            # 확인해보니 hierarchical 프로세스에서는 이 필드가 **항상
            # manager_agent의 role**로 채워진다(매니저가 내부적으로 위임하더라도
            # Crew 실행 루프 관점에서는 매니저 자신이 "이 Task를 실행한 Agent"이기
            # 때문). 실제로 매니저=기획자, 워커=분석가로 hierarchical 실행했더니
            # 분석가가 만든 산출물인데도 `task_output.agent == "기획자"`로 찍히는
            # 걸 실측으로 확인했다. 대신 `worker_tasks`/`worker_rows`는 우리가
            # 구성한 순서이고 CrewAI는 `self.tasks`를 그 순서 그대로 순차
            # 실행하므로(`_execute_tasks`), 콜백이 몇 번째 호출됐는지로
            # `worker_rows`를 인덱싱하면 실제 담당 Agent를 정확히 알 수 있다.
            idx = len(task_completions)
            agent_role = (
                worker_rows[idx]["role_name"]
                if idx < len(worker_rows)
                else task_output.agent
            )
            task_completions.append(
                {
                    "agent_role": agent_role,
                    "output_preview": (task_output.raw or "")[:200],
                }
            )

        crew_kwargs: dict[str, object] = {
            "agents": worker_agents,
            "tasks": worker_tasks,
            "verbose": False,
            "memory": True,
            # 사용자 요청(2026-09-27, "다른 리뷰 항목 마저 진행해줘")으로 Task
            # 완료마다 감사로그를 남긴다. `step_callback`은 일부러 안 쓴다 —
            # CrewAI가 콜백에 넘기는 AgentAction/AgentFinish에는 어느 Agent가
            # 낸 스텝인지 식별할 필드가 없어(실측 확인: thought/tool/tool_input/
            # text/result 뿐) 감사로그에 "누가"를 못 남긴다.
            "task_callback": _capture_task_completion,
        }
        if process == "hierarchical":
            crew_kwargs["process"] = Process.hierarchical
            if manager_agent_id is not None:
                crew_kwargs["manager_agent"] = crewai_agents[manager_index]
            else:
                crew_kwargs["manager_llm"] = crew_llm
        elif process == "sequential":
            crew_kwargs["process"] = Process.sequential
        else:
            raise ValueError(f"지원하지 않는 process입니다: {process!r}")

        crew = Crew(**crew_kwargs)

        delegations: list[dict] = []
        known_roles = [r["role_name"] for r in rows]

        def _capture_delegation(source, event) -> None:
            if _looks_like_delegation(event.tool_name):
                delegations.append(
                    {
                        "from_agent_role": event.agent_role,
                        "to_agent_role": _resolve_delegation_target(
                            event.tool_args, known_roles
                        ),
                        "tool": event.tool_name,
                    }
                )

        with crewai_event_bus.scoped_handlers():
            crewai_event_bus.on(ToolUsageFinishedEvent)(_capture_delegation)
            # 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
            # 연결해줘") — 지금까지 Gate A-G는 단일 실행 경로(`agent_runtime.
            # execute_agent()`)에만 연결돼 있었다. `framework="crewai"`를 쓰면
            # SDK가 `crew.kickoff()`가 낸 CrewOutput.tasks_output에서 자동으로
            # 멀티에이전트 신호(Gate F: 합의·정보 전파·역할 준수·충돌 해결)를
            # 뽑아준다 — 반환값은 그대로 CrewOutput이라 아래 로직은 전혀
            # 안 바뀐다.
            crew_label = ", ".join(r["role_name"] for r in worker_rows)
            eval_result = agent_evaluation.run_and_score_crew(crew_label, crew.kickoff)
            result = eval_result["output"]

    per_agent = []
    for row, task_output in zip(worker_rows, result.tasks_output, strict=False):
        # 사용자 요청(2026-10-06, "model provider/모델명이 AI 생성·관리·Crew
        # 실행 단계에 모두 있는데 최종 어떻게 적용되는지 분석" → "(b)
        # 실행 후 덮어쓰기만이라도 막는 방향으로 수정") — Crew 실행은 참여
        # Agent 전원에게 요청 시점의 provider/model **하나**를 똑같이
        # 적용한다(개별 Agent의 "모델 설정" 저장값은 애초에 안 읽음,
        # `_crewai_llm()` 호출부 참고). 그런데 실행이 끝나면 그 하나의
        # 값을 모든 참여 Agent의 `llm_provider`/`llm_model`에 그대로
        # 덮어써 와서, "AI 에이전트 관리"에서 Agent별로 따로 설정해둔
        # 기본값(단일 실행 경로 `agent_runtime.execute_agent()`가 쓰는
        # 그 값)이 Crew 실행 한 번으로 조용히 사라지는 문제가 있었다.
        # `last_output`/`framework`(이번 실행이 실제로 crewai였다는
        # 사실)는 그대로 갱신하되, llm_provider/llm_model은 더 이상
        # 건드리지 않는다 — "모델 설정"은 사용자가 명시적으로
        # 바꾸기 전까지는 Crew 실행과 무관하게 유지된다.
        conn.execute(
            "UPDATE agent_pool SET last_output = ?, framework = 'crewai' WHERE id = ?",
            (task_output.raw, row["id"]),
        )
        per_agent.append(
            {
                "agent_id": row["id"],
                "role_name": row["role_name"],
                "output": task_output.raw,
            }
        )
    if manager_index is not None:
        manager_row = rows[manager_index]
        per_agent.append(
            {
                "agent_id": manager_row["id"],
                "role_name": manager_row["role_name"],
                "output": None,  # 매니저는 조율·위임만 하고 자기 몫의 산출물은 없다
            }
        )

    audit.record(
        conn,
        actor_id,
        "crew_run_completed",
        "crew_run",
        None,
        after={
            "agent_ids": agent_ids,
            "process": process,
            "delegation_count": len(delegations),
            # CrewAI가 실제로 집계해주는 토큰 사용량 — 참고용 기록일 뿐
            # F10 비용 조회(cost.py)의 cost_estimate 계산에는 아직 연결
            # 안 함(2026-09-27, "CrewAI 개념을 제대로 활용하는지 확인" 요청
            # 으로 확인: cost.py는 수동입력만 쓰는 게 1차 범위로 이미
            # 문서화돼 있다 — 자동 비용 갱신은 그 스코프를 벗어나는 더 큰
            # 변경이라 감사로그에 참고 데이터만 남긴다).
            "token_usage": result.token_usage.model_dump(),
            # 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
            # 연결해줘") — Agent 실행 결과의 Gate A-G도 집계값 성격이라
            # token_usage와 같은 자리(참고 기록)에 남긴다. `agent_evaluations`
            # 테이블은 `deliverables.id` 전용 FK라(협업 워크스페이스 개념)
            # `run_crew()`엔 대응하는 deliverable이 없어 재사용하지 않았다 —
            # 새 테이블을 만드는 대신 이미 있는 "감사로그 참고 기록" 패턴을
            # 그대로 재사용한다(원칙4).
            "gate_scores": eval_result["gate_scores"],
            "tcr": eval_result["tcr"],
        },
    )
    for delegation in delegations:
        audit.record(
            conn,
            actor_id,
            "agent_delegation_occurred",
            "agent_pool",
            None,
            after=delegation,
        )
    for completion in task_completions:
        audit.record(
            conn,
            actor_id,
            "crew_task_completed",
            "crew_run",
            None,
            after=completion,
        )
    conn.commit()

    return {
        "final_output": result.raw,
        "process": process,
        "delegations": delegations,
        "agents": per_agent,
        "gate_scores": eval_result["gate_scores"],
        "tcr": eval_result["tcr"],
        # SCREEN_UX_BENCHMARK_PROPOSAL.md §4-A(2026-10-12) — CrewAI가 이미
        # 집계해주는 값인데 지금까지 audit_log 참고 기록에만 남고 API
        # 응답엔 빠져 있어 화면에 보여줄 수가 없었다. 비용/실행시간은
        # CrewAI가 집계해주지 않아(UsageMetrics에 없음) 여기 포함하지
        # 않는다 — 없는 값을 지어내지 않는다.
        "token_usage": result.token_usage.model_dump(),
    }


def run_crew_for_team(
    conn: sqlite3.Connection,
    team_id: int,
    task_id: int,
    actor_id: int,
    process: str = "sequential",
    provider: str | None = None,
    model: str | None = None,
    manager_agent_id: int | None = None,
    content_type: str = "text",
) -> dict:
    """사용자 요청(2026-10-07, "crew 실행에서 묶인 에이전트 그룹의 경우
    하나의 그룹만 가능한 구조인데, 복수 그룹 가능하도록 개선, 이 그룹이
    전체 업무에서 사람-에이전트 연동이 가능한 구조인지 확인") —
    `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 3. 기존
    `run_crew()`(즉석 실행 — `agent_ids`를 매번 직접 고름, `task_id` 없이
    결과가 `agent_pool.last_output`에만 남고 끝)와 달리, 이 함수는 **팀
    (teams.py, "등록→재사용" 그룹)을 실제 할 일(task)의 맥락에서** 실행한다
    — "복수 그룹"은 한 할 일에 팀을 여러 개 배정할 수 있다는 뜻이다(§61
    "다중 confirmed 허용"으로 이미 가능했다 — team 배정도 resource_type별
    제한 없이 여러 건 confirmed로 공존한다). 이 함수 자체는 그중 **한
    팀**을 실행한다(호출자가 어느 팀인지 고른다 — 사용자 결정, "팀 하나를
    골라서 실행").

    `task_descriptions`는 팀의 Agent 멤버 전원에게 **같은 내용**을 준다
    — 할 일의 `role_name`과 최신 사람 작성 deliverable(있으면)을
    맥락으로 묶은 것으로, `deliverables.start_agent_execution()`의
    단일-Agent 프롬프트와 같은 패턴이다(일관성 유지). CrewAI의
    sequential/hierarchical 프로세스가 그 공통 맥락을 agent들에게 어떻게
    나눠 처리할지는 평소처럼 Crew 자체가 정한다 — 여기서 Agent별로 다른
    지시를 미리 쪼개지 않는다(쪼개려면 팀 멤버의 `role_label`을 더 적극
    활용해야 하는데, 이는 스코프 밖의 더 큰 설계 변경이다).

    사람-에이전트 연동: 이 함수가 반환하는 `agents`(Agent별 산출물)는
    호출자(`deliverables.run_team_crew_job()`)가 각각 `deliverables`
    (author_type='agent') + F7 결재 체인으로 저장한다 — 그 순간부터
    팀 리더·팀원(사람)이 같은 할 일의 협업 워크스페이스에서 결과물을
    보고, 댓글을 달고, 승인/반려할 수 있다. 즉 "즉석 실행"이던 Crew가
    처음으로 업무 파이프라인(F5-F7, 협업 워크스페이스, Gate A-G)에
    연결된다 — 지금까지 `run_crew()`가 완전히 격리돼 있던 것과의 핵심
    차이."""
    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team is None:
        raise ValueError("team_not_found")
    confirmed = conn.execute(
        "SELECT id FROM assignments WHERE task_id = ? AND resource_type = 'team' "
        "AND resource_id = ? AND status = 'confirmed'",
        (task_id, team_id),
    ).fetchone()
    if confirmed is None:
        raise ValueError("team_not_confirmed_for_task")

    agent_ids = teams.active_agent_member_ids(conn, team_id)
    if not agent_ids:
        raise ValueError("team_has_no_active_agents")

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    latest_human = conn.execute(
        "SELECT content FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    required_skills = json.loads(task["required_skills"])
    skills_text = ", ".join(required_skills) if required_skills else "없음"
    desc_parts = [f"'{task['role_name']}' 업무를 수행하세요(필요 스킬: {skills_text})."]
    if latest_human is not None:
        desc_parts.append(f"참고할 사람 작성 자료:\n{latest_human['content']}")
    instruction = _CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")
    if instruction:
        desc_parts.append(instruction)
    description = "\n\n".join(desc_parts)
    task_descriptions = [description] * len(agent_ids)

    return run_crew(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process=process,
        provider=provider,
        model=model,
        manager_agent_id=manager_agent_id,
    )
