"""Phase 14 — Skill Pool·Tool Pool 실제 구현(사용자 요청,
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
    항목(rag/graphdb)이 자동으로 채워져야 한다 — 이전에 코드로 하드코딩됐던
    매핑이 데이터로도 그대로 살아있어야 기존 동작이 안 깨진다.
    `init_schema()`를 두 번 불러도 중복 생성되지 않아야 한다(멱등성).

    **정정(2026-10-06)**: 원래 "vectordb"/"rag", "graphdb"/"관계질의" 두
    쌍 다 별칭으로 있었으나, "필요 스킬" 자동완성 중복 리포트로 각각
    "rag"/"graphdb" 하나로 통합했다."""
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
    vectordb_graphdb_names = {"rag", "graphdb"}
    assert vectordb_graphdb_names <= skill_names
    assert "vectordb" not in skill_names
    assert "관계질의" not in skill_names
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


def test_update_tool_rejects_kind_change_and_revalidates_config(client_and_conn):
    """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을 추가해줘",
    이어서 "에이전트 연결 관련 영향을 주는 도구명/스킬명은 수정 대상에서
    제외") — 지금까지 Tool Pool은 등록·활성화 토글·삭제만 있고, 설명/
    config를 고치려면 지우고 다시 만들어야 했다. `kind`·`name`은 여전히
    바꿀 수 없어야 하고(`name`은 Skill 매칭이 문자열로 참조한다),
    `config`를 바꾸면 `create_tool()`과 같은 검증(native_id 존재 확인)을
    다시 돌려야 한다."""
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    tool_id = pool_registry.create_tool(
        conn, admin_id, "웹검색 도구", "설명 v1", "native", {"native_id": "web_search"}
    )

    pool_registry.update_tool(conn, admin_id, tool_id, description="설명 v2")
    tools = {t["id"]: t for t in pool_registry.list_tools(conn)}
    assert tools[tool_id]["name"] == "웹검색 도구"  # name은 안 바뀜
    assert tools[tool_id]["description"] == "설명 v2"
    assert tools[tool_id]["kind"] == "native"  # kind도 안 바뀜

    # kind·name 자체는 허용 필드가 아니다 — 보내면 바로 거부된다.
    with pytest.raises(ValueError, match="unsupported_fields"):
        pool_registry.update_tool(conn, admin_id, tool_id, kind="custom_python")
    with pytest.raises(ValueError, match="unsupported_fields"):
        pool_registry.update_tool(conn, admin_id, tool_id, name="다른 이름")

    # config를 바꿀 때도 create_tool()과 같은 native_id 검증이 다시 돈다.
    with pytest.raises(ValueError, match="unknown_native_id"):
        pool_registry.update_tool(
            conn, admin_id, tool_id, config={"native_id": "존재안함"}
        )

    with pytest.raises(ValueError, match="tool_not_found"):
        pool_registry.update_tool(conn, admin_id, 999999, description="x")

    actions = {
        r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "tool_pool_updated" in actions


def test_update_skill_revalidates_augmentation_mode(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    skill_id = pool_registry.create_skill(conn, admin_id, "임시스킬", "설명 v1")

    pool_registry.update_skill(
        conn,
        admin_id,
        skill_id,
        description="설명 v2",
        default_tool_pool_ids=[1, 2],
        augmentation_mode="always",
    )
    skills = {s["id"]: s for s in pool_registry.list_skills(conn)}
    assert skills[skill_id]["description"] == "설명 v2"
    assert skills[skill_id]["default_tool_pool_ids"] == [1, 2]
    assert skills[skill_id]["augmentation_mode"] == "always"

    with pytest.raises(ValueError, match="unsupported_augmentation_mode"):
        pool_registry.update_skill(
            conn, admin_id, skill_id, augmentation_mode="not_a_mode"
        )
    with pytest.raises(ValueError, match="unsupported_fields"):
        pool_registry.update_skill(conn, admin_id, skill_id, name="다른 이름")
    with pytest.raises(ValueError, match="skill_not_found"):
        pool_registry.update_skill(conn, admin_id, 999999, description="x")


def test_update_tool_and_skill_pool_endpoints_rbac(client_and_conn):
    """PATCH 엔드포인트도 등록(POST)과 같은 RBAC을 따라야 한다 — member는
    둘 다 403, manager는 native 도구/스킬은 가능하지만 mcp_remote 도구
    수정은 여전히 admin 전용이어야 한다(수정 경로로 그 제한을 우회할
    수 없어야 한다)."""
    client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    native_tool_id = pool_registry.create_tool(
        conn, admin_id, "네이티브 도구", "", "native", {"native_id": "web_search"}
    )
    mcp_tool_id = pool_registry.create_tool(
        conn, admin_id, "MCP 도구", "", "mcp_remote", {"url": "http://localhost:9000"}
    )
    skill_id = pool_registry.create_skill(conn, admin_id, "임시스킬2", "")

    resp = client.post(
        "/login", json={"username": "member1", "password": "memberpass"}
    )
    assert resp.status_code == 200
    resp = client.patch(f"/api/tool-pool/{native_tool_id}", json={"description": "x"})
    assert resp.status_code == 403
    resp = client.patch(f"/api/skill-pool/{skill_id}", json={"description": "x"})
    assert resp.status_code == 403

    resp = client.post(
        "/login", json={"username": "manager1", "password": "managerpass"}
    )
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/tool-pool/{native_tool_id}", json={"description": "매니저 수정"}
    )
    assert resp.status_code == 200
    resp = client.patch(f"/api/skill-pool/{skill_id}", json={"description": "매니저 수정"})
    assert resp.status_code == 200
    # mcp_remote는 매니저가 수정 못 한다(등록과 같은 제한).
    resp = client.patch(
        f"/api/tool-pool/{mcp_tool_id}", json={"description": "매니저 시도"}
    )
    assert resp.status_code == 403

    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/tool-pool/{mcp_tool_id}", json={"description": "admin 수정"}
    )
    assert resp.status_code == 200
    resp = client.get("/api/tool-pool")
    row = next(t for t in resp.json() if t["id"] == mcp_tool_id)
    assert row["description"] == "admin 수정"

    resp = client.patch("/api/tool-pool/999999", json={"description": "x"})
    assert resp.status_code == 404
    resp = client.patch("/api/skill-pool/999999", json={"description": "x"})
    assert resp.status_code == 404


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
    assert [t.name for t in tools] == ["org_relationship_query"]

    # VectorDB 도구를 스킬과 무관하게 명시적으로 하나 더 연결하면 같이 나온다.
    pool_registry.link_agent_tool(conn, admin_id, agent_id, vectordb_tool_id)
    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(
            conn, stack, ["graphdb"], agent_id=agent_id
        )
    tool_names = {t.name for t in tools}
    assert tool_names == {"org_relationship_query", "knowledge_base_search"}

    # 도구를 비활성화하면 스킬이 매칭돼도 더 이상 나오지 않는다.
    pool_registry.set_tool_active(conn, admin_id, graphdb_tool_id, False)
    with ExitStack() as stack:
        tools = pool_registry.resolve_pool_tools(
            conn, stack, ["graphdb"], agent_id=agent_id
        )
    assert [t.name for t in tools] == ["knowledge_base_search"]


