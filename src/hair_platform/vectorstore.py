"""VectorDB 기반 지식검색 Agent의 임베딩 저장/검색 — ADR.md 결정 11
(사용자 요청, 2026-09-26, 3차).

별도 벡터DB 서버(Pinecone·Chroma 서버 모드 등)를 두지 않고, 이미 있는
SQLite(`knowledge_chunks`)에 임베딩을 JSON으로 저장하고 검색은 애플리케이션
레벨에서 코사인 유사도로 계산한다(로컬/온프레미스 우선 원칙, SPEC.md §3).
실사용 규모·질의 빈도가 커지면 `sqlite-vec` 같은 전용 인덱스로 교체를
재검토한다(오픈 이슈 — ADR 결정 11).

**비용 발생 지점**: `embed_text()`(따라서 `index_text()`·`search()`)를 호출하면
실제 임베딩 API가 호출된다 — 호출부(`app.py`)에서 opt-in으로 게이팅한다.
"""

from __future__ import annotations

import json
import math
import sqlite3

DEFAULT_EMBEDDING_MODELS = {
    "openai": "text-embedding-3-small",
}


def embed_text(
    text: str, provider: str | None = None, model: str | None = None
) -> tuple[str, str, list[float]]:
    """실제 임베딩 API 호출 지점 — 여기서만 비용이 발생한다.

    현재는 OpenAI 임베딩 엔드포인트만 지원한다 — Anthropic은 공개 임베딩
    API가 없고, Ollama 로컬 임베딩 모델 지원은 오픈 이슈로 이월한다.
    """
    provider = provider or "openai"
    if provider != "openai":
        raise ValueError(
            f"임베딩은 현재 openai만 지원합니다(요청: {provider!r}) — "
            "Anthropic은 공개 임베딩 API가 없고 Ollama 로컬 임베딩은 오픈 이슈입니다."
        )
    model = model or DEFAULT_EMBEDDING_MODELS["openai"]

    import openai  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    client = openai.OpenAI()
    resp = client.embeddings.create(model=model, input=text)
    return provider, model, resp.data[0].embedding


def index_text(
    conn: sqlite3.Connection,
    source_agent_id: int | None,
    text: str,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """텍스트를 임베딩해 `knowledge_chunks`에 저장한다(비용 발생)."""
    resolved_provider, resolved_model, embedding = embed_text(text, provider, model)
    cur = conn.execute(
        "INSERT INTO knowledge_chunks "
        "(source_agent_id, text, embedding, embedding_provider, embedding_model) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            source_agent_id,
            text,
            json.dumps(embedding),
            resolved_provider,
            resolved_model,
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 3,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """쿼리를 임베딩해(비용 발생) 저장된 청크와 코사인 유사도로 비교한다.

    서로 다른 임베딩 모델로 만든 벡터는 같은 공간에 있지 않으므로 비교하지
    않는다 — 쿼리와 같은 provider/model로 색인된 청크만 후보로 삼는다.
    """
    resolved_provider, resolved_model, query_embedding = embed_text(
        query, provider, model
    )
    rows = conn.execute(
        "SELECT * FROM knowledge_chunks WHERE embedding_provider = ? AND embedding_model = ?",
        (resolved_provider, resolved_model),
    ).fetchall()
    scored = []
    for row in rows:
        chunk_embedding = json.loads(row["embedding"])
        score = _cosine_similarity(query_embedding, chunk_embedding)
        scored.append(
            {
                "id": row["id"],
                "text": row["text"],
                "source_agent_id": row["source_agent_id"],
                "score": score,
            }
        )
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:top_k]


def as_crewai_tool(conn: sqlite3.Connection, top_k: int = 3):
    """VectorDB 검색을 실제 CrewAI Tool로 감싼다(ADR 결정 12, 사용자 요청
    2026-09-27 — "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘").

    이전에는 `agent_runtime.augment_with_retrieval()`가 Task를 만들기 전에
    무조건 검색 결과를 프롬프트에 끼워 넣었다(정적 주입) — Agent에게 호출
    여부를 판단할 권한이 없었다. 이 함수로 만든 Tool을 `Agent(tools=[...])`
    에 등록하면 **Agent가 실제로 검색이 필요한지 스스로 판단해서 호출**한다
    (`crewai.tools.tool` 데코레이터를 실제 설치된 패키지 소스로 확인해
    반영함 — `inspect`로 시그니처 확인). `crewai`는 지연 import한다 — 이
    함수를 안 쓰면 `vectorstore.py` 자체는 crewai 설치 없이도 그대로
    동작한다(기존 `execute_agent()` 경로가 이 모듈을 그렇게 쓰고 있다)."""
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    @tool("지식 베이스 검색")
    def search_knowledge_base(query: str) -> str:
        """과거에 저장된 산출물 중 이 질의와 관련된 내용을 검색한다.
        검색이 실제로 필요할 때만 호출하라 — 불필요하게 반복 호출하지 마라."""
        hits = search(conn, query, top_k=top_k)
        if not hits:
            return "관련된 과거 산출물을 찾지 못했습니다."
        return "\n".join(f"- {h['text']}" for h in hits)

    return search_knowledge_base
