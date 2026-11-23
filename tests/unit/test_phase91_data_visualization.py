"""데이터 시각화 적극 활용(2026-11-21, "데이터 시각화 스킬을 에이전트에 적극 활용해줘")."""

from __future__ import annotations

import json

from hair_platform import db, presentation_deck, skill_catalog, webui

_DECK_WITH_CHART = (
    '<main class="hair-deck"><section class="slide"><h2>분기 매출</h2>'
    "<div class=\"hair-chart\" data-chart='{\"type\":\"line\",\"labels\":[\"1분기\",\"2분기\"],"
    "\"series\":{\"매출\":[42,48]},\"unit\":\"억 원\"}'></div></section></main>"
)


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def test_deck_with_chart_gets_chart_runtime_once():
    out = presentation_deck.finalize(_DECK_WITH_CHART)
    assert out.count('id="hair-deck-charts"') == 1
    assert presentation_deck.CHART_JS_URL in out
    assert out.index('id="hair-deck-charts"') < out.lower().rindex("</body>")
    assert presentation_deck.finalize(out) == out  # 멱등
    assert ".hair-deck .hair-chart{" in out  # 차트 영역 CSS


def test_deck_without_chart_has_no_script():
    out = presentation_deck.finalize('<main class="hair-deck"><section class="slide"><h2>x</h2></section></main>')
    assert "<script" not in out


def test_presentation_skill_documents_chart_component():
    guide = skill_catalog.SKILL_GUIDES["프레젠테이션작성"]
    assert guide.output_format is not None
    assert 'class="hair-chart"' in guide.output_format


def test_visualization_skill_says_when_and_how():
    guide = skill_catalog.SKILL_GUIDES["데이터시각화"]
    assert "3개 이상" in guide.description  # 언제
    assert guide.output_format is not None
    assert "```chart" in guide.output_format  # 마크다운·대화에서는 어떻게
    assert 'class="hair-chart"' in guide.output_format  # 덱에서는 어떻게


def test_existing_db_gets_new_visualization_text_and_agent_links_once():
    conn = _conn()
    row = conn.execute(
        "SELECT description FROM skill_pool WHERE name = '데이터시각화'"
    ).fetchone()
    assert row[0] == skill_catalog.SKILL_GUIDES["데이터시각화"].description
    tags = {
        r[0]: json.loads(r[1])
        for r in conn.execute("SELECT role_name, skill_tags FROM agent_pool")
    }
    for role in skill_catalog.ONE_TIME_AGENT_SKILL_ADDITIONS["2026-11-21-data-visualization-agents"]:
        assert "데이터시각화" in tags[role], role
    # admin이 연결을 빼면 재기동해도 되살리지 않는다(한 번만 실행).
    conn.execute(
        "UPDATE agent_pool SET skill_tags = '[\"실적서작성\"]' WHERE role_name = '실적 보고서 작성자'"
    )
    conn.commit()
    db.init_schema(conn)
    again = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '실적 보고서 작성자'"
    ).fetchone()[0]
    assert json.loads(again) == ["실적서작성"]


def test_old_catalog_text_is_replaced_in_existing_db():
    """개편 직전 문구가 남아 있는 DB(운영 DB)도 새 문구로 바뀐다."""
    conn = _conn()
    old = "레이블·계열 숫자 데이터를 '차트 생성기'로 실제 Chart.js 설정으로 만든 뒤, 그 결과를 ```chart 코드펜스에 그대로 넣어 그래프로 보여준다. 차트 종류는 비교=막대, 추이=선, 구성비=도넛으로 고른다."
    assert skill_catalog.is_replaceable("데이터시각화", "description", old)
    assert not skill_catalog.is_replaceable("데이터시각화", "description", "관리자가 직접 고친 문구")
    _ = conn


def test_chart_fence_accepts_simple_spec():
    html = webui.INDEX_HTML
    assert "function _chartConfigFromSpec(spec)" in html
    assert "new Chart(canvas, _chartConfigFromSpec(JSON.parse(canvas.dataset.chartConfig)))" in html


def test_chart_fence_inside_deck_becomes_deck_chart():
    """덱 HTML 안에 마크다운식 ```chart 펜스를 쓴 경우(2026-11-21 실측)도 덱 차트로 그린다."""
    deck = (
        '<main class="hair-deck"><section class="slide"><h2>매출</h2>\n<div>\n'
        '  ```chart\n  {"type":"bar","labels":["1분기","2분기"],"series":{"매출":[42,48]}}\n  ```\n'
        "</div></section></main>"
    )
    out = presentation_deck.finalize(deck)
    assert "```chart" not in out
    assert 'class="hair-chart" data-chart="{&quot;type&quot;:&quot;bar&quot;' in out
    assert 'id="hair-deck-charts"' in out
    # JSON이 깨졌으면 그대로 둔다.
    broken = deck.replace('"labels"', "labels")
    assert "```chart" in presentation_deck.finalize(broken)


