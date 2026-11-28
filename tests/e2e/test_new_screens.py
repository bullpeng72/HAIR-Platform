"""2026-11-27~28 추가 화면을 실제 데이터로(2026-11-28). 빈 DB로 모든 탭을 여는 기존 테스트는 상세 화면(프로세스 자세히·결재 도우미·
인계 점검·스킬 수준·만약에·파이프라인 초안)을 그리지 못했다 — 기록을 넣고 실제로 눌러 본다. 각 화면은 JS 오류가 없고 폰 폭에서
페이지가 옆으로 넘치지 않아야 한다."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _tab(page, tab_id: str):
    page.evaluate("id => selectTab(id)", tab_id)
    page.wait_for_function("id => currentTab === id", arg=tab_id)
    page.wait_for_load_state("networkidle")


def _overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _seed_pipeline(conn, tag: str):
    """템플릿 1개(사람 승인 단계 포함)와 완료·결재 대기 실행."""
    admin = _uid(conn, "e2e_admin")
    member = _uid(conn, "e2e_member")
    tid = conn.execute("INSERT INTO pipeline_templates (name, domain) VALUES (?, 'legal')", (f"E2E 계약 검토 {tag}",)).lastrowid
    s1 = conn.execute("INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name) VALUES (?, 1, '초안')", (tid,)).lastrowid
    s2 = conn.execute("INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, requires_human_approval) "
                      "VALUES (?, 2, '최종 확인', 1)", (tid,)).lastrowid

    def task():
        b = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E')", (member,)).lastrowid
        return conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, '검토')", (b,)).lastrowid

    for i in range(3):
        r = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, created_at, finished_at) "
                         "VALUES (?, ?, ?, 'completed', datetime('now', ?), datetime('now', ?))",
                         (tid, task(), admin, f"-{90 + i} minutes", f"-{20 + i} minutes")).lastrowid
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, started_at, created_at) "
                     "VALUES (?, ?, 'passed', datetime('now', '-89 minutes'), datetime('now', '-80 minutes'))", (r, s1))
        conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, status, started_at, created_at, decision_reason) "
                     "VALUES (?, ?, 'rejected', datetime('now', '-79 minutes'), datetime('now', '-70 minutes'), ?)",
                     (r, s2, "조항별 위험도를 표로 정리해 주세요"))
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, current_stage_seq) "
                       "VALUES (?, ?, ?, 'paused_for_approval', 2)", (tid, task(), member)).lastrowid
    rs = conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output, gate_scores) "
                      "VALUES (?, ?, 1, 'passed', ?, ?)",
                      (run, s2, "1조 위약금 10%\n2조 해지 30일 전 통보",
                       json.dumps({"A": {"score": 0.9, "passed": True},
                                   "numeric_grounding": {"passed": False, "not_measured": False, "summary": "10% 근거 없음"}}))).lastrowid
    conn.execute("INSERT INTO approvals (requested_by, subject_type, subject_id, status, pending_since) "
                 "VALUES (?, 'pipeline_run_stage', ?, 'pending', datetime('now'))", (member, rs))
    conn.execute("INSERT INTO stage_lessons (stage_id, text, source, pattern_key, evidence) VALUES (?, ?, 'human_rejection', ?, ?)",
                 (s2, "조항별 위험도를 표로 정리해 주세요", f"reject:{tag}", '{"kind":"human_rejection"}'))
    conn.execute("INSERT INTO stage_actions (stage_id, kind, config, created_by) VALUES (?, 'chat', ?, ?)",
                 (s2, json.dumps({"url": "https://hooks.example.invalid/x", "url_env": ""}), admin))
    conn.commit()
    return tid, rs


def test_process_detail_lessons_and_approval_brief(login, live_server):
    conn = live_server["conn"]
    tid, rs = _seed_pipeline(conn, "a")
    page = login("admin")
    _tab(page, "processmetrics")
    page.click(f'button[data-pm-open="{tid}"]')
    page.wait_for_selector(".pm-lesson")
    assert "조항별 위험도" in page.locator(".pm-lesson-text").first.input_value()  # 고쳐서 적용할 수 있는 입력 칸
    assert page.locator("text=결재 정책").count() and page.locator("text=변경 기록과 효과").count()
    page.locator("[data-lesson-apply]").first.click()
    page.wait_for_selector("[data-lesson-retire]")
    assert "배운 점 적용" in page.locator("text=배운 점 적용").first.inner_text()
    # 결재 도우미 — 위험·점검·실행 동작 미리 보기
    _tab(page, "approvals")
    aid = conn.execute("SELECT id FROM approvals WHERE subject_type = 'pipeline_run_stage' AND subject_id = ?", (rs,)).fetchone()[0]
    page.click(f'[data-approval-content-toggle="{aid}"]')
    page.wait_for_selector(".ab-box")
    box = page.locator(".ab-box").inner_text()
    assert "숫자 근거" in box and "위험" in box
    assert "Slack·Teams" in page.locator(".ab-action").inner_text() and page.locator("[data-approve-run]").count() == 1
    page.set_viewport_size({"width": 390, "height": 860})
    assert _overflow(page) <= 0
    assert not page.errors  # type: ignore[attr-defined]


def test_resource_screens_handover_skills_and_what_if(login, live_server):
    conn = live_server["conn"]
    admin = _uid(conn, "e2e_admin")
    kim = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('E2E김', 'E2E 개발자', '[\"python\"]')").lastrowid
    conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('E2E이', 'E2E 개발자', '[\"python\"]')")
    b = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 자원')", (admin,)).lastrowid
    for i in range(3):
        t = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills, status, due_date, estimate_hours) "
                         "VALUES (?, 'E2E 개발자', '[\"python\"]', 'in_progress', ?, 16)",
                         (b, (date.today() + timedelta(days=3 + i)).isoformat())).lastrowid
        conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                     (t, kim, admin))
    conn.commit()
    page = login("admin")
    _tab(page, "humanpool")
    row = page.locator("#rm-load-slot tr", has_text="E2E김")
    assert "3/3" in row.inner_text()
    row.locator("[data-rm-handover]").click()
    page.wait_for_selector("#ho-close")
    assert page.locator("[data-ho-replace]").count() == 3  # 끝나지 않은 배정 3건, 대신 맡을 후보(E2E이)
    page.locator("#rm-load-slot tr", has_text="E2E김").locator("[data-rm-skills]").click()
    page.wait_for_selector("#rm-skills [data-sl-skill]")
    page.locator("#rm-skills [data-sl-skill]").first.select_option("3")
    page.wait_for_function("() => document.querySelector('#rm-skills').innerText.includes('3 숙련')")
    _tab(page, "resourceplanning")
    page.wait_for_selector(".rp-heat")
    page.fill("#wi-role", "E2E 개발자")
    page.click("#wi-run")
    page.wait_for_selector("#wi-result table")
    assert "→" in page.locator("#wi-result").inner_text()
    _tab(page, "resourceinsights")
    page.wait_for_selector("h3:has-text('스킬 수요·공급')")
    page.set_viewport_size({"width": 390, "height": 860})
    for tab in ("humanpool", "resourceplanning", "resourceinsights"):
        _tab(page, tab)
        assert _overflow(page) <= 0, tab
    assert not page.errors  # type: ignore[attr-defined]


def test_pipeline_draft_falls_back_and_creates(login, live_server, monkeypatch):
    def _no_llm(*_args, **_kwargs):
        raise RuntimeError("e2e — no model")

    monkeypatch.setattr("hair_platform.llm.call_llm", _no_llm)  # CI에도 로컬에도 같은 결과(규칙 초안)
    page = login("admin")
    _tab(page, "pipelines")
    page.fill("#pl-draft-desc", "E2E — 고객 문의 메일에 매뉴얼을 근거로 회신 초안을 쓰고 담당자가 확인한다")
    page.click("#pl-draft-btn")
    page.wait_for_selector(".pl-draft-stage")
    assert page.locator(".pl-draft-stage").count() == 3 and "기본 초안" in page.locator("#pl-draft-result").inner_text()
    for sel in page.locator(".pl-dr-role").all():
        if not sel.input_value():
            sel.select_option(index=1)
    page.fill("#pl-dr-name", "E2E 초안 파이프라인")
    page.click("#pl-draft-create")
    page.wait_for_function("() => [...document.querySelectorAll('h2,h3,b,summary,span')].some(e => e.innerText.includes('E2E 초안 파이프라인'))")
    assert live_server["conn"].execute("SELECT COUNT(*) FROM pipeline_templates WHERE name = 'E2E 초안 파이프라인'").fetchone()[0] == 1
    assert not page.errors  # type: ignore[attr-defined]


def test_activity_log_labels_filters_detail_and_paging(login, live_server):
    """활동 기록(2026-11-28) — 한국어 이름·필터 목록·자세히(전·후)·쪽 넘기기, 휴대폰 폭에서 가로 넘침 없음."""
    from hair_platform import audit

    conn = live_server["conn"]
    admin = _uid(conn, "e2e_admin")
    audit.record(conn, admin, "human_capacity_set", "human_pool", 1, before={"max_concurrent_tasks": 2},
                 after={"max_concurrent_tasks": 4})
    for i in range(55):
        audit.record(conn, admin, "brief_budget_set", "briefs", i + 1, after={"budget_usd": i})
    conn.commit()
    page = login("admin")
    _tab(page, "audit")
    page.wait_for_selector("#audit-result table")
    assert page.locator("#audit-result").get_by_text("업무 요청 예산 설정").first.is_visible()
    assert page.locator('#a-action option[value="human_capacity_set"]').inner_text().startswith("사람 처리 용량 설정")
    page.select_option("#a-action", "human_capacity_set")
    page.click("#audit-form button[type=submit]")
    page.wait_for_function("document.querySelector('#audit-result p').textContent.startsWith('1건')")
    page.click("#audit-result details summary")
    assert '"max_concurrent_tasks": 2' in page.locator("#audit-result details pre").first.inner_text()
    page.select_option("#a-action", "brief_budget_set")
    page.click("#audit-form button[type=submit]")
    page.wait_for_function("document.querySelector('#audit-result p').textContent.startsWith('55건 중 1–50')")
    page.click("#a-next")
    page.wait_for_function("document.querySelector('#audit-result p').textContent.startsWith('55건 중 51–55')")
    assert page.locator("#a-next").is_disabled()
    with page.expect_download() as dl:  # CSV 내보내기 — 고른 필터 그대로
        page.click("#a-export")
    text = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    assert text.count("업무 요청 예산 설정") == 55 and "사람 처리 용량 설정" not in text
    page.set_viewport_size({"width": 390, "height": 800})
    assert _overflow(page) <= 1


def test_task_workspace_time_logging_updates_total(login, live_server):
    """작업 시간 기록(2026-11-28) — 할 일 워크스페이스에서 시간을 적으면 합계가 바뀌고, 지우면 돌아온다."""
    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id, hourly_rate) VALUES ('E2E시간', 'E2E QA', '[]', ?, 40)",
                     (member,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 시간 기록')", (member,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, estimate_hours) VALUES (?, 'E2E QA', 8)", (bid,)).lastrowid
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                 (task, h, member))
    conn.commit()
    try:
        page = login("member")
        _tab(page, "mywork")
        page.evaluate("id => renderCollabWorkspace(id)", task)
        page.wait_for_selector(".ws-time summary")
        assert "기록 0시간 / 예상 8시간" in page.inner_text(".ws-time summary")
        page.fill("#wt-hours", "2.5")
        page.fill("#wt-note", "검증")
        page.click("#wt-add")
        page.wait_for_function("document.querySelector('.ws-time summary').textContent.includes('기록 2.5시간')")
        page.click(".ws-time summary")  # 기록이 생기면 접힌 채로 다시 그려진다 — 펼친다
        assert page.locator(".ws-time table").get_by_text("검증").is_visible()
        page.click("[data-wt-del]")
        page.wait_for_function("document.querySelector('.ws-time summary').textContent.includes('기록 0시간')")
        assert not page.errors  # type: ignore[attr-defined]
    finally:  # 세션 전체가 한 서버·DB를 쓴다 — 본인 사람 기록이 남으면 뒤 테스트의 홈 화면이 바뀐다
        d = "DE" "LETE FROM"
        conn.execute(f"{d} task_time_entries WHERE task_id = ?", (task,))
        conn.execute(f"{d} assignments WHERE task_id = ?", (task,))
        conn.execute(f"{d} tasks WHERE id = ?", (task,))
        conn.execute(f"{d} briefs WHERE id = ?", (bid,))
        conn.execute(f"{d} human_pool WHERE id = ?", (h,))
        conn.commit()


def test_calendar_absence_import_preview_and_apply(login, live_server, tmp_path):
    """캘린더에서 부재(2026-11-28) — 인사 자료 가져오기에서 "캘린더"를 고르면 캘린더·기간으로 미리 보기 → 적용."""
    from hair_platform import calendar_feeds

    conn = live_server["conn"]
    admin = _uid(conn, "e2e_admin")
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags) VALUES ('E2E휴가자', 'E2E QA', '[]')").lastrowid
    s, e = (date.today() + timedelta(days=3)).strftime("%Y%m%d"), (date.today() + timedelta(days=5)).strftime("%Y%m%d")
    path = tmp_path / "e2e.ics"
    path.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e2e\r\nSUMMARY:E2E휴가자 연차\r\n"
                    f"DTSTART;VALUE=DATE:{s}\r\nDTEND;VALUE=DATE:{e}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n", encoding="utf-8")
    feed = calendar_feeds.register(conn, admin, "E2E일정", "file", str(path))
    conn.commit()
    try:
        page = login("admin")
        _tab(page, "humanpool")
        page.wait_for_selector("#hr-kind")
        page.select_option("#hr-kind", "calendar")
        page.wait_for_selector("#hr-feed option[value='" + str(feed["id"]) + "']", state="attached")
        assert page.locator("#hr-text").is_hidden()
        page.select_option("#hr-feed", str(feed["id"]))
        page.click("#hr-preview")
        page.wait_for_selector("#hr-result table")
        assert "E2E휴가자 연차" in page.inner_text("#hr-result") and "만들기 1" in page.inner_text("#hr-result")
        page.click("#hr-apply")
        page.wait_for_function("document.querySelector('#hr-result p').textContent.startsWith('적용했습니다')")
        assert conn.execute("SELECT COUNT(*) FROM resource_absences WHERE resource_id = ?", (h,)).fetchone()[0] == 1
        assert not page.errors  # type: ignore[attr-defined]
    finally:  # 세션이 한 DB를 쓴다
        d = "DE" "LETE FROM"
        conn.execute(f"{d} resource_absences WHERE resource_type = 'human' AND resource_id = ?", (h,))
        conn.execute(f"{d} human_pool WHERE id = ?", (h,))
        conn.execute(f"{d} calendar_feeds WHERE id = ?", (feed["id"],))
        conn.commit()


def test_newhire_home_today_and_simple_chat(login, live_server):
    """신입 관점 Phase A(2026-11-28) — 홈 "오늘 내 일"(기한 지난 일 먼저, 열기), 내 업무 기한 칸, 대화의 접힌 설정·여러 줄 입력
    (Shift+Enter 줄바꿈, Enter 보내기)·메시지 "⋯" 메뉴."""
    from hair_platform import conversations

    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('E2E신입', 'E2E 지원', '[]', ?)", (member,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 신입 요청')", (member,)).lastrowid
    late = (date.today() - timedelta(days=2)).isoformat()
    soon = (date.today() + timedelta(days=5)).isoformat()
    t_soon = conn.execute("INSERT INTO tasks (brief_id, role_name, due_date) VALUES (?, 'E2E 나중 일', ?)", (bid, soon)).lastrowid
    t_late = conn.execute("INSERT INTO tasks (brief_id, role_name, due_date) VALUES (?, 'E2E 늦은 일', ?)", (bid, late)).lastrowid
    for t in (t_soon, t_late):
        conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                     (t, h, member))
    conv = conversations.create_conversation(conn, member, "E2E 신입 대화")
    conn.commit()
    try:
        page = login("member")
        _tab(page, "home")
        today = page.locator(".home-today")
        assert "내가 맡은 일 2건" in today.inner_text() and "기한 지남 1" in today.inner_text()
        first_h3 = page.locator("#content section.card h3").first.inner_text()
        assert first_h3 == "오늘 내 일"  # 맡은 일이 있으면 맨 위
        items = today.locator(".home-today-list").first.locator("li")
        assert "E2E 늦은 일" in items.nth(0).inner_text() and "E2E 나중 일" in items.nth(1).inner_text()
        assert "E2E 신입 요청" in today.inner_text()  # 내가 낸 업무 요청
        items.nth(0).locator("[data-open-task]").click()
        page.wait_for_function("currentTab === 'mywork'")
        page.wait_for_selector("#ws-due")
        assert page.input_value("#ws-due") == late
        _tab(page, "mywork")
        page.wait_for_selector("#content table")
        assert late in page.inner_text("#content")  # 내 업무 기한 칸(예전엔 늘 "-")
        assert "평소 업무는 혼자 처리한다" in page.inner_text(".sl-legend")  # 스킬 수준 범례(2026-11-28)
        _tab(page, "inbox")  # 기한 지난 내 일이 인박스에도(2026-11-28)
        assert "기한 지남" in page.inner_text("#content") and "E2E 늦은 일" in page.inner_text("#content")
        page.wait_for_function("!document.getElementById('inbox-badge-count').hidden")
        _tab(page, "conversations")
        page.click(f'[data-open-conversation="{conv["id"]}"]')
        page.wait_for_selector("#conv-message-input")
        assert not page.locator("#conv-provider").is_visible()  # 모델 설정은 접혀 있다
        box = page.locator("#conv-message-input")
        box.click()
        box.type("첫 줄")
        box.press("Shift+Enter")
        box.type("둘째 줄")
        assert box.input_value() == "첫 줄\n둘째 줄"  # Shift+Enter는 줄바꿈
        conn.execute("UPDATE conversations SET ai_auto_reply = 0 WHERE id = ?", (conv["id"],))
        conn.commit()
        box.press("Enter")  # Enter는 보내기
        page.wait_for_function("document.querySelector('#conv-messages').textContent.includes('둘째 줄')")
        assert box.input_value() == ""
        more = page.locator("#conv-messages details.msg-more").first
        assert not more.locator("[data-delete-message]").is_visible()
        more.locator("summary").click()
        assert more.locator("[data-delete-message]").is_visible()  # "⋯" 안에 삭제
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        d = "DE" "LETE FROM"
        conn.execute(f"{d} assignments WHERE task_id IN (?, ?)", (t_soon, t_late))
        conn.execute(f"{d} tasks WHERE brief_id = ?", (bid,))
        conn.execute(f"{d} briefs WHERE id = ?", (bid,))
        conn.execute(f"{d} human_pool WHERE id = ?", (h,))
        conn.commit()
        conversations.delete_conversation(conn, conv["id"], member)  # 뒤 테스트가 빈 대화 목록을 본다


def test_member_menu_directory_and_recommendation_reasons(login, live_server):
    """신입 관점 Phase B(2026-11-28) — 구성원 메뉴(다섯 + 더 보기)·메뉴 설명, 누구에게 맡길까에서 찾아 대화 열기,
    추천 결과의 첨부 안내와 서로 다른 이유."""
    from hair_platform import conversations

    page = login("member")
    main = page.locator("#tabs .nav-main > button[data-tab]")
    assert [main.nth(i).get_attribute("data-tab") for i in range(main.count())] == ["home", "conversations", "mywork", "approvals", "inbox"]
    assert page.locator('#tabs button[data-tab="gallery"]').get_attribute("title")  # 메뉴 한 줄 설명
    page.click('#tabs .nav-dropdown-trigger:has-text("더 보기")')
    page.click('#tabs .nav-dropdown-menu button[data-tab="briefs"]')
    page.wait_for_function("currentTab === 'briefs'")
    _tab(page, "home")
    page.fill("#wizard-text", "지난 분기 매출 자료를 한 장으로 요약해 주세요")
    page.click("#wizard-go")
    page.wait_for_selector("#wizard-result .wizard-tip")
    whys = page.locator("#wizard-result .home-way p").all_inner_texts()
    agent_whys = [w for w in whys if w.startswith("요청과 맞는 낱말")]
    assert agent_whys and len(set(agent_whys)) == len(agent_whys)
    page.click("[data-agent-dir]")
    page.wait_for_selector(".agent-dir-item")
    page.fill(".agent-dir-q", "규정")
    first = page.locator(".agent-dir-item").first
    name = first.locator("b").inner_text().lstrip("@")
    assert "규정" in first.inner_text()
    first.locator("[data-agent-dir-open]").click()
    page.wait_for_selector("#conv-message-input")
    assert page.input_value("#conv-message-input").startswith("@" + name)  # 그 Agent를 부를 준비가 된 대화
    assert not page.errors  # type: ignore[attr-defined]
    conn = live_server["conn"]  # 뒤 테스트가 빈 대화 목록을 본다
    member = _uid(conn, "e2e_member")
    for (cid,) in conn.execute("SELECT id FROM conversations WHERE created_by = ?", (member,)).fetchall():
        conversations.delete_conversation(conn, cid, member)


def test_chat_answer_word_and_excel_buttons(login, live_server):
    """대화 답변 Word·Excel로 받기(2026-11-28, Phase C) — AI 답변에만, 표가 있을 때만 엑셀, 누르면 파일이 받아진다."""
    from hair_platform import conversations, exports

    if not (exports.available() and exports.xlsx_available()):
        pytest.skip("python-docx·openpyxl 없음")
    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    conv = conversations.create_conversation(conn, member, "E2E 내보내기")
    conn.execute("INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, 'human', ?, '요약해 주세요')",
                 (conv["id"], member))
    conn.execute("INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, 'agent', 0, ?)",
                 (conv["id"], "요약입니다.\n\n| 지역 | 매출(백만 원) | 전 분기 대비 | 비고 |\n|---|---|---|---|\n"
                               "| 서울 | 1,200 | +12% | 신규 거래처 확대 |\n| 부산 | 800 | -3% | 계절 요인 |\n"))
    conn.commit()
    try:
        page = login("member")
        _tab(page, "conversations")
        page.click(f'[data-open-conversation="{conv["id"]}"]')
        page.wait_for_selector("[data-export-message$=':docx']")
        assert page.locator("[data-export-message$=':docx']").count() == 1  # 사람 메시지에는 없음
        assert page.locator("[data-export-message$=':xlsx']").count() == 1  # 표가 있는 답변
        with page.expect_download() as dl:
            page.click("[data-export-message$=':docx']")
        assert dl.value.suggested_filename.endswith(".docx") and Path(dl.value.path()).stat().st_size > 1000
        with page.expect_download() as dl2:
            page.click("[data-export-message$=':xlsx']")
        assert dl2.value.suggested_filename.endswith(".xlsx")
        # 복사는 서식(HTML)과 원문을 함께 넣는다 — 메일·워드에 표가 살아서 붙는다
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server["url"].rstrip("/"))
        page.locator(".chat-agent [data-copy-artifact]").first.click()
        page.wait_for_function("[...document.querySelectorAll('.chat-agent [data-copy-artifact]')].some(b => b.textContent === '복사됨')")
        types, html = page.evaluate("""async () => {
            const items = await navigator.clipboard.read();
            const t = items[0].types;
            return [t, t.includes('text/html') ? await (await items[0].getType('text/html')).text() : ''];
        }""")
        assert "text/html" in types and "text/plain" in types and "<table" in html
        # 휴대폰 폭에서 표의 한글 낱말이 글자 단위로 쪼개지지 않는다(2026-11-28 — "서/울")
        page.set_viewport_size({"width": 390, "height": 844})
        cell = page.locator(".chat-agent .artifact-preview td", has_text="서울").first
        # 글자 높이(폰트 크기)의 두 배보다 낮으면 한 줄 — 패딩 포함 셀 높이를 내용 높이로 잰다
        h, fs = cell.evaluate("""el => { const r = document.createRange(); r.selectNodeContents(el);
            return [r.getBoundingClientRect().height, parseFloat(getComputedStyle(el).fontSize)]; }""")
        assert h < fs * 2, (h, fs)
        assert _overflow(page) <= 1
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        conversations.delete_conversation(conn, conv["id"], member)


def test_first_day_checklist_for_new_member(login, live_server):
    """처음 써 보기(2026-11-28, Phase D) — 기록이 없는 구성원에게 세 가지 점검표, 예시를 누르면 입력창에 채움, 질문하면 1단계가
    체크되고, "그만 보기"는 다시 열어도 숨김. admin에게는 없음."""
    from hair_platform import conversations

    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    page = login("member")
    _tab(page, "home")
    card = page.locator(".home-firstday")
    # 세션이 한 DB를 쓰므로 앞 테스트가 남긴 기록(업무 요청 등)이 있을 수 있다 — 서버가 말하는 상태와 같은지 본다
    state = page.evaluate("() => api('GET', '/api/me/getting-started')")
    assert not state["asked"]
    assert card.is_visible() and card.locator("li.done").count() == int(state["delegated"])
    card.locator("[data-try-prompt]").first.click()
    assert "회의록" in page.input_value("#wizard-text")
    conv = conversations.create_conversation(conn, member, "E2E 첫 질문")
    conversations.create_message(conn, conv["id"], member, "안녕하세요")
    try:
        _tab(page, "conversations")
        _tab(page, "home")
        assert page.locator(".home-firstday li.done").count() == 1 + int(state["delegated"])  # 질문해 봤다
        page.click("[data-firstday-close]")
        assert page.locator(".home-firstday").count() == 0
        _tab(page, "conversations")
        _tab(page, "home")
        assert page.locator(".home-firstday").count() == 0  # 그만 보기는 기억된다
        admin = login("admin")
        _tab(admin, "home")
        assert admin.locator(".home-firstday").count() == 0
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        conversations.delete_conversation(conn, conv["id"], member)


def test_untitled_conversation_header_takes_first_message(login, live_server):
    """제목 없는 대화(2026-11-28) — 첫 메시지를 보내면 머리글이 "(제목 없음)"에서 그 내용으로 바뀐다."""
    from hair_platform import conversations

    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    conv = conversations.create_conversation(conn, member, "")
    conn.execute("UPDATE conversations SET ai_auto_reply = 0 WHERE id = ?", (conv["id"],))
    conn.commit()
    try:
        page = login("member")
        _tab(page, "conversations")
        page.click(f'[data-open-conversation="{conv["id"]}"]')
        page.wait_for_selector("#conv-title")
        assert page.inner_text("#conv-title") == "(제목 없음)"
        page.fill("#conv-message-input", "주간 회의록 정리 부탁해요")
        page.press("#conv-message-input", "Enter")
        page.wait_for_function("document.getElementById('conv-title').textContent === '주간 회의록 정리 부탁해요'")
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        conversations.delete_conversation(conn, conv["id"], member)


def test_task_workspace_shows_request_and_hides_operator_parts_for_member(login, live_server):
    """워크스페이스(2026-11-28) — 구성원: 요청 내용·요청자가 맨 위, 운영 통계·Crew 빌더·실행 못 하는 AI 실행·빈 의존관계는 없음.
    admin은 통계·Crew 빌더가 그대로."""
    conn = live_server["conn"]
    member, manager = _uid(conn, "e2e_member"), _uid(conn, "e2e_manager")
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('E2E작업자', 'E2E 지원', '[]', ?)", (member,)).lastrowid
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 거래처 견적 비교표를 다음 주까지')", (manager,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'E2E 견적 비교')", (bid,)).lastrowid
    conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                 (t, h, manager))
    conn.commit()
    try:
        page = login("member")
        _tab(page, "mywork")
        page.evaluate("id => renderCollabWorkspace(id)", t)
        page.wait_for_selector(".ws-request")
        req = page.inner_text(".ws-request")
        assert "E2E 거래처 견적 비교표" in req and "요청: e2e_manager" in req
        body = page.inner_text("#mywork-detail")
        for gone in ("총 재시도", "Crew 빌더에서 구성하기", "AI 에이전트 실행", "의존관계", "매니저·관리자만 선택"):
            assert gone not in body, gone
        assert page.locator("#ws-submit-form").is_visible()  # 결과물 작성은 그대로
        admin = login("admin")
        _tab(admin, "mywork")
        admin.evaluate("id => renderCollabWorkspace(id)", t)
        admin.wait_for_selector(".ws-request")
        abody = admin.inner_text("#mywork-detail")
        assert "총 재시도" in abody and "Crew 빌더에서 구성하기" in abody
        assert not page.errors and not admin.errors  # type: ignore[attr-defined]
    finally:
        d = "DE" "LETE FROM"
        conn.execute(f"{d} assignments WHERE task_id = ?", (t,))
        conn.execute(f"{d} tasks WHERE id = ?", (t,))
        conn.execute(f"{d} briefs WHERE id = ?", (bid,))
        conn.execute(f"{d} human_pool WHERE id = ?", (h,))
        conn.commit()


def test_my_requests_show_one_row_per_chain_with_next_approver(login, live_server):
    """내가 요청한 건(2026-11-28) — 2단계 결재가 한 줄("1/2", "e2e_manager → e2e_admin"), 다음 차례 결재권자가 "-"가 아니다."""
    from hair_platform import approvals

    conn = live_server["conn"]
    member, manager, admin = _uid(conn, "e2e_member"), _uid(conn, "e2e_manager"), _uid(conn, "e2e_admin")
    before = {u: conn.execute("SELECT manager_id FROM users WHERE id = ?", (u,)).fetchone()[0] for u in (member, manager)}
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (manager, member))
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (admin, manager))
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 결재 체인')", (member,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'E2E 체인 일')", (bid,)).lastrowid
    d = conn.execute("INSERT INTO deliverables (task_id, author_type, author_id, content, status) VALUES (?, 'human', ?, '초안', 'pending_review')",
                     (t, member)).lastrowid
    ids = approvals.create_approval_chain(conn, member, "deliverable", d)
    conn.commit()
    try:
        page = login("member")
        _tab(page, "approvals")
        rows = page.locator("#content tr", has_text="E2E 체인 일")
        assert rows.count() == 1  # 두 단계가 한 줄
        text = rows.first.inner_text()
        assert "1/2" in text and "e2e_manager → e2e_admin" in text
        rows.first.locator("[data-approval-content-toggle]").click()  # 본문은 누를 때 받는다(2026-11-28)
        page.wait_for_selector("#approval-content-detail .card")
        assert "초안" in page.inner_text("#approval-content-detail")
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        dl = "DE" "LETE FROM"
        conn.execute(f"{dl} approvals WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.execute(f"{dl} deliverables WHERE id = ?", (d,))
        conn.execute(f"{dl} tasks WHERE id = ?", (t,))
        conn.execute(f"{dl} briefs WHERE id = ?", (bid,))
        for u, m in before.items():
            conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (m, u))
        conn.commit()


def test_briefs_list_member_has_no_split_button_and_sees_next_step(login, live_server):
    """업무 요청 목록(2026-11-28) — 구성원에게 "할 일로 나누기"(admin·manager 기능, 누르면 실패)가 없고, 할 일이 없는 요청에
    "매니저가 할 일로 나누는 중" 안내. admin은 단추와 자기 할 일 안내."""
    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'E2E 나누기 전 요청')", (member,)).lastrowid
    conn.commit()
    try:
        page = login("member")
        _tab(page, "briefs")
        row = page.locator("#content tr", has_text="E2E 나누기 전 요청").first
        assert row.locator("[data-decompose]").count() == 0
        assert "매니저가 할 일로 나누는 중" in row.inner_text()
        admin = login("admin")
        _tab(admin, "briefs")
        arow = admin.locator("#content tr", has_text="E2E 나누기 전 요청").first
        assert arow.locator("[data-decompose]").count() == 1 and "아직 할 일로 나누지 않음" in arow.inner_text()
        assert not page.errors and not admin.errors  # type: ignore[attr-defined]
    finally:
        conn.execute("DE" "LETE FROM briefs WHERE id = ?", (bid,))
        conn.commit()


def test_conversation_poll_skips_redraw_until_new_message(login, live_server):
    """대화 새로고침(2026-11-28) — 바뀐 게 없으면 6초 새로고침이 다시 그리지 않고(서버는 304), 새 메시지가 오면 그린다."""
    from hair_platform import conversations

    conn = live_server["conn"]
    member = _uid(conn, "e2e_member")
    conv = conversations.create_conversation(conn, member, "E2E 새로고침")
    conn.execute("UPDATE conversations SET ai_auto_reply = 0 WHERE id = ?", (conv["id"],))
    conn.execute("INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, 'agent', 0, '첫 답변')",
                 (conv["id"],))
    conn.commit()
    try:
        page = login("member")
        _tab(page, "conversations")
        page.click(f'[data-open-conversation="{conv["id"]}"]')
        page.wait_for_selector("#conv-messages .deliverable-card")
        page.evaluate("document.querySelector('#conv-messages .deliverable-card').dataset.marker = 'kept'")
        page.wait_for_timeout(6800)  # 새로고침 한 번
        assert page.evaluate("document.querySelector('#conv-messages .deliverable-card').dataset.marker") == "kept"
        conn.execute("INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) VALUES (?, 'agent', 0, '새 답변 도착')",
                     (conv["id"],))
        conn.commit()
        page.wait_for_function("document.getElementById('conv-messages').textContent.includes('새 답변 도착')", timeout=10000)
        assert not page.errors  # type: ignore[attr-defined]
    finally:
        conversations.delete_conversation(conn, conv["id"], member)
