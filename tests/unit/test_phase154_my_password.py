"""내 비밀번호 바꾸기 + 비밀번호가 바뀌면 옛 로그인 끊기(2026-11-27, 사용성 평가 13번·개선 제안 5-3).

예전: 사용자는 자기 비밀번호를 바꿀 수 없었고(관리자 재설정만), 로그인 토큰은 비밀번호가 바뀌어도 14일 동안 유효했다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hair_platform import auth


def _login(client, name="member1", pw="memberpass"):
    r = client.post("/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token):
    return {"Authorization": "Bearer " + token}


def test_change_my_password_checks_current_and_length(client_and_conn):
    client, _conn = client_and_conn
    _login(client)
    bad = client.post("/api/me/password", json={"current_password": "wrong", "new_password": "newpass123"})
    assert bad.status_code == 400 and "지금 비밀번호" in bad.json()["detail"]
    short = client.post("/api/me/password", json={"current_password": "memberpass", "new_password": "short"})
    assert short.status_code == 400 and "8자" in short.json()["detail"]
    same = client.post("/api/me/password", json={"current_password": "memberpass", "new_password": "memberpass"})
    assert same.status_code == 400


def test_changing_password_logs_out_other_sessions_but_keeps_this_one(client_and_conn):
    client, conn = client_and_conn
    other_tab = _login(client)  # 다른 탭(또는 다른 PC)의 로그인
    this_tab = _login(client)
    assert client.get("/api/me", headers=_bearer(other_tab)).status_code == 200
    r = client.post("/api/me/password", headers=_bearer(this_tab),
                    json={"current_password": "memberpass", "new_password": "brand-new-pass"})
    assert r.status_code == 200
    new_token = r.json()["token"]
    stale = client.get("/api/me", headers=_bearer(other_tab))
    assert stale.status_code == 401 and "비밀번호가 바뀌어" in stale.json()["detail"]
    assert client.get("/api/me", headers=_bearer(this_tab)).status_code == 401  # 바꾸기 전 토큰도 끊김
    assert client.get("/api/me", headers=_bearer(new_token)).status_code == 200  # 응답으로 받은 새 토큰은 이어짐
    assert client.get("/api/me").status_code == 200  # 같은 브라우저의 쿠키 세션도 새 표지로 이어짐
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 401
    assert client.post("/login", json={"username": "member1", "password": "brand-new-pass"}).status_code == 200
    row = conn.execute("SELECT action FROM audit_log WHERE action = 'user_password_changed'").fetchone()
    assert row is not None


def test_admin_reset_also_ends_existing_logins(client_and_conn):
    client, conn = client_and_conn
    member_token = _login(client)
    _login(client, "admin1", "adminpass")
    member_id = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    assert client.patch(f"/api/users/{member_id}/password", json={"password": "reset-by-admin"}).status_code == 200
    assert client.get("/api/me", headers=_bearer(member_token)).status_code == 401


def test_cookie_session_of_other_browser_is_ended(client_and_conn):
    client, _conn = client_and_conn
    other_browser = TestClient(client.app)
    assert other_browser.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    _login(client)
    assert client.post("/api/me/password", json={"current_password": "memberpass",
                                                  "new_password": "brand-new-pass"}).status_code == 200
    assert other_browser.get("/api/me").status_code == 401


def test_tokens_without_password_version_still_work_until_expiry(client_and_conn):
    """이 기능 전에 발급된 토큰(표지 없음)은 만료까지 그대로 — 업그레이드하자마자 모두 로그아웃되지 않게."""
    client, conn = client_and_conn
    member_id = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    legacy = auth.create_session_token(client.app.state.secret_key, member_id)
    assert client.get("/api/me", headers=_bearer(legacy)).status_code == 200
