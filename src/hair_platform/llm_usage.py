"""LLM 토큰 사용량 기록 — 하드코딩·스텁 점검(2026-11-22) 3순위.

예전에는 토큰 수를 CrewAI 실행 결과(`crew_runs.token_usage`)에만 남겼고 어느 모델인지도
기록하지 않았다. 직접 호출 경로(`llm.py` — 대화·단일 Agent 실행·판정 LLM 등)는 아예 남기지
않았다. 비용은 사람이 입력한 `cost_estimate × 배정 건수` 추정치뿐이었다.

여기서는 모든 호출의 실제 사용량을 provider·모델별로 남긴다.
- 직접 호출: `llm.py`의 provider별 호출이 응답의 usage(Anthropic `usage.input_tokens/
  output_tokens`, OpenAI `usage.prompt_tokens/completion_tokens`, Ollama `prompt_eval_count/
  eval_count`)를 `record()`로 넘긴다.
- CrewAI(litellm) 경로: litellm 성공 콜백 하나로 모든 호출을 받는다(`install_litellm_callback`).

`llm.py`는 DB 연결 없이 불리므로, 앱 기동 때 `configure(conn)`으로 저장할 연결을 넘긴다.
설정되지 않았으면(CLI·테스트 등) 아무것도 하지 않는다. 기록 실패가 LLM 호출을 깨뜨리면 안 되므로
모든 기록은 예외를 삼킨다.

금액은 지어내지 않는다 — 모델 레지스트리에 admin이 입력한 단가(100만 토큰당 입력/출력)가 있는
모델만 금액을 계산하고, 없으면 토큰 수만 보여 준다(`report`).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import sqlite3
import threading
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

# 업무 맥락(2026-11-23, "P2 진행해줘" — P2-11 업무별 효율·비용). 지금까지는 provider·모델별 합계뿐이라
# "어느 업무·Agent·파이프라인에 얼마가 들었나"를 알 수 없었다. 작업 실행기가 `with_scope`(또는
# `scope`)로 맥락을 정하면 그 안의 모든 호출 기록에 붙는다(바깥 맥락과 합쳐진다).
SCOPE_KEYS = ("task_id", "agent_id", "pipeline_run_id", "conversation_id")
_SCOPE: contextvars.ContextVar[dict] = contextvars.ContextVar("llm_usage_scope", default={})  # noqa: B039 — 읽기 전용(항상 복사해 새 dict로 set)


@contextlib.contextmanager
def scope(**values: Any):
    merged = {**_SCOPE.get(), **{k: v for k, v in values.items() if k in SCOPE_KEYS and v is not None}}
    token = _SCOPE.set(merged)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def with_scope(**fields: Any):
    """작업 함수 데코레이터 — 값은 그 함수의 인자 이름(문자열)이거나, 묶인 인자 dict를 받는 함수.
    예: `@with_scope(task_id="task_id")`, `@with_scope(task_id=lambda a: ...)`. 맥락을 못 구해도
    실행은 그대로 한다."""

    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            values: dict[str, Any] = {}
            try:
                bound = sig.bind_partial(*args, **kwargs).arguments
                for key, how in fields.items():
                    values[key] = bound.get(how) if isinstance(how, str) else how(bound)
            except Exception:  # noqa: BLE001 — 맥락 계산 실패가 실행을 막으면 안 된다
                pass
            with scope(**values):
                return fn(*args, **kwargs)

        return wrapper

    return deco


def task_of_pipeline_run(bound: dict) -> int | None:
    row = bound["conn"].execute("SELECT task_id FROM pipeline_runs WHERE id = ?", (bound["run_id"],)).fetchone()
    return row[0] if row else None

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'direct'
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at ON llm_usage(created_at);
"""


def configure(conn: sqlite3.Connection | None) -> None:
    """사용량을 저장할 연결을 정한다(None이면 기록 끔)."""
    global _CONN
    with _LOCK:
        _CONN = conn


# 평가 중 실제 호출 기록(2026-11-25, P1-10) — `track_calls()` 블록 안의 모든 LLM 호출(provider, model)을 모은다.
# 평가 기준을 "이번 실행이 실제로 쓴 모델" 기준으로 고르는 데 쓴다(`agent_evaluation.run_and_score*`).
_CALLS: contextvars.ContextVar[list | None] = contextvars.ContextVar("llm_usage_calls", default=None)


@contextlib.contextmanager
def track_calls():
    calls: list[tuple[str, str]] = []
    token = _CALLS.set(calls)
    try:
        yield calls
    finally:
        _CALLS.reset(token)


def current_pipeline_run_id() -> int | None:
    return _SCOPE.get().get("pipeline_run_id")


