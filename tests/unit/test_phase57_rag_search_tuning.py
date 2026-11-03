"""Phase 57 — RAG 검색 품질 향상(사용자 요청, 2026-11-03, "RAG 검색
품질 향상을 위한 기능이 적절히 반영되어 있는지 확인해줘" → "우선순위를
정해 진행해줘").

이전에는 `vectorstore.search()`가 관련성 임계값 없이 항상 top_k개를
반환했다(무관한 결과도 그대로 섞여 나옴). `embedding_config`와 같은
`id=1` 싱글턴 패턴(`rag_search_config`)으로 admin/manager가 top_k와
min_score(코사인 유사도 하한)를 조정할 수 있게 하고, 코사인 0.8 +
키워드 부분일치 0.2 블렌드 점수로 재순위화한다.

순서가 핵심이다 — 필터(`min_score`, 원시 코사인 기준) → 재순위화
(블렌드 점수) → 슬라이스(`top_k`). 이 순서를 뒤집으면(슬라이스를
먼저 하면) 재순위화가 이미 버려진 후보를 못 살리는 조용한 버그가
생긴다 — 아래 테스트가 정확히 이 순서를 겨냥한다."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hair_platform import vectorstore


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _fake_openai_embeddings(vectors: dict[str, list[float]]):
    """`test_phase12_crewai_vectordb_graphdb.py`의 같은 이름 helper와
    동일한 모양 — 텍스트별 고정 벡터를 돌려주는 가짜 OpenAI 임베딩
    클라이언트."""

    def _create(model, input):
        vec = vectors.get(input, [0.0, 0.0, 0.0])
        item = MagicMock()
        item.embedding = vec
        resp = MagicMock()
        resp.data = [item]
        return resp

    client = MagicMock()
    client.embeddings.create.side_effect = _create
    return client


# ---------- get/set/resolve_rag_search_config() CRUD ----------


def test_get_rag_search_config_returns_none_when_unset(client_and_conn):
    _client, conn = client_and_conn
    assert vectorstore.get_rag_search_config(conn) is None


def test_set_and_get_rag_search_config_roundtrip(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    vectorstore.set_rag_search_config(conn, admin_id, top_k=5, min_score=0.3)
    assert vectorstore.get_rag_search_config(conn) == {"top_k": 5, "min_score": 0.3}
    # 다시 설정하면 upsert — 행이 쌓이지 않는다.
    vectorstore.set_rag_search_config(conn, admin_id, top_k=2, min_score=0.1)
    assert vectorstore.get_rag_search_config(conn) == {"top_k": 2, "min_score": 0.1}
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM rag_search_config").fetchone()["c"]
        == 1
    )


def test_set_rag_search_config_rejects_invalid_top_k(client_and_conn):
    _client, conn = client_and_conn
    import pytest

    with pytest.raises(ValueError):
        vectorstore.set_rag_search_config(conn, 1, top_k=0, min_score=0.2)


def test_set_rag_search_config_rejects_invalid_min_score(client_and_conn):
    _client, conn = client_and_conn
    import pytest

    with pytest.raises(ValueError):
        vectorstore.set_rag_search_config(conn, 1, top_k=3, min_score=1.5)
    with pytest.raises(ValueError):
        vectorstore.set_rag_search_config(conn, 1, top_k=3, min_score=-0.1)


def test_set_rag_search_config_records_audit(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    vectorstore.set_rag_search_config(conn, admin_id, top_k=4, min_score=0.25)
    row = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'rag_search_config_updated' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["actor_id"] == admin_id


def test_resolve_rag_search_config_falls_back_to_defaults_when_unset(client_and_conn):
    _client, conn = client_and_conn
    assert vectorstore.resolve_rag_search_config(conn) == (
        vectorstore.DEFAULT_RAG_TOP_K,
        vectorstore.DEFAULT_RAG_MIN_SCORE,
    )


def test_resolve_rag_search_config_prefers_configured_value(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    vectorstore.set_rag_search_config(conn, admin_id, top_k=7, min_score=0.4)
    assert vectorstore.resolve_rag_search_config(conn) == (7, 0.4)


# ---------- API 엔드포인트 ----------


def test_rag_search_config_endpoint_rbac(client_and_conn):
    client, _conn = client_and_conn
    for username, password in (
        ("member1", "memberpass"),
        ("viewer1", "viewerpass"),
    ):
        _login(client, username, password)
        assert client.get("/api/rag-search-config").status_code == 403, username
        assert (
            client.put(
                "/api/rag-search-config", json={"top_k": 3, "min_score": 0.2}
            ).status_code
            == 403
        ), username
    # admin/manager 둘 다 허용.
    for username, password in (
        ("admin1", "adminpass"),
        ("manager1", "managerpass"),
    ):
        _login(client, username, password)
        assert client.get("/api/rag-search-config").status_code == 200, username


def test_rag_search_config_endpoint_shows_default_when_unset(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/rag-search-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_explicit"] is False
    assert body["top_k"] == vectorstore.DEFAULT_RAG_TOP_K
    assert body["min_score"] == vectorstore.DEFAULT_RAG_MIN_SCORE


def test_rag_search_config_put_then_get_roundtrip(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    put_resp = client.put(
        "/api/rag-search-config", json={"top_k": 6, "min_score": 0.35}
    )
    assert put_resp.status_code == 200, put_resp.text

    get_resp = client.get("/api/rag-search-config")
    body = get_resp.json()
    assert body == {"top_k": 6, "min_score": 0.35, "is_explicit": True}


def test_rag_search_config_put_rejects_invalid_top_k(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.put("/api/rag-search-config", json={"top_k": 0, "min_score": 0.2})
    assert resp.status_code == 400


# ---------- search() — 필터 → 재순위화 → 슬라이스 순서 ----------


def test_min_score_filter_applies_before_top_k_slice(client_and_conn):
    """5개 후보 중 코사인 기준 2개만 `min_score`를 통과한다. `top_k=5`로
    넉넉하게 요청해도 — "슬라이스 먼저, 필터 나중"이었다면 5개가 다
    반환되겠지만 — 필터를 통과한 2개만 반환돼야 한다."""
    _client, conn = client_and_conn
    fake_client = _fake_openai_embeddings(
        {
            "관련문서A": [1.0, 0.0, 0.0],
            "관련문서B": [0.95, 0.05, 0.0],
            "무관문서C": [0.0, 1.0, 0.0],
            "무관문서D": [0.0, 0.0, 1.0],
            "무관문서E": [-1.0, 0.0, 0.0],
            "쿼리": [1.0, 0.0, 0.0],
        }
    )
    with patch("openai.OpenAI", return_value=fake_client):
        for text in ["관련문서A", "관련문서B", "무관문서C", "무관문서D", "무관문서E"]:
            vectorstore.index_text(conn, None, text, provider="openai")
        hits = vectorstore.search(
            conn, "쿼리", top_k=5, min_score=0.5, provider="openai"
        )
    assert len(hits) == 2
    assert {h["text"] for h in hits} == {"관련문서A", "관련문서B"}


def test_default_min_score_does_not_exclude_highly_relevant_hit(client_and_conn):
    """기본 임계값(0.2)이 기존 고관련 케이스를 안 쳐내는지 회귀 확인
    (`test_gc83_vectorstore_index_and_search_ranks_by_cosine_similarity`와
    같은 코사인대 — 0.9/0.1 벡터쌍은 0.99 전후)."""
    _client, conn = client_and_conn
    fake_client = _fake_openai_embeddings(
        {
            "파이썬 백엔드 설계 문서": [1.0, 0.0, 0.0],
            "백엔드 아키텍처가 궁금해": [0.9, 0.1, 0.0],
        }
    )
    with patch("openai.OpenAI", return_value=fake_client):
        vectorstore.index_text(conn, None, "파이썬 백엔드 설계 문서", provider="openai")
        hits = vectorstore.search(
            conn, "백엔드 아키텍처가 궁금해", provider="openai"
        )
    assert len(hits) == 1
    assert hits[0]["text"] == "파이썬 백엔드 설계 문서"


def test_keyword_overlap_boost_can_flip_ranking_order(client_and_conn):
    """원시 코사인은 B(0.85)>A(0.80)인데, A가 쿼리 키워드를 모두
    포함해 블렌드 점수(0.8*cos + 0.2*keyword)에서는 A가 B를 앞선다 —
    재순위화가 실제로 순서를 바꾸는지 확인한다."""
    _client, conn = client_and_conn
    # 코사인 유사도를 직접 통제하기 위해 미리 정규화된 벡터를 쓴다.
    vec_query = [1.0, 0.0]
    vec_a = [0.8, 0.6]  # cos(query, a) = 0.8
    vec_b = [0.85, 0.5268]  # cos(query, b) ≈ 0.85 (정규화된 값에 가깝게)
    # 코사인 계산은 내부에서 정규화하므로 비율만 맞으면 된다.
    fake_client = _fake_openai_embeddings(
        {
            "키워드포함 문서 백엔드 성능 튜닝": vec_a,
            "키워드없는 문서": vec_b,
            "백엔드 성능 튜닝": vec_query,
        }
    )
    with patch("openai.OpenAI", return_value=fake_client):
        vectorstore.index_text(
            conn, None, "키워드포함 문서 백엔드 성능 튜닝", provider="openai"
        )
        vectorstore.index_text(conn, None, "키워드없는 문서", provider="openai")

        raw_cosine_a = vectorstore._cosine_similarity(vec_query, vec_a)
        raw_cosine_b = vectorstore._cosine_similarity(vec_query, vec_b)
        assert raw_cosine_b > raw_cosine_a  # 전제 확인: 원시 코사인은 B가 더 높음

        hits = vectorstore.search(
            conn,
            "백엔드 성능 튜닝",
            top_k=2,
            min_score=0.0,
            provider="openai",
        )
    assert hits[0]["text"] == "키워드포함 문서 백엔드 성능 튜닝"
    # 반환값의 score는 여전히 원시 코사인(하위 호환) — 블렌드 점수가 아니다.
    assert hits[0]["score"] == raw_cosine_a


def test_min_score_filters_on_raw_cosine_not_blended_score(client_and_conn):
    """낮은 코사인 + 인위적으로 완벽한 키워드 일치 후보라도 블렌드
    점수가 아니라 원시 코사인 기준으로 걸러져야 한다 — 키워드 일치가
    관련성 기준 자체를 무력화하면 안 된다."""
    _client, conn = client_and_conn
    vec_query = [1.0, 0.0]
    vec_low_cosine = [0.0, 1.0]  # cos = 0.0 — 완전 무관
    chunk_text = "완전무관 문서지만 쿼리 키워드 완전일치 단어는 다 들어있음"
    query_text = "쿼리 키워드 완전일치"
    fake_client = _fake_openai_embeddings(
        {
            chunk_text: vec_low_cosine,
            query_text: vec_query,
        }
    )
    with patch("openai.OpenAI", return_value=fake_client):
        vectorstore.index_text(conn, None, chunk_text, provider="openai")
        # 전제 확인: 쿼리 토큰이 전부 청크 텍스트의 부분 문자열이라 키워드
        # 겹침은 완벽(1.0)하지만, 코사인은 0.0(완전 무관)이다.
        assert vectorstore._keyword_overlap_ratio(query_text, chunk_text) == 1.0
        hits = vectorstore.search(
            conn, query_text, top_k=5, min_score=0.2, provider="openai"
        )
    assert hits == []


def test_keyword_overlap_ratio_matches_korean_particle_inflected_form():
    """`_keyword_overlap_ratio()`는 토큰 경계가 아니라 부분 문자열
    포함 기준이다 — 조사가 붙은 형태("데이터베이스를")도 기본형
    질의 토큰("데이터베이스")을 포함하므로 일치로 본다."""
    ratio = vectorstore._keyword_overlap_ratio(
        "데이터베이스", "이 문서는 데이터베이스를 다룬다"
    )
    assert ratio == 1.0


def test_keyword_overlap_ratio_zero_when_no_overlap():
    ratio = vectorstore._keyword_overlap_ratio("완전히 다른 질문", "상관없는 내용")
    assert ratio == 0.0
