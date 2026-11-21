"""Phase 85 — 스킬 카탈로그(사용자 요청, 2026-11-20, "프레젠테이션 스킬을
참조해, 다른 스킬들의 경우도 '지침, 출력형식, 예시, 제약사항'을 다듬어 스킬
품질을 개선해줘").

`skill_catalog.SKILL_GUIDES`가 모든 시드 스킬의 네 필드를 정의하고,
`db._apply_skill_catalog`는 비어 있거나 내장 기본값 그대로인 필드만 바꾼다 —
사람이 고친 필드는 보존한다.
"""

from __future__ import annotations

import pytest

from hair_platform import db, deliverables, pool_registry, skill_catalog, skill_markdown


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    return conn


def test_catalog_covers_every_seeded_skill_with_all_fields():
    conn = _fresh()
    names = {r["name"] for r in conn.execute("SELECT name FROM skill_pool")}
    assert set(skill_catalog.SKILL_GUIDES) == names
    for name, guide in skill_catalog.SKILL_GUIDES.items():
        for field in skill_catalog.FIELDS:
            assert getattr(guide, field), f"{name}.{field} 비어 있음"
        # 로컬 모델 프롬프트 예산 — 프레젠테이션작성(디자인 시스템 부품 목록)만 예외.
        if name != "프레젠테이션작성":
            assert sum(len(getattr(guide, f)) for f in skill_catalog.FIELDS) < 1600, name


def test_fresh_db_matches_catalog():
    conn = _fresh()
    for row in conn.execute("SELECT * FROM skill_pool"):
        guide = skill_catalog.SKILL_GUIDES[row["name"]]
        for field in skill_catalog.FIELDS:
            assert row[field] == getattr(guide, field), f"{row['name']}.{field}"


def test_legacy_default_is_upgraded_but_human_edit_is_kept():
    conn = _fresh()
    sid = conn.execute("SELECT id FROM skill_pool WHERE name = '기획서작성'").fetchone()["id"]
    # 개편 직전 내장 기본값(해시가 등록된 값)으로 되돌린 필드는 다시 카탈로그 값이 된다.
    legacy_output = (
        "배경 → 목표 → 실행계획(어떻게·누가·언제) → 예산 → 리스크와 대응방안 순서의 문서. "
        "결론을 맨 앞에 둔다."
    )
    assert skill_catalog.is_replaceable("기획서작성", "output_format", legacy_output)
    conn.execute(
        "UPDATE skill_pool SET output_format = ?, constraints = ? WHERE id = ?",
        (legacy_output, "우리 팀만의 제약", sid),
    )
    conn.commit()

    db.init_schema(conn)

    row = conn.execute("SELECT * FROM skill_pool WHERE id = ?", (sid,)).fetchone()
    guide = skill_catalog.SKILL_GUIDES["기획서작성"]
    assert row["output_format"] == guide.output_format
    assert row["constraints"] == "우리 팀만의 제약"  # 사람이 고친 필드는 보존


def test_research_and_lookup_skills_keep_source_rules():
    conn = _fresh()
    skills = {s["name"]: s for s in pool_registry.list_skills(conn)}
    for name in ("내부지식검색", "조직배정관계조회", "실시간웹검색", "딥리서치", "AI업계동향브리핑"):
        assert "출처" in skills[name]["constraints"] + skills[name]["output_format"], name


def test_proposal_skill_has_no_inline_style_instructions():
    guide = skill_catalog.SKILL_GUIDES["제안서작성"]
    # 슬라이드 형식은 포함 스킬(프레젠테이션작성)이 정한다 — 제안서작성 자신은
    # <style> 인라인 지시를 갖지 않고 덱 골격을 따른다고만 한다.
    assert "프레젠테이션작성" in guide.description + guide.output_format
    assert "<style>" not in guide.description + guide.output_format


# ---------- 스킬 포함 관계(2026-11-20, "제안서 작성의 경우, 프레젠테이션 스킬을
# 활용 또는 참조하는 방식으로 개선해줘") ----------

