"""계획 분담 단계(`plan_merge`) — 리드가 하위 작업을 계획하고, 팀원이 각자 수행하고, 리드가 합친다.

사용자 요청(2026-11-23, "P2 진행해줘" — P2-9 여러 Agent 협업 방식, 사용자 결정 "새 모드 추가"). 위임형
(CrewAI hierarchical)은 매니저가 실제로 위임하지 않고 계획만 요약하거나 혼자 처리하는 문제가 반복돼
새로 지정할 수 없게 막았고, 그래서 다인원 단계는 사실상 순차 릴레이 하나뿐이었다. 이 모드는 "누가
무엇을 할지"를 모델의 즉흥적인 위임 도구 호출에 맡기지 않고 플랫폼이 세 단계로 직접 진행한다:

1. **계획**: 리드가 팀원별 하위 작업을 JSON으로 낸다(팀원 역할 목록 안에서만). 계획을 읽지 못하면
   단계에 적힌 팀원별 지시로 대신한다(실행이 멈추지 않는다).
2. **분담 실행**: 팀원마다 원 요청 + 자기 하위 작업으로 실행한다. 클라우드 모델(Anthropic·OpenAI)만
   동시에 돌린다 — 로컬 Ollama는 요청을 하나씩 처리해 동시에 보내도 빨라지지 않는다. 동시 실행은
   스레드마다 별도 DB 연결을 쓴다(메모리 DB면 순서대로).
3. **병합**: 리드가 팀원 결과를 하나의 완성된 결과물로 합친다 — 이 결과물이 단계 산출물이고 Gate로
   채점된다.

반환 형식은 `crew_runtime.run_crew()`와 같아(`agents`·`final_output`·`gate_scores`·`tcr`·
`delegations`) 파이프라인 엔진의 이후 판정·재시도·결재 흐름은 그대로 쓴다."""

from __future__ import annotations

import contextvars
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from hair_platform import agent_evaluation, agent_runtime, llm, progress

_CLOUD_PROVIDERS = ("anthropic", "openai")
_MAX_PARALLEL = 3
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _plan_prompt(base: str, workers: list[dict]) -> str:
    team = "\n".join(f"- {w['role_name']}" + (f": {w['instructions']}" if w["instructions"] else "") for w in workers)
    return (
        f"{base}\n\n[분담 계획] 당신은 이 단계의 리드다. 위 요청을 아래 팀원들에게 나눠 맡길 하위 작업을 정한다.\n"
        f"[팀원]\n{team}\n\n"
        '다음 형식의 JSON 배열만 출력한다(다른 말 없이): [{"role": "팀원 역할명", "task": "그 팀원이 할 일 — 범위·산출물을 구체적으로"}]. '
        "역할명은 위 팀원 목록의 이름을 그대로 쓰고, 팀원마다 작업은 하나만, 리드 자신의 작업은 넣지 않는다. "
        "작업끼리 겹치지 않게 나누고, 합치면 요청 전체가 빠짐없이 덮이게 한다."
    )


def parse_plan(text: str, worker_roles: list[str]) -> list[dict]:
    """리드의 계획에서 팀원 역할 목록에 있는 작업만 뽑는다(역할당 하나, 계획에 나온 순서)."""
    m = _JSON_ARRAY_RE.search(text or "")
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    plan, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        role, task = str(item.get("role", "")).strip(), str(item.get("task", "")).strip()
        if role in worker_roles and role not in seen and task:
            plan.append({"role": role, "task": task})
            seen.add(role)
    return plan


