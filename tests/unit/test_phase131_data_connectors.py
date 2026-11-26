"""사내 DB 조회 연동(2026-11-26, P1-9 — `data_connectors.py`)."""

from __future__ import annotations

import sqlite3

import pytest

from hair_platform import data_admin, data_connectors, knowledge_access


@pytest.fixture
def sales_db(tmp_path):
    path = tmp_path / "sales.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, quarter TEXT, region TEXT, amount INTEGER, memo TEXT)")
    c.executemany("INSERT INTO sales (quarter, region, amount, memo) VALUES (?, ?, ?, ?)",
                  [("2026Q3", r, 100 * i, "delete me" if i == 1 else "") for i, r in
                   enumerate(["서울", "부산", "대구", "광주", "대전"] * 60, start=1)])
    c.commit()
    c.close()
    return str(path)


def _admin(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM sales",
    "  select region, sum(amount) from sales group by region;",
    "WITH q AS (SELECT * FROM sales) SELECT count(*) FROM q",
    "EXPLAIN SELECT * FROM sales",
    "SELECT * FROM sales WHERE memo = 'delete me'",  # 리터럴 안의 낱말은 막지 않는다
    "SELECT replace(region, '서울', 'Seoul') FROM sales",  # 함수 이름 REPLACE
])
def test_read_only_sql_is_allowed(sql):
    assert data_connectors.check_sql(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM sales",
    "UPDATE sales SET amount = 0",
    "SELECT 1; DROP TABLE sales",
    "WITH x AS (DELETE FROM sales RETURNING *) SELECT * FROM x",  # PostgreSQL 데이터 변경 CTE
    "SELECT * INTO backup FROM sales",
    "SELECT * FROM sales INTO OUTFILE '/tmp/x'",
    "/* SELECT */ DROP TABLE sales",
    "PRAGMA table_info(sales)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "",
])
def test_writes_and_multi_statements_are_rejected(sql):
    with pytest.raises(data_connectors.ConnectorError):
        data_connectors.check_sql(sql)


def test_sqlite_connection_tables_describe_query_and_row_limit(client_and_conn, sales_db):
    _client, conn = client_and_conn
    data_connectors.register(conn, _admin(conn), "sales_db", "sqlite", sales_db, description="영업 실적", row_limit=50)
    assert "sales_db (sqlite, 최대 50행): 영업 실적" in data_connectors.run_action(conn, "connections")
    assert "sales" in data_connectors.run_action(conn, "tables")
    described = data_connectors.run_action(conn, "describe", table="sales")
    assert "amount" in described and "INTEGER" in described
    agg = data_connectors.run_action(conn, "query", sql="SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region")
    assert "5행" in agg and "| 서울 |" in agg
    many = data_connectors.run_query(conn, "SELECT * FROM sales")
    assert len(many["rows"]) == 50 and many["truncated"] is True
    assert "잘림" in data_connectors.run_action(conn, "query", sql="SELECT * FROM sales")
    assert "DB 조회 실패" in data_connectors.run_action(conn, "query", sql="DELETE FROM sales")


def test_connection_itself_is_read_only_even_if_guard_is_bypassed(client_and_conn, sales_db):
    _client, conn = client_and_conn
    data_connectors.register(conn, _admin(conn), "sales_db", "sqlite", sales_db)
    row = conn.execute("SELECT * FROM data_connections WHERE name = 'sales_db'").fetchone()
    db = data_connectors._open(row)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM sales")
    db.close()
    assert sqlite3.connect(sales_db).execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 300


def test_slow_query_is_stopped(client_and_conn, sales_db, monkeypatch):
    _client, conn = client_and_conn
    monkeypatch.setattr(data_connectors, "TIMEOUT_SECONDS", 0.3)
    data_connectors.register(conn, _admin(conn), "sales_db", "sqlite", sales_db)
    slow = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(data_connectors.ConnectorError, match="초를 넘어"):
        data_connectors.run_query(conn, slow)


def test_query_is_audited_with_requester(client_and_conn, sales_db):
    _client, conn = client_and_conn
    admin = _admin(conn)
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    data_connectors.register(conn, admin, "sales_db", "sqlite", sales_db)
    with knowledge_access.as_viewer(member):
        data_connectors.run_query(conn, "SELECT COUNT(*) FROM sales")
    row = conn.execute("SELECT actor_id, after_value FROM audit_log WHERE action = 'data_query_executed'").fetchone()
    assert row[0] == member and "COUNT(*)" in row[1]


def test_multiple_connections_need_a_name_and_drivers_are_reported(client_and_conn, sales_db, tmp_path):
    _client, conn = client_and_conn
    admin = _admin(conn)
    data_connectors.register(conn, admin, "sales_db", "sqlite", sales_db)
    data_connectors.register(conn, admin, "erp", "postgresql", "db.internal:5432/erp", username="reader",
                             password_env="HAIR_DB_ERP_PASSWORD")
    assert "연결이 여러 개" in data_connectors.run_action(conn, "tables")
    msg = data_connectors.run_action(conn, "tables", connection="erp")
    assert "드라이버" in msg or "조회 실패" in msg  # psycopg 미설치면 설치 안내, 설치돼 있으면 접속 실패
    assert data_connectors.get(conn, 2)["password_set"] is False  # 값은 저장하지 않고 환경변수 이름만


def test_register_validation_api_rbac_and_reset(client_and_conn, sales_db):
    client, conn = client_and_conn
    with pytest.raises(data_connectors.ConnectorError):
        data_connectors.register(conn, _admin(conn), "x", "oracle", sales_db)
    with pytest.raises(data_connectors.ConnectorError):
        data_connectors.register(conn, _admin(conn), "x", "sqlite", "/없는/경로.db")
    with pytest.raises(data_connectors.ConnectorError):
        data_connectors.register(conn, _admin(conn), "x", "postgresql", "h:1/d", password_env="my-password")
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/data-connections").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.post("/api/data-connections", json={"name": "sales_db", "kind": "sqlite", "target": sales_db})
    assert r.status_code == 201
    cid = r.json()["id"]
    t = client.post(f"/api/data-connections/{cid}/test")
    assert t.status_code == 200 and "sales" in t.json()["tables"]
    assert client.patch(f"/api/data-connections/{cid}", json={"is_active": False}).json()["is_active"] is False
    assert client.post(f"/api/data-connections/{cid}/test").status_code == 400  # 꺼진 연결
    data_admin.reset_to_factory_state(conn)
    assert conn.execute("SELECT COUNT(*) FROM data_connections").fetchone()[0] == 0


def test_tool_and_skill_are_seeded_for_analysis_agents(client_and_conn):
    _client, conn = client_and_conn
    tool = conn.execute("SELECT config FROM tool_pool WHERE name = '사내 DB 조회'").fetchone()
    assert tool is not None and "database_query" in tool[0]
    holders = {r[0] for r in conn.execute("SELECT role_name FROM agent_pool WHERE skill_tags LIKE '%사내DB조회%'")}
    assert holders == {"데이터 분석가", "재무 분석가", "영업 분석가", "실적 보고서 작성자", "인사 분석가"}  # 인사 분석가: 2026-11-26 인사 템플릿
