"""시스템 상태 한눈에(2026-11-27, 사용성 평가 11번·개선 제안 5-4).

운영자가 "지금 플랫폼이 제대로 돌고 있나"를 보려면 홈의 관리 점검·연동 설정의 연결 상태·작업 큐·모델 레지스트리를
따로 열어야 했고, 서버 PC의 DB 크기·디스크 여유·기본 AI 모델 연결은 어디에도 보이지 않았다. 여기서 한 번에 모은다.

- `summary()` — admin 화면용 전체 상태(항목마다 ok/warn/error + 설명).
- `health()` — 바깥 감시 도구용 최소 응답(`GET /health`, 로그인 불필요). 내부 정보(경로·모델 이름 등)는 넣지 않는다.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
import time
import urllib.request

STARTED_AT = time.time()
DISK_WARN_BYTES = 2 * 1024**3  # 디스크 여유 2GB 미만이면 경고
OLLAMA_TIMEOUT_SECONDS = 1.5


def _db_file(conn: sqlite3.Connection) -> str | None:
    for _seq, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return path or None
    return None


def _item(key: str, label: str, status: str, detail: str, **extra) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, **extra}


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _database(conn: sqlite3.Connection) -> list[dict]:
    items = []
    try:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.Error as exc:
        return [_item("db", "데이터베이스", "error", f"DB를 읽지 못함: {exc}")]
    path = _db_file(conn)
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    wal = os.path.getsize(path + "-wal") if path and os.path.exists(path + "-wal") else 0
    items.append(_item("db", "데이터베이스", "ok" if ok else "error",
                       ("정상" if ok else "무결성 점검 실패 — 백업에서 복원이 필요할 수 있습니다") +
                       (f" · 크기 {_fmt_bytes(size)}" + (f"(쓰기 대기 {_fmt_bytes(wal)})" if wal else "") if path else " · 메모리 DB"),
                       path=path))
    if path:
        items.append(_backup_item(conn))
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free
        items.append(_item("disk", "디스크 여유", "warn" if free < DISK_WARN_BYTES else "ok",
                           f"{_fmt_bytes(free)} 남음" + (" — 2GB 미만입니다. 오래된 실행 기록·업로드 문서를 정리하거나 디스크를 늘리세요"
                                                         if free < DISK_WARN_BYTES else "")))
    return items


def _backup_item(conn: sqlite3.Connection) -> dict:
    from hair_platform import backup

    last = backup.latest(conn)
    auto = backup.keep_count() > 0
    if last is None:
        return _item("backup", "백업", "warn", "아직 백업이 없습니다 — 서버 PC에서 'hair backup'을 실행하세요" +
                     ("(hair serve가 켜져 있으면 하루 안에 자동으로 뜹니다)" if auto else "(자동 백업 꺼짐: HAIR_BACKUP_KEEP=0)"))
    age = time.time() - last["mtime"]
    hours = int(age // 3600)
    when = f"{hours // 24}일 전" if hours >= 24 else (f"{hours}시간 전" if hours else "방금")
    stale = age > backup.STALE_WARN_SECONDS
    return _item("backup", "백업", "warn" if stale else "ok",
                 f"마지막 백업 {last['created_at']}({when}) · {_fmt_bytes(last['size'])} · "
                 + (f"자동 백업 켜짐(최근 {backup.keep_count()}개 보관)" if auto else "자동 백업 꺼짐")
                 + (" — 이틀 넘게 새 백업이 없습니다. hair serve가 켜져 있는지, 저장 위치에 쓸 수 있는지 확인하세요" if stale else ""))


def _jobs(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform import job_queue

    st = job_queue.status(conn)
    warns = job_queue.warnings(conn)
    c = st["counts"]
    detail = (f"실행 방식 {'별도 작업 프로세스(hair worker)' if st['mode'] == 'external' else '웹 서버 안'} · "
              f"실행 중 {c.get('running', 0)} · 대기 {c.get('queued', 0)} · 실패 {c.get('failed', 0)}")
    items = [_item("jobs", "작업 큐", "warn" if warns else "ok", detail + ("".join(" — " + w for w in warns)))]
    busy = {k: v for k, v in (st.get("models") or {}).items() if v.get("waiting")}
    if busy:
        items.append(_item("model_wait", "모델 대기", "warn",
                           ", ".join(f"{k}: 실행 {v['running']}·대기 {v['waiting']}" for k, v in busy.items()) +
                           " — 같은 모델을 쓰는 호출이 줄 서 있습니다(모델 레지스트리의 '동시 실행 상한')"))
    return items


def _ollama_reachable(base_url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=OLLAMA_TIMEOUT_SECONDS) as resp:  # noqa: S310 — 관리자가 정한 로컬 주소
            names = [m.get("name") for m in json.loads(resp.read().decode() or "{}").get("models", [])]
        return True, f"{len(names)}개 모델"
    except (OSError, ValueError) as exc:
        return False, str(exc)[:120]


def _models(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform import llm

    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
    except Exception as exc:  # noqa: BLE001 — 설정 오류도 상태로 보인다
        return [_item("llm", "기본 AI 모델", "error", f"기본 모델을 정하지 못함: {exc}")]
    items = [_item("llm", "기본 AI 모델", "ok", f"{provider} / {model}")]
    uses_ollama = provider == "ollama" or conn.execute(
        "SELECT 1 FROM model_registry WHERE provider = 'ollama' AND is_active = 1 LIMIT 1").fetchone() is not None
    if uses_ollama:
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ok, info = _ollama_reachable(base)
        items.append(_item("ollama", "로컬 모델 서버(Ollama)", "ok" if ok else "error",
                           (f"연결됨 · {info}" if ok else f"연결 안 됨({info}) — 서버 PC에서 Ollama가 켜져 있는지 확인하세요")))
    return items


def _connections(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform import connection_health

    warns = connection_health.warnings(conn)
    total = len(connection_health.summary(conn))
    if not total:
        return []
    return [_item("connections", "외부 연결(DB·메일·캘린더 등)", "warn" if warns else "ok",
                  f"{total}곳 등록" + ("".join(" — " + w for w in warns[:3]) if warns else " · 최근 확인에서 문제 없음"))]


def _config(conn: sqlite3.Connection) -> list[dict]:
    from hair_platform import config_check

    try:
        diffs = config_check.check(conn)
    except Exception as exc:  # noqa: BLE001
        return [_item("config", "기본 구성", "warn", f"점검하지 못함: {exc}")]
    return [_item("config", "기본 구성", "warn" if diffs else "ok",
                  f"기본 구성과 다른 곳 {len(diffs)}건 — 사용자 관리에서 점검" if diffs else "기본 구성과 같음")]


def summary(conn: sqlite3.Connection) -> dict:
    from hair_platform import get_version

    uptime = int(time.time() - STARTED_AT)
    server = _item("server", "서버", "ok",
                   f"HAIR {get_version()} · 켜진 지 {uptime // 3600}시간 {uptime % 3600 // 60}분 · Python "
                   f"{platform.python_version()} · {platform.system()}")
    sections = [
        {"title": "서버·데이터", "items": [server, *_database(conn)]},
        {"title": "실행", "items": [*_jobs(conn), *_models(conn)]},
        {"title": "연결·구성", "items": [*_connections(conn), *_config(conn)]},
    ]
    worst = "ok"
    for sec in sections:
        for it in sec["items"]:
            if it["status"] == "error":
                worst = "error"
            elif it["status"] == "warn" and worst == "ok":
                worst = "warn"
    return {"status": worst, "sections": sections, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def health(conn: sqlite3.Connection) -> tuple[int, dict]:
    """감시 도구용 — DB를 읽을 수 있으면 200. 내부 정보는 넣지 않는다."""
    try:
        conn.execute("SELECT 1").fetchone()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    from hair_platform import job_queue

    try:
        queue_warn = bool(job_queue.warnings(conn))
    except sqlite3.Error:
        queue_warn = True
    body = {"status": "ok" if db_ok and not queue_warn else ("degraded" if db_ok else "error"),
            "db": "ok" if db_ok else "error", "jobs": "warn" if queue_warn else "ok",
            "uptime_seconds": int(time.time() - STARTED_AT), "python": sys.version.split()[0]}
    return (200 if db_ok else 503), body
