"""대화 중심 작업공간(Conversational Workspace) — 사용자
요청(2026-10-08) "현재 방식의 업무 처리는... 유연성이나 Agility가 부족해. 사용자가 로그인하면
기본적으로 대화창이 주어지고, 다른 사용자나 에이전트(에이전트
그룹)을 호출하거나 skill을 사용하여 업무를 수행하는 방식으로 사용자
경험을 개선... 기존 방식과의 공존(업무 성격에 따라 선택)도 좋아").

Task 참가자 초대+댓글 @멘션이 "Task가 있어야만" 쓸 수 있던
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
  (`agent_runtime.execute_agent()` 재사용, Phase 3와 같은 패턴 —
  `start_agent_execution()`의 confirmed 배정 요구를 우회).
- 특정 Team 멘션 → `crew_runtime.run_crew()` 직접 호출(`run_crew_for_
  team()`은 Task에 confirmed 배정이 있어야만 호출 가능해 대화에는
  안 맞는다, 그 함수가 내부에서 쓰는 저수준 함수를 그대로 가져온다) —
  **정정(2026-10-15)**: 이 줄은 원래 "팀 멤버 Agent들 각자의 저장된
  provider/model로 실행"이라고 적었으나 사실이 아니었다. `run_crew()`는
  Crew 전체가 공유하는 단일 provider/model만 받으므로(혼합 provider
  자체를 지원하지 않는다), 실제로는 호출 시점에 넘긴(또는 자동 판별된)
  **하나의** provider/model을 팀 멤버 전원이 공유한다(`run_team_mention_
  job()`의 2026-10-15 분석 주석 참고). hierarchical 구조로 설계된
  팀(예: "자율 개발 크루")은 `teams.manager_agent_id_for_team()`으로
  식별해 `process="hierarchical"`로 라우팅한다.
- 멘션 없는 평범한 메시지(`ai_auto_reply=1`일 때) 또는 `skill` 명시
  → **대화 자체에 설정된** provider/model(`conversations.llm_provider`
  /`llm_model`)로 `llm.call_llm()` 직접 호출 — agent_pool을 전혀
  거치지 않는, 등록 절차 없는 "기본 AI"다.
"""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import (
    agent_runtime,
    audit,
    crew_runtime,
    deliverables,
    llm,
    pipeline_runtime,
    pool_registry,
    teams,
    vectorstore,
)

_DEFAULT_SYSTEM_PROMPT = (
    "당신은 HAIR-Platform의 대화형 작업공간에 참여하는 AI 비서입니다. "
    "대화 맥락을 참고해 자연스럽고 간결하게 응답하세요. "
    "최종 결정과 책임은 사람에게 있다는 점을 기억하세요."
)
_CONTEXT_MESSAGE_LIMIT = 10
# 사용자 요청(2026-10-18, "@자율 개발 크루" 품질 분석 후속) — 실측으로
# 확인한 "앵커링" 문제(최근 10건 안에 과거 생성 코드가 여러 건 있으면
# 새 spec보다 그 과거 코드를 거의 그대로 재사용함)를 프롬프트로 명시
# 차단한다. `run_team_mention_job()`에서만 쓴다(그 문제가 실제로
# 재현된 경로).
#
# 재발 확인(2026-10-19, "(제목 없음) 대화 분석") — 위 문구는 "복사하지
# 마라"만 막았지 "빈손으로 응답하지 마라"는 막지 않았다. 실측: 대화록에
# 이미 완성된 코드가 포함된 기획서가 있으면(§3 skill 경계 문제와 별개로
# 실제 재현), 매니저가 그 코드를 복사도, 설명도 안 하고 그냥
# "위 코드는 이미 완성된 게임입니다"라는 한 줄만 쓰고 끝냈다(실제
# 산출물 0건, Gate A/C가 그대로 fail) — "복사 금지"라는 글자 그대로의
# 규칙은 지켰지만 의도(실제로 쓸 수 있는 새 산출물 제출)는 완전히
# 비껴갔다. "이번 응답 자체에 완성된 산출물을 반드시 포함하라"는 요구를
# 명시적으로 추가해 이 빈틈을 막는다.
_ANTI_ANCHORING_NOTE = (
    "\n\n[중요] 위 대화 기록에 이전에 생성된 코드나 산출물이 포함돼 "
    "있어도, 그것을 그대로 복사하거나 재사용하지 마세요. 가장 최근 "
    "사용자 요청과 그 요청이 참조하는 가장 최근의 스펙/요구사항 "
    "문서를 기준으로 처음부터 다시 작성하세요. 과거 산출물은 참고 "
    "자료일 뿐이며, 새 요구사항과 다르면 전부 새로 작성해야 합니다. "
    "구현하지 않은 기능을 구현했다고 설명하지 마세요 — 실제로 작성한 "
    "코드에 있는 것만 설명하세요. 당신의 이번 응답 자체에 실제로 "
    "완성된 산출물(코드 전체 등)을 직접 포함해야 합니다 — "
    '"위에 이미 완성돼 있습니다", "이전 내용을 참고하세요", '
    '"위 코드는 이미 동작합니다"처럼 산출물 제시를 생략하거나 대화 '
    "기록의 다른 메시지를 가리키기만 하는 응답은 금지합니다. 그런 "
    "메시지가 보이더라도 당신이 맡은 작업의 결과물은 이번 응답 안에 "
    "처음부터 끝까지 직접 작성하세요."
)
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


