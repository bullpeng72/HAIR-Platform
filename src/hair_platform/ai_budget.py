"""AI 사용 한도(2026-11-25, P2-14 — "AI 비용 상한·할당이 없음").

예전에는 누구든(viewer 포함) 대화에서 AI를 부를 수 있었고 사용자·조직 단위 비용 한도가 없었다. admin이 한 달 기준
**사용자별 한도**와 **조직 전체 한도**(USD)를 정하고, viewer의 AI 호출 허용 여부를 정한다. 모두 기본값은 "제한 없음·
허용"이라 설정하기 전에는 동작이 바뀌지 않는다.

- 비용 = 이번 달(UTC 1일부터) `llm_usage` 토큰 × "모델 레지스트리" 단가. 단가를 등록하지 않은 모델(로컬 Ollama 등)은
  0으로 센다 — 금액이 들지 않는 호출을 막지 않는다.
- 막는 곳: AI를 부르기 직전(`llm.call_llm`·`llm.call_llm_with_tools`·`crew_runtime.run_crew`). 넘었으면 호출하지
  않고 `BudgetExceeded`(사람이 읽을 사유)를 올린다 — 실행 실패로 인박스에 남는다.
- 누구의 사용인지: 작업 입구에서 정한 요청자(`knowledge_access.current_viewer()`, P1-5와 같은 문맥)를 사용 기록
  (`llm_usage.user_id`)에 남긴다. admin은 사용자별 한도에서 빠지고 조직 전체 한도만 적용된다(갑작스러운 청구 방지).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime

DEFAULTS: dict = {
    "user_monthly_usd": None,   # 사용자 1명의 한 달 한도(없으면 제한 없음)
    "org_monthly_usd": None,    # 조직 전체 한 달 한도
    "warn_ratio": 0.8,          # 이 비율을 넘으면 비용 화면에 경고
    "viewer_ai_allowed": True,  # viewer가 대화에서 AI를 부를 수 있는지
}
_MAX_USD = 1_000_000.0

_CONN: sqlite3.Connection | None = None
_CACHE: dict | None = None
_LOCK = threading.Lock()


class BudgetExceeded(RuntimeError):
    """한도를 넘어 AI를 부르지 않았다 — 메시지는 그대로 사람에게 보여 준다."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ai_budget_config ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " config TEXT NOT NULL,"
        " updated_by INTEGER REFERENCES users(id),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)")}
    if cols and "user_id" not in cols:
        conn.execute("ALTER TABLE llm_usage ADD COLUMN user_id INTEGER")
    if cols:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user_month ON llm_usage(user_id, created_at)")


def configure(conn: sqlite3.Connection) -> None:
    global _CONN, _CACHE
    _CONN = conn
    _CACHE = None


def get(conn: sqlite3.Connection | None = None) -> dict:
    global _CACHE
    if conn is None:
        if _CACHE is not None:
            return _CACHE
        conn = _CONN
    cfg = dict(DEFAULTS)
    if conn is not None:
        try:
            row = conn.execute("SELECT config FROM ai_budget_config WHERE id = 1").fetchone()
            if row:
                cfg.update({k: v for k, v in json.loads(row[0]).items() if k in DEFAULTS})
        except sqlite3.Error:
            pass
    if conn is _CONN:
        with _LOCK:
            _CACHE = cfg
    return cfg


def validate(payload: dict) -> dict:
    cfg = dict(DEFAULTS)

    def _usd(value, name):
        if value in (None, ""):
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}은 금액(USD) 숫자여야 합니다") from None
        if not 0 <= v <= _MAX_USD:
            raise ValueError(f"{name}은 0~{int(_MAX_USD):,} USD 사이여야 합니다")
        return round(v, 2)

    cfg["user_monthly_usd"] = _usd(payload.get("user_monthly_usd"), "사용자별 한 달 한도")
    cfg["org_monthly_usd"] = _usd(payload.get("org_monthly_usd"), "조직 전체 한 달 한도")
    try:
        warn = float(payload.get("warn_ratio", DEFAULTS["warn_ratio"]))
    except (TypeError, ValueError):
        raise ValueError("경고 비율은 0~1 사이 숫자여야 합니다") from None
    if not 0.1 <= warn <= 1.0:
        raise ValueError("경고 비율은 0.1~1 사이여야 합니다")
    cfg["warn_ratio"] = round(warn, 2)
    cfg["viewer_ai_allowed"] = bool(payload.get("viewer_ai_allowed", True))
    return cfg


def save(conn: sqlite3.Connection, actor_id: int, payload: dict) -> dict:
    from hair_platform import audit

    global _CACHE
    before = get(conn)
    cfg = validate(payload)
    conn.execute(
        "INSERT INTO ai_budget_config (id, config, updated_by, updated_at) VALUES (1, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET config = excluded.config, updated_by = excluded.updated_by, "
        "updated_at = excluded.updated_at",
        (json.dumps(cfg, ensure_ascii=False), actor_id),
    )
    audit.record(conn, actor_id, "ai_budget_updated", "ai_budget_config", 1, before=before, after=cfg)
    conn.commit()
    with _LOCK:
        _CACHE = None
    return cfg


def _month_start() -> str:
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-01 00:00:00")


