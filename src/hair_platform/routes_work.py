"""업무 API(업무 요청·할 일·내 업무·전체 업무·인박스·배정·실행 작업·파이프라인 실행 조회·AI에게 일 맡기기·후보 매칭) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import json
import sqlite3
from functools import partial

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from hair_platform import job_queue  # 백그라운드 작업 큐(2026-11-26, P1-6 2단계)
from hair_platform import (
    assignments,
    audit,
    brief_decompose_llm,
    conversations,
    cost,
    crew_history,
    crew_runtime,
    dashboard,
    data_admin,
    deliverables,
    documents,
    inbox,
    jobs,
    match_llm_judge,
    pipeline_runtime,
    pool,
    pool_registry,
    teams,
)
from hair_platform.app import (
    parse_brief_stub,
)
from hair_platform.assignment import match_candidates
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    AdhocCrewExecuteRequest,
    AssignmentCancelRequest,
    AssignmentRequest,
    AssistRequest,
    BriefSubmission,
    DelegateAgentRequest,
    DeliverableCreate,
    GenerateRequest,
    PipelineExecuteRequest,
    TaskDecomposition,
    TaskDependencyCreate,
    TaskHumanJudgmentUpdate,
    TaskNotesUpdate,
    TaskParticipantInvite,
    TaskPriorityUpdate,
    TaskRetryBudgetUpdate,
    TaskStatusUpdate,
    TeamCrewExecuteRequest,
    TeamLeaderUpdate,
    WizardRequest,
)
from hair_platform.routes_common import (
    _reject_if_too_large,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """업무 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.post("/api/briefs", status_code=201)
    def submit_brief(
        payload: BriefSubmission,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        if not payload.raw_text.strip():
            raise HTTPException(
                status_code=422, detail="업무 요청 내용을 입력하세요"
            )
        # 역할 후보(2026-11-22, 하드코딩·스텁 점검 2순위) — 요청 본문과 맞는 기존 Agent 역할을
        # 먼저 제안하고(LLM 없음·무료), 맞는 역할이 없을 때만 예전처럼 줄 단위로 나눈다.
        from hair_platform import pool_catalog

        lines = parse_brief_stub(payload.raw_text)
        # 짧은 줄 여러 개("백엔드 개발자 1명\n디자이너 1명")는 사용자가 역할을 직접 나열한 것 — 그대로 쓴다.
        explicit_role_list = len(lines) >= 2 and all(len(line) <= 20 for line in lines)
        stub_roles = lines if explicit_role_list else (
            pool_catalog.suggest_roles(conn, payload.raw_text) or lines
        )
        source = payload.source_conversation_id
        if source is not None and not conversations.is_participant(conn, source, user["id"]):
            raise HTTPException(status_code=403, detail="참가 중인 대화에서만 업무 요청으로 전환할 수 있습니다")
        cur = conn.execute(
            "INSERT INTO briefs (submitted_by, raw_text, stub_roles, source_conversation_id) "
            "VALUES (?, ?, ?, ?)",
            (user["id"], payload.raw_text, json.dumps(stub_roles, ensure_ascii=False), source),
        )
        conn.commit()
        brief_id = cur.lastrowid  # INSERT 직후라 늘 있다(타입상 int | None)
        if source is not None and brief_id is not None:
            conversations.record_brief_transfer(conn, source, user["id"], brief_id)
        return {"id": brief_id, "stub_roles": stub_roles, "status": "submitted",
                "source_conversation_id": source}

    # ---------- AI에게 일 맡기기 마법사(2026-11-24, P2-8 — `wizard.py`) ----------
    @app.post("/api/wizard/recommend")
    def wizard_recommend(payload: WizardRequest, user: sqlite3.Row = Depends(get_current_user)):
        """적은 업무 한 문장 → 맞는 방법(빠른 질문·Agent·파이프라인·정기 실행) 추천(LLM 없음·무료)."""
        from hair_platform import wizard

        try:
            return wizard.recommend(conn, payload.text, user["role"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="맡길 일을 한 문장 이상 적어 주세요") from exc

    @app.get("/api/briefs")
    def list_briefs(user: sqlite3.Row = Depends(get_current_user)):
        """Web UI가 브리프 목록을 브라우징할 수 있어야 Task 분해 화면으로
        이어갈 수 있다 — 지금까지는 생성 응답의 id를 기억해야만 했다.

        사용자 요청(2026-11-02, "업무 요청 목록에 요청자를 명시해줘") —
        `submitted_by`는 원래도 응답에 있었지만 숫자 ID뿐이라 화면에
        표시할 수 없었다. `users`를 조인해 이름도 같이 내려준다(계정이
        삭제돼도 `submitted_by` 자체는 FK라 브리프가 남아있는 한 안
        끊기지만, 혹시 몰라 `None` 폴백은 둔다).

        사용자 보고(2026-11-02, "admin이 요청한 업무가 모든 멤버에게
        보이고 있어") — 실측 확인된 진짜 버그. 이 엔드포인트는
        `get_current_user`만 요구해 member/viewer를 포함한 로그인한
        전원이 조직 전체 브리프를 다 봤다. `/api/my-work`
        (`deliverables.list_my_work`, 전원 — 본인 관련 것만) vs
        `/api/all-work`(admin/manager 전용, 조직 전체)로 이미 갈라
        둔 "조직 전체 현황은 admin/manager만" 원칙과 어긋난다. 같은
        원칙을 그대로 적용한다: admin/manager는 전체, member/viewer는
        **본인이 제출했거나(submitted_by) 그 브리프 아래 Task에 확정
        배정으로 관련된(사람 당사자/Agent 요청자/팀 리더·팀원) 것만**
        — `list_my_work()`의 관계 판정 로직(사람은 human_pool.user_id로,
        팀은 team_leader_id 또는 team_members 소속으로 식별)을 그대로
        따른다."""
        base_sql = (
            "SELECT b.*, u.username AS submitted_by_username FROM briefs b "
            "LEFT JOIN users u ON u.id = b.submitted_by"
        )
        if user["role"] in (Role.ADMIN.value, Role.MANAGER.value):
            rows = conn.execute(base_sql + " ORDER BY b.id DESC").fetchall()
        else:
            rows = conn.execute(
                base_sql + """
                WHERE b.submitted_by = ?
                   OR EXISTS (
                       SELECT 1 FROM tasks t
                       JOIN assignments a ON a.task_id = t.id AND a.status = 'confirmed'
                       LEFT JOIN human_pool hp
                           ON hp.id = a.resource_id AND a.resource_type = 'human'
                       WHERE t.brief_id = b.id
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
                   )
                ORDER BY b.id DESC
                """,
                (user["id"], user["id"], user["id"], user["id"], user["id"]),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "raw_text": row["raw_text"],
                "stub_roles": json.loads(row["stub_roles"]),
                "status": row["status"],
                "submitted_by": row["submitted_by"],
                "submitted_by_username": row["submitted_by_username"],
            }
            for row in rows
        ]

    @app.delete("/api/briefs/{brief_id}")
    def delete_brief_endpoint(
        brief_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28, "각 메뉴에서 데이터 삭제나, 초기화 기능
        추가") — brief와 그 아래 tasks·jobs·assignments를 전부 지운다.
        `confirmed` 배정이 있으면 막는다(먼저 배정/결재에서 정리)."""
        try:
            data_admin.delete_brief(conn, user["id"], brief_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="업무 요청을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            # 무엇이 막는지(할 일·리소스)를 보여 준다(2026-11-25).
            raise HTTPException(
                status_code=409,
                detail=getattr(exc, "detail", None)
                or "확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        except data_admin.DeleteBlocked as exc:
            raise HTTPException(status_code=409, detail=exc.reason) from exc
        except sqlite3.Error as exc:
            # 예상 못 한 DB 오류도 500 빈 응답 대신 이유를 보인다(삭제는 이미 되돌려짐 — `data_admin._atomic_delete`).
            raise HTTPException(status_code=409, detail=f"삭제하지 못했습니다(변경 없음): {exc}") from exc
        return {"id": brief_id, "deleted": True}

    @app.post("/api/briefs/{brief_id}/tasks", status_code=201)
    def decompose_brief_into_tasks(
        brief_id: int,
        payload: TaskDecomposition,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Task 분해 결과 등록(후보 매칭은 포함하지 않는다).

        `depends_on_index`(사용자 요청, 2026-11-04, "업무 분해 단위
        가이드" 설계) — 같은 배치 안의 다른 항목을 선행 작업으로
        지정할 수 있다. 전체 Task를 먼저 다 만든 뒤에야 실제
        `task_id`가 생기므로, 의존관계는 Task 삽입이 끝난 다음
        두 번째 루프에서 연결한다. 순환 등으로 하나라도 실패하면
        이미 삽입한 Task·의존관계를 전부 롤백한다(부분 생성 상태로
        남기지 않음 — 기존 코드는 이 분기 자체가 없었다)."""
        brief = conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")

        created = []
        for item in payload.tasks:
            cur = conn.execute(
                "INSERT INTO tasks (brief_id, role_name, required_skills, headcount) "
                "VALUES (?, ?, ?, ?)",
                (
                    brief_id,
                    item.role_name,
                    json.dumps(item.required_skills, ensure_ascii=False),
                    item.headcount,
                ),
            )
            created.append({"id": cur.lastrowid, "role_name": item.role_name})

        try:
            for i, item in enumerate(payload.tasks):
                if item.depends_on_index is None:
                    continue
                deliverables.add_task_dependency_for_decomposition(
                    conn,
                    created[i]["id"],
                    user["id"],
                    created[item.depends_on_index]["id"],
                )
        except ValueError as exc:
            conn.rollback()
            detail = {
                "cycle_detected": "이 의존관계 조합은 순환을 만듭니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=409, detail=detail) from None

        conn.execute("UPDATE briefs SET status = 'reviewed' WHERE id = ?", (brief_id,))
        conn.commit()
        return {"brief_id": brief_id, "tasks": created}

    @app.post("/api/briefs/{brief_id}/decompose-suggest")
    def suggest_brief_decomposition_endpoint(
        brief_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """LLM이 업무 요청을 독립 검증 가능한 할 일 단위로 쪼개도록
        제안만 돌려준다 — DB에 아무것도 쓰지 않는다(admin이 검토·수정
        후 `POST /api/briefs/{brief_id}/tasks`로 직접 제출해야 실제로
        생성됨). 실제 LLM API를 호출해 비용이 발생한다(이 엔드포인트를
        명시적으로 호출했을 때만)."""
        try:
            return brief_decompose_llm.suggest_decomposition(conn, brief_id)
        except ValueError as exc:
            if str(exc) == "brief_not_found":
                raise HTTPException(
                    status_code=404, detail="브리프를 찾을 수 없습니다"
                ) from None
            raise

    @app.get("/api/briefs/{brief_id}/tasks")
    def list_tasks_for_brief(
        brief_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Web UI가 매칭/배분 화면에서 브리프별 Task 목록을 골라야 한다 —
        지금까지는 분해 응답의 id를 기억해야만 했다.

        사용자 요청(2026-09-29, "각 업무의 결과물을 어떻게 확인하는지...
        분석하고 부족한 기능을 개선해줘")에 따라 `deliverables.
        task_completion_summary()`(`내 업무` 목록과 공용)를 붙여, 이
        화면만 보고도 어떤 할 일이 최종 결과물(승인됨)까지 나왔는지
        알 수 있게 했다 — 이전엔 할 일마다 워크스페이스를 직접 열어야
        했다."""
        rows = conn.execute(
            "SELECT * FROM tasks WHERE brief_id = ? ORDER BY id", (brief_id,)
        ).fetchall()
        return [
            {
                "id": row["id"],
                "role_name": row["role_name"],
                "required_skills": json.loads(row["required_skills"]),
                "headcount": row["headcount"],
                "notes": row["notes"],
                "status": row["status"],
                "priority": row["priority"],
                **deliverables.task_completion_summary(conn, row["id"]),
            }
            for row in rows
        ]

    @app.delete("/api/briefs/{brief_id}/tasks/{task_id}")
    def delete_task_endpoint(
        brief_id: int,
        task_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — 할 일(task) 하나를 그 아래
        assignments·job_items까지 지운다. `confirmed` 배정이 있으면
        막는다. `brief_id`는 URL 일관성(중첩 리소스)을 위해 받지만 실제
        조회·삭제는 `task_id`만으로 한다."""
        del brief_id
        try:
            data_admin.delete_task(conn, user["id"], task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            # 무엇이 막는지(할 일·리소스)를 보여 준다(2026-11-25).
            raise HTTPException(
                status_code=409,
                detail=getattr(exc, "detail", None)
                or "확정된(confirmed) 배정이 있어 삭제할 수 없습니다 — 먼저 정리하세요",
            ) from exc
        except data_admin.DeleteBlocked as exc:
            raise HTTPException(status_code=409, detail=exc.reason) from exc
        except sqlite3.Error as exc:
            # 예상 못 한 DB 오류도 500 빈 응답 대신 이유를 보인다(삭제는 이미 되돌려짐 — `data_admin._atomic_delete`).
            raise HTTPException(status_code=409, detail=f"삭제하지 못했습니다(변경 없음): {exc}") from exc
        return {"id": task_id, "deleted": True}

    @app.get("/api/pool/search")
    def search_pool(
        role: str | None = None,
        skill: str | None = None,
        availability: str | None = None,
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """Human Pool + Agent Pool(스텁) 통합 검색. 전원 조회 가능(RBAC 매트릭스).

        코드 재점검(2026-09-26)에서 발견: role/skill 필터가 human_pool에만 걸리고
        Agent Pool은 무조건 전부 반환되던 버그를 수정 — 양쪽에 동일 필터를 적용한다.

        Phase 5("검색/필터 스케일") — `available_only`(boolean)를
        `availability`(가능/불가 둘 다 명시적으로 찾을 수 있게)로 넓히고,
        감사로그와 같은 `limit`/`offset`+`total` 페이지네이션 규약을
        적용했다. Human+Agent를 하나로 합친 뒤 자르는 방식이라(두 풀을
        SQL에서 각각 페이지네이션하면 "한 페이지"가 한쪽 풀로만 채워질
        수 있어 부정확해진다) 큰 풀에서는 비효율적일 수 있다 — 이 저장소가
        전제하는 소규모 팀 규모에서는 허용 가능한 트레이드오프다.

        "부서" 필터(원안)는 넣지 않았다 — 이 스키마엔 애초에 "부서"라는
        필드가 없다(조직 구조는 `manager_id` 상사 체인/조직도로만 표현된다).
        존재하지 않는 데이터를 필터로 꾸며내지 않고, 이미 있는 `role`이
        사실상 같은 역할(직군 단위 검색)을 한다는 점을 그대로 문서화한다.

        **실측으로 발견한 버그(리뷰, "Phase1~5까지... 분석하고 개선해줘")**:
        `availability` 필터가 `human_pool_candidates()` 안에만 있어서,
        `availability='unavailable'`로 검색해도 Agent(항상 `is_available
        =True`로 고정)는 이 필터를 아예 안 받아 그대로 결과에 섞여
        나왔다 — "불가능한 후보만" 찾는 필터가 사실상 안 지켜졌다.
        Agent는 애초에 "불가" 상태가 없다는 걸 반영해, `availability=
        'unavailable'`일 땐 Agent 후보 자체를 통째로 뺀다.
        """
        limit = max(1, min(limit, 200))
        agent_candidates = (
            []
            if availability == "unavailable"
            else pool.agent_pool_candidates(conn, role=role, skill=skill)
        )
        candidates = (
            pool.human_pool_candidates(
                conn, role=role, skill=skill, availability=availability
            )
            + agent_candidates
        )
        rows = conn.execute("SELECT id, name FROM human_pool").fetchall()
        name_by_id = {row["id"]: row["name"] for row in rows}
        # 사용자 보고(2026-10-06)로 `ResourceCandidate.user_id`가 추가됐다
        # — 이 검색은 viewer까지 전원 조회 가능(RBAC 매트릭스)이라, 계정
        # 미연결 경고는 그보다 좁은 매칭/배정 화면(admin/manager/member)
        # 응답에만 노출하고 여긴 그대로 뺀다(불필요한 노출 확대 방지).
        items = [
            {
                **c.model_dump(exclude={"user_id"}),
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 전엔 Agent는 항상 None으로 덮어썼다(이름 자체가 없던
                # 시절 코드). `pool.agent_pool_candidates()`가 이제
                # `candidate.name`을 채워 주므로 Agent는 그 값을 그대로
                # 쓴다(사람은 기존처럼 따로 조회한 값을 그대로 쓴다).
                "name": name_by_id.get(c.id) if c.type == "human" else c.name,
            }
            for c in candidates
        ]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/tasks/{task_id}/match")
    def match_task(
        task_id: int,
        use_llm_judge: bool = False,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """매칭 미리보기 + 점수 breakdown — 배정 요청(assignments)은 v2 범위.

        사용자 결정(2026-09-29, "매칭 점수 breakdown을 배정 후보
        본인에게 공개") — MEMBER도 호출할 수 있게 열되, 다른 후보의
        breakdown까지 보이면 프라이버시 문제라 자기 자신(human_pool.
        user_id로 연결된 항목)에 해당하는 한 건만 필터링해 돌려준다.

        사용자 요청(2026-11-02, "LLM judge로 추천 인력을 찾도록 개선") —
        결정론적 매칭(`match_candidates()`, 무료·즉시 — 역할명 부분일치
        포함)이 `generate_new=True`를 반환했을 때만, 그리고 호출부가
        `use_llm_judge=True`를 명시적으로 줬을 때만 `match_llm_judge.
        judge_candidates()`를 2차로 호출한다 — `llm.py`의 "비용 발생
        호출은 상위에서 게이팅" 원칙과 동일하게 기본값은 호출 없이
        무료로 끝난다(`retry_job_item`의 `use_llm` 파라미터와 같은
        패턴).

        사용자 요청(2026-11-02, "1번 방식 찾기시에 3번(직접 검색)처럼
        부분일치로 찾아지도록 개선하고 3번 방식은 삭제") — 수동 검색
        폴백("인력 검색"과 같은 `/api/pool/search` 재사용)은 제거했다.
        결정론적 매칭 자체가 역할명 부분일치까지 보도록 넓어졌고
        (`assignment._role_matches()`), 그래도 못 찾으면 LLM judge가
        최종 수단이다."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")

        required_skills = json.loads(task["required_skills"])
        candidates = pool.human_pool_candidates(conn) + pool.agent_pool_candidates(conn)
        result = match_candidates(task["role_name"], required_skills, candidates)
        match_mode = "deterministic"

        if result.generate_new and use_llm_judge:
            result = match_llm_judge.judge_candidates(conn, task, candidates)
            match_mode = "llm_judge" if not result.generate_new else "llm_judge_failed"

        top = [
            {
                "candidate": sc.candidate.model_dump(),
                "score": sc.score,
                "breakdown": sc.breakdown,
                "exact_match": sc.exact_match,
                "rationale": sc.rationale,
            }
            for sc in result.top
        ]
        if user["role"] == Role.MEMBER.value:
            my_human = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (user["id"],)
            ).fetchone()
            my_human_id = my_human["id"] if my_human else None
            top = [
                t
                for t in top
                if t["candidate"]["type"] == "human"
                and t["candidate"]["id"] == my_human_id
            ]

        return {
            "task_id": task_id,
            "generate_new": result.generate_new,
            "top": top,
            "match_mode": match_mode,
        }

    @app.post("/api/tasks/{task_id}/assignments", status_code=201)
    def request_assignment(
        task_id: int,
        payload: AssignmentRequest,
        user: sqlite3.Row = Depends(
            require_role(Role.ADMIN, Role.MANAGER, Role.MEMBER)
        ),
    ):
        """배정 요청. 승인 전까지는 `requested` 상태로만 남는다.

        사용자 결정(2026-09-29, "팀원 셀프서비스(본인이 직접 배정 요청)를
        허용") — MEMBER는 본인의 human_pool 레코드로만 요청할 수 있다
        (다른 사람·Agent를 대신 요청하는 건 여전히 admin/manager 전용).
        승인 게이트와 self-approval 방지는 그대로 적용되므로
        셀프서비스는 "요청 권한"만 넓힐 뿐 확정 권한은 늘리지 않는다."""
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task를 찾을 수 없습니다")
        if user["role"] == Role.MEMBER.value:
            my_human = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (user["id"],)
            ).fetchone()
            if (
                payload.resource_type != "human"
                or my_human is None
                or payload.resource_id != my_human["id"]
            ):
                raise HTTPException(
                    status_code=403, detail="본인 배정 요청만 가능합니다"
                )
        try:
            return assignments.create_assignment_request(
                conn,
                task_id,
                payload.resource_type,
                payload.resource_id,
                user["id"],
                team_leader_id=payload.team_leader_id,
                replaces_assignment_id=payload.replaces_assignment_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/delegate", status_code=201)
    def delegate_agent(
        task_id: int,
        payload: DelegateAgentRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """위임 — 결재 없이 즉시 확정되는 경량 배정. `request_assignment`
        ("배정")와 같은 ADMIN/MANAGER 권한 경계를 쓴다(Agent 관련 배정
        행동이 지금까지 MEMBER 셀프서비스 밖이었던 것과 동일) — 결재가
        없어 더 즉각적인 만큼, 권한은 더 좁히지 않고 그대로 유지한다."""
        try:
            return assignments.create_delegation(
                conn, task_id, payload.agent_id, user["id"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/assignments")
    def list_task_assignments(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-06, "팀 구성과 협업 프로세스를 하나의
        화면으로 합치는 방안" → 옵션 C 채택) — "업무 요청" 탭에서 할 일
        하나의 배정 현황(요청됨/확정됨/반려됨/대체됨)을 바로 보여주려면
        이 할 일에 걸린 `assignments` 전체를 조회할 방법이 있어야 하는데
        지금까지 없었다(`/api/org-chart`는 confirmed만, 그마저 task
        단위 조회가 아니다). 조직도(`GET /api/org-chart`)가 이미 전원
        공개하는 것과 같은 수준(이름+상태, 비용·성과점수는 제외)이라
        `get_current_user`로 연다 — status 제한 없이 이력 전체를
        최신순으로 돌려준다(화면에서 현재 상태/과거 이력을 구분해
        보여줄 수 있도록)."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        rows = conn.execute(
            """
            SELECT a.id, a.resource_type, a.resource_id, a.status,
                   a.team_leader_id,
                   hp.name AS human_name,
                   COALESCE(ap.name, ap.role_name) AS agent_role_name,
                   t.name AS team_name,
                   pt.name AS pipeline_name
            FROM assignments a
            LEFT JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
            LEFT JOIN agent_pool ap ON ap.id = a.resource_id AND a.resource_type = 'agent'
            LEFT JOIN teams t ON t.id = a.resource_id AND a.resource_type = 'team'
            LEFT JOIN pipeline_templates pt ON pt.id = a.resource_id AND a.resource_type = 'pipeline'
            WHERE a.task_id = ?
            ORDER BY a.id DESC
            """,
            (task_id,),
        ).fetchall()
        resource_names = {
            "human": lambda row: row["human_name"],
            "agent": lambda row: row["agent_role_name"],
            "team": lambda row: row["team_name"],
            "pipeline": lambda row: row["pipeline_name"],
        }
        return [
            {
                "id": row["id"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "resource_name": resource_names[row["resource_type"]](row),
                "status": row["status"],
                "team_leader_id": row["team_leader_id"],
            }
            for row in rows
        ]

    @app.get("/api/my-work")
    def get_my_work(
        limit: int = 50,
        offset: int = 0,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """확정된 배정 중 내가 당사자이거나(사람) 요청자인(Agent) task."""
        return deliverables.list_my_work(conn, user["id"], limit, offset)

    @app.get("/api/all-work")
    def get_all_work(
        limit: int = 200,
        offset: int = 0,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """할 일 보드의 "전체" 범위. `/api/my-work`와 같은 모양을
        반환하되 user_id로 안 좁힌다 — 조직 전체 현황이라 ADMIN/MANAGER
        전용."""
        return deliverables.list_all_work(conn, limit, offset)

    @app.get("/api/inbox")
    def get_inbox_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        """Phase 5 — 통합 인박스. 결재 대기(Admin/매니저)·Agent 실행
        실패·낮은 Gate 점수 경고를 한 목록으로 합쳐 반환한다. 새 권한
        규칙을 추가하지 않는다 — 각
        항목은 기존 조회 함수(`approvals.list_inbox`/`deliverables.
        list_my_work`)가 이미 강제하는 접근 범위를 그대로 물려받는다."""
        return inbox.get_inbox(conn, user["id"], user["role"])

    @app.get("/api/tasks/{task_id}")
    def get_task_endpoint(task_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """2026-10-08 — Task 단건 조회.
        지금까지 Task 단건 조회 엔드포인트가 없어 `notes` 등 새 필드를
        확인하려면 `GET /api/briefs/{brief_id}/tasks` 전체를 받아 걸러내야
        했다."""
        detail = deliverables.get_task_detail(conn, task_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return detail

    @app.get("/api/tasks/{task_id}/budget")
    def get_task_budget_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도
        예산 추적") — `cost.task_report()`(단가 기반 추정 비용 + 실제
        토큰 사용량)와 `dashboard.task_retry_summary()`(파이프라인
        단계 시도 + Crew 재실행 합산)를 한 번에 돌려준다. 권한은
        위 `GET /api/tasks/{task_id}`와 동일."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return {
            "cost": cost.task_report(conn, task_id),
            "retries": dashboard.task_retry_summary(conn, task_id),
        }

    @app.patch("/api/tasks/{task_id}/notes")
    def update_task_notes_endpoint(
        task_id: int,
        payload: TaskNotesUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """Task 메모. 참가자 본인만 수정(결재 불필요한 가벼운
        메모라 대리 작성 제한만 `create_comment()`와 동일하게 건다)."""
        try:
            return deliverables.update_task_notes(
                conn, task_id, user["id"], payload.notes
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None

    @app.patch("/api/tasks/{task_id}/status")
    def update_task_status_endpoint(
        task_id: int,
        payload: TaskStatusUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 할 일 보드 칸반 상태 이동.
        `update_task_notes_endpoint()`와 같은 참가자 전용 판정."""
        try:
            return deliverables.update_task_status(
                conn, task_id, user["id"], payload.status
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None

    @app.patch("/api/tasks/{task_id}/priority")
    def update_task_priority_endpoint(
        task_id: int,
        payload: TaskPriorityUpdate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4."""
        try:
            return deliverables.update_task_priority(
                conn, task_id, user["id"], payload.priority
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None

    @app.patch("/api/tasks/{task_id}/retry-budget")
    def update_task_retry_budget_endpoint(
        task_id: int,
        payload: TaskRetryBudgetUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도
        예산 추적") — `notes`/`status`/`priority`(참가자 전용)와 달리
        거버넌스성 설정이라 admin/manager 전용으로 좁힌다."""
        try:
            return deliverables.update_task_retry_budget(
                conn, task_id, user["id"], payload.max_retry_budget
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.patch("/api/tasks/{task_id}/human-judgment")
    def update_task_human_judgment_endpoint(
        task_id: int,
        payload: TaskHumanJudgmentUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-11-17, "Human Agency Scale... 인간 고유
        능력을 보호하는 Task는 Gate 점수와 무관하게 항상 사람에게
        올라가야 한다" → "구현 시작") — `retry-budget`과 같은 이유로
        admin/manager 전용 거버넌스 설정."""
        try:
            return deliverables.update_task_human_judgment(
                conn, task_id, user["id"], payload.requires_human_judgment
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.post("/api/tasks/{task_id}/dependencies", status_code=201)
    def add_task_dependency_endpoint(
        task_id: int,
        payload: TaskDependencyCreate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 선행 작업 추가. 자기참조·순환은
        `deliverables.add_task_dependency()`가 검사한다."""
        try:
            return deliverables.add_task_dependency(
                conn, task_id, user["id"], payload.depends_on_task_id
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except ValueError as exc:
            reason = str(exc)
            if reason == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            if reason == "depends_on_task_not_found":
                raise HTTPException(
                    status_code=404, detail="선행 작업을 찾을 수 없습니다"
                ) from None
            if reason == "self_dependency":
                raise HTTPException(
                    status_code=400,
                    detail="자기 자신을 선행 작업으로 지정할 수 없습니다",
                ) from None
            if reason == "cycle_detected":
                raise HTTPException(
                    status_code=409, detail="이 의존관계를 추가하면 순환이 생깁니다"
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 등록된 의존관계입니다"
            ) from None

    @app.delete("/api/tasks/{task_id}/dependencies/{depends_on_task_id}")
    def remove_task_dependency_endpoint(
        task_id: int,
        depends_on_task_id: int,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 4 — 설계 문서엔 없지만 POST의 자연스러운
        대칭으로 추가(`remove_task_dependency()` 독스트링 참고)."""
        try:
            deliverables.remove_task_dependency(
                conn, task_id, user["id"], depends_on_task_id
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=404, detail="등록된 의존관계를 찾을 수 없습니다"
            ) from None
        return {
            "task_id": task_id,
            "depends_on_task_id": depends_on_task_id,
            "deleted": True,
        }

    @app.get("/api/tasks/{task_id}/dependencies")
    def get_task_dependencies_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """2026-10-08, Phase 4 — **사용자 결정**: 1차 "선행 작업
        목록"이 아니라 Riido 수준의 그래프 시각화로 바로 간다. 이
        엔드포인트는 그 그래프가 그릴 노드/엣지(연결된 컴포넌트 전체)를
        반환한다(`deliverables.task_dependency_graph()`)."""
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            return deliverables.task_dependency_graph(conn, task_id)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.get("/api/tasks/{task_id}/activity")
    def get_task_activity_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """배정·결과물·댓글·Agent 실행·결재·Task 변경을 합친 통합
        타임라인."""
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            return deliverables.task_activity(conn, task_id)
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.post("/api/tasks/{task_id}/participants", status_code=201)
    def invite_task_participant_endpoint(
        task_id: int,
        payload: TaskParticipantInvite,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """2026-10-08, Phase 2 — 결재 불필요한 가벼운 초대.
        admin/manager는 항상 가능하고, 그 외엔
        이 task에 이미 댓글을 달 수 있는 사람(정식 참가자 또는 이미
        초대된 사람)만 — "초대의 초대"는 허용하되 완전한 외부인이
        마음대로 사람/Agent를 끌어들이진 못하게 한다."""
        if user["role"] not in ("admin", "manager") and not (
            deliverables.can_comment_on_task(conn, task_id, user["id"])
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무에 참가자를 초대할 권한이 없습니다",
            )
        try:
            return deliverables.invite_participant(
                conn,
                task_id,
                user["id"],
                payload.participant_type,
                payload.participant_id,
            )
        except ValueError as exc:
            if str(exc) == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            if str(exc) == "agent_not_found":
                raise HTTPException(
                    status_code=404, detail="Agent를 찾을 수 없습니다"
                ) from None
            if str(exc) == "user_not_found":
                raise HTTPException(
                    status_code=404, detail="사용자를 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=409, detail="이미 초대된 참가자입니다"
            ) from None

    @app.get("/api/tasks/{task_id}/participants")
    def list_task_participants_endpoint(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Phase 2 — 초대된 참가자 목록."""
        if user["role"] not in ("admin", "manager") and not (
            deliverables.can_comment_on_task(conn, task_id, user["id"])
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_participants(conn, task_id)

    @app.get("/api/tasks/{task_id}/deliverables")
    def list_task_deliverables(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """협업 워크스페이스 타임라인(시간순)."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_deliverables(conn, task_id)

    @app.post("/api/tasks/{task_id}/deliverables", status_code=201)
    def create_task_deliverable(
        task_id: int,
        payload: DeliverableCreate,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사람 결과물 작성. 대리 작성 금지(참가자 본인만)."""
        try:
            deliverable_id = deliverables.create_deliverable(
                conn, task_id, user["id"], payload.content, payload.content_type
            )
        except ValueError as exc:
            if str(exc) == "task_not_found":
                raise HTTPException(
                    status_code=404, detail="할 일을 찾을 수 없습니다"
                ) from None
            raise HTTPException(
                status_code=400, detail="내용을 입력해야 합니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        return {"id": deliverable_id}

    @app.post("/api/tasks/{task_id}/assist")
    def assist_task(
        task_id: int,
        payload: AssistRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """ "LLM으로 도와줘". 실제 LLM API를 호출하므로 비용이
        발생한다. 결과는 저장하지 않는다 — 검토 후 별도로 제출해야 한다.
        팀원/뷰어는 provider/model을 선택할 수 없다(RBAC 결정, 비용
        통제)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider or payload.model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )
        try:
            draft = deliverables.assist(
                conn,
                task_id,
                user["id"],
                payload.draft_context,
                payload.provider,
                payload.model,
                payload.content_type,
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        return {"draft": draft}

    @app.post("/api/tasks/{task_id}/execute-agent", status_code=202)
    async def trigger_agent_execution(
        task_id: int,
        background_tasks: BackgroundTasks,
        provider: str | None = Form(None),
        model: str | None = Form(None),
        agent_id: int | None = Form(None),
        content_type: str = Form("text"),
        attachment: UploadFile | None = File(None),
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """Phase 4로 비동기화 — task에 배정된 Agent 실행을 큐에
        올린다. 검증만 동기로 마치고 202로 즉시 응답하며, 실제 LLM 호출
        (비용 발생)은 응답 전송 후 백그라운드에서 진행된다 — Web UI는
        `GET .../agent-execution-jobs`로 폴링해 완료를 확인한다. 요청자
        본인 또는 매니저/관리자(감독 대리 실행 허용 — "작성"과
        달리 "지금 실행시켜라"는 대리해도 저자성이 안 흐려짐).

        사용자 요청(2026-10-26, "A안 + B안")으로 멀티파트로 전환했다 —
        `attachment`(선택)는 이번 1회 실행에만 쓸 참고자료다. 추출한
        텍스트만 `deliverables.start_agent_execution()`에 넘기고 그
        원본 파일이나 텍스트는 어디에도 저장하지 않는다(감사 로그엔
        파일명·글자수만 남김). "AI 에이전트 관리" 화면의 전역 "자료
        업로드" 버튼(지난 설계)은 업무 맥락이 없어 폐지했고, 그 자리를
        여기(업무에 실제로 배정된 실행 경로)가 대신한다."""
        if user["role"] not in ("admin", "manager") and (provider or model):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model을 직접 선택할 수 없습니다",
            )

        attachment_text: str | None = None
        attachment_filename: str | None = None
        if attachment is not None and attachment.filename:
            raw_bytes = await attachment.read()
            _reject_if_too_large(attachment.filename, raw_bytes)
            attachment_filename = attachment.filename
            try:
                attachment_text = documents.extract_text(attachment_filename, raw_bytes)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            job = deliverables.start_agent_execution(
                conn,
                task_id,
                user["id"],
                allow_supervisor_override=user["role"] in ("admin", "manager"),
                agent_id=agent_id,
                content_type=content_type,
                attachment_text=attachment_text,
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except LookupError as exc:
            if str(exc) == "agent_already_running":
                raise HTTPException(
                    status_code=409,
                    detail="이미 실행 중인 작업이 있습니다 — 완료를 기다려 주세요",
                ) from None
            raise HTTPException(
                status_code=409,
                detail="이 업무에 확정된 AI 에이전트 배정이 없습니다",
            ) from None
        if attachment_text is not None:
            audit.record(
                conn,
                user["id"],
                "agent_execution_attachment_used",
                "agent_execution_jobs",
                job["job_id"],
                after={
                    "filename": attachment_filename,
                    "char_count": len(attachment_text),
                },
            )
            conn.commit()
        job_queue.enqueue(
            conn, "deliverable.agent_execution",
            job["job_id"],
            task_id,
            job["agent_id"],
            user["id"],
            job["role_name"],
            job["prompt"],
            provider,
            model,
            content_type,
            background_tasks=background_tasks,
        )
        return {"job_id": job["job_id"], "status": "running"}

    @app.post("/api/tasks/{task_id}/execute-team-crew", status_code=202)
    def trigger_team_crew_execution(
        task_id: int,
        payload: TeamCrewExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
        개선... 사람-에이전트 연동이 가능한 구조인지 확인") — Phase 3.
        이 할 일에 confirmed로 배정된 팀 하나의 Agent 멤버 전원을 CrewAI Crew로
        실행하고, 결과를 이 할 일의 deliverables/결재 체인에 연결한다 —
        단일 Agent 실행(`execute-agent`)과 같은 비동기 패턴."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider
            or payload.model
            or payload.planning
            or payload.manager_provider
            or payload.manager_model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model/매니저 LLM/planning을 직접 선택할 수 없습니다",
            )
        try:
            job = deliverables.start_team_crew_execution(
                conn,
                task_id,
                payload.team_id,
                user["id"],
                manager_agent_id=payload.manager_agent_id,
                allow_supervisor_override=user["role"] in ("admin", "manager"),
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except LookupError as exc:
            if str(exc) == "agent_already_running":
                raise HTTPException(
                    status_code=409,
                    detail="이미 실행 중인 작업이 있습니다 — 완료를 기다려 주세요",
                ) from None
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            detail = {
                "team_not_found": "팀을 찾을 수 없습니다",
                "team_not_confirmed_for_task": "이 할 일에 확정 배정된 팀이 아닙니다",
                "team_has_no_active_agents": "이 팀에 온보딩(active)된 Agent 멤버가 없습니다",
                "manager_agent_not_in_team": "매니저로 지정한 Agent가 이 팀의 멤버가 아닙니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 호출자가
        # process/manager_agent_id를 명시하지 않았으면(기존처럼 매번
        # 새로 고르지 않아도 되도록) "팀 관리"에서 저장해둔 팀 기본값을
        # 쓴다 — 그마저 없으면 기존처럼 "sequential"/None(자동 추정)으로
        # 떨어진다(하위 호환).
        team_defaults = teams.get_team(conn, payload.team_id)
        effective_process = (
            payload.process
            or (team_defaults["default_process"] if team_defaults else None)
            or "sequential"
        )
        effective_manager_agent_id = payload.manager_agent_id or (
            team_defaults["default_manager_agent_id"] if team_defaults else None
        )
        job_queue.enqueue(
            conn, "deliverable.team_crew",
            job["job_by_agent_id"],
            task_id,
            payload.team_id,
            user["id"],
            effective_process,
            payload.provider,
            payload.model,
            effective_manager_agent_id,
            payload.content_type,
            memory=payload.memory,
            planning=payload.planning,
            manager_provider=payload.manager_provider,
            manager_model=payload.manager_model,
            background_tasks=background_tasks,
        )
        return {"job_ids": list(job["job_by_agent_id"].values()), "status": "running"}

    @app.post("/api/tasks/{task_id}/execute-adhoc-crew", status_code=202)
    def trigger_adhoc_crew_execution(
        task_id: int,
        payload: AdhocCrewExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 업무에 바로 실행") —
        팀 사전 등록·배정·승인 없이, 이 Task의 참가자가 Agent를 즉석으로
        골라 바로 CrewAI Crew로 실행하고 결과를 이 Task의 deliverable+
        결재 체인에 연결한다(`execute-team-crew`와 같은 비동기 패턴,
        `deliverables.start_adhoc_crew_execution()` 참고)."""
        if user["role"] not in ("admin", "manager") and (
            payload.provider
            or payload.model
            or payload.planning
            or payload.manager_provider
            or payload.manager_model
        ):
            raise HTTPException(
                status_code=400,
                detail="팀원은 provider/model/매니저 LLM/planning을 직접 선택할 수 없습니다",
            )
        if len(payload.agent_ids) != len(payload.task_descriptions):
            raise HTTPException(
                status_code=400,
                detail="agent_ids와 task_descriptions의 개수가 같아야 합니다",
            )
        # 사용자 요청(2026-10-20, "독립 작업 병렬 실행") — 이 검증은
        # `crew_runtime.run_crew()` 안에서도 하지만, 거기서 걸리면
        # BackgroundTasks 안에서 조용히 'failed' job으로만 남는다(길이
        # 불일치 검증을 여기서 미리 막는 것과 같은 이유 — 사용자가 즉시
        # 알 수 있게 요청 시점에 400으로 막는다).
        if payload.parallel_agent_ids and payload.process != "sequential":
            raise HTTPException(
                status_code=400,
                detail="병렬 실행(parallel_agent_ids)은 process='sequential'에서만 지정할 수 있습니다",
            )
        # 사용자 요청(2026-11-04, "Agent 자가비평" 설계) — 위 parallel_
        # agent_ids 검증과 같은 이유로 BackgroundTasks 안에서 조용히
        # 'failed'로만 남기지 않고 요청 시점에 400으로 막는다. self_review
        # 자체는 provider/model 같은 비용 선택권이 없는 단순 토글이라
        # 위 supervisor 전용 필드 체크에는 포함하지 않는다 — member도
        # 설정할 수 있어야 한다.
        if payload.self_review and (
            payload.process != "sequential" or payload.parallel_agent_ids
        ):
            raise HTTPException(
                status_code=400,
                detail="자기 검토(self_review)는 process='sequential'이고 parallel_agent_ids가 비어 있을 때만 사용할 수 있습니다",
            )
        try:
            job = deliverables.start_adhoc_crew_execution(
                conn,
                task_id,
                user["id"],
                payload.agent_ids,
                manager_agent_id=payload.manager_agent_id,
                allow_supervisor_override=user["role"] in ("admin", "manager"),
            )
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            ) from None
        except LookupError as exc:
            if str(exc) == "agent_already_running":
                raise HTTPException(
                    status_code=409,
                    detail="이미 실행 중인 작업이 있습니다 — 완료를 기다려 주세요",
                ) from None
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            detail = {
                "no_agents_selected": "참여할 Agent를 1명 이상 선택하세요",
                "some_agents_not_active": "선택한 Agent 중 온보딩(active) 상태가 아닌 Agent가 있습니다",
                "manager_agent_not_in_agent_ids": "매니저로 지정한 Agent가 참여 Agent 목록에 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        job_queue.enqueue(
            conn, "deliverable.adhoc_crew",
            job["job_by_agent_id"],
            task_id,
            user["id"],
            payload.agent_ids,
            payload.task_descriptions,
            payload.process,
            payload.provider,
            payload.model,
            payload.manager_agent_id,
            payload.content_type,
            memory=payload.memory,
            planning=payload.planning,
            manager_provider=payload.manager_provider,
            manager_model=payload.manager_model,
            parallel_agent_ids=payload.parallel_agent_ids,
            self_review=payload.self_review,
            background_tasks=background_tasks,
        )
        return {"job_ids": list(job["job_by_agent_id"].values()), "status": "running"}

    @app.get("/api/tasks/{task_id}/suggested-agents")
    def suggest_agents_for_task_endpoint(
        task_id: int, limit: int = 5, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움" — Crew
        빌더 로드맵 5단계) — "업무에 바로 실행" 모드가 Task 선택 시
        호출해 Agent 선택 행을 자동으로 채운다. 읽기 전용·비용 없음이라
        `execute-adhoc-crew`와 같은 기준(이 Task의 참가자 또는 admin/
        manager)으로 넓게 허용한다."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return pool_registry.suggest_agents_for_task(conn, task_id, limit=limit)

    @app.get("/api/tasks/{task_id}/agent-execution-jobs")
    def list_task_agent_execution_jobs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """Phase 4 — Web UI가 폴링해 "생성 중…"/실패 상태를 협업
        워크스페이스 타임라인에 얹는다. 접근 권한은 결과물 타임라인
        조회와 동일하다."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return deliverables.list_execution_jobs(conn, task_id)

    @app.post("/api/tasks/{task_id}/execute-pipeline", status_code=202)
    def trigger_pipeline_execution(
        task_id: int,
        payload: PipelineExecuteRequest,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
        개발 Agent 팀을 구성... 각 단계별 단독 또는 복수의 에이전트가
        순차/병행 처리") — 할 일 하나에 단계형 파이프라인을 실행한다.
        `execute-team-crew`와 같은 비동기 2단계 패턴(동기로 run 행만
        만들고, 실제 단계 실행은 BackgroundTasks)."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        if payload.use_graph_engine:
            from hair_platform import GRAPH_ENGINE_INSTALL_HINT, graph_engine_available

            # 실행 행을 만들기 전에 확인한다(2026-11-23) — 만든 뒤에 import가 실패하면 'running'
            # 상태로 고아가 됐다.
            if not graph_engine_available():
                raise HTTPException(status_code=400, detail=GRAPH_ENGINE_INSTALL_HINT)
        try:
            run_id = pipeline_runtime.start_pipeline_run(
                conn, user["id"], payload.pipeline_template_id, task_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            detail = {
                "pipeline_template_not_found": "파이프라인 템플릿을 찾을 수 없습니다",
                "pipeline_template_inactive": "비활성화된 파이프라인 템플릿입니다",
                "task_not_found": "할 일을 찾을 수 없습니다",
                "pipeline_template_has_no_stages": "단계가 없는 파이프라인 템플릿입니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        if payload.use_graph_engine:
            # 사용자 요청(2026-11-18, "다음 개선 진행해줘") — LangGraph
            # 실행엔진은 아직 `single`/`sequential_relay`만 지원한다.
            # `start_pipeline_run()`이 이미 run 행을 만들어버린 뒤라
            # (여기서 막으면 run이 'running' 상태로 고아가 됨) 미리
            # 검증해 실패 시 run 자체를 바로 취소 처리한다.
            from hair_platform import execution_graph

            try:
                execution_graph.validate_template_supported(
                    conn, payload.pipeline_template_id
                )
            except ValueError as exc:
                conn.execute(
                    "UPDATE pipeline_runs SET status = 'cancelled', "
                    "finished_at = datetime('now') WHERE id = ?",
                    (run_id,),
                )
                conn.commit()
                raise HTTPException(status_code=400, detail=str(exc)) from None
            job_queue.enqueue(
                conn, "pipeline.graph",
                run_id,
                user["id"],
                background_tasks=background_tasks,
            )
        else:
            job_queue.enqueue(
                conn, "pipeline.stage",
                run_id,
                user["id"],
                background_tasks=background_tasks,
            )
        return {"run_id": run_id, "status": "running"}

    @app.get("/api/tasks/{task_id}/pipeline-runs")
    def list_task_pipeline_runs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """파이프라인 진행상황 카드가 폴링한다 — 권한은 위 Agent 실행 잡
        목록 조회와 동일하다."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return pipeline_runtime.list_pipeline_runs_for_task(conn, task_id)

    @app.post("/api/pipeline-runs/{run_id}/resume")
    def resume_pipeline_run_endpoint(
        run_id: int,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """서버 재시작으로 중단된 실행 이어서 실행(2026-11-25, P1-6) — AI를 다시 부르므로 파이프라인 실행과 같은
        권한(admin·manager). 끝난 단계는 다시 하지 않는다(기본 엔진: 진행 중이던 단계부터, 그래프: 마지막 체크포인트부터)."""
        try:
            engine = pipeline_runtime.resume_interrupted_run(conn, user["id"], run_id)
        except ValueError as exc:
            status = 404 if "찾을 수 없습니다" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        if engine == "graph":
            job_queue.enqueue(
                conn, "pipeline.resume_graph",
                run_id,
                background_tasks=background_tasks,
            )
        else:
            job_queue.enqueue(
                conn, "pipeline.stage",
                run_id,
                user["id"],
                background_tasks=background_tasks,
            )
        return {"run_id": run_id, "status": "running", "engine": engine}

    @app.post("/api/pipeline-runs/{run_id}/cancel")
    def cancel_pipeline_run_endpoint(
        run_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-11-17, "Human Agency Scale... 코파일럿...
        실행 중단 요청" → "구현 시작") — 권한은 위 파이프라인 실행
        이력 조회와 동일(참가자 또는 admin/manager)."""
        run = conn.execute(
            "SELECT task_id FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(
                conn, run["task_id"], user["id"]
            )
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            return pipeline_runtime.request_pipeline_run_cancel(
                conn, user["id"], run_id
            )
        except ValueError as exc:
            if str(exc) == "pipeline_run_already_finished":
                raise HTTPException(
                    status_code=409, detail="이미 끝난 실행입니다"
                ) from exc
            raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다") from exc

    @app.get("/api/tasks/{task_id}/crew-runs")
    def list_task_crew_runs(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행
        가시성") — 이 Task의 팀/즉석 Crew 실행 이력(진행 중인 것
        포함 — `progress` 필드로 "N명 중 M명 완료"를 보여줄 수 있다).
        권한은 위 파이프라인 실행 이력 조회와 동일하다."""
        task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다")
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, task_id, user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return crew_history.list_runs_for_task(conn, task_id)

    @app.get("/api/tasks/{task_id}/execution-permission")
    def get_execution_permission(
        task_id: int, user: sqlite3.Row = Depends(get_current_user)
    ):
        """사용자가 스크린샷으로 보고한 갭(2026-09-29, "이 업무의 협업
        워크스페이스에 접근할 권한이 없습니다") 보완 — 웹 UI가 "이 업무의
        AI 에이전트 실행" 버튼을 실제로 보여주기 전에 먼저 확인한다."""
        try:
            return deliverables.execution_eligibility(
                conn, task_id, user["id"], user["role"] in ("admin", "manager")
            )
        except ValueError:
            raise HTTPException(
                status_code=404, detail="할 일을 찾을 수 없습니다"
            ) from None

    @app.post("/api/briefs/{brief_id}/generate", status_code=201)
    def generate_agents(
        brief_id: int,
        payload: GenerateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """생성 버튼. Job Queue+샌드박스로 부족한 역할을 배치 생성한다.

        `framework="crewai"`면 실제 CrewAI Agent+Task+Crew를
        구성해 kickoff한다. `use_llm=True`+`framework` 미지정(또는
        `"simple_llm"`)이면 기존 1회성 LLM 호출이다. 둘 다 명시적으로
        요청했을 때만 실제 API를 호출했다(2026-09-26). 2026-11-22부터 framework를
        지정하지 않으면 crewai — 무료 스텁은 `framework="stub"` 명시.
        """
        brief = conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief is None:
            raise HTTPException(status_code=404, detail="브리프를 찾을 수 없습니다")
        generator = None
        # 지정이 없으면 crewai — 단, crewai가 설치되지 않은 기본 설치본이면 simple_llm(2026-11-23
        # pip 설치 점검: 기본 설치에서 아무 옵션 없이 생성하면 "No module named 'crewai'"로 실패했다).
        from hair_platform import crewai_available

        framework = payload.framework or (
            "simple_llm" if payload.use_llm or not crewai_available() else "crewai"
        )
        if framework == "crewai":
            generator = partial(
                crew_runtime.crewai_agent_generator,
                provider=payload.provider,
                model=payload.model,
            )
        elif framework == "simple_llm" or (framework != "stub" and payload.use_llm):
            generator = partial(
                jobs.llm_agent_generator, provider=payload.provider, model=payload.model
            )
        return jobs.create_and_run_job(
            conn, brief_id, payload.task_ids, user["id"], generator=generator
        )

    @app.post("/api/jobs/{job_id}/items/{item_id}/retry")
    def retry_job_item(
        job_id: int,
        item_id: int,
        use_llm: bool = False,
        provider: str | None = None,
        model: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """실패 역할만 재시도."""
        generator = None
        if use_llm:
            generator = partial(
                jobs.llm_agent_generator, provider=provider, model=model
            )
        try:
            return jobs.retry_failed_item(
                conn, job_id, item_id, user["id"], generator=generator
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/assignments/{assignment_id}/team-leader")
    def update_team_leader_endpoint(
        assignment_id: int,
        payload: TeamLeaderUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 결정(2026-10-06, "팀 리더는 배정 요청자가 기본값,
        변경 가능") — 팀 배정이 아직 `requested` 상태일 때만 바꿀 수
        있다(`assignments.update_team_leader()` 참고)."""
        try:
            assignments.update_team_leader(
                conn, user["id"], assignment_id, payload.team_leader_id
            )
        except ValueError as exc:
            detail = {
                "assignment_not_found": "배정을 찾을 수 없습니다",
                "not_a_team_assignment": "팀 배정이 아닙니다",
                "team_leader_only_changeable_while_requested": "결재 대기 중(requested)일 때만 팀 리더를 바꿀 수 있습니다",
                "user_not_found": "사용자를 찾을 수 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": assignment_id, "team_leader_id": payload.team_leader_id}

    @app.post("/api/assignments/{assignment_id}/cancel")
    def cancel_assignment_endpoint(
        assignment_id: int,
        payload: AssignmentCancelRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 보고(2026-10-19, "업무 요청 삭제가 확정된 배정 때문에
        막히는데 정리할 방법이 없다") — 결재 체인 없이(이미 확정됐거나,
        아직 결재 중인 배정 자체를 되돌리는 것뿐이라 새 승인이 필요
        없다) 즉시 취소한다. `assignments.cancel_assignment()` 참고."""
        try:
            result = assignments.cancel_assignment(
                conn, user["id"], assignment_id, payload.reason
            )
        except ValueError as exc:
            detail = {
                "assignment_not_found": "배정을 찾을 수 없습니다",
                "assignment_not_cancellable": "이미 종료된 배정(반려/대체/취소됨)은 다시 취소할 수 없습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return result
