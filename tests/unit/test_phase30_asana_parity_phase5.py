"""Phase 30 — ASANA 모방 기획 Phase 5(마지막 단계): 온보딩 간소화(사용자
요청, 2026-10-08, "...기획 및 설계안을 제안해줘" →
승인 후 "Phase 1→5 순서로" 진행 지시).

코드 변경분은 `POST /api/demo/seed`(admin 전용, 샘플 Brief 1건+
Task 2건+Human 1명+Agent 1개 생성)뿐이다 — 탭 재구성("오늘 할 일"/
"설정" 2구역화)은 순수 UI 변경이라 `test_phase11_admin_user_nav_split.py`
의 `test_gc73`가 이미 갱신되어 검증한다(이 파일에서 중복 검증하지
않는다)."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc_admin_can_seed_demo_data(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/demo/seed")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["brief_id"]
    assert len(body["task_ids"]) == 2
    assert body["human_id"]
    assert body["agent_id"]

    brief_row = conn.execute(
        "SELECT * FROM briefs WHERE id = ?", (body["brief_id"],)
    ).fetchone()
    assert brief_row is not None
    assert (
        brief_row["submitted_by"]
        == conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[
            "id"
        ]
    )

    task_count = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE brief_id = ?", (body["brief_id"],)
    ).fetchone()["c"]
    assert task_count == 2

    agent_row = conn.execute(
        "SELECT status FROM agent_pool WHERE id = ?", (body["agent_id"],)
    ).fetchone()
    assert agent_row["status"] == "active"


def test_gc_non_admin_cannot_seed_demo_data(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/demo/seed")
    assert resp.status_code == 403, resp.text

    _login(client, "member1", "memberpass")
    resp = client.post("/api/demo/seed")
    assert resp.status_code == 403, resp.text


def test_gc_seeding_twice_creates_separate_data(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    first = client.post("/api/demo/seed").json()
    second = client.post("/api/demo/seed").json()
    assert first["brief_id"] != second["brief_id"]
    assert set(first["task_ids"]).isdisjoint(second["task_ids"])


def test_gc_seed_recorded_in_audit_log(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    body = client.post("/api/demo/seed").json()

    row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'demo_data_seeded' "
        "AND subject_id = ? ORDER BY id DESC LIMIT 1",
        (body["brief_id"],),
    ).fetchone()
    assert row is not None
