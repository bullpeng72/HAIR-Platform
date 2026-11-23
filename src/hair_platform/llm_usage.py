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

import sqlite3
import threading
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

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


def record(provider: str, model: str, prompt_tokens: Any, completion_tokens: Any, source: str = "direct") -> None:
    conn = _CONN
    if conn is None or not provider or not model:
        return
    try:
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        if p == 0 and c == 0:
            return
        with _LOCK:
            conn.execute(
                "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, model, p, c, source),
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
        full = str(kwargs.get("model") or "")
        provider, _, model = full.partition("/")
        if not model:
            provider = str((kwargs.get("litellm_params") or {}).get("custom_llm_provider") or "")
            model = full
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
