"""Phase 21 — 모델 레지스트리(사용자 요청, 2026-10-06, "운영 관리에 사용
가능한 ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고 등록된
모델을 불러오는 방식으로 UI/UX를 개선").

"AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"의 "모델명"
입력란이 전부 자유 텍스트였다 — 실제로 쓸 수 있는 모델이 뭔지 모른 채
타이핑해야 했고, ollama 기본값("llama3.1")이 로컬에 없어 404가 난 사고가
실제 선례였다. skill_pool/tool_pool과 같은 "등록→
선택" 패턴을 모델명에도 적용한다 — `model_registry` 테이블+CRUD API+
provider별 실시간 조회("불러오기")를 신설한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import model_registry


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc221_create_model_rejects_unsupported_provider_and_empty_name():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from hair_platform import db

    db.init_schema(conn)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'a', 'x', 'admin')"
    )
    conn.commit()

    with pytest.raises(ValueError):
        model_registry.create_model(conn, 1, "unknown_provider", "some-model")
    with pytest.raises(ValueError):
        model_registry.create_model(conn, 1, "openai", "   ")


def test_gc222_model_registry_crud_via_api(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")

    resp = client.post(
        "/api/model-registry", json={"provider": "openai", "name": "gpt-4o-mini"}
    )
    assert resp.status_code == 201, resp.text
    model_id = resp.json()["id"]

    resp = client.get("/api/model-registry")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["id"] == model_id and r["is_active"] for r in rows)

    resp = client.get("/api/model-registry", params={"provider": "ollama"})
    assert resp.status_code == 200
    assert resp.json() == []  # openai로 등록했으니 ollama 필터엔 안 걸린다

    # 중복 등록은 409.
    resp = client.post(
        "/api/model-registry", json={"provider": "openai", "name": "gpt-4o-mini"}
    )
    assert resp.status_code == 409

    resp = client.patch(
        f"/api/model-registry/{model_id}/active", json={"is_active": False}
    )
    assert resp.status_code == 200
    row = next(
        r for r in client.get("/api/model-registry").json() if r["id"] == model_id
    )
    assert row["is_active"] is False

    resp = client.delete(f"/api/model-registry/{model_id}")
    assert resp.status_code == 200
    assert client.get("/api/model-registry").json() == []


def test_gc223_model_registry_rbac(client_and_conn):
    """등록/수정/조회는 admin+manager, 삭제는 admin 전용(tool_pool/
    skill_pool과 같은 권한 매트릭스) — member/viewer는 전부 403."""
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/model-registry", json={"provider": "anthropic", "name": "claude-sonnet-5"}
    )
    assert resp.status_code == 201, resp.text
    model_id = resp.json()["id"]

    resp = client.delete(f"/api/model-registry/{model_id}")
    assert resp.status_code == 403  # manager는 삭제 불가

    _login(client, "member1", "memberpass")
    assert client.get("/api/model-registry").status_code == 403
    assert (
        client.post(
            "/api/model-registry", json={"provider": "openai", "name": "x"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/model-registry/{model_id}/active", json={"is_active": False}
        ).status_code
        == 403
    )
    assert client.delete(f"/api/model-registry/{model_id}").status_code == 403

    _login(client, "viewer1", "viewerpass")
    assert client.get("/api/model-registry").status_code == 403


def test_gc_model_registry_names_endpoint_open_to_all_roles_no_sensitive_fields(
    client_and_conn,
):
    """사용자 보고(2026-10-22, 스크린샷 — "member 권한으로 대화시 모델
    프로바이더를 선택해도 등록된 모델이 불려오지 않음") — `/api/model-
    registry`(위 RBAC 테스트)는 admin+manager 전용으로 유지하되,
    대화 워크스페이스 등 4개 화면의 provider 드롭다운이 공유하는
    `fetchModelsByProvider()`는 member/viewer에서도 모델 목록을 받아야
    한다. `/api/model-registry/names`는 모든 로그인 role에 열려 있고,
    `id`/`is_active` 같은 관리용 필드 없이 `provider`/`name`만 반환해야
    한다(활성 모델만)."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/model-registry", json={"provider": "anthropic", "name": "claude-sonnet-5"}
    )
    assert resp.status_code == 201, resp.text
    inactive_id = client.post(
        "/api/model-registry", json={"provider": "openai", "name": "비활성모델"}
    ).json()["id"]
    client.patch(
        f"/api/model-registry/{inactive_id}/active", json={"is_active": False}
    )

    for username, password in [
        ("member1", "memberpass"),
        ("viewer1", "viewerpass"),
        ("manager1", "managerpass"),
    ]:
        _login(client, username, password)
        resp = client.get("/api/model-registry/names")
        assert resp.status_code == 200, (username, resp.text)
        names = resp.json()
        assert {"provider": "anthropic", "name": "claude-sonnet-5"} in names
        assert all(set(n.keys()) == {"provider", "name"} for n in names)
        assert not any(n["name"] == "비활성모델" for n in names)  # 비활성 제외


def test_gc224_fetch_candidates_success_does_not_persist_anything(client_and_conn):
    """ "불러오기"는 조회만 한다 — 결과를 보여줄 뿐 등록하지 않는다(등록은
    사용자가 고른 것만 별도 POST로)."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")

    with patch(
        "hair_platform.model_registry.fetch_available_models",
        return_value=["qwen3-coder:latest", "exaone3.5:7.8b"],
    ) as m:
        resp = client.get("/api/model-registry/fetch", params={"provider": "ollama"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "provider": "ollama",
        "models": ["qwen3-coder:latest", "exaone3.5:7.8b"],
    }
    m.assert_called_once_with("ollama")
    assert client.get("/api/model-registry").json() == []  # 저장 안 됐다


def test_gc225_fetch_candidates_wraps_provider_errors_as_400(client_and_conn):
    """ollama 서버 접속 실패·API 키 미설정 등은 흔한 실패 모드라 500이
    아니라 400으로, 사람이 읽을 수 있는 메시지로 돌려준다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")

    with patch(
        "hair_platform.model_registry.fetch_available_models",
        side_effect=RuntimeError("connection refused"),
    ):
        resp = client.get("/api/model-registry/fetch", params={"provider": "ollama"})
    assert resp.status_code == 400
    assert "connection refused" in resp.json()["detail"]

    resp = client.get(
        "/api/model-registry/fetch", params={"provider": "not_a_provider"}
    )
    assert resp.status_code == 400
