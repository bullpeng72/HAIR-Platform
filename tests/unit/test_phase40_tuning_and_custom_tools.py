"""Phase 40 — CrewAI 벤치마크 분석(2026-10-13, "CrewAI... HAIR-Platform에
필요한 기능들을 분석하고 충분히 자유도 높게 지원되고 있는지 분석해줘")에서
"1,2까지 반영하고, 생성된 CrewAI 에이전트를 선택해 Finetuning 하는 기능을
구현해줘, @데코레이터 툴을 추가 생성할 수 있는 기능을 제공해줘"로 이어진
4가지 구현을 검증한다:

1. `max_rpm`/`max_execution_time`(Agent·Crew 동작 안전장치)
2. `Task.guardrail`(Gate A-G 이전 1차 품질 방어선 — 빈/너무 짧은 산출물 차단)
3. "파인튜닝"(사용자 확인: 실제 LLM 재학습이 아니라 CrewAI Agent/LLM
   동작 파라미터 조정) — `PATCH /api/agent-pool/{id}/tuning`
4. `tool_pool.kind='custom_python'` — `@tool` 데코레이터 기반 커스텀
   Tool을 코드 수정 없이 등록(admin 전용 — mcp_stdio와 같은 신뢰 등급)
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import crew_runtime, pool_registry


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 1) max_rpm/max_execution_time + 3) 파인튜닝 설정 ----------


def test_set_agent_tuning_config_requires_admin_or_manager_and_persists(
    client_and_conn,
):
    client, conn = client_and_conn
    agent_id = _make_active_agent(conn, "백엔드 개발자")

    _login(client, "member1", "memberpass")
    resp = client.patch(f"/api/agent-pool/{agent_id}/tuning", json={"max_iter": 5})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/agent-pool/{agent_id}/tuning",
        json={
            "max_iter": 5,
            "max_rpm": 10,
            "max_execution_time": 120,
            "max_retry_limit": 1,
            "reasoning": True,
            "allow_code_execution": False,
            "respect_context_window": True,
            "temperature": 0.3,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tuning_config"] == {
        "max_iter": 5,
        "max_rpm": 10,
        "max_execution_time": 120,
        "max_retry_limit": 1,
        "reasoning": True,
        "allow_code_execution": False,
        "respect_context_window": True,
        "temperature": 0.3,
    }

    resp = client.get("/api/agent-pool")
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["tuning_config"]["max_iter"] == 5
    assert row["tuning_config"]["temperature"] == 0.3

    # None으로 보낸 필드는 저장에서 빠진다(지정 안 함 = CrewAI 기본값).
    resp = client.patch(
        f"/api/agent-pool/{agent_id}/tuning", json={"max_iter": None, "max_rpm": 7}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tuning_config"] == {"max_rpm": 7}


def test_set_agent_tuning_config_404_for_missing_agent(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.patch("/api/agent-pool/999999/tuning", json={"max_iter": 5})
    assert resp.status_code == 404


def test_run_crew_applies_tuning_config_without_crewai_validation_error(
    client_and_conn,
):
    """tuning_config가 실제 `crewai.Agent(**kwargs)` 생성자에 올바른
    키/타입으로 전달되는지 확인한다 — 이름이나 타입이 틀렸으면 CrewAI
    자체 pydantic 검증이 `crew.kickoff()`를 부르기도 전에(Agent 생성
    시점에) 예외를 던진다. 그 바깥 경계(`crewai.Crew.kickoff`)만
    mock해 실제 LLM 호출은 막는다(기존 crew_runtime.py 관례)."""
    client, conn = client_and_conn
    agent_id = _make_active_agent(conn, "백엔드 개발자")
    tuning = {
        "max_iter": 5,
        "max_rpm": 20,
        "max_execution_time": 60,
        "max_retry_limit": 1,
        "reasoning": False,
        "allow_code_execution": False,
        "respect_context_window": True,
        "temperature": 0.2,
    }
    conn.execute(
        "UPDATE agent_pool SET tuning_config = ? WHERE id = ?",
        (json.dumps(tuning), agent_id),
    )
    conn.commit()
    _login(client, "admin1", "adminpass")

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("결과", ["결과"]),
    ):
        result = crew_runtime.run_crew(
            conn,
            [agent_id],
            ["업무를 수행하세요"],
            _admin_id(conn),
            process="sequential",
            provider="openai",
        )
    assert result["final_output"] == "결과"


def _admin_id(conn):
    return conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]


# ---------- 2) Task.guardrail(1차 품질 방어선) ----------


def test_min_length_guardrail_rejects_short_and_accepts_long_output():
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    ok, value = crew_runtime._min_length_guardrail(_FakeOutput("짧음"))
    assert ok is False
    assert "너무 짧습니다" in value

    ok, value = crew_runtime._min_length_guardrail(_FakeOutput("   "))
    assert ok is False

    long_text = "충분히 구체적인 산출물 내용입니다 " * 3
    ok, value = crew_runtime._min_length_guardrail(_FakeOutput(long_text))
    assert ok is True
    assert value == long_text


# ---------- 4) custom_python 도구 ----------


def test_create_custom_python_tool_requires_admin(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "커스텀 도구",
            "kind": "custom_python",
            "config": {"function_source": "def tool_fn(query: str) -> str:\n    return query"},
        },
    )
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "커스텀 도구",
            "kind": "custom_python",
            "config": {"function_source": "def tool_fn(query: str) -> str:\n    return query"},
        },
    )
    assert resp.status_code == 201, resp.text


def test_create_custom_python_tool_rejects_invalid_source(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")

    resp = client.post(
        "/api/tool-pool",
        json={"name": "빈 소스", "kind": "custom_python", "config": {}},
    )
    assert resp.status_code == 400

    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "함수명 틀림",
            "kind": "custom_python",
            "config": {"function_source": "def other_fn(query: str) -> str:\n    return query"},
        },
    )
    assert resp.status_code == 400

    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "문법 오류",
            "kind": "custom_python",
            "config": {"function_source": "def tool_fn(query: str) -> str:\n    return query("},
        },
    )
    assert resp.status_code == 400


def test_custom_python_tool_executes_via_resolve_pool_tools(client_and_conn):
    """등록한 Python 소스가 skill_pool 매칭을 거쳐 실제로 호출 가능한
    CrewAI Tool로 만들어지고, 그 Tool을 실제로 실행하면(서브프로세스로)
    진짜 결과가 돌아오는지 확인한다(Docker 유무와 무관하게 동작해야
    한다 — 이 테스트 환경엔 Docker가 없다)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "대문자 변환",
            "kind": "custom_python",
            "description": "질의를 대문자로 바꿔 돌려준다",
            "config": {
                "function_source": (
                    "def tool_fn(query: str) -> str:\n"
                    "    return query.upper()\n"
                ),
                "timeout_seconds": 10,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    tool_id = resp.json()["id"]

    resp = client.post(
        "/api/skill-pool",
        json={
            "name": "대문자화",
            "description": "테스트용",
            "default_tool_pool_ids": [tool_id],
            "augmentation_mode": "tool",
        },
    )
    assert resp.status_code == 201, resp.text

    from contextlib import ExitStack

    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(conn, stack, ["대문자화"])
    assert len(tools) == 1
    assert tools[0].run(query="hello") == "HELLO"


def test_custom_python_tool_timeout_returns_error_string_not_exception(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "무한루프",
            "kind": "custom_python",
            "config": {
                "function_source": (
                    "def tool_fn(query: str) -> str:\n"
                    "    while True:\n"
                    "        pass\n"
                ),
                "timeout_seconds": 1,
            },
        },
    )
    tool_id = resp.json()["id"]
    row = conn.execute(
        "SELECT * FROM tool_pool WHERE id = ?", (tool_id,)
    ).fetchone()
    tool = pool_registry._custom_python_crewai_tool(row)
    output = tool.run(query="x")
    assert "오류" in output or "시간 초과" in output or "sandbox timeout" in output
