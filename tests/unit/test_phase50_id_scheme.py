"""Phase 50 — Agent/Tool/Skill 표시용 ID 체계(사용자 요청, 2026-10-27,
"에이전트, 도구/skill ID 체계를 수립하고(종류 구분 가능) 초기화하여
정리해줘").

사용자가 두 가지를 확인해줬다(2026-10-27): (1) 기존 정수 PK(`id`)는
FK·URL 경로가 의존하므로 그대로 두고 "표시용 코드"만 추가한다(기본키
교체 아님), (2) 종류는 풀마다 이미 있는 분류를 그대로 쓴다 — Agent는
레퍼런스(`AGENT_SPECS`) vs 커스텀, Tool은 `kind`, Skill은
`augmentation_mode`.

이 파일은 (1) `id_scheme.assign_code()`의 일련번호 로직 자체,
(2) `db.init_schema()`가 신규/기존 DB 양쪽에서 22/11/48개 전체에
올바른 코드를 배정하는지, (3) 새로 생성되는 Agent/Tool/Skill이 생성
즉시(다음 재기동을 기다리지 않고) 코드를 받는지, (4) API 응답에
`code`가 실제로 실리는지를 검증한다."""

from __future__ import annotations

import pytest

from hair_platform import data_admin, db, id_scheme, pool_registry


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


# ---------- assign_code() 자체 ----------


def test_assign_code_starts_at_001_and_increments_within_same_segment():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    # 레퍼런스 22종이 이미 AGT-REF-001..022를 다 썼으니, 새 세그먼트
    # (AGT-CUSTOM)로 번호가 1부터 다시 시작하는지 확인한다.
    cur = conn.execute("INSERT INTO agent_pool (role_name) VALUES ('임시 역할 A')")
    code_a = id_scheme.assign_code(
        conn, "agent_pool", cur.lastrowid, id_scheme.AGENT_CUSTOM_SEGMENT
    )
    cur2 = conn.execute("INSERT INTO agent_pool (role_name) VALUES ('임시 역할 B')")
    code_b = id_scheme.assign_code(
        conn, "agent_pool", cur2.lastrowid, id_scheme.AGENT_CUSTOM_SEGMENT
    )
    assert code_a == "AGT-CUSTOM-001"
    assert code_b == "AGT-CUSTOM-002"


def test_assign_code_different_segments_number_independently():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    cur = conn.execute(
        "INSERT INTO tool_pool (name, kind) VALUES ('임시 도구', 'custom_python')"
    )
    pyexec_code = id_scheme.assign_code(
        conn, "tool_pool", cur.lastrowid, id_scheme.TOOL_KIND_SEGMENT["custom_python"]
    )
    # 기존 레퍼런스 PYEXEC 7개(Spec EARS 린터~수치 데이터 분석기, 2026-
    # 11-11 추가) 다음 번호라 008이어야 한다 — NATIVE/MCPSTDIO/MCPRMT
    # 세그먼트 번호와는 무관.
    assert pyexec_code == "TUL-PYEXEC-008"


def test_assign_code_unknown_tool_kind_or_skill_mode_raises():
    with pytest.raises(KeyError):
        id_scheme.TOOL_KIND_SEGMENT["made_up_kind"]
    with pytest.raises(KeyError):
        id_scheme.SKILL_MODE_SEGMENT["made_up_mode"]


# ---------- init_schema() 전체 배정(신규 DB) ----------


def test_init_schema_assigns_codes_to_all_reference_rows():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    agent_codes = [
        r["code"] for r in conn.execute("SELECT code FROM agent_pool").fetchall()
    ]
    tool_codes = [
        r["code"] for r in conn.execute("SELECT code FROM tool_pool").fetchall()
    ]
    skill_codes = [
        r["code"] for r in conn.execute("SELECT code FROM skill_pool").fetchall()
    ]

    # 2026-11-08 "콘텐츠 카피라이팅" 추가(사업/기획 업무 처리 파이프라인
    # 신설, AGENT_SPECS 정규 목록에 포함)로 22 → 23. 2026-11-11 "데이터
    # 분석가"/"제안서 작성자"/"실적 보고서 작성자" 추가(제안서/실적서
    # 작성 파이프라인 신설)로 23 → 26, Tool "수치 데이터 분석기" 추가로
    # 11 → 12, Skill 3종(data-analysis-guide/proposal-writing-guide/
    # performance-report-writing-guide) 추가로 48 → 51.
    assert len(agent_codes) == 26
    assert all(c is not None for c in agent_codes)
    assert all(c.startswith("AGT-REF-") for c in agent_codes)  # 26종 전원 레퍼런스
    assert len(set(agent_codes)) == 26  # 전부 유일

    assert len(tool_codes) == 12
    assert all(c is not None for c in tool_codes)
    assert len(set(tool_codes)) == 12

    assert len(skill_codes) == 51
    assert all(c is not None for c in skill_codes)
    assert len(set(skill_codes)) == 51


