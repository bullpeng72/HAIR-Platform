"""LangGraph 기반 파이프라인 실행 엔진 — 사용자 요청(2026-11-18, "대화,
업무 배정에서 skill, tool, agent, 파이프라인 등을 공고하게 실행시키고...
에이전틱 실행엔진 개념을 도입하려고 해" → 조사+제안 → "langgraph +
langgraph-checkpoint-sqlite 신규 의존성 추가 방향으로 설계를 구체화하고
진행해줘") 1단계 구현.

**왜 LangGraph 코어만 채택했는지** — `pipeline_runtime.py`의 단계별
실행은 사실상 수작업으로 짠 상태 머신(재시도/에스컬레이션을 if문으로
직접 구현)인데, 체크포인트가 전혀 없어 서버가 죽으면(이번 세션에만
`conversation_jobs`가 "running"에 40분 넘게 멈춘 사고가 2번 있었음—
같은 범주의 문제가 파이프라인에도 있다) 그 실행은 영원히 고아가
된다. LangGraph의 그래프+조건부 엣지+SQLite 체크포인터는 바로 이
문제(상태 머신 자체의 신뢰성)를 해결하는 전용 도구라 코어만 가져왔다
— LangChain의 LLM 래퍼/에이전트 추상화는 전혀 안 쓴다. 실제 LLM/Crew
호출은 기존 `crew_runtime.run_crew()`를 그대로 재사용한다(중복
구현 없음).

**범위(2단계, 2026-11-18 "진행해줘") — 기능 폭을 넓혔다**:
- `single`/`sequential_relay` 모드(여전히 `parallel_delegated`는 제외 —
  이유는 1단계와 동일, 외부 조사로 확인한 CrewAI hierarchical 모드의
  신뢰성 문제).
- `self_review` 티어(마지막 자동 재시도에서 자가검토), Task 전체
  `max_retry_budget`(`dashboard.task_retry_summary()` 재사용), 실행 중
  `cancel_requested` 확인, 사람 반려 사유(`decision_reason`) 재주입.
- **읽기 쪽 호환성** — 기존 `deliverables`/`agent_evaluations`/
  `approvals`/`pipeline_runs.current_stage_seq`를 전부 기존과 똑같이
  기록한다. 그래서 Task 협업 워크스페이스·결재함(`GET /api/approvals`)
  ·파이프라인 진행률 화면이 이 엔진으로 실행된 결과도 **수정 없이**
  그대로 보여준다.
- **아직 제외한 것**: `requirements_traceability`/`delegation_check`
  Gate(parallel_delegated 전용이라 모드 자체가 빠지면서 자연히 범위 밖).

**3단계(2026-11-18 "다음 개선 진행해줘") — 실제 라이브 엔드포인트 연결**:
- `pipeline_runs.engine`(기본 `'legacy'`) — `run_pipeline_graph()`가
  시작하는 즉시 `'graph'`로 표시한다. `app.py`의 `decide_approval()`은
  `subject_type == 'pipeline_run_stage'`를 처리할 때 이 플래그로
  분기한다 — `'graph'`면 `resume_pipeline_graph_from_approval()`을
  불러 실제로 `interrupt()`를 재개시키고, 아니면 기존 `pipeline_
  runtime.resolve_stage_decision()`(DB 컬럼을 직접 조작하는 상태
  머신 전제)을 그대로 쓴다 — 두 "진실의 원천"을 섞어 호출하면 충돌
  하므로 반드시 분기해야 한다.
- `POST /api/tasks/{task_id}/execute-pipeline`의 `use_graph_engine:
  bool`(기본 `False`, `models.PipelineExecuteRequest`) — admin/manager
  가 opt-in으로 이 엔진을 켤 수 있는 실제 진입점. 템플릿이
  `parallel_delegated` 단계를 포함하면 run 생성 직후 즉시 취소하고
  400을 돌린다(고아 run을 안 남김).
- **체크포인터 생애주기(실측으로 잡은 버그)** — `:memory:` DB(개발/
  테스트 기본값)에서 매 호출마다 새 `SqliteSaver(":memory:")`를 열면
  SQLite의 `:memory:`는 커넥션마다 완전히 분리된 별개 DB라, 시작
  시점에 멈춘 체크포인트를 재개 시점이 영원히 못 찾는다(파일 기반은
  같은 파일이라 원래부터 문제없었음). `get_checkpointer(conn)`이
  메인 `conn` 인스턴스당 체크포인터를 하나만 열어 캐시·재사용한다
  (`app.state.db`가 메인 커넥션 하나를 공유하는 것과 같은 원리) —
  `run_pipeline_graph_job()`/`resume_pipeline_graph_from_approval()`
  둘 다 이 함수를 통해서만 체크포인터를 얻는다. `langgraph` 관련
  import는 전부 함수 안에서 지연 로드한다(`graph` extra가 선택적
  의존성이라 — 그래프 엔진 run이 하나도 없는 배포에서는 이 import
  자체가 안 일어나야 한다).

**검증된 핵심 가치**: 서버 프로세스를 완전히 종료하고 재시작해도
(`SqliteSaver`가 파일에 체크포인트를 남김) 중단된 지점부터 정확히
재개된다 — `tests/unit/test_execution_graph.py`의 크래시-재개 테스트가
실제로 커넥션을 닫고 새로 열어 이를 재현·확인한다. 실제 HTTP
엔드포인트(`execute-pipeline` → Gate 실패로 에스컬레이션 → 실제
`POST /api/approvals/{id}/decide`로 승인 → 완료)로 이어지는 전체
경로도 `test_live_trigger_and_approval_decide_bridges_to_graph_engine`
으로 종단 검증했다."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from hair_platform import (
    agent_evaluation,
    approvals,
    audit,
    crew_runtime,
    dashboard,
    deliverables,
    presentation_deck,
)
from hair_platform import llm_usage  # 업무 맥락별 사용량(2026-11-23, P2-11)
from hair_platform import pipeline_runtime as pr


class PipelineGraphState(TypedDict):
    run_id: int
    task_id: int
    actor_id: int
    pipeline_template_id: int
    seq_orders: list[int]
    seq_index: int
    base_input: str
    retry_feedback: str
    attempt: int
    gate_scores: dict | None
    stage_output: str | None
    passed: bool | None
    needs_human: bool | None
    # 버그 수정(구현 직후 자체 테스트 중 발견) — `_node_run_stage()`가
    # 반환 dict에 이 3개 키를 넣어뒀는데 TypedDict 스키마에 선언을
    # 안 해뒀더니, LangGraph가 노드 간 상태 전달(채널 병합)에서 이
    # 키들을 안정적으로 안 넘겨 재시도 라우팅(`_route_after_stage()`가
    # `state["_stage_max_retry"]`를 읽음)이 조용히 틀리게 동작했다
    # (재시도 없이 곧장 다음 단계로 넘어감 — 실제 재현해 잡음). 노드가
    # 반환하는 모든 키는 반드시 TypedDict에 선언돼 있어야 한다는 게
    # 이번에 확인한 핵심 교훈.
    stage_max_retry: int | None
    stage_row_id: int | None
    stage_name: str | None
    # 2단계(2026-11-18, "진행해줘") 추가 — 실행 중 취소 여부(사람이
    # `pipeline_runs.cancel_requested`를 세운 경우)와, Task 전체
    # `max_retry_budget` 소진 여부(단계 내 `max_retry`가 남아 있어도
    # 재시도를 막아야 하는지) — 둘 다 라우팅 결정에 쓰여 TypedDict에
    # 선언해야 한다(위 교훈 참고).
    cancelled: bool | None
    budget_exceeded: bool | None


def validate_template_supported(conn: sqlite3.Connection, template_id: int) -> None:
    """지원 안 하는 모드(`parallel_delegated`)가 섞여 있으면 조용히
    다르게 동작하는 대신 바로 거부한다 — 그런 단계는 기존
    `pipeline_runtime.run_pipeline_stage_job()` 경로를 그대로 쓰도록
    안내한다."""
    rows = conn.execute(
        "SELECT name, mode FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND mode NOT IN ('single', 'sequential_relay', 'plan_merge')",
        (template_id,),
    ).fetchall()
    if rows:
        names = ", ".join(f"{r['name']}({r['mode']})" for r in rows)
        raise ValueError(
            f"unsupported_stage_mode_for_graph_engine: {names} — 이 엔진은 "
            "parallel_delegated(hierarchical)를 아직 지원하지 않습니다. "
            "기존 pipeline_runtime.run_pipeline_stage_job()을 쓰세요."
        )


def _ordered_seq_orders(conn: sqlite3.Connection, template_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT seq_order FROM pipeline_stages WHERE pipeline_template_id = ? "
        "ORDER BY seq_order",
        (template_id,),
    ).fetchall()
    return [r["seq_order"] for r in rows]


def _node_run_stage(state: PipelineGraphState, config) -> dict:
    conn: sqlite3.Connection = config["configurable"]["conn"]
    run_id = state["run_id"]

    # 2단계(2026-11-18, "진행해줘") — 실행 중 취소 확인. `pipeline_
    # runtime.py`와 동일하게, 이미 시작된 Crew 호출 자체를 끊지는
    # 않고(더 큰 작업) 비용이 드는 다음 호출 직전(단계 시작 시점)
    # 에서만 멈춘다.
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run["cancel_requested"]:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'cancelled', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        audit.record(
            conn, state["actor_id"], "pipeline_run_cancelled", "pipeline_runs", run_id
        )
        conn.commit()
        return {"cancelled": True}

    stage = conn.execute(
        "SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (state["pipeline_template_id"], state["seq_orders"][state["seq_index"]]),
    ).fetchone()

    stage_agents = conn.execute(
        "SELECT * FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order, id",
        (stage["id"],),
    ).fetchall()
    agent_ids = [pr._resolve_role_agent_id(conn, a["role_name"]) for a in stage_agents]

    stage_instructions = stage["instructions"] + state["retry_feedback"]
    combined_base = (
        f"{state['base_input']}\n\n{stage_instructions}"
        if stage_instructions
        else state["base_input"]
    )
    if stage["mode"] == "sequential_relay":
        task_descriptions = [
            combined_base
            + (
                f"\n\n[이 단계에서 당신의 구체적 역할] {a['instructions']}"
                if a["instructions"]
                else ""
            )
            for a in stage_agents
        ]
    else:  # single
        task_descriptions = [combined_base]

    # 2단계 — self_review 티어. "이 단계의 마지막 자동 재시도"일 때만
    # 켠다 — 일반 재시도보다 한 단계 더 꼼꼼한 자가검토를 사람에게
    # 올리기 전 마지막으로 시도한다(`pipeline_runtime.py`의 로드맵
    # 6번과 동일한 설계). 이 엔진은 parallel_delegated를 이미 제외해
    # process가 항상 "sequential"이라, 원본의 구조적 비호환 가드가
    # 필요 없다(항상 호환).
    use_self_review = state["attempt"] == stage["max_retry"]
    if use_self_review:
        last_try_notice = (
            "\n\n[마지막 자동 재시도] 이번이 자동 재시도의 마지막 기회입니다 — "
            "스스로 작성한 초안을 원 요구사항과 한 줄씩 대조해 빠지거나 "
            "깨진 부분을 반드시 수정하세요. 이번에도 기준을 충족하지 못하면 "
            "사람에게 결재 요청이 올라갑니다."
        )
        task_descriptions = [d + last_try_notice for d in task_descriptions]

    expected_outputs = [stage["expected_output"]] * len(agent_ids)
    stage_provider, stage_model = pr._resolve_stage_llm(conn, stage)

    # 진행 표시(2026-11-24, P0-2 파이프라인 진행률) — 단계 시작 시각·지금 하는 일·작성 중 미리보기를
    # 실행 행에 남긴다(`stage_progress`).
    with pr.stage_progress(conn, state["run_id"], stage):
        if stage["mode"] == "plan_merge":
            # 계획 분담(2026-11-23, P2-9) — 기존 엔진과 같은 `plan_merge.run()`. 병합본이 단계 산출물.
            from hair_platform import plan_merge

            lead_name = stage["lead_role_name"]
            result = plan_merge.run(
                conn,
                {"agent_id": pr._resolve_role_agent_id(conn, lead_name), "role_name": lead_name, "instructions": ""},
                [{"agent_id": aid, "role_name": a["role_name"], "instructions": a["instructions"]}
                 for a, aid in zip(stage_agents, agent_ids) if a["role_name"] != lead_name],
                combined_base,
                state["actor_id"],
                expected_output=stage["expected_output"],
                provider=stage_provider,
                model=stage_model,
                eval_task_type=agent_evaluation.eval_task_type_for(stage["output_content_type"]),
            )
        else:
            result = crew_runtime.run_crew(
                conn,
                agent_ids,
                task_descriptions,
                state["actor_id"],
                process="sequential",
                memory=False,
                provider=stage_provider,
                model=stage_model,
                expected_outputs=expected_outputs,
                self_review=use_self_review,
                eval_task_type=agent_evaluation.eval_task_type_for(stage["output_content_type"]),
            )

    final_entries = [
        a for a in result["agents"] if a.get("stage", "final") == "final"
    ] or result["agents"]
    if stage["mode"] == "sequential_relay" and len(final_entries) > 1:
        stage_output = "\n\n---\n\n".join(
            f"### {a['role_name']}\n\n{a['output']}" for a in final_entries
        )
    elif final_entries:
        stage_output = final_entries[-1]["output"]
    else:
        stage_output = result["final_output"]
    # 언어 품질 교정 — classic 엔진과 같은 함수(`pr.repair_stage_language`).
    stage_output = pr.repair_stage_language(
        conn, stage_output, stage_provider, stage_model, state["actor_id"]
    )
    author_agent_id = (
        final_entries[-1]["agent_id"] if final_entries else agent_ids[-1]
    )

    gate_scores = dict(result["gate_scores"])
    if stage["is_primary_deliverable"]:
        gate_scores["code_syntax"] = agent_evaluation.compute_code_syntax_check(
            stage_output
        )
    # 실행 가능성 Gate(2026-11-22 C안) — 단계 gate_ids에 있을 때만 잰다(SW 개발 "구현").
    if "deliverable_runnable" in json.loads(stage["gate_ids"] or "[]"):
        gate_scores["deliverable_runnable"] = agent_evaluation.compute_deliverable_runnable(
            stage_output
        )
    # 수치 근거·테스트 실제 실행(2026-11-23) — 기존 엔진과 같은 검사(`attach_quality_checks`).
    # 근거 = 브리프·앞 단계 산출물 + 이번 단계의 도구 결과(2026-11-24 — 계산기 결과 수치 오탐 방지).
    agent_evaluation.attach_quality_checks(
        gate_scores, stage_output, "\n\n".join(filter(None, [combined_base, result.get("tool_evidence")]))
    )
    # 요구사항 추적성·단계 gate_ids 반영(2026-11-21) — 기존 엔진과 같은 판정.
    traceability = pr.requirements_traceability_score(
        conn, state["run_id"], stage, stage_output
    )
    if traceability is not None:
        gate_scores["requirements_traceability"] = traceability
    # 검토 판정 반영(2026-11-25) — 기존 엔진과 같은 판정(`pr.review_verdict`).
    verdict = pr.review_verdict(stage_output) if stage["on_fail_return_stage_id"] else None
    if verdict is not None:
        gate_scores["review_verdict"] = verdict
    stage_gate_ids = json.loads(stage["gate_ids"]) if stage["gate_ids"] else None
    ratio = pr._gate_pass_ratio(gate_scores, stage_gate_ids)
    passed = ratio >= stage["min_gate_pass_ratio"]
    if verdict is not None and not verdict["not_measured"] and not verdict["passed"]:
        passed = False

    task_row = conn.execute(
        "SELECT max_retry_budget, requires_human_judgment FROM tasks WHERE id = ?",
        (state["task_id"],),
    ).fetchone()
    needs_human_on_pass = bool(
        stage["requires_human_approval"]
        or (task_row is not None and task_row["requires_human_judgment"])
    )

    attempt = state["attempt"]
    if passed and not needs_human_on_pass:
        row_status = "passed"
    elif passed and needs_human_on_pass:
        row_status = "running"  # 아래에서 승인 대기로 바뀜
    elif attempt < stage["max_retry"]:
        row_status = "failed_retrying"
    else:
        row_status = "escalated"

    cur = conn.execute(
        "INSERT INTO pipeline_run_stages "
        "(pipeline_run_id, stage_id, attempt, status, output, gate_scores, tcr) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            stage["id"],
            attempt,
            row_status,
            stage_output,
            json.dumps(gate_scores, ensure_ascii=False),
            result["tcr"],
        ),
    )
    stage_row_id = cur.lastrowid
    assert stage_row_id is not None

    # 2단계 — Task 전체 재시도 예산(`pipeline_runtime.py`의 로드맵
    # 9번과 동일). `dashboard.task_retry_summary()`는 방금 위에서
    # 커밋 전 INSERT한 이 시도 자신의 행도 같은 커넥션·같은(아직
    # 커밋 전) 트랜잭션 안에서 바로 보인다 — 그래서 "이번 시도까지
    # 포함한 총 재시도 수"가 예산을 넘겼으면, 단계 내 `max_retry`가
    # 아직 남아 있어도(`attempt < max_retry`) 재시도 대신 바로
    # 에스컬레이션으로 덮어쓴다(원본과 동일한 순서 — 원본도 INSERT
    # 먼저, 예산 체크는 그다음).
    budget_exceeded = False
    if (
        row_status == "failed_retrying"
        and task_row is not None
        and task_row["max_retry_budget"] is not None
    ):
        retries_so_far = dashboard.task_retry_summary(conn, state["task_id"])[
            "total_retries"
        ]
        budget_exceeded = retries_so_far >= task_row["max_retry_budget"]
        if budget_exceeded:
            row_status = "escalated"
            conn.execute(
                "UPDATE pipeline_run_stages SET status = 'escalated' WHERE id = ?",
                (stage_row_id,),
            )
    conn.commit()

    # 2단계 — 읽기 쪽 호환성: 통과한 단계는 기존과 똑같이 `deliverables`
    # 행을 남긴다(Task 협업 워크스페이스가 이걸로 결과를 보여줌).
    # 승인이 필요 없으면 통과 자체가 승인('approved'), 필요하면
    # 'pending_review'로 올라간다 — `pipeline_runtime.py`와 동일.
    deliverable_id = None
    if passed:
        deliverable_status = "pending_review" if needs_human_on_pass else "approved"
        cur2 = conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'agent', ?, ?, ?, ?)",
            (
                state["task_id"],
                author_agent_id,
                # HAIR 덱(2026-11-20) — `pipeline_runtime`과 동일.
                *presentation_deck.finalize_with_type(
                    stage_output, stage["output_content_type"]
                ),
                deliverable_status,
            ),
        )
        deliverable_id = cur2.lastrowid
        assert deliverable_id is not None
        agent_evaluation.record_evaluation(conn, deliverable_id, gate_scores, result["tcr"])
        conn.execute(
            "UPDATE pipeline_run_stages SET deliverable_id = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            (deliverable_id, stage_row_id),
        )
        conn.commit()

    # 2단계 — 읽기 쪽 호환성: 결재함(`GET /api/approvals`)이 실제로
    # 보여주려면 진짜 `approvals` 행이 있어야 한다. 에스컬레이션과
    # "통과했지만 승인 필요" 둘 다 같은 체인 생성 함수를 쓴다(원본과
    # 동일). 주의 — 이 엔진의 재개는 아직 `resume_pipeline_graph()`를
    # 직접 호출해야 한다(결재함의 승인/반려 버튼이 이 그래프의
    # `interrupt()`를 직접 재개시키는 다리는 다음 단계 — 모듈
    # docstring 참고).
    if row_status == "escalated":
        approvals.create_approval_chain(
            conn, state["actor_id"], "pipeline_run_stage", stage_row_id
        )
        audit.record(
            conn,
            state["actor_id"],
            "pipeline_stage_escalated",
            "pipeline_run_stage",
            stage_row_id,
            after={
                "attempt": attempt,
                "gate_scores": gate_scores,
                "retry_budget_exceeded": budget_exceeded,
            },
        )
        conn.commit()
    elif row_status == "running":
        approvals.create_approval_chain(
            conn, state["actor_id"], "pipeline_run_stage", stage_row_id
        )
        audit.record(
            conn,
            state["actor_id"],
            "pipeline_stage_awaiting_approval",
            "pipeline_run_stage",
            stage_row_id,
            after={"deliverable_id": deliverable_id},
        )
        conn.commit()

    return {
        "stage_output": stage_output,
        "gate_scores": gate_scores,
        "passed": passed,
        "needs_human": needs_human_on_pass,
        "attempt": attempt,
        "retry_feedback": "",
        "stage_row_id": stage_row_id,
        "stage_max_retry": stage["max_retry"],
        "stage_name": stage["name"],
        "cancelled": False,
        "budget_exceeded": budget_exceeded,
    }  # type: ignore[return-value]


def _route_after_stage(
    state: PipelineGraphState,
) -> Literal["cancelled", "retry", "human_approval", "advance"]:
    if state.get("cancelled"):
        return "cancelled"
    if state["passed"] and not state["needs_human"]:
        return "advance"
    if state["passed"] and state["needs_human"]:
        return "human_approval"
    if state["attempt"] < state["stage_max_retry"] and not state.get(  # type: ignore[operator]
        "budget_exceeded"
    ):
        return "retry"
    return "human_approval"


def _next_attempt(conn: sqlite3.Connection, run_id: int, stage_id: int) -> int:
    """이 실행에서 그 단계의 다음 시도 번호 — DB 기록 기준(2026-11-22). 상태값으로만 세면 검토
    실패 후 앞 단계로 돌아갔다 다시 올 때 1로 초기화돼, 검토 재시도 한도가 무의미해진다."""
    return (
        conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) FROM pipeline_run_stages "
            "WHERE pipeline_run_id = ? AND stage_id = ?",
            (run_id, stage_id),
        ).fetchone()[0]
        + 1
    )


def _node_prepare_retry(state: PipelineGraphState, config) -> dict:
    conn: sqlite3.Connection = config["configurable"]["conn"]
    stage = conn.execute(
        "SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (state["pipeline_template_id"], state["seq_orders"][state["seq_index"]]),
    ).fetchone()
    target = pr.return_target_stage(conn, stage) if stage is not None else None
    if target is not None and target["seq_order"] in state["seq_orders"]:
        # 검토 실패 → 지정한 앞 단계(작성)로 돌아가 검토 의견을 반영해 다시 쓴다(기존 엔진과 같은 동작).
        draft = conn.execute(
            "SELECT output FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_id = ? "
            "AND status IN ('passed', 'approved') ORDER BY id DESC LIMIT 1",
            (state["run_id"], target["id"]),
        ).fetchone()
        conn.execute(
            "UPDATE pipeline_runs SET current_stage_seq = ? WHERE id = ?",
            (target["seq_order"], state["run_id"]),
        )
        audit.record(
            conn, state["actor_id"], "pipeline_stage_rework_requested", "pipeline_run_stage",
            state.get("stage_row_id"), after={"return_to_stage_id": target["id"]},
        )
        conn.commit()
        return {
            "seq_index": state["seq_orders"].index(target["seq_order"]),
            "attempt": _next_attempt(conn, state["run_id"], target["id"]),
            "retry_feedback": pr.rework_feedback(conn, state["run_id"], target),
            "base_input": pr.with_brief_reference(
                conn, state["task_id"], draft["output"] if draft else state["base_input"]
            ),
        }
    feedback = pr._gate_failure_summary(state["gate_scores"] or {})
    return {
        "attempt": state["attempt"] + 1,
        "retry_feedback": (
            f"\n\n[이전 시도({state['attempt']}차) Gate 결과] 다음 기준을 "
            f"통과하지 못했습니다: {feedback}. 이를 구체적으로 보완해 "
            "다시 작성하세요."
        ),
    }


def _node_human_approval(state: PipelineGraphState, config) -> dict:
    conn: sqlite3.Connection = config["configurable"]["conn"]
    # 버그 수정(구현 직후 자체 테스트 중 발견) — `interrupt()` 호출 이후의
    # 코드는 사람이 결정을 내려 재개(resume)할 때만 실행된다(첫 호출은
    # 여기서 그래프 실행 자체가 멈춘다) — 그래서 "승인 대기 상태로
    # 표시"는 반드시 `interrupt()` **이전**에 커밋해야, 멈춰 있는 동안
    # 밖에서 `pipeline_runs.status`를 조회했을 때 정확히 보인다.
    conn.execute(
        "UPDATE pipeline_runs SET status = 'paused_for_approval' WHERE id = ?",
        (state["run_id"],),
    )
    conn.commit()
    decision = interrupt(
        {
            "run_id": state["run_id"],
            "stage_name": state.get("stage_name"),
            "gate_scores": state["gate_scores"],
            "passed": state["passed"],
        }
    )
    status = "approved" if decision.get("decision") == "approve" else "rejected"
    run_stage_row = conn.execute(
        "SELECT id, deliverable_id FROM pipeline_run_stages WHERE pipeline_run_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (state["run_id"],),
    ).fetchone()
    conn.execute(
        "UPDATE pipeline_run_stages SET status = ? WHERE id = ?",
        (status, run_stage_row["id"]),
    )
    conn.execute(
        "UPDATE pipeline_runs SET status = 'running' WHERE id = ?", (state["run_id"],)
    )
    if status == "approved" and run_stage_row["deliverable_id"] is not None:
        # 읽기 쪽 호환성(`pipeline_runtime.resolve_stage_decision()`와
        # 동일) — 사람 승인까지 끝난 단계인데 deliverable 자체가
        # 'pending_review'로 남아 있으면 `inbox.get_inbox()`의
        # low_gate_score 항목이 계속 "아직 조치 필요"로 집어 올린다.
        deliverables.resolve_deliverable_decision(
            conn, run_stage_row["deliverable_id"], True
        )
    conn.commit()
    if status == "approved":
        return {"passed": True, "retry_feedback": ""}
    reason = decision.get("reason") or "(구체적 사유 입력 없음)"
    return {
        "passed": False,
        "attempt": state["attempt"] + 1,
        "retry_feedback": f"\n\n[사람 반려 사유] {reason} 이를 반영해 다시 작성하세요.",
    }


def _route_after_approval(state: PipelineGraphState) -> Literal["advance", "retry"]:
    return "advance" if state["passed"] else "retry"


def _node_advance(state: PipelineGraphState, config) -> dict:
    conn: sqlite3.Connection = config["configurable"]["conn"]
    next_index = state["seq_index"] + 1
    if next_index >= len(state["seq_orders"]):
        conn.execute(
            "UPDATE pipeline_runs SET status = 'completed', finished_at = datetime('now') "
            "WHERE id = ?",
            (state["run_id"],),
        )
        audit.record(
            conn, state["actor_id"], "pipeline_run_completed", "pipeline_runs",
            state["run_id"],
        )
        conn.commit()
        return {"seq_index": next_index}
    # 2단계 — 읽기 쪽 호환성: 기존 파이프라인 진행률 화면이 읽는
    # `pipeline_runs.current_stage_seq`를 똑같이 유지한다.
    conn.execute(
        "UPDATE pipeline_runs SET current_stage_seq = ? WHERE id = ?",
        (state["seq_orders"][next_index], state["run_id"]),
    )
    conn.commit()
    next_stage = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (state["pipeline_template_id"], state["seq_orders"][next_index]),
    ).fetchone()
    return {
        "seq_index": next_index,
        # 원 요청(Brief)을 모든 단계에 함께(2026-11-21, `pipeline_runtime.with_brief_reference`).
        "base_input": pr.with_brief_reference(conn, state["task_id"], state["stage_output"] or ""),
        # DB 기준 시도 번호(처음 오는 단계는 1) — 검토 실패로 돌아갔다 다시 와도 이어서 센다.
        "attempt": _next_attempt(conn, state["run_id"], next_stage["id"]) if next_stage else 1,
        "retry_feedback": "",
    }


def _route_after_advance(state: PipelineGraphState) -> str:
    # 반환값은 노드 이름 또는 END("__end__") — LangGraph의 END 상수는 타입상 그냥 str라
    # Literal 반환형과 맞지 않아(Pylance reportReturnType) str로 선언한다(2026-11-23).
    return END if state["seq_index"] >= len(state["seq_orders"]) else "run_stage"


def _run_config(conn: sqlite3.Connection, run_id: int) -> RunnableConfig:
    """그래프 실행 설정 — 실행마다 같은 thread_id(체크포인트 키)와 DB 연결을 노드에 넘긴다.
    네 곳(실행·재개 × invoke·stream)이 같은 모양이라 한 곳에 둔다(RunnableConfig 타입 명시)."""
    return {"configurable": {"thread_id": f"pipeline_run_{run_id}", "conn": conn}}


def build_graph():
    builder = StateGraph(PipelineGraphState)
    builder.add_node("run_stage", _node_run_stage)
    builder.add_node("prepare_retry", _node_prepare_retry)
    builder.add_node("human_approval", _node_human_approval)
    builder.add_node("advance", _node_advance)

    builder.add_edge(START, "run_stage")
    builder.add_conditional_edges(
        "run_stage",
        _route_after_stage,
        {
            "retry": "prepare_retry",
            "human_approval": "human_approval",
            "advance": "advance",
            "cancelled": END,
        },
    )
    builder.add_edge("prepare_retry", "run_stage")
    builder.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"advance": "advance", "retry": "prepare_retry"},
    )
    builder.add_conditional_edges(
        "advance", _route_after_advance, {"run_stage": "run_stage", END: END}
    )
    return builder


def _build_initial_state(
    conn: sqlite3.Connection, run_id: int, actor_id: int, run: sqlite3.Row
) -> PipelineGraphState:
    seq_orders = _ordered_seq_orders(conn, run["pipeline_template_id"])
    return {
        "run_id": run_id,
        "task_id": run["task_id"],
        "actor_id": actor_id,
        "pipeline_template_id": run["pipeline_template_id"],
        "seq_orders": seq_orders,
        "seq_index": 0,
        "base_input": pr._stage_initial_input(conn, run["task_id"]),
        "retry_feedback": "",
        "attempt": 1,
        "gate_scores": None,
        "stage_output": None,
        "passed": None,
        "needs_human": None,
        "stage_max_retry": None,
        "stage_row_id": None,
        "stage_name": None,
        "cancelled": None,
        "budget_exceeded": None,
    }


def run_pipeline_graph(
    conn: sqlite3.Connection,
    run_id: int,
    actor_id: int,
    checkpointer,
) -> dict:
    """새 실행을 시작한다(이미 진행 중인 `thread_id`가 있으면
    `resume_pipeline_graph()`를 쓸 것 — 이 함수는 항상 처음부터
    시작한다)."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("pipeline_run_not_found")
    validate_template_supported(conn, run["pipeline_template_id"])
    # 3단계(2026-11-18) — 이 run이 그래프 엔진으로 실행 중임을 표시한다.
    # `app.py`의 `decide_approval()`이 이 플래그로 `pipeline_runtime.
    # resolve_stage_decision()`(DB 컬럼 직접 조작 전제)과 혼용하지
    # 않고 올바른 엔진으로만 재개하도록 분기한다.
    conn.execute("UPDATE pipeline_runs SET engine = 'graph' WHERE id = ?", (run_id,))
    conn.commit()
    initial_state = _build_initial_state(conn, run_id, actor_id, run)
    graph = build_graph().compile(checkpointer=checkpointer)
    config = _run_config(conn, run_id)
    result = graph.invoke(initial_state, config=config)
    _cleanup_checkpoint_if_terminal(conn, run_id, checkpointer)
    return result


