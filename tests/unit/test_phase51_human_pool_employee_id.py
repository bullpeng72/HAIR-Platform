"""Phase 51 — Human Pool·사용자(users) 사번(employee_id, 사용자 요청,
2026-10-27, "다른 UI에도 ID가 남아 있는지 확인해줘" → "사람의 경우 ID는
향후 사번 적용 예정으로 8자리~12자리 입력 및 표시 가능해야 함" → "사용자
등록에 사번 입력 및 표시되도록 UI개선").

agent_pool/tool_pool/skill_pool의 `code`와 달리 human_pool/users의
employee_id는 "종류 구분용 자동 배정 코드"가 아니라 관리자가 직접
입력하는 실제 조직 사번이다 — 선택 필드(아직 사번 체계가 없는 사람도
많음), 값을 주면 숫자 8~12자리인지만 검증하고, 중복은 DB UNIQUE
인덱스로 막는다. 두 테이블은 독립적으로 검증한다(같은 사람이라도
human_pool과 users가 1:1로 강제되지 않으므로 값을 서로 맞출 필요는
없다)."""

from __future__ import annotations

import pytest


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_create_human_pool_without_employee_id_still_works(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool", json={"name": "홍길동", "role": "QA", "skill_tags": []}
    )
    assert resp.status_code == 201, resp.text
    human_id = resp.json()["id"]

    resp = client.get("/api/human-pool")
    row = next(p for p in resp.json() if p["id"] == human_id)
    assert row["employee_id"] is None


def test_create_human_pool_with_valid_employee_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={
            "name": "김철수",
            "role": "백엔드 개발자",
            "skill_tags": [],
            "employee_id": "12345678",
        },
    )
    assert resp.status_code == 201, resp.text
    human_id = resp.json()["id"]

    resp = client.get("/api/human-pool")
    row = next(p for p in resp.json() if p["id"] == human_id)
    assert row["employee_id"] == "12345678"


@pytest.mark.parametrize(
    "bad_value", ["1234567", "1234567890123", "abcd5678", "12 345678"]
)
def test_create_human_pool_rejects_malformed_employee_id(client_and_conn, bad_value):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={
            "name": "박영희",
            "role": "QA",
            "skill_tags": [],
            "employee_id": bad_value,
        },
    )
    assert resp.status_code == 422, resp.text


def test_create_human_pool_rejects_duplicate_employee_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={
            "name": "이민수",
            "role": "QA",
            "skill_tags": [],
            "employee_id": "99988877",
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/api/human-pool",
        json={
            "name": "최영수",
            "role": "QA",
            "skill_tags": [],
            "employee_id": "99988877",
        },
    )
    assert resp.status_code == 409, resp.text


def test_update_human_pool_sets_employee_id_and_rejects_duplicate(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool", json={"name": "정미래", "role": "QA", "skill_tags": []}
    )
    human_id = resp.json()["id"]

    resp = client.patch(f"/api/human-pool/{human_id}", json={"employee_id": "11112222"})
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/human-pool")
    row = next(p for p in resp.json() if p["id"] == human_id)
    assert row["employee_id"] == "11112222"

    resp = client.post(
        "/api/human-pool",
        json={
            "name": "강다른",
            "role": "QA",
            "skill_tags": [],
            "employee_id": "33334444",
        },
    )
    other_id = resp.json()["id"]
    resp = client.patch(f"/api/human-pool/{other_id}", json={"employee_id": "11112222"})
    assert resp.status_code == 409, resp.text

    resp = client.patch(f"/api/human-pool/{human_id}", json={"employee_id": "bad"})
    assert resp.status_code == 422, resp.text


# ---------- users(사용자 등록) ----------


def test_create_user_without_employee_id_still_works(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "no_empid_user", "password": "pw12345", "role": "member"},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get("/api/users")
    row = next(u for u in resp.json() if u["username"] == "no_empid_user")
    assert row["employee_id"] is None


def test_create_user_with_valid_employee_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={
            "username": "empid_user",
            "password": "pw12345",
            "role": "member",
            "employee_id": "20261027",
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.get("/api/users")
    row = next(u for u in resp.json() if u["username"] == "empid_user")
    assert row["employee_id"] == "20261027"


@pytest.mark.parametrize("bad_value", ["1234567", "1234567890123", "abcdefgh"])
def test_create_user_rejects_malformed_employee_id(client_and_conn, bad_value):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={
            "username": "bad_empid_user",
            "password": "pw12345",
            "role": "member",
            "employee_id": bad_value,
        },
    )
    assert resp.status_code == 422, resp.text


def test_create_user_rejects_duplicate_employee_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={
            "username": "dup_empid_a",
            "password": "pw12345",
            "role": "member",
            "employee_id": "55566677",
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/api/users",
        json={
            "username": "dup_empid_b",
            "password": "pw12345",
            "role": "member",
            "employee_id": "55566677",
        },
    )
    assert resp.status_code == 409, resp.text


def test_patch_user_employee_id_sets_and_rejects_duplicate(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/users",
        json={"username": "patch_empid_a", "password": "pw12345", "role": "member"},
    )
    user_a = resp.json()["id"]
    resp = client.post(
        "/api/users",
        json={
            "username": "patch_empid_b",
            "password": "pw12345",
            "role": "member",
            "employee_id": "44455566",
        },
    )
    assert resp.status_code == 201, resp.text

    resp = client.patch(
        f"/api/users/{user_a}/employee-id", json={"employee_id": "99911122"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.get("/api/users")
    row = next(u for u in resp.json() if u["id"] == user_a)
    assert row["employee_id"] == "99911122"

    resp = client.patch(
        f"/api/users/{user_a}/employee-id", json={"employee_id": "44455566"}
    )
    assert resp.status_code == 409, resp.text

    resp = client.patch(f"/api/users/{user_a}/employee-id", json={"employee_id": "x"})
    assert resp.status_code == 422, resp.text

    resp = client.patch(
        "/api/users/999999/employee-id", json={"employee_id": "12121212"}
    )
    assert resp.status_code == 404, resp.text
