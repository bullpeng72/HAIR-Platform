"""모델별 동시 호출 상한 + 우선순위 대기(2026-11-27, 개선 계획 3-1 — "대화 응답이 긴 파이프라인 뒤에 줄 서지 않게").

실측(2026-11-27, Ornith 35B): Ollama는 한 모델에 요청을 **하나씩, 온 순서대로** 처리한다. 짧은 대화 응답이 혼자면 16초인데
긴 생성 1개가 돌면 43초, 3개가 줄 서 있으면 96초 — 대화가 앞의 긴 생성이 **모두** 끝날 때까지 기다렸다. 플랫폼 안에서
같은 모델 호출을 상한만큼만 내보내고 나머지는 우선순위 순으로 기다리게 하면, 대화는 지금 돌고 있는 생성 하나만 기다린다
(이미 Ollama에 들어간 생성을 끊지는 못한다).

- **상한**: 모델 레지스트리의 "동시 실행 상한"(`model_registry.max_concurrent`). 비우면 Ollama는 1(Ollama 기본 처리 방식과
  같아 처리량은 그대로 — Ollama를 `OLLAMA_NUM_PARALLEL`로 여러 개 동시 처리하게 했다면 그 값으로 올린다. 기본값은
  `HAIR_OLLAMA_MAX_CONCURRENT`로 바꿀 수 있다), 클라우드는 상한 없음(바로 보냄).
- **우선순위**: 작업 종류로 정한다(`job_queue`가 작업을 실행할 때 `priority()`로 설정) — 0 대화 응답, 1 Agent 단일 실행·
  기타(기본), 2 파이프라인·Crew·지식 등록. 같은 우선순위는 먼저 온 순서.
- **범위**: 한 프로세스 안(웹 서버 embedded 모드, 또는 `hair worker`). 직접 호출(`llm.py`)과 CrewAI(litellm) 호출 모두.
- 기다리는 동안 작업 진행 문구에 "모델 대기 — 앞에 N건"을 남긴다(멈춤 감시에도 활동으로 보인다).
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import os
import threading
import time
from typing import Any

INTERACTIVE, NORMAL, BACKGROUND = 0, 1, 2
_PRIORITY: contextvars.ContextVar[int] = contextvars.ContextVar("llm_gate_priority", default=NORMAL)

_LIMITS: dict[tuple[str, str], int] = {}  # 모델 레지스트리에 적은 상한(모델 레지스트리 캐시 갱신 때 채움)
_LOCK = threading.Condition()
_ACTIVE: dict[tuple[str, str], int] = {}
_WAITING: dict[tuple[str, str], list[tuple[int, int]]] = {}  # (우선순위, 순번) — 정렬 상태로 유지
_SEQ = itertools.count()
# 이 실행 흐름이 이미 잡은 자리 — 도구 호출 루프 안에서 도구가 같은 모델을 다시 불러도 막히지 않게(재진입 허용).
_HELD: contextvars.ContextVar[tuple] = contextvars.ContextVar("llm_gate_held", default=())
REPORT_SECONDS = 5.0


def default_limit(provider: str) -> int | None:
    if provider != "ollama":
        return None
    try:
        value = int(os.environ.get("HAIR_OLLAMA_MAX_CONCURRENT") or 1)
    except ValueError:
        value = 1
    return value if value > 0 else None


def limit_for(provider: str, model: str) -> int | None:
    explicit = _LIMITS.get((provider, model))
    if explicit is not None:
        return explicit if explicit > 0 else None
    return default_limit(provider)


def set_limits(rows: dict[tuple[str, str], int]) -> None:
    with _LOCK:
        _LIMITS.clear()
        _LIMITS.update(rows)
        _LOCK.notify_all()


@contextlib.contextmanager
def priority(level: int):
    token = _PRIORITY.set(level)
    try:
        yield
    finally:
        _PRIORITY.reset(token)


def kind_priority(kind: str) -> int:
    """작업 종류 → 우선순위(`job_queue`)."""
    if kind.startswith("conversation.") and kind not in ("conversation.pipeline_stage_and_notify",
                                                         "conversation.resume_graph_from_approval",
                                                         "conversation.team_mention"):
        return INTERACTIVE
    if kind.startswith(("pipeline.", "knowledge.")) or kind in (
            "deliverable.team_crew", "deliverable.adhoc_crew", "conversation.pipeline_stage_and_notify",
            "conversation.resume_graph_from_approval", "conversation.team_mention"):
        return BACKGROUND
    return NORMAL


def snapshot() -> dict:
    """지금 모델별 실행·대기 수(화면·점검용)."""
    with _LOCK:
        keys = set(_ACTIVE) | set(_WAITING)
        return {f"{p}/{m}": {"running": _ACTIVE.get((p, m), 0), "waiting": len(_WAITING.get((p, m), [])),
                             "limit": limit_for(p, m)} for p, m in sorted(keys)}


@contextlib.contextmanager
def slot(provider: str, model: str):
    """이 모델 호출 하나의 자리. 상한이 없으면 바로 통과."""
    key = (provider or "", model or "")
    if limit_for(*key) is None or key in _HELD.get():
        yield
        return
    from hair_platform import progress

    reporter = progress._REPORTER.get()
    ticket = (_PRIORITY.get(), next(_SEQ))
    with _LOCK:
        queue = _WAITING.setdefault(key, [])
        queue.append(ticket)
        queue.sort()
        last_report = 0.0
        started = time.monotonic()
        while True:
            limit = limit_for(*key)
            if limit is None or (_ACTIVE.get(key, 0) < limit and queue[0] == ticket):
                break
            now = time.monotonic()
            if reporter is not None and now - last_report >= REPORT_SECONDS:
                ahead = queue.index(ticket) + _ACTIVE.get(key, 0)
                try:
                    reporter(f"모델 대기 — {model}을(를) 다른 작업이 쓰는 중(앞에 {ahead}건, {int(now - started)}초 대기)")
                except Exception:  # noqa: BLE001, S110 — 표시 실패가 호출을 막으면 안 된다
                    pass
                last_report = now
            _LOCK.wait(timeout=REPORT_SECONDS)
        queue.remove(ticket)
        _ACTIVE[key] = _ACTIVE.get(key, 0) + 1
        _LOCK.notify_all()  # 상한이 2 이상이면 다음 차례도 들어올 수 있다
    held = _HELD.set(_HELD.get() + (key,))
    try:
        yield
    finally:
        _HELD.reset(held)
        with _LOCK:
            _ACTIVE[key] -= 1
            if not _ACTIVE[key]:
                _ACTIVE.pop(key, None)
            if not _WAITING.get(key):
                _WAITING.pop(key, None)
            _LOCK.notify_all()


# ------------------------------------------------------------------ CrewAI(litellm) 경로


def _split_litellm_model(name: str) -> tuple[str, str]:
    prefix, _, rest = (name or "").partition("/")
    if not rest:
        return "", name
    if prefix in ("ollama_chat", "ollama"):
        return "ollama", rest
    return prefix, rest


def install_litellm_gate() -> None:
    """crewai가 부르는 `litellm.completion`을 감싸 같은 상한·우선순위를 적용한다(한 번만)."""
    try:
        import litellm
    except ImportError:
        return
    if getattr(litellm.completion, "_hair_gated", False):
        return
    original = litellm.completion

    def gated(*args: Any, **kwargs: Any):
        provider, model = _split_litellm_model(str(kwargs.get("model") or (args[0] if args else "")))
        if kwargs.get("stream"):
            cm = slot(provider, model)
            cm.__enter__()
            try:
                stream = original(*args, **kwargs)
            except BaseException:
                cm.__exit__(None, None, None)
                raise

            def iterate():
                try:
                    yield from stream  # type: ignore[misc]  # stream=True면 동기 제너레이터
                finally:
                    cm.__exit__(None, None, None)
            return iterate()
        with slot(provider, model):
            return original(*args, **kwargs)

    gated._hair_gated = True  # type: ignore[attr-defined]
    litellm.completion = gated
