"""실행 중 단계 표시 — 오래 걸리는 Agent 응답이 지금 무엇을 하는지 작업 행에 남긴다.

사용자 요청(2026-11-23, "P0부터 개선 진행해줘" — P0-2 속도·진행 표시). 실측: 로컬 35B 모델로
단일 Agent 1회 응답에 3~19분이 걸렸는데, 대화창은 그동안 "● 실행 중"만 보여 줘 자료 조회 중인지,
도구를 부르는 중인지, 검토·교정 중인지 알 수 없었다.

호출 경로(`agent_runtime`·`tool_calling`·`language_qa`·`numeric_check`)는 작업 ID를 모른다 —
작업 실행기가 `reporting(conn, table, job_id)` 안에서 실행하면 그 안의 `report("...")`가 해당 작업
행의 `progress`·`progress_at`을 갱신한다(`tool_calling.record_calls()`와 같은 contextvar 방식).
작업 밖에서 부르면 아무 일도 하지 않는다. 기록 실패가 실행을 막지 않는다."""

from __future__ import annotations

import contextlib
import contextvars
import sqlite3
from collections.abc import Callable

_REPORTER: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "progress_reporter", default=None
)
# 도구 호출 단계에 보여 줄 이름 — 함수 호출용 ASCII 이름(`custom_tool_2337`) → 등록 이름
# ("수치 데이터 분석기"). `pool_registry._custom_python_crewai_tool()`이 채운다.
TOOL_LABELS: dict[str, str] = {}
# 작업 행에 단계를 기록할 수 있는 테이블(문자열을 SQL에 넣으므로 허용 목록으로 고정).
_TABLES = ("conversation_jobs", "agent_execution_jobs")


def report(text: str) -> None:
    reporter = _REPORTER.get()
    if reporter is None:
        return
    try:
        reporter(text[:200])
    except Exception:  # noqa: BLE001, S110 — 진행 표시 실패가 실행을 막으면 안 된다
        pass


@contextlib.contextmanager
def reporting(conn: sqlite3.Connection, table: str, job_id: int):
    if table not in _TABLES:
        raise ValueError(f"unsupported_progress_table:{table}")

    def write(text: str) -> None:
        conn.execute(
            f"UPDATE {table} SET progress = ?, progress_at = datetime('now') WHERE id = ?",
            (text, job_id),
        )
        conn.commit()

    token = _REPORTER.set(write)
    try:
        yield
    finally:
        _REPORTER.reset(token)
