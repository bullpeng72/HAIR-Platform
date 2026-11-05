"""협업 워크스페이스 — 결과물(Deliverable) 엔티티, "내 업무" 진입 흐름, 사람의
LLM 활용 업무 지원(사용자 요청, 2026-09-28, "HAIR의 핵심은 사람과 AI Agent가
협업하여 일하는 구조야... 사람이 한 결과물을 AI Agent에게 전달... 사람이
HITL/HOTL로 AI Agent 업무 감독 등이 가능해야해" → "팀이 승인되고... 해당
업무가 내 업무 메뉴에서 확인되고... 사람-에이전트협업 프로세스로 펼쳐지고
입출력을 처리할 수 있도록" → "사람이 하는 업무의 경우 단순 simple_llm 호출을
포함하고... skill을 사용할 수 있도록").

Phase 1 범위(결과물 엔티티 + 내 업무
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
쪼갰다. 원래 설계(Phase C)는
`crew_runtime.run_crew()`(다중 Task Crew)를 대상으로 CrewAI
`register_before_tool_call_hook()` 기반 도구 호출 단위 일시정지까지
그렸지만, 실제로 협업 워크스페이스가 쓰는 경로는 단일 LLM 호출인
`agent_runtime.execute_agent()`라 "실행 중간에 멈출 지점"이 없다 — 그래서
사용자 결정(2026-09-28)에 따라 스코프를 "비동기 실행+상태 폴링"으로
좁혔다. 도구 호출 단위 일시정지는 `crew_runtime.run_crew()`용으로 남겨둔
별도 항목이다(Phase C).

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

**구현 시 스펙에서 조정한 것**: execute-agent는 스펙 초안엔
`crew_runtime.run_crew()`라고 적었으나, 그건 여러 Agent를 Crew로 묶는
함수라 "이 task에 배정된 Agent 하나를 실행"하는 이번 용도와 안 맞는다 —
이미 있는 단일 Agent 온디맨드 실행 경로 `agent_runtime.execute_agent()`
(Agent별 저장된 provider/model, RAG 증강을 그대로 재사용)로 구현한다.

**Phase 2 설계**: Agent가 만든 deliverable(`status='pending_review'`)이
생기는 즉시 `approvals.create_approval_chain()`으로 결재 체인을 만든다
(`requested_by`=execute-agent를 트리거한 사람, `subject_type=
'deliverable'`) — 다단계 결재라인·자기승인 방지를 그대로
재사용한다(새 승인 로직을 따로 만들지 않는다). 사람이 작성한
deliverable(`'draft'`)은 자동으로는 승인 대상이 아니다 — Agent 산출물만
생성 즉시 게이트를 탄다. "수정 후 재제출"(`revises_id`)은 Phase 2
스코프 밖으로 남긴다.

**Phase 2 후속(2026-09-29, 라이브 시나리오 실행에서 발견)**: 사람
deliverable이 'draft'에 영영 머물고 완료 판정 경로가
전혀 없다는 갭이 드러나, `submit_deliverable()`로 본인이 명시적으로
"검토해달라"고 제출하면 그때 같은 결재 체인이 붙도록 했다 — Agent
경로와 대칭이지만 자동이 아니라 사람이 스스로 트리거해야 한다는 점만
다르다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import (
    agent_evaluation,
    agent_runtime,
    approvals,
    audit,
    content_validators,
    crew_history,
    crew_runtime,
    llm,
    pool_registry,
    teams,
)


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
    걸리지 않는다(알려진 한계).

    사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) —
    `resource_type='team'`도 판정한다: 그 팀의 **리더**("team_leader")
    이거나 **사람 멤버**("team_member", 로그인 계정 연결된 경우만 —
    위와 같은 한계)면 참가자다. 이 함수는 이미 "모든 confirmed 배정을
    순회"하는 루프라(다중 confirmed 지원 전부터 그랬다), 사람/Agent
    배정이 여러 건 공존해도 **수정 없이 그대로** 전부 판정한다 —
    "복수의 사람 + 복수의 Agent"를 지원하려고 이 함수를 따로 고칠
    필요가 없었다."""
    for row in _confirmed_assignments_for_task(conn, task_id):
        if row["resource_type"] == "human":
            human = conn.execute(
                "SELECT user_id FROM human_pool WHERE id = ?", (row["resource_id"],)
            ).fetchone()
            if human is not None and human["user_id"] == user_id:
                return "assignee"
        elif row["resource_type"] == "agent" and row["requested_by"] == user_id:
            return "requester"
        elif row["resource_type"] == "team":
            if row["team_leader_id"] == user_id:
                return "team_leader"
            member = conn.execute(
                """
                SELECT 1 FROM team_members tm
                JOIN human_pool hp ON hp.id = tm.member_id AND tm.member_type = 'human'
                WHERE tm.team_id = ? AND hp.user_id = ?
                """,
                (row["resource_id"], user_id),
            ).fetchone()
            if member is not None:
                return "team_member"
    return None


def can_comment_on_task(conn: sqlite3.Connection, task_id: int, actor_id: int) -> bool:
    """2026-10-08, Phase 2 — 댓글
    작성·열람 권한은 `task_participant_relationship()`보다 넓다: 결재
    없이 "초대"만 된 사람도 댓글은 달 수 있다(결과물 작성·실행 트리거는
    여전히 기존 참가자 판정만 통과시킨다 — "초대만으로
    작성자 자격이 생기진 않는다" 원칙 유지, `task_participant_
    relationship()` 자체는 손대지 않는다). Agent 초대는 이 함수의
    대상이 아니다(Agent는 댓글을 사람처럼 "달 수 있는지" 판정받는
    주체가 아니라 Phase 3에서 트리거되는 쪽이다)."""
    if task_participant_relationship(conn, task_id, actor_id) is not None:
        return True
    row = conn.execute(
        "SELECT 1 FROM task_participants WHERE task_id = ? "
        "AND participant_type = 'human' AND participant_id = ?",
        (task_id, actor_id),
    ).fetchone()
    return row is not None


def invite_participant(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    participant_type: str,
    participant_id: int,
) -> dict:
    """`POST /api/tasks/{task_id}/participants`(Phase 2) — 결재가
    필요 없는 가벼운 초대. 누가 초대할 수 있는지(admin/manager 또는
    기존 참가자)는 RBAC role 분기라 `app.py` 라우트에서 판정한다(이
    파일의 기존 관례 — 참가자 전용 판정은 여기, role 분기는 라우트)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if participant_type == "agent":
        found = conn.execute(
            "SELECT id FROM agent_pool WHERE id = ?", (participant_id,)
        ).fetchone()
        if found is None:
            raise ValueError("agent_not_found")
    else:
        found = conn.execute(
            "SELECT id FROM users WHERE id = ?", (participant_id,)
        ).fetchone()
        if found is None:
            raise ValueError("user_not_found")
    try:
        cur = conn.execute(
            "INSERT INTO task_participants (task_id, participant_type, "
            "participant_id, invited_by) VALUES (?, ?, ?, ?)",
            (task_id, participant_type, participant_id, actor_id),
        )
    except sqlite3.IntegrityError:
        raise ValueError("already_invited") from None
    participant_row_id = cur.lastrowid
    assert participant_row_id is not None
    audit.record(
        conn,
        actor_id,
        "task_participant_invited",
        "task_participants",
        participant_row_id,
        after={
            "task_id": task_id,
            "participant_type": participant_type,
            "participant_id": participant_id,
        },
    )
    conn.commit()
    return {
        "id": participant_row_id,
        "task_id": task_id,
        "participant_type": participant_type,
        "participant_id": participant_id,
        "invited_by": actor_id,
    }


def list_participants(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """`GET /api/tasks/{task_id}/participants`(Phase 2).

    `display_name`(Phase 3 추가) — Agent는 `agent_pool.name`(지정한
    이름이 있으면 그걸, 없으면 `role_name` — 댓글 `@멘션`에 실제로 써야
    하는 정확한 문자열, `detect_mentioned_agents()`와 일치해야 하므로
    그대로 노출), 사람은 `users.username`. `GET /api/agent-pool`은
    admin/manager 전용이라 일반 참가자는 멘션 대상의 정확한 이름을 알
    방법이 이것뿐이다 — 참가자 카드에서 그대로 보여주기 위해 여기서
    조인해 내려준다.

    사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
    (채팅 포함)에도 활용될 수 있도록")로 `name`을 우선한다."""
    rows = conn.execute(
        "SELECT * FROM task_participants WHERE task_id = ? "
        "ORDER BY invited_at ASC, id ASC",
        (task_id,),
    ).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        if entry["participant_type"] == "agent":
            agent = conn.execute(
                "SELECT role_name, name FROM agent_pool WHERE id = ?",
                (entry["participant_id"],),
            ).fetchone()
            entry["display_name"] = (
                (agent["name"] or agent["role_name"]) if agent else None
            )
        else:
            user = conn.execute(
                "SELECT username FROM users WHERE id = ?", (entry["participant_id"],)
            ).fetchone()
            entry["display_name"] = user["username"] if user else None
        result.append(entry)
    return result


def task_completion_summary(conn: sqlite3.Connection, task_id: int) -> dict:
    """업무 하나의 결과물 진행 상태를 한 줄로 요약한다 — 사용자 요청
    (2026-09-29, "각 업무의 결과물을 어떻게 확인하는지... 분석하고
    부족한 기능을 개선해줘")에서 드러난 갭을 메운다: 지금까지 "내 업무"
    목록·브리프의 할 일 목록 둘 다 deliverable_count/최근 시각만 보여줄
    뿐 "지금 상태가 뭔지"·"최종 결과물(승인됨)이 나왔는지"는 업무마다
    협업 워크스페이스를 직접 열어 전체 타임라인을 끝까지 스캔해야만
    알 수 있었다. `list_my_work()`·브리프 할 일 목록(`app.py`) 양쪽에서
    재사용한다(같은 집계 로직을 두 번 만들지 않는다)."""
    count_row = conn.execute(
        "SELECT COUNT(*) AS c FROM deliverables WHERE task_id = ?", (task_id,)
    ).fetchone()
    latest_row = conn.execute(
        "SELECT status, created_at FROM deliverables WHERE task_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    approved_row = conn.execute(
        "SELECT 1 FROM deliverables WHERE task_id = ? AND status = 'approved' LIMIT 1",
        (task_id,),
    ).fetchone()
    return {
        "deliverable_count": count_row["c"],
        "latest_deliverable_at": latest_row["created_at"] if latest_row else None,
        "latest_status": latest_row["status"] if latest_row else None,
        "has_final_result": approved_row is not None,
    }


