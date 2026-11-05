"""비용 조회. 1차 범위: 사람·Agent 비용 모두 수동 입력이다(HR 연동·실 LLM
과금 연동은 오픈 이슈로 이월). Admin·매니저만 조회 가능(RBAC 매트릭스).
"""

from __future__ import annotations

import json
import sqlite3


def set_agent_cost(
    conn: sqlite3.Connection, agent_id: int, cost_estimate: float
) -> None:
    conn.execute(
        "UPDATE agent_pool SET cost_estimate = ? WHERE id = ?",
        (cost_estimate, agent_id),
    )
    conn.commit()


def _confirmed_count(
    conn: sqlite3.Connection, resource_type: str, resource_id: int
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM assignments "
        "WHERE resource_type = ? AND resource_id = ? AND status = 'confirmed'",
        (resource_type, resource_id),
    ).fetchone()
    return row["c"]


def _confirmed_count_for_task(
    conn: sqlite3.Connection, resource_type: str, resource_id: int, task_id: int
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM assignments "
        "WHERE resource_type = ? AND resource_id = ? AND status = 'confirmed' "
        "AND task_id = ?",
        (resource_type, resource_id, task_id),
    ).fetchone()
    return row["c"]


def task_report(conn: sqlite3.Connection, task_id: int) -> dict:
    """사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산
    추적") — `report()`와 같은 "리소스 단가 × 확정 배정 건수" 계산을
    **이 Task 하나로만** 좁힌다. 실 LLM 과금 연동은 `report()`의
    docstring이 이미 "오픈 이슈로 이월"로 명시해둔 별개 영역이라
    여기서도 건들지 않는다 — 대신 `crew_runs.token_usage`(CrewAI가
    실제로 집계해 이미 저장해주는 진짜 토큰 수)를 이 Task의 모든
    Crew 실행에 걸쳐 합산해 **추정 비용과 별개 키로** 돌려준다(달러로
    지어내 환산하지 않는다 — "없는 값을 지어내지 않는다"는 이
    코드베이스의 기존 원칙 그대로)."""
    resources = []
    total_cost = 0.0
    for row in conn.execute("SELECT * FROM human_pool").fetchall():
        count = _confirmed_count_for_task(conn, "human", row["id"], task_id)
        if count == 0:
            continue
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "human",
                "id": row["id"],
                "code": row["employee_id"],
                "name": row["name"],
                "role": row["role"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )
    for row in conn.execute(
        "SELECT * FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        count = _confirmed_count_for_task(conn, "agent", row["id"], task_id)
        if count == 0:
            continue
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "agent",
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "role": row["role_name"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )

    token_totals = {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "successful_requests": 0,
    }
    for row in conn.execute(
        "SELECT token_usage FROM crew_runs WHERE task_id = ? AND token_usage IS NOT NULL",
        (task_id,),
    ).fetchall():
        tu = json.loads(row["token_usage"])
        for key in token_totals:
            token_totals[key] += tu.get(key) or 0

    return {
        "resources": resources,
        "estimated_cost": total_cost,
        "token_usage": token_totals,
    }


def report(conn: sqlite3.Connection) -> dict:
    resources = []
    total_cost = 0.0
    for row in conn.execute("SELECT * FROM human_pool").fetchall():
        count = _confirmed_count(conn, "human", row["id"])
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "human",
                "id": row["id"],
                # 사용자 요청(2026-10-27, "비용의 ID도 agent code로
                # 변경") — human 쪽은 agent의 code에 대응하는 값이
                # employee_id(사번)다. 미입력이면 화면에서 "#id"로
                # 폴백한다(다른 사람 관련 화면과 같은 패턴).
                "code": row["employee_id"],
                "name": row["name"],
                "role": row["role"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )
    for row in conn.execute(
        "SELECT * FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        count = _confirmed_count(conn, "agent", row["id"])
        cost = row["cost_estimate"] * count
        total_cost += cost
        resources.append(
            {
                "type": "agent",
                "id": row["id"],
                "code": row["code"],
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 전엔 Agent는 이름 개념 자체가 없어 항상 None이었다.
                "name": row["name"],
                "role": row["role_name"],
                "cost_estimate": row["cost_estimate"],
                "confirmed_assignments": count,
                "total_cost": cost,
            }
        )
    # 사용자 요청(2026-10-29, 개선 계획 Phase 1 "Team 성과/비용 집계") —
    # Team 자체는 `cost_estimate` 컬럼이 없다(비용은 멤버 Agent 개별로만
    # 입력된다 — 위 agent 루프에서 이미 집계됨). 여기서는 "이 팀이
    # 확정 배정으로 얼마나 투입됐는지"만 보여주고, `total_cost`에는
    # 더하지 않는다(멤버 Agent 쪽 집계와 중복 합산되지 않도록).
    for row in conn.execute("SELECT * FROM teams WHERE status = 'active'").fetchall():
        count = _confirmed_count(conn, "team", row["id"])
        resources.append(
            {
                "type": "team",
                "id": row["id"],
                "code": None,
                "name": row["name"],
                "role": None,
                "cost_estimate": None,
                "confirmed_assignments": count,
                "total_cost": None,
            }
        )
    return {"resources": resources, "total_cost": total_cost}
