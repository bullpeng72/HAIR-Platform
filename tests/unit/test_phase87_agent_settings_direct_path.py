"""에이전트 설정을 직접 호출 경로에도 적용(2026-11-21, "직접 호출 경로에서
적용되도록 개선") — 대화 @멘션·업무 실행·실행 버튼은 모두
`agent_runtime.execute_agent()`를 거친다. 지금까지 이 경로는 파인튜닝 중 추론
모드(시간 제한 연장)와 기대 산출물만 썼고, 온도·실행 시간 제한·최대 반복 횟수·
목표·배경은 Crew 실행에서만 쓰였다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import agent_runtime, model_options


def _agent(conn, tuning=None, **persona):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, tuning_config, goal, backstory) "
        "VALUES ('분석가', '[]', 'active', ?, ?, ?)",
        (json.dumps(tuning or {}), persona.get("goal"), persona.get("backstory")),
    )
    conn.commit()
    return cur.lastrowid


def _admin(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]


def test_untuned_agent_keeps_call_shape(client_and_conn):
    """튜닝이 없으면 호출 인자가 예전과 같다(추가 인자 없음)."""
    _c, conn = client_and_conn
    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, _agent(conn), _admin(conn), "질문")
    assert set(m.call_args.kwargs) == {"timeout_seconds"}
    assert m.call_args.kwargs["timeout_seconds"] is None


def test_tuning_reaches_direct_call(client_and_conn):
    _c, conn = client_and_conn
    aid = _agent(conn, {"temperature": 0.2, "max_execution_time": 300, "reasoning": True})
    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, aid, _admin(conn), "질문")
    assert m.call_args.kwargs["temperature"] == 0.2
    # 실행 시간 제한을 직접 정하면 추론 모드의 자동 연장보다 우선한다.
    assert m.call_args.kwargs["timeout_seconds"] == 300.0


def test_max_iter_limits_tool_loop(client_and_conn):
    _c, conn = client_and_conn
    aid = _agent(conn, {"max_iter": 3, "temperature": 0.5})
    with (
        patch("hair_platform.tool_calling.resolve_callable_tools", return_value=[object()]),
        patch("hair_platform.llm.call_llm_with_tools", return_value="결과") as m,
    ):
        agent_runtime.execute_agent(conn, aid, _admin(conn), "질문")
    assert m.call_args.kwargs["max_iterations"] == 3
    assert m.call_args.kwargs["temperature"] == 0.5


def test_persona_goal_and_backstory_reach_system_prompt(client_and_conn):
    _c, conn = client_and_conn
    aid = _agent(conn, goal="경쟁사 동향을 숫자로 정리한다", backstory="10년차 시장 분석가")
    with patch("hair_platform.llm.call_llm", return_value="결과") as m:
        agent_runtime.execute_agent(conn, aid, _admin(conn), "질문")
    system = m.call_args.args[2]
    assert "목표: 경쟁사 동향을 숫자로 정리한다" in system
    assert "배경: 10년차 시장 분석가" in system


def test_agent_temperature_overrides_model_and_is_clamped(monkeypatch):
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")  # 샘플링 값만 본다(출력 한도는 phase94)
    monkeypatch.setattr(model_options, "_CACHE", {})
    model_options.set_cached("anthropic", "c", {"top_p": 0.9})
    assert model_options.request_options("anthropic", "c", 1.5) == {"top_p": 0.9, "temperature": 1.0}
    assert model_options.request_options("ollama", "x", 1.5) == {"temperature": 1.5}
    assert model_options.request_options("ollama", "x") == {}


def test_temperature_sent_in_ollama_payload(monkeypatch):
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")  # 샘플링 값만 본다(출력 한도는 phase94)
    from hair_platform import llm

    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "답"}}

    def fake_post(url, json=None, timeout=None):
        sent.update(json or {})
        return _Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(model_options, "_CACHE", {})
    llm.call_llm("ollama", "m", "s", "p", temperature=0.3)
    assert sent["options"] == {"temperature": 0.3}


def test_native_tool_rename_merges_duplicate_without_breaking_links():
    """스킬명 변경 때 도구 시드 이름도 바뀌어, 기존 DB에 같은 도구가 새 이름으로
    하나 더 생겼던 문제(2026-11-21) — 원래 행(id)을 새 이름으로 바꾸고, 쓰이지
    않는 중복 행은 지운다."""
    import sqlite3

    from hair_platform import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    vid = conn.execute("SELECT id FROM tool_pool WHERE name = 'VectorDB 내부지식검색'").fetchone()[0]
    # 옛 상태 재현: 원래 행은 옛 이름, 새 이름 행은 쓰이지 않는 중복.
    conn.execute("UPDATE tool_pool SET name = 'VectorDB 지식검색' WHERE id = ?", (vid,))
    conn.execute(
        "INSERT INTO tool_pool (name, description, kind, config) VALUES "
        "('VectorDB 내부지식검색', 'dup', 'native', '{\"native_id\": \"vectordb_search\"}')"
    )
    conn.commit()
    db.init_schema(conn)
    rows = conn.execute("SELECT id, name FROM tool_pool WHERE name LIKE 'VectorDB%'").fetchall()
    assert [tuple(r) for r in rows] == [(vid, "VectorDB 내부지식검색")]
    skill_tools = conn.execute(
        "SELECT default_tool_pool_ids FROM skill_pool WHERE name = '내부지식검색'"
    ).fetchone()[0]
    assert json.loads(skill_tools) == [vid]
