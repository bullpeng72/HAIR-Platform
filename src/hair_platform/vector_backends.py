"""지식 베이스 벡터 검색 백엔드(2026-11-25, 사용자 요청 "지식베이스 RAG 기능을 sqlite가 아니라 pip 패키지로 배포
가능한 vectorDB로 교체").

**역할 분담** — SQLite `knowledge_chunks`가 원본(텍스트·출처·감사·초기화·FK)이고, 백엔드는 "이 임베딩과 가까운
청크 id 후보"만 돌려준다. 텍스트·출처는 늘 SQLite에서 다시 읽으므로 백엔드에는 벡터와 provider/model만 둔다.

- `ChromaBackend` — ChromaDB(`pip install chromadb`, crewai 의존성으로 이미 설치돼 있는 경우가 많다)를 서버 없이
  프로세스 안에서(`PersistentClient`) 쓴다. 근사 최근접 색인(HNSW)이라 청크 1만 개에서 검색 1회 약 40ms
  (실측 — SQLite 전체 비교는 약 1.7초, 상위 5개 결과는 10회 모두 동일). DB 파일 옆 `<db>-vectors/` 폴더(`HAIR_VECTOR_DIR`로 변경)에 저장한다.
- `SqliteScanBackend` — 예전 방식 그대로(SQLite의 JSON 임베딩 전체와 코사인 계산, 정확한 결과). 메모리 DB
  (테스트)·chromadb 미설치·`HAIR_VECTOR_BACKEND=sqlite`일 때 쓴다.

**일관성(두 저장소는 한 트랜잭션으로 묶이지 않는다)** — 삭제 경로 4곳(문서·지식 제출·Agent 삭제·청크 삭제)
중 일부는 실패 시 전부 되돌리는 원자적 삭제 안에서 돈다. 거기서 벡터를 먼저 지우면 SQLite가 되돌려질 때 벡터만
사라진다. 그래서 삭제 경로는 건드리지 않고 `sync()`가 맞춘다: 백엔드에만 있는 id는 지우고(행이 지워진 청크),
SQLite에만 있는 id는 SQLite의 JSON 임베딩으로 다시 넣는다(임베딩 API 재호출 없음 — 비용 0). `search()`가 검색
전에 개수를 비교해 다를 때만 돌리므로 평소에는 COUNT 두 번이다. 기존 데이터의 첫 이전도 같은 경로로 된다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
from typing import Any

_CLIENTS: dict[str, Any] = {}
# HNSW 색인 설정(2026-11-25 실측, 1,024차원·청크 1만 개) — Chroma 기본값(search_ef=10)은 정확한 결과 대비 상위 5개
# 재현율이 0.53~0.69에 그쳤다. 아래 값은 무작위·주제별로 뭉친 데이터 모두 재현율 1.000, 검색 1회 약 10ms(색인은
# 1만 개에 약 9초, 한 번만). 컬렉션을 만들 때 정해지므로 바꾸려면 `hair reindex-vectors --rebuild`.
_HNSW_PARAMS = {"hnsw:M": 48, "hnsw:construction_ef": 400, "hnsw:search_ef": 800}
_LOCK = threading.Lock()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _db_path(conn: sqlite3.Connection) -> str:
    """연결된 메인 DB 파일 경로(메모리 DB면 빈 문자열)."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            return row[2] or ""
    return ""


class SqliteScanBackend:
    """예전 방식 — 같은 provider/model 청크 전부와 정확한 코사인 비교."""

    name = "sqlite"

    def query(self, conn, provider: str, model: str, embedding: list[float], n: int | None) -> list[tuple[int, float]]:
        rows = conn.execute(
            "SELECT id, embedding FROM knowledge_chunks WHERE embedding_provider = ? AND embedding_model = ?",
            (provider, model),
        ).fetchall()
        scored = [(row[0], _cosine(embedding, json.loads(row[1]))) for row in rows]
        scored.sort(key=lambda s: s[1], reverse=True)
        return scored if n is None else scored[:n]

    def add(self, conn, chunk_id: int, provider: str, model: str, embedding: list[float]) -> None:
        return None  # 원본 행 자체가 색인

    def sync(self, conn, provider: str | None = None, model: str | None = None) -> dict:
        return {"added": 0, "removed": 0}

    def reset(self, conn) -> None:
        return None


