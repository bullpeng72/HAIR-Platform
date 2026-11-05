"""파이프라인 "코드 검증" 단계에서 "구현 산출물이 1단계 요구사항을 실제로
빠짐없이 반영했는지"를 LLM judge로 채점하는 선택적 보조 기능 — 사용자
요청(2026-11-05, 로드맵 4번 "Gate 구문검사 + 요구사항 추적성 자동
채점"). "코드 검증" 단계는 바로 이전 단계(구현)의 출력만 보고(
`pipeline_runtime._execute_stage_attempt()`의 `base_input` 조회가 최신
1건만 가져온다), 1단계(요구분석/계획 확정)의 EARS 요구사항 문서를
자동으로 보지 못한다 — 그래서 호출부가 1단계 출력을 별도로 직접 조회해
이 모듈에 넘긴다.

`brief_decompose_llm.suggest_decomposition()`/`match_llm_judge.
judge_candidates()`와 같은 fail-safe 계약: API 에러·JSON 파싱 실패 등
어떤 이유로든 실패하면 절대 예외를 올리지 않고 `{"score": None,
"status": "not_measured"}`로 안전하게 되돌아간다.

**중요 — 호출부 책임**: `pipeline_runtime.py`의 `_gate_pass_ratio()`는
`gate_scores.values()`를 제네릭하게 순회해 `not_measured`가 아닌 모든
항목을 통과/차단 계산에 포함시킨다. "코드 검증" 단계는 이미
`min_gate_pass_ratio=0.5`인 실제 차단 임계값이 걸려 있으므로, 이
모듈이 반환하는 점수를 `gate_scores`에 저장할 때 호출부가 반드시
`not_measured`를 `True`로 강제해야 한다(이 모듈 자체는 그 결정에
관여하지 않는다 — v1은 정보 제공 전용, 기존 통과/차단 동작을 바꾸지
않는다)."""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import llm

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "당신은 요구사항 추적성 검토자입니다. 요구사항 문서와 구현 결과물을 "
    "비교해, 요구사항 문서에 나열된 개별 요구사항 각각이 구현 결과물에 "
    "실제로 반영됐는지 판정하세요. 반드시 JSON 객체 하나만 출력하세요: "
    '{"total_requirements": int, "covered": int, "missing": ["..."]}. '
    "total_requirements는 요구사항 문서에서 식별한 개별 요구사항 개수, "
    "covered는 그중 구현 결과물에 실제로 반영된 개수, missing은 반영되지 "
    "않은 요구사항을 간단히 설명한 문자열 목록입니다. 요구사항 문서에서 "
    "개별 요구사항을 하나도 식별할 수 없으면 total_requirements를 0으로 "
    "출력하세요."
)


def assess_traceability(
    conn: sqlite3.Connection, requirements_text: str, implementation_text: str
) -> dict:
    """`{"score": float|None, "status": str, "total_requirements": int,
    "covered": int, "missing": list[str]}`를 반환한다. DB에 아무것도
    쓰지 않는다 — 순수 채점. 어떤 이유로든 실패하면(LLM 에러, JSON
    파싱 실패, `total_requirements` 0) `not_measured`로 안전하게
    폴백한다 — 절대 raise하지 않는다."""
    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
        prompt = (
            f"요구사항 문서:\n{requirements_text}\n\n"
            f"구현 결과물:\n{implementation_text}"
        )
        raw = llm.call_llm(provider, model, _SYSTEM_PROMPT, prompt)
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("no_json_block")
        parsed = json.loads(match.group(0))

        total = parsed.get("total_requirements")
        covered = parsed.get("covered")
        missing = parsed.get("missing", [])
        if not isinstance(total, int) or not isinstance(covered, int):
            raise ValueError("invalid_counts")
        if not isinstance(missing, list) or not all(
            isinstance(m, str) for m in missing
        ):
            missing = []
        if total <= 0:
            return {
                "score": None,
                "status": "not_measured",
                "not_measured": True,
                "passed": False,
                "total_requirements": 0,
                "covered": 0,
                "missing": [],
            }

        covered = max(0, min(covered, total))
        score = covered / total
        return {
            "score": score,
            "status": "pass" if covered == total else "fail",
            "not_measured": False,
            "passed": covered == total,
            "total_requirements": total,
            "covered": covered,
            "missing": missing,
        }
    except Exception:  # noqa: BLE001 — 절대 raise하지 않는다, 항상 not_measured로 폴백
        return {
            "score": None,
            "status": "not_measured",
            "not_measured": True,
            "passed": False,
            "total_requirements": 0,
            "covered": 0,
            "missing": [],
        }