def _cascade_delete_conversation(
    conn: sqlite3.Connection, conversation_id: int
) -> None:
    """메시지·작업(job)·참가자·대화 행 자체까지 전부 지운다(`data_admin.
    delete_task()`와 같은 "완전한 연쇄 삭제" 관례). `delete_conversation()`
    과 `leave_conversation()`(마지막 참가자가 나간 경우)이 공유한다 —
    커밋/오딧 기록은 호출자가 각자의 맥락에 맞게 남긴다."""
    conn.execute(
        "DELETE FROM conversation_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute(
        "DELETE FROM conversation_jobs WHERE conversation_id = ?", (conversation_id,)
    )
    conn.execute(
        "DELETE FROM conversation_participants WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def delete_conversation(
    conn: sqlite3.Connection, conversation_id: int, actor_id: int
) -> None:
    """사용자 요청(2026-10-10, "대화 세션 삭제/나가기 기능을 구현해줘")
    — 대화를 통째로 지운다. 초대는 참가자 누구나 할 수 있는 가벼운
    레이어지만(`invite_participant()` 참고), 삭제는 되돌릴 수 없어
    만든 사람(`created_by`)만 할 수 있게 좁힌다 — 다른 참가자는
    자기 참가만 빼는 `leave_conversation()`을 쓴다.

    만든 사람이라도 이미 `leave_conversation()`으로 나갔다면(참가자가
    아님) 거부한다 — 나간 뒤에도 "원 제작자"라는 이유로 남은 사람들의
    대화를 바깥에서 지울 수 있게 두지 않는다."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    if conv["created_by"] != actor_id:
        raise PermissionError("not_creator")

    _cascade_delete_conversation(conn, conversation_id)
    audit.record(
        conn,
        actor_id,
        "conversation_deleted",
        "conversations",
        conversation_id,
        before={"title": conv["title"], "linked_task_id": conv["linked_task_id"]},
    )
    conn.commit()


# ---------- 참가자 ----------


def leave_conversation(
    conn: sqlite3.Connection, conversation_id: int, actor_id: int
) -> dict:
    """참가자가 자기 자신을 대화에서 뺀다 — 다른 사람을 빼는 기능은
    없다(admin/manager 게이트 없이 참가자 서로를 신뢰하는 가벼운
    레이어이기 때문, `invite_participant()`와 같은 전제).

    나간 뒤 human 참가자가 0명이 되면(마지막 사람이 나간 경우) 누구도
    다시 들어올 길이 없는 유령 대화가 DB에 영원히 남는다 — 그 경우
    `delete_conversation()`과 같은 연쇄 삭제로 대화 자체를 지운다."""
    if get_conversation(conn, conversation_id) is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")

    conn.execute(
        "DELETE FROM conversation_participants WHERE conversation_id = ? "
        "AND participant_type = 'human' AND participant_id = ?",
        (conversation_id, actor_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_participant_left",
        "conversation_participants",
        conversation_id,
    )

    remaining_humans = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_participants WHERE "
        "conversation_id = ? AND participant_type = 'human'",
        (conversation_id,),
    ).fetchone()["c"]
    conversation_deleted = remaining_humans == 0
    if conversation_deleted:
        _cascade_delete_conversation(conn, conversation_id)
        audit.record(
            conn,
            actor_id,
            "conversation_deleted",
            "conversations",
            conversation_id,
            after={"reason": "last_participant_left"},
        )

    conn.commit()
    return {
        "conversation_id": conversation_id,
        "left": True,
        "conversation_deleted": conversation_deleted,
    }


def invite_participant(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    participant_type: str,
    participant_id: int,
) -> dict:
    """결재 불필요(같은 원칙) — 이미 참가 중인 사람 누구나
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
        # 사용자 보고(2026-10-28, "대화 호출시에... 비활성화 된 사람...
        # 목록이 노출되는 오류") — search_mentionable()은 이제
        # is_active=1만 보여주지만, 그 검색을 거치지 않고 참가자 ID를
        # 직접 지정해 초대하는 경로(API 직접 호출 등)까지 막아야 같은
        # 구멍이 다시 안 생긴다.
        found = conn.execute(
            "SELECT id FROM users WHERE id = ? AND is_active = 1", (participant_id,)
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
    """`display_name`을 사람(`users.username`)/Agent(`agent_pool.name`,
    없으면 `role_name`)/Team(`teams.name`) 각각에 맞게 조인해 붙인다 —
    `@멘션`에 실제로 써야 하는 정확한 문자열이기도 하다(같은 이유).

    사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
    (채팅 포함)에도 활용될 수 있도록") — Agent가 지정한 이름이 있으면
    그걸 최우선으로 보여준다/멘션에 쓴다."""
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
                "SELECT role_name, name FROM agent_pool WHERE id = ?",
                (entry["participant_id"],),
            ).fetchone()
            entry["display_name"] = (
                (r["name"] or r["role_name"]) if r is not None else None
            )
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
        WHERE u.is_active = 1
          AND (u.username LIKE ? OR hp.role LIKE ? OR hp.skill_tags LIKE ?)
        ORDER BY last_invited IS NULL, last_invited DESC, u.username
        LIMIT 20
        """,
        (actor_id, like, like, like),
    ).fetchall()
    agents = conn.execute(
        """
        SELECT id, role_name, name,
            (SELECT MAX(cp.invited_at) FROM conversation_participants cp
             WHERE cp.participant_type = 'agent' AND cp.participant_id = agent_pool.id
               AND cp.invited_by = ?) AS last_invited
        FROM agent_pool
        WHERE status = 'active'
          AND (role_name LIKE ? OR skill_tags LIKE ? OR name LIKE ?)
        ORDER BY last_invited IS NULL, last_invited DESC, COALESCE(name, role_name)
        LIMIT 20
        """,
        (actor_id, like, like, like),
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
        # "role_name" 키는 그대로 두되 값은 지정된 이름을 우선한다 — 이미
        # 여러 화면이 이 키로 "@멘션" 텍스트/표시 라벨을 그대로 쓰고
        # 있어(사용자 요청, 2026-10-13) 키를 바꾸면 그 화면들을 전부
        # 고쳐야 한다. 값만 바뀌므로 그 화면들은 자동으로 이름을 쓰게
        # 된다.
        "agents": [
            {"id": r["id"], "role_name": r["name"] or r["role_name"]} for r in agents
        ],
        "teams": [{"id": r["id"], "name": r["name"]} for r in teams_rows],
    }


def mark_conversation_read(
    conn: sqlite3.Connection, conversation_id: int, user_id: int
) -> None:
    """사용자 요청(2026-10-14, "호출이 안 되는 거 같아" — 실측으로
    원인 발견) — 처음엔 "읽음" 상태를 서버에 저장하지 않고 프런트엔드
    메모리 커서(최초 폴링은 알림 생략)로 중복 알림을 막았는데, 그 탭이
    멘션이 이미 와 있는 *뒤에* 열리면(실제 재현된 상황: admin이 먼저
    세 번 호출하고 pm_kim은 그 뒤에 로그인) 그 멘션들이 전부 "최초
    폴링"에 걸려 영영 알림이 안 떴다. 서버가 참가자별 "이 대화를 마지막
    으로 본 메시지"를 기억해야 한다 — `GET /messages`가 호출될 때마다
    (=이 대화를 실제로 보고 있을 때) 호출해 갱신한다."""
    latest = conn.execute(
        "SELECT MAX(id) AS m FROM conversation_messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()["m"]
    if latest is None:
        return
    conn.execute(
        "UPDATE conversation_participants SET last_read_message_id = ? "
        "WHERE conversation_id = ? AND participant_type = 'human' AND participant_id = ?",
        (latest, conversation_id, user_id),
    )
    conn.commit()


def list_my_recent_mentions(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """사용자 요청(2026-10-14, "호출 받은 사람에게 브라우저 알림이
    오고, 알림 클릭시 대화 참가 가능하도록") — 프런트엔드가 주기적으로
    폴링해 "내가 참가 중인 대화들 중 나를 @멘션한, 아직 안 읽은 메시지"
    를 찾는다. `_detect_mentions()`와 같은 "@username 부분 문자열 포함"
    판정을 쓰되, 대화 하나가 아니라 **내가 참가 중인 모든 대화**를
    대상으로 한다는 점이 다르다.

    "안 읽음" 기준은 `conversation_participants.last_read_message_id`
    (`mark_conversation_read()`가 그 대화를 실제로 열 때마다 갱신) —
    프런트엔드가 직접 커서를 들고 있지 않는다. 그래야 "멘션이 온 뒤에
    탭을 연 경우"에도 그 대화를 실제로 열기 전까지는 계속 "안 읽음"으로
    남아 알림이 뜬다(처음 설계는 클라이언트 메모리 커서로 최초 폴링을
    무조건 건너뛰어서 이 경우를 놓쳤다 — 실측 재현으로 발견)."""
    user = conn.execute(
        "SELECT username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None:
        return []
    like_pattern = f"%@{user['username']}%"
    rows = conn.execute(
        """
        SELECT cm.id, cm.conversation_id, cm.content, cm.created_at, c.title
        FROM conversation_messages cm
        JOIN conversation_participants cp
          ON cp.conversation_id = cm.conversation_id
         AND cp.participant_type = 'human' AND cp.participant_id = ?
        JOIN conversations c ON c.id = cm.conversation_id
        WHERE cm.content LIKE ?
          AND NOT (cm.author_type = 'human' AND cm.author_id = ?)
          AND cm.id > COALESCE(cp.last_read_message_id, 0)
        ORDER BY cm.id ASC
        LIMIT 50
        """,
        [user_id, like_pattern, user_id],
    ).fetchall()
    return [
        {
            "message_id": r["id"],
            "conversation_id": r["conversation_id"],
            "conversation_title": r["title"] or "(제목 없음)",
            "content": r["content"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


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
    messages = [dict(r) for r in rows]
    # Gate A-G 안전장치(2026-10-18) — 저장은 JSON 문자열이지만 화면이
    # 바로 쓸 수 있게 파싱해서 내려준다(없으면 None 그대로).
    for m in messages:
        if m.get("gate_scores"):
            m["gate_scores"] = json.loads(m["gate_scores"])
    return messages


def delete_message(
    conn: sqlite3.Connection, conversation_id: int, message_id: int, actor_id: int
) -> None:
    """사용자 요청(2026-10-14, "단위 메시지 삭제 기능") — 대화 전체
    삭제(`delete_conversation()`, 생성자 전용)와 별개로 메시지 한 건만
    지운다. 메시지의 "작성자"가 사람 메시지와 AI/Agent 메시지에서
    서로 다른 의미라(AI 응답은 사람이 직접 쓴 게 아니라 **트리거한**
    것) 권한을 두 겹으로 둔다 — 사용자 결정: 본인이 보낸 사람 메시지는
    본인이, 그 외 모든 메시지(AI/Agent 응답 포함)는 이미 대화 전체
    삭제 권한을 가진 생성자(`created_by`)가 지울 수 있다. 둘 다
    해당 안 되면 거부."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    message = conn.execute(
        "SELECT * FROM conversation_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    ).fetchone()
    if message is None:
        raise ValueError("message_not_found")
    is_own_human_message = (
        message["author_type"] == "human" and message["author_id"] == actor_id
    )
    is_creator = conv["created_by"] == actor_id
    if not (is_own_human_message or is_creator):
        raise PermissionError("not_allowed_to_delete_this_message")

    conn.execute("DELETE FROM conversation_messages WHERE id = ?", (message_id,))
    audit.record(
        conn,
        actor_id,
        "conversation_message_deleted",
        "conversation_messages",
        message_id,
        before={
            "conversation_id": conversation_id,
            "author_type": message["author_type"],
            "author_id": message["author_id"],
        },
    )
    conn.commit()


def _detect_mentions(
    conn: sqlite3.Connection,
    conversation_id: int,
    content: str,
    author_human_id: int | None = None,
) -> dict:
    """이미 초대된 사람/Agent/Team 중 본문에 "@이름"이 부분 문자열로
    있는지 본다(`detect_mentioned_agents()`와 같은 판정 — 한국어엔
    영어 `\\b` 같은 단어 경계가 없어 정규식 토큰화보다 단순 포함이
    더 안전하다).

    사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
    (채팅 포함)에도 활용될 수 있도록") — `@role_name`(기존 방식)과
    `@지정한이름`(신규) 둘 다 인정한다. 이름을 나중에 지정해도 기존
    대화에서 `@역할명`으로 부르던 습관이 그대로 깨지지 않도록 하위
    호환을 유지한다.

    **사람 멘션도 감지한다(2026-10-14 버그 수정)** — 사용자 보고: "@pm_kim
    처럼 사람을 멘션했는데 AI가 기본 모델로 자동응답했다." 원인은 이
    함수가 `participant_type IN ('agent', 'team')`만 조회해서, 사람을
    멘션해도 `has_mentions=False`로 판정됐다는 것 — `app.py`의 트리거
    분기(`not has_mentions and ai_auto_reply`)가 "멘션이 전혀 없는
    메시지"와 "사람을 멘션한 메시지"를 구분하지 못해 똑같이 기본 AI
    자동응답을 켰다. 사람에게는 (Agent/Team과 달리) 멘션해도 실행할
    job이 없지만, `has_mentions`를 정확히 채우기 위해 탐지는 한다 —
    그래야 "호출 대상이 없으면 기본 모델, 있으면 그 대상만" 원칙대로
    사람을 멘션했을 때 기본 AI가 끼어들지 않는다(사람이 직접 답해야
    하는 상황이므로).

    `author_human_id`(2026-10-14, 사용자 보고 "본인이 본인에게 멘션을
    보낼 수 있는데, 이 기능이 맞는지/필요한지" — 직접 재현해 확인한
    버그) — 보낸 사람 자신의 "@본인아이디"는 사람 멘션으로 치지 않는다.
    `list_my_recent_mentions()`(브라우저 알림 경로)는 이미 자기 자신이
    보낸 메시지를 제외하고 있었는데, 이 함수(AI 자동응답 억제 판정
    경로)는 빠져 있었다 — 그래서 "@본인아이디"가 들어간 메시지를
    보내면(의도한 타겟이 실제로는 아무도 없는데도) 사람을 호출한 것으로
    오판정돼 기본 AI 자동응답이 조용히 꺼졌다(직접 재현: member1이
    "@member1 나한테 메모"를 보내면 `ai_auto_reply=True`인데도
    `triggered_jobs == []`). 본인 멘션은 "아무도 안 부른 것"과 똑같이
    취급해야 "호출 대상이 없으면 기본 모델이 답한다" 원칙이 일관되게
    지켜진다."""
    rows = conn.execute(
        "SELECT participant_type, participant_id FROM conversation_participants "
        "WHERE conversation_id = ? AND participant_type IN ('human', 'agent', 'team')",
        (conversation_id,),
    ).fetchall()
    humans: list[dict] = []
    agents: list[dict] = []
    matched_teams: list[dict] = []
    for row in rows:
        if row["participant_type"] == "human":
            if row["participant_id"] == author_human_id:
                continue
            r = conn.execute(
                "SELECT id, username FROM users WHERE id = ?",
                (row["participant_id"],),
            ).fetchone()
            if r is not None and ("@" + r["username"]) in content:
                humans.append({"id": r["id"], "username": r["username"]})
        elif row["participant_type"] == "agent":
            r = conn.execute(
                "SELECT id, role_name, name FROM agent_pool WHERE id = ?",
                (row["participant_id"],),
            ).fetchone()
            if r is not None and (
                ("@" + r["role_name"]) in content
                or (r["name"] and ("@" + r["name"]) in content)
            ):
                agents.append({"id": r["id"], "role_name": r["name"] or r["role_name"]})
        else:
            r = conn.execute(
                "SELECT id, name FROM teams WHERE id = ?", (row["participant_id"],)
            ).fetchone()
            if r is not None and ("@" + r["name"]) in content:
                matched_teams.append(dict(r))
    return {"humans": humans, "agents": agents, "teams": matched_teams}


def _uninvited_mention_candidates(
    conn: sqlite3.Connection, conversation_id: int, content: str
) -> list[str]:
    """사용자 보고(2026-10-19, "'기획서 작성자'로 설정했는데 기본 AI
    (openai/gpt-4o-mini)가 대신 응답했다") 원인 분석 — `_detect_mentions()`
    는 의도적으로 "이미 초대된 참가자"만 본다(원문에서 이름을 추측해
    자동 초대하지 않는 안전 설계, `search_mentionable()` 참고). 자동완성
    드롭다운을 거치지 않고 `@역할명`을 직접 타이핑하면 그 대상이 아직
    이 대화의 참가자가 아닌 한 멘션으로 인식되지 않고, `ai_auto_reply`가
    켜져 있으면 기본 AI가 아무 안내 없이 대신 답한다 — 사용자는 특정
    Agent의 설정(provider/model/스킬)으로 답을 받았다고 착각하기 쉽다.
    사용자 결정(2026-10-19) — 자동 초대는 여전히 하지 않되(기존 안전
    설계 유지), 이 상황(원문에 실제 활성 Agent/Team 이름과 일치하는
    `@텍스트`가 있는데 초대가 안 돼 있음)만 찾아 경고 문구를 붙일 수
    있게 후보 이름 목록을 반환한다. 사람은 대상에서 제외한다(사람을
    멘션해도 실행할 job이 없어 "기본 AI 대신 응답"과 섞여 헷갈릴 소지가
    적다)."""
    invited_agent_ids = {
        r["participant_id"]
        for r in conn.execute(
            "SELECT participant_id FROM conversation_participants "
            "WHERE conversation_id = ? AND participant_type = 'agent'",
            (conversation_id,),
        ).fetchall()
    }
    invited_team_ids = {
        r["participant_id"]
        for r in conn.execute(
            "SELECT participant_id FROM conversation_participants "
            "WHERE conversation_id = ? AND participant_type = 'team'",
            (conversation_id,),
        ).fetchall()
    }
    found: list[str] = []
    for row in conn.execute(
        "SELECT id, role_name, name FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        if row["id"] in invited_agent_ids:
            continue
        label = row["name"] or row["role_name"]
        if ("@" + label) in content:
            found.append(label)
    for row in conn.execute(
        "SELECT id, name FROM teams WHERE is_active = 1"
    ).fetchall():
        if row["id"] in invited_team_ids:
            continue
        if ("@" + row["name"]) in content:
            found.append(row["name"])
    return found


def _format_messages_for_summary(rows: list[sqlite3.Row]) -> str:
    return "\n".join(
        ("AI" if row["author_type"] == "agent" else "사용자") + ": " + row["content"]
        for row in rows
    )


def _ensure_context_summary(
    conn: sqlite3.Connection, conversation_id: int, conv: sqlite3.Row
) -> str | None:
    """사용자 요청(2026-10-14, "10건 이전의 메시지는 요약 형태로
    전달되도록 개선해줘") — `_build_transcript()`가 최근
    `_CONTEXT_MESSAGE_LIMIT`건보다 오래된 메시지를 그냥 버리던 것을,
    `conversations.context_summary`에 캐시된 요약으로 대체한다.

    **증분 갱신**(비용 관리) — 메시지가 쌓일 때마다 과거 전체를 처음부터
    다시 요약하면, 대화가 길어질수록 요약 1회 비용이 계속 커진다. 대신
    "지난번 요약이 어디까지 반영됐는지"(`context_summary_through_id`)를
    기억해, **그 이후 창 밖으로 새로 밀려난 메시지만** 기존 요약에
    얹어서 갱신한다 — 매번 비슷하게 작은 비용으로 끝난다. 창이 아직
    그대로면(새로 밀려난 메시지가 없으면) 캐시를 그대로 돌려주고 LLM을
    아예 호출하지 않는다."""
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM conversation_messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()["c"]
    if total <= _CONTEXT_MESSAGE_LIMIT:
        return None  # 아직 요약할 "오래된 과거"가 없다 — 전부 최근 창 안에 있음

    cutoff_offset = total - _CONTEXT_MESSAGE_LIMIT - 1
    cutoff_row = conn.execute(
        "SELECT id FROM conversation_messages WHERE conversation_id = ? "
        "ORDER BY id ASC LIMIT 1 OFFSET ?",
        (conversation_id, cutoff_offset),
    ).fetchone()
    cutoff_id = cutoff_row["id"]

    existing_summary = conv["context_summary"]
    existing_through_id = conv["context_summary_through_id"]
    if existing_through_id == cutoff_id:
        return existing_summary  # 이미 최신 — LLM 호출 불필요

    new_rows = conn.execute(
        "SELECT author_type, content FROM conversation_messages "
        "WHERE conversation_id = ? AND id > ? AND id <= ? ORDER BY id ASC",
        (conversation_id, existing_through_id or 0, cutoff_id),
    ).fetchall()
    new_text = _format_messages_for_summary(new_rows)

    provider, model = llm.resolve_provider_and_model(
        conv["llm_provider"], conv["llm_model"], conn=conn
    )
    prompt = (
        (f"[기존 요약]\n{existing_summary}\n\n" if existing_summary else "")
        + f"[추가로 반영할 대화]\n{new_text}\n\n"
        "위 내용을 반영해 전체를 하나의 요약으로 다시 작성하세요 — 핵심 결정·"
        "합의된 내용·중요한 숫자/이름 위주로, 5문장 이내로 간결하게."
    )
    new_summary = llm.call_llm(
        provider,
        model,
        "당신은 대화 요약 보조자입니다. 다른 설명 없이 요약 본문만 출력하세요.",
        prompt,
    )
    conn.execute(
        "UPDATE conversations SET context_summary = ?, context_summary_through_id = ? "
        "WHERE id = ?",
        (new_summary, cutoff_id, conversation_id),
    )
    conn.commit()
    return new_summary


_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)\n```", re.DOTALL)
_CODE_FENCE_STRIP_THRESHOLD_LINES = 15


def _strip_large_code_fences(content: str) -> str:
    """사용자 보고(2026-10-19, "(제목 없음) 대화 분석" — "자율 개발 크루"가
    `_build_transcript()`로 받은 과거 메시지에 이미 완성된 코드(기획서
    작성자가 §5에 넣은 265줄짜리 게임 전체)가 그대로 포함돼 있자, 그
    코드를 다시 쓰지도 복사하지도 않고 그냥 "위 코드는 이미 완성돼
    있다"고만 답하고 끝낸 실제 사례에서 — 원인의 절반은 이 함수가
    메시지 전문을 글자 수 제한 없이 그대로 넘긴다는 것이었다(나머지
    절반은 `_ANTI_ANCHORING_NOTE`의 허점, 위에서 따로 메움). 긴 코드
    블록(15줄 초과)을 생략 표시로 바꿔, 매니저/Agent가 "이미 완성된
    산출물"을 보지 못하게 한다 — 짧은 예시 스니펫(15줄 이하)은 맥락상
    유용한 경우가 많아 그대로 둔다. 이 함수는 `_build_transcript()`
    에서만 쓰여 모든 응답 경로(기본 AI/Agent 멘션/Team 멘션)에 똑같이
    적용된다 — 토큰 낭비와 앵커링 위험을 전반적으로 줄이는 효과도
    겸한다."""

    def _replace(match: re.Match[str]) -> str:
        lang, body = match.group(1), match.group(2)
        line_count = body.count("\n") + 1
        if line_count <= _CODE_FENCE_STRIP_THRESHOLD_LINES:
            return match.group(0)
        return (
            "```" + lang + "\n"
            f"[코드 {line_count}줄 생략 — 이 메시지의 전체 내용은 대화 화면에서 "
            "직접 확인하세요. 이 내용을 그대로 복사하지 말고, 이번 요청에 맞게 "
            "처음부터 다시 작성해야 합니다.]\n"
            "```"
        )

    return _CODE_FENCE_RE.sub(_replace, content)


