"""로그인 시도 제한(2026-11-27, 개선 제안 5-2 — "비밀번호를 무한히 대입해 볼 수 있다").

- **계정별**: 같은 아이디로 `MAX_FAILURES_PER_USER`번(5) 연속 틀리면 `LOCK_SECONDS`(15분) 동안 그 아이디의 로그인을 막는다.
  맞는 비밀번호로 로그인하면 셈을 지운다.
- **접속 주소별**: 한 주소에서 여러 아이디로 `MAX_FAILURES_PER_IP`번(20) 틀리면 그 주소를 15분 막는다(아이디를 바꿔 가며
  흔한 비밀번호를 넣어 보는 시도).
- 막힌 동안은 비밀번호가 맞아도 들어갈 수 없다(맞는지 알려 주지 않는다). 응답은 429와 남은 시간.
- 처음 막힐 때 활동 기록(`login_locked`)을 남긴다 — 있는 아이디면 그 계정에, 없는 아이디면 남기지 않는다(대상 없음).
- 기록은 프로세스 메모리에 둔다(서버를 다시 켜면 풀린다). 웹 서버는 한 프로세스라 충분하다.
"""

from __future__ import annotations

import threading
import time
from collections import deque

MAX_FAILURES_PER_USER = 5
MAX_FAILURES_PER_IP = 20
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60

_LOCK = threading.Lock()
_FAILS: dict[str, deque] = {}
_LOCKED_UNTIL: dict[str, float] = {}


def _keys(username: str, ip: str | None) -> tuple[str, str | None]:
    return "u:" + (username or "").strip().lower(), ("ip:" + ip) if ip else None


def _now() -> float:
    return time.monotonic()


def retry_after(username: str, ip: str | None) -> int:
    """막혀 있으면 남은 초, 아니면 0."""
    now = _now()
    with _LOCK:
        left = 0.0
        for key in _keys(username, ip):
            if key and _LOCKED_UNTIL.get(key, 0) > now:
                left = max(left, _LOCKED_UNTIL[key] - now)
        return int(left) + (1 if left else 0)


def record_failure(username: str, ip: str | None) -> bool:
    """실패를 센다. 이번 실패로 새로 막혔으면 True."""
    now = _now()
    newly = False
    with _LOCK:
        for key, limit in zip(_keys(username, ip), (MAX_FAILURES_PER_USER, MAX_FAILURES_PER_IP), strict=True):
            if not key:
                continue
            q = _FAILS.setdefault(key, deque())
            q.append(now)
            while q and now - q[0] > WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit and _LOCKED_UNTIL.get(key, 0) <= now:
                _LOCKED_UNTIL[key] = now + LOCK_SECONDS
                q.clear()
                newly = True
    return newly


def record_success(username: str) -> None:
    key, _ = _keys(username, None)
    with _LOCK:
        _FAILS.pop(key, None)
        _LOCKED_UNTIL.pop(key, None)


def unlock(username: str) -> None:
    """관리자가 비밀번호를 재설정하면 그 계정의 잠금도 푼다."""
    record_success(username)


def reset_all() -> None:
    """테스트용."""
    with _LOCK:
        _FAILS.clear()
        _LOCKED_UNTIL.clear()
