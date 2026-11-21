"""Phase 84 — "프레젠테이션작성" 스킬 + HAIR 덱 디자인 시스템(사용자 요청,
2026-11-20, "프레젠테이션작성 skill을 새로 추가해줘 … 품질 높은 HTML 방식의
발표자료 생성이 가능하도록").

모델은 정해진 클래스로 슬라이드 마크업만 쓰고, `presentation_deck.finalize()`
가 저장 시점에 디자인 시스템 CSS를 넣어 혼자 열리는 HTML 문서로 만든다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations, presentation_deck

_DECK_BODY = (
    '<main class="hair-deck" data-accent="teal">'
    '<section class="slide cover black"><h1 class="hero">HITL 도입 제안</h1>'
    '<div class="rule"></div><p class="meta">2026-11-20 · 전략팀</p></section>'
    '<section class="slide statement"><h2>승인 지점을 업무 흐름에 내장한다</h2></section>'
    "</main>"
)


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]


def test_non_deck_text_is_untouched():
    for text in (None, "", "# 일반 마크다운", "<!DOCTYPE html><html><body>x</body></html>"):
        assert presentation_deck.finalize(text) == text
    assert presentation_deck.finalize_with_type("평문", "markdown") == ("평문", "markdown")


def test_chatter_and_fence_are_stripped_into_a_full_document():
    raw = (
        "다음은 요청하신 발표자료입니다.\n\n```html\n<!DOCTYPE html><html lang=\"ko\"><head>"
        "<meta charset=\"utf-8\"><title>HITL</title></head><body>"
        + _DECK_BODY
        + "</body></html>\n```\n\n수정이 필요하면 말씀해 주세요."
    )
    out = presentation_deck.finalize(raw)
    assert out.startswith("<!DOCTYPE html>")  # 대화 화면이 HTML 문서로 인식하는 조건
    assert out.rstrip().endswith("</html>")
    assert "다음은 요청하신" not in out and "```" not in out
    assert 'id="hair-deck-css"' in out
    # 디자인 시스템은 <head> 맨 앞 — 모델이 따로 쓴 스타일이 뒤에서 덮어쓸 수 있게.
    assert out.index('id="hair-deck-css"') < out.index("<title>")
    assert presentation_deck.finalize(out) == out  # 멱등


def test_fragment_is_wrapped_with_title_from_first_heading():
    out = presentation_deck.finalize(_DECK_BODY)
    assert out.startswith("<!DOCTYPE html>")
    assert "<title>HITL 도입 제안</title>" in out
    assert '<meta charset="utf-8">' in out
    assert out.count('class="slide') == 2


def test_deliverable_content_type_is_corrected_to_html():
    content, content_type = presentation_deck.finalize_with_type(_DECK_BODY, "markdown")
    assert content_type == "html"
    assert 'id="hair-deck-css"' in content


def test_css_contract_matches_platform_slideshow_and_rules():
    css = presentation_deck.DECK_CSS
    # 플랫폼 슬라이드쇼 컨트롤러(`webui._injectSlideshowController`)는 `.slide`를 센다.
    assert ".hair-deck .slide{" in css
    # 1280×720 논리 캔버스 기준 균일 스케일 + 인쇄 16:9 1장=1페이지.
    assert "100cqw / 1120" in css and "100cqh / 597" in css
    assert "@page{size:1280px 720px" in css
    # 외부 리소스 없음(오프라인·다운로드에서도 동일하게 보인다).
    assert "http" not in css and "@import" not in css and "url(" not in css


def test_skill_is_seeded_with_structured_guidance(client_and_conn):
    _client, conn = client_and_conn
    row = conn.execute(
        "SELECT * FROM skill_pool WHERE name = '프레젠테이션작성'"
    ).fetchone()
    assert row is not None
    assert row["augmentation_mode"] == "none"
    assert row["category"] == "general" and row["show_in_conversation"] == 1
    assert row["history_mode"] == "full" and row["use_attachments"] == 1
    assert 'class="hair-deck"' in row["output_format"]
    assert "근거가 뜻하는 바" in row["constraints"]
    assert row["code"]  # SKL-NONE-xxx


def test_conversation_reply_with_skill_is_finalized_and_prompt_grounded(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = conversations.create_conversation(conn, admin_id, "발표자료")["id"]
    conversations.create_message(
        conn, conv_id, admin_id, "HITL 도입 제안 발표자료 만들어줘", skill="프레젠테이션작성"
    )
    job_id = conversations.start_default_ai_job(conn, conv_id, admin_id)
    with patch(
        "hair_platform.llm.call_llm", return_value="발표자료입니다.\n" + _DECK_BODY
    ) as mock_call:
        conversations.run_default_ai_job(conn, job_id, conv_id, admin_id, skill="프레젠테이션작성")

    system_arg = mock_call.call_args[0][2]
    assert 'class="hair-deck"' in system_arg  # 출력 골격이 프롬프트에 들어간다
    reply = conn.execute(
        "SELECT content FROM conversation_messages WHERE conversation_id = ? "
        "AND author_type = 'agent' ORDER BY id DESC LIMIT 1",
        (conv_id,),
    ).fetchone()["content"]
    assert reply.startswith("<!DOCTYPE html>")
    assert 'id="hair-deck-css"' in reply
