"""VectorDB 기반 지식검색 Agent의 임베딩 저장/검색 — 사용자 요청
(2026-09-26, 3차).

별도 벡터DB 서버(Pinecone·Chroma 서버 모드 등)를 두지 않고, 이미 있는
SQLite(`knowledge_chunks`)에 임베딩을 JSON으로 저장하고 검색은 애플리케이션
레벨에서 코사인 유사도로 계산한다(로컬/온프레미스 우선 원칙).
실사용 규모·질의 빈도가 커지면 `sqlite-vec` 같은 전용 인덱스로 교체를
재검토한다(오픈 이슈).

**비용 발생 지점**: `embed_text()`(따라서 `index_text()`·`search()`)를 호출하면
실제 임베딩 API가 호출된다 — 호출부(`app.py`)에서 opt-in으로 게이팅한다.

**실측으로 발견한 버그(2026-10-26, "외부 자료 조사가" Agent가 대화창
`@멘션`에서 "Error code: 401 - ... Missing credentials"로 크래시)** —
`embed_text()`가 `provider` 생략 시 무조건 `"openai"`로 하드코딩돼
있었다. 이 Agent의 LLM provider는 `ollama`(OPENAI_API_KEY 없음)인데,
skill_tags에 "rag"가 있어 `agent_runtime.retrieve_context()`가 매
실행마다 `vectorstore.search()`(→`embed_text()`)를 호출했고, OpenAI
키가 없는 이 환경에서는 Agent 자신의 provider와 무관하게 항상 즉시
크래시했다(네트워크 호출 전, 클라이언트 생성 단계에서 실패해 0.2초
만에 터짐 — 실측 확인). 위에 적은 "로컬/온프레미스 우선 원칙"이
실제로는 기본 provider 선택에 반영돼 있지 않았던 것 — `OPENAI_API_KEY`
가 없으면 로컬 Ollama 임베딩으로 자동 전환한다(`_default_embedding_
provider()`)."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3

from hair_platform import audit

DEFAULT_EMBEDDING_MODELS = {
    "openai": "text-embedding-3-small",
    # 사용자 결정(2026-10-26, "mxbai-embed-large:latest 모델 레지스트리에
    # 등록했어... 맞춰줘") — 이 환경엔 `nomic-embed-text`가 안 받아져
    # 있고 `mxbai-embed-large:latest`가 이미 받아져 있어 그걸 기본값으로
    # 맞췄다. 아래 `_MAX_EMBED_CHARS_BY_MODEL`도 같이 봐야 한다 —
    # `nomic-embed-text`(8192토큰)와 컨텍스트 한도가 16배 다르다.
    "ollama": "mxbai-embed-large:latest",
}


def _default_embedding_provider() -> str:
    """`llm._auto_detect()`와 같은 패턴 — `OPENAI_API_KEY`가 있으면
    openai, 없으면 키가 필요 없는 로컬 Ollama로 자동 판별한다(이
    파일 docstring의 "로컬/온프레미스 우선 원칙")."""
    return "openai" if os.environ.get("OPENAI_API_KEY") else "ollama"


def get_embedding_config(conn: sqlite3.Connection) -> dict | None:
    """admin이 설정해둔 임베딩 provider/모델 — 한 번도 설정한 적
    없으면(행이 없으면) `None`(호출부가 자동판별로 폴백한다)."""
    row = conn.execute(
        "SELECT provider, model FROM embedding_config WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return {"provider": row["provider"], "model": row["model"]}


def set_embedding_config(
    conn: sqlite3.Connection, actor_id: int, provider: str, model: str
) -> None:
    """사용자 요청(2026-10-26, "embedding 모델 설정 기능을 구현해줘
    (하드코딩 하지않도록)") — 임베딩 공간은 시스템 전체에 하나만 둬야
    하므로(서로 다른 모델로 만든 벡터는 같은 공간이 아니라 비교하지
    않는다 — 이 파일의 `search()` 자체 설계 원칙) Agent별 설정이 아니라
    `id=1` 고정 단일 행이다."""
    if provider not in ("openai", "ollama"):
        raise ValueError(f"unsupported_provider:{provider!r}")
    model = (model or "").strip()
    if not model:
        raise ValueError("empty_model")
    before = get_embedding_config(conn)
    conn.execute(
        "INSERT INTO embedding_config (id, provider, model) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET provider = excluded.provider, "
        "model = excluded.model",
        (provider, model),
    )
    audit.record(
        conn,
        actor_id,
        "embedding_config_updated",
        "embedding_config",
        1,
        before=before,
        after={"provider": provider, "model": model},
    )
    conn.commit()


def fetch_available_embedding_models(provider: str) -> list[str]:
    """사용자 요청(2026-11-10, "ollama, openai 선택시 가용한 모델이
    자동 표시되도록") — 임베딩 설정 화면의 모델 `<input>`에 후보를
    채워 넣는 라이브 조회. `model_registry.fetch_available_models()`
    와 정반대 방향으로 거른다 — 그쪽은 "채팅 모델 등록" 화면이라
    `_OPENAI_NON_CHAT_KEYWORDS`로 임베딩 모델을 **제외**하는데, 여기는
    임베딩 설정 전용이라 OpenAI는 "embedding" 포함 모델만 **포함**한다.
    Ollama는 `/api/tags`가 채팅/임베딩 구분 메타데이터를 안 줘서
    (model_registry 쪽과 같은 한계) 보유 모델 전부를 그대로 반환한다
    — 어차피 저장 자체(`set_embedding_config`)는 자유 텍스트라 후보에
    없는 이름도 그대로 입력할 수 있다(이 함수는 참고용 자동완성일
    뿐, 목록을 강제하지 않는다). 접속 실패·키 미설정 시 예외를 그대로
    던진다 — 호출자(app.py)가 사람이 읽을 메시지로 바꾼다(model_
    registry.fetch_available_models()와 같은 관례)."""
    if provider == "ollama":
        import httpx  # 지연 import — model_registry.fetch_available_models()와 같은 경계

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        resp = httpx.get(f"{base_url}/api/tags", timeout=10)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))

    if provider == "openai":
        import openai  # 지연 import — model_registry.fetch_available_models()와 같은 경계

        client = openai.OpenAI()
        names = [m.id for m in client.models.list()]
        return sorted(n for n in names if "embedding" in n.lower())

    raise ValueError(f"unsupported_provider:{provider!r}")


def resolve_default_provider_and_model(conn: sqlite3.Connection) -> tuple[str, str]:
    """`search()`/`index_text()`가 `provider`/`model`을 안 받았을 때
    쓸 기본값을 고른다 — admin이 명시적으로 설정해둔 `embedding_config`
    가 있으면 그걸 우선하고, 없으면 기존 자동판별(`_default_embedding_
    provider()`)+하드코딩 기본 모델로 폴백한다(동작 변경 없음,
    설정하면 그게 우선할 뿐)."""
    configured = get_embedding_config(conn)
    if configured is not None:
        return configured["provider"], configured["model"]
    provider = _default_embedding_provider()
    return provider, DEFAULT_EMBEDDING_MODELS[provider]


# 사용자 요청(2026-11-03, "RAG 검색 품질 향상을 위한 기능이 적절히
# 반영되어 있는지 확인... 우선순위를 정해 진행해줘") — `search()`가
# 지금까지 관련성 임계값 없이 top_k만큼 무조건 반환했다(무관한 결과도
# 그대로 섞여 나옴). `embedding_config`와 같은 `id=1` 싱글턴 패턴 —
# admin이 top_k/min_score(코사인 유사도 하한)를 조정할 수 있게 한다.
DEFAULT_RAG_TOP_K = 3
DEFAULT_RAG_MIN_SCORE = 0.2


def get_rag_search_config(conn: sqlite3.Connection) -> dict | None:
    """admin이 설정해둔 top_k/min_score — 한 번도 설정한 적 없으면
    (행이 없으면) `None`(호출부가 기본값으로 폴백한다)."""
    row = conn.execute(
        "SELECT top_k, min_score FROM rag_search_config WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return {"top_k": row["top_k"], "min_score": row["min_score"]}


def set_rag_search_config(
    conn: sqlite3.Connection, actor_id: int, top_k: int, min_score: float
) -> None:
    """`set_embedding_config()`와 같은 `id=1` 싱글턴 upsert + 감사로그
    패턴."""
    if top_k < 1:
        raise ValueError("invalid_top_k")
    if not (0.0 <= min_score <= 1.0):
        raise ValueError("invalid_min_score")
    before = get_rag_search_config(conn)
    conn.execute(
        "INSERT INTO rag_search_config (id, top_k, min_score) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET top_k = excluded.top_k, "
        "min_score = excluded.min_score",
        (top_k, min_score),
    )
    audit.record(
        conn,
        actor_id,
        "rag_search_config_updated",
        "rag_search_config",
        1,
        before=before,
        after={"top_k": top_k, "min_score": min_score},
    )
    conn.commit()


def resolve_rag_search_config(conn: sqlite3.Connection) -> tuple[int, float]:
    """`search()`가 `top_k`/`min_score`를 안 받았을 때 쓸 기본값을
    고른다 — `resolve_default_provider_and_model()`과 같은 2단 폴백
    (admin이 설정해둔 값이 있으면 그걸 우선, 없으면 하드코딩 기본값)."""
    configured = get_rag_search_config(conn)
    if configured is not None:
        return configured["top_k"], configured["min_score"]
    return DEFAULT_RAG_TOP_K, DEFAULT_RAG_MIN_SCORE


_KEYWORD_TOKEN_RE = re.compile(r"\w+")
_COSINE_WEIGHT = 0.8
_KEYWORD_WEIGHT = 0.2


def _keyword_overlap_ratio(query: str, text: str) -> float:
    """비용 없는 재순위화 보조 신호 — 토큰 "경계" 일치가 아니라 "부분
    문자열 포함" 기준이다. 한국어는 조사가 명사에 붙어("텍스트를")
    교착어 특성상 정확한 토큰 경계 매칭은 과소매칭이 심하다 — 질의
    토큰이 청크 텍스트 안에 부분 문자열로 들어있는지만 본다("텍스트"
    토큰이 "텍스트를" 안에서 발견됨). 완전한 한국어 형태소 분석은
    아니다 — `_KEYWORD_WEIGHT`(0.2)짜리 보조 신호일 뿐이라 이 저장소
    규모에선 허용 가능한 한계로 판단한다."""
    query_tokens = {t for t in _KEYWORD_TOKEN_RE.findall(query.lower()) if len(t) >= 2}
    if not query_tokens:
        return 0.0
    text_lower = text.lower()
    matched = sum(1 for t in query_tokens if t in text_lower)
    return matched / len(query_tokens)


# 사용자 보고(2026-10-14, "Error code: 400 - ... maximum context length is
# 8192 tokens") — 실측으로 확인한 원인: `agent_runtime.
# augment_with_retrieval()`이 대화 전체 transcript를 그대로 VectorDB 검색
# 질의로 넘기는데, 메시지 "개수" 제한(`conversations._CONTEXT_MESSAGE_LIMIT`)
# 은 있어도 토큰 "수" 제한은 없다 — 코드 산출물 하나(실측 사례: 8,256자)만
# 으로도 OpenAI `text-embedding-3-small`의 8192토큰 한도를 넘긴다(Ollama
# `nomic-embed-text`도 같은 8192토큰 한도). 정확한 토크나이저(tiktoken)는
# 이 프로젝트 의존성이 아니라서 보수적인 문자 수 상한으로 잘라낸다(한국어는
# 영어보다 문자당 토큰 밀도가 높아 넉넉히 잡는다) — 검색 질의는 최근 맥락이
# 더 중요하므로 앞부분이 아니라 뒷부분을 남긴다.
#
# 모델별 한도(2026-10-26, "mxbai-embed-large:latest로 맞춰줘") — 기본
# Ollama 임베딩 모델을 `mxbai-embed-large:latest`로 바꾸면서 실측
# 확인(`ollama show mxbai-embed-large:latest`): BERT 계열이라 컨텍스트가
# **512 토큰뿐**이다(nomic-embed-text/OpenAI의 8192토큰 대비 16분의 1).
# 하나의 전역 상수를 그대로 쓰면 보통 길이의 산출물만 임베딩해도 Ollama가
# 조용히 잘라내거나(에러 없이) 의미가 깨진 임베딩을 돌려줄 위험이 크다 —
# 같은 보수적 비율(8192토큰→6000자)로 모델별 한도를 따로 둔다. 등록되지
# 않은 모델은 더 짧은 쪽(512토큰 기준)으로 안전하게 가정한다.
_MAX_EMBED_CHARS_BY_MODEL: dict[str, int] = {
    "text-embedding-3-small": 6000,
    "nomic-embed-text": 6000,
    "mxbai-embed-large:latest": 350,
}
_DEFAULT_MAX_EMBED_CHARS = 350


def _truncate_for_embedding(text: str, model: str) -> str:
    limit = _MAX_EMBED_CHARS_BY_MODEL.get(model, _DEFAULT_MAX_EMBED_CHARS)
    return text[-limit:] if len(text) > limit else text


def max_embed_chars(model: str) -> int:
    """`documents.py`가 업로드 파일을 모델 한도에 맞는 청크로 쪼갤 때 쓰는
    글자수 한도 — `_truncate_for_embedding()`과 같은 표를 그대로 노출만
    한다. 색인 시점에 조용히 잘리는 대신(`index_text()`가 그대로 하듯),
    청크를 미리 이 한도 이내로 나눠서 내용이 안 버려지게 한다."""
    return _MAX_EMBED_CHARS_BY_MODEL.get(model, _DEFAULT_MAX_EMBED_CHARS)


def embed_text(
    text: str, provider: str | None = None, model: str | None = None
) -> tuple[str, str, list[float]]:
    """실제 임베딩 API 호출 지점 — 여기서만 비용이 발생한다.

    OpenAI 임베딩 엔드포인트와 Ollama 로컬 임베딩을 지원한다(사용자 결정,
    2026-09-29, "VectorDB 임베딩 ollama 지원" — `llm.py`의 `_call_ollama()`와
    같은 `OLLAMA_BASE_URL` 환경변수·httpx 패턴). Anthropic은 여전히
    지원하지 않는다 — 공개 임베딩 API 자체가 없다.
    """
    provider = provider or _default_embedding_provider()
    if provider == "openai":
        model = model or DEFAULT_EMBEDDING_MODELS["openai"]
        text = _truncate_for_embedding(text, model)

        import openai  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

        client = openai.OpenAI()
        resp = client.embeddings.create(model=model, input=text)
        return provider, model, resp.data[0].embedding
    if provider == "ollama":
        model = model or DEFAULT_EMBEDDING_MODELS["ollama"]
        text = _truncate_for_embedding(text, model)

        import httpx  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        resp = httpx.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=120,
        )
        resp.raise_for_status()
        return provider, model, resp.json()["embedding"]
    raise ValueError(
        f"임베딩은 현재 openai/ollama만 지원합니다(요청: {provider!r}) — "
        "Anthropic은 공개 임베딩 API가 없습니다."
    )


def index_text(
    conn: sqlite3.Connection,
    source_agent_id: int | None,
    text: str,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """텍스트를 임베딩해 `knowledge_chunks`에 저장한다(비용 발생).

    `provider`/`model`을 안 넘기면 admin이 설정해둔 `embedding_config`
    (없으면 자동판별)를 쓴다 — `embed_text()` 자체는 `conn`이 없어
    DB 설정을 못 읽으므로, 여기서 먼저 해석해 넘긴다."""
    if provider is None or model is None:
        default_provider, default_model = resolve_default_provider_and_model(conn)
        provider = provider or default_provider
        model = model or default_model
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
    top_k: int | None = None,
    min_score: float | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """쿼리를 임베딩해(비용 발생) 저장된 청크와 코사인 유사도로 비교한다.

    서로 다른 임베딩 모델로 만든 벡터는 같은 공간에 있지 않으므로 비교하지
    않는다 — 쿼리와 같은 provider/model로 색인된 청크만 후보로 삼는다.

    `provider`/`model`을 안 넘기면 `index_text()`와 같은 방식으로
    `embedding_config`(없으면 자동판별)를 쓴다 — 색인할 때와 검색할
    때 기본값이 서로 달라지면 매칭되는 청크가 하나도 없어 보이므로
    반드시 같은 해석 경로를 거쳐야 한다.

    `top_k`/`min_score`를 안 넘기면 `resolve_rag_search_config(conn)`로
    해석한다(명시 인자가 항상 우선).

    순서가 중요하다 — ① 원시 코사인으로 `min_score` 필터 → ②
    `_keyword_overlap_ratio()`를 섞은 블렌드 점수로 정렬 → ③ `top_k`
    슬라이스. 필터를 코사인 기준으로 먼저 거는 이유: 키워드 일치
    때문에 블렌드 점수가 올라가도 실제로는 무관한 청크(코사인이 낮은
    청크)를 관련성 기준으로 통과시켜버리면 안 되기 때문이다. 반대로
    `top_k`를 먼저 자르면 재순위화가 이미 버려진 후보를 못 살린다."""
    if top_k is None or min_score is None:
        default_top_k, default_min_score = resolve_rag_search_config(conn)
        top_k = top_k if top_k is not None else default_top_k
        min_score = min_score if min_score is not None else default_min_score
    if provider is None or model is None:
        default_provider, default_model = resolve_default_provider_and_model(conn)
        provider = provider or default_provider
        model = model or default_model
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
        cosine_score = _cosine_similarity(query_embedding, chunk_embedding)
        if cosine_score < min_score:
            continue
        keyword_score = _keyword_overlap_ratio(query, row["text"])
        blended_score = (
            _COSINE_WEIGHT * cosine_score + _KEYWORD_WEIGHT * keyword_score
        )
        scored.append(
            {
                "id": row["id"],
                "text": row["text"],
                "source_agent_id": row["source_agent_id"],
                "score": cosine_score,
                "_blended_score": blended_score,
            }
        )
    scored.sort(key=lambda s: s["_blended_score"], reverse=True)
    top = scored[:top_k]
    for item in top:
        del item["_blended_score"]
    return top


def as_crewai_tool(
    conn: sqlite3.Connection,
    top_k: int | None = None,
    min_score: float | None = None,
):
    """VectorDB 검색을 실제 CrewAI Tool로 감싼다(사용자 요청
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

    # 사용자 보고(2026-10-06, 실제 크루 실행에서 "OpenAI function name
    # cannot be empty" 크래시) — 설치된 crewai 버전의 `sanitize_tool_name()`
    # (`crewai/utilities/string_utils.py`)이 함수명을 ASCII로 정규화할 때
    # `name.encode("ascii", "ignore")`로 비-ASCII 문자를 전부 버린다.
    # 한글만으로 된 이름("지식 베이스 검색")은 이 과정에서 완전히 빈
    # 문자열이 돼 `validate_function_name()`이 즉시 예외를 던진다 — Tool
    # Pool/감사로그에 보이는 한글 이름(`tool_pool.name` = "VectorDB
    # 지식검색")은 건드리지 않고, LLM에 실제로 전달되는 함수명만 ASCII로
    # 바꾼다(설명은 그대로 한글 docstring에 남겨 LLM이 용도를 이해하게 함).
    @tool("knowledge_base_search")
    def search_knowledge_base(query: str) -> str:
        """과거에 저장된 산출물 중 이 질의와 관련된 내용을 검색한다.
        검색이 실제로 필요할 때만 호출하라 — 불필요하게 반복 호출하지 마라."""
        hits = search(conn, query, top_k=top_k, min_score=min_score)
        if not hits:
            return "관련된 과거 산출물을 찾지 못했습니다."
        return "\n".join(f"- {h['text']}" for h in hits)

    return search_knowledge_base


# ---------- Admin "지식 베이스" 탭(사용자 요청, 2026-10-26, "공통의
# 지식을 관리하기 위해 Admin에 VectorDB, GraphDB 관리 기능을 추가해줘
# (데이터 입력/삭제/조회 및 간단한 통계 등)") ----------


def list_chunks(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> dict:
    """`GET /api/audit-log`와 같은 `{"items", "total", "limit", "offset"}`
    페이지네이션 규약(`app.py`의 `get_audit_log` 참고). 출처를 한눈에
    보이게 Agent 산출물(`source_agent_id`)·업로드 문서(`source_document_id`)
    이름까지 LEFT JOIN으로 같이 가져온다(둘 다 NULL이면 "직접 입력"이거나
    출처 추적이 없는 과거 데이터)."""
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        "SELECT kc.id, kc.text, kc.embedding_provider, kc.embedding_model, kc.created_at, "
        "kc.source_agent_id, a.role_name AS source_agent_role_name, "
        "kc.source_document_id, d.filename AS source_document_filename "
        "FROM knowledge_chunks kc "
        "LEFT JOIN agent_pool a ON a.id = kc.source_agent_id "
        "LEFT JOIN documents d ON d.id = kc.source_document_id "
        "ORDER BY kc.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()["c"]
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def delete_chunk(conn: sqlite3.Connection, actor_id: int, chunk_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM knowledge_chunks WHERE id = ?", (chunk_id,)
    ).fetchone()
    if row is None:
        raise ValueError("chunk_not_found")
    conn.execute("DELETE FROM knowledge_chunks WHERE id = ?", (chunk_id,))
    audit.record(
        conn, actor_id, "knowledge_chunk_deleted", "knowledge_chunks", chunk_id
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    """ "지식 베이스" 탭의 VectorDB 통계 줄 — 전부 가벼운 COUNT/SUM이라
    매번 새로 계산해도 된다(캐시 불필요)."""
    totals = conn.execute(
        "SELECT COUNT(*) AS total_chunks, COALESCE(SUM(LENGTH(text)), 0) AS total_characters "
        "FROM knowledge_chunks"
    ).fetchone()
    by_model = conn.execute(
        "SELECT embedding_provider, embedding_model, COUNT(*) AS count "
        "FROM knowledge_chunks GROUP BY embedding_provider, embedding_model "
        "ORDER BY count DESC"
    ).fetchall()
    by_source = conn.execute(
        "SELECT "
        "SUM(CASE WHEN source_agent_id IS NOT NULL THEN 1 ELSE 0 END) AS agent_output, "
        "SUM(CASE WHEN source_document_id IS NOT NULL THEN 1 ELSE 0 END) AS uploaded_document, "
        "SUM(CASE WHEN source_agent_id IS NULL AND source_document_id IS NULL THEN 1 ELSE 0 END) AS other "
        "FROM knowledge_chunks"
    ).fetchone()
    return {
        "total_chunks": totals["total_chunks"],
        "total_characters": totals["total_characters"],
        "by_model": [dict(row) for row in by_model],
        "by_source": dict(by_source),
    }
