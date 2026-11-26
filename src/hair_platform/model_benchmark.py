"""모델 레지스트리의 provider/모델별 실측 성능 테스트(사용자 요청 2026-11-12, 정교화 2026-11-27).

2026-11-27 분석("성능 테스트 로직을 분석하고 정교화 방안을 제안해줘" → "A → C → B 순서로 진행해줘")에서 확인한
예전 방식(짧은 질문 1회, 답변 글자 수 ÷ 벽시계 시간, 그 숫자 하나로 tier)의 문제:
- **모델 로드 시간이 결과를 좌우** — 같은 Ornith 35B가 첫 호출 21.9자/초, 바로 다음 73.2자/초(9.7초 중 5.6초가 로드).
- **생각(추론) 토큰을 세지 않음** — 실제 생성은 exaone보다 1.6배 빠른데(45.5 대 27.8 tok/s) 더 느린 모델로 나왔다.
- **한 번만, 짧은 질문만** — 실제 단계는 수천~수만 토큰을 읽고 시작한다(6.5K토큰 읽기: exaone 15.0초, Ornith 7.9초).
- **"느리면 심층추론"이라는 뒤집힌 추천**, 도구 호출·수치 보존 같은 능력은 전혀 보지 않음(exaone은 도구 미지원).

그래서 두 가지 측정을 둔다.
- **빠른 측정(quick, A)** — 예열 호출 1회(첫 로드 시간은 따로 기록) 후 짧은 질문 3회의 중앙값. 지표를 나눈다:
  생성 tok/s(생각 토큰 포함), 입력 처리 tok/s, 첫 응답까지 시간. 생각 기능이 있는 Ollama 모델은 "생각 끔"도 3회 잰다.
  tier는 생성 속도로만(단계 출력 1,500토큰 기준 시간) — 느리다고 심층추론을 추천하지 않는다.
- **전체 점검(full, A + C + B)** — 위에 더해
  - C 능력 점검(AI 채점 없이 규칙으로): 도구 호출, JSON 형식, 입력 수치 보존, 한국어(영어 독백 없음), 생각 끄기 준수.
  - B 실제 업무 시나리오: 긴 문서(약 6~7K토큰) 읽기 → 입력 처리 속도 → "전형적인 단계 1회 예상 시간"
    (입력 8,000토큰 읽기 + 출력 1,500토큰). tier는 이 예상 시간과 능력 점검으로 정한다.

측정값은 provider가 알려 주는 토큰 수·시간(`llm_usage.measure`)을 쓴다 — Ollama는 로드·입력 처리·생성 시간을 따로
알려 주고, 클라우드는 토큰 수만 알려 주므로 벽시계 시간으로 나눈다(클라우드의 입력 처리 속도는 근사).
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time

from hair_platform import audit, llm, llm_usage

_TEST_SYSTEM_PROMPT = "간결하고 명확하게 답하는 어시스턴트입니다."
_TEST_PROMPT = "한국어로 세 문장 이내로, 좋은 제품 기획이란 무엇인지 설명해줘."
REPEATS = 3

# 전형적인 파이프라인 단계 1회(실측 근거: 인사 파이프라인 단계 입력 5~10K토큰, 출력 1~2K토큰).
STAGE_INPUT_TOKENS = 8000
STAGE_OUTPUT_TOKENS = 1500
# 예상 시간 경계(초) — 표준: 대화처럼 1분 안 / 정밀·신속: 3분 안.
STANDARD_MAX_SECONDS = 60.0
PRECISION_MAX_SECONDS = 180.0

# 예전 화면·테스트와의 호환용(글자/초 경계) — 새 추천에는 쓰지 않는다.
FAST_CHARS_PER_SECOND = 50.0
MODERATE_CHARS_PER_SECOND = 20.0

TIER_ORDER = ("standard", "precision_fast", "deep_reasoning")


def recommend_tier(chars_per_second: float) -> str:
    """예전 글자/초 기준 — 호환용으로만 남긴다(새 측정은 `recommend_tiers_*`)."""
    if chars_per_second >= FAST_CHARS_PER_SECOND:
        return "standard"
    if chars_per_second >= MODERATE_CHARS_PER_SECOND:
        return "precision_fast"
    return "deep_reasoning"


# ------------------------------------------------------------------ 호출 하나 재기


def _timed_call(provider: str, name: str, system: str, prompt: str, think: bool | None = None) -> dict:
    with llm_usage.measure() as items, llm.reasoning(think):
        start = time.monotonic()
        output = llm.call_llm(provider, name, system, prompt)
        wall = time.monotonic() - start
    m = items[-1] if items else {}
    out_tokens = m.get("output_tokens") or None
    in_tokens = m.get("input_tokens") or None
    eval_s = m.get("eval_s") or 0.0
    prompt_s = m.get("prompt_s") or 0.0
    if out_tokens and eval_s > 0:
        gen_tps = out_tokens / eval_s
    elif out_tokens and wall > 0:
        gen_tps = out_tokens / wall  # 클라우드 — 입력 처리 시간이 섞인 근사
    else:
        gen_tps = None
    return {
        "output": output, "wall_s": wall, "chars": len(output or ""), "input_tokens": in_tokens,
        "output_tokens": out_tokens, "gen_tps": gen_tps,
        "prefill_tps": (in_tokens / prompt_s) if in_tokens and prompt_s > 0 else None,
        "first_token_s": (m.get("load_s", 0.0) + prompt_s) if "prompt_s" in m else None,
        "load_s": m.get("load_s"), "thinking_chars": m.get("thinking_chars", 0),
    }


def _median(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def _summary(runs: list[dict]) -> dict:
    return {
        "runs": len(runs),
        "wall_s": _median(r["wall_s"] for r in runs),
        "chars": _median(r["chars"] for r in runs),
        "gen_tps": _median(r["gen_tps"] for r in runs),
        "prefill_tps": _median(r["prefill_tps"] for r in runs),
        "first_token_s": _median(r["first_token_s"] for r in runs),
        "thinking_chars": _median(r["thinking_chars"] for r in runs),
        "wall_spread_s": round(max(r["wall_s"] for r in runs) - min(r["wall_s"] for r in runs), 3) if runs else None,
    }


def _capabilities(provider: str, name: str) -> list[str] | None:
    """Ollama 모델이 스스로 밝힌 능력(tools·thinking 등). 클라우드·알 수 없으면 None."""
    if provider != "ollama":
        return None
    from hair_platform import model_recommend

    show = model_recommend._ollama_show(name)
    return list(show.get("capabilities") or []) if show else None


# ------------------------------------------------------------------ C. 능력 점검


_TOOL_PROMPT = "사원번호 E-1024 직원의 연봉을 도구로 조회한 뒤, 조회한 금액을 그대로 한 줄로 답해."


def _check_tools(provider: str, name: str, caps: list[str] | None) -> dict:
    if caps is not None and "tools" not in caps:
        return {"passed": False, "reason": "모델이 도구 호출을 지원하지 않는다고 밝힘(Ollama 모델 정보)"}
    from pydantic import BaseModel

    calls: list[dict] = []

    class _Args(BaseModel):
        employee_id: str = ""

    class _SalaryTool:
        name = "lookup_salary"
        description = "사원번호로 직원 연봉(원)을 조회한다."
        args_schema = _Args

        def run(self, **kwargs):
            calls.append(kwargs)
            return "E-1024 연봉: 65,000,000원"

    try:
        with llm.reasoning(False):
            out = llm.call_llm_with_tools(provider, name, _TEST_SYSTEM_PROMPT, _TOOL_PROMPT, [_SalaryTool()], max_iterations=3)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "reason": f"도구 호출 중 오류: {str(exc)[:200]}"}
    used = bool(calls)
    digits = re.sub(r"[^0-9]", "", out or "")
    answered = "65000000" in digits or "6500만" in (out or "").replace(" ", "")
    return {"passed": used and answered, "called": used, "answer_ok": answered,
            "reason": "도구를 부르고 결과를 답에 옮김" if used and answered else
            ("도구를 부르지 않음" if not used else "도구 결과(65,000,000원)를 답에 옮기지 못함")}


_JSON_PROMPT = ("다음 지원자 정보에서 값을 뽑아 JSON 하나만 출력해. 키: salary(원 단위 정수), start_date(YYYY-MM-DD), "
                "years(정수). 설명·코드 블록 없이 JSON만.\n\n지원자 A — 희망 연봉 72,500,000원, 입사 가능일 2027년 3월 2일, 경력 7년.")


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        value = json.loads(m.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _check_json_and_numbers(provider: str, name: str) -> tuple[dict, dict]:
    try:
        r = _timed_call(provider, name, _TEST_SYSTEM_PROMPT, _JSON_PROMPT, think=False)
    except Exception as exc:  # noqa: BLE001
        err = {"passed": False, "reason": f"호출 오류: {str(exc)[:200]}"}
        return err, dict(err)
    data = _parse_json(r["output"])
    if data is None:
        return ({"passed": False, "reason": "JSON으로 읽을 수 없는 답"},
                {"passed": False, "reason": "JSON을 읽지 못해 수치를 확인할 수 없음"})
    keys_ok = all(k in data for k in ("salary", "start_date", "years"))
    fmt = {"passed": keys_ok, "reason": "JSON 형식·키 일치" if keys_ok else f"키 누락: {sorted(set(['salary', 'start_date', 'years']) - set(data))}"}
    salary = re.sub(r"[^0-9]", "", str(data.get("salary", "")))
    years = re.sub(r"[^0-9]", "", str(data.get("years", "")))
    wrong = [f"{k}={data.get(k)!r}" for k, ok in (("salary", salary == "72500000"),
                                                  ("start_date", str(data.get("start_date")) == "2027-03-02"),
                                                  ("years", years == "7")) if not ok]
    nums = {"passed": not wrong, "reason": "연봉·날짜·경력 그대로" if not wrong else "바뀐 값: " + ", ".join(wrong)}
    return fmt, nums


_KOREAN_PROMPT = "팀 회의록을 요약할 때 지켜야 할 원칙 세 가지를 한국어로 설명해줘."


def _check_korean(provider: str, name: str) -> dict:
    from hair_platform import language_qa

    try:
        r = _timed_call(provider, name, _TEST_SYSTEM_PROMPT, _KOREAN_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "reason": f"호출 오류: {str(exc)[:200]}"}
    text = r["output"] or ""
    _, narration = language_qa.strip_narration(text)
    latin = len(re.findall(r"[A-Za-z]", text))
    hangul = len(re.findall(r"[가-힣]", text))
    issues = language_qa.find_issue_segments(text)
    ok = language_qa.is_mostly_korean(text) and narration == 0 and latin <= hangul * 0.15 and not issues
    reasons = []
    if narration:
        reasons.append(f"영어 작업 독백 {narration}줄")
    if latin > hangul * 0.15:
        reasons.append(f"영문자 비율 높음({latin}/{hangul})")
    if issues:
        reasons.append("한자·깨진 글자: " + " / ".join(issues[:2]))
    if not language_qa.is_mostly_korean(text):
        reasons.append("한국어가 주가 아님")
    return {"passed": ok, "reason": "한국어로만 답함" if ok else ", ".join(reasons)}


def _check_think_off(off_runs: list[dict]) -> dict:
    leaked = [r for r in off_runs if r["thinking_chars"] or "<think>" in (r["output"] or "")]
    return {"passed": not leaked,
            "reason": "생각 끄기를 지킴" if not leaked else f"생각 끔인데 사고 과정 출력({len(leaked)}/{len(off_runs)}회)"}


# ------------------------------------------------------------------ B. 긴 문서


_LONG_DOC = ("제3조(계약 기간) 본 계약의 기간은 2027년 1월 1일부터 2027년 12월 31일까지로 한다. "
             "갑과 을은 상호 협의하여 연장할 수 있다. ") * 120


def _check_long_doc(provider: str, name: str) -> dict:
    try:
        r = _timed_call(provider, name, _TEST_SYSTEM_PROMPT, _LONG_DOC + "\n\n위 문서의 계약 기간을 한 줄로 답해.", think=False)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "reason": f"호출 오류: {str(exc)[:200]}"}
    prefill = r["prefill_tps"]
    if prefill is None and r["input_tokens"] and r["output_tokens"] and r["gen_tps"]:
        # 클라우드 근사 — 벽시계에서 생성 시간을 빼고 남은 시간을 입력 처리로 본다.
        gen_part = r["output_tokens"] / r["gen_tps"] if r["gen_tps"] else 0
        prefill = r["input_tokens"] / max(r["wall_s"] - gen_part, 0.1)
    answer_ok = "2027" in (r["output"] or "") and ("1월 1일" in r["output"] or "01-01" in r["output"] or "1.1" in r["output"])
    truncated = bool(r["input_tokens"]) and r["input_tokens"] < 4000
    reason = f"{r['input_tokens'] or '?'}토큰 읽기 {r['wall_s']:.1f}초"
    if truncated:
        reason += " — 입력이 잘림(컨텍스트 한도가 작음: 모델 레지스트리의 컨텍스트 한도를 늘리세요)"
    if not answer_ok:
        reason += " — 문서 내용(계약 기간)을 답하지 못함"
    return {"passed": answer_ok and not truncated, "prefill_tps": round(prefill, 1) if prefill else None,
            "input_tokens": r["input_tokens"], "wall_s": round(r["wall_s"], 2), "reason": reason}


# ------------------------------------------------------------------ 추천


def estimate_stage_seconds(gen_tps: float | None, prefill_tps: float | None) -> float | None:
    if not gen_tps:
        return None
    read = STAGE_INPUT_TOKENS / prefill_tps if prefill_tps else 0.0
    return round(read + STAGE_OUTPUT_TOKENS / gen_tps, 1)


def recommend_tiers_quick(gen_tps: float | None) -> list[str]:
    """빠른 측정 — 생성 속도만으로 표준·정밀·신속만 추천한다(심층추론은 능력 점검이 필요)."""
    est = estimate_stage_seconds(gen_tps, None)
    if est is None:
        return []
    if est <= STANDARD_MAX_SECONDS:
        return ["standard", "precision_fast"]
    if est <= PRECISION_MAX_SECONDS:
        return ["precision_fast"]
    return []


def recommend_tiers_full(est: float | None, checks: dict, thinking_capable: bool) -> tuple[list[str], list[str]]:
    def ok(key):
        return bool((checks.get(key) or {}).get("passed"))

    tiers, warnings = [], []
    if est is not None and est <= STANDARD_MAX_SECONDS and ok("json") and ok("korean"):
        tiers.append("standard")
    if est is not None and est <= PRECISION_MAX_SECONDS and ok("numbers") and ok("korean"):
        tiers.append("precision_fast")
    if thinking_capable and ok("tools") and ok("numbers"):
        tiers.append("deep_reasoning")
    if "tools" in checks and not ok("tools"):
        warnings.append("도구를 쓰는 Agent에 배정하지 마세요(도구 호출 점검 실패)")
    if "numbers" in checks and not ok("numbers"):
        warnings.append("수치를 옮기는 업무(공고·보고서·재무)에 주의 — 입력 수치를 바꿈")
    if "think_off" in checks and not ok("think_off"):
        warnings.append("추론 끔 설정을 무시함 — 결과에 사고 과정이 섞일 수 있음")
    if "korean" in checks and not ok("korean"):
        warnings.append("한국어 결과물에 영어·한자 혼입 — 언어 교정이 필요")
    if est is not None and est > PRECISION_MAX_SECONDS:
        warnings.append(f"단계 1회 예상 {est:.0f}초 — 긴 업무는 오래 걸림")
    return tiers, warnings


# ------------------------------------------------------------------ 실행


def _record_failure(conn, model_id, actor_id, provider, name, elapsed, exc, mode) -> dict:
    cur = conn.execute(
        "INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, error, mode) "
        "VALUES (?, ?, 'failed', ?, ?, ?)",
        (model_id, actor_id, elapsed, str(exc), mode),
    )
    audit.record(conn, actor_id, "model_benchmark_failed", "model_registry", model_id,
                 after={"provider": provider, "name": name, "error": str(exc), "mode": mode})
    conn.commit()
    return {"id": cur.lastrowid, "model_id": model_id, "provider": provider, "name": name, "status": "failed",
            "latency_seconds": elapsed, "error": str(exc), "mode": mode}


def run_benchmark(conn: sqlite3.Connection, model_id: int, actor_id: int, mode: str = "quick") -> dict:
    """빠른 측정(quick) 또는 전체 점검(full)을 실제로 돌려 `model_benchmarks`에 쌓는다(이력 누적).
    예열 호출이 실패하면(모델 미설치·API 키 없음 등) 실패로 기록한다."""
    if mode not in ("quick", "full"):
        raise ValueError("invalid_mode")
    row = conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)).fetchone()
    if row is None:
        raise ValueError("model_not_found")
    provider, name = row["provider"], row["name"]

    start = time.monotonic()
    try:
        warm = _timed_call(provider, name, _TEST_SYSTEM_PROMPT, _TEST_PROMPT)
    except Exception as exc:  # noqa: BLE001 — 호출자가 사람이 읽을 메시지로 바꿔 보여준다
        return _record_failure(conn, model_id, actor_id, provider, name, time.monotonic() - start, exc, mode)

    caps = _capabilities(provider, name)
    thinking_capable = provider != "ollama" or (caps is not None and "thinking" in caps)
    details: dict = {
        "mode": mode,
        "first_call_s": round(warm["wall_s"], 2),
        "load_s": round(warm["load_s"], 2) if warm.get("load_s") is not None else None,
        "capabilities": caps,
    }
    try:
        default_runs = [_timed_call(provider, name, _TEST_SYSTEM_PROMPT, _TEST_PROMPT) for _ in range(REPEATS)]
        details["default"] = _summary(default_runs)
        off_runs: list[dict] = []
        if provider == "ollama" and caps is not None and "thinking" in caps:
            off_runs = [_timed_call(provider, name, _TEST_SYSTEM_PROMPT, _TEST_PROMPT, think=False) for _ in range(REPEATS)]
            details["think_off"] = _summary(off_runs)
    except Exception as exc:  # noqa: BLE001
        return _record_failure(conn, model_id, actor_id, provider, name, time.monotonic() - start, exc, mode)

    # 단계 예상에는 그 모델로 실제 낼 수 있는 가장 빠른 설정(생각 끔이 가능하면 그쪽) 생성 속도를 쓴다.
    best = details.get("think_off") or details["default"]
    gen_tps = best["gen_tps"]
    if mode == "quick":
        tiers = recommend_tiers_quick(gen_tps)
        warnings: list[str] = [] if tiers else ["생성이 느려 표준·정밀 tier를 권하지 않음 — 전체 점검으로 능력을 확인하세요"]
        details["estimated_stage_s"] = estimate_stage_seconds(gen_tps, None)
        details["estimate_basis"] = f"출력 {STAGE_OUTPUT_TOKENS:,}토큰 생성 시간(입력 읽기 제외 — 전체 점검에서 측정)"
    else:
        checks: dict = {}
        checks["tools"] = _check_tools(provider, name, caps)
        checks["json"], checks["numbers"] = _check_json_and_numbers(provider, name)
        checks["korean"] = _check_korean(provider, name)
        if off_runs:
            checks["think_off"] = _check_think_off(off_runs)
        checks["long_doc"] = _check_long_doc(provider, name)
        details["checks"] = checks
        prefill = checks["long_doc"].get("prefill_tps") or best.get("prefill_tps")
        est = estimate_stage_seconds(gen_tps, prefill)
        details["estimated_stage_s"] = est
        details["estimate_basis"] = f"입력 {STAGE_INPUT_TOKENS:,}토큰 읽기 + 출력 {STAGE_OUTPUT_TOKENS:,}토큰 생성"
        tiers, warnings = recommend_tiers_full(est, checks, thinking_capable)
    details["recommended_tiers"] = tiers
    details["warnings"] = warnings
    details["total_s"] = round(time.monotonic() - start, 1)

    d = details["default"]
    latency = d["wall_s"] or warm["wall_s"]
    chars = int(d["chars"] or 0)
    cps = chars / latency if latency else 0.0
    primary = tiers[0] if tiers else None
    cur = conn.execute(
        "INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, output_chars, "
        "chars_per_second, recommended_tier, mode, details) VALUES (?, ?, 'success', ?, ?, ?, ?, ?, ?)",
        (model_id, actor_id, latency, chars, cps, primary, mode, json.dumps(details, ensure_ascii=False)),
    )
    audit.record(conn, actor_id, "model_benchmark_completed", "model_registry", model_id,
                 after={"provider": provider, "name": name, "mode": mode, "gen_tps": gen_tps,
                        "estimated_stage_s": details.get("estimated_stage_s"), "recommended_tiers": tiers})
    conn.commit()
    return {"id": cur.lastrowid, "model_id": model_id, "provider": provider, "name": name, "status": "success",
            "mode": mode, "latency_seconds": latency, "output_chars": chars, "chars_per_second": cps,
            "recommended_tier": primary, "details": details}


def _row(r: sqlite3.Row) -> dict:
    out = dict(r)
    raw = out.get("details")
    try:
        out["details"] = json.loads(raw) if raw else None
    except ValueError:
        out["details"] = None
    return out


def list_benchmarks(conn: sqlite3.Connection, model_id: int) -> list[dict]:
    """이 모델의 실측 이력 전체(최신순)."""
    rows = conn.execute(
        "SELECT * FROM model_benchmarks WHERE model_registry_id = ? ORDER BY id DESC", (model_id,)
    ).fetchall()
    return [_row(r) for r in rows]


def latest_benchmarks_by_model(conn: sqlite3.Connection) -> dict[int, dict]:
    """등록된 모델마다 가장 최근 결과 1건 + 가장 최근 **전체 점검** 1건(`latest_full`, 빠른 측정이 더 최근일 때)."""
    rows = conn.execute(
        "SELECT mb.* FROM model_benchmarks mb "
        "JOIN (SELECT model_registry_id, MAX(id) AS max_id FROM model_benchmarks "
        "GROUP BY model_registry_id) latest "
        "ON latest.model_registry_id = mb.model_registry_id AND latest.max_id = mb.id"
    ).fetchall()
    result = {r["model_registry_id"]: _row(r) for r in rows}
    fulls = conn.execute(
        "SELECT mb.* FROM model_benchmarks mb "
        "JOIN (SELECT model_registry_id, MAX(id) AS max_id FROM model_benchmarks "
        "WHERE mode = 'full' AND status = 'success' GROUP BY model_registry_id) latest "
        "ON latest.model_registry_id = mb.model_registry_id AND latest.max_id = mb.id"
    ).fetchall()
    for r in fulls:
        item = result.get(r["model_registry_id"])
        if item is not None and item["id"] != r["id"]:
            item["latest_full"] = _row(r)
    return result
