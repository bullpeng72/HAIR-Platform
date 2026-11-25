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
    # 2026-11-25부터 이 단계는 계획 분담(리드 PM이 맨 앞).
    assert _stage_roles(conn, BIZ, 2) == ["PM", "외부 자료 조사가", "데이터 분석가", "3C4P SWOT 분석가"]
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


def test_admin_screen_fills_config_check_after_content_is_drawn():
    """2026-11-25 회귀(사용자 보고 — 사용자 관리의 "기본 구성 점검"이 "불러오는 중…"으로 멈춤): renderAdmin 앞에서
    setTimeout(…, 0)으로 부르면 /api/users 응답 전에 실행돼 칸이 아직 없어 그냥 끝났다. 화면을 그린 뒤 불러야 한다."""
    from hair_platform import webui

    html = webui.INDEX_HTML
    body = html[html.index("async function renderAdmin(content) {"):]
    body = body[: body.index("\n}\n")]
    assert "setTimeout(renderConfigCheckSection" not in body
    assert body.index('id="config-check-section"') < body.index("renderConfigCheckSection();")



def test_old_default_relay_stage_is_offered_as_plan_merge_upgrade():
    """2026-11-25 — 사업/기획 "자료 조사·분석"이 계획 분담으로 바뀌었다. 예전 기본(순차 릴레이, 추가 역할 없음)인 설치본은
    반영 가능 항목으로 올리고, 반영하면 모드·리드(PM)가 바뀐다. admin이 역할을 더한 단계는 차이만 보여 준다."""
    conn = _conn()
    sid = conn.execute(
        "SELECT s.id FROM pipeline_stages s JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "WHERE t.name = ? AND s.seq_order = 2", (BIZ,)).fetchone()[0]
    conn.execute("UPDATE pipeline_stages SET mode = 'sequential_relay', lead_role_name = NULL WHERE id = ?", (sid,))
    conn.execute("DELETE FROM pipeline_stage_agents WHERE stage_id = ? AND role_name = 'PM'", (sid,))
    conn.commit()
    items = config_check.check(conn)
    assert [i["kind"] for i in items] == ["stage_mode_upgrade"] and items[0]["fixable"]
    config_check.apply(conn, _admin(conn), [items[0]["id"]])
    row = conn.execute("SELECT mode, lead_role_name FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    assert tuple(row) == ("plan_merge", "PM")
    assert _stage_roles(conn, BIZ, 2) == ["PM", "외부 자료 조사가", "데이터 분석가", "3C4P SWOT 분석가"]
    assert config_check.check(conn) == []

    # admin이 역할을 더한 순차 릴레이는 반영 가능으로 올리지 않는다
    conn.execute("UPDATE pipeline_stages SET mode = 'sequential_relay', lead_role_name = NULL WHERE id = ?", (sid,))
    conn.execute("INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, instructions) "
                 "VALUES (?, '문서 교정자', 9, '')", (sid,))
    conn.commit()
    kinds = {i["kind"]: i["fixable"] for i in config_check.check(conn)}
    assert "stage_mode_upgrade" not in kinds and kinds.get("stage_setting_differs") is False
