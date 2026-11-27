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
    # 템플릿은 요약 한 줄로 접혀 있다(2026-11-27, 사용성 평가 5번) — 그 템플릿을 [편집]으로 펼친다.
    assert not card.is_visible()
    page.locator('[data-pipeline-template-card]', has=card).locator("[data-pl-toggle]").click()
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


def test_model_registry_benchmark_results_render(login, live_server):
    """성능 테스트 정교화(2026-11-27) — 예전 방식·빠른 측정·전체 점검 결과가 모두 화면에 그려지고, 버튼이 둘이다."""
    import json

    from hair_platform import model_registry

    conn = live_server["conn"]
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    old = model_registry.create_model(conn, admin_id, "ollama", "e2e-old-bench")
    new = model_registry.create_model(conn, admin_id, "ollama", "e2e-new-bench")
    conn.execute("INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, output_chars, "
                 "chars_per_second, recommended_tier) VALUES (?, ?, 'success', 9.7, 196, 20.2, 'precision_fast')", (old, admin_id))
    full = {"mode": "full", "load_s": 6.2, "default": {"runs": 3, "wall_s": 2.7, "gen_tps": 44.3},
            "think_off": {"runs": 3, "wall_s": 1.5, "gen_tps": 44.1}, "estimated_stage_s": 76.2,
            "estimate_basis": "입력 8,000토큰 읽기 + 출력 1,500토큰 생성",
            "checks": {"tools": {"passed": False, "reason": "모델이 도구 호출을 지원하지 않는다고 밝힘"},
                       "numbers": {"passed": True, "reason": "연봉·날짜·경력 그대로"}},
            "recommended_tiers": ["precision_fast"], "warnings": ["도구를 쓰는 Agent에 배정하지 마세요(도구 호출 점검 실패)"]}
    quick = {"mode": "quick", "default": {"runs": 3, "wall_s": 2.5, "gen_tps": 27.7}, "estimated_stage_s": 54.1,
             "recommended_tiers": ["standard", "precision_fast"], "warnings": []}
    for mode, details in (("full", full), ("quick", quick)):
        conn.execute("INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, output_chars, "
                     "chars_per_second, recommended_tier, mode, details) VALUES (?, ?, 'success', 2.5, 150, 60, ?, ?, ?)",
                     (new, admin_id, details["recommended_tiers"][0], mode, json.dumps(details, ensure_ascii=False)))
    conn.commit()
    page = login("admin")
    _tab(page, "modelregistry")
    # 요약 한 줄(2026-11-27 화면 개선) — 생성 속도와 점검 결과 요약, 설정은 접혀 있다
    new_row = page.locator(f'tr[data-mr-row="{new}"]').inner_text()
    assert "27.7 tok/s" in new_row and "점검 1/2" in new_row and "도구 호출" in new_row
    assert "예전 방식" in page.locator(f'tr[data-mr-row="{old}"]').inner_text()
    assert page.locator(f'[data-mr-detail="{new}"]').is_hidden()
    page.click(f'[data-mr-toggle="{new}"]')
    panel = page.locator(f'[data-mr-panel="{new}"]')
    assert panel.is_visible()
    assert page.locator(f'[data-run-benchmark="{new}"]').count() == 2  # 빠른 측정·전체 점검
    text = panel.inner_text()
    assert "빠른 측정" in text and "최근 전체 점검" in text and "도구를 쓰는 Agent에 배정하지 마세요" in text
    assert page.locator(f'[data-apply-benchmark-tier="{new}"]').get_attribute("data-tiers") == "standard,precision_fast"
    assert not page.errors  # type: ignore[attr-defined]


