"""HAIR-Platform API — Phase 3 v0–v4 (전체 개발 버전 완료).

v0 범위: Auth 최소구현(세션 기반) + RBAC 4종 역할
+ 브리프 제출(규칙기반 스텁) + Human Pool 온보딩 + 최소 승인 워크플로우
(단일 승인자, self-approval 방지).

v1 범위 추가: Review UI(Task 분해 결과 검토, 후보 매칭 미포함) +
Pool 통합 검색 + Assignment Engine 매칭(점수 breakdown 포함, `assignment.py`).

v2 범위 추가: 배분 요청 → 승인 후 확정(`assignments`+`assignment_history`
트랜잭션 원자성) + 조직도 조회(confirmed 배정만 노출) + 재배치
(같은 task 재승인 시 이전 confirmed 배정을 superseded로 전환, `assignments.py`).

v3 범위 추가: 다단계 결재라인(조직도 매니저 체인 자동 라우팅 — `approvals.py`) +
Job Queue+Docker 샌드박스로 Agent 생성(실패 역할만 재시도 — `jobs.py`·
`sandbox.py`) + Agent 온보딩/오프보딩(이때 `pool.agent_pool_candidates()`가
처음으로 실제 데이터를 반환하게 된다) + 감사로그(append-only — `audit.py`).

v4(마지막 개발 버전) 범위 추가: 성과리뷰(사람 데이터는 사전 동의(opt-in) 없이는
절대 노출하지 않는다 — `performance.py`) + 비용 조회(1차는 수동 입력만
— `cost.py`). 이로써 핵심 기능이 전부 구현됐다.

구조(2026-11-25 기술 부채 정리): 예전엔 모든 라우트가 `create_app()` 한 함수(5,900여 줄) 안의 클로저였다. 이제
`create_app()`은 기동 준비(DB·세션·고아 작업 정리·자동화 루프)와 로그인·첫 화면만 맡고, 나머지 라우트는 묶음별
`routes_*.py` 모듈의 `register(app, conn)`이 등록한다(본문·경로·권한·응답은 옮기기 전과 같음 — 분리 전후 라우트
238개의 경로·메서드·함수·소스 해시·의존성·묶음 안 순서를 비교해 확인). 새 라우트는 해당 묶음 모듈에 둔다.
"""

from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from hair_platform import (
    conversations,
    db,
    deliverables,
    pipeline_runtime,
    retention,
    webui,
)
from hair_platform.auth import (
    authenticate,
    create_session_token,
    get_current_user,
    password_version,
)
from hair_platform.models import (
    LoginRequest,
)