# 6단계(2026-11-18, "conversations.py 통합") — `graph.invoke()`는
# 여러 단계가 자동 통과해도 전부 끝날 때까지(또는 interrupt까지)
# 한 번에 쭉 돈다. 대화에서 트리거된 run은 `pipeline_runtime.
# run_pipeline_stage_job()`의 `on_stage_resolved` 콜백처럼 "몇 단계가
# 지났는지"를 바로 알려야 한다(2026-10-28에 이미 한 번 고친 바로 그
# "자동 통과 단계가 여러 개 이어지면 대화창에 긴 시간 신호가 없다"는
# 문제 — 그래프 엔진에도 똑같이 적용됨). `graph.stream(stream_mode=
# "updates")`로 바꾸면 노드별 업데이트가 그때그때 나오므로, "run_stage"
# /"advance" 청크가 보일 때마다 콜백을 부른다(`__interrupt__` 청크도
# 그대로 나온다 — 실측 확인, `tests/unit/test_execution_graph.py`).
_PER_STAGE_STEP_KEYS = {"run_stage", "advance"}


def run_pipeline_graph_with_callback(
    conn: sqlite3.Connection,
    run_id: int,
    actor_id: int,
    checkpointer,
    on_stage_resolved=None,
) -> None:
    """`run_pipeline_graph()`의 스트리밍 버전 — 반환값이 없다(스트림은
    노드별 업데이트만 주지 전체 state dict를 안 줌 — 호출자가 run
    상태를 다시 조회해야 한다, `conversations.py`가 이미 그렇게 함)."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("pipeline_run_not_found")
    validate_template_supported(conn, run["pipeline_template_id"])
    conn.execute("UPDATE pipeline_runs SET engine = 'graph' WHERE id = ?", (run_id,))
    conn.commit()
    initial_state = _build_initial_state(conn, run_id, actor_id, run)
    graph = build_graph().compile(checkpointer=checkpointer)
    config = _run_config(conn, run_id)
    for step in graph.stream(initial_state, config=config, stream_mode="updates"):
        if on_stage_resolved is not None and _PER_STAGE_STEP_KEYS & set(step.keys()):
            on_stage_resolved()
    _cleanup_checkpoint_if_terminal(conn, run_id, checkpointer)


def resume_pipeline_graph(
    conn: sqlite3.Connection,
    run_id: int,
    checkpointer,
    decision: dict | None = None,
) -> dict:
    """서버 재시작 후 재개하거나(decision=None — 체크포인트 그대로
    이어감), 사람 승인/반려 결정을 전달한다(`{"decision": "approve"|
    "reject", "reason": str}`)."""
    graph = build_graph().compile(checkpointer=checkpointer)
    config = _run_config(conn, run_id)
    resume_value = Command(resume=decision) if decision is not None else None
    result = graph.invoke(resume_value, config=config)
    _cleanup_checkpoint_if_terminal(conn, run_id, checkpointer)
    return result


def resume_pipeline_graph_with_callback(
    conn: sqlite3.Connection,
    run_id: int,
    checkpointer,
    decision: dict | None = None,
    on_stage_resolved=None,
) -> None:
    """`resume_pipeline_graph()`의 스트리밍 버전(`run_pipeline_graph_
    with_callback()`과 동일한 이유 — 위 모듈 레벨 주석 참고)."""
    graph = build_graph().compile(checkpointer=checkpointer)
    config = _run_config(conn, run_id)
    resume_value = Command(resume=decision) if decision is not None else None
    for step in graph.stream(resume_value, config=config, stream_mode="updates"):
        if on_stage_resolved is not None and _PER_STAGE_STEP_KEYS & set(step.keys()):
            on_stage_resolved()
    _cleanup_checkpoint_if_terminal(conn, run_id, checkpointer)


def _cleanup_checkpoint_if_terminal(
    conn: sqlite3.Connection, run_id: int, checkpointer
) -> None:
    """실측으로 발견한 운영 공백(2026-11-18) — 완료/취소된 run의
    LangGraph 체크포인트(`checkpoints`/`writes` 테이블)를 그대로
    두면 무기한 쌓인다(완료된 run은 다시 재개될 일이 없어 더 이상
    필요 없음). `graph.invoke()`가 끝난 직후(그래프가 완전히 정지한
    뒤)에만 호출해야 안전하다 — 실행 중인 체크포인트 쓰기 시퀀스와
    겹칠 위험이 없는 지점. `pipeline_run_stages`/`deliverables` 등
    실제 "진실"은 메인 DB에 이미 전부 영속돼 있으므로(2단계에서
    보장한 읽기 쪽 호환성), 체크포인트 삭제는 순수 정리일 뿐
    데이터 손실이 아니다."""
    run = conn.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if run is not None and run["status"] in ("completed", "cancelled", "failed"):
        checkpointer.delete_thread(f"pipeline_run_{run_id}")


def is_graph_engine_run(conn: sqlite3.Connection, run_id: int) -> bool:
    """`app.py`의 `decide_approval()`이 `pipeline_runtime.resolve_stage_
    decision()`(기존 엔진)과 `resume_pipeline_graph_from_approval()`
    (이 엔진) 중 어느 쪽으로 재개해야 하는지 분기하는 데 쓴다."""
    row = conn.execute(
        "SELECT engine FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return row is not None and row["engine"] == "graph"


def checkpoint_db_path(conn: sqlite3.Connection) -> str:
    """메인 DB와 같은 디렉터리에 체크포인트 파일을 둔다 — "SQLite
    하나로 전부"라는 기존 철학과 맞추면서도, LangGraph 체크포인터는
    자기 전용 스키마(`checkpoints`/`writes` 테이블)가 필요해 메인
    DB 파일 자체를 공유하진 않고 옆에 별도 파일을 둔다. 메인 DB가
    `:memory:`(개발/테스트 기본값, `app.py`의 `HAIR_PLATFORM_DB_PATH`
    미설정 시 폴백)면 체크포인트도 `:memory:`."""
    row = conn.execute("PRAGMA database_list").fetchall()
    main_path = next((r["file"] for r in row if r["name"] == "main"), "") or ""
    if not main_path:
        return ":memory:"
    from pathlib import Path

    return str(Path(main_path).with_name(Path(main_path).name + ".langgraph_checkpoints.sqlite3"))


# 실측으로 발견한 제약(2026-11-18, 라이브 연결 작업 중) — `:memory:`
# DB(개발/테스트 기본값)에서 `SqliteSaver.from_conn_string(":memory:")`
# 를 호출마다 새로 열면, SQLite의 `:memory:`는 커넥션마다 완전히
# 분리된 별개의 DB라(파일 기반과 달리 "같은 경로 = 같은 내용"이 전혀
# 아님) 매번 텅 빈 체크포인트로 시작한다 — 즉 "서버가 죽었다 재시작"
# 처럼 일부러 새로 여는 테스트에서는 맞는 동작이었지만, 같은 프로세스
# 안에서 ① 그래프를 시작해 사람 승인 대기로 멈추고 ② 결재함에서
# 승인해 재개하는 **정상 흐름**에서도 매번 새 커넥션을 열면 ①에서
# 멈춘 체크포인트를 ②가 영원히 찾지 못한다. `app.state.db`가 메인
# 커넥션 하나를 프로세스 전체가 공유하는 것과 똑같은 이유로, 체크포인터도
# 메인 `conn` 인스턴스당 하나만 열어 재사용해야 한다(파일 기반 DB는
# 매번 새로 열어도 같은 파일이라 원래부터 문제없었음 — 크래시 복구
# 테스트가 실제로 그렇게 함).
#
# 실측 버그(구현 중 바로 발견) — `sqlite3.Connection`은 C로 구현된
# 타입이라 `weakref.ref()`를 못 건다(`TypeError: cannot create weak
# reference to 'sqlite3.Connection' object`) — `WeakKeyDictionary`를
# 못 쓴다. 대신 `id(conn)`을 키로 쓰고, 값에 `conn` 자신도 같이
# 저장해(`(conn, saver)`) 매 조회마다 `is` 동일성을 재확인한다 — 이
# 캐시가 `conn`을 계속 참조해 절대 GC되지 않게 만들어서(의도적인
# 트레이드오프) `id()` 재사용으로 전혀 다른 커넥션이 같은 키로
# 조회되는 사고를 원천적으로 막는다. 실제 서버는 프로세스 생애주기
# 내내 `conn` 단 하나뿐이라 메모리 증가가 없고, 테스트 스위트에서도
# `:memory:` 커넥션 자체가 가벼워 세션 전체에 누적돼도 무시할 수준.
_checkpointer_cache: dict[int, tuple[sqlite3.Connection, object]] = {}
_checkpointer_lock = threading.Lock()


def get_checkpointer(conn: sqlite3.Connection):
    """이 메인 `conn` 인스턴스에 묶인 체크포인터를 재사용(없으면 1회
    생성)한다. `conn`을 캐시 키로 쓰는 이유 — 경로 문자열(`":memory:"`
    등)로 캐시하면 서로 다른 앱 인스턴스(예: 테스트 스위트의 여러
    `client_and_conn` fixture 인스턴스)가 전부 같은 체크포인터를
    공유해버려 테스트 간 상태가 새는 사고가 난다; `conn` 객체 자체는
    인스턴스마다 항상 다르므로 안전하다. LangGraph import는 캐시
    미스일 때만 일어난다 — `graph` extra가 선택적 의존성이라, 그래프
    엔진을 한 번도 안 쓰는 배포에서는 이 함수 자체가 호출되지 않아야
    한다(호출부가 그래프 엔진 run에서만 불러야 함)."""
    with _checkpointer_lock:
        cached = _checkpointer_cache.get(id(conn))
        if cached is not None and cached[0] is conn:
            return cached[1]
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = checkpoint_db_path(conn)
        raw_conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(raw_conn)
        _checkpointer_cache[id(conn)] = (conn, saver)
        return saver


@llm_usage.with_scope(task_id=llm_usage.task_of_pipeline_run, pipeline_run_id="run_id")
def run_pipeline_graph_job(conn: sqlite3.Connection, run_id: int, actor_id: int) -> dict:
    """`app.py`의 `trigger_pipeline_execution()`이 `payload.use_graph_
    engine=True`일 때 `BackgroundTasks`로 거는 진입점 — 공유
    체크포인터(`get_checkpointer()`)를 알아서 구해 쓴다."""
    return run_pipeline_graph(conn, run_id, actor_id, get_checkpointer(conn))


@llm_usage.with_scope(task_id=llm_usage.task_of_pipeline_run, pipeline_run_id="run_id")
def run_pipeline_graph_job_with_callback(
    conn: sqlite3.Connection, run_id: int, actor_id: int, on_stage_resolved=None
) -> None:
    """`conversations.py`의 `_run_team_mention_via_pipeline()`이 거는
    진입점 — `run_pipeline_graph_job()`의 콜백 버전(매 단계마다
    `on_stage_resolved()`를 부른다)."""
    run_pipeline_graph_with_callback(
        conn, run_id, actor_id, get_checkpointer(conn), on_stage_resolved=on_stage_resolved
    )


def resume_pipeline_graph_from_approval(
    conn: sqlite3.Connection, run_id: int, decision: dict
) -> dict:
    """결재함의 승인/반려 버튼이 이 함수를 호출해 실제로 `interrupt()`
    를 재개시킨다(`app.py`의 `decide_approval()`이 `BackgroundTasks`로
    건다). 호출 전에 반드시 `is_graph_engine_run()`으로 확인할 것 —
    기존 엔진 run에 호출하면 체크포인트가 없어 그냥 아무 일도 안
    일어난다(LangGraph가 모르는 thread_id는 처음부터 시작하려 들기
    때문 — `app.py`가 분기 자체를 틀리게 하지 않는 한 발생하지 않음).
    `run_pipeline_graph_job()`과 반드시 같은 체크포인터(`get_
    checkpointer()`)를 써야 한다 — 그래야 `:memory:` 모드에서도 멈춘
    지점을 찾는다(위 모듈 레벨 주석 참고)."""
    return resume_pipeline_graph(conn, run_id, get_checkpointer(conn), decision=decision)


def resume_pipeline_graph_from_approval_with_callback(
    conn: sqlite3.Connection, run_id: int, decision: dict, on_stage_resolved=None
) -> None:
    """`conversations.py`의 `resume_pipeline_graph_from_approval_and_
    notify()`가 거는 진입점 — `resume_pipeline_graph_from_approval()`
    의 콜백 버전."""
    resume_pipeline_graph_with_callback(
        conn, run_id, get_checkpointer(conn), decision=decision,
        on_stage_resolved=on_stage_resolved,
    )