def test_model_settings_panel_saves_only_changed_fields(login, live_server):
    """설정 패널(2026-11-27) — 이름 붙은 칸을 고치면 '저장하지 않은 변경' 표시, 저장 한 번에 바뀐 항목만 저장."""
    from hair_platform import model_registry

    conn = live_server["conn"]
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    mid = model_registry.create_model(conn, admin_id, "ollama", "e2e-panel-model")
    conn.commit()
    page = login("admin")
    _tab(page, "modelregistry")
    page.click(f'[data-mr-toggle="{mid}"]')
    page.fill(f'[data-mr-field="context_window"][data-model-id="{mid}"]', "16384")
    page.check(f'[data-mr-tier][data-model-id="{mid}"][value="standard"]')
    assert "저장하지 않은 변경" in page.locator(f'[data-mr-dirty="{mid}"]').inner_text()
    page.click(f'[data-mr-save="{mid}"]')
    # 저장 뒤 화면을 다시 그려도 "저장했습니다" 안내가 남는다(2026-11-27 — 예전엔 다시 그리며 지웠다)
    page.wait_for_function("() => document.getElementById('global-msg').textContent.includes('저장했습니다')")
    assert "최적화 Tier·컨텍스트 한도" in page.locator("#global-msg").inner_text()
    row = conn.execute("SELECT context_window, optimization_tiers, sampling_options, price_input_per_1m FROM model_registry WHERE id = ?",
                       (mid,)).fetchone()
    assert row["context_window"] == 16384 and "standard" in row["optimization_tiers"]
    assert row["price_input_per_1m"] is None  # 건드리지 않은 단가는 저장하지 않는다
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE subject_type = 'model_registry' AND subject_id = ? "
                                          "ORDER BY id", (mid,))]
    assert "model_registry_sampling_options_changed" not in actions and "model_registry_context_window_changed" in actions
    page.wait_for_selector(f'[data-mr-panel="{mid}"]', state="visible")  # 다시 그려도 펼친 채로
    assert not page.errors  # type: ignore[attr-defined]


def test_llm_optimize_marks_models_that_cannot_call_tools(login, live_server):
    """LLM 설정 최적화(2026-11-27) — 도구를 쓰는 Agent에는 도구 호출을 못 하는 모델이 ✗로 고를 수 없게, 현재 모델이면 경고."""
    import json

    from hair_platform import model_registry

    conn = live_server["conn"]
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    no_tools = model_registry.create_model(conn, admin_id, "ollama", "e2e-no-tools")
    details = {"mode": "full", "capabilities": ["completion"], "estimated_stage_s": 76,
               "checks": {"tools": {"passed": False}, "numbers": {"passed": True}}, "recommended_tiers": ["precision_fast"]}
    conn.execute("INSERT INTO model_benchmarks (model_registry_id, tested_by, status, latency_seconds, mode, details) "
                 "VALUES (?, ?, 'success', 2.0, 'full', ?)", (no_tools, admin_id, json.dumps(details)))
    agent = conn.execute("SELECT id FROM agent_pool WHERE role_name = '채용 담당자'").fetchone()[0]
    conn.execute("UPDATE agent_pool SET llm_provider = 'ollama', llm_model = 'e2e-no-tools' WHERE id = ?", (agent,))
    conn.commit()
    page = login("admin")
    _tab(page, "agentoptimize")
    option = page.locator(f'.optimize-model-pick[data-agent-id="{agent}"] option', has_text="e2e-no-tools").first
    assert option.is_disabled() and "도구 호출 불가" in option.inner_text()
    row = page.locator("tr", has=page.locator(f'.optimize-apply-check[data-agent-id="{agent}"]')).first
    assert "쓸 수 없음" in row.inner_text()
    assert page.locator(f'.optimize-apply-check[data-agent-id="{agent}"]').get_attribute("data-changed") == "1"
    assert not page.errors  # type: ignore[attr-defined]


