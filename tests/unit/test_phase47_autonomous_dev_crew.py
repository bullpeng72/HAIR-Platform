"""Phase 47 — "자율 개발 크루" 실행 경로, 사용자 요청(2026-10-14, "기획서가
제시되면 이를 받아 자율적으로 개발을 완료할 복수의 개발자(10명)가 포함된
crewai 에이전트 그룹 구성 방안을 제안해줘(권한 위임을 통한 자율성을 보장,
가능하면 하네스 방법론 적용)" → 제안 승인 후 구현).

**정리(2026-10-26, 사용자 분석 요청 "같은 업무의 에이전트가 1,2,3 등으로
되어 있어... 각 영역에 하나의 에이전트만 있어도 되지 않는지")** — 원래
10명(백엔드 4·프론트엔드 3·데이터/인프라 2·풀스택 1)이었던 구성을 영역별
1명(4명)으로 줄였다. 실측으로 확인한 이유: CrewAI hierarchical은
`Task.agent`를 무시하고 매니저에게 Task를 **하나만** 주며, 매니저가
"Delegate work to coworker" 도구로 **그때그때 동적으로** 역할별 하위
과업을 나눈다(`crew_runtime.py`의 `hierarchical_with_manager` 주석 참고) —
같은 영역의 여러 Agent는 skill_tags·tuning_config가 완전히 동일한 클론이라
매니저가 구분해 쓸 근거가 없었고, 매니저가 같은 코워커 한 명에게 하위
과업을 여러 번 나눠 위임하는 것도 기술적으로 동일하게 동작해 기능 손실이
없다.

**비용/네트워크 발생 방지(중요, test_phase12와 같은 원칙)**: `crewai.
Crew.kickoff` 그 자체만 patch한다 — 그 아래(LLM.call 등)는 절대 건드리지
않는다(내부 재시도 로직이 patch를 우회해 실제 API로 새어나간 전례가 있음).

이 테스트는 "개발 계획 수립가"를 매니저로, 4명의 개발자 + UI 기획자를
워커로 두는 hierarchical Crew가 실제로 구성되는지(매니저가 agents 목록에서
빠지고 manager_agent로만 들어가는지, 5명의 워커가 전부 Crew에 포함되는지,
allow_delegation이 걸려 있는지)를 확인한다 — "권한 위임을 통한 자율성"의
실제 구현 지점이 `crew_runtime.run_crew()`의 기존 `allow_delegation=True`
전역 기본값이라는 걸 이 규모에서도 재확인한다."""

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
    "백엔드 개발자",
    "프론트엔드 개발자",
    "데이터/인프라 개발자",
    "풀스택 개발자",
]
_WORKER_ROLE_NAMES = [*_DEV_ROLE_NAMES, "UI 기획자"]  # 4명 개발자 + UI 기획자 = 5명


def test_gc_autonomous_dev_crew_has_exactly_four_developers():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    rows = conn.execute(
        "SELECT role_name FROM agent_pool WHERE role_name IN ({})".format(
            ",".join("?" * len(_DEV_ROLE_NAMES))
        ),
        _DEV_ROLE_NAMES,
    ).fetchall()
    assert len(rows) == 4


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
        return _crew_output("자율 개발 크루 실행 완료", ["자율 개발 크루 실행 완료"])

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
    assert len(crew.agents) == 5
    assert crew.manager_agent is not None
    assert crew.manager_agent.role == "개발 계획 수립가"
    assert crew.memory is True

    worker_roles_in_crew = {a.role for a in crew.agents}
    assert worker_roles_in_crew == set(_WORKER_ROLE_NAMES)

    # "권한 위임을 통한 자율성" — 워커 전원이 서로 위임/질의 가능해야
    # 한다(`run_crew()`의 기존 전역 기본값이 이 규모에서도 유지되는지 확인).
    assert all(a.allow_delegation is True for a in crew.agents)

    # Task를 하나만 주고 매니저가 코워커들에게 역할별 하위 과업을 실제로
    # 쪼개 위임한 뒤 통합하게 한다(2026-10-18 수정 — `crew_runtime.py`의
    # `hierarchical_with_manager` 주석 참고) — Crew에 전달하는 Task도
    # 하나뿐이고 그 결과도 매니저 1건(통합 산출물)만 남는다.
    assert len(crew.tasks) == 1
    assert crew.tasks[0].agent is None  # 특정 워커에 미리 고정하지 않음
    coworker_mention = crew.tasks[0].description
    for role in _WORKER_ROLE_NAMES:
        assert role in coworker_mention  # 매니저가 위임할 수 있는 코워커 목록 안내

    outputs = {a["agent_id"]: a["output"] for a in result["agents"]}
    assert outputs[manager_id] == "자율 개발 크루 실행 완료"
    for worker_id in worker_ids:
        assert (
            worker_id not in outputs
        )  # 워커별 개별 산출물이 아니라 매니저 통합본 하나


def test_gc_autonomous_dev_crew_team_is_visible_and_has_six_members():
    """사용자 보고(2026-10-14, "위 Agent 그룹 확인이 안되") 재발 방지 —
    Agent를 개별로 만드는 것만으로는 "팀 관리" 화면에 그룹이 보이지
    않는다. 실제 `teams`/`team_members` 레코드가 생겨야 한다."""
    conn = db.get_connection(":memory:")
    db.init_schema(conn)

    team = conn.execute("SELECT id FROM teams WHERE name = '자율 개발 크루'").fetchone()
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
    assert len(member_roles) == 6

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
    assert conn.execute("SELECT COUNT(*) c FROM team_members").fetchone()["c"] == 6


def test_gc_autonomous_dev_crew_developers_carry_tdd_and_commit_skills():
    """하네스 방법론 적용 확인 — 4명 전원이 TDD-AI 워크플로우·커밋 규칙
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
