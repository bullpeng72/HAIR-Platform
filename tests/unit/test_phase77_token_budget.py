"""Phase 77 — 토큰 기반 컨텍스트 예산 관리(사용자 요청, 2026-11-18,
"대화에서 업무처리시 Tool, skill 활용, 메시지 길이 관리에 제약사항이
많아... 엔터프라이즈 환경 적용이 가능한 수준의 확실한 개선 방안을
도출해줘" — 세 축(Tool 호출 제약 해소/메모리 활용/토큰 기반 컨텍스트
관리) 중 최우선으로 합의된 개선축 B).

실측 근거: 대화 #14/#20에서 엑셀 첨부파일 1개(19,321자)만으로 로컬
모델(ornith, 32,768토큰 한도)이 15분 가까이 돌고도 빈 응답을 낸 사고
(2026-11-18) — `_build_transcript()`가 메시지 '개수'만 제한하고 토큰
수는 전혀 안 셌다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations, db, model_registry, token_budget
from hair_platform.auth import Role, create_user


def _fake_embed(text, provider=None, model=None):
    return (provider or "openai", model or "text-embedding-3-small", [0.1, 0.2, 0.3])


def _make_admin(conn) -> int:
    existing = conn.execute(
        "SELECT id FROM users WHERE username = 'tb_admin'"
    ).fetchone()
    if existing is not None:
        return existing["id"]
    return create_user(conn, "tb_admin", "adminpass", Role.ADMIN)


def test_count_tokens_empty_string_is_zero():
    assert token_budget.count_tokens("", "openai") == 0


def test_count_tokens_ollama_applies_safety_multiplier():
    """실측 보정(2026-11-18) — cl100k_base가 이 로컬 모델의 실제
    토큰화보다 적게 세므로(대화 #14 실측: cl100k 0.630 vs 실제 Ollama
    0.983 비율), Ollama는 더 크게(보수적으로) 세야 안전하다."""
    text = "안녕하세요 " * 50
    openai_count = token_budget.count_tokens(text, "openai")
    ollama_count = token_budget.count_tokens(text, "ollama")
    assert ollama_count > openai_count
    assert ollama_count == int(openai_count * token_budget._OLLAMA_SAFETY_MULTIPLIER)


def test_resolve_context_window_uses_admin_registered_value():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    actor_id = _make_admin(conn)
    model_id = model_registry.create_model(conn, actor_id, "ollama", "test-model")
    model_registry.set_context_window(conn, actor_id, model_id, 32768)
    assert token_budget.resolve_context_window(conn, "ollama", "test-model") == 32768


def test_resolve_context_window_falls_back_to_provider_default_when_unset():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    assert (
        token_budget.resolve_context_window(conn, "ollama", "never-registered")
        == token_budget.DEFAULT_CONTEXT_WINDOW_BY_PROVIDER["ollama"]
    )
    assert (
        token_budget.resolve_context_window(conn, "anthropic", "never-registered")
        == token_budget.DEFAULT_CONTEXT_WINDOW_BY_PROVIDER["anthropic"]
    )


def test_input_budget_tokens_subtracts_reserved_and_margin():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = _make_admin(conn)
    model_id = model_registry.create_model(conn, admin_id, "openai", "test-model")
    model_registry.set_context_window(conn, admin_id, model_id, 10_000)
    budget = token_budget.input_budget_tokens(
        conn, "openai", "test-model", reserved_tokens=1_000
    )
    # 10_000 * 0.6 - 1_000 - 256
    assert budget == int(10_000 * token_budget._INPUT_BUDGET_RATIO) - 1_000 - 256


def test_input_budget_tokens_floors_at_zero_when_reserved_exceeds_window():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = _make_admin(conn)
    model_id = model_registry.create_model(conn, admin_id, "openai", "test-model")
    model_registry.set_context_window(conn, admin_id, model_id, 1_000)
    budget = token_budget.input_budget_tokens(
        conn, "openai", "test-model", reserved_tokens=100_000
    )
    assert budget == 0


def test_fit_text_to_budget_returns_unchanged_when_within_budget():
    text = "짧은 문장입니다."
    result = token_budget.fit_text_to_budget(text, "openai", 1000)
    assert result == text


def test_fit_text_to_budget_truncates_and_marks_omission():
    text = "데이터 " * 5000  # 충분히 긴 텍스트
    result = token_budget.fit_text_to_budget(text, "openai", 50)
    assert len(result) < len(text)
    assert "이하 생략" in result


def test_fit_text_to_budget_zero_budget_returns_empty():
    assert token_budget.fit_text_to_budget("아무 내용", "openai", 0) == ""


def test_fit_lines_to_budget_keeps_most_recent_first():
    """최신 메시지(리스트 맨 뒤)부터 채우고, 예산이 부족하면 오래된
    메시지(리스트 앞쪽)부터 드롭된다."""
    lines = ["사용자: 오래된 메시지 " * 20, "AI: 중간 메시지", "사용자: 가장 최근 메시지"]
    # 가장 최근 메시지 하나만 겨우 들어갈 정도의 작은 예산
    budget = token_budget.count_tokens(lines[-1], "openai") + 5
    result = token_budget.fit_lines_to_budget(lines, "openai", budget)
    assert "가장 최근 메시지" in result
    assert "오래된 메시지" not in result


def test_fit_lines_to_budget_truncates_single_oversized_recent_message():
    """회귀 가드 — 2026-11-18 실측 사고 재현: 가장 최근 메시지 1건
    (거대한 첨부파일)만으로도 예산을 넘으면, 통째로 드롭하지 않고
    잘라서라도 넣는다(완전히 빈 입력이 되는 것보다 안전)."""
    huge_attachment = "항목 데이터 " * 3000
    lines = ["사용자: " + huge_attachment]
    budget = 50
    result = token_budget.fit_lines_to_budget(lines, "openai", budget)
    assert result != ""
    assert "이하 생략" in result


def test_fit_lines_to_budget_empty_budget_returns_empty():
    assert token_budget.fit_lines_to_budget(["사용자: 안녕"], "openai", 0) == ""


def test_fit_lines_to_budget_empty_lines_returns_empty():
    assert token_budget.fit_lines_to_budget([], "openai", 1000) == ""


# ---------- _build_transcript() 통합 — provider/model 넘기면 예산 적용 ----------


def _make_conversation_with_huge_attachment(conn):
    """사용자 요청(2026-11-18, "...메모리를 활용하는 방안... 개선 방안을
    도출해줘" 개선축 C) 구현 이후 — 이 글자 수(30,000자)는 `conversations.
    _LARGE_ATTACHMENT_CHAR_THRESHOLD`를 넘어 대화 메모리에 색인되므로
    (실제 임베딩 API 호출), 다른 임베딩 테스트와 같은 관례로 모킹한다."""
    admin_id = _make_admin(conn)
    conv = conversations.create_conversation(conn, admin_id, "예산 테스트")
    conv_id = conv["id"]
    with patch("hair_platform.vectorstore.embed_text", side_effect=_fake_embed):
        conversations.create_attachment_message(
            conn, conv_id, admin_id, "huge.xlsx", "행 데이터 " * 5000
        )
    conversations.create_message(conn, conv_id, admin_id, "분석해줘")
    return conv_id


def test_build_transcript_without_provider_is_unchanged_backward_compat():
    """provider를 안 넘기면(기존 호출부가 아직 안 넘기면) 지금까지와
    100% 동일하게 개수 기준으로만 자른다 — 하위 호환 회귀 가드.

    개선축 C(대화 메모리) 구현 이후 — 큰 첨부는 메시지 저장 시점부터
    이미 미리보기로 줄어들어 있으므로(`_LARGE_ATTACHMENT_CHAR_
    THRESHOLD` 이하), 여기서는 그 문턱 **아래**인 짧은 첨부를 써서
    "이 함수 자체는 provider 없이 호출되면 아무것도 안 자른다"는
    본래 검증 의도를 축 C와 분리해서 확인한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = _make_admin(conn)
    conv_id = conversations.create_conversation(conn, admin_id, "짧은 첨부 테스트")["id"]
    conversations.create_attachment_message(
        conn, conv_id, admin_id, "note.txt", "행 데이터 " * 10
    )
    conversations.create_message(conn, conv_id, admin_id, "분석해줘")
    transcript = conversations._build_transcript(conn, conv_id)
    assert "행 데이터" in transcript
    assert "이하 생략" not in transcript


def test_build_transcript_with_provider_fits_huge_attachment_into_budget():
    """핵심 회귀 테스트 — provider/model을 넘기면 거대한 첨부파일이
    포함된 대화록도 예산 안으로 잘려 들어간다(2026-11-18 사고의 직접
    재현 + 수정 확인)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = _make_admin(conn)
    model_id = model_registry.create_model(conn, admin_id, "openai", "test-model")
    model_registry.set_context_window(conn, admin_id, model_id, 2_000)
    conv_id = _make_conversation_with_huge_attachment(conn)

    transcript = conversations._build_transcript(
        conn, conv_id, provider="openai", model="test-model"
    )
    tokens_used = token_budget.count_tokens(transcript, "openai")
    budget = token_budget.input_budget_tokens(conn, "openai", "test-model")
    assert tokens_used <= budget
    assert "이하 생략" in transcript


def test_build_transcript_with_provider_uses_default_window_when_unregistered():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    conv_id = _make_conversation_with_huge_attachment(conn)
    # 등록 안 된 모델 — provider별 보수적 기본값(ollama=8192)으로 폴백
    transcript = conversations._build_transcript(
        conn, conv_id, provider="ollama", model="never-registered"
    )
    tokens_used = token_budget.count_tokens(transcript, "ollama")
    budget = token_budget.input_budget_tokens(conn, "ollama", "never-registered")
    assert tokens_used <= budget
