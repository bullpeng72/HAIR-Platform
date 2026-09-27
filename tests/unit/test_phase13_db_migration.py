"""Phase 13 — 실제 발생한 500 에러 수정: 기존 DB 파일에 새 컬럼 마이그레이션
(사용자 보고, 2026-09-27).

"AI 에이전트 관리"·"Crew 실행" 메뉴에서 500 Internal Server Error가 실제로
발생했다 — 원인은 데모 서버의 `hair.db`가 `agent_pool.framework` 컬럼이
생기기 전에 만들어진 파일이었고, `CREATE TABLE IF NOT EXISTS`는 이미 있는
테이블에 새 컬럼을 추가하지 못해서 `SELECT * FROM agent_pool` 결과에
`framework` 키가 없었기 때문이다(`row["framework"]`에서 KeyError → 500).
"""

from __future__ import annotations

from hair_platform import db


def test_gc95_init_schema_migrates_pre_existing_agent_pool_table_without_new_columns():
    conn = db.get_connection(":memory:")
    # framework/llm_provider/llm_model/last_output이 생기기 전의 "옛날" 스키마를
    # 흉내낸다 — 실제로 겪은 상황(기존 hair.db 파일)과 동일한 조건.
    conn.execute(
        "CREATE TABLE agent_pool ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "role_name TEXT NOT NULL, "
        "skill_tags TEXT NOT NULL DEFAULT '[]', "
        "cost_estimate REAL NOT NULL DEFAULT 0.0, "
        "performance_score REAL, "
        "status TEXT NOT NULL DEFAULT 'pending_onboarding', "
        "source_job_item_id INTEGER"
        ")"
    )
    conn.execute(
        "INSERT INTO agent_pool (role_name, status) VALUES ('기존 에이전트', 'active')"
    )
    conn.commit()

    # init_schema()를 다시 돌리는 것이 서버 재시작 시 실제로 벌어지는 일이다.
    db.init_schema(conn)

    row = conn.execute(
        "SELECT * FROM agent_pool WHERE role_name = '기존 에이전트'"
    ).fetchone()
    assert row["framework"] == "stub"  # 기본값으로 안전하게 채워졌다
    assert row["llm_provider"] is None
    assert row["llm_model"] is None
    assert row["last_output"] is None
    assert row["status"] == "active"  # 기존 데이터는 보존된다


def test_gc96_init_schema_is_idempotent_on_fresh_db():
    """새 DB에서는 컬럼이 이미 SCHEMA에 있으므로 마이그레이션이 아무 것도 안
    해야 하고, 두 번 불러도 에러가 없어야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 두 번째 호출도 에러 없이 통과해야 한다
    row = conn.execute(
        "INSERT INTO agent_pool (role_name) VALUES ('테스트') RETURNING framework"
    ).fetchone()
    assert row["framework"] == "stub"


def test_gc97_list_agent_pool_endpoint_survives_pre_migration_db(client_and_conn):
    """실제로 500이 났던 경로 — GET /api/agent-pool가 framework 없는 옛 행에도
    안전해야 한다(방금 마이그레이션했으므로 컬럼 자체는 이제 항상 있다)."""
    client, conn = client_and_conn
    conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("마이그레이션 이전 데이터",),
    )
    conn.commit()

    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200
    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200
    row = next(a for a in resp.json() if a["role_name"] == "마이그레이션 이전 데이터")
    assert row["framework"] == "stub"