def record(provider: str, model: str, prompt_tokens: Any, completion_tokens: Any, source: str = "direct") -> None:
    tracked = _CALLS.get()
    if tracked is not None and provider:
        tracked.append((provider, model))
    conn = _CONN
    if conn is None or not provider or not model:
        return
    try:
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        if p == 0 and c == 0:
            return
        ctx = _SCOPE.get()
        from hair_platform import knowledge_access  # 요청자(2026-11-25, P2-14 — 사용자별 AI 비용 한도)

        user_id = knowledge_access.current_viewer()
        with _LOCK:
            try:
                conn.execute(
                    "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, source, "
                    "task_id, agent_id, pipeline_run_id, conversation_id, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (provider, model, p, c, source, ctx.get("task_id"), ctx.get("agent_id"),
                     ctx.get("pipeline_run_id"), ctx.get("conversation_id"), user_id),
                )
            except sqlite3.OperationalError:  # user_id 열이 생기기 전(마이그레이션 전) 스키마
                conn.execute(
                    "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, source, "
                    "task_id, agent_id, pipeline_run_id, conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (provider, model, p, c, source, ctx.get("task_id"), ctx.get("agent_id"),
                     ctx.get("pipeline_run_id"), ctx.get("conversation_id")),
                )
            conn.commit()
    except Exception:  # noqa: BLE001 — 기록 실패가 LLM 호출을 깨뜨리지 않게
        pass


def record_anthropic(model: str, resp: Any) -> None:
    usage = getattr(resp, "usage", None)
    record("anthropic", model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))


def record_openai(model: str, resp: Any) -> None:
    usage = getattr(resp, "usage", None)
    record("openai", model, getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))


def record_ollama(model: str, data: Any) -> None:
    if isinstance(data, dict):
        record("ollama", model, data.get("prompt_eval_count"), data.get("eval_count"))


def _litellm_success(kwargs, completion_response, start_time, end_time) -> None:  # noqa: ARG001
    try:
        # litellm은 콜백의 `model`에서 provider 접두사를 떼어 넘기므로(`ollama_chat/hf.co/a/b` →
        # `hf.co/a/b`) 첫 `/`로 나누면 `hf.co`가 provider가 된다(2026-11-24 실측). provider는
        # `custom_llm_provider`를 먼저 보고, Ollama의 `ollama_chat` 경로는 모델 레지스트리와
        # 같은 `ollama`로 맞춘다.
        full = str(kwargs.get("model") or "")
        provider = str((kwargs.get("litellm_params") or {}).get("custom_llm_provider") or "")
        if provider:
            model = full[len(provider) + 1:] if full.startswith(provider + "/") else full
        else:
            provider, _, model = full.partition("/")
            if not model:
                provider, model = "", full
        if provider == "ollama_chat":
            provider = "ollama"
        usage = getattr(completion_response, "usage", None)
        if usage is None and isinstance(completion_response, dict):
            usage = completion_response.get("usage")
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, 0))
        record(provider, model, get("prompt_tokens"), get("completion_tokens"), source="crewai")
    except Exception:  # noqa: BLE001
        pass


def install_litellm_callback() -> bool:
    """litellm 성공 콜백 등록(한 번만). litellm이 없으면 False."""
    try:
        import litellm
    except Exception:  # noqa: BLE001
        return False
    callbacks = litellm.success_callback
    if _litellm_success not in callbacks:
        callbacks.append(_litellm_success)
    return True


