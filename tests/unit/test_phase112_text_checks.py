"""결과물 문장 검증 — 인용 근거·용어 일관성(2026-11-24, P0-1 남은 항목).

원래 분석의 실측 오류("1분기 착수"→"1분 착수", "발표 노트"→"발포 노트")와 재무 파이프라인 실측
("인건비"→"건비")을 잡고, 실제 결과물에서 나왔던 오탐(어미·접미사·첫소리가 다른 정상 낱말)은 잡지 않는다.
"""

from __future__ import annotations

import pytest

from hair_platform import agent_evaluation, text_checks

EVIDENCE = (
    "2026년 사업 계획: 1분기 착수, 2분기 파일럿, 하반기 전사 확대. 발표 노트는 슬라이드마다 작성한다. "
    "고객 만족도 조사 결과 응답자의 72%가 서비스 속도에 만족했다. 예산(백만 원): 인건비 1,200 / 마케팅 400. "
    "계약서 제5조는 을은 유지보수 업무와 관련하여 갑에게 발생한 모든 손해를 제한 없이 배상한다고 규정한다. "
    "검증식은 실적 나누기 예산이다. 결정용 요약과 사용자 안내, 누적치 표를 붙인다."
)


def _found(text):
    return [(s["found"], s["expected"]) for s in text_checks.term_consistency(text, EVIDENCE)["suspects"]]


@pytest.mark.parametrize("text,expected", [
    ("프로젝트는 1분 착수 후 2분기 파일럿으로 넘어간다.", ("1분", "1분기")),
    ("각 슬라이드에 발포 노트를 붙인다.", ("발포", "발표")),
    ("IT인프라(−30백만 원)·건비(−20백만 원)는 정상이다.", ("건비", "인건비")),
])
def test_dropped_or_changed_syllable_is_flagged(text, expected):
    assert expected in _found(text)


@pytest.mark.parametrize("text", [
    "계약서는 손해를 제한 없이 배상한다라고 규정한다.",   # 어미 차이(배상한다고)
    "적정성 검증 결과를 붙인다.",                          # 접미사 뗀 정상 낱말(검증식→검증)
    "결정 요청 사항과 사용 상태, 누적 회수를 적는다.",     # 결정용·사용자·누적치
    "계획과 계약은 다르다. 결론을 먼저 쓴다.",             # 첫소리가 다른 정상 낱말
    "근거 없음으로 표시된 항목은 없다.",                   # 있음/없음(명사형 어미)
])
def test_normal_words_are_not_flagged(text):
    assert _found(text) == []


def test_term_check_needs_enough_korean_evidence():
    assert text_checks.term_consistency("1분 착수", "short")["not_measured"] is True


def test_quote_grounding_checks_only_citations():
    out = (
        '계약서 제5조는 "을은 유지보수 업무와 관련하여 갑에게 발생한 모든 손해를 제한 없이 배상한다"라고 규정한다.\n'
        '보고서에 따르면 "고객의 90%가 가격 인하를 원한다"고 밝혔다.\n'
        '이번 전략의 핵심은 "빠르게 실험하고 데이터로 판단하는 문화"이다.'  # 강조 — 인용 표지 없음
    )
    quotes = text_checks.citation_quotes(out)
    assert len(quotes) == 2 and not any("실험" in q for q in quotes)
    result = text_checks.quote_grounding(out, EVIDENCE)
    assert result["status"] == "warn" and result["checked"] == 2
    assert [u["text"] for u in result["unsupported"]] == ["고객의 90%가 가격 인하를 원한다"]


def test_quote_with_ellipsis_checks_each_piece():
    out = '원문에 따르면 「을은 유지보수 업무와 관련하여 … 모든 손해를 제한 없이 배상한다」고 적혀 있다.'
    assert text_checks.quote_grounding(out, EVIDENCE)["status"] == "pass"


def test_no_citations_or_evidence_is_not_measured():
    assert text_checks.quote_grounding("인용 없는 보고서다.", EVIDENCE)["not_measured"] is True
    assert text_checks.quote_grounding('보고서에 따르면 "어떤 긴 문장이 여기에 있다고 한다"고 한다.', "")["not_measured"] is True


def test_attach_quality_checks_records_both_gates():
    gates: dict = {}
    out = '각 슬라이드에 발포 노트를 붙인다. 보고서에 따르면 "고객의 90%가 가격 인하를 원한다"고 밝혔다.'
    agent_evaluation.attach_quality_checks(gates, out, EVIDENCE)
    assert gates["term_consistency"]["status"] == "warn"
    assert gates["quote_grounding"]["status"] == "warn"
    clean: dict = {}
    agent_evaluation.attach_quality_checks(clean, "보고서를 정리했다. 발표 노트를 붙인다.", EVIDENCE)
    assert clean["term_consistency"]["status"] == "pass" and "quote_grounding" not in clean
