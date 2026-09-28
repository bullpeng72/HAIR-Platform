"""Phase 3 — Agent 산출물 Gate A-G 평가 인라인 연결(`docs/HAIR_MASTER_PLAN.md` §5,
사용자 요청 2026-09-28 "Phase 3 진행해줘"). 로드맵 3번 항목 — 기존 `eval_wiring.py`
(SPEC.md §12 시절 만든 실행 예시/probe 스크립트, `python -m hair_platform.eval_wiring`로
수동 실행)와 달리, 이 모듈은 `deliverables.execute_agent_for_task()` 안에서 매
Agent 실행마다 자동으로 호출되는 실제 프로덕션 배선이다.

**실측으로 확인한 것(2026-09-28, agent-evaluator 1.1.1 설치 소스 직접 실행)**:
- `@agent_eval`로 감싼 함수를 호출하면 반환값은 원래 함수가 낸 튜플의 첫 원소
  (`response`)만 돌아온다(`metadata`는 SDK 내부로 소비된다) — 그래서 실제
  `agent_runtime.execute_agent()` 호출을 감싼 함수를 만들어도 호출자는 평소처럼
  결과 문자열을 그대로 받을 수 있다.
- `PerformanceMonitor.generate_report().extra_metrics["harness_groups"]`가
  Gate A-G + "overall" 점수를 담고, `agent_evaluator.gates.base.evaluate_gate_scores`
  (canonical import 경로 — `agent_evaluator.quick_eval`도 재노출하지만 Pyright가
  canonical 위치를 이쪽으로 가리킨다)에 넘기면 게이트별 pass/fail 판정이 나온다.
- 이 파이프라인은 **추가 LLM 호출을 하지 않는다**(`llm_judge`·환각탐지 미설정) —
  넘겨준 `EvalMetadata`와 응답 텍스트만으로 휴리스틱 채점한다.
- TCR(완료율)은 `report.accuracy_metrics["tcr"]["tcr"]`(0-100 실수)에서 나온다 —
  `agent-evaluator` CLI(`cli/gate.py`)가 채점에 실제로 쓰는 값과 같은 경로다.
- 모니터는 호출된 모든 task를 누적 집계한다 — 따라서 "이번 한 번의 실행"만
  채점하려면 **호출마다 새 `PerformanceMonitor`를 만들어야 한다**(모듈 전역
  싱글턴을 쓰면 이전 실행들과 점수가 섞인다). `output_dir`는 `auto_save=False`
  기본값 때문에 실제로 쓰이지 않는다(`save_to_file()`을 부르지 않는 한) — 여기서는
  파일로 안 남기고 `agent_evaluations` 테이블에 바로 적재한다.

**한계(정직하게 명시, eval_wiring.py의 선례를 그대로 따름)**: HAIR-Platform의
Agent 실행은 도구 호출이 없는 단발 LLM 호출이라 Gate B(행동 무결성)·D(성능
계약)·E(보안 경계)는 최소 설정으로는 `not_measured`(점수 null)로 나올 수 있다 —
이건 버그가 아니라 "측정할 신호가 없다"는 정직한 표시다. Gate G(설명가능성)는
`execute_agent_for_task()`가 짜는 프롬프트가 "판단 근거를 명시하라"고 요구하지
않으므로 낮게 나올 가능성이 높다 — 이번 스코프는 "평가 인프라 연결"이지 "Agent
응답 품질 개선"이 아니라서 프롬프트는 건드리지 않았다(`docs/LIMITS.md` §24).

**`agent_pool.performance_score`와의 관계**: 기존 `performance.set_agent_performance()`
는 관리자 수동 입력 전용으로 남긴다(§5-2 "완전 자동 대체 아님, 구분해서 보여준다") —
이 모듈은 그 컬럼을 갱신하지 않는다. 자동 측정치는 `agent_evaluations` 테이블에서
`aggregate_agent_score()`로 별도 조회한다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from agent_evaluator import (
    EvalMetadata,
    ExplainabilityConfig,
    PerformanceMonitor,
    ScopeConfig,
    SLAConfig,
    agent_eval,
)
from agent_evaluator.gates.base import evaluate_gate_scores

GATE_IDS = ["A", "B", "C", "D", "E", "F", "G"]
DEFAULT_GATE_THRESHOLD = 0.7

# 실제 LLM 호출(수 초~수십 초) 기준 SLA. eval_wiring.py는 결정론적 스텁 기준
# (5s/10s, "빨라야 정상"인 반대 근거)이라 여기 그대로 쓰면 정상 호출도 전부
# Gate D 위반으로 잡힌다 — 실제 LLM 응답 지연 특성에 맞춰 넉넉히 잡는다.
_SLA = SLAConfig(p95_ms=30000.0, p99_ms=60000.0)
_EXPLAINABILITY = ExplainabilityConfig(require_reasoning=True)
_SCOPE = ScopeConfig(allowed_tools=[], fail_on_violation=False, violation_penalty=0.5)


def run_and_score(role_name: str, call_fn: Callable[[], str]) -> dict:
    """실제 Agent 실행(`call_fn`)을 감싸 그 자리에서 Gate A-G를 측정한다.
    DB에 아무것도 쓰지 않는다 — `call_fn`이 예외를 던지면(LLM 호출 실패)
    그대로 전파하고, 이 시점엔 아직 deliverable 행이 없어야 하므로 호출자는
    `call_fn`이 성공한 뒤에만 deliverable을 INSERT하고, 그 id로
    `record_evaluation()`을 따로 불러야 한다(deliverable 생성 실패 시 평가
    행만 붕 뜨는 것을 막는다 — `deliverables.execute_agent_for_task()`의
    "실패 시 아무 것도 안 남는다" 불변식을 그대로 유지)."""
    monitor = PerformanceMonitor(
        output_dir="results/deliverables/", agent_version="auto"
    )

    @agent_eval(
        monitor,
        # 실측으로 발견(2026-09-28): 'deliverable'은 canonical task_type이
        # 아니다("unrecognised task_type" 경고, SDK가 그래도 그대로 받아는
        # 준다). 산출물은 문서/텍스트 생성이므로 SDK 목록의
        # 'document_creation'이 정확히 맞는다.
        task_type="document_creation",
        question_arg="role_name",
        sla=_SLA,
        explainability=_EXPLAINABILITY,
        scope=_SCOPE,
    )
    def _run(role_name: str) -> tuple[str, EvalMetadata]:
        response = call_fn()
        metadata = EvalMetadata(
            completion_score=1.0,
            tool_calls=[],
            extra={"role_name": role_name},
        )
        return response, metadata

    output = _run(role_name)

    report = monitor.generate_report()
    harness_groups = (report.extra_metrics or {}).get("harness_groups", {})
    gate_scores = evaluate_gate_scores(
        harness_groups, gate_ids=GATE_IDS, default_threshold=DEFAULT_GATE_THRESHOLD
    )
    tcr_block = (report.accuracy_metrics or {}).get("tcr") or {}
    tcr = tcr_block.get("tcr")

    return {"output": output, "gate_scores": gate_scores, "tcr": tcr}


def record_evaluation(
    conn: sqlite3.Connection, deliverable_id: int, gate_scores: dict, tcr: float | None
) -> None:
    """`run_and_score()`의 결과를 `agent_evaluations`에 append한다(커밋은
    호출자 책임 — `deliverables.execute_agent_for_task()`가 deliverable
    INSERT와 한 트랜잭션으로 커밋한다)."""
    conn.execute(
        "INSERT INTO agent_evaluations (deliverable_id, gate_scores, tcr) VALUES (?, ?, ?)",
        (deliverable_id, json.dumps(gate_scores, ensure_ascii=False), tcr),
    )


def latest_evaluation(conn: sqlite3.Connection, deliverable_id: int) -> dict | None:
    """`list_deliverables()` 등에서 산출물 타임라인에 Gate 점수를 함께
    보여줄 때 쓴다. 여러 번 평가됐어도(append-only) 가장 최근 것만 낸다."""
    row = conn.execute(
        "SELECT * FROM agent_evaluations WHERE deliverable_id = ? "
        "ORDER BY evaluated_at DESC, id DESC LIMIT 1",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "deliverable_id": row["deliverable_id"],
        "gate_scores": json.loads(row["gate_scores"]),
        "tcr": row["tcr"],
        "evaluated_at": row["evaluated_at"],
    }


def aggregate_agent_score(conn: sqlite3.Connection, agent_id: int) -> dict | None:
    """§5-2 — `agent_pool.performance_score`(관리자 수동)와 별도로 보여줄
    자동 측정 평균값. 그 Agent가 만든 모든 deliverable의 최신 평가를 모아
    Gate별 평균 점수(측정된 것만)와 평균 TCR을 낸다. 평가 이력이 없으면
    None(수동 값만 있고 자동 값은 "아직 없음"을 의미)."""
    rows = conn.execute(
        "SELECT ae.gate_scores, ae.tcr FROM agent_evaluations ae "
        "JOIN deliverables d ON d.id = ae.deliverable_id "
        "WHERE d.author_type = 'agent' AND d.author_id = ?",
        (agent_id,),
    ).fetchall()
    if not rows:
        return None

    gate_totals: dict[str, list[float]] = {gid: [] for gid in GATE_IDS}
    tcr_values: list[float] = []
    for row in rows:
        scores = json.loads(row["gate_scores"])
        for gid in GATE_IDS:
            score = scores.get(gid, {}).get("score")
            if score is not None:
                gate_totals[gid].append(score)
        if row["tcr"] is not None:
            tcr_values.append(row["tcr"])

    gate_averages = {
        gid: (sum(values) / len(values) if values else None)
        for gid, values in gate_totals.items()
    }
    return {
        "sample_count": len(rows),
        "gate_averages": gate_averages,
        "avg_tcr": (sum(tcr_values) / len(tcr_values) if tcr_values else None),
    }
