"""매니저 대시보드 — Phase 5(사용자 요청, 2026-09-28, "Phase 5(기업용
UI/UX)에 포함할 항목을 고르세요" → "매니저 대시보드" 선택 — "팀 Agent
평균 Gate 점수, 대기 승인 건수, 비용 추이를 한 화면에"). 이미 있는
데이터를 모아 보여줄 뿐, 새 집계 테이블을 만들지 않는다.

**"비용 추이" 실측 확인**: 처음엔 시계열 비용 데이터가 전혀 없다고
생각했다(`cost.py`의 `cost_estimate`는 그때그때 값 하나뿐이라). 하지만
`app.py`의 `agent_cost_updated`(비용 입력)·`human_pool_updated`
(사람 정보 수정, cost_estimate 포함) 감사로그 액션이 이미 `after_value`
에 변경된 cost_estimate 값과 `audit_log.created_at` 타임스탬프를 갖고
있다는 걸 실측으로 확인했다 — 새 계측을 추가할 필요 없이 이 이력을
그대로 "추이"로 쓴다. 단, 이 DB를 새로 부트스트랩했고 지금까지 아무도
비용을 바꾼 적이 없으면 이력이 비어있을 수 있다(정직한 한계 — 과거로
소급해 데이터를 만들어낼 방법은 없다).
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import agent_evaluation, approvals, cost


def team_average_gate_scores(conn: sqlite3.Connection) -> dict:
    """모든 Agent 산출물 평가를 통틀어 Gate별 평균 점수(측정된 것만)와
    평균 TCR을 낸다 — `agent_evaluation.aggregate_agent_score()`의
    "Agent 하나" 범위를 "팀 전체"로 넓힌 버전(같은 계산 로직)."""
    rows = conn.execute("SELECT gate_scores, tcr FROM agent_evaluations").fetchall()
    if not rows:
        return {
            "sample_count": 0,
            "gate_averages": dict.fromkeys(agent_evaluation.GATE_IDS),
            "avg_tcr": None,
        }
    gate_totals: dict[str, list[float]] = {gid: [] for gid in agent_evaluation.GATE_IDS}
    tcr_values: list[float] = []
    for row in rows:
        scores = json.loads(row["gate_scores"])
        for gid in agent_evaluation.GATE_IDS:
            score = scores.get(gid, {}).get("score")
            if score is not None:
                gate_totals[gid].append(score)
        if row["tcr"] is not None:
            tcr_values.append(row["tcr"])
    gate_averages = {
        gid: (sum(values) / len(values) if values else None)
        for gid, values in gate_totals.items()
    }
    return {
        "sample_count": len(rows),
        "gate_averages": gate_averages,
        "avg_tcr": (sum(tcr_values) / len(tcr_values) if tcr_values else None),
    }


def cost_trend(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """`agent_cost_updated`/`human_pool_updated` 감사로그에서
    cost_estimate가 실제로 바뀐 이력만 뽑아 최신순으로 낸다.

    **실측으로 발견한 버그(2026-09-28, "Phase1~5까지... 분석하고
    개선해줘" 리뷰 중)**: 원래는 SQL에서 먼저 `LIMIT`을 건 뒤 Python에서
    `cost_estimate` 유무를 걸렀다 — `human_pool_updated`는 비용 말고도
    role/availability/user_id 변경에도 찍히므로, 비용과 무관한 변경이
    최근에 몰리면(예: 가용성을 여러 번 토글) 그 `LIMIT`이 전부 무관한
    행으로 소진돼 더 오래된 진짜 비용 변경 이력이 응답에서 통째로
    사라졌다(`test_probe_cost_trend_limit_applied_before_relevance_
    filter`로 재현). `action IN (...)`만 걸고 전부 가져온 뒤 Python에서
    `cost_estimate` 유무로 거르고, 그 *다음*에 `limit`을 자른다 — 이
    저장소가 전제하는 소규모 팀 감사로그 규모에서는 전체 스캔 비용이
    허용 가능하다."""
    rows = conn.execute(
        "SELECT created_at, subject_type, subject_id, after_value FROM audit_log "
        "WHERE action IN ('agent_cost_updated', 'human_pool_updated') "
        "ORDER BY created_at DESC, id DESC",
    ).fetchall()
    trend = []
    for row in rows:
        after = json.loads(row["after_value"]) if row["after_value"] else {}
        if "cost_estimate" not in after:
            continue
        trend.append(
            {
                "created_at": row["created_at"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "cost_estimate": after["cost_estimate"],
            }
        )
        if len(trend) >= limit:
            break
    return trend


def workload(conn: sqlite3.Connection) -> list[dict]:
    """2026-10-08, Phase 4 — `GET
    /api/dashboard/workload`. 사람/Agent별 **진행 중**(`tasks.status
    = 'in_progress'`) Task 수. 새 집계 테이블 없이 confirmed 배정 +
    상태만으로 계산한다(이 파일 서두의 "이미 있는 데이터를 모아
    보여줄 뿐" 원칙 그대로)."""
    rows = conn.execute(
        """
        SELECT a.resource_type, a.resource_id,
               COALESCE(hp.name, ap.name, ap.role_name) AS name,
               COUNT(*) AS in_progress_count
        FROM assignments a
        JOIN tasks t ON t.id = a.task_id
        LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
        LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
        WHERE a.status = 'confirmed' AND t.status = 'in_progress'
          AND a.resource_type IN ('human', 'agent')
        GROUP BY a.resource_type, a.resource_id
        ORDER BY in_progress_count DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def bottlenecks(conn: sqlite3.Connection) -> list[dict]:
    """2026-10-08, Phase 4 — `GET
    /api/dashboard/bottlenecks`("주의 필요" 목록).

    설계 문서가 제안한 "마감 임박"은 이 스키마에 마감일 개념 자체가
    없어(상태/우선순위까지만 추가했다) 적용할 데이터가 없다 —
    대신 실제로 계산 가능한 두 신호로 좁혔다: (1) 의존관계상 아직
    안 끝난 선행 작업에 막혀 있는 task, (2) 결과물이 2회 이상 반려된
    task. `reason`이 둘 다 해당하면 `"blocked+rejected_twice"`로
    합쳐 하나의 행으로 낸다(같은 task가 목록에 중복으로 안 뜨게)."""
    blocked = conn.execute(
        """
        SELECT DISTINCT t.id, t.role_name, t.status, t.priority
        FROM tasks t
        JOIN task_dependencies td ON td.task_id = t.id
        JOIN tasks dep ON dep.id = td.depends_on_task_id
        WHERE t.status != 'done' AND dep.status != 'done'
        """
    ).fetchall()
    rejected = conn.execute(
        """
        SELECT t.id, t.role_name, t.status, t.priority, COUNT(*) AS rejected_count
        FROM tasks t
        JOIN deliverables d ON d.task_id = t.id
        WHERE d.status = 'rejected'
        GROUP BY t.id
        HAVING COUNT(*) >= 2
        """
    ).fetchall()
    result: dict[int, dict] = {}
    for row in blocked:
        entry = dict(row)
        entry["reasons"] = ["blocked"]
        result[row["id"]] = entry
    for row in rejected:
        if row["id"] in result:
            result[row["id"]]["reasons"].append("rejected_twice")
            result[row["id"]]["rejected_count"] = row["rejected_count"]
        else:
            entry = dict(row)
            entry["reasons"] = ["rejected_twice"]
            result[row["id"]] = entry
    return list(result.values())


# 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 1번 "Gate
# 점수 기반 자동 경고") — 연속 실패 몇 건부터 "주의 필요"로 볼지는 임의
# 기준이다. task 쪽 반려 기준(`bottlenecks()`의 "2회 이상")보다 한 단계
# 더 엄격하게 잡았다 — Agent 하나의 연속 실패는 task 하나의 반복 반려보다
# "이 Agent를 계속 써도 되는가"라는 더 구조적인 질문이라 판단.
_REPEATED_GATE_FAILURE_THRESHOLD = 3


def _has_failing_gate(gate_scores: dict) -> bool:
    return any(gate.get("passed") is False for gate in gate_scores.values())


def agents_with_repeated_gate_failures(
    conn: sqlite3.Connection, threshold: int = _REPEATED_GATE_FAILURE_THRESHOLD
) -> list[dict]:
    """최근 Gate 평가 `threshold`건이 전부 하나 이상의 Gate에서
    실패(passed=False)한 활성 Agent를 찾는다 — "이 Agent 계속 믿고 써도
    되는가"를 사람이 "AI 에이전트 관리" 화면을 일일이 열어보지 않아도
    대시보드에서 바로 보게 한다.

    평가 건수가 `threshold` 미만이면 판단하지 않는다(표본 부족 — 우연한
    실패 1~2건으로 단정하지 않는다). 측정 자체가 없는(not_measured)
    Gate는 "실패"로 안 친다 — `passed`가 명시적으로 `False`일 때만."""
    agent_rows = conn.execute(
        "SELECT id, role_name, name FROM agent_pool WHERE status = 'active'"
    ).fetchall()
    result = []
    for agent in agent_rows:
        rows = conn.execute(
            """
            SELECT ae.gate_scores FROM agent_evaluations ae
            JOIN deliverables d ON d.id = ae.deliverable_id
            WHERE d.author_type = 'agent' AND d.author_id = ?
            ORDER BY ae.id DESC LIMIT ?
            """,
            (agent["id"], threshold),
        ).fetchall()
        if len(rows) < threshold:
            continue
        if all(_has_failing_gate(json.loads(row["gate_scores"])) for row in rows):
            result.append(
                {
                    "agent_id": agent["id"],
                    "role_name": agent["name"] or agent["role_name"],
                    "recent_evaluation_count": threshold,
                }
            )
    return result


def summary(conn: sqlite3.Connection, user_id: int, role: str) -> dict:
    """`GET /api/dashboard` — 한 화면에 필요한 항목을 한 번에 담는다."""
    return {
        "team_gate_scores": team_average_gate_scores(conn),
        "pending_approval_count": len(approvals.list_inbox(conn, user_id, role)),
        "cost_trend": cost_trend(conn),
        "cost_snapshot": cost.report(conn),
        "workload": workload(conn),
        "bottlenecks": bottlenecks(conn),
        "agent_bottlenecks": agents_with_repeated_gate_failures(conn),
    }
