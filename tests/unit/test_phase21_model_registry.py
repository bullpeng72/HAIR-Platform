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
    """사용자 요청(2026-11-02, "모델 관리 메뉴 영역은 관리자(admin)
    전용으로 변경") — 조회/등록/수정/삭제 전부 admin 전용으로
    좁혀졌다(기존엔 삭제만 admin 전용, 나머지는 admin+manager였다) —
    manager/member/viewer는 전부 403. 단 `/api/model-registry/names`
    (provider+name만 노출하는 조회 전용 엔드포인트)는 그대로 전
    role에 열려 있다(아래 별도 테스트)."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/model-registry", json={"provider": "anthropic", "name": "claude-sonnet-5"}
    )
    assert resp.status_code == 201, resp.text
    model_id = resp.json()["id"]

    for username, password in (
        ("manager1", "managerpass"),
        ("member1", "memberpass"),
        ("viewer1", "viewerpass"),
    ):
        _login(client, username, password)
        assert client.get("/api/model-registry").status_code == 403, username
        assert (
            client.post(
                "/api/model-registry", json={"provider": "openai", "name": "x"}
            ).status_code
            == 403
        ), username
        assert (
            client.patch(
                f"/api/model-registry/{model_id}/active", json={"is_active": False}
            ).status_code
            == 403
        ), username
        assert (
            client.patch(
                f"/api/model-registry/{model_id}/optimization-tiers",
                json={"optimization_tiers": ["standard"]},
            ).status_code
            == 403
        ), username
        assert (
            client.delete(f"/api/model-registry/{model_id}").status_code == 403
        ), username


def test_gc_model_registry_names_endpoint_open_to_all_roles_no_sensitive_fields(
    client_and_conn,
):
    """사용자 보고(2026-10-22, 스크린샷 — "member 권한으로 대화시 모델
    프로바이더를 선택해도 등록된 모델이 불려오지 않음") — `/api/model-
    registry`(위 RBAC 테스트)는 admin 전용으로 유지하되(2026-11-02,
    "모델 관리 메뉴 영역은 관리자 전용으로 변경"으로 manager도
    제외됨), 대화 워크스페이스 등 4개 화면의 provider 드롭다운이 공유하는
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
    client.patch(f"/api/model-registry/{inactive_id}/active", json={"is_active": False})

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


# ---------- 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정
# 최적화 각 tier에 적용되는 모델을 매핑하는 기능을 제공... 일단 설정된
# 후 변경할 수 있는 기능이 제공되지 않아. 모델 선택후 재설정도
# 가능하도록 개선") — optimization_tiers 지정·변경·초기화(재설정)
# CRUD(1차로 premium/balanced/economy 간접 등급을 만들었다가, 사용자가
# "tier 직접 매핑으로 변경"을 선택해 다시 설계했다). ----------


def test_set_optimization_tiers_persists_and_is_returned_by_list_models():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from hair_platform import db

    db.init_schema(conn)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'a', 'x', 'admin')"
    )
    conn.commit()
    model_id = model_registry.create_model(conn, 1, "ollama", "custom-model:latest")

    # 등록 직후엔 아직 매핑 없음([]) — 패턴 추정으로 폴백한다.
    row = next(m for m in model_registry.list_models(conn) if m["id"] == model_id)
    assert row["optimization_tiers"] == []

    model_registry.set_optimization_tiers(conn, 1, model_id, ["deep_reasoning"])
    row = next(m for m in model_registry.list_models(conn) if m["id"] == model_id)
    assert row["optimization_tiers"] == ["deep_reasoning"]

    # 재설정(여러 tier 동시 매핑) — "일단 설정된 후 변경할 수 있는
    # 기능이 제공되지 않아"에 대응: 몇 번이든 다시 바꿀 수 있어야 한다.
    model_registry.set_optimization_tiers(
        conn, 1, model_id, ["precision_fast", "standard"]
    )
    row = next(m for m in model_registry.list_models(conn) if m["id"] == model_id)
    assert row["optimization_tiers"] == ["precision_fast", "standard"]

    # 초기화([]) — 패턴 추정으로 되돌린다.
    model_registry.set_optimization_tiers(conn, 1, model_id, [])
    row = next(m for m in model_registry.list_models(conn) if m["id"] == model_id)
    assert row["optimization_tiers"] == []


def test_set_optimization_tiers_rejects_invalid_value_and_missing_model():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from hair_platform import db

    db.init_schema(conn)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'a', 'x', 'admin')"
    )
    conn.commit()
    model_id = model_registry.create_model(conn, 1, "ollama", "custom-model:latest")

    with pytest.raises(ValueError):
        model_registry.set_optimization_tiers(conn, 1, model_id, ["엄청좋음"])
    with pytest.raises(ValueError):
        model_registry.set_optimization_tiers(conn, 1, 999999, ["standard"])


def test_optimization_tiers_endpoint_rbac_and_round_trip(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    model_id = client.post(
        "/api/model-registry",
        json={"provider": "ollama", "name": "custom-model:latest"},
    ).json()["id"]

    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": ["deep_reasoning", "standard"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": model_id,
        "optimization_tiers": ["deep_reasoning", "standard"],
    }
    row = next(
        r for r in client.get("/api/model-registry").json() if r["id"] == model_id
    )
    assert set(row["optimization_tiers"]) == {"deep_reasoning", "standard"}

    # 재설정 — 같은 엔드포인트로 다른 값, 그리고 []로 초기화.
    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": ["precision_fast"]},
    )
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": []},
    )
    assert resp.status_code == 200
    row = next(
        r for r in client.get("/api/model-registry").json() if r["id"] == model_id
    )
    assert row["optimization_tiers"] == []

    # 잘못된 값은 422(pydantic Literal 검증).
    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": ["엄청좋음"]},
    )
    assert resp.status_code == 422

    resp = client.patch(
        "/api/model-registry/999999/optimization-tiers",
        json={"optimization_tiers": ["standard"]},
    )
    assert resp.status_code == 404

    # 사용자 요청(2026-11-02, "모델 관리 메뉴 영역은 관리자(admin)
    # 전용으로 변경") — manager도 이제 403.
    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": ["standard"]},
    )
    assert resp.status_code == 403, resp.text

    _login(client, "member1", "memberpass")
    resp = client.patch(
        f"/api/model-registry/{model_id}/optimization-tiers",
        json={"optimization_tiers": ["standard"]},
    )
    assert resp.status_code == 403


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


# ---------- 성능 테스트(사용자 요청, 2026-11-12, "ollama, openai 등
# 프로바이더의 모델 성능 Test하고 결과 표시, 티어 추천 기능") ----------


def test_gc226_benchmark_success_records_speed_and_tier_recommendation(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    model_id = client.post(
        "/api/model-registry", json={"provider": "ollama", "name": "test-model"}
    ).json()["id"]

    with patch(
        "hair_platform.llm.call_llm", return_value="가" * 300
    ):
        # 전역 `time` 모듈이 아니라 `model_benchmark` 모듈 안에서 바라보는
        # 이름만 바꿔치기한다 — 테스트 클라이언트/FastAPI 내부가 쓰는
        # 실제 time.monotonic()까지 건드리면 요청 처리 자체가 무한 대기에
        # 빠질 위험이 있다(실제로 한 번 재현됨).
        with patch("hair_platform.model_benchmark.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 3.0]
            resp = client.post(f"/api/model-registry/{model_id}/benchmark")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["latency_seconds"] == 3.0
    assert body["output_chars"] == 300
    assert body["chars_per_second"] == 100.0
    assert body["recommended_tier"] == "standard"  # 100자/초 >= 50(FAST 경계)

    # GET 목록에 최신 결과가 그대로 끼워져 나온다.
    rows = client.get("/api/model-registry").json()
    row = next(r for r in rows if r["id"] == model_id)
    assert row["latest_benchmark"]["recommended_tier"] == "standard"

    # 이력도 쌓인다.
    history = client.get(f"/api/model-registry/{model_id}/benchmarks").json()
    assert len(history) == 1
    assert history[0]["status"] == "success"


def test_gc227_benchmark_failure_recorded_with_error(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    model_id = client.post(
        "/api/model-registry", json={"provider": "ollama", "name": "missing-model"}
    ).json()["id"]

    with patch(
        "hair_platform.llm.call_llm",
        side_effect=RuntimeError("model not found locally"),
    ):
        resp = client.post(f"/api/model-registry/{model_id}/benchmark")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert "model not found locally" in body["error"]

    rows = client.get("/api/model-registry").json()
    row = next(r for r in rows if r["id"] == model_id)
    assert row["latest_benchmark"]["status"] == "failed"


def test_gc228_benchmark_tier_recommendation_thresholds():
    from hair_platform import model_benchmark

    assert model_benchmark.recommend_tier(100.0) == "standard"
    assert model_benchmark.recommend_tier(30.0) == "precision_fast"
    assert model_benchmark.recommend_tier(5.0) == "deep_reasoning"
    # 경계값 자체(동률)도 명확히 포함 관계를 확인
    assert (
        model_benchmark.recommend_tier(model_benchmark.FAST_CHARS_PER_SECOND)
        == "standard"
    )
    assert (
        model_benchmark.recommend_tier(model_benchmark.MODERATE_CHARS_PER_SECOND)
        == "precision_fast"
    )


def test_gc229_benchmark_rbac_admin_only(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    model_id = client.post(
        "/api/model-registry", json={"provider": "ollama", "name": "rbac-model"}
    ).json()["id"]

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/model-registry/{model_id}/benchmark")
    assert resp.status_code == 403

    _login(client, "member1", "memberpass")
    resp = client.get(f"/api/model-registry/{model_id}/benchmarks")
    assert resp.status_code == 403


def test_gc230_benchmark_unknown_model_returns_404(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/model-registry/999999/benchmark")
    assert resp.status_code == 404


def test_gc231_delete_model_after_benchmark_does_not_500(client_and_conn):
    """사용자 보고(2026-11-12, "모델 레지스트리에서 모델 삭제시 삭제되지
    않고 500 Internal Server Error 발생") — `model_benchmarks.
    model_registry_id`가 FK로 참조하는데(PRAGMA foreign_keys=ON),
    성능 테스트를 한 번이라도 돌린 모델을 삭제하면 FK 위반으로 DB가
    그 자리에서 예외를 던졌다. 삭제 시 이력도 같이 지워져야 한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    model_id = client.post(
        "/api/model-registry", json={"provider": "ollama", "name": "to-be-deleted"}
    ).json()["id"]

    with patch("hair_platform.llm.call_llm", return_value="결과"):
        resp = client.post(f"/api/model-registry/{model_id}/benchmark")
    assert resp.status_code == 201, resp.text

    before = conn.execute(
        "SELECT COUNT(*) AS c FROM model_benchmarks WHERE model_registry_id = ?",
        (model_id,),
    ).fetchone()["c"]
    assert before == 1

    resp = client.delete(f"/api/model-registry/{model_id}")
    assert resp.status_code == 200, resp.text

    after = conn.execute(
        "SELECT COUNT(*) AS c FROM model_benchmarks WHERE model_registry_id = ?",
        (model_id,),
    ).fetchone()["c"]
    assert after == 0
    remaining_ids = [r["id"] for r in client.get("/api/model-registry").json()]
    assert model_id not in remaining_ids
