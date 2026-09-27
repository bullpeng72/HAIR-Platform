"""Phase 12 — 실제 CrewAI 연동 + VectorDB/GraphDB 기반 Agent 구현
(사용자 요청, 2026-09-26, "실제 구현해줘" — ADR.md 결정 10·11의 실제 구현).

**비용/네트워크 발생 방지(중요)**: 이 세션에서 CrewAI 검증 중 실제로 한 번
비용이 발생하는 사고가 있었다 — `crewai.LLM.call`을 patch해도 CrewAI 내부
재시도 로직이 그걸 우회해 실제 OpenAI로 새어나갔다(mock.called=False인데도
진짜 응답이 왔다). 안전하게 검증되는 유일한 경계는 **`crewai.Crew.kickoff`
그 자체**다 — 이 아래(LLM.call 등)는 절대 patch 대상으로 삼지 않는다. 이
파일의 모든 CrewAI 테스트는 `patch("crewai.Crew.kickoff", ...)`만 쓴다.
임베딩(OpenAI)도 마찬가지로 `openai.OpenAI` 클라이언트 자체를 mock한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


# ---------- crew_runtime: 단일 Agent 생성(ADR 결정 10) ----------


def test_gc77_crewai_agent_generator_shapes_result_like_other_generators():
    with patch(
        "crewai.Crew.kickoff", return_value=_crew_output("기획서 초안", ["기획서 초안"])
    ) as m:
        result = crew_runtime.crewai_agent_generator(
            "기획자", ["요구사항정의"], provider="openai", model="gpt-4o-mini"
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


# ---------- crew_runtime: 다중 Agent 협업(ADR 결정 11) ----------


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


def test_gc81_run_crew_captures_real_delegation_events():
    """CrewAI 이벤트버스에서 실제로 위임 도구 사용 이벤트가 오면 정직하게
    캡처하는지 확인한다(추측이 아니라 실제 이벤트 기반)."""
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
    assert result["delegations"][0]["tool"] == "Delegate work to coworker"

    actions = [
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    ]
    assert "agent_delegation_occurred" in actions


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
    지정돼야 한다."""
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
        return _crew_output("최종 산출물", ["분석서"])

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
    assert crew.memory is True  # ADR 결정 10 약속(memory=True) 반영 확인

    outputs = {a["agent_id"]: a["output"] for a in result["agents"]}
    assert outputs[agent2] == "분석서"
    assert outputs[agent1] is None  # 매니저는 자기 몫의 산출물이 없다

    row1 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent1,)).fetchone()
    row2 = conn.execute("SELECT * FROM agent_pool WHERE id = ?", (agent2,)).fetchone()
    assert row2["last_output"] == "분석서"
    assert row1["last_output"] is None  # 매니저 행은 실행되지 않았으니 갱신 안 됨


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
    """ADR 결정 11이 스스로 "반드시 걸어야 한다"고 적어뒀던 max_iter 상한과,
    결정 10이 약속한 memory=True가 실제로 세팅되는지 확인한다(둘 다 이전에는
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


# ---------- graphquery(GraphDB 관계분석 Agent) + NF9 경계 ----------


def test_gc86_graphquery_never_selects_performance_columns():
    """NF9 경계를 소스 레벨에서 강제한다 — 실제 SQL 쿼리 문자열만 검사한다
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
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('지식검색 Agent', '[\"vectordb\", \"rag\"]', 'active')"
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
