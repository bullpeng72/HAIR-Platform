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
    audit,
    context_policy,
    conversation_memory,
    deliverables,
    knowledge_submissions,
    llm,
    token_budget,
)

_DEFAULT_SYSTEM_PROMPT = (
    "당신은 HAIR-Platform의 대화형 작업공간에 참여하는 AI 비서입니다. "
    "대화 맥락을 참고해 자연스럽고 간결하게 응답하세요. "
    "최종 결정과 책임은 사람에게 있다는 점을 기억하세요."
)
_CONTEXT_MESSAGE_LIMIT = 10
# 사용자 보고(2026-11-12, "AI 모델의 응답 생성이 시간 제한을 넘겨
# 중단됐습니다") — 실측 확인: 문제는 모델 선택이 아니었다(로컬 27B급
# 모델 3종을 직접 벤치마크한 결과 생성 속도는 전부 비슷했다, 14.7~
# 15.5 tok/s) — 원인은 대화 자체가 이미 커진 것(실측 사례: 메시지
# 5건에 40,534자, `_CONTEXT_MESSAGE_LIMIT` 요약 압축은 아직 10건
# 미만이라 적용 전). 그만큼 큰 프롬프트를 로컬 모델이 프리필하는
# 시간까지 합치면 Ollama 기본값을 넘기기 쉽다.
#
# 뒤이은 사용자 결정(2026-11-12, "클라우드보다 Ollama가 더 길게...
# 대화 및 업무에 같은 기준이 적용되도록") — 그래서 "기본 AI"
# 전용으로 여기 따로 두지 않고, `llm.py`의 `OLLAMA_LLM_TIMEOUT_
# SECONDS`(1800초)를 전역 Ollama 기본값 자체로 올렸다 — 이 모듈은
# 이제 `call_llm()`에 `timeout_seconds`를 안 넘기고 그 전역 기본값을
# 그대로 받는다(아래 `run_default_ai_job()` 참고). 실패 메시지에
# 정확한 분을 보여줄 때만 그 정본 값을 그대로 참조한다(여기 숫자를
# 다시 정의하지 않음 — 정본은 `llm.py` 하나뿐).
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
    커밋/오딧 기록은 호출자가 각자의 맥락에 맞게 남긴다.

    사용자 보고(2026-11-19, "대화 삭제 시 500 Error 발생") 원인 수정 —
    `conversation_knowledge_chunks.source_message_id`가 `conversation_
    messages(id)`를 FK로 참조하는데(`PRAGMA foreign_keys=ON`이 전역
    으로 켜져 있음, `db.py`의 `get_connection()` 참고), 그동안
    `conversation_messages`를 **먼저** 지운 뒤 `conversation_knowledge_
    chunks`를 나중에 지워서 `sqlite3.IntegrityError: FOREIGN KEY
    constraint failed`가 그대로 났다 — 참조하는 쪽(chunks)을 먼저
    지우도록 순서를 바꾼다. `pipeline_runs.source_conversation_id`도
    같은 이유로 `conversations` 삭제 자체를 막을 수 있어(대화에서
    트리거된 파이프라인 실행이 있던 경우) — 파이프라인 run 자체는
    산출물·감사기록이 있는 실제 업무 이력이라 지우지 않고, 참조만
    끊는다(`NULL`로)."""
    conn.execute(
        "DELETE FROM conversation_knowledge_chunks WHERE conversation_id = ?",
        (conversation_id,),
    )
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
    conn.execute(
        "UPDATE pipeline_runs SET source_conversation_id = NULL "
        "WHERE source_conversation_id = ?",
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


def reap_orphaned_conversation_jobs(conn: sqlite3.Connection, keep: frozenset[int] | set[int] = frozenset()) -> int:
    """사용자 보고(2026-11-19, "ollama 실행되지 않고 있는데... 실행
    중이 표시됨") 조사 중 발견한 갭 — `deliverables.reap_orphaned_
    execution_jobs()`/`pipeline_runtime.reap_orphaned_pipeline_runs()`
    는 이미 앱 기동 시 `running`으로 남은 job을 "이전 프로세스의
    잔재"로 확정해 정리하는데, `conversation_jobs`(기본 AI/Agent
    멘션/Team 멘션)만 이 정리가 전혀 없었다 — 서버가 실행 도중 죽으면
    (이번 경우 디버깅을 위한 강제 재시작) `run_default_ai_job()` 등의
    `except Exception: _fail_job(...)` 핸들러 자체가 프로세스와 함께
    죽어버려 호출될 기회가 없으므로, 그 job은 영원히 `running`으로
    남고 Web UI는 "실행 중"을 계속 보여준다(재시작해도 안 풀림 — 같은
    종류의 먹통 상태). 이 앱은 "단일 프로세스" 전제이므로(위 두 reap
    함수와 동일 근거), 기동 시점에 `running`으로 남은 job은 전부
    이전 프로세스의 잔재로 확정할 수 있다."""
    rows = conn.execute(
        "SELECT id FROM conversation_jobs WHERE status = 'running'"
    ).fetchall()
    # 작업 큐에 아직 대기 중인 작업의 행은 남긴다(2026-11-26, P1-6 2단계 — 작업 프로세스가 곧 실행한다).
    rows = [r for r in rows if r["id"] not in keep]
    for row in rows:
        conn.execute(
            "UPDATE conversation_jobs SET status = 'failed', "
            "error = '서버가 재시작되어 실행이 중단되었습니다', "
            "finished_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
    if rows:
        conn.commit()
    return len(rows)


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
    # Agent는 최대 50개(2026-11-24, P2-12) — 기본 제공 Agent가 36개로 늘어 20개 제한이면 빈 검색어(@만 입력)
    # 목록에서 일부 기본 Agent가 아예 안 보였다.
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
        LIMIT 50
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
    # 평가 HTML 리포트가 있는 메시지(2026-11-25) — 화면에 "평가 리포트" 링크를 단다.
    from hair_platform import evaluation_reports

    with_report = evaluation_reports.messages_with_report(conn, [m["id"] for m in messages if m.get("author_type") == "agent"])
    for m in messages:
        m["has_report"] = m["id"] in with_report
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

    # 사용자 보고(2026-11-19, "대화 삭제 시 500 Error 발생") 조사 중
    # 같은 원인으로 확인된 쌍둥이 버그 — `conversation_knowledge_
    # chunks.source_message_id`가 이 메시지를 FK로 참조할 수 있다
    # (첨부파일을 이 메시지에 올려 색인된 경우). 먼저 지워야
    # `conversation_messages` 삭제 자체가 FK 위반으로 막히지 않는다.
    conn.execute(
        "DELETE FROM conversation_knowledge_chunks WHERE source_message_id = ?",
        (message_id,),
    )
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


def edit_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    message_id: int,
    actor_id: int,
    content: str,
) -> dict:
    """`PATCH /api/conversations/{conversation_id}/messages/{message_id}` —
    사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된 HTML을
    수정할 수 있는 기능 추가 제안") 1단계 — 프레젠테이션 자료 작성자 같은
    Agent가 만든 HTML에 색 대비 등의 버그가 있을 때, 사람이 소스를 직접
    고쳐 저장할 수 있게 한다. 권한은 `delete_message()`와 완전히
    동일하게 둔다(같은 메시지를 지울 수 있는 사람이면 고칠 수도 있어야
    자연스럽다 — 본인이 보낸 사람 메시지는 본인이, 그 외(AI/Agent
    응답 포함)는 대화 생성자가)."""
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
        raise PermissionError("not_allowed_to_edit_this_message")

    conn.execute(
        "UPDATE conversation_messages SET content = ? WHERE id = ?",
        (content, message_id),
    )
    audit.record(
        conn,
        actor_id,
        "conversation_message_edited",
        "conversation_messages",
        message_id,
        before={"content_length": len(message["content"])},
        after={"content_length": len(content)},
    )
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM conversation_messages WHERE id = ?", (message_id,)
    ).fetchone()
    result = dict(updated)
    if result.get("gate_scores"):
        result["gate_scores"] = json.loads(result["gate_scores"])
    return result


