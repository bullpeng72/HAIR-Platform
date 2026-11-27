"""P2-8 화면 복잡도(2026-11-24 — "go ahead"): AI에게 일 맡기기 마법사 + 자주 쓰는 탭만 기본 표시."""

from __future__ import annotations

import re

import pytest

from hair_platform import db, webui, wizard


@pytest.fixture(scope="module")
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _top(conn, text, role="member"):
    r = wizard.recommend(conn, text, role)["recommendations"]
    assert sum(1 for x in r if x["recommended"]) == 1 and r[0]["recommended"]
    return r[0], r


@pytest.mark.parametrize("text,template", [
    ("3분기 부서 예산 집행 실적을 분석해 경영진 보고서를 써 줘. NPV도 계산해서 투자 의견 포함", "재무 분석 보고"),
    ("A사 구매팀장에게 보낼 후속 제안 메일 써 줘, 다음 주 데모 일정 잡기", "영업 제안 메일"),
    ("유지보수 계약서의 손해배상·해지 조항 검토해 줘", "계약서 1차 검토"),
    ("로그인 API에 비밀번호 재설정 기능을 구현해 줘", "소프트웨어 개발"),
])
def test_domain_work_goes_to_matching_pipeline(conn, text, template):
    top, _ = _top(conn, text)
    assert top["kind"] == "pipeline" and template in top["template_name"]
    assert top["template_id"] and "자동 진행" in top["why"]


def test_recurring_work_recommends_schedule_and_flags_member_permission(conn):
    top, recs = _top(conn, "매주 월요일 지난주 실적 보고서 만들어 줘")
    assert top["kind"] == "schedule" and top["allowed"] is False and "admin·manager" in top["note"]
    assert any(r["kind"] == "pipeline" and "실적 보고" in r["template_name"] for r in recs)
    top_mgr, _ = _top(conn, "매주 월요일 지난주 실적 보고서 만들어 줘", role="manager")
    assert top_mgr["allowed"] is True and top_mgr["note"] is None


def test_short_question_prefers_quick_chat(conn):
    top, _ = _top(conn, "OKR이랑 KPI 차이가 뭐야?")
    assert top["kind"] == "chat"


def test_agent_recommendation_has_mentionable_agent(conn):
    _, recs = _top(conn, "회의록 정리해 줘")
    agents = [r for r in recs if r["kind"] == "agent"]
    assert agents and agents[0]["agent_id"] and agents[0]["role_name"] == "PM"
    assert recs[-1]["kind"] in ("chat", "agent")  # 대화 기본 AI는 늘 대안으로 들어간다
    assert any(r["kind"] == "chat" for r in recs)


def test_empty_text_rejected(conn):
    with pytest.raises(ValueError):
        wizard.recommend(conn, "   ", "member")


def test_wizard_api(client_and_conn):
    client, _conn = client_and_conn
    assert client.post("/login", json={"username": "member1", "password": "memberpass"}).status_code == 200
    r = client.post("/api/wizard/recommend", json={"text": "유지보수 계약서 손해배상 조항 검토"})
    assert r.status_code == 200 and r.json()["recommendations"][0]["kind"] == "pipeline"
    assert client.post("/api/wizard/recommend", json={"text": " "}).status_code == 400


def test_every_tab_is_reachable_from_the_workflow_menu():
    """메뉴 구조(2026-11-27, 사용성 평가 7번) — 일하기·검토·결재·현황(▾)·설정(▾)과 헤더(❓ 도움말 → 이용 가이드, ⚙ 내 설정 →
    연동 설정)로 바꾼 뒤에도 어느 화면도 메뉴에서 빠지지 않는다(예전 "더 보기" 접기는 없앴다)."""
    html = webui.INDEX_HTML
    tab_ids = set(re.findall(r'\{ id: "([a-z_]+)", label:', html))

    def ids(const):
        body = re.search(r"const " + const + r" = \[([^\]]*)\]", html).group(1)  # pyright: ignore[reportOptionalMemberAccess]
        return re.findall(r'"([a-z_]+)"', body)

    work, review, status = ids("NAV_WORK"), ids("NAV_REVIEW"), ids("NAV_STATUS")
    settings = set(re.findall(r'"([a-z_]+)"', html[html.index("const SETTINGS_GROUPS"):html.index("const NAV_WORK")])) - {"__divider__"}
    header = {"manual", "integrations"}  # ❓ 도움말·🔍 메뉴 찾기 / ⚙ 내 설정
    reachable = set(work) | set(review) | set(status) | settings | header
    assert tab_ids <= reachable, tab_ids - reachable
    assert work[0] == "home" and "inbox" in review and "approvals" in review
    assert not ({"manual", "integrations"} & (set(work) | set(review) | set(status)))
    assert 'id="my-settings-btn"' in html and "ESSENTIAL_TABS" not in html
    assert 'id="wizard-text"' in html and "_runWizard" in html


