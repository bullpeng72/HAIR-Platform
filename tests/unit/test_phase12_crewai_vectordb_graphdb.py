"""Phase 12 — 실제 CrewAI 연동 + VectorDB/GraphDB 기반 Agent 구현
(사용자 요청, 2026-09-26, "실제 구현해줘").

**비용/네트워크 발생 방지(중요)**: 이 세션에서 CrewAI 검증 중 실제로 한 번
비용이 발생하는 사고가 있었다 — `crewai.LLM.call`을 patch해도 CrewAI 내부
재시도 로직이 그걸 우회해 실제 OpenAI로 새어나갔다(mock.called=False인데도
진짜 응답이 왔다). 안전하게 검증되는 유일한 경계는 **`crewai.Crew.kickoff`
그 자체**다 — 이 아래(LLM.call 등)는 절대 patch 대상으로 삼지 않는다. 이
파일의 모든 CrewAI 테스트는 `patch("crewai.Crew.kickoff", ...)`만 쓴다.
임베딩(OpenAI)도 마찬가지로 `openai.OpenAI` 클라이언트 자체를 mock한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from hair_platform import agent_runtime, crew_runtime, graphquery, vectorstore


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


# ---------- crew_runtime: 단일 Agent 생성 ----------


def test_gc77_crewai_agent_generator_shapes_result_like_other_generators(
    client_and_conn,
):
    _client, conn = client_and_conn
    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("기획서 초안", ["기획서 초안"])
    ) as m:
        result = crew_runtime.crewai_agent_generator(
            conn, "기획자", ["요구사항정의"], provider="openai", model="gpt-4o-mini"
        )
    m.assert_called_once()
    assert result["role_name"] == "기획자"
    assert result["llm_provider"] == "openai"
    assert result["llm_model"] == "gpt-4o-mini"
    assert result["generated_output"] == "기획서 초안"
    assert result["framework"] == "crewai"
    assert "agents_yaml_snippet" in result  # 샌드박스 smoke-test가 이 키를 본다


def test_gc78_generate_with_framework_crewai_records_framework_column(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "설계자 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "설계자", "required_skills": ["아키텍처설계"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("설계 문서", ["설계 문서"]),
    ):
        resp = client.post(
            f"/api/briefs/{brief_id}/generate",
            json={"task_ids": [task_id], "framework": "crewai", "provider": "openai"},
        )
    assert resp.status_code == 201
    assert resp.json()["status"] == "succeeded"
    row = conn.execute("SELECT * FROM agent_pool WHERE role_name = '설계자'").fetchone()
    assert row["framework"] == "crewai"
    assert row["last_output"] == "설계 문서"


# ---------- crew_runtime: 다중 Agent 협업 ----------


def _make_two_active_agents(conn):
    ids = []
    for role, skills in [("기획자", ["요구사항정의"]), ("분석가", ["요구사항분석"])]:
        cur = conn.execute(
            "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
            (role, f'["{skills[0]}"]'),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_gc79_run_crew_chains_context_and_stores_each_agent_output(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종 산출물", ["기획서", "분석서"]),
    ) as m:
        result = crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="sequential",
            provider="openai",
        )
    m.assert_called_once()
    assert result["final_output"] == "최종 산출물"
    assert result["process"] == "sequential"
    assert [a["output"] for a in result["agents"]] == ["기획서", "분석서"]

    row1 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent1,)).fetchone()
    row2 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent2,)).fetchone()
    assert row1["last_output"] == "기획서"
    assert row2["last_output"] == "분석서"
    assert row1["framework"] == "crewai"

    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "crew_run_completed" in actions


def test_gc220_run_crew_preserves_each_agents_saved_model_config(client_and_conn):
    """사용자 요청(2026-10-06, "model provider/모델명이 AI 생성·관리·Crew
    실행 단계에 모두 있는데 최종 어떻게 적용되는지 분석" → "(b) 실행 후
    덮어쓰기만이라도 막는 방향으로 수정") — Crew 실행은 참여 Agent
    전원에게 요청 시점의 provider/model 하나를 동일하게 적용하는데,
    실행이 끝나면 그 값을 모든 참여 Agent의 `llm_provider`/`llm_model`
    (="AI 에이전트 관리"의 "모델 설정" 저장값, 단일 실행 경로
    `agent_runtime.execute_agent()`가 기본값으로 읽는 바로 그 컬럼)에
    덮어써 와서, Agent별로 따로 설정해둔 기본값이 Crew 실행 한 번으로
    조용히 사라지는 문제가 있었다. `last_output`/`framework`는 여전히
    갱신하되 `llm_provider`/`llm_model`은 더 이상 안 건드려야 한다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)
    # "AI 에이전트 관리 → 모델 설정"으로 Agent별 기본값을 미리 따로
    # 저장해둔 상태를 흉내낸다 — 이후 Crew 실행에 쓸 provider(openai)와는
    # 일부러 다르게 둔다.
    conn.execute(
        "UPDATE agent_pool SET llm_provider = 'anthropic', llm_model = 'claude-sonnet-5' "
        "WHERE id = ?",
        (agent1,),
    )
    conn.execute(
        "UPDATE agent_pool SET llm_provider = 'ollama', llm_model = 'qwen3-coder' "
        "WHERE id = ?",
        (agent2,),
    )
    conn.commit()

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종 산출물", ["기획서", "분석서"]),
    ):
        crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="sequential",
            provider="openai",
            model="gpt-4o-mini",
        )

    row1 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent1,)).fetchone()
    row2 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent2,)).fetchone()
    # 저장해둔 모델 설정은 Crew 실행과 무관하게 그대로 남아야 한다.
    assert row1["llm_provider"] == "anthropic"
    assert row1["llm_model"] == "claude-sonnet-5"
    assert row2["llm_provider"] == "ollama"
    assert row2["llm_model"] == "qwen3-coder"
    # last_output/framework는 여전히 이번 실행 결과로 갱신된다.
    assert row1["last_output"] == "기획서"
    assert row2["last_output"] == "분석서"
    assert row1["framework"] == "crewai"
    assert row2["framework"] == "crewai"


