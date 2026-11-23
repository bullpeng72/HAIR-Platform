"""조직 정책 설정화(2026-11-21, "사번 형식이랑 초기화 보호 팀 설정화 진행해줘").

1. 사번 형식 — `models.py`의 숫자 8~12자리 고정 정규식 → admin 설정(`org_policy`).
2. 공장 초기화 보호 팀 — 팀 이름 목록 → 팀별 `protect_on_reset` 표시.
"""

from __future__ import annotations

import pytest

from hair_platform import data_admin, db, org_policy, teams


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


@pytest.fixture(autouse=True)
def _restore_policy():
    yield
    # 다른 테스트에 영향이 없도록 기본값으로(캐시는 모듈 전역).
    org_policy._STATE.update(
        pattern=org_policy.DEFAULT_PATTERN,
        hint=org_policy.DEFAULT_HINT,
        regex=org_policy.re.compile(org_policy.DEFAULT_PATTERN),
    )


# ---------- 1. 사번 형식 ----------


def test_default_policy_keeps_previous_rule(client_and_conn):
    assert org_policy.employee_id_policy()["is_default"]
    assert org_policy.validate_employee_id("12345678") == "12345678"
    assert org_policy.validate_employee_id(None) is None
    with pytest.raises(ValueError):
        org_policy.validate_employee_id("1234567")  # 7자리
    with pytest.raises(ValueError):
        org_policy.validate_employee_id("EMP-123456")


def test_admin_changes_pattern_and_user_creation_follows(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conn.execute("UPDATE users SET employee_id = '20260001' WHERE username = 'member1'")
    conn.commit()

    resp = client.put(
        "/api/org-policy",
        json={"employee_id_pattern": r"^EMP-\d{6}$", "employee_id_hint": "EMP- 뒤 숫자 6자리"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["employee_id"]
    assert body["pattern"] == r"^EMP-\d{6}$" and not body["is_default"]
    assert body["existing_mismatched"] >= 1  # 기존 사번은 바꾸지 않고 건수만 알린다

    ok = client.post(
        "/api/users",
        json={"username": "emp_new", "password": "pw123456", "role": "member", "employee_id": "EMP-123456"},
    )
    assert ok.status_code in (200, 201), ok.text
    bad = client.post(
        "/api/users",
        json={"username": "emp_bad", "password": "pw123456", "role": "member", "employee_id": "12345678"},
    )
    assert bad.status_code == 422
    assert "EMP- 뒤 숫자 6자리" in bad.text

    # 비우면 기본값으로.
    reset = client.put("/api/org-policy", json={"employee_id_pattern": "", "employee_id_hint": ""})
    assert reset.json()["employee_id"]["is_default"]
    assert org_policy.validate_employee_id("12345678") == "12345678"


def test_invalid_pattern_rejected_and_only_admin_can_change(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    assert client.get("/api/org-policy").status_code == 200  # 안내 문구는 누구나 읽는다
    assert client.put("/api/org-policy", json={"employee_id_pattern": r"^\d+$"}).status_code == 403
    _login(client, "admin1", "adminpass")
    resp = client.put("/api/org-policy", json={"employee_id_pattern": "^(EMP"})
    assert resp.status_code == 400 and "invalid_pattern" in resp.text


def test_policy_survives_restart(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    client.put("/api/org-policy", json={"employee_id_pattern": r"^[A-Z]{2}\d{4}$", "employee_id_hint": "영문 2자+숫자 4자리"})
    org_policy._STATE.update(pattern="x", hint="x", regex=org_policy.re.compile("x"))  # 캐시를 흐트러뜨린 뒤
    db.init_schema(conn)  # 재기동하면 DB 값으로 다시 올라온다
    assert org_policy.employee_id_policy()["hint"] == "영문 2자+숫자 4자리"
    assert org_policy.validate_employee_id("AB1234") == "AB1234"


# ---------- 2. 공장 초기화 보호 팀 ----------


def test_reset_protection_follows_team_flag_not_name(client_and_conn):
    client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    renamed = teams.create_team(conn, admin_id, "이름이 바뀐 보호 팀")
    same_name_unprotected = teams.create_team(conn, admin_id, "데이터 분석·시각화 전담팀")

    _login(client, "manager1", "managerpass")
    url = f"/api/teams/{renamed}/reset-protection"
    assert client.patch(url, json={"protect_on_reset": True}).status_code == 403  # admin 전용
    _login(client, "admin1", "adminpass")
    assert client.patch(url, json={"protect_on_reset": True}).status_code == 200
    assert teams.get_team(conn, renamed)["protect_on_reset"] is True

    data_admin.reset_to_factory_state(conn)
    remaining = {r[0] for r in conn.execute("SELECT id FROM teams")}
    assert renamed in remaining
    assert same_name_unprotected not in remaining  # 이름만으로는 더 이상 보호되지 않는다


def test_legacy_named_team_gets_flag_once_on_migration():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    from hair_platform.auth import Role, create_user

    actor = create_user(conn, "op_admin", "pw", Role.ADMIN)
    team_id = teams.create_team(conn, actor, "데이터 분석·시각화 전담팀")
    # 업그레이드 전 DB 재현: 마이그레이션 기록이 없는 상태에서 재기동.
    conn.execute("DELETE FROM schema_migrations WHERE name = '2026-11-21-team-protect-on-reset'")
    conn.commit()
    db.init_schema(conn)
    assert teams.get_team(conn, team_id)["protect_on_reset"] is True
    # admin이 끄면 재기동해도 다시 켜지 않는다.
    teams.set_team_reset_protection(conn, actor, team_id, False)
    db.init_schema(conn)
    assert teams.get_team(conn, team_id)["protect_on_reset"] is False
