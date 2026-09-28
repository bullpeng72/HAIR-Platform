"""Phase 14 — Skill Pool·Tool Pool 실제 구현(ADR.md 결정 12, 사용자 요청,
2026-09-27, "tool_pool·skill_pool 테이블도 실제로 구현해줘").

이전 라운드("MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘")에서는
VectorDB/GraphDB를 진짜 CrewAI Tool로 바꿨지만, "스킬→도구" 매핑 자체는
`agent_runtime.VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS`에 여전히
하드코딩돼 있었다. 이번에 `tool_pool`/`skill_pool`/`agent_tool_links`
테이블과 `pool_registry.py`를 실제로 구현해 그 매핑을 데이터로 옮겼다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import db, pool_registry


def test_gc108_init_schema_seeds_default_vectordb_graphdb_pools():
    """스키마 초기화 시 VectorDB/GraphDB native Tool과, 이걸 쓰는 skill_pool
    항목(vectordb/rag/graphdb/관계질의)이 자동으로 채워져야 한다 — 이전에
    코드로 하드코딩됐던 매핑이 데이터로도 그대로 살아있어야 기존 동작이
    안 깨진다. `init_schema()`를 두 번 불러도 중복 생성되지 않아야 한다
    (멱등성)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 멱등성 확인

    tools = pool_registry.list_tools(conn)
    tool_names = {t["name"] for t in tools}
    native_names = {"VectorDB 지식검색", "GraphDB 관계조회"}
    assert native_names <= tool_names
    for t in tools:
        if t["name"] in native_names:
            assert t["kind"] == "native"
            assert t["is_active"] is True

    skills = pool_registry.list_skills(conn)
    skill_names = {s["name"] for s in skills}
    vectordb_graphdb_names = {"vectordb", "rag", "graphdb", "관계질의"}
    assert vectordb_graphdb_names <= skill_names
    for s in skills:
        if s["name"] in vectordb_graphdb_names:
            assert s["augmentation_mode"] == "tool"
            assert len(s["default_tool_pool_ids"]) == 1


def test_gc109_create_tool_rejects_unknown_kind_and_native_id():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    with pytest.raises(ValueError):
        pool_registry.create_tool(conn, 1, "이상한도구", "", "not_a_kind", {})
    with pytest.raises(ValueError):
        pool_registry.create_tool(
            conn, 1, "이상한네이티브", "", "native", {"native_id": "존재안함"}
        )


