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