def _db_path(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    return path or None  # 메모리 DB는 빈 문자열


def _is_cloud(conn: sqlite3.Connection, agent_id: int, provider: str | None, model: str | None) -> bool:
    row = conn.execute("SELECT llm_provider, llm_model FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
    try:
        resolved, _m = llm.resolve_provider_and_model(provider or row["llm_provider"], model or row["llm_model"])
    except Exception:  # noqa: BLE001 — 판단 못 하면 순서대로
        return False
    return resolved in _CLOUD_PROVIDERS


def run(
    conn: sqlite3.Connection,
    lead: dict,
    workers: list[dict],
    base_description: str,
    actor_id: int,
    *,
    expected_output: str = "",
    provider: str | None = None,
    model: str | None = None,
    eval_task_type: str = agent_evaluation.DEFAULT_EVAL_TASK_TYPE,
) -> dict:
    """`lead`/`workers`: {"agent_id", "role_name", "instructions"}."""
    expect = f"\n\n[기대 산출물 형식] {expected_output}" if expected_output else ""
    progress.report(f"분담 계획 수립 중 — {lead['role_name']}")
    plan_text = agent_runtime.execute_agent(conn, lead["agent_id"], actor_id,
                                            _plan_prompt(base_description, workers), provider, model)
    plan = parse_plan(plan_text, [w["role_name"] for w in workers])
    plan_source = "lead_plan"
    if not plan:
        # 계획을 읽지 못하면 단계에 적힌 팀원별 지시(없으면 원 요청 전체)로 대신한다.
        plan = [{"role": w["role_name"], "task": w["instructions"] or "위 요청 중 당신의 역할에 해당하는 부분"} for w in workers]
        plan_source = "stage_instructions"
    # 리드가 빠뜨린 팀원에게 기본 하위 작업(2026-11-25, P1-11 — P2-9 실측: PM이 "외부 자료 조사가"에게 일을 주지 않고 그 몫을
    # 병합 때 직접 채웠다). 단계에 넣은 역할은 모두 기여하게 한다 — 그 팀원의 단계 지시(없으면 역할에 해당하는 부분)를 맡긴다.
    lead_assigned = [item["role"] for item in plan]
    auto_assigned = [w["role_name"] for w in workers if w["role_name"] not in set(lead_assigned)]
    for w in workers:
        if w["role_name"] in auto_assigned:
            plan.append({"role": w["role_name"], "auto": True,
                         "task": w["instructions"] or "위 요청 중 당신의 역할에 해당하는 부분"})
    by_role = {w["role_name"]: w for w in workers}

    def _work(item: dict, own_conn: sqlite3.Connection) -> dict:
        worker = by_role[item["role"]]
        prompt = (f"{base_description}\n\n[당신의 하위 작업 — 리드 {lead['role_name']}가 배정]\n{item['task']}\n\n"
                  "이 하위 작업의 결과물만 완성해 낸다(다른 팀원 몫은 쓰지 않는다).")
        output = agent_runtime.execute_agent(own_conn, worker["agent_id"], actor_id, prompt, provider, model)
        return {"agent_id": worker["agent_id"], "role_name": worker["role_name"], "task": item["task"],
                "output": output, "stage": "final"}

    path = _db_path(conn)
    parallel = (
        len(plan) > 1 and path is not None
        and all(_is_cloud(conn, by_role[i["role"]]["agent_id"], provider, model) for i in plan)
    )
    if parallel:
        from hair_platform import db

        progress.report(f"하위 작업 {len(plan)}건 동시 실행 중")

        assert path is not None

        def _work_own_conn(item: dict) -> dict:
            # 작업 스레드는 contextvar를 물려받지 않는다 — 호출한 쪽 맥락(사용량의 업무·실행 귀속)을
            # 복사해 그 안에서 돌린다. 진행 표시는 끈다(여러 스레드가 주 연결로 동시에 쓰지 않게 —
            # 위에서 "동시 실행 중"으로 한 번 알렸다).
            own = db.get_connection(path)
            try:
                progress._REPORTER.set(None)
                progress._PARTIAL.set(None)  # 작성 중 미리보기도 끈다(2026-11-24, P0-2)
                return _work(item, own)
            finally:
                own.close()

        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL, len(plan))) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _work_own_conn, item) for item in plan]
            results = [f.result() for f in futures]
    else:
        results = []
        for item in plan:
            progress.report(f"하위 작업 실행 중 — {item['role']}")
            results.append(_work(item, conn))

    parts = "\n\n".join(f"### {r['role_name']} — {r['task']}\n\n{r['output']}" for r in results)
    merge_prompt = (
        f"{base_description}\n\n[팀원 결과]\n{parts}\n\n"
        "[병합] 당신은 리드다. 위 팀원 결과를 요청 전체에 답하는 **하나의 완성된 결과물**로 합친다 — 겹치는 내용은 "
        "하나로 정리하고, 서로 어긋나는 수치·주장은 근거가 있는 쪽으로 맞추고, 빠진 부분이 있으면 직접 채운다. "
        "팀원별 결과를 나열하지 말고 최종 결과물만 출력한다." + expect
    )
    progress.report(f"결과 병합 중 — {lead['role_name']}")
    scored = agent_evaluation.run_and_score(
        lead["role_name"],
        lambda: agent_runtime.execute_agent(conn, lead["agent_id"], actor_id, merge_prompt, provider, model),
        task_type=eval_task_type,
        long_running=True,  # 단계 전체(계획·팀원·병합)를 대표하는 채점 — 응답 시간 SLA로 못 잰다
    )
    merged = scored["output"]
    agents = [*results, {"agent_id": lead["agent_id"], "role_name": lead["role_name"], "output": merged, "stage": "final"}]
    # 배정 확인(2026-11-25 P2-9 실측) — 리드가 일을 주지 않은 팀원은 위에서 기본 하위 작업을 받았다(P1-11). 참고용 배지
    # (advisory — 통과 판정과 무관)에 리드가 직접 나눈 팀원과 자동 배정된 팀원을 함께 남긴다.
    unassigned = [w["role_name"] for w in workers if w["role_name"] not in {item["role"] for item in plan}]
    gate_scores = dict(scored["gate_scores"])
    gate_scores["plan_coverage"] = {
        "score": round(len(set(lead_assigned)) / len(workers), 3) if workers else None,
        "status": "warn" if (auto_assigned and plan_source == "lead_plan") or unassigned else "pass",
        "passed": True,
        "advisory": True,
        "not_measured": not workers,
        "assigned": lead_assigned,
        "auto_assigned": auto_assigned if plan_source == "lead_plan" else [],
        "unassigned": unassigned,
        "plan_source": plan_source,
    }
    return {
        "agents": agents,
        "final_output": merged,
        "gate_scores": gate_scores,
        "tcr": scored["tcr"],
        "delegations": [{"tool": "plan_merge", "from": lead["role_name"], "to": r["role_name"], "task": r["task"]}
                        for r in results],
        "plan_source": plan_source,
        "plan_unassigned": unassigned,
        "parallel": parallel,
        # 병합본 수치의 근거 — 팀원 결과(각자 도구 결과로 수치 검사를 이미 거침, `execute_agent`).
        "tool_evidence": "\n\n".join(f"[팀원 {r['role_name']} 결과]\n{r['output']}" for r in results),
    }