def test_gc110_create_tool_and_toggle_active(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    tool_id = pool_registry.create_tool(
        conn,
        admin_id,
        "사내 검색 MCP",
        "사내 문서 검색 서버",
        "mcp_remote",
        {"url": "http://localhost:9000/mcp", "transport": "sse"},
    )
    tools = {t["id"]: t for t in pool_registry.list_tools(conn)}
    assert tools[tool_id]["name"] == "사내 검색 MCP"
    assert tools[tool_id]["is_active"] is True

    pool_registry.set_tool_active(conn, admin_id, tool_id, False)
    tools = {t["id"]: t for t in pool_registry.list_tools(conn)}
    assert tools[tool_id]["is_active"] is False

    with pytest.raises(ValueError):
        pool_registry.set_tool_active(conn, admin_id, 999999, True)

    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "tool_pool_created" in actions
    assert "tool_pool_active_changed" in actions


def test_gc111_create_skill_rejects_unknown_augmentation_mode(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    with pytest.raises(ValueError):
        pool_registry.create_skill(
            conn, admin_id, "웹검색", "", [], "always_and_forever"
        )

    skill_id = pool_registry.create_skill(
        conn, admin_id, "웹검색", "웹 검색 역량", [], "tool"
    )
    skills = {s["id"]: s for s in pool_registry.list_skills(conn)}
    assert skills[skill_id]["name"] == "웹검색"


def test_gc112_link_and_unlink_agent_tool(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('기획자', '[\"요구사항정의\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    tools = {t["name"]: t["id"] for t in pool_registry.list_tools(conn)}
    vectordb_tool_id = tools["VectorDB 지식검색"]

    with pytest.raises(ValueError):
        pool_registry.link_agent_tool(conn, admin_id, 999999, vectordb_tool_id)
    with pytest.raises(ValueError):
        pool_registry.link_agent_tool(conn, admin_id, agent_id, 999999)

    pool_registry.link_agent_tool(conn, admin_id, agent_id, vectordb_tool_id)
    assert pool_registry.list_agent_tool_ids(conn, agent_id) == [vectordb_tool_id]

    # 같은 연결을 다시 걸어도(멱등) 에러가 안 나고 중복도 안 생긴다.
    pool_registry.link_agent_tool(conn, admin_id, agent_id, vectordb_tool_id)
    assert pool_registry.list_agent_tool_ids(conn, agent_id) == [vectordb_tool_id]

    pool_registry.unlink_agent_tool(conn, admin_id, agent_id, vectordb_tool_id)
    assert pool_registry.list_agent_tool_ids(conn, agent_id) == []


def test_gc113_resolve_pool_tools_combines_skill_match_and_explicit_link(
    client_and_conn,
):
    """`resolve_pool_tools()`가 (1) skill_pool 매칭(예: "graphdb" 스킬 →
    GraphDB 도구)과 (2) 명시적 `agent_tool_links`(스킬과 무관하게 추가한
    도구)를 합쳐서 돌려주는지 확인한다. 비활성화된 도구는 매칭돼도
    빠져야 한다."""
    from contextlib import ExitStack

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

    tools_by_name = {t["name"]: t["id"] for t in pool_registry.list_tools(conn)}
    graphdb_tool_id = tools_by_name["GraphDB 관계조회"]
    vectordb_tool_id = tools_by_name["VectorDB 지식검색"]

    # graphdb 스킬 매칭만으로 GraphDB 도구가 나온다(명시적 연결 없이도).
    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(
            conn, stack, ["graphdb"], agent_id=agent_id
        )
    assert [t.name for t in tools] == ["조직도 관계 조회"]

    # VectorDB 도구를 스킬과 무관하게 명시적으로 하나 더 연결하면 같이 나온다.
    pool_registry.link_agent_tool(conn, admin_id, agent_id, vectordb_tool_id)
    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(
            conn, stack, ["graphdb"], agent_id=agent_id
        )
    tool_names = {t.name for t in tools}
    assert tool_names == {"조직도 관계 조회", "지식 베이스 검색"}

    # 도구를 비활성화하면 스킬이 매칭돼도 더 이상 나오지 않는다.
    pool_registry.set_tool_active(conn, admin_id, graphdb_tool_id, False)
    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(
            conn, stack, ["graphdb"], agent_id=agent_id
        )
    assert [t.name for t in tools] == ["지식 베이스 검색"]


def test_gc114_tool_pool_and_skill_pool_endpoints_rbac_and_crud(client_and_conn):
    client, conn = client_and_conn
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('기획자', '[\"요구사항정의\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    resp = client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert resp.status_code == 200
    resp = client.get("/api/tool-pool")
    assert resp.status_code == 403

    resp = client.post(
        "/login", json={"username": "manager1", "password": "managerpass"}
    )
    assert resp.status_code == 200

    resp = client.get("/api/tool-pool")
    assert resp.status_code == 200
    assert len(resp.json()) == 3  # 시드된 VectorDB/GraphDB/recommend_fix 도구(Phase 16)

    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "사내 검색 MCP",
            "kind": "mcp_remote",
            "config": {"url": "http://localhost:9000/mcp"},
        },
    )
    assert resp.status_code == 201
    new_tool_id = resp.json()["id"]

    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "사내 검색 MCP",
            "kind": "mcp_remote",
            "config": {"url": "http://localhost:9999/other"},
        },
    )
    assert resp.status_code == 409  # 이름 중복

    resp = client.post(
        "/api/tool-pool", json={"name": "이상한도구", "kind": "native", "config": {}}
    )
    assert resp.status_code == 400  # native인데 native_id가 없음

    resp = client.patch(
        f"/api/tool-pool/{new_tool_id}/active", json={"is_active": False}
    )
    assert resp.status_code == 200

    resp = client.get("/api/skill-pool")
    assert resp.status_code == 200
    assert (
        len(resp.json()) == 22
    )  # 시드된 22개(vectordb/graphdb 4 + agent-eval 절차 스킬 18, Phase 16)

    resp = client.post(
        "/api/skill-pool",
        json={
            "name": "웹검색",
            "default_tool_pool_ids": [new_tool_id],
            "augmentation_mode": "tool",
        },
    )
    assert resp.status_code == 201

    resp = client.post(f"/api/agent-pool/{agent_id}/tools/{new_tool_id}")
    assert resp.status_code == 201
    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["linked_tool_ids"] == [new_tool_id]

    resp = client.delete(f"/api/agent-pool/{agent_id}/tools/{new_tool_id}")
    assert resp.status_code == 200
    resp = client.get("/api/agent-pool")
    row = next(a for a in resp.json() if a["id"] == agent_id)
    assert row["linked_tool_ids"] == []


def test_gc115_run_crew_resolves_tools_from_data_driven_skill_pool_not_hardcoded_sets(
    client_and_conn,
):
    """실제로 하드코딩이 데이터로 바뀌었는지 확인한다 — `agent_runtime.
    VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS`를 완전히 지워도(patch로 빈
    set으로 바꿔도) `run_crew()`가 여전히 도구를 붙여야 한다(더 이상 그
    상수에 의존하지 않으므로)."""
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    from hair_platform import crew_runtime

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

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return CrewOutput(
            raw="완료",
            tasks_output=[TaskOutput(description="d", agent="agent", raw="완료")],
        )

    with (
        patch("hair_platform.agent_runtime.VECTORDB_SKILL_TAGS", set()),
        patch("hair_platform.agent_runtime.GRAPHDB_SKILL_TAGS", set()),
        patch("crewai.Crew.kickoff", new=_kickoff_capture),
    ):
        crew_runtime.run_crew(
            conn, [agent_id], ["누가 이 업무를 맡고 있어?"], admin_id, provider="openai"
        )

    crew = captured["crew"]
    tool_names = [t.name for t in crew.agents[0].tools]
    assert "조직도 관계 조회" in tool_names
