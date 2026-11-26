"""긴 생성 중 진행 표시(2026-11-26, 개선 계획 1-5 — `llm._waiting_heartbeat`).

인사 파이프라인 실측: 도구가 붙은 최종 검토 단계가 스트리밍이 아닌 도구 호출 경로로 34분 동안 응답을 기다리는 사이
진행 문구가 "응답 생성 중"에서 한 번도 바뀌지 않아 멈춘 것처럼 보였다."""

from __future__ import annotations

import time

from hair_platform import llm, progress


def _with_reporter(seen: list):
    return progress._REPORTER.set(seen.append)


def test_non_streaming_call_reports_elapsed_time_while_waiting(monkeypatch):
    monkeypatch.setattr(llm, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(llm, "_call_ollama", lambda *a, **k: time.sleep(0.25) or "답")
    seen: list[str] = []
    token = _with_reporter(seen)
    try:
        assert llm.call_llm("ollama", "local-35b", "s", "p") == "답"
    finally:
        progress._REPORTER.reset(token)
    assert len(seen) >= 2 and all("응답 생성 중 — local-35b" in t and "경과" in t for t in seen)
    count = len(seen)
    time.sleep(0.15)
    assert len(seen) == count  # 호출이 끝나면 멈춘다


def test_tool_loop_call_reports_too(monkeypatch):
    monkeypatch.setattr(llm, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(llm, "_call_ollama_with_tools", lambda *a, **k: time.sleep(0.2) or "검토 결과")
    seen: list[str] = []
    token = _with_reporter(seen)
    try:
        assert llm.call_llm_with_tools("ollama", "m", "s", "p", [object()]) == "검토 결과"
    finally:
        progress._REPORTER.reset(token)
    assert seen and "도구를 쓰는 응답은 끝날 때 한꺼번에" in seen[0]


def test_no_reporter_or_streaming_means_no_heartbeat(monkeypatch):
    monkeypatch.setattr(llm, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(llm, "_call_ollama", lambda *a, **k: time.sleep(0.15) or "답")
    assert llm.call_llm("ollama", "m", "s", "p") == "답"  # 작업 밖 — 예외 없이 그냥 호출
    seen: list[str] = []
    monkeypatch.setitem(llm._STREAMERS, "ollama", lambda *a, **k: time.sleep(0.15) or "스트림")
    token = _with_reporter(seen)
    try:
        assert llm.call_llm("ollama", "m", "s", "p", on_partial=lambda t: None) == "스트림"
    finally:
        progress._REPORTER.reset(token)
    assert seen == []  # 스트리밍은 자체 진행 표시가 있다