def _build_transcript(
    conn: sqlite3.Connection, conversation_id: int, limit: int = _CONTEXT_MESSAGE_LIMIT
) -> str:
    """최근 N건 + (있으면) 그 이전 과거의 캐시된 요약을 이어붙인다(멀티턴
    대화 맥락). 모든 응답 경로(기본 AI/Agent 멘션/Team 멘션)가 이 함수
    하나를 공유한다."""
    conv = conn.execute(
        "SELECT llm_provider, llm_model, context_summary, context_summary_through_id "
        "FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    summary = _ensure_context_summary(conn, conversation_id, conv) if conv else None

    rows = conn.execute(
        "SELECT author_type, content FROM conversation_messages "
        "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    lines = [
        ("AI" if row["author_type"] == "agent" else "사용자")
        + ": "
        + _strip_large_code_fences(row["content"])
        for row in reversed(rows)
    ]
    if summary:
        lines.insert(0, f"(이전 대화 요약: {summary})")
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
    Phase 3와 같은 2단계 패턴). `skill`이 있으면(사용자 결정 3)
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

    mentions = _detect_mentions(
        conn, conversation_id, content, author_human_id=actor_id
    )
    conn.commit()

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "mentioned_humans": mentions["humans"],
        "mentioned_agents": mentions["agents"],
        "mentioned_teams": mentions["teams"],
        "skill": skill,
        "ai_auto_reply": bool(conv["ai_auto_reply"]),
        # 버그 수정(2026-10-14, "Human을 호출했지만 AI가 자동 응답했다")
        # — 사람 멘션도 "호출 대상이 있다"로 쳐야, `app.py`의
        # `not has_mentions and ai_auto_reply` 분기가 "멘션 없음"과
        # "사람을 멘션함"을 더는 혼동하지 않는다(사람을 멘션했을 땐
        # 실행할 job은 없지만, 기본 AI가 대신 끼어들어서도 안 된다).
        "has_mentions": bool(
            mentions["humans"] or mentions["agents"] or mentions["teams"]
        ),
    }


