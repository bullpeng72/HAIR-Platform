"""기본 구성 점검(2026-11-23, "P2 진행해줘" — P2-10)."""

from __future__ import annotations

import json

from hair_platform import config_check, db

BIZ = "사업/기획 업무 처리 파이프라인"


def _conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _stage_roles(conn, tpl, seq):
    return [r[0] for r in conn.execute(
        "SELECT sa.role_name FROM pipeline_stage_agents sa JOIN pipeline_stages s ON s.id = sa.stage_id "
        "JOIN pipeline_templates t ON t.id = s.pipeline_template_id WHERE t.name = ? AND s.seq_order = ? "
        "ORDER BY sa.seq_order", (tpl, seq))]


def _admin(conn):
    from hair_platform.auth import Role, create_user

    return create_user(conn, "cfg_admin", "pw", Role.ADMIN)


def test_fresh_install_has_no_differences():
    assert config_check.check(_conn()) == []


def test_missing_stage_role_is_found_and_restored_in_reference_order():
    """실측(2026-11-23): 운영 DB의 사업/기획 2단계에 데이터 분석가가 빠져 있었다."""
    conn = _conn()
    conn.execute(
        "DELETE FROM pipeline_stage_agents WHERE role_name = '데이터 분석가' AND stage_id = (SELECT s.id FROM "
        "pipeline_stages s JOIN pipeline_templates t ON t.id = s.pipeline_template_id WHERE t.name = ? AND s.seq_order = 2)",
        (BIZ,),
    )
    conn.commit()
    items = config_check.check(conn)
    assert len(items) == 1 and items[0]["kind"] == "stage_role_missing" and items[0]["fixable"]
    result = config_check.apply(conn, _admin(conn), [items[0]["id"]])
    assert result["applied"] == [items[0]["id"]]
    assert _stage_roles(conn, BIZ, 2) == ["외부 자료 조사가", "데이터 분석가", "3C4P SWOT 분석가"]
    assert config_check.check(conn) == []


def test_changed_settings_are_reported_but_not_fixable():
    conn = _conn()
    conn.execute(
        "UPDATE pipeline_stages SET requires_human_approval = 0 WHERE seq_order = 1 AND pipeline_template_id = "
        "(SELECT id FROM pipeline_templates WHERE name = ?)", (BIZ,))
    items = config_check.check(conn)
    assert items and items[0]["kind"] == "stage_setting_differs" and not items[0]["fixable"]
    result = config_check.apply(conn, _admin(conn), [items[0]["id"]])
    assert result["applied"] == [] and result["skipped"] == [items[0]["id"]]


def test_missing_agent_skill_skill_and_tool_are_restored():
    conn = _conn()
    row = conn.execute("SELECT id, skill_tags FROM agent_pool WHERE role_name = '데이터 분석가'").fetchone()
    tags = [t for t in json.loads(row["skill_tags"]) if t != "데이터시각화"]
    conn.execute("UPDATE agent_pool SET skill_tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), row["id"]))
    conn.execute("DELETE FROM skill_pool WHERE name = '회의록정리'")
    conn.execute("DELETE FROM tool_pool WHERE name = '발표자료 아웃라인 생성기'")
    conn.execute("DELETE FROM skill_pool WHERE name = '발표자료구성'")  # 이 도구를 쓰는 스킬도 함께
    conn.commit()
    kinds = {i["kind"] for i in config_check.check(conn)}
    assert {"agent_skills_missing", "skill_missing", "tool_missing"} <= kinds
    ids = [i["id"] for i in config_check.check(conn) if i["fixable"]]
    # 도구를 먼저 되살려야 스킬이 그 도구를 가리킬 수 있다 — 화면은 check() 순서(도구가 맨 뒤)로 보낸다.
    assert ids[-1].startswith("tool:")
    config_check.apply(conn, _admin(conn), ids)
    remaining = [i for i in config_check.check(conn) if i["fixable"]]
    assert remaining == []
    skill = conn.execute("SELECT default_tool_pool_ids FROM skill_pool WHERE name = '발표자료구성'").fetchone()[0]
    tool_id = conn.execute("SELECT id FROM tool_pool WHERE name = '발표자료 아웃라인 생성기'").fetchone()[0]
    assert json.loads(skill) == [tool_id]


def test_endpoints_are_admin_only(client_and_conn):
    client, _conn_ = client_and_conn
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.get("/api/admin/config-check").status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert client.get("/api/admin/config-check").json() == []
    assert client.post("/api/admin/config-check/apply", json={"item_ids": ["nope"]}).json() == {"applied": [], "skipped": ["nope"]}
