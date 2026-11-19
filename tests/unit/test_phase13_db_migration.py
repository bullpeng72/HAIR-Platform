"""Phase 13 — 실제 발생한 500 에러 수정: 기존 DB 파일에 새 컬럼 마이그레이션
(사용자 보고, 2026-09-27).

"AI 에이전트 관리"·"Crew 실행" 메뉴에서 500 Internal Server Error가 실제로
발생했다 — 원인은 데모 서버의 `hair.db`가 `agent_pool.framework` 컬럼이
생기기 전에 만들어진 파일이었고, `CREATE TABLE IF NOT EXISTS`는 이미 있는
테이블에 새 컬럼을 추가하지 못해서 `SELECT * FROM agent_pool` 결과에
`framework` 키가 없었기 때문이다(`row["framework"]`에서 KeyError → 500).
"""

from __future__ import annotations

import json

import pytest

from hair_platform import db


def test_gc95_init_schema_migrates_pre_existing_agent_pool_table_without_new_columns():
    conn = db.get_connection(":memory:")
    # framework/llm_provider/llm_model/last_output이 생기기 전의 "옛날" 스키마를
    # 흉내낸다 — 실제로 겪은 상황(기존 hair.db 파일)과 동일한 조건.
    conn.execute(
        "CREATE TABLE agent_pool ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "role_name TEXT NOT NULL, "
        "skill_tags TEXT NOT NULL DEFAULT '[]', "
        "cost_estimate REAL NOT NULL DEFAULT 0.0, "
        "performance_score REAL, "
        "status TEXT NOT NULL DEFAULT 'pending_onboarding', "
        "source_job_item_id INTEGER"
        ")"
    )
    conn.execute(
        "INSERT INTO agent_pool (role_name, status) VALUES ('기존 에이전트', 'active')"
    )
    conn.commit()

    # init_schema()를 다시 돌리는 것이 서버 재시작 시 실제로 벌어지는 일이다.
    db.init_schema(conn)

    row = conn.execute(
        "SELECT * FROM agent_pool WHERE role_name = '기존 에이전트'"
    ).fetchone()
    assert row["framework"] == "stub"  # 기본값으로 안전하게 채워졌다
    assert row["llm_provider"] is None
    assert row["llm_model"] is None
    assert row["last_output"] is None
    assert row["status"] == "active"  # 기존 데이터는 보존된다


def test_gc96_init_schema_is_idempotent_on_fresh_db():
    """새 DB에서는 컬럼이 이미 SCHEMA에 있으므로 마이그레이션이 아무 것도 안
    해야 하고, 두 번 불러도 에러가 없어야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # 두 번째 호출도 에러 없이 통과해야 한다
    row = conn.execute(
        "INSERT INTO agent_pool (role_name) VALUES ('테스트') RETURNING framework"
    ).fetchone()
    assert row["framework"] == "stub"


@pytest.mark.parametrize(
    "removed_name,kept_name,tool_pool_name",
    [
        # "websearch"/"외부검색" 케이스(원래 세 번째였던 "외부 자료 검색
        # (Wikipedia)")는 사용자 요청(2026-10-26, "도구/skill에서 동작하지
        # 않는 검색은 삭제해줘")로 그 tool_pool 시드 자체가 빠지면서
        # 제거했다 — 이 테스트는 "별칭 통합" 메커니즘 자체를 검증하는
        # 것이라, 여전히 시드되는 vectordb/graphdb 두 케이스만으로도
        # 충분히 검증된다.
        ("vectordb", "지식검색", "VectorDB 지식검색"),
        ("관계질의", "관계조회", "GraphDB 관계조회"),
    ],
)
def test_gc210_init_schema_merges_duplicate_skill_alias(
    removed_name, kept_name, tool_pool_name
):
    """사용자 요청(2026-10-06, "할일로 나누기"의 "필요 스킬" 자동완성에
    "websearch"/"외부검색"이 완전히 같은 뜻으로 중복 표시된다는 리포트 →
    "외부검색" 하나만 남기도록 개선, 이어서 같은 패턴인 "vectordb"/"지식검색"·
    "관계조회"/"관계질의"도 동일하게 통합) — 이미 제거 대상 skill_pool 행과
    그 이름을 쓰는 할 일/Agent가 있던 기존 DB에서도, init_schema()를 다시
    돌리면 그 행이 지워지고 그걸 쓰던 데이터가 남기는 쪽 이름으로
    안전하게 옮겨가야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    # 통합 전 상태를 흉내낸다: 제거 대상 skill_pool 행을 되살리고,
    # 그 이름을 쓰는 task/agent 데이터를 심는다.
    tool_id = conn.execute(
        "SELECT id FROM tool_pool WHERE name = ?", (tool_pool_name,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO skill_pool (name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, '별칭', ?, 'tool')",
        (removed_name, f"[{tool_id}]"),
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('u', 'h', 'member')"
    )
    user_id = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, status) VALUES (?, 't', 'decomposed')",
        (user_id,),
    )
    brief_id = conn.execute("SELECT id FROM briefs").fetchone()["id"]
    conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills, headcount) "
        "VALUES (?, '담당', ?, 1)",
        (brief_id, json.dumps([removed_name, "인사 정책"], ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES "
        "('담당 Agent', ?, 'active')",
        (json.dumps([removed_name], ensure_ascii=False),),
    )
    conn.commit()

    db.init_schema(conn)  # 서버 재시작 시 실제로 벌어지는 일

    skill_names = {
        row["name"] for row in conn.execute("SELECT name FROM skill_pool").fetchall()
    }
    assert removed_name not in skill_names
    assert kept_name in skill_names

    task = conn.execute(
        "SELECT required_skills FROM tasks WHERE role_name = '담당'"
    ).fetchone()
    assert json.loads(task["required_skills"]) == [kept_name, "인사 정책"]

    agent = conn.execute(
        "SELECT skill_tags FROM agent_pool WHERE role_name = '담당 Agent'"
    ).fetchone()
    assert json.loads(agent["skill_tags"]) == [kept_name]


def test_gc97_list_agent_pool_endpoint_survives_pre_migration_db(client_and_conn):
    """실제로 500이 났던 경로 — GET /api/agent-pool가 framework 없는 옛 행에도
    안전해야 한다(방금 마이그레이션했으므로 컬럼 자체는 이제 항상 있다)."""
    client, conn = client_and_conn
    conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("마이그레이션 이전 데이터",),
    )
    conn.commit()

    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200
    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200
    row = next(a for a in resp.json() if a["role_name"] == "마이그레이션 이전 데이터")
    assert row["framework"] == "stub"


