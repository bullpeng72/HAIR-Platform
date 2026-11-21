"""Phase 82 — 스킬 이름 "~가이드" 접미사 제거(사용자 요청, 2026-11-20,
"skill 이름에서 '가이드'를 제거해줘").

시드는 이미 새 이름을 쓰므로, 이 파일은 **이전 이름으로 만들어진 기존
DB**가 기동 시 마이그레이션으로 정확히 따라오는지 검증한다 — 스킬 행의
id(=코드)는 유지되고, 그 이름을 문자열로 참조하던 Agent/사람/Task 태그와
대화 메시지의 `used_skill`이 함께 바뀌어야 연결이 끊기지 않는다.
"""

from __future__ import annotations

import json

from hair_platform import db


def _fresh_conn():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    return conn


def test_seeds_have_no_guide_suffixed_skill_names():
    conn = _fresh_conn()
    names = [r["name"] for r in conn.execute("SELECT name FROM skill_pool")]
    assert not [n for n in names if n.endswith("가이드")]
    for expected in ("데이터분석", "데이터시각화", "제안서작성", "문서작성", "스킬생성"):
        assert expected in names


def test_legacy_guide_names_are_renamed_with_references():
    conn = _fresh_conn()
    # 이전 버전 DB를 흉내 낸다 — 새 이름 행을 옛 이름으로 되돌리고, 그
    # 이름을 참조하는 Agent/메시지를 만든다.
    skill_id = conn.execute(
        "SELECT id FROM skill_pool WHERE name = '데이터분석'"
    ).fetchone()["id"]
    conn.execute("UPDATE skill_pool SET name = '데이터분석가이드' WHERE id = ?", (skill_id,))
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        ("분석가", json.dumps(["데이터분석가이드", "내부지식검색"], ensure_ascii=False)),
    ).lastrowid
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('u1', 'x', 'member')"
    )
    user_id = conn.execute("SELECT id FROM users WHERE username = 'u1'").fetchone()["id"]
    conv_id = conn.execute(
        "INSERT INTO conversations (title, created_by) VALUES ('c', ?)", (user_id,)
    ).lastrowid
    msg_id = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content, used_skill) VALUES (?, 'human', ?, 'q', '데이터분석가이드')",
        (conv_id, user_id),
    ).lastrowid
    conn.commit()

    db.init_schema(conn)  # 재기동
    db.init_schema(conn)  # 멱등

    assert (
        conn.execute("SELECT name FROM skill_pool WHERE id = ?", (skill_id,)).fetchone()[
            "name"
        ]
        == "데이터분석"
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM skill_pool WHERE name IN ('데이터분석', '데이터분석가이드')"
        ).fetchone()["c"]
        == 1
    )
    tags = json.loads(
        conn.execute("SELECT skill_tags FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()[
            "skill_tags"
        ]
    )
    assert tags == ["데이터분석", "내부지식검색"]
    assert (
        conn.execute(
            "SELECT used_skill FROM conversation_messages WHERE id = ?", (msg_id,)
        ).fetchone()["used_skill"]
        == "데이터분석"
    )


def test_functional_renames_knowledge_and_relationship_skills():
    """사용자 요청(2026-11-20, "지식검색 -> 내부지식검색, 관계조회도 기능에
    맞춰 오해없도록 스킬명 변경") — 이름·설명·참조가 함께 바뀌고, admin이
    직접 고친 설명은 덮어쓰지 않는다."""
    conn = _fresh_conn()
    kb_id = conn.execute("SELECT id FROM skill_pool WHERE name = '내부지식검색'").fetchone()["id"]
    rel_id = conn.execute(
        "SELECT id FROM skill_pool WHERE name = '조직배정관계조회'"
    ).fetchone()["id"]
    # 개명 이전 DB 흉내 — 옛 이름 + 옛 기본 설명(관계) / admin이 고친 설명(지식).
    conn.execute(
        "UPDATE skill_pool SET name = '지식검색', description = 'admin이 직접 쓴 설명' "
        "WHERE id = ?",
        (kb_id,),
    )
    conn.execute(
        "UPDATE skill_pool SET name = '관계조회', description = 'GraphDB 기반 관계분석 역량' "
        "WHERE id = ?",
        (rel_id,),
    )
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        ("조사가", json.dumps(["지식검색", "관계조회"], ensure_ascii=False)),
    ).lastrowid
    conn.commit()

    db.init_schema(conn)

    kb = conn.execute("SELECT name, description FROM skill_pool WHERE id = ?", (kb_id,)).fetchone()
    rel = conn.execute(
        "SELECT name, description FROM skill_pool WHERE id = ?", (rel_id,)
    ).fetchone()
    assert kb["name"] == "내부지식검색"
    assert kb["description"] == "admin이 직접 쓴 설명"  # 손댄 설명은 유지
    assert rel["name"] == "조직배정관계조회"
    assert "조직도" in rel["description"]  # 옛 기본 설명은 새 문구로
    tags = json.loads(
        conn.execute("SELECT skill_tags FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()[
            "skill_tags"
        ]
    )
    assert tags == ["내부지식검색", "조직배정관계조회"]
    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM skill_pool WHERE name IN ('지식검색', '관계조회')"
        ).fetchone()["c"]
        == 0
    )