def create_attachment_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    filename: str,
    text: str,
) -> int:
    """사용자 요청(2026-10-26, "A안 — 대화에 주입되는 문서는... 저장되지
    않고 휘발되는 내용물") — 업로드한 파일을 VectorDB(영구·전역)에
    색인하는 대신, 그 대화의 메시지로 직접 집어넣는다. 이렇게 하면
    `conversations._build_transcript()`가 기존 대화 메시지와 똑같이
    이 내용을 프롬프트에 포함시키고, 이 대화가 지워지면(이미 있는
    `_cascade_delete_conversation()`) 같이 사라진다 — 별도 보관/만료
    설계가 필요 없다(휘발성이 저장을 안 함으로써 구조적으로 보장됨).

    `create_message()`와 같은 INSERT 모양이지만 멘션 감지·`ai_auto_reply`
    트리거 판정은 하지 않는다(파일 첨부 자체는 "누군가를 호출"하는
    행위가 아니라서 — 호출은 뒤따르는 별도 메시지에서 사람이 직접
    한다)."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")

    content = f"\U0001f4ce 첨부 자료 — {filename}\n\n{text}"
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) "
        "VALUES (?, 'human', ?, ?)",
        (conversation_id, actor_id, content),
    )
    message_id = cur.lastrowid
    assert message_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_attachment_added",
        "conversation_messages",
        message_id,
        after={
            "conversation_id": conversation_id,
            "filename": filename,
            "char_count": len(text),
        },
    )
    conn.commit()
    return message_id


# ---------- 공용 job 마감 처리 ----------


def _finish_job_with_message(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    author_id: int,
    content: str,
    actor_id: int,
    used_skill: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> None:
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content, used_skill, llm_provider, llm_model) VALUES (?, 'agent', ?, ?, ?, ?, ?)",
        (conversation_id, author_id, content, used_skill, llm_provider, llm_model),
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


def _describe_job_failure(exc: Exception, trigger_type: str | None = None) -> str:
    """사용자 보고(2026-10-15, "@자율 개발 크루... 일부 응답이 실패했습니다:
    Expecting value: line 1 column 1 (char 0)") 재발 방지 — 이 메시지는
    CrewAI/litellm 내부(에이전트 간 위임 도구 호출 인자를 JSON으로 파싱하는
    지점 등 — `json.loads()`가 빈/깨진 완성 결과를 받으면 그대로 난다)에서
    새어나온 raw `json.JSONDecodeError`를 그대로 화면에 띄운 것이었다 —
    어느 Agent가 실패했는지도, 재시도하면 나아지는지도 알 수 없어
    사용자가 아무 행동도 취할 수 없었다(`webui.py`가 `job.error`를 그대로
    이어붙여 보여준다). 원본 예외 문자열은 `_fail_job()`이 `audit_log`에는
    그대로 남긴다 — 여기서 바뀌는 건 화면에 노출되는 문구뿐이다."""
    if isinstance(exc, json.JSONDecodeError):
        return (
            "AI 모델이 일부 작업에서 올바른 형식의 응답을 만들지 못했습니다"
            "(여러 Agent가 협업·위임하는 과정에서 간헐적으로 발생할 수 있는 "
            "일시적 오류입니다) — 잠시 후 다시 시도해 주세요. 반복되면 호출한 "
            "Agent/Team 규모를 줄이거나 다른 LLM Provider로 바꿔보세요."
        )
    # 사용자 보고(2026-10-18, "@자율 개발 크루" 품질 분석 중 실측) — 대화가
    # 길어진 상태에서 QA/품질 관리자처럼 표가 많은 긴 응답을 요구하는
    # Agent를 멘션하면, 응답 생성이 `llm.py`의 넉넉해진 타임아웃(900초)
    # 조차 넘기는 경우가 있다(raw 예외 문자열이 그냥 "timed out"이라
    # 어느 Agent가, 왜 실패했는지 사용자가 알 길이 없었다).
    #
    # 재발/정정(2026-10-28, 사용자 분석 요청 — "팀의 경우 15분 제한으로
    # 실패했다") — 실측 확인 결과 Team 멘션(`crew_runtime._crewai_llm()`)
    # 경로는 애초에 `llm.py`의 900초 타임아웃을 거친 적이 없다(완전히
    # 별도 LLM 생성 경로). 그런데도 이 메시지는 "timed out" 문자열만
    # 보고 실제 적용된 값과 무관하게 "15분"을 고정으로 박아 넣고
    # 있었다 — Team에는 틀린 숫자를 보여준 셈이다. `trigger_type`으로
    # 실제 어느 타임아웃 값이 적용됐는지 구분해 정확한 분만 보여준다.
    if "timed out" in str(exc).lower() or isinstance(exc, TimeoutError):
        if trigger_type == "team_mention":
            minutes = int(crew_runtime.CREWAI_LLM_TIMEOUT_SECONDS // 60)
            return (
                f"👥 팀(여러 Agent 협업) 응답 생성이 시간 제한(약 {minutes}분)을 "
                "넘겨 중단됐습니다 — 팀원이 많거나 위임·종합할 내용이 복잡할 때 "
                "발생할 수 있는 문제입니다. 요청을 더 간결하게 나눠서 다시 "
                "시도하거나, 팀 구성(멤버 수)을 줄여보세요."
            )
        return (
            "AI 모델의 응답 생성이 시간 제한을 넘겨 중단됐습니다 — 대화가 "
            "길어졌거나 요청한 내용이 복잡할 때 발생할 수 있는 문제입니다. "
            "요청을 더 간결하게 나눠서 다시 시도하거나, 대화가 많이 길어졌다면 "
            "새 대화를 시작해 보세요."
        )
    return str(exc)


def _fail_job(
    conn: sqlite3.Connection, job_id: int, actor_id: int, exc: Exception
) -> None:
    conn.rollback()
    raw_error = str(exc)
    trigger_row = conn.execute(
        "SELECT trigger_type FROM conversation_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    trigger_type = trigger_row["trigger_type"] if trigger_row else None
    error = _describe_job_failure(exc, trigger_type)
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
        after={"error": raw_error},
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
    """Phase 3와 완전히 같은 패턴 — `agent_runtime.execute_agent()`
    를 confirmed 배정 확인 없이 직접 호출한다(그 Agent 자신의 저장된
    provider/model을 그대로 쓴다)."""
    try:
        transcript = _build_transcript(conn, conversation_id)
        sink: dict = {}
        output = agent_runtime.execute_agent(
            conn,
            agent_id,
            actor_id,
            transcript or "대화를 시작합니다.",
            retrieval_sink=sink,
        )
        _finish_job_with_message(
            conn,
            job_id,
            conversation_id,
            agent_id,
            output,
            actor_id,
            llm_provider=sink.get("llm_provider"),
            llm_model=sink.get("llm_model"),
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
    (Task/배정 확인 전혀 없음). 정정(2026-10-15) — 바로 위 문단은
    예전에 "팀 멤버 Agent들 각자의 저장된 provider/model로 CrewAI
    실행"이라고 적었으나 사실이 아니었다(`run_crew()`는 Crew 전체가
    공유하는 **단일** provider/model만 받는다, `memory=True` 임베더도
    하나뿐이라 애초에 혼합 provider를 지원하는 구조가 아니다) — 실제
    동작은 호출 시점에 넘긴 `provider`/`model`(없으면 환경변수/API
    키로 자동 판별된 **하나의** 값)을 전원이 공유한다. 잘못된 기록을
    남겨두면 다음에 또 이걸 "이미 되는 기능"으로 오해하므로 바로잡는다.

    사용자 보고(2026-10-15, "@자율 개발 크루... 일부 응답이 실패했습니다:
    Expecting value: line 1 column 1 (char 0)") 원인 분석 — "자율 개발
    크루" 같은 매니저+워커 hierarchical 팀(`test_phase47_
    autonomous_dev_crew.py` 참고)도 여기선 구분 없이 `process`
    기본값(sequential)으로 넘어갔다. 매니저까지 포함한 전원이 동일한
    전체 대화록을 중복으로 처음부터 수행하며 서로 자유롭게 위임할 수
    있어, 설계된 "매니저 1명이 조율해 위임"보다 호출/위임 트래픽이
    훨씬 크고 체이닝된 컨텍스트도 더 빨리 불어난다 — CrewAI/litellm이
    위임 도구 호출 인자를 JSON으로 파싱하다 실패하는 조건(모델이 빈/
    깨진 응답을 낼 때)에 더 쉽게 걸리는 근본 원인이었다.
    `teams.manager_agent_id_for_team()`으로 매니저를 식별해, 있으면
    설계된 대로 hierarchical로 라우팅한다(없는 팀은 기존 sequential
    그대로 — 하위 호환).

    사용자 요청(2026-10-18, "@자율 개발 크루... 생성 결과물의 품질을
    개선하기 위해... 어떤 개선이 필요할지 분석") 후속 — 실측(실제 대화
    7의 "위 spec을 준수해 게임을 개발해줘" 실행)으로 확인한 두 문제를
    메운다:
    1) `_build_transcript()`가 최근 10건을 원문 그대로 넣는데, 그
       10건 안에 과거에 생성된 거대한 코드 산출물(수천 자)이 여러 건
       섞여 있으면 매니저가 방금 받은 새 spec보다 그 과거 코드에
       "앵커링"돼 거의 그대로 재사용했다(ARIA·localStorage 등 구현
       안 한 기능을 구현했다고 거짓 서술까지 했다). 과거 코드를
       맹목적으로 베끼지 말라는 지시를 명시적으로 추가한다
       (`_ANTI_ANCHORING_NOTE`).
    2) Gate A-G가 fail해도 이 경로는 측정만 하고 버려졌다 — 메시지에
       `gate_scores`/`delegation_count`를 같이 저장해 화면에서 바로
       보이게 한다(아래 INSERT).

    사용자 요청(2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우
    파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수 있도록
    재구성해줘") — 위 두 문제(앵커링/Gate 방치)를 메워도 이 경로는
    여전히 매니저 LLM 한 번의 위임+자유 서술 종합일 뿐, 코드 검증/QA
    기능테스트/품질 관리 같은 실제 검증 단계가 없다. `teams.
    pipeline_template_id`가 연결된 팀(현재는 "자율 개발 크루"만)은
    이 단발 실행 대신 `pipeline_runtime`의 단계형 엔진(Gate 기준
    자동 재시도, 재시도 소진 시 사람 승인 에스컬레이션)을 태운다 —
    `_run_team_mention_via_pipeline()` 참고. 연결 안 된 팀은 아래
    기존 로직 그대로(하위 호환)."""
    team_row = conn.execute(
        "SELECT pipeline_template_id FROM teams WHERE id = ?", (team_id,)
    ).fetchone()
    if team_row is not None and team_row["pipeline_template_id"] is not None:
        _run_team_mention_via_pipeline(
            conn,
            job_id,
            conversation_id,
            team_id,
            team_row["pipeline_template_id"],
            actor_id,
        )
        return
    try:
        agent_ids = teams.active_agent_member_ids(conn, team_id)
        if not agent_ids:
            raise ValueError("team_has_no_active_agents")
        transcript = (
            _build_transcript(conn, conversation_id) or "대화를 시작합니다."
        ) + _ANTI_ANCHORING_NOTE
        task_descriptions = [transcript] * len(agent_ids)
        manager_agent_id = teams.manager_agent_id_for_team(conn, team_id)
        if manager_agent_id is not None:
            # 사용자 보고(2026-10-19, "매니저의 저장된 설정을 쓰도록") —
            # `run_crew()`는 Crew 전체가 공유하는 provider/model 하나만
            # 받는데, 여기서 안 넘기면 `llm.resolve_provider_and_model()`
            # 의 자동판별(ANTHROPIC_API_KEY 없으면 OPENAI_API_KEY)로
            # 떨어진다 — "자율 개발 크루" 12명 전원이 각자 Ollama(무료)로
            # 설정돼 있어도 실제로는 유료 openai로 실행되는 결과를 낳았다
            # (실측 확인). hierarchical 실행의 provider/model은 매니저가
            # 조율 전체를 담당하므로, 매니저 Agent 자신에게 저장된
            # 설정을 그대로 쓴다.
            manager_row = conn.execute(
                "SELECT llm_provider, llm_model FROM agent_pool WHERE id = ?",
                (manager_agent_id,),
            ).fetchone()
            result = crew_runtime.run_crew(
                conn,
                agent_ids,
                task_descriptions,
                actor_id,
                process="hierarchical",
                manager_agent_id=manager_agent_id,
                provider=manager_row["llm_provider"] if manager_row else None,
                model=manager_row["llm_model"] if manager_row else None,
            )
        else:
            result = crew_runtime.run_crew(conn, agent_ids, task_descriptions, actor_id)
        gate_scores_json = json.dumps(result.get("gate_scores"), ensure_ascii=False)
        delegation_count = len(result.get("delegations") or [])
        posted = 0
        for agent in result["agents"]:
            if agent["output"] is None:
                continue  # 매니저 역할 — 산출물 없음(run_crew()의 기존 관례)
            conn.execute(
                "INSERT INTO conversation_messages (conversation_id, author_type, "
                "author_id, content, llm_provider, llm_model, gate_scores, "
                "delegation_count) VALUES (?, 'agent', ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    agent["agent_id"],
                    agent["output"],
                    result.get("resolved_provider"),
                    result.get("resolved_model"),
                    gate_scores_json,
                    delegation_count,
                ),
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


_PIPELINE_STAGE_STATUS_LABEL = {
    "running": "진행 중",
    "passed": "통과",
    "failed_retrying": "재시도 중",
    "escalated": "승인 대기(재시도 소진)",
    "approved": "승인됨",
    "rejected": "반려",
}
_PIPELINE_RUN_STATUS_LABEL = {
    "running": "진행 중",
    "paused_for_approval": "사람 승인 대기",
    "completed": "완료",
    "failed": "실패",
    "cancelled": "취소됨",
}


def ensure_task_for_conversation(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    origin_note: str | None = None,
) -> int:
    """사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물(필요시
    지정한 결과물)이 갤러리에 가도록") — 대화에 결과물을 남기려는
    모든 경로(파이프라인 자동 실행, 수동 "결과물로 저장")가 공유하는
    "이 대화가 쓸 Task를 확보한다" 로직. 원래 `_run_team_mention_via_
    pipeline()` 안에 있던 걸 분리했다.

    대화가 이미 특정 Task에 연결(`linked_task_id`)돼 있으면 그대로
    쓴다. 없으면 최소 synthetic Brief+Task를 만들고, **만든 뒤
    `conversations.linked_task_id`를 그 Task로 갱신한다** — 예전엔
    이 갱신이 없어서 같은 대화에서 파이프라인 연결 팀을 두 번째
    멘션하면 Task/Brief가 매번 새로 생겨 결과물이 흩어졌다(실측으로
    발견한 버그, 이번에 같이 고침). 새 스키마 컬럼은 추가하지 않는다
    (최소 침습 원칙 유지)."""
    conv = conn.execute(
        "SELECT title, linked_task_id FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conv is None:
        raise ValueError("conversation_not_found")
    if conv["linked_task_id"] is not None:
        return conv["linked_task_id"]

    task_title = conv["title"] or f"대화 #{conversation_id}"
    brief_cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) "
        "VALUES (?, ?, '[]')",
        (actor_id, f"[대화 '{task_title}']"),
    )
    brief_id = brief_cur.lastrowid
    task_cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, notes) VALUES (?, ?, ?)",
        (
            brief_id,
            task_title,
            f"대화 '{task_title}'에서 {origin_note or '자동 생성됨.'}",
        ),
    )
    task_id = task_cur.lastrowid
    assert task_id is not None
    conn.execute(
        "UPDATE conversations SET linked_task_id = ? WHERE id = ?",
        (task_id, conversation_id),
    )
    return task_id


