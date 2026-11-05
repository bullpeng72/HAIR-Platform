"""Phase 52 — "자율 개발 크루"를 파이프라인 기반 실행으로 재구성(사용자
요청, 2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우 파이프라인을
참조해 실제 개발 품질을 보장하며 수행될 수 있도록 재구성해줘").

**배경**: "@자율 개발 크루"로 만든 Breakout 게임이 "Gate 1/5 통과"에도
동작하지 않는 코드였다 — 원인은 (1) 전 멤버가 약한 로컬 모델, (2) Gate가
코드 실행 검증 없는 휴리스틱, (3) 위임 결과 병합이 매니저 LLM 혼자만의
자유 서술. 이미 있던 "소프트웨어 개발 파이프라인"(코드 검증/QA/품질
관리 전용 단계 + Gate 기준 자동 재시도/승인 에스컬레이션 엔진)에 이
팀을 연결해(`teams.pipeline_template_id`), `@자율 개발 크루` 멘션이
단발 실행 대신 그 엔진을 타게 한다.

**비용/네트워크 발생 방지**(test_phase48/test_phase31과 같은 원칙):
`crew_runtime.run_crew` 그 자체만 patch한다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import reference_pipeline_seeds


def _login(client, username="admin1", password="adminpass"):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _good_run_crew(gate_scores=None, tcr=90.0):
    """`pipeline_runtime._execute_stage_attempt()`가 쓰는 호출 모양에
    맞춘 side_effect — test_phase48의 `_good_run_crew()`와 동일 패턴."""
    gate_scores = gate_scores or {
        "A": {"score": 0.9, "passed": True},
        "B": {"not_measured": True},
    }

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
        return {
            "final_output": "단계 산출물",
            "agents": [
                {
                    "agent_id": aid,
                    "role_name": "role",
                    "name": None,
                    "output": f"output-{aid}",
                }
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": tcr,
            "delegations": [],
            "resolved_provider": "anthropic",
            "resolved_model": "claude-sonnet-5",
        }

    return _fn


def _dev_crew_team_id(conn):
    """2026-11-02 — 사용자 요청("기존 대화, 업무 요청, 팀 관리 내용을
    모두 초기화해줘" — "자율 개발 크루"도 포함)으로 `reference_
    pipeline_seeds.seed()`가 이 팀을 더는 자동 생성하지 않는다. 이
    파일의 테스트들은 "파이프라인에 연결된 팀"의 동작을 확인하는 게
    목적이라, 없으면 옛 시드와 같은 모양으로 직접 만든다."""
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


def test_gc_dev_crew_mention_without_linked_task_creates_synthetic_task_and_pauses_for_approval(
    client_and_conn,
):
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)
    tasks_before = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "게임 개발 방"}).json()[
        "id"
    ]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    assert resp.status_code == 201, resp.text

    # 사용자 보고(2026-10-29, "브레이크 아웃 게임 생성 대화에서...
    # 생성된 코드가 제시안된 원인") 재현/수정 확인 — 1단계 프롬프트가
    # 팀 이름("자율 개발 크루")이 아니라 실제 대화 내용을 받아야 한다.
    # `crew_runtime.run_crew`에 실제로 넘어가는 task_description을
    # 추적해 확인한다.
    captured_descriptions = []

    def _tracking_run_crew(
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
        captured_descriptions.append(task_descriptions[0])
        return _good_run_crew()(
            conn,
            agent_ids,
            task_descriptions,
            actor_id,
            process,
            provider,
            model,
            manager_agent_id,
            expected_outputs,
            self_review,
        )

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_tracking_run_crew):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 벽돌깨기 게임을 만들어줘"},
        )
        assert resp.status_code == 201, resp.text

    assert captured_descriptions
    assert "벽돌깨기 게임을 만들어줘" in captured_descriptions[0]

    # 새 task가 하나 생겼어야 한다(synthetic) — Task Board 구분을 위해
    # notes에 대화 출처가 남는다. role_name은 더 이상 팀 이름이 아니라
    # 대화 제목이어야 한다(팀 이름이 그대로 1단계 프롬프트에 박혀
    # 실제 요청이 안 들어가던 버그 수정).
    tasks_after = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    assert tasks_after == tasks_before + 1
    new_task = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
    assert "대화" in new_task["notes"]
    assert "자율 개발 크루" in new_task["notes"]
    assert new_task["role_name"] == "게임 개발 방"

    # 대화 전사록이 "사람이 작성한 deliverable"로 심어져 있어야
    # `_stage_initial_input()`의 기존 폴백이 집어간다.
    seed_deliverable = conn.execute(
        "SELECT * FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY id DESC LIMIT 1",
        (new_task["id"],),
    ).fetchone()
    assert seed_deliverable is not None
    assert "벽돌깨기 게임을 만들어줘" in seed_deliverable["content"]

    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE task_id = ?", (new_task["id"],)
    ).fetchone()
    assert run is not None
    assert run["source_conversation_id"] == conv_id
    # 1단계("요구분석/계획 확정")는 설계상 항상 사람 승인 대기.
    assert run["status"] == "paused_for_approval"

    # 멘션 Job은 승인 대기 상태에서도 바로 마감돼야 한다(무기한 폴링
    # 방지) — 폴링 엔드포인트로 확인.
    jobs = client.get(f"/api/conversations/{conv_id}/jobs").json()
    assert jobs and all(j["status"] == "succeeded" for j in jobs)

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert len(agent_messages) == 1
    assert "승인" in agent_messages[0]["content"]


def test_gc_dev_crew_mention_with_linked_task_reuses_it(client_and_conn):
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)

    _login(client, "member1", "memberpass")
    brief_id = client.post("/api/briefs", json={"raw_text": "벽돌깨기"}).json()["id"]
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "자율 개발 크루", "required_skills": []}]},
    )
    assert resp.status_code == 201, resp.text
    linked_task_id = resp.json()["tasks"][0]["id"]
    tasks_before = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]

    # `POST /api/conversations`의 `linked_task_id`는 호출자가 그 Task의
    # confirmed 배정 참가자여야 한다(`task_participant_relationship()`)
    # — 이 테스트의 관심사는 그 권한 체크가 아니라 "linked_task_id가
    # 있으면 synthetic task를 또 만들지 않는지"이므로, 그 전제 조건
    # 설정은 직접 INSERT로 간단히 둔다(다른 테스트 파일들도 이 패턴을
    # 쓴다 — 예: test_phase48의 `_make_task()`).
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    conv_cur = conn.execute(
        "INSERT INTO conversations (created_by, title, linked_task_id) VALUES (?, ?, ?)",
        (member1_id, "업무 전용 방", linked_task_id),
    )
    conv_id = conv_cur.lastrowid
    conn.execute(
        "INSERT INTO conversation_participants (conversation_id, participant_type, "
        "participant_id, invited_by) VALUES (?, 'human', ?, ?)",
        (conv_id, member1_id, member1_id),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    assert resp.status_code == 201, resp.text

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 진행해줘"},
        )
        assert resp.status_code == 201, resp.text

    # linked_task_id가 있으므로 synthetic task를 새로 만들지 않아야 한다.
    tasks_after = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    assert tasks_after == tasks_before

    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE task_id = ?", (linked_task_id,)
    ).fetchone()
    assert run is not None
    assert run["source_conversation_id"] == conv_id


def test_gc_dev_crew_approval_resume_posts_followup_message_to_conversation(
    client_and_conn,
):
    """1단계 승인 → 나머지 단계(2~7단계, 전부 승인 불필요)가 자동으로
    이어서 진행되고, 그 결과가 같은 대화에 단계별로 매번 진행
    메시지로 올라오는지 확인한다(`app.py`의 `decide_approval()` →
    `conversations.run_pipeline_stage_job_and_notify()` 경로).

    사용자 보고(2026-10-28, "결재 승인 되었으나, 대화창의 경우 이후
    진행 확인되지 않음") 수정 확인 — 예전엔 전체가 다 끝난 뒤 메시지
    하나만 올라와서, 승인 불필요 단계가 연달아 넘어가는 긴 시간 동안
    대화창에 아무 신호가 없었다. 이제 `pipeline_runtime.run_pipeline_
    stage_job()`의 `on_stage_resolved` 콜백으로 단계가 resolve될
    때마다(이 테스트는 전부 통과하는 mock이라 2~7단계가 한 번의
    승인으로 전부 자동 완주한다) 매번 메시지가 올라와야 한다."""
    client, conn = client_and_conn
    team_id = _dev_crew_team_id(conn)

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "승인 흐름 방"}).json()[
        "id"
    ]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@자율 개발 크루 진행해줘"},
        )

    messages_before = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len([m for m in messages_before if m["author_type"] == "agent"]) == 1

    # admin1이 결재자로 승인한다(요청자는 member1이라 self-approval
    # 제약에 안 걸림).
    _login(client, "admin1", "adminpass")
    approval = conn.execute(
        "SELECT id FROM approvals WHERE subject_type = 'pipeline_run_stage' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert approval is not None

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/approvals/{approval['id']}/decide", json={"approve": True}
        )
        assert resp.status_code == 200, resp.text

    _login(client, "member1", "memberpass")
    messages_after = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages_after = [m for m in messages_after if m["author_type"] == "agent"]
    # 1단계 승인 전 1건(요구분석/계획 확정 통과 + 승인 대기) +
    # 2~7단계가 각각 resolve될 때마다 1건씩(전부 자동 통과하는 mock
    # 이라 승인 한 번으로 끝까지 완주) — 최소 2건(진짜로 더 올라옴)
    # 이상이어야 "끝날 때 한 번만"이 아니라 "단계마다"임을 증명한다.
    assert len(agent_messages_after) > 2
    assert "완료" in agent_messages_after[-1]["content"]
    # 사용자 요청(2026-10-28, "승인자가 누구인지 표시되도록 개선") —
    # 1단계를 승인한 admin1의 이름이 진행 메시지에 나와야 한다.
    assert "결재: admin1" in agent_messages_after[-1]["content"]


def test_gc_team_without_pipeline_link_still_uses_legacy_single_shot_crew(
    client_and_conn,
):
    """파이프라인에 연결 안 된(`pipeline_template_id IS NULL`) 일반
    팀은 기존처럼 `crew_runtime.run_crew()`를 한 번만 호출해 워커별
    메시지를 바로 올려야 한다 — 이번 재구성이 다른 팀에 영향을 주지
    않는지 확인하는 회귀 테스트."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("일반워커",),
    )
    agent_id = cur.lastrowid
    cur = conn.execute("INSERT INTO teams (name) VALUES ('일반팀')")
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id) VALUES (?, 'agent', ?)",
        (team_id, agent_id),
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT pipeline_template_id FROM teams WHERE id = ?", (team_id,)
        ).fetchone()["pipeline_template_id"]
        is None
    )

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "일반 방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    crew_result = {
        "agents": [{"agent_id": agent_id, "role_name": "일반워커", "output": "답변"}],
        "token_usage": {},
        "delegation_count": 0,
    }
    with patch(
        "hair_platform.crew_runtime.run_crew", return_value=crew_result
    ) as mock_crew:
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@일반팀 검토해줘"},
        )
        assert resp.status_code == 201, resp.text
        mock_crew.assert_called_once()

    assert (
        conn.execute(
            "SELECT COUNT(*) AS c FROM pipeline_runs WHERE source_conversation_id = ?",
            (conv_id,),
        ).fetchone()["c"]
        == 0
    )
    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "답변"