def test_gc80_run_crew_rejects_mismatched_lengths_and_inactive_agents(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, _agent2 = _make_two_active_agents(conn)

    with pytest.raises(ValueError):
        crew_runtime.run_crew(conn, [agent1], ["a", "b"], admin_id)

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'pending_onboarding')",
        ("품질 담당자",),
    )
    inactive_id = cur.lastrowid
    conn.commit()
    with pytest.raises(PermissionError):
        crew_runtime.run_crew(conn, [inactive_id], ["일해줘"], admin_id)


def test_gc198_run_crew_scores_gate_a_g_via_framework_crewai_adapter(client_and_conn):
    """사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
    연결해줘") — `run_crew()`가 `agent_evaluation.run_and_score_crew()`를
    거치면서 `framework="crewai"` 어댑터로 `tasks_output`에서
    agent_interactions를 자동 추출해 Gate A-G를 채점하고, 반환값에
    `gate_scores`/`tcr`로 노출해야 한다. gc79와 같은 셋업에 채점 검증만
    더한다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종 산출물", ["기획서", "분석서"]),
    ):
        result = crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="sequential",
            provider="openai",
        )

    # kickoff()가 반환하는 CrewOutput 자체는 채점 과정에서 변형되지 않는다
    # (_split_raw()가 raw_result만 caller에 돌려주는 SDK 동작 — Files
    # 섹션에서 확인한 계약).
    assert result["final_output"] == "최종 산출물"
    gate_scores = result["gate_scores"]
    assert set(gate_scores.keys()) == {"A", "B", "C", "D", "E", "F", "G"}
    for gate in gate_scores.values():
        assert set(gate.keys()) >= {"score", "status", "passed"}
    # tcr은 not_measured일 수 있어 None도 허용하되, 키 자체는 항상 있어야 함
    assert "tcr" in result

    row = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'crew_run_completed'"
    ).fetchone()
    logged = json.loads(row["after_value"])
    assert set(logged["gate_scores"].keys()) == {"A", "B", "C", "D", "E", "F", "G"}
    assert "tcr" in logged


def test_gc199_crew_run_endpoint_exposes_gate_scores(client_and_conn):
    """API 응답(`POST /api/crew/run`)에도 gate_scores/tcr이 그대로
    전달돼야 한다 — `run_crew_endpoint`가 `run_crew()`의 반환 dict를 그대로
    돌려주므로 엔드포인트 코드 변경 없이도 통과해야 한다."""
    client, conn = client_and_conn
    agent1, agent2 = _make_two_active_agents(conn)
    _login(client, "manager1", "managerpass")

    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("완료", ["산출물1", "산출물2"])
    ):
        resp = client.post(
            "/api/crew/run",
            json={
                "agent_ids": [agent1, agent2],
                "task_descriptions": ["a", "b"],
                "provider": "openai",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body["gate_scores"].keys()) == {"A", "B", "C", "D", "E", "F", "G"}
    assert "tcr" in body


def test_gc81_run_crew_captures_real_delegation_events():
    """CrewAI 이벤트버스에서 실제로 위임 도구 사용 이벤트가 오면 정직하게
    캡처하는지 확인한다(추측이 아니라 실제 이벤트 기반).

    **정정(2026-10-06, 사용자 요청 "묶인 Agent간 협업 관계를 다이어그램
    으로 표시")** — "누가 누구에게 무엇을
    위임했는지" 기록을 약속했는데, 실제 구현은 "누구
    에게"(`event.tool_args`의 `coworker`)를 읽지도 않고 버리고 있었다 —
    다이어그램에 화살표 대상이 필요해 다시 보니 발견한 문서-코드
    불일치였다. `to_agent_role`을 추가해 메운다."""
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'a', 'x', 'admin')"
    )
    agent1, agent2 = _make_two_active_agents(conn)

    def _kickoff_with_delegation(self, *args, **kwargs):
        event = ToolUsageFinishedEvent(
            tool_name="Delegate work to coworker",
            tool_args={"coworker": "분석가"},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            output="ok",
            agent_role="기획자",
        )
        crewai_event_bus.emit(object(), event)
        return _crew_output("최종 산출물", ["기획서", "분석서"])

    with patch("crewai.Crew.kickoff", new=_kickoff_with_delegation):
        result = crew_runtime.run_crew(
            conn, [agent1, agent2], ["a", "b"], 1, provider="openai"
        )

    assert len(result["delegations"]) == 1
    assert result["delegations"][0]["from_agent_role"] == "기획자"
    assert result["delegations"][0]["to_agent_role"] == "분석가"
    assert result["delegations"][0]["tool"] == "Delegate work to coworker"

    actions = [
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    ]
    assert "agent_delegation_occurred" in actions


def test_gc219_resolve_delegation_target_sanitizes_like_crewai_and_falls_back():
    """`_resolve_delegation_target()`은 CrewAI 자신의 `sanitize_agent_name()`
    (공백 정리+소문자화)과 같은 기준으로 매칭해야, LLM이 대소문자/공백을
    다르게 써내도(실사용에서 흔함) 실제 Agent로 정확히 맞춰진다. 매칭
    실패(존재하지 않는 동료를 지목 — CrewAI 쪽에서도 에러가 나는 상황)
    시에는 원문을 그대로 보존해 정보를 버리지 않는다."""
    known_roles = ["백엔드 개발자", "QA 엔지니어"]

    assert (
        crew_runtime._resolve_delegation_target(
            {"coworker": "  QA   엔지니어  "}, known_roles
        )
        == "QA 엔지니어"
    )
    assert (
        crew_runtime._resolve_delegation_target(
            {"co_worker": "백엔드 개발자"}, known_roles
        )
        == "백엔드 개발자"
    )
    # 대괄호로 감싼 리스트 형태(LLM이 가끔 이렇게 써냄, base_agent_tools.py
    # 참고)도 첫 항목만 취해 매칭한다.
    assert (
        crew_runtime._resolve_delegation_target(
            {"coworker": "[QA 엔지니어, 백엔드 개발자]"}, known_roles
        )
        == "QA 엔지니어"
    )
    # 존재하지 않는 동료 — 원문을 그대로 보존(정보 손실 없음).
    assert (
        crew_runtime._resolve_delegation_target(
            {"coworker": "데이터 과학자"}, known_roles
        )
        == "데이터 과학자"
    )
    # coworker 키 자체가 없으면 None.
    assert crew_runtime._resolve_delegation_target({"task": "x"}, known_roles) is None
    assert crew_runtime._resolve_delegation_target("not a dict", known_roles) is None


def test_gc82_crew_run_endpoint_rbac_and_errors(client_and_conn):
    client, conn = client_and_conn
    agent1, agent2 = _make_two_active_agents(conn)

    _login(client, "member1", "memberpass")
    resp = client.post(
        "/api/crew/run",
        json={"agent_ids": [agent1, agent2], "task_descriptions": ["a", "b"]},
    )
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/crew/run",
        json={"agent_ids": [agent1], "task_descriptions": ["a", "b"]},
    )
    assert resp.status_code == 400

    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("완료", ["산출물1", "산출물2"])
    ):
        resp = client.post(
            "/api/crew/run",
            json={
                "agent_ids": [agent1, agent2],
                "task_descriptions": ["a", "b"],
                "provider": "openai",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["final_output"] == "완료"


# ---------- crew_runtime: CrewAI 기능 적용 리뷰에서 발견한 버그 수정
# (사용자 요청, 2026-09-27, "crewai 기능을 적절히 적용되었는지 분석해줘") ----------


def test_gc98_run_crew_hierarchical_with_manager_agent_id_does_not_crash(
    client_and_conn,
):
    """실측으로 확인한 실사용 크래시 버그 회귀 테스트 — manager_agent_id가
    가리키는 Agent가 Crew(agents=...)에도 같이 들어가면 CrewAI 자체
    validator(check_manager_llm)가 ValidationError(manager_agent_in_agents)를
    던졌다. 매니저는 agents/tasks 목록에서 빠지고 manager_agent로만
    지정돼야 한다.

    정정(2026-10-18, "매니저가 하위 과업을 실제로 쪼개서 위임하도록
    개선해줘") — 예전엔 (매니저 제외) 멤버 수만큼 완전히 동일한 Task를
    1:1로 미리 만들어뒀었다. 그런데 CrewAI는 hierarchical에서
    `_get_agent_to_use()`가 Task.agent를 무시하고 항상 매니저가 직접
    실행하므로(crew.py:1183), 그 구조로는 매니저가 "똑같은 통짜 과업"을
    멤버 수만큼 반복해서 아무 코워커에게나 떠넘기는 식으로 동작해 거의
    동일한 완성품이 멤버 수만큼 쏟아졌다(실측: 11명 팀 → 완성품 11개).
    이제는 Task를 하나만 만들어 매니저가 코워커들에게 역할별 하위 과업을
    실제로 쪼개 위임하고, 그 결과를 통합한 **하나의** 최종 산출물을
    내도록 바꿨다 — 그래서 `result["agents"]`는 이제 매니저 1건만
    돌려주고, 그 `output`은 더 이상 None이 아니라 통합된 실제 결과물이다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(
        conn
    )  # agent1=기획자(매니저), agent2=분석가

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("최종 산출물", ["최종 산출물"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        result = crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="hierarchical",
            manager_agent_id=agent1,
            provider="openai",
        )

    crew = captured["crew"]
    assert len(crew.agents) == 1  # 매니저(agent1)는 agents 목록에서 빠졌다
    assert crew.manager_agent is not None
    assert crew.memory is True  # memory=True 반영 확인
    assert len(crew.tasks) == 1  # 코워커에게 쪼개 위임할 Task가 하나뿐
    assert crew.tasks[0].agent is None  # 특정 워커에 미리 고정하지 않음

    outputs = {a["agent_id"]: a["output"] for a in result["agents"]}
    assert agent2 not in outputs  # 워커별 개별 산출물이 아니라 매니저 통합본 하나
    assert outputs[agent1] == "최종 산출물"  # 매니저가 위임·통합한 최종 결과물

    row1 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent1,)).fetchone()
    row2 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent2,)).fetchone()
    assert row1["last_output"] == "최종 산출물"
    assert row2["last_output"] is None  # 분석가 행은 이 경로에서 갱신되지 않음