def save_message_as_deliverable(
    conn: sqlite3.Connection, conversation_id: int, message_id: int, actor_id: int
) -> int:
    """사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물(필요시
    지정한 결과물)이 갤러리에 가도록") — 대화의 모든 메시지를 자동으로
    결과물화하면 탐색적 잡담까지 다 쌓여 갤러리가 지저분해진다. 대신
    사람이 "이건 남길 만하다"고 판단한 메시지 하나를 명시적으로
    결과물로 승격한다 — 사람 메시지든 Agent 응답이든 동일하게
    동작한다(원 메시지의 author_type/author_id를 그대로 물려받아
    "누가/무엇이 실제로 작성했는지"가 결과물에도 정확히 남는다)."""
    message = conn.execute(
        "SELECT * FROM conversation_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    ).fetchone()
    if message is None:
        raise ValueError("message_not_found")

    task_id = ensure_task_for_conversation(
        conn, conversation_id, actor_id, origin_note="메시지를 결과물로 저장해 자동 생성됨."
    )
    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, ?, ?, ?, 'text', 'approved')",
        (task_id, message["author_type"], message["author_id"], message["content"]),
    )
    deliverable_id = cur.lastrowid
    assert deliverable_id is not None
    audit.record(
        conn,
        actor_id,
        "conversation_message_saved_as_deliverable",
        "deliverable",
        deliverable_id,
        after={
            "conversation_id": conversation_id,
            "message_id": message_id,
            "task_id": task_id,
        },
    )
    conn.commit()
    return deliverable_id