# ---------- 2026-11-19, "에이전틱 실행엔진"에 맞춰 TOOL/SKILL/AGENT/
# PIPELINE/TEAM 정비 — 기존 hierarchical 설정을 sequential로 전환 ----------


def test_existing_dev_crew_team_with_stale_manager_label_migrates_to_sequential_graph_engine():
    """"구현" 단계가 2026-11-07에 sequential_relay로 전환된 뒤에도, 그
    전에 생성된 "자율 개발 크루" 팀은 옛 hierarchical 설정(default_
    process='hierarchical', 매니저 지정)과 옛 role_label("4단계:
    매니저(hierarchical 위임 리드)")이 그대로 남아 있을 수 있다 —
    재기동 시 sequential + 신규 그래프 엔진으로 전환되고 role_label도
    바로잡혀야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    manager_agent = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = '개발 계획 수립가'"
    ).fetchone()
    assert manager_agent is not None

    cur = conn.execute(
        "INSERT INTO teams (name, description, is_active, status, default_process, "
        "default_manager_agent_id) VALUES ('자율 개발 크루', '', 1, 'active', "
        "'hierarchical', ?)",
        (manager_agent["id"],),
    )
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, ?)",
        (
            team_id,
            manager_agent["id"],
            "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)",
        ),
    )
    conn.commit()

    # 서버 재시작 시 실제로 벌어지는 일 — 다시 돌려도 안전해야 한다.
    db.init_schema(conn)

    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    assert team["default_process"] == "sequential"
    assert team["default_manager_agent_id"] is None
    assert team["use_graph_engine"] == 1
    assert team["pipeline_template_id"] is not None

    member = conn.execute(
        "SELECT role_label FROM team_members WHERE team_id = ? AND member_id = ?",
        (team_id, manager_agent["id"]),
    ).fetchone()
    assert member["role_label"] == "3단계: 개발 계획 수립"


def test_dev_crew_team_with_admin_customized_label_is_not_auto_converted():
    """관리자가 이미 role_label을 직접 손봤다면(옛 stale 문구와 더 이상
    일치하지 않음) hierarchical 설정은 건드리지 않는다 — 다른 마이그
    레이션들과 같은 안전장치(의도적으로 되돌린 값을 다시 덮어쓰지
    않음)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    manager_agent = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = '개발 계획 수립가'"
    ).fetchone()
    assert manager_agent is not None

    cur = conn.execute(
        "INSERT INTO teams (name, description, is_active, status, default_process, "
        "default_manager_agent_id) VALUES ('자율 개발 크루', '', 1, 'active', "
        "'hierarchical', ?)",
        (manager_agent["id"],),
    )
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '관리자가 직접 적은 라벨')",
        (team_id, manager_agent["id"]),
    )
    conn.commit()

    db.init_schema(conn)

    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    assert team["default_process"] == "hierarchical"
    assert team["default_manager_agent_id"] == manager_agent["id"]
    assert team["use_graph_engine"] == 0
