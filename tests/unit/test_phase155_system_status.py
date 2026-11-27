"""시스템 상태 한눈에 + /health(2026-11-27, 사용성 평가 11번·개선 제안 5-4)."""

from __future__ import annotations

from hair_platform import system_status


def test_health_is_public_and_has_no_internal_details(client_and_conn):
    client, _conn = client_and_conn
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok" and body["status"] in ("ok", "degraded")
    text = r.text
    assert "hair.db" not in text and "ollama" not in text.lower() and "model" not in text  # 경로·모델 이름 없음


def test_system_status_is_admin_only_and_lists_sections(client_and_conn, monkeypatch):
    client, _conn = client_and_conn
    monkeypatch.setattr(system_status, "_ollama_reachable", lambda base: (False, "connection refused"))
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    assert client.get("/api/admin/system-status").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    st = client.get("/api/admin/system-status").json()
    keys = {it["key"] for sec in st["sections"] for it in sec["items"]}
    assert {"server", "db", "jobs", "llm", "config"} <= keys
    db_item = next(it for sec in st["sections"] for it in sec["items"] if it["key"] == "db")
    assert db_item["status"] == "ok"
    assert st["status"] in ("ok", "warn", "error")


def test_unreachable_ollama_is_an_error_when_ollama_is_used(client_and_conn, monkeypatch):
    client, conn = client_and_conn
    conn.execute("INSERT INTO model_registry (provider, name, is_active) VALUES ('ollama', 'm', 1)")
    conn.commit()
    monkeypatch.setattr(system_status, "_ollama_reachable", lambda base: (False, "connection refused"))
    st = system_status.summary(conn)
    oll = next(it for sec in st["sections"] for it in sec["items"] if it["key"] == "ollama")
    assert oll["status"] == "error" and "Ollama" in oll["detail"] and st["status"] == "error"


def test_low_disk_is_a_warning(tmp_path, monkeypatch):
    from hair_platform import db

    conn = db.get_connection(str(tmp_path / "s.db"))
    db.init_schema(conn)

    class Usage:
        free = 500 * 1024**2

    monkeypatch.setattr(system_status.shutil, "disk_usage", lambda path: Usage())
    disk = next(it for it in system_status._database(conn) if it["key"] == "disk")
    assert disk["status"] == "warn" and "2GB" in disk["detail"]


def test_brief_tasks_report_assignment_counts(client_and_conn):
    """업무 요청 상세의 단계 표시(2026-11-27, 사용성 평가 8번) — 할 일마다 확정 배정·결재 대기 배정 수."""
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    brief = client.post("/api/briefs", json={"raw_text": "분기 매출 보고서"}).json()
    tasks = client.post(f"/api/briefs/{brief['id']}/tasks",
                        json={"tasks": [{"role_name": "실적 보고서 작성자", "required_skills": []},
                                        {"role_name": "재무 분석가", "required_skills": []},
                                        {"role_name": "발표 자료 작성자", "required_skills": []}]}).json()["tasks"]
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    for tid, st in ((tasks[0]["id"], "confirmed"), (tasks[1]["id"], "requested"), (tasks[2]["id"], "cancelled")):
        conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'agent', 1, ?, ?)",
                     (tid, admin, st))
    conn.commit()
    rows = {t["id"]: t for t in client.get(f"/api/briefs/{brief['id']}/tasks").json()}
    assert (rows[tasks[0]["id"]]["assigned_count"], rows[tasks[0]["id"]]["assignment_pending_count"]) == (1, 0)
    assert (rows[tasks[1]["id"]]["assigned_count"], rows[tasks[1]["id"]]["assignment_pending_count"]) == (0, 1)
    assert (rows[tasks[2]["id"]]["assigned_count"], rows[tasks[2]["id"]]["assignment_pending_count"]) == (0, 0)  # 취소는 안 셈
