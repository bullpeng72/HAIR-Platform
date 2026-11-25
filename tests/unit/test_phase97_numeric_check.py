"""수치 근거 검사(2026-11-23, "P0부터 개선 진행해줘" — P0-1). 사례는 같은 날 Agent 라이브
실행에서 실제로 나온 문장이다."""

from __future__ import annotations

import json

from hair_platform import numeric_check as nc

REQUEST = "3분기 실적 보고서를 써줘. 매출 55억(목표 52억), 영업이익 6.2억(목표 7억), 신규고객 120곳(목표 100곳)."


def test_correct_percentages_are_supported():
    out = "매출 105.8% 달성, 영업이익 88.6%, 신규고객 120% 달성. 매출은 목표보다 +5.8%, 고객은 +20%."
    assert nc.unsupported_claims(out, REQUEST) == []


def test_real_errors_are_flagged():
    # 실측: 0.8÷7 = 11.4%인데 13.8%라고 썼다.
    bad = nc.unsupported_claims("영업이익은 목표 대비 **88.6%에 그쳐 13.8%(0.8억 원) 미달**이다.", REQUEST)
    assert bad is not None
    assert [c["text"] for c in bad] == ["13.8%"]
    # 실측: 비율(11.3%·13.5%)을 %p로 썼다 — %p는 두 비율의 차이만 허용한다.
    bad = nc.unsupported_claims("이익률 11.3%p→13.5%p 개선이 과제다.", REQUEST)
    assert bad is not None
    assert {c["text"] for c in bad} == {"11.3%p", "13.5%p"}
    # 실측: 하반기 합계를 113억(실제 116억)으로 잘못 더한 뒤 증감률을 냈다.
    series = '{"periods": ["1분기", "2분기", "3분기", "4분기"], "values": [42, 48, 55, 61]}'
    bad = nc.unsupported_claims("하반기(113억)가 상반기(90억)보다 25.6% 크다.", series)
    assert bad is not None
    assert [c["text"] for c in bad] == ["25.6%"]


def test_coincidental_combinations_do_not_hide_errors():
    """처음 구현은 두 숫자의 합·차이도 일반 %의 후보로 써서 6.2+100=106.2, 120−106.2=13.8로
    틀린 13.8%를 통과시켰다."""
    bad = nc.unsupported_claims("영업이익은 13.8% 미달이다.", REQUEST)
    assert bad and bad[0]["value"] == 13.8


def test_quoted_source_values_and_approx_and_code_blocks():
    evidence = "KITA: 2026년 시장은 17.8% 성장한 9,098억 달러. SK하이닉스: 9,750억 달러."
    assert nc.unsupported_claims("KITA는 17.8% 성장을 전망한다.", evidence) == []
    # "약"은 상대 2%까지 허용 — 9,750÷9,098 = 107.17%
    assert nc.unsupported_claims("SK하이닉스 전망이 약 107% 수준이다.", evidence) == []
    # 코드 블록·차트 설정 안의 숫자는 검사하지 않는다.
    assert nc.unsupported_claims('```chart\n{"unit": "%", "data": [99.9]}\n```', evidence) == []


def test_unmeasurable_returns_none_and_gate_is_informational():
    assert nc.unsupported_claims("성장률 12%", "숫자 없음") is None
    many = " ".join(str(i * 7 + 0.5) for i in range(nc.MAX_EVIDENCE_NUMBERS + 5))
    assert nc.unsupported_claims("성장률 12%", many) is None
    g = nc.gate_score("영업이익 13.8% 미달", REQUEST)
    assert g["status"] == "warn" and g["passed"] is False and g["unsupported"][0]["text"] == "13.8%"
    assert nc.gate_score("숫자 없는 문장", REQUEST)["not_measured"] is True


def test_repair_replaces_only_flagged_sentences_and_guards_results():
    text = "## 요약\n매출 105.8% 달성.\n영업이익은 13.8% 미달이다.\n끝."
    bad = nc.unsupported_claims(text, REQUEST)
    assert bad is not None
    fixed, report = nc.repair(text, bad, REQUEST, lambda s, p: json.dumps(["영업이익은 11.4%(0.8÷7) 미달이다."], ensure_ascii=False))
    assert "11.4%" in fixed and "105.8%" in fixed and report["fixed"] == 1
    # 응답을 못 읽으면 원문 유지
    same, report = nc.repair(text, bad, REQUEST, lambda s, p: "모르겠습니다")
    assert same == text and report["error"] == "unparseable_response"
    # 문장 단위로 근거 없는 수치가 줄지 않으면 받지 않는다 — 실측: 13.8%를 11.4%로 고치면서
    # 같은 문장에 근거 없는 "8.6%p"를 새로 넣은 교정.
    same_count = json.dumps(["영업이익은 11.4%(8.6%p) 미달이다."], ensure_ascii=False)
    kept, report = nc.repair(text, bad, REQUEST, lambda s, p: same_count)
    assert kept == text and report["fixed"] == 0 and report["error"] == "no_improvement"
    unchanged, report = nc.repair(text, bad, REQUEST, lambda s, p: json.dumps(["영업이익은 13.8% 미달이다."], ensure_ascii=False))
    assert unchanged == text and report["fixed"] == 0


def test_unresolved_numbers_get_a_visible_note():
    note = nc.unresolved_note("영업이익은 13.8% 미달이다.", REQUEST)
    assert note.strip().startswith(nc.NOTE_PREFIX) and "13.8%" in note
    assert nc.unresolved_note("매출 105.8% 달성.", REQUEST) == ""


def test_pipeline_records_numeric_grounding_without_changing_pass(monkeypatch):
    from hair_platform import pipeline_runtime

    scores = {"A": {"passed": True, "not_measured": False}, "numeric_grounding": nc.gate_score("13.8% 미달", REQUEST)}
    assert pipeline_runtime._gate_pass_ratio(scores, ["A"]) == 1.0
