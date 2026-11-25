"""업무 영역 템플릿 — 재무·영업/고객지원·법무(2026-11-24, "P2 진행해줘" — P2-12)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hair_platform import db, domain_packs, pool_registry, skill_catalog


@pytest.fixture(scope="module")
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _run(tool_name, payload) -> Any:  # JSON이면 dict, 아니면 원문 str
    source = {n: s for n, _d, s, _t in domain_packs.TOOL_SPECS}[tool_name]
    raw = pool_registry._run_custom_python_tool(source, payload if isinstance(payload, str) else json.dumps(payload), 10)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def test_finance_calculator_variance_and_investment():
    r = _run("재무 지표 계산기", {"budget": {"인건비": 120, "마케팅": 40}, "actual": {"인건비": 118, "마케팅": 52, "교육": 5},
                                "cash_flows": [-1000, 300, 400, 500, 200], "rate": 0.08})
    rows = {row["항목"]: row for row in r["예산_대비_실적"]}
    assert rows["마케팅"]["차이(실적-예산)"] == 12 and rows["마케팅"]["차이율(%)"] == 30.0 and rows["마케팅"]["집행률(%)"] == 130.0
    assert "확인 필요" in rows["교육"]["비고"]
    assert r["합계"] == {"예산": 160.0, "실적": 170.0, "차이": 10.0, "차이율(%)": 6.25}
    inv = r["투자_타당성"]
    assert inv["NPV"] == pytest.approx(164.6354, abs=1e-3)
    assert inv["IRR(%)"] == pytest.approx(15.32, abs=0.01)
    assert inv["단순 회수기간(기)"] == 2.6
    assert "계산 불가" in _run("재무 지표 계산기", {"cash_flows": [100, 200]})["투자_타당성"]["IRR(%)"]
    assert "JSON" in _run("재무 지표 계산기", "자유 텍스트")


def test_sales_funnel_calculator():
    r = _run("영업 퍼널 계산기", {"stages": {"리드": 1200, "상담": 300, "제안": 120, "계약": 30}, "deal_value": 500,
                                "deals": [{"name": "A", "amount": 1000, "probability": 60}, {"name": "B", "amount": 400, "probability": 0.25}]})
    assert [s["전환율(%)"] for s in r["단계별_전환"]] == [25.0, 40.0, 25.0]
    assert r["전체_전환율(%)"] == 2.5 and r["가장_낮은_전환_구간"] == "리드 → 상담"
    assert r["예상_매출(마지막 단계 수 × 건당 금액)"] == 15000
    assert r["가중_파이프라인_금액"] == 700.0  # 확률 60과 0.25를 둘 다 받아들인다


def test_contract_checker_finds_missing_clauses_and_risky_phrases():
    text = ("제1조(목적) 본 계약은 ...\n제5조(대금) 갑은 매월 지급한다.\n제9조(해지) 갑은 일방적으로 해지할 수 있다.\n"
            "제10조 본 계약은 자동 갱신된다.\n제12조(손해배상) 을은 모든 손해를 제한 없이 배상한다.")
    r = _run("계약 조항 점검기", text)
    assert {"목적·업무 범위", "대금·지급 조건", "해지·해제", "손해배상"} <= set(r["확인된_조항"])
    assert {"비밀유지", "준거법", "책임 제한"} <= set(r["찾지_못한_조항"])
    reasons = " ".join(f["확인 사항"] for f in r["주의_문구"])
    assert "자동 갱신" in reasons and "무제한" in reasons and "한쪽만" in reasons
    assert "법률 자문이 아니다" in r["안내"]
    assert _run("계약 조항 점검기", {"text": text})["찾지_못한_조항"] == r["찾지_못한_조항"]  # 구조화 입력도 받는다


def test_seeded_skills_have_guides_and_tool_links(conn):
    for name, spec in domain_packs.SKILL_SPECS.items():
        row = conn.execute("SELECT augmentation_mode, default_tool_pool_ids, output_format, constraints, category "
                           "FROM skill_pool WHERE name = ?", (name,)).fetchone()
        assert row is not None, name
        assert row["output_format"] and row["constraints"] and row["category"] == "general"
        assert skill_catalog.SKILL_GUIDES[name].description == spec["guide"][0]
        if spec.get("tool"):
            tool_id = conn.execute("SELECT id FROM tool_pool WHERE name = ?", (spec["tool"],)).fetchone()[0]
            assert row["augmentation_mode"] == "tool" and json.loads(row["default_tool_pool_ids"]) == [tool_id]
        else:
            assert row["augmentation_mode"] == "none"
    for name in domain_packs.TOOL_SPECS:
        cfg = json.loads(conn.execute("SELECT config FROM tool_pool WHERE name = ?", (name[0],)).fetchone()[0])
        assert cfg["input_schema"]["properties"]  # 함수 호출 경로에 구조화 입력으로 노출


def test_seeded_agents_and_pipelines(conn):
    for role, tags, _t in domain_packs.AGENT_SPECS:
        row = conn.execute("SELECT status, skill_tags FROM agent_pool WHERE role_name = ?", (role,)).fetchone()
        assert row["status"] == "active" and json.loads(row["skill_tags"]) == tags
        for tag in tags:
            assert conn.execute("SELECT 1 FROM skill_pool WHERE name = ?", (tag,)).fetchone(), (role, tag)
    for spec in domain_packs.PIPELINES:
        stages = conn.execute(
            "SELECT s.* , (SELECT role_name FROM pipeline_stage_agents WHERE stage_id = s.id) AS role FROM pipeline_stages s "
            "JOIN pipeline_templates t ON t.id = s.pipeline_template_id WHERE t.name = ? ORDER BY s.seq_order", (spec["name"],)
        ).fetchall()
        assert [s["role"] for s in stages] == [st["role"] for st in spec["stages"]]
        assert stages[0]["requires_human_approval"] == 1
        primary = [s for s in stages if s["is_primary_deliverable"]]
        review = stages[-1]
        assert len(primary) == 1
        assert json.loads(review["gate_ids"]) == ["A", "requirements_traceability"] and review["min_gate_pass_ratio"] == 1.0
        assert review["checks_requirements_traceability"] == 1 and review["on_fail_return_stage_id"] == primary[0]["id"]


def test_seed_is_idempotent_and_keeps_admin_edits(conn):
    conn.execute("UPDATE agent_pool SET skill_tags = '[]' WHERE role_name = '재무 분석가'")
    conn.execute("UPDATE pipeline_stages SET instructions = '관리자 수정' WHERE name = '조항 검토'")
    db.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM pipeline_templates WHERE domain IN ('finance', 'sales', 'legal')").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM agent_pool WHERE role_name = '재무 분석가'").fetchone()[0] == 1
    assert conn.execute("SELECT skill_tags FROM agent_pool WHERE role_name = '재무 분석가'").fetchone()[0] == "[]"
    assert conn.execute("SELECT instructions FROM pipeline_stages WHERE name = '조항 검토'").fetchone()[0] == "관리자 수정"


def test_pipeline_stage_output_gets_language_repair():
    """파이프라인 단계 산출물도 언어 교정을 거친다(2026-11-24 실측 — 재무 보고서에 "1周内"가 남았다)."""
    from unittest.mock import patch

    from hair_platform import pipeline_runtime

    c = db.get_connection(":memory:")
    db.init_schema(c)
    report = "## 권고\n\n마케팅 초과 집행 내역을 1周内 확인한다. 교육 예산 집행 계획을 확정한다. 투자안은 검증 후 승인한다."
    answers = iter([json.dumps(["마케팅 초과 집행 내역을 1주 이내 확인한다. 교육 예산 집행 계획을 확정한다. 투자안은 검증 후 승인한다."])])
    with patch("hair_platform.llm.resolve_provider_and_model", return_value=("anthropic", "m")), \
            patch("hair_platform.llm.call_llm", side_effect=lambda *a, **k: next(answers)):
        fixed = pipeline_runtime.repair_stage_language(c, report, None, None, 1)
    assert "周" not in fixed and "1주 이내" in fixed
    # 문제 조각이 없으면 LLM을 부르지 않는다
    with patch("hair_platform.llm.call_llm", side_effect=AssertionError("호출하면 안 된다")):
        clean = "## 권고\n\n마케팅 초과 집행 내역을 확인한다."
        assert pipeline_runtime.repair_stage_language(c, clean, "ollama", "m", 1) == clean