_LAYOUT_PROBE = r"""() => {
  const vw = window.innerWidth, content = document.getElementById('content');
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const inScroll = e => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
    const ox = getComputedStyle(p).overflowX; if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true; } return false; };
  const name = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + ' "' + (e.innerText || '').trim().slice(0, 30) + '"';
  return {
    overflow: document.documentElement.scrollWidth - vw,
    unwrappedTables: [...content.querySelectorAll('table')].filter(t => visible(t) && !inScroll(t)).map(name),
    plainButtons: [...content.querySelectorAll('button')].filter(b => visible(b) && !b.className).map(name),
    headerOnlyTables: [...content.querySelectorAll('table')].filter(t => visible(t) && t.querySelectorAll('tr').length <= 1).map(name),
  };
}"""


def test_every_screen_fits_a_phone_and_keeps_one_look(login):
    """화면 일관성(2026-11-27, 개선 계획 4-4) — 모든 화면을 폰 폭(390px)으로 열어 페이지가 옆으로 넘치지 않는지, 표는
    가로 스크롤 상자 안에 있는지, 모양 클래스 없는 버튼이 없는지, 머리글만 있는 빈 표가 없는지(빈 상태 문구를 보여야 함).
    실측: 이용 가이드가 2,282px, 연동 설정이 180px 넘쳤고 평가 기준의 빈 덮어쓰기 표는 머리글만 보였다."""
    for role in ("admin", "member"):
        page = login(role)
        page.set_viewport_size({"width": 390, "height": 860})
        tabs = page.evaluate("() => TABS.filter(t => t.roles.includes(ME.role)).map(t => t.id)")
        problems = {}
        for tab_id in tabs:
            _tab(page, tab_id)
            r = page.evaluate(_LAYOUT_PROBE)
            bad = {k: v for k, v in r.items() if (v > 0 if k == "overflow" else v)}
            if bad:
                problems[tab_id] = bad
        assert problems == {}, (role, problems)


def test_login_lands_on_home_and_empty_lists_say_what_to_do(login):
    """사용성 평가(2026-11-27) 1·3번 — 로그인 첫 화면은 홈(예전엔 빈 "대화" 표), 빈 목록은 다음 행동을 안내한다."""
    for role in ("admin", "member", "viewer"):
        page = login(role)
        assert page.evaluate("currentTab") == "home", role
    page = login("member")
    _tab(page, "mywork")
    empty = page.locator("#content tr.table-empty")
    assert "아직 맡은 일이 없습니다" in empty.inner_text()
    empty.locator("[data-go-tab=home]").click()
    page.wait_for_function("currentTab === 'home'")
    _tab(page, "conversations")
    assert "새 대화 시작" in page.locator("#content tr.table-empty").inner_text()
    assert not page.errors  # type: ignore[attr-defined]


def test_agent_rows_keep_destructive_actions_in_settings(login):
    """사용성 평가 6번 — 행마다 빨간 [오프보딩][삭제]가 [실행] 옆에 있던 것을 ⚙ 설정 창의 위험 구역으로 옮겼다."""
    page = login("admin")
    _tab(page, "agentpool")
    rows = page.locator("#content table tbody tr")
    assert rows.count() > 5
    assert page.locator("#content table [data-delete-agent]:visible, #content table [data-deprecate]:visible").count() == 0
    first = rows.first
    assert first.locator("[data-execute]").is_visible()
    first.locator("[data-open-agent-settings]").click()
    zone = page.locator("#agent-settings-overlay .as-danger")
    assert zone.locator("[data-delete-agent]").is_visible() and zone.locator("[data-deprecate]").is_visible()
    zone.locator("[data-deprecate]").click()
    page.locator(".confirm-modal-box").last.get_by_role("button").last.wait_for()
    assert page.locator(".confirm-modal-overlay").count() >= 2  # 설정 창 위에 확인 창
    assert not page.errors  # type: ignore[attr-defined]


