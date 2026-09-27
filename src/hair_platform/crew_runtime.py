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
    `manager_agent_id` 또는 매니저 LLM)가 작업을 조율한다. 모든 Agent에
    `allow_delegation=True`를 켜서 실제 동료 위임/질의가 가능하게 한다 —
    실제로 위임이 발동했는지는 CrewAI 이벤트버스에서 캡처해 정직하게
    기록한다(추측 아님).
    """
    if len(agent_ids) != len(task_descriptions):
        raise ValueError("agent_ids와 task_descriptions의 개수가 같아야 합니다")
    if not agent_ids:
        raise ValueError("agent_ids가 비어 있습니다")

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

    crew_kwargs: dict[str, object] = {
        "agents": crewai_agents,
        "tasks": tasks,
        "verbose": False,
    }
    if process == "hierarchical":
        crew_kwargs["process"] = Process.hierarchical
        if manager_agent_id is not None:
            crew_kwargs["manager_agent"] = crewai_agents[
                agent_ids.index(manager_agent_id)
            ]
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
    for row, task_output in zip(rows, result.tasks_output, strict=False):
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
    conn.commit()

    return {
        "final_output": result.raw,
        "process": process,
        "delegations": delegations,
        "agents": per_agent,
    }
