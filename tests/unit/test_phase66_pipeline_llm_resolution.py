"""Phase 66 — 파이프라인 단계가 Agent별 저장된 LLM provider/model을
실제로 쓰도록 수정(실측, 2026-11-06, park_member 로드러너 게임 데모).

**배경**: `pipeline_runtime._execute_stage_attempt()`는 지금까지
`crew_runtime.run_crew()`에 `provider`/`model`을 전혀 안 넘겼다 —
"AI 에이전트 관리" 화면에서 Ollama로 설정해둔 Agent도 파이프라인
경로에서는 그 설정이 무시되고 매번 자동판별(Anthropic 키 없으면
OpenAI)로 돌았다. `_resolve_stage_llm()`이 이 갭을 메운다 — 이
단계의 "대표 Agent"(parallel_delegated면 리드, 아니면 첫 참여 Agent)
의 저장된 provider/model을 돌려준다.

같은 라운드에서 함께 발견한 두 번째 문제: CrewAI 매니저가 위임을
반복하다 LLM 컨텍스트 윈도우를 초과하면 그 예외가 CrewAI/LiteLLM
내부에서 로그만 남기고 우리 코드까지 안 올라와, `pipeline_runs.
status='running'`이 영원히 방치된다(실측: 2시간 넘게 방치, 수동
정리 필요). `reap_orphaned_pipeline_runs()`가 `deliverables.
reap_orphaned_execution_jobs()`와 같은 전제(단일 프로세스 재기동
시 'running' 잔재 정리)로 이를 막는다.
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


def _set_agent_llm(conn, role_name, provider, model):
    conn.execute(
        "UPDATE agent_pool SET llm_provider = ?, llm_model = ? WHERE role_name = ?",
        (provider, model, role_name),
    )
    conn.commit()


# ---------- _resolve_stage_llm ----------


def test_resolve_stage_llm_uses_lead_role_for_parallel_delegated():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    _set_agent_llm(conn, "개발 계획 수립가", "ollama", "ornith-1.5:35b")
    _set_agent_llm(conn, "백엔드 개발자", "ollama", "다른모델")  # 리드가 아니므로 무시돼야 함

    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    sid = pr.add_stage(
        conn,
        admin_id,
        tid,
        "구현",
        "parallel_delegated",
        [("개발 계획 수립가", ""), ("백엔드 개발자", "백엔드")],
        lead_role_name="개발 계획 수립가",
    )
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    provider, model = pr._resolve_stage_llm(conn, stage)
    assert (provider, model) == ("ollama", "ornith-1.5:35b")


def test_resolve_stage_llm_uses_first_agent_for_single_mode():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    _set_agent_llm(conn, "요구사항 분석가", "anthropic", "claude-sonnet-5")

    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    sid = pr.add_stage(
        conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")]
    )
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    provider, model = pr._resolve_stage_llm(conn, stage)
    assert (provider, model) == ("anthropic", "claude-sonnet-5")


def test_resolve_stage_llm_falls_back_to_none_when_unconfigured():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    # 기본 시드 Agent는 llm_provider/llm_model이 NULL — 아무것도 안 건드림.

    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    sid = pr.add_stage(
        conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")]
    )
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (sid,)).fetchone()
    assert pr._resolve_stage_llm(conn, stage) == (None, None)


def test_execute_stage_attempt_passes_resolved_provider_model_to_run_crew():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    _set_agent_llm(conn, "요구사항 분석가", "ollama", "qwen3.8:27b-mlx")
    task_id = _make_task(conn)

    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    pr.add_stage(conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")])

    captured = {}

    def _fake_run_crew(conn, agent_ids, task_descriptions, actor_id, **kwargs):
        captured["provider"] = kwargs.get("provider")
        captured["model"] = kwargs.get("model")
        return {
            "final_output": "출력",
            "agents": [
                {"agent_id": agent_ids[0], "role_name": "role", "name": None, "output": "출력"}
            ],
            "gate_scores": {"A": {"score": 0.9, "passed": True}},
            "tcr": 90.0,
            "delegations": [],
            "resolved_provider": "ollama",
            "resolved_model": "qwen3.8:27b-mlx",
            "token_usage": {"total_tokens": 1},
        }

    with patch("hair_platform.crew_runtime.run_crew", side_effect=_fake_run_crew):
        run_id = pr.start_pipeline_run(conn, admin_id, tid, task_id)
        pr.run_pipeline_stage_job(conn, run_id, admin_id)

    assert captured["provider"] == "ollama"
    assert captured["model"] == "qwen3.8:27b-mlx"
    run = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"


# ---------- reap_orphaned_pipeline_runs ----------


def test_reap_orphaned_pipeline_runs_marks_running_as_failed():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "admin1", "adminpass", Role.ADMIN)
    task_id = _make_task(conn)
    tid = pr.create_pipeline_template(conn, admin_id, "p", "d", "software_dev")
    pr.add_stage(conn, admin_id, tid, "요구분석", "single", [("요구사항 분석가", "")])

    cur = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) "
        "VALUES (?, ?, ?, 'running')",
        (tid, task_id, admin_id),
    )
    stuck_run_id = cur.lastrowid
    cur2 = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) "
        "VALUES (?, ?, ?, 'completed')",
        (tid, task_id, admin_id),
    )
    completed_run_id = cur2.lastrowid
    conn.commit()

    reaped = pr.reap_orphaned_pipeline_runs(conn)
    assert reaped == 1

    stuck = conn.execute(
        "SELECT status, finished_at FROM pipeline_runs WHERE id = ?", (stuck_run_id,)
    ).fetchone()
    assert stuck["status"] == "failed"
    assert stuck["finished_at"] is not None

    completed = conn.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (completed_run_id,)
    ).fetchone()
    assert completed["status"] == "completed"  # 이미 끝난 건 안 건드림


def test_reap_orphaned_pipeline_runs_is_idempotent_with_nothing_stuck():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    assert pr.reap_orphaned_pipeline_runs(conn) == 0
