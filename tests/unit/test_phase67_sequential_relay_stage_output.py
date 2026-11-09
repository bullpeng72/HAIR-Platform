"""Phase 67 — "구현" 단계 hierarchical(parallel_delegated) → sequential_relay
전환 + 그 과정에서 함께 드러난 sequential_relay의 stage_output 캡처 버그 수정
(실측·외부 조사, 2026-11-07, park_member 로드러너 게임 데모 연장선).

**배경**: CrewAI의 hierarchical(위임) 프로세스는 커뮤니티에 널리 보고된
신뢰성 문제가 있고(매니저가 위임을 안 하거나, 하더라도 코워커가 끝까지
완성 안 함), BMAD-METHOD 공식 가이드 자체도 "순차로 하나씩 완성 후
다음으로"를 기본·권장 패턴으로 삼는다(병렬 위임은 서드파티 애드온). 그래서
"구현" 단계를 이미 "설계"/"QA 기능테스트"에 쓰이던 `sequential_relay`로
전환했다.

전환 과정에서 발견한 두 번째 문제: `_execute_stage_attempt()`는 지금까지
`sequential_relay`든 뭐든 **마지막 참여자의 답변만** `stage_output`으로
캡처했다(`result["agents"][-1]`) — "설계"(AA→DA)도 이미 이 버그를 안고
있었지만 `is_primary_deliverable`이 아니라 아무도 몰랐다. "구현"이
sequential_relay가 되면서(5명이 각자 다른 모듈 작성) 즉시 치명적으로
드러난다 — 참여자 전원의 최종 산출물을 순서대로 이어붙이도록 고친다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import db
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '게임', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


def _run_crew_returning(agents):
    def _fake_run_crew(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        return {
            "final_output": "fallback",
            "agents": agents,
            "gate_scores": {"A": {"score": 0.9, "passed": True}},
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "ollama",
            "resolved_model": "x",
            "token_usage": {"total_tokens": 1},
        }

    return _fake_run_crew


# ---------- stage_output 캡처: sequential_relay 다중 참여자 ----------


def test_sequential_relay_concatenates_all_participant_outputs():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "sequential_relay",
        [
            ("UI 기획자", "화면 흐름"),
            ("백엔드 개발자", "로직"),
            ("프론트엔드 개발자", "렌더링"),
        ],
    )

    agents = [
        {"agent_id": 1, "role_name": "UI 기획자", "name": None, "output": "화면 흐름 설계안"},
        {"agent_id": 2, "role_name": "백엔드 개발자", "name": None, "output": "```js\nfunction logic(){}\n```"},
        {"agent_id": 3, "role_name": "프론트엔드 개발자", "name": None, "output": "```js\nfunction render(){}\n```"},
    ]
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_run_crew_returning(agents)
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage = conn.execute(
        "SELECT output, deliverable_id FROM pipeline_run_stages WHERE pipeline_run_id = ?",
        (run_id,),
    ).fetchone()
    assert "화면 흐름 설계안" in stage["output"]
    assert "function logic" in stage["output"]
    assert "function render" in stage["output"]
    # 순서대로 이어붙었는지(UI → 백엔드 → 프론트엔드) 확인.
    assert stage["output"].index("화면 흐름") < stage["output"].index("logic")
    assert stage["output"].index("logic") < stage["output"].index("render")


def test_single_participant_sequential_relay_output_unchanged():
    """참여자가 1명뿐이면 이어붙일 게 없으니 그 1건 그대로 — 기존 동작과
    100% 동일해야 한다(회귀 가드)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    pr.add_stage(conn, admin_id, tid, "s1", "sequential_relay", [("AA", "설계")])

    agents = [{"agent_id": 1, "role_name": "AA", "name": None, "output": "단독 산출물"}]
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_run_crew_returning(agents)
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage = conn.execute(
        "SELECT output FROM pipeline_run_stages WHERE pipeline_run_id = ?", (run_id,)
    ).fetchone()
    assert stage["output"] == "단독 산출물"