def save_message_as_knowledge(
    conn: sqlite3.Connection, conversation_id: int, message_id: int, actor_id: int
) -> int:
    """사용자 요청(2026-11-10, "대화에서 생성된 결과물을 지식으로 보내는
    경로가 없음") — `save_message_as_deliverable()`과 같은 "사람이 남길
    만하다고 판단한 메시지 하나만 명시적으로" 원칙을 그대로 따르되,
    보내는 곳이 갤러리(deliverables) 대신 지식베이스(VectorDB,
    `knowledge_chunks`)라는 점만 다르다. 임베딩 API를 실제로 호출하므로
    비용이 발생한다(opt-in — 명시적으로 호출했을 때만, `agent_runtime.
    index_knowledge()`와 동일한 전제)."""
    message = conn.execute(
        "SELECT * FROM conversation_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    ).fetchone()
    if message is None:
        raise ValueError("message_not_found")

    chunk_id = vectorstore.index_text(conn, None, message["content"])
    audit.record(
        conn,
        actor_id,
        "conversation_message_indexed_as_knowledge",
        "knowledge_chunks",
        chunk_id,
        after={"conversation_id": conversation_id, "message_id": message_id},
    )
    conn.commit()
    return chunk_id


def _run_team_mention_via_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    team_id: int,
    pipeline_template_id: int,
    actor_id: int,
) -> None:
    """`run_team_mention_job()`이 `teams.pipeline_template_id`가 연결된
    팀일 때 위임한다 — `pipeline_runtime.py`의 단계 실행/Gate 재시도/
    에스컬레이션 로직은 전혀 수정하지 않고 그대로 재사용한다(Task
    협업 워크스페이스 경로가 이미 실전 검증한 바로 그 엔진). 이
    함수가 하는 일은 순전히 "대화라는 Task 없는 맥락을 어떻게 그
    엔진에 먹이고, 진행 상황을 어떻게 다시 대화로 돌려주는가"뿐이다.

    **Task 확보** — 대화가 `linked_task_id`로 이미 특정 Task에
    종속돼 있으면 그 Task를 그대로 쓴다. 없으면(가장 흔한 자유
    대화의 경우) `briefs`/`tasks`에 최소 synthetic 행을 만든다
    (`eval_wiring.py`의 `_run_generation()`이 쓰는 것과 같은 2-INSERT
    패턴) — `tasks.notes`에 출처를 남겨 Task Board에서 구분 가능하게
    한다. 새 스키마 컬럼은 추가하지 않는다(최소 침습).

    **진행 상황 전달** — `pipeline_runtime.run_pipeline_stage_job()`은
    이미 승인 대기/완료/실패 중 하나에 도달할 때까지 재귀적으로
    돈다. 그 시점에 바로 `conversation_jobs`를 마감하고(무기한 폴링
    방지) `post_pipeline_run_status_message()`로 대화에 진행 요약
    메시지를 올린다. 승인 후 재개됐을 때의 후속 메시지는
    `pipeline_runs.source_conversation_id`를 보고 `app.py`의
    `decide_approval()`이 같은 헬퍼를 한 번 더 호출한다."""
    try:
        # 사용자 보고(2026-10-29, "브레이크 아웃 게임 생성 대화에서 마지막
        # 승인 처리했지만 생성된 코드가 제시안된 원인") 근본원인 수정 —
        # `pipeline_runtime._stage_initial_input()`은 1단계 프롬프트를
        # `tasks.role_name`(+사람이 올린 deliverable이 있으면 그것)으로만
        # 만든다. synthetic task의 role_name을 지금까지 팀 이름("자율
        # 개발 크루")으로 넣어왔는데, 그게 그대로 1단계 Agent에게 "자율
        # 개발 크루 업무를 수행하라"는 프롬프트가 돼버렸다 — 실제 대화
        # 내용(예: "브레이크아웃 게임을 개발해줘")은 어디에도 안 들어가
        # 1단계부터 완전히 엉뚱한 주제(크루 자체를 관리하는 시스템)로
        # 산출물이 나왔고, 이후 모든 단계가 그 틀린 기반 위에서 그대로
        # 이어졌다(Gate는 "글이 갖춰져 있는지"만 보는 휴리스틱이라 내용이
        # 틀려도 계속 통과시켰다). 대화 전사록을 **사람이 작성한
        # deliverable**로 심어두면 `_stage_initial_input()`의 기존
        # "사람 작성 자료" 폴백이 그대로 집어가므로, 파이프라인 엔진은
        # 전혀 안 고치고 1단계가 받는 입력만 바로잡을 수 있다.
        transcript = _build_transcript(conn, conversation_id) or "대화를 시작합니다."
        team_row = conn.execute(
            "SELECT name FROM teams WHERE id = ?", (team_id,)
        ).fetchone()
        team_name = team_row["name"] if team_row else "팀"
        task_id = ensure_task_for_conversation(
            conn,
            conversation_id,
            actor_id,
            origin_note=f"@{team_name} 호출로 자동 생성됨(파이프라인 기반 실행).",
        )

        conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'human', ?, ?, 'text', 'approved')",
            (task_id, actor_id, transcript),
        )

        run_id = pipeline_runtime.start_pipeline_run(
            conn, actor_id, pipeline_template_id, task_id
        )
        conn.execute(
            "UPDATE pipeline_runs SET source_conversation_id = ? WHERE id = ?",
            (conversation_id, run_id),
        )
        conn.commit()

        # 사용자 보고(2026-10-28, "결재 승인 되었으나, 대화창의 경우
        # 이후 진행 확인되지 않음") 분석 후 조치 — 승인이 필요 없는
        # 단계가 여러 개 연달아 자동 통과하면(Gate 0.0) `run_pipeline_
        # stage_job()` 호출 하나가 그 전부를 조용히 끝낼 때까지 수
        # 분~수십 분 아무 메시지도 안 올라왔다. 매 단계 resolve마다
        # 콜백으로 진행 메시지를 올려 "멈춘 게 아니라 진행 중"임을
        # 그때그때 보여준다 — 끝나고 나서 한 번 더 부르면 콜백이 이미
        # 마지막 상태도 올렸으므로 중복이라 빼낸다.
        pipeline_runtime.run_pipeline_stage_job(
            conn,
            run_id,
            actor_id,
            on_stage_resolved=lambda: post_pipeline_run_status_message(conn, run_id),
        )

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
                "pipeline_run_id": run_id,
            },
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        _fail_job(conn, job_id, actor_id, exc)


