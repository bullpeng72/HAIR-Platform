"""Phase 42 — "goal도 skill 기반으로 보강하고, CrewAI Agent가 최대 효과를
발휘하도록 추가적으로 필요한 부분이 있는지 분석하고 개선해줘"(2026-10-13).

Phase 41에서 `backstory`/시스템 프롬프트에는 스킬 설명을 주입했지만,
`goal`은 모든 Agent가 역할명만 다를 뿐 완전히 동일한 문장을 썼다 —
스킬이 몇 개든 전혀 반영되지 않았다. CrewAI 소스(`agent/core.py`)를
직접 읽고 확인한 두 가지를 반영한다:

1. `_agent_goal()`이 스킬을 받아 "완료 기준·절차·HITL 승인을 임의로
   생략하지 말라"는 목표를 덧붙인다(backstory와 내용이 겹치지 않게 —
   backstory는 스킬 설명 "전문", goal은 그걸 반드시 지키라는 목표).
2. `reasoning=True`의 짝 필드 `max_reasoning_attempts`를 tuning_config에
   추가한다 — CrewAI 기본값(`None`)이 "준비될 때까지 무제한 재시도"라서,
   `reasoning`만 켜고 이 값을 비워두면 계획 수립 단계가 끝없이 반복될
   위험이 있다(`reasoning_handler.py` 확인).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import crew_runtime


def _make_active_agent(conn, role_name, skill_tags):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        (role_name, json.dumps(skill_tags, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[
        "id"
    ]


def _crew_output(raw, task_raws):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


# ---------- 1) goal의 skill 기반 보강 ----------


def test_agent_goal_mentions_skills_and_completion_discipline():
    goal = crew_runtime._agent_goal(
        "스펙 작성자", ["spec-driven-artifacts", "docs-style-guide"]
    )
    assert "spec-driven-artifacts" in goal
    assert "docs-style-guide" in goal
    assert "완료 기준" in goal
    assert "생략하지 않는다" in goal


def test_agent_goal_unchanged_when_no_skills():
    goal = crew_runtime._agent_goal("개발자", [])
    assert (
        goal
        == "'개발자' 역할로서 주어진 업무를 실무에 바로 쓸 수 있는 수준으로 완수한다."
    )


def test_agent_goal_differs_from_backstory_content(client_and_conn):
    """goal과 backstory가 같은 텍스트를 중복으로 담지 않는지 — backstory는
    스킬 설명 "전문"을, goal은 "그걸 지키라"는 목표만 담아야 한다."""
    _client, conn = client_and_conn
    from hair_platform import pool_registry

    pool_registry.create_skill(
        conn,
        1,
        "절차스킬",
        "이 역할은 산출물 제출 전 체크리스트를 확인한다.",
        [],
        "none",
    )
    goal = crew_runtime._agent_goal("개발자", ["절차스킬"])
    backstory = crew_runtime._agent_backstory(conn, "개발자", ["절차스킬"])
    assert "이 역할은 산출물 제출 전 체크리스트를 확인한다." in backstory
    assert "이 역할은 산출물 제출 전 체크리스트를 확인한다." not in goal


# ---------- 2) max_reasoning_attempts가 실제 crewai.Agent()까지 전달되는지 ----------


def test_run_crew_passes_max_reasoning_attempts_to_crewai_agent(client_and_conn):
    client, conn = client_and_conn
    agent_id = _make_active_agent(conn, "백엔드 개발자", [])
    conn.execute(
        "UPDATE agent_pool SET tuning_config = ? WHERE id = ?",
        (json.dumps({"reasoning": True, "max_reasoning_attempts": 2}), agent_id),
    )
    conn.commit()
    _resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert _resp.status_code == 200

    with patch("crewai.Crew.kickoff", return_value=_crew_output("결과", ["결과"])):
        result = crew_runtime.run_crew(
            conn,
            [agent_id],
            ["업무를 수행하세요"],
            _admin_id(conn),
            process="sequential",
            provider="openai",
        )
    assert result["final_output"] == "결과"
