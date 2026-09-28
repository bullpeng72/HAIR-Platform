"""협업 워크스페이스 — 결과물(Deliverable) 엔티티, "내 업무" 진입 흐름, 사람의
LLM 활용 업무 지원(사용자 요청, 2026-09-28, "HAIR의 핵심은 사람과 AI Agent가
협업하여 일하는 구조야... 사람이 한 결과물을 AI Agent에게 전달... 사람이
HITL/HOTL로 AI Agent 업무 감독 등이 가능해야해" → "팀이 승인되고... 해당
업무가 내 업무 메뉴에서 확인되고... 사람-에이전트협업 프로세스로 펼쳐지고
입출력을 처리할 수 있도록" → "사람이 하는 업무의 경우 단순 simple_llm 호출을
포함하고... skill을 사용할 수 있도록").

`docs/PHASE1_DELIVERABLE_SPEC.md`의 Phase 1 범위(결과물 엔티티 + 내 업무
진입 흐름 + Skill/Tool 역할 재정의)에 더해, Phase 2(산출물 승인 게이트,
사용자 요청 2026-09-28 "다음 단계 진행해줘")·Phase 3(Agent 평가 인라인
연결, 사용자 요청 2026-09-28 "Phase 3 진행해줘", 실제 측정 로직은
`agent_evaluation.py`에 분리)도 이 모듈에서 구현한다 — 비동기 실행
(Phase 4)만 범위 밖이다.

**author_id 규칙**: `author_type='human'`이면 `author_id`는
`human_pool.id`가 아니라 **`users.id`**다(실제로 글을 쓴 로그인 계정 —
`audit.record()`의 `actor_id` 관례와 통일). `'agent'`면 `agent_pool.id`.

**구현 시 스펙에서 조정한 것**: §2-6(execute-agent)은 스펙 초안엔
`crew_runtime.run_crew()`라고 적었으나, 그건 여러 Agent를 Crew로 묶는
함수라 "이 task에 배정된 Agent 하나를 실행"하는 이번 용도와 안 맞는다 —
이미 있는 단일 Agent 온디맨드 실행 경로 `agent_runtime.execute_agent()`
(Agent별 저장된 provider/model, RAG 증강을 그대로 재사용)로 구현한다.

**Phase 2 설계**: Agent가 만든 deliverable(`status='pending_review'`)이
생기는 즉시 `approvals.create_approval_chain()`으로 결재 체인을 만든다
(`requested_by`=execute-agent를 트리거한 사람, `subject_type=
'deliverable'`) — F7의 다단계 결재라인·ADR 3-A 자기승인 방지를 그대로
재사용한다(새 승인 로직을 따로 만들지 않는다). 사람이 작성한
deliverable(`'draft'`)은 승인 대상이 아니다 — Agent 산출물만 게이트를
탄다. "수정 후 재제출"(`revises_id`)은 Phase 2 스코프 밖으로 남긴다
(`docs/LIMITS.md` 참고).
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import agent_evaluation, agent_runtime, approvals, audit, llm


def _confirmed_assignments_for_task(
    conn: sqlite3.Connection, task_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM assignments WHERE task_id = ? AND status = 'confirmed'",
        (task_id,),
    ).fetchall()


def task_participant_relationship(
    conn: sqlite3.Connection, task_id: int, user_id: int
) -> str | None:
    """이 task의 confirmed 배정에서 user_id가 사람 당사자("assignee")이거나
    Agent 배정 요청자("requester")인지 판정한다. 둘 다 아니면 None —
    `human_pool.user_id`가 없는(로그인 계정 미연결) 사람은 구조적으로 여기
    걸리지 않는다(`docs/PHASE1_DELIVERABLE_SPEC.md` §1 "알려진 한계")."""
    for row in _confirmed_assignments_for_task(conn, task_id):
        if row["resource_type"] == "human":
            human = conn.execute(
                "SELECT user_id FROM human_pool WHERE id = ?", (row["resource_id"],)
            ).fetchone()
            if human is not None and human["user_id"] == user_id:
                return "assignee"
        elif row["resource_type"] == "agent" and row["requested_by"] == user_id:
            return "requester"
    return None


def list_my_work(
    conn: sqlite3.Connection, user_id: int, limit: int = 50, offset: int = 0
) -> list[dict]:
    """`GET /api/my-work`(§2-1) — 확정된 배정 중 user_id가 사람 당사자이거나
    Agent 배정 요청자인 task 목록. rbac 제약 없음(자기 관련 것만 나온다)."""
    rows = conn.execute(
        """
        SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
               t.brief_id, t.role_name,
               CASE WHEN a.resource_type = 'human' THEN 'assignee' ELSE 'requester' END
                   AS rel
        FROM assignments a
        JOIN tasks t ON t.id = a.task_id
        LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
        WHERE a.status = 'confirmed'
          AND (
            (a.resource_type = 'human' AND hp.user_id = ?)
            OR (a.resource_type = 'agent' AND a.requested_by = ?)
          )
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, user_id, limit, offset),
    ).fetchall()

    result = []
    for row in rows:
        agg = conn.execute(
            "SELECT COUNT(*) AS c, MAX(created_at) AS latest FROM deliverables "
            "WHERE task_id = ?",
            (row["task_id"],),
        ).fetchone()
        result.append(
            {
                "task_id": row["task_id"],
                "brief_id": row["brief_id"],
                "role_name": row["role_name"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "assignment_id": row["assignment_id"],
                "my_relationship": row["rel"],
                "deliverable_count": agg["c"],
                "latest_deliverable_at": agg["latest"],
            }
        )
    return result


def list_deliverables(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """`GET /api/tasks/{task_id}/deliverables`(§2-2) — 타임라인 순서(시간순).

    Phase 2: `pending_review`인 deliverable엔 지금 결정 가능한(`status=
    'pending'`) 결재 건의 id를 `approval_id`로 함께 내려준다 — 웹 UI가
    "결재" 탭으로 안 보내고 워크스페이스 안에서 바로 승인/반려하려면
    이 id가 필요하다. 다른 상태는 항상 `approval_id=None`.

    Phase 3: `author_type='agent'`인 deliverable엔 `evaluation`(Gate A-G
    점수 + TCR, 없으면 None)도 같이 내려준다 — 결재자가 승인/반려 전에
    측정치를 볼 수 있어야 한다(`docs/HAIR_MASTER_PLAN.md` §5)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    rows = conn.execute(
        "SELECT * FROM deliverables WHERE task_id = ? ORDER BY created_at ASC, id ASC",
        (task_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["approval_id"] = None
        if row["status"] == "pending_review":
            approval = conn.execute(
                "SELECT id FROM approvals WHERE subject_type = 'deliverable' "
                "AND subject_id = ? AND status = 'pending'",
                (row["id"],),
            ).fetchone()
            if approval is not None:
                item["approval_id"] = approval["id"]
        item["evaluation"] = (
            agent_evaluation.latest_evaluation(conn, row["id"])
            if row["author_type"] == "agent"
            else None
        )
        result.append(item)
    return result


def resolve_deliverable_decision(
    conn: sqlite3.Connection, deliverable_id: int, approve: bool
) -> dict:
    """Phase 2 — 산출물 승인/반려 결과를 `deliverables`에 반영한다(커밋은
    호출자 책임 — `app.py`의 `decide_approval()`이 `approvals` 갱신과 함께
    한 트랜잭션으로 커밋한다, `assignments.resolve_assignment_decision()`
    과 같은 관례)."""
    row = conn.execute(
        "SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if row is None:
        raise ValueError("deliverable_not_found")
    new_status = "approved" if approve else "rejected"
    conn.execute(
        "UPDATE deliverables SET status = ? WHERE id = ?", (new_status, deliverable_id)
    )
    return {"deliverable_id": deliverable_id, "status": new_status}


def create_deliverable(
    conn: sqlite3.Connection, task_id: int, actor_id: int, content: str
) -> int:
    """`POST /api/tasks/{task_id}/deliverables`(§2-3) — 그 task의 사람
    당사자/Agent 요청자 본인만 작성할 수 있다(대리 작성 금지 — 관리자/매니저도
    이 함수로는 남 대신 못 쓴다, `app.py`도 role 우회를 안 줌)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if not content or not content.strip():
        raise ValueError("empty_content")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")

    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, status) "
        "VALUES (?, 'human', ?, ?, 'draft')",
        (task_id, actor_id, content),
    )
    deliverable_id = cur.lastrowid
    assert deliverable_id is not None
    audit.record(
        conn,
        actor_id,
        "deliverable_created",
        "deliverables",
        deliverable_id,
        after={"task_id": task_id, "author_type": "human"},
    )
    conn.commit()
    return deliverable_id