def parse_brief_stub(raw_text: str) -> list[str]:
    """규칙기반 스텁 — 줄바꿈 단위로 역할 후보를 나눈다(실제 LLM 파싱은 이후 버전)."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines or [raw_text.strip()]


def create_app(db_path: str | None = None) -> FastAPI:
    """`db_path`를 생략하면(uvicorn `--factory` 경로가 인자 없이 호출하는
    바로 그 경우) `HAIR_PLATFORM_DB_PATH` 환경변수를 본다 — 이전까지
    여기 아무 폴백이 없어 `uvicorn hair_platform.app:create_app --factory`
    로 띄우면 `--db-path`를 지정할 방법 자체가 없이 매 재시작마다
    조용히 ":memory:"(휘발성)로 뜨고 있었다(2026-10-10, `hair serve` CLI
    작업 중 발견). 명시적으로 인자를 넘기는
    기존 호출(테스트 등)은 전부 그대로 동작한다."""
    if db_path is None:
        db_path = os.environ.get("HAIR_PLATFORM_DB_PATH", ":memory:")
    # 사용자 요청(2026-09-26) — 프로젝트 루트 `.env`(gitignore됨)에서
    # ANTHROPIC_API_KEY/OPENAI_API_KEY/HAIR_PLATFORM_SECRET_KEY 등을 읽는다.
    # `.env`가 없으면 조용히 아무 일도 하지 않는다(기존 동작과 동일).
    # 이미 설정된 실제 환경변수를 덮어쓰지 않는다(override=False, 기본값).
    load_dotenv()

    # API 문서(/openapi.json)의 버전도 `hair --version`·화면 상단과 같은 설치 메타데이터에서(2026-11-27 — 예전엔 지정하지
    # 않아 FastAPI 기본값 0.1.0으로 보였다).
    from hair_platform import get_version

    app = FastAPI(title="HAIR-Platform", version=get_version())
    # 세션 기반 인증(SessionMiddleware).
    # 코드 재점검(2026-09-26)에서 발견: "배포 시 환경변수로 교체한다"는 주석만 있고
    # 실제로 환경변수를 읽는 코드가 없어 항상 소스에 공개된 고정 키를 쓰던 오류를
    # 수정 — HAIR_PLATFORM_SECRET_KEY가 없으면 개발용 기본값으로 폴백한다.
    #
    # 하드코딩·스텁 점검(2026-11-22) 1순위 — 환경변수가 없을 때 소스에 공개된 고정 문자열
    # ("dev-secret-change-in-production")로 폴백해, 소스를 본 누구나 세션 쿠키·Bearer 토큰을
    # 위조할 수 있었다. 이제 첫 기동 때 무작위 키를 만들어 DB에 저장하고 재사용한다(재시작해도
    # 로그인이 유지된다). 운영에서는 여전히 환경변수로 지정하는 것을 권장한다.
    conn = db.get_connection(db_path)
    db.init_schema(conn)
    secret_key = os.environ.get("HAIR_PLATFORM_SECRET_KEY") or db.session_secret(conn)
    app.add_middleware(SessionMiddleware, secret_key=secret_key)
    # get_current_user가 Bearer 토큰(탭별 다계정 로그인, auth.py 참고)을
    # 복호화할 때 같은 키를 쓴다 — 쿠키 서명과 토큰 서명이 어긋나면 안 된다.
    app.state.secret_key = secret_key

    # 실측으로 발견(2026-09-28, 리뷰) — 이전 프로세스가 실행 도중
    # 죽으면 agent_execution_jobs가 영원히 'running'으로 남아 해당
    # task의 실행 버튼이 복구 불가능하게 막힌다. 단일 프로세스 전제이므로
    # 기동 시점의 'running'은 전부 이전 프로세스의 잔재로 확정하고 정리한다.
    #
    # 작업 큐(2026-11-26, P1-6 2단계 — `job_queue.py`): external 모드면 "실행 중" 작업은 작업 프로세스(`hair worker`)의
    # 것이라 웹 서버 재시작 때 정리하지 않는다(작업 프로세스가 자기 기동 때 정리). embedded면 이 서버가 남긴 것만
    # 정리하되, 기록만 되고 시작하지 못한 대기 작업의 행은 남겨 아래에서 이어서 실행한다.
    from hair_platform import job_queue

    external_worker = job_queue.mode() == "external"
    keep = job_queue.protected_ids(conn) if not external_worker else {}
    if not external_worker:
        job_queue.mark_interrupted(conn, "server-")
        deliverables.reap_orphaned_execution_jobs(conn, keep["agent_execution_jobs"])
    # 실측(2026-11-06, park_member 로드러너 게임 데모) — CrewAI 매니저가
    # 위임 반복 중 LLM 컨텍스트 윈도우를 초과하면 그 예외가 CrewAI/
    # LiteLLM 내부에서 로그만 남기고 우리 코드까지 안 올라와,
    # `pipeline_runs.status='running'`이 영원히 방치된다(위 agent_
    # execution_jobs와 같은 "단일 프로세스 재기동 시 정리" 전제).
    if not external_worker:
        pipeline_runtime.reap_orphaned_pipeline_runs(conn, keep["pipeline_runs"])
    # 사용자 보고(2026-11-19, "ollama 실행되지 않고 있는데... 실행 중이
    # 표시됨") — 위 두 reap과 같은 이유로 `conversation_jobs`(기본 AI/
    # Agent 멘션/Team 멘션)도 정리가 빠져 있었다 — 서버 재시작으로도
    # 안 풀리는 "실행 중" 먹통 상태의 원인이었다.
    if not external_worker:
        conversations.reap_orphaned_conversation_jobs(conn, keep["conversation_jobs"])
    # 사용자 결정(2026-09-29, "assignment_history/audit_log 보관기간 1년") —
    # 오래 켜진 배포에서 기동 때마다 자연스럽게 정리되도록 여기서도 실행한다
    # (retention.py 참고, 지울 게 없으면 DB에 손도 대지 않는다).
    retention.purge_expired(conn)
    app.state.db = conn
    # 실제 LLM 사용량 기록(2026-11-22) — 직접 호출(`llm.py`)과 CrewAI(litellm) 경로 모두.
    # 평가 기준 설정·AI 사용 한도·litellm 콜백도 — 작업 프로세스(`hair worker`)와 같은 설정(2026-11-26, P1-6 2단계).
    job_queue.configure_runtime(conn)
    # 자동화 루프(정기 실행·외부 알림, 2026-11-23 — `automation.py`) — `hair serve`(HAIR_AUTOMATION=1)
    # 에서만 띄운다. 프로세스당 한 번.
    from hair_platform import automation

    if automation.start_loop(conn):
        # `hair serve`일 때만(자동화 루프와 같은 조건) — 낱말확인 분석기·어휘를 미리 만든다(2026-11-25).
        from hair_platform import korean_lexicon

        korean_lexicon.warm_up_in_background()

    # 라우트 묶음(2026-11-25 — create_app 분리): 각 모듈의 register(app, conn)가 그 묶음의 라우트를 등록한다.
    # 묶음끼리는 URL 앞부분이 겹치지 않아 등록 순서가 판정에 영향을 주지 않고, 묶음 안 순서는 그대로다.
    from hair_platform import (
        routes_approvals,
        routes_conversations,
        routes_deliverables,
        routes_models,
        routes_reports,
        routes_resources,
        routes_users,
        routes_work,
    )

    routes_users.register(app, conn)
    routes_work.register(app, conn)
    routes_approvals.register(app, conn)
    routes_conversations.register(app, conn)
    routes_deliverables.register(app, conn)
    routes_resources.register(app, conn)
    routes_models.register(app, conn)
    routes_reports.register(app, conn)

    @app.post("/login")
    def login(payload: LoginRequest, request: Request):
        # 로그인 시도 제한(2026-11-27, 개선 제안 5-2 — `login_guard.py`): 아이디별 5회·접속 주소별 20회 틀리면 15분 막는다.
        from hair_platform import audit, login_guard

        ip = request.client.host if request.client else None
        wait = login_guard.retry_after(payload.username, ip)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"로그인 시도가 너무 많아 잠시 막았습니다 — {max(1, (wait + 59) // 60)}분 뒤에 다시 시도하세요",
                headers={"Retry-After": str(wait)},
            )
        user = authenticate(conn, payload.username, payload.password)
        if user is None:
            if login_guard.record_failure(payload.username, ip):
                target = conn.execute("SELECT id FROM users WHERE username = ?", (payload.username,)).fetchone()
                if target is not None:
                    audit.record(conn, target["id"], "login_locked", "user", target["id"],
                                 after={"ip": ip, "minutes": login_guard.LOCK_SECONDS // 60})
                    conn.commit()
            raise HTTPException(
                status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다"
            )
        login_guard.record_success(payload.username)
        pv = password_version(user["password_hash"])  # 비밀번호가 바뀌면 이 로그인은 끊긴다(2026-11-27)
        request.session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "pv": pv,
        }
        # 탭별 다계정 로그인(auth.py 주석 참고) — Web UI는 이 토큰을
        # sessionStorage(탭마다 독립)에 저장해 쿠키 세션과 별도로 쓴다.
        token = create_session_token(secret_key, user["id"], pv)
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "token": token,
        }

    @app.get("/health")
    def health():
        """감시 도구용 상태 확인(2026-11-27, 개선 제안 5-4) — 로그인 없이 DB·작업 큐 상태만(내부 정보 없음)."""
        from fastapi.responses import JSONResponse

        from hair_platform import system_status

        code, body = system_status.health(conn)
        return JSONResponse(body, status_code=code)

    @app.post("/logout")
    def logout(request: Request):
        """Web UI 세션 종료 — 로그인은 있었는데 로그아웃이 없던 것을 보완."""
        request.session.clear()
        return {"status": "logged_out"}

    @app.get("/api/me")
    def get_me(user: sqlite3.Row = Depends(get_current_user)):
        """Web UI 셸이 현재 로그인 사용자·role을 알아야 RBAC에 맞게 화면을 그린다."""
        return {"id": user["id"], "username": user["username"], "role": user["role"]}


    # 연동 설정 API(개인 API 키·코드 저장소·기본 구성 점검·외부 알림·정기 실행·이벤트 트리거) — 2026-11-25
    # `routes_integrations.py`로 분리(create_app 분리 1단계, 경로·권한·응답은 그대로).
    from hair_platform import routes_integrations

    routes_integrations.register(app, conn)


    # ---------- 협업 워크스페이스(사용자 요청, 2026-09-28) ----------
    # RBAC 원칙: 관리자/매니저는 감독 목적으로 조회는 전부 가능하지만,
    # 작성(deliverable/댓글)은 그 task의 사람 당사자/Agent 요청자 본인만
    # — 대리 작성 금지.


    # ---------- 대화 중심 작업공간(Conversational Workspace,
    # 2026-10-08, 사용자 요청 "로그인하면 기본적으로 대화창이 주어지고
    # ... 사람이나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용
    # 하여 업무를 수행") ----------


    # ---------- 대화 첨부(저장 없이 그 대화 메시지로 직접 주입 — "A안")
    # ----------


    # ---------- "지식 베이스" 탭(사용자 요청, 2026-10-26, "공통의 지식을
    # 관리하기 위해 Admin에 VectorDB, GraphDB 관리 기능을 추가해줘") ----------


    # 사용자 요청(2026-11-20, 지식베이스 등록 체계 개편) — `POST /api/
    # agent-pool/{id}/index-knowledge`(Agent `last_output`/임의 text를 결재
    # 없이 즉시 색인)는 삭제했다. 지식베이스 입구는 admin 직접 등록
    # (`/api/documents*`)과 제출 → admin 결재(`/api/knowledge-submissions`)
    # 둘뿐이다.


    # ---------- 모델 레지스트리(사용자 요청, 2026-10-06) ----------
    # "운영 관리"에 provider별 사용 가능한 모델을 등록해두고, "AI 에이전트
    # 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"의 자유 텍스트
    # 모델명 입력을 여기서 등록한 목록 중에서 고르는 방식으로 바꾼다
    # (skill_pool/tool_pool과 같은 "등록→선택" 패턴).


    # ---------- 임베딩 모델 설정(사용자 요청, 2026-10-26, "embedding
    # 모델 설정 기능을 구현해줘 (하드코딩 하지않도록)") ----------
    # `vectorstore.py`의 임베딩 provider/모델명이 코드 상수로 박혀 있던
    # 것("mxbai-embed-large:latest로 맞춰줘"처럼 바꾸려면 매번 소스를
    # 고쳐야 했다)을 admin이 직접 바꿀 수 있는 설정으로 뺀다. model_
    # registry(여러 개 등록해두고 고르는 카탈로그)와 달리 이건 "지금
    # 시스템이 실제로 쓰는 단 하나의 설정값"이라 GET/PUT 단일 리소스로
    # 둔다(목록·삭제 없음).


    # ---------- 팀(사용자 요청, 2026-10-06) ----------
    # "복수의 사람이 복수의 Agent와 또는 CrewAI 장점인 역할에 따른 Agent를
    # 묶어 생성된 그룹과 협업" — `docs/MULTI_PARTY_TEAM_COLLABORATION_
    # PLAN.md` Phase 1+2. skill_pool/tool_pool/model_registry와 같은
    # "등록→재사용" 패턴(`teams.py`)을 사람+Agent 혼합 그룹에 적용한다.


    # ---------- 파이프라인 빌더(사용자 요청 2026-10-18) ----------


    @app.get("/", response_class=HTMLResponse)
    def index():
        """Web UI 셸(코드 재점검 R4). 위 JSON API를 그대로 호출하는 단일
        페이지 바닐라 JS 앱이다 — 새 비즈니스 로직은 없다(webui.py)."""
        return webui.INDEX_HTML


    if not external_worker:
        job_queue.resume_queued(conn)  # 이전 서버가 기록만 하고 시작하지 못한 작업(2026-11-26, P1-6 2단계)
    return app
