"""Phase 71 — Skill/Tool Pool 분류(SW개발업무/일반업무) + 대화창 필터링
(사용자 요청, 2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로 구분하고
필터링 되도록 개선. 대화창에서는 일반업무가 표시되고, 개발업무는
default로 표시되지 않도록 개선").

레퍼런스 스킬/도구 전수(51개+12개)를 직접 읽어 분류했다 — 기준은 Agent가
아니라 스킬/도구 자체의 실제 내용(소프트웨어 코드/아키텍처/테스트/Gate
채점에 한정되는지, 조사·전략·보고서·데이터분석 등 도메인 무관
지식업무인지)이다. `category` 컬럼은 `show_in_conversation`과 같은
"컬럼이 막 추가된 시점에만 1회 백필" 패턴으로 마이그레이션한다.
"""

from __future__ import annotations

import pytest

from hair_platform import db, pool_registry


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


# ---------- DB 마이그레이션/백필 ----------


def test_category_column_backfilled_correctly_on_fresh_db():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 멱등성 확인

    skills = pool_registry.list_skills(conn)
    skill_categories = {s["category"] for s in skills}
    assert skill_categories == {"sw_dev", "general"}
    sw_dev_count = sum(1 for s in skills if s["category"] == "sw_dev")
    general_count = sum(1 for s in skills if s["category"] == "general")
    assert sw_dev_count + general_count == len(skills) == 51
    # 2026-11-11 "SKL-NONE-023(승인루프)은 개발업무 Skill로 변경" —
    # 34 → 35, 17 → 16.
    assert sw_dev_count == 35
    assert general_count == 16

    tools = pool_registry.list_tools(conn)
    sw_dev_tool_count = sum(1 for t in tools if t["category"] == "sw_dev")
    general_tool_count = sum(1 for t in tools if t["category"] == "general")
    assert sw_dev_tool_count + general_tool_count == len(tools) == 12
    assert sw_dev_tool_count == 4
    assert general_tool_count == 8


@pytest.mark.parametrize(
    "name",
    [
        "harness-gate-ci",
        "설계결정기록",
        "요구사항검증",
        "진행관리조율",
        "코드리뷰체크리스트",
        "아키텍처설계가이드",
        "승인루프",
    ],
)
def test_known_dev_skills_classified_as_sw_dev(name):
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute("SELECT category FROM skill_pool WHERE name = ?", (name,)).fetchone()
    assert row is not None
    assert row["category"] == "sw_dev"


@pytest.mark.parametrize(
    "name",
    [
        "지식검색",
        "관계조회",
        "실시간웹검색",
        "커버리지체크",
        "문서작성가이드",
        "전략분석프레임워크",
        "데이터분석가이드",
        "제안서작성가이드",
        "실적서작성가이드",
    ],
)
def test_known_general_skills_classified_as_general(name):
    """커버리지체크/문서작성가이드는 개발 파이프라인에서 유래했지만
    내용 자체가 도메인 무관이고, 실제로 기획서/C-level 보고서 작성자가
    이미 재사용 중이라 'general'이어야 한다(Agent가 아니라 스킬 내용
    기준 분류라는 설계 확인). 승인루프는 사용자 재검토(2026-11-11,
    "SKL-NONE-023은 개발업무 Skill로 변경") 후 sw_dev로 옮겨 이
    목록에서 제외됐다 — `test_known_dev_skills_classified_as_sw_dev`
    참고."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute("SELECT category FROM skill_pool WHERE name = ?", (name,)).fetchone()
    assert row is not None
    assert row["category"] == "general"


def test_category_backfill_does_not_override_admin_edit_on_restart():
    """`show_in_conversation` 백필과 같은 "컬럼이 막 추가된 시점에만
    1회" 원칙 확인 — 이미 category 컬럼이 있는 DB에서 `init_schema()`를
    다시 불러도 관리자가 직접 바꾼 값을 덮어쓰면 안 된다."""
    from hair_platform.auth import Role, create_user

    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    skill_id = _skill_id(conn, "설계결정기록")
    pool_registry.update_skill(conn, admin_id, skill_id, category="general")
    db.init_schema(conn)  # 재기동
    row = conn.execute("SELECT category FROM skill_pool WHERE name = '설계결정기록'").fetchone()
    assert row["category"] == "general"  # 백필이 다시 'sw_dev'로 되돌리지 않음


def _skill_id(conn, name):
    return conn.execute("SELECT id FROM skill_pool WHERE name = ?", (name,)).fetchone()["id"]


# ---------- pool_registry CRUD ----------


def test_create_skill_rejects_invalid_category():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.create_skill(conn, 1, "임시스킬", "d", category="invalid")


def test_create_tool_rejects_invalid_category():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.create_tool(
            conn, 1, "임시도구", "d", "native", {"native_id": "tavily_search"}, category="invalid"
        )


def test_create_skill_and_tool_default_category_is_general(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    skill_id = pool_registry.create_skill(conn, admin_id, "임시스킬-기본값", "d")
    tool_id = pool_registry.create_tool(
        conn, admin_id, "임시도구-기본값", "d", "native", {"native_id": "tavily_search"}
    )
    skills = {s["id"]: s for s in pool_registry.list_skills(conn)}
    tools = {t["id"]: t for t in pool_registry.list_tools(conn)}
    assert skills[skill_id]["category"] == "general"
    assert tools[tool_id]["category"] == "general"


def test_update_skill_and_tool_category(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    skill_id = pool_registry.create_skill(
        conn, admin_id, "임시스킬-수정", "d", category="sw_dev"
    )
    pool_registry.update_skill(conn, admin_id, skill_id, category="general")
    skills = {s["id"]: s for s in pool_registry.list_skills(conn)}
    assert skills[skill_id]["category"] == "general"

    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.update_skill(conn, admin_id, skill_id, category="invalid")

    tool_id = pool_registry.create_tool(
        conn,
        admin_id,
        "임시도구-수정",
        "d",
        "native",
        {"native_id": "tavily_search"},
        category="sw_dev",
    )
    pool_registry.update_tool(conn, admin_id, tool_id, category="general")
    tools = {t["id"]: t for t in pool_registry.list_tools(conn)}
    assert tools[tool_id]["category"] == "general"

    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.update_tool(conn, admin_id, tool_id, category="invalid")


def test_list_skills_and_tools_filter_by_category():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    general_skills = pool_registry.list_skills(conn, category="general")
    sw_dev_skills = pool_registry.list_skills(conn, category="sw_dev")
    assert len(general_skills) + len(sw_dev_skills) == len(pool_registry.list_skills(conn))
    assert all(s["category"] == "general" for s in general_skills)
    assert all(s["category"] == "sw_dev" for s in sw_dev_skills)

    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.list_skills(conn, category="invalid")
    with pytest.raises(ValueError, match="unsupported_category"):
        pool_registry.list_tools(conn, category="invalid")


# ---------- API 계약 ----------


def test_skill_pool_names_conversation_excludes_sw_dev_by_default(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/skill-pool/names?conversation=true")
    assert resp.status_code == 200
    names = resp.json()
    assert "지식검색" in names  # general
    assert "harness-gate-ci" not in names  # sw_dev — default-off


def test_skill_pool_names_without_conversation_param_ignores_category(client_and_conn):
    """Agent/Task의 skill_tags 자동완성은 category와 무관하게 항상
    전체 목록이어야 한다(CrewAI 실행 경로는 이 스킬들도 실제로
    동작하므로)."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/skill-pool/names")
    assert resp.status_code == 200
    names = resp.json()
    assert "지식검색" in names
    assert "harness-gate-ci" in names  # conversation=false면 그대로 보임


