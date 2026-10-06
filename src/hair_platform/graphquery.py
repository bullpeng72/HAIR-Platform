"""GraphDB 기반 관계분석 Agent의 관계 질의 — ADR.md 결정 11(사용자 요청,
2026-09-26, 3차).

전용 그래프DB 서버(Neo4j 등)를 두지 않고, 이미 있는 관계형 데이터
(`users.manager_id`, `assignments`, `tasks`)를 그래프로 취급해 조회한다
(로컬/온프레미스 우선 원칙, SPEC.md §3 — 결정 5의 SQLite 중심 저장 정책과도
일관된다). 새 저장소를 추가하지 않는다. 실사용 규모·질의 복잡도가 커지면
`networkx`나 정식 그래프DB로 교체를 재검토한다(오픈 이슈 — ADR 결정 11).

**NF9 경계(중요, 실제로 코드에서 강제)**: ADR 결정 11은 관계분석 Agent가
F8 성과 데이터(`performance_score`·`performance_consent`)를 우회 노출할
위험을 지적했다 — 이 모듈의 모든 쿼리는 "누가 누구를 관리하는지"·"이
업무를 누가 맡았는지" 같은 **관계** 정보만 SELECT한다. 성과 관련 컬럼은
이 파일 어디에도 등장하지 않는다(회귀 테스트가 소스 자체를 검사해 이를
보장한다).
"""

from __future__ import annotations

import sqlite3


def manager_chain(
    conn: sqlite3.Connection, user_id: int, max_depth: int = 10
) -> list[dict]:
    """이 사람의 상사 → 그 상사의 상사 순으로 체인을 반환한다(순환 방지).

    `approvals.build_manager_chain`과 같은 순회 로직이지만, 이 모듈은 F7
    결재라인 자동라우팅이 아니라 "관계 질의" 자체가 목적이라 사람이 읽을
    관계 정보(username 포함)를 반환한다.
    """
    chain: list[dict] = []
    seen = {user_id}
    row = conn.execute(
        "SELECT manager_id FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    depth = 0
    while row and row["manager_id"] is not None and depth < max_depth:
        manager_id = row["manager_id"]
        if manager_id in seen:
            break
        manager_row = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (manager_id,)
        ).fetchone()
        if manager_row is None:
            break
        chain.append({"id": manager_row["id"], "username": manager_row["username"]})
        seen.add(manager_id)
        row = conn.execute(
            "SELECT manager_id FROM users WHERE id = ?", (manager_id,)
        ).fetchone()
        depth += 1
    return chain


def direct_reports(conn: sqlite3.Connection, manager_id: int) -> list[dict]:
    """이 사람에게 직접 보고하는 사람 목록(관계만 — 성과 데이터 없음)."""
    rows = conn.execute(
        "SELECT id, username FROM users WHERE manager_id = ?", (manager_id,)
    ).fetchall()
    return [{"id": r["id"], "username": r["username"]} for r in rows]


def task_assignment_owner(conn: sqlite3.Connection, task_id: int) -> dict | None:
    """이 Task를 실제로 담당하는(confirmed) 리소스와 요청자 관계를 반환한다."""
    row = conn.execute(
        "SELECT a.id AS assignment_id, a.resource_type, a.resource_id, "
        "a.requested_by, u.username AS requested_by_username "
        "FROM assignments a JOIN users u ON u.id = a.requested_by "
        "WHERE a.task_id = ? AND a.status = 'confirmed' ORDER BY a.id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "assignment_id": row["assignment_id"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "requested_by": row["requested_by"],
        "requested_by_username": row["requested_by_username"],
    }


def org_chart_summary(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """F5 조직도와 같은 데이터를 관계 목록으로 요약한다 — F8 성과 데이터는
    포함하지 않는다(NF9)."""
    rows = conn.execute(
        "SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id, "
        "a.requested_by, t.role_name FROM assignments a "
        "JOIN tasks t ON t.id = a.task_id "
        "WHERE a.status = 'confirmed' ORDER BY a.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def context_for_prompt(conn: sqlite3.Connection) -> str:
    """관계분석 Agent가 LLM 프롬프트에 넣을 관계 컨텍스트 텍스트를 만든다."""
    rows = org_chart_summary(conn)
    if not rows:
        return "현재 확정된 배정 관계가 없습니다."
    lines = [
        f"- 배정#{r['assignment_id']}: task#{r['task_id']}({r['role_name']}) -> "
        f"{r['resource_type']}#{r['resource_id']} (요청자 user#{r['requested_by']})"
        for r in rows
    ]
    return "현재 조직의 확정된 배정 관계:\n" + "\n".join(lines)


def as_crewai_tool(conn: sqlite3.Connection):
    """GraphDB 관계조회를 실제 CrewAI Tool로 감싼다(ADR 결정 12, 사용자 요청
    2026-09-27 — "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘"). NF9
    경계는 `context_for_prompt()` 자체가 성과 컬럼을 절대 SELECT하지 않는
    것으로 이미 보장된다 — Tool로 감싸도 호출하는 함수가 그대로이므로 이
    보장은 변하지 않는다(회귀 테스트 `test_gc86`이 계속 이 파일의 소스
    자체를 검사한다). `crewai`는 지연 import한다."""
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    # 사용자 보고(2026-10-06, 실제 크루 실행에서 "OpenAI function name
    # cannot be empty" 크래시) — 설치된 crewai 버전의 `sanitize_tool_name()`
    # 이 함수명을 ASCII로 정규화하며 비-ASCII 문자를 전부 버린다(`name.
    # encode("ascii", "ignore")`). 한글만으로 된 이름("조직도 관계 조회")은
    # 빈 문자열이 돼 즉시 예외가 난다 — Tool Pool 한글 이름은 그대로 두고
    # LLM에 실제로 전달되는 함수명만 ASCII로 바꾼다.
    @tool("org_relationship_query")
    def query_org_relationships() -> str:
        """조직도·직속상사·배정 현황 등 관계형 데이터를 조회한다(성과
        데이터는 포함하지 않는다). 관계 정보가 실제로 필요할 때만 호출하라."""
        return context_for_prompt(conn)

    return query_org_relationships