def post_pipeline_run_status_message(conn: sqlite3.Connection, run_id: int) -> None:
    """`pipeline_runs.source_conversation_id`가 있는 run의 진행 상황을
    그 대화에 메시지 한 건으로 요약해 올린다 — 최초 트리거
    (`_run_team_mention_via_pipeline()`)와 승인 후 재개
    (`app.py`의 `decide_approval()`) 양쪽에서 공유하는 헬퍼다."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["source_conversation_id"] is None:
        return
    template = conn.execute(
        "SELECT name FROM pipeline_templates WHERE id = ?",
        (run["pipeline_template_id"],),
    ).fetchone()
    stages = conn.execute(
        "SELECT id, seq_order, name FROM pipeline_stages "
        "WHERE pipeline_template_id = ? ORDER BY seq_order",
        (run["pipeline_template_id"],),
    ).fetchall()
    lines = [f"**파이프라인 '{template['name'] if template else '?'}' 진행 상황**"]
    for stage in stages:
        latest = conn.execute(
            "SELECT id, status, attempt FROM pipeline_run_stages WHERE pipeline_run_id = ? "
            "AND stage_id = ? ORDER BY attempt DESC, id DESC LIMIT 1",
            (run_id, stage["id"]),
        ).fetchone()
        if latest is None:
            label = "대기"
        else:
            status_value = str(latest["status"])
            label = _PIPELINE_STAGE_STATUS_LABEL.get(status_value, status_value)
            if status_value == "failed_retrying":
                label += f"({latest['attempt']}회차)"
            # 사용자 요청(2026-10-28, "승인자가 누구인지 표시되도록
            # 개선") — 사람이 실제로 결정(승인/반려)한 단계는 그
            # 결재자 이름을 같이 보여준다. 다단계 결재 체인이면 가장
            # 마지막(= id가 가장 큰)으로 처리된 결정자를 보여준다 —
            # Gate 자동 통과(승인 단계 자체가 없는 "통과")는 애초에
            # `approvals` 행이 없으니 그대로 빠진다.
            if status_value in ("approved", "rejected"):
                decider = conn.execute(
                    """
                    SELECT u.username FROM approvals a
                    JOIN users u ON u.id = a.approved_by
                    WHERE a.subject_type = 'pipeline_run_stage' AND a.subject_id = ?
                      AND a.approved_by IS NOT NULL
                    ORDER BY a.id DESC LIMIT 1
                    """,
                    (latest["id"],),
                ).fetchone()
                if decider is not None:
                    label += f"(결재: {decider['username']})"
        marker = "▶" if stage["seq_order"] == run["current_stage_seq"] else " "
        lines.append(f"{marker} {stage['seq_order']}. {stage['name']} — {label}")
    overall = _PIPELINE_RUN_STATUS_LABEL.get(run["status"], run["status"])
    lines.append(f"\n전체 상태: **{overall}**")
    if run["status"] == "paused_for_approval":
        # 사용자 요청(2026-10-28, "사람 승인 대기로 표시, 승인자가
        # 누구인지 알 수 없음") — 지정된 승인자(`approver_id`)가 있으면
        # 이름을 밝히고, 없으면(상사 체인이 비어 admin/manager 누구나
        # 처리 가능한 경우) 그렇다고 명시한다.
        pending_approval = conn.execute(
            """
            SELECT a.approver_id, u.username AS approver_username
            FROM approvals a
            JOIN pipeline_run_stages prs ON a.subject_type = 'pipeline_run_stage'
                AND a.subject_id = prs.id
            LEFT JOIN users u ON u.id = a.approver_id
            WHERE prs.pipeline_run_id = ? AND a.status = 'pending'
            ORDER BY a.id DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if pending_approval is not None and pending_approval["approver_username"]:
            # 사용자 요청(2026-11-05, 로드맵 6번 "자동 단계적 에스컬레이션")
            # — 실측 확인: 승인자 이름이 여기 적혀도 `@` 접두사가 없어
            # `list_my_recent_mentions()`(`LIKE '%@username%'`)에 전혀
            # 안 걸렸고, `@`를 붙여도 승인자가 이 대화의 참가자가 아니면
            # 그 매칭 자체가 JOIN `conversation_participants`에서 빠진다
            # — 즉 에스컬레이션이 걸려도 승인자는 대화창에서 전혀 알
            # 길이 없었다(결재함을 직접 열어봐야만 보임). 승인자를
            # (아직 참가자가 아니면) 이 대화에 추가한 뒤 실제로 `@`
            # 멘션한다 — "에스컬레이션"이 이름만 뿐 사람에게 실제로
            # 전달되지 않는 반쪽짜리였던 걸 고친다.
            approver_id = pending_approval["approver_id"]
            if approver_id is not None and not is_participant(
                conn, run["source_conversation_id"], approver_id
            ):
                try:
                    conn.execute(
                        "INSERT INTO conversation_participants (conversation_id, "
                        "participant_type, participant_id, invited_by) "
                        "VALUES (?, 'human', ?, ?)",
                        (run["source_conversation_id"], approver_id, run["actor_id"]),
                    )
                    audit.record(
                        conn,
                        run["actor_id"],
                        "pipeline_escalation_approver_invited",
                        "conversation_participants",
                        run["source_conversation_id"],
                        after={"approver_id": approver_id},
                    )
                except sqlite3.IntegrityError:
                    pass  # 경합으로 이미 추가됨 — 무시(멱등).
            lines.append(
                f"사람 승인이 필요합니다 — @{pending_approval['approver_username']}님이 "
                "결재함에서 확인해주세요."
            )
        else:
            lines.append(
                "사람 승인이 필요합니다 — 지정된 승인자가 없어 admin/manager 누구나 "
                "결재함에서 확인할 수 있습니다."
            )
    elif run["status"] == "failed":
        lines.append("재시도를 모두 소진해 실패로 종료됐습니다.")
    content = "\n".join(lines)

    team_row = conn.execute(
        """
        SELECT t.id FROM teams t
        WHERE t.pipeline_template_id = ?
        """,
        (run["pipeline_template_id"],),
    ).fetchone()
    author_agent_id = None
    if team_row is not None:
        author_agent_id = teams.manager_agent_id_for_team(conn, team_row["id"])
        if author_agent_id is None:
            active_ids = teams.active_agent_member_ids(conn, team_row["id"])
            author_agent_id = active_ids[0] if active_ids else None
    if author_agent_id is None:
        return  # 메시지를 붙일 Agent가 없음(팀이 비활성화됐거나 멤버가 전혀 없음) — 조용히 포기.

    conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content) VALUES (?, 'agent', ?, ?)",
        (run["source_conversation_id"], author_agent_id, content),
    )
    conn.commit()


