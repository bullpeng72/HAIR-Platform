"""대화 응답 작업 실행(2026-11-27 — `conversations.py` 2,936줄 분리, 기술 부채 정리).

`conversations.py`에는 대화·참가자·메시지·문맥 구성이 남고, 여기에는 멘션·기본 AI·팀 응답을 **실행하는 작업**과 작업 마감,
파이프라인 진행 알림이 있다. 본문은 옮기기 전 그대로다. 예전 이름(`conversations.run_default_ai_job` 등)은
`conversations` 모듈이 이 모듈로 넘겨 주므로(모듈 `__getattr__`) 호출부·작업 큐 경로·테스트는 바꾸지 않아도 된다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import (
    agent_evaluation,
    agent_runtime,
    audit,
    context_policy,
    conversation_memory,
    crew_runtime,
    deliverables,
    knowledge_access,
    language_qa,
    llm,
    llm_usage,  # 업무 맥락별 사용량(2026-11-23, P2-11)
    pipeline_runtime,
    presentation_deck,
    teams,
    token_budget,
    tool_calling,
)
from hair_platform import progress as _progress  # 작성 중 미리보기(2026-11-24, P0-2)

from hair_platform.conversations import (  # noqa: E402 — 대화 쪽 공용 도우미
    DEFAULT_AI_AUTHOR_ID,
    _ANTI_ANCHORING_NOTE,
    _DEFAULT_SYSTEM_PROMPT,
    _build_transcript,
    _context_note,
    _graph_engine_available,
    _transcript_reserve_tokens,
    _uninvited_mention_candidates,
    ensure_task_for_conversation,
    get_conversation,
    is_participant,
)


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
    gate_scores: dict | None = None,
    applied_context_policy: dict | None = None,
) -> None:
    # 사용자 요청(2026-11-19, "다음 개선 진행해줘" → conversations.py
    # 통합) — `run_team_mention_job()`의 Crew 경로는 이미 `gate_scores`
    # 를 저장해 화면에 보여주는데, "기본 AI"/단일 Agent 멘션(둘 다 이
    # 헬퍼를 공유)만 Gate A-G 측정이 전혀 없었다(원 조사에서 확인된
    # 공백) — `gate_scores`는 선택 인자라 안 넘기면(기존 호출부 전부)
    # `NULL`로 기존과 100% 동일.
    # 평가 HTML 리포트(2026-11-25) — 토큰은 메시지의 Gate 점수에 남기지 않고 리포트만 메시지 id로 저장한다.
    from hair_platform import evaluation_reports

    gate_scores, report_token = evaluation_reports.split(gate_scores) if gate_scores else (gate_scores, None)
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, "
        "content, used_skill, llm_provider, llm_model, gate_scores, context_policy) "
        "VALUES (?, 'agent', ?, ?, ?, ?, ?, ?, ?)",
        (
            conversation_id,
            author_id,
            # HAIR 덱(2026-11-20, `presentation_deck.py`) — "프레젠테이션작성"
            # 스킬 응답이면 디자인 시스템 CSS를 넣은 완전한 HTML 문서로 만든다
            # (대화 화면이 iframe 슬라이드쇼로 보여 주고, 다운로드해도 혼자
            # 열린다). 덱이 아니면 그대로.
            presentation_deck.finalize(content),
            used_skill,
            llm_provider,
            llm_model,
            json.dumps(gate_scores, ensure_ascii=False) if gate_scores else None,
            # 대화 컨텍스트 정책(2026-11-20) — 이 응답에 실제로 적용한 정책
            # (화면 배지·감사·재현용). 안 넘기면 NULL(= 기존 동작 'full').
            json.dumps(applied_context_policy, ensure_ascii=False)
            if applied_context_policy
            else None,
        ),
    )
    message_id = cur.lastrowid
    evaluation_reports.store_for_message(conn, message_id, report_token)
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


def _describe_job_failure(
    exc: Exception, trigger_type: str | None = None, provider: str | None = None
) -> str:
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
        if provider == "ollama":
            minutes = int(llm.OLLAMA_LLM_TIMEOUT_SECONDS // 60)
            return (
                f"AI 모델의 응답 생성이 시간 제한(약 {minutes}분)을 넘겨 "
                "중단됐습니다 — 대화가 길어졌거나 요청한 내용이 복잡할 때 "
                "발생할 수 있는 문제입니다. 요청을 더 간결하게 나눠서 다시 "
                "시도하거나, 대화가 많이 길어졌다면 새 대화를 시작해 보세요."
            )
        return (
            "AI 모델의 응답 생성이 시간 제한을 넘겨 중단됐습니다 — 대화가 "
            "길어졌거나 요청한 내용이 복잡할 때 발생할 수 있는 문제입니다. "
            "요청을 더 간결하게 나눠서 다시 시도하거나, 대화가 많이 길어졌다면 "
            "새 대화를 시작해 보세요."
        )
    return str(exc)


def _fail_job(
    conn: sqlite3.Connection,
    job_id: int,
    actor_id: int,
    exc: Exception,
    *,
    provider: str | None = None,
) -> None:
    conn.rollback()
    raw_error = str(exc)
    trigger_row = conn.execute(
        "SELECT trigger_type FROM conversation_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    trigger_type = trigger_row["trigger_type"] if trigger_row else None
    error = _describe_job_failure(exc, trigger_type, provider)
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


@llm_usage.with_scope(conversation_id="conversation_id", agent_id="agent_id")
def _run_agent_mention_job(
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
        # 버그 수정(2026-11-13, 사용자 보고 — "제안서작성" 스킬
        # 요청이 직전 메시지의 프레젠테이션 스타일을 그대로 베낌) 조사
        # 중 발견 — 이 경로(단일 Agent 멘션)도 team_mention과 똑같이
        # `_build_transcript()`가 넘긴 과거 산출물에 앵커링될 위험을
        # 그대로 안고 있었는데 `_ANTI_ANCHORING_NOTE`가 안 붙어 있었다.
        # 같은 위험이면 같은 방어를 적용한다.
        #
        # 토큰 예산(2026-11-18, 개선축 B) — `execute_agent()`가 내부에서
        # 쓸 provider/model을 먼저 알아야 `_build_transcript()`에 넘겨
        # 미리 잘라낼 수 있다(그 함수와 같은 우선순위: 인자로 받은
        # provider/model 없음 → 이 Agent에 저장된 값 → 전역 기본값).
        agent_llm_row = conn.execute(
            "SELECT llm_provider, llm_model FROM agent_pool WHERE id = ?",
            (agent_id,),
        ).fetchone()
        budget_provider, budget_model = llm.resolve_provider_and_model(
            agent_llm_row["llm_provider"] if agent_llm_row else None,
            agent_llm_row["llm_model"] if agent_llm_row else None,
        )
        # 대화 메모리(2026-11-18, 개선축 C) — "기본 AI"와 같은 원칙, 이
        # 경로엔 별도 `system` 변수가 없어(`execute_agent()`가 내부에서
        # 직접 만듦) transcript 자체에 덧붙인다. 색인된 청크가 없으면
        # 비용 없이 바로 `None`.
        last_human_for_memory = context_policy.latest_human_message(conn, conversation_id)
        question = last_human_for_memory["content"] if last_human_for_memory else ""
        # 대화 컨텍스트 정책(2026-11-20) — Agent에 덮어쓰기 값이 있으면 그것,
        # 없으면 그 Agent 스킬들의 가장 포괄적인 값(`context_policy.for_agent`).
        policy = context_policy.finalize(
            context_policy.for_agent(conn, agent_id),
            last_human_for_memory["context_override"] if last_human_for_memory else None,
            question,
        )
        context_note = _context_note(policy)
        search_query = context_policy.retrieval_query(conn, conversation_id, question, policy)
        memory_context = (
            conversation_memory.retrieve_context_for_query(
                conn,
                conversation_id,
                search_query,
                provider=budget_provider,
                model=budget_model,
                reserved_tokens=_transcript_reserve_tokens(question, budget_provider),
            )
            if policy["attachments"]
            else None
        )
        memory_note = (
            (
                "\n\n[이 대화에 첨부된 자료에서 찾은 관련 내용 — 아래 "
                "내용을 근거로 답변하세요.]\n" + memory_context
            )
            if memory_context
            else ""
        )
        # 메모리를 먼저 확정하고 대화록은 그 나머지로 채운다(2026-11-20 —
        # 예전엔 둘 다 입력 예산 전체를 각자 따로 써서 합계가 넘쳤다).
        transcript = _build_transcript(
            conn,
            conversation_id,
            provider=budget_provider,
            model=budget_model,
            reserved_tokens=token_budget.count_tokens(
                context_note + memory_note, budget_provider
            ),
            history_mode=policy["history"],
        )
        sink: dict = {}
        # Gate 품질확인(2026-11-19, "다음 개선 진행해줘" → conversations.py
        # 통합) — `deliverables.execute_agent_for_task()`(Task 기반 단일
        # 실행 경로)는 이미 `agent_evaluation.run_and_score()`로 감싸
        # Gate A-G를 측정하는데, 똑같이 `execute_agent()`를 쓰는 이
        # 대화 멘션 경로만 그 측정이 전혀 없었다(원 조사에서 확인된
        # 공백 — `run_team_mention_job()`의 Crew 경로는 이미 있음).
        # `agent_pool.role_name`을 그대로 넘긴다(Task 경로와 동일한
        # 호출 모양).
        agent_role_row = conn.execute(
            "SELECT role_name FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        eval_result = agent_evaluation.run_and_score(
            agent_role_row["role_name"] if agent_role_row else "Agent",
            lambda: agent_runtime.execute_agent(
                conn,
                agent_id,
                actor_id,
                (transcript or "대화를 시작합니다.") + memory_note + context_note,
                retrieval_sink=sink,
                retrieval_query=search_query or None,
            ),
        )
        output = eval_result["output"]
        # 사용자 요청(2026-11-18, "스킬이 아니라 에이전트를 호출하는
        # 방식이면 가능한지 분석해줘") 조사 중 발견 — 단일 Agent
        # 멘션(`agent_runtime.execute_agent()`)도 "기본 AI"
        # (`run_default_ai_job()`)와 완전히 똑같은 구조적 제약(CrewAI
        # 도구 호출 루프가 없음)을 갖는데, "기본 AI"에만 있던 경고
        # (`unsupported_realtime_skill_names()`)가 이 경로엔 전혀
        # 없었다 — 멘션된 Agent의 skill_tags에 비-native 도구(mcp_*/
        # custom_python)가 연결된 스킬이 있어도 아무 경고 없이 LLM이
        # 암산한 결과를 내놓고 있었다(예: "데이터분석" 스킬을
        # 가진 Agent를 멘션해도 실제 "수치 데이터 분석기" 도구는 전혀
        # 호출되지 않음). 같은 경고를 여기도 붙인다 — 단, 안내 문구는
        # "Agent를 멘션하라"고 하지 않는다(지금 이미 Agent 멘션으로도
        # 안 됐으므로) — Team 멘션만 실제로 도구를 호출한다.
        agent_row = conn.execute(
            "SELECT skill_tags FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        unsupported = tool_calling.unsupported_skill_names(
            conn, json.loads(agent_row["skill_tags"]) if agent_row else []
        )
        if unsupported:
            names = ", ".join(f'"{n}"' for n in unsupported)
            output = (
                f"> ⚠ {names} 스킬에 연결된 도구 서버(MCP)에 연결하지 못해 그 도구를 실행하지 않고 "
                "답변했습니다 — admin은 '도구/스킬 Pool' 탭에서 그 도구의 서버 주소·실행 명령을 확인하세요.\n\n"
                + output
            )
        output = language_qa.repair_output(
            conn,
            output,
            sink.get("llm_provider"),
            sink.get("llm_model"),
            actor_id,
            "conversation_agent_mention",
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
            gate_scores=eval_result["gate_scores"],
            applied_context_policy=policy,
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


@llm_usage.with_scope(conversation_id="conversation_id")
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
        "SELECT pipeline_template_id, use_graph_engine FROM teams WHERE id = ?",
        (team_id,),
    ).fetchone()
    if team_row is not None and team_row["pipeline_template_id"] is not None:
        _run_team_mention_via_pipeline(
            conn,
            job_id,
            conversation_id,
            team_id,
            team_row["pipeline_template_id"],
            actor_id,
            # 그래프 엔진 의존성(`graph` extra)이 없는 설치본이면 기존 엔진으로 돈다(2026-11-23) —
            # 다른 환경에서 켜 둔 팀 설정이 남아 있어도 백그라운드에서 ImportError로 죽지 않게.
            use_graph_engine=bool(team_row["use_graph_engine"]) and _graph_engine_available(),
        )
        return
    try:
        agent_ids = teams.active_agent_member_ids(conn, team_id)
        if not agent_ids:
            raise ValueError("team_has_no_active_agents")
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
            budget_provider, budget_model = llm.resolve_provider_and_model(
                manager_row["llm_provider"] if manager_row else None,
                manager_row["llm_model"] if manager_row else None,
            )
        else:
            # 버그 발견(2026-11-18, 신규 "데이터 분석·시각화 전담팀" 라이브
            # 검증 중 실측) — 매니저가 없는 팀은 `run_crew()`에 provider/
            # model을 전혀 안 넘겨 `llm.resolve_provider_and_model(None,
            # None)`이 ANTHROPIC_API_KEY/OPENAI_API_KEY 자동판별로
            # 떨어진다. 이 플랫폼처럼 admin이 공통 기본 LLM(`platform_
            # llm_config`)을 Ollama로 설정해둬도 이 경로는 그 설정을 전혀
            # 안 보고 그대로 실패했다 — 매니저가 있는 팀은 바로 위에서
            # 매니저 자신의 저장된 provider/model을 명시적으로 넘겨 이미
            # 이 문제를 피하고 있다(2026-10-19 수정, 바로 위 분기 주석
            # 참고). sequential 모드는 애초에 멤버 개별 provider/model을
            # 안 쓰고 크루 전체가 하나를 공유하므로(`_agent_kwargs_for()`
            # 가 전원에게 같은 `crew_llm`을 준다 — 검증 완료), 여기서
            # 명시적으로 풀어 넘기는 값도 "크루 전체가 공유할 하나"일
            # 뿐 특정 Agent 설정을 덮어쓰는 게 아니다 — `resolve_
            # provider_and_model()`의 "Agent 실행 호출부는 conn 폴백을
            # 안 받는다"는 설계 원칙(그 함수 docstring)과 충돌하지 않는다.
            # 플랫폼 공통 기본값이 설정 안 돼 있으면(`get_platform_
            # default_llm()`이 None) 이 호출은 기존과 완전히 동일하게
            # 환경변수/API 키 자동판별로 그대로 떨어진다 — 순수 추가적
            # (additive) 수정이라 기존 동작을 바꾸지 않는다.
            budget_provider, budget_model = llm.resolve_provider_and_model(
                None, None, conn=conn
            )
        # 토큰 예산(2026-11-18, 개선축 B) — 실측 사고(19KB 첨부파일 1개로
        # 로컬 모델이 15분 돌고도 빈 응답, 또는 지저분한 데이터에서
        # 미완성 추론 과정이 그대로 메시지로 누출) 재발 방지. provider/
        # model을 미리 알아야 `_build_transcript()`가 한도 안에서 잘라낼
        # 수 있다 — 위에서 매니저 유무에 따라 이미 똑같이 계산해뒀다.
        # 대화 메모리(2026-11-18, 개선축 C) — "기본 AI"/단일 Agent
        # 멘션과 같은 원칙. 색인된 청크가 없으면 비용 없이 바로 `None`.
        last_human_for_memory = conn.execute(
            "SELECT content FROM conversation_messages WHERE conversation_id = ? "
            "AND author_type = 'human' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        memory_context = conversation_memory.retrieve_context_for_query(
            conn,
            conversation_id,
            last_human_for_memory["content"] if last_human_for_memory else "",
            provider=budget_provider,
            model=budget_model,
            reserved_tokens=_transcript_reserve_tokens(
                last_human_for_memory["content"] if last_human_for_memory else "",
                budget_provider,
            ),
        )
        memory_note = (
            (
                "\n\n[이 대화에 첨부된 자료에서 찾은 관련 내용 — 아래 "
                "내용을 근거로 답변하세요.]\n" + memory_context
            )
            if memory_context
            else ""
        )
        transcript = (
            _build_transcript(
                conn,
                conversation_id,
                provider=budget_provider,
                model=budget_model,
                reserved_tokens=token_budget.count_tokens(
                    _ANTI_ANCHORING_NOTE + memory_note, budget_provider
                ),
            )
            or "대화를 시작합니다."
        ) + memory_note + _ANTI_ANCHORING_NOTE
        task_descriptions = [transcript] * len(agent_ids)
        if manager_agent_id is not None:
            result = crew_runtime.run_crew(
                conn,
                agent_ids,
                task_descriptions,
                actor_id,
                process="hierarchical",
                manager_agent_id=manager_agent_id,
                provider=budget_provider,
                model=budget_model,
            )
        else:
            result = crew_runtime.run_crew(
                conn,
                agent_ids,
                task_descriptions,
                actor_id,
                provider=budget_provider,
                model=budget_model,
            )
        from hair_platform import evaluation_reports  # 평가 HTML 리포트(2026-11-25) — 토큰은 빼고 메시지마다 리포트 저장

        crew_gate_scores, crew_report_token = evaluation_reports.split(result.get("gate_scores") or {})
        gate_scores_json = json.dumps(crew_gate_scores if result.get("gate_scores") else None, ensure_ascii=False)
        delegation_count = len(result.get("delegations") or [])
        posted = 0
        for agent in result["agents"]:
            if agent["output"] is None:
                continue  # 매니저 역할 — 산출물 없음(run_crew()의 기존 관례)
            msg_cur = conn.execute(
                "INSERT INTO conversation_messages (conversation_id, author_type, "
                "author_id, content, llm_provider, llm_model, gate_scores, "
                "delegation_count) VALUES (?, 'agent', ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    agent["agent_id"],
                    presentation_deck.finalize(agent["output"]),
                    result.get("resolved_provider"),
                    result.get("resolved_model"),
                    gate_scores_json,
                    delegation_count,
                ),
            )
            evaluation_reports.store_for_message(conn, msg_cur.lastrowid, crew_report_token)
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


def _run_team_mention_via_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    team_id: int,
    pipeline_template_id: int,
    actor_id: int,
    use_graph_engine: bool = False,
) -> None:
    """`run_team_mention_job()`이 `teams.pipeline_template_id`가 연결된
    팀일 때 위임한다 — `pipeline_runtime.py`의 단계 실행/Gate 재시도/
    에스컬레이션 로직은 전혀 수정하지 않고 그대로 재사용한다(Task
    협업 워크스페이스 경로가 이미 실전 검증한 바로 그 엔진). 이
    함수가 하는 일은 순전히 "대화라는 Task 없는 맥락을 어떻게 그
    엔진에 먹이고, 진행 상황을 어떻게 다시 대화로 돌려주는가"뿐이다.

    `use_graph_engine`(2026-11-18, "다음 개선 진행해줘" → conversations.py
    통합) — `teams.use_graph_engine`이 켜진 팀이면 `pipeline_runtime.
    run_pipeline_stage_job()` 대신 신규 LangGraph 엔진(`execution_
    graph.py`)으로 돌린다. 단계별 진행 메시지는 `run_pipeline_graph_
    job_with_callback()`(`graph.stream()` 기반)으로 매 단계(run_stage/
    advance)마다 올린다 — 기존 `on_stage_resolved` 콜백과 동일한
    체감(6단계 보강, 처음엔 "멈추거나 끝날 때 1회"로 좁혔다가 바로
    다음 라운드에서 끌어올림).

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

        if use_graph_engine:
            from hair_platform import execution_graph

            # 6단계 보강(2026-11-18) — v1에서 "멈추거나 끝날 때 1회"로
            # 좁혔던 걸 콜백 버전으로 끌어올린다 — 레거시 엔진과 동일한
            # 체감(자동 통과 단계가 여러 개 이어져도 그때그때 알림).
            execution_graph.run_pipeline_graph_job_with_callback(
                conn,
                run_id,
                actor_id,
                on_stage_resolved=lambda: post_pipeline_run_status_message(conn, run_id),
            )
        else:
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


