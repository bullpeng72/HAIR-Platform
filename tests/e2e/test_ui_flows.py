"""핵심 화면 흐름(2026-11-26, 개선 계획 2-1). 오늘 한 화면 수정(대화 화면 배치·선택 상자 간격·메뉴 찾기·필수 도구·
"쓰기" 표시)을 포함해, 문법 검사로는 못 잡는 실제 동작을 브라우저로 확인한다."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _tab(page, tab_id: str):
    page.evaluate("id => selectTab(id)", tab_id)
    page.wait_for_function("id => currentTab === id", arg=tab_id)
    page.wait_for_load_state("networkidle")


def test_every_screen_opens_without_js_errors(login):
    """역할별로 보이는 모든 화면을 차례로 열어 페이지 JS 오류가 0건인지 — 화면 코드 회귀를 넓게 잡는다."""
    for role in ("admin", "manager", "member", "viewer"):
        page = login(role)
        tabs = page.evaluate("() => TABS.filter(t => t.roles.includes(ME.role)).map(t => t.id)")
        assert tabs, role
        for tab_id in tabs:
            _tab(page, tab_id)
            assert page.locator("#global-msg.error").count() == 0 or not page.locator("#global-msg.error").inner_text().strip(), (role, tab_id)
        assert page.errors == [], (role, page.errors[:3])  # type: ignore[attr-defined]


def test_home_and_role_menus(login):
    admin = login("admin")
    _tab(admin, "home")
    assert admin.locator("#content").get_by_text("처리할 일").first.is_visible()
    assert admin.locator('button[data-tab="admin"]').count() == 1
    member = login("member")
    assert member.locator('button[data-tab="admin"]').count() == 0
    assert member.locator('button[data-tab="cost"]').count() == 0


def test_menu_search_ctrl_k_jumps_and_respects_role(login):
    page = login("admin")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#menu-search-input", state="visible")
    page.fill("#menu-search-input", "비용")
    assert "비용" in page.locator("#menu-search-results li.active").inner_text()
    page.keyboard.press("Enter")
    page.wait_for_function("() => currentTab === 'cost'")
    assert page.locator("#menu-search").is_hidden()
    # 초성 검색
    page.click("#menu-search-btn")
    page.fill("#menu-search-input", "ㄱㅈ")
    assert "결재" in page.locator("#menu-search-results").inner_text()
    page.keyboard.press("Escape")
    # member에게는 비용 화면이 나오지 않고, 가이드 절은 나온다
    member = login("member")
    member.click("#menu-search-btn")
    member.fill("#menu-search-input", "비용")
    results = member.locator("#menu-search-results").inner_text()
    assert "화면\n비용" not in results and "가이드" in results


def test_help_button_opens_the_guide_section_for_the_screen(login):
    page = login("admin")
    _tab(page, "approvals")
    page.click("#screen-help-btn")
    page.wait_for_function("() => currentTab === 'manual'")
    target = page.locator(".manual-content .manual-target")
    target.wait_for(state="attached")
    assert "결재 처리" in target.inner_text()


def test_conversation_model_notice_order_and_select_spacing(login, live_server):
    from hair_platform import conversations

    conn = live_server["conn"]
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    conv = conversations.create_conversation(conn, admin_id, "E2E 대화")
    page = login("admin")
    _tab(page, "conversations")
    page.click(f'[data-open-conversation="{conv["id"]}"]')
    page.wait_for_selector("#conv-to-brief")
    order = page.evaluate("""() => {
        const notice = [...document.querySelectorAll('p.muted')].find(p => p.textContent.includes('이 모델 설정은'));
        const brief = document.getElementById('conv-to-brief');
        return notice && brief ? (notice.compareDocumentPosition(brief) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 : null;
    }""")
    assert order is True  # 모델 안내가 "업무 요청으로 전환"보다 위(2026-11-26 사용자 요청)
    notice = page.locator("p.muted", has_text="이 모델 설정은").inner_text()
    assert "서버가 자동 판별한" not in notice and "매니저 Agent의 모델" in notice  # 안내 정정
    gap = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('conv-model')).marginLeft)")
    assert gap > 0  # 공급자·모델 선택 상자가 붙어 보이지 않게


def test_pipeline_builder_required_tools_chip_add_and_remove(login, live_server):
    conn = live_server["conn"]
    page = login("admin")
    _tab(page, "pipelines")
    # 필수 도구로 이미 지정된 단계(공고·면접 설계) — 다른 단계의 "+ 필수 도구 추가" 목록에도 같은 이름이 있어 글자로 찾지 않는다
    card = page.locator('[data-required-tools*="채용 공고 점검기"]').first
    assert card.is_visible()
    stage_id = int(card.get_attribute("data-stage-id"))
    card.locator("select[data-add-required-tool]").select_option("인사 지표 계산기")
    page.wait_for_function(
        "sid => [...document.querySelectorAll('[data-required-tools]')].some(e => e.dataset.stageId == sid && "
        "JSON.parse(e.dataset.requiredTools).includes('인사 지표 계산기'))", arg=str(stage_id))
    import json

    assert json.loads(conn.execute("SELECT required_tools FROM pipeline_stages WHERE id = ?", (stage_id,)).fetchone()[0]) == [
        "채용 공고 점검기", "인사 지표 계산기"]
    page.locator(f'[data-stage-id="{stage_id}"] button[data-tool="인사 지표 계산기"]').click()
    page.wait_for_function(
        "sid => [...document.querySelectorAll('[data-required-tools]')].some(e => e.dataset.stageId == sid && "
        "!JSON.parse(e.dataset.requiredTools).includes('인사 지표 계산기'))", arg=str(stage_id))


def test_tool_pool_shows_write_badge_for_side_effect_tools(login, live_server):
    from hair_platform import pool_registry

    conn = live_server["conn"]
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    pool_registry.create_tool(conn, admin_id, "E2E 발송 도구", "메일을 보낸다", "mcp_remote",
                              {"url": "https://mcp.example.com", "transport": "streamable-http"})
    page = login("admin")
    _tab(page, "poolregistry")
    row = page.locator("tr", has_text="E2E 발송 도구")
    assert row.locator(".gate-badge", has_text="쓰기").is_visible()
    search_row = page.locator("tr", has_text="일반 웹 검색(Parallel Search)")
    assert search_row.locator(".gate-badge", has_text="쓰기").count() == 0


def test_integrations_shows_connector_sections_for_admin_only(login):
    admin = login("admin")
    _tab(admin, "integrations")
    text = admin.locator("#content").inner_text()
    for title in ("사내 DB(admin)", "메일 수신함(admin)", "캘린더(admin)", "코드 저장소(admin)"):
        assert title in text, title
    member = login("member")
    _tab(member, "integrations")
    assert "사내 DB(admin)" not in member.locator("#content").inner_text()
