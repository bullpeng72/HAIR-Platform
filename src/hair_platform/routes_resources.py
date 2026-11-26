"""리소스 API(AI 에이전트·사람 인력·도구/스킬 Pool·팀·Crew 실행·파이프라인 템플릿) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
)
from fastapi.responses import PlainTextResponse

from hair_platform import job_queue  # 백그라운드 작업 큐(2026-11-26, P1-6 2단계)
from hair_platform import (
    agent_evaluation,
    agent_optimizer,
    agent_runtime,
    audit,
    context_policy,
    cost,
    crew_history,
    crew_runtime,
    data_admin,
    deliverables,
    language_qa,
    performance,
    pipeline_runtime,
    pool_registry,
    skill_markdown,
    teams,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    AgentContextPolicyUpdate,
    AgentCreateDirect,
    AgentExecuteRequest,
    AgentModelConfig,
    AgentNameUpdate,
    AgentPersonaConfig,
    AgentTuningConfig,
    ConsentRequest,
    CostUpdateRequest,
    CrewRunRequest,
    HumanPoolCreate,
    HumanPoolUpdate,
    PerformanceScoreRequest,
    PipelineStageCreate,
    PipelineStageReorder,
    PipelineStageUpdate,
    PipelineTemplateActiveUpdate,
    PipelineTemplateCreate,
    SkillMarkdownImport,
    SkillPoolCreate,
    SkillPoolUpdate,
    TeamActiveUpdate,
    TeamCreate,
    TeamDefaultsUpdate,
    TeamMemberAdd,
    TeamMemberReorder,
    TeamResetProtectionUpdate,
    ToolActiveUpdate,
    ToolPoolCreate,
    ToolPoolUpdate,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """리소스 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.post("/api/human-pool", status_code=201)
    def create_human_pool_entry(
        payload: HumanPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        if payload.user_id is not None:
            target_user = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.user_id,)
            ).fetchone()
            if target_user is None:
                raise HTTPException(
                    status_code=404, detail="연결할 사용자를 찾을 수 없습니다"
                )
            already_linked = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ?", (payload.user_id,)
            ).fetchone()
            if already_linked is not None:
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 사람 인력(id="
                    f"{already_linked['id']})에 연결된 계정입니다",
                )
        try:
            cur = conn.execute(
                "INSERT INTO human_pool "
                "(name, role, skill_tags, availability, cost_estimate, manager_id, "
                "user_id, employee_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.name,
                    payload.role,
                    json.dumps(payload.skill_tags, ensure_ascii=False),
                    payload.availability,
                    payload.cost_estimate,
                    payload.manager_id,
                    payload.user_id,
                    payload.employee_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"이미 등록된 사번입니다: {payload.employee_id}",
            ) from exc
        conn.commit()
        return {"id": cur.lastrowid, "name": payload.name}

    @app.get("/api/human-pool")
    def list_human_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Web UI가 Human Pool 관리 화면(수정/오프보딩/동의)에서 원본 레코드가
        필요하다 — `/api/pool/search`는 공통 ResourceCandidate 어댑터라 availability
        원문·manager_id·performance_consent가 빠져 있어 이 목적에 못 쓴다."""
        rows = conn.execute("SELECT * FROM human_pool ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                # 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번
                # 적용 예정으로 8자리~12자리 입력 및 표시 가능해야 함")
                "employee_id": row["employee_id"],
                "name": row["name"],
                "role": row["role"],
                "skill_tags": json.loads(row["skill_tags"]),
                "availability": row["availability"],
                "cost_estimate": row["cost_estimate"],
                "manager_id": row["manager_id"],
                "user_id": row["user_id"],
                "performance_consent": bool(row["performance_consent"]),
                "performance_score": row["performance_score"],
            }
            for row in rows
        ]

    @app.patch("/api/human-pool/{human_id}")
    def update_human_pool_entry(
        human_id: int,
        payload: HumanPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """코드 재점검(R3)에서 발견한 비대칭 보완 — Agent Pool엔 onboard/deprecate가
        있는데 Human Pool은 생성 후 수정할 방법이 전혀 없었다."""
        row = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="사람을 찾을 수 없습니다")

        # 사용자 결정(2026-09-29, "(b) Human Pool도 admin 전용으로
        # 좁히기") — Agent Pool의
        # cost_estimate/performance_score는 이미 admin 전용 전용
        # 엔드포인트로만 수정 가능한데, Human Pool은 이 일반
        # PATCH로 매니저도 자유롭게 고칠 수 있어 비대칭이었다. 다른
        # 필드(역할·스킬·가용성·상사 지정 등)는 매니저의 정상 인사
        # 업무라 그대로 두고, cost_estimate만 admin 전용으로 좁힌다.
        if payload.cost_estimate is not None and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="Human Pool 비용(cost_estimate) 수정은 admin 전용입니다",
            )

        updates: dict[str, object] = {}
        if payload.role is not None:
            updates["role"] = payload.role
        if payload.skill_tags is not None:
            updates["skill_tags"] = json.dumps(payload.skill_tags, ensure_ascii=False)
        if payload.availability is not None:
            updates["availability"] = payload.availability
        if payload.cost_estimate is not None:
            updates["cost_estimate"] = payload.cost_estimate
        if payload.user_id is not None:
            target_user = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.user_id,)
            ).fetchone()
            if target_user is None:
                raise HTTPException(
                    status_code=404, detail="연결할 사용자를 찾을 수 없습니다"
                )
            # 사용자 보고(2026-10-06)로 추가 — 두 Human Pool 레코드가 같은
            # 계정에 연결되면 `WHERE user_id = ?` 기반 조회(`/api/my-work`
            # 등)가 둘 중 하나만 조용히 골라 다른 쪽은 영원히 안 보이게
            # 된다. 자기 자신에게 다시 연결하는 건 허용(멱등).
            already_linked = conn.execute(
                "SELECT id FROM human_pool WHERE user_id = ? AND id != ?",
                (payload.user_id, human_id),
            ).fetchone()
            if already_linked is not None:
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 사람 인력(id="
                    f"{already_linked['id']})에 연결된 계정입니다",
                )
            updates["user_id"] = payload.user_id
        if payload.manager_id is not None:
            manager = conn.execute(
                "SELECT id FROM users WHERE id = ?", (payload.manager_id,)
            ).fetchone()
            if manager is None:
                raise HTTPException(status_code=404, detail="매니저를 찾을 수 없습니다")
            updates["manager_id"] = payload.manager_id
        if payload.employee_id is not None:
            updates["employee_id"] = payload.employee_id
        if not updates:
            raise HTTPException(status_code=400, detail="수정할 필드가 없습니다")

        set_clause = ", ".join(f"{key} = ?" for key in updates)
        try:
            conn.execute(
                f"UPDATE human_pool SET {set_clause} WHERE id = ?",
                (*updates.values(), human_id),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"이미 등록된 사번입니다: {payload.employee_id}",
            ) from exc
        audit.record(
            conn,
            user["id"],
            "human_pool_updated",
            "human_pool",
            human_id,
            before={k: row[k] for k in updates},
            after=updates,
        )
        conn.commit()
        return {"id": human_id, **updates}

    @app.post("/api/human-pool/{human_id}/offboard")
    def offboard_human(
        human_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """코드 재점검(R3)에서 발견한 누락 보완 — 퇴사한 사람을 검색/매칭에서
        완전히 배제할 방법이 없었다(Agent의 `/deprecate`에 대응)."""
        row = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="사람을 찾을 수 없습니다")
        conn.execute(
            "UPDATE human_pool SET availability = 'offboarded' WHERE id = ?",
            (human_id,),
        )
        audit.record(
            conn,
            user["id"],
            "human_offboarded",
            "human_pool",
            human_id,
            before={"availability": row["availability"]},
            after={"availability": "offboarded"},
        )
        conn.commit()
        return {"id": human_id, "availability": "offboarded"}

    @app.delete("/api/human-pool/{human_id}")
    def delete_human_endpoint(
        human_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28) — 사람 인력 레코드를 완전히 지운다
        (오프보딩은 상태만 바꿀 뿐 지우지는 않는다 — 이건 그 위의 진짜
        삭제). `confirmed` 배정이 있으면 막는다."""
        try:
            data_admin.delete_human(conn, user["id"], human_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="사람을 찾을 수 없습니다"
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
        return {"id": human_id, "deleted": True}

    @app.post("/api/agent-pool", status_code=201)
    def create_agent_direct(
        payload: AgentCreateDirect,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
        참조) — Brief/Task 분해 없이 역할명·goal·backstory·
        expected_output·skill_tags를 한 번에 입력해 즉시 Agent를
        등록한다("AI 에이전트 생성" 탭의 두 번째 모드). 기존 Task 기반
        생성과 동일하게 `pending_onboarding`으로 시작 — 온보딩 승인
        (`POST /api/agent-pool/{id}/onboard`)은 그대로 admin 전용."""
        try:
            agent_id = agent_runtime.create_agent_directly(
                conn,
                user["id"],
                payload.role_name,
                payload.skill_tags,
                goal=payload.goal,
                backstory=payload.backstory,
                expected_output=payload.expected_output,
            )
        except ValueError as exc:
            detail = {
                "empty_role_name": "역할명을 입력하세요",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {
            "id": agent_id,
            "role_name": payload.role_name,
            "status": "pending_onboarding",
        }

    @app.get("/api/agent-pool")
    def list_agent_pool(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Web UI가 온보딩/오프보딩 화면에서 status 무관 전체 목록을 봐야
        한다 — 검색(`/api/pool/search`)은 `status='active'`만 노출해서
        온보딩 대기(pending_onboarding) 항목을 찾을 방법이 없었다.

        **실측으로 발견한 갭(2026-09-28, "Phase1~4까지... 분석하고
        개선해줘" 리뷰 중)**: Phase 3가 만든 `agent_evaluation.
        aggregate_agent_score()`("자동값과 수동값을 구분해서
        보여준다")가 실제로는 어떤 API/화면에도 연결돼 있지 않아 테스트
        말고는 아무도 못 보고 있었다 — `auto_evaluation`으로 여기 얹어서
        `performance_score`(관리자 수동)와 나란히 노출한다."""
        rows = conn.execute("SELECT * FROM agent_pool ORDER BY id DESC").fetchall()
        return [
            {
                "id": row["id"],
                # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를
                # 수립하고(종류 구분 가능) 초기화하여 정리") — "AGT-REF-
                # 001"(레퍼런스 22종)/"AGT-CUSTOM-001"(관리자 생성) 형식
                # 표시용 코드(`id_scheme.py`). id는 FK·URL 경로가 그대로
                # 쓰므로 안 바뀐다.
                "code": row["code"],
                "role_name": row["role_name"],
                # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                # 선택 입력이라 비어 있을 수 있다(비어 있으면 화면이
                # role_name으로 폴백한다).
                "name": row["name"],
                "skill_tags": json.loads(row["skill_tags"]),
                "cost_estimate": row["cost_estimate"],
                "performance_score": row["performance_score"],
                "auto_evaluation": agent_evaluation.aggregate_agent_score(
                    conn, row["id"]
                ),
                "status": row["status"],
                "llm_provider": row["llm_provider"],
                "llm_model": row["llm_model"],
                "last_output": row["last_output"],
                "framework": row["framework"],
                "mcp_servers": json.loads(row["mcp_servers"]),
                # 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해
                # Finetuning") — 지정 안 한 값은 전부 키 자체가 없다(={}),
                # 화면이 그걸 "CrewAI 기본값 사용 중"으로 보여준다.
                "tuning_config": json.loads(row["tuning_config"]),
                "linked_tool_ids": pool_registry.list_agent_tool_ids(conn, row["id"]),
                # 사용자 요청(2026-10-07, "AI 에이전트 관리 노출 검토") —
                # 이 Agent가 어느 팀 소속인지 여기서 바로 보여준다(팀은
                # "팀 관리" 탭에서 정의하지만, 관리 화면에도 연결 관계가
                # 보여야 "복수 에이전트 그룹"이 업무와 어떻게 이어지는지
                # 알 수 있다).
                "team_names": teams.team_names_for_agent(conn, row["id"]),
                # 대화 컨텍스트 정책(2026-11-20) — NULL이면 스킬에서 계산.
                "history_mode": row["history_mode"],
                "use_attachments": None
                if row["use_attachments"] is None
                else bool(row["use_attachments"]),
                "effective_context_policy": context_policy.for_agent(conn, row["id"]),
                # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 저장된
                # 오버라이드(없으면 빈 문자열/None)와, 실제 Crew 실행에
                # "지금 적용되는" 값을 구분해 둘 다 보여준다 — 후자는
                # `crew_runtime.py`의 자동 생성 로직을 그대로 호출해
                # 미리보기로 쓴다(화면이 그 로직을 중복 구현하지 않도록).
                "goal": row["goal"],
                "backstory": row["backstory"],
                "expected_output": row["expected_output"],
                "effective_goal": row["goal"]
                or crew_runtime._agent_goal(
                    row["role_name"], json.loads(row["skill_tags"])
                ),
                "effective_backstory": row["backstory"]
                or crew_runtime._agent_backstory(
                    conn, row["role_name"], json.loads(row["skill_tags"])
                ),
                "effective_expected_output": row["expected_output"]
                or crew_runtime.DEFAULT_EXPECTED_OUTPUT,
            }
            for row in rows
        ]

    @app.post("/api/agent-pool/{agent_id}/execute")
    def execute_agent_endpoint(
        agent_id: int,
        payload: AgentExecuteRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """온보딩된 Agent에게 실제 업무를 맡긴다(사용자 요청, 2026-09-26) —
        호출하면 실제 Anthropic/OpenAI API가 불려 비용이 발생한다."""
        retrieval_sink: dict = {}
        try:
            output = agent_runtime.execute_agent(
                conn,
                agent_id,
                user["id"],
                payload.prompt,
                payload.provider,
                payload.model,
                retrieval_sink=retrieval_sink,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409, detail="온보딩(active)된 Agent만 실행할 수 있습니다"
            ) from exc
        # 언어 품질 검사(2026-11-22 점검 — 대화·산출물 경로와 달리 이 직접 실행 API만
        # 빠져 있었다, `language_qa.py`).
        output = language_qa.repair_output(
            conn,
            output,
            retrieval_sink.get("llm_provider"),
            retrieval_sink.get("llm_model"),
            user["id"],
            "agent_execute_api",
        )
        return {
            "id": agent_id,
            "output": output,
            "retrieved_context": retrieval_sink.get("retrieved_context"),
        }

    @app.patch("/api/agent-pool/{agent_id}/model")
    def set_agent_model_config(
        agent_id: int,
        payload: AgentModelConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Agent별 기본 LLM provider/model(+ MCP 서버 설정)을 저장한다(사용자
        요청, 2026-09-26, 2차 — Anthropic/OpenAI/Ollama 중 선택; MCP는
        2026-09-27 추가). 비용이 발생하지 않는다 — LLM/MCP를 호출하지 않고
        설정만 저장한다. 이후 `/execute`가 provider/model을 안 받으면 여기서
        저장한 값을 기본으로 쓰고, `POST /api/crew/run`이 여기서 저장한
        `mcp_servers`로 그 Agent에 실제 MCP 도구를 붙인다."""
        try:
            resolved_provider, resolved_model = agent_runtime.set_model_config(
                conn,
                agent_id,
                user["id"],
                payload.provider,
                payload.model,
                mcp_servers=(
                    [s.model_dump() for s in payload.mcp_servers]
                    if payload.mcp_servers is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        return {
            "id": agent_id,
            "llm_provider": resolved_provider,
            "llm_model": resolved_model,
        }

    @app.patch("/api/agent-pool/{agent_id}/tuning")
    def set_agent_tuning_config(
        agent_id: int,
        payload: AgentTuningConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해
        Finetuning 하는 기능") — CrewAI Agent/LLM 동작 파라미터 저장.
        `set_agent_model_config`와 같은 권한(admin+manager, 비용 없는
        설정 전용 엔드포인트)."""
        try:
            saved = agent_runtime.set_tuning_config(
                conn, agent_id, user["id"], payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc
        return {"id": agent_id, "tuning_config": saved}

    @app.patch("/api/agent-pool/{agent_id}/persona")
    def set_agent_persona(
        agent_id: int,
        payload: AgentPersonaConfig,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "CrewAI 에이전트... goal, backstory...
        고급 에이전트 관리 기능") — `set_agent_tuning_config`와 같은
        권한(admin+manager, 비용 없는 설정 전용 엔드포인트).
        `agent_runtime.set_persona()` 참고."""
        try:
            saved = agent_runtime.set_persona(
                conn, agent_id, user["id"], **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "agent_not_found":
                raise HTTPException(
                    status_code=404, detail="Agent를 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": agent_id, **saved}

    @app.patch("/api/agent-pool/{agent_id}/context-policy")
    def set_agent_context_policy(
        agent_id: int,
        payload: AgentContextPolicyUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """대화 컨텍스트 정책(2026-11-20) — 이 Agent를 `@멘션`했을 때의
        이전 대화/첨부 사용 덮어쓰기. `null`이면 스킬에서 계산(가장
        포괄적인 값)으로 되돌린다. 권한은 persona/tuning과 같다."""
        row = conn.execute("SELECT id FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        conn.execute(
            "UPDATE agent_pool SET history_mode = ?, use_attachments = ? WHERE id = ?",
            (
                payload.history_mode,
                None if payload.use_attachments is None else int(payload.use_attachments),
                agent_id,
            ),
        )
        audit.record(
            conn,
            user["id"],
            "agent_context_policy_updated",
            "agent_pool",
            agent_id,
            after=payload.model_dump(),
        )
        conn.commit()
        return {
            "id": agent_id,
            "history_mode": payload.history_mode,
            "use_attachments": payload.use_attachments,
            "effective_context_policy": context_policy.for_agent(conn, agent_id),
        }

    @app.get("/api/agent-pool/{agent_id}/optimize-recommendation")
    def get_agent_optimize_recommendation(
        agent_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-18, "Agent의 목표, 기능, 입출력 등을 고려해
        가용한 LLM 모델에서 종류 및 설정 등을 최적화 setting 하는 admin
        기능") — 규칙 기반 1차 추천(비용 없음, `agent_optimizer.py`).
        저장은 하지 않는다 — admin이 검토 후 기존 모델/파인튜닝 저장
        버튼으로 직접 적용한다."""
        try:
            return agent_optimizer.recommend_for_agent(conn, agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc

    @app.post("/api/agent-pool/{agent_id}/optimize-recommendation/refine")
    def refine_agent_optimize_recommendation(
        agent_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """규칙 기반 추천을 LLM에게 검토·보정받는다 — **실제 LLM API를
        호출해 비용이 발생한다**(명시적으로 이 엔드포인트를 호출했을 때만).
        LLM이 레지스트리에 없는 모델을 환각하거나 잘못된 JSON을 내면
        규칙 기반 추천으로 안전하게 되돌아간다(`source` 필드로 구분)."""
        try:
            return agent_optimizer.refine_with_llm(conn, agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
            ) from exc

    @app.get("/api/agent-pool-optimization/recommendations")
    def list_agent_optimize_recommendations(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """ "LLM 설정 최적화" 일괄 화면용 — 전체 Agent(deprecated 제외)의
        규칙 기반 추천을 한 번에 반환한다(비용 없음).

        사용자 요청(2026-11-02, "모델 관리 메뉴 영역은 관리자(admin)
        전용으로 변경") — "AI 에이전트 관리" 화면의 Agent별 "⚡ 최적화
        추천"(`GET /api/agent-pool/{id}/optimize-recommendation`)은
        manager도 그대로 쓸 수 있다(다른 엔드포인트, 영향 없음) — 이
        엔드포인트는 "LLM 설정 최적화" 일괄 화면 전용이라 그 화면만
        admin 전용으로 좁힌다."""
        return agent_optimizer.recommend_all(conn)

    @app.get("/api/tool-pool")
    def list_tool_pool(
        category: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """재사용 가능한 도구(MCP 서버 또는 native Tool)
        레지스트리 조회. `category`(사용자 요청, 2026-11-11, "스킬과
        도구를 SW개발업무와 일반업무로 구분하고 필터링 되도록 개선")를
        주면 `'sw_dev'`/`'general'` 중 하나로만 좁힌다."""
        try:
            return pool_registry.list_tools(conn, category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tool-pool", status_code=201)
    def create_tool_pool_entry(
        payload: ToolPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        # 사용자 결정(2026-09-29, "(a) MCP 종류만 admin 전용으로
        # 좁히기") — mcp_stdio/mcp_remote는
        # 등록되는 순간이 아니라 "그 스킬을 가진 Agent가 실행될 때" 조직
        # 전체 Agent가 실제 외부 프로세스/네트워크에 접속하게 만드는
        # 조직 차원 정책이라(GitHub Copilot 원칙 1), native(웹서치 도구
        # 등)와 달리 admin 전용으로 좁힌다. 엔드포인트 전체를 admin
        # 전용으로 만들지 않고 이 두 kind만 막는 이유는 native 도구
        # 등록(운영 업무 성격)까지 매니저에게서 뺏을 이유가 없어서다.
        #
        # 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는
        # 기능") — `custom_python`은 등록된 Python 소스가 실행되는 순간
        # 서버 프로세스와 같은 신뢰 수준으로 동작한다(`pool_registry.
        # _run_custom_python_tool()` 참고) — mcp_stdio와 같은 등급의
        # 위험이라 같은 이유로 admin 전용으로 묶는다.
        if (
            payload.kind in ("mcp_stdio", "mcp_remote", "custom_python")
            and user["role"] != Role.ADMIN.value
        ):
            raise HTTPException(
                status_code=403,
                detail="MCP·커스텀 Python 도구(mcp_stdio/mcp_remote/custom_python) 등록은 admin 전용입니다",
            )
        try:
            tool_id = pool_registry.create_tool(
                conn,
                user["id"],
                payload.name,
                payload.description,
                payload.kind,
                payload.config,
                payload.tool_names,
                payload.category,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"이미 있는 이름입니다: {payload.name}"
            ) from exc
        return {"id": tool_id}

    @app.patch("/api/tool-pool/{tool_id}")
    def update_tool_pool_entry(
        tool_id: int,
        payload: ToolPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을
        추가해줘") — 지금까지 등록 뒤엔 삭제·재등록만 가능했다. `kind`·
        `name`은 바꿀 수 없다(`pool_registry.update_tool()` 참고 — name은
        Agent/Skill 연결이 참조하는 문자열이라 바꾸면 그 연결이 조용히
        끊어진다). 등록(POST)과 같은 이유로 mcp_stdio/mcp_remote/
        custom_python 종류는 admin 전용으로 좁힌다 — 수정 경로로 그
        제한을 우회할 수 없어야 한다."""
        row = conn.execute(
            "SELECT kind FROM tool_pool WHERE id = ?", (tool_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다")
        if (
            row["kind"] in ("mcp_stdio", "mcp_remote", "custom_python")
            and user["role"] != Role.ADMIN.value
        ):
            raise HTTPException(
                status_code=403,
                detail="MCP·커스텀 Python 도구(mcp_stdio/mcp_remote/custom_python) 수정은 admin 전용입니다",
            )
        try:
            pool_registry.update_tool(
                conn, user["id"], tool_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            detail = {"tool_not_found": "도구를 찾을 수 없습니다"}.get(
                str(exc), str(exc)
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": tool_id}

    @app.patch("/api/tool-pool/{tool_id}/active")
    def set_tool_pool_active(
        tool_id: int,
        payload: ToolActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pool_registry.set_tool_active(conn, user["id"], tool_id, payload.is_active)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="도구를 찾을 수 없습니다"
            ) from exc
        return {"id": tool_id, "is_active": payload.is_active}

    @app.delete("/api/tool-pool/{tool_id}")
    def delete_tool_pool_entry(
        tool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — Tool Pool 항목을 완전히 지운다(비활성화가
        아니라 진짜 삭제). 참조하던 `agent_tool_links`도 같이 지운다."""
        try:
            pool_registry.delete_tool(conn, user["id"], tool_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="도구를 찾을 수 없습니다"
            ) from exc
        return {"id": tool_id, "deleted": True}

    @app.get("/api/skill-pool")
    def list_skill_pool(
        category: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """`skill_tags`를 정식 엔티티로 승격한 Skill Pool
        조회. `augmentation_mode="tool"`인 스킬은 `default_tool_pool_ids`가
        가리키는 도구를 이 스킬을 가진 모든 Agent에 자동으로 붙인다.
        `category`(사용자 요청, 2026-11-11, "스킬과 도구를 SW개발업무와
        일반업무로 구분하고 필터링 되도록 개선")를 주면 `'sw_dev'`/
        `'general'` 중 하나로만 좁힌다."""
        try:
            return pool_registry.list_skills(conn, category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/skill-pool/names")
    def list_skill_pool_names(
        conversation: bool = False,
        include_dev: bool = False,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """Phase 5 — Skill 자동완성(사용자 요청, "협업 워크스페이스 UX
        개선"). `/api/skill-pool`은 도구 연결 상세(augmentation_mode 등)
        까지 담아 Admin/매니저 전용이지만, 스킬 "이름"만 필요한 자동완성은
        `required_skills`/`skill_tags`를 입력하는 모든 role(팀원 포함)이
        써야 한다 — 이름만 떼어 노출하는 별도 엔드포인트를 둔다.

        `conversation=true`(사용자 요청, 2026-10-22, "skill에 대화창
        표시 속성 추가, 가능한 것들만 표시되도록 개선") — 대화창 스킬
        드롭다운(`conv-skill-select`)만 `show_in_conversation=0`인
        스킬(mcp_remote/mcp_stdio/custom_python 도구가 붙어 대화창
        단발 호출 경로에서 구조적으로 실행이 안 되는 것들)을 뺀다.
        Agent/Task의 `skill_tags` 자동완성(`skill-datalist`)은 이
        파라미터 없이 그대로 호출해 전체 목록을 계속 받는다 — 그
        경로(CrewAI 실행)는 이 스킬들도 실제로 동작하기 때문이다.

        `include_dev=true`(사용자 요청, 2026-11-11, "개발업무 Skill의
        경우 Default로 대화창에 보이지 않도록 설정하고, 필요시
        노출하도록" — 한 번 "예외 없이 항상 제외"로 단순화했다가, 이
        요청으로 "기본은 숨김, 필요하면 노출"로 되돌렸다) —
        `conversation=true`일 때만 의미가 있다: 기본값은 `category=
        'general'`만 보여주고, 이 플래그를 켜야 `'sw_dev'` 스킬도 함께
        보인다(완전히 숨기는 게 아니라 default-off). `conversation=false`
        (Agent/Task 자동완성)는 이 플래그와 무관하게 항상 전체 목록이다."""
        skills = pool_registry.list_skills(conn)
        if conversation:
            skills = [s for s in skills if s["show_in_conversation"]]
            if not include_dev:
                skills = [s for s in skills if s["category"] != "sw_dev"]
        return [s["name"] for s in skills]

    @app.get("/api/skill-pool/context-policies")
    def list_skill_context_policies(
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """대화 컨텍스트 정책(2026-11-20) — 대화 입력창의 "이전 대화" 칩이
        스킬을 고를 때 그 스킬 기본값을 보여주는 데 쓴다. `names`
        엔드포인트처럼 모든 role에 이름 수준 정보만 노출한다."""
        return {
            s["name"]: {"history": s["history_mode"], "attachments": s["use_attachments"]}
            for s in pool_registry.list_skills(conn)
        }

    @app.post("/api/skill-pool", status_code=201)
    def create_skill_pool_entry(
        payload: SkillPoolCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            skill_id = pool_registry.create_skill(
                conn,
                user["id"],
                payload.name,
                payload.description,
                payload.default_tool_pool_ids,
                payload.augmentation_mode,
                payload.show_in_conversation,
                payload.category,
                payload.output_format,
                payload.examples,
                payload.constraints,
                payload.includes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"이미 있는 이름입니다: {payload.name}"
            ) from exc
        return {"id": skill_id}

    @app.patch("/api/skill-pool/{skill_id}")
    def update_skill_pool_entry(
        skill_id: int,
        payload: SkillPoolUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을
        추가해줘") — `name`은 바꿀 수 없다(`pool_registry.update_skill()`
        참고)."""
        try:
            pool_registry.update_skill(
                conn, user["id"], skill_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "skill_not_found":
                raise HTTPException(
                    status_code=404, detail="스킬을 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": skill_id}

    @app.get("/api/skill-pool/{skill_id}/export")
    def export_skill_pool_entry(
        skill_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-11-17, "마크다운 규격으로 export") — 이
        스킬을 frontmatter + 섹션 규격 마크다운 원문으로 내려준다."""
        skills = pool_registry.list_skills(conn)
        skill = next((s for s in skills if s["id"] == skill_id), None)
        if skill is None:
            raise HTTPException(status_code=404, detail="스킬을 찾을 수 없습니다")
        markdown_text = skill_markdown.export_skill_markdown(conn, skill)
        # 한글 스킬 이름은 Content-Disposition 헤더에 latin-1로 인코딩할
        # 수 없으므로(실측: UnicodeEncodeError) RFC 5987 filename*로
        # UTF-8 퍼센트 인코딩한다 — filename(ASCII 폴백)은 코드로.
        encoded_name = urllib.parse.quote(f"{skill['name']}.md")
        return PlainTextResponse(
            markdown_text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{skill["code"]}.md"; '
                    f"filename*=UTF-8''{encoded_name}"
                )
            },
        )

    @app.post("/api/skill-pool/import", status_code=201)
    def import_skill_pool_entry(
        payload: SkillMarkdownImport,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-11-17, "외부에서 마크다운 포멧의 Skill을
        import") — `export_skill_pool_entry()`가 내려주는 것과 같은
        규격의 마크다운을 받아 새 Skill Pool 항목으로 등록한다."""
        try:
            fields = skill_markdown.parse_skill_markdown(conn, payload.markdown)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"마크다운 형식을 해석할 수 없습니다: {exc}"
            ) from exc
        try:
            skill_id = pool_registry.create_skill(conn, user["id"], **fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"이미 있는 이름입니다: {fields['name']}"
            ) from exc
        return {"id": skill_id}

    @app.delete("/api/skill-pool/{skill_id}")
    def delete_skill_pool_entry(
        skill_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-09-28) — Skill Pool 항목을 완전히 지운다.
        같은 skill_tags를 가진 Agent/사람은 그대로 남고, 다음 실행부터
        이 스킬 기반 자동 도구 연결만 사라진다."""
        try:
            pool_registry.delete_skill(conn, user["id"], skill_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="스킬을 찾을 수 없습니다"
            ) from exc
        return {"id": skill_id, "deleted": True}

    @app.get("/api/teams")
    def list_teams_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return teams.list_teams(conn)

    @app.get("/api/teams/{team_id}")
    def get_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        team = teams.get_team(conn, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다")
        return team

    @app.post("/api/teams", status_code=201)
    def create_team_endpoint(
        payload: TeamCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            team_id = teams.create_team(
                conn,
                user["id"],
                payload.name,
                payload.description,
                task_id=payload.task_id,
                brief_id=payload.brief_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": team_id, "name": payload.name}

    @app.post("/api/teams/{team_id}/onboard")
    def onboard_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-29, "Team 온보딩 라이프사이클") —
        `pending_onboarding` 상태의 TF팀을 실제로 @멘션/배정 후보에
        노출시킨다(Buzz 비교 분석의 "명시적 권한 부여" 원칙)."""
        try:
            return teams.onboard_team(conn, user["id"], team_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc

    @app.post("/api/teams/{team_id}/deprecate")
    def deprecate_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            return teams.deprecate_team(conn, user["id"], team_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc

    @app.post("/api/teams/{team_id}/complete-mission")
    def complete_team_mission_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            return teams.complete_team_mission(conn, user["id"], team_id)
        except ValueError as exc:
            if str(exc) == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from exc
            detail = {
                "team_not_active": "활성 상태인 팀만 미션 완료로 표시할 수 있습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.patch("/api/teams/{team_id}/active")
    def set_team_active_endpoint(
        team_id: int,
        payload: TeamActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            teams.set_team_active(conn, user["id"], team_id, payload.is_active)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc
        return {"id": team_id, "is_active": payload.is_active}

    @app.patch("/api/teams/{team_id}/reset-protection")
    def set_team_reset_protection_endpoint(
        team_id: int,
        payload: TeamResetProtectionUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """팀별 "공장 초기화 시 보호"(2026-11-21) — 초기화는 admin 전용 작업이라 켜고 끄는
        것도 admin만 한다."""
        try:
            teams.set_team_reset_protection(conn, user["id"], team_id, payload.protect_on_reset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다") from exc
        return {"id": team_id, "protect_on_reset": payload.protect_on_reset}

    @app.patch("/api/teams/{team_id}")
    def update_team_defaults_endpoint(
        team_id: int,
        payload: TeamDefaultsUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "고급 에이전트 관리") — 이 팀의
        기본 process/매니저를 저장한다. `teams.update_team_defaults()`
        참고."""
        try:
            saved = teams.update_team_defaults(
                conn, user["id"], team_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            if str(exc) == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from exc
            detail = {
                "manager_must_be_active_agent_member_of_team": "지정한 매니저가 이 팀의 온보딩(active)된 Agent 멤버가 아닙니다",
                "pipeline_template_not_found": "파이프라인 템플릿을 찾을 수 없습니다",
                "pipeline_template_inactive": "비활성화된 파이프라인 템플릿입니다",
                "use_graph_engine_requires_pipeline_template": "신규 실행엔진을 켜려면 먼저 파이프라인 템플릿을 연결해야 합니다",
                "hierarchical_mode_blocked_for_new_teams": "신규로 hierarchical 모드를 지정할 수 없습니다 — CrewAI hierarchical 모드의 신뢰성 문제로 더는 새로 전환할 수 없게 막아뒀습니다. sequential_relay 기반 파이프라인(가능하면 신규 실행엔진)을 사용하세요. 이미 hierarchical인 팀은 계속 쓸 수 있습니다.",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"id": team_id, **saved}

    @app.delete("/api/teams/{team_id}")
    def delete_team_endpoint(
        team_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            teams.delete_team(conn, user["id"], team_id)
        except ValueError as exc:
            if str(exc) == "confirmed_assignment_exists":
                raise HTTPException(
                    status_code=409, detail="확정된 배정이 있어 삭제할 수 없습니다"
                ) from exc
            raise HTTPException(
                status_code=404, detail="팀을 찾을 수 없습니다"
            ) from exc
        return {"id": team_id, "deleted": True}

    @app.post("/api/teams/{team_id}/members", status_code=201)
    def add_team_member_endpoint(
        team_id: int,
        payload: TeamMemberAdd,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            member_row_id = teams.add_team_member(
                conn,
                user["id"],
                team_id,
                payload.member_type,
                payload.member_id,
                payload.role_label,
            )
        except ValueError as exc:
            if str(exc) == "manager_role_label_blocked_for_new_teams":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        '역할 라벨에 "매니저"가 들어간 Agent는 위임형(hierarchical) 매니저로 '
                        "인식되는데, 위임형은 신뢰성 문제로 새 팀에 지정할 수 없습니다 — "
                        '"리드"·"PM"처럼 다른 라벨을 쓰세요(이미 위임형으로 쓰던 팀은 그대로 가능).'
                    ),
                ) from exc
            detail = (
                "팀을 찾을 수 없습니다"
                if str(exc) == "team_not_found"
                else "멤버를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="이미 이 팀의 멤버입니다"
            ) from exc
        return {"id": member_row_id}

    @app.delete("/api/teams/{team_id}/members/{member_row_id}")
    def remove_team_member_endpoint(
        team_id: int,
        member_row_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            teams.remove_team_member(conn, user["id"], team_id, member_row_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="팀 멤버를 찾을 수 없습니다"
            ) from exc
        return {"id": member_row_id, "deleted": True}

    @app.patch("/api/teams/{team_id}/members/order")
    def reorder_team_members_endpoint(
        team_id: int,
        payload: TeamMemberReorder,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 팀
        관리 화면의 Drawflow 캔버스에서 노드를 드래그해 순서를 바꾸면
        호출된다. `teams.reorder_team_members()` 참고."""
        try:
            teams.reorder_team_members(
                conn, user["id"], team_id, payload.member_row_ids
            )
        except ValueError as exc:
            if str(exc) == "team_not_found":
                raise HTTPException(
                    status_code=404, detail="팀을 찾을 수 없습니다"
                ) from exc
            detail = {
                "member_row_ids_mismatch": "이 팀의 현재 Agent 멤버 전체와 정확히 일치하지 않습니다",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from exc
        return teams.get_team(conn, team_id)

    @app.get("/api/pipeline-templates")
    def list_pipeline_templates_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        return pipeline_runtime.list_pipeline_templates(conn)

    @app.get("/api/pipeline-templates/{template_id}")
    def get_pipeline_template_endpoint(
        template_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        template = pipeline_runtime.get_pipeline_template(conn, template_id)
        if template is None:
            raise HTTPException(
                status_code=404, detail="파이프라인 템플릿을 찾을 수 없습니다"
            )
        return template

    @app.post("/api/pipeline-templates", status_code=201)
    def create_pipeline_template_endpoint(
        payload: PipelineTemplateCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            template_id = pipeline_runtime.create_pipeline_template(
                conn, user["id"], payload.name, payload.description, payload.domain
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": template_id, "name": payload.name}

    @app.patch("/api/pipeline-templates/{template_id}/active")
    def set_pipeline_template_active_endpoint(
        template_id: int,
        payload: PipelineTemplateActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.set_pipeline_template_active(
                conn, user["id"], template_id, payload.is_active
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="파이프라인 템플릿을 찾을 수 없습니다"
            ) from exc
        return {"id": template_id, "is_active": payload.is_active}

    @app.delete("/api/pipeline-templates/{template_id}")
    def delete_pipeline_template_endpoint(
        template_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            pipeline_runtime.delete_pipeline_template(conn, user["id"], template_id)
        except ValueError as exc:
            detail = (
                "실행 이력이 있는 파이프라인 템플릿은 삭제할 수 없습니다"
                if str(exc) == "pipeline_template_has_run_history"
                else "파이프라인 템플릿을 찾을 수 없습니다"
            )
            status_code = (
                409 if str(exc) == "pipeline_template_has_run_history" else 404
            )
            raise HTTPException(status_code=status_code, detail=detail) from exc
        return {"id": template_id, "deleted": True}

    @app.post("/api/pipeline-templates/{template_id}/stages", status_code=201)
    def add_pipeline_stage_endpoint(
        template_id: int,
        payload: PipelineStageCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            stage_id = pipeline_runtime.add_stage(
                conn,
                user["id"],
                template_id,
                payload.name,
                payload.mode,
                [(a.role_name, a.instructions) for a in payload.agents],
                lead_role_name=payload.lead_role_name,
                instructions=payload.instructions,
                gate_ids=payload.gate_ids,
                min_gate_pass_ratio=payload.min_gate_pass_ratio,
                max_retry=payload.max_retry,
                requires_human_approval=payload.requires_human_approval,
                is_primary_deliverable=payload.is_primary_deliverable,
                checks_requirements_traceability=payload.checks_requirements_traceability,
                is_requirements_source=payload.is_requirements_source,
                on_fail_return_stage_id=payload.on_fail_return_stage_id,
                output_content_type=payload.output_content_type,
                expected_output=payload.expected_output,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

    @app.patch("/api/pipeline-templates/{template_id}/stages/{stage_id}")
    def update_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        payload: PipelineStageUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.update_stage(
                conn, user["id"], stage_id, **payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

    @app.delete("/api/pipeline-templates/{template_id}/stages/{stage_id}")
    def delete_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.delete_stage(conn, user["id"], stage_id)
        except ValueError as exc:
            detail = (
                "실행 이력이 있는 단계는 삭제할 수 없습니다"
                if str(exc) == "pipeline_stage_has_run_history"
                else "단계를 찾을 수 없습니다"
            )
            status_code = 409 if str(exc) == "pipeline_stage_has_run_history" else 404
            raise HTTPException(status_code=status_code, detail=detail) from exc
        return {"id": stage_id, "deleted": True}

    @app.post("/api/pipeline-templates/{template_id}/stages/{stage_id}/reorder")
    def reorder_pipeline_stage_endpoint(
        template_id: int,
        stage_id: int,
        payload: PipelineStageReorder,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            pipeline_runtime.reorder_stage(
                conn, user["id"], stage_id, payload.direction
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": stage_id}

    @app.post("/api/agent-pool/{agent_id}/tools/{tool_pool_id}", status_code=201)
    def link_agent_tool_endpoint(
        agent_id: int,
        tool_pool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """Agent별로 skill 매칭과 무관하게 Tool Pool 항목을 하나 더 붙인다
        — 다음 `run_crew()`/`crewai_agent_generator()` 실행
        부터 반영된다."""
        try:
            pool_registry.link_agent_tool(conn, user["id"], agent_id, tool_pool_id)
        except ValueError as exc:
            detail = (
                "Agent를 찾을 수 없습니다"
                if str(exc) == "agent_not_found"
                else "도구를 찾을 수 없습니다"
            )
            raise HTTPException(status_code=404, detail=detail) from exc
        return {"agent_id": agent_id, "tool_pool_id": tool_pool_id}

    @app.delete("/api/agent-pool/{agent_id}/tools/{tool_pool_id}")
    def unlink_agent_tool_endpoint(
        agent_id: int,
        tool_pool_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        pool_registry.unlink_agent_tool(conn, user["id"], agent_id, tool_pool_id)
        return {"agent_id": agent_id, "tool_pool_id": tool_pool_id}

    @app.post("/api/crew/run", status_code=201)
    def run_crew_endpoint(
        payload: CrewRunRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """여러 온보딩된 Agent를 CrewAI Crew로 묶어 실제 협업
        실행한다(Task.context로 산출물 전달 + allow_delegation으로 실제 동료
        위임/질의). 항상 실제 LLM API를 호출하므로 비용이 발생한다."""
        try:
            result = crew_runtime.run_crew(
                conn,
                payload.agent_ids,
                payload.task_descriptions,
                user["id"],
                process=payload.process,
                provider=payload.provider,
                model=payload.model,
                manager_agent_id=payload.manager_agent_id,
                memory=payload.memory,
                planning=payload.planning,
                manager_provider=payload.manager_provider,
                manager_model=payload.manager_model,
                parallel_agent_ids=payload.parallel_agent_ids,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"온보딩(active)된 Agent만 참여할 수 있습니다: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from hair_platform import evaluation_reports

        return {**result, "gate_scores": evaluation_reports.public(result.get("gate_scores"))}  # 내부 항목 제외(2026-11-25)

    @app.get("/api/crew-runs")
    def list_crew_runs(
        limit: int = 50,
        team_id: int | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-20, "Crew 빌더... 실행 이력") — "Crew
        빌더"의 "실행 이력" 탭이 Task를 가로질러 조회한다(admin/manager
        전용 — 이 목록이 모든 Task의 실행을 한 번에 보여주므로 개별
        Task 참가자 권한 범위를 넘는다).

        `team_id`(2026-10-29, 개선 계획 Phase 1) — "팀 관리" 화면에서
        특정 팀의 실행 이력만 좁혀볼 수 있게 한다."""
        return crew_history.list_recent_runs(conn, limit=limit, team_id=team_id)

    @app.get("/api/crew-runs/{run_id}")
    def get_crew_run(run_id: int, user: sqlite3.Row = Depends(get_current_user)):
        """단건 상세 — 협업 다이어그램·토큰 사용량·Gate 점수까지 포함된
        전체 데이터(`crewDiagramSvg()`/`renderCrewResult()`가 그대로
        쓸 수 있는 모양). 권한은 그 Crew 실행이 속한 Task의 참가자
        판정과 동일(파이프라인 실행 이력 조회와 같은 기준)."""
        run = crew_history.get_run(conn, run_id)
        if run is None:
            raise HTTPException(
                status_code=404, detail="Crew 실행 이력을 찾을 수 없습니다"
            )
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, run["task_id"], user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        return run

    @app.post("/api/crew-runs/{run_id}/retry-remaining", status_code=202)
    def retry_crew_run_remaining(
        run_id: int,
        background_tasks: BackgroundTasks,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 요청(2026-10-20, "실패 시 일부 재시도" — Crew 빌더
        로드맵 5단계) — 실패한 Crew 실행에서 이미 완료된 Agent는
        제외하고 남은 Agent만으로 새 즉석 Crew 실행을 시작한다. 권한은
        `get_crew_run`과 동일(참가자 또는 admin/manager)."""
        run = crew_history.get_run(conn, run_id)
        if run is None:
            raise HTTPException(
                status_code=404, detail="Crew 실행 이력을 찾을 수 없습니다"
            )
        if user["role"] not in ("admin", "manager") and (
            deliverables.task_participant_relationship(conn, run["task_id"], user["id"])
            is None
        ):
            raise HTTPException(
                status_code=403,
                detail="이 업무의 협업 워크스페이스에 접근할 권한이 없습니다",
            )
        try:
            retry = deliverables.start_crew_run_retry(
                conn,
                run_id,
                user["id"],
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
                "run_not_found": "Crew 실행 이력을 찾을 수 없습니다",
                "run_not_failed": "실패한 실행만 재시도할 수 있습니다",
                "nothing_to_retry": "재시도할 Agent가 남아있지 않습니다(이미 전원 완료됨)",
            }.get(str(exc), str(exc))
            raise HTTPException(status_code=400, detail=detail) from None
        job_queue.enqueue(
            conn, "deliverable.adhoc_crew",
            retry["job_by_agent_id"],
            retry["task_id"],
            user["id"],
            retry["agent_ids"],
            retry["task_descriptions"],
            "sequential",
            None,
            None,
            None,
            background_tasks=background_tasks,
        )
        return {"job_ids": list(retry["job_by_agent_id"].values()), "status": "running"}

    @app.post("/api/agent-pool/{agent_id}/onboard")
    def onboard_agent(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """Agent 온보딩 승인(Admin 전용). 승인 후 검색에 노출된다."""
        agent = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        conn.execute(
            "UPDATE agent_pool SET status = 'active' WHERE id = ?", (agent_id,)
        )
        audit.record(
            conn,
            user["id"],
            "agent_onboarded",
            "agent_pool",
            agent_id,
            before={"status": agent["status"]},
            after={"status": "active"},
        )
        conn.commit()
        return {"id": agent_id, "status": "active"}

    @app.post("/api/agent-pool/{agent_id}/deprecate")
    def deprecate_agent(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """Agent 오프보딩(Admin 전용). 이후 검색에서 제외된다."""
        agent = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        conn.execute(
            "UPDATE agent_pool SET status = 'deprecated' WHERE id = ?", (agent_id,)
        )
        audit.record(
            conn,
            user["id"],
            "agent_deprecated",
            "agent_pool",
            agent_id,
            before={"status": agent["status"]},
            after={"status": "deprecated"},
        )
        conn.commit()
        return {"id": agent_id, "status": "deprecated"}

    @app.delete("/api/agent-pool/{agent_id}")
    def delete_agent_endpoint(
        agent_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))
    ):
        """사용자 요청(2026-09-28) — AI 에이전트 레코드를 그 아래 지식
        색인·도구 연결까지 전부 지운다(오프보딩은 상태만 바꿀 뿐 지우지는
        않는다 — 이건 진짜 삭제). `confirmed` 배정이 있으면 막는다."""
        try:
            data_admin.delete_agent(conn, user["id"], agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Agent를 찾을 수 없습니다"
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
        return {"id": agent_id, "deleted": True}

    @app.post("/api/human-pool/{human_id}/consent")
    def set_performance_consent(
        human_id: int,
        payload: ConsentRequest,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사전 동의(opt-in). 본인 또는 Admin만 설정할 수 있다."""
        human = conn.execute(
            "SELECT * FROM human_pool WHERE id = ?", (human_id,)
        ).fetchone()
        if human is None:
            raise HTTPException(
                status_code=404, detail="Human Pool 레코드를 찾을 수 없습니다"
            )
        if human["user_id"] != user["id"] and user["role"] != Role.ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="본인 또는 Admin만 동의를 설정할 수 있습니다",
            )
        performance.set_human_consent(conn, human_id, payload.consent)
        audit.record(
            conn,
            user["id"],
            "performance_consent_set",
            "human_pool",
            human_id,
            after={"consent": payload.consent},
        )
        conn.commit()
        return {"id": human_id, "performance_consent": payload.consent}

    @app.post("/api/human-pool/{human_id}/performance")
    def set_human_performance_score(
        human_id: int,
        payload: PerformanceScoreRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """성과 점수 입력. 동의 없이는 거부된다.

        사용자 결정(2026-09-29,
        "(b) Human Pool도 admin 전용으로 좁히기") — Agent Pool의
        `/performance`(admin 전용)와 대칭을 맞췄다. 이전엔 admin+manager
        공용이었다."""
        try:
            performance.set_human_performance(conn, human_id, payload.score)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="사람을 찾을 수 없습니다"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="동의 없이는 성과데이터를 수집할 수 없습니다",
            ) from exc
        audit.record(
            conn,
            user["id"],
            "human_performance_recorded",
            "human_pool",
            human_id,
            after={"score": payload.score},
        )
        conn.commit()
        return {"id": human_id, "performance_score": payload.score}

    @app.post("/api/agent-pool/{agent_id}/performance")
    def set_agent_performance_score(
        agent_id: int,
        payload: PerformanceScoreRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """Agent 성과 점수(사람이 아니므로 동의 불필요)."""
        performance.set_agent_performance(conn, agent_id, payload.score)
        audit.record(
            conn,
            user["id"],
            "agent_performance_recorded",
            "agent_pool",
            agent_id,
            after={"score": payload.score},
        )
        conn.commit()
        return {"id": agent_id, "performance_score": payload.score}

    @app.post("/api/agent-pool/{agent_id}/cost")
    def set_agent_cost(
        agent_id: int,
        payload: CostUpdateRequest,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """Agent 비용 수동 입력(1차 범위)."""
        cost.set_agent_cost(conn, agent_id, payload.cost_estimate)
        audit.record(
            conn,
            user["id"],
            "agent_cost_updated",
            "agent_pool",
            agent_id,
            after={"cost_estimate": payload.cost_estimate},
        )
        conn.commit()
        return {"id": agent_id, "cost_estimate": payload.cost_estimate}

    @app.patch("/api/agent-pool/{agent_id}/name")
    def set_agent_name(
        agent_id: int,
        payload: AgentNameUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시
        (채팅 포함)에도 활용될 수 있도록 개선") — `role_name`은 생성
        당시 역할이라 같은 역할의 Agent가 여럿이면 구분이 안 됐다.
        비용/성과점수(admin 전용)와 달리 이건 human_pool의 역할·스킬
        수정과 같은 "정상 운영 업무"라 admin+manager 둘 다 허용한다
        (사람 인력 쪽과의 권한 비대칭 방지). 빈 문자열/공백은 이름
        지우기(다시 role_name 표시로 복귀)로 정규화한다."""
        row = conn.execute(
            "SELECT id FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent를 찾을 수 없습니다")
        name = (payload.name.strip() if payload.name else "") or None
        conn.execute("UPDATE agent_pool SET name = ? WHERE id = ?", (name, agent_id))
        audit.record(
            conn,
            user["id"],
            "agent_name_updated",
            "agent_pool",
            agent_id,
            after={"name": name},
        )
        conn.commit()
        return {"id": agent_id, "name": name}
