"""도구 사용 기록(2026-11-26, 개선 계획 1-1 — "도구를 실제로 썼는지 확인할 수 없음").

인사 파이프라인 실측에서 결과물에 "채용 공고 점검기 점검 결과: 0건"이라고 적혀 있었지만, 도구 호출은 단계 채점에만
잠깐 쓰이고 저장되지 않아 **실제로 불렀는지 말만 했는지** 사람이 구분할 수 없었다. 모든 도구 호출이 지나는
`tool_calling.run_tool()`에서 여기로 한 줄씩 남긴다.

**어디서 불렀나** — 실행 문맥을 그대로 붙인다:
- 할 일·Agent·파이프라인 실행·대화: `llm_usage` 업무 맥락(`llm_usage.scope`)
- 파이프라인 단계와 시도 번호: `stage(stage_id, attempt)`(`pipeline_runtime.stage_progress`가 세운다 — 단계 시도 행은
  실행이 끝난 뒤에 만들어지므로 (실행, 단계, 시도)로 잇는다)
- 대화·단독 실행 작업 행: `progress.current_job()`
- 요청자: `knowledge_access.current_viewer()`

입력·결과는 앞부분만(각 600자) 남긴다 — 결과 전문은 결과물·채점 근거에 이미 있다. 기록 실패는 도구 실행을 막지 않는다.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import sqlite3
from collections.abc import Iterator
from typing import Any

PREVIEW_CHARS = 600
_STAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("tool_audit_stage", default=None)
# 도구가 실패했을 때 `run_tool`이 돌려주는 문구의 앞부분 — 성공/실패 구분용.
FAILURE_PREFIXES = ("알 수 없는 도구입니다", "도구 실행 중 오류가 발생했습니다")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_calls ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " tool_name TEXT NOT NULL,"
        " tool_label TEXT NOT NULL,"
        " ok INTEGER NOT NULL,"
        " duration_ms INTEGER,"
        " args_preview TEXT,"
        " result_preview TEXT,"
        " task_id INTEGER, agent_id INTEGER, pipeline_run_id INTEGER, stage_id INTEGER, stage_attempt INTEGER,"
        " conversation_id INTEGER, job_table TEXT, job_id INTEGER, user_id INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(pipeline_run_id, stage_id, stage_attempt)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_job ON tool_calls(job_table, job_id)")


@contextlib.contextmanager
def stage(stage_id: int, attempt: int | None) -> Iterator[None]:
    token = _STAGE.set({"stage_id": stage_id, "attempt": attempt})
    try:
        yield
    finally:
        _STAGE.reset(token)


def _preview(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value or "")
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"


def capture() -> dict:
    """지금 실행 문맥 — 다른 스레드에서 도는 처리기(CrewAI 이벤트)가 `record(..., ctx=)`로 넘긴다."""
    from hair_platform import knowledge_access, llm_usage, progress

    return {"scope": dict(llm_usage._SCOPE.get()), "stage": dict(_STAGE.get() or {}),
            "job": progress.current_job() or (None, None), "user_id": knowledge_access.current_viewer()}


def record(name: str, label: str, arguments: Any, result: str, duration_ms: int | None, *, ok: bool | None = None,
           ctx: dict | None = None) -> None:
    from hair_platform import llm_usage

    conn = llm_usage._CONN
    if conn is None:
        return
    try:
        cap = ctx or capture()
        ctx_scope, st, job = cap["scope"], cap["stage"], cap["job"]
        if ok is None:
            ok = not str(result).startswith(FAILURE_PREFIXES)
        with llm_usage._LOCK:
            conn.execute(
                "INSERT INTO tool_calls (tool_name, tool_label, ok, duration_ms, args_preview, result_preview, task_id, "
                "agent_id, pipeline_run_id, stage_id, stage_attempt, conversation_id, job_table, job_id, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, label or name, 1 if ok else 0, duration_ms, _preview(arguments), _preview(result),
                 ctx_scope.get("task_id"), ctx_scope.get("agent_id"), ctx_scope.get("pipeline_run_id"), st.get("stage_id"),
                 st.get("attempt"), ctx_scope.get("conversation_id"), job[0], job[1], cap["user_id"]),
            )
            conn.commit()
    except Exception:  # noqa: BLE001, S110 — 기록 실패가 도구 실행을 막으면 안 된다
        pass


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, created_at, tool_name, tool_label, ok, duration_ms, args_preview, result_preview, agent_id, "
        f"stage_id, stage_attempt FROM tool_calls WHERE {where} ORDER BY id", params)]  # noqa: S608 — where는 내부 고정


def for_stage_attempt(conn: sqlite3.Connection, run_id: int, stage_id: int, attempt: int) -> list[dict]:
    return _rows(conn, "pipeline_run_id = ? AND stage_id = ? AND stage_attempt = ?", (run_id, stage_id, attempt))


def for_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return _rows(conn, "pipeline_run_id = ?", (run_id,))


def for_job(conn: sqlite3.Connection, table: str, job_id: int) -> list[dict]:
    return _rows(conn, "job_table = ? AND job_id = ?", (table, job_id))


def summary(calls: list[dict]) -> list[dict]:
    """도구별 호출 수·실패 수 — 화면 표시용("채용 공고 점검기 ×2")."""
    out: dict[str, dict] = {}
    for c in calls:
        s = out.setdefault(c["tool_label"], {"tool": c["tool_label"], "calls": 0, "failed": 0})
        s["calls"] += 1
        s["failed"] += 0 if c["ok"] else 1
    return list(out.values())
