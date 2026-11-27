"""모델·설정 API(모델 레지스트리·임베딩·지식 검색 설정·기본/보조 모델·실제 사용량·조직 정책) — `create_app()` 분리(2026-11-25, 기술 부채 정리).

본문은 옮기기 전 `create_app()` 안의 라우트 그대로다(경로·권한·응답·묶음 안 등록 순서 동일). 새 라우트도 이 모듈의
`register()` 안에 둔다."""

from __future__ import annotations

import sqlite3

from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
)

from hair_platform import (
    llm,
    llm_usage,
    model_benchmark,
    model_registry,
    org_policy,
    vectorstore,
)
from hair_platform.auth import (
    Role,
    get_current_user,
    require_role,
)
from hair_platform.models import (
    AuxiliaryLlmConfigUpdate,
    EmbeddingConfigUpdate,
    ModelActiveUpdate,
    ModelContextWindowUpdate,
    ModelMaxConcurrentUpdate,
    ModelOptimizationTiersUpdate,
    ModelPricingUpdate,
    ModelRegistryCreate,
    ModelSamplingOptionsUpdate,
    OrgPolicyUpdate,
    OutputLanguageUpdate,
    PlatformLlmConfigUpdate,
    RagSearchConfigUpdate,
)


def register(app: FastAPI, conn: sqlite3.Connection) -> None:
    """모델·설정 API 라우트를 `app`에 등록한다(`create_app()`이 부른다)."""
    @app.get("/api/model-registry")
    def list_model_registry(
        provider: str | None = None,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        return model_registry.list_models(conn, provider=provider)

    @app.get("/api/model-registry/names")
    def list_model_registry_names(
        provider: str | None = None,
        user: sqlite3.Row = Depends(get_current_user),
    ):
        """사용자 보고(2026-10-22, 스크린샷 — "member 권한으로 대화시
        모델 프로바이더를 선택해도 등록된 모델이 불려오지 않음") —
        `/api/model-registry`(바로 위)는 admin 전용인데(2026-11-02,
        "모델 관리 메뉴 영역은 관리자 전용으로 변경"으로 manager도
        제외됐다), `webui.py`의 `fetchModelsByProvider()`가 이걸로
        조회하고 403을 조용히 빈 목록으로 폴백한다(`catch (e) { return
        byProvider; }`) — "AI 에이전트 생성"·"AI 에이전트 관리(모델
        설정)"·"Crew 실행"·대화 워크스페이스 4곳 전부 admin이 아닌
        역할도 쓰는 화면인데 모델 드롭다운만 "기본값 사용"/"직접
        입력..."으로 비어 보였다. `model_registry`엔 provider/모델명뿐
        민감 정보가 없으므로(`/api/skill-pool/names`와 같은 이유),
        id/is_active 같은 관리용 필드는 빼고 provider+name만 모든
        role에 노출하는 조회 전용 엔드포인트를 따로 둔다(`/api/model-
        registry`의 admin 전용 제한은 그대로 — 등록/수정/삭제 RBAC
        테스트 `test_gc223_model_registry_rbac`가 이미 그 경계를
        검증한다)."""
        return [
            {"provider": m["provider"], "name": m["name"]}
            for m in model_registry.list_models(conn, provider=provider)
            if m["is_active"]
        ]

    @app.get("/api/model-registry/fetch")
    def fetch_model_registry_candidates(
        provider: str,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """provider가 실제로 제공/보유한 모델 후보를 라이브로 조회한다 —
        조회만으로는 아무것도 저장되지 않는다(등록은 별도
        `POST /api/model-registry`). ollama는 로컬 서버 접속 실패,
        openai/anthropic은 API 키 미설정·네트워크 오류가 흔한 실패
        모드라 그대로 500을 내지 않고 사람이 읽을 수 있는 메시지로
        바꿔 400으로 돌려준다."""
        if provider not in model_registry.PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"지원하지 않는 provider입니다: {provider}"
            )
        try:
            models = model_registry.fetch_available_models(provider)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{provider} 모델 목록을 불러오지 못했습니다: {exc}",
            ) from exc
        return {"provider": provider, "models": models}

    @app.post("/api/model-registry", status_code=201)
    def create_model_registry_entry(
        payload: ModelRegistryCreate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            model_id = model_registry.create_model(
                conn, user["id"], payload.provider, payload.name
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="이미 등록된 모델입니다"
            ) from exc
        return {"id": model_id, "provider": payload.provider, "name": payload.name}

    @app.patch("/api/model-registry/{model_id}/active")
    def set_model_registry_active(
        model_id: int,
        payload: ModelActiveUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            model_registry.set_model_active(
                conn, user["id"], model_id, payload.is_active
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="모델을 찾을 수 없습니다"
            ) from exc
        return {"id": model_id, "is_active": payload.is_active}

    @app.patch("/api/model-registry/{model_id}/optimization-tiers")
    def set_model_registry_optimization_tiers(
        model_id: int,
        payload: ModelOptimizationTiersUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화
        각 tier에 적용되는 모델을 매핑하는 기능을 제공") — `agent_
        optimizer.py`가 이름 패턴 추정 대신 이 모델을 어떤 tier
        (deep_reasoning/precision_fast/standard)의 최우선 후보로 쓸지
        admin이 직접 지정한다. `optimization_tiers: []`로 다시 호출하면
        매핑을 지워 패턴 추정으로 되돌아간다 — 몇 번이든 다시 호출해
        바꿀 수 있다(하위 호환: 설정 안 한 모델은 기존처럼 패턴 추정
        그대로 동작)."""
        try:
            model_registry.set_optimization_tiers(
                conn, user["id"], model_id, payload.optimization_tiers
            )
        except ValueError as exc:
            if str(exc) == "model_not_found":
                raise HTTPException(
                    status_code=404, detail="모델을 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": model_id, "optimization_tiers": payload.optimization_tiers}

    @app.patch("/api/model-registry/{model_id}/context-window")
    def set_model_registry_context_window(
        model_id: int,
        payload: ModelContextWindowUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-11-18, "...엔터프라이즈 환경 적용이 가능한
        수준의 확실한 개선 방안을 도출해줘" 개선축 B) — 이 모델이 실제로
        받아들이는 토큰 한도를 admin이 등록한다. `null`로 보내면 초기화
        (provider별 보수적 기본값으로 폴백)."""
        try:
            model_registry.set_context_window(
                conn, user["id"], model_id, payload.context_window
            )
        except ValueError as exc:
            if str(exc) == "model_not_found":
                raise HTTPException(
                    status_code=404, detail="모델을 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": model_id, "context_window": payload.context_window}

    @app.patch("/api/model-registry/{model_id}/max-concurrent")
    def set_model_registry_max_concurrent(
        model_id: int,
        payload: ModelMaxConcurrentUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """모델별 동시 실행 상한(2026-11-27, 개선 계획 3-1 — `llm_gate`)."""
        try:
            model_registry.set_max_concurrent(conn, user["id"], model_id, payload.max_concurrent)
        except ValueError as exc:
            if str(exc) == "model_not_found":
                raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다") from exc
            raise HTTPException(status_code=400, detail="동시 실행 상한은 1~64입니다") from exc
        return {"id": model_id, "max_concurrent": payload.max_concurrent}

    @app.patch("/api/model-registry/{model_id}/sampling-options")
    def set_model_registry_sampling_options(
        model_id: int,
        payload: ModelSamplingOptionsUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """모델별 생성 옵션(2026-11-21, "특정 모델만을 위한 하드코딩은 없도록")
        — top_k/top_p. 빈 값은 보내지 않는다(그 모델의 기본값)."""
        try:
            saved = model_registry.set_sampling_options(
                conn, user["id"], model_id, payload.model_dump(exclude_none=True)
            )
        except ValueError as exc:
            if str(exc) == "model_not_found":
                raise HTTPException(
                    status_code=404, detail="모델을 찾을 수 없습니다"
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": model_id, "sampling_options": saved}

    # 컨텍스트 한도·생성 옵션 추천(2026-11-26 사용자 요청 — `model_recommend.py`). 조회는 채워 주기만 하고, 적용은 저장한다.
    def _registry_row(model_id: int):
        row = conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다")
        return row

    @app.get("/api/model-registry/{model_id}/recommendation")
    def get_model_registry_recommendation(model_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import model_recommend

        row = _registry_row(model_id)
        return model_recommend.recommend(row["provider"], row["name"])

    @app.post("/api/model-registry/{model_id}/apply-recommendation")
    def apply_model_registry_recommendation(model_id: int, user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """추천값을 저장한다 — 컨텍스트 한도와, 생성 옵션 중 추천이 있는 항목만(기존 다른 값은 유지)."""
        import json as _json

        from hair_platform import model_recommend

        row = _registry_row(model_id)
        rec = model_recommend.recommend(row["provider"], row["name"])
        if not rec.get("available"):
            raise HTTPException(status_code=409, detail=" ".join(rec.get("reasons") or ["추천값이 없습니다"]))
        if rec.get("context_window"):
            model_registry.set_context_window(conn, user["id"], model_id, int(rec["context_window"]))
        options = _json.loads(row["sampling_options"] or "{}")
        for key in ("top_k", "top_p", "max_output_tokens"):
            if rec.get(key) is not None:
                options[key] = rec[key]
        saved = model_registry.set_sampling_options(conn, user["id"], model_id, options)
        return {"id": model_id, "context_window": rec.get("context_window"), "sampling_options": saved,
                "reasons": rec.get("reasons") or []}

    @app.patch("/api/model-registry/{model_id}/pricing")
    def set_model_registry_pricing(
        model_id: int,
        payload: ModelPricingUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """모델별 단가(100만 토큰당 입력/출력, 2026-11-22) — 실제 사용량 금액 계산용."""
        try:
            saved = model_registry.set_pricing(
                conn, user["id"], model_id, payload.price_input_per_1m, payload.price_output_per_1m
            )
        except ValueError as exc:
            if str(exc) == "model_not_found":
                raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다") from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": model_id, **saved}

    @app.get("/api/llm-usage")
    def get_llm_usage(
        days: int = 30,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """provider·모델별 실제 토큰 사용량과(단가가 입력된 모델만) 금액(2026-11-22)."""
        return llm_usage.report(conn, days if days > 0 else None)

    @app.get("/api/llm-usage/work")
    def get_llm_usage_by_work(
        days: int = 30,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """업무(할 일)별·Agent별 실제 사용량과 효율 지표(2026-11-23, P2-11 — `llm_usage.work_report`)."""
        return llm_usage.work_report(conn, days if days > 0 else None)

    @app.post("/api/model-registry/{model_id}/benchmark", status_code=201)
    def run_model_registry_benchmark(
        model_id: int,
        mode: str = "quick",
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-11-12, "ollama, openai 등 프로바이더의 모델
        성능 Test하고 결과 표시, 티어 추천 기능") — 고정 테스트 프롬프트
        1건을 실제로 호출해 속도를 재고(비용 발생 가능 — 클라우드
        provider는 실제 API 과금), 결과를 이력에 쌓는다. 속도만 보는
        참고용 tier 추천이며 품질 평가가 아니다(`model_benchmark.py`
        모듈 docstring 참고)."""
        try:
            result = model_benchmark.run_benchmark(conn, model_id, user["id"], mode=mode)
        except ValueError as exc:
            if str(exc) == "invalid_mode":
                raise HTTPException(status_code=400, detail="mode는 quick 또는 full입니다") from exc
            raise HTTPException(
                status_code=404, detail="모델을 찾을 수 없습니다"
            ) from exc
        return result

    @app.get("/api/model-registry/{model_id}/benchmarks")
    def list_model_registry_benchmarks(
        model_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        return model_benchmark.list_benchmarks(conn, model_id)

    @app.delete("/api/model-registry/{model_id}")
    def delete_model_registry_entry(
        model_id: int,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            model_registry.delete_model(conn, user["id"], model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="모델을 찾을 수 없습니다"
            ) from exc
        return {"id": model_id, "deleted": True}

    @app.get("/api/embedding-config/fetch")
    def fetch_embedding_config_candidates(
        provider: str,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """임베딩 설정 폼이 provider 선택 시 자동으로 불러오는 모델
        후보(`GET /api/model-registry/fetch`와 같은 관례) — 조회만으로는
        아무것도 저장되지 않는다."""
        if provider not in ("openai", "ollama"):
            raise HTTPException(
                status_code=400, detail=f"지원하지 않는 provider입니다: {provider}"
            )
        try:
            models = vectorstore.fetch_available_embedding_models(provider)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{provider} 임베딩 모델 목록을 불러오지 못했습니다: {exc}",
            ) from exc
        return {"provider": provider, "models": models}

    @app.get("/api/embedding-config")
    def get_embedding_config_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """admin이 명시적으로 설정해둔 값이 있으면 그걸, 없으면 지금
        실제로 쓰이는 자동판별 결과를 `is_explicit=False`로 알려준다 —
        화면에 "지금 뭐가 쓰이고 있는지"를 항상 보여줄 수 있게 한다."""
        configured = vectorstore.get_embedding_config(conn)
        if configured is not None:
            return {**configured, "is_explicit": True}
        provider, model = vectorstore.resolve_default_provider_and_model(conn)
        return {"provider": provider, "model": model, "is_explicit": False}

    @app.put("/api/embedding-config")
    def set_embedding_config_endpoint(
        payload: EmbeddingConfigUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            vectorstore.set_embedding_config(
                conn, user["id"], payload.provider, payload.model
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"provider": payload.provider, "model": payload.model}

    @app.get("/api/rag-search-config")
    def get_rag_search_config_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        """사용자 요청(2026-11-03, "RAG 검색 품질 향상... 우선순위를
        정해 진행해줘") — `/api/embedding-config`와 같은 모양: admin이
        설정해둔 값이 있으면 그걸, 없으면 기본값을 `is_explicit=False`로
        알려준다."""
        configured = vectorstore.get_rag_search_config(conn)
        if configured is not None:
            return {**configured, "is_explicit": True}
        top_k, min_score = vectorstore.resolve_rag_search_config(conn)
        return {"top_k": top_k, "min_score": min_score, "is_explicit": False}

    @app.put("/api/rag-search-config")
    def set_rag_search_config_endpoint(
        payload: RagSearchConfigUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN, Role.MANAGER)),
    ):
        try:
            vectorstore.set_rag_search_config(
                conn, user["id"], payload.top_k, payload.min_score
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"top_k": payload.top_k, "min_score": payload.min_score}

    @app.get("/api/platform-llm-config")
    def get_platform_llm_config_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본
        동작을 위한 LLM을 지정할 수 있게") — `/api/embedding-config`와
        같은 모양: admin이 명시적으로 설정해둔 값이 있으면 그걸,
        없으면 지금 실제로 쓰이는 자동판별 결과를 `is_explicit=False`로
        알려준다."""
        configured = llm.get_platform_default_llm(conn)
        if configured is not None:
            return {**configured, "is_explicit": True}
        provider, model = llm.resolve_provider_and_model(None, None)
        return {"provider": provider, "model": model, "is_explicit": False}

    @app.put("/api/platform-llm-config")
    def set_platform_llm_config_endpoint(
        payload: PlatformLlmConfigUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            llm.set_platform_default_llm(
                conn, user["id"], payload.provider, payload.model
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"provider": payload.provider, "model": payload.model}

    # 보조 작업 모델(2026-11-24, P0-2 응답 속도) — 언어·수치 교정 같은 짧은 보조 호출 전용.
    @app.get("/api/auxiliary-llm-config")
    def get_auxiliary_llm_config_endpoint(
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        from hair_platform import model_options

        configured = llm.get_auxiliary_llm(conn)
        active = model_options.auxiliary_model()
        return {
            "provider": configured["provider"] if configured else None,
            "model": configured["model"] if configured else None,
            # 설정했지만 레지스트리에서 비활성화돼 쓰이지 않는 경우를 화면에 알리기 위해
            "in_use": bool(active),
        }

    @app.put("/api/auxiliary-llm-config")
    def set_auxiliary_llm_config_endpoint(
        payload: AuxiliaryLlmConfigUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            llm.set_auxiliary_llm(conn, user["id"], payload.provider, payload.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"provider": payload.provider, "model": payload.model}

    @app.get("/api/org-policy")
    def get_org_policy_endpoint(user: sqlite3.Row = Depends(get_current_user)):
        """조직 정책(2026-11-21) — 사번 입력란이 형식 안내를 보여 주도록 로그인 사용자 누구나
        읽을 수 있다(값 자체는 비밀이 아니다). 변경은 admin만."""
        return {
            "employee_id": org_policy.employee_id_policy(),
            "output_language": org_policy.output_language_policy(),
        }

    @app.put("/api/org-policy")
    def set_org_policy_endpoint(
        payload: OrgPolicyUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        try:
            result = org_policy.set_employee_id_policy(
                conn, user["id"], payload.employee_id_pattern, payload.employee_id_hint
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"employee_id": result}

    @app.put("/api/org-policy/output-language")
    def set_output_language_endpoint(
        payload: OutputLanguageUpdate,
        user: sqlite3.Row = Depends(require_role(Role.ADMIN)),
    ):
        """조직 결과물 언어(2026-11-22) — 에이전트 지시에 반영, 한국어가 아니면 언어 교정을 끈다."""
        try:
            return org_policy.set_output_language(conn, user["id"], payload.code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---------- 평가 기준 설정(2026-11-25 — `evaluation_config.py`, admin) ----------
    @app.get("/api/admin/evaluation-config")
    def get_evaluation_config(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import evaluation_config

        return {"config": evaluation_config.get(conn), "defaults": evaluation_config.DEFAULTS,
                "gate_ids": list(evaluation_config.GATE_IDS)}

    @app.put("/api/admin/evaluation-config")
    def put_evaluation_config(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import evaluation_config

        try:
            return {"config": evaluation_config.save(conn, user["id"], payload)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/evaluation-config/reset")
    def reset_evaluation_config(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        from hair_platform import evaluation_config

        return {"config": evaluation_config.reset(conn, user["id"])}

    @app.get("/api/admin/evaluation-config/targets.json")
    def export_evaluation_targets(user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """SDK `.aoo/targets.json` 형식 — `agent-eval gate`가 같은 Gate 기준으로 채점하게 내려받는다."""
        import json as _json

        from fastapi.responses import Response

        from hair_platform import evaluation_config

        body = _json.dumps(evaluation_config.to_targets(evaluation_config.get(conn)), ensure_ascii=False, indent=2)
        return Response(content=body, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="targets.json"'})

    @app.post("/api/admin/evaluation-config/targets")
    def import_evaluation_targets(payload: dict = Body(...), user: sqlite3.Row = Depends(require_role(Role.ADMIN))):
        """`.aoo/targets.json` 내용을 받아 Gate 기준만 반영한다(SLA·추론 표지는 그대로)."""
        from hair_platform import evaluation_config

        try:
            merged = evaluation_config.from_targets(payload, evaluation_config.get(conn))
            return {"config": evaluation_config.save(conn, user["id"], merged)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