def test_init_schema_tool_codes_reflect_kind():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    rows = conn.execute("SELECT code, kind FROM tool_pool").fetchall()
    for row in rows:
        segment = id_scheme.TOOL_KIND_SEGMENT[row["kind"]]
        assert row["code"].startswith(f"TUL-{segment}-")


def test_init_schema_skill_codes_reflect_augmentation_mode():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    rows = conn.execute("SELECT code, augmentation_mode FROM skill_pool").fetchall()
    for row in rows:
        segment = id_scheme.SKILL_MODE_SEGMENT[row["augmentation_mode"]]
        assert row["code"].startswith(f"SKL-{segment}-")


def test_init_schema_is_idempotent_does_not_reassign_existing_codes():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    first = conn.execute(
        "SELECT code FROM agent_pool WHERE role_name = '백엔드 개발자'"
    ).fetchone()["code"]

    db.init_schema(conn)  # 재기동을 흉내
    second = conn.execute(
        "SELECT code FROM agent_pool WHERE role_name = '백엔드 개발자'"
    ).fetchone()["code"]
    assert first == second


# ---------- 기존(마이그레이션 전) DB 백필 ----------


def test_backfill_missing_codes_only_touches_null_codes():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    # 이미 배정된 코드를 하나 손으로 지워 "code 없는 기존 행"을 흉내낸다
    # (기존 DB에 컬럼만 추가된 직후 상태와 동일).
    conn.execute("UPDATE agent_pool SET code = NULL WHERE role_name = '백엔드 개발자'")
    other_code_before = conn.execute(
        "SELECT code FROM agent_pool WHERE role_name = '코드 검증가'"
    ).fetchone()["code"]

    counts = id_scheme.backfill_missing_codes(conn)

    assert counts["agent_pool"] == 1  # 방금 NULL로 만든 것 하나만
    restored = conn.execute(
        "SELECT code FROM agent_pool WHERE role_name = '백엔드 개발자'"
    ).fetchone()["code"]
    assert restored is not None and restored.startswith("AGT-REF-")
    other_code_after = conn.execute(
        "SELECT code FROM agent_pool WHERE role_name = '코드 검증가'"
    ).fetchone()["code"]
    assert other_code_after == other_code_before  # 멀쩡했던 건 안 건드림


def test_reset_and_reassign_codes_wipes_then_reassigns_deterministically():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    before = {
        table: [
            r["code"]
            for r in conn.execute(f"SELECT code FROM {table} ORDER BY id").fetchall()
        ]
        for table in ("agent_pool", "tool_pool", "skill_pool")
    }

    counts = id_scheme.reset_and_reassign_codes(conn)

    assert counts == {"agent_pool": 26, "tool_pool": 12, "skill_pool": 51}
    after = {
        table: [
            r["code"]
            for r in conn.execute(f"SELECT code FROM {table} ORDER BY id").fetchall()
        ]
        for table in ("agent_pool", "tool_pool", "skill_pool")
    }
    # id 오름차순으로 결정적으로 재배정되므로, 삭제된 행이 없으면 결과는
    # 초기화 전과 동일해야 한다("재배정했다"는 사실 자체를 검증).
    assert after == before


# ---------- 신규 생성 시 즉시 코드 배정 ----------


def test_pool_registry_create_tool_gets_code_immediately(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()[
        "id"
    ]
    tool_id = pool_registry.create_tool(
        conn,
        admin_id,
        "임시 커스텀 도구",
        "설명",
        "custom_python",
        {"function_source": "def tool_fn(query: str) -> str:\n    return query"},
    )
    row = conn.execute("SELECT code FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
    assert row["code"] is not None
    assert row["code"].startswith("TUL-PYEXEC-")


def test_pool_registry_create_skill_gets_code_immediately(client_and_conn):
    _client, conn = client_and_conn
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()[
        "id"
    ]
    skill_id = pool_registry.create_skill(
        conn, admin_id, "임시 커스텀 스킬", "설명", augmentation_mode="none"
    )
    row = conn.execute(
        "SELECT code FROM skill_pool WHERE id = ?", (skill_id,)
    ).fetchone()
    assert row["code"] is not None
    assert row["code"].startswith("SKL-NONE-")


def test_seed_demo_data_agent_gets_custom_code(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()[
        "id"
    ]
    result = data_admin.seed_demo_data(conn, admin_id)
    row = conn.execute(
        "SELECT code FROM agent_pool WHERE id = ?", (result["agent_id"],)
    ).fetchone()
    assert row["code"] == "AGT-CUSTOM-001"


# ---------- API 응답에 code 노출 ----------


def test_agent_pool_list_endpoint_exposes_code(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/agent-pool")
    assert resp.status_code == 200, resp.text
    agents = resp.json()
    assert len(agents) == 26
    assert all(a["code"] and a["code"].startswith("AGT-REF-") for a in agents)


def test_tool_pool_and_skill_pool_list_endpoints_expose_code(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    tools = client.get("/api/tool-pool").json()
    skills = client.get("/api/skill-pool").json()
    assert all(t["code"] for t in tools)
    assert all(s["code"] for s in skills)
