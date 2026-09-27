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
"""

from __future__ import annotations

import json
import os
import sqlite3

from hair_platform import audit, llm

DELEGATION_TOOL_NAMES = {"Delegate work to coworker", "Ask question to coworker"}

# ADR 결정 11 한계 섹션이 "반드시 걸어야 한다"고 명시했던 상한(사용자 요청,
# 2026-09-27, "crewai 기능 적절히 적용됐는지 분석" 리뷰에서 실제로 미반영 상태를
# 발견해 반영함). CrewAI Agent 기본값(25)보다 보수적으로 잡는다 — 우리는 Crew
# 전체 Agent에 allow_delegation=True를 켜서 위임 왕복이 겹칠 수 있기 때문이다.
DELEGATION_MAX_ITER = 10


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
    role_name: str,
    required_skills: list[str],
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """F4 생성 시점에 실제 CrewAI Agent+Task+Crew(단일, sequential)를 구성해
    kickoff한다. `jobs.GeneratorFn`과 같은 시그니처라 그대로 갈아끼울 수 있다.
    """
    from crewai import Agent, Crew, Process, Task

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    crew_llm = _crewai_llm(resolved_provider, resolved_model)

    agent = Agent(
        role=role_name,
        goal=_agent_goal(role_name),
        backstory=_agent_backstory(role_name, required_skills),
        llm=crew_llm,
        allow_delegation=False,  # 단일 Agent Crew라 위임할 동료가 없다.
        verbose=False,
    )
    task = Task(
        description=(
            f"'{role_name}' 역할로서 지금 막 팀에 합류했습니다. 이 역할이 "
            "프로젝트에서 처음 맡을 만한 구체적인 산출물을 하나 작성하세요."
        ),
        expected_output="실무에 바로 쓸 수 있는 구체적인 산출물",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
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

    crewai_agents = [
        Agent(
            role=row["role_name"],
            goal=_agent_goal(row["role_name"]),
            backstory=_agent_backstory(row["role_name"], json.loads(row["skill_tags"])),
            llm=crew_llm,
            allow_delegation=True,  # ADR 결정 11 — 정적 릴레이가 아니라 실제 협업
            max_iter=DELEGATION_MAX_ITER,
            verbose=False,
        )
        for row in rows
    ]

    tasks: list[Task] = []
    for i, description in enumerate(task_descriptions):
        context = [tasks[i - 1]] if i > 0 and process == "sequential" else None
        tasks.append(
            Task(
                description=description,
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

    def _capture_delegation(source, event) -> None:
        if event.tool_name in DELEGATION_TOOL_NAMES:
            delegations.append(
                {"from_agent_role": event.agent_role, "tool": event.tool_name}
            )

    with crewai_event_bus.scoped_handlers():
        crewai_event_bus.on(ToolUsageFinishedEvent)(_capture_delegation)
        result = crew.kickoff()

    per_agent = []
    for row, task_output in zip(worker_rows, result.tasks_output, strict=False):
        conn.execute(
            "UPDATE agent_pool SET last_output = ?, llm_provider = ?, llm_model = ?, "
            "framework = 'crewai' WHERE id = ?",
            (task_output.raw, resolved_provider, resolved_model, row["id"]),
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
    }
