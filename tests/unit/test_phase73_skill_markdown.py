"""Phase 73 — Skill Pool 구조화 필드 + 마크다운 import/export
(사용자 요청, 2026-11-17, "skill을 정해진 섹션 규격을 참조해 구조화하고,
외부에서 마크다운 포멧의 Skill을 import하거나, 마크다운 규격으로
export, skill의 동작을 파인튜닝하기 위해 수정하는 기능을 갖추도록...
개선").

`skill_pool`에 `output_format`/`examples`/`constraints` 3개 선택
컬럼을 추가(`description`은 그대로 "지침" 본문)하고, 이를 frontmatter
(name/category/augmentation_mode/tool) + 섹션(## 지침/## 출력 형식/
## 예시/## 제약사항) 규격 마크다운으로 내보내고/들여오는
`skill_markdown.py`를 더한다.
"""

from __future__ import annotations

from hair_platform import db, pool_registry, skill_markdown


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


# ---------- 신규 컬럼 — create/update/list ----------


def test_create_skill_with_new_fields_roundtrips_through_list_skills(client_and_conn):
    _client, conn = client_and_conn
    pool_registry.create_skill(
        conn,
        1,
        "신규필드테스트",
        "지침 본문",
        augmentation_mode="none",
        output_format="마크다운 문서",
        examples="예시 텍스트",
        constraints="이것만은 하지 마세요",
    )
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "신규필드테스트"
    )
    assert skill["output_format"] == "마크다운 문서"
    assert skill["examples"] == "예시 텍스트"
    assert skill["constraints"] == "이것만은 하지 마세요"


def test_create_skill_without_new_fields_leaves_them_none(client_and_conn):
    # 2026-11-20 스킬 카탈로그(`skill_catalog.py`) 이후 시드 스킬은 전부 네
    # 필드가 채워지므로, 새 필드 없이 직접 만든 스킬로 확인한다.
    _client, conn = client_and_conn
    pool_registry.create_skill(conn, 1, "필드없음테스트", "지침 본문", augmentation_mode="none")
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "필드없음테스트"
    )
    assert skill["output_format"] is None
    assert skill["examples"] is None
    assert skill["constraints"] is None


def test_update_skill_can_set_new_fields(client_and_conn):
    _client, conn = client_and_conn
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "내부지식검색"
    )
    pool_registry.update_skill(conn, 1, skill["id"], output_format="표 형식")
    updated = next(
        s for s in pool_registry.list_skills(conn) if s["id"] == skill["id"]
    )
    assert updated["output_format"] == "표 형식"
    assert updated["description"] == skill["description"]  # 다른 필드 무변화


def test_skill_knowledge_text_unchanged_when_new_fields_absent(client_and_conn):
    """새 필드가 전부 NULL인 스킬(관리자가 지침만 넣어 만든 스킬)은 글자
    하나 다르지 않게 지침 한 줄만 나와야 한다(회귀 가드)."""
    _client, conn = client_and_conn
    pool_registry.create_skill(conn, 1, "지침만스킬", "지침 한 줄", augmentation_mode="none")
    assert pool_registry.skill_knowledge_text(conn, ["지침만스킬"]) == "- [지침만스킬] 지침 한 줄"  # 2026-11-21: 스킬 이름 표시


def test_skill_knowledge_text_appends_labeled_lines_when_new_fields_present(
    client_and_conn,
):
    _client, conn = client_and_conn
    pool_registry.create_skill(
        conn,
        1,
        "라벨테스트",
        "핵심 지침",
        augmentation_mode="none",
        output_format="출력형식값",
        examples="예시값",
        constraints="제약값",
    )
    text = pool_registry.skill_knowledge_text(conn, ["라벨테스트"])
    assert text == (
        "- [라벨테스트] 핵심 지침\n"
        "  출력 형식: 출력형식값\n"
        "  예시: 예시값\n"
        "  제약사항: 제약값"
    )