def test_gc196_init_schema_seeds_websearch_pool(client_and_conn):
    """사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
    API(가능한 무료)를... 추가해줘") — 같은 방식으로, 외부 자료
    검색(Wikipedia) native Tool과 "외부검색" skill_pool 항목이 스키마
    초기화 시 자동으로 채워지고, "외부검색" 스킬을 가진 Agent에 실제로
    그 Tool이 연결되는지 확인한다.

    **정정(2026-10-06)**: 원래 "websearch"/"외부검색" 두 별칭이 모두
    있었으나, "필요 스킬" 자동완성에 완전히 같은 뜻이 두 번 떠 중복으로
    보인다는 리포트로 "외부검색" 하나로 통합했다."""
    from contextlib import ExitStack

    _client, conn = client_and_conn

    tools = pool_registry.list_tools(conn)
    tool_names = {t["name"] for t in tools}
    assert "외부 자료 검색(Wikipedia)" in tool_names
    web_tool = next(t for t in tools if t["name"] == "외부 자료 검색(Wikipedia)")
    assert web_tool["kind"] == "native"
    assert web_tool["is_active"] is True

    skills = pool_registry.list_skills(conn)
    skill_names = {s["name"] for s in skills}
    assert "외부검색" in skill_names
    assert "websearch" not in skill_names
    for s in skills:
        if s["name"] == "외부검색":
            assert s["augmentation_mode"] == "tool"

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('외부지식 Agent', '[\"외부검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()
    with ExitStack() as stack:
        resolved = pool_registry.resolve_pool_tools(
            conn, stack, ["외부검색"], agent_id=agent_id
        )
    assert [t.name for t in resolved] == ["external_knowledge_search"]


class _FakeGeneralWebSearchMCPAdapter:
    """`crewai_tools.MCPServerAdapter`의 안전한 mock 경계(같은
    정신) — 실제 클래스는 생성 시점에 진짜 네트워크 접속을 시도하므로
    (`connect_timeout` 기본 30초), 이 클래스 자체를 patch하는 것이
    유일하게 안전한 검증 방법이다. 실제 접속 자체는 라이브로 이미
    확인했다 — `web_search`/`web_fetch` 두 Tool이 실제로 열리고, 실제
    뉴스 검색까지 성공."""

    def __init__(self, serverparams, *tool_names, connect_timeout=30):
        self.serverparams = serverparams

    def __enter__(self):
        from crewai.tools import BaseTool

        class _FakeTool(BaseTool):
            def _run(self, **kwargs):
                return "ok"

        return [_FakeTool(name="web_search", description="가짜 일반 웹 검색 도구")]

    def __exit__(self, *exc):
        return False


def test_gc197_init_schema_seeds_general_websearch_mcp_pool(client_and_conn):
    """사용자 요청(2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지
    확인해줘" → "진행해줘") — Parallel Search(계정·키 불필요 MCP 서버)가
    `mcp_remote` 종류로 시드되고, "일반웹검색" 스킬을 가진 Agent에 실제로
    연결되는지 확인한다. 네트워크 접속 자체는 `crewai_tools.
    MCPServerAdapter`를 mock해 안전하게 검증한다."""
    from contextlib import ExitStack

    _client, conn = client_and_conn

    tools = pool_registry.list_tools(conn)
    web_tool = next(
        (t for t in tools if t["name"] == "일반 웹 검색(Parallel Search)"), None
    )
    assert web_tool is not None
    assert web_tool["kind"] == "mcp_remote"
    assert web_tool["is_active"] is True

    skills = pool_registry.list_skills(conn)
    skill_names = {s["name"] for s in skills}
    assert "일반웹검색" in skill_names

    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('웹검색 Agent', '[\"일반웹검색\"]', 'active')"
    )
    agent_id = cur.lastrowid
    conn.commit()

    with patch("crewai_tools.MCPServerAdapter", _FakeGeneralWebSearchMCPAdapter):
        with ExitStack() as stack:
            resolved = pool_registry.resolve_pool_tools(
                conn, stack, ["일반웹검색"], agent_id=agent_id
            )
    assert [t.name for t in resolved] == ["web_search"]


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
    # 시드된 VectorDB/GraphDB/recommend_fix/외부검색/일반웹검색(5) +
    # 2026-10-14 "기본 배포에 Tool을 포함해줘"로 추가된 레퍼런스 파이프라인
    # custom_python 도구 5개(Spec EARS 린터·실패모드 기계적 도출·골든셋
    # 커버리지 체커·분석 프레임워크 템플릿 생성기·발표자료 아웃라인 생성기) +
    # 2026-10-14(PM/AA/DA 등 기획→개발 팀 구성)로 추가된 "테스트 케이스
    # 기계적 도출" 1개(= 10 + 1 = 11).
    assert len(resp.json()) == 11

    # 사용자 결정(2026-09-29, "(a) MCP 종류만 admin 전용으로 좁히기") —
    # 매니저는 mcp_remote/mcp_stdio 등록이 이제 403이다. native는
    # 여전히 매니저도 가능.
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "사내 검색 MCP",
            "kind": "mcp_remote",
            "config": {"url": "http://localhost:9000/mcp"},
        },
    )
    assert resp.status_code == 403

    resp = client.post(
        "/api/tool-pool", json={"name": "이상한도구", "kind": "native", "config": {}}
    )
    assert resp.status_code == 400  # native인데 native_id가 없음(매니저도 native는 가능)

    resp = client.post(
        "/login", json={"username": "admin1", "password": "adminpass"}
    )
    assert resp.status_code == 200

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

    resp = client.patch(
        f"/api/tool-pool/{new_tool_id}/active", json={"is_active": False}
    )
    assert resp.status_code == 200

    resp = client.get("/api/skill-pool")
    assert resp.status_code == 200
    assert (
        len(resp.json()) == 47
    )  # 시드된 22개(rag/graphdb 2 + agent-eval 절차 스킬 18 + 외부검색 1 +
    # 일반웹검색 1) — vectordb/관계질의는 2026-10-06 자동완성 중복 통합으로
    # 제거됨 — "websearch"는 "외부검색"과 완전히 같은 뜻이라 2026-10-06에
    # 하나로 통합했다.
    # + 2026-10-14 "기본 배포에 Skill을 포함해줘"로 추가된 레퍼런스
    # 파이프라인 Skill 15개(= 22 + 15 = 37).
    # + 2026-10-14(PM/AA/DA 등 기획→개발 팀 구성)로 추가된 Skill 9개
    # (지식 스킬 8개: pm-delivery-coordination·
    # application-architecture-design-guide·data-model-design-guide·
    # module-decomposition-and-task-assignment·
    # frontend-ui-implementation-guide·ui-ux-planning-guide·
    # test-plan-and-case-design-guide·quality-acceptance-criteria-guide
    # + tool-wrapping 스킬 1개: test-case-mechanical-derivation
    # = 37 + 9 = 46).
    # + 2026-10-14("자율 개발 크루" 10인 개발자 풀)로 추가된 Skill 1개
    # (data-infra-implementation-guide = 46 + 1 = 47).

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