def test_skill_pool_names_include_dev_only_matters_with_conversation(client_and_conn):
    """사용자 요청(2026-11-11, "개발업무 Skill의 경우 Default로 대화창에
    보이지 않도록 설정 하고, 필요시 노출하도록") — 한 번 "예외 없이
    항상 제외"로 단순화했다가, 이 요청으로 "기본은 숨김, 필요하면
    노출"로 되돌렸다. `include_dev=true`면 sw_dev 스킬도 다시 보여야
    한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/skill-pool/names?conversation=true&include_dev=true")
    assert resp.status_code == 200
    names = resp.json()
    assert "지식검색" in names
    assert "harness-gate-ci" in names  # include_dev=true로 다시 보임


def test_skill_pool_list_endpoint_filters_by_category_query_param(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/skill-pool?category=general")
    assert resp.status_code == 200
    skills = resp.json()
    assert all(s["category"] == "general" for s in skills)

    resp_bad = client.get("/api/skill-pool?category=invalid")
    assert resp_bad.status_code == 400


def test_tool_pool_list_endpoint_filters_by_category_query_param(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/tool-pool?category=sw_dev")
    assert resp.status_code == 200
    tools = resp.json()
    assert all(t["category"] == "sw_dev" for t in tools)

    resp_bad = client.get("/api/tool-pool?category=invalid")
    assert resp_bad.status_code == 400


def test_create_skill_pool_entry_accepts_category(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/skill-pool",
        json={
            "name": "api-생성-테스트-스킬",
            "description": "d",
            "augmentation_mode": "none",
            "category": "sw_dev",
        },
    )
    assert resp.status_code == 201, resp.text
    row = conn.execute(
        "SELECT category FROM skill_pool WHERE name = 'api-생성-테스트-스킬'"
    ).fetchone()
    assert row["category"] == "sw_dev"


def test_create_tool_pool_entry_accepts_category(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "api-생성-테스트-도구",
            "description": "d",
            "kind": "native",
            "config": {"native_id": "tavily_search"},
            "category": "sw_dev",
        },
    )
    assert resp.status_code == 201, resp.text
    row = conn.execute(
        "SELECT category FROM tool_pool WHERE name = 'api-생성-테스트-도구'"
    ).fetchone()
    assert row["category"] == "sw_dev"


def test_update_skill_pool_entry_patches_category(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/skill-pool",
        json={"name": "patch-테스트-스킬", "description": "d", "augmentation_mode": "none"},
    )
    skill_id = resp.json()["id"]
    resp2 = client.patch(f"/api/skill-pool/{skill_id}", json={"category": "sw_dev"})
    assert resp2.status_code == 200, resp2.text
    row = conn.execute("SELECT category FROM skill_pool WHERE id = ?", (skill_id,)).fetchone()
    assert row["category"] == "sw_dev"


def test_update_tool_pool_entry_patches_category(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/tool-pool",
        json={
            "name": "patch-테스트-도구",
            "description": "d",
            "kind": "native",
            "config": {"native_id": "tavily_search"},
        },
    )
    tool_id = resp.json()["id"]
    resp2 = client.patch(f"/api/tool-pool/{tool_id}", json={"category": "sw_dev"})
    assert resp2.status_code == 200, resp2.text
    row = conn.execute("SELECT category FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
    assert row["category"] == "sw_dev"
