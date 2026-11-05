"""Phase 63 — 자동 단계적 에스컬레이션(사용자 요청, 2026-11-05, "업무
배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너 수준의 게임...
개선 계획을 수립해줘" → 로드맵 6번 "자동 단계적 에스컬레이션...
다음 개선 진행해줘").

기존 파이프라인 에스컬레이션은 완전히 이진(재시도 N번 → 바로 사람)
이었다. 이 파일은 두 가지를 검증한다:
1. 사람에게 올라가기 직전(마지막 자동 시도)에 로드맵 2번 "Agent
   자가비평"(self_review)이 자동으로 켜지는지 — "일반 재시도"와
   "사람 에스컬레이션" 사이의 새 중간 단계.
2. 설계 조사 중 발견한 별개 버그 — 에스컬레이션 시 승인자에게 실제로
   알림(@멘션)이 가는지(기존엔 `@` 접두사가 없고, 승인자가 대화
   참가자도 아니어서 전혀 전달 안 됐다).

**비용/네트워크 발생 방지**: `test_phase48_pipeline_runtime.py`/
`test_phase52_dev_crew_pipeline_integration.py`와 같은 원칙 —
`crew_runtime.run_crew` 그 자체만 patch한다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations, db, reference_pipeline_seeds
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user


def _login(client, username="admin1", password="adminpass"):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task(conn):
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (1, 'x', '[]')"
    )
    brief_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '테스트', '[]')",
        (brief_id,),
    )
    conn.commit()
    return cur.lastrowid


def _bad_run_crew_tracking(calls):
    """항상 Gate A가 fail인 결과를 내면서, 각 호출의 `self_review`
    인자와 `task_descriptions[0]`을 `calls`에 기록한다."""
    bad_gate = {"A": {"score": 0.1, "passed": False}}

    def _fn(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process="sequential",
        provider=None,
        model=None,
        manager_agent_id=None,
        expected_outputs=None,
        self_review=False,
    ):
        calls.append({"self_review": self_review, "description": task_descriptions[0]})
        return {
            "final_output": "산출물",
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": f"output-{aid}",
                }
                for aid in agent_ids
            ],
            "gate_scores": bad_gate,
            "tcr": 10.0,
            "delegations": [],
            "resolved_provider": "openai",
            "resolved_model": "gpt-4o-mini",
            "token_usage": {"total_tokens": 1},
        }

    return _fn


# ---------- pipeline_runtime.py — 단계적 에스컬레이션 티어 ----------


def test_final_retry_attempt_uses_self_review_for_single_mode():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "코드검증",
        "single",
        [("코드 검증가", "")],
        max_retry=2,
        min_gate_pass_ratio=0.9,
    )

    calls: list[dict] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 2  # max_retry=2
    assert calls[0]["self_review"] is False  # 1차 시도 — 일반 재시도
    assert calls[1]["self_review"] is True  # 2차(마지막) 시도 — self_review 켜짐
    # 마지막 시도 지시문엔 이전 Gate 실패 사유 + "마지막 자동 재시도" 경고가 공존.
    assert "이전 시도" in calls[1]["description"]
    assert "마지막 자동 재시도" in calls[1]["description"]

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert (
        run["status"] == "paused_for_approval"
    )  # self_review를 써도 여전히 fail하면 정상 에스컬레이션


def test_final_retry_attempt_uses_self_review_for_sequential_relay_mode():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "QA",
        "sequential_relay",
        [("테스트 계획 수립가", ""), ("QA 기능테스터", "")],
        max_retry=2,
        min_gate_pass_ratio=0.9,
    )

    calls: list[dict] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 2
    assert calls[0]["self_review"] is False
    assert calls[1]["self_review"] is True


def test_parallel_delegated_mode_never_uses_self_review():
    """구조적 비호환 가드 — hierarchical(parallel_delegated)은 self_review와
    함께 쓰면 `crew_runtime.run_crew()`가 ValueError를 던지므로, 마지막
    시도여도 self_review=False로 고정돼야 한다(회귀 방지)."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "parallel_delegated",
        [("개발 계획 수립가", ""), ("백엔드 개발자", "")],
        lead_role_name="개발 계획 수립가",
        max_retry=2,
        min_gate_pass_ratio=0.9,
    )

    calls: list[dict] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 2
    assert calls[0]["self_review"] is False
    assert calls[1]["self_review"] is False  # parallel_delegated라 끝까지 False

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    assert (
        run["status"] == "paused_for_approval"
    )  # 정상적으로 에스컬레이션(크래시 없음)