def test_proposal_includes_presentation_and_prompt_carries_both():
    conn = _fresh()
    skill = next(s for s in pool_registry.list_skills(conn) if s["name"] == "제안서작성")
    assert skill["includes"] == ["프레젠테이션작성"]
    system = deliverables.skill_grounded_system_prompt(conn, "대화 참여자", ["제안서작성"])
    assert "요구사항 대응표" in system  # 제안서 고유 규칙
    assert 'class="hair-deck"' in system  # 포함된 프레젠테이션작성의 덱 골격
    assert "- [프레젠테이션작성] (함께 적용 — 제안서작성 스킬이 포함)" in system


def test_included_skill_is_not_duplicated_when_also_selected():
    conn = _fresh()
    text = pool_registry.skill_knowledge_text(conn, ["제안서작성", "프레젠테이션작성"])
    assert text.count("HAIR 덱 디자인 시스템으로 작성하는 역량") == 1
    assert "(함께 적용" not in text  # 직접 고른 스킬은 '함께 적용' 표시가 붙지 않는다


def test_include_cycles_terminate(client_and_conn):
    _client, conn = client_and_conn
    pool_registry.create_skill(conn, 1, "순환A", "A 지침", augmentation_mode="none")
    pool_registry.create_skill(conn, 1, "순환B", "B 지침", augmentation_mode="none", includes=["순환A"])
    a_id = conn.execute("SELECT id FROM skill_pool WHERE name = '순환A'").fetchone()["id"]
    pool_registry.update_skill(conn, 1, a_id, includes=["순환B"])
    text = pool_registry.skill_knowledge_text(conn, ["순환A"])
    assert text.count("A 지침") == 1 and text.count("B 지침") == 1


def test_include_validation(client_and_conn):
    _client, conn = client_and_conn
    with pytest.raises(ValueError, match="unknown_included_skill"):
        pool_registry.create_skill(conn, 1, "검증X", "x", augmentation_mode="none", includes=["없는스킬"])
    pool_registry.create_skill(conn, 1, "검증Y", "y", augmentation_mode="none")
    y_id = conn.execute("SELECT id FROM skill_pool WHERE name = '검증Y'").fetchone()["id"]
    with pytest.raises(ValueError, match="include_self"):
        pool_registry.update_skill(conn, 1, y_id, includes=["검증Y"])


def test_includes_roundtrip_through_markdown(client_and_conn):
    _client, conn = client_and_conn
    skill = next(s for s in pool_registry.list_skills(conn) if s["name"] == "제안서작성")
    md = skill_markdown.export_skill_markdown(conn, skill)
    assert "includes: 프레젠테이션작성" in md
    parsed = skill_markdown.parse_skill_markdown(conn, md.replace("name: 제안서작성", "name: 제안서복제"))
    assert parsed["includes"] == ["프레젠테이션작성"]
    pool_registry.create_skill(conn, 1, **parsed)
    clone = next(s for s in pool_registry.list_skills(conn) if s["name"] == "제안서복제")
    assert clone["includes"] == ["프레젠테이션작성"]


def test_admin_cleared_includes_survive_restart(client_and_conn):
    _client, conn = client_and_conn
    sid = conn.execute("SELECT id FROM skill_pool WHERE name = '제안서작성'").fetchone()["id"]
    pool_registry.update_skill(conn, 1, sid, includes=[])
    db.init_schema(conn)
    assert next(s for s in pool_registry.list_skills(conn) if s["id"] == sid)["includes"] == []


def test_hand_edited_proposal_description_is_replaced_as_requested():
    old = (
        "제안서(RFP 대응·수주 제안)는 ...(사용자가 예전에 덧붙인 <style> 인라인 슬라이드 지시)"
    )
    # 등록된 해시가 아닌 다른 사람 수정본은 여전히 보존된다.
    assert not skill_catalog.is_replaceable("제안서작성", "description", old)
