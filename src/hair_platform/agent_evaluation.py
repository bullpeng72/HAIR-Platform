"""Phase 3 — Agent 산출물 Gate A-G 평가 인라인 연결(사용자 요청 2026-09-28
"Phase 3 진행해줘"). 로드맵 3번 항목 — 기존 `eval_wiring.py`
(수동 실행 예시/probe 스크립트, `python -m hair_platform.eval_wiring`로
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
응답 품질 개선"이 아니라서 프롬프트는 건드리지 않았다.

**`agent_pool.performance_score`와의 관계**: 기존 `performance.set_agent_performance()`
는 관리자 수동 입력 전용으로 남긴다("완전 자동 대체 아님, 구분해서 보여준다") —
이 모듈은 그 컬럼을 갱신하지 않는다. 자동 측정치는 `agent_evaluations` 테이블에서
`aggregate_agent_score()`로 별도 조회한다.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
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

_FENCED_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_PYTHON_TAGS = {"python", "py"}
_JS_TAGS = {"javascript", "js"}
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?:\s[^>]*)?>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


def _extract_inline_scripts(html: str) -> list[str]:
    """raw HTML 문서(또는 ` ```html ` 펜스 블록 내용)에서 인라인
    `<script>` 본문만 뽑는다 — `<script src="...">`처럼 본문이 없는
    외부 스크립트는 제외(검사할 내용 자체가 없음)."""
    return [s for s in _INLINE_SCRIPT_RE.findall(html) if s.strip()]


def _check_js_snippets_with_node(
    snippets: list[str], node_path: str, label: str
) -> tuple[int, list[str]]:
    """각 JS 조각을 `.js` 임시 파일에 써서 `node --check`로 검사한다
    (**`.html` 확장자를 쓰면 Node가 내용과 무관하게 `ERR_UNKNOWN_
    FILE_EXTENSION`으로 항상 즉시 실패한다** — 2026-11-05 직접 실행
    확인으로 발견한 버그, 이 함수로 재발을 막는다: HTML 원본을
    통째로 Node에 넘기지 않고 인라인 `<script>` 본문만 `.js`로 검사).
    `(검사한 개수, 에러 목록)`을 반환한다."""
    checked = 0
    errors: list[str] = []
    for snippet in snippets:
        checked += 1
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=True, encoding="utf-8"
        ) as f:
            f.write(snippet)
            f.flush()
            try:
                proc = subprocess.run(
                    [node_path, "--check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                errors.append(f"{label}: node --check timeout")
                continue
            if proc.returncode != 0:
                errors.append(f"{label}: {proc.stderr.strip()[:500]}")
    return checked, errors


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


def run_and_score_crew(crew_label: str, call_fn: Callable[[], object]) -> dict:
    """`crew_runtime.run_crew()`의 실제 `crew.kickoff()` 호출(`call_fn`)을
    감싸 Gate A-G를 측정한다 — 사용자 요청(2026-09-29, "crewAI의 핵심
    개념과 기능들을 적절하게 반영하였는지 확인해줘"에서 찾은 갭을
    "Gate A-G를 CrewAI 멀티에이전트 경로에도 연결해줘"로 메움).

    **`run_and_score()`와의 핵심 차이**: 단일 실행 경로는 도구 호출·위임
    같은 신호가 아예 없어(agent loop 자체가 없음) `EvalMetadata`를 직접
    손으로 채워야 했다. 이 함수는 `agent_eval(..., framework="crewai")`
    를 쓴다 — SDK가 `call_fn()`이 반환한 실제 `CrewOutput`(튜플로 안
    감싸고 그대로 반환하면 된다)의 `tasks_output`을 자동으로
    `EvalMetadata.agent_interactions`로 변환해준다(`agent_evaluator.
    framework_adapters._extract_crewai_metadata()`, 실측 확인:
    `_split_raw()`가 비-튜플 반환값을 `(raw, None)`으로 취급해 프레임워크
    어댑터가 자동으로 붙는다 — 데코레이터를 통과한 반환값도 원본
    `CrewOutput` 그대로라 호출자는 지금까지처럼 `.raw`/`.tasks_output`/
    `.token_usage`를 그대로 쓸 수 있다). 이게 바로 Gate F(멀티에이전트
    협업: 합의·정보 전파·역할 준수·충돌 해결)가 실제로 측정될 신호를
    받는 유일한 경로다 — `run_crew()`가 이미 실시간으로 캡처하는 위임
    이벤트와는 별개로(그건 감사로그용), SDK 쪽 멀티에이전트 채점은
    `tasks_output`만 보고 자체적으로 판단한다.

    `call_fn`이 예외를 던지면(LLM 호출 실패 등) 그대로 전파한다 —
    `run_and_score()`와 같은 원칙."""
    monitor = PerformanceMonitor(
        output_dir="results/deliverables/", agent_version="auto"
    )

    @agent_eval(
        monitor,
        task_type="document_creation",
        question_arg="crew_label",
        framework="crewai",
        sla=_SLA,
        explainability=_EXPLAINABILITY,
        scope=_SCOPE,
    )
    def _run(crew_label: str):
        return call_fn()

    crew_output = _run(crew_label)

    report = monitor.generate_report()
    harness_groups = (report.extra_metrics or {}).get("harness_groups", {})
    gate_scores = evaluate_gate_scores(
        harness_groups, gate_ids=GATE_IDS, default_threshold=DEFAULT_GATE_THRESHOLD
    )
    tcr_block = (report.accuracy_metrics or {}).get("tcr") or {}
    tcr = tcr_block.get("tcr")

    return {"output": crew_output, "gate_scores": gate_scores, "tcr": tcr}


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
    """`agent_pool.performance_score`(관리자 수동)와 별도로 보여줄
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


def compute_code_syntax_check(content: str) -> dict:
    """산출물 텍스트에서 펜스 코드 블록(` ```python ... ``` ` 등)을 뽑아
    실제로 구문이 유효한지 결정론적으로 확인한다 — 추가 LLM 호출 없음,
    비용 0. 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사") — Gate
    A-G는 순수 휴리스틱이라 "코드"라고 주장하는 텍스트가 실제로 파싱
    가능한지는 지금까지 아무도 기계적으로 확인하지 않았다.

    Python은 `compile()`로 검사한다(`pool_registry._validate_custom_
    python_source()`와 같은 선례, 새 의존성 없음). JS/HTML은 사용자가
    명시적으로 선택한 방식대로 **Node.js가 PATH에 있을 때만**
    `node --check`로 검사하고, 없으면 그 블록은 조용히 `not_measured`로
    건너뛴다(이 저장소 어디에도 Node를 런타임 의존성으로 요구하는 곳이
    없으므로 새로 강제하지 않는다).

    블록이 하나도 없거나 전부 모르는 언어/검사 도구 없음이면
    `status: "not_measured"` — "검사할 게 없었다"는 뜻이고 실패가
    아니다. 호출부가 이 결과를 `gate_scores`에 넣을 때 `_gate_pass_
    ratio()`(제네릭 순회라 어떤 키든 자동으로 통과/차단 계산에 들어감)
    오염을 피하려면 필요에 따라 `not_measured`를 강제해야 한다 — 이
    함수 자체는 그 결정에 관여하지 않는다."""
    blocks = _FENCED_CODE_BLOCK_RE.findall(content or "")
    checked = 0
    errors: list[str] = []
    node_path = None

    for lang, code in blocks:
        lang = lang.lower().strip()
        if lang in _PYTHON_TAGS:
            checked += 1
            try:
                compile(code, "<deliverable>", "exec")
            except SyntaxError as exc:
                errors.append(f"python: {exc}")
        elif lang in _JS_TAGS:
            if node_path is None:
                node_path = shutil.which("node") or ""
            if not node_path:
                continue  # Node 없음 — 이 블록은 검사 대상에서 제외(not_measured 사유).
            block_checked, block_errors = _check_js_snippets_with_node(
                [code], node_path, lang
            )
            checked += block_checked
            errors.extend(block_errors)
        elif lang == "html":
            if node_path is None:
                node_path = shutil.which("node") or ""
            if not node_path:
                continue  # Node 없음 — 이 블록은 검사 대상에서 제외(not_measured 사유).
            # 펜스 블록의 HTML 전체가 아니라, 그 안에 든 인라인
            # <script>만 검사한다 — HTML 자체는 node --check로 검증할
            # "문법" 개념이 없고(<script>가 하나도 없으면 검사할 게
            # 없다는 뜻, fail이 아니다), html 태그를 그대로 .html로
            # 저장해 node에 넘기면 내용과 무관하게 항상 실패한다.
            scripts = _extract_inline_scripts(code)
            block_checked, block_errors = _check_js_snippets_with_node(
                scripts, node_path, "html(<script>)"
            )
            checked += block_checked
            errors.extend(block_errors)
        # 모르는 언어 태그는 건너뜀(아는 언어만 검사).

    if checked == 0:
        return {
            "score": None,
            "status": "not_measured",
            "not_measured": True,
            "passed": False,
            "checked_blocks": 0,
        }
    score = 1.0 if not errors else 0.0
    return {
        "score": score,
        "status": "pass" if not errors else "fail",
        "not_measured": False,
        "passed": not errors,
        "checked_blocks": checked,
        "errors": errors,
    }


def compute_raw_html_syntax_check(content: str) -> dict:
    """펜스로 감싸지 않은 **전체 raw HTML 문서**용(로드맵 5번, 2026-11-05)
    — `content_type="html"`로 명시 요청한 경우 LLM에게 주는 지시문
    (`deliverables._CONTENT_TYPE_INSTRUCTIONS["html"]`)이 "완전한 HTML
    문서로 작성하세요(`<!DOCTYPE html>`부터 `</html>`까지)"라 펜스를
    요구하지 않는다 — `compute_code_syntax_check()`의 펜스 스캔은 이
    경우 거의 항상 `checked_blocks=0`으로 빠져 검증 기회를 놓친다.

    HTML 문서 자체는 `node --check`로 검증할 "문법" 개념이 없다 —
    인라인 `<script>`가 있을 때만 그 내용을 검사한다(없으면
    not_measured, fail이 아니다). 반환 shape은 `compute_code_syntax_
    check()`와 동일."""
    node_path = shutil.which("node") or ""
    if not node_path:
        return {
            "score": None,
            "status": "not_measured",
            "not_measured": True,
            "passed": False,
            "checked_blocks": 0,
        }
    scripts = _extract_inline_scripts(content or "")
    if not scripts:
        return {
            "score": None,
            "status": "not_measured",
            "not_measured": True,
            "passed": False,
            "checked_blocks": 0,
        }
    checked, errors = _check_js_snippets_with_node(scripts, node_path, "html(<script>)")
    return {
        "score": 1.0 if not errors else 0.0,
        "status": "pass" if not errors else "fail",
        "not_measured": False,
        "passed": not errors,
        "checked_blocks": checked,
        "errors": errors,
    }
