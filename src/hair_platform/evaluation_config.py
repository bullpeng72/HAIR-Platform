"""평가 기준 설정(2026-11-25, 사용자 요청 "평가기준 설정 화면 만들어줘").

Agent-Evaluator SDK로 결과물을 채점할 때 쓰는 기준 — 예전엔 `agent_evaluation.py`에 상수로 박혀 있어(모든 Gate 0.7,
SLA p95 30초·p99 60초, 한국어 추론 표지 7개, 도구 범위 감점 0.5) 바꾸려면 코드를 고치고 서버를 다시 띄워야 했다.
admin이 화면에서 바꾸고, 다음 평가부터 바로 적용된다(이미 기록된 평가는 다시 채점하지 않는다).

- Gate별 통과 기준(`gates`, 0~1) — SDK `evaluate_gate_scores(thresholds=…)`. 비운 Gate는 `gate_default`.
- 응답 시간 SLA(`sla_p95_ms`·`sla_p99_ms`) — SDK `SLAConfig`, Gate C(신뢰성)·D(성능).
- 추가 추론 표지(`reasoning_markers`) — SDK `ExplainabilityConfig.reasoning_markers`에 더한다, Gate G(관측성).
- 도구 범위 위반 감점(`scope_violation_penalty`) — SDK `ScopeConfig`.

`.aoo/targets.json` 호환: SDK CLI(`agent-eval gate`)가 읽는 목표 파일 형식(`gates`·`gate_default`)으로 내보내고
가져올 수 있어, 저장된 결과 파일을 CLI로 채점할 때도 같은 기준을 쓸 수 있다.
"""

from __future__ import annotations

import json
import sqlite3
import threading

GATE_IDS = ("A", "B", "C", "D", "E", "F", "G")
DEFAULTS: dict = {
    "gate_default": 0.7,
    "gates": {},
    "sla_p95_ms": 30000.0,
    "sla_p99_ms": 60000.0,
    "reasoning_markers": ["근거", "때문", "므로", "이유", "판단", "그러므로", "그래서"],
    "scope_violation_penalty": 0.5,
}
MAX_MARKERS = 30
MAX_MARKER_CHARS = 20

_LOCK = threading.Lock()
_CACHE: dict | None = None
_CONN: sqlite3.Connection | None = None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_config ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " config TEXT NOT NULL,"
        " updated_by INTEGER REFERENCES users(id),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def configure(conn: sqlite3.Connection) -> None:
    """평가가 DB 연결 없이 도는 경로(`agent_evaluation.run_and_score`)가 읽을 연결을 정한다(`create_app()`이 부른다)."""
    global _CONN, _CACHE
    _CONN = conn
    _CACHE = None


