"""로그인 시도 제한(2026-11-27, 개선 제안 5-2 — 예전엔 비밀번호를 무한히 대입해 볼 수 있었다)."""

from __future__ import annotations

from hair_platform import login_guard


def _try(client, name, pw):
    return client.post("/login", json={"username": name, "password": pw})


def test_account_is_locked_after_five_failures_even_with_right_password(client_and_conn):
    client, conn = client_and_conn
    for _ in range(4):
        assert _try(client, "member1", "wrong").status_code == 401
    assert _try(client, "member1", "memberpass").status_code == 200  # 잠기기 전 성공은 셈을 지운다
    for _ in range(5):
        assert _try(client, "member1", "wrong").status_code == 401
    r = _try(client, "member1", "memberpass")
    assert r.status_code == 429 and "분 뒤" in r.json()["detail"] and int(r.headers["Retry-After"]) > 0
    assert _try(client, "admin1", "adminpass").status_code == 200  # 다른 계정은 그대로
    row = conn.execute("SELECT after_value FROM audit_log WHERE action = 'login_locked'").fetchone()
    assert row is not None


def test_lock_expires(client_and_conn, monkeypatch):
    client, _conn = client_and_conn
    clock = [1000.0]
    monkeypatch.setattr(login_guard, "_now", lambda: clock[0])
    for _ in range(5):
        _try(client, "member1", "wrong")
    assert _try(client, "member1", "memberpass").status_code == 429
    clock[0] += login_guard.LOCK_SECONDS + 1
    assert _try(client, "member1", "memberpass").status_code == 200


def test_spraying_many_usernames_from_one_address_is_blocked(client_and_conn):
    client, _conn = client_and_conn
    for i in range(login_guard.MAX_FAILURES_PER_IP):
        _try(client, f"nobody{i}", "password1")
    assert _try(client, "admin1", "adminpass").status_code == 429  # 같은 주소 전체를 잠시 막는다


def test_admin_reset_unlocks_the_account(client_and_conn):
    client, conn = client_and_conn
    for _ in range(5):
        _try(client, "member1", "wrong")
    assert _try(client, "member1", "memberpass").status_code == 429
    login_guard._FAILS.pop("ip:testclient", None)
    login_guard._LOCKED_UNTIL.pop("ip:testclient", None)
    assert _try(client, "admin1", "adminpass").status_code == 200
    member_id = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    assert client.patch(f"/api/users/{member_id}/password", json={"password": "fresh-pass-1"}).status_code == 200
    assert _try(client, "member1", "fresh-pass-1").status_code == 200