def test_wizard_pipeline_action_chain_for_manager(client_and_conn):
    """화면의 "업무로 만들고 바로 실행"이 부르는 순서 그대로: 업무 요청 → 할 일 → 파이프라인 실행."""
    from unittest.mock import patch

    client, conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    text = "유지보수 계약서의 손해배상·해지 조항 검토해 줘"
    rec = client.post("/api/wizard/recommend", json={"text": text}).json()["recommendations"][0]
    brief = client.post("/api/briefs", json={"raw_text": text}).json()
    tasks = client.post(f"/api/briefs/{brief['id']}/tasks",
                        json={"tasks": [{"role_name": rec["template_name"], "required_skills": []}]}).json()
    task_id = tasks["tasks"][0]["id"]
    with patch("hair_platform.pipeline_runtime.run_pipeline_stage_job"):
        r = client.post(f"/api/tasks/{task_id}/execute-pipeline", json={"pipeline_template_id": rec["template_id"]})
    assert r.status_code == 202, r.text
    run = conn.execute("SELECT pipeline_template_id, status FROM pipeline_runs WHERE task_id = ?", (task_id,)).fetchone()
    assert run["pipeline_template_id"] == rec["template_id"]


def test_settings_menu_is_one_row_of_group_dropdowns():
    """2026-11-25(사용자 요청 — admin 메뉴가 그룹마다 한 줄씩 펼쳐져 화면을 많이 차지): "설정" 한 줄 + 그룹 드롭다운."""
    html = webui.INDEX_HTML
    build = html[html.index("function buildNav() {"): html.index("function _closeNavDropdowns()")]
    assert 'settingsTag.textContent = "설정"' in build
    assert 'document.createElement(group.collapsible ? "details" : "div")' not in build  # 그룹별 줄(예전 방식) 없음
    assert "nav-dropdown-menu" in build and "menu.hidden = true" in build
    assert 'ev.key === "Escape"' in html and '.closest(".nav-dropdown")' in html  # 바깥 클릭·Esc로 닫힘
    assert "_syncNavDropdowns();  // 설정 그룹 버튼에 지금 화면 표시" in html
    assert 'badge.textContent = "관리자 전용"' not in html and 'btn.title = "관리자 전용"' in html


def test_wizard_input_is_full_width_with_button_below():
    """2026-11-25(사용자 보고 — 입력창이 한쪽에 치우치고 추천 받기 버튼이 붙어 어긋남)."""
    html = webui.INDEX_HTML
    assert '<div class="wizard-box"><textarea id="wizard-text"' in html
    assert '<div class="wizard-actions">' in html and ".wizard-box textarea { width: 100%;" in html
    assert '<textarea id="wizard-text" rows="2" style="flex:1' not in html
    assert '(ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); wizardBtn.click(); }' in html


def test_settings_dropdown_menu_stays_on_one_line_when_it_fits():
    """2026-11-25(사용자 요청 — 리소스 관리 하위 메뉴 7개가 640px 상한 때문에 두 줄로 접힘)."""
    html = webui.INDEX_HTML
    assert "max-width: min(92vw, 640px)" not in html
    assert "max-width: calc(100vw - 1.5rem)" in html
    assert "menu.getBoundingClientRect().right - (window.innerWidth - 12)" in html  # 오른쪽 넘침은 왼쪽으로 당김
