"""처리 용량(2026-11-27, AI Native 분석 4단계 — 7번 "로컬 모델 프로세스의 긴 리드타임·동시 처리량 미측정").

실측으로 파이프라인 한 건은 8~34분이 걸리고, Ollama는 한 모델에 요청을 하나씩 처리한다. 일이 몰리면 대기가 얼마나 늘어나는지,
지금 맡기면 언제 끝나는지를 몰랐다. 작업 큐(`background_jobs`)의 시각 기록으로 계산한다.

- 줄별(대화 응답 · 일반 · 파이프라인 등 긴 작업 — `llm_gate.kind_priority`와 같은 구분) 작업 수, 시작까지 기다린 시간
  (`created_at→started_at`), 실행 시간(`started_at→finished_at`)의 평균·90번째 백분위.
- 바쁜 정도: 기간 중 작업이 하나라도 돌고 있던 시간의 비율(겹치는 작업은 한 번만 센다). 로컬 모델 하나로 돌리면 이 값이
  높을수록 새 일이 줄을 선다.
- 지금 대기·실행 중인 작업, 작업 프로세스, 모델별 동시 실행 상한.
- 지금 파이프라인을 맡기면: 앞에 선 긴 작업 수 × 최근 긴 작업 평균 실행 시간 ÷ 동시 처리 수(로컬 모델 상한, 클라우드면
  최근 평균 대기) + 그 파이프라인의 최근 완료 실행에서 사람 대기를 뺀 시간(중앙값). 추정치다.
"""

from __future__ import annotations

import sqlite3
import statistics

from hair_platform import llm_gate, process_metrics

LANES = {llm_gate.INTERACTIVE: "대화 응답", llm_gate.NORMAL: "일반", llm_gate.BACKGROUND: "파이프라인·긴 작업"}
DEFAULT_DAYS = 7
BUSY_WARN = 0.7
PIPELINE_WAIT_WARN_SECONDS = 600
INTERACTIVE_WAIT_WARN_SECONDS = 30


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[-1]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _busy_fraction(intervals: list[tuple[float, float]], window_seconds: float) -> float | None:
    """겹치는 구간을 합친 '무엇이든 돌고 있던 시간' ÷ 기간."""
    if window_seconds <= 0:
        return None
    total = 0.0
    cur_s = cur_e = None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None and cur_s is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None and cur_s is not None:
        total += cur_e - cur_s
    return min(1.0, total / window_seconds)


def _default_slots(conn: sqlite3.Connection) -> tuple[int | None, str]:
    """기본 모델의 동시 처리 수 — 로컬(Ollama)이면 상한(기본 1), 클라우드면 None(상한 없음)."""
    from hair_platform import llm

    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
    except Exception:  # noqa: BLE001 — 모델 설정이 없어도 화면은 뜬다
        return 1, "(기본 모델 미설정)"
    if provider != "ollama":
        return None, f"{provider}:{model}"
    return (llm_gate.limit_for(provider, model) or 1), f"{provider}:{model}"


def report(conn: sqlite3.Connection, days: int = DEFAULT_DAYS) -> dict:
    now = process_metrics._now()
    rows = conn.execute(
        "SELECT kind, status, priority, created_at, started_at, finished_at FROM background_jobs "
        "WHERE created_at >= datetime('now', ?)", (f"-{int(days)} days",)).fetchall()
    lanes: dict[int, dict] = {k: {"waits": [], "runs": [], "jobs": 0, "done": 0, "failed": 0} for k in LANES}
    intervals: list[tuple[float, float]] = []
    epoch = now
    for r in rows:
        lane = r["priority"] if r["priority"] in LANES else llm_gate.kind_priority(r["kind"])
        b = lanes[lane]
        b["jobs"] += 1
        b["done"] += r["status"] == "done"
        b["failed"] += r["status"] in ("failed", "interrupted")
        c, s, f = process_metrics._ts(r["created_at"]), process_metrics._ts(r["started_at"]), process_metrics._ts(r["finished_at"])
        if c and s:
            b["waits"].append(max(0.0, (s - c).total_seconds()))
        if s and f:
            b["runs"].append(max(0.0, (f - s).total_seconds()))
            intervals.append(((s - epoch).total_seconds(), (f - epoch).total_seconds()))
        elif s and r["status"] == "running":
            intervals.append(((s - epoch).total_seconds(), 0.0))
    lane_rows = []
    for k, label in LANES.items():
        b = lanes[k]
        lane_rows.append({"lane": k, "label": label, "jobs": b["jobs"], "done": b["done"], "failed": b["failed"],
                          "wait_avg_seconds": _mean(b["waits"]), "wait_p90_seconds": _p90(b["waits"]),
                          "run_avg_seconds": _mean(b["runs"]), "run_p90_seconds": _p90(b["runs"])})
    queued: dict[int, int] = dict.fromkeys(LANES, 0)
    running: dict[int, int] = dict.fromkeys(LANES, 0)
    for r in conn.execute("SELECT kind, status, priority FROM background_jobs WHERE status IN ('queued', 'running')"):
        lane = r["priority"] if r["priority"] in LANES else llm_gate.kind_priority(r["kind"])
        (queued if r["status"] == "queued" else running)[lane] += 1
    slots, model_label = _default_slots(conn)
    busy = _busy_fraction(intervals, days * 86400)
    bg = lanes[llm_gate.BACKGROUND]
    bg_run = _mean(bg["runs"])
    ahead = queued[llm_gate.BACKGROUND] + running[llm_gate.BACKGROUND]
    if slots is None:
        queue_wait = _mean(bg["waits"]) or 0.0
    elif bg_run is None:
        queue_wait = None
    else:
        queue_wait = ahead * bg_run / slots
    from hair_platform import job_queue

    out = {
        "window_days": days, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "lanes": lane_rows,
        "now": {"queued": {LANES[k]: v for k, v in queued.items()}, "running": {LANES[k]: v for k, v in running.items()},
                "workers": len(job_queue.live_workers(conn)), "worker_mode": job_queue.mode(),
                "model_gate": llm_gate.snapshot()},
        "default_model": model_label, "slots": slots,
        "busy_fraction": busy,
        "pipeline_queue_wait_seconds": queue_wait,
        "pipeline_jobs_ahead": ahead,
    }
    out["signals"] = _signals(out, lanes)
    return out