def suggest_message_fix(
    conn: sqlite3.Connection,
    conversation_id: int,
    message_id: int,
    actor_id: int,
    feedback: str,
    provider: str | None,
    model: str | None,
) -> str:
    """`POST /api/conversations/{id}/messages/{id}/suggest-fix` —
    사용자 요청(2026-11-16, "PIP로 배포 가능한 환경에서 생성된 HTML을
    수정할 수 있는 기능" 3단계) — 색 대비 버그 등을 손으로 고치는 대신
    "이 부분 색이 안 보여" 같은 자연어 피드백을 주면 LLM이 수정안을
    만든다. `deliverables.suggest_content_fix()`와 같은 설계 — 결과는
    저장하지 않는다(사람이 검토 후 기존 `edit_message()`/`PATCH`로
    직접 커밋). 권한은 그 저장 권한(`edit_message()`)과 완전히 동일
    하게 둔다."""
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
        raise PermissionError("not_allowed_to_edit_this_message")
    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    # 사용자 요청(2026-11-17, "모든 대화 결과물에서도 수정이 가능하도록")
    # 전까지는 "수정"/"AI에게 요청" 버튼 자체가 완전한 HTML 문서로 감지된
    # 메시지에만 떴으므로 여기서도 항상 html 지시문을 썼다 — 이제는 일반
    # 텍스트/마크다운 메시지도 이 버튼을 쓸 수 있으므로, 고정하지 않고
    # `_looks_like_full_html_document()`(대화 메시지엔 content_type
    # 컬럼이 없어, 프런트의 `_looksLikeFullHtmlDocument()`와 동일한
    # 판정을 한 번 더 거친다)로 매번 판정한다 — 일반 텍스트 메시지에
    # "완전한 HTML 문서로 쓰라"는 엉뚱한 지시가 섞이는 걸 막는다.
    content_type = "html" if _looks_like_full_html_document(message["content"]) else "text"
    type_instruction = deliverables._CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")
    system = (
        "주어진 기존 메시지 내용을 사용자 피드백에 따라 수정하는 역할입니다. "
        "피드백과 관련된 부분만 고치고, 나머지 내용과 구조는 그대로 "
        "보존하세요(요청받지 않은 부분까지 다시 쓰지 마세요). 전체 "
        "내용을 처음부터 끝까지 완성된 형태로 다시 출력하세요(일부만 "
        "보여주거나 '...' 같은 생략 표시를 쓰지 마세요)."
        + (("\n\n" + type_instruction) if type_instruction else "")
    )
    prompt = (
        f"기존 메시지 내용:\n{message['content']}\n\n"
        f"사용자 피드백(이 부분을 고쳐주세요): {feedback}"
    )
    return llm.call_llm(resolved_provider, resolved_model, system, prompt)


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

    collapsed = _CODE_FENCE_RE.sub(_replace, content)

    # 버그 수정(2026-11-13, 사용자 보고 — "제안서작성" 스킬로
    # 요청했는데 직전 메시지(프레젠테이션 자료 작성자가 만든 HTML)의
    # 색상표·구조를 그대로 베낀 결과가 나옴) — 실측 확인: 그 직전
    # 메시지는 응답이 중간에 끊겨 닫는 펜스(```)가 없었다. 위
    # `_CODE_FENCE_RE`는 여는 펜스와 닫는 펜스가 **둘 다** 있어야
    # 매칭되므로(`webui.py`의 `stripOuterFence()`가 가졌던 것과 같은
    # 사각지대 — 그쪽은 이미 고쳤다), 닫히지 않은 긴 HTML 전체가
    # 한 글자도 안 접힌 채 다음 요청의 프롬프트에 그대로 끼어들어,
    # 모델이 그 안의 구체적인 색상 변수까지 그대로 베끼는 "앵커링"이
    # 재발했다. ``` 개수가 홀수(=닫히지 않은 펜스가 남음)면, 마지막
    # 여는 펜스 이후도 같은 기준으로 접는다.
    if collapsed.count("```") % 2 == 1:
        last_fence_pos = collapsed.rfind("```")
        newline_pos = collapsed.find("\n", last_fence_pos)
        if newline_pos != -1:
            lang = collapsed[last_fence_pos + 3 : newline_pos]
            body = collapsed[newline_pos + 1 :]
            line_count = body.count("\n") + 1
            if line_count > _CODE_FENCE_STRIP_THRESHOLD_LINES:
                collapsed = (
                    collapsed[:last_fence_pos]
                    + "```"
                    + lang
                    + "\n"
                    + f"[코드 {line_count}줄 생략(응답이 끊겨 닫는 표시가 없음) "
                    "— 이 메시지의 전체 내용은 대화 화면에서 직접 확인하세요. "
                    "이 내용을 그대로 복사하지 말고, 이번 요청에 맞게 처음부터 "
                    "다시 작성해야 합니다.]\n"
                )
    return collapsed


