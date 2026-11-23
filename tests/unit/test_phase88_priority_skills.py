"""우선순위 1 스킬 7개(2026-11-21, "우선순위 1 스킬 7개 진행해줘") — 카피라이팅·
사업성분석·회의록정리·백엔드구현·API설계·보안점검 + "스킬생성"용 '스킬·도구 목록
조회' 도구."""

from __future__ import annotations

import json

import pytest

from hair_platform import agent_runtime, db, pool_catalog, pool_registry, skill_catalog

NEW = ["카피라이팅", "사업성분석", "회의록정리", "백엔드구현", "API설계", "보안점검"]


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def test_new_skills_have_all_four_sections(conn):
    rows = {r["name"]: r for r in pool_registry.list_skills(conn)}
    for name in NEW:
        r = rows[name]
        assert r["category"] == skill_catalog.NEW_SKILLS[name]["category"]
        for field in ("description", "output_format", "examples", "constraints"):
            assert r[field] and len(r[field]) > 40, (name, field)
        assert r["show_in_conversation"]
    assert rows["사업성분석"]["augmentation_mode"] == "tool"  # 수치 데이터 분석기로 실제 계산


def test_reference_agents_get_new_skills_once(conn):
    tags = {
        r["role_name"]: json.loads(r["skill_tags"])
        for r in conn.execute("SELECT role_name, skill_tags FROM agent_pool")
    }
    for role, additions in skill_catalog.AGENT_SKILL_ADDITIONS.items():
        for skill in additions:
            assert skill in tags[role], (role, skill)
    assert tags["콘텐츠 카피라이팅"] == ["카피라이팅"]  # 이제 실제로 등록된 스킬
    # admin이 연결을 뺀 뒤 재기동해도 다시 붙이지 않는다(처음 만들 때만 연결).
    conn.execute(
        "UPDATE agent_pool SET skill_tags = ? WHERE role_name = 'AA'",
        (json.dumps(["아키텍처설계"]),),
    )
    conn.commit()
    db.init_schema(conn)
    aa = conn.execute("SELECT skill_tags FROM agent_pool WHERE role_name = 'AA'").fetchone()[0]
    assert json.loads(aa) == ["아키텍처설계"]


def test_skill_creator_uses_catalog_lookup_tool(conn):
    row = conn.execute(
        "SELECT augmentation_mode, default_tool_pool_ids FROM skill_pool WHERE name = '스킬생성'"
    ).fetchone()
    tool_ids = json.loads(row["default_tool_pool_ids"])
    tool = conn.execute("SELECT name, config FROM tool_pool WHERE id = ?", (tool_ids[0],)).fetchone()
    assert row["augmentation_mode"] == "tool"
    assert tool["name"] == pool_catalog.TOOL_NAME
    assert json.loads(tool["config"])["native_id"] == pool_catalog.NATIVE_ID
    # 직접 호출 경로(대화 @멘션·스킬 선택)는 조회 결과를 정적으로 주입한다.
    ctx = agent_runtime.retrieve_context(conn, ["스킬생성"], "회의 내용을 정리해서 결정사항과 할 일을 뽑는 스킬")
    assert ctx is not None  # 도구가 연결된 스킬이라 주입할 내용이 있어야 한다
    assert "회의록정리" in ctx.split("중복 후보):")[1].splitlines()[0]
    assert "- 수치 데이터 분석기 |" in ctx


def test_catalog_lookup_is_registered_crewai_factory():
    assert pool_registry.NATIVE_TOOL_FACTORIES[pool_catalog.NATIVE_ID] is pool_catalog.as_crewai_tool


def test_similar_ignores_generic_words(conn):
    rows = conn.execute("SELECT name, description FROM skill_pool").fetchall()
    assert pool_catalog._similar("스킬 만들기 업무", rows) == []
    assert pool_catalog._similar("REST API 엔드포인트 명세를 정의", rows)[0] == "API설계"
