"""팀 위임형 우회 차단·계산 도구 스킬 대화창 노출(2026-11-23, "팀이나 파이프라인에서 병행위임
기능 가능 여부를 확인..." → "이어서 정리해줘")."""

from __future__ import annotations

import json

import pytest

from hair_platform import db, teams
from hair_platform.auth import Role, create_user


def _setup():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin = create_user(conn, "td_admin", "pw", Role.ADMIN)
    agents = [r[0] for r in conn.execute("SELECT id FROM agent_pool WHERE status = 'active' LIMIT 3")]
    return conn, admin, agents


# ---------- 1. 새 팀은 "매니저" 라벨로 위임형이 될 수 없다 ----------


def test_new_team_cannot_become_hierarchical_through_manager_label():
    conn, admin, agents = _setup()
    team = teams.create_team(conn, admin, "신규팀")
    with pytest.raises(ValueError, match="manager_role_label_blocked_for_new_teams"):
        teams.add_team_member(conn, admin, team, "agent", agents[0], "매니저")
    assert teams.manager_agent_id_for_team(conn, team) is None
    # 일반 라벨·사람 멤버의 "매니저" 라벨은 그대로 허용(사람은 매니저 추정 대상이 아니다).
    teams.add_team_member(conn, admin, team, "agent", agents[1], "리드")
    human = conn.execute("SELECT id FROM human_pool LIMIT 1").fetchone()
    if human is not None:
        teams.add_team_member(conn, admin, team, "human", human[0], "프로젝트 매니저")
    with pytest.raises(ValueError, match="hierarchical_mode_blocked_for_new_teams"):
        teams.update_team_defaults(conn, admin, team, default_process="hierarchical")


def test_existing_hierarchical_team_keeps_working():
    conn, admin, agents = _setup()
    team = teams.create_team(conn, admin, "기존 위임형 팀")
    # 정책 이전에 만들어진 팀을 흉내 낸다 — 라벨이 이미 들어가 있다.
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) VALUES (?, 'agent', ?, '매니저')",
        (team, agents[0]),
    )
    assert teams.manager_agent_id_for_team(conn, team) == agents[0]
    teams.add_team_member(conn, admin, team, "agent", agents[1], "부매니저")
    teams.update_team_defaults(conn, admin, team, default_process="hierarchical")


def test_add_member_endpoint_returns_400_with_guidance(client_and_conn):
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    team_id = client.post("/api/teams", json={"name": "API 신규팀"}).json()["id"]
    agent_id = conn.execute("SELECT id FROM agent_pool WHERE status = 'active' LIMIT 1").fetchone()[0]
    resp = client.post(
        f"/api/teams/{team_id}/members",
        json={"member_type": "agent", "member_id": agent_id, "role_label": "매니저"},
    )
    assert resp.status_code == 400 and "매니저" in resp.json()["detail"]


# ---------- 2. 계산 도구(custom_python) 스킬은 대화창에 보인다 ----------


def test_custom_tool_skills_are_shown_and_mcp_skills_stay_hidden():
    conn, _admin, _agents = _setup()
    shown = dict(conn.execute("SELECT name, show_in_conversation FROM skill_pool").fetchall())
    for name in ("데이터분석", "데이터시각화", "사업성분석", "커버리지체크", "프레임워크템플릿생성"):
        assert shown[name] == 1, name
    for name in ("일반웹검색", "recommend-fix-workflow"):  # MCP 도구 — 대화창에서 호출 불가
        assert shown[name] == 0, name


def test_migration_reshows_only_skills_hidden_by_the_old_rule():
    conn, _admin, _agents = _setup()
    conn.execute("UPDATE skill_pool SET show_in_conversation = 0 WHERE name IN ('데이터분석', '일반웹검색', '경영보고서작성')")
    db._show_custom_tool_skills_in_conversation(conn)
    shown = dict(conn.execute("SELECT name, show_in_conversation FROM skill_pool").fetchall())
    assert shown["데이터분석"] == 1  # custom_python 도구 — 되살림
    assert shown["일반웹검색"] == 0  # MCP — 그대로
    assert shown["경영보고서작성"] == 0  # 도구 없는 스킬은 admin이 끈 것 — 건드리지 않음
    assert json.loads(conn.execute("SELECT default_tool_pool_ids FROM skill_pool WHERE name='데이터분석'").fetchone()[0])
