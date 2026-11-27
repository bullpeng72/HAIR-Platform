"""백그라운드 작업 큐 + 별도 작업 프로세스(2026-11-26, P1-6 2단계 — "서버를 재시작하면 실행 중이던 파이프라인이 멈춘다").

지금까지 대화 응답·Agent 실행·Crew·파이프라인·지식 등록은 웹 서버 안의 스레드(FastAPI `BackgroundTasks`·`threading`)로
돌았다. 1단계(`pipeline_runtime.resume_interrupted_run`)로 재시작 뒤 **이어서 실행**은 되지만, 재시작하는 순간 실행 중이던
LLM 호출은 버려진다. 이 모듈은 실행을 **기록된 작업**으로 바꾸고, 원하면 웹 서버와 **다른 프로세스**(`hair worker`)에서 돌린다.

**두 가지 모드**(`HAIR_WORKER_MODE`)
- `embedded`(기본) — 지금과 같다. 웹 서버가 작업을 기록하고 곧바로 자기 스레드에서 실행한다. 명령 하나(`hair serve`)로 끝.
- `external` — 웹 서버는 작업을 **기록만** 하고, `hair worker`가 가져가 실행한다. 웹 서버를 재시작해도 실행 중인 작업은
  계속 돈다. 웹 서버는 기동 때 "실행 중" 작업을 정리하지 않는다(작업 프로세스의 것이므로) — 작업 프로세스가 자기 기동 때
  정리한다(이전 작업 프로세스가 죽으며 남긴 작업 = 1단계와 같은 "중단됨 → 이어서 실행").

**작업 프로세스는 하나만** — 작업 프로세스는 `worker_heartbeats`에 몇 초마다 살아 있음을 남기고, 살아 있는 다른 작업
프로세스가 있으면 시작을 거부한다(같은 실행을 두 곳에서 돌리지 않게). 상태는 "기본 구성 점검"·`hair check`에서 보인다.

작업 함수는 `KINDS`에 이름으로 등록한다(인자는 JSON으로 저장할 수 있는 값 — ID·문자열·dict). 함수 첫 인자는 DB 연결이다.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import socket
import sqlite3
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# 이름 → "모듈:함수". 지연 import(순환 import 방지·안 쓰는 모듈은 불러오지 않음).
KINDS: dict[str, str] = {
    "conversation.agent_mention": "hair_platform.conversations:run_agent_mention_job",
    "conversation.team_mention": "hair_platform.conversations:run_team_mention_job",
    "conversation.default_ai": "hair_platform.conversations:run_default_ai_job",
    "conversation.resume_graph_from_approval": "hair_platform.conversations:resume_pipeline_graph_from_approval_and_notify",
    "conversation.pipeline_stage_and_notify": "hair_platform.conversations:run_pipeline_stage_job_and_notify",
    "knowledge.register_submission": "hair_platform.knowledge_submissions:register_submission",
    "deliverable.agent_execution": "hair_platform.deliverables:run_agent_execution_job",
    "deliverable.team_crew": "hair_platform.deliverables:run_team_crew_job",
    "deliverable.adhoc_crew": "hair_platform.deliverables:run_adhoc_crew_job",
    "deliverable.comment_execution": "hair_platform.deliverables:run_comment_triggered_execution_job",
    "pipeline.stage": "hair_platform.pipeline_runtime:run_pipeline_stage_job",
    "pipeline.graph": "hair_platform.execution_graph:run_pipeline_graph_job",
    "pipeline.resume_graph": "hair_platform.execution_graph:resume_interrupted_graph_job",
}

# 실행 행을 가리키는 첫 인자 — 작업 프로세스 기동 정리가 아직 대기 중인 작업의 행을 건드리지 않게(`protected_ids`).
_ROW_TABLE: dict[str, str] = {
    "conversation.agent_mention": "conversation_jobs",
    "conversation.team_mention": "conversation_jobs",
    "conversation.default_ai": "conversation_jobs",
    "conversation.resume_graph_from_approval": "pipeline_runs",
    "conversation.pipeline_stage_and_notify": "pipeline_runs",
    "deliverable.agent_execution": "agent_execution_jobs",
    "deliverable.comment_execution": "agent_execution_jobs",
    "deliverable.team_crew": "agent_execution_jobs",   # 첫 인자가 {agent_id: job_id}
    "deliverable.adhoc_crew": "agent_execution_jobs",
    "pipeline.stage": "pipeline_runs",
    "pipeline.graph": "pipeline_runs",
    "pipeline.resume_graph": "pipeline_runs",
}


def _int_keys(d: Any) -> Any:
    return {int(k): v for k, v in d.items()} if isinstance(d, dict) else d


# JSON은 dict의 정수 키를 문자열로 바꾼다 — Crew 작업의 `job_by_agent_id`({agent_id: job_id})는 `.get(int)`로 찾으므로 되돌린다.
_ARG_DECODERS: dict[str, Callable[[list], list]] = {
    "deliverable.team_crew": lambda a: [_int_keys(a[0]), *a[1:]] if a else a,
    "deliverable.adhoc_crew": lambda a: [_int_keys(a[0]), *a[1:]] if a else a,
}

MODES = ("embedded", "external")
HEARTBEAT_SECONDS = 5.0
STALE_SECONDS = 30.0  # 이 시간 넘게 소식이 없는 작업 프로세스는 죽은 것으로 본다
POLL_SECONDS = 1.0
MAX_CONCURRENT = 4


def mode() -> str:
    m = (os.environ.get("HAIR_WORKER_MODE") or "embedded").strip().lower()
    return m if m in MODES else "embedded"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS background_jobs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"
        " args TEXT NOT NULL DEFAULT '[]',"
        " kwargs TEXT NOT NULL DEFAULT '{}',"
        " status TEXT NOT NULL DEFAULT 'queued',"   # queued | running | done | failed | interrupted
        " worker TEXT,"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " error TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " started_at TEXT,"
        " finished_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_status ON background_jobs(status, id)")
    # 우선순위(2026-11-27, 개선 계획 3-1 — `llm_gate.kind_priority`: 0 대화 응답, 1 기본, 2 파이프라인·Crew·지식 등록)
    if not any(r[1] == "priority" for r in conn.execute("PRAGMA table_info(background_jobs)")):
        conn.execute("ALTER TABLE background_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS worker_heartbeats ("
        " worker TEXT PRIMARY KEY,"
        " pid INTEGER NOT NULL,"
        " host TEXT NOT NULL,"
        " started_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " beat_at REAL NOT NULL,"            # time.time() — 프로세스 사이에서 비교하므로 벽시계
        " running INTEGER NOT NULL DEFAULT 0)"
    )


def _resolve(kind: str) -> Callable[..., Any]:
    target = KINDS.get(kind)
    if target is None:
        raise KeyError(f"등록되지 않은 작업 종류: {kind}")
    module, _, func = target.partition(":")
    return getattr(importlib.import_module(module), func)


# ------------------------------------------------------------------ 넣기(웹 서버)

def enqueue(conn: sqlite3.Connection, kind: str, *args: Any, background_tasks: Any = None, **kwargs: Any) -> int:
    """작업을 기록한다. embedded 모드면 이 프로세스에서 곧바로 실행을 시작한다 — 요청 처리 중이면 FastAPI
    `BackgroundTasks`로(응답 뒤 실행, 지금까지와 같은 순서·테스트 동작), 아니면(정기 실행·이벤트 트리거) 스레드로.
    external 모드면 기록만 하고 `hair worker`가 가져간다."""
    if kind not in KINDS:
        raise KeyError(f"등록되지 않은 작업 종류: {kind}")
    payload = json.dumps(list(args), ensure_ascii=False)  # JSON으로 못 바꾸는 인자면 여기서 TypeError
    kw = json.dumps(kwargs, ensure_ascii=False)
    from hair_platform import llm_gate

    cur = conn.execute("INSERT INTO background_jobs (kind, args, kwargs, priority) VALUES (?, ?, ?, ?)",
                       (kind, payload, kw, llm_gate.kind_priority(kind)))
    conn.commit()
    job_id = int(cur.lastrowid or 0)
    if mode() == "embedded":
        if background_tasks is not None:
            background_tasks.add_task(run_job, conn, job_id, _server_worker_name(), reraise=True)
        else:
            threading.Thread(target=run_job, args=(conn, job_id, _server_worker_name()), kwargs={"reraise": True},
                             name=f"hair-job-{job_id}", daemon=True).start()
    return job_id


def _server_worker_name() -> str:
    return f"server-{os.getpid()}"


# ------------------------------------------------------------------ 실행(공통)

def _commit(conn: sqlite3.Connection) -> None:
    """작업 스레드들이 연결 하나를 같이 쓴다(`db.get_connection` — 앱 전체 공유). 두 작업이 동시에 시작하면 다른 스레드의
    `commit()`이 이 스레드의 변경까지 이미 확정해, 여기서 "no transaction is active"가 난다(CI 실측 2026-11-27 — 이 예외로
    작업 스레드가 죽어 작업이 '실행 중'으로 영원히 남고 자리만 비었다). 변경은 이미 확정된 것이므로 성공으로 본다."""
    try:
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "no transaction is active" not in str(exc):
            raise


def _claim(conn: sqlite3.Connection, job_id: int, worker: str) -> sqlite3.Row | None:
    cur = conn.execute(
        "UPDATE background_jobs SET status = 'running', worker = ?, attempts = attempts + 1, started_at = datetime('now') "
        "WHERE id = ? AND status = 'queued'",
        (worker, job_id),
    )
    _commit(conn)
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM background_jobs WHERE id = ?", (job_id,)).fetchone()


def _finish(conn: sqlite3.Connection, job_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE background_jobs SET status = ?, error = ?, finished_at = datetime('now') WHERE id = ?",
        (status, (error or None) and error[-4000:], job_id),
    )
    _commit(conn)


def run_job(conn: sqlite3.Connection, job_id: int, worker: str, reraise: bool = False) -> str:
    """작업 하나를 가져가 실행한다 — 이미 다른 곳이 가져갔으면 아무것도 하지 않는다. 결과 상태를 돌려준다.
    `reraise`: 실패를 기록한 뒤 예외를 다시 올린다(embedded — 예전 `BackgroundTasks`·스레드와 같은 동작)."""
    row = _claim(conn, job_id, worker)
    if row is None:
        return "skipped"
    from hair_platform import llm_gate

    try:
        func = _resolve(row["kind"])
        args = json.loads(row["args"] or "[]")
        decode = _ARG_DECODERS.get(row["kind"])
        # 이 작업의 모델 호출 우선순위(2026-11-27, 3-1) — 대화 응답이 파이프라인 호출보다 먼저 모델 자리를 받는다.
        with llm_gate.priority(llm_gate.kind_priority(row["kind"])):
            func(conn, *(decode(args) if decode else args), **json.loads(row["kwargs"] or "{}"))
    except Exception as exc:  # noqa: BLE001 — 작업 함수가 자기 실패를 기록하지만, 큐 기록도 남긴다
        if not reraise:
            log.exception("background job %s (%s) failed", job_id, row["kind"])
        _finish(conn, job_id, "failed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}")
        if reraise:
            raise
        return "failed"
    _finish(conn, job_id, "done")
    return "done"


# ------------------------------------------------------------------ 기동 정리

def mark_interrupted(conn: sqlite3.Connection, worker_prefix: str) -> int:
    """이 종류의 프로세스가 죽으며 남긴 실행 중(running) 작업을 interrupted로(실제 실행 행은 각 모듈의 기동 정리가 처리).
    대기 중(queued) 작업은 남긴다 — embedded는 기동 때 `resume_queued`가, external은 작업 프로세스가 실행한다."""
    cur = conn.execute(
        "UPDATE background_jobs SET status = 'interrupted', finished_at = datetime('now'), "
        "error = COALESCE(error, '프로세스가 재시작돼 중단됐습니다') "
        "WHERE status = 'running' AND worker LIKE ?",
        (worker_prefix + "%",),
    )
    conn.commit()
    return cur.rowcount


def protected_ids(conn: sqlite3.Connection) -> dict[str, set[int]]:
    """대기 중(queued) 작업이 가리키는 실행 행 ID — 테이블별."""
    out: dict[str, set[int]] = {"pipeline_runs": set(), "agent_execution_jobs": set(), "conversation_jobs": set()}
    for r in conn.execute("SELECT kind, args FROM background_jobs WHERE status = 'queued'"):
        table = _ROW_TABLE.get(r["kind"])
        try:
            first = (json.loads(r["args"] or "[]") or [None])[0]
        except (ValueError, IndexError):
            continue
        if table is None or first is None:
            continue
        values = first.values() if isinstance(first, dict) else [first]
        out[table].update(int(v) for v in values if isinstance(v, int) or (isinstance(v, str) and v.isdigit()))
    return out


def live_workers(conn: sqlite3.Connection) -> list[dict]:
    now = time.time()
    return [dict(r) | {"age": round(now - r["beat_at"], 1)}
            for r in conn.execute("SELECT * FROM worker_heartbeats ORDER BY beat_at DESC")
            if now - r["beat_at"] <= STALE_SECONDS]


def status(conn: sqlite3.Connection) -> dict:
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM background_jobs GROUP BY status")}
    oldest = conn.execute(
        "SELECT MIN(created_at) FROM background_jobs WHERE status = 'queued'").fetchone()[0]
    from hair_platform import llm_gate

    # models: 이 프로세스의 모델별 실행·대기 수(2026-11-27, 3-1) — external 모드면 작업 프로세스 쪽 값은 여기 안 보인다.
    return {"mode": mode(), "workers": live_workers(conn), "counts": counts, "oldest_queued_at": oldest,
            "models": llm_gate.snapshot()}


def warnings(conn: sqlite3.Connection) -> list[str]:
    """관리 점검에 보일 경고 — external인데 작업 프로세스가 없으면 작업이 실행되지 않고 쌓이기만 한다."""
    st = status(conn)
    queued = st["counts"].get("queued", 0)
    out = []
    if st["mode"] == "external" and not st["workers"]:
        out.append("작업 프로세스(hair worker)가 실행되고 있지 않습니다 — 대화 응답·Agent 실행·파이프라인이 시작되지 않고 "
                   f"쌓입니다(대기 {queued}건). 서버 PC에서 'HAIR_WORKER_MODE=external hair worker'를 실행하세요.")
    elif st["mode"] == "external" and queued and st["oldest_queued_at"]:
        row = conn.execute("SELECT (julianday('now') - julianday(?)) * 86400", (st["oldest_queued_at"],)).fetchone()
        if row and row[0] and row[0] > 600:
            out.append(f"10분 넘게 시작하지 못한 작업이 있습니다(대기 {queued}건) — 작업 프로세스 동시 실행 수(--concurrency)를 "
                       "늘리거나 오래 걸리는 실행을 확인하세요.")
    return out


def resume_queued(conn: sqlite3.Connection) -> int:
    """embedded 기동 때 — 이전 서버가 기록만 하고 시작하지 못한 대기 작업을 이어서 실행한다."""
    ids = [r[0] for r in conn.execute("SELECT id FROM background_jobs WHERE status = 'queued' ORDER BY id")]
    for job_id in ids:
        threading.Thread(target=run_job, args=(conn, job_id, _server_worker_name()), name=f"hair-job-{job_id}",
                         daemon=True).start()
    return len(ids)


def configure_runtime(conn: sqlite3.Connection) -> None:
    """작업 실행에 필요한 프로세스 전역 설정 — 웹 서버(`create_app`)와 작업 프로세스(`hair worker`)가 같이 쓴다."""
    from hair_platform import ai_budget, evaluation_config, llm_usage

    llm_usage.configure(conn)
    evaluation_config.configure(conn)  # 평가 기준 설정(2026-11-25) — 평가 경로가 읽을 연결
    ai_budget.configure(conn)  # AI 사용 한도(2026-11-25, P2-14)
    llm_usage.install_litellm_callback()
    from hair_platform import llm_gate

    llm_gate.install_litellm_gate()  # CrewAI 호출에도 모델별 동시 호출 상한·우선순위(2026-11-27, 3-1)


# ------------------------------------------------------------------ 작업 프로세스(`hair worker`)

class Worker:
    """external 모드 작업 프로세스. DB 파일 경로로 자기 연결을 연다(웹 서버와 같은 파일, WAL)."""

    def __init__(self, db_path: str, *, max_concurrent: int = MAX_CONCURRENT, poll_seconds: float = POLL_SECONDS,
                 connect: Callable[[str], sqlite3.Connection] | None = None, name: str | None = None):
        from hair_platform import db

        self.db_path = db_path
        self._connect = connect or db.get_connection
        self.name = name or f"worker-{socket.gethostname()}-{os.getpid()}"
        self.max_concurrent = max(1, int(max_concurrent))
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.conn = self._connect(db_path)
        self._beat_conn = self._connect(db_path)  # 하트비트는 별도 연결 — 작업 연결의 트랜잭션을 건드리지 않는다

    # -- 시작·정리
    def start(self) -> dict:
        others = [w for w in live_workers(self.conn) if w["worker"] != self.name]
        if others:
            w = others[0]
            raise RuntimeError(f"다른 작업 프로세스가 이미 실행 중입니다({w['worker']}, pid {w['pid']}) — 하나만 실행하세요")
        self._beat()
        # 이전 작업 프로세스가 죽으며 남긴 것 정리 — 각 실행 행은 1단계와 같은 "중단됨 → 이어서 실행" 상태가 된다.
        from hair_platform import conversations, deliverables, pipeline_runtime

        # 작업 프로세스가 꺼져 있는 동안 쌓인 대기 작업(queued)은 정리하지 않고 이어서 실행한다 — 그 실행 행도 남긴다.
        keep = protected_ids(self.conn)
        cleaned = {
            "jobs": mark_interrupted(self.conn, "worker-"),
            "execution_jobs": deliverables.reap_orphaned_execution_jobs(self.conn, keep["agent_execution_jobs"]),
            "pipeline_runs": pipeline_runtime.reap_orphaned_pipeline_runs(self.conn, keep["pipeline_runs"]),
            "conversation_jobs": conversations.reap_orphaned_conversation_jobs(self.conn, keep["conversation_jobs"]),
        }
        threading.Thread(target=self._beat_loop, name="hair-worker-heartbeat", daemon=True).start()
        return cleaned

    def _beat(self) -> None:
        running = sum(1 for t in self._threads if t.is_alive())
        self._beat_conn.execute(
            "INSERT INTO worker_heartbeats (worker, pid, host, beat_at, running) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(worker) DO UPDATE SET beat_at = excluded.beat_at, running = excluded.running",
            (self.name, os.getpid(), socket.gethostname(), time.time(), running),
        )
        self._beat_conn.commit()

    def _beat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._beat()
            except sqlite3.Error:
                log.warning("worker heartbeat failed", exc_info=True)

    # -- 한 바퀴: 빈자리만큼 가져가 실행
    def tick(self) -> int:
        self._threads = [t for t in self._threads if t.is_alive()]
        free = self.max_concurrent - len(self._threads)
        started = 0
        if free <= 0:
            return 0
        # 우선순위 순(2026-11-27, 3-1). 오래 걸리는 작업(파이프라인·Crew·지식 등록)은 자리를 모두 차지하지 않게 하나를
        # 대화 응답용으로 남긴다(자리가 하나뿐이면 그대로).
        running_bg = sum(1 for t in self._threads if getattr(t, "hair_priority", 1) >= 2)
        bg_cap = max(1, self.max_concurrent - 1) - running_bg
        rows = self.conn.execute(
            "SELECT id, priority FROM background_jobs WHERE status = 'queued' ORDER BY priority, id LIMIT ?",
            (free + 50,)).fetchall()
        ids = []
        for job_id, prio in rows:
            if len(ids) >= free:
                break
            if prio >= 2:
                if bg_cap <= 0:
                    continue
                bg_cap -= 1
            ids.append((job_id, prio))
        for job_id, prio in ids:
            t = threading.Thread(target=run_job, args=(self.conn, job_id, self.name), name=f"hair-job-{job_id}", daemon=True)
            t.hair_priority = prio  # type: ignore[attr-defined]
            t.start()
            self._threads.append(t)
            started += 1
        return started

    def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.poll_seconds)
        finally:
            self.stop()

    def stop(self, wait_seconds: float = 0.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + wait_seconds
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        try:
            self._beat_conn.execute("DELETE FROM worker_heartbeats WHERE worker = ?", (self.name,))
            self._beat_conn.commit()
        except sqlite3.Error:
            pass