def run_pipeline_stage_job_and_notify(
    conn: sqlite3.Connection, run_id: int, actor_id: int
) -> None:
    """`app.py`의 `decide_approval()`이 `pipeline_run_stage` 승인/반려
    처리 후 재개 BackgroundTask로 쓴다(`pipeline_runtime.run_pipeline_
    stage_job` 자리 대체) — 승인 후 재개된 단계부터 다시 자동 진행되는
    매 단계마다(승인 불필요 단계가 여러 개 연달아 지나가도 각각)
    대화에서 트리거된 run이면(`source_conversation_id`가 있으면)
    진행 상황 메시지를 대화에 올린다(2026-10-28, 사용자 보고 "결재
    승인 되었으나, 대화창의 경우 이후 진행 확인되지 않음" — 예전엔
    전체가 다 끝난 뒤 한 번만 올려서, 자동 통과 단계가 여러 개
    이어지면 그 긴 시간 동안 대화창에 아무 신호가 없었다). Task
    협업 워크스페이스에서 직접 띄운 run은 `post_pipeline_run_status_
    message()`가 `source_conversation_id`가 없음을 보고 조용히 아무
    것도 안 해 기존 동작과 100% 동일하다."""
    pipeline_runtime.run_pipeline_stage_job(
        conn,
        run_id,
        actor_id,
        on_stage_resolved=lambda: post_pipeline_run_status_message(conn, run_id),
    )


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
            conv["llm_provider"], conv["llm_model"], conn=conn
        )
        last_human = conn.execute(
            "SELECT content FROM conversation_messages WHERE conversation_id = ? "
            "AND author_type = 'human' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        skills_for_augmentation: list[str] = []
        if skill:
            skills_for_augmentation = [skill]
            system = deliverables.skill_grounded_system_prompt(
                conn, "대화 참여자", [skill]
            )
        elif conv["linked_task_id"] is not None:
            task = conn.execute(
                "SELECT role_name, required_skills FROM tasks WHERE id = ?",
                (conv["linked_task_id"],),
            ).fetchone()
            if task is not None:
                skills_for_augmentation = json.loads(task["required_skills"])
                system = deliverables.skill_grounded_system_prompt(
                    conn, task["role_name"], skills_for_augmentation
                )
            else:
                system = _DEFAULT_SYSTEM_PROMPT
        else:
            system = _DEFAULT_SYSTEM_PROMPT
        # 사용자 요청(2026-10-22, "일반웹검색 스킬 점검해줘" 조사 결과) —
        # 이 경로(agent_pool/CrewAI Tool 호출 루프가 전혀 없는 "기본 AI")는
        # 지금까지 skill_pool.description 텍스트만 주입하고 실제 조회를
        # 전혀 하지 않았다 — 모델이 "검색하겠습니다"라고 의도만 서술하고
        # 끝나는 버그로 보고됐다. `agent_runtime.retrieve_context()`(native
        # 도구: 외부검색/Wikipedia·RAG·관계질의)로 실제 조회 결과를
        # system prompt에 덧붙인다 — `transcript`(멀티턴 대화 전체)는
        # 그대로 두고 system에만 추가해, `augment_with_retrieval()`의
        # "요청: {prompt}" 포맷(단일 프롬프트 호출부 전용)을 건드리지
        # 않는다.
        if skills_for_augmentation:
            query = last_human["content"] if last_human is not None else ""
            retrieved_context = agent_runtime.retrieve_context(
                conn, skills_for_augmentation, query
            )
            if retrieved_context:
                system = (
                    system
                    + "\n\n[실시간 조회 결과 — 아래 내용을 근거로 답변하세요]\n"
                    + retrieved_context
                )
        transcript = _build_transcript(conn, conversation_id)
        output = llm.call_llm(provider, model, system, transcript)
        if skills_for_augmentation:
            unsupported = pool_registry.unsupported_realtime_skill_names(
                conn, skills_for_augmentation
            )
            if unsupported:
                names = ", ".join(f'"{n}"' for n in unsupported)
                output = (
                    f"> ⚠ {names} 스킬은 실시간 외부 검색 도구가 연결돼 있지만, "
                    "대화창의 기본 AI 응답은 그 도구를 직접 호출할 수 없어 실제 "
                    "검색 없이 답변했습니다(구조적 제약 — Agent/Team을 멘션해 "
                    "CrewAI 실행으로 돌리면 실제로 검색합니다).\n\n" + output
                )
        if last_human is not None:
            uninvited = _uninvited_mention_candidates(
                conn, conversation_id, last_human["content"]
            )
            if uninvited:
                names = ", ".join(f'"@{n}"' for n in uninvited)
                output = (
                    f"> ⚠ {names}를 찾았지만 아직 이 대화에 초대되지 않아 기본 AI가 "
                    "대신 응답했습니다. 메시지창에서 @를 입력해 자동완성으로 초대한 "
                    "뒤 다시 멘션해 보세요.\n\n" + output
                )
        _finish_job_with_message(
            conn,
            job_id,
            conversation_id,
            DEFAULT_AI_AUTHOR_ID,
            output,
            actor_id,
            used_skill=skill,
            llm_provider=provider,
            llm_model=model,
        )
    except Exception as exc:  # noqa: BLE001
        _fail_job(conn, job_id, actor_id, exc)