# `_build_transcript()`가 다른 소비처(system 프롬프트 등) 때문에 예산이
# 0까지 줄어도 최신 사용자 메시지만큼은 항상 담기 위한 바닥 예산.
_MIN_TRANSCRIPT_TOKENS = 1024
# 컨텍스트 정책 'recent'(직전 1~2턴) — 현재 질문 + 직전 문답 1.5턴.
_RECENT_MESSAGE_LIMIT = 4

# 컨텍스트 정책 'none'(현재 질문만)일 때 `_ANTI_ANCHORING_NOTE` 대신 붙이는
# 지시 — 앵커링할 과거 산출물이 애초에 없고, 코드 생성용 지시("완성된
# 산출물(코드 전체 등)을 포함하라")가 질의응답을 제작 요청으로 오해하게
# 만든 실측 사례(2026-11-20, 내부지식검색 "구체적인 작업 요청이 없다" 응답)가
# 있었다.
_STANDALONE_QUESTION_NOTE = (
    "\n\n[중요] 위 질문 하나에 대해 답하세요. 이전 대화는 의도적으로 "
    "제공하지 않았습니다 — 이전 대화가 없다는 이유로 되묻지 말고, 주어진 "
    "자료를 근거로 바로 답하세요."
)


def _context_note(policy: dict) -> str:
    return _STANDALONE_QUESTION_NOTE if policy["history"] == "none" else _ANTI_ANCHORING_NOTE


