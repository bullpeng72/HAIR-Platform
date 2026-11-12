"""모델 레지스트리의 provider/모델별 실측 성능 테스트(사용자 요청,
2026-11-12, "ollama, openai 등 프로바이더의 모델 성능 Test하고 결과
표시, 티어 추천 기능").

`agent_optimizer.py`의 "⚡ 최적화 추천"은 **이름 패턴 추정**(모델명에
"code"/"coder"가 들어있으면 precision_fast로 우대 등)이거나, admin이
"모델 레지스트리"에서 **직접 지정한** tier 매핑을 쓴다 — 둘 다 모델을
실제로 호출해보지 않는다. 이 모듈은 그 공백을 메운다: 등록된 모델에
실제로 표준 프롬프트를 보내 **응답 속도를 실측**하고, 그 결과로 tier를
추천한다 — 추정이 아니라 측정이다.

**측정 항목을 의도적으로 단순하게 유지한다**: 벽시계 지연시간(초) +
응답 글자 수 → 글자/초. 진짜 "토큰/초"가 아니다 — provider마다 토크나이저가
달라 공정 비교가 어렵고, `llm.call_llm()`은 텍스트만 반환해 실제 토큰
사용량 자체를 노출하지 않는다(이미 `cost.py`가 "실제 토큰 사용량은
CrewAI 경로(`crew_runs.token_usage`)에서만 실측 가능하다"고 명시해둔
것과 같은 한계 — 없는 값을 지어내지 않는다는 이 코드베이스의 기존
원칙을 그대로 따른다). "글자/초"는 provider 간에도 공정하게 비교 가능한
가장 단순한 대리 지표다.

**tier 추천은 속도 하나만 본다 — 품질 평가가 아니다.** 모델이 "얼마나
똑똑한지"는 이 테스트로 알 수 없다(그건 LLM judge 같은 별도 평가가
필요한, 이번 요청 범위 밖인 문제). 이 추천이 실제로 답하는 질문은
"이 모델이 표준 tier(실시간 응답)가 요구하는 속도를 낼 수 있는가"
하나뿐이다 — 그래서 화면에도 "속도만 반영한 참고용"이라고 명시한다.
"""

from __future__ import annotations

import sqlite3
import time

from hair_platform import audit, llm

_TEST_SYSTEM_PROMPT = "간결하고 명확하게 답하는 어시스턴트입니다."
_TEST_PROMPT = "한국어로 세 문장 이내로, 좋은 제품 기획이란 무엇인지 설명해줘."

# 실측 참고치(2026-11-12, 로컬 27B~7.8B급 모델 3종 벤치마크) —
# bonsai-2:27b 14.7 tok/s, qwen3.8:27b-mlx 15.5 tok/s, exaone3.5:7.8b
# 27.7 tok/s. 한글 기준 토큰당 평균 글자 수를 대략 2.5자로 보고 아래
# 경계값을 글자/초로 환산했다 — provider/모델마다 실제 비율이 달라
# 정확한 토큰/초 환산이 아니라 "참고용 경계"일 뿐이다(화면에도 그렇게
# 표시한다).
FAST_CHARS_PER_SECOND = 50.0
MODERATE_CHARS_PER_SECOND = 20.0


def recommend_tier(chars_per_second: float) -> str:
    """순수 속도 기준 — 품질/능력 판단이 전혀 아니다(모듈 docstring
    참고). `model_registry.OPTIMIZATION_TIERS`와 같은 문자열을 쓴다."""
    if chars_per_second >= FAST_CHARS_PER_SECOND:
        return "standard"
    if chars_per_second >= MODERATE_CHARS_PER_SECOND:
        return "precision_fast"
    return "deep_reasoning"


