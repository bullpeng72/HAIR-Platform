"""대화 스코프 메모리(RAG) — 사용자 요청(2026-11-18, "대화에서 업무처리시
Tool, skill 활용, 메시지 길이 관리에 제약사항이 많아... 메모리를 활용하는
방안 등을 고려해 엔터프라이즈 환경 적용이 가능한 수준의 확실한 개선
방안을 도출해줘" 개선축 C — B(토큰 예산)·C(메모리)·A(Tool 호출 제약
해소) 세 축 중 두 번째).

실측 근거: 대화 #14/#20에서 엑셀 첨부파일 1개(19KB)만으로 로컬 모델의
32,768토큰 한도를 넘겨 15분 돌고도 빈 응답을 낸 사고(2026-11-18) —
개선축 B(`token_budget.py`)로 "한도를 넘기기 전에 미리 잘라내기"는
이미 고쳤지만, 잘려 나간 뒷부분은 그냥 버려진다. 이 모듈은 그 버려지는
내용을 **이 대화 전용의 임시 색인**(`conversation_knowledge_chunks`)에
넣어뒀다가, 질문이 올 때마다 관련 있는 부분만 다시 꺼내 쓴다 — 전역
`knowledge_chunks`(`vectorstore.py`)와 분리해 2026-10-26 결정("대화에
주입되는 문서는 저장되지 않고 휘발되는 내용물")을 그대로 지킨다: 이
색인은 그 대화가 지워지면(`conversations._cascade_delete_conversation()`)
같이 지워지고, 다른 대화의 검색에는 전혀 안 걸린다.

숫자 열이 있는 표(CSV/XLSX)는 청크 검색만으론 "합계가 얼마야" 같은
질문에 약하므로(관련 청크를 찾아도 그 안의 일부 행만 보임 — 전체
합계는 전체 행을 다 봐야 계산 가능), 업로드 시점에
`documents.extract_numeric_summary()`로 미리 계산한 합계·평균 등을
별도 청크로 같이 저장한다."""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit, documents, token_budget, vectorstore

# 실측 버그(2026-11-18) — 첨부 업로드 상한(PPTX 25MB)을 그대로 추출하면
# 텍스트가 수천만 자에 이를 수 있어, 상한 없이 전부 색인하면 청크
# 수천~수만 개를 순서대로 임베딩 API에 호출하게 된다(테스트 하나가
# 11분 넘게 실제 네트워크 호출을 반복). 100,000자(약 2,000자짜리 청크
# 50개 안팎 — 몇십 초 안에 끝나는 수준)로 색인 대상 자체를 미리 자른다.
_MAX_INDEXED_CHARS = 100_000