def _transcript_reserve_tokens(latest_human_content: str | None, provider: str) -> int:
    """첨부자료 메모리 검색(`conversation_memory.retrieve_context_for_
    query()`)이 예산을 잡을 때 대화록 몫으로 미리 떼어 둘 토큰 수 —
    최신 사용자 메시지 전체(최소 `_MIN_TRANSCRIPT_TOKENS`)와 고정
    지시문(`_ANTI_ANCHORING_NOTE`). 메모리를 먼저 채운 뒤 대화록은
    `_build_transcript(reserved_tokens=...)`로 남은 만큼만 채운다 —
    둘 다 "입력 예산 전체"를 각자 따로 쓰던 이중 계산(2026-11-20
    사용자 보고)을 막는다."""
    return max(
        _MIN_TRANSCRIPT_TOKENS,
        token_budget.count_tokens(latest_human_content or "", provider),
    ) + token_budget.count_tokens(_ANTI_ANCHORING_NOTE, provider)


def _graph_engine_available() -> bool:
    from hair_platform import graph_engine_available

    return graph_engine_available()


def _build_transcript(
    conn: sqlite3.Connection,
    conversation_id: int,
    limit: int = _CONTEXT_MESSAGE_LIMIT,
    *,
    provider: str | None = None,
    model: str | None = None,
    reserved_tokens: int = 0,
    history_mode: str = "full",
) -> str:
    """최근 N건 + (있으면) 그 이전 과거의 캐시된 요약을 이어붙인다(멀티턴
    대화 맥락). 모든 응답 경로(기본 AI/Agent 멘션/Team 멘션)가 이 함수
    하나를 공유한다.

    `history_mode`(2026-11-20, 대화 컨텍스트 정책 — `context_policy.py`)
    — 'full'은 위 기존 동작 그대로, 'recent'는 요약 없이 최근
    `_RECENT_MESSAGE_LIMIT`건(직전 1~2턴), 'none'은 현재 질문(최신 사람
    메시지) 하나만. 'recent'/'none'은 요약을 안 쓰므로 요약 생성 LLM
    호출(`_ensure_context_summary`)도 일어나지 않는다.

    `provider`/`model`(사용자 요청, 2026-11-18, "...엔터프라이즈 환경
    적용이 가능한 수준의 확실한 개선 방안을 도출해줘" 개선축 B) — 넘기면
    `token_budget`으로 모델의 실제 컨텍스트 한도 안에 들어오도록 최신
    메시지부터 채워 넣고, 그래도 넘치는 개별 메시지(예: 거대한 첨부
    파일)는 잘라낸다. 생략하면(기존 호출부가 아직 안 넘기면) 지금까지와
    100% 동일하게 개수 기준으로만 자른다 — 하위 호환. `reserved_tokens`
    는 호출자가 이미 확정한 system 프롬프트 등 다른 소비처의 토큰 수 —
    2026-11-18 사고(첨부파일 하나가 입력 전체를 거의 다 차지해 모델이
    답변을 생성할 토큰 여유가 하나도 안 남아 빈 응답을 낸 것)를 막는
    핵심 안전장치다."""
    if history_mode == "none":
        rows = conn.execute(
            "SELECT author_type, content FROM conversation_messages "
            "WHERE conversation_id = ? AND author_type = 'human' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchall()
        summary = None
    elif history_mode == "recent":
        rows = conn.execute(
            "SELECT author_type, content FROM conversation_messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, _RECENT_MESSAGE_LIMIT),
        ).fetchall()
        summary = None
    else:
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
    if provider is None:
        return "\n".join(lines)
    budget = token_budget.input_budget_tokens(
        conn, provider, model or "", reserved_tokens=reserved_tokens
    )
    # 버그 수정(2026-11-20, 사용자 보고 — "내부지식검색" 스킬 질의에 "구체적인
    # 질문이 보이지 않는다"는 답만 옴) — system 프롬프트(내부지식검색 결과 +
    # 첨부자료 메모리)가 입력 예산을 전부 차지하면 여기 예산이 0이 되고
    # `fit_lines_to_budget()`이 ""를 돌려줘, 사용자 질문 없이 LLM이
    # 호출됐다(경고도 없이). 대화록 없이는 모델이 무엇을 할지 알 수
    # 없으므로, 최소한 최신 메시지가 들어갈 바닥 예산을 항상 보장한다
    # (`_INPUT_BUDGET_RATIO`가 남겨 둔 출력 여유분 안에서 흡수되는 크기).
    budget = max(budget, _MIN_TRANSCRIPT_TOKENS)
    return token_budget.fit_lines_to_budget(lines, provider, budget)


AUTO_TITLE_MAX = 40


def _auto_title(conn: sqlite3.Connection, conversation_id: int, content: str) -> None:
    """제목 없는 대화는 첫 메시지로 이름 짓기(2026-11-28, 신입·일반 사용자 관점) — 대화를 열고 바로 묻는 사람이 대부분이라
    목록이 "(제목 없음)"으로 가득 찼다. @멘션은 빼고 첫 줄 앞부분만. 사람이 붙인 제목은 건드리지 않는다."""
    row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None or (row["title"] or "").strip():
        return
    text = content or ""
    if "@" in text:  # 띄어쓰기가 든 이름(@규정 요약가)까지 — 아는 이름을 긴 것부터 지우고, 남은 @낱말도 지운다
        names = [r[0] for r in conn.execute(
            "SELECT role_name FROM agent_pool UNION SELECT name FROM teams UNION SELECT username FROM users") if r[0]]
        for n in sorted(names, key=len, reverse=True):
            text = text.replace("@" + n, " ")
        text = re.sub(r"@\S+", " ", text)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line).strip(" .,·-")
    if not line:
        return
    title = line if len(line) <= AUTO_TITLE_MAX else line[:AUTO_TITLE_MAX].rstrip() + "…"
    conn.execute("UPDATE conversations SET title = ? WHERE id = ? AND (title IS NULL OR trim(title) = '')",
                 (title, conversation_id))