def test_non_final_retry_does_not_use_self_review():
    """`max_retry=3`이면 1·2차는 일반 재시도, 3차(마지막)만 self_review."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "", "x")
    pr.add_stage(
        conn,
        admin_id,
        tid,
        "코드검증",
        "single",
        [("코드 검증가", "")],
        max_retry=3,
        min_gate_pass_ratio=0.9,
    )

    calls: list[dict] = []
    with patch(
        "hair_platform.crew_runtime.run_crew",
        side_effect=_bad_run_crew_tracking(calls),
    ):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert len(calls) == 3
    assert [c["self_review"] for c in calls] == [False, False, True]


# ---------- conversations.py — 승인자 실제 알림 ----------


def _dev_crew_team_id(conn):
    """`test_phase52_dev_crew_pipeline_integration.py`의 동명 헬퍼와
    같은 패턴 — `reference_pipeline_seeds.seed()`가 이 팀을 자동
    생성하지 않으므로 없으면 직접 만든다(멤버 포함 — `post_pipeline_
    run_status_message()`가 메시지를 붙일 Agent를 찾으려면 필요)."""
    row = conn.execute(
        "SELECT id FROM teams WHERE name = ?",
        (reference_pipeline_seeds.DEV_CREW_TEAM_NAME,),
    ).fetchone()
    if row is not None:
        return row["id"]

    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    assert template is not None
    cur = conn.execute(
        "INSERT INTO teams (name, description, is_active, status, pipeline_template_id) "
        "VALUES (?, ?, 1, 'active', ?)",
        (
            reference_pipeline_seeds.DEV_CREW_TEAM_NAME,
            reference_pipeline_seeds.DEV_CREW_TEAM_DESCRIPTION,
            template["id"],
        ),
    )
    team_id = cur.lastrowid
    for role_name, role_label in reference_pipeline_seeds.DEV_CREW_TEAM_MEMBER_SPECS:
        agent_row = conn.execute(
            "SELECT id FROM agent_pool WHERE role_name = ?", (role_name,)
        ).fetchone()
        if agent_row is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO team_members "
            "(team_id, member_type, member_id, role_label) "
            "VALUES (?, 'agent', ?, ?)",
            (team_id, agent_row["id"], role_label),
        )
    conn.commit()
    return team_id


def _good_run_crew_simple(conn, agent_ids, task_descriptions, actor_id, **kwargs):
    return {
        "final_output": "산출물",
        "agents": [
            {"agent_id": aid, "role_name": "role", "name": None, "output": "o"}
            for aid in agent_ids
        ],
        "gate_scores": {"A": {"score": 0.9, "passed": True}},
        "tcr": 90.0,
        "delegations": [],
        "resolved_provider": "openai",
        "resolved_model": "gpt-4o-mini",
        "token_usage": {"total_tokens": 1},
    }


def test_pipeline_escalation_invites_approver_and_mentions_them(client_and_conn):
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)

    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    # member1의 조직도 상사를 manager1으로 설정 — `build_manager_chain()`
    # 이 실제로 resolve할 대상이 있어야 이 수정의 핵심 경로가 걸린다.
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?", (manager1_id, member1_id)
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "게임 개발 방"}).json()[
        "id"
    ]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    # 에스컬레이션 전용 테스트가 아니라 "사람 승인이 필요합니다" 메시지
    # 자체를 검증하는 것이므로, 1단계(요구분석/계획 확정, 항상 사람
    # 승인 필요)가 그 메시지를 내는 가장 간단하고 확실한 경로다.
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew_simple
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 벽돌깨기 게임을 만들어줘"},
        )
        assert resp.status_code == 201, resp.text

    # manager1이 이 대화의 참가자로 자동 추가됐는지.
    assert conversations.is_participant(conn, conv_id, manager1_id)

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert any("@manager1" in m["content"] for m in agent_messages)

    # 실제로 "안 읽은 멘션"으로 집히는지(이 수정의 실질적 목적).
    mentions = conversations.list_my_recent_mentions(conn, manager1_id)
    assert any(m["conversation_id"] == conv_id for m in mentions)


def test_pipeline_escalation_idempotent_when_approver_already_participant(
    client_and_conn,
):
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)

    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE users SET manager_id = ? WHERE id = ?", (manager1_id, member1_id)
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "게임 개발 방"}).json()[
        "id"
    ]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    # manager1을 미리 참가자로 넣어둔다(멱등성 확인용).
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": manager1_id},
    )

    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew_simple
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 벽돌깨기 게임을 만들어줘"},
        )
        assert resp.status_code == 201, resp.text  # IntegrityError로 500 안 남

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert any("@manager1" in m["content"] for m in agent_messages)


def test_pipeline_escalation_without_manager_chain_skips_invite(client_and_conn):
    """member1에게 상사가 없으면(기본 상태) `approver_id`가 None —
    초대 시도 자체가 없고 기존 "admin/manager 누구나" 문구 그대로."""
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "게임 개발 방"}).json()[
        "id"
    ]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    participants_before = len(conversations.list_participants(conn, conv_id))

    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew_simple
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 벽돌깨기 게임을 만들어줘"},
        )
        assert resp.status_code == 201, resp.text

    assert len(conversations.list_participants(conn, conv_id)) == participants_before
    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert any("admin/manager 누구나" in m["content"] for m in agent_messages)
