"""협업 워크스페이스 실행(2026-11-27 — `deliverables.py` 2,846줄 분리, 기술 부채 정리).

`deliverables.py`에는 참가자·결과물·댓글·결재 반영·할 일 설정·선행 관계·활동·버전 이력이 남고, 여기에는 할 일에서 Agent·
팀 Crew·즉석 Crew를 **실행하는 작업**(실행 가능 여부, 시작, 백그라운드 실행, 재시도, 실행 목록·정리)이 있다. 본문은 옮기기
전 그대로다. 예전 이름(`deliverables.run_team_crew_job` 등)은 `deliverables` 모듈이 이 모듈로 넘겨 주므로(모듈
`__getattr__`) 호출부·작업 큐 경로·테스트 패치는 바꾸지 않아도 된다.
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
    evaluation_reports,
    language_qa,
    llm_usage,  # 업무 맥락별 사용량(2026-11-23, P2-11)
    presentation_deck,
    teams,
)

from hair_platform.deliverables import (  # noqa: E402 — 결과물 쪽 공용 도우미
    _CONTENT_TYPE_INSTRUCTIONS,
    _friendly_execution_error,
    task_participant_relationship,
)


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


@llm_usage.with_scope(task_id="task_id", agent_id="agent_id")
def _run_agent_execution_job(
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
            task_type=agent_evaluation.eval_task_type_for(content_type),
        )
        # 언어 품질 검사 + 자동 교정 1회(2026-11-20, `language_qa.py`).
        output = language_qa.repair_output(
            conn,
            eval_result["output"],
            retrieval_sink.get("llm_provider"),
            retrieval_sink.get("llm_model"),
            actor_id,
            "task_agent_execution",
        )

        cur = conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
            (task_id, agent_id, *presentation_deck.finalize_with_type(output, content_type)),
        )
        deliverable_id = cur.lastrowid
        assert deliverable_id is not None
        # 수치 근거·테스트 실제 실행(2026-11-23, P0-1·P0-3). 근거 = 실행 때 실제로 넣은 프롬프트.
        agent_evaluation.attach_quality_checks(
            eval_result["gate_scores"], output, retrieval_sink.get("augmented_prompt") or prompt
        )
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
                "gate_scores": evaluation_reports.public(eval_result["gate_scores"]),
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
        assert cur.lastrowid is not None
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
        assert cur.lastrowid is not None
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
        agent_evaluation.attach_quality_checks(agent_gate_scores, output_raw)
        cur = conn.execute(
            "INSERT INTO deliverables (task_id, author_type, author_id, content, "
            "content_type, status) VALUES (?, 'agent', ?, ?, ?, 'pending_review')",
            (task_id, agent_id, *presentation_deck.finalize_with_type(output_raw, content_type)),
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
                "gate_scores": evaluation_reports.public(agent_gate_scores),
                "tcr": None,
                "partial_recovery": True,
            },
        )


@llm_usage.with_scope(task_id="task_id")
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
                (
                    task_id,
                    agent["agent_id"],
                    *presentation_deck.finalize_with_type(agent["output"], content_type),
                ),
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
                    "gate_scores": evaluation_reports.public(agent_gate_scores),
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


@llm_usage.with_scope(task_id="task_id")
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
                (
                    task_id,
                    agent["agent_id"],
                    *presentation_deck.finalize_with_type(agent["output"], content_type),
                ),
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
                    "gate_scores": evaluation_reports.public(agent_gate_scores),
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
        description_by_agent_id = dict(zip(run["agent_ids"], original_descriptions, strict=False))

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


def reap_orphaned_execution_jobs(conn: sqlite3.Connection, keep: frozenset[int] | set[int] = frozenset()) -> int:
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
    # 작업 큐에 아직 대기 중인 작업의 행은 남긴다(2026-11-26, P1-6 2단계 — 작업 프로세스가 곧 실행한다).
    rows = [r for r in rows if r["id"] not in keep]
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
    from hair_platform import progress, tool_calling

    # MCP 도구 세션(2026-11-24, P1-7) — 워크스페이스 단일 Agent 실행도 MCP 도구를 쓸 수 있게.
    with progress.reporting(conn, "agent_execution_jobs", job_id), tool_calling.mcp_session():
        return _run_agent_execution_job(
            conn, job_id, task_id, agent_id, actor_id, role_name, prompt, provider, model,
            content_type,
        )