def list_all_work(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[dict]:
    """`GET /api/all-work`(2026-10-12) — 할 일 보드가 지금까지
    "내 업무"(`list_my_work()`, user_id로 좁힘)만 보여줄 수 있어 admin/
    manager가 조직 전체 현황을 한 보드에서 못 봤다("진짜 갭"). 같은
    `tasks`+`assignments` 조인·같은 `task_completion_summary()`를 그대로
    쓰고, `WHERE user_id = ?` 좁힘만 뺐다 — 새 쿼리 로직을 만들지 않는다.
    ADMIN/MANAGER 전용(app.py에서 role 체크, 조직 전체 데이터라 민감)."""
    rows = conn.execute(
        """
        SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
               t.brief_id, t.role_name
        FROM assignments a
        JOIN tasks t ON t.id = a.task_id
        WHERE a.status = 'confirmed'
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    result = []
    for row in rows:
        summary = task_completion_summary(conn, row["task_id"])
        result.append(
            {
                "task_id": row["task_id"],
                "brief_id": row["brief_id"],
                "role_name": row["role_name"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "assignment_id": row["assignment_id"],
                "my_relationship": None,  # "내 업무"가 아니므로 관계 라벨 없음
                **summary,
            }
        )
    return result


def list_my_work(
    conn: sqlite3.Connection, user_id: int, limit: int = 50, offset: int = 0
) -> list[dict]:
    """`GET /api/my-work` — 확정된 배정 중 user_id가 사람 당사자이거나
    Agent 배정 요청자인 task 목록. rbac 제약 없음(자기 관련 것만 나온다).

    실측으로 발견한 갭(2026-10-07, 시나리오 5를 "팀 Crew
    그룹 실행"만으로 재작성해 라이브 실행하다 발견 — 개별 사람/Agent
    배정 없이 **팀 배정 하나만** 있는 task는 team_leader/team_member
    둘 다 "내 업무"에 전혀 안 떴다) — 이 함수는 `resource_type='team'`
    을 아예 모르고 'human'/'agent' 두 경우만 봤다. `task_participant_
    relationship()`(워크스페이스 접근 권한 판정, 바로 위 함수)은 Phase
    1+2 때 이미 team_leader/team_member를 인식하도록 고쳤는데, "내 업무"
    **목록** 조회는 완전히 별개의 쿼리라 그때 같이 안 고쳐졌다 — 지금까지
    모든 팀 배정 시나리오가 개별 배정도 같이 써서 우연히 가려져 있었다."""
    rows = conn.execute(
        """
        SELECT a.id AS assignment_id, a.task_id, a.resource_type, a.resource_id,
               t.brief_id, t.role_name,
               CASE
                   WHEN a.resource_type = 'human' THEN 'assignee'
                   WHEN a.resource_type = 'agent' THEN 'requester'
                   WHEN a.resource_type = 'team' AND a.team_leader_id = ? THEN 'team_leader'
                   ELSE 'team_member'
               END AS rel
        FROM assignments a
        JOIN tasks t ON t.id = a.task_id
        LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
        WHERE a.status = 'confirmed'
          AND (
            (a.resource_type = 'human' AND hp.user_id = ?)
            OR (a.resource_type = 'agent' AND a.requested_by = ?)
            OR (a.resource_type = 'team' AND (
                a.team_leader_id = ?
                OR EXISTS (
                    SELECT 1 FROM team_members tm
                    JOIN human_pool hp2
                        ON hp2.id = tm.member_id AND tm.member_type = 'human'
                    WHERE tm.team_id = a.resource_id AND hp2.user_id = ?
                )
            ))
          )
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, user_id, user_id, user_id, user_id, limit, offset),
    ).fetchall()

    result = []
    for row in rows:
        summary = task_completion_summary(conn, row["task_id"])
        result.append(
            {
                "task_id": row["task_id"],
                "brief_id": row["brief_id"],
                "role_name": row["role_name"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "assignment_id": row["assignment_id"],
                "my_relationship": row["rel"],
                **summary,
            }
        )
    return result


def list_deliverables(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """`GET /api/tasks/{task_id}/deliverables` — 타임라인 순서(시간순).

    Phase 2: `pending_review`인 deliverable엔 지금 결정 가능한(`status=
    'pending'`) 결재 건의 id를 `approval_id`로 함께 내려준다 — 웹 UI가
    "결재" 탭으로 안 보내고 워크스페이스 안에서 바로 승인/반려하려면
    이 id가 필요하다. 다른 상태는 항상 `approval_id=None`.

    Phase 3: `author_type='agent'`인 deliverable엔 `evaluation`(Gate A-G
    점수 + TCR, 없으면 None)도 같이 내려준다 — 결재자가 승인/반려 전에
    측정치를 볼 수 있어야 한다."""
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
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    content: str,
    content_type: str = "text",
) -> int:
    """`POST /api/tasks/{task_id}/deliverables` — 그 task의 사람
    당사자/Agent 요청자 본인만 작성할 수 있다(대리 작성 금지 — 관리자/매니저도
    이 함수로는 남 대신 못 쓴다, `app.py`도 role 우회를 안 줌).

    `content_type`(2026-10-07, Tier 2) — 작성자 본인이 고른다(사람이
    직접 쓰는 글이라 LLM처럼 프롬프트로 형식을 지시할 필요가 없다).
    `DeliverableCreate` Pydantic 모델이 이미 4개 값으로 검증했으므로
    여기선 그대로 믿는다 — DB의 CHECK 제약이 마지막 방어선."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if not content or not content.strip():
        raise ValueError("empty_content")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")

    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, 'human', ?, ?, ?, 'draft')",
        (task_id, actor_id, content, content_type),
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
    갭을 메운다: 사람이 작성한 deliverable은 지금까지
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
    """`GET /api/deliverables/{id}/comments`."""
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


def detect_mentioned_agents(
    conn: sqlite3.Connection, task_id: int, content: str
) -> list[dict]:
    """2026-10-08, Phase 3 — 댓글 본문에
    `@에이전트이름`이 있는지 찾는다. "이름"은 `agent_pool.role_name`이고,
    이 task에 **초대된(task_participants) Agent만** 대상이다(배정
    여부와 무관 — 가벼운 호출이 핵심). 한국어 텍스트엔 영어
    `\\b` 같은 단어 경계가 없어 정규식 토큰화 대신 "@역할명" 부분
    문자열 포함 여부로 단순하게 판정한다(역할명에 공백이 있어도
    문제없다).

    사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
    (채팅 포함)에도 활용될 수 있도록") — `@role_name`(기존)과
    `@지정한이름`(신규) 둘 다 인정해 하위 호환을 유지한다."""
    rows = conn.execute(
        """
        SELECT ap.id, ap.role_name, ap.name FROM task_participants tp
        JOIN agent_pool ap ON ap.id = tp.participant_id
        WHERE tp.task_id = ? AND tp.participant_type = 'agent'
        """,
        (task_id,),
    ).fetchall()
    return [
        {"id": r["id"], "role_name": r["name"] or r["role_name"]}
        for r in rows
        if ("@" + r["role_name"]) in content
        or (r["name"] and ("@" + r["name"]) in content)
    ]


def create_comment(
    conn: sqlite3.Connection, deliverable_id: int, actor_id: int, content: str
) -> dict:
    """`POST /api/deliverables/{id}/comments` — 부모 task 기준으로
    참가자 판정을 재사용한다. Phase 1에서는 항상 author_type='human'
    (Agent가 댓글을 남기는 경로는 없음).

    2026-10-08, Phase 2 — 판정을
    `task_participant_relationship()`(결재 거친 정식 참가자만)에서
    `can_comment_on_task()`(정식 참가자 **또는** 초대만 된 사람)로
    넓혔다. 결과물 작성·실행 트리거는 여전히 다른 함수들이 기존의
    엄격한 판정을 그대로 쓴다 — 댓글만 문턱을 낮춘다.

    **반환 타입 변경(2026-10-08, Phase 3)**: `int`(comment_id)에서
    `dict`로 바꿨다 — `app.py`가 `@멘션`된 Agent가 있으면 댓글 저장
    *직후* `BackgroundTasks`로 트리거해야 하는데, 그러려면 이 함수가
    댓글을 만들면서 이미 계산한 `task_id`·멘션 목록을 그대로 돌려줘야
    호출부가 또 같은 조회를 반복하지 않는다."""
    d = conn.execute(
        "SELECT task_id FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if d is None:
        raise ValueError("deliverable_not_found")
    if not content or not content.strip():
        raise ValueError("empty_content")
    if not can_comment_on_task(conn, d["task_id"], actor_id):
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
    mentioned_agents = detect_mentioned_agents(conn, d["task_id"], content)
    conn.commit()
    return {
        "id": comment_id,
        "task_id": d["task_id"],
        "deliverable_id": deliverable_id,
        "mentioned_agents": mentioned_agents,
    }


def start_comment_triggered_execution(
    conn: sqlite3.Connection,
    task_id: int,
    comment_id: int,
    agent_id: int,
    actor_id: int,
    prompt: str,
) -> int:
    """2026-10-08, Phase 3 — 댓글
    `@멘션`으로 가볍게 Agent를 불러 물어본다. `start_agent_execution()`
    과 달리 confirmed 배정도, Gate 평가도, 결재 체인도 거치지 않는다
    — **사용자 결정(a)**: 결과는 항상 댓글로만 남고, 정식 결과물로
    승격하려면 사람이 별도로 "결과물로 제출"을 눌러야 한다(결재 우회
    뒷문을 막기 위한 구조적 선택).

    `agent_already_running` 가드는 `start_agent_execution()`과 같은
    이유로 재사용한다 — 버튼 실행이든 댓글 트리거든 같은
    `agent_execution_jobs` 테이블을 공유하므로, 버튼으로 실행 중일 때
    댓글 멘션이 겹치는 것도(또는 그 반대도) 막아야 이중 LLM 호출을
    피한다."""
    already_running = conn.execute(
        "SELECT id FROM agent_execution_jobs WHERE task_id = ? AND status = 'running'",
        (task_id,),
    ).fetchone()
    if already_running is not None:
        raise LookupError("agent_already_running")

    cur = conn.execute(
        "INSERT INTO agent_execution_jobs (task_id, requested_by, status, "
        "source_comment_id) VALUES (?, ?, 'running', ?)",
        (task_id, actor_id, comment_id),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(
        conn,
        actor_id,
        "comment_triggered_execution_started",
        "agent_execution_jobs",
        job_id,
        after={"task_id": task_id, "agent_id": agent_id, "comment_id": comment_id},
    )
    conn.commit()
    return job_id


def run_comment_triggered_execution_job(
    conn: sqlite3.Connection,
    job_id: int,
    deliverable_id: int,
    agent_id: int,
    actor_id: int,
    prompt: str,
) -> None:
    """`start_comment_triggered_execution()`이 반환한 job을 실제로
    실행한다(`app.py`가 `BackgroundTasks`로 등록 — `run_agent_execution_
    job()`과 같은 이유로 실패까지 전부 job 행에 기록한다, 예외를 던져도
    받아줄 호출자가 없다). 성공하면 **같은 deliverable의 댓글 스레드에
    `author_type='agent'`로 응답만 남긴다** — deliverable·Gate 평가·
    결재 체인은 전혀 만들지 않는다(사용자 결정 a)."""
    try:
        output = agent_runtime.execute_agent(conn, agent_id, actor_id, prompt)
        cur = conn.execute(
            "INSERT INTO deliverable_comments (deliverable_id, author_type, "
            "author_id, content) VALUES (?, 'agent', ?, ?)",
            (deliverable_id, agent_id, output),
        )
        reply_comment_id = cur.lastrowid
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'succeeded', "
            "finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        audit.record(
            conn,
            actor_id,
            "deliverable_comment_created",
            "deliverable_comments",
            reply_comment_id,
            after={
                "deliverable_id": deliverable_id,
                "author_type": "agent",
                "agent_id": agent_id,
            },
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계
        conn.rollback()
        friendly_error = _friendly_execution_error(exc)
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'failed', error = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (friendly_error, job_id),
        )
        audit.record(
            conn,
            actor_id,
            "comment_triggered_execution_failed",
            "agent_execution_jobs",
            job_id,
            after={"agent_id": agent_id, "error": friendly_error},
        )
        conn.commit()


def record_decision_comment(
    conn: sqlite3.Connection, deliverable_id: int, actor_id: int, content: str
) -> int:
    """승인/반려 시 남긴 선택적 사유를 댓글 타임라인에 남긴다(2026-10-07,
    실사용 리뷰로 발견한 갭) — `create_comment()`와 거의 같지만 참가자
    판정(`task_participant_relationship()`)을 **일부러 건너뛴다**:
    결재 체인은 조직도 매니저 체인 기준이라 결재자가 그 task의 참가자가
    아닌 경우가 흔한데(예: 시나리오 3 CS — 다른 task에 배정된 매니저가
    결재), 그 경우 반려해도 "왜"를 시스템에 남길 방법이 전혀 없었다.
    호출자(`app.py::decide_approval`)가 이미 승인/반려 권한 자체를
    확인했으므로 여기서 다시 참가자 여부를 확인할 필요가 없다 — 오히려
    참가자가 아닌 결재자야말로 이 함수가 메우려는 정확한 사용자층이다."""
    d = conn.execute(
        "SELECT id FROM deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if d is None:
        raise ValueError("deliverable_not_found")
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
        "deliverable_decision_comment_created",
        "deliverable_comments",
        comment_id,
        after={"deliverable_id": deliverable_id},
    )
    conn.commit()
    return comment_id


# 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT, HTML 등
# 다양할 수 있을텐데... Tier 2까지 진행해줘") — Agent/LLM이 지정된
# content_type으로 결과물을 쓰도록 프롬프트에 지시를 덧붙인다. "text"는
# 지시 없음(기존 동작 그대로 — 이 기능 추가 전 모든 호출은 암묵적으로
# content_type="text"였다). `crew_runtime.py`에도 같은 내용을 복사해
# 둔다 — 그 모듈은 이 모듈을 거꾸로 import할 수 없다(순환 참조,
# `deliverables.py`가 이미 `crew_runtime.py`를 쓴다).
_CONTENT_TYPE_INSTRUCTIONS = {
    "text": "",
    "markdown": "결과물은 Markdown 형식으로 작성하세요(제목은 #, 목록은 -, 강조는 **, "
    "코드는 ``` 블록을 쓰세요).",
    "html": "결과물은 완전한 HTML 문서로 작성하세요(<!DOCTYPE html>부터 </html>까지, "
    "스타일은 <style> 태그 안에 인라인으로만 넣고 외부 리소스는 참조하지 마세요).",
    "code": "결과물은 코드로 작성하세요. 전체를 ``` 코드 블록 안에 넣고, 필요하면 "
    "블록 앞뒤에 짧은 설명만 덧붙이세요.",
}


def skill_grounded_system_prompt(
    conn: sqlite3.Connection, role_name: str, required_skills: list[str]
) -> str:
    """Agent의 skill_tags 매칭과 같은 조회 로직을 재사용하되, 도구를
    붙이는 대신 지식 텍스트를 프롬프트에 주입한다(`pool_registry.
    skill_knowledge_text()`로 공용화 — 2026-10-13, Agent 자율 실행
    경로에도 같은 지식 주입을 적용하면서 중복 로직을 하나로 모았다)."""
    knowledge = pool_registry.skill_knowledge_text(conn, required_skills)
    base = llm.system_prompt_for_human_assist(role_name, required_skills)
    if knowledge:
        return (
            base
            + f"\n\n참고 지식({pool_registry.SKILL_KNOWLEDGE_GUIDANCE})\n"
            + knowledge
            + f"\n\n{pool_registry.NO_TOOL_LOOP_GUARD}"
        )
    return base


def assist(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    draft_context: str | None,
    provider: str | None,
    model: str | None,
    content_type: str = "text",
) -> str:
    """`POST /api/tasks/{task_id}/assist` — "LLM으로 도와줘". 결과는
    저장하지 않는다(사람이 검토·편집 후 `create_deliverable()`로 별도
    제출). provider/model이 팀원 요청인지 확인하는 RBAC은 `app.py`가
    한다(이 함수는 role을 모른다 — 참가자 여부만 확인).

    `content_type`(2026-10-07, Tier 2) — 사람이 결과물을 만들 때 고를
    형식과 LLM에게 "이 형식으로 써 달라"고 미리 알려주는 지시가 같아야
    자연스럽다(예: 마크다운으로 받을 거면 LLM도 마크다운으로 쓰게)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")

    required_skills = json.loads(task["required_skills"])
    system = skill_grounded_system_prompt(conn, task["role_name"], required_skills)
    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    prompt = draft_context or (
        f"'{task['role_name']}' 업무를 처음 시작합니다. 초안을 작성해 주세요."
    )
    instruction = _CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")
    if instruction:
        prompt = f"{prompt}\n\n{instruction}"
    return llm.call_llm(resolved_provider, resolved_model, system, prompt)


def execution_eligibility(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    allow_supervisor_override: bool,
) -> dict:
    """`GET /api/tasks/{task_id}/execution-permission` — "이 업무의 AI
    에이전트 실행" 버튼을 실제로 눌러도 되는지 **누르기 전에** 판단한다.

    사용자가 스크린샷으로 보고한 갭(2026-09-29): `start_agent_execution()`
    은 이 task의 사람 참가자("assignee")여도 Agent 배정을 요청한 사람
    ("requester")이 아니면 막는데(같은 task에 사람+Agent가 함께 배정된
    협업 구조라 흔히 생기는 조합), 웹 UI는 참가자기만 하면 무조건 실행
    버튼을 보여주고 있었다 — human assignee가 버튼을 눌러야만 "권한이
    없습니다" 403으로 뒤늦게 알게 됐다. `start_agent_execution()`이 이
    함수를 그대로 재사용하도록 리팩터해 판정 로직이 두 곳에서 갈라지지
    않게 했다."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    rel = task_participant_relationship(conn, task_id, actor_id)
    if rel != "requester" and not allow_supervisor_override:
        return {"eligible": False, "reason": "not_requester"}
    agent_assignment = conn.execute(
        "SELECT id FROM assignments WHERE task_id = ? AND status = 'confirmed' "
        "AND resource_type = 'agent'",
        (task_id,),
    ).fetchone()
    if agent_assignment is None:
        return {"eligible": False, "reason": "no_confirmed_agent"}
    return {"eligible": True, "reason": None}


def start_agent_execution(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    allow_supervisor_override: bool = False,
    agent_id: int | None = None,
    content_type: str = "text",
    attachment_text: str | None = None,
) -> dict:
    """`POST /api/tasks/{task_id}/execute-agent`(Phase 4로 비동기화) —

    `attachment_text`(사용자 요청, 2026-10-26, "A안 — 업무 수행시 업무에
    할당된 에이전트에 제공되는 문서는... 저장되지 않고 휘발되는 내용물")
    — 이번 실행 1회에만 쓸 참고자료를 프롬프트에 직접 이어붙인다.
    `app.py`가 업로드된 파일에서 `documents.extract_text()`로 뽑은
    텍스트를 그대로 넘긴다 — **DB 어디에도 저장하지 않는다**(이
    함수가 조립하는 `prompt` 문자열에만 녹아들고 사라진다).
    이 task의 confirmed agent 배정을 찾아 검증·프롬프트 구성까지만
    동기로 마치고 `agent_execution_jobs`에 `status='running'` 행을 만든
    뒤 즉시 반환한다. 실제 LLM 호출은 `run_agent_execution_job()`이
    `app.py`의 `BackgroundTasks`로 응답 전송 후 처리한다 — 요청자 본인
    이거나(`allow_supervisor_override=False`) 매니저/관리자(`True`)면
    통과. 가장 최근 사람 작성 deliverable을 Task 입력에 포함시킨다
    ("출력 처리" — "결과물 전달" 갭을 메우는 지점).

    `agent_id`는 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2)
    으로 추가했다 — "다중 confirmed 허용"으로 한 task에 confirmed agent
    배정이 여러 건 공존할 수 있게 되면서, 지금까지처럼 그냥
    `.fetchone()`으로 "아무 거나 하나" 고르면 안 된다. 명시하면 그
    Agent가 실제로 confirmed인지 검증 후 그것만 쓴다 — 비워두면
    **기존과 똑같이** 매칭되는 confirmed agent를 그대로 쓴다(지금까지의
    유일한 실사용 패턴인 "task당 confirmed agent 1건"은 동작이 전혀
    안 바뀐다 — 여러 건인데 agent_id도 안 주면 여전히 `.fetchone()`이
    그중 하나를 고른다는 것도 동일하다; "여러 건 중 꼭 골라야 한다"는
    강제는 이번 범위에 넣지 않았다 — 필요하면 후속 작업).

    provider/model은 여기서 다루지 않는다 — `app.py`가 요청에서 받은
    값을 검증 없이 그대로 들고 있다가 `run_agent_execution_job()`에
    직접 넘긴다(이 함수를 거칠 이유가 없는 순수 통과값).

    반환값의 `agent_id`/`role_name`/`prompt`는 DB에 다시 저장하지 않고
    `app.py`가 그대로 `run_agent_execution_job()`에 넘긴다 — 새 컬럼을
    안 늘리려는 선택.

    `content_type`(2026-10-07, Tier 2)은 `provider`/`model`과 달리 이
    함수가 직접 쓴다 — 프롬프트에 형식 지시를 덧붙여야 하기 때문이다.
    실제 deliverable 저장은 `run_agent_execution_job()`이 하므로,
    `content_type` 자체는 `app.py`가 그 함수에 별도로 그대로 넘긴다
    (이 함수의 반환값엔 포함하지 않음 — provider/model과 같은 순수
    통과값 패턴)."""
    elig = execution_eligibility(conn, task_id, actor_id, allow_supervisor_override)
    if elig["reason"] == "not_requester":
        raise PermissionError("not_a_participant")
    if elig["reason"] == "no_confirmed_agent":
        raise LookupError("no_confirmed_agent_assignment")

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if agent_id is not None:
        agent_assignment = conn.execute(
            "SELECT * FROM assignments WHERE task_id = ? AND status = 'confirmed' "
            "AND resource_type = 'agent' AND resource_id = ?",
            (task_id, agent_id),
        ).fetchone()
        if agent_assignment is None:
            raise LookupError("no_confirmed_agent_assignment")
    else:
        agent_assignment = conn.execute(
            "SELECT * FROM assignments WHERE task_id = ? AND status = 'confirmed' "
            "AND resource_type = 'agent'",
            (task_id,),
        ).fetchone()
    agent_id = agent_assignment["resource_id"]

    # 실측으로 발견한 갭(2026-09-28, 리뷰 2회차) — 이 함수는 원래 같은
    # task에 이미 'running' job이 있어도 확인 없이 새 job을 또 만들었다.
    # Web UI는 버튼을 비활성화해 막지만, 빠른 연속 클릭이나 여러
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
    if attachment_text:
        prompt_parts.append(f"이번 실행에 첨부된 참고 자료:\n{attachment_text}")
    instruction = _CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")
    if instruction:
        prompt_parts.append(instruction)
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


def _prompt_snapshot_json(retrieval_sink: dict) -> str | None:
    """`agent_runtime.execute_agent()`가 side-channel로 채워준
    system_prompt/augmented_prompt/llm_provider/llm_model을 `agent_
    execution_jobs.prompt_snapshot`에 저장할 JSON으로 묶는다(사용자
    요청, 2026-10-13, "문서 내용에서 얻을 인사이트" → 재현 가능한
    감사로그). 아무것도 안 채워졌으면(예: Agent 조회 자체가 실패해
    `execute_agent()` 본문에 못 들어간 경우) `None` — 빈 JSON 문자열을
    남겨 "시도는 했지만 내용이 없다"처럼 보이게 하지 않는다."""
    if not retrieval_sink:
        return None
    snapshot = {
        k: retrieval_sink[k]
        for k in ("system_prompt", "augmented_prompt", "llm_provider", "llm_model")
        if k in retrieval_sink
    }
    return json.dumps(snapshot, ensure_ascii=False) if snapshot else None


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
    content_type: str = "text",
) -> None:
    """`start_agent_execution()`이 반환한 job을 실제로 실행한다 —
    `app.py`가 `BackgroundTasks.add_task()`로 등록해 HTTP 응답을 보낸
    *뒤에* 실행되므로, 여기서 예외를 밖으로 던져도 받아줄 호출자가 없다
    (starlette가 조용히 로그만 남기고 삼킨다). 그래서 이 함수는 실패까지
    포함해 모든 결과를 `agent_execution_jobs` 행에 직접 기록한다 — Phase
    2/3의 동기 경로가 "예외를 그대로 전파해 아무 것도 안 남긴다"였던
    것과 달리, 비동기 경로는 실패도 사람이 UI에서 볼 수 있게 저장하는
    쪽이 맞다(HOTL — 실행이 죽었는지조차 모르면 감독이 안 된다)."""
    # 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    # 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    # 그라운딩이 실제로 뭘 검색해 프롬프트에 넣었는지 사후 확인할 방법이
    # 없었다(결재자가 "실제 근거 기반 답변인지" 검증 불가). side-channel
    # dict로 받아 job에 같이 남긴다.
    #
    # 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현
    # 가능한 감사로그) — 같은 side-channel에 system_prompt·증강된
    # 프롬프트·그 시점 provider/model까지 추가로 실어 `prompt_snapshot`
    # JSON으로 저장한다(성공/실패 둘 다 — 실패도 "무엇을 시도했는지"는
    # 남겨야 사후 재현·디버깅이 된다).
    retrieval_sink: dict = {}
    try:
        # Phase 3 — 실제 LLM 호출을 agent_evaluation.run_and_score()로
        # 감싸 Gate A-G를 그 자리에서 측정한다.
        eval_result = agent_evaluation.run_and_score(
            role_name,
            lambda: agent_runtime.execute_agent(
                conn,
                agent_id,
                actor_id,
                prompt,
                provider,
                model,
                retrieval_sink=retrieval_sink,
            ),
        )

        cur = conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
            (task_id, agent_id, eval_result["output"], content_type),
        )
        deliverable_id = cur.lastrowid
        assert deliverable_id is not None
        agent_evaluation.record_evaluation(
            conn, deliverable_id, eval_result["gate_scores"], eval_result["tcr"]
        )
        approvals.create_approval_chain(conn, actor_id, "deliverable", deliverable_id)
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'succeeded', deliverable_id = ?, "
            "retrieved_context = ?, prompt_snapshot = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            (
                deliverable_id,
                retrieval_sink.get("retrieved_context"),
                _prompt_snapshot_json(retrieval_sink),
                job_id,
            ),
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
    except Exception as exc:  # noqa: BLE001
        # 백그라운드 작업의 최종 경계다 — 위 docstring 참고.
        conn.rollback()
        friendly_error = _friendly_execution_error(exc)
        conn.execute(
            "UPDATE agent_execution_jobs SET status = 'failed', error = ?, "
            "prompt_snapshot = ?, finished_at = datetime('now') WHERE id = ?",
            (friendly_error, _prompt_snapshot_json(retrieval_sink), job_id),
        )
        # 실측으로 발견한 갭(리뷰 3회차) — "모든 쓰기 액션... 조회"
        # 관례상 `decide_approval()`은 approved/rejected 둘 다 감사로그에
        # 남기는데(`f"approval_{new_status}"`), 이 실패 경로만 감사로그가
        # 없었다 — HOTL 감독자가 "이 Agent 실행이 왜/언제 실패했는지"를
        # 감사로그 화면에서 못 보고 있었다. 성공 경로(`deliverable_created`)와
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


def start_team_crew_execution(
    conn: sqlite3.Connection,
    task_id: int,
    team_id: int,
    actor_id: int,
    manager_agent_id: int | None = None,
    allow_supervisor_override: bool = False,
) -> dict:
    """`POST /api/tasks/{task_id}/execute-team-crew` — 사용자 요청
    (2026-10-07, "crew 실행에서... 복수 그룹 가능하도록 개선, 이 그룹이
    전체 업무에서 사람-에이전트 연동이 가능한 구조인지 확인") —
    Phase 3. 이 할 일에
    confirmed로 배정된 **팀 하나**의 Agent 멤버 전원을 실제 CrewAI
    Crew로 묶어 실행한다(사용자 결정 — 한 할 일에 팀이 여러 개 배정돼
    있어도 "팀 하나를 골라서 실행", 전부 동시 실행 아님).

    `start_agent_execution()`과 같은 비동기 2단계 패턴(검증+job 행
    생성까지만 동기, 실제 CrewAI kickoff은 `run_team_crew_job()`이
    BackgroundTasks로 처리) — 팀은 Agent가 여러 명이라 `agent_execution_
    jobs` 행도 여러 개 만든다. `manager_agent_id`가 있으면
    (hierarchical_with_manager) 결과물을 내는 주체가 매니저 1명뿐이라
    (2026-10-18부터 — "매니저는 산출물 없음"이던 예전 관례가 "매니저가
    코워커 결과를 통합한 진짜 결과물을 낸다"로 바뀜, `crew_runtime.
    run_crew()` 참고) job도 매니저 1개만 만든다 — **정정(2026-11-04,
    실측으로 재현한 버그 수정)**: 예전엔 이 변경 이후에도 계속 코워커
    들에게만 job을 만들어, 결과물이 있는 매니저는 매칭되는 job이 없고
    job이 있는 코워커는 결과물 목록에 안 나타나 **결과물이 하나도
    생성되지 않았다**. 기존 "AI 에이전트 실행" 폴링 UI(`GET .../
    agent-execution-jobs`)가 그대로 여러(또는 매니저 1개) job을 보여줄
    수 있어 새 폴링 메커니즘을 만들지 않았다.

    참가자 판정은 단일 Agent 실행(`execution_eligibility()`, "요청자
    본인만")보다 넓다 — 팀 리더·팀원 둘 다 실행을 트리거할 수 있다(팀은
    원래 여럿이 함께 일하는 단위라 "요청자 1인"으로 좁히면 팀의 취지와
    안 맞는다는 판단, admin/manager는 기존처럼 감독 대리 허용)."""
    rel = task_participant_relationship(conn, task_id, actor_id)
    if rel not in ("team_leader", "team_member") and not allow_supervisor_override:
        raise PermissionError("not_a_team_participant")

    already_running = conn.execute(
        "SELECT id FROM agent_execution_jobs WHERE task_id = ? AND status = 'running'",
        (task_id,),
    ).fetchone()
    if already_running is not None:
        raise LookupError("agent_already_running")

    team = conn.execute("SELECT id FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team is None:
        raise ValueError("team_not_found")
    confirmed = conn.execute(
        "SELECT id FROM assignments WHERE task_id = ? AND resource_type = 'team' "
        "AND resource_id = ? AND status = 'confirmed'",
        (task_id, team_id),
    ).fetchone()
    if confirmed is None:
        raise ValueError("team_not_confirmed_for_task")

    agent_ids = teams.active_agent_member_ids(conn, team_id)
    if not agent_ids:
        raise ValueError("team_has_no_active_agents")
    if manager_agent_id is not None and manager_agent_id not in agent_ids:
        raise ValueError("manager_agent_not_in_team")

    # hierarchical_with_manager면 결과물을 내는 주체가 매니저 1명뿐이라
    # (위 docstring 정정 참고) job도 매니저 1개만 — 코워커 job을 같이
    # 만들면 영원히 'running'으로 남아(다음 실행을 "이미 실행 중"으로
    # 막는 부작용까지 생김) 더 나쁘다.
    job_target_ids: list[int]
    if manager_agent_id is not None:
        job_target_ids = [manager_agent_id]
    else:
        job_target_ids = agent_ids
    job_by_agent_id: dict[int, int] = {}
    for agent_id in job_target_ids:
        cur = conn.execute(
            "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
            "VALUES (?, ?, 'running')",
            (task_id, actor_id),
        )
        job_by_agent_id[agent_id] = cur.lastrowid
    audit.record(
        conn,
        actor_id,
        "team_crew_execution_started",
        "agent_execution_jobs",
        None,
        after={"task_id": task_id, "team_id": team_id, "agent_ids": agent_ids},
    )
    conn.commit()
    return {"job_by_agent_id": job_by_agent_id, "agent_ids": agent_ids}


def start_adhoc_crew_execution(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    agent_ids: list[int],
    manager_agent_id: int | None = None,
    allow_supervisor_override: bool = False,
) -> dict:
    """`POST /api/tasks/{task_id}/execute-adhoc-crew` — 사용자 요청
    (2026-10-20, "crewai의 잠재력을 모두 끌어내고, 에이전트 그룹(crew
    구성)을 직관적으로 하기 위한... 개편") — `start_team_crew_execution()`
    과 거의 동일하지만 **팀이라는 전제조건을 없앤다**. 지금까지는 실제
    업무에 에이전트 조합을 써보려면 "① 팀부터 만들고(사전 등록) → ②
    배정+승인 → ③ 실행"의 3단계를 거쳐야 했다(그 전에 미리 테스트하고
    싶으면 결과가 버려지는 `/api/crew/run` 샌드박스로 가야 했다) — 이
    함수는 Task 참가자가 **Agent를 그 자리에서 즉석으로 골라** 바로
    실행하고, 결과가 그대로 이 Task의 deliverable+결재 체인으로
    쌓이게 한다(`start_team_crew_execution()`과 동일한 업무 연결 —
    팀 등록·배정·승인 단계만 생략).

    참가자 판정은 `start_team_crew_execution()`보다 더 넓다 — 팀
    리더/팀원으로 좁히지 않고 이 Task의 confirmed 배정에 걸린 사람이면
    누구나(`task_participant_relationship()`이 `None`이 아니면) 실행할
    수 있다(팀이라는 매개가 없으니 "팀 역할"로 좁힐 근거 자체가 없다).

    `agent_ids`가 전부 `status='active'`인지 여기서 미리 확인한다 —
    `crew_runtime.run_crew()`도 같은 검사를 하지만, 거기서 걸리면
    BackgroundTasks 안에서 조용히 실패 처리되므로(`run_adhoc_crew_job()`
    의 except 블록) 사용자가 즉시 알 수 있게 요청 시점에 400으로
    막는다."""
    rel = task_participant_relationship(conn, task_id, actor_id)
    if rel is None and not allow_supervisor_override:
        raise PermissionError("not_a_task_participant")

    already_running = conn.execute(
        "SELECT id FROM agent_execution_jobs WHERE task_id = ? AND status = 'running'",
        (task_id,),
    ).fetchone()
    if already_running is not None:
        raise LookupError("agent_already_running")

    if not agent_ids:
        raise ValueError("no_agents_selected")
    placeholders = ",".join("?" * len(agent_ids))
    active_count = conn.execute(
        f"SELECT COUNT(*) AS c FROM agent_pool WHERE id IN ({placeholders}) "
        "AND status = 'active'",
        agent_ids,
    ).fetchone()["c"]
    if active_count != len(set(agent_ids)):
        raise ValueError("some_agents_not_active")
    if manager_agent_id is not None and manager_agent_id not in agent_ids:
        raise ValueError("manager_agent_not_in_agent_ids")

    # 실측으로 발견한 버그 수정(2026-11-04, 파이프라인 "구현" 단계
    # 합성 로직 수정 작업 중 라이브 스모크 테스트로 재현) —
    # `manager_agent_id`가 있으면(hierarchical_with_manager)
    # `crew_runtime.run_crew()`는 `result["agents"]`에 **매니저 1건만**
    # 돌려준다(2026-10-18에 "매니저는 산출물 없음"에서 "매니저가 통합한
    # 진짜 결과물이 있음"으로 바뀜). 그런데 이 함수는 그 변경 이후에도
    # 계속 "매니저를 제외한 코워커들에게만" job을 만들고 있었다 — 그
    # 결과 매니저(결과물 있음)는 매칭되는 job이 없어서
    # `run_adhoc_crew_job()`이 스킵하고, 코워커들(job 있음)은
    # `result["agents"]`에 아예 안 나타나서 역시 매칭이 안 돼, 이
    # 조합(hierarchical + manager_agent_id)에서는 **결과물이 단 하나도
    # 안 생겼다**(라이브로 재현 확인). 코워커 job을 만들어봐야 영원히
    # 'running' 상태로 남아(다음 실행을 "이미 실행 중"으로 막는
    # 부작용까지 생김) 더 나쁘므로, "이 모드에서 실제로 결과물을 내는
    # 주체가 누구인지"에 맞춰 매니저 1명한테만 job을 만든다.
    job_target_ids: list[int]
    if manager_agent_id is not None:
        job_target_ids = [manager_agent_id]
    else:
        job_target_ids = agent_ids
    job_by_agent_id: dict[int, int] = {}
    for agent_id in job_target_ids:
        cur = conn.execute(
            "INSERT INTO agent_execution_jobs (task_id, requested_by, status) "
            "VALUES (?, ?, 'running')",
            (task_id, actor_id),
        )
        job_by_agent_id[agent_id] = cur.lastrowid
    audit.record(
        conn,
        actor_id,
        "adhoc_crew_execution_started",
        "agent_execution_jobs",
        None,
        after={"task_id": task_id, "agent_ids": agent_ids},
    )
    conn.commit()
    return {"job_by_agent_id": job_by_agent_id, "agent_ids": agent_ids}


def _persist_partial_crew_outputs(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    content_type: str,
    partial_outputs: dict[int, str],
    job_by_agent_id: dict[int, int],
) -> None:
    """사용자 요청(2026-10-20, "실패 시 일부 재시도") — `run_adhoc_crew_job()`/
    `run_team_crew_job()`이 공유한다. Crew 실행이 중간에 실패해도
    `partial_outputs`에 담긴(이미 Task를 끝낸) Agent만큼은 정상 성공
    경로와 동일하게 `deliverables` INSERT+Gate 평가+결재 체인을
    만든다. Gate A-G는 `agent_evaluation.run_and_score_crew()`가 실제
    kickoff 호출을 감싸서 측정하는 구조라 실패한 실행에선 원천적으로
    측정 불가능하다 — 거짓 점수를 채우지 않고 전부 `not_measured`로
    정직하게 남긴다(이미 있는 Gate B/E "미평가" 배지 표시를 그대로
    재사용)."""
    base_not_measured_gate_scores = {
        gid: {"not_measured": True} for gid in agent_evaluation.GATE_IDS
    }
    for agent_id, output_raw in partial_outputs.items():
        job_id = job_by_agent_id.get(agent_id)
        if job_id is None:
            continue
        # 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사") — Gate A-G는
        # kickoff 실패로 측정 불가능해도, 구문 검사는 완료된 Agent의 실제
        # 출력 텍스트만으로 결정론적으로 계산 가능하므로 똑같이 추가한다.
        # 로드맵 5번(2026-11-05) — `content_type`이 실제로 신뢰 가능한
        # 값이므로(요청자 명시, DB CHECK로 검증됨) content_type별 보조
        # 검증기까지 쓰는 `content_validators`로 교체(펜스 스캔만 하던
        # `compute_code_syntax_check()` 직접 호출보다 더 정확함).
        agent_gate_scores = dict(base_not_measured_gate_scores)
        agent_gate_scores["code_syntax"] = content_validators.validate_by_content_type(
            content_type, output_raw
        )
        cur = conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
            (task_id, agent_id, output_raw, content_type),
        )
        deliverable_id = cur.lastrowid
        assert deliverable_id is not None
        agent_evaluation.record_evaluation(
            conn, deliverable_id, agent_gate_scores, None
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
                "gate_scores": agent_gate_scores,
                "tcr": None,
                "partial_recovery": True,
            },
        )


def run_adhoc_crew_job(
    conn: sqlite3.Connection,
    job_by_agent_id: dict[int, int],
    task_id: int,
    actor_id: int,
    agent_ids: list[int],
    task_descriptions: list[str],
    process: str,
    provider: str | None,
    model: str | None,
    manager_agent_id: int | None,
    content_type: str = "text",
    memory: bool = True,
    planning: bool = False,
    manager_provider: str | None = None,
    manager_model: str | None = None,
    parallel_agent_ids: list[int] | None = None,
    self_review: bool = False,
) -> None:
    """`start_adhoc_crew_execution()`이 만든 job들을 실제로 실행한다 —
    `run_team_crew_job()`과 같은 백그라운드 경계·결과 저장 방식이지만,
    `crew_runtime.run_crew_for_team()`(팀 멤버 조회 필요) 대신
    `crew_runtime.run_crew()`를 **직접** 호출한다(팀이 없으므로
    `agent_ids`/`task_descriptions`를 호출자가 그대로 넘긴다). 이 덕분에
    오늘의 "팀 Agent 실행"(전원 동일 설명)과 달리 **Agent별로 서로
    다른 task description**을 줄 수 있다 — 실제로 더 유연하다.

    사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성") —
    `crew_history.create_run()`으로 시작 시점에 행을 만들고
    (`kind='adhoc'`), `crew_runtime.run_crew()`의 `on_task_complete`
    콜백으로 Task 완료마다 즉시 commit해 실행 중에도 폴링으로 진행
    상황을 볼 수 있게 하며, 끝나면 `complete_run()`/`fail_run()`으로
    협업 다이어그램·토큰 사용량·Gate 점수까지 영구 기록한다(이전엔
    이 정보가 실행 직후 전부 사라졌다).

    사용자 요청(2026-10-20, "실패 시 일부 재시도") — 지금까지는 Crew가
    중간에 실패하면 이미 Task를 끝낸 Agent의 산출물까지 전부 버려지고
    재시도 시 전원을 처음부터 다시 돌려야 했다. `on_task_complete`
    콜백이 Task 완료마다 넘겨주는 `output_raw`(전체 텍스트)를
    `partial_outputs`에 쌓아뒀다가, Crew 실행 자체가 예외로 끝나도
    이미 완료된 Agent만큼은 정상 성공 경로와 동일하게 deliverable+
    결재 체인을 만든다 — 다만 Gate A-G는 `run_and_score_crew()`가
    실제 kickoff 호출을 감싸서 측정하는 구조라(호출이 예외로 끝났으니
    평가 자체가 불가능) 전부 `not_measured`로 정직하게 남긴다(거짓
    점수를 채우지 않는다 — 기존 Gate B/E "미평가" 배지 표시를 그대로
    재사용, 새 UI 불필요)."""
    partial_outputs: dict[int, str] = {}

    def _on_task_complete(c: dict) -> None:
        crew_history.append_progress(conn, run_id, c["agent_role"], c["output_preview"])
        if c.get("agent_id") is not None:
            partial_outputs[c["agent_id"]] = c["output_raw"]

    run_id = crew_history.create_run(
        conn,
        task_id,
        "adhoc",
        actor_id,
        agent_ids,
        process,
        manager_agent_id=manager_agent_id,
        task_descriptions=task_descriptions,
    )
    crew_run_completed = False
    try:
        result = crew_runtime.run_crew(
            conn,
            agent_ids,
            task_descriptions,
            actor_id,
            process=process,
            provider=provider,
            model=model,
            manager_agent_id=manager_agent_id,
            on_task_complete=_on_task_complete,
            memory=memory,
            planning=planning,
            manager_provider=manager_provider,
            manager_model=manager_model,
            parallel_agent_ids=parallel_agent_ids,
            self_review=self_review,
        )
        # 실제 Crew 실행(`run_crew()`) 자체는 여기서 이미 끝났다 —
        # 아래 deliverable 저장 단계가 따로 실패해도(`agent_execution_
        # jobs`가 그건 별도로 'failed' 기록한다) 이 Crew 실행 자체가
        # "실패"였던 건 아니므로, `crew_run_completed`를 여기서 바로
        # True로 세워 except 블록이 이미 성공한 실행을 되돌려 'failed'로
        # 덮어쓰지 않게 한다.
        crew_history.complete_run(conn, run_id, result)
        crew_run_completed = True
        for agent in result["agents"]:
            job_id = job_by_agent_id.get(agent["agent_id"])
            if job_id is None or agent["output"] is None:
                continue  # 매니저 — job 자체를 안 만들었으므로 스킵
            if agent.get("stage") == "draft":
                # self_review=True(사용자 요청, 2026-11-04)면 worker당
                # `result["agents"]`에 초안+검토 2건이 들어온다. `job_by_
                # agent_id`는 worker 1명당 job 1개뿐이라, 필터링 없이
                # 그대로 두면 같은 job에 deliverable+결재 체인이 2개씩
                # 생겨(초안도 검토본도 결재 큐에 오름) 사람 개입을
                # 오히려 늘려버린다 — self_review의 목적과 정반대다.
                # 검토(최종)본만 결재 큐에 올리고, 초안은 `crew_history.
                # complete_run()`이 이미 `result` 전체를 저장했으므로
                # 실행 이력에서 그대로 조회 가능하다(투명성 유지).
                continue
            # 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사") — Agent마다
            # 산출물 내용이 다르므로, crew 전체가 공유하는 `result["gate_
            # scores"]`에 직접 쓰지 않고 deliverable별 복사본에만 넣는다
            # (차단 개념이 없는 경로라 안전 — 아래 `record_evaluation()` 참고).
            # 로드맵 5번(2026-11-05) — `content_type`이 실제로 신뢰
            # 가능한 값(요청자 명시, DB CHECK로 검증됨)이므로 content_
            # type별 보조 검증기까지 쓰는 `content_validators`로 교체.
            agent_gate_scores = dict(result["gate_scores"])
            agent_gate_scores["code_syntax"] = (
                content_validators.validate_by_content_type(
                    content_type, agent["output"]
                )
            )
            cur = conn.execute(
                "INSERT INTO deliverables (task_id, author_type, author_id, content, "
                "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
                (task_id, agent["agent_id"], agent["output"], content_type),
            )
            deliverable_id = cur.lastrowid
            assert deliverable_id is not None
            agent_evaluation.record_evaluation(
                conn, deliverable_id, agent_gate_scores, result["tcr"]
            )
            approvals.create_approval_chain(
                conn, actor_id, "deliverable", deliverable_id
            )
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
                    "agent_id": agent["agent_id"],
                    "gate_scores": agent_gate_scores,
                    "tcr": result["tcr"],
                },
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계
        conn.rollback()
        friendly_error = _friendly_execution_error(exc)
        if not crew_run_completed:
            _persist_partial_crew_outputs(
                conn, task_id, actor_id, content_type, partial_outputs, job_by_agent_id
            )
            crew_history.fail_run(
                conn, run_id, friendly_error, completed_agent_ids=list(partial_outputs)
            )
        for agent_id, job_id in job_by_agent_id.items():
            if agent_id in partial_outputs:
                continue  # 위에서 이미 'succeeded'로 처리됨
            conn.execute(
                "UPDATE agent_execution_jobs SET status = 'failed', error = ?, "
                "finished_at = datetime('now') WHERE id = ?",
                (friendly_error, job_id),
            )
            audit.record(
                conn,
                actor_id,
                "agent_execution_failed",
                "agent_execution_jobs",
                job_id,
                after={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "error": friendly_error,
                },
            )
        conn.commit()


def run_team_crew_job(
    conn: sqlite3.Connection,
    job_by_agent_id: dict[int, int],
    task_id: int,
    team_id: int,
    actor_id: int,
    process: str,
    provider: str | None,
    model: str | None,
    manager_agent_id: int | None,
    content_type: str = "text",
    memory: bool = True,
    planning: bool = False,
    manager_provider: str | None = None,
    manager_model: str | None = None,
) -> None:
    """`start_team_crew_execution()`이 만든 job들을 실제로 실행한다 —
    `run_agent_execution_job()`과 같은 백그라운드 경계(예외가 여기서
    끝난다, 실패도 job 행에 기록). 팀의 Agent별 산출물 각각을
    `deliverables`(author_type='agent') + 결재 체인으로 만든다 —
    이 순간부터 팀 리더·팀원(사람)이 협업 워크스페이스에서 그 결과물을
    보고 댓글·승인/반려할 수 있다(이 함수가 Crew를 업무 파이프라인에
    연결하는 지점).

    사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행 가시성") —
    `run_adhoc_crew_job()`과 같은 패턴으로 `crew_history`에 영구
    기록한다(`kind='team'`, `team_id` 포함). `agent_ids`는 `job_by_
    agent_id`의 키(워커)에 매니저를 더해 복원한다 — 이 함수는 전체
    목록을 따로 받지 않는다(`start_team_crew_execution()`이 반환했던
    것을 호출부가 넘기지 않아서다).

    사용자 요청(2026-10-20, "실패 시 일부 재시도") — `run_adhoc_crew_
    job()`과 같은 부분 복구 로직(`_persist_partial_crew_outputs()`
    공유)을 쓴다. 다만 팀 실행은 전원이 같은 description을 받으므로
    (`run_crew_for_team()`이 내부에서 구성) `task_descriptions`를
    따로 안 받는다 — `crew_history`엔 `None`으로 남아, 이 run은
    "재시도"가 항상 그 하나의 공통 description을 그대로 재사용한다."""
    agent_ids_for_history = list(job_by_agent_id.keys())
    if manager_agent_id is not None:
        agent_ids_for_history.append(manager_agent_id)
    partial_outputs: dict[int, str] = {}

    def _on_task_complete(c: dict) -> None:
        crew_history.append_progress(conn, run_id, c["agent_role"], c["output_preview"])
        if c.get("agent_id") is not None:
            partial_outputs[c["agent_id"]] = c["output_raw"]

    run_id = crew_history.create_run(
        conn,
        task_id,
        "team",
        actor_id,
        agent_ids_for_history,
        process,
        manager_agent_id=manager_agent_id,
        team_id=team_id,
    )
    crew_run_completed = False
    try:
        result = crew_runtime.run_crew_for_team(
            conn,
            team_id,
            task_id,
            actor_id,
            process=process,
            provider=provider,
            model=model,
            manager_agent_id=manager_agent_id,
            content_type=content_type,
            on_task_complete=_on_task_complete,
            memory=memory,
            planning=planning,
            manager_provider=manager_provider,
            manager_model=manager_model,
        )
        crew_history.complete_run(conn, run_id, result)
        crew_run_completed = True
        for agent in result["agents"]:
            job_id = job_by_agent_id.get(agent["agent_id"])
            if job_id is None or agent["output"] is None:
                continue  # 매니저 — job 자체를 안 만들었으므로 스킵
            # 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사") — Agent마다
            # 산출물 내용이 다르므로, crew 전체가 공유하는 `result["gate_
            # scores"]`에 직접 쓰지 않고 deliverable별 복사본에만 넣는다
            # (차단 개념이 없는 경로라 안전 — 아래 `record_evaluation()` 참고).
            # 로드맵 5번(2026-11-05) — `content_type`이 실제로 신뢰
            # 가능한 값(요청자 명시, DB CHECK로 검증됨)이므로 content_
            # type별 보조 검증기까지 쓰는 `content_validators`로 교체.
            agent_gate_scores = dict(result["gate_scores"])
            agent_gate_scores["code_syntax"] = (
                content_validators.validate_by_content_type(
                    content_type, agent["output"]
                )
            )
            cur = conn.execute(
                "INSERT INTO deliverables (task_id, author_type, author_id, content, "
                "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
                (task_id, agent["agent_id"], agent["output"], content_type),
            )
            deliverable_id = cur.lastrowid
            assert deliverable_id is not None
            agent_evaluation.record_evaluation(
                conn, deliverable_id, agent_gate_scores, result["tcr"]
            )
            approvals.create_approval_chain(
                conn, actor_id, "deliverable", deliverable_id
            )
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
                    "agent_id": agent["agent_id"],
                    "team_id": team_id,
                    "gate_scores": agent_gate_scores,
                    "tcr": result["tcr"],
                },
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계
        conn.rollback()
        friendly_error = _friendly_execution_error(exc)
        if not crew_run_completed:
            _persist_partial_crew_outputs(
                conn, task_id, actor_id, content_type, partial_outputs, job_by_agent_id
            )
            crew_history.fail_run(
                conn, run_id, friendly_error, completed_agent_ids=list(partial_outputs)
            )
        for agent_id, job_id in job_by_agent_id.items():
            if agent_id in partial_outputs:
                continue  # 위에서 이미 'succeeded'로 처리됨
            conn.execute(
                "UPDATE agent_execution_jobs SET status = 'failed', error = ?, "
                "finished_at = datetime('now') WHERE id = ?",
                (friendly_error, job_id),
            )
            audit.record(
                conn,
                actor_id,
                "agent_execution_failed",
                "agent_execution_jobs",
                job_id,
                after={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "error": friendly_error,
                },
            )
        conn.commit()


def start_crew_run_retry(
    conn: sqlite3.Connection,
    run_id: int,
    actor_id: int,
    allow_supervisor_override: bool = False,
) -> dict:
    """사용자 요청(2026-10-20, "실패 시 일부 재시도" — Crew 빌더 로드맵
    5단계) — 실패한 `crew_runs`에서 이미 Task를 끝낸(즉 deliverable이
    만들어진) Agent는 제외하고, 남은 Agent만으로 새 즉석(adhoc) Crew
    실행을 시작한다. 원래 팀 실행이었어도 재시도는 항상 adhoc 경로로
    돌린다 — 팀 사전조건(confirmed 배정 등)을 재확인할 이유가 없고,
    남은 인원만 골라 돌리는 건 애초에 adhoc 경로가 하는 일이라 더
    단순하고 안전하다. hierarchical로 실행하다 실패한 경우, 재시도는
    매니저 재위임 없이 남은 워커만 sequential로 돈다는 단순화도 같은
    이유 — 의도적 설계 결정이다(매니저 자체는 산출물이 없어 "완료 못한
    Agent"가 아니므로 재시도 대상에서 자연히 제외된다)."""
    run = crew_history.get_run(conn, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run["status"] != "failed":
        raise ValueError("run_not_failed")

    completed_agent_ids = set(run["completed_agent_ids"])
    remaining_agent_ids = [
        a
        for a in run["agent_ids"]
        if a not in completed_agent_ids and a != run["manager_agent_id"]
    ]
    if not remaining_agent_ids:
        raise ValueError("nothing_to_retry")

    original_descriptions = run["task_descriptions"]
    if original_descriptions is None:
        # 팀 실행은 전원이 같은 description을 받으므로 따로 저장하지
        # 않는다(`run_team_crew_job()` 참고) — role_name 기반 공통
        # 문구로 대신 복원한다.
        task = conn.execute(
            "SELECT role_name FROM tasks WHERE id = ?", (run["task_id"],)
        ).fetchone()
        description_by_agent_id = {
            a: f"'{task['role_name']}' 업무를 수행하세요." for a in run["agent_ids"]
        }
    else:
        description_by_agent_id = dict(zip(run["agent_ids"], original_descriptions))

    # 완료된 동료의 실제 결과물 전문을 맥락으로 붙인다 — sequential
    # relay가 중간에 끊긴 경우 남은 Agent가 앞 단계 맥락을 잃지 않게
    # 하기 위함(완전히 새로 시작하는 것보다 더 자연스럽다).
    completed_context = ""
    if completed_agent_ids:
        rows = conn.execute(
            "SELECT author_id, content FROM deliverables WHERE task_id = ? "
            "AND author_type = 'agent' AND author_id IN ({})".format(
                ",".join("?" * len(completed_agent_ids))
            ),
            (run["task_id"], *completed_agent_ids),
        ).fetchall()
        if rows:
            completed_context = "\n\n참고: 이미 완료된 동료 결과물\n" + "\n".join(
                f"- {r['content'][:500]}" for r in rows
            )

    remaining_descriptions = [
        description_by_agent_id.get(a, "이어서 업무를 수행하세요.") + completed_context
        for a in remaining_agent_ids
    ]

    job = start_adhoc_crew_execution(
        conn,
        run["task_id"],
        actor_id,
        remaining_agent_ids,
        manager_agent_id=None,
        allow_supervisor_override=allow_supervisor_override,
    )
    return {
        "job_by_agent_id": job["job_by_agent_id"],
        "agent_ids": remaining_agent_ids,
        "task_descriptions": remaining_descriptions,
        "task_id": run["task_id"],
    }


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
    jobs = [dict(r) for r in rows]
    for job in jobs:
        # 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 재현
        # 가능한 감사로그) — skill_tags/mcp_servers와 같은 관례로, JSON
        # 컬럼은 저장만 하지 않고 여기서 파싱해 돌려준다(화면이 직접
        # JSON.parse 안 해도 되게).
        job["prompt_snapshot"] = (
            json.loads(job["prompt_snapshot"]) if job["prompt_snapshot"] else None
        )
    return jobs


def reap_orphaned_execution_jobs(conn: sqlite3.Connection) -> int:
    """**실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
    개선해줘" 리뷰 중)**: `status='running'`인 job은 프로세스가 죽기
    전엔 절대 그 상태로 남을 이유가 없다(`run_agent_execution_job()`이
    항상 `succeeded`/`failed`로 마무리한다) — 그런데 서버 프로세스가
    실행 도중 죽으면(배포 재시작, 크래시) 그 job은 영원히 `running`으로
    남고, Web UI는 그 task의 "AI 에이전트 실행" 버튼을 계속 비활성화한
    채로 막아버린다(재시작해도 절대 안 풀림 — 새로고침을 아무리 해도
    복구 불가능한 UI 먹통 상태였다). 이 앱은 "단일 프로세스" 전제이므로,
    `create_app()` 기동 시점에
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


def get_task_detail(conn: sqlite3.Connection, task_id: int) -> dict | None:
    """2026-10-08 — `GET /api/tasks/
    {task_id}`. 지금까지 Task 단건을 직접 조회하는 엔드포인트가 없었다
    (`GET /api/briefs/{brief_id}/tasks`로 간접 조회만 가능 — `app.py`가
    명시적 dict를 만들어 반환하는 곳이라 `notes` 같은 새 컬럼이 추가돼도
    저절로 노출되지 않는다). 이 함수가 `SELECT *`로 `tasks`의 모든
    컬럼(`notes` 포함)을 그대로 반환한다."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    detail = dict(row)
    detail["required_skills"] = json.loads(detail["required_skills"])
    return detail


def update_task_notes(
    conn: sqlite3.Connection, task_id: int, actor_id: int, notes: str
) -> dict:
    """`PATCH /api/tasks/{task_id}/notes` — `create_comment()`와 같은
    참가자 판정(`task_participant_relationship()`)을 재사용한다. 결재가
    필요 없는 가벼운 메모라 승인 체인을 타지 않는다."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")

    conn.execute("UPDATE tasks SET notes = ? WHERE id = ?", (notes, task_id))
    audit.record(
        conn, actor_id, "task_notes_updated", "tasks", task_id, after={"notes": notes}
    )
    conn.commit()
    detail = get_task_detail(conn, task_id)
    assert detail is not None
    return detail


def update_task_status(
    conn: sqlite3.Connection, task_id: int, actor_id: int, status: str
) -> dict:
    """`PATCH /api/tasks/{task_id}/status`(Phase 4) — 할 일 보드
    칸반 상태 이동. `update_task_notes()`와 같은 참가자 전용 판정(admin/
    manager도 이 task의 참가자가 아니면 못 바꾼다 — 그때 정한 관례를
    그대로 따른다, 조직도 감독 권한과 "이 업무를 직접 진행 중인지"는
    별개 개념)."""
    task = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")
    old_status = task["status"]
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    audit.record(
        conn,
        actor_id,
        "task_status_updated",
        "tasks",
        task_id,
        before={"status": old_status},
        after={"status": status},
    )
    conn.commit()
    detail = get_task_detail(conn, task_id)
    assert detail is not None
    return detail


def update_task_priority(
    conn: sqlite3.Connection, task_id: int, actor_id: int, priority: str
) -> dict:
    """`PATCH /api/tasks/{task_id}/priority`(Phase 4) — `update_task_
    status()`와 같은 참가자 전용 판정."""
    task = conn.execute(
        "SELECT priority FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")
    old_priority = task["priority"]
    conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id))
    audit.record(
        conn,
        actor_id,
        "task_priority_updated",
        "tasks",
        task_id,
        before={"priority": old_priority},
        after={"priority": priority},
    )
    conn.commit()
    detail = get_task_detail(conn, task_id)
    assert detail is not None
    return detail


def update_task_retry_budget(
    conn: sqlite3.Connection, task_id: int, actor_id: int, max_retry_budget: int | None
) -> dict:
    """`PATCH /api/tasks/{task_id}/retry-budget`(로드맵 9번, 2026-11-05)
    — `notes`/`status`/`priority`와 달리 참가자 전용이 아니라
    admin/manager 전용 거버넌스 설정이다(호출부 `app.py`의
    `require_role(Role.ADMIN, Role.MANAGER)`가 이미 막아주므로 여기선
    참가자 판정을 하지 않는다). `None`이면 예산 해제(제한 없음)."""
    task = conn.execute(
        "SELECT max_retry_budget FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    old_budget = task["max_retry_budget"]
    conn.execute(
        "UPDATE tasks SET max_retry_budget = ? WHERE id = ?",
        (max_retry_budget, task_id),
    )
    audit.record(
        conn,
        actor_id,
        "task_retry_budget_updated",
        "tasks",
        task_id,
        before={"max_retry_budget": old_budget},
        after={"max_retry_budget": max_retry_budget},
    )
    conn.commit()
    detail = get_task_detail(conn, task_id)
    assert detail is not None
    return detail


def _would_create_cycle(
    conn: sqlite3.Connection, task_id: int, depends_on_task_id: int
) -> bool:
    """`task_id`가 `depends_on_task_id`에 의존하는 edge를 추가하면 순환이
    생기는지 DFS로 검사한다. `depends_on_task_id`에서 출발해 기존
    "의존한다" edge(`task_id`열 → `depends_on_task_id`열 방향)를 따라가다
    `task_id`에 닿으면 — 즉 `depends_on_task_id`가 이미 간접적으로
    `task_id`에 의존하고 있으면 — 거기에 "`task_id`가 `depends_on_
    task_id`에 의존한다"는 edge까지 더하는 순간 고리가 닫힌다. Task
    개수가 많지 않은 전제라 인덱스 없는 순수 DFS로 충분하다(설계 문서
    그대로)."""
    if task_id == depends_on_task_id:
        return True
    visited: set[int] = set()
    stack = [depends_on_task_id]
    while stack:
        current = stack.pop()
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (current,),
        ).fetchall()
        stack.extend(r["depends_on_task_id"] for r in rows)
    return False


def _insert_task_dependency_row(
    conn: sqlite3.Connection, task_id: int, actor_id: int, depends_on_task_id: int
) -> dict:
    """`add_task_dependency()`와 `add_task_dependency_for_decomposition()`이
    공유하는 자기참조/순환 검사 + INSERT + 감사로그 — **commit은 안 한다**
    (호출자가 트랜잭션 경계를 결정한다: 참가자 경로는 단건이라 바로
    commit하고, 분해 시점 경로는 Task 생성 전체와 묶어 한 번에 commit해야
    원자성이 보장된다)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    dep_task = conn.execute(
        "SELECT id FROM tasks WHERE id = ?", (depends_on_task_id,)
    ).fetchone()
    if dep_task is None:
        raise ValueError("depends_on_task_not_found")
    if task_id == depends_on_task_id:
        raise ValueError("self_dependency")
    if _would_create_cycle(conn, task_id, depends_on_task_id):
        raise ValueError("cycle_detected")
    try:
        cur = conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) "
            "VALUES (?, ?)",
            (task_id, depends_on_task_id),
        )
    except sqlite3.IntegrityError:
        raise ValueError("already_exists") from None
    dependency_id = cur.lastrowid
    assert dependency_id is not None
    audit.record(
        conn,
        actor_id,
        "task_dependency_added",
        "task_dependencies",
        dependency_id,
        after={"task_id": task_id, "depends_on_task_id": depends_on_task_id},
    )
    return {
        "id": dependency_id,
        "task_id": task_id,
        "depends_on_task_id": depends_on_task_id,
    }


def add_task_dependency(
    conn: sqlite3.Connection, task_id: int, actor_id: int, depends_on_task_id: int
) -> dict:
    """`POST /api/tasks/{task_id}/dependencies`(Phase 4) —
    참가자 전용(위와 같은 관례). 자기참조는 DB CHECK로도 막혀 있지만
    (`db.py`), 더 명확한 에러 메시지를 위해 여기서도 먼저 본다. 간접
    순환은 `_would_create_cycle()`로 애플리케이션 레벨에서 검사한다
    (DB 제약만으로는 못 막는다)."""
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")
    result = _insert_task_dependency_row(conn, task_id, actor_id, depends_on_task_id)
    conn.commit()
    return result


def add_task_dependency_for_decomposition(
    conn: sqlite3.Connection, task_id: int, actor_id: int, depends_on_task_id: int
) -> dict:
    """사용자 요청(2026-11-04, "업무 분해 단위 가이드" 설계) —
    `POST /api/briefs/{brief_id}/tasks` 전용. 막 생성된 Task는 아직
    참가자가 하나도 없으므로 `add_task_dependency()`의
    `task_participant_relationship()` 게이트를 그대로 쓰면 항상
    막힌다 — 의도적으로 그 체크를 생략한다(호출자가 이미 decompose
    엔드포인트의 admin/manager 역할 게이트를 통과했으므로, 참가자
    여부보다 강한 권한 보증이다). **commit은 호출자 책임** — 같은
    배치의 다른 Task/의존관계 삽입과 원자적으로 묶여야 한다."""
    return _insert_task_dependency_row(conn, task_id, actor_id, depends_on_task_id)


def remove_task_dependency(
    conn: sqlite3.Connection, task_id: int, actor_id: int, depends_on_task_id: int
) -> None:
    """`DELETE /api/tasks/{task_id}/dependencies/{depends_on_task_id}`
    (Phase 4) — 설계 문서엔 명시되지 않았지만, 잘못 추가한 의존
    관계를 되돌릴 방법이 전혀 없으면 이 기능 자체가 위험해진다(한 번
    실수하면 영구히 그 task가 막힌 것처럼 보임) — POST의 자연스러운
    대칭으로 추가했다."""
    if task_participant_relationship(conn, task_id, actor_id) is None:
        raise PermissionError("not_a_participant")
    row = conn.execute(
        "SELECT id FROM task_dependencies WHERE task_id = ? "
        "AND depends_on_task_id = ?",
        (task_id, depends_on_task_id),
    ).fetchone()
    if row is None:
        raise ValueError("dependency_not_found")
    conn.execute("DELETE FROM task_dependencies WHERE id = ?", (row["id"],))
    audit.record(
        conn,
        actor_id,
        "task_dependency_removed",
        "task_dependencies",
        row["id"],
        before={"task_id": task_id, "depends_on_task_id": depends_on_task_id},
    )
    conn.commit()


def task_dependency_graph(conn: sqlite3.Connection, task_id: int) -> dict:
    """`GET /api/tasks/{task_id}/dependencies`(Phase 4) — **사용자
    결정**(2026-10-08, "Riido 수준의 그래프 시각화로"): 이 task 하나의
    직접 선행/후행 목록이 아니라, 이 task가 속한 의존관계 그래프의
    **연결된 컴포넌트 전체**(양방향 BFS/DFS)를 반환한다 — 그래야
    화면에서 "이 작업이 전체 흐름의 어디쯤인지" 한눈에 보이는 그래프를
    그릴 수 있다(Riido의 "작업 연결 시각화"가 프로젝트 단위로 보여주는
    것과 같은 이유)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    visited = {task_id}
    stack = [task_id]
    edge_set: set[tuple[int, int]] = set()
    while stack:
        current = stack.pop()
        for row in conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (current,),
        ).fetchall():
            edge_set.add((current, row["depends_on_task_id"]))
            if row["depends_on_task_id"] not in visited:
                visited.add(row["depends_on_task_id"])
                stack.append(row["depends_on_task_id"])
        for row in conn.execute(
            "SELECT task_id FROM task_dependencies WHERE depends_on_task_id = ?",
            (current,),
        ).fetchall():
            edge_set.add((row["task_id"], current))
            if row["task_id"] not in visited:
                visited.add(row["task_id"])
                stack.append(row["task_id"])

    nodes = []
    for node_id in visited:
        row = conn.execute(
            "SELECT id, role_name, status, priority FROM tasks WHERE id = ?",
            (node_id,),
        ).fetchone()
        nodes.append(dict(row))
    return {
        "nodes": nodes,
        "edges": [{"task_id": a, "depends_on_task_id": b} for a, b in sorted(edge_set)],
    }


_ACTIVITY_SUBQUERIES = """
SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'assignment'
  AND al.subject_id IN (SELECT id FROM assignments WHERE task_id = ?)

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'deliverables'
  AND al.subject_id IN (SELECT id FROM deliverables WHERE task_id = ?)

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'deliverable_comments'
  AND al.subject_id IN (
      SELECT dc.id FROM deliverable_comments dc
      JOIN deliverables d ON d.id = dc.deliverable_id
      WHERE d.task_id = ?
  )

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'agent_execution_jobs'
  AND (
    al.subject_id IN (SELECT id FROM agent_execution_jobs WHERE task_id = ?)
    OR (al.subject_id IS NULL AND json_extract(al.after_value, '$.task_id') = ?)
  )

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'approval'
  AND al.subject_id IN (
      SELECT ap.id FROM approvals ap
      WHERE (ap.subject_type = 'assignment'
             AND ap.subject_id IN (SELECT id FROM assignments WHERE task_id = ?))
         OR (ap.subject_type = 'deliverable'
             AND ap.subject_id IN (SELECT id FROM deliverables WHERE task_id = ?))
  )

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'tasks' AND al.subject_id = ?

UNION ALL

SELECT al.id, al.actor_id, al.action, al.subject_type, al.subject_id,
       al.before_value, al.after_value, al.created_at
FROM audit_log al
WHERE al.subject_type = 'task_dependencies'
  AND al.subject_id IN (
      SELECT id FROM task_dependencies
      WHERE task_id = ? OR depends_on_task_id = ?
  )

ORDER BY created_at DESC, id DESC
"""


def task_activity(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """`GET /api/tasks/{task_id}/activity` — 배정·결과물·댓글·Agent
    실행·결재·Task 자체 변경을 한 타임라인으로 합친다. 지금까지는 이
    흐름을 보려면 협업 워크스페이스 화면 여러 섹션(배정 이력, 결과물
    목록, 댓글, 실행 Job, 결재)을 전부 직접 스캔해야 했다.

    `audit_log`는 엔티티별로 제각각인 `subject_type`을 쓰므로(
    `audit.record()` 호출부 전수조사로 확정: 'assignment'/'deliverables'/
    'deliverable_comments'/'agent_execution_jobs'/'approval'/'tasks'/
    'task_dependencies'(Phase 4 추가)), task_id로 되짚어가는 경로가
    엔티티마다 다르다 — `UNION ALL`로 전부 모은다. 한 가지 예외:
    `team_crew_execution_started`(`deliverables.start_team_crew_
    execution()`)는 `subject_id=None`으로 기록되고 대신 `after_value`
    JSON에 `task_id`가 박혀 있다 — `json_extract()`로 그 경우만 따로
    짚는다(SQLite 3.38+ 내장 함수, 이 환경에서 동작 확인됨). `approval`
    은 `subject_id`가 approvals.id라 `approvals.subject_type`
    ('assignment' 또는 'deliverable')으로 한 번 더 분기해야 실제
    assignment/deliverable까지 거쳐 task_id에 닿는다. `task_dependencies`
    는 이 task가 `task_id`·`depends_on_task_id` 어느 쪽이든 걸리면
    포함한다(선행 작업이 추가/제거돼도 이 task의 활동으로 본다)."""
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")

    rows = conn.execute(
        _ACTIVITY_SUBQUERIES,
        (task_id,) * 10,
    ).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        entry["before_value"] = (
            json.loads(entry["before_value"]) if entry["before_value"] else None
        )
        entry["after_value"] = (
            json.loads(entry["after_value"]) if entry["after_value"] else None
        )
        result.append(entry)
    return result
