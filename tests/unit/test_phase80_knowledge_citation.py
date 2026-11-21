"""Phase 80 — 내부지식검색 스킬 질의 누락 수정 + 출처 표기(사용자 보고,
2026-11-20, "대화에서 대화 내용을 '지식베이스로 보내기' 후 '내부지식검색'
스킬로 질의했지만 찾아지지 않고 있어" → "내부지식검색 스킬이 ... 유사한 답을
내주도록, 또한 출처를 포함하도록 개선해줘").

실측 원인(대화 #26, Ornith 35B / 32,768토큰 창):
- system(728) + 내부지식검색 결과(10,259) + 첨부자료 메모리(19,272) 추정
  토큰이 입력 예산(19,404)을 넘겨 `_build_transcript()` 예산이 0 →
  사용자 질문 없이 LLM이 호출돼 "구체적인 질문이 보이지 않는다"고 답함.
- "지식베이스로 보내기"가 메시지 전체를 청크 1개로 저장해, 임베딩 한도
  (mxbai 350자)를 넘는 앞부분이 임베딩에서 버려짐 + 출처 메타데이터 없음.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import (
    conversation_memory,
    conversations,
    knowledge_submissions,
    vectorstore,
)


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[
        "id"
    ]


def _fake_embed(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [1.0, 0.0, 0.1])


def _insert_kb_chunk(conn, text, source_document_id=None, source_agent_id=None):
    cur = conn.execute(
        "INSERT INTO knowledge_chunks (source_agent_id, source_document_id, text, "
        "embedding, embedding_provider, embedding_model) VALUES (?, ?, ?, ?, "
        "'openai', 'text-embedding-3-small')",
        (source_agent_id, source_document_id, text, json.dumps([1.0, 0.0, 0.1])),
    )
    return cur.lastrowid


def _insert_memory_chunk(conn, conv_id, idx, text):
    conn.execute(
        "INSERT INTO conversation_knowledge_chunks (conversation_id, "
        "source_message_id, filename, chunk_index, text, embedding, "
        "embedding_provider, embedding_model) VALUES (?, NULL, '무제.html', ?, ?, ?, "
        "'openai', 'text-embedding-3-small')",
        (conv_id, idx, text, json.dumps([1.0, 0.0, 0.1])),
    )


def test_default_ai_keeps_question_when_knowledge_and_memory_fill_budget(
    client_and_conn,
):
    """회귀 가드 — 내부지식검색 결과 + 큰 첨부자료 메모리가 입력 예산을 전부
    차지해도, 최신 사용자 질문은 반드시 LLM에 전달돼야 한다."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "내부지식검색 예산")["id"]

    # 지식베이스(전역) — ollama 기본 8,192 창의 입력 예산을 혼자 넘길 만큼 크게.
    _insert_kb_chunk(conn, "# HITL 조사\n\n" + "사람이 승인하는 지점. " * 1500)
    # 첨부자료 메모리 — 예산을 또 한 번 다 채울 만큼 여러 개.
    for i in range(40):
        _insert_memory_chunk(conn, conv_id, i, f"[{i}] " + "무관한 첨부 내용 " * 60)
    conn.commit()

    question = "HITL 개념을 설명해줘"
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        conversations.create_message(conn, conv_id, admin_id, question, skill="내부지식검색")
        job_id = conversations.start_default_ai_job(conn, conv_id, admin_id)
        with patch(
            "hair_platform.llm.resolve_provider_and_model",
            return_value=("ollama", "test-model"),
        ), patch("hair_platform.llm.call_llm", return_value="답변") as mock_call:
            conversations.run_default_ai_job(
                conn, job_id, conv_id, admin_id, skill="내부지식검색"
            )

    mock_call.assert_called_once()
    system_arg = mock_call.call_args[0][2]
    transcript_arg = mock_call.call_args[0][3]
    assert question in transcript_arg
    # 내부지식검색 결과는 여전히 들어가 있어야 한다.
    assert "사람이 승인하는 지점" in system_arg