def create_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    content: str,
    skill: str | None = None,
    context_override: str | None = None,
) -> dict:
    """사용자 메시지를 저장하고, 어떤 트리거가 필요한지 판정까지만
    한다(실제 LLM 호출은 `app.py`가 `BackgroundTasks`로 스케줄 —
    Phase 3와 같은 2단계 패턴). `skill`이 있으면(사용자 결정 3)
    명시적 요청이라 `ai_auto_reply` 설정과 무관하게 항상 트리거한다.

    `context_override`(2026-11-20, 대화 컨텍스트 정책) — 'full'/'recent'/
    'none'이면 이 메시지에 대한 응답에서 스킬 기본값 대신 그 값을 쓴다
    (`context_policy.finalize()`). None이면 스킬(또는 Agent) 기본값."""
    if context_override is not None and context_override not in context_policy.HISTORY_MODES:
        raise ValueError("invalid_context_override")
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
        "content, used_skill, context_override) VALUES (?, 'human', ?, ?, ?, ?)",
        (conversation_id, actor_id, content, skill, context_override),
    )
    message_id = cur.lastrowid
    assert message_id is not None
    _auto_title(conn, conversation_id, content)
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


# 사용자 요청(2026-11-18, "...메모리를 활용하는 방안 등을 고려해
# 엔터프라이즈 환경 적용이 가능한 수준의 확실한 개선 방안을 도출해줘"
# 개선축 C) — 이 글자 수를 넘는 첨부는 전문을 메시지에 그대로 박는 대신
# `conversation_memory.index_attachment()`로 청크+임베딩해 대화 전용
# 색인에 넣고, 메시지에는 짧은 미리보기+안내만 남긴다(2026-11-18 사고:
# 19,321자 첨부파일 1개가 로컬 모델 컨텍스트 한도를 혼자서 거의 다
# 차지해 15분 돌고도 빈 응답을 낸 것의 근본 해결 — 개선축 B(`token_
# budget.py`)가 "안 넘치게 자르기"였다면, 이건 "애초에 통째로 안 넣고
# 필요한 부분만 다시 찾아 쓰기"). 이 문턱보다 작은 첨부는 지금까지와
# 100% 동일하게 전문을 그대로 메시지에 남긴다(짧은 메모/코드 조각 등은
# 검색보다 직접 보이는 쪽이 더 유용하고, 매번 임베딩 API를 부를 필요도
# 없다 — 순수 추가적 변경, 하위 호환).
_LARGE_ATTACHMENT_CHAR_THRESHOLD = 3000
_LARGE_ATTACHMENT_PREVIEW_CHARS = 500


