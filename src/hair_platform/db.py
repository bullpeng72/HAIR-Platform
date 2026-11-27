"""SQLite 연결 및 스키마 — v4(전체) 범위까지 다룬다.

실시간 쓰기가 빈번한 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 쓴다.
`approvals.step`·`approver_id`로 다단계 결재라인(조직도 매니저 관계
기반 자동 라우팅)을 표현한다. `audit_log`는 트리거로 UPDATE/DELETE를 막아
append-only를 DB 레벨에서 강제한다. `human_pool.performance_consent`가
False이면 `performance_score`를 절대 채우지 않는다(사전 동의 기반 최소수집).

(2026-09-26): `agent_pool.framework`가 어떤 생성 경로(stub/
simple_llm/crewai)로 만들어졌는지 기록한다. `knowledge_chunks`는 내부지식검색
Agent(VectorDB)의 임베딩 저장소다 — 별도 벡터DB
서버 없이 SQLite에 임베딩을 JSON으로 저장하고, 검색은 애플리케이션에서
코사인 유사도로 계산한다(로컬/온프레미스 우선 원칙). 관계분석 Agent
(GraphDB)는 새 테이블이 필요 없다 — 이미 있는 `users.manager_id`·
`assignments`·`human_pool`을 그래프로 취급해 조회한다(`graphquery.py`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hair_platform import id_scheme, reference_pipeline_seeds


def get_connection(path: str | Path) -> sqlite3.Connection:
    """이 앱은 프로세스 전체가 이 커넥션 객체 하나를 공유한다(`app.state.db`,
    `app.py`). `sqlite3.threadsafety == 3`(SERIALIZED — 이 환경에서 직접
    확인됨)이라 여러 스레드가 동시에 같은 커넥션을 써도 SQLite 자체의
    뮤텍스가 호출을 직렬화해 데이터 손상 위험은 없다 — 다만 기본
    `busy_timeout`(0)에서는 잠금 경합 시 기다리지 않고 즉시
    `sqlite3.OperationalError: database is locked`를 던진다. 사용자
    요청(2026-11-03, "사내망을 통해 다른 사용자도 접속하도록 개선 방안을
    제안해줘" → "1번, 2번 진행")으로 사내망 다중 사용자 노출을 준비하며
    발견 — 앱 자신의 스레드 간 경합은 SQLite 뮤텍스가 이미 안전하게
    처리하지만, DB 파일을 직접 여는 외부 프로세스(백업 스크립트, `sqlite3`
    CLI로 직접 들여다보기 — 이번 세션에도 디버깅 중 실제로 이렇게 한 적
    있음)와 겹치면 그 즉시 실패가 뜰 수 있다. 짧게 기다렸다 재시도하도록
    busy_timeout을 켠다."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    manager_id INTEGER REFERENCES users(id)
);

-- `employee_id`(사용자 요청, 2026-10-27, "다른 UI에도 ID가 남아 있는지
-- 확인해줘" — "사람의 경우 ID는 향후 사번 적용 예정으로 8자리~12자리
-- 입력 및 표시 가능해야 함") — agent_pool/tool_pool/skill_pool의
-- `code`와 달리 "종류"를 구분하는 자동 배정 코드가 아니라, 관리자가
-- 실제 조직 사번을 직접 입력하는 필드라 이름과 생성 방식이 다르다
-- (id_scheme.py와 무관 — NULL 허용, 입력 전까지는 화면에서 "#id"로
-- 폴백). 숫자 8~12자리 검증은 models.py(HumanPoolCreate/Update)에서.
CREATE TABLE IF NOT EXISTS human_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    skill_tags TEXT NOT NULL DEFAULT '[]',
    availability TEXT NOT NULL DEFAULT 'available',
    cost_estimate REAL NOT NULL DEFAULT 0.0,
    manager_id INTEGER REFERENCES users(id),
    user_id INTEGER REFERENCES users(id),
    performance_consent INTEGER NOT NULL DEFAULT 0,
    performance_score REAL
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    raw_text TEXT NOT NULL,
    stub_roles TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'submitted'
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    role_name TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    headcount INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('backlog', 'queued', 'in_progress', 'done')),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent'))
);

-- (2026-10-08, Phase 4) — Task 간
-- 선행관계. `CHECK (task_id != depends_on_task_id)`로 자기참조만 DB
-- 레벨에서 막는다 — 간접 순환(A→B→C→A)까지는 CHECK로 못 막으므로
-- 추가 시점에 애플리케이션에서 DFS로 검사한다(deliverables.py
-- `_would_create_cycle()`, task 개수가 적어 성능 문제 없음).
CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id),
    CHECK (task_id != depends_on_task_id),
    UNIQUE(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    step INTEGER NOT NULL DEFAULT 1,
    approver_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    resource_type TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'requested',
    approval_id INTEGER REFERENCES approvals(id)
);

CREATE TABLE IF NOT EXISTS assignment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_by INTEGER NOT NULL REFERENCES users(id),
    reason TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL REFERENCES briefs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_by INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);

-- `code`(사용자 요청, 2026-10-27, "에이전트, 도구/skill ID 체계를
-- 수립하고(종류 구분 가능) 초기화하여 정리") — `id`(정수 PK)는 FK·URL
-- 경로가 그대로 쓰므로 안 바꾸고, 사람이 보고 종류를 바로 구분할 수
-- 있는 표시용 코드만 따로 둔다(예: "AGT-REF-001"). `id_scheme.py`
-- 참고 — tool_pool/skill_pool도 같은 패턴.
CREATE TABLE IF NOT EXISTS agent_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    role_name TEXT NOT NULL,
    skill_tags TEXT NOT NULL DEFAULT '[]',
    cost_estimate REAL NOT NULL DEFAULT 0.0,
    performance_score REAL,
    status TEXT NOT NULL DEFAULT 'pending_onboarding',
    source_job_item_id INTEGER REFERENCES job_items(id),
    llm_provider TEXT,
    llm_model TEXT,
    last_output TEXT,
    framework TEXT NOT NULL DEFAULT 'stub',
    mcp_servers TEXT NOT NULL DEFAULT '[]',
    name TEXT,
    tuning_config TEXT NOT NULL DEFAULT '{}'
);