# ---------- _backfill_skill_structured_fields() — 기존 53개 스킬 개선
# (사용자 요청, 2026-11-17, "기존 skill들을 개선된 기능에 맞춰 최적의
# 품질을 낼 수 있도록 개선해줘") ----------


def test_every_seeded_skill_has_all_four_sections_on_fresh_db():
    """2026-11-17 백필은 근거가 있는 41개만 채우고 외부 정의를 가리키는
    포인터뿐인 14개는 NULL로 남겼다. 2026-11-20 스킬 카탈로그(`skill_catalog.
    py`)가 그 14개도 원본 SKILL.md(~/.claude/skills)를 근거로 채웠다 —
    이제 시드 스킬 56개 전부가 지침·출력 형식·예시·제약사항을 갖는다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 멱등성 확인(여러 번 불러도 같은 결과)

    rows = conn.execute(
        "SELECT name, description, output_format, examples, constraints FROM skill_pool"
    ).fetchall()
    assert len(rows) == 80  # +1 메일수신함조회(2026-11-26), P1-9 사내 DB 조회(2026-11-26): 스킬 +1, P0-3 코드 저장소 조회(2026-11-24): 도구 +1·스킬 +1, P2-12 업무 영역 템플릿(2026-11-24): 도구 +3·Agent +7·스킬 +9
    incomplete = [
        r["name"]
        for r in rows
        if not all(r[f] for f in ("description", "output_format", "examples", "constraints"))
    ]
    assert incomplete == []


def test_backfill_examples_field_contains_real_newlines_not_literal_backslash_n():
    """회귀 가드 — 코드 생성 과정에서 한 번 실수로 실제 줄바꿈 대신
    리터럴 문자 '\\n' 두 글자가 저장된 적이 있었다(수정 완료)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    row = conn.execute(
        "SELECT examples FROM skill_pool WHERE name = '설계결정기록'"
    ).fetchone()
    assert "\n" in row["examples"]
    assert "\\n" not in row["examples"]


def test_backfill_does_not_overwrite_a_later_manual_edit(client_and_conn):
    """관리자가 백필 이후 값을 직접 수정했다면, 다음 재기동(멱등 재호출)
    이 그 수정을 덮어쓰면 안 된다 — `category`/`show_in_conversation`
    백필과 같은 원칙."""
    _client, conn = client_and_conn
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "내부지식검색"
    )
    pool_registry.update_skill(conn, 1, skill["id"], constraints="관리자가 직접 수정한 값")
    db.init_schema(conn)  # 재기동 시뮬레이션
    updated = next(
        s for s in pool_registry.list_skills(conn) if s["id"] == skill["id"]
    )
    assert updated["constraints"] == "관리자가 직접 수정한 값"


# ---------- 조사 관련 skill — 출처 명시 요구(사용자 요청, 2026-11-17,
# "웹검색, 딥리서치, RAG 등 조사 관련 skill의 경우 출처를 반드시
# 명시하도록 해줘") ----------


def test_research_skills_require_citing_sources_in_constraints():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skills = {s["name"]: s for s in pool_registry.list_skills(conn)}
    for name in (
        "내부지식검색",
        "조직배정관계조회",
        "일반웹검색",
        "실시간웹검색",
        "최신정보필수확인",
        "딥리서치",
        "AI업계동향브리핑",
    ):
        constraints = skills[name]["constraints"] or ""
        assert "출처" in constraints, f"{name}의 constraints에 출처 명시 요구가 없음: {constraints!r}"


# ---------- "스킬생성" — 메타 스킬(사용자 요청, 2026-11-17,
# "주어진 업무를 잘 처리할 수 있는 skill을 생성하는 skill을 추가해줘
# (대화창에서 활용)") ----------