def _signals(r: dict, lanes: dict) -> list[dict]:
    sig = []
    if r["busy_fraction"] is not None and r["busy_fraction"] >= BUSY_WARN:
        sig.append({"level": "warn", "text": f"최근 {r['window_days']}일 중 {round(r['busy_fraction'] * 100)}% 동안 작업이 돌고 있었습니다 — "
                    "일이 더 늘면 줄이 길어집니다. Ollama 동시 처리(OLLAMA_NUM_PARALLEL과 모델 레지스트리 동시 실행 상한)·"
                    "가벼운 단계에 작은 모델·클라우드 모델 보조를 검토하세요."})
    bg_wait = _p90(lanes[llm_gate.BACKGROUND]["waits"])
    if bg_wait is not None and bg_wait >= PIPELINE_WAIT_WARN_SECONDS:
        sig.append({"level": "warn", "text": f"파이프라인·긴 작업 10건 중 1건은 시작까지 {round(bg_wait / 60)}분 이상 기다렸습니다."})
    it_wait = _p90(lanes[llm_gate.INTERACTIVE]["waits"])
    if it_wait is not None and it_wait >= INTERACTIVE_WAIT_WARN_SECONDS:
        sig.append({"level": "warn", "text": f"대화 응답 10건 중 1건은 시작까지 {round(it_wait)}초 이상 기다렸습니다 — 작업 프로세스·동시 실행 상한을 확인하세요."})
    if r["now"]["worker_mode"] == "external" and r["now"]["workers"] == 0:
        sig.append({"level": "warn", "text": "작업 프로세스(hair worker)가 하나도 살아 있지 않습니다 — 맡긴 일이 시작되지 않습니다."})
    return sig


def template_run_seconds(conn: sqlite3.Connection, template_id: int) -> float | None:
    """최근 90일 완료 실행에서 사람 결재 대기를 뺀 시간(중앙값) — AI 작업 + 그 밖."""
    d = process_metrics.template_detail(conn, template_id, 90)
    runs = [r for r in d["recent_runs"] if r["status"] == "completed" and r["lead_seconds"] is not None]
    vals = [max(0.0, r["lead_seconds"] - (r["wait_seconds"] or 0)) for r in runs]
    return statistics.median(vals) if vals else None


def estimates(conn: sqlite3.Connection, template_ids: list[int], cap: dict | None = None,
              run_seconds: dict[int, float | None] | None = None) -> dict[int, dict]:
    """파이프라인별 '지금 맡기면' — 시작까지 + 실행 시간(사람 결재 대기 제외). `run_seconds`를 주면(프로세스 성과 요약이 이미
    계산한 값) 다시 읽지 않는다."""
    cap = cap or report(conn)
    wait = cap["pipeline_queue_wait_seconds"]
    out = {}
    for tid in template_ids:
        run = run_seconds[tid] if run_seconds is not None and tid in run_seconds else template_run_seconds(conn, tid)
        out[tid] = {"queue_wait_seconds": wait, "run_seconds": run,
                    "total_seconds": (wait or 0) + run if run is not None else None}
    return out
