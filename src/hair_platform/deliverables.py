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
`agent_evaluation.py`에 분리)·Phase 4(execute-agent 비동기화, 사용자
요청 2026-09-28 "Phase 4 진행해줘")도 이 모듈에서 구현한다.

**Phase 4 설계**: `execute_agent_for_task()`(동기, 응답까지 LLM 호출을
기다림) 하나였던 것을 `start_agent_execution()`(검증+프롬프트 구성만
동기, `agent_execution_jobs`에 `status='running'` 행을 만들고 즉시
반환)과 `run_agent_execution_job()`(실제 LLM 호출+평가+deliverable
생성, `app.py`가 FastAPI `BackgroundTasks`로 응답 전송 후 실행)으로
쪼갰다. 원래 설계(`docs/COLLABORATION_PLATFORM_PLAN.md` Phase C)는
`crew_runtime.run_crew()`(다중 Task Crew)를 대상으로 CrewAI
`register_before_tool_call_hook()` 기반 도구 호출 단위 일시정지까지
그렸지만, 실제로 협업 워크스페이스가 쓰는 경로는 단일 LLM 호출인
`agent_runtime.execute_agent()`라 "실행 중간에 멈출 지점"이 없다 — 그래서
사용자 결정(2026-09-28)에 따라 스코프를 "비동기 실행+상태 폴링"으로
좁혔다. 도구 호출 단위 일시정지는 `crew_runtime.run_crew()`용으로 남겨둔
별도 항목이다(`docs/LIMITS.md` §25, `docs/COLLABORATION_PLATFORM_PLAN.md`
Phase C).

**`deliverables.status`에 'generating' 상태를 추가하지 않은 이유**:
SQLite는 기존 테이블의 CHECK 제약을 `ALTER TABLE`로 바꿀 수 없다(테이블
재생성만 가능) — 이미 배포된 DB 파일(`hair.db`)의 `deliverables.status`
CHECK을 안전하게 넓힐 방법이 없어, 진행 중 상태는 완전히 새 테이블
`agent_execution_jobs`(`CREATE TABLE IF NOT EXISTS`라 기존 DB에도 안전하게
추가됨)로 분리했다. 완료되면 정상적으로 `deliverables`에 `'pending_review'`
행이 생긴다 — 기존 스키마·API 계약은 그대로다.

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
deliverable(`'draft'`)은 자동으로는 승인 대상이 아니다 — Agent 산출물만
생성 즉시 게이트를 탄다. "수정 후 재제출"(`revises_id`)은 Phase 2
스코프 밖으로 남긴다(`docs/LIMITS.md` 참고).

**Phase 2 후속(2026-09-29, 라이브 시나리오 실행에서 발견, `docs/LIMITS.md`
§33-34)**: 사람 deliverable이 'draft'에 영영 머물고 완료 판정 경로가
전혀 없다는 갭이 드러나, `submit_deliverable()`로 본인이 명시적으로
"검토해달라"고 제출하면 그때 같은 결재 체인이 붙도록 했다 — Agent
경로와 대칭이지만 자동이 아니라 사람이 스스로 트리거해야 한다는 점만
다르다.
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