def _merged(stored: dict | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    for key, value in (stored or {}).items():
        if key in cfg:
            cfg[key] = value
    return cfg


def get(conn: sqlite3.Connection | None = None) -> dict:
    """현재 기준(기본값 위에 저장값). 연결이 없으면 `configure()`로 정한 연결, 그것도 없으면 기본값."""
    global _CACHE
    if conn is None:
        if _CACHE is not None:
            return _CACHE
        conn = _CONN
    stored = None
    if conn is not None:
        try:
            row = conn.execute("SELECT config FROM evaluation_config WHERE id = 1").fetchone()
            stored = json.loads(row[0]) if row else None
        except sqlite3.Error:
            stored = None
    cfg = _merged(stored)
    if conn is _CONN:
        with _LOCK:
            _CACHE = cfg
    return cfg


def thresholds(cfg: dict) -> dict[str, float]:
    """Gate별 기준(빈 Gate는 기본 기준)."""
    return {g: float(cfg["gates"].get(g, cfg["gate_default"])) for g in GATE_IDS}


def validate(payload: dict) -> dict:
    """입력 검증 — 잘못되면 ValueError("…" 한국어 사유)."""
    cfg = _merged(None)

    def _ratio(value, name):
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}은 0~1 사이 숫자여야 합니다") from None
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{name}은 0~1 사이여야 합니다")
        return round(v, 3)

    cfg["gate_default"] = _ratio(payload.get("gate_default", cfg["gate_default"]), "기본 통과 기준")
    gates = payload.get("gates") or {}
    if not isinstance(gates, dict):
        raise ValueError("Gate별 기준 형식이 올바르지 않습니다")
    cfg["gates"] = {}
    for gid, value in gates.items():
        if gid not in GATE_IDS:
            raise ValueError(f"알 수 없는 Gate입니다: {gid}")
        if value in (None, ""):
            continue
        cfg["gates"][gid] = _ratio(value, f"Gate {gid} 기준")
    try:
        p95 = float(payload.get("sla_p95_ms", cfg["sla_p95_ms"]))
        p99 = float(payload.get("sla_p99_ms", cfg["sla_p99_ms"]))
    except (TypeError, ValueError):
        raise ValueError("응답 시간 SLA는 밀리초 숫자여야 합니다") from None
    if not 1000 <= p95 <= 3_600_000 or not 1000 <= p99 <= 3_600_000:
        raise ValueError("응답 시간 SLA는 1초(1000ms)~1시간 사이여야 합니다")
    if p99 < p95:
        raise ValueError("p99는 p95보다 같거나 커야 합니다")
    cfg["sla_p95_ms"], cfg["sla_p99_ms"] = p95, p99
    markers = payload.get("reasoning_markers", cfg["reasoning_markers"])
    if isinstance(markers, str):
        markers = [m for m in (x.strip() for x in markers.replace(",", "\n").splitlines()) if m]
    if not isinstance(markers, list) or not all(isinstance(m, str) for m in markers):
        raise ValueError("추론 표지는 낱말 목록이어야 합니다")
    markers = list(dict.fromkeys(m.strip() for m in markers if m.strip()))
    if len(markers) > MAX_MARKERS or any(len(m) > MAX_MARKER_CHARS for m in markers):
        raise ValueError(f"추론 표지는 {MAX_MARKERS}개, 각 {MAX_MARKER_CHARS}자까지입니다")
    cfg["reasoning_markers"] = markers
    cfg["scope_violation_penalty"] = _ratio(payload.get("scope_violation_penalty", cfg["scope_violation_penalty"]),
                                            "도구 범위 위반 감점")
    return cfg


def save(conn: sqlite3.Connection, actor_id: int, payload: dict) -> dict:
    from hair_platform import audit

    before = get(conn)
    cfg = validate(payload)
    conn.execute(
        "INSERT INTO evaluation_config (id, config, updated_by, updated_at) VALUES (1, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET config = excluded.config, updated_by = excluded.updated_by, "
        "updated_at = excluded.updated_at",
        (json.dumps(cfg, ensure_ascii=False), actor_id),
    )
    audit.record(conn, actor_id, "evaluation_config_updated", "evaluation_config", 1, before=before, after=cfg)
    conn.commit()
    if conn is _CONN:
        configure(conn)
    return get(conn)


def reset(conn: sqlite3.Connection, actor_id: int) -> dict:
    """기본값으로 되돌린다."""
    return save(conn, actor_id, json.loads(json.dumps(DEFAULTS)))


def to_targets(cfg: dict) -> dict:
    """SDK `.aoo/targets.json` 형식(`agent-eval gate`가 읽음) — Gate 기준만 담는다(SLA·표지는 SDK CLI 대상 아님)."""
    return {"gate_default": cfg["gate_default"], "gates": thresholds(cfg)}


def from_targets(data: dict, current: dict) -> dict:
    """`.aoo/targets.json`의 Gate 기준을 현재 설정 위에 얹은 입력(저장 전 — `save()`로 검증·저장)."""
    if not isinstance(data, dict):
        raise ValueError("targets.json 형식이 올바르지 않습니다")
    merged = dict(current)
    if data.get("gate_default") is not None:
        merged["gate_default"] = data["gate_default"]
    if isinstance(data.get("gates"), dict):
        merged["gates"] = {k: v for k, v in data["gates"].items() if k in GATE_IDS}
    return merged
