"""사전에 없는 한국어 낱말 탐지(2026-11-25, `korean_lexicon.py`) — 실측 결과물의 지어낸 낱말("이모니즘"·"분인"·"순현나름")."""

from __future__ import annotations

import pytest

from hair_platform import agent_evaluation, korean_lexicon, pipeline_runtime

_FILLER = "이 문서는 3분기 실적 보고서의 요구사항 충족 여부와 수치 정확성을 점검한 결과를 정리한 것이다. " * 3


@pytest.fixture
def small_vocab(monkeypatch):
    """플랫폼 어휘(첫 빌드 수십 초) 대신 작은 어휘로 — 화면에 쓰는 외래어 몇 개."""
    monkeypatch.setattr(korean_lexicon, "_VOCAB", (frozenset({"웹훅", "인박스"}), frozenset({"웹훅", "인박스", "파이프라인"})))


def test_flags_invented_words_from_live_runs(small_vocab):
    pytest.importorskip("kiwipiepy")
    for sentence, word in [("\"가장 약한 구간\" 이모니즘 약함", "이모니즘"),
                           ("수기·메신저 등으로 분인 처리하시다", "분인"),
                           ("순현나름 기준으로 계산했다", "순현")]:
        found = korean_lexicon.odd_words(_FILLER + sentence)
        assert found is not None
        assert [w["word"] for w in found] == [word], sentence
        assert sentence[:4] in found[0]["context"] or word in found[0]["context"]


def test_does_not_flag_compounds_endings_vocab_evidence_or_repeated_terms(small_vocab):
    pytest.importorskip("kiwipiepy")
    text = _FILLER + (
        "지식재산 조항과 미집행 예산, 팀이름 규칙을 정리했다. 결재 카드의 버튼을 누른다. "  # 합성어·조사
        "웹훅과 인박스, 파이프라인을 설정한다. "  # 플랫폼 어휘
        "고객사 쿠루미아의 요청이다. "  # 근거에 나온 고유명사
        "스크리닝 결과, 스크리닝 기준, 스크리닝 대상."  # 한 문서에 3번 → 의도한 용어
    )
    assert korean_lexicon.odd_words(text, evidence="고객사 쿠루미아가 요청한 보고서") == []


def test_short_text_and_missing_analyzer_are_not_measured(monkeypatch, small_vocab):
    assert korean_lexicon.odd_words("짧은 글") is None
    monkeypatch.setattr(korean_lexicon, "_KIWI", None)
    monkeypatch.setattr(korean_lexicon, "_KIWI_ERROR", "ModuleNotFoundError")
    result = korean_lexicon.gate_score(_FILLER + "이모니즘")
    assert result["not_measured"] and "kiwipiepy" in result["reason"]


def test_lexicon_gate_is_advisory_and_never_fails_a_stage_or_inbox(small_vocab):
    pytest.importorskip("kiwipiepy")
    gate = korean_lexicon.gate_score(_FILLER + "이모니즘 약함")
    assert gate["advisory"] and gate["passed"] is True and gate["status"] == "warn" and gate["words"]
    scores = agent_evaluation.attach_quality_checks({"A": {"score": 0.9, "passed": True}}, _FILLER + "이모니즘 약함", "근거")
    assert "lexicon_check" in scores
    assert pipeline_runtime._gate_pass_ratio(scores) == 1.0  # 참고용은 통과 비율에 안 들어감
    assert agent_evaluation.has_failing_gate(scores) is False  # 인박스 "낮은 Gate 경고"도 안 뜸
    assert agent_evaluation.has_failing_gate({"x": {"passed": False, "advisory": True}}) is False


def test_wrong_examples_in_ui_text_are_not_learned_as_vocabulary():
    assert korean_lexicon._WRONG_EXAMPLE.sub(" ", '예: "순현금흐름"→"순현나름", "1분기"→"1분"') .count("순현나름") == 0


def test_can_be_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("HAIR_LEXICON_CHECK", "0")
    assert korean_lexicon.enabled() is False and korean_lexicon.warm_up_in_background() is False
    result = korean_lexicon.gate_score(_FILLER + "이모니즘")
    assert result["not_measured"] and "HAIR_LEXICON_CHECK" in result["reason"]


def test_platform_corpus_does_not_teach_known_invented_words():
    """플랫폼 어휘(화면·가이드·스킬)에 실측에서 나온 지어낸 낱말이 예시로 들어가면 정상 낱말로 배워 놓친다 —
    가이드에 예시를 쓸 때는 화살표 오른쪽 인용(→"…")으로만 쓰거나 낱말 자체를 쓰지 않는다."""
    corpus = korean_lexicon._platform_corpus()
    for invented in ("이모니즘", "분인", "순현나름"):
        assert invented not in corpus, invented


def test_lexicon_check_is_off_when_output_language_is_not_korean(client_and_conn):
    """외국어 결과물을 "사전에 없는 낱말"로 오탐하지 않게 — 조직 결과물 언어가 한국어일 때만(2026-11-25)."""
    from hair_platform import org_policy

    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    try:
        org_policy.set_output_language(conn, admin, "ja")
        assert korean_lexicon.enabled() is False
        assert korean_lexicon.gate_score(_FILLER + "이모니즘")["not_measured"]
    finally:
        org_policy.set_output_language(conn, admin, "ko")
    assert korean_lexicon.enabled() is True
