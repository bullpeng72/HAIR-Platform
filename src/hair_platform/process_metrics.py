"""프로세스 성과 측정(2026-11-27, AI Native 프로세스 분석 1단계 — 1번 "프로세스 성과 측정", 8번 "전체 흐름 보기").

지금까지 지표는 실행 한 건·업무 한 건 단위(품질 점수·토큰 비용·평균 실행 시간·결재 수)뿐이라 "이 일이 얼마나
빨라지고 좋아졌나"에 답할 수 없었다. 이미 쌓이는 기록만으로 파이프라인(=업무 프로세스)별 성과를 계산한다.
새로 기록하는 값은 사람이 적는 "기준 시간"(예전 방식으로 이 일이 한 건에 걸리던 시간) 하나뿐이다.

쓰는 기록과 뜻
- 리드타임: `pipeline_runs.created_at` → `finished_at`(완료된 실행만).
- AI 작업 시간: 단계 기록마다 `started_at`(단계 시작) → `created_at`(단계 기록은 AI가 끝났을 때 생긴다).
- 사람 대기 시간: 사람 결재가 걸린 단계 기록의 `created_at` → 그 결재의 마지막 결정 시각(`audit_log`의
  `approval_approved`/`approval_rejected` — 두 실행 엔진 모두 결재함 경로로 결정하므로 공통으로 남는다).
- 그 밖(작업 순서 대기 등): 리드타임에서 위 둘을 뺀 나머지(0 미만이면 0). 작업 큐 대기·단계 사이 준비 시간이다.
- 재작업: 품질 검사 실패로 다시 한 시도(`failed_retrying`) + 사람이 반려한 시도(`rejected`).
- 한 번에 완료: 재작업도, 사람 검토로 넘어간 실패(`escalated`)도 없이 끝난 완료 실행.
- 자동 처리율: 통과한 단계 결과 중 사람 결재 없이 넘어간 비율.
- 비용: 실행 기간 동안 그 업무(task)로 기록된 LLM 사용량(`llm_usage.task_id`) — 단가가 없는 모델이 섞이면
  `cost_partial`. 같은 업무에 동시에 다른 실행이 있으면 함께 잡힐 수 있다(추정치).

시각은 모두 SQLite `datetime('now')`(UTC, "YYYY-MM-DD HH:MM:SS")로 저장돼 그대로 비교한다.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

from hair_platform import audit

PERIOD_CHOICES = (7, 30, 90, 0)  # 0 = 전체 기간
DEFAULT_PERIOD_DAYS = 30
TREND_WEEKS = 8
MAX_BASELINE_MINUTES = 60 * 24 * 30  # 한 건에 30일 — 그보다 길면 입력 실수로 본다

# 개선 신호 기준 — 표본이 너무 적으면 비율이 의미가 없어 완료 3건 이상일 때만 낸다.
_MIN_SAMPLE = 3
_LOW_FIRST_PASS = 0.5
_HIGH_REWORK_PER_RUN = 1.0
_HIGH_FAILURE_RATE = 0.3

_REWORK_STATUSES = ("failed_retrying", "rejected")
_PASSED_STATUSES = ("passed", "approved")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _normalize_days(days: int | None) -> int:
    if days is None:
        return DEFAULT_PERIOD_DAYS
    days = int(days)
    if days not in PERIOD_CHOICES:
        raise ValueError("period_days_invalid")
    return days


def _cost_of(row) -> float | None:
    if row["price_input_per_1m"] is None or row["price_output_per_1m"] is None:
        return None
    return (row["prompt_tokens"] / 1_000_000 * row["price_input_per_1m"]
            + row["completion_tokens"] / 1_000_000 * row["price_output_per_1m"])


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _load_runs(conn: sqlite3.Connection, days: int, template_id: int | None = None) -> list[dict]:
    """기간 안에 시작한 실행과 그 단계 기록·결재 결정 시각·사용량을 한 번에 모은다."""
    where, params = [], []
    if days:
        where.append("r.created_at >= datetime('now', ?)")
        params.append(f"-{days} days")
    if template_id is not None:
        where.append("r.pipeline_template_id = ?")
        params.append(template_id)
    sql = ("SELECT r.id, r.pipeline_template_id, r.task_id, r.status, r.current_stage_seq, r.created_at, "
           "r.finished_at FROM pipeline_runs r" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY r.id")
    runs = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not runs:
        return runs
    by_id = {r["id"]: r for r in runs}
    for r in runs:
        r["stages"] = []
    has_started = _has_column(conn, "pipeline_run_stages", "started_at")
    started_col = "rs.started_at" if has_started else "NULL"
    # SQLite 변수 개수 제한을 피하려고 묶음으로 나눠 조회한다.
    run_ids = list(by_id)
    stage_rows: list[dict] = []
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        stage_rows += [dict(s) for s in conn.execute(
            f"SELECT rs.id, rs.pipeline_run_id, rs.stage_id, rs.attempt, rs.status, rs.created_at, "
            f"{started_col} AS started_at, s.seq_order, s.name AS stage_name FROM pipeline_run_stages rs "
            f"JOIN pipeline_stages s ON s.id = rs.stage_id WHERE rs.pipeline_run_id IN ({marks}) ORDER BY rs.id",
            chunk,
        ).fetchall()]
    decisions: dict[int, dict] = {}
    stage_ids = [s["id"] for s in stage_rows]
    for i in range(0, len(stage_ids), 500):
        chunk = stage_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for d in conn.execute(
            "SELECT a.subject_id AS stage_row_id, COUNT(DISTINCT a.id) AS approvals, "
            "MAX(l.created_at) AS decided_at, "
            "SUM(CASE WHEN l.action = 'approval_rejected' THEN 1 ELSE 0 END) AS rejections "
            "FROM approvals a LEFT JOIN audit_log l ON l.subject_type = 'approval' AND l.subject_id = a.id "
            "AND l.action IN ('approval_approved', 'approval_rejected') "
            f"WHERE a.subject_type = 'pipeline_run_stage' AND a.subject_id IN ({marks}) GROUP BY a.subject_id",
            chunk,
        ).fetchall():
            decisions[d["stage_row_id"]] = dict(d)
    for s in stage_rows:
        d = decisions.get(s["id"])
        s["human"] = d is not None
        s["decided_at"] = d["decided_at"] if d else None
        by_id[s["pipeline_run_id"]]["stages"].append(s)
    _attach_usage(conn, runs)
    return runs


def _attach_usage(conn: sqlite3.Connection, runs: list[dict]) -> None:
    for r in runs:
        r["tokens"], r["cost"], r["cost_partial"] = 0, 0.0, False
    if not _has_column(conn, "llm_usage", "task_id"):
        return
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    for r in runs:
        rows = conn.execute(
            "SELECT u.prompt_tokens, u.completion_tokens, m.price_input_per_1m, m.price_output_per_1m "
            "FROM llm_usage u LEFT JOIN model_registry m ON m.provider = u.provider AND m.name = u.model "
            "WHERE u.task_id = ? AND u.created_at >= ? AND u.created_at <= ?",
            (r["task_id"], r["created_at"], r["finished_at"] or now),
        ).fetchall()
        for u in rows:
            r["tokens"] += u["prompt_tokens"] + u["completion_tokens"]
            c = _cost_of(u)
            if c is None:
                r["cost_partial"] = True
            else:
                r["cost"] += c


def _run_metrics(run: dict, now: datetime) -> dict:
    """실행 한 건의 시간 분해·재작업·사람 개입."""
    ai = 0.0
    wait = 0.0
    for s in run["stages"]:
        a = _seconds(_ts(s["started_at"]), _ts(s["created_at"]))
        s["ai_seconds"] = a
        if a is not None:
            ai += a
        w = None
        if s["human"]:
            end = _ts(s["decided_at"]) if s["decided_at"] else (now if run["status"] == "paused_for_approval" else None)
            w = _seconds(_ts(s["created_at"]), end)
        s["wait_seconds"] = w
        if w is not None:
            wait += w
    lead = _seconds(_ts(run["created_at"]), _ts(run["finished_at"])) if run["status"] == "completed" else None
    rework = sum(1 for s in run["stages"] if s["status"] in _REWORK_STATUSES)
    escalated = sum(1 for s in run["stages"] if s["status"] == "escalated")
    return {
        "lead_seconds": lead,
        "ai_seconds": ai,
        "wait_seconds": wait,
        "other_seconds": max(0.0, lead - ai - wait) if lead is not None else None,
        "rework": rework,
        "escalated": escalated,
        "first_pass": run["status"] == "completed" and rework == 0 and escalated == 0,
        "passed_steps": sum(1 for s in run["stages"] if s["status"] in _PASSED_STATUSES),
        "automated_steps": sum(1 for s in run["stages"] if s["status"] in _PASSED_STATUSES and not s["human"]),
        "human_decisions": sum(1 for s in run["stages"] if s["human"] and s["decided_at"]),
    }


def _summarize(runs: list[dict], baseline_minutes: int | None) -> dict:
    """같은 묶음(템플릿 하나 또는 전체)의 실행들을 성과 지표로 요약한다."""
    completed = [r for r in runs if r["status"] == "completed"]
    failed = [r for r in runs if r["status"] == "failed"]
    finished = len(completed) + len(failed)
    m_completed = [r["m"] for r in completed]
    leads = [m["lead_seconds"] for m in m_completed if m["lead_seconds"] is not None]
    passed_steps = sum(r["m"]["passed_steps"] for r in runs)
    automated_steps = sum(r["m"]["automated_steps"] for r in runs)
    cost_runs = completed or runs
    lead_median = _median(leads)
    out = {
        "runs": len(runs),
        "completed": len(completed),
        "failed": len(failed),
        "cancelled": sum(1 for r in runs if r["status"] == "cancelled"),
        "running": sum(1 for r in runs if r["status"] == "running"),
        "waiting_for_human": sum(1 for r in runs if r["status"] == "paused_for_approval"),
        "completion_rate": _rate(len(completed), finished),
        "lead_median_seconds": lead_median,
        "lead_mean_seconds": _mean(leads),
        "ai_mean_seconds": _mean([m["ai_seconds"] for m in m_completed]),
        "wait_mean_seconds": _mean([m["wait_seconds"] for m in m_completed]),
        "other_mean_seconds": _mean([m["other_seconds"] for m in m_completed if m["other_seconds"] is not None]),
        "first_pass_rate": _rate(sum(1 for m in m_completed if m["first_pass"]), len(completed)),
        "rework_per_run": _mean([float(r["m"]["rework"]) for r in runs if r["status"] in ("completed", "failed")]),
        "automation_rate": _rate(automated_steps, passed_steps),
        "human_decisions": sum(r["m"]["human_decisions"] for r in runs),
        "tokens_per_run": _mean([float(r["tokens"]) for r in cost_runs]),
        "cost_per_run": _mean([r["cost"] for r in cost_runs]) if cost_runs else None,
        "cost_partial": any(r["cost_partial"] for r in cost_runs),
        "baseline_minutes": baseline_minutes,
        "baseline_hours_covered": (baseline_minutes * len(completed) / 60) if baseline_minutes else None,
        "lead_vs_baseline": (
            (lead_median / (baseline_minutes * 60)) if baseline_minutes and lead_median is not None else None
        ),
    }
    out["signals"] = _signals(out)
    return out


def _signals(s: dict) -> list[dict]:
    """먼저 손볼 곳 — 표본(완료·종료 3건 이상)이 있을 때만, 근거 숫자와 함께 낸다."""
    signals: list[dict] = []
    finished = s["completed"] + s["failed"]
    if finished >= _MIN_SAMPLE and s["completion_rate"] is not None and (1 - s["completion_rate"]) >= _HIGH_FAILURE_RATE:
        signals.append({"kind": "failures", "level": "warn",
                        "text": f"끝난 실행의 {round((1 - s['completion_rate']) * 100)}%가 실패로 끝났습니다"})
    if s["completed"] >= _MIN_SAMPLE:
        if s["first_pass_rate"] is not None and s["first_pass_rate"] < _LOW_FIRST_PASS:
            signals.append({"kind": "first_pass", "level": "warn",
                            "text": f"한 번에 끝난 비율이 {round(s['first_pass_rate'] * 100)}%입니다 — 재작업이 잦은 단계를 확인하세요"})
        if (s["wait_mean_seconds"] or 0) > (s["ai_mean_seconds"] or 0) and (s["wait_mean_seconds"] or 0) >= 60:
            signals.append({"kind": "human_wait", "level": "info",
                            "text": "사람 결재를 기다린 시간이 AI 작업 시간보다 깁니다 — 결재가 병목입니다"})
    if finished >= _MIN_SAMPLE and (s["rework_per_run"] or 0) >= _HIGH_REWORK_PER_RUN:
        signals.append({"kind": "rework", "level": "warn",
                        "text": f"실행 한 건에 평균 {s['rework_per_run']:.1f}번 다시 작성합니다"})
    if s["baseline_minutes"] and s["lead_vs_baseline"] is not None and s["lead_vs_baseline"] > 1:
        signals.append({"kind": "slower_than_baseline", "level": "warn",
                        "text": "완료까지 걸린 시간(중앙값)이 예전 방식의 기준 시간보다 깁니다"})
    return signals


def _stage_flow(conn: sqlite3.Connection, template_id: int, runs: list[dict]) -> list[dict]:
    """단계별 흐름 — 몇 건이 지나갔고, 어디서 시간을 쓰고, 어디서 되돌아가고, 지금 몇 건이 걸려 있는지."""
    stages = conn.execute(
        "SELECT id, seq_order, name, requires_human_approval FROM pipeline_stages "
        "WHERE pipeline_template_id = ? ORDER BY seq_order",
        (template_id,),
    ).fetchall()
    flow = []
    for st in stages:
        rows = [s for r in runs for s in r["stages"] if s["stage_id"] == st["id"]]
        ai = [s["ai_seconds"] for s in rows if s.get("ai_seconds") is not None]
        wait = [s["wait_seconds"] for s in rows if s.get("wait_seconds") is not None]
        reached = {s["pipeline_run_id"] for s in rows}
        flow.append({
            "stage_id": st["id"],
            "seq_order": st["seq_order"],
            "name": st["name"],
            "requires_human_approval": bool(st["requires_human_approval"]),
            "runs_reached": len(reached),
            "attempts": len(rows),
            "first_try_passed": sum(1 for s in rows if s["attempt"] == 1 and s["status"] in _PASSED_STATUSES),
            "rework": sum(1 for s in rows if s["status"] in _REWORK_STATUSES),
            "human_rejections": sum(1 for s in rows if s["status"] == "rejected"),
            "escalated": sum(1 for s in rows if s["status"] == "escalated"),
            "human_reviews": sum(1 for s in rows if s["human"]),
            "ai_mean_seconds": _mean(ai),
            "wait_mean_seconds": _mean(wait),
            "time_total_seconds": sum(ai) + sum(wait),
            "rework_rate": _rate(sum(1 for s in rows if s["status"] in _REWORK_STATUSES), len(rows)),
            "now_running": sum(1 for r in runs if r["status"] == "running" and r["current_stage_seq"] == st["seq_order"]),
            "now_waiting": sum(1 for r in runs if r["status"] == "paused_for_approval"
                               and r["current_stage_seq"] == st["seq_order"]),
        })
    total = sum(f["time_total_seconds"] for f in flow)
    for f in flow:
        f["time_share"] = (f["time_total_seconds"] / total) if total else None
        f["bottleneck"] = False
    if total and len(flow) > 1:
        top = max(flow, key=lambda f: f["time_total_seconds"])
        if top["time_share"] and top["time_share"] >= 0.4:
            top["bottleneck"] = True
    return flow


def _weekly_trend(runs: list[dict], now: datetime, weeks: int = TREND_WEEKS) -> list[dict]:
    """최근 몇 주 동안 주별 완료 건수·리드타임 중앙값·한 번에 완료 비율(완료 시각 기준)."""
    start_of_this_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = []
    for i in range(weeks - 1, -1, -1):
        start = start_of_this_week - timedelta(weeks=i)
        end = start + timedelta(weeks=1)
        done = [r for r in runs if r["status"] == "completed" and (f := _ts(r["finished_at"])) and start <= f < end]
        leads = [r["m"]["lead_seconds"] for r in done if r["m"]["lead_seconds"] is not None]
        buckets.append({
            "week_start": start.strftime("%Y-%m-%d"),
            "completed": len(done),
            "lead_median_seconds": _median(leads),
            "first_pass_rate": _rate(sum(1 for r in done if r["m"]["first_pass"]), len(done)),
        })
    return buckets


def _waiting_list(conn: sqlite3.Connection, runs: list[dict], now: datetime, limit: int = 20) -> list[dict]:
    """지금 사람 결재를 기다리는 실행 — 오래 기다린 순."""
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM pipeline_templates").fetchall()}
    out = []
    for r in runs:
        if r["status"] != "paused_for_approval" or not r["stages"]:
            continue
        last = r["stages"][-1]
        waited = _seconds(_ts(last["created_at"]), now)
        out.append({
            "run_id": r["id"], "task_id": r["task_id"], "template_id": r["pipeline_template_id"],
            "template_name": names.get(r["pipeline_template_id"], ""), "stage_name": last["stage_name"],
            "waiting_seconds": waited, "escalated": last["status"] == "escalated",
        })
    out.sort(key=lambda x: -(x["waiting_seconds"] or 0))
    return out[:limit]


def _templates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    baseline = "baseline_minutes" if _has_column(conn, "pipeline_templates", "baseline_minutes") else "NULL"
    return conn.execute(
        f"SELECT id, name, domain, is_active, {baseline} AS baseline_minutes FROM pipeline_templates ORDER BY id"
    ).fetchall()


def overview(conn: sqlite3.Connection, days: int | None = None) -> dict:
    """전체 요약 + 파이프라인별 요약 + 지금 결재를 기다리는 실행 + 주별 추이."""
    days = _normalize_days(days)
    now = _now()
    # 추이는 기간과 무관하게 최근 몇 주를 보여 주므로 그만큼은 항상 읽는다.
    load_days = 0 if days == 0 else max(days, TREND_WEEKS * 7)
    runs = _load_runs(conn, load_days)
    for r in runs:
        r["m"] = _run_metrics(r, now)
    cutoff = now - timedelta(days=days) if days else None
    in_period = [r for r in runs if cutoff is None or (_ts(r["created_at"]) or now) >= cutoff]
    templates = []
    for t in _templates(conn):
        t_runs = [r for r in in_period if r["pipeline_template_id"] == t["id"]]
        if not t_runs and not t["is_active"]:
            continue
        summary = _summarize(t_runs, t["baseline_minutes"])
        templates.append({"template_id": t["id"], "name": t["name"], "domain": t["domain"],
                          "is_active": bool(t["is_active"]), **summary})
    templates.sort(key=lambda t: (-t["runs"], t["template_id"]))
    total = _summarize(in_period, None)
    covered = [t["baseline_hours_covered"] for t in templates if t["baseline_hours_covered"]]
    total["baseline_hours_covered"] = sum(covered) if covered else None
    total["templates_with_baseline"] = sum(1 for t in templates if t["baseline_minutes"])
    return {
        "period_days": days,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "templates": templates,
        "waiting": _waiting_list(conn, runs, now),
        "trend": _weekly_trend(runs, now),
    }


def template_detail(conn: sqlite3.Connection, template_id: int, days: int | None = None) -> dict:
    """파이프라인 하나의 요약 + 단계별 흐름(병목) + 주별 추이 + 최근 실행."""
    days = _normalize_days(days)
    t = next((row for row in _templates(conn) if row["id"] == template_id), None)
    if t is None:
        raise LookupError("pipeline_template_not_found")
    now = _now()
    load_days = 0 if days == 0 else max(days, TREND_WEEKS * 7)
    runs = _load_runs(conn, load_days, template_id)
    for r in runs:
        r["m"] = _run_metrics(r, now)
    cutoff = now - timedelta(days=days) if days else None
    in_period = [r for r in runs if cutoff is None or (_ts(r["created_at"]) or now) >= cutoff]
    recent = []
    for r in sorted(in_period, key=lambda x: -x["id"])[:20]:
        m = r["m"]
        recent.append({
            "run_id": r["id"], "task_id": r["task_id"], "status": r["status"], "created_at": r["created_at"],
            "finished_at": r["finished_at"], "lead_seconds": m["lead_seconds"], "ai_seconds": m["ai_seconds"],
            "wait_seconds": m["wait_seconds"], "rework": m["rework"], "first_pass": m["first_pass"],
            "human_decisions": m["human_decisions"], "tokens": r["tokens"],
        })
    return {
        "period_days": days,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "template_id": t["id"], "name": t["name"], "domain": t["domain"], "is_active": bool(t["is_active"]),
        "summary": _summarize(in_period, t["baseline_minutes"]),
        "stages": _stage_flow(conn, template_id, in_period),
        "trend": _weekly_trend(runs, now),
        "recent_runs": recent,
        "waiting": _waiting_list(conn, in_period, now),
    }


def set_baseline(conn: sqlite3.Connection, actor_id: int, template_id: int, minutes: int | None) -> dict:
    """예전 방식으로 이 업무 한 건에 걸리던 시간(분). None이면 지운다. 감사 기록을 남긴다."""
    if minutes is not None:
        minutes = int(minutes)
        if minutes <= 0 or minutes > MAX_BASELINE_MINUTES:
            raise ValueError("baseline_minutes_out_of_range")
    row = conn.execute(
        "SELECT id, baseline_minutes FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        raise LookupError("pipeline_template_not_found")
    conn.execute("UPDATE pipeline_templates SET baseline_minutes = ? WHERE id = ?", (minutes, template_id))
    audit.record(conn, actor_id, "pipeline_baseline_set", "pipeline_template", template_id,
                 before={"baseline_minutes": row["baseline_minutes"]}, after={"baseline_minutes": minutes})
    conn.commit()
    return {"template_id": template_id, "baseline_minutes": minutes}