def month_cost(conn: sqlite3.Connection, user_id: int | None = None) -> float:
    """이번 달 비용(USD) — `user_id`를 주면 그 사람 것만. 단가 없는 모델은 0."""
    sql = (
        "SELECT COALESCE(SUM(u.prompt_tokens / 1000000.0 * COALESCE(m.price_input_per_1m, 0) "
        " + u.completion_tokens / 1000000.0 * COALESCE(m.price_output_per_1m, 0)), 0) "
        "FROM llm_usage u LEFT JOIN model_registry m ON m.provider = u.provider AND m.name = u.model "
        "WHERE u.created_at >= ?"
    )
    params: list = [_month_start()]
    if user_id is not None:
        sql += " AND u.user_id = ?"
        params.append(user_id)
    try:
        return float(conn.execute(sql, params).fetchone()[0] or 0.0)
    except sqlite3.Error:
        return 0.0


def is_unpriced(conn: sqlite3.Connection, provider: str | None, model: str | None) -> bool:
    """단가를 등록하지 않은 모델(로컬 Ollama 등 — 금액이 들지 않음)인지. 모르면 False(한도 적용 쪽)."""
    if not provider or not model:
        return False
    if provider == "ollama":
        return True
    try:
        row = conn.execute(
            "SELECT price_input_per_1m, price_output_per_1m FROM model_registry WHERE provider = ? AND name = ?",
            (provider, model),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is None or (row[0] is None and row[1] is None)


def check(user_id: int | None = None, provider: str | None = None, model: str | None = None) -> None:
    """AI를 부르기 직전 — 한도를 넘었거나 viewer 호출이 막혀 있으면 `BudgetExceeded`. 설정이 없으면 아무것도 안 한다.
    부를 모델(`provider`·`model`)이 단가 없는 모델(로컬 Ollama 등)이면 금액이 들지 않으므로 한도로 막지 않는다
    (viewer 호출 허용 여부는 그대로 본다)."""
    conn = _CONN
    if conn is None:
        return
    cfg = get()
    if cfg["user_monthly_usd"] is None and cfg["org_monthly_usd"] is None and cfg["viewer_ai_allowed"]:
        return
    if user_id is None:
        from hair_platform import knowledge_access

        user_id = knowledge_access.current_viewer()
    role = None
    if user_id is not None:
        try:
            row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            role = row[0] if row else None
        except sqlite3.Error:
            role = None
    if role == "viewer" and not cfg["viewer_ai_allowed"]:
        raise BudgetExceeded("열람자(viewer) 계정은 AI를 부를 수 없도록 설정돼 있습니다 — admin에게 문의하세요")
    if is_unpriced(conn, provider, model):
        return
    if cfg["org_monthly_usd"] is not None:
        spent = month_cost(conn)
        if spent >= cfg["org_monthly_usd"]:
            raise BudgetExceeded(
                f"이번 달 조직 전체 AI 비용 한도(${cfg['org_monthly_usd']:,.2f})에 도달해 AI를 부르지 않았습니다 "
                f"(사용 ${spent:,.2f}) — admin이 \"비용\" 탭에서 한도를 바꿀 수 있습니다"
            )
    if cfg["user_monthly_usd"] is not None and user_id is not None and role != "admin":
        spent = month_cost(conn, user_id)
        if spent >= cfg["user_monthly_usd"]:
            raise BudgetExceeded(
                f"이번 달 내 AI 비용 한도(${cfg['user_monthly_usd']:,.2f})에 도달해 AI를 부르지 않았습니다 "
                f"(사용 ${spent:,.2f}) — 다음 달에 초기화되며, 더 필요하면 admin에게 요청하세요"
            )


def status(conn: sqlite3.Connection) -> dict:
    """비용 화면용 — 설정과 이번 달 사용(조직·사용자별 상위)."""
    cfg = get(conn)
    org = month_cost(conn)
    users = []
    try:
        rows = conn.execute(
            "SELECT u.user_id, us.username, us.role, "
            "SUM(u.prompt_tokens / 1000000.0 * COALESCE(m.price_input_per_1m, 0) "
            " + u.completion_tokens / 1000000.0 * COALESCE(m.price_output_per_1m, 0)) AS cost "
            "FROM llm_usage u LEFT JOIN model_registry m ON m.provider = u.provider AND m.name = u.model "
            "LEFT JOIN users us ON us.id = u.user_id WHERE u.created_at >= ? AND u.user_id IS NOT NULL "
            "GROUP BY u.user_id ORDER BY cost DESC LIMIT 50",
            (_month_start(),),
        ).fetchall()
        limit = cfg["user_monthly_usd"]
        for r in rows:
            cost = float(r["cost"] or 0.0)
            users.append({"user_id": r["user_id"], "username": r["username"], "role": r["role"],
                          "cost": round(cost, 4),
                          "ratio": (round(cost / limit, 3) if limit and r["role"] != "admin" else None)})
    except sqlite3.Error:
        pass
    org_ratio = round(org / cfg["org_monthly_usd"], 3) if cfg["org_monthly_usd"] else None
    return {"config": cfg, "month_start": _month_start(), "org_cost": round(org, 4), "org_ratio": org_ratio,
            "users": users}