def index_attachment(
    conn: sqlite3.Connection,
    actor_id: int,
    conversation_id: int,
    source_message_id: int | None,
    filename: str,
    text: str,
    raw_bytes: bytes | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """첨부 텍스트를 청크+임베딩해 이 대화 전용 색인에 저장한다(비용
    발생 — 청크마다 임베딩 API 호출). `documents.chunk_text()`를 그대로
    재사용한다(전역 색인기 `documents.ingest_document()`와 같은 청킹
    전략 — 중복 구현 없음).

    `raw_bytes`를 넘기면(원본 바이트가 있을 때만 가능 — CSV/XLSX)
    `documents.extract_numeric_summary()`로 숫자 열 합계·평균 등을
    미리 계산해 별도 청크로 같이 저장한다.

    버그 수정(2026-11-18, 구현 직후 자체 회귀 테스트 중 발견) — 첨부
    업로드 상한(PPTX 25MB)을 그대로 추출하면 텍스트가 수천만 자에
    이를 수 있는데, 상한 없이 전부 청크+임베딩하면 청크 수천~수만
    개를 **순서대로** 임베딩 API에 하나씩 호출하게 된다(실측: 테스트
    하나가 11분 넘게 실제 네트워크 호출을 반복하다 멈추지 않아 강제
    종료해야 했다) — 지연·비용 둘 다 감당할 수 없는 수준이라, 색인
    대상 자체를 `_MAX_INDEXED_CHARS`로 미리 잘라낸다(XLSX 행 상한과
    같은 "정직하게 잘라내고 알린다" 원칙)."""
    if provider is None or model is None:
        default_provider, default_model = vectorstore.resolve_default_provider_and_model(
            conn
        )
        provider = provider or default_provider
        model = model or default_model

    truncated = len(text) > _MAX_INDEXED_CHARS
    if truncated:
        text = text[:_MAX_INDEXED_CHARS] + (
            f"\n...(길이 제한으로 이하 생략 — 원문은 {len(text):,}자였습니다)"
        )
    chunks = documents.chunk_text(text, vectorstore.max_embed_chars(model))
    numeric_summary = (
        documents.extract_numeric_summary(filename, raw_bytes)
        if raw_bytes is not None
        else None
    )
    if numeric_summary:
        # 맨 앞에 둬 "합계/평균 질문"에 최우선으로 검색되게 한다(저장
        # 순서 자체가 검색 점수에 영향을 주진 않지만, 코사인 유사도가
        # 비슷한 다른 청크와 묶여 검색될 때 사람이 먼저 보는 게 자연스
        # 럽다).
        chunks = [numeric_summary] + chunks

    chunk_count = 0
    for i, chunk in enumerate(chunks):
        resolved_provider, resolved_model, embedding = vectorstore.embed_text(
            chunk, provider, model
        )
        conn.execute(
            "INSERT INTO conversation_knowledge_chunks "
            "(conversation_id, source_message_id, filename, chunk_index, text, "
            "embedding, embedding_provider, embedding_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                source_message_id,
                filename,
                i,
                chunk,
                json.dumps(embedding),
                resolved_provider,
                resolved_model,
            ),
        )
        chunk_count += 1

    audit.record(
        conn,
        actor_id,
        "conversation_attachment_indexed",
        "conversations",
        conversation_id,
        after={
            "filename": filename,
            "chunk_count": chunk_count,
            "has_numeric_summary": numeric_summary is not None,
        },
    )
    conn.commit()
    return {
        "filename": filename,
        "chunk_count": chunk_count,
        "has_numeric_summary": numeric_summary is not None,
    }


def search(
    conn: sqlite3.Connection,
    conversation_id: int,
    query: str,
    top_k: int | None = None,
    min_score: float = 0.0,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """`vectorstore.search()`와 같은 코사인 유사도 방식이지만, 이
    대화(`conversation_id`)에 속한 청크만 후보로 삼는다 — 다른 대화의
    첨부파일은 절대 섞여 들어오지 않는다.

    `top_k=None`(기본값)이면 점수순 전체를 돌려준다 — 호출자(`retrieve_
    context_for_query()`)가 토큰 예산 기준으로 직접 자른다. 과거엔
    여기서 고정 5개로 미리 잘랐는데, 청크 수·모델 예산과 무관한
    임의의 숫자라 작은 파일도 중요한 행이 통째로 빠지는 문제가 있었다
    (사용자 보고, 2026-11-19, "자료 파일의 일부만 전달되며... 중요
    정보가 모두 손실")."""
    if provider is None or model is None:
        default_provider, default_model = vectorstore.resolve_default_provider_and_model(
            conn
        )
        provider = provider or default_provider
        model = model or default_model
    resolved_provider, resolved_model, query_embedding = vectorstore.embed_text(
        query, provider, model
    )
    rows = conn.execute(
        "SELECT * FROM conversation_knowledge_chunks WHERE conversation_id = ? "
        "AND embedding_provider = ? AND embedding_model = ?",
        (conversation_id, resolved_provider, resolved_model),
    ).fetchall()
    scored = []
    for row in rows:
        chunk_embedding = json.loads(row["embedding"])
        score = vectorstore._cosine_similarity(query_embedding, chunk_embedding)  # 같은 계산 한 곳(2026-11-25)
        if score < min_score:
            continue
        scored.append(
            {
                "id": row["id"],
                "filename": row["filename"],
                "text": row["text"],
                "score": score,
            }
        )
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:top_k]


