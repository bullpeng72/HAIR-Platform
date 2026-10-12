"""Phase 35 — 사용자 요청(2026-10-12, "하나의 브라우저에서 두 개의 계정을
동시에 로그인할 수 있도록 개선해줘").

기존 SessionMiddleware 쿠키는 브라우저(또는 테스트에서는 `TestClient`) 전체가
공유하므로, 같은 쿠키 저장소를 쓰는 탭 A에서 로그인한 뒤 탭 B에서 다른 계정으로
로그인하면 탭 A도 그 계정으로 바뀌어버렸다. `/login`이 돌려주는 토큰을
`Authorization: Bearer` 헤더로 보내면 공유 쿠키보다 우선시돼 탭마다(여기서는
헤더마다) 다른 계정을 유지할 수 있다(auth.py의 `get_current_user` 참고).
"""

from __future__ import annotations


def test_bearer_token_overrides_shared_cookie_for_different_accounts(client_and_conn):
    client, _conn = client_and_conn

    # "탭 A": admin1 로그인 — 공유 쿠키도 admin1로 설정된다.
    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200
    token_admin = resp.json()["token"]
    assert token_admin

    # "탭 B": 같은 브라우저(=같은 TestClient, 같은 쿠키 저장소)에서 member1로
    # 로그인 — 공유 쿠키가 이제 member1로 덮어써진다(기존 동작과 동일).
    resp = client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert resp.status_code == 200
    token_member = resp.json()["token"]
    assert token_member
    assert token_member != token_admin

    # 헤더 없이 보내면(옛 클라이언트/테스트와 동일) 공유 쿠키를 그대로 따라가
    # 가장 최근 로그인인 member1로 식별된다.
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "member1"

    # 탭 A는 자신의 토큰을 헤더로 보내므로, 공유 쿠키가 member1이어도
    # 여전히 admin1로 식별돼야 한다 — 이것이 이번 개선의 핵심.
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token_admin}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin1"

    # 탭 B도 자신의 토큰으로 독립적으로 member1을 유지한다.
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token_member}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "member1"

    # 탭 B가 로그아웃(쿠키 세션만 지움)해도 탭 A의 토큰은 영향을 받지 않는다.
    resp = client.post("/logout")
    assert resp.status_code == 200
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token_admin}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin1"


def test_invalid_bearer_token_is_rejected(client_and_conn):
    client, _conn = client_and_conn
    resp = client.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_deactivated_user_bearer_token_is_rejected(client_and_conn):
    """`get_current_user`가 쿠키 경로처럼 매 요청마다 DB에서 `is_active`를
    다시 확인하는지 — 토큰 자체는 유효해도 계정이 비활성화되면 막혀야 한다."""
    client, conn = client_and_conn
    resp = client.post("/login", json={"username": "member1", "password": "memberpass"})
    token = resp.json()["token"]

    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'member1'")
    conn.commit()

    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