def test_slow_screen_does_not_overwrite_the_screen_opened_after_it(login):
    """첫 화면을 홈으로 바꾼 뒤 드러난 경쟁 상태(2026-11-27) — 홈이 서버 응답을 기다리는 사이 다른 메뉴를 누르면, 늦게 온
    홈 응답이 새 화면을 홈으로 덮어썼다. 홈의 응답을 일부러 늦춰 재현한다."""
    import time as _time

    page = login("member")
    page.route("**/api/inbox", lambda route: (_time.sleep(1.0), route.continue_()))
    page.evaluate("selectTab('home')")  # 같은 탭이면 새로 그리기만 하므로 먼저 다른 화면으로
    page.evaluate("selectTab('manual'); selectTab('home'); setTimeout(() => selectTab('approvals'), 50)")
    page.wait_for_timeout(1800)
    assert page.evaluate("currentTab") == "approvals"
    text = page.locator("#content").inner_text()
    assert "내가 요청한 건" in text and "AI에게 일 맡기기" not in text


def test_pipeline_templates_are_summaries_opened_one_at_a_time(login):
    """사용성 평가 5번 — 템플릿 8개의 모든 단계가 한 페이지에 펼쳐져 높이 14,895px·입력칸 190개였다."""
    page = login("admin")
    _tab(page, "pipelines")
    cards = page.locator("[data-pipeline-template-card]")
    assert cards.count() >= 5
    assert page.locator(".pl-body:visible").count() == 0
    assert page.evaluate("document.documentElement.scrollHeight") < 3000
    assert "단계 · 사람 승인" in cards.first.inner_text()
    cards.nth(0).locator("[data-pl-toggle]").click()
    cards.nth(1).locator("[data-pl-toggle]").click()
    assert page.locator(".pl-body:visible").count() == 1  # 하나씩만 열린다
    assert cards.nth(1).locator(".pl-body").is_visible()
    page.evaluate("refresh()")  # 저장 뒤 다시 그려도 연 템플릿은 그대로
    page.wait_for_load_state("networkidle")
    assert page.locator("[data-pipeline-template-card]").nth(1).locator(".pl-body").is_visible()
    assert not page.errors  # type: ignore[attr-defined]


def test_pool_shows_tools_or_skills_and_tucks_rare_actions_away(login):
    """사용성 평가 5번 — 도구·스킬 목록과 등록 폼이 한 페이지에 다 있어 높이 11,010px·버튼 306개였다."""
    page = login("admin")
    _tab(page, "poolregistry")
    assert page.locator('[data-pool-sec="tools"]').is_visible() and not page.locator('[data-pool-sec="skills"]').is_visible()
    assert page.locator("[data-delete-tool]:visible").count() == 0  # 삭제는 ⋯ 안에
    page.locator('[data-pool-view="skills"]').click()
    assert page.locator('[data-pool-sec="skills"]').is_visible() and not page.locator('[data-pool-sec="tools"]').is_visible()
    row = page.locator('[data-pool-sec="skills"] tbody tr').first
    row.locator("details.row-more > summary").click()
    assert row.locator("[data-export-skill]").is_visible() and row.locator("[data-delete-skill]").is_visible()
    page.evaluate("refresh()")
    page.wait_for_load_state("networkidle")
    assert page.locator('[data-pool-sec="skills"]').is_visible()  # 새로 그려도 보던 목록 그대로
    assert not page.errors  # type: ignore[attr-defined]


def test_change_my_password_from_header(login, live_server, browser):
    """사용성 평가 13번 — 헤더의 내 계정 이름을 눌러 비밀번호를 바꾼다(e2e 전용 계정을 만들어 다른 테스트에 영향 없게)."""
    from hair_platform.auth import Role, create_user

    create_user(live_server["conn"], "e2e_pw_user", "old-pass-123", Role("member"))
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="ko-KR")
    try:
        page = ctx.new_page()
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda exc: page.errors.append(str(exc)))  # type: ignore[attr-defined]
        page.goto(live_server["url"])
        page.fill("#login-username", "e2e_pw_user")
        page.fill("#login-password", "old-pass-123")
        page.click("#login-form button[type=submit]")
        page.wait_for_selector("#app-view", state="visible")
        assert "(구성원)" in page.locator("#whoami").inner_text()  # 역할도 한국어로
        page.click("#whoami")
        page.fill("#mp-current", "old-pass-123")
        page.fill("#mp-new", "new-pass-456")
        page.fill("#mp-new2", "new-pass-45X")
        page.click("#my-password-form button[type=submit]")
        assert "다릅니다" in page.locator("#mp-msg").inner_text()
        page.fill("#mp-new2", "new-pass-456")
        page.click("#my-password-form button[type=submit]")
        page.wait_for_selector("#my-password-overlay", state="detached")
        assert "비밀번호를 바꿨습니다" in page.locator("#global-msg").inner_text()
        page.evaluate("selectTab('inbox')")  # 새 토큰으로 계속 쓸 수 있다
        page.wait_for_function("currentTab === 'inbox'")
        page.wait_for_load_state("networkidle")
        assert not page.locator("#global-msg.error").count() or not page.locator("#global-msg.error").inner_text().strip()
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        ctx.close()


