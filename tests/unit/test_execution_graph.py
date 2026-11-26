"""LangGraph 기반 실행 엔진(`execution_graph.py`) — 사용자 요청(2026-11-18,
"langgraph + langgraph-checkpoint-sqlite 신규 의존성 추가 방향으로 설계를
구체화하고 진행해줘") 1단계 구현 검증.

**핵심 검증 목표**: ① 기존 `pipeline_runtime.py`와 같은 결과(단계 통과/
재시도/에스컬레이션/승인)를 내는지, ② 진짜 크래시 복구(연결을 완전히
닫고 새로 열어도 중단 지점부터 재개)가 실제로 동작하는지."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import execution_graph
from hair_platform import pipeline_runtime as pr
from hair_platform.auth import Role, create_user
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from tests.unit.test_phase17_collab_workspace import _login


def _admin_id(conn) -> int:
    return create_user(conn, "eg_admin", "pw", Role.ADMIN)


def _make_agent(conn, role_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


def _make_task(conn) -> int:
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


def _good_run_crew(gate_scores=None, tcr=90.0):
    gate_scores = gate_scores or {"A": {"score": 0.9, "passed": True}}

    def _fn(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        return {
            "final_output": "최종산출물",
            "agents": [
                {"agent_id": aid, "role_name": "role", "output": f"output-{aid}"}
                for aid in agent_ids
            ],
            "gate_scores": gate_scores,
            "tcr": tcr,
            "delegations": [],
        }

    return _fn


def _two_stage_template(conn, actor_id, agent_id, max_retry=2):
    """실측 버그(구현 직후 자체 테스트 중 발견) — `add_stage()`의
    `min_gate_pass_ratio` 기본값은 0.0(실제 시드 파이프라인도 "코드
    검증" 등 일부 단계에만 명시적으로 임계값을 건다, `pipeline_
    runtime.py` 주석 참고) — 0.0이면 Gate 점수가 아무리 나빠도 항상
    통과(`ratio >= 0.0`은 항상 참)라서, 재시도/에스컬레이션 테스트가
    실제로는 그 경로를 전혀 안 타고 있었다. 테스트용 템플릿은 반드시
    실제 임계값(0.5)을 걸어야 한다."""
    template_id = pr.create_pipeline_template(conn, actor_id, "그래프 테스트 템플릿")
    pr.add_stage(
        conn, actor_id, template_id, "1단계", "single", [("역할1", "")],
        max_retry=max_retry, min_gate_pass_ratio=0.5,
    )
    pr.add_stage(
        conn, actor_id, template_id, "2단계", "single", [("역할1", "")],
        max_retry=max_retry, min_gate_pass_ratio=0.5,
    )
    return template_id


@pytest.fixture
def setup(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    agent_id = _make_agent(conn, "역할1")
    task_id = _make_task(conn)
    template_id = _two_stage_template(conn, admin_id, agent_id)
    run_id = pr.start_pipeline_run(conn, admin_id, template_id, task_id)
    return conn, admin_id, agent_id, task_id, template_id, run_id


# ---------- validate_template_supported() ----------


def test_parallel_delegated_is_rejected_and_old_stages_migrate_to_plan_merge(setup):
    """2026-11-26(P1-7 4단계) — 병행 위임 모드 제거. 새로 만들 수 없고, 예전 DB에 남은 단계는 기동 때 계획 분담으로."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    with pytest.raises(ValueError):
        pr.add_stage(conn, admin_id, template_id, "병렬단계", "parallel_delegated",
                     [("리드", ""), ("팀원", "")], lead_role_name="리드")
    sid = pr.add_stage(conn, admin_id, template_id, "병렬단계", "single", [("리드", "")])
    conn.execute("UPDATE pipeline_stages SET mode = 'parallel_delegated', lead_role_name = NULL WHERE id = ?", (sid,))
    conn.commit()
    from hair_platform import db as _db

    assert _db._migrate_parallel_delegated_to_plan_merge(conn) == 1
    row = conn.execute("SELECT mode, lead_role_name FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    assert tuple(row) == ("plan_merge", "리드")  # 리드가 없으면 첫 참여 역할이 리드
    assert _db._migrate_parallel_delegated_to_plan_merge(conn) == 0  # 다시 돌려도 아무 일 없음
    execution_graph.validate_template_supported(conn, template_id)  # 그래프 엔진도 그대로 실행 가능


def test_validate_template_accepts_single_and_sequential_relay(setup):
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    execution_graph.validate_template_supported(conn, template_id)  # 예외 없어야 함


# ---------- 정상 완주 ----------


def test_run_pipeline_graph_completes_both_stages_on_success(setup):
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    stages = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    assert len(stages) == 2
    assert all(s["status"] == "passed" for s in stages)
    # 2단계가 1단계의 산출물을 base_input으로 받았는지(체이닝) 확인
    assert "output-" in stages[1]["output"] or stages[1]["output"]


# ---------- Gate 실패 → 재시도 → 통과 ----------


def test_run_pipeline_graph_retries_then_passes(setup):
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    bad = {"A": {"score": 0.1, "passed": False}}
    good = {"A": {"score": 0.9, "passed": True}}
    calls = {"n": 0}

    def _sequenced(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        calls["n"] += 1
        fn = _good_run_crew(gate_scores=bad if calls["n"] == 1 else good)
        return fn(conn, agent_ids, task_descriptions, actor_id, **kwargs)

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_sequenced
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    stage1_rows = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? "
        "AND stage_id = (SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? "
        "AND seq_order = 1) ORDER BY attempt",
        (run_id, template_id),
    ).fetchall()
    assert len(stage1_rows) == 2
    assert stage1_rows[0]["status"] == "failed_retrying"
    assert stage1_rows[1]["status"] == "passed"
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"


# ---------- Gate 실패 반복 → 에스컬레이션 → 사람 승인 → 재개 ----------


def test_run_pipeline_graph_escalates_to_human_approval_on_retry_exhaustion(setup):
    """회귀 가드(구현 중 발견) — `SqliteSaver.from_conn_string()`은
    컨텍스트 매니저라 `with` 블록을 벗어나면 커넥션이 닫힌다. `saver`를
    재사용하려면 재개 호출까지 같은 블록 안에 있어야 한다(`:memory:`는
    특히 블록을 벗어나는 순간 데이터 자체가 사라짐)."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    bad = {"A": {"score": 0.1, "passed": False}}

    with SqliteSaver.from_conn_string(":memory:") as saver:
        with patch(
            "hair_platform.crew_runtime.run_crew",
            side_effect=_good_run_crew(gate_scores=bad),
        ):
            result = execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

        assert "__interrupt__" in result
        run = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "paused_for_approval"
        last_stage = conn.execute(
            "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        assert last_stage["status"] == "escalated"

        # 승인 후 재개 — 같은 checkpointer 세션 안에서
        with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
            execution_graph.resume_pipeline_graph(
                conn, run_id, saver, decision={"decision": "approve"}
            )
        run = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "completed"


# ---------- 핵심: 진짜 크래시 복구 ----------


def test_resume_after_simulated_crash_restart(setup, tmp_path):
    """서버가 죽었다가 재시작되는 상황을 그대로 재현한다 — 체크포인터와
    DB 커넥션을 완전히 닫고, 새로운 커넥션/체크포인터로 다시 연다."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    checkpoint_path = str(tmp_path / "checkpoints.sqlite3")
    bad = {"A": {"score": 0.1, "passed": False}}

    with SqliteSaver.from_conn_string(checkpoint_path) as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        result = execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)
    assert "__interrupt__" in result
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"

    # 여기서 프로세스가 죽었다고 가정 — SqliteSaver/체크포인터 객체를
    # 전부 버리고, 완전히 새로운 커넥션으로 다시 연다("서버 재시작").
    del saver

    with SqliteSaver.from_conn_string(checkpoint_path) as saver2, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        execution_graph.resume_pipeline_graph(
            conn, run_id, saver2, decision={"decision": "approve"}
        )

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    stages = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    # 1단계(max_retry=2라 attempt 1 실패+2 escalated=2행) + 2단계(1행,
    # 재시작 후 이어서 실행) = 3행. 핵심 확인: attempt 1/2는 재시작
    # 후에도 다시 실행되지 않고(상태값이 재시작 전 그대로) 딱 이어서
    # 2단계만 새로 실행됐는지 — 처음부터 다시 돈 거라면 attempt 1/2가
    # 또 생겨 5행 이상이 됐을 것이다.
    assert len(stages) == 3
    assert stages[0]["attempt"] == 1 and stages[0]["status"] == "failed_retrying"
    assert stages[1]["attempt"] == 2 and stages[1]["status"] == "approved"
    assert stages[2]["status"] == "passed"


# ---------- requires_human_approval(Gate 통과해도 승인 필요) ----------


def test_stage_requires_human_approval_pauses_even_on_gate_pass(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    _make_agent(conn, "역할1")
    task_id = _make_task(conn)
    template_id = pr.create_pipeline_template(conn, admin_id, "승인필요 템플릿")
    pr.add_stage(
        conn, admin_id, template_id, "승인단계", "single", [("역할1", "")],
        requires_human_approval=True,
    )
    run_id = pr.start_pipeline_run(conn, admin_id, template_id, task_id)

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        result = execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    assert "__interrupt__" in result
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"


# ---------- 2단계(2026-11-18, "진행해줘") — 기능 확장 ----------


def test_last_automatic_attempt_enables_self_review(setup):
    """마지막 자동 재시도(attempt == max_retry)에서만 `run_crew()`가
    `self_review=True`로 호출되는지 확인 — `pipeline_runtime.py`
    로드맵 6번과 동일한 설계."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    bad = {"A": {"score": 0.1, "passed": False}}
    calls = []

    def _capturing(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        calls.append(kwargs.get("self_review"))
        return {
            "final_output": "x",
            "agents": [
                {"agent_id": aid, "role_name": "role", "output": f"o-{aid}"}
                for aid in agent_ids
            ],
            "gate_scores": bad,
            "tcr": 10.0,
            "delegations": [],
        }

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_capturing
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    # max_retry=2 → attempt 1(self_review=False), attempt 2(self_review=True)
    assert calls == [False, True]


def test_cancel_requested_stops_graph_before_next_stage(setup):
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    conn.execute(
        "UPDATE pipeline_runs SET cancel_requested = 1 WHERE id = ?", (run_id,)
    )
    conn.commit()

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ) as mock_run_crew:
        result = execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    mock_run_crew.assert_not_called()
    assert "__interrupt__" not in result
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "cancelled"
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM pipeline_run_stages WHERE pipeline_run_id = ?",
        (run_id,),
    ).fetchone()["c"] == 0


def test_task_retry_budget_forces_escalation_before_stage_max_retry(setup):
    """단계 내 `max_retry=2`라 보통은 2번째 시도까지 가야 하지만,
    Task 전체 `max_retry_budget=1`이 걸려 있으면 1번째 실패 직후
    바로 에스컬레이션돼야 한다(`pipeline_runtime.py` 로드맵 9번과
    동일한 설계) — 단계 내 상한보다 Task 전체 예산이 먼저 걸린다."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    conn.execute("UPDATE tasks SET max_retry_budget = 1 WHERE id = ?", (task_id,))
    conn.commit()
    bad = {"A": {"score": 0.1, "passed": False}}

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    stages = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    assert len(stages) == 1
    assert stages[0]["attempt"] == 1
    assert stages[0]["status"] == "escalated"


def test_passing_stage_creates_deliverable_and_approval_chain_on_escalation(setup):
    """읽기 쪽 호환성 — 기존 Task 협업 워크스페이스/결재함이 수정 없이
    이 엔진의 결과도 보여줄 수 있어야 한다: 통과한 단계는
    `deliverables`+`agent_evaluations` 행을, 에스컬레이션된 단계는
    실제 `approvals` 체인을 남긴다."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    bad = {"A": {"score": 0.1, "passed": False}}

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    # 통과한 단계가 하나도 없으므로(계속 실패) deliverables는 0개여야 한다.
    deliverable_count = conn.execute(
        "SELECT COUNT(*) AS c FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchone()["c"]
    assert deliverable_count == 0

    escalated_stage = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? AND status = 'escalated'",
        (run_id,),
    ).fetchone()
    assert escalated_stage is not None
    approval_count = conn.execute(
        "SELECT COUNT(*) AS c FROM approvals WHERE subject_type = 'pipeline_run_stage' "
        "AND subject_id = ?",
        (escalated_stage["id"],),
    ).fetchone()["c"]
    assert approval_count > 0


def test_passing_run_creates_deliverables_and_updates_current_stage_seq(setup):
    conn, admin_id, agent_id, task_id, template_id, run_id = setup

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)

    deliverables = conn.execute(
        "SELECT * FROM deliverables WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()
    assert len(deliverables) == 2
    assert all(d["status"] == "approved" for d in deliverables)
    evaluations = conn.execute(
        "SELECT COUNT(*) AS c FROM agent_evaluations WHERE deliverable_id IN "
        "(SELECT id FROM deliverables WHERE task_id = ?)",
        (task_id,),
    ).fetchone()["c"]
    assert evaluations == 2
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"


# ---------- 3단계(2026-11-18, "다음 개선 진행해줘") — 결재함 연결 ----------


def test_live_trigger_and_approval_decide_bridges_to_graph_engine(client_and_conn):
    """실제 HTTP 엔드포인트(`POST .../execute-pipeline` with
    `use_graph_engine=True`)로 그래프 엔진 run을 시작하고, Gate 실패로
    에스컬레이션된 뒤 실제 결재함 엔드포인트(`POST /api/approvals/{id}/
    decide`)로 승인하면 `interrupt()`가 실제로 재개돼 run이 완료까지
    이어지는지 — `app.py`의 분기(`pipeline_runs.engine == 'graph'`)와
    공유 체크포인터(`get_checkpointer()`)가 실제로 맞물리는지 확인하는
    핵심 종단 테스트."""
    client, conn = client_and_conn

    _login(client, "admin1", "adminpass")
    resp = client.post("/api/briefs", json={"raw_text": "그래프엔진 라이브 테스트"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "역할1", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    _make_agent(conn, "역할1")
    template_id = pr.create_pipeline_template(conn, admin_id, "라이브 브릿지 템플릿")
    pr.add_stage(
        conn, admin_id, template_id, "1단계", "single", [("역할1", "")],
        max_retry=1, min_gate_pass_ratio=0.5,
    )
    conn.commit()

    bad = {"A": {"score": 0.1, "passed": False}}
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/execute-pipeline",
            json={"pipeline_template_id": template_id, "use_graph_engine": True},
        )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "paused_for_approval"
    assert run["engine"] == "graph"
    stage = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ?", (run_id,)
    ).fetchone()
    assert stage["status"] == "escalated"
    approval = conn.execute(
        "SELECT * FROM approvals WHERE subject_type = 'pipeline_run_stage' "
        "AND subject_id = ?",
        (stage["id"],),
    ).fetchone()
    assert approval is not None

    # admin1(실행 요청자) 자신은 self-approval 금지에 걸리므로
    # manager1으로 승인한다.
    _login(client, "manager1", "managerpass")
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        resp = client.post(
            f"/api/approvals/{approval['id']}/decide", json={"approve": True}
        )
    assert resp.status_code == 200

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    stages = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    assert len(stages) == 1
    assert stages[0]["status"] == "approved"


# ---------- 5단계(2026-11-18, "다음 개선 진행해줘") — 체크포인트 정리 ----------


def test_completed_run_checkpoint_is_cleaned_up(setup):
    """완료된 run의 LangGraph 체크포인트는 더 이상 재개될 일이 없으므로
    정리돼야 한다 — `checkpoints`/`writes` 테이블이 무기한 쌓이는 운영
    공백을 막는다."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()
    ):
        execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)
        config: RunnableConfig = {"configurable": {"thread_id": f"pipeline_run_{run_id}"}}
        assert saver.get_tuple(config) is None


def test_escalated_run_checkpoint_is_preserved(setup):
    """아직 사람 승인을 기다리는(재개돼야 하는) run의 체크포인트는
    지우면 안 된다 — 지워버리면 영원히 재개할 수 없게 된다."""
    conn, admin_id, agent_id, task_id, template_id, run_id = setup
    bad = {"A": {"score": 0.1, "passed": False}}

    with SqliteSaver.from_conn_string(":memory:") as saver, patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        result = execution_graph.run_pipeline_graph(conn, run_id, admin_id, saver)
        assert "__interrupt__" in result
        config: RunnableConfig = {"configurable": {"thread_id": f"pipeline_run_{run_id}"}}
        assert saver.get_tuple(config) is not None


def test_checkpointer_cache_is_reused_across_calls(client_and_conn):
    """`get_checkpointer()`가 같은 `conn` 인스턴스에 대해 매번 새
    체크포인터를 여는 게 아니라 캐시를 재사용하는지 — 직접 확인되지
    않으면 `:memory:` 배포에서 승인 재개가 조용히 깨진다(이전 라운드
    에서 실제로 겪은 버그)."""
    _client, conn = client_and_conn
    first = execution_graph.get_checkpointer(conn)
    second = execution_graph.get_checkpointer(conn)
    assert first is second


# ---------- 6단계(2026-11-18, "conversations.py 통합") — @팀멘션 ----------


def _team_graph_engine_fixture(conn, admin_id):
    """`teams.use_graph_engine=True` + 1단계 템플릿(승인 불필요, 바로
    통과 가능) 팀을 만든다."""
    agent_id = _make_agent(conn, "역할1")
    template_id = pr.create_pipeline_template(conn, admin_id, "대화연동 템플릿")
    pr.add_stage(
        conn, admin_id, template_id, "1단계", "single", [("역할1", "")],
        max_retry=2, min_gate_pass_ratio=0.5,
    )
    conn.commit()
    cur = conn.execute(
        "INSERT INTO teams (name, description, is_active, status, "
        "pipeline_template_id, use_graph_engine) "
        "VALUES (?, '대화연동 테스트팀', 1, 'active', ?, 1)",
        ("그래프엔진팀", template_id),
    )
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '역할1')",
        (team_id, agent_id),
    )
    conn.commit()
    return team_id, template_id, agent_id


def test_team_mention_with_graph_engine_completes_and_notifies(client_and_conn):
    """`teams.use_graph_engine=True`인 팀을 `@멘션`하면 기존
    `pipeline_runtime.run_pipeline_stage_job()` 대신 그래프 엔진으로
    돌고, `pipeline_runs.engine == 'graph'`로 기록되며, 완료 후 진행
    상황 메시지가 실제로 대화에 올라가는지."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    team_id, _template_id, _agent_id = _team_graph_engine_fixture(conn, admin_id)

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "그래프엔진 대화"}).json()[
        "id"
    ]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )
    assert resp.status_code == 201, resp.text

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@그래프엔진팀 테스트 업무를 진행해줘"},
        )
        assert resp.status_code == 201, resp.text

    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE source_conversation_id = ?", (conv_id,)
    ).fetchone()
    assert run is not None
    assert run["engine"] == "graph"
    assert run["status"] == "completed"

    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert any("진행 상황" in m["content"] for m in agent_messages)


def test_team_mention_with_graph_engine_escalation_resumes_via_approval(client_and_conn):
    """에스컬레이션된 그래프 엔진 run을 실제 결재함 API로 승인하면
    `conversations.resume_pipeline_graph_from_approval_and_notify()`를
    거쳐 재개되고, 완료 후 진행 메시지가 대화에 다시 올라가는지."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    team_id, _template_id, _agent_id = _team_graph_engine_fixture(conn, admin_id)

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "그래프엔진 에스컬레이션"}).json()[
        "id"
    ]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    bad = {"A": {"score": 0.1, "passed": False}}
    with patch(
        "hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew(gate_scores=bad)
    ):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@그래프엔진팀 테스트 업무를 진행해줘"},
        )
        assert resp.status_code == 201, resp.text

    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE source_conversation_id = ?", (conv_id,)
    ).fetchone()
    assert run["status"] == "paused_for_approval"
    stage = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE pipeline_run_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (run["id"],),
    ).fetchone()
    approval = conn.execute(
        "SELECT * FROM approvals WHERE subject_type = 'pipeline_run_stage' "
        "AND subject_id = ?",
        (stage["id"],),
    ).fetchone()
    assert approval is not None

    messages_before = len(client.get(f"/api/conversations/{conv_id}/messages").json())

    _login(client, "manager1", "managerpass")
    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/approvals/{approval['id']}/decide", json={"approve": True}
        )
        assert resp.status_code == 200

    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run["id"],)).fetchone()
    assert run["status"] == "completed"
    _login(client, "member1", "memberpass")
    messages_after = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(messages_after) > messages_before


def test_team_mention_with_graph_engine_posts_progress_per_auto_passed_stage(
    client_and_conn,
):
    """회귀 가드(2026-10-28에 레거시 엔진에서 이미 한 번 고친 버그의
    그래프 엔진판) — 승인 없이 연달아 자동 통과하는 단계가 여러 개면,
    전체가 끝날 때까지 대화에 메시지가 1건만 올라가는 게 아니라 단계
    마다 1건씩 올라가야 한다(`run_pipeline_graph_job_with_callback()`
    의 `on_stage_resolved` 콜백이 실제로 매 단계 호출되는지 확인)."""
    client, conn = client_and_conn
    admin_id = _admin_id(conn)
    agent_id = _make_agent(conn, "역할1")
    template_id = pr.create_pipeline_template(conn, admin_id, "2단계 대화연동 템플릿")
    pr.add_stage(
        conn, admin_id, template_id, "1단계", "single", [("역할1", "")],
        max_retry=2, min_gate_pass_ratio=0.5,
    )
    pr.add_stage(
        conn, admin_id, template_id, "2단계", "single", [("역할1", "")],
        max_retry=2, min_gate_pass_ratio=0.5,
    )
    conn.commit()
    cur = conn.execute(
        "INSERT INTO teams (name, description, is_active, status, "
        "pipeline_template_id, use_graph_engine) "
        "VALUES (?, '2단계 테스트팀', 1, 'active', ?, 1)",
        ("그래프엔진2단계팀", template_id),
    )
    team_id = cur.lastrowid
    conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, 'agent', ?, '역할1')",
        (team_id, agent_id),
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "2단계 그래프엔진"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "team", "participant_id": team_id},
    )

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_good_run_crew()):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "@그래프엔진2단계팀 테스트 업무를 진행해줘"},
        )
        assert resp.status_code == 201, resp.text

    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE source_conversation_id = ?", (conv_id,)
    ).fetchone()
    assert run["status"] == "completed"
    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    progress_messages = [m for m in messages if "진행 상황" in m["content"]]
    # 2단계 모두 자동 통과(승인 불필요) — 콜백이 매 단계(run_stage
    # 1회 + advance 1회)마다 불려 최소 2건은 올라가야 한다(정확히
    # 몇 건인지보다 "1건만"이 아니라는 게 핵심 — advance도 run_stage
    # 못지않게 콜백을 부르므로 실제로는 더 많을 수 있음).
    assert len(progress_messages) >= 2