def test_gc99_run_crew_validates_manager_agent_id_usage(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    with pytest.raises(ValueError):
        # sequential에서는 manager_agent_id를 쓸 수 없다
        crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["a", "b"],
            admin_id,
            process="sequential",
            manager_agent_id=agent1,
        )

    with pytest.raises(ValueError):
        # agent_ids에 없는 manager_agent_id
        crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["a", "b"],
            admin_id,
            process="hierarchical",
            manager_agent_id=999999,
        )


def test_gc100_run_crew_sets_max_iter_and_memory_by_default(client_and_conn):
    """설계 문서가 스스로 "반드시 걸어야 한다"고 적어뒀던 max_iter 상한과,
    약속한 memory=True가 실제로 세팅되는지 확인한다(둘 다 이전에는
    코드에 반영되지 않았던 문서-코드 불일치였다)."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("최종 산출물", ["기획서", "분석서"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["a", "b"], admin_id, provider="openai"
        )

    crew = captured["crew"]
    assert crew.memory is True
    assert all(a.max_iter == crew_runtime.DELEGATION_MAX_ITER for a in crew.agents)


def test_gc101_run_crew_records_audit_log_per_task_via_task_callback(client_and_conn):
    """사용자 요청(2026-09-27, "다른 리뷰 항목 마저 진행해줘") — task_callback으로
    Task 완료마다 감사로그를 남기는지 확인한다. `crewai.Crew.kickoff`를
    실제 CrewAI가 하는 것과 동일하게 각 Task 완료 시점마다
    `self.task_callback(task_output)`을 호출하는 side_effect로 대체해
    콜백 배선 자체가 진짜로 동작하는지 검증한다(추측 아님, 위임 이벤트
    캡처 테스트와 같은 정신)."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    def _kickoff_with_task_callback(self, *args, **kwargs):
        from crewai.tasks.task_output import TaskOutput

        outputs = [
            TaskOutput(description="task-0", agent="기획자", raw="기획서"),
            TaskOutput(description="task-1", agent="분석가", raw="분석서"),
        ]
        for output in outputs:
            self.task_callback(output)
        return _crew_output("최종 산출물", ["기획서", "분석서"])

    with patch("crewai.Crew.kickoff", new=_kickoff_with_task_callback):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["a", "b"], admin_id, provider="openai"
        )

    rows = conn.execute(
        "SELECT action, after_value FROM audit_log WHERE action = 'crew_task_completed' "
        "ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    roles = [json.loads(r["after_value"])["agent_role"] for r in rows]
    assert roles == ["기획자", "분석가"]
    assert json.loads(rows[0]["after_value"])["output_preview"] == "기획서"


def test_gc102_task_callback_attributes_hierarchical_output_to_real_worker(
    client_and_conn,
):
    """실사용 중 발견한 버그 회귀 테스트(2026-09-27, 사용자가 Web UI에서
    manager_agent_id=1(기획자)로 hierarchical 실행 후 재현) — 원래는
    (매니저 제외) 멤버 수만큼 완전히 동일한 Task를 1:1로 미리 만들어둔
    구조였다. CrewAI 소스 확인 결과 hierarchical에서는 `TaskOutput.agent`가
    **항상 매니저의 role**로 채워지는데(`crew.py`의 `_get_agent_to_use`),
    그 구조에서는 매니저가 실제로는 분석가 몫의 작업을 만들어냈어도
    `task_output.agent == "기획자"`로 와서 감사로그가 실제 담당자를
    놓쳤다 — 그래서 당시엔 `worker_rows` 순서로 실제 담당 Agent(분석가)를
    되찾아 귀속시켰다.

    정정(2026-10-18, "매니저가 하위 과업을 실제로 쪼개서 위임하도록
    개선해줘") — 이제는 Task가 하나뿐이고, 그 결과는 매니저가 코워커들
    에게 위임해 받은 결과를 **직접 통합**한 진짜 매니저 자신의 산출물
    이다(어느 특정 워커가 혼자 몰래 떠안은 게 아니다 — 실제로 누구에게
    무엇을 위임했는지는 `delegations`/`agent_delegation_occurred`
    감사로그에 별도로 남는다). 그래서 이제는 "기획자"로 귀속되는 게
    왜곡이 아니라 정답이다 — 더 이상 `worker_rows`로 되돌릴 대상이
    없다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(
        conn
    )  # agent1=기획자(매니저), agent2=분석가

    def _kickoff_single_integrated_task(self, *args, **kwargs):
        from crewai.tasks.task_output import TaskOutput

        output = TaskOutput(description="task-0", agent="기획자", raw="데이터 정의서")
        self.task_callback(output)
        return _crew_output("데이터 정의서", ["데이터 정의서"])

    with patch("crewai.Crew.kickoff", new=_kickoff_single_integrated_task):
        result = crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="hierarchical",
            manager_agent_id=agent1,
            provider="openai",
        )

    row = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'crew_task_completed'"
    ).fetchone()
    logged = json.loads(row["after_value"])
    assert logged["agent_role"] == "기획자"  # 매니저 본인이 통합한 결과물
    assert logged["output_preview"] == "데이터 정의서"

    outputs = {a["agent_id"]: a["output"] for a in result["agents"]}
    assert outputs[agent1] == "데이터 정의서"
    assert agent2 not in outputs


# ---------- crew_runtime: MCP 연동(사용자 요청 2026-09-27,
# "crewai-tools의 MCPServerAdapter... 참조해 mcp 기능을 구현해줘") ----------


def _fake_tool(name="웹검색"):
    """`crewai.tools.BaseTool`을 실제로 상속한 가짜 MCP 도구 — CrewAI의
    `Agent.tools: list[BaseTool]`는 pydantic이 타입을 검증하므로 아무
    객체나 넣으면 Agent 생성 자체가 실패한다(실측 확인)."""
    from crewai.tools import BaseTool

    class _Tool(BaseTool):
        def _run(self, **kwargs):
            return "ok"

    return _Tool(name=name, description="테스트용 가짜 MCP 도구")


class _FakeMCPServerAdapter:
    """`crewai_tools.MCPServerAdapter`의 안전한 mock 경계 — 실제 클래스는
    생성 시점에 진짜 네트워크/프로세스 접속을 시도하므로(`connect_timeout`
    기본 30초), 접속 가능한 MCP 서버가 없는 이 환경에서는 이 클래스 자체를
    patch하는 것이 유일하게 안전한 방법이다(`crewai.Crew.kickoff`를 patch
    하는 것과 같은 정신)."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, serverparams, *tool_names, connect_timeout=30):
        self.serverparams = serverparams
        self.tool_names = tool_names
        self.stopped = False
        _FakeMCPServerAdapter.calls.append(
            {"serverparams": serverparams, "tool_names": tool_names}
        )

    def __enter__(self):
        return [_fake_tool()]

    def __exit__(self, *exc):
        self.stopped = True
        return False


def test_gc103_mcp_tools_helper_short_circuits_when_no_servers_configured():
    """mcp_servers가 비어 있으면 crewai_tools/mcp를 아예 import하지 않는다
    (지연 import 원칙 — 설치 안 해도 기존 crewai 경로는 그대로 동작해야
    한다). import 자체를 안 하는지는 `sys.modules`에서 지워도 에러가 안
    나는 것으로 간접 확인한다."""
    import sys
    from contextlib import ExitStack

    had_crewai_tools = sys.modules.pop("crewai_tools", None)
    try:
        with ExitStack() as stack:
            result = crew_runtime._mcp_tools(stack, [])
        assert result == []
        assert "crewai_tools" not in sys.modules  # import를 안 했다
    finally:
        if had_crewai_tools is not None:
            sys.modules["crewai_tools"] = had_crewai_tools


def test_gc104_run_crew_attaches_real_mcp_tools_from_configured_agent_servers(
    client_and_conn,
):
    """Agent별 `mcp_servers` 설정(`PATCH /api/agent-pool/{id}/model`로 저장)이
    실제로 `MCPServerAdapter`를 통해 그 Agent의 `tools`에 붙는지 확인한다.
    `crewai_tools.MCPServerAdapter` 자체를 mock해 안전하게 검증한다(실제
    접속 가능한 MCP 서버가 없으므로)."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    mcp_config = {
        "url": "http://localhost:9999/mcp",
        "transport": "streamable-http",
        "tool_names": [],
    }
    conn.execute(
        "UPDATE agent_pool SET mcp_servers = ? WHERE id = ?",
        (json.dumps([mcp_config]), agent1),
    )
    conn.commit()

    _FakeMCPServerAdapter.calls = []
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("최종 산출물", ["기획서", "분석서"])

    with (
        patch("crewai_tools.MCPServerAdapter", _FakeMCPServerAdapter),
        patch("crewai.Crew.kickoff", new=_kickoff_capture),
    ):
        crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="sequential",
            provider="openai",
        )

    assert len(_FakeMCPServerAdapter.calls) == 1
    assert _FakeMCPServerAdapter.calls[0]["serverparams"] == {
        "url": "http://localhost:9999/mcp",
        "transport": "streamable-http",
    }

    crew = captured["crew"]
    agent1_tools = crew.agents[0].tools
    agent2_tools = crew.agents[1].tools
    assert len(agent1_tools) == 1
    assert agent1_tools[0].name == "웹검색"
    assert agent2_tools == []  # agent2는 mcp_servers를 설정하지 않았다


def test_gc105_agent_model_config_endpoint_persists_mcp_servers(client_and_conn):
    """`PATCH /api/agent-pool/{id}/model`로 저장한 mcp_servers가 실제로
    DB에 남고, mcp_servers를 아예 안 보내면(None) 기존 설정을 보존하는지
    확인한다(부분 갱신 계약)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    (agent1,) = _make_two_active_agents(conn)[:1]

    resp = client.patch(
        f"/api/agent-pool/{agent1}/model",
        json={
            "provider": "openai",
            "mcp_servers": [{"url": "http://localhost:9999/mcp", "transport": "sse"}],
        },
    )
    assert resp.status_code == 200

    row = conn.execute(
        "SELECT mcp_servers FROM agent_pool WHERE id = ?", (agent1,)
    ).fetchone()
    saved = json.loads(row["mcp_servers"])
    assert len(saved) == 1
    assert saved[0]["url"] == "http://localhost:9999/mcp"

    # mcp_servers를 안 보내면 기존 설정이 그대로 보존돼야 한다(부분 갱신).
    resp = client.patch(
        f"/api/agent-pool/{agent1}/model", json={"provider": "anthropic"}
    )
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT mcp_servers FROM agent_pool WHERE id = ?", (agent1,)
    ).fetchone()
    assert json.loads(row["mcp_servers"]) == saved


