"""파이프라인 단계 실행기 — 두 오케스트레이션 엔진 공통(2026-11-25, P1-7 "실행 엔진 정리" 1단계).

예전에는 기본 엔진(`pipeline_runtime`)과 그래프 엔진(`execution_graph`)이 각자 `crew_runtime.run_crew()`를 불렀다. 그래서
같은 Agent가 대화(직접 호출 `agent_runtime.execute_agent`)와 파이프라인 단계(CrewAI)에서 서로 다른 실행 경로를 탔다.
이 모듈이 단계의 참여 Agent 실행을 한곳에서 맡고, 실행 방식을 고른다.

- `crewai`(기본 — 지금까지와 같음): `crew_runtime.run_crew()`.
- `direct`: 단일(`single`)·순차 릴레이(`sequential_relay`) 단계를 직접 호출로 돈다 — 단일은 한 번, 순차 릴레이는 앞사람
  결과를 다음 사람에게 넘기며 차례로(CrewAI sequential이 앞 Task 출력을 맥락으로 넘기는 것과 같은 구조). 대화와 같은
  실행기라 작성 중 미리보기·끊긴 응답 이어 쓰기·출력 정리·수치 교정·지식 검색 공개 범위·AI 사용 한도가 똑같이 적용되고,
  crewai 버전에 따라 동작이 달라지지 않는다. CrewAI에만 있던 두 가지를 맞춘다: 기대 산출물 형식을 지시에 넣고, 빈 결과면
  한 번 다시 시킨다(CrewAI guardrail과 같은 바닥선).
  계획 분담(`plan_merge`)은 이미 직접 호출이고, 병행 위임(`parallel_delegated`)은 CrewAI hierarchical이 필요해 늘 `crewai`.

고르는 방법: 환경변수 `HAIR_STAGE_RUNTIME`(crewai|direct, 기본 crewai). 실측(P1-7 2단계)으로 기본값을 정한다.
반환 형식은 `run_crew()`와 같다(`agents`·`final_output`·`gate_scores`·`tcr`·`delegations`·`tool_evidence`).
"""

from __future__ import annotations

import os
import sqlite3

from hair_platform import agent_evaluation, agent_runtime, crew_runtime

RUNTIMES = ("crewai", "direct")
DIRECT_MODES = ("single", "sequential_relay")
_EMPTY_RETRY_NOTE = (
    "\n\n[다시 작성] 직전 시도의 결과가 비어 있었습니다. 위 요청에 대한 결과물 본문을 빠짐없이 작성하세요."
)


def runtime_for(stage) -> str:
    """이 단계를 어떤 방식으로 실행할지."""
    choice = os.environ.get("HAIR_STAGE_RUNTIME", "crewai").strip().lower()
    if choice not in RUNTIMES:
        choice = "crewai"
    if choice == "direct" and stage["mode"] not in DIRECT_MODES:
        return "crewai"
    return choice


def run_stage_agents(
    conn: sqlite3.Connection,
    stage,
    agent_ids: list[int],
    task_descriptions: list[str],
    actor_id: int,
    *,
    process: str = "sequential",
    provider: str | None = None,
    model: str | None = None,
    manager_agent_id: int | None = None,
    expected_outputs: list[str] | None = None,
    self_review: bool = False,
    eval_task_type: str = agent_evaluation.DEFAULT_EVAL_TASK_TYPE,
) -> dict:
    """단계의 참여 Agent를 실행한다(계획 분담은 호출자가 `plan_merge.run()`으로 따로 돈다)."""
    if runtime_for(stage) == "direct":
        return _run_direct(conn, stage, agent_ids, task_descriptions, actor_id, provider=provider, model=model,
                           expected_outputs=expected_outputs, eval_task_type=eval_task_type)
    return crew_runtime.run_crew(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process=process,
        # 단계 간 출력은 base_input으로 직접 이어 붙이므로 CrewAI 자체 메모리는 끈다(숨은 OpenAI 임베딩 의존 — 2026-11-06 실측).
        memory=False,
        eval_task_type=eval_task_type,
        provider=provider,
        model=model,
        manager_agent_id=manager_agent_id,
        expected_outputs=expected_outputs,
        self_review=self_review,
    )


def _role(conn: sqlite3.Connection, agent_id: int) -> str:
    row = conn.execute("SELECT role_name FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    return row["role_name"] if row else f"agent#{agent_id}"


def _expected_output_text(conn: sqlite3.Connection, agent_id: int, stage_expected: str | None) -> str:
    """기대 산출물 — 단계 값 → Agent 저장값 순(CrewAI 경로 `run_crew()`의 폴백과 같은 우선순위)."""
    if stage_expected and stage_expected.strip():
        return stage_expected.strip()
    row = conn.execute("SELECT expected_output FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    return (row["expected_output"] or "").strip() if row else ""


def _call_agent(conn, agent_id: int, actor_id: int, prompt: str, provider, model, evidence: list[str]) -> str:
    """직접 호출 1회 + 빈 결과면 한 번 더(CrewAI guardrail과 같은 바닥선). 도구 결과는 `evidence`에 모은다."""
    output = ""
    for attempt in range(2):
        sink: dict = {}
        output = agent_runtime.execute_agent(
            conn, agent_id, actor_id, prompt if attempt == 0 else prompt + _EMPTY_RETRY_NOTE, provider, model,
            retrieval_sink=sink,
        )
        evidence.extend(sink.get("tool_results") or [])
        if (output or "").strip():
            return output
    return output


def _run_direct(conn, stage, agent_ids, task_descriptions, actor_id, *, provider, model, expected_outputs,
                eval_task_type) -> dict:
    expected_outputs = expected_outputs or [""] * len(agent_ids)
    evidence: list[str] = []
    agents: list[dict] = []

    def _chain() -> str:
        previous: list[tuple[str, str]] = []
        for i, agent_id in enumerate(agent_ids):
            role = _role(conn, agent_id)
            prompt = task_descriptions[i] if i < len(task_descriptions) else task_descriptions[-1]
            if previous:  # 순차 릴레이 — 앞사람 결과를 이어받는다
                prompt += "\n\n[앞 참여자 결과 — 이어받아 당신 몫을 작성]\n" + "\n\n".join(
                    f"### {r}\n{o}" for r, o in previous
                )
            expected = _expected_output_text(conn, agent_id, expected_outputs[i] if i < len(expected_outputs) else "")
            if expected:
                prompt += f"\n\n[기대 산출물 형식] {expected}"
            output = _call_agent(conn, agent_id, actor_id, prompt, provider, model, evidence)
            agents.append({"agent_id": agent_id, "role_name": role, "name": None, "output": output, "stage": "final"})
            previous.append((role, output))
        return agents[-1]["output"] if agents else ""

    label = _role(conn, agent_ids[0]) if len(agent_ids) == 1 else f"{stage['name']}(순차 {len(agent_ids)}명)"
    # 단일은 호출 1회라 응답 시간(C·D)을 잰다. 순차 릴레이는 여러 호출이라 CrewAI 경로와 같이 C·D를 재지 않는다.
    scored = agent_evaluation.run_and_score(label, _chain, task_type=eval_task_type, long_running=len(agent_ids) > 1)
    return {
        "agents": agents,
        "final_output": scored["output"],
        "gate_scores": scored["gate_scores"],
        "tcr": scored["tcr"],
        "delegations": [],
        "tool_evidence": "\n\n".join(evidence),
        "runtime": "direct",
    }
