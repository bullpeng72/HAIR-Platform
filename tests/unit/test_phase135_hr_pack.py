"""인사(HR) 업무 영역 템플릿(2026-11-26, P1-9 — "인사 템플릿 없음")."""

from __future__ import annotations

import json
import types

import pytest

from hair_platform import db, domain_packs, wizard


def _tool(source: str):
    mod = types.ModuleType("hr_tool")
    eval(compile(source, "<tool>", "exec"), mod.__dict__)  # noqa: S307 — 저장소 안의 고정 소스(플랫폼도 같은 방식)
    return mod.tool_fn


@pytest.fixture(scope="module")
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


@pytest.mark.parametrize("hired, days", [
    ("2026-03-02", 8),    # 1년 미만 — 개근 1개월마다 1일
    ("2025-12-27", 10),   # 11개월 미만(10개월)
    ("2025-11-26", 15),   # 1년
    ("2024-11-26", 15),   # 2년
    ("2023-11-26", 16),   # 3년 — 최초 1년 초과 2년마다 1일
    ("2022-11-26", 16),   # 4년
    ("2021-11-26", 17),   # 5년
    ("2000-01-01", 25),   # 상한 25일
])
def test_annual_leave_follows_labor_standards_act_article_60(hired, days):
    out = json.loads(_tool(domain_packs.HR_METRICS_SOURCE)(
        json.dumps({"annual_leave": {"hire_date": hired, "as_of": "2026-11-26"}})))
    assert out["연차"]["발생 연차(일)"] == days and "제60조" in out["연차"]["근거"]


def test_turnover_funnel_and_time_to_hire():
    out = json.loads(_tool(domain_packs.HR_METRICS_SOURCE)(json.dumps({
        "turnover": {"start_headcount": 120, "end_headcount": 130, "leavers": 8, "hires": 18, "voluntary_leavers": 7},
        "funnel": {"지원": 320, "서류 합격": 60, "면접": 25, "최종 합격": 6, "입사": 5},
        "time_to_hire_days": [30, 45, 21, 60],
    }, ensure_ascii=False)))
    assert out["이직률"]["평균 인원"] == 125 and out["이직률"]["이직률(%)"] == 6.4
    assert out["이직률"]["자발적 이직률(%)"] == 5.6 and out["이직률"]["입사율(%)"] == 14.4
    assert out["채용_퍼널"]["전체 전환율(%)"] == 1.56 and out["채용_퍼널"]["가장 약한 구간"] == "지원 → 서류 합격"
    assert out["채용_소요일"] == {"건수": 4, "평균": 39.0, "중앙값": 37.5, "최대": 60}
    bad = _tool(domain_packs.HR_METRICS_SOURCE)("not json")
    assert "JSON" in bad


def test_job_posting_check_flags_discrimination_and_missing_items():
    check = _tool(domain_packs.JOB_POSTING_CHECK_SOURCE)
    bad = json.loads(check("[영업직 채용]\n30세 이하 남성 우대, 용모 단정한 분\n미혼자 선호, 사진 첨부 필수\n"
                           "부모 직업 기재\n출신 지역 기재\n본적지 기재\n담당 업무: B2B 영업\n마감: 12월 31일까지"))
    reasons = " ".join(f["확인 사항"] for f in bad["주의_문구"])
    for law in ("남녀고용평등법", "연령차별금지법", "제4조의3"):
        assert law in reasons
    assert len(bad["주의_문구"]) >= 8 and "고쳐야 함" in bad["판정"]
    assert "급여·처우" in bad["빠진_필수_항목"] and "담당 업무" in bad["확인된_필수_항목"]
    good = json.loads(check(json.dumps({"text": (
        "담당 업무: B2B 영업\n자격 요건: 영업 3년 이상\n고용 형태: 정규직\n근무지: 서울\n근무 시간: 주 5일\n"
        "급여: 연봉 협의\n전형 절차: 서류-면접\n지원 방법: 이메일\n마감: 12월 31일까지\n젊은 조직 문화, 성장하는 팀\n"
        "기본적인 협업 태도를 봅니다")},  # "기본적" ⊃ "본적" 오탐(2026-11-26 실측)
        ensure_ascii=False)))
    assert good["주의_문구"] == [] and good["빠진_필수_항목"] == [] and good["판정"] == "차별 소지 문구 없음"


def test_hr_agents_skills_tools_and_pipeline_are_seeded(conn):
    for role in ("채용 담당자", "인사 분석가", "온보딩 플래너"):
        assert conn.execute("SELECT status FROM agent_pool WHERE role_name = ?", (role,)).fetchone()["status"] == "active"
    for skill in ("채용공고작성", "면접평가설계", "인사지표분석", "온보딩계획수립"):
        assert conn.execute("SELECT 1 FROM skill_pool WHERE name = ?", (skill,)).fetchone(), skill
    for tool in ("인사 지표 계산기", "채용 공고 점검기"):
        cfg = json.loads(conn.execute("SELECT config FROM tool_pool WHERE name = ?", (tool,)).fetchone()[0])
        assert cfg["input_schema"]["type"] == "object"
    stages = conn.execute(
        "SELECT s.name, s.requires_human_approval, s.is_primary_deliverable, s.checks_requirements_traceability, "
        "(SELECT role_name FROM pipeline_stage_agents WHERE stage_id = s.id) AS role FROM pipeline_stages s "
        "JOIN pipeline_templates t ON t.id = s.pipeline_template_id WHERE t.domain = 'hr' ORDER BY s.seq_order").fetchall()
    assert [s["role"] for s in stages] == ["업무 요구사항 분석가", "채용 담당자", "문서 검토자"]
    assert stages[0]["requires_human_approval"] == 1 and stages[1]["is_primary_deliverable"] == 1
    assert stages[2]["checks_requirements_traceability"] == 1


def test_wizard_recommends_hiring_pipeline(conn):
    recs = wizard.recommend(conn, "백엔드 개발자 채용 공고와 면접 질문을 만들어 줘", "manager")["recommendations"]
    pipeline = [r for r in recs if r["kind"] == "pipeline"]
    assert pipeline and "채용 공고·면접 설계 파이프라인" in pipeline[0]["title"]