def resume_pipeline_graph_from_approval_and_notify(
    conn: sqlite3.Connection, run_id: int, decision: dict
) -> None:
    """`run_pipeline_stage_job_and_notify()`의 신규 LangGraph 엔진
    쌍둥이 — `app.py`의 `decide_approval()`이 그래프 엔진 run
    (`pipeline_runs.engine == 'graph'`) 승인/반려 재개 BackgroundTask로
    쓴다. 재개 후 다시 자동 진행되는 매 단계마다 알린다(`run_pipeline_
    graph_job_with_callback()`과 동일한 이유). Task 협업 워크스페이스
    에서 직접 띄운 run은 `post_pipeline_run_status_message()`가
    `source_conversation_id`가 없음을 보고 조용히 아무 것도 안 해
    기존 동작과 100% 동일."""
    from hair_platform import execution_graph

    execution_graph.resume_pipeline_graph_from_approval_with_callback(
        conn,
        run_id,
        decision,
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


@llm_usage.with_scope(conversation_id="conversation_id")
def _run_default_ai_job(
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
    provider: str | None = None
    try:
        conv = get_conversation(conn, conversation_id)
        assert conv is not None
        provider, model = llm.resolve_provider_and_model(
            conv["llm_provider"], conv["llm_model"], conn=conn
        )
        last_human = context_policy.latest_human_message(conn, conversation_id)
        skills_for_augmentation: list[str] = []
        system_role = "대화 참여자"
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
                system_role = task["role_name"]
                system = deliverables.skill_grounded_system_prompt(
                    conn, task["role_name"], skills_for_augmentation
                )
            else:
                system = _DEFAULT_SYSTEM_PROMPT
        else:
            system = _DEFAULT_SYSTEM_PROMPT
        # 대화 컨텍스트 정책(2026-11-20, `context_policy.py`) — 스킬 기본값
        # (예: 내부지식검색 = 이전 대화 없음·첨부 미사용) + 이 메시지의 덮어쓰기
        # + 후속 질문 자동 상향. 이후 검색어·첨부 메모리·대화록·지시문이
        # 전부 이 정책을 따른다.
        question = last_human["content"] if last_human is not None else ""
        policy = context_policy.finalize(
            context_policy.for_skills(conn, skills_for_augmentation),
            last_human["context_override"] if last_human is not None else None,
            question,
        )
        context_note = _context_note(policy)
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
        callable_tools: list = []
        if skills_for_augmentation:
            query = context_policy.retrieval_query(
                conn, conversation_id, question, policy
            )
            retrieved_context = agent_runtime.retrieve_context(
                conn, skills_for_augmentation, query
            )
            # 스킬 활용·품질 개선(2026-11-21, `skill_prompting.py`) — 조회 결과와
            # 실제로 호출 가능한 도구를 정한 뒤 그 조건에 맞는 시스템 프롬프트로 다시
            # 만든다(도구 안내가 사실과 맞고, 질문과 관련 있는 스킬이 앞에 온다).
            callable_tools = tool_calling.resolve_callable_tools(
                conn, skills_for_augmentation
            )
            system = deliverables.skill_grounded_system_prompt(
                conn,
                system_role,
                skills_for_augmentation,
                request=question,
                callable_tools=callable_tools,
                has_retrieval=bool(retrieved_context),
            )
            if retrieved_context:
                # 사용자 보고(2026-11-17, "실시간웹검색 스킬로 대화를
                # 했지만 '이번 응답은 실제 실시간 웹 검색 도구 호출이
                # 가능하지 않은 환경에서'와 같은 메시지가 반복 출력")
                # — 실측으로 원인 확인: `skill_grounded_system_prompt()`
                # 가 `NO_TOOL_LOOP_GUARD`("이 실행 경로에는 실제로 호출
                # 가능한 도구가 연결되어 있지 않습니다... 그 도구를
                # 실제로 호출할 수 없습니다")를 **무조건** 먼저 넣어둔
                # 뒤, 바로 다음에 실제 검색 결과를 붙이고 있었다 — 같은
                # 프롬프트 안에 "도구를 못 쓴다"와 "여기 실제 검색
                # 결과가 있다"가 동시에 있어 모델이 그 모순을 해소하려고
                # 매번 "도구 호출이 불가능한 환경"이라는 디스클레이머를
                # 답변에 그대로 베껴 넣었다(실제로는 서버가 이미 검색을
                # 끝내고 결과를 줬으므로 전혀 사실이 아닌 디스클레이머).
                # `NO_TOOL_LOOP_GUARD`는 일반 사례(mcp_remote/mcp_stdio
                # 등 정말 아무 결과도 못 가져오는 스킬)엔 여전히 맞는
                # 말이라 그대로 두고, 검색이 실제로 성공한 이 경우에만
                # 바로 뒤에 정정 지시를 덧붙여 모순을 없앤다.
                system = (
                    system
                    + "\n\n[실시간 조회 결과 — 아래 내용을 근거로 답변하세요. "
                    "이 검색은 시스템이 이미 대신 수행해 그 결과를 아래에 그대로 준 "
                    "것입니다 — '실시간 검색이 불가능한 환경'이라는 식의 변명이나 "
                    "디스클레이머를 답변에 넣지 말고, 아래 내용을 실제 조사한 사실처럼 "
                    "그대로 활용해 답하세요.]\n"
                    + retrieved_context
                )
        # 대화 메모리(2026-11-18, 개선축 C) — 스킬 선택과 무관하게 항상
        # 적용한다(위 블록과 달리 `skills_for_augmentation`이 있어야만
        # 도는 게 아니다 — 거대 첨부파일이 있는 대화라면 어떤 스킬이든
        # 관련 내용을 찾아 써야 한다). 이 대화에 색인된 청크가 없으면
        # `retrieve_context_for_query()`가 임베딩 호출 없이 바로 `None`을
        # 반환하므로, 첨부파일을 안 올린 평범한 대화는 비용/지연이 전혀
        # 늘지 않는다.
        memory_query = context_policy.retrieval_query(
            conn, conversation_id, question, policy
        )
        memory_context = (
            conversation_memory.retrieve_context_for_query(
                conn,
                conversation_id,
                memory_query,
                provider=provider,
                model=model,
                # system 프롬프트(스킬 그라운딩 + 내부지식검색 결과)와 대화록 몫을
                # 먼저 빼고 남은 예산만 첨부자료에 쓴다(2026-11-20 사용자 보고
                # — 첨부자료가 예산을 다 차지해 질문이 프롬프트에서 사라짐).
                reserved_tokens=token_budget.count_tokens(system, provider)
                + _transcript_reserve_tokens(question, provider),
            )
            if policy["attachments"]
            else None
        )
        if memory_context:
            system = (
                system
                + "\n\n[이 대화에 첨부된 자료에서 찾은 관련 내용 — 아래 "
                "내용을 근거로 답변하세요. 첨부파일 전체가 너무 커서 "
                "관련된 부분만 미리 찾아 드린 것입니다.]\n"
                + memory_context
            )
        # 버그 수정(2026-11-13, 사용자 보고 — "제안서작성" 스킬로
        # 요청했는데 이 대화의 직전 메시지(다른 Agent가 만든 프레젠테이션
        # HTML)의 색상표·구조를 그대로 베낀 결과가 나옴) — "기본 AI"
        # 경로도 team_mention과 똑같이 `_build_transcript()`가 넘긴
        # 과거 산출물에 앵커링될 위험을 그대로 안고 있었는데
        # `_ANTI_ANCHORING_NOTE`가 안 붙어 있었다. 같은 위험이면 같은
        # 방어를 적용한다(스킬 그라운딩이 있을 때만이 아니라 항상 —
        # 멘션 없는 기본 AI 응답도 과거 메시지를 그대로 베낄 수 있다).
        #
        # 토큰 예산(2026-11-18, "...엔터프라이즈 환경 적용이 가능한
        # 수준의 확실한 개선 방안을 도출해줘" 개선축 B) — 실측 사고(대화
        # #14/#20, 첨부파일 19KB 하나만으로 로컬 모델 32,768토큰 한도를
        # 넘겨 15분 돌고도 빈 응답) 재발 방지. `system`은 이 지점에서
        # 이미 완성돼 있으므로(스킬 그라운딩+검색 결과 주입까지 끝남)
        # 정확히 그 토큰 수를 예약하고 넘긴다.
        transcript = _build_transcript(
            conn,
            conversation_id,
            provider=provider,
            model=model,
            reserved_tokens=token_budget.count_tokens(system, provider)
            + token_budget.count_tokens(context_note, provider),
            history_mode=policy["history"],
        ) + context_note
        # Tool 호출 제약 해소(2026-11-18, 개선축 A) — 이 경로("기본 AI")는
        # 지금까지 CrewAI Agent/Task/Tool 루프가 없어 custom_python/native
        # 도구를 실제로 호출할 수 없었다(native는 정적 텍스트 주입만,
        # custom_python은 아예 손도 못 댐). `tool_calling.
        # resolve_callable_tools()`로 이 스킬이 가리키는 도구(native/
        # custom_python만 — mcp는 범위 밖, 모듈 독스트링 참고)를 찾아,
        # 있으면 provider 네이티브 함수 호출 루프(`llm.call_llm_with_
        # tools()`)로 돌린다 — 없으면(도구 없는 스킬/스킬 미선택) 기존과
        # 완전히 동일한 1턴 호출.
        # (`callable_tools`는 위 스킬 블록에서 시스템 프롬프트를 만들 때 이미 정했다.)
        # Gate 품질확인(2026-11-19, "다음 개선 진행해줘" → conversations.py
        # 통합) — 단일 Agent 멘션과 같은 이유로 "기본 AI" 경로도 Gate
        # A-G 측정이 전혀 없었다. 경고 배너를 덧붙이기 전의 순수 LLM
        # 출력만 측정 대상으로 삼는다(배너는 실제 생성 내용이 아니라
        # 휴리스틱을 왜곡할 수 있음).
        # 작성 중 미리보기(2026-11-24, P0-2) — 작업 안에서만 스트리밍. 미리보기가 없으면 `on_partial`을 넘기지
        # 않는다. `dict` 타입으로 묶어야 타입 검사기가 함수 값을 다른 키워드 인자 자리로 오해하지 않는다.
        stream_kwargs: dict = {}
        if (_partial := _progress.partial_sink()):
            stream_kwargs["on_partial"] = _partial
        eval_result = agent_evaluation.run_and_score(
            skill or "대화 기본 AI",
            lambda: (
                llm.call_llm_with_tools(provider, model, system, transcript, callable_tools)
                if callable_tools
                else llm.call_llm(provider, model, system, transcript, **stream_kwargs)
            ),
        )
        output = eval_result["output"]
        if skills_for_augmentation:
            unsupported = tool_calling.unsupported_skill_names(
                conn, skills_for_augmentation
            )
            if unsupported:
                # 사용자 보고(2026-11-18, "데이터분석 스킬 사용시
                # '실시간 외부 검색 도구가 연결돼 있지만...' 경고가
                # 뜬다") — 이 경고는 "mcp_remote/mcp_stdio/custom_python
                # 도구는 이 경로(CrewAI 루프 없음)에서 호출이 안 된다"는
                # 구조적 사실을 알리려고 만들어졌는데(`pool_registry.
                # unsupported_realtime_skill_names()` 참고), 문구가 그
                # 도구를 전부 "실시간 외부 검색"이라고 단정해버렸다 —
                # 만들어질 당시엔 실제로 전부 검색 도구(일반웹검색 등)
                # 였지만, 이후 "데이터분석"처럼 검색이 아니라
                # 계산(수치 데이터 분석기, 합계/평균/증감률 등)을 하는
                # custom_python 도구도 생겨 더는 사실이 아니게 됐다.
                # 어떤 종류의 도구든 맞는, 도구 종류를 단정하지 않는
                # 문구로 바꾼다.
                #
                # 추가 수정(2026-11-18, "스킬이 아니라 에이전트를 호출
                # 하는 방식이면 가능한지 분석해줘" 조사 중 발견) —
                # "Agent/Team을 멘션하면 된다"는 기존 안내도 틀렸다.
                # 단일 Agent 멘션(`run_agent_mention_job()` →
                # `agent_runtime.execute_agent()`)도 이 경로와 완전히
                # 똑같은 구조적 제약(도구 호출 루프 없음)을 가져서
                # 똑같이 안 된다 — 실제로 도구가 호출되는 건 Team
                # 멘션(`crew_runtime.run_crew()`, 진짜 CrewAI Tool
                # 루프)뿐이다. "Agent"를 빼고 "Team"만 안내한다.
                names = ", ".join(f'"{n}"' for n in unsupported)
                output = (
                    f"> ⚠ {names} 스킬에 연결된 도구 서버(MCP)에 연결하지 못해 그 도구를 실행하지 않고 "
                "답변했습니다 — admin은 '도구/스킬 Pool' 탭에서 그 도구의 서버 주소·실행 명령을 확인하세요.\n\n"
                + output
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
        # 언어 품질 검사 + 자동 교정 1회(2026-11-20, `language_qa.py`) — 한국어
        # 응답에 섞인 중국어·한자·깨진 글자를 같은 모델로 문장 단위 교정한다.
        output = language_qa.repair_output(
            conn, output, provider, model, actor_id, "conversation_default_ai"
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
            gate_scores=eval_result["gate_scores"],
            applied_context_policy=policy,
        )
    except Exception as exc:  # noqa: BLE001
        _fail_job(conn, job_id, actor_id, exc, provider=provider)


# 실행 중 단계 표시(2026-11-23, P0-2 — `progress.py`) — 단일 Agent 멘션·기본 AI 작업은 그 안의
# 조회·생성·도구 실행·검토·교정 단계를 작업 행에 남긴다. 팀 멘션(CrewAI)은 실행 중 이벤트를
# 받을 수 없어 대상이 아니다.
def run_agent_mention_job(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    agent_id: int,
    actor_id: int,
) -> None:
    from hair_platform import progress

    # MCP 도구 세션(2026-11-24, P1-7 — `tool_calling.mcp_session`): 이 작업 동안 MCP 서버 접속을 열어 둔다.
    with progress.reporting(conn, "conversation_jobs", job_id), tool_calling.mcp_session():
        return _run_agent_mention_job(conn, job_id, conversation_id, agent_id, actor_id)


@knowledge_access.viewer_from_actor  # 지식 검색 공개 범위를 요청자 기준으로(2026-11-25, P1-5)
def run_default_ai_job(
    conn: sqlite3.Connection,
    job_id: int,
    conversation_id: int,
    actor_id: int,
    skill: str | None = None,
) -> None:
    from hair_platform import progress

    with progress.reporting(conn, "conversation_jobs", job_id), tool_calling.mcp_session():
        progress.report("응답 생성 중")
        return _run_default_ai_job(conn, job_id, conversation_id, actor_id, skill)