def test_manual_shows_one_chapter_and_help_opens_the_right_one(login):
    """사용성 평가 10번 — 이용 가이드가 56,000자·높이 34,000px 한 페이지였다. 절 하나씩 보이고, ❓ 도움말은 그 절을 연다."""
    page = login("admin")
    _tab(page, "manual")
    visible = page.locator(".manual-chapter:visible")
    assert visible.count() == 1 and page.evaluate("document.documentElement.scrollHeight") < 12000
    page.locator('.manual-toc [data-manual-ch="trouble"]').click()
    assert page.locator('.manual-chapter[data-ch="trouble"]').is_visible()
    page.locator(".manual-pager button").first.click()  # ← 이전 절
    assert page.locator('.manual-chapter[data-ch="menu"]').is_visible()
    _tab(page, "evalconfig")
    page.click("#screen-help-btn")  # 평가 기준 화면의 도움말 → 6절 안의 "평가 기준 설정"
    page.wait_for_function("() => currentTab === 'manual'")
    target = page.locator(".manual-content .manual-target")
    target.wait_for()
    assert target.is_visible() and page.locator('.manual-chapter[data-ch="pipeline"]').is_visible()
    page.check("#manual-all")
    assert page.locator(".manual-chapter:visible").count() >= 9
    page.uncheck("#manual-all")
    assert not page.errors  # type: ignore[attr-defined]


def test_brief_progress_shows_steps_and_next_action(login, live_server):
    """사용성 평가 8번 — 업무 한 건의 요청 → 할 일 → 배정 → 결과물 → 결재를 한 화면에, 할 일마다 다음에 할 일."""
    conn = live_server["conn"]
    page = login("admin")
    ids = page.evaluate("""async () => {
      const post = (u, b) => fetch(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)}).then(r => r.json());
      const brief = await post('/api/briefs', {raw_text: 'E2E 진행 보기 업무'});
      const t = await post('/api/briefs/' + brief.id + '/tasks', {tasks: [{role_name: '실적 보고서 작성자', required_skills: []}, {role_name: '재무 분석가', required_skills: []}]});
      return {brief: brief.id, tasks: t.tasks.map(x => x.id)};
    }""")
    admin = conn.execute("SELECT id FROM users WHERE username = 'e2e_admin'").fetchone()[0]
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'agent', 1, ?, 'confirmed')",
                 (ids["tasks"][0], admin))
    conn.commit()
    _tab(page, "briefs")
    page.click(f'[data-viewtasks="{ids["brief"]}"]')
    stepper = page.locator(".brief-stepper")
    stepper.wait_for()
    steps = " ".join(stepper.inner_text().split())
    assert "배정 1/2" in steps and "결재 0/2" in steps
    rows = page.locator("#brief-detail table tbody tr")
    assert "작업 시작(실행)" in rows.nth(0).inner_text() and "배정하기" in rows.nth(1).inner_text()
    rows.nth(0).locator("[data-task-workspace]").click()
    page.locator("#task-detail").get_by_text("협업 워크스페이스").first.wait_for()
    assert not page.errors  # type: ignore[attr-defined]
