"""지식 베이스 벡터 DB 백엔드(2026-11-25, `vector_backends.py`) — ChromaDB 임베디드 + SQLite 원본 동기화."""

from __future__ import annotations

import hashlib
import math

import pytest

pytest.importorskip("chromadb")

from hair_platform import data_admin, db, vector_backends, vectorstore  # noqa: E402

MODEL = "mxbai-embed-large:latest"  # ':'가 든 이름 — Chroma 컬렉션 이름 규칙을 그대로 쓰면 실패한다


def _fake_embed(text, provider=None, model=None):
    """낱말 해시 기반의 결정적 임베딩(외부 호출 없음) — 같은 낱말이 많을수록 가깝다."""
    vec = [0.0] * 64
    for word in text.split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return "ollama", MODEL, [v / norm for v in vec]


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectorstore, "embed_text", _fake_embed)
    monkeypatch.delenv("HAIR_VECTOR_DIR", raising=False)
    monkeypatch.delenv("HAIR_VECTOR_BACKEND", raising=False)
    conn = db.get_connection(str(tmp_path / "kb.db"))
    db.init_schema(conn)
    return conn


TEXTS = [
    "3분기 매출 보고서 영업이익 증가",
    "신입 사원 온보딩 절차 안내",
    "서버 장애 대응 매뉴얼 재기동 순서",
    "3분기 영업 실적 지역별 매출",
    "휴가 신청 절차와 승인 규칙",
]


def _chroma(conn) -> vector_backends.ChromaBackend:
    backend = vector_backends.get_backend(conn)
    assert isinstance(backend, vector_backends.ChromaBackend)
    return backend


def _search(conn, query, **kw):
    return vectorstore.search(conn, query, top_k=kw.get("top_k", 3), min_score=kw.get("min_score", 0.0),
                              provider="ollama", model=MODEL)


def test_file_db_uses_chroma_and_matches_exact_scan(file_db, monkeypatch):
    conn = file_db
    assert vector_backends.get_backend(conn).name == "chroma"
    for t in TEXTS:
        vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL)
    via_chroma = _search(conn, "3분기 매출")
    monkeypatch.setenv("HAIR_VECTOR_BACKEND", "sqlite")
    via_scan = _search(conn, "3분기 매출")
    assert [h["id"] for h in via_chroma] == [h["id"] for h in via_scan]
    for a, b in zip(via_chroma, via_scan, strict=True):
        assert a["score"] == pytest.approx(b["score"], abs=1e-5)
        assert a["text"] == b["text"]
    assert vectorstore.stats(conn)["vector_backend"] == "sqlite"
    monkeypatch.delenv("HAIR_VECTOR_BACKEND")
    assert vectorstore.stats(conn)["vector_backend"] == "chroma"


def test_memory_db_keeps_exact_scan():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    assert vector_backends.get_backend(conn).name == "sqlite"


def test_collection_name_is_valid_for_model_names_with_colon():
    name = vector_backends.ChromaBackend.collection_name("ollama", MODEL)
    assert name.startswith("kb_") and set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_")


def test_rows_deleted_elsewhere_drop_out_of_search_and_index(file_db):
    """문서 삭제·Agent 삭제처럼 SQLite만 지우는 경로 — 검색에서 빠지고 벡터도 정리된다."""
    conn = file_db
    ids = [vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL) for t in TEXTS]
    conn.execute("DELETE FROM knowledge_chunks WHERE id = ?", (ids[0],))
    conn.commit()
    hits = _search(conn, "3분기 매출 보고서 영업이익 증가")
    assert ids[0] not in [h["id"] for h in hits]
    col = _chroma(conn)._collection("ollama", MODEL)
    assert col.count() == len(TEXTS) - 1


def test_missing_vectors_are_refilled_without_embedding_calls(file_db, monkeypatch):
    """색인 폴더를 잃거나 add가 실패해도 SQLite의 임베딩으로 채운다(임베딩 API 재호출 없음)."""
    conn = file_db
    for t in TEXTS:
        vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL)
    backend = _chroma(conn)
    backend.reset(conn)
    calls = []
    monkeypatch.setattr(vectorstore, "embed_text", lambda *a, **k: (calls.append(a), _fake_embed(*a, **k))[1])
    hits = _search(conn, "서버 장애 대응")
    assert hits and hits[0]["text"] == TEXTS[2]
    assert len(calls) == 1  # 질의 임베딩 한 번뿐 — 청크는 다시 임베딩하지 않는다
    assert backend._collection("ollama", MODEL).count() == len(TEXTS)


def test_chroma_failure_falls_back_to_exact_scan(file_db, monkeypatch):
    conn = file_db
    for t in TEXTS:
        vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL)

    def boom(*_a, **_k):
        raise RuntimeError("vector db down")

    monkeypatch.setattr(vector_backends.ChromaBackend, "query", boom)
    hits = _search(conn, "휴가 신청")
    assert hits and hits[0]["text"] == TEXTS[4]


def test_reset_clears_vector_collections(file_db):
    conn = file_db
    for t in TEXTS:
        vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL)
    backend = _chroma(conn)
    assert backend._collection("ollama", MODEL).count() == len(TEXTS)
    data_admin.reset_to_factory_state(conn)
    names = [c if isinstance(c, str) else c.name for c in backend._client().list_collections()]
    assert not [n for n in names if n.startswith("kb_")]
    assert _search(conn, "3분기 매출") == []


def test_cli_reindex_vectors(tmp_path, monkeypatch, capsys):
    from hair_platform import cli

    monkeypatch.setattr(vectorstore, "embed_text", _fake_embed)
    monkeypatch.delenv("HAIR_VECTOR_DIR", raising=False)
    monkeypatch.delenv("HAIR_VECTOR_BACKEND", raising=False)
    path = str(tmp_path / "cli.db")
    conn = db.get_connection(path)
    db.init_schema(conn)
    for t in TEXTS:
        vectorstore.index_text(conn, None, t, provider="ollama", model=MODEL)
    vector_backends.get_backend(conn).reset(conn)
    assert cli.main(["reindex-vectors", "--db-path", path]) == 0
    out = capsys.readouterr().out
    assert f"추가 {len(TEXTS)}" in out and f"{path}-vectors" in out
    assert cli.main(["reindex-vectors", "--db-path", path, "--rebuild"]) == 0
    assert f"추가 {len(TEXTS)}" in capsys.readouterr().out


def test_telemetry_is_disabled(file_db):
    client = _chroma(file_db)._client()
    assert client.get_settings().anonymized_telemetry is False


def test_token_budget_works_without_tiktoken_encoding(monkeypatch):
    """2026-11-25 — 인코딩 표를 못 받는 폐쇄망에서도 서버가 뜨고(지연 로드), 글자 수로 보수적으로 센다."""
    import sys

    from hair_platform import token_budget

    token_budget._encoding.cache_clear()
    monkeypatch.setitem(sys.modules, "tiktoken", None)  # import tiktoken → ImportError
    try:
        text = "가" * 100
        assert token_budget.count_tokens(text, "openai") == 100  # 글자 1개 = 1토큰(실제보다 크게)
        cut = token_budget.fit_text_to_budget(text * 10, "openai", 200)
        assert len(cut) < 1000 and cut.endswith(token_budget._OMISSION_MARKER)
    finally:
        token_budget._encoding.cache_clear()