# ---------- crew_runtime: CrewAI 개념 활용도 감사(사용자 요청, 2026-09-27,
# "CrewAI 기능/개념을 제대로 활용하도록 구현되어 있는지 확인해줘") ----------


def test_gc106_run_crew_attaches_real_crewai_tool_for_graphdb_agent(client_and_conn):
    """실측으로 발견한 갭 회귀 테스트 — `agent_runtime.execute_agent()`(개별
    실행)는 vectordb/graphdb skill_tags Agent에 RAG 증강을 붙이는데
    `crew_runtime.run_crew()`(Crew 협업)에는 전혀 안 붙어 있었다.

    **정정(2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록
    개선해줘")**: 처음엔 execute_agent()와 똑같이 Task description에
    정적으로 끼워 넣는 방식으로 고쳤었지만, 이번 요청으로 진짜 CrewAI
    Tool을 붙이는 방식으로 바꿨다 — description은 원문 그대로 두고,
    Agent의 `tools`에 실제 "org_relationship_query" Tool이 붙어서 Agent가
    스스로 호출 여부를 판단하게 한다. (CrewAI Tool 함수명은 2026-10-06에
    한글→ASCII로 변경됨 — 설치된 crewai 버전이 함수명을
    ASCII로 정규화하며 비-ASCII 문자를 전부 지워 한글 전용 이름이 빈
    문자열이 되는 크래시를 막기 위함.)"""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('기획자', '[\"요구사항정의\"]', 'active')"
    )
    normal_agent = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('관계분석 Agent', '[\"graphdb\"]', 'active')"
    )
    graphdb_agent = cur.lastrowid
    conn.commit()

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("최종 산출물", ["기획서", "관계 기반 답변"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn,
            [normal_agent, graphdb_agent],
            ["기획서를 작성해줘", "누가 이 업무를 맡고 있어?"],
            admin_id,
            process="sequential",
            provider="openai",
        )

    crew = captured["crew"]
    # description은 정적 주입 없이 원문 그대로다.
    assert crew.tasks[0].description == "기획서를 작성해줘"
    assert crew.tasks[1].description == "누가 이 업무를 맡고 있어?"
    # 대신 실제 CrewAI Tool이 해당 Agent에만 붙는다.
    assert crew.agents[0].tools == []
    tool_names = [t.name for t in crew.agents[1].tools]
    assert "org_relationship_query" in tool_names


def test_gc107_crewai_agent_generator_attaches_vectordb_tool(client_and_conn):
    """생성 시점(`crewai_agent_generator`)에도 rag 스킬을 요청하면
    실제 CrewAI Tool이 붙는지 확인한다(사용자 요청, 2026-09-27 — "이미
    생성된 또는 생성하는 AI Agent"에서 둘 다 동작해야 한다는 요구).
    (원래 "vectordb" 별칭으로도 됐으나 자동완성 중복 정리로 2026-10-06에
    "rag"로 통합됨.)"""
    _client, conn = client_and_conn
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output("검색 결과 요약", ["검색 결과 요약"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.crewai_agent_generator(
            conn, "지식검색 Agent", ["rag"], provider="openai"
        )

    crew = captured["crew"]
    tool_names = [t.name for t in crew.agents[0].tools]
    assert "knowledge_base_search" in tool_names


# ---------- vectorstore(VectorDB 지식검색 Agent) ----------


def _fake_openai_embeddings(vectors: dict[str, list[float]]):
    """텍스트별 고정 벡터를 돌려주는 가짜 OpenAI 임베딩 클라이언트."""

    def _create(model, input):
        vec = vectors.get(input, [0.0, 0.0, 0.0])
        item = MagicMock()
        item.embedding = vec
        resp = MagicMock()
        resp.data = [item]
        return resp

    client = MagicMock()
    client.embeddings.create.side_effect = _create
    return client


def test_gc83_vectorstore_index_and_search_ranks_by_cosine_similarity(client_and_conn):
    _client, conn = client_and_conn
    fake_client = _fake_openai_embeddings(
        {
            "파이썬 백엔드 설계 문서": [1.0, 0.0, 0.0],
            "프론트엔드 UI 문서": [0.0, 1.0, 0.0],
            "백엔드 아키텍처가 궁금해": [0.9, 0.1, 0.0],
        }
    )
    with patch("openai.OpenAI", return_value=fake_client):
        vectorstore.index_text(conn, None, "파이썬 백엔드 설계 문서")
        vectorstore.index_text(conn, None, "프론트엔드 UI 문서")
        hits = vectorstore.search(conn, "백엔드 아키텍처가 궁금해", top_k=1)

    assert len(hits) == 1
    assert hits[0]["text"] == "파이썬 백엔드 설계 문서"
    assert fake_client.embeddings.create.call_count == 3


def test_gc84_vectorstore_ignores_embeddings_from_different_model():
    conn = __import__("sqlite3").connect(":memory:")
    conn.row_factory = __import__("sqlite3").Row
    from hair_platform import db

    db.init_schema(conn)
    conn.execute(
        "INSERT INTO knowledge_chunks "
        "(source_agent_id, text, embedding, embedding_provider, embedding_model) "
        "VALUES (NULL, 'x', '[1.0, 0.0]', 'openai', 'text-embedding-old')"
    )
    conn.commit()
    fake_client = _fake_openai_embeddings({"query": [1.0, 0.0]})
    with patch("openai.OpenAI", return_value=fake_client):
        hits = vectorstore.search(conn, "query")
    assert hits == []  # 다른 임베딩 모델의 벡터와는 비교하지 않는다


def test_gc85_embed_text_rejects_non_openai_provider():
    with pytest.raises(ValueError):
        vectorstore.embed_text("텍스트", provider="anthropic")


def test_gc87_truncate_for_embedding_leaves_short_text_untouched():
    short = "짧은 텍스트"
    assert vectorstore._truncate_for_embedding(short) == short


def test_gc87_truncate_for_embedding_caps_long_text_to_the_tail():
    long_text = "가" * (vectorstore._MAX_EMBED_CHARS + 500)
    truncated = vectorstore._truncate_for_embedding(long_text)
    assert len(truncated) == vectorstore._MAX_EMBED_CHARS
    assert truncated == long_text[-vectorstore._MAX_EMBED_CHARS :]


def test_gc87_embed_text_sends_truncated_input_to_openai():
    """사용자 보고(2026-10-14, "Error code: 400 - ... maximum context
    length is 8192 tokens") 재현 — 대화 transcript처럼 아주 긴 텍스트를
    그대로 넘겨도 실제 API 호출에는 잘린 입력만 전달돼야 한다."""
    captured = {}

    def _create(model, input):
        captured["input"] = input
        item = MagicMock()
        item.embedding = [0.1, 0.2]
        resp = MagicMock()
        resp.data = [item]
        return resp

    client = MagicMock()
    client.embeddings.create.side_effect = _create

    huge_text = "질문입니다 " * 3000  # _MAX_EMBED_CHARS(6000)보다 한참 긺
    assert len(huge_text) > vectorstore._MAX_EMBED_CHARS
    with patch("openai.OpenAI", return_value=client):
        vectorstore.embed_text(huge_text)

    assert len(captured["input"]) == vectorstore._MAX_EMBED_CHARS
    assert captured["input"] == huge_text[-vectorstore._MAX_EMBED_CHARS :]


# ---------- graphquery(GraphDB 관계분석 Agent) + 성과 데이터 경계 ----------


def test_gc86_graphquery_never_selects_performance_columns():
    """경계를 소스 레벨에서 강제한다 — 실제 SQL 쿼리 문자열만 검사한다
    (docstring 설명 문구에 컬럼명이 언급되는 것과는 별개로, 진짜 SELECT
    구문에 성과 컬럼이 들어가면 이 테스트가 잡는다)."""
    import inspect
    import re

    source = inspect.getsource(graphquery)
    sql_snippets = re.findall(r'conn\.execute\(\s*"([^"]*(?:"\s*"[^"]*)*)"', source)
    assert sql_snippets, "SQL 쿼리를 하나도 못 찾았습니다 — 정규식을 다시 확인하세요"
    for sql in sql_snippets:
        assert "performance_score" not in sql
        assert "performance_consent" not in sql


def test_gc87_manager_chain_and_direct_reports(client_and_conn):
    client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    conn.execute("UPDATE users SET manager_id = ? WHERE id = ?", (admin_id, manager_id))
    conn.commit()

    chain = graphquery.manager_chain(conn, manager_id)
    assert chain == [{"id": admin_id, "username": "admin1"}]

    reports = graphquery.direct_reports(conn, admin_id)
    assert reports == [{"id": manager_id, "username": "manager1"}]
    _client = client  # 픽스처 사용 흔적(사용하지 않으면 unused 경고)


def test_gc88_task_assignment_owner_and_context_for_prompt(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]
    resp = client.post(
        f"/api/tasks/{task_id}/assignments",
        json={"resource_type": "human", "resource_id": human_id},
    )
    approval_id = resp.json()["approval_id"]
    _login(client, "admin1", "adminpass")
    client.post(f"/api/approvals/{approval_id}/decide", json={"approve": True})

    owner = graphquery.task_assignment_owner(conn, task_id)
    assert owner["resource_type"] == "human"
    assert owner["resource_id"] == human_id
    assert owner["requested_by_username"] == "manager1"

    context = graphquery.context_for_prompt(conn)
    assert f"task#{task_id}" in context
    assert "백엔드 개발자" in context


def test_gc89_context_for_prompt_handles_empty_org(client_and_conn):
    _client, conn = client_and_conn
    assert graphquery.context_for_prompt(conn) == "현재 확정된 배정 관계가 없습니다."


# ---------- agent_runtime: RAG/그래프 증강 실행 ----------


def test_gc90_execute_agent_augments_prompt_for_vectordb_role(client_and_conn):
    """(원래 "vectordb"/"rag" 두 별칭을 같이 넣어 테스트했으나, 자동완성
    중복 정리로 2026-10-06에 "vectordb"가 "rag"로 통합돼 "rag" 하나만
    남음.)"""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('지식검색 Agent', '[\"rag\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with (
        patch(
            "hair_platform.vectorstore.search",
            return_value=[
                {
                    "id": 1,
                    "text": "과거 산출물 A",
                    "source_agent_id": None,
                    "score": 0.9,
                }
            ],
        ) as search_mock,
        patch(
            "hair_platform.llm.call_llm", return_value="검색 결과 기반 답변"
        ) as llm_mock,
    ):
        output = agent_runtime.execute_agent(
            conn, agent_id, admin_id, "결제 모듈 설계 사례 있어?", provider="openai"
        )

    search_mock.assert_called_once()
    assert output == "검색 결과 기반 답변"
    augmented_prompt = llm_mock.call_args[0][3]
    assert "과거 산출물 A" in augmented_prompt
    assert "결제 모듈 설계 사례 있어?" in augmented_prompt


def test_gc91_execute_agent_augments_prompt_for_graphdb_role(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('관계분석 Agent', '[\"graphdb\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="관계 기반 답변") as llm_mock:
        output = agent_runtime.execute_agent(
            conn, agent_id, admin_id, "누가 이 업무를 맡고 있어?", provider="openai"
        )

    assert output == "관계 기반 답변"
    augmented_prompt = llm_mock.call_args[0][3]
    assert "확정된 배정" in augmented_prompt
    assert "누가 이 업무를 맡고 있어?" in augmented_prompt


def test_gc92_execute_agent_does_not_augment_normal_role(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('Backend 개발자', '[\"python\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with patch("hair_platform.llm.call_llm", return_value="일반 응답") as llm_mock:
        agent_runtime.execute_agent(
            conn, agent_id, admin_id, "API 만들어줘", provider="openai"
        )
    assert llm_mock.call_args[0][3] == "API 만들어줘"  # 증강 없이 그대로


def test_gc195_execute_agent_augments_prompt_for_websearch_role(client_and_conn):
    """사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
    API를... 추가해줘") — "외부검색" skill_tags를 가진 Agent는
    vectordb·graphdb와 같은 방식으로 실행 전에
    `websearch.search()` 결과를 프롬프트에 덧붙여야 한다. (원래
    "websearch" 별칭으로도 됐으나 자동완성 중복 리포트로 2026-10-06에
    "외부검색"으로 통합됨.)"""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('외부지식 Agent', '[\"외부검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with (
        patch(
            "hair_platform.websearch.search",
            return_value=[
                {"title": "원격근무", "snippet": "원격근무 설명", "url": "https://x"}
            ],
        ) as search_mock,
        patch(
            "hair_platform.llm.call_llm", return_value="외부 자료 기반 답변"
        ) as llm_mock,
    ):
        output = agent_runtime.execute_agent(
            conn, agent_id, admin_id, "원격근무 정책이 뭐야?", provider="openai"
        )

    search_mock.assert_called_once()
    assert output == "외부 자료 기반 답변"
    augmented_prompt = llm_mock.call_args[0][3]
    assert "원격근무 설명" in augmented_prompt
    assert "원격근무 정책이 뭐야?" in augmented_prompt


# ---------- index-knowledge ----------


def test_gc93_index_knowledge_endpoint(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status, last_output) VALUES "
        "('기획자', '[]', 'active', '기획서 산출물')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/index-knowledge", json={})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.post("/api/agent-pool/999999/index-knowledge", json={})
    assert resp.status_code == 404

    fake_client = _fake_openai_embeddings({"기획서 산출물": [1.0, 0.0]})
    with patch("openai.OpenAI", return_value=fake_client):
        resp = client.post(f"/api/agent-pool/{agent_id}/index-knowledge", json={})
    assert resp.status_code == 200
    chunk = conn.execute(
        "SELECT * FROM knowledge_chunks WHERE id = ?", (resp.json()["chunk_id"],)
    ).fetchone()
    assert chunk["text"] == "기획서 산출물"


def test_gc94_index_knowledge_fails_clearly_with_no_text(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('신규 Agent', '[]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    _login(client, "manager1", "managerpass")
    resp = client.post(f"/api/agent-pool/{agent_id}/index-knowledge", json={})
    assert resp.status_code == 400