def submit_deliverable(
    conn: sqlite3.Connection, deliverable_id: int, actor_id: int
) -> dict:
    """`POST /api/deliverables/{id}/submit` — 라이브 시나리오 실행에서 드러난
    갭(`docs/LIMITS.md` §33)을 메운다: 사람이 작성한 deliverable은 지금까지
    'draft'에 영영 머물렀고 완료 판정 경로가 API에 전혀 없었다. Agent
    산출물이 이미 쓰는 결재 체인(`approvals.create_approval_chain()`)을
    그대로 재사용해 "다 됐으니 검토해달라"는 명시적 제출을 추가한다 — 새
    승인 로직을 만들지 않는다(모듈 docstring의 Phase 2 원칙 그대로 유지).

    본인이 작성한 'draft' 상태의 human deliverable만 제출할 수 있다(대리
    제출 금지 — `create_deliverable()`의 참가자 본인 원칙과 통일). Agent
    산출물은 생성 즉시 자동으로 결재 체인이 붙으므로 이 함수의 대상이
    아니다."""
    row = conn.execute(
        "SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if row is None:
        raise ValueError("deliverable_not_found")
    if row["author_type"] != "human":
        raise ValueError("not_human_deliverable")
    if row["status"] != "draft":
        raise ValueError("not_in_draft_status")
    if row["author_id"] != actor_id:
        raise PermissionError("not_the_author")

    conn.execute(
        "UPDATE deliverables SET status = 'pending_review' WHERE id = ?",
        (deliverable_id,),
    )
    approvals.create_approval_chain(conn, actor_id, "deliverable", deliverable_id)
    audit.record(
        conn,
        actor_id,
        "deliverable_submitted",
        "deliverables",
        deliverable_id,
        after={"status": "pending_review"},
    )
    conn.commit()
    return {"deliverable_id": deliverable_id, "status": "pending_review"}


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


def start_agent_execution(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    allow_supervisor_override: bool = False,
) -> dict:
    """`POST /api/tasks/{task_id}/execute-agent`(§2-6, Phase 4로 비동기화) —
    이 task의 confirmed agent 배정을 찾아 검증·프롬프트 구성까지만
    동기로 마치고 `agent_execution_jobs`에 `status='running'` 행을 만든
    뒤 즉시 반환한다. 실제 LLM 호출은 `run_agent_execution_job()`이
    `app.py`의 `BackgroundTasks`로 응답 전송 후 처리한다 — 요청자 본인
    이거나(`allow_supervisor_override=False`) 매니저/관리자(`True`)면
    통과. 가장 최근 사람 작성 deliverable을 Task 입력에 포함시킨다(§1
    "출력 처리" — "결과물 전달" 갭을 메우는 지점).

    provider/model은 여기서 다루지 않는다 — `app.py`가 요청에서 받은
    값을 검증 없이 그대로 들고 있다가 `run_agent_execution_job()`에
    직접 넘긴다(이 함수를 거칠 이유가 없는 순수 통과값).

    반환값의 `agent_id`/`role_name`/`prompt`는 DB에 다시 저장하지 않고
    `app.py`가 그대로 `run_agent_execution_job()`에 넘긴다 — 새 컬럼을
    안 늘리려는 선택."""
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

    # 실측으로 발견한 갭(2026-09-28, 리뷰 2회차) — 이 함수는 원래 같은
    # task에 이미 'running' job이 있어도 확인 없이 새 job을 또 만들었다.
    # Web UI는 버튼을 비활성화해 막지만(§25), 빠른 연속 클릭이나 여러
    # 탭/직접 API 호출은 이 검증을 우회한다 — 그러면 같은 트리거로 두
    # 번의 실제 LLM 호출(비용 이중 지출)과 두 개의 독립된 deliverable+
    # 결재 체인이 생긴다. Web UI 가드에만 기대지 않고 서버 쪽에서도
    # 막는다.
    already_running = conn.execute(
        "SELECT id FROM agent_execution_jobs WHERE task_id = ? AND status = 'running'",
        (task_id,),
    ).fetchone()
    if already_running is not None:
        raise LookupError("agent_already_running")

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

    cur = conn.execute(
        "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
        "VALUES (?, ?, 'running')",
        (task_id, actor_id),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(
        conn,
        actor_id,
        "agent_execution_started",
        "agent_execution_jobs",
        job_id,
        after={"task_id": task_id, "agent_id": agent_id},
    )
    conn.commit()
    return {
        "job_id": job_id,
        "agent_id": agent_id,
        "role_name": task["role_name"],
        "prompt": prompt,
    }


def run_agent_execution_job(
    conn: sqlite3.Connection,
    job_id: int,
    task_id: int,
    agent_id: int,
    actor_id: int,
    role_name: str,
    prompt: str,
    provider: str | None,
    model: str | None,
) -> None:
    """`start_agent_execution()`이 반환한 job을 실제로 실행한다 —
    `app.py`가 `BackgroundTasks.add_task()`로 등록해 HTTP 응답을 보낸
    *뒤에* 실행되므로, 여기서 예외를 밖으로 던져도 받아줄 호출자가 없다
    (starlette가 조용히 로그만 남기고 삼킨다). 그래서 이 함수는 실패까지
    포함해 모든 결과를 `agent_execution_jobs` 행에 직접 기록한다 — Phase
    2/3의 동기 경로가 "예외를 그대로 전파해 아무 것도 안 남긴다"였던
    것과 달리, 비동기 경로는 실패도 사람이 UI에서 볼 수 있게 저장하는
    쪽이 맞다(HOTL — 실행이 죽었는지조차 모르면 감독이 안 된다)."""
    try:
        # Phase 3 — 실제 LLM 호출을 agent_evaluation.run_and_score()로
        # 감싸 Gate A-G를 그 자리에서 측정한다(docs/HAIR_MASTER_PLAN.md
        # §5).
        eval_result = agent_evaluation.run_and_score(
            role_name,
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
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'succeeded', deliverable_id = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (deliverable_id, job_id),
        )
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
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계, 위 docstring 참고
        conn.rollback()
        friendly_error = _friendly_execution_error(exc)
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'failed', error = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (friendly_error, job_id),
        )
        # 실측으로 발견한 갭(리뷰 3회차) — F13("모든 쓰기 액션... 조회")
        # 관례상 `decide_approval()`은 approved/rejected 둘 다 감사로그에
        # 남기는데(`f"approval_{new_status}"`), 이 실패 경로만 감사로그가
        # 없었다 — HOTL 감독자가 "이 Agent 실행이 왜/언제 실패했는지"를
        # F13 화면에서 못 보고 있었다. 성공 경로(`deliverable_created`)와
        # 대칭으로 남긴다.
        audit.record(
            conn,
            actor_id,
            "agent_execution_failed",
            "agent_execution_jobs",
            job_id,
            after={"task_id": task_id, "agent_id": agent_id, "error": friendly_error},
        )
        conn.commit()


# 실측으로 발견한 갭(리뷰 2회차) — start_agent_execution()이 확인한
# 시점엔 확정 배정·활성 Agent였어도, 실제 백그라운드 실행 전에
# 관리자가 Agent를 삭제/오프보딩하면 agent_runtime.execute_agent()가
# 내부 코드(예: "agent_not_active")를 그대로 던진다. 그게 여과 없이
# Web UI에 노출되던 걸 사람이 읽을 수 있는 문구로 바꿔준다. 목록에
# 없는 예외(실제 LLM API 오류 등)는 원문 그대로 보여준다 — 그쪽은
# 이미 사람이 읽을 수 있는 메시지이기 때문이다.
_FRIENDLY_ERROR_MESSAGES = {
    "agent_not_found": "Agent를 찾을 수 없습니다(삭제됐을 수 있습니다)",
    "agent_not_active": "Agent가 더 이상 활성 상태가 아닙니다(오프보딩됐을 수 있습니다)",
}


def _friendly_execution_error(exc: Exception) -> str:
    return _FRIENDLY_ERROR_MESSAGES.get(str(exc), str(exc))


def list_execution_jobs(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """`GET /api/tasks/{task_id}/agent-execution-jobs` — Web UI가 폴링해
    "생성 중…"/실패 상태를 타임라인에 얹는다. 최신순(가장 최근 실행이
    맨 위)."""
    rows = conn.execute(
        "SELECT * FROM agent_execution_jobs WHERE task_id = ? "
        "ORDER BY created_at DESC, id DESC",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def reap_orphaned_execution_jobs(conn: sqlite3.Connection) -> int:
    """**실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
    개선해줘" 리뷰 중)**: `status='running'`인 job은 프로세스가 죽기
    전엔 절대 그 상태로 남을 이유가 없다(`run_agent_execution_job()`이
    항상 `succeeded`/`failed`로 마무리한다) — 그런데 서버 프로세스가
    실행 도중 죽으면(배포 재시작, 크래시) 그 job은 영원히 `running`으로
    남고, Web UI는 그 task의 "AI 에이전트 실행" 버튼을 계속 비활성화한
    채로 막아버린다(재시작해도 절대 안 풀림 — 새로고침을 아무리 해도
    복구 불가능한 UI 먹통 상태였다). `docs/LIMITS.md` §3이 이미 이
    앱을 "단일 프로세스" 전제로 명시하므로, `create_app()` 기동 시점에
    `running`으로 남은 job은 전부 이전 프로세스의 잔재로 확정할 수
    있다 — 그래서 앱 기동 시 이 함수로 전부 `failed`로 정리한다."""
    rows = conn.execute(
        "SELECT id FROM agent_execution_jobs WHERE status = 'running'"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'failed', "
            "error = '서버가 재시작되어 실행이 중단되었습니다', "
            "finished_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
    if rows:
        conn.commit()
    return len(rows)
