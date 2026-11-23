"""개발 스킬·에이전트 품질 개선(2026-11-21, "개발 스킬, 에이전트의 품질을 확인하고 개선해줘")."""

from __future__ import annotations

import json

from hair_platform import agent_runtime, db, pool_registry, skill_catalog

NEW = ("단위테스트작성", "버그원인분석", "코드베이스분석")


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _tags(conn, role):
    return json.loads(
        conn.execute("SELECT skill_tags FROM agent_pool WHERE role_name = ?", (role,)).fetchone()[0]
    )


def test_new_dev_skills_have_all_sections():
    conn = _conn()
    rows = {r["name"]: r for r in pool_registry.list_skills(conn)}
    for name in NEW:
        r = rows[name]
        assert r["category"] == "sw_dev"
        for field in ("description", "output_format", "examples", "constraints"):
            assert r[field] and len(r[field]) > 40, (name, field)
    # 실측(백엔드 개발자 자기 테스트 실패 2건)에서 나온 규칙.
    rules = rows["단위테스트작성"]["constraints"]
    assert "기대값은 직접 다시 계산" in rules and "서로 다른 예외" in rules


def test_dev_agents_get_and_lose_the_right_skills_once():
    conn = _conn()
    for role in ("백엔드 개발자", "풀스택 개발자", "프론트엔드 개발자", "데이터/인프라 개발자", "QA 기능테스터"):
        assert "단위테스트작성" in _tags(conn, role), role
    assert "버그원인분석" in _tags(conn, "코드 검증가")
    analyst = _tags(conn, "코드베이스 분석가")
    assert "코드베이스분석" in analyst
    assert "조직배정관계조회" not in analyst and "내부지식검색" not in analyst
    assert "조직배정관계조회" not in _tags(conn, "DA")
    # admin이 다시 붙여도 재기동 때 또 떼지 않는다(한 번만 실행).
    conn.execute(
        "UPDATE agent_pool SET skill_tags = ? WHERE role_name = 'DA'",
        (json.dumps(["데이터모델설계", "조직배정관계조회"], ensure_ascii=False),),
    )
    conn.commit()
    db.init_schema(conn)
    assert "조직배정관계조회" in _tags(conn, "DA")


def test_codebase_analyst_no_longer_gets_org_chart_or_kb_rules():
    """실측: 코드 분석 요청마다 조직도 데이터와 '지식베이스 결과만으로 답하라' 규칙이 주입됐다."""
    conn = _conn()
    ctx = agent_runtime.retrieve_context(
        conn, _tags(conn, "코드베이스 분석가"), "이 저장소의 인증 모듈 구조를 분석해줘"
    )
    assert ctx is None


def test_one_time_changes_support_add_and_remove():
    spec = skill_catalog.ONE_TIME_AGENT_SKILL_ADDITIONS["2026-11-21-dev-agent-quality"]
    assert spec["코드베이스 분석가"]["remove"] == ["조직배정관계조회", "내부지식검색"]