def create_attachment_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    filename: str,
    text: str,
    raw_bytes: bytes | None = None,
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
    한다).

    `text`가 `_LARGE_ATTACHMENT_CHAR_THRESHOLD`를 넘으면(개선축 C) 전문
    대신 미리보기만 메시지에 남기고, 전체는 이 대화 전용 메모리에
    색인한다 — `raw_bytes`를 같이 넘기면(CSV/XLSX일 때만 의미 있음)
    숫자 열 사전 계산 요약도 함께 색인된다. 이 색인도 `conversation_
    knowledge_chunks`가 `conversation_id`로 스코프돼 있어 "저장되지
    않고 휘발되는" 원칙을 그대로 지킨다(전역 VectorDB가 아니다 — 대화가
    지워지면 같이 지워진다)."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")

    is_large = len(text) > _LARGE_ATTACHMENT_CHAR_THRESHOLD
    if is_large:
        preview = text[:_LARGE_ATTACHMENT_PREVIEW_CHARS]
        content = (
            f"\U0001f4ce 첨부 자료 — {filename} ({len(text):,}자 — 전체는 "
            "이 대화의 메모리에 색인되어, 관련 질문을 하면 필요한 부분이 "
            f"자동으로 검색됩니다)\n\n{preview}...(이하 생략)"
        )
    else:
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
            "indexed_as_memory": is_large,
        },
    )
    conn.commit()
    if is_large:
        conversation_memory.index_attachment(
            conn,
            actor_id,
            conversation_id,
            message_id,
            filename,
            text,
            raw_bytes=raw_bytes,
        )
    return message_id


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


_OUTER_FENCE_CLOSED_RE = re.compile(r"^```[a-zA-Z0-9]*\n(.*?)\n?```\s*$", re.DOTALL)
_OUTER_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z0-9]*\n(.*)$", re.DOTALL)
_FULL_HTML_DOC_RE = re.compile(r"^<!DOCTYPE\s+html|^<html[\s>]", re.IGNORECASE)


def _looks_like_full_html_document(raw: str) -> bool:
    """`webui.py`의 `_looksLikeFullHtmlDocument()`(대화 화면이 메시지를
    HTML로 보여줄지 판단하는 바로 그 함수)와 동일한 로직의 Python
    버전 — 대화창에서는 이미 완전한 HTML 문서로 보이던 메시지가,
    갤러리로 보내는 순간엔 이 판정이 없어 평문으로 저장되던 버그
    (2026-11-16, 사용자 보고 "갤러리 미리보기에서 HTML 소스로 보임")
    수정에 쓴다. 코드 중복이지만 JS/Python 경계라 공유 함수로 뺄 수
    없다(기존 `_CONTENT_TYPE_INSTRUCTIONS`의 deliverables.py/
    crew_runtime.py 중복과 같은 성격)."""
    stripped = (raw or "").strip()
    closed = _OUTER_FENCE_CLOSED_RE.match(stripped)
    if closed:
        unwrapped = closed.group(1)
    else:
        open_only = _OUTER_FENCE_OPEN_RE.match(stripped)
        unwrapped = open_only.group(1) if open_only else stripped
    return bool(_FULL_HTML_DOC_RE.match(unwrapped.strip()))