def test_primary_skill_label_only_when_clearly_ahead():
    """단어 겹침이 근소하게 앞선 스킬을 '주 스킬'로 단정하지 않는다(2026-11-21 실측 —
    덱 Agent에서 개요 스킬이 주 스킬로 표시돼 덱 디자인 시스템을 버렸다)."""
    from hair_platform import skill_prompting

    conn = _conn()
    skills = ["발표자료구성", "경영보고서작성", "프레젠테이션작성", "데이터시각화"]
    block = skill_prompting.knowledge_block(
        conn, skills, "2026년 3분기 실적을 경영진에게 보고하는 발표자료를 6장 이내로 만들어줘"
    )
    assert "이번 요청의 주 스킬" not in block
    # admin이 정한 순서 유지.
    order = [line.split("]")[0][3:] for line in block.splitlines() if line.startswith("- [")]
    assert order[:3] == ["발표자료구성", "경영보고서작성", "프레젠테이션작성"]
    # 분명히 앞서면 표시한다.
    clear = skill_prompting.knowledge_block(
        conn, ["커밋메시지작성", "보안점검"], "로그인 API의 SQL 인젝션 취약점을 보안 점검해줘"
    )
    assert clear.startswith("이번 요청의 주 스킬(판단 참고): 보안점검")


def test_hair_prefixed_slide_classes_are_normalized():
    """모델이 "slide cover" 대신 "hair-slide hair-cover"를 쓴 덱(2026-11-21 실측)도 덱 CSS·
    슬라이드쇼가 슬라이드를 찾도록 접두어를 뗀다. 덱이 정의하지 않은 클래스는 그대로."""
    deck = (
        '<main class="hair-deck"><section class="hair-slide hair-cover"><h1>제목</h1></section>'
        '<section class="hair-slide my-own"><h2>본문</h2></section></main>'
    )
    out = presentation_deck.finalize(deck)
    assert '<section class="slide cover">' in out
    assert '<section class="slide my-own">' in out
    assert "hair-slide" not in out


def test_full_chart_config_without_colors_gets_palette():
    out = presentation_deck.finalize(_DECK_WITH_CHART)
    assert "if(!d.backgroundColor)" in out  # 덱: 도구 결과(전체 설정)에도 강조색 팔레트
    assert "if (!d.backgroundColor)" in webui.INDEX_HTML  # 플랫폼 화면도 같은 보정


# ---------- 2026-11-21 발표자료 실측 후속 3가지 ----------


def test_vietnamese_leak_is_detected_and_fixed():
    from hair_platform import language_qa

    text = "가" * 40 + "\n시간·인력 환산 tiết약 효과 정량 산정\n카페(café) 이름은 정상"
    assert language_qa.find_issue_segments(text) == ["시간·인력 환산 tiết약 효과 정량 산정"]
    assert language_qa._apply_known_fixes("시간·인력 환산 tiết약 효과") == "시간·인력 환산 절약 효과"


def test_presentation_rules_forbid_invented_meta_and_classes():
    rules = skill_catalog.SKILL_GUIDES["프레젠테이션작성"].constraints
    assert rules is not None
    assert "연도·날짜·출처·표본 수·담당 부서·목표치를 지어내지 않는다" in rules
    assert "⑱ 출력 형식에 나온 클래스만 쓴다" in rules
    # 개정 직전 문구가 남은 DB도 새 문구로 바뀐다.
    assert skill_catalog.is_replaceable(
        "프레젠테이션작성", "constraints", "x"
    ) is False


def test_quality_bar_forbids_invented_meta():
    from hair_platform import skill_prompting

    assert "연도·날짜·출처·표본 수·담당 부서·목표치를 만들어 넣지 않는다" in skill_prompting.QUALITY_BAR


def test_hair_prefixed_defined_classes_are_normalized_everywhere():
    deck = (
        '<main class="hair-deck"><section class="slide"><div class="hair-decision"><h3>요청</h3></div>'
        '<p class="hair-note">노트</p><span class="hair-bignum">520</span>'
        '<div class="hair-chart" data-chart=\'{"type":"bar","labels":["a"],"series":{"x":[1]}}\'></div>'
        "</section></main>"
    )
    out = presentation_deck.finalize(deck)
    assert 'class="decision"' in out and 'class="note"' in out
    assert 'class="hair-chart"' in out  # 원래 정의된 이름은 그대로
    assert presentation_deck.undefined_classes(out) == ["hair-bignum"]  # 지어낸 이름만 남는다


def test_defined_classes_come_from_css_and_guide_and_reach_the_page():
    names = presentation_deck.defined_classes()
    for n in ("slide", "metrics", "metric", "steps", "ask", "hair-chart", "cover", "black"):
        assert n in names
    assert all(n.isascii() for n in names)
    assert json.dumps(names) in webui.INDEX_HTML  # 화면 점검이 같은 목록을 쓴다
    assert "__DECK_CLASSES_JSON__" not in webui.INDEX_HTML


def test_multiple_charts_on_one_slide_are_shrunk():
    """한 장에 차트가 여러 개면 높이를 줄인다(2026-11-21 실측: 결론 문장이 슬라이드 밖으로 밀림)."""
    out = presentation_deck.finalize(_DECK_WITH_CHART)
    assert "cs.length>1" in out


def test_chart_js_url_has_single_source():
    from hair_platform import presentation_deck as pd

    assert webui.INDEX_HTML.count(pd.CHART_JS_URL) == 1
    assert "__CHART_JS_URL__" not in webui.INDEX_HTML
