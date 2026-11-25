"""Gate A-G 평가 HTML 리포트(2026-11-25, 사용자 요청 "agent-evaluator에 html 리포트 생성 기능이 있어 — 호출해 쓰도록").

Agent-Evaluator SDK의 `agent_evaluator.reporting.generate_comprehensive_html_report(monitor)`는 평가 모니터 하나로
Gate A–G 중심 종합 리포트(점수·지표·RCA 진단·개선 제안)를 외부 자원 없는 HTML 한 장으로 만든다(실측 약 69KB·0.5초).
HAIR는 평가마다 새 `PerformanceMonitor`를 만들고 점수만 읽은 뒤 버려 왔다 — 이제 그 자리에서 리포트를 만들어 둔다.

흐름(평가를 기록하는 호출부 7곳을 고치지 않으려고 이미 그 길을 따라 흐르는 `gate_scores`를 쓴다):
1. `agent_evaluation.run_and_score*()`가 모니터로 리포트를 만들어 압축해 이 모듈의 작은 메모리 캐시에 두고,
   `gate_scores["_eval_report"]`에 토큰만 넣는다(미측정·참고용 표시라 통과 판정·인박스·요약 집계가 무시한다).
2. `agent_evaluation.record_evaluation()`이 그 토큰을 빼고(저장되는 Gate 점수에는 남기지 않음) 리포트를
   `evaluation_reports`에 평가 id로 저장한다.
3. 결과물 카드의 "평가 리포트"가 `/api/deliverables/{id}/evaluation-report`로 최신 평가의 리포트를 연다 —
   스크립트가 든 HTML이라 CSP `sandbox`로 격리된 출처에서 연다(로그인 세션·API에 접근 못 함).

`HAIR_EVAL_HTML_REPORT=0`이면 만들지 않는다. 리포트 생성이 실패해도 평가는 그대로 진행된다.
"""

from __future__ import annotations

import gzip
import os
import secrets
import sqlite3
import threading
from collections import OrderedDict

REPORT_KEY = "_eval_report"
_CACHE_MAX = 256  # 평가 → 기록 사이에만 머문다(보통 수 초). 기록되지 않은 리포트는 오래된 것부터 버린다.
_CACHE: OrderedDict[str, bytes] = OrderedDict()
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("HAIR_EVAL_HTML_REPORT", "1") != "0"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_reports ("
        " evaluation_id INTEGER PRIMARY KEY REFERENCES agent_evaluations(id) ON DELETE CASCADE,"
        " html_gz BLOB NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    # 대화 메시지의 리포트(2026-11-25 — 대화 응답은 `agent_evaluations`를 거치지 않고 Gate 점수를 메시지에 바로 저장한다).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS message_evaluation_reports ("
        " message_id INTEGER PRIMARY KEY REFERENCES conversation_messages(id) ON DELETE CASCADE,"
        " html_gz BLOB NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def capture(monitor) -> dict | None:
    """모니터로 HTML 리포트를 만들어 캐시에 두고 `gate_scores`에 넣을 토큰 항목을 돌려준다(꺼져 있거나 실패하면 None)."""
    if not enabled():
        return None
    try:
        from agent_evaluator.reporting import generate_comprehensive_html_report

        html = generate_comprehensive_html_report(monitor)
    except Exception:  # noqa: BLE001 — 리포트는 부가 기능: 실패해도 평가·실행은 계속
        return None
    token = secrets.token_hex(8)
    with _LOCK:
        _CACHE[token] = gzip.compress(html.encode("utf-8"))
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return {"score": None, "status": "not_measured", "not_measured": True, "advisory": True, "passed": True,
            "token": token}


def public(gate_scores: dict | None) -> dict | None:
    """저장·API 응답용 Gate 점수 — 내부 항목(`_`로 시작, 리포트 토큰 등)을 뺀다. 메모리의 원본은 그대로 둔다(리포트 저장에 쓰임)."""
    if not gate_scores:
        return gate_scores
    return {k: v for k, v in gate_scores.items() if not str(k).startswith("_")}


def split(gate_scores: dict) -> tuple[dict, str | None]:
    """(리포트 토큰을 뺀 Gate 점수, 토큰) — 저장할 점수에는 토큰을 남기지 않는다(호출자 dict는 건드리지 않음)."""
    entry = (gate_scores or {}).get(REPORT_KEY)
    clean = {k: v for k, v in (gate_scores or {}).items() if k != REPORT_KEY}
    return clean, (entry.get("token") if isinstance(entry, dict) else None)


def store(conn: sqlite3.Connection, evaluation_id: int, token: str | None) -> bool:
    """캐시의 리포트를 평가 id로 저장한다(커밋은 호출자 — `record_evaluation()`과 같은 트랜잭션). 여러 결과물이
    같은 리포트를 쓰면(Crew 실행의 Agent별 결과물) 캐시에서 빼지 않고 각각 저장한다."""
    if not token:
        return False
    with _LOCK:
        blob = _CACHE.get(token)
    if blob is None:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO evaluation_reports (evaluation_id, html_gz) VALUES (?, ?)", (evaluation_id, blob)
    )
    return True


def html_for_deliverable(conn: sqlite3.Connection, deliverable_id: int) -> str | None:
    """결과물의 가장 최근 평가에 딸린 리포트(없으면 None)."""
    row = conn.execute(
        "SELECT r.html_gz FROM evaluation_reports r JOIN agent_evaluations e ON e.id = r.evaluation_id "
        "WHERE e.deliverable_id = ? ORDER BY e.evaluated_at DESC, e.id DESC LIMIT 1",
        (deliverable_id,),
    ).fetchone()
    return gzip.decompress(row[0]).decode("utf-8") if row else None


def has_report(conn: sqlite3.Connection, evaluation_id: int) -> bool:
    return conn.execute("SELECT 1 FROM evaluation_reports WHERE evaluation_id = ?", (evaluation_id,)).fetchone() is not None


def store_for_message(conn: sqlite3.Connection, message_id: int, token: str | None) -> bool:
    """대화 메시지의 리포트 저장(커밋은 호출자). Crew 멘션처럼 메시지 여러 개가 한 평가를 공유하면 각각 저장한다."""
    if not token:
        return False
    with _LOCK:
        blob = _CACHE.get(token)
    if blob is None:
        return False
    conn.execute("INSERT OR REPLACE INTO message_evaluation_reports (message_id, html_gz) VALUES (?, ?)",
                 (message_id, blob))
    return True


def html_for_message(conn: sqlite3.Connection, message_id: int) -> str | None:
    row = conn.execute("SELECT html_gz FROM message_evaluation_reports WHERE message_id = ?", (message_id,)).fetchone()
    return gzip.decompress(row[0]).decode("utf-8") if row else None


def messages_with_report(conn: sqlite3.Connection, message_ids: list[int]) -> set[int]:
    if not message_ids:
        return set()
    ph = ",".join("?" * len(message_ids))
    return {r[0] for r in conn.execute(
        f"SELECT message_id FROM message_evaluation_reports WHERE message_id IN ({ph})", message_ids)}