def run_benchmark(conn: sqlite3.Connection, model_id: int, actor_id: int) -> dict:
    """등록된 모델 하나에 고정 테스트 프롬프트를 실제로 보내 속도를
    재고 결과를 `model_benchmarks`에 쌓는다(이력 누적, 덮어쓰지 않음
    — 모듈 docstring 참고). 실패(모델 미설치·API 키 없음·타임아웃 등)
    도 그대로 기록해 "왜 안 됐는지"가 이력에 남는다."""
    row = conn.execute(
        "SELECT * FROM model_registry WHERE id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    provider, name = row["provider"], row["name"]

    start = time.monotonic()
    try:
        output = llm.call_llm(provider, name, _TEST_SYSTEM_PROMPT, _TEST_PROMPT)
    except Exception as exc:  # noqa: BLE001 — 호출자가 사람이 읽을 메시지로 바꿔 보여준다
        elapsed = time.monotonic() - start
        cur = conn.execute(
            "INSERT INTO model_benchmarks "
            "(model_registry_id, tested_by, status, latency_seconds, error) "
            "VALUES (?, ?, 'failed', ?, ?)",
            (model_id, actor_id, elapsed, str(exc)),
        )
        benchmark_id = cur.lastrowid
        audit.record(
            conn,
            actor_id,
            "model_benchmark_failed",
            "model_registry",
            model_id,
            after={"provider": provider, "name": name, "error": str(exc)},
        )
        conn.commit()
        return {
            "id": benchmark_id,
            "model_id": model_id,
            "provider": provider,
            "name": name,
            "status": "failed",
            "latency_seconds": elapsed,
            "error": str(exc),
        }

    elapsed = time.monotonic() - start
    output_chars = len(output)
    chars_per_second = output_chars / elapsed if elapsed > 0 else 0.0
    tier = recommend_tier(chars_per_second)

    cur = conn.execute(
        "INSERT INTO model_benchmarks "
        "(model_registry_id, tested_by, status, latency_seconds, output_chars, "
        "chars_per_second, recommended_tier) VALUES (?, ?, 'success', ?, ?, ?, ?)",
        (model_id, actor_id, elapsed, output_chars, chars_per_second, tier),
    )
    benchmark_id = cur.lastrowid
    audit.record(
        conn,
        actor_id,
        "model_benchmark_completed",
        "model_registry",
        model_id,
        after={
            "provider": provider,
            "name": name,
            "latency_seconds": elapsed,
            "chars_per_second": chars_per_second,
            "recommended_tier": tier,
        },
    )
    conn.commit()
    return {
        "id": benchmark_id,
        "model_id": model_id,
        "provider": provider,
        "name": name,
        "status": "success",
        "latency_seconds": elapsed,
        "output_chars": output_chars,
        "chars_per_second": chars_per_second,
        "recommended_tier": tier,
    }


def list_benchmarks(conn: sqlite3.Connection, model_id: int) -> list[dict]:
    """이 모델의 실측 이력 전체(최신순) — 모델 레지스트리 화면에서
    "직전 결과"는 [0]만, 이력 전체는 펼쳐서 본다."""
    rows = conn.execute(
        "SELECT * FROM model_benchmarks WHERE model_registry_id = ? "
        "ORDER BY id DESC",
        (model_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_benchmarks_by_model(conn: sqlite3.Connection) -> dict[int, dict]:
    """등록된 전체 모델 각각의 **가장 최근** 실측 결과 1건씩만 —
    `model_registry.list_models()`가 목록 응답에 바로 끼워 넣을 때
    쓴다(모델마다 또 쿼리하지 않고 한 번에 모아옴)."""
    rows = conn.execute(
        "SELECT mb.* FROM model_benchmarks mb "
        "JOIN (SELECT model_registry_id, MAX(id) AS max_id FROM model_benchmarks "
        "GROUP BY model_registry_id) latest "
        "ON latest.model_registry_id = mb.model_registry_id AND latest.max_id = mb.id"
    ).fetchall()
    return {r["model_registry_id"]: dict(r) for r in rows}
