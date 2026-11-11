"""Phase 60 — Crew 빌더 Agent 자기검토(self_review, 사용자 요청,
2026-11-04, "업무 배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너
수준의 게임... 개선 계획을 수립해줘" → "2번(Agent 자가비평)...
설계 들어가줘").

실시간 데모(park_member 계정, 로드러너 게임 웹 구현)에서 Crew 재시도를
거듭할 때마다 새 기능(중력/사다리)을 추가하면서 이전에 되던 기능(금
수집/점수)이 조용히 퇴행하는 "두더지 잡기" 문제를 3라운드 수동 피드백
으로 직접 겪었다 — `self_review=True`면 각 worker의 초안 뒤에 같은
Agent의 검토 Task를 추가해, Agent 스스로 원 요구사항과 대조해 고치되
기존에 되던 기능은 보존하도록 유도한다.

**비용/네트워크 발생 방지**: `test_phase24_team_crew_bridge.py`와 같은
`patch("crewai.Crew.kickoff", new=...)` 패턴(진짜 LLM 호출 없음)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def _make_task(client, role_name="로드러너 게임 웹 구현"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def _make_member_task_participant(conn, task_id, user_id, agent_id):
    """`task_participant_relationship()`의 "requester"(Agent 배정 요청자)
    경로로 참가자 자격을 준다 — member가 API를 통해 직접 Agent 배정을
    요청할 순 없지만(본인 human_pool만 가능), 여기선 그 RBAC와 무관한
    "이미 확정된 배정이 있는 상태"를 테스트 셋업으로 직접 만든다."""
    conn.execute(
        "INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
        "VALUES (?, 'agent', ?, ?, 'confirmed')",
        (task_id, agent_id, user_id),
    )
    conn.commit()


# ---------- run_crew() — Task 생성/체이닝 단위 테스트 ----------


def test_run_crew_self_review_inserts_one_extra_review_task_per_worker(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["count"] = len(self.tasks)
        captured["contexts"] = [t.context for t in self.tasks]
        captured["expected_outputs"] = [t.expected_output for t in self.tasks]
        return _crew_output("최종", ["draft0", "review0", "draft1", "review1"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["벽돌깨기 게임을 만드세요", "로드러너 게임을 만드세요"],
            admin_id,
            process="sequential",
            self_review=True,
        )
    assert captured["count"] == 4  # worker 2명 x (초안+검토)
    # review0(인덱스 1)은 draft0(인덱스 0)을 context로 받는다.
    assert captured["contexts"][1] is not None and len(captured["contexts"][1]) == 1
    assert captured["expected_outputs"][1] == "모든 요구사항이 반영되고 기존 동작은 보존된 최종본"
    assert captured["expected_outputs"][3] == "모든 요구사항이 반영되고 기존 동작은 보존된 최종본"


def test_run_crew_self_review_review_task_description_includes_preservation_instruction(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["descriptions"] = [t.description for t in self.tasks]
        return _crew_output("최종", ["draft0", "review0"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1], ["로드러너 게임을 만드세요"], admin_id,
            process="sequential", self_review=True,
        )
    review_desc = captured["descriptions"][1]
    assert "로드러너 게임을 만드세요" in review_desc
    assert "보존" in review_desc


def test_run_crew_self_review_chains_next_worker_from_reviewed_output_not_draft(
    client_and_conn,
):
    """회귀 테스트 — `context = tasks[i-1]` → `tasks[-1]` 수정 확인.
    worker 2의 초안은 worker 1의 "검토 후 최종본"(review1)에서 이어받아야
    한다(worker 1의 초안이 아니라)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")
    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["tasks"] = list(self.tasks)
        return _crew_output("최종", ["draft0", "review0", "draft1", "review1"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["작업1", "작업2"], admin_id,
            process="sequential", self_review=True,
        )
    tasks = captured["tasks"]
    _draft0, review0, draft1, _review1 = tasks
    assert draft1.context == [review0]  # review0(최종본)에서 이어받음 — draft0 아님


def test_run_crew_self_review_result_agents_attribution_is_correct(client_and_conn):
    """회귀 테스트 — `zip(worker_rows, result.tasks_output, strict=False)`
    버그 수정 확인. self_review로 Task가 4개(draft0/review0/draft1/review1)
    인데 worker_rows는 agent 2개뿐이라, 고치기 전엔 뒤 worker의 결과가
    누락되거나 엉뚱한 agent_id에 귀속됐다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output(
            "최종", ["agent1-draft", "agent1-review", "agent2-draft", "agent2-review"]
        )

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        result = crew_runtime.run_crew(
            conn, [agent1, agent2], ["작업1", "작업2"], admin_id,
            process="sequential", self_review=True,
        )
    assert len(result["agents"]) == 4
    a1_draft, a1_review, a2_draft, a2_review = result["agents"]
    assert a1_draft["agent_id"] == agent1
    assert a1_draft["output"] == "agent1-draft"
    assert a1_draft["stage"] == "draft"
    assert a1_review["agent_id"] == agent1
    assert a1_review["output"] == "agent1-review"
    assert a1_review["stage"] == "final"
    assert a2_draft["agent_id"] == agent2
    assert a2_draft["output"] == "agent2-draft"
    assert a2_draft["stage"] == "draft"
    assert a2_review["agent_id"] == agent2
    assert a2_review["output"] == "agent2-review"
    assert a2_review["stage"] == "final"


def test_run_crew_self_review_without_self_review_has_all_final_stage(client_and_conn):
    """회귀 가드 — self_review=False(기본값)면 모든 항목이 항상
    "final"이어야 한다(기존 소비자와 하위호환)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output("최종", ["x", "y"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        result = crew_runtime.run_crew(
            conn, [agent1, agent2], ["작업1", "작업2"], admin_id, process="sequential"
        )
    assert len(result["agents"]) == 2
    assert all(a["stage"] == "final" for a in result["agents"])


def test_run_crew_self_review_rejected_when_process_hierarchical(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    with pytest.raises(ValueError, match="sequential"):
        crew_runtime.run_crew(
            conn, [agent1], ["작업1"], admin_id,
            process="hierarchical", self_review=True,
        )


def test_run_crew_self_review_rejected_when_parallel_agent_ids_nonempty(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    from hair_platform import crew_runtime

    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")
    with pytest.raises(ValueError, match="sequential"):
        crew_runtime.run_crew(
            conn, [agent1, agent2], ["작업1", "작업2"], admin_id,
            process="sequential", self_review=True, parallel_agent_ids=[agent1],
        )


# ---------- API 엔드포인트 ----------


def test_execute_adhoc_crew_endpoint_accepts_self_review_from_member(client_and_conn):
    client, conn = client_and_conn
    member_id = conn.execute(
        "SELECT id FROM users WHERE username='member1'"
    ).fetchone()["id"]
    _login(client, "admin1", "adminpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "백엔드 개발자")
    _make_member_task_participant(conn, task_id, member_id, agent_id)

    _login(client, "member1", "memberpass")
    resp = client.post(
        f"/api/tasks/{task_id}/execute-adhoc-crew",
        json={
            "agent_ids": [agent_id],
            "task_descriptions": ["작업을 하세요"],
            "process": "sequential",
            "self_review": True,
        },
    )
    assert resp.status_code == 202, resp.text


def test_execute_adhoc_crew_endpoint_rejects_self_review_with_hierarchical_process(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    task_id = _make_task(client)
    agent_id = _make_active_agent(conn, "백엔드 개발자")
    resp = client.post(
        f"/api/tasks/{task_id}/execute-adhoc-crew",
        json={
            "agent_ids": [agent_id],
            "task_descriptions": ["작업을 하세요"],
            "process": "hierarchical",
            "manager_agent_id": agent_id,
            "self_review": True,
        },
    )
    assert resp.status_code == 400


# ---------- hierarchical_with_manager — job 생성 버그 수정(사용자
# 요청, 2026-11-04, "지금 바로 고쳐줘" — 파이프라인 "구현" 단계 합성
# 로직 수정 작업 중 라이브 스모크 테스트로 재현한 별개 버그) ----------


def test_start_adhoc_crew_execution_creates_job_for_manager_not_workers(
    client_and_conn,
):
    """`manager_agent_id`가 있으면 결과물을 내는 주체가 매니저 1명뿐
    이므로(`crew_runtime.run_crew()`의 hierarchical_with_manager 분기),
    job도 매니저 앞으로 1개만 생겨야 한다 — 고치기 전엔 반대로 코워커
    들에게만 job이 생겨 결과물이 하나도 안 만들어졌다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    task_id = _make_task(client)
    manager = _make_active_agent(conn, "개발 계획 수립가")
    worker1 = _make_active_agent(conn, "백엔드 개발자")
    worker2 = _make_active_agent(conn, "프론트엔드 개발자")

    from hair_platform import deliverables

    job = deliverables.start_adhoc_crew_execution(
        conn,
        task_id,
        admin_id,
        [manager, worker1, worker2],
        manager_agent_id=manager,
        allow_supervisor_override=True,
    )
    assert job["job_by_agent_id"] == {manager: job["job_by_agent_id"][manager]}
    assert worker1 not in job["job_by_agent_id"]
    assert worker2 not in job["job_by_agent_id"]


def test_run_adhoc_crew_job_hierarchical_with_manager_creates_exactly_one_deliverable(
    client_and_conn,
):
    """종단 테스트 — 수정 전엔 이 조합(hierarchical + manager_agent_id)
    에서 결과물이 0건 생겼다(라이브로 재현). 수정 후엔 매니저의
    (합성된) 결과물이 정확히 1건 생겨야 한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    task_id = _make_task(client)
    manager = _make_active_agent(conn, "개발 계획 수립가")
    worker1 = _make_active_agent(conn, "백엔드 개발자")

    from hair_platform import deliverables

    job = deliverables.start_adhoc_crew_execution(
        conn,
        task_id,
        admin_id,
        [manager, worker1],
        manager_agent_id=manager,
        allow_supervisor_override=True,
    )

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output("매니저가 통합한 최종 산출물", ["매니저가 통합한 최종 산출물"])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        deliverables.run_adhoc_crew_job(
            conn,
            job["job_by_agent_id"],
            task_id,
            admin_id,
            [manager, worker1],
            ["위임하세요", "작업하세요"],
            "hierarchical",
            None,
            None,
            manager,
        )

    rows = conn.execute(
        "SELECT author_id, content FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["author_id"] == manager
    assert rows[0]["content"] == "매니저가 통합한 최종 산출물"


# ---------- run_adhoc_crew_job() 종단 테스트 ----------


def test_run_adhoc_crew_job_creates_only_one_deliverable_per_agent_when_self_review(
    client_and_conn,
):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='admin1'"
    ).fetchone()["id"]
    task_id = _make_task(client)
    agent1 = _make_active_agent(conn, "백엔드 개발자")
    agent2 = _make_active_agent(conn, "프론트엔드 개발자")

    from hair_platform import crew_history, deliverables

    job = deliverables.start_adhoc_crew_execution(
        conn, task_id, admin_id, [agent1, agent2], allow_supervisor_override=True
    )

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output(
            "최종", ["agent1-draft", "agent1-review", "agent2-draft", "agent2-review"]
        )

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        deliverables.run_adhoc_crew_job(
            conn,
            job["job_by_agent_id"],
            task_id,
            admin_id,
            [agent1, agent2],
            ["작업1", "작업2"],
            "sequential",
            None,
            None,
            None,
            self_review=True,
        )

    rows = conn.execute(
        "SELECT author_id, content FROM deliverables WHERE task_id = ? ORDER BY author_id",
        (task_id,),
    ).fetchall()
    assert len(rows) == 2  # 초안 아닌 최종본만, agent당 1개
    by_agent = {r["author_id"]: r["content"] for r in rows}
    assert by_agent[agent1] == "agent1-review"
    assert by_agent[agent2] == "agent2-review"

    # 초안은 결재 큐(deliverables)엔 안 올라가지만 실행 이력 자체에는
    # 여전히 남아있어야 한다(투명성 유지) — `crew_history.complete_run()`
    # 이 `result` 전체(초안 포함)를 저장한다. `on_task_complete`로
    # 실시간 집계되는 `progress` 로그는 CrewAI의 실제 task_callback
    # 메커니즘이 호출해야 쌓이는데, 이 테스트는 `Crew.kickoff` 자체를
    # 완전히 모킹해(진짜 LLM 호출 없음) 그 내부 콜백 루프가 안 도므로
    # 여기서는 검증 대상이 아니다 — `agent_outputs`(저장된 최종 result)
    # 쪽으로 투명성을 확인한다.
    run = crew_history.list_runs_for_task(conn, task_id)[0]
    full = crew_history.get_run(conn, run["id"])
    stages = [a["stage"] for a in full["agents"]]
    assert stages == ["draft", "final", "draft", "final"]
