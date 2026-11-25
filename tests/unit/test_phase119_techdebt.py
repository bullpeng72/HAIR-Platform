"""2026-11-25 기술 부채 점검(/techdebt --fix-now) 회귀 테스트."""

from __future__ import annotations

from hair_platform import agent_evaluation, conversation_memory, dashboard, inbox, vectorstore


def test_not_measured_gate_is_not_a_failure():
    """실측(계약서·영업 파이프라인): 평가 7건 중 3건이 측정 안 한 code_syntax·requirements_traceability 때문에
    "Gate 실패"로 잡혔다 — 인박스 낮은 Gate 경고·대시보드 반복 실패 Agent가 잘못 뜬다."""
    not_measured_only = {
        "A": {"score": 0.85, "passed": True},
        "B": {"score": None, "passed": True, "not_measured": True},
        "code_syntax": {"score": None, "status": "not_measured", "not_measured": True, "passed": False},
        "requirements_traceability": {"score": None, "not_measured": True, "passed": False},
    }
    assert agent_evaluation.has_failing_gate(not_measured_only) is False
    real_failure = {**not_measured_only, "A": {"score": 0.3, "passed": False}}
    assert agent_evaluation.has_failing_gate(real_failure) is True
    assert agent_evaluation.has_failing_gate({}) is False


def test_old_private_copies_are_gone():
    assert not hasattr(dashboard, "_has_failing_gate") and not hasattr(inbox, "_has_failing_gate")
    assert not hasattr(conversation_memory, "_cosine_similarity")
    assert vectorstore._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