def test_sequential_relay_filters_draft_entries_when_self_review():
    """self_review로 draft+final이 섞여 있으면(마지막 자동 재시도 등) final만
    이어붙이고 draft는 제외해야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "sequential_relay",
        [("백엔드 개발자", "로직"), ("프론트엔드 개발자", "렌더링")],
    )

    agents = [
        {"agent_id": 1, "role_name": "백엔드 개발자", "name": None, "output": "초안-백엔드", "stage": "draft"},
        {"agent_id": 1, "role_name": "백엔드 개발자", "name": None, "output": "최종-백엔드", "stage": "final"},
        {"agent_id": 2, "role_name": "프론트엔드 개발자", "name": None, "output": "초안-프론트", "stage": "draft"},
        {"agent_id": 2, "role_name": "프론트엔드 개발자", "name": None, "output": "최종-프론트", "stage": "final"},
    ]
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_run_crew_returning(agents)
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    stage = conn.execute(
        "SELECT output FROM pipeline_run_stages WHERE pipeline_run_id = ?", (run_id,)
    ).fetchone()
    assert "최종-백엔드" in stage["output"]
    assert "최종-프론트" in stage["output"]
    assert "초안-백엔드" not in stage["output"]
    assert "초안-프론트" not in stage["output"]


# ---------- 시드된 "소프트웨어 개발 파이프라인" 자체 구조 확인 ----------


def test_seeded_implementation_stage_is_sequential_relay_without_lead():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT mode, lead_role_name FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND name = '구현'",
        (template["id"],),
    ).fetchone()
    assert stage["mode"] == "sequential_relay"
    assert stage["lead_role_name"] is None


def test_seeded_planning_stage_instructions_map_requirements_to_modules():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT instructions FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND name = '개발 계획 수립'",
        (template["id"],),
    ).fetchone()
    assert "완료 기준" in stage["instructions"]
    assert "요구사항" in stage["instructions"]


# ---------- 마이그레이션: 기존(옛 hierarchical) DB 현행화 ----------


def _seed_old_hierarchical_implementation_stage(conn):
    """2026-11-06 이전 상태(parallel_delegated + 매니저 "개발 계획
    수립가" + 6명 구체제)를 그대로 흉내낸다 — 이미 돌아가고 있던 기존
    DB가 재기동 시 안전하게 현행화되는지 확인하기 위한 준비."""
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND name = '구현'",
        (template["id"],),
    ).fetchone()
    stage_id = stage["id"]
    conn.execute(
        "UPDATE pipeline_stages SET mode = 'parallel_delegated', "
        "lead_role_name = '개발 계획 수립가', "
        "instructions = '앞 단계의 개발 계획에 따라 모듈을 구현하세요.' "
        "WHERE id = ?",
        (stage_id,),
    )
    conn.execute("DELETE FROM pipeline_stage_agents WHERE stage_id = ?", (stage_id,))
    old_roster = [
        ("개발 계획 수립가", ""),
        ("백엔드 개발자", "백엔드 모듈을 구현하세요."),
        ("프론트엔드 개발자", "프론트엔드 모듈을 구현하세요."),
        ("데이터/인프라 개발자", "데이터/인프라 모듈을 구현하세요."),
        ("풀스택 개발자", "필요한 쪽을 유동적으로 구현하세요."),
        ("UI 기획자", "구현에 필요한 화면 흐름/인터랙션을 정리해 전달하세요."),
    ]
    for seq, (role_name, instructions) in enumerate(old_roster, start=1):
        conn.execute(
            "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
            "instructions) VALUES (?, ?, ?, ?)",
            (stage_id, role_name, seq, instructions),
        )
    conn.commit()
    return stage_id


def test_restart_migrates_old_hierarchical_implementation_stage_to_sequential_relay():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    stage_id = _seed_old_hierarchical_implementation_stage(conn)

    db._sync_dev_pipeline_implementation_stage_agents(conn)

    stage = conn.execute(
        "SELECT mode, lead_role_name, instructions FROM pipeline_stages WHERE id = ?",
        (stage_id,),
    ).fetchone()
    assert stage["mode"] == "sequential_relay"
    assert stage["lead_role_name"] is None
    assert "Delegate work to coworker" not in stage["instructions"]

    roles = [
        r["role_name"]
        for r in conn.execute(
            "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ? "
            "ORDER BY seq_order",
            (stage_id,),
        ).fetchall()
    ]
    assert roles == [
        "UI 기획자",
        "데이터/인프라 개발자",
        "백엔드 개발자",
        "프론트엔드 개발자",
        "풀스택 개발자",
    ]


def test_migration_does_not_touch_admin_customized_implementation_stage_mode():
    """관리자가 이미 mode를 다른 값으로 손봤다면(예: single로 바꿈) 그
    mode/instructions는 건드리지 않아야 한다 — 전체 교체(hierarchical→
    sequential_relay 전환)는 옛 hierarchical 형태일 때만 안전하게
    적용되는 분기이지, 관리자가 이미 다른 값으로 바꾼 stage를 되돌리면
    안 된다. (역할 목록 자체를 "현재 템플릿에 있는데 빠진 역할 추가"는
    이 전체 교체와 무관한, 2026-10-28에 이미 있던 별개의 멱등 동기화라
    이 테스트의 대상이 아니다.)"""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND name = '구현'",
        (template["id"],),
    ).fetchone()
    stage_id = stage["id"]
    custom_instructions = "관리자가 직접 작성한 커스텀 구현 지시문입니다."
    conn.execute(
        "UPDATE pipeline_stages SET mode = 'single', lead_role_name = NULL, "
        "instructions = ? WHERE id = ?",
        (custom_instructions, stage_id),
    )
    conn.commit()

    db._sync_dev_pipeline_implementation_stage_agents(conn)

    stage_after = conn.execute(
        "SELECT mode, instructions FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert stage_after["mode"] == "single"
    assert stage_after["instructions"] == custom_instructions


def test_migration_updates_stale_planning_instructions():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage_id = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND name = '개발 계획 수립'",
        (template["id"],),
    ).fetchone()["id"]
    conn.execute(
        "UPDATE pipeline_stages SET instructions = "
        "'설계 산출물을 독립적으로 개발 가능한 모듈 단위로 쪼개고 선행조건·완료기준을 정하세요.' "
        "WHERE id = ?",
        (stage_id,),
    )
    conn.commit()

    db._sync_dev_pipeline_implementation_stage_agents(conn)

    stage = conn.execute(
        "SELECT instructions FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert "요구사항" in stage["instructions"]
    assert "완료 기준" in stage["instructions"]


def test_migration_preserves_custom_planning_instructions():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage_id = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND name = '개발 계획 수립'",
        (template["id"],),
    ).fetchone()["id"]
    custom_text = "관리자가 직접 작성한 커스텀 지시문입니다."
    conn.execute(
        "UPDATE pipeline_stages SET instructions = ? WHERE id = ?",
        (custom_text, stage_id),
    )
    conn.commit()

    db._sync_dev_pipeline_implementation_stage_agents(conn)

    stage = conn.execute(
        "SELECT instructions FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    assert stage["instructions"] == custom_text


def test_migration_is_idempotent_on_already_migrated_db():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db._sync_dev_pipeline_implementation_stage_agents(conn)  # 두 번째 호출
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT mode, lead_role_name FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND name = '구현'",
        (template["id"],),
    ).fetchone()
    assert stage["mode"] == "sequential_relay"
    assert stage["lead_role_name"] is None


def test_code_review_stage_has_requirements_traceability_flag_seeded():
    """2026-11-08 — 단계명과 무관한 `checks_requirements_traceability`
    플래그로 일반화한 뒤에도, 기본 시드 "코드 검증" 단계는 그대로
    True여야 한다(기존 동작 보존)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = '소프트웨어 개발 파이프라인'"
    ).fetchone()
    stage = conn.execute(
        "SELECT checks_requirements_traceability FROM pipeline_stages "
        "WHERE pipeline_template_id = ? AND name = '코드 검증'",
        (template["id"],),
    ).fetchone()
    assert bool(stage["checks_requirements_traceability"]) is True

    other_stages = conn.execute(
        "SELECT name, checks_requirements_traceability FROM pipeline_stages "
        "WHERE pipeline_template_id = ? AND name != '코드 검증'",
        (template["id"],),
    ).fetchall()
    assert all(not row["checks_requirements_traceability"] for row in other_stages)