def list_comments(conn: sqlite3.Connection, deliverable_id: int) -> list[dict]:
    """`GET /api/deliverables/{id}/comments`(§2-4)."""
    d = conn.execute(
        "SELECT id FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if d is None:
        raise ValueError("deliverable_not_found")
    rows = conn.execute(
        "SELECT * FROM deliverable_comments WHERE deliverable_id = ? "
        "ORDER BY created_at ASC, id ASC",
        (deliverable_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_comment(
    conn: sqlite3.Connection, deliverable_id: int, actor_id: int, content: str
) -> int:
    """`POST /api/deliverables/{id}/comments`(§2-4) — 부모 task 기준으로
    참가자 판정(§2-2)을 재사용한다. Phase 1에서는 항상 author_type='human'
    (Agent가 댓글을 남기는 경로는 없음)."""
    d = conn.execute(
        "SELECT task_id FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if d is None:
        raise ValueError("deliverable_not_found")
    if not content or not content.strip():
        raise ValueError("empty_content")
    if task_participant_relationship(conn, d["task_id"], actor_id) is None:
        raise PermissionError("not_a_participant")

    cur = conn.execute(
        "INSERT INTO deliverable_comments (deliverable_id, author_type, author_id, "
        "content) VALUES (?, 'human', ?, ?)",
        (deliverable_id, actor_id, content),
    )
    comment_id = cur.lastrowid
    assert comment_id is not None
    audit.record(
        conn,
        actor_id,
        "deliverable_comment_created",
        "deliverable_comments",
        comment_id,
        after={"deliverable_id": deliverable_id},
    )
    conn.commit()
    return comment_id


def _skill_grounded_system_prompt(
    conn: sqlite3.Connection, role_name: str, required_skills: list[str]
) -> str:
    """§4 — Agent의 skill_tags 매칭과 같은 조회 로직(`skill_pool WHERE name
    IN (...)`)을 재사용하되, 도구를 붙이는 대신 지식 텍스트를 프롬프트에
    주입한다."""
    rows: list[sqlite3.Row] = []
    if required_skills:
        placeholders = ",".join("?" * len(required_skills))
        rows = conn.execute(
            f"SELECT description FROM skill_pool WHERE name IN ({placeholders})",
            required_skills,
        ).fetchall()
    knowledge = "\n".join(f"- {r['description']}" for r in rows if r["description"])
    base = llm.system_prompt_for_human_assist(role_name, required_skills)
    if knowledge:
        return base + "\n\n참고 지식:\n" + knowledge
    return base


def assist(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    draft_context: str | None,
    provider: str | None,
    model: str | None,
) -> str:
    """`POST /api/tasks/{task_id}/assist`(§2-5) — "LLM으로 도와줘". 결과는
    저장하지 않는다(사람이 검토·편집 후 `create_deliverable()`로 별도
    제출). provider/model이 팀원 요청인지 확인하는 RBAC은 `app.py`가
    한다(이 함수는 role을 모른다 — 참가자 여부만 확인)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")

    required_skills = json.loads(task["required_skills"])
    system = _skill_grounded_system_prompt(conn, task["role_name"], required_skills)
    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    prompt = draft_context or (
        f"'{task['role_name']}' 업무를 처음 시작합니다. 초안을 작성해 주세요."
    )
    return llm.call_llm(resolved_provider, resolved_model, system, prompt)


def execute_agent_for_task(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    provider: str | None,
    model: str | None,
    allow_supervisor_override: bool = False,
) -> int:
    """`POST /api/tasks/{task_id}/execute-agent`(§2-6) — 이 task의 confirmed
    agent 배정을 찾아 실행한다. requester 본인이거나(`allow_supervisor_
    override=False`) 매니저/관리자(`True`, `app.py`가 role로 판단)면 통과.
    가장 최근 사람 작성 deliverable을 Task 입력에 포함시킨다(§1 "출력
    처리" — 이게 "결과물 전달" 갭을 실제로 메우는 지점).

    Phase 2: 결과 deliverable마다 결재 체인을 새로 만든다(`requested_by`
    =이 함수를 호출한 `actor_id` — 감독자 대리 실행이었다면 그 감독자가
    자기 트리거의 승인자가 될 수 없다, ADR 3-A와 같은 원리)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    rel = task_participant_relationship(conn, task_id, actor_id)
    if rel != "requester" and not allow_supervisor_override:
        raise PermissionError("not_a_participant")

    agent_assignment = conn.execute(
        "SELECT * FROM assignments WHERE task_id = ? AND status = 'confirmed' "
        "AND resource_type = 'agent'",
        (task_id,),
    ).fetchone()
    if agent_assignment is None:
        raise LookupError("no_confirmed_agent_assignment")
    agent_id = agent_assignment["resource_id"]

    latest_human = conn.execute(
        "SELECT content FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    required_skills = json.loads(task["required_skills"])
    prompt_parts = [
        (
            f"'{task['role_name']}' 업무를 수행하세요"
            f"(필요 스킬: {', '.join(required_skills) if required_skills else '없음'})."
        )
    ]
    if latest_human is not None:
        prompt_parts.append(f"참고할 사람 작성 자료:\n{latest_human['content']}")
    prompt = "\n\n".join(prompt_parts)

    # Phase 3 — 실제 LLM 호출을 agent_evaluation.run_and_score()로 감싸 Gate
    # A-G를 그 자리에서 측정한다(docs/HAIR_MASTER_PLAN.md §5). 호출이
    # 실패하면 여기서 예외가 그대로 전파되고, deliverable은 아직 INSERT되지
    # 않은 상태다 — 기존 실패 동작(아무 것도 안 남는다)과 동일하게 유지한다.
    eval_result = agent_evaluation.run_and_score(
        task["role_name"],
        lambda: agent_runtime.execute_agent(
            conn, agent_id, actor_id, prompt, provider, model
        ),
    )

    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, status) "
        "VALUES (?, 'agent', ?, ?, 'pending_review')",
        (task_id, agent_id, eval_result["output"]),
    )
    deliverable_id = cur.lastrowid
    assert deliverable_id is not None
    agent_evaluation.record_evaluation(
        conn, deliverable_id, eval_result["gate_scores"], eval_result["tcr"]
    )
    approvals.create_approval_chain(conn, actor_id, "deliverable", deliverable_id)
    audit.record(
        conn,
        actor_id,
        "deliverable_created",
        "deliverables",
        deliverable_id,
        after={
            "task_id": task_id,
            "author_type": "agent",
            "agent_id": agent_id,
            "gate_scores": eval_result["gate_scores"],
            "tcr": eval_result["tcr"],
        },
    )
    conn.commit()
    return deliverable_id