class ChromaBackend:
    """ChromaDB 임베디드 — 임베딩 모델마다 컬렉션 하나(다른 모델 벡터끼리는 비교하지 않는다)."""

    name = "chroma"

    def __init__(self, path: str):
        self.path = path

    def _client(self):
        with _LOCK:
            client = _CLIENTS.get(self.path)
            if client is None:
                import chromadb
                from chromadb.config import Settings

                # 익명 사용 통계 전송을 끈다(로컬/온프레미스 우선 — 외부로 아무것도 보내지 않는다).
                client = chromadb.PersistentClient(
                    path=self.path, settings=Settings(anonymized_telemetry=False, allow_reset=True)
                )
                _CLIENTS[self.path] = client
            return client

    @staticmethod
    def collection_name(provider: str, model: str) -> str:
        # Chroma 이름은 [a-zA-Z0-9._-] 3~512자 — "mxbai-embed-large:latest"처럼 ':'가 든 모델명은 못 쓴다.
        return "kb_" + hashlib.sha1(f"{provider}|{model}".encode()).hexdigest()[:20]

    def _collection(self, provider: str, model: str):
        return self._client().get_or_create_collection(
            self.collection_name(provider, model),
            metadata={"hnsw:space": "cosine", "provider": provider, "model": model, **_HNSW_PARAMS},
        )

    def query(self, conn, provider: str, model: str, embedding: list[float], n: int | None) -> list[tuple[int, float]]:
        col = self._collection(provider, model)
        total = col.count()
        if total == 0:
            return []
        k = total if n is None else min(n, total)
        res = col.query(query_embeddings=[embedding], n_results=k, include=["distances"])
        ids, dists = res["ids"][0], (res.get("distances") or [[]])[0]
        # cosine 공간의 거리 = 1 − 코사인 유사도
        return [(int(i), 1.0 - float(d)) for i, d in zip(ids, dists, strict=True)]

    def add(self, conn, chunk_id: int, provider: str, model: str, embedding: list[float]) -> None:
        self._collection(provider, model).upsert(ids=[str(chunk_id)], embeddings=[embedding])

    def sync(self, conn, provider: str | None = None, model: str | None = None) -> dict:
        """SQLite(원본)와 맞춘다 — 개수가 같으면 바로 끝(평소). provider/model을 안 주면 전부."""
        if provider is None or model is None:
            pairs = conn.execute(
                "SELECT DISTINCT embedding_provider, embedding_model FROM knowledge_chunks"
            ).fetchall()
            known = {(p, m) for p, m in pairs}
            added = removed = 0
            for p, m in known:
                r = self.sync(conn, p, m)
                added += r["added"]
                removed += r["removed"]
            # 원본에 더는 없는 모델의 컬렉션(초기화 뒤 남은 것 등)은 통째로 지운다.
            wanted = {self.collection_name(p, m) for p, m in known}
            client = self._client()
            for col in client.list_collections():
                cname = col if isinstance(col, str) else col.name
                if cname.startswith("kb_") and cname not in wanted:
                    removed += client.get_collection(cname).count()
                    client.delete_collection(cname)
            return {"added": added, "removed": removed}
        col = self._collection(provider, model)
        sql_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE embedding_provider = ? AND embedding_model = ?",
            (provider, model),
        ).fetchone()[0]
        if col.count() == sql_count:
            return {"added": 0, "removed": 0}
        sql_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM knowledge_chunks WHERE embedding_provider = ? AND embedding_model = ?",
                (provider, model),
            )
        }
        vec_ids = {int(i) for i in col.get(include=[])["ids"]}
        stale = vec_ids - sql_ids
        missing = sorted(sql_ids - vec_ids)
        if stale:
            col.delete(ids=[str(i) for i in stale])
        for start in range(0, len(missing), 500):
            batch = missing[start:start + 500]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT id, embedding FROM knowledge_chunks WHERE id IN ({placeholders})", batch
            ).fetchall()
            col.upsert(ids=[str(r[0]) for r in rows], embeddings=[json.loads(r[1]) for r in rows])
        return {"added": len(missing), "removed": len(stale)}

    def reset(self, conn) -> None:
        client = self._client()
        for col in client.list_collections():
            cname = col if isinstance(col, str) else col.name
            if cname.startswith("kb_"):
                client.delete_collection(cname)


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
    except Exception:  # noqa: BLE001 — 설치 안 됐거나 깨졌으면 예전 방식으로
        return False
    return True


def vector_dir(conn: sqlite3.Connection) -> str | None:
    """벡터 저장 폴더 — `HAIR_VECTOR_DIR`, 없으면 `<db 파일>-vectors`. 메모리 DB면 None."""
    explicit = os.environ.get("HAIR_VECTOR_DIR")
    if explicit:
        return explicit
    path = _db_path(conn)
    return f"{path}-vectors" if path else None


def get_backend(conn: sqlite3.Connection):
    """`HAIR_VECTOR_BACKEND`(chroma|sqlite, 기본 chroma) — chroma를 못 쓰면(메모리 DB·미설치) sqlite."""
    choice = os.environ.get("HAIR_VECTOR_BACKEND", "chroma").strip().lower()
    if choice == "chroma":
        path = vector_dir(conn)
        if path and _chroma_available():
            return ChromaBackend(path)
    return SqliteScanBackend()
