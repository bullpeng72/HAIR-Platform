"""매니저 대시보드 — Phase 5(사용자 요청, 2026-09-28, "Phase 5(기업용
UI/UX)에 포함할 항목을 고르세요" → "매니저 대시보드" 선택,
`docs/HAIR_MASTER_PLAN.md` §6 "팀 Agent 평균 Gate 점수, 대기 승인
건수, 비용 추이를 한 화면에"). 이미 있는 데이터를 모아 보여줄 뿐, 새
집계 테이블을 만들지 않는다.

**"비용 추이" 실측 확인**: 처음엔 시계열 비용 데이터가 전혀 없다고
생각했다(`cost.py`의 `cost_estimate`는 그때그때 값 하나뿐이라). 하지만
`app.py`의 `agent_cost_updated`(F10 비용 입력)·`human_pool_updated`
(사람 정보 수정, cost_estimate 포함) 감사로그 액션이 이미 `after_value`
에 변경된 cost_estimate 값과 `audit_log.created_at` 타임스탬프를 갖고
있다는 걸 실측으로 확인했다 — 새 계측을 추가할 필요 없이 이 이력을
그대로 "추이"로 쓴다. 단, 이 DB를 새로 부트스트랩했고 지금까지 아무도
비용을 바꾼 적이 없으면 이력이 비어있을 수 있다(정직한 한계 — 과거로
소급해 데이터를 만들어낼 방법은 없다. `docs/LIMITS.md` 참고).
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


def summary(conn: sqlite3.Connection, user_id: int, role: str) -> dict:
    """`GET /api/dashboard` — 한 화면에 필요한 3종을 한 번에 담는다."""
    return {
        "team_gate_scores": team_average_gate_scores(conn),
        "pending_approval_count": len(approvals.list_inbox(conn, user_id, role)),
        "cost_trend": cost_trend(conn),
        "cost_snapshot": cost.report(conn),
    }