def test_skill_creation_guide_is_seeded_and_conversation_visible():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "스킬생성"
    )
    # 2026-11-21부터 '스킬·도구 목록 조회' 도구로 실제 스킬 풀을 보고 설계한다
    # (tool 방식). 등록 자체는 여전히 사람이 import로 승인한다.
    assert skill["augmentation_mode"] == "tool"
    assert skill["category"] == "general"
    assert skill["show_in_conversation"] is True
    assert skill["output_format"]
    assert skill["examples"]
    assert skill["constraints"]


def test_skill_creation_guide_example_is_valid_importable_markdown():
    """임베딩된 예시 안의 마크다운 스켈레톤이 그냥 설명용 텍스트가
    아니라, 실제로 `skill_markdown.parse_skill_markdown()`으로
    파싱되는 유효한 규격인지 확인한다(사람이 그대로 복붙해 '마크다운
    으로 스킬 가져오기'에 쓸 수 있어야 하므로)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "스킬생성"
    )
    skeleton_start = skill["examples"].index("---\nname:")
    md = skill["examples"][skeleton_start:]
    fields = skill_markdown.parse_skill_markdown(conn, md)
    assert fields["name"] == "VOC분류요약"  # 2026-11-20 — '~가이드' 접미사 명명 폐기
    assert fields["augmentation_mode"] == "none"
    assert fields["description"]
    assert fields["output_format"]
    assert fields["constraints"]


# ---------- skill_markdown.py — export/import 단위 테스트 ----------


def test_export_skill_markdown_includes_tool_native_id_when_single_native_tool():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "내부지식검색"
    )
    md = skill_markdown.export_skill_markdown(conn, skill)
    assert md.startswith("---\n")
    assert "name: 내부지식검색" in md
    assert "tool: vectordb_search" in md
    assert "## 지침" in md
    assert "플랫폼 지식베이스" in md


def test_export_skill_markdown_omits_tool_when_no_native_tool():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "harness-gate-ci"
    )
    md = skill_markdown.export_skill_markdown(conn, skill)
    assert "tool:" not in md


def test_export_skill_markdown_omits_empty_optional_sections(client_and_conn):
    _client, conn = client_and_conn
    pool_registry.create_skill(conn, 1, "섹션없음테스트", "지침 본문", augmentation_mode="none")
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "섹션없음테스트"
    )
    md = skill_markdown.export_skill_markdown(conn, skill)
    assert "## 출력 형식" not in md
    assert "## 예시" not in md
    assert "## 제약사항" not in md


def test_parse_skill_markdown_roundtrips_all_fields():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    md = (
        "---\n"
        "name: 왕복테스트\n"
        "category: sw_dev\n"
        "augmentation_mode: none\n"
        "---\n\n"
        "## 지침\n\n지침 본문\n여러 줄\n\n"
        "## 출력 형식\n\n출력형식값\n\n"
        "## 예시\n\n예시값\n\n"
        "## 제약사항\n\n제약값\n"
    )
    fields = skill_markdown.parse_skill_markdown(conn, md)
    assert fields == {
        "name": "왕복테스트",
        "description": "지침 본문\n여러 줄",
        "default_tool_pool_ids": [],
        "augmentation_mode": "none",
        "category": "sw_dev",
        "output_format": "출력형식값",
        "examples": "예시값",
        "constraints": "제약값",
        "includes": [],  # 2026-11-20 — frontmatter에 includes가 없으면 빈 목록
    }


def test_parse_skill_markdown_resolves_tool_native_id():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    md = (
        "---\n"
        "name: 도구참조테스트\n"
        "category: general\n"
        "augmentation_mode: tool\n"
        "tool: vectordb_search\n"
        "---\n\n"
        "## 지침\n\n본문\n"
    )
    fields = skill_markdown.parse_skill_markdown(conn, md)
    tool_row = conn.execute(
        "SELECT id FROM tool_pool WHERE name = 'VectorDB 내부지식검색'"
    ).fetchone()
    assert fields["default_tool_pool_ids"] == [tool_row["id"]]


def test_parse_skill_markdown_rejects_unknown_tool_native_id():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    md = (
        "---\nname: 실패테스트\naugmentation_mode: tool\ntool: no_such_tool\n---\n\n"
        "## 지침\n\n본문\n"
    )
    try:
        skill_markdown.parse_skill_markdown(conn, md)
        raise AssertionError("should have raised")
    except ValueError as exc:
        assert "no_such_tool" in str(exc)


def test_parse_skill_markdown_rejects_missing_frontmatter():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    try:
        skill_markdown.parse_skill_markdown(conn, "## 지침\n\n본문\n")
        raise AssertionError("should have raised")
    except ValueError as exc:
        assert str(exc) == "missing_frontmatter"


def test_parse_skill_markdown_rejects_missing_name():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    try:
        skill_markdown.parse_skill_markdown(conn, "---\ncategory: general\n---\n\n본문\n")
        raise AssertionError("should have raised")
    except ValueError as exc:
        assert str(exc) == "missing_name"


def test_export_then_parse_then_create_skill_matches_original(client_and_conn):
    """실제 export → (가상의 외부 편집 없이) → import 전체 경로가
    기존 스킬과 동등한 새 스킬을 만드는지 종단 확인."""
    _client, conn = client_and_conn
    original = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "내부지식검색"
    )
    md = skill_markdown.export_skill_markdown(conn, original)
    fields = skill_markdown.parse_skill_markdown(conn, md)
    fields["name"] = "내부지식검색_복사본"  # 이름 중복 방지
    new_id = pool_registry.create_skill(conn, 1, **fields)
    copy = next(s for s in pool_registry.list_skills(conn) if s["id"] == new_id)
    assert copy["description"] == original["description"]
    assert copy["augmentation_mode"] == original["augmentation_mode"]
    assert copy["default_tool_pool_ids"] == original["default_tool_pool_ids"]


# ---------- API 엔드포인트 ----------


def test_export_endpoint_returns_markdown_admin_only(client_and_conn):
    client, conn = client_and_conn
    skill = next(
        s for s in pool_registry.list_skills(conn) if s["name"] == "내부지식검색"
    )

    resp = client.get(f"/api/skill-pool/{skill['id']}/export")
    assert resp.status_code == 401  # 로그인 전

    _login(client, "manager1", "managerpass")
    resp = client.get(f"/api/skill-pool/{skill['id']}/export")
    assert resp.status_code == 200
    assert "name: 내부지식검색" in resp.text
    assert resp.headers["content-type"].startswith("text/markdown")


def test_export_endpoint_404_for_unknown_id(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.get("/api/skill-pool/999999/export")
    assert resp.status_code == 404


def test_import_endpoint_creates_skill_and_rejects_member(client_and_conn):
    client, conn = client_and_conn
    md = (
        "---\nname: API로추가한스킬\ncategory: general\naugmentation_mode: none\n"
        "---\n\n## 지침\n\n본문 지침\n\n## 출력 형식\n\n표\n"
    )

    _login(client, "member1", "memberpass")
    resp = client.post("/api/skill-pool/import", json={"markdown": md})
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.post("/api/skill-pool/import", json={"markdown": md})
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    row = conn.execute(
        "SELECT * FROM skill_pool WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["name"] == "API로추가한스킬"
    assert row["output_format"] == "표"


def test_import_endpoint_rejects_malformed_markdown(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/skill-pool/import", json={"markdown": "이건 마크다운이 아님"})
    assert resp.status_code == 400


def test_import_endpoint_duplicate_name_returns_409(client_and_conn):
    client, _conn = client_and_conn
    md = "---\nname: 내부지식검색\naugmentation_mode: none\n---\n\n## 지침\n\n본문\n"
    _login(client, "manager1", "managerpass")
    resp = client.post("/api/skill-pool/import", json={"markdown": md})
    assert resp.status_code == 409
