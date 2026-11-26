"""Ollama 추론(생각) 켜고 끄기(2026-11-26, 개선 계획 3-2 실측 — `llm.reasoning`).

실측: Ornith 35B(MoE, 활성 약 3B)는 생각을 끄면 같은 요구사항 정리를 15초에 끝냈는데(qwen3:4b-instruct 15.8초보다 빠르고
수치도 정확), 플랫폼은 Ollama 호출에 `think`를 한 번도 넘기지 않아 모든 호출이 긴 사고 과정을 먼저 만들었다."""

from __future__ import annotations

import json

from hair_platform import llm, requirements_traceability_llm


class _Resp:
    status_code = 200

    def __init__(self, content="답"):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}, "prompt_eval_count": 1, "eval_count": 1}


def test_think_field_is_sent_only_inside_the_context(monkeypatch):
    bodies = []
    monkeypatch.setattr("httpx.post", lambda url, json=None, timeout=None: bodies.append(json) or _Resp())
    llm._call_ollama("m", "s", "p", None)
    with llm.reasoning(False):
        llm._call_ollama("m", "s", "p", None)
        with llm.reasoning(None):  # None은 바깥 설정을 그대로 둔다
            llm._call_ollama("m", "s", "p", None)
    with llm.reasoning(True):
        llm._call_ollama("m", "s", "p", None)
    assert ["think" in b for b in bodies] == [False, True, True, True]
    assert [b.get("think") for b in bodies[1:]] == [False, False, True]


def test_tool_loop_request_carries_think(monkeypatch):
    bodies = []
    monkeypatch.setattr("httpx.post", lambda url, json=None, timeout=None: bodies.append(json) or _Resp("최종 답"))

    from pydantic import BaseModel

    class _Args(BaseModel):
        query: str = ""

    class _Tool:
        name = "t"
        description = "도구"
        args_schema = _Args

        def run(self, **kw):
            return "ok"

    with llm.reasoning(False):
        out = llm._call_ollama_with_tools("m", "s", "p", [_Tool()], None, 3)
    assert out == "최종 답" and bodies and all(b.get("think") is False for b in bodies)


def test_traceability_judge_turns_thinking_off(client_and_conn, monkeypatch):
    _client, conn = client_and_conn
    seen = []
    monkeypatch.setattr(llm, "resolve_provider_and_model", lambda *a, **k: ("ollama", "m"))
    monkeypatch.setattr(llm, "call_llm", lambda *a, **k: seen.append(llm._THINK.get())
                        or json.dumps({"total_requirements": 1, "covered": 1, "missing": []}))
    requirements_traceability_llm.assess_traceability(conn, "1. A", "A")
    assert seen == [False]
    assert llm._THINK.get() is None  # 문맥을 벗어나면 되돌아간다


def test_agent_reasoning_setting_reaches_the_call(client_and_conn, monkeypatch):
    from hair_platform import agent_runtime

    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    agent = conn.execute("SELECT id FROM agent_pool WHERE role_name = '업무 요구사항 분석가'").fetchone()[0]
    seen = []
    monkeypatch.setattr(agent_runtime.llm, "call_llm", lambda *a, **k: seen.append(llm._THINK.get()) or "## 요구사항\n- A")
    monkeypatch.setattr(agent_runtime.llm, "call_llm_with_tools",
                        lambda *a, **k: seen.append(llm._THINK.get()) or "## 요구사항\n- A")
    for value, expected in ((False, False), (True, True), (None, None)):
        tuning = {} if value is None else {"reasoning": value}
        conn.execute("UPDATE agent_pool SET llm_provider = 'ollama', llm_model = 'm', tuning_config = ? WHERE id = ?",
                     (json.dumps(tuning), agent))
        conn.commit()
        seen.clear()
        agent_runtime.execute_agent(conn, agent, admin, "요구사항을 정리해줘")
        assert seen and seen[0] is expected, (value, seen)