def report(conn: sqlite3.Connection, days: int | None = 30) -> dict:
    """provider·모델별 사용량과(단가가 있으면) 금액. 단가는 `model_registry`의
    `price_input_per_1m`/`price_output_per_1m`(admin 입력) — 없는 모델은 `cost=None`."""
    where = "WHERE u.created_at >= datetime('now', ?)" if days else ""
    params: tuple = (f"-{int(days)} days",) if days else ()
    rows = conn.execute(
        "SELECT u.provider, u.model, COUNT(*) AS calls, SUM(u.prompt_tokens) AS prompt_tokens, "
        "SUM(u.completion_tokens) AS completion_tokens, m.price_input_per_1m, m.price_output_per_1m "
        "FROM llm_usage u LEFT JOIN model_registry m ON m.provider = u.provider AND m.name = u.model "
        f"{where} GROUP BY u.provider, u.model ORDER BY prompt_tokens + completion_tokens DESC",
        params,
    ).fetchall()
    models = []
    total_cost = 0.0
    unpriced = 0
    for r in rows:
        priced = r["price_input_per_1m"] is not None and r["price_output_per_1m"] is not None
        cost = (
            r["prompt_tokens"] / 1_000_000 * r["price_input_per_1m"]
            + r["completion_tokens"] / 1_000_000 * r["price_output_per_1m"]
            if priced
            else None
        )
        if cost is None:
            unpriced += 1
        else:
            total_cost += cost
        models.append(
            {
                "provider": r["provider"],
                "model": r["model"],
                "calls": r["calls"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "cost": cost,
            }
        )
    return {"days": days, "models": models, "total_cost": total_cost, "unpriced_models": unpriced}


def ensure_scope_columns(conn: sqlite3.Connection) -> None:
    """업무 맥락 컬럼(2026-11-23, P2-11) — 예전 행은 NULL(맥락 없음)로 남는다."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)")}
    for column in SCOPE_KEYS:
        if column not in existing:
            conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {column} INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_task_id ON llm_usage(task_id)")


def _cost(row) -> float | None:
    if row["price_input_per_1m"] is None or row["price_output_per_1m"] is None:
        return None
    return (row["prompt_tokens"] / 1_000_000 * row["price_input_per_1m"]
            + row["completion_tokens"] / 1_000_000 * row["price_output_per_1m"])


def work_report(conn: sqlite3.Connection, days: int | None = 30, limit: int = 50) -> dict:
    """업무(할 일)별·Agent별 실제 사용량과 효율 지표(2026-11-23, P2-11).

    업무별: LLM 호출 수·토큰·금액(단가가 있는 모델만 — 하나라도 없으면 `cost_partial`), 파이프라인
    실행 수와 평균 소요 시간(분), 사람이 처리한 결재 수(이 업무의 결과물·파이프라인 단계 결재 중
    승인/반려된 것), 결과물 수. Agent별: 호출 수·토큰·금액. 맥락 없이 기록된 사용량(이 기능 이전
    기록, 맥락 밖 호출)은 `unattributed`로 따로 센다 — 지어서 나누지 않는다."""
    where = "WHERE u.created_at >= datetime('now', ?)" if days else "WHERE 1 = 1"
    params: tuple = (f"-{int(days)} days",) if days else ()
    base = (
        "SELECT u.task_id, u.agent_id, u.prompt_tokens, u.completion_tokens, m.price_input_per_1m, "
        "m.price_output_per_1m FROM llm_usage u LEFT JOIN model_registry m "
        f"ON m.provider = u.provider AND m.name = u.model {where}"
    )
    tasks: dict[int, dict] = {}
    agents: dict[int, dict] = {}
    unattributed = {"calls": 0, "tokens": 0}
    for r in conn.execute(base, params).fetchall():
        tokens = r["prompt_tokens"] + r["completion_tokens"]
        cost = _cost(r)
        if r["task_id"] is None and r["agent_id"] is None:
            unattributed["calls"] += 1
            unattributed["tokens"] += tokens
        for key, bucket in ((r["task_id"], tasks), (r["agent_id"], agents)):
            if key is None:
                continue
            b = bucket.setdefault(key, {"calls": 0, "tokens": 0, "cost": 0.0, "cost_partial": False})
            b["calls"] += 1
            b["tokens"] += tokens
            if cost is None:
                b["cost_partial"] = True
            else:
                b["cost"] += cost
    task_rows = []
    for task_id, b in sorted(tasks.items(), key=lambda kv: -kv[1]["tokens"])[:limit]:
        t = conn.execute(
            "SELECT t.role_name, b.raw_text FROM tasks t JOIN briefs b ON b.id = t.brief_id WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        runs = conn.execute(
            "SELECT COUNT(*) AS n, AVG((julianday(finished_at) - julianday(created_at)) * 1440) AS minutes "
            "FROM pipeline_runs WHERE task_id = ? AND finished_at IS NOT NULL", (task_id,),
        ).fetchone()
        approvals = conn.execute(
            "SELECT COUNT(*) FROM approvals a WHERE a.status IN ('approved', 'rejected') AND ("
            "(a.subject_type = 'deliverable' AND a.subject_id IN (SELECT id FROM deliverables WHERE task_id = ?)) OR "
            "(a.subject_type = 'pipeline_run_stage' AND a.subject_id IN (SELECT prs.id FROM pipeline_run_stages prs "
            "JOIN pipeline_runs pr ON pr.id = prs.pipeline_run_id WHERE pr.task_id = ?)))",
            (task_id, task_id),
        ).fetchone()[0]
        deliverable_count = conn.execute("SELECT COUNT(*) FROM deliverables WHERE task_id = ?", (task_id,)).fetchone()[0]
        task_rows.append({
            "task_id": task_id,
            "role_name": t["role_name"] if t else None,
            "brief": (t["raw_text"] or "")[:80] if t else None,
            **b,
            "pipeline_runs": runs["n"],
            "avg_run_minutes": round(runs["minutes"], 1) if runs["minutes"] is not None else None,
            "human_decisions": approvals,
            "deliverables": deliverable_count,
        })
    agent_rows = []
    for agent_id, b in sorted(agents.items(), key=lambda kv: -kv[1]["tokens"])[:limit]:
        a = conn.execute("SELECT role_name, name FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
        agent_rows.append({"agent_id": agent_id, "name": (a["name"] or a["role_name"]) if a else None, **b})
    return {"days": days, "tasks": task_rows, "agents": agent_rows, "unattributed": unattributed}