# ---------- show_in_conversation(사용자 요청, 2026-10-22, "skill에
# 대화창 표시 속성 추가, 가능한 것들만 표시되도록 개선") ----------


def test_gc_show_in_conversation_defaults_true_but_backfill_hides_non_native_tool_skills():
    """새 컬럼은 생성 시 기본 True지만, `init_schema()`가 기본 시드
    스킬을 깔고 나면 mcp_remote/mcp_stdio/custom_python 도구가 붙은
    스킬(대화창 기본 AI 경로에서 구조적으로 실행 불가)은 자동으로
    False가 돼야 한다 — native 3개(rag/graphdb/외부검색)는 그대로
    True."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skills = {s["name"]: s for s in pool_registry.list_skills(conn)}

    for name in ("rag", "graphdb", "외부검색"):
        assert skills[name]["show_in_conversation"] is True

    hidden = {
        "일반웹검색",
        "recommend-fix-workflow",
        "spec-ears-lint",
        "failure-mode-derivation",
        "golden-set-coverage-check",
        "framework-template-generator",
        "presentation-outline-builder",
        "test-case-mechanical-derivation",
    }
    for name in hidden:
        assert skills[name]["show_in_conversation"] is False, name

    # 순수 텍스트 지침 스킬(도구 없음)은 건드리지 않는다 — 계속 True.
    assert skills["tdd-ai-workflow-checklist"]["show_in_conversation"] is True


def test_gc_skill_pool_names_conversation_filter(client_and_conn):
    client, conn = client_and_conn
    _ = conn
    client.post("/login", json={"username": "member1", "password": "memberpass"})

    resp = client.get("/api/skill-pool/names")
    assert resp.status_code == 200
    all_names = resp.json()
    assert "일반웹검색" in all_names  # skill_tags 자동완성은 전체를 그대로 받는다.

    resp = client.get("/api/skill-pool/names?conversation=true")
    assert resp.status_code == 200
    conv_names = resp.json()
    assert "일반웹검색" not in conv_names  # 대화창 드롭다운은 숨김 스킬을 뺀다.
    assert "rag" in conv_names
    assert "tdd-ai-workflow-checklist" in conv_names
    assert len(conv_names) < len(all_names)


def test_gc_create_and_update_skill_show_in_conversation(client_and_conn):
    client, conn = client_and_conn
    _ = conn
    client.post("/login", json={"username": "admin1", "password": "adminpass"})

    resp = client.post(
        "/api/skill-pool",
        json={"name": "임시스킬", "show_in_conversation": False},
    )
    assert resp.status_code == 201
    skill_id = resp.json()["id"]
    skill = next(
        s for s in client.get("/api/skill-pool").json() if s["id"] == skill_id
    )
    assert skill["show_in_conversation"] is False

    resp = client.patch(
        f"/api/skill-pool/{skill_id}", json={"show_in_conversation": True}
    )
    assert resp.status_code == 200
    skill = next(
        s for s in client.get("/api/skill-pool").json() if s["id"] == skill_id
    )
    assert skill["show_in_conversation"] is True


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
    assert "org_relationship_query" in tool_names