def test_memory_context_respects_reserved_tokens(client_and_conn):
    """다른 소비처가 예산을 다 쓰면 첨부자료 메모리는 최소 1개만 담는다
    (예전엔 `reserved_tokens`를 몰라 입력 예산 전체를 다시 채웠다)."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _login(client, "admin1", "adminpass")
    conv_id = conversations.create_conversation(conn, admin_id, "메모리 예산")["id"]
    for i in range(10):
        _insert_memory_chunk(conn, conv_id, i, f"청크{i} " + "내용 " * 30)
    conn.commit()

    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        unreserved = conversation_memory.retrieve_context_for_query(
            conn, conv_id, "질문", provider="ollama", model="test-model"
        )
        reserved = conversation_memory.retrieve_context_for_query(
            conn,
            conv_id,
            "질문",
            provider="ollama",
            model="test-model",
            reserved_tokens=100_000,
        )
    assert unreserved is not None and reserved is not None
    assert unreserved.count("[첨부자료") == 10
    assert reserved.count("[첨부자료") == 1


def test_knowledge_hits_carry_source_labels_and_citation_rules(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    doc_id = conn.execute(
        "INSERT INTO documents (filename, uploaded_by) VALUES ('HITL 가이드.pdf', ?)",
        (admin_id,),
    ).lastrowid
    doc_chunk = _insert_kb_chunk(conn, "HITL은 사람의 승인 지점이다.", source_document_id=doc_id)
    legacy_chunk = _insert_kb_chunk(
        conn, "# Human-AI Agent 협업 조사\n\nHITL 내용", source_document_id=None
    )
    conn.commit()

    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        hits = vectorstore.search(conn, "HITL", top_k=5, min_score=0.0)
    text = vectorstore.format_hits_with_sources(conn, hits)

    assert f"[청크#{doc_chunk}] 출처: 지식 베이스 문서 'HITL 가이드.pdf'" in text
    # 출처 메타데이터가 없는 과거 청크는 본문 첫 제목으로 출처명을 만든다.
    assert f"[청크#{legacy_chunk}] 출처: 'Human-AI Agent 협업 조사'" in text
    assert "색인일" in text
    assert "## 출처" in text  # 답변 마지막 출처 절 지시
    assert "추측하지 말고" in text


def test_save_message_as_knowledge_chunks_long_message_with_source_document(
    client_and_conn,
):
    """임베딩 한도(mxbai 350자)를 넘는 긴 메시지는 여러 청크로 나뉘어야
    하고(예전엔 1청크 → 마지막 350자만 임베딩), 전부 출처 문서 하나에
    묶여야 한다. 2026-11-20 개편 후 이 경로는 제출 → 결재 → 등록이며,
    admin 본인 제출은 결재 없이 등록된다(출처는 제출 건의 `source_label`)."""
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = conversations.create_conversation(conn, admin_id, "HITL 조사방")["id"]
    long_content = "\n\n".join(f"문단 {i}: " + "HITL 설명 " * 30 for i in range(10))
    msg = conversations.create_message(conn, conv_id, admin_id, long_content)

    with patch(
        "hair_platform.vectorstore.resolve_default_provider_and_model",
        return_value=("ollama", "mxbai-embed-large:latest"),
    ), patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        sub = conversations.save_message_as_knowledge(
            conn, conv_id, msg["id"], admin_id, "admin"
        )
        assert sub["needs_registration"] is True
        registered = knowledge_submissions.register_submission(conn, sub["id"])

    assert registered["status"] == "registered"
    assert registered["source_label"] == f"대화 'HITL 조사방' · 메시지 #{msg['id']}"
    doc = conn.execute(
        "SELECT id, source_type, chunk_count FROM documents WHERE id = ?",
        (registered["document_id"],),
    ).fetchone()
    assert doc["source_type"] == "submission"
    assert doc["chunk_count"] > 1
    chunks = conn.execute(
        "SELECT text FROM knowledge_chunks WHERE source_document_id = ?",
        (doc["id"],),
    ).fetchall()
    assert len(chunks) == doc["chunk_count"]
    assert all(len(c["text"]) <= 350 for c in chunks)