def has_indexed_chunks(conn: sqlite3.Connection, conversation_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM conversation_knowledge_chunks WHERE conversation_id = ? LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row is not None


def retrieve_context_for_query(
    conn: sqlite3.Connection,
    conversation_id: int,
    query: str,
    provider: str | None = None,
    model: str | None = None,
    reserved_tokens: int = 0,
) -> str | None:
    """이 대화에 색인된 청크가 하나도 없으면 임베딩 호출 자체를
    생략한다(비용 없음 — 첨부파일을 안 올린 대화의 모든 요청마다 매번
    임베딩 API를 부를 필요가 없다). 관련 청크가 하나도 없으면(min_score
    미만) `None`.

    버그 수정(2026-11-19, 사용자 보고 "자료 파일의 일부만 전달되며,
    자료파일의 중요 정보가 모두 손실") — 예전엔 `search()`가 점수
    상위 5개로 고정해서 잘랐다. 19KB(용돈관리.xlsx, 32건 거래)처럼
    모델 예산에 충분히 들어가는 파일도 청크 5개만 포함돼 나머지 거래
    행이 통째로 빠졌다(카드별/항목별 분석처럼 개별 행을 직접 봐야
    하는 질문은 `documents.extract_numeric_summary()`의 열별 합계만
    으론 못 메운다 — 그건 그룹별 집계가 아니라 열 전체 합계만 준다).
    `provider`/`model`을 넘기면 `token_budget.input_budget_tokens()`
    로 그 모델의 실제 예산만큼, 관련도 순으로 들어가는 데까지 청크를
    전부 담는다 — 작은 파일은 사실상 전부, 큰 파일은 예산 안에서
    최대한 담긴다. 안 넘기면(호출부가 provider/model을 아직 모르는
    경우를 위한 보수적 폴백) 4,000토큰으로 둔다."""
    if not has_indexed_chunks(conn, conversation_id):
        return None
    try:
        hits = search(conn, conversation_id, query)
    except Exception:  # noqa: BLE001 — 임베딩 실패가 전체 응답을 막으면 안 된다
        # `agent_runtime.retrieve_context()`의 vectordb 조회와 같은 원칙
        # ("검색 실패가 Agent 실행 자체를 막으면 안 된다") — 여기서도
        # 똑같이 적용한다.
        return None
    if not hits:
        return None
    # 버그 수정(2026-11-20, 사용자 보고 — "지식베이스로 보내기" 후 "지식
    # 검색" 스킬로 질의했는데 "구체적인 질문이 보이지 않는다"는 답만 옴).
    # 실측 원인: 이 예산을 "모델 입력 예산 전체"로 잡아, 호출자가 이미
    # 확정한 system 프롬프트(+내부지식검색 결과)를 빼지 않고 첨부자료 청크로
    # 예산을 혼자 다 채웠다 — 그 뒤 `_build_transcript()`에 남은 예산이
    # 0이 돼 사용자 질문 자체가 프롬프트에서 사라졌다. `reserved_tokens`
    # 로 다른 소비처 몫을 먼저 빼고 남은 만큼만 쓴다.
    budget = (
        token_budget.input_budget_tokens(
            conn, provider, model, reserved_tokens=reserved_tokens
        )
        if provider and model
        else 4000
    )
    count_provider = provider or "openai"
    sections: list[str] = []
    used_tokens = 0
    for h in hits:
        section = f"[첨부자료 '{h['filename']}'에서 찾은 관련 내용]\n{h['text']}"
        section_tokens = token_budget.count_tokens(section, count_provider)
        if sections and used_tokens + section_tokens > budget:
            break  # 최소 1개는 항상 포함한다(완전히 빈 손으로 돌아가지 않게).
        sections.append(section)
        used_tokens += section_tokens
    return "\n\n".join(sections)
