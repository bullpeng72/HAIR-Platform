"""대화 중심 작업공간(Conversational Workspace) — `docs/
ASANA_PARITY_DESIGN_PLAN.md` §9(2026-10-08, 사용자 요청 "현재 방식의
업무 처리는... 유연성이나 Agility가 부족해. 사용자가 로그인하면
기본적으로 대화창이 주어지고, 다른 사용자나 에이전트(에이전트
그룹)을 호출하거나 skill을 사용하여 업무를 수행하는 방식으로 사용자
경험을 개선... 기존 방식과의 공존(업무 성격에 따라 선택)도 좋아").

F15(Task 참가자 초대+댓글 @멘션)가 "Task가 있어야만" 쓸 수 있던
구조를 깨고, **로그인 직후 바로 쓸 수 있는 독립 엔티티**로 확장한다
— `conversations.linked_task_id`가 채워지면 그 Task 전용 대화,
NULL이면 완전히 독립된 "내 대화"다.

**사용자 결정(2026-10-08)**:
1. 1인당 대화 여러 개 허용(주제별로 "새 대화 시작").
2. Team(에이전트 그룹) 호출 시 워커 Agent별 메시지를 각각 올린다
   (매니저가 합친 응답 하나로 묶지 않는다).
3. "skill 사용"은 (a) 메시지에 스킬을 명시적으로 붙여 AI 응답을
   그 스킬로 그라운딩하는 것(기존 `assist()` 메커니즘 재사용).
4. 미초대 대상 멘션 시 검색 범위는 조직 전체(`search_mentionable()`).
5. `ai_auto_reply` 기본값은 켜짐(1).
로드맵은 전체 범위(9-6)를 한 번에 구현한다.

**세 가지 응답 경로가 서로 다른 "정체성"의 모델을 쓴다**:
- 특정 Agent 멘션 → 그 Agent 자신의 저장된 provider/model
  (`agent_runtime.execute_agent()` 재사용, F15 Phase 3와 같은 패턴 —
  `start_agent_execution()`의 confirmed 배정 요구를 우회).
- 특정 Team 멘션 → 팀 멤버 Agent들 각자의 저장된 provider/model로
  CrewAI 실행(`crew_runtime.run_crew()` 직접 호출 — `run_crew_for_
  team()`은 Task에 confirmed 배정이 있어야만 호출 가능해 대화에는
  안 맞는다, 그 함수가 내부에서 쓰는 저수준 함수를 그대로 가져온다).
- 멘션 없는 평범한 메시지(`ai_auto_reply=1`일 때) 또는 `skill` 명시
  → **대화 자체에 설정된** provider/model(`conversations.llm_provider`
  /`llm_model`)로 `llm.call_llm()` 직접 호출 — agent_pool을 전혀
  거치지 않는, 등록 절차 없는 "기본 AI"다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import agent_runtime, audit, crew_runtime, deliverables, llm, teams

_DEFAULT_SYSTEM_PROMPT = (
    "당신은 HAIR-Platform의 대화형 작업공간에 참여하는 AI 비서입니다. "
    "대화 맥락을 참고해 자연스럽고 간결하게 응답하세요. "
    "최종 결정과 책임은 사람에게 있다는 점을 기억하세요."
)
_CONTEXT_MESSAGE_LIMIT = 10
# agent_pool.id는 AUTOINCREMENT라 1부터 시작 — 0은 "실존하는 agent_pool
# 행이 없는 대화 자체의 기본 AI"를 가리키는 예약 sentinel로 안전하게
# 쓸 수 있다(deliverable_comments.author_id와 같은, FK 없는 다형 컬럼
# 관례를 그대로 따른다).
DEFAULT_AI_AUTHOR_ID = 0


# ---------- 대화 CRUD ----------


def create_conversation(
    conn: sqlite3.Connection,
    actor_id: int,
    title: str,
    linked_task_id: int | None = None,
) -> dict:
    """`linked_task_id`를 주면 그 Task에 종속된 대화다 — 생성자가 그
    Task의 참가자여야 한다(무관한 사람이 아무 Task에나 대화를 붙이는
    것을 막는다). 생성자는 자동으로 첫 참가자가 된다."""
    if linked_task_id is not None:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (linked_task_id,)
        ).fetchone()
        if task is None:
            raise ValueError("task_not_found")
        if (
            deliverables.task_participant_relationship(conn, linked_task_id, actor_id)
            is None
        ):
            raise PermissionError("not_a_task_participant")

    cur = conn.execute(
        "INSERT INTO conversations (created_by, title, linked_task_id) VALUES (?, ?, ?)",
        (actor_id, title, linked_task_id),
    )
    conversation_id = cur.lastrowid
    assert conversation_id is not None
    conn.execute(
        "INSERT INTO conversation_participants (conversation_id, participant_type, "
        "participant_id, invited_by) VALUES (?, 'human', ?, ?)",
        (conversation_id, actor_id, actor_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_created",
        "conversations",
        conversation_id,
        after={"title": title, "linked_task_id": linked_task_id},
    )
    conn.commit()
    detail = get_conversation(conn, conversation_id)
    assert detail is not None
    return detail


def get_conversation(conn: sqlite3.Connection, conversation_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def is_participant(
    conn: sqlite3.Connection, conversation_id: int, actor_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM conversation_participants WHERE conversation_id = ? "
        "AND participant_type = 'human' AND participant_id = ?",
        (conversation_id, actor_id),
    ).fetchone()
    return row is not None


def list_conversations_for_user(conn: sqlite3.Connection, actor_id: int) -> list[dict]:
    """사용자 결정(1) — 1인당 대화가 여러 개(주제별)일 수 있다, 최신
    순으로 목록을 낸다."""
    rows = conn.execute(
        """
        SELECT c.* FROM conversations c
        JOIN conversation_participants cp ON cp.conversation_id = c.id
        WHERE cp.participant_type = 'human' AND cp.participant_id = ?
        ORDER BY c.id DESC
        """,
        (actor_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 참가자 ----------


def invite_participant(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    participant_type: str,
    participant_id: int,
) -> dict:
    """결재 불필요(F15 F21-1과 같은 원칙) — 이미 참가 중인 사람 누구나
    더 초대할 수 있다(이 대화는 조직도 기반 RBAC 바깥의 가벼운 레이어
    라 admin/manager 전용 게이트를 두지 않는다)."""
    if get_conversation(conn, conversation_id) is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")

    if participant_type == "agent":
        found = conn.execute(
            "SELECT id FROM agent_pool WHERE id = ?", (participant_id,)
        ).fetchone()
        if found is None:
            raise ValueError("agent_not_found")
    elif participant_type == "team":
        found = conn.execute(
            "SELECT id FROM teams WHERE id = ?", (participant_id,)
        ).fetchone()
        if found is None:
            raise ValueError("team_not_found")
    else:
        found = conn.execute(
            "SELECT id FROM users WHERE id = ?", (participant_id,)
        ).fetchone()
        if found is None:
            raise ValueError("user_not_found")

    try:
        cur = conn.execute(
            "INSERT INTO conversation_participants (conversation_id, participant_type, "
            "participant_id, invited_by) VALUES (?, ?, ?, ?)",
            (conversation_id, participant_type, participant_id, actor_id),
        )
    except sqlite3.IntegrityError:
        raise ValueError("already_invited") from None
    participant_row_id = cur.lastrowid
    assert participant_row_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_participant_invited",
        "conversation_participants",
        participant_row_id,
        after={
            "conversation_id": conversation_id,
            "participant_type": participant_type,
            "participant_id": participant_id,
        },
    )
    conn.commit()
    return {
        "id": participant_row_id,
        "conversation_id": conversation_id,
        "participant_type": participant_type,
        "participant_id": participant_id,
        "invited_by": actor_id,
    }


def list_participants(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    """`display_name`을 사람(`users.username`)/Agent(`agent_pool.role_name`)
    /Team(`teams.name`) 각각에 맞게 조인해 붙인다 — `@멘션`에 실제로
    써야 하는 정확한 문자열이기도 하다(F15 `list_participants()`와
    같은 이유)."""
    rows = conn.execute(
        "SELECT * FROM conversation_participants WHERE conversation_id = ? "
        "ORDER BY invited_at ASC, id ASC",
        (conversation_id,),
    ).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        if entry["participant_type"] == "agent":
            r = conn.execute(
                "SELECT role_name FROM agent_pool WHERE id = ?",
                (entry["participant_id"],),
            ).fetchone()
            entry["display_name"] = r["role_name"] if r is not None else None
        elif entry["participant_type"] == "team":
            r = conn.execute(
                "SELECT name FROM teams WHERE id = ?", (entry["participant_id"],)
            ).fetchone()
            entry["display_name"] = r["name"] if r is not None else None
        else:
            r = conn.execute(
                "SELECT username FROM users WHERE id = ?", (entry["participant_id"],)
            ).fetchone()
            entry["display_name"] = r["username"] if r is not None else None
        result.append(entry)
    return result


def search_mentionable(conn: sqlite3.Connection, query: str, actor_id: int) -> dict:
    """사용자 결정(4) — 미초대 대상 검색 범위는 조직 전체(같은 Brief/
    Task로 좁히지 않는다). 활성 계정/Agent/팀만 후보로 보여준다. UI가
    이 결과에서 고른 대상을 바로 `invite_participant()`로 초대한
    *뒤에* 멘션 텍스트를 메시지에 끼워 넣는 흐름이다 — 백엔드가 아직
    모르는 이름을 원문에서 파싱해 추측하지 않는다(안전하고 명확함).

    채팅 UI 개선(2026-10-10, 사용자 요청 "채팅에서 @로 호출하려면
    미리 전체 이름을 알고 있어야 해") — 두 가지를 더한다: (1) 이름
    컬럼뿐 아니라 사람의 `human_pool.role`/`skill_tags`, Agent의
    `skill_tags`, Team의 `description`까지 부분 일치시켜 "이름을
    몰라도 역할/스킬로 찾을 수 있게" 한다. (2) `actor_id`가 과거에
    직접 초대한 이력(`conversation_participants.invited_by`)이 있는
    대상을 그 초대 시각 최신순으로 앞세운다 — "자주/최근 부르는
    사람"이 매번 똑같이 타이핑하지 않아도 먼저 보이게."""
    like = f"%{query}%"
    humans = conn.execute(
        """
        SELECT DISTINCT u.id, u.username,
            (SELECT MAX(cp.invited_at) FROM conversation_participants cp
             WHERE cp.participant_type = 'human' AND cp.participant_id = u.id
               AND cp.invited_by = ?) AS last_invited
        FROM users u
        LEFT JOIN human_pool hp ON hp.user_id = u.id
        WHERE u.username LIKE ? OR hp.role LIKE ? OR hp.skill_tags LIKE ?
        ORDER BY last_invited IS NULL, last_invited DESC, u.username
        LIMIT 20
        """,
        (actor_id, like, like, like),
    ).fetchall()
    agents = conn.execute(
        """
        SELECT id, role_name,
            (SELECT MAX(cp.invited_at) FROM conversation_participants cp
             WHERE cp.participant_type = 'agent' AND cp.participant_id = agent_pool.id
               AND cp.invited_by = ?) AS last_invited
        FROM agent_pool
        WHERE status = 'active' AND (role_name LIKE ? OR skill_tags LIKE ?)
        ORDER BY last_invited IS NULL, last_invited DESC, role_name
        LIMIT 20
        """,
        (actor_id, like, like),
    ).fetchall()
    teams_rows = conn.execute(
        """
        SELECT id, name,
            (SELECT MAX(cp.invited_at) FROM conversation_participants cp
             WHERE cp.participant_type = 'team' AND cp.participant_id = teams.id
               AND cp.invited_by = ?) AS last_invited
        FROM teams
        WHERE is_active = 1 AND (name LIKE ? OR description LIKE ?)
        ORDER BY last_invited IS NULL, last_invited DESC, name
        LIMIT 20
        """,
        (actor_id, like, like),
    ).fetchall()
    return {
        "humans": [{"id": r["id"], "username": r["username"]} for r in humans],
        "agents": [{"id": r["id"], "role_name": r["role_name"]} for r in agents],
        "teams": [{"id": r["id"], "name": r["name"]} for r in teams_rows],
    }


# ---------- 모델/자동응답 설정 ----------


def update_model_settings(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    provider: str | None,
    model: str | None,
) -> dict:
    if get_conversation(conn, conversation_id) is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    conn.execute(
        "UPDATE conversations SET llm_provider = ?, llm_model = ? WHERE id = ?",
        (provider, model, conversation_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_model_updated",
        "conversations",
        conversation_id,
        after={"provider": provider, "model": model},
    )
    conn.commit()
    detail = get_conversation(conn, conversation_id)
    assert detail is not None
    return detail


def update_auto_reply(
    conn: sqlite3.Connection, conversation_id: int, actor_id: int, enabled: bool
) -> dict:
    if get_conversation(conn, conversation_id) is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    conn.execute(
        "UPDATE conversations SET ai_auto_reply = ? WHERE id = ?",
        (1 if enabled else 0, conversation_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_auto_reply_updated",
        "conversations",
        conversation_id,
        after={"enabled": enabled},
    )
    conn.commit()
    detail = get_conversation(conn, conversation_id)
    assert detail is not None
    return detail


# ---------- 메시지 ----------


def list_messages(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM conversation_messages WHERE conversation_id = ? "
        "ORDER BY created_at ASC, id ASC",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _detect_mentions(
    conn: sqlite3.Connection, conversation_id: int, content: str
) -> dict:
    """이미 초대된 Agent/Team 중 본문에 "@이름"이 부분 문자열로
    있는지 본다(F15 `detect_mentioned_agents()`와 같은 판정 — 한국어엔
    영어 `\\b` 같은 단어 경계가 없어 정규식 토큰화보다 단순 포함이
    더 안전하다)."""
    rows = conn.execute(
        "SELECT participant_type, participant_id FROM conversation_participants "
        "WHERE conversation_id = ? AND participant_type IN ('agent', 'team')",
        (conversation_id,),
    ).fetchall()
    agents: list[dict] = []
    matched_teams: list[dict] = []
    for row in rows:
        if row["participant_type"] == "agent":
            r = conn.execute(
                "SELECT id, role_name FROM agent_pool WHERE id = ?",
                (row["participant_id"],),
            ).fetchone()
            if r is not None and ("@" + r["role_name"]) in content:
                agents.append(dict(r))
        else:
            r = conn.execute(
                "SELECT id, name FROM teams WHERE id = ?", (row["participant_id"],)
            ).fetchone()
            if r is not None and ("@" + r["name"]) in content:
                matched_teams.append(dict(r))
    return {"agents": agents, "teams": matched_teams}


def _build_transcript(
    conn: sqlite3.Connection, conversation_id: int, limit: int = _CONTEXT_MESSAGE_LIMIT
) -> str:
    """최근 N건을 간단한 transcript로 이어붙인다(멀티턴 대화 맥락 —
    9-6 로드맵 "구 F21-2"). 모든 응답 경로(기본 AI/Agent 멘션/Team
    멘션)가 이 함수 하나를 공유한다."""
    rows = conn.execute(
        "SELECT author_type, content FROM conversation_messages "
        "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    lines = [
        ("AI" if row["author_type"] == "agent" else "사용자") + ": " + row["content"]
        for row in reversed(rows)
    ]
    return "\n".join(lines)


def create_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    content: str,
    skill: str | None = None,
) -> dict:
    """사용자 메시지를 저장하고, 어떤 트리거가 필요한지 판정까지만
    한다(실제 LLM 호출은 `app.py`가 `BackgroundTasks`로 스케줄 —
    F15 Phase 3와 같은 2단계 패턴). `skill`이 있으면(사용자 결정 3)
    명시적 요청이라 `ai_auto_reply` 설정과 무관하게 항상 트리거한다."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not content or not content.strip():
        raise ValueError("empty_content")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    if skill is not None:
        found_skill = conn.execute(
            "SELECT 1 FROM skill_pool WHERE name = ?", (skill,)
        ).fetchone()
        if found_skill is None:
            raise ValueError("skill_not_found")

    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content, used_skill) VALUES (?, 'human', ?, ?, ?)",
        (conversation_id, actor_id, content, skill),
    )
    message_id = cur.lastrowid
    assert message_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_message_created",
        "conversation_messages",
        message_id,
        after={"conversation_id": conversation_id, "author_type": "human"},
    )

    mentions = _detect_mentions(conn, conversation_id, content)
    conn.commit()

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "mentioned_agents": mentions["agents"],
        "mentioned_teams": mentions["teams"],
        "skill": skill,
        "ai_auto_reply": bool(conv["ai_auto_reply"]),
        "has_mentions": bool(mentions["agents"] or mentions["teams"]),
    }


# ---------- 공용 job 마감 처리 ----------


def _finish_job_with_message(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    author_id: int,
    content: str,
    actor_id: int,
    used_skill: str | None = None,
) -> None:
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content, used_skill) VALUES (?, 'agent', ?, ?, ?)",
        (conversation_id, author_id, content, used_skill),
    )
    message_id = cur.lastrowid
    conn.execute(
        "UPDATE conversation_jobs SET status = 'succeeded', finished_at = datetime('now') "
        "WHERE id = ?",
        (job_id,),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_message_created",
        "conversation_messages",
        message_id,
        after={"conversation_id": conversation_id, "author_type": "agent"},
    )
    conn.commit()


def _fail_job(
    conn: sqlite3.Connection, job_id: int, actor_id: int, exc: Exception
) -> None:
    conn.rollback()
    error = str(exc)
    conn.execute(
        "UPDATE conversation_jobs SET status = 'failed', error = ?, "
        "finished_at = datetime('now') WHERE id = ?",
        (error, job_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_job_failed",
        "conversation_jobs",
        job_id,
        after={"error": error},
    )
    conn.commit()


def list_jobs(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM conversation_jobs WHERE conversation_id = ? "
        "ORDER BY created_at DESC, id DESC",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 트리거: 특정 Agent 멘션 ----------


def start_agent_mention_job(
    conn: sqlite3.Connection, conversation_id: int, agent_id: int, actor_id: int
) -> int:
    cur = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type, agent_id, status) "
        "VALUES (?, 'agent_mention', ?, 'running')",
        (conversation_id, agent_id),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_agent_mention_started",
        "conversation_jobs",
        job_id,
        after={"conversation_id": conversation_id, "agent_id": agent_id},
    )
    conn.commit()
    return job_id


def run_agent_mention_job(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    agent_id: int,
    actor_id: int,
) -> None:
    """F15 Phase 3와 완전히 같은 패턴 — `agent_runtime.execute_agent()`
    를 confirmed 배정 확인 없이 직접 호출한다(그 Agent 자신의 저장된
    provider/model을 그대로 쓴다)."""
    try:
        transcript = _build_transcript(conn, conversation_id)
        output = agent_runtime.execute_agent(
            conn, agent_id, actor_id, transcript or "대화를 시작합니다."
        )
        _finish_job_with_message(
            conn, job_id, conversation_id, agent_id, output, actor_id
        )
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계
        _fail_job(conn, job_id, actor_id, exc)


# ---------- 트리거: 특정 Team(에이전트 그룹) 멘션 ----------


def start_team_mention_job(
    conn: sqlite3.Connection, conversation_id: int, team_id: int, actor_id: int
) -> int:
    cur = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type, team_id, status) "
        "VALUES (?, 'team_mention', ?, 'running')",
        (conversation_id, team_id),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_team_mention_started",
        "conversation_jobs",
        job_id,
        after={"conversation_id": conversation_id, "team_id": team_id},
    )
    conn.commit()
    return job_id


def run_team_mention_job(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    team_id: int,
    actor_id: int,
) -> None:
    """사용자 결정(2) — 워커 Agent별 메시지를 각각 올린다(매니저 조율
    결과 하나로 합치지 않는다). `crew_runtime.run_crew_for_team()`은
    Task에 confirmed 배정이 있어야만 호출 가능해 대화에는 안 맞는다
    — 그 함수가 내부적으로 쓰는 저수준 `run_crew()`를 직접 호출한다
    (Task/배정 확인 전혀 없음, 팀 멤버 Agent들 각자의 저장된
    provider/model로 CrewAI 실행)."""
    try:
        agent_ids = teams.active_agent_member_ids(conn, team_id)
        if not agent_ids:
            raise ValueError("team_has_no_active_agents")
        transcript = _build_transcript(conn, conversation_id)
        task_descriptions = [transcript or "대화를 시작합니다."] * len(agent_ids)
        result = crew_runtime.run_crew(conn, agent_ids, task_descriptions, actor_id)
        posted = 0
        for agent in result["agents"]:
            if agent["output"] is None:
                continue  # 매니저 역할 — 산출물 없음(run_crew()의 기존 관례)
            conn.execute(
                "INSERT INTO conversation_messages (conversation_id, author_type, "
                "author_id, content) VALUES (?, 'agent', ?, ?)",
                (conversation_id, agent["agent_id"], agent["output"]),
            )
            posted += 1
        conn.execute(
            "UPDATE conversation_jobs SET status = 'succeeded', "
            "finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        audit.record(
            conn,
            actor_id,
            "conversation_team_mention_completed",
            "conversation_jobs",
            job_id,
            after={
                "conversation_id": conversation_id,
                "team_id": team_id,
                "messages_posted": posted,
            },
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        _fail_job(conn, job_id, actor_id, exc)


# ---------- 트리거: 대화 자체의 기본 AI(자동응답/skill 명시) ----------


def start_default_ai_job(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    skill: str | None = None,
) -> int:
    trigger_type = "skill" if skill else "auto_reply"
    cur = conn.execute(
        "INSERT INTO conversation_jobs (conversation_id, trigger_type, status) "
        "VALUES (?, ?, 'running')",
        (conversation_id, trigger_type),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_ai_reply_started",
        "conversation_jobs",
        job_id,
        after={"conversation_id": conversation_id, "skill": skill},
    )
    conn.commit()
    return job_id


def run_default_ai_job(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    actor_id: int,
    skill: str | None = None,
) -> None:
    """agent_pool을 전혀 거치지 않는 "기본 AI" — `llm.call_llm()`을
    직접 호출한다(Task의 "LLM으로 도와줘"(`assist()`)가 쓰는 것과
    같은 원시 함수). `skill`이 있으면 그 스킬로, `linked_task_id`가
    있으면 그 Task의 `required_skills`로, 둘 다 없으면 일반 비서
    역할의 기본 system prompt로 그라운딩한다."""
    try:
        conv = get_conversation(conn, conversation_id)
        assert conv is not None
        provider, model = llm.resolve_provider_and_model(
            conv["llm_provider"], conv["llm_model"]
        )
        if skill:
            system = deliverables.skill_grounded_system_prompt(
                conn, "대화 참여자", [skill]
            )
        elif conv["linked_task_id"] is not None:
            task = conn.execute(
                "SELECT role_name, required_skills FROM tasks WHERE id = ?",
                (conv["linked_task_id"],),
            ).fetchone()
            system = (
                deliverables.skill_grounded_system_prompt(
                    conn, task["role_name"], json.loads(task["required_skills"])
                )
                if task is not None
                else _DEFAULT_SYSTEM_PROMPT
            )
        else:
            system = _DEFAULT_SYSTEM_PROMPT
        transcript = _build_transcript(conn, conversation_id)
        output = llm.call_llm(provider, model, system, transcript)
        _finish_job_with_message(
            conn,
            job_id,
            conversation_id,
            DEFAULT_AI_AUTHOR_ID,
            output,
            actor_id,
            used_skill=skill,
        )
    except Exception as exc:  # noqa: BLE001
        _fail_job(conn, job_id, actor_id, exc)