def save_message_as_deliverable(
    conn: sqlite3.Connection, conversation_id: int, message_id: int, actor_id: int
) -> int:
    """사용자 요청(2026-11-10, "대화... 등에서 생성한 최종결과물(필요시
    지정한 결과물)이 갤러리에 가도록") — 대화의 모든 메시지를 자동으로
    결과물화하면 탐색적 잡담까지 다 쌓여 갤러리가 지저분해진다. 대신
    사람이 "이건 남길 만하다"고 판단한 메시지 하나를 명시적으로
    결과물로 승격한다 — 사람 메시지든 Agent 응답이든 동일하게
    동작한다(원 메시지의 author_type/author_id를 그대로 물려받아
    "누가/무엇이 실제로 작성했는지"가 결과물에도 정확히 남는다).

    `content_type`(2026-11-16 수정) — 대화 메시지는 content_type
    컬럼이 없어 화면이 `_looksLikeFullHtmlDocument()`로 매번 스스로
    판단하는데, 이 함수는 그 판정 없이 항상 `'text'`로 저장해 갤러리
    에서는 완전한 HTML 문서도 펜스가 그대로 보이는 평문으로 보였다
    (실측 확인). 저장 시점에 한 번 판정해 고정한다."""
    message = conn.execute(
        "SELECT * FROM conversation_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    ).fetchone()
    if message is None:
        raise ValueError("message_not_found")

    task_id = ensure_task_for_conversation(
        conn, conversation_id, actor_id, origin_note="메시지를 결과물로 저장해 자동 생성됨."
    )
    content_type = "html" if _looks_like_full_html_document(message["content"]) else "text"
    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, ?, ?, ?, ?, 'approved')",
        (task_id, message["author_type"], message["author_id"], message["content"], content_type),
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
    conn: sqlite3.Connection,
    conversation_id: int,
    message_id: int,
    actor_id: int,
    actor_role: str,
) -> dict:
    """사용자 요청(2026-11-10, "대화에서 생성된 결과물을 지식으로 보내는
    경로가 없음")으로 생긴 경로 — 처음엔 admin/manager가 누르면 결재 없이
    즉시 색인했다.

    사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
    경우와 ... '지식베이스로 보내기' 및 admin 결재 후 등록되는 것으로
    개선") — 이제 `knowledge_submissions.submit()`의 얇은 별칭이다:
    참여자 누구나 제출하고, admin이 결재해야 색인된다(admin 본인 제출은
    즉시 등록). 반환값의 `needs_registration`이 True면 호출자가 커밋 뒤
    `knowledge_submissions.register_submission()`을 백그라운드로 건다.
    화면은 `POST /api/knowledge-submissions`를 직접 쓰고, 이 함수는 기존
    API 경로(`.../save-as-knowledge`) 호환용으로만 남긴다."""
    message = conn.execute(
        "SELECT id FROM conversation_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    ).fetchone()
    if message is None:
        raise ValueError("message_not_found")
    return knowledge_submissions.submit(
        conn, actor_id, actor_role, "conversation_message", message_id
    )


# ---------- 대화 → 업무 요청 전환(2026-11-23, P1-4) ----------
# 사용자 요청("P1 진행해줘" — 대화와 정식 업무의 단절). 지금까지 대화에서 정한 일을 결재·Gate가
# 붙는 정식 업무로 넘기려면 내용을 직접 복사해 "업무 요청"에 붙여야 했다(이용 가이드 2절 6단계).
# 대화에서 바로 업무 요청 초안을 만들고(기본은 LLM 없이 무료, 선택하면 LLM이 구조화), 사람이 다듬어
# 제출하면 어느 대화에서 왔는지 남기고 대화에도 전환 사실을 적는다.

TRANSFER_NOTE_PREFIX = "📋 이 대화를 업무 요청"
_DRAFT_MAX_MESSAGE_CHARS = 2000
_DRAFT_MAX_TOTAL_CHARS = 12000

BRIEF_DRAFT_SYSTEM = (
    "너는 대화 기록을 정식 업무 요청서로 정리하는 담당자다. 대화에 나온 내용만 근거로 다음 순서의 "
    "마크다운으로 쓴다: 제목 한 줄 → ## 목표 → ## 배경 → ## 요구사항(검증 가능한 항목 목록, 대화에 나온 "
    "수치·기간·대상·형식 조건은 원문 그대로) → ## 산출물 → ## 완료 기준 → ## 확인 필요(대화에서 정해지지 "
    "않은 것). 대화에 없는 사실·수치는 지어내지 않는다. 머리말·맺음말 없이 요청서만 출력한다."
)


def _transfer_messages(conn: sqlite3.Connection, conversation_id: int, message_ids: list[int] | None) -> list[dict]:
    msgs = [m for m in list_messages(conn, conversation_id) if not m["content"].startswith(TRANSFER_NOTE_PREFIX)]
    if message_ids:
        wanted = set(message_ids)
        msgs = [m for m in msgs if m["id"] in wanted]
    return msgs


def brief_draft_from_conversation(
    conn: sqlite3.Connection,
    conversation_id: int,
    actor_id: int,
    message_ids: list[int] | None = None,
    use_llm: bool = False,
) -> dict:
    """업무 요청 초안 — 저장하지 않는다(사람이 다듬어 `POST /api/briefs`로 제출).
    기본(무료): 사람 메시지를 순서대로 요청 내용으로, 마지막 AI 응답을 참고 발췌로 붙인다.
    `use_llm=True`: 대화 기본 모델로 목표·요구사항·완료 기준을 구조화한다(비용 발생)."""
    conv = get_conversation(conn, conversation_id)
    if conv is None:
        raise ValueError("conversation_not_found")
    if not is_participant(conn, conversation_id, actor_id):
        raise PermissionError("not_a_participant")
    msgs = _transfer_messages(conn, conversation_id, message_ids)
    if not msgs:
        raise ValueError("no_messages")
    title = (conv["title"] or "").strip() or "대화에서 전환한 업무"
    footer = f"\n\n(대화 #{conversation_id}「{title}」에서 전환)"
    if use_llm:
        lines, total = [], 0
        for m in msgs:
            who = "사람" if m["author_type"] == "human" else "AI"
            piece = f"[{who}] {m['content'][:_DRAFT_MAX_MESSAGE_CHARS]}"
            if total + len(piece) > _DRAFT_MAX_TOTAL_CHARS:
                break
            lines.append(piece)
            total += len(piece)
        provider, model = llm.resolve_provider_and_model(conv["llm_provider"], conv["llm_model"], conn=conn)
        text = llm.call_llm(provider, model, BRIEF_DRAFT_SYSTEM, "\n\n".join(lines)).strip()
        if text:
            return {"raw_text": text + footer, "source": "llm", "message_count": len(lines),
                    "llm_provider": provider, "llm_model": model}
    human = [m["content"].strip() for m in msgs if m["author_type"] == "human"]
    agent = [m["content"].strip() for m in msgs if m["author_type"] == "agent"]
    parts = [title, "", "## 요청 내용"] + [f"- {h[:_DRAFT_MAX_MESSAGE_CHARS]}" for h in human]
    if agent:
        parts += ["", "## 대화에서 나온 참고 답변(발췌)", agent[-1][:1500]]
    return {"raw_text": "\n".join(parts) + footer, "source": "rule", "message_count": len(msgs)}


def record_brief_transfer(conn: sqlite3.Connection, conversation_id: int, actor_id: int, brief_id: int) -> None:
    """업무 요청 제출 뒤 대화에 전환 사실을 남긴다 — 사람 메시지로 바로 넣어 AI 자동응답을 부르지
    않는다(`create_message()`를 거치지 않음)."""
    conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) "
        "VALUES (?, 'human', ?, ?)",
        (conversation_id, actor_id,
         f"{TRANSFER_NOTE_PREFIX} #{brief_id}로 전환했습니다 — \"업무 요청\" 탭에서 할 일로 나누고 배정하세요."),
    )
    audit.record(conn, actor_id, "conversation_transferred_to_brief", "conversations", conversation_id,
                 after={"brief_id": brief_id})
    conn.commit()


# ---------- 응답 작업 실행은 `conversation_jobs.py`로(2026-11-27 분리) ----------
# 예전 이름으로도 그대로 쓸 수 있게 넘겨 준다(작업 큐 경로 "hair_platform.conversations:run_default_ai_job"·호출부·테스트).
_MOVED_TO_JOBS = frozenset(['_PIPELINE_RUN_STATUS_LABEL', '_PIPELINE_STAGE_STATUS_LABEL', '_describe_job_failure', '_fail_job', '_finish_job_with_message', '_run_agent_mention_job', '_run_default_ai_job', '_run_team_mention_via_pipeline', 'list_jobs', 'post_pipeline_run_status_message', 'resume_pipeline_graph_from_approval_and_notify', 'run_agent_mention_job', 'run_default_ai_job', 'run_pipeline_stage_job_and_notify', 'run_team_mention_job', 'start_agent_mention_job', 'start_default_ai_job', 'start_team_mention_job'])


def __getattr__(name: str):
    if name in _MOVED_TO_JOBS:
        from hair_platform import conversation_jobs

        return getattr(conversation_jobs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

