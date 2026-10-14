"""Phase 47 — "자율 개발 크루" 실행 경로, 사용자 요청(2026-10-14, "기획서가
제시되면 이를 받아 자율적으로 개발을 완료할 복수의 개발자(10명)가 포함된
crewai 에이전트 그룹 구성 방안을 제안해줘(권한 위임을 통한 자율성을 보장,
가능하면 하네스 방법론 적용)" → 제안 승인 후 구현).

**비용/네트워크 발생 방지(중요, test_phase12와 같은 원칙)**: `crewai.
Crew.kickoff` 그 자체만 patch한다 — 그 아래(LLM.call 등)는 절대 건드리지
않는다(내부 재시도 로직이 patch를 우회해 실제 API로 새어나간 전례가 있음).

이 테스트는 "개발 계획 수립가"를 매니저로, 10명의 개발자 + UI 기획자를
워커로 두는 hierarchical Crew가 실제로 구성되는지(매니저가 agents 목록에서
빠지고 manager_agent로만 들어가는지, 11명의 워커가 전부 Crew에 포함되는지,
allow_delegation이 걸려 있는지)를 확인한다 — "권한 위임을 통한 자율성"의
실제 구현 지점이 `crew_runtime.run_crew()`의 기존 `allow_delegation=True`
전역 기본값이라는 걸 이 11명 규모에서도 재확인한다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import crew_runtime, db


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


_DEV_ROLE_NAMES = [
    "개발자",
    "백엔드 개발자 2",
    "백엔드 개발자 3",
    "백엔드 개발자 4",
    "프론트엔드 개발자",
    "프론트엔드 개발자 2",
    "프론트엔드 개발자 3",
    "데이터/인프라 개발자 1",
    "데이터/인프라 개발자 2",
    "풀스택 개발자",
]
_WORKER_ROLE_NAMES = [*_DEV_ROLE_NAMES, "UI 기획자"]  # 10명 개발자 + UI 기획자 = 11명


def test_gc_autonomous_dev_crew_has_exactly_ten_developers():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    rows = conn.execute(
        "SELECT role_name FROM agent_pool WHERE role_name IN ({})".format(
            ",".join("?" * len(_DEV_ROLE_NAMES))
        ),
        _DEV_ROLE_NAMES,
    ).fetchall()
    assert len(rows) == 10


def test_gc_hierarchical_crew_excludes_manager_from_worker_agents_and_allows_delegation(
    client_and_conn,
):
    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]

    manager_id = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = '개발 계획 수립가'"
    ).fetchone()["id"]
    worker_ids = [
        conn.execute(
            "SELECT id FROM agent_pool WHERE role_name = ?", (role,)
        ).fetchone()["id"]
        for role in _WORKER_ROLE_NAMES
    ]
    agent_ids = [manager_id, *worker_ids]
    task_descriptions = ["(매니저 — 실행되지 않음)"] + [
        f"기획서/설계 산출물을 바탕으로 '{role}' 담당 모듈을 구현하라"
        for role in _WORKER_ROLE_NAMES
    ]

    captured = {}

    def _kickoff_capture(self, *args, **kwargs):
        captured["crew"] = self
        return _crew_output(
            "자율 개발 크루 실행 완료", [f"{role} 산출물" for role in _WORKER_ROLE_NAMES]
        )

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        result = crew_runtime.run_crew(
            conn,
            agent_ids,
            task_descriptions,
            admin_id,
            process="hierarchical",
            manager_agent_id=manager_id,
            provider="openai",
        )

    crew = captured["crew"]
    # 매니저는 agents 목록에서 빠지고 manager_agent로만 들어간다
    # (CrewAI validator가 둘 다 들어가면 ValidationError를 던짐 — phase12
    # 회귀 테스트와 같은 불변식).
    assert len(crew.agents) == 11
    assert crew.manager_agent is not None
    assert crew.manager_agent.role == "개발 계획 수립가"
    assert crew.memory is True

    worker_roles_in_crew = {a.role for a in crew.agents}
    assert worker_roles_in_crew == set(_WORKER_ROLE_NAMES)

    # "권한 위임을 통한 자율성" — 11명 워커 전원이 서로 위임/질의 가능해야
    # 한다(`run_crew()`의 기존 전역 기본값이 이 규모에서도 유지되는지 확인).
    assert all(a.allow_delegation is True for a in crew.agents)

    # 매니저를 제외한 11명 전원의 산출물이 정확히 매핑됐는지 확인한다.
    outputs = {a["agent_id"]: a["output"] for a in result["agents"]}
    assert outputs[manager_id] is None  # 매니저는 조율만, 자기 몫 산출물 없음
    for role, worker_id in zip(_WORKER_ROLE_NAMES, worker_ids, strict=True):
        assert outputs[worker_id] == f"{role} 산출물"


def test_gc_autonomous_dev_crew_team_is_visible_and_has_twelve_members():
    """사용자 보고(2026-10-14, "위 Agent 그룹 확인이 안되") 재발 방지 —
    Agent를 개별로 만드는 것만으로는 "팀 관리" 화면에 그룹이 보이지
    않는다. 실제 `teams`/`team_members` 레코드가 생겨야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    team = conn.execute(
        "SELECT id FROM teams WHERE name = '자율 개발 크루'"
    ).fetchone()
    assert team is not None

    member_roles = {
        r["role_name"]
        for r in conn.execute(
            """
            SELECT ap.role_name FROM team_members tm
            JOIN agent_pool ap ON ap.id = tm.member_id AND tm.member_type = 'agent'
            WHERE tm.team_id = ?
            """,
            (team["id"],),
        ).fetchall()
    }
    assert member_roles == {"개발 계획 수립가", *_WORKER_ROLE_NAMES}
    assert len(member_roles) == 12

    manager_label = conn.execute(
        """
        SELECT role_label FROM team_members tm
        JOIN agent_pool ap ON ap.id = tm.member_id AND tm.member_type = 'agent'
        WHERE tm.team_id = ? AND ap.role_name = '개발 계획 수립가'
        """,
        (team["id"],),
    ).fetchone()["role_label"]
    assert "매니저" in manager_label


def test_gc_autonomous_dev_crew_team_seeding_is_idempotent():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM team_members").fetchone()["c"] == 12


def test_gc_autonomous_dev_crew_developers_carry_tdd_and_commit_skills():
    """하네스 방법론 적용 확인 — 10명 전원이 TDD-AI 워크플로우·커밋 규칙
    스킬을 보유해야 한다(사용자 요청의 "가능하면 하네스 방법론 적용")."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    for role in _DEV_ROLE_NAMES:
        row = conn.execute(
            "SELECT skill_tags FROM agent_pool WHERE role_name = ?", (role,)
        ).fetchone()
        skills = json.loads(row["skill_tags"])
        assert "tdd-ai-workflow-checklist" in skills
        assert "commit-pr-message-rules" in skills