-- 사용자 요청(2026-10-06, "복수의 사람이 복수의 Agent와 또는 CrewAI
-- 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록... 다시
-- 기획") — Phase 1+2.
-- human_pool/agent_pool 멤버를 역할명과 함께 묶어 여러 업무에 재사용
-- 가능한 "팀"으로 등록한다(skill_pool/tool_pool과 같은 "등록→재사용"
-- 패턴). 사람/Agent 풀 자체는 그대로 두고 참조만 한다.
-- `pipeline_template_id`(2026-10-28, 사용자 요청 "자율 개발 크루'의
-- 경우 파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수
-- 있도록 재구성") — 연결돼 있으면 `@팀이름` 멘션이 `crew_runtime.
-- run_crew()` 단발 실행 대신 그 파이프라인(Gate 기준 자동 재시도·
-- 사람 승인 에스컬레이션까지 갖춘 엔진)을 탄다. NULL이면 기존 단발
-- 실행 그대로(하위 호환) — `conversations.run_team_mention_job()`
-- 참고.
-- `status`/`task_id`/`brief_id`/`completed_at`(2026-10-29, 사용자 요청
-- "TF팀을 teams의 확장으로") — `task_id`/`brief_id`가 주어지면 그
-- 업무에 종속된 TF팀(생성 즉시 `pending_onboarding`), 안 주어지면
-- 기존처럼 재사용 가능한 범용 팀("자율 개발 크루" 등, 생성 즉시
-- `active`) — `teams.create_team()` 참고. `is_active`는 `status`의
-- 거울(`status=='active' ⟺ is_active==1`)로 계속 유지한다 — 기존에
-- `is_active`만 읽는 지점들(`conversations.py`의 팀 @멘션 검색,
-- `webui.py`의 Task 배정 드롭다운)이 코드 변경 없이 그대로 올바르게
-- 동작하게 하기 위함. `task_id`/`brief_id`에 FK를 안 거는 이유는
-- `assignments.resource_id`와 같다 — task/brief 삭제 시 선행 처리를
-- 늘리지 않기 위해서다(UI에서 LEFT JOIN으로 느슨하게 처리). 컬럼
-- 기본값은 `'active'`(= `is_active` 기본값 1과 맞춤) — `create_team()`
-- 은 항상 두 값을 명시적으로 같이 넣어 이 기본값을 안 쓰지만,
-- `name`만 주는 단순 raw INSERT(테스트 픽스처 등)가 두 컬럼을
-- 서로 어긋나게 두지 않기 위한 안전장치다. 기존 DB 마이그레이션은
-- 백필 판별을 위해 일부러 다른 기본값(`'pending_onboarding'`)을
-- 쓴다 — 아래 `_migrate_schema()` 참고.
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    pipeline_template_id INTEGER REFERENCES pipeline_templates(id),
    status TEXT NOT NULL DEFAULT 'active',
    task_id INTEGER,
    brief_id INTEGER,
    completed_at TEXT
);

-- `idx_teams_task_id`/`idx_teams_brief_id`는 여기서 안 만든다 — 기존
-- DB의 `teams`는 이 시점에 아직 `task_id`/`brief_id` 컬럼이 없어
-- (`CREATE TABLE IF NOT EXISTS`가 no-op이라 컬럼이 안 생김) 인덱스
-- 생성이 바로 실패한다(실측). `_migrate_schema()`가 `_ensure_column`
-- 으로 컬럼을 추가한 뒤 같은 인덱스를 만든다 — 신규 DB도 그 시점에
-- 문제없이 만들어지므로(컬럼이 이미 있어 `_ensure_column`이 no-op)
-- 여기 두는 건 중복일 뿐 아니라 기존 DB에서 깨진다.

CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    member_type TEXT NOT NULL CHECK (member_type IN ('human', 'agent')),
    member_id INTEGER NOT NULL,
    role_label TEXT NOT NULL DEFAULT '',
    UNIQUE(team_id, member_type, member_id)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id INTEGER REFERENCES agent_pool(id),
    -- 사용자 요청(2026-10-26, 외부 자료 업로드) — `source_agent_id`와
    -- 별개 출처. 하나는 "이 청크가 어느 Agent의 산출물이었는지", 이건
    -- "이 청크가 어느 업로드 문서(`documents`)에서 나왔는지"를 가리킨다.
    source_document_id INTEGER REFERENCES documents(id),
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 사용자 요청(2026-11-18, "대화에서 업무처리시... 메모리를 활용하는
-- 방안 등을 고려해 엔터프라이즈 환경 적용이 가능한 수준의 확실한
-- 개선 방안을 도출해줘" 개선축 C) — `knowledge_chunks`(전역, 영구
-- VectorDB)와 의도적으로 분리한 대화 전용 임시 색인. 2026-10-26
-- 결정("대화에 주입되는 문서는 저장되지 않고 휘발되는 내용물")을
-- 보존한다 — `conversation_id`로 스코프돼 그 대화가 지워지면(`_cascade_
-- delete_conversation()`) 같이 지워지고, 다른 대화의 검색에는 전혀
-- 안 걸린다. 거대한 첨부파일(2026-11-18 사고: 19KB 하나가 로컬 모델
-- 컨텍스트 한도를 넘김)을 전문 그대로 메시지에 박는 대신 청크+임베딩
-- 해뒀다가, 질문이 올 때마다 관련 있는 청크만 꺼내 쓴다.
CREATE TABLE IF NOT EXISTS conversation_knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    source_message_id INTEGER REFERENCES conversation_messages(id),
    filename TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conversation_knowledge_chunks_conv
    ON conversation_knowledge_chunks(conversation_id);

CREATE TABLE IF NOT EXISTS tool_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
    config TEXT NOT NULL DEFAULT '{}',
    tool_names TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    category TEXT NOT NULL DEFAULT 'general' CHECK (category IN ('sw_dev', 'general'))
);

CREATE TABLE IF NOT EXISTS skill_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    default_tool_pool_ids TEXT NOT NULL DEFAULT '[]',
    augmentation_mode TEXT NOT NULL DEFAULT 'tool'
        CHECK (augmentation_mode IN ('tool', 'always', 'none')),
    show_in_conversation INTEGER NOT NULL DEFAULT 1,
    category TEXT NOT NULL DEFAULT 'general' CHECK (category IN ('sw_dev', 'general'))
);

-- `code` 컬럼의 UNIQUE 인덱스는 여기(정적 SCHEMA)가 아니라
-- `_migrate_schema()`에서 만든다 — 기존 DB는 `executescript(SCHEMA)`
-- 시점엔 아직 `code` 컬럼이 없어(`_ensure_column()`으로 나중에 추가)
-- 여기 두면 "no such column: code"로 바로 깨진다(실측 확인).

-- 사용자 요청(2026-10-06, "운영 관리에 사용 가능한 ollama 모델 및
-- openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을 불러오는
-- 방식으로 UI/UX를 개선") — 지금까지 "모델명" 입력란이 전부 자유
-- 텍스트라, 실제로 쓸 수 있는 모델이 뭔지 모른 채 타이핑해야 했다
-- (ollama의 기본값 "llama3.1"이 로컬에 없어 404가 난 사고가 실제
-- 선례). skill_pool/tool_pool과 같은 패턴으로 "등록→드롭다운에서
-- 선택" 구조를 모델명에도 적용한다.
-- `optimization_tiers`(사용자 요청, 2026-10-26, "모델 레지스트리에
-- llm 설정 최적화 각 tier에 적용되는 모델을 매핑하는 기능을 제공" —
-- 1차로 premium/balanced/economy "등급" 간접 매핑을 만들었다가, 사용자가
-- "tier 직접 매핑으로 변경"을 선택해 다시 설계) — `agent_optimizer.py`의
-- tier(deep_reasoning/precision_fast/standard) 이름 자체에 모델을 직접
-- 매핑한다(JSON 배열, 하나의 모델이 여러 tier에 적용될 수 있다).
-- '[]'(기본값)면 그 모델은 어떤 tier에도 명시적으로 매핑되지 않은
-- 상태 — `agent_optimizer.recommend_model()`이 이 매핑이 있는 모델을
-- 패턴 추정보다 항상 먼저 후보로 올리고, 특정 tier에 매핑된 모델이
-- 하나도 없으면 그 tier는 기존처럼 이름 패턴 추정으로 폴백한다(하위
-- 호환). 새 모델 출시·provider 교체 시 코드를 고치지 않고 이 매핑만
-- 바꾸면 되고, 몇 번이든 다시 바꿀 수 있다.
-- `context_window`(사용자 요청, 2026-11-18, "Skill, tool 사용 제약을
-- 없애고, 메모리를 활용하는 방안 등을 고려해 엔터프라이즈 환경 적용이
-- 가능한 수준의 확실한 개선 방안을 도출해줘" 개선축 B) — 이 모델이
-- 실제로 받아들이는 토큰 한도. NULL(기본값)이면 `token_budget.
-- DEFAULT_CONTEXT_WINDOW_BY_PROVIDER`의 provider별 보수적 기본값으로
-- 폴백한다 — Ollama는 로컬 서버 실행 플래그(`-c 32768` 등)로 결정되는
-- 값이라 DB가 자동으로 알 수 없으므로 admin이 "모델 레지스트리"
-- 화면에서 직접 등록해야 정확해진다.
CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'ollama')),
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    optimization_tiers TEXT NOT NULL DEFAULT '[]',
    context_window INTEGER,
    UNIQUE(provider, name)
);

-- 사용자 요청(2026-11-12, "ollama, openai 등 프로바이더의 모델 성능
-- Test하고 결과 표시, 티어 추천 기능") — `model_registry.py` 참고.
-- append-only 이력(agent_evaluations/crew_runs와 같은 패턴) — 같은
-- 모델을 여러 번 테스트해도 지우지 않고 쌓는다(모델 서빙 환경이
-- 바뀌면 — 예: 다른 모델이 동시에 메모리에 로드돼 있었는지 — 같은
-- 모델도 결과가 달라질 수 있어, 과거 값을 덮어쓰면 "왜 지난번과 다른
-- 결과가 나왔는지" 추적할 수 없게 된다).
CREATE TABLE IF NOT EXISTS model_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_registry_id INTEGER NOT NULL REFERENCES model_registry(id),
    tested_by INTEGER NOT NULL REFERENCES users(id),
    tested_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    latency_seconds REAL,
    output_chars INTEGER,
    chars_per_second REAL,
    recommended_tier TEXT,
    error TEXT
);

-- 사용자 요청(2026-10-26, "embedding 모델 설정 기능을 구현해줘 (하드코딩
-- 하지않도록)") — `vectorstore.py`의 임베딩 provider/모델명이 지금까지
-- `DEFAULT_EMBEDDING_MODELS` 코드 상수로 박혀 있어("mxbai-embed-large:
-- latest로 맞춰줘"처럼 바꾸려면 매번 소스를 고쳐야 했다), admin이 직접
-- 바꿀 수 있는 설정값으로 뺀다. 한 시스템에 임베딩 공간은 하나만 둬야
-- 서로 다른 모델로 만든 벡터를 섞어 비교하지 않는다(vectorstore.py
-- 자체 설계 원칙) — 그래서 Agent별 설정이 아니라 `id=1` 고정 단일
-- 행(싱글턴)이다. 행이 아예 없으면(= 한 번도 설정 안 함) 기존 자동판별
-- (OPENAI_API_KEY 유무 → 없으면 ollama)+하드코딩 기본 모델로 그대로
-- 폴백한다(동작 안 바뀜, 설정하면 그게 우선).
CREATE TABLE IF NOT EXISTS embedding_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL CHECK (provider IN ('openai', 'ollama')),
    model TEXT NOT NULL
);

-- 사용자 요청(2026-11-02, "LLM 설정에 HAIR-Platform 공통/기본 동작을
-- 위한 LLM을 지정할 수 있게") — `llm.resolve_provider_and_model()`이
-- 지금까지 명시 인자 → 환경변수 → API 키 자동판별 순으로만 provider/
-- model을 골랐다. admin이 "플랫폼 공통 기본 LLM"을 지정해두면 그게
-- 환경변수보다 먼저 적용되도록 하는 단일 설정 행이다 — embedding_config
-- 와 똑같은 `id=1` 싱글턴 패턴이되, 여긴 임베딩 전용 제약(openai/ollama
-- 만)과 달리 Anthropic도 포함한 3종 provider를 전부 받는다.
CREATE TABLE IF NOT EXISTS platform_llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'ollama')),
    model TEXT NOT NULL
);

-- 보조 작업 모델(2026-11-24, P0-2 응답 속도) — 언어 교정·수치 문장 교정처럼 짧고 기계적인
-- 보조 호출만 따로 보낼 모델. 비어 있으면 예전처럼 본 작업과 같은 모델을 쓴다. 로컬 대형
-- 모델 하나를 모든 호출이 순서대로 기다려, 1초짜리 교정이 앞 요청 뒤에서 타임아웃 나던 문제.
CREATE TABLE IF NOT EXISTS auxiliary_llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'ollama')),
    model TEXT NOT NULL
);

-- 사용자 요청(2026-11-03, "RAG 검색 품질 향상... 우선순위를 정해
-- 진행해줘") — `vectorstore.search()`가 지금까지 관련성 임계값 없이
-- top_k(모든 호출부에 3으로 하드코딩)만큼 무조건 반환했다(무관한
-- 결과도 그대로 섞여 나옴). embedding_config와 같은 id=1 싱글턴
-- 패턴으로 admin이 top_k/min_score(코사인 유사도 하한)를 조정할 수
-- 있게 뺀다. 설정 안 하면(행 없음) 기존 top_k=3 + 신규 기본
-- min_score=0.2로 폴백한다(vectorstore.DEFAULT_RAG_TOP_K/
-- DEFAULT_RAG_MIN_SCORE 참고).
CREATE TABLE IF NOT EXISTS rag_search_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    top_k INTEGER NOT NULL CHECK (top_k >= 1),
    min_score REAL NOT NULL CHECK (min_score >= 0.0 AND min_score <= 1.0)
);

CREATE TABLE IF NOT EXISTS agent_tool_links (
    agent_id INTEGER NOT NULL REFERENCES agent_pool(id),
    tool_pool_id INTEGER NOT NULL REFERENCES tool_pool(id),
    PRIMARY KEY (agent_id, tool_pool_id)
);

CREATE TABLE IF NOT EXISTS deliverables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text'
        CHECK (content_type IN ('text', 'markdown', 'html', 'code')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'revised')),
    revises_id INTEGER REFERENCES deliverables(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deliverable_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deliverables_task_id ON deliverables(task_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_comments_deliverable_id
    ON deliverable_comments(deliverable_id);

-- (2026-10-08, 사용자 요청 "ASANA 모방
-- 수준의 기능 및 UI 제공을 위한 기획 및 설계안을 제안해줘" → "Phase
-- 1→5 순서로" 진행 지시, Phase 2) — "초대"는 결재가 필요 없는 가벼운
-- 멤버십이다. `assignments`(결재 필요, 작업 책임 발생)와 명확히
-- 분리한다. `participant_id`는 `participant_type='human'`이면
-- `human_pool.id`가 아니라 **`users.id`**다(`deliverable_comments.
-- author_id`/`audit_log.actor_id` 관례와 통일 — 초대는 human_pool
-- 리소스 매칭이 아니라 "이 로그인 계정을 대화에 부른다"는 개념이라
-- human_pool을 거칠 이유가 없다). `'agent'`면 `agent_pool.id`.
CREATE TABLE IF NOT EXISTS task_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human', 'agent')),
    participant_id INTEGER NOT NULL,
    invited_by INTEGER NOT NULL REFERENCES users(id),
    invited_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, participant_type, participant_id)
);

-- (2026-10-08, 사용자 요청 "로그인하면
-- 기본적으로 대화창이 주어지고... 사람이나 에이전트(에이전트 그룹)를
-- 호출하거나 skill을 사용하여 업무를 수행하는 방식으로 UX를 개선") —
-- "대화"는 Task가 있어야만 존재하는 게 아니다. linked_task_id가
-- 채워지면 그 Task 전용 대화, NULL이면
-- 로그인 직후 바로 쓰는 독립된 "내 대화"다 — 하나의 모델로 둘 다
-- 표현한다. llm_provider/llm_model은 agent_pool과 같은 관례로 NULL=
-- 플랫폼 기본값(`llm.resolve_provider_and_model()`), ai_auto_reply
-- (사용자 결정: 기본 켜짐)는 멘션 없는 평범한 메시지에도 이 설정으로
-- 자동 응답할지를 결정한다.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    linked_task_id INTEGER REFERENCES tasks(id),
    llm_provider TEXT,
    llm_model TEXT,
    ai_auto_reply INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 사용자 요청(2026-10-14, "10건 이전의 메시지는 요약 형태로 전달되도록
    -- 개선해줘") — `_build_transcript()`가 최근 10건을 넘는 과거를 그냥
    -- 버리던 것을, 캐시된 요약으로 대체한다. `context_summary_through_id`
    -- 는 이 요약이 몇 번째 메시지까지 반영했는지 가리킨다(증분 갱신용 —
    -- 매번 전체를 다시 요약하지 않고 새로 창 밖으로 밀려난 메시지만 추가
    -- 요약한다).
    context_summary TEXT,
    context_summary_through_id INTEGER
);

-- task_participants와 같은 모양이지만 'team'(에이전트 그룹)을
-- participant_type에 추가한다 — "에이전트 그룹 호출"을 표현하려면
-- 꼭 필요하다.
CREATE TABLE IF NOT EXISTS conversation_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human', 'agent', 'team')),
    participant_id INTEGER NOT NULL,
    invited_by INTEGER NOT NULL REFERENCES users(id),
    invited_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(conversation_id, participant_type, participant_id)
);

-- used_skill(사용자 결정: "사용자가 skill을 조회해 대화창에서 모델에
-- 일을 시킬 수 있음")은 이 메시지가 어떤 스킬로 그라운딩된 요청이었는지
-- 화면에 보여주기 위한 출처 표시일 뿐, FK가 아니다(skill_pool에서
-- 지워져도 과거 메시지 기록은 그대로 남아야 한다).
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    used_skill TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- agent_execution_jobs(Task 전용)와 같은 역할이지만 대화는 Task와
-- 무관하게 여러 멘션(Agent 여러 명 + Team 여러 개 + 기본 AI)이 동시에
-- 트리거될 수 있어 "task당 1개만 running" 같은 가드를 두지 않는다 —
-- 각 트리거가 독립된 job 행을 갖고 병렬로 끝난다(실제 그룹 채팅에서
-- 여러 봇이 동시에 응답하는 것과 같은 모델).
CREATE TABLE IF NOT EXISTS conversation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    trigger_type TEXT NOT NULL CHECK (
        trigger_type IN ('auto_reply', 'skill', 'agent_mention', 'team_mention')
    ),
    agent_id INTEGER REFERENCES agent_pool(id),
    team_id INTEGER REFERENCES teams(id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversation_participants_conversation_id
    ON conversation_participants(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id
    ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_jobs_conversation_id
    ON conversation_jobs(conversation_id);

-- 사용자 요청(2026-10-26, "대화창 및 에이전트로 구성된 업무에서 외부
-- 자료(마크다운, pdf, html)를 입력받을 수 있도록") — 업로드한 파일의
-- 메타데이터만 여기 저장한다. 실제 텍스트/임베딩은 `knowledge_chunks`
-- (아래 `source_document_id`)에 청크 단위로 쪼개져 들어간다 — VectorDB
-- 검색은 전역이라(`vectorstore.search()`가 `source_agent_id`로 필터링
--하지 않는 것과 같은 원칙), `conversation_id`는 "어디서 올렸는지"
-- 추적용일 뿐 검색 범위를 좁히지 않는다.
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    conversation_id INTEGER REFERENCES conversations(id),
    char_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 사용자 요청(2026-11-20, 지식베이스 등록 체계 개편) — 'direct'(admin
    -- 직접 등록) | 'submission'(제출 → admin 결재 → 등록). 후자는
    -- `submission_id`로 원 제출 건(출처·제출자·승인자)을 가리킨다.
    source_type TEXT NOT NULL DEFAULT 'direct',
    submission_id INTEGER REFERENCES knowledge_submissions(id)
);

-- 사용자 요청(2026-11-20, "지식 베이스는 admin 담당자가 문서로 등록하는
-- 경우와 대화 및 업무배정을 통해 생성된 산출물 중 '지식베이스로 보내기'
-- 및 admin 결재 후 등록되는 것으로 개선") — 경로 B의 제출 건.
-- `content_snapshot`은 제출 시점에 전처리(펜스 제거·HTML 본문 추출)까지
-- 마친 **등록될 텍스트 그 자체**다 — admin이 결재 화면에서 보는 내용과
-- 실제로 색인되는 내용이 같고, 원본(대화 메시지/산출물)이 나중에
-- 수정·삭제돼도 지식은 영향받지 않는다(그래서 `source_id`는 FK가 아님).
CREATE TABLE IF NOT EXISTS knowledge_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('conversation_message', 'deliverable')),
    source_id INTEGER NOT NULL,
    conversation_id INTEGER,
    task_id INTEGER,
    source_label TEXT NOT NULL,
    title TEXT NOT NULL,
    note TEXT,
    content_snapshot TEXT NOT NULL,
    content_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'registering', 'registered', 'rejected',
                          'withdrawn', 'failed', 'superseded', 'removed')),
    supersedes_id INTEGER REFERENCES knowledge_submissions(id),
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    reject_reason TEXT,
    error TEXT,
    document_id INTEGER REFERENCES documents(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_submissions_source
    ON knowledge_submissions(source_type, source_id);

CREATE TABLE IF NOT EXISTS agent_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
    gate_scores TEXT NOT NULL,
    tcr REAL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_evaluations_deliverable_id
    ON agent_evaluations(deliverable_id);

CREATE TABLE IF NOT EXISTS agent_execution_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    deliverable_id INTEGER REFERENCES deliverables(id),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    prompt_snapshot TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_jobs_task_id
    ON agent_execution_jobs(task_id);

-- 사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해... 개발
-- Agent 팀을 구성하고... 단계별 단독 또는 복수의 에이전트가 순차/병행
-- 처리하며... 다른 업무에도 적용될 수 있도록 일반화") — 단계(stage) 1개
-- 는 "역할 1명(single)" 또는 "역할 N명이 순서대로 이어받아 처리
-- (sequential_relay)" 또는 "리드 1명이 코워커 N명에게 실제로 위임
-- (parallel_delegated, crew_runtime.run_crew()의 hierarchical_with_manager
-- 경로 재사용)" 중 하나다. 이 엔진은 SW 개발인지 시장조사-전략인지
-- 전혀 모른다(역할 이름만 참조) — 그래서 같은 스키마로 다른 업무
-- 파이프라인도 그대로 만들 수 있다(범용화).
CREATE TABLE IF NOT EXISTS pipeline_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT 'general',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_template_id INTEGER NOT NULL REFERENCES pipeline_templates(id),
    seq_order INTEGER NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'single'
        CHECK (mode IN ('single', 'sequential_relay', 'parallel_delegated', 'plan_merge')),
    lead_role_name TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    gate_ids TEXT NOT NULL DEFAULT '["A","B","C","D","E","F","G"]',
    min_gate_pass_ratio REAL NOT NULL DEFAULT 0.0,
    max_retry INTEGER NOT NULL DEFAULT 2,
    requires_human_approval INTEGER NOT NULL DEFAULT 0,
    -- 사용자 보고(2026-10-19, "Todo 앱의 최종 결과물에 코드가 포함돼야
    -- 하는데 품질 리포트만 있다") 원인 분석 — Task 협업 워크스페이스의
    -- "최종 결과물"은 지금까지 "가장 최근에 approved된 deliverable"을
    -- 그대로 집었다. 파이프라인 단계는 승인 불필요 단계가 전부 자동
    -- approved로 쌓이는데, 마지막 단계(코드 검증/QA/품질 판정류)가
    -- 시간상 항상 가장 나중이라 "검증 결과 텍스트"가 "진짜 산출물"
    -- (예: 구현 단계의 코드)을 밀어내고 최종 결과물 자리를 차지했다.
    -- 어느 단계의 산출물이 "진짜 최종 산출물"인지는 템플릿 작성자만
    -- 알 수 있어(코드/기획서/보고서 등 도메인마다 다름) 단계별로
    -- 명시적으로 표시해둔다.
    is_primary_deliverable INTEGER NOT NULL DEFAULT 0,
    -- 사용자 요청(2026-11-08, "게임이 아닌 일상 사무 업무도 일반화") —
    -- 기존엔 `pipeline_runtime.py`가 `stage["name"] == "코드 검증"`
    -- 문자열 매칭으로 요구사항 추적성 LLM judge를 걸었다. 다른 도메인
    -- 파이프라인(사업기획서 등)이 이 검증 단계를 다른 이름으로 지어도
    -- 받을 수 있도록, 단계명과 무관한 명시적 플래그로 뺀다.
    checks_requirements_traceability INTEGER NOT NULL DEFAULT 0,
    -- 사용자 요청(2026-11-10, "코드성격의 결과물과, 리포트, 프레젠테이션
    -- 결과물 등 결과물 출력 유형 다양화") — 파이프라인 경로는 지금까지
    -- `deliverables.content_type`을 항상 'text'로 하드코딩했다(단계가
    -- 실제로 코드를 생성해도 코드 하이라이팅 없이 밋밋하게 렌더됨).
    -- 어느 단계가 코드/마크다운/HTML을 만드는지는 템플릿 작성자만 알 수
    -- 있어(도메인마다 다름) is_primary_deliverable과 같은 패턴으로
    -- 단계별 플래그를 둔다. 'html'로 지정한 단계는 기존 html 렌더러
    -- (sandbox iframe, 스크립트 실행 허용)가 이미 reveal.js 같은
    -- 자체완결형 프레젠테이션 HTML도 그대로 렌더하므로, 새 content_type
    -- 값("presentation" 등)이나 DB 마이그레이션 없이 "프레젠테이션"
    -- 산출물까지 커버된다.
    output_content_type TEXT NOT NULL DEFAULT 'text'
        CHECK (output_content_type IN ('text', 'markdown', 'html', 'code')),
    UNIQUE(pipeline_template_id, seq_order)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_stages_template_id
    ON pipeline_stages(pipeline_template_id);

CREATE TABLE IF NOT EXISTS pipeline_stage_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id),
    role_name TEXT NOT NULL,
    seq_order INTEGER NOT NULL DEFAULT 1,
    instructions TEXT NOT NULL DEFAULT '',
    UNIQUE(stage_id, role_name)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_stage_agents_stage_id
    ON pipeline_stage_agents(stage_id);

-- `source_conversation_id`(2026-10-28, "자율 개발 크루" 파이프라인화) —
-- 대화 `@팀이름` 멘션이 띄운 run이면 채워진다. 승인 후 재개돼 다음
-- 단계가 끝나거나 전체가 완료/실패할 때 어느 대화에 진행 메시지를
-- 올릴지 이 컬럼으로 안다(`conversations.post_pipeline_run_status_
-- message()`). Task 협업 워크스페이스에서 직접 띄운 run은 NULL.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_template_id INTEGER NOT NULL REFERENCES pipeline_templates(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'paused_for_approval', 'completed', 'failed', 'cancelled')),
    current_stage_seq INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    source_conversation_id INTEGER REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_task_id
    ON pipeline_runs(task_id);

-- `decision_reason`(2026-10-28, 사용자 요청 "반려 사유를 재시도
-- 지시문에 직접 끼워 넣는 식으로 개선") — 사람이 결재함에서 반려할 때
-- 적는 사유(`ApprovalDecision.reason`)를 지금까지 `audit_log`에만
-- 남기고 재시도 프롬프트엔 전혀 안 넣었다. Gate 실패 재시도는 이미
-- `gate_scores` 기반 피드백을 넣어주는데, 사람이 반려한 단계는 애초에
-- Gate를 통과했길래 사람 승인까지 온 것이라(`_gate_failure_summary()`
-- 가 "세부 사유 없음"만 돌려줌) 사람이 적은 진짜 반려 이유가 재시도에
-- 전혀 반영되지 않는 공백이 있었다.
CREATE TABLE IF NOT EXISTS pipeline_run_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id INTEGER NOT NULL REFERENCES pipeline_runs(id),
    stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id),
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'passed', 'failed_retrying', 'escalated', 'approved', 'rejected')),
    output TEXT,
    gate_scores TEXT,
    tcr REAL,
    deliverable_id INTEGER REFERENCES deliverables(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    decision_reason TEXT
);

-- 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고... Crew 빌더"
-- 로드맵 3단계, "실행 이력 + 진행 가시성") — 파이프라인 실행
-- (pipeline_runs)만 DB에 남고, 팀 Crew 실행·즉석 Crew 실행은 실행
-- 즉시 흩어진 deliverables/agent_execution_jobs 행으로만 남아 "이
-- Crew가 언제 누구랑 무슨 설정으로 돌았는지, 협업 다이어그램·토큰
-- 사용량이 어땠는지"를 한 번에 볼 방법이 없었다(admin 전용 즉석
-- 테스트 샌드박스에서만 실행 직후 한 번 보여주고 사라짐). 이 테이블이
-- 팀 Crew·즉석 Crew 실행 둘 다 영구 기록한다 — `kind`로 어느 경로인지
-- 구분한다('team'은 team_id가 채워짐, 'adhoc'은 NULL).
-- `progress`는 CrewAI `task_callback`이 Task 완료마다 추가하는
-- {agent_role, output_preview} 배열(JSON) — 실행 중에도 폴링으로
-- "N명 중 M명 완료"를 보여줄 수 있게 미리 기록해둔다.
CREATE TABLE IF NOT EXISTS crew_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL CHECK (kind IN ('team', 'adhoc')),
    team_id INTEGER REFERENCES teams(id),
    requested_by INTEGER NOT NULL REFERENCES users(id),
    agent_ids TEXT NOT NULL DEFAULT '[]',
    process TEXT NOT NULL,
    manager_agent_id INTEGER REFERENCES agent_pool(id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    progress TEXT NOT NULL DEFAULT '[]',
    gate_scores TEXT,
    tcr REAL,
    token_usage TEXT,
    delegations TEXT,
    agent_outputs TEXT,
    final_output TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_crew_runs_task_id ON crew_runs(task_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_stages_run_id
    ON pipeline_run_stages(pipeline_run_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id INTEGER,
    before_value TEXT,
    after_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""

# 마이그레이션·시드는 별도 파일로 나눴다(2026-11-27, 개선 제안 6-2). 예전 이름(`db._run_once` 등)은 그대로 쓸 수 있게 다시 내보낸다.
from hair_platform.db_migrations import (  # noqa: E402, F401
    _column_exists,
    _ensure_column,
    _migrate_tool_pool_custom_python_kind,
    _backfill_conversation_last_read,
    _GUIDE_SUFFIX_SKILL_RENAMES,
    _FUNCTIONAL_SKILL_RENAMES,
    _FUNCTIONAL_SKILL_DESCRIPTIONS,
    _rename_guide_suffixed_skills,
    _SKILL_CONTEXT_POLICY_DEFAULTS,
    _backfill_skill_context_policy,
    _OLD_STAGE_MODE_CHECK,
    _NEW_STAGE_MODE_CHECK,
    _migrate_parallel_delegated_to_plan_merge,
    _migrate_pipeline_stage_plan_merge_mode,
    _migrate_schema,
    _DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES,
    _DEV_PIPELINE_STALE_DESCRIPTION,
    _DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS,
    _DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS_V2,
    _DEV_PIPELINE_STALE_PLANNING_INSTRUCTIONS,
    _sync_dev_pipeline_implementation_stage_agents,
    _DEV_PIPELINE_GATED_STAGE_NAMES,
    _DEV_PIPELINE_GATE_PASS_RATIO,
    _DEV_CREW_CLOUD_PROVIDER,
    _DEV_CREW_CLOUD_MODEL,
    _DEV_CREW_LEGACY_ROLE_LABEL_MIGRATIONS,
    _sync_dev_crew_pipeline_integration,
    _backfill_show_in_conversation,
    _SW_DEV_CATEGORY_SKILL_NAMES,
    _SW_DEV_CATEGORY_TOOL_NAMES,
    _RECLASSIFIED_SW_DEV_SKILLS_2026_11_23,
    _RECLASSIFIED_SW_DEV_TOOLS_2026_11_23,
    _reclassify_coverage_check_sw_dev,
    _backfill_skill_tool_category,
    _backfill_skill_structured_fields,
    _apply_skill_catalog,
    _apply_skill_includes,
    _seed_presentation_skill,
    _BUSINESS_STAGE_REASSIGNMENTS,
    _READ_ONLY_BUILTIN_MCP,
    _LIGHT_ROLE_OLD_TUNING,
    _light_roles_reasoning_off,
    _mark_unknown_mcp_side_effects,
    _set_domain_required_tools,
    _reassign_business_pipeline_agents,
    _FULL_GATE_IDS,
    _review_pipeline_quality_settings,
    _require_traceability_to_pass,
    _single_author_runnable_implementation,
    _review_returns_to_primary_stage,
    _upgrade_chart_generator_tool,
    _add_tool_input_formats,
    _show_custom_tool_skills_in_conversation,
    _add_tool_input_schemas,
    _run_once,
    _apply_one_time_agent_skill_additions,
    _seed_catalog_skills,
    _backfill_data_visualization_feature,
    _NATIVE_TOOL_RENAMES,
    _tool_is_referenced,
    _rename_native_tools,
)
from hair_platform.db_seed import _seed_default_pools  # noqa: E402, F401


def init_schema(conn: sqlite3.Connection) -> None:
    # 사용자 요청(2026-10-22, "skill에 대화창 표시 속성 추가, 가능한
    # 것들만 표시되도록 개선") — `show_in_conversation`이 이번 호출에서
    # 처음 생기는지를 스키마/시드 전에 미리 기록해둔다. 시드(아래
    # `_seed_default_pools()`/`reference_pipeline_seeds.seed()`)가 전부
    # 끝난 뒤에만 백필을 돌려야 한다 — 기존 Agent/Human처럼 skill_pool도
    # 신규 DB에서는 이 두 호출이 실제로 행을 채우는 지점이라, 그 전에
    # 백필하면(이전에 `_migrate_schema()` 안에서 돌렸을 때 실측된 버그)
    # 아직 빈 테이블이라 아무 것도 못 끈다.
    is_new_show_in_conversation_column = not _column_exists(
        conn, "skill_pool", "show_in_conversation"
    )
    # 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로
    # 구분... 개선") — 위 show_in_conversation과 같은 이유로 시드 전에
    # 미리 기록해둔다(skill_pool/tool_pool 둘 다 같은 시점에 추가되는
    # 쌍이라 하나만 확인해도 충분 — `_migrate_schema()`가 둘을 항상
    # 같이 `_ensure_column()`한다).
    is_new_category_column = not _column_exists(conn, "skill_pool", "category")
    is_new_output_format_column = not _column_exists(conn, "skill_pool", "output_format")
    is_new_history_mode_column = not _column_exists(conn, "skill_pool", "history_mode")
    is_new_includes_column = not _column_exists(conn, "skill_pool", "includes")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    _seed_default_pools(conn)
    # 사용자 요청(2026-10-14, "기본 배포에 코드 및 Skill, Tool, Agent를
    # 포함해줘") — Harness Methodology 개발 파이프라인(6)·경영/사업
    # 전략 파이프라인(6) Agent 12개와 그 전용 Tool/Skill을 매 기동마다
    # 멱등하게(이미 있으면 건너뜀) 재현한다.
    reference_pipeline_seeds.seed(conn)
    if is_new_show_in_conversation_column:
        _backfill_show_in_conversation(conn)
    if is_new_category_column:
        _backfill_skill_tool_category(conn)
    if is_new_output_format_column:
        _backfill_skill_structured_fields(conn)
    if is_new_history_mode_column:
        _backfill_skill_context_policy(conn)
    _backfill_data_visualization_feature(conn)
    _seed_presentation_skill(conn)
    _seed_catalog_skills(conn)
    _apply_one_time_agent_skill_additions(conn)
    _upgrade_chart_generator_tool(conn)
    _run_once(conn, "2026-11-21-business-pipeline-agents", _reassign_business_pipeline_agents)
    _set_domain_required_tools(conn)  # 업무 영역 템플릿 단계의 필수 도구(2026-11-26, 개선 계획 1-2)
    _run_once(conn, "2026-11-26-mcp-side-effects", _mark_unknown_mcp_side_effects)
    _run_once(conn, "2026-11-26-light-roles-reasoning-off", _light_roles_reasoning_off)
    _run_once(conn, "2026-11-21-pipeline-quality-settings", _review_pipeline_quality_settings)
    _run_once(conn, "2026-11-21-traceability-must-pass", _require_traceability_to_pass)
    _run_once(conn, "2026-11-22-runnable-implementation", _single_author_runnable_implementation)
    _run_once(conn, "2026-11-22-review-returns-to-writing", _review_returns_to_primary_stage)
    _run_once(conn, "2026-11-23-coverage-check-sw-dev", _reclassify_coverage_check_sw_dev)
    _run_once(conn, "2026-11-22-tool-input-formats", _add_tool_input_formats)
    _run_once(conn, "2026-11-23-show-custom-tool-skills", _show_custom_tool_skills_in_conversation)
    _run_once(conn, "2026-11-23-tool-input-schemas", _add_tool_input_schemas)
    _apply_skill_catalog(conn)
    if is_new_includes_column:
        _apply_skill_includes(conn)
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 시드가 전부 끝난 뒤에만
    # 돌려야 한다(위 show_in_conversation 백필과 같은 이유 — 레퍼런스
    # Agent/Tool/Skill 자체가 아직 안 생긴 시점엔 코드를 배정할 대상이
    # 없다). 이미 code가 있는 행은 건드리지 않아 매 기동마다 불러도
    # 안전하다.
    id_scheme.backfill_missing_codes(conn)
    conn.commit()
    # 모델별 생성 옵션을 호출 지점(`llm.py`·CrewAI)이 쓰는 캐시로 올린다.
    from hair_platform import model_options

    model_options.refresh(conn)
    from hair_platform import org_policy

    org_policy.refresh(conn)
    # 이름으로 보호하던 팀에 보호 표시를 한 번만 옮긴다(이후 admin이 끄면 그대로 둔다).
    _run_once(
        conn,
        "2026-11-21-team-protect-on-reset",
        lambda c: c.execute(
            "UPDATE teams SET protect_on_reset = 1 WHERE name = '데이터 분석·시각화 전담팀'"
        ),
    )
    conn.commit()


def session_secret(conn: sqlite3.Connection) -> str:
    """세션·토큰 서명 키 — `HAIR_PLATFORM_SECRET_KEY`가 없을 때 쓴다(2026-11-22). 처음 부르면
    무작위 키(256비트 이상)를 만들어 `platform_secrets`에 저장하고, 이후에는 그 값을 돌려준다.
    이 표는 초기화(`data_admin` 공장 초기화 대상 표 목록)와 내보내기 대상에 넣지 않는다."""
    import secrets as _secrets

    conn.execute(
        "CREATE TABLE IF NOT EXISTS platform_secrets ("
        "name TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    row = conn.execute(
        "SELECT value FROM platform_secrets WHERE name = 'session_secret'"
    ).fetchone()
    if row is not None:
        return row[0]
    value = _secrets.token_urlsafe(48)
    conn.execute(
        "INSERT INTO platform_secrets (name, value) VALUES ('session_secret', ?)", (value,)
    )
    conn.commit()
    return value
