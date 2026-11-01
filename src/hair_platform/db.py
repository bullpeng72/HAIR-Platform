"""SQLite 연결 및 스키마 — v4(전체) 범위까지 다룬다.

실시간 쓰기가 빈번한 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 쓴다.
`approvals.step`·`approver_id`로 다단계 결재라인(조직도 매니저 관계
기반 자동 라우팅)을 표현한다. `audit_log`는 트리거로 UPDATE/DELETE를 막아
append-only를 DB 레벨에서 강제한다. `human_pool.performance_consent`가
False이면 `performance_score`를 절대 채우지 않는다(사전 동의 기반 최소수집).

(2026-09-26): `agent_pool.framework`가 어떤 생성 경로(stub/
simple_llm/crewai)로 만들어졌는지 기록한다. `knowledge_chunks`는 지식검색
Agent(VectorDB)의 임베딩 저장소다 — 별도 벡터DB
서버 없이 SQLite에 임베딩을 JSON으로 저장하고, 검색은 애플리케이션에서
코사인 유사도로 계산한다(로컬/온프레미스 우선 원칙). 관계분석 Agent
(GraphDB)는 새 테이블이 필요 없다 — 이미 있는 `users.manager_id`·
`assignments`·`human_pool`을 그래프로 취급해 조회한다(`graphquery.py`).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from hair_platform import id_scheme, reference_pipeline_seeds


def get_connection(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
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

CREATE TABLE IF NOT EXISTS tool_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
    config TEXT NOT NULL DEFAULT '{}',
    tool_names TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skill_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    default_tool_pool_ids TEXT NOT NULL DEFAULT '[]',
    augmentation_mode TEXT NOT NULL DEFAULT 'tool'
        CHECK (augmentation_mode IN ('tool', 'always', 'none')),
    show_in_conversation INTEGER NOT NULL DEFAULT 1
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
CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'ollama')),
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    optimization_tiers TEXT NOT NULL DEFAULT '[]',
    UNIQUE(provider, name)
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
        CHECK (mode IN ('single', 'sequential_relay', 'parallel_delegated')),
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


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, coltype_with_default: str
) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_with_default}")


def _migrate_tool_pool_custom_python_kind(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능")
    — `tool_pool.kind`는 컬럼이 아니라 **CHECK 제약**으로 허용값을
    제한한다(`'mcp_stdio', 'mcp_remote', 'native'`). SQLite는 기존 테이블의
    CHECK 제약을 `ALTER TABLE`로 못 바꾼다(`deliverables.status`를 못 넓혀
    `agent_execution_jobs`를 새로 만들었던 것과 같은 한계) — 여기선 컬럼이
    아니라 제약 자체를 넓혀야 해서 테이블을 통째로 재생성하는 수밖에 없다.

    **실측으로 발견한 버그**: 이 마이그레이션 없이 `kind='custom_python'`
    으로 INSERT하면 CHECK 제약 위반으로 `sqlite3.IntegrityError`가 나는데,
    `app.py`의 예외 처리가 그 예외를 전부 "이미 있는 이름입니다"(이름
    UNIQUE 위반 전용 메시지)로 오분류해 보여준다 — 원인이 완전히 다른데
    사용자에게 거짓 사유를 보여주는 상태였다(직접 작성한 테스트로 재현
    확인)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tool_pool'"
    ).fetchone()
    if row is None or row["sql"] is None or "custom_python" in row["sql"]:
        return  # 테이블이 아직 없거나(신규 DB — CREATE TABLE에 이미 포함) 이미 마이그레이션됨.
    # 실측으로 발견한 버그 — `agent_tool_links.tool_pool_id`가 이 테이블을
    # FK로 참조한다(`PRAGMA foreign_keys=ON`이 전역으로 걸려 있다, `db.py`
    # `get_connection()` 참고). FK가 켜진 채로 참조되는 테이블을 DROP하면
    # `sqlite3.IntegrityError: FOREIGN KEY constraint failed`가 난다 —
    # 이 마이그레이션 구간만 꺼둔다(SQLite 공식 문서가 테이블 재생성
    # 마이그레이션의 표준 절차로 권장하는 방식).
    # 실측으로 발견한 두 번째 버그 — `PRAGMA foreign_keys`는 SQLite
    # 공식 문서에 "트랜잭션 도중엔 no-op"이라고 명시돼 있다. 앞/뒤에
    # `commit()`으로 트랜잭션을 확실히 비우지 않으면 OFF/ON 토글 자체가
    # 조용히 무시돼, 마이그레이션은 성공해도 그 뒤로 FK 강제가 영영
    # 꺼진 채로 남는다(직접 작성한 재현 스크립트로 확인).
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # 실측으로 발견한 세 번째 버그 — 이 마이그레이션이 DROP TABLE
        # 단계에서 실패하면(예: 위 FK 버그가 고쳐지기 전 실제 서버에서
        # 한 번 터졌던 경우) `tool_pool_new`가 고아 테이블로 남아
        # 다음 재시작 때 "table already exists"로 또 실패한다 —
        # 재시도가 항상 깨끗하게 시작되도록 먼저 치운다.
        conn.execute("DROP TABLE IF EXISTS tool_pool_new")
        conn.execute("""
            CREATE TABLE tool_pool_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
                config TEXT NOT NULL DEFAULT '{}',
                tool_names TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """)
        conn.execute("INSERT INTO tool_pool_new SELECT * FROM tool_pool")
        conn.execute("DROP TABLE tool_pool")
        conn.execute("ALTER TABLE tool_pool_new RENAME TO tool_pool")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _backfill_conversation_last_read(conn: sqlite3.Connection) -> None:
    """`conversation_participants.last_read_message_id`가 막 추가된
    뒤(위 `_ensure_column`) 전부 `NULL`인 채로 두면, "읽은 적 없음"으로
    해석돼 그 참가자가 참여 중인 대화의 **모든 과거 메시지**가 "안 읽은
    멘션"으로 한꺼번에 몰려온다 — 몇 달치 대화를 이미 다 본 사람에게
    로그인 한 번에 수십 건씩 브라우저 알림이 쏟아지는 셈이다.

    완벽한 과거 "읽음" 시점을 되살릴 방법은 없지만(그런 기록을 애초에
    안 남겼다), 합리적인 근사치를 쓴다 — **이 대화에 직접 메시지를 보낸
    적이 있는 참가자는, 그 자신의 마지막 메시지 시점까지는 적극적으로
    읽고 있었다고 보고 거기까지 읽은 것으로 표시한다.**

    실측으로 바로잡은 지점(2026-10-14, 사용자가 직접 재현해 보고한
    "호출이 안 되는 거 같아") — 처음엔 "지금 시점(=백필 실행 시점)까지
    전부 읽음"으로 더 단순하게 짰었다. 그런데 실데이터로 검증하다가
    바로 이 버그를 만났다: pm_kim이 과거에 이 대화에서 한 번 말한 적은
    있었지만(그래서 "말한 적 없는 참가자"엔 안 걸림), 그 **이후에** 새로
    온 "@pm_kim" 멘션 3건까지 전부 "이미 읽음"으로 뭉개버렸다 — 참가자의
    마지막 활동 이후에 온 멘션은 당연히 본 적이 없는데 "지금 시점"을
    기준으로 삼은 게 원인이었다. 그래서 "지금 시점"이 아니라 "**그 참가자
    자신의 가장 최근 메시지 id**"로 바꿨다 — 그 이후에 온 멘션은(설령
    백필이 뒤늦게 돌아도) 여전히 안 읽음으로 남는다.

    메시지를 한 번도 안 보낸 참가자(초대만 되고 아직 대화를 열어본 적
    없을 가능성이 높음)는 `NULL`로 남겨 "전부 안 읽음" 그대로 둔다.

    `WHERE last_read_message_id IS NULL` 조건 덕에 매 기동 재실행해도
    안전하다(이미 값이 있는 행은 다시 안 건드림 — 한 번만 적용되는
    1회성 백필)."""
    conn.execute("""
        UPDATE conversation_participants
        SET last_read_message_id = (
            SELECT MAX(cm.id) FROM conversation_messages cm
            WHERE cm.conversation_id = conversation_participants.conversation_id
              AND cm.author_type = 'human'
              AND cm.author_id = conversation_participants.participant_id
        )
        WHERE last_read_message_id IS NULL
          AND participant_type = 'human'
          AND EXISTS (
              SELECT 1 FROM conversation_messages cm2
              WHERE cm2.conversation_id = conversation_participants.conversation_id
                AND cm2.author_type = 'human'
                AND cm2.author_id = conversation_participants.participant_id
          )
        """)
    conn.commit()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS`는 이미 존재하는 테이블에 새 컬럼을 추가하지
    못한다 — 이 저장소가 커오면서 `agent_pool`에 여러 번 컬럼을 추가했는데,
    이미 만들어진 DB 파일을 쓰는 배포(이 세션의 데모 서버 포함)에서는 이
    마이그레이션 없이 컬럼이 영영 안 생겨 `SELECT *` 결과에 없는 키에 접근할
    때 500 에러가 실제로 발생했다("AI 에이전트 관리"·"Crew 실행" 메뉴)."""
    _ensure_column(conn, "agent_pool", "llm_provider", "TEXT")
    _ensure_column(conn, "agent_pool", "llm_model", "TEXT")
    _ensure_column(conn, "agent_pool", "last_output", "TEXT")
    _ensure_column(conn, "agent_pool", "framework", "TEXT NOT NULL DEFAULT 'stub'")
    # 사용자 요청(2026-09-29, "사용자 삭제 기능 필요") — users.id는
    # audit_log/assignments/approvals/human_pool.user_id 등에서 NOT NULL
    # FK로 참조되므로 하드 삭제는 감사 이력·배정 이력을 깨뜨린다(append-only
    # 원칙과 정면 충돌). Human/Agent Pool이 이미 쓰는
    # "오프보딩"(레코드는 남기고 비활성화) 패턴을 계정에도 그대로
    # 적용한다.
    _ensure_column(conn, "users", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "agent_pool", "mcp_servers", "TEXT NOT NULL DEFAULT '[]'")
    # 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    # 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    # 그라운딩이 실제로 프롬프트에 뭘 주입했는지 사후에
    # 확인할 방법이 어디에도 없었다(결재자가 "이 답변이 실제 근거에
    # 기반했는지" 검증 불가). 실행 job에 근거를 같이 남긴다.
    _ensure_column(conn, "agent_execution_jobs", "retrieved_context", "TEXT")
    # 사용자 요청(2026-10-06, "필요 스킬" 자동완성에 "websearch"/"외부검색"이
    # 완전히 같은 뜻(둘 다 web_search native 도구)인데 둘 다 떠 중복으로
    # 보인다는 리포트 → "하나만 표시되도록 개선") — 두 별칭 중 "외부검색"을
    # 정식 이름으로 남기고 "websearch"는 skill_pool에서 제거한다. 같은
    # 요청으로 "vectordb"/"rag"(둘 다 vectordb_search), "graphdb"/"관계질의"
    # (둘 다 graphdb_query)도 같은 방식으로 통합한다 — "rag"/"graphdb"는
    # 실제 테스트·데이터에서 더 많이 쓰이던 쪽. 이미
    # 만들어진 DB에 남아있는 제거 대상 행과, 그 이름을 쓰던
    # tasks.required_skills/agent_pool.skill_tags의 값도 남기는 쪽 이름으로
    # 맞춰야 매칭이 끊기지 않는다 — `_seed_default_pools`의 `INSERT OR
    # IGNORE`만으로는 이미 심어진 행을 지우지 못하므로 여기서 명시적으로
    # 정리한다(멱등 — 제거 대상 행이 없으면 조용히 0건).
    for removed_name, kept_name in (
        ("websearch", "외부검색"),
        ("vectordb", "rag"),
        ("관계질의", "graphdb"),
    ):
        removed_row = conn.execute(
            "SELECT id FROM skill_pool WHERE name = ?", (removed_name,)
        ).fetchone()
        if removed_row is None:
            continue
        for table, column in (
            ("tasks", "required_skills"),
            ("agent_pool", "skill_tags"),
        ):
            for row in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                tags = json.loads(row[column] or "[]")
                if removed_name in tags:
                    tags = [kept_name if t == removed_name else t for t in tags]
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), row["id"]),
                    )
        conn.execute("DELETE FROM skill_pool WHERE id = ?", (removed_row["id"],))

    # 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) —
    # `team_leader_id`는
    # resource_type='team' 배정의 결재 주체(기본값은 배정 요청자,
    # 변경 가능 — `assignments.update_team_leader()`). `replaces_
    # assignment_id`는 "다중 confirmed 허용"으로 바꾸면서(같은 task·
    # 같은 resource_type이어도 더 이상 자동으로 서로를 밀어내지 않는다)
    # 그래도 "이 배정은 저 배정을 명시적으로 교체한다"는 의도를 표현할
    # 길이 필요해서 추가했다 — 이 값이 있을 때만 승인 시 그 특정
    # assignment가 superseded된다(`assignments.resolve_assignment_
    # decision()`).
    _ensure_column(
        conn, "assignments", "team_leader_id", "INTEGER REFERENCES users(id)"
    )
    _ensure_column(
        conn,
        "assignments",
        "replaces_assignment_id",
        "INTEGER REFERENCES assignments(id)",
    )
    # 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT,
    # HTML 등 다양할 수 있을텐데 현재 지원 가능한 범위와... Tier 2까지
    # 진행해줘") — 지금까지 `deliverables.content`는 포맷 구분 없는
    # 순수 TEXT였다(LLM이 뭘 내든, 사람이 뭘 쓰든 전부 같은 컬럼에
    # 섞여 들어감 — 렌더링도 `renderContent()`의 코드펜스/굵게 처리
    # 뿐이었다). `content_type`으로 저장 시점에 포맷을 명시해 화면이
    # 포맷에 맞게 렌더링(마크다운 전체 렌더링, HTML은 sandbox iframe
    # 미리보기)할 수 있게 한다 — 실제 파일(PPT 등) 저장은 Tier 3(스키마
    # 재구성 필요)로 남겨둔 범위 밖이다.
    _ensure_column(
        conn,
        "deliverables",
        "content_type",
        "TEXT NOT NULL DEFAULT 'text' "
        "CHECK (content_type IN ('text', 'markdown', 'html', 'code'))",
    )

    # 2026-10-08, 사용자 요청 "ASANA 모방
    # 수준의 기능 및 UI 제공을 위한 기획 및 설계안을 제안해줘" → Phase 1
    # 승인 — Task 자체에 메모를 남길 수 있는 자유 텍스트 공간. 기존
    # `deliverables.content`(결과물)나 `deliverable_comments`(결과물에 대한
    # 댓글)와 달리, 결과물이 아직 없는 단계에서도 Task 참가자들이 맥락/
    # 의사결정을 적어둘 수 있는 곳이 없었다.
    _ensure_column(conn, "tasks", "notes", "TEXT NOT NULL DEFAULT ''")

    # 2026-10-08, Phase 3 — 댓글
    # `@멘션`으로 트리거된 Agent 실행을 "AI 에이전트 실행" 버튼으로
    # 트리거된 것과 구분하기 위한 출처 표시. NULL이면 기존 경로(버튼),
    # 값이 있으면 그 댓글이 트리거했다는 뜻 — 어느 쪽이든 같은
    # `agent_execution_jobs` 테이블·폴링 UI를 그대로 재사용한다.
    _ensure_column(
        conn,
        "agent_execution_jobs",
        "source_comment_id",
        "INTEGER REFERENCES deliverable_comments(id)",
    )

    # 2026-10-08, Phase 4 — 작업 구조
    # 심화(상태/우선순위). `task_dependencies`는 새 테이블이라
    # `_ensure_column()` 불필요 — SCHEMA의 `CREATE TABLE IF NOT EXISTS`
    # 만으로 기존 DB에도 안전하게 추가된다.
    _ensure_column(
        conn,
        "tasks",
        "status",
        "TEXT NOT NULL DEFAULT 'queued' "
        "CHECK (status IN ('backlog', 'queued', 'in_progress', 'done'))",
    )
    _ensure_column(
        conn,
        "tasks",
        "priority",
        "TEXT NOT NULL DEFAULT 'medium' "
        "CHECK (priority IN ('low', 'medium', 'high', 'urgent'))",
    )

    # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시(채팅
    # 포함)에도 활용될 수 있도록 개선") — 지금까지 Agent는 `role_name`
    # (생성 당시 역할, 예: "백엔드 개발자 Agent")이 식별자 역할까지
    # 겸하고 있어, 같은 역할의 Agent가 여럿이면 구분이 안 되고
    # "에이전트 #7"처럼 ID로만 불러야 했다. Human Pool의 `name`과 같은
    # 자리 — 선택 입력(NULL 허용)이라 기존 Agent는 전부 비어 있는
    # 채로 시작하고, 비어 있으면 화면·멘션 전부 `role_name`으로
    # 그대로 돌아간다(하위 호환 100%, 아무것도 깨지지 않음).
    _ensure_column(conn, "agent_pool", "name", "TEXT")

    # 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 1,2번
    # 구현) — "AI 재현 가능한 감사로그" 인사이트. 지금까지 `audit_log`의
    # `agent_executed` 행이 원본(증강 전) 프롬프트만 `before_value`에
    # 남기고, 실제로 LLM에 들어간 system prompt·RAG/GraphDB/외부검색로
    # 증강된 최종 프롬프트·그때 쓴 provider/model은 어디에도 안
    # 남았다(`agent_pool.llm_provider`/`llm_model`은 다음 실행에
    # 덮어써져 과거 실행 시점 값을 잃는다) — Gate 점수가 낮게 나온
    # 산출물을 "왜 이렇게 나왔는지" 사후 재현할 방법이 없었다. 이
    # 실행(`run_agent_execution_job()`)과 직접 연결된
    # `agent_execution_jobs`에 그 시점 스냅샷을 JSON으로 남긴다.
    _ensure_column(conn, "agent_execution_jobs", "prompt_snapshot", "TEXT")

    # 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    # 하는 기능") — 실제 LLM 가중치 재학습이 아니라(사용자 확인) CrewAI
    # `Agent`/`LLM`의 동작 파라미터(max_iter/max_rpm/max_execution_time/
    # max_retry_limit/reasoning/allow_code_execution/
    # respect_context_window/temperature)를 Agent별로 저장해두고
    # `crew_runtime.py`의 CrewAI 생성 경로(생성 시점 단일 Agent + Crew
    # 실행 둘 다)에 반영하는 기능이다. 전부 선택값이라 JSON 하나로
    # 묶는다(mcp_servers와 같은 관례) — 키가 없으면 CrewAI 기본값을
    # 그대로 쓴다(지정 안 한 Agent는 지금과 100% 동일하게 동작).
    _ensure_column(conn, "agent_pool", "tuning_config", "TEXT NOT NULL DEFAULT '{}'")
    _migrate_tool_pool_custom_python_kind(conn)
    # 사용자 요청(2026-10-14, "호출 받은 사람에게 브라우저 알림이
    # 오고... ") — 실측으로 발견한 버그: 처음엔 "최초 폴링은 알림 없이
    # 기준점만 잡는다"는 클라이언트 메모리 커서(`_mentionNotifySinceId`)
    # 로 중복 알림을 막았는데, 그 탭이 멘션이 이미 온 *뒤에* 열리면(실제
    # 테스트 상황 그대로 — admin이 먼저 세 번 호출하고, pm_kim이 그 뒤에
    # 로그인) 그 멘션들이 전부 "최초 폴링"에 걸려 영원히 알림이 안 뜬다.
    # 서버가 참가자별 "마지막으로 읽은 메시지"를 기억해야 진짜로 고쳐진다.
    _ensure_column(conn, "conversation_participants", "last_read_message_id", "INTEGER")
    _backfill_conversation_last_read(conn)
    # 사용자 요청(2026-10-14, "10건 이전의 메시지는 요약 형태로 전달되도록
    # 개선해줘") — `_build_transcript()`(conversations.py)가 최근
    # `_CONTEXT_MESSAGE_LIMIT`(10)건보다 오래된 메시지는 그냥 버리던 것을,
    # 캐시된 요약으로 대체한다.
    _ensure_column(conn, "conversations", "context_summary", "TEXT")
    _ensure_column(conn, "conversations", "context_summary_through_id", "INTEGER")
    # 사용자 요청(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를
    # 개선해줘") — 같은 대화라도 트리거(기본 AI/Agent 멘션/Team 멘션)마다
    # 실제로 쓰이는 provider/model이 다르다(`conversations.py`의
    # `run_default_ai_job`/`run_agent_mention_job`/`run_team_mention_job`
    # 참고 — 대화의 `llm_provider`/`llm_model` 설정은 기본 AI 응답에만
    # 쓰이고, Agent 멘션은 그 Agent 자신의 저장값을, Team 멘션은 환경변수/
    # API 키 기반 자동판별값을 쓴다). 메시지마다 실제로 쓰인 값을 저장해야
    # 화면에 정확히 보여줄 수 있어 대화 설정이 아니라 메시지 단위로 둔다.
    _ensure_column(conn, "conversation_messages", "llm_provider", "TEXT")
    _ensure_column(conn, "conversation_messages", "llm_model", "TEXT")
    # 사용자 요청(2026-10-18, "@자율 개발 크루... 생성 결과물의 품질을
    # 개선하기 위해... 어떤 개선이 필요할지") 분석 후속 — 실측으로 확인한
    # "Gate가 fail해도 대화창 경로에서는 측정만 하고 버려진다" 문제를
    # 메우는 최소한의 안전장치. `run_crew()`가 이미 계산해서 반환하는
    # gate_scores/delegations 개수를 메시지에 같이 저장해, 화면에서
    # "이 결과가 Gate 기준을 통과했는지"·"매니저가 실제로 위임했는지"를
    # 바로 보여준다(Task 협업워크스페이스 경로의 agent_evaluations/
    # 결재 체인만큼 엄격하진 않지만, 적어도 조용히 사라지진 않는다).
    _ensure_column(conn, "conversation_messages", "gate_scores", "TEXT")
    _ensure_column(conn, "conversation_messages", "delegation_count", "INTEGER")
    # 사용자 보고(2026-10-19, "Todo 앱 최종 결과물에 코드가 아니라 품질
    # 리포트만 있다") — `pipeline_stages` CREATE TABLE에 추가한 설명
    # 참고. 어느 단계가 "진짜 최종 산출물"인지 표시하는 플래그.
    _ensure_column(
        conn, "pipeline_stages", "is_primary_deliverable", "INTEGER NOT NULL DEFAULT 0"
    )
    # 사용자 요청(2026-10-20, "CrewAI agent의 경우 에이전트 생성은 role,
    # goal, backstory, tool 등을 설정하고... HAIR-Platform에 crewai
    # 에이전트 및 에이전트 그룹을 상세조정하고 수정하기 위한 고급 에이전트
    # 관리 기능 추가를 기획해줘") — 지금까지 `Agent.goal`/`backstory`는
    # `crew_runtime._agent_goal()`/`_agent_backstory()`가 role_name+
    # skill_tags로 매번 자동 생성했고, `Task.expected_output`은 플랫폼
    # 전역에서 하드코딩 문자열 하나뿐이었다(관리자가 덮어쓸 방법 없음).
    # 전부 NULL 허용 — NULL/빈 문자열이면 기존처럼 자동 생성(100% 하위
    # 호환, `tuning_config` 빈 값 관례와 동일).
    _ensure_column(conn, "agent_pool", "goal", "TEXT")
    _ensure_column(conn, "agent_pool", "backstory", "TEXT")
    _ensure_column(conn, "agent_pool", "expected_output", "TEXT")
    # Team(재사용 가능한 사람+Agent 그룹, `teams` 테이블)은 멤버 목록뿐
    # process(sequential/hierarchical)·매니저 기본값이 없어, "팀 Agent
    # 실행"을 누를 때마다 매번 새로 골라야 했다(또는
    # `manager_agent_id_for_team()`의 `role_label`="매니저" 문자열 추정에
    # 의존). NULL이면 기존처럼 그 추정/매 실행 시 선택에 그대로 맡긴다.
    _ensure_column(conn, "teams", "default_process", "TEXT")
    _ensure_column(
        conn, "teams", "default_manager_agent_id", "INTEGER REFERENCES agent_pool(id)"
    )
    # 파이프라인 단계는 `instructions`(Task.description 보강)는 있었지만
    # `expected_output`은 없어, 모든 단계가 똑같은 하드코딩 문구를 썼다
    # (예: "QA 기능테스트" 단계도 "구현" 단계와 같은 기대 산출물 문구).
    # 빈 문자열 = 기존 하드코딩 문구 그대로 사용.
    _ensure_column(
        conn, "pipeline_stages", "expected_output", "TEXT NOT NULL DEFAULT ''"
    )
    # 사용자 요청(2026-10-20, "실패 시 일부 재시도") — 실패한 crew_run을
    # 복구하려면 원본 Agent별 task description(재시도 시 복원용)과, 실제로
    # Task를 끝내 deliverable이 만들어진 Agent 목록(남은 Agent만 추려
    # 재시도하기 위함)이 필요하다. 둘 다 NULL/빈 배열이면 기존 run(이
    # 마이그레이션 이전에 생성된 행)은 그냥 "재시도 불가"로 취급된다.
    _ensure_column(conn, "crew_runs", "task_descriptions", "TEXT")
    _ensure_column(
        conn, "crew_runs", "completed_agent_ids", "TEXT NOT NULL DEFAULT '[]'"
    )
    # 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 지금까지
    # `team_members`엔 순서 개념이 전혀 없어(암묵적으로 `id` 삽입 순서만
    # 있음) 사용자가 순차 릴레이 순서를 바꿀 수단이 없었다. NULL = "아직
    # 캔버스로 순서를 정한 적 없음" → 기존처럼 `id` 순서로 폴백(하위 호환).
    _ensure_column(conn, "team_members", "sequence_order", "INTEGER")
    _ensure_column(
        conn, "skill_pool", "show_in_conversation", "INTEGER NOT NULL DEFAULT 1"
    )
    _ensure_column(
        conn,
        "knowledge_chunks",
        "source_document_id",
        "INTEGER REFERENCES documents(id)",
    )
    # 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각
    # tier에 적용되는 모델을 매핑") — 기존 DB(이 컬럼 없이 만들어진)에도
    # 추가. '[]'(기본값) = 이 tier엔 아직 명시적 매핑이 없음(패턴 추정
    # 폴백, 기존 동작 유지).
    _ensure_column(
        conn, "model_registry", "optimization_tiers", "TEXT NOT NULL DEFAULT '[]'"
    )
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 기존 DB에도 `code` 컬럼을
    # 추가한다. UNIQUE 인덱스는 컬럼이 확실히 생긴 뒤인 여기서만
    # 만든다(정적 SCHEMA에 두면 아직 컬럼 없는 기존 DB에서 깨짐 —
    # 위 실측 기록 참고). 실제 코드 배정(`id_scheme.backfill_missing_
    # codes()`)은 `init_schema()`가 시드까지 다 끝낸 뒤 호출한다.
    for table in ("agent_pool", "tool_pool", "skill_pool"):
        _ensure_column(conn, table, "code", "TEXT")
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_code ON {table}(code)"
        )
    # 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    # 8자리~12자리 입력 및 표시 가능해야 함") — 기존 DB에도 컬럼을
    # 추가한다(위 code 컬럼들과 같은 이유로 정적 SCHEMA에 안 두고
    # 여기서만 인덱스를 만든다).
    _ensure_column(conn, "human_pool", "employee_id", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_human_pool_employee_id "
        "ON human_pool(employee_id)"
    )
    # 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    # UI개선") — human_pool과 별개로 로그인 계정(`users`)에도 같은
    # 체계로 둔다(두 테이블 간 값을 서로 강제 일치시키진 않는다 — 계정
    # 없는 human_pool 레코드도 있고, 그 반대로 human_pool에 아직 안
    # 올라온 계정도 있어 1:1이 보장되지 않는다).
    _ensure_column(conn, "users", "employee_id", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee_id "
        "ON users(employee_id)"
    )
    _sync_dev_pipeline_implementation_stage_agents(conn)
    # 사용자 요청(2026-10-28, "반려 사유를 재시도 지시문에 직접 끼워
    # 넣는 식으로 개선") — 기존 DB에도 컬럼을 추가한다.
    _ensure_column(conn, "pipeline_run_stages", "decision_reason", "TEXT")
    # 사용자 요청(2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우
    # 파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수 있도록
    # 재구성해줘") — 정적 SCHEMA의 `teams`/`pipeline_runs`는 새로
    # 만들어지는 DB에만 해당 컬럼이 있다. 기존 DB에도 추가한다.
    _ensure_column(
        conn,
        "teams",
        "pipeline_template_id",
        "INTEGER REFERENCES pipeline_templates(id)",
    )
    _ensure_column(
        conn,
        "pipeline_runs",
        "source_conversation_id",
        "INTEGER REFERENCES conversations(id)",
    )
    _sync_dev_crew_pipeline_integration(conn)
    # 사용자 요청(2026-10-29, "TF팀을 teams의 확장으로, Team 온보딩
    # 라이프사이클 도입 — 기존 활성 팀은 전부 자동 active 처리") —
    # 기존 DB에도 컬럼을 추가하고, 이미 활성/비활성이던 팀들을
    # 재검토 없이 새 status로 백필한다. `status='pending_onboarding'`
    # 조건이 "이미 한 번 백필됐거나 관리자가 그 뒤 직접 바꾼 행은
    # 다시 안 건드린다"는 멱등 가드 역할을 한다.
    _ensure_column(
        conn, "teams", "status", "TEXT NOT NULL DEFAULT 'pending_onboarding'"
    )
    _ensure_column(conn, "teams", "task_id", "INTEGER")
    _ensure_column(conn, "teams", "brief_id", "INTEGER")
    _ensure_column(conn, "teams", "completed_at", "TEXT")
    conn.execute(
        "UPDATE teams SET status = 'active' "
        "WHERE is_active = 1 AND status = 'pending_onboarding'"
    )
    conn.execute(
        "UPDATE teams SET status = 'deprecated' "
        "WHERE is_active = 0 AND status = 'pending_onboarding'"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_task_id ON teams(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_brief_id ON teams(brief_id)")


# 위 마이그레이션이 지우는 대상 — 2026-10-26 "자율 개발 크루" 12명→6명
# 정리 전, "구현" 단계에 심어졌던 중복 클론 role_name들(지금의
# agent_pool에는 존재하지 않는 고아 참조).
_DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES = (
    "개발자",
    "백엔드 개발자 2",
    "백엔드 개발자 3",
    "백엔드 개발자 4",
    "프론트엔드 개발자 2",
    "프론트엔드 개발자 3",
    "데이터/인프라 개발자 1",
    "데이터/인프라 개발자 2",
)
# 위 정리 전 `DEV_PIPELINE_DESCRIPTION`에 있던 문구 — 아래 함수가 DB에
# 이 문구가 그대로 남아 있을 때만 새 문구로 바꾼다(관리자가 이미 직접
# 수정했다면 더 이상 이 문자열과 일치하지 않으므로 건드리지 않는다).
_DEV_PIPELINE_STALE_DESCRIPTION = (
    "기획서 → 요구분석(계획 확정, 사람 승인 1회) → 설계(AA→DA) → 개발 계획 수립 → "
    "구현(매니저가 10명+UI기획자에게 실제 위임) → 코드 검증 → QA 기능테스트 → "
    "품질 관리 최종 판정까지, Gate A-G 기준으로 자동 반복하며 진행하는 정식 "
    "개발 파이프라인(BMAD-METHOD 대응 — 계획 확정 시점에만 사람이 개입한다)."
)


def _sync_dev_pipeline_implementation_stage_agents(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-28, "파이프라인 관리 — '#1 소프트웨어 개발
    파이프라인(software_dev)'을 현행화해줘") — `_seed_dev_pipeline()`은
    `pipeline_templates.name`이 이미 있으면 그냥 return한다(관리자가
    고친 내용을 재기동 때 덮어쓰지 않으려는 의도적 설계, 그 함수의
    docstring 참고). 그런데 그 설계 때문에 "자율 개발 크루"를 12명→6명
    으로 정리한 2026-10-26 커밋(`DEV_CREW_TEAM_MEMBER_SPECS` 변경)이
    `DEV_PIPELINE_STAGES`의 "구현" 단계 agents 목록까지 맞게 고쳤어도
    이미 한 번 심어진 `pipeline_stage_agents` 행에는 전혀 반영되지
    않았다(실측 확인 — "구현" 단계에 더 이상 agent_pool에 없는
    "백엔드 개발자 2/3/4" 등 8개 role_name이 고아로 남아 있었고, 정작
    지금 쓰이는 "백엔드 개발자"/"데이터/인프라 개발자"는 빠져 있었다).
    "파이프라인 관리" 화면은 이 테이블을 그대로 읽어 보여주므로
    (`pipeline_runtime.py`), 화면에 옛 12명 체제가 계속 떴다.

    고아 role_name만 지우고 현재 `DEV_PIPELINE_STAGES`에 있는데 빠진
    role만 추가한다 — 멱등(이미 현행화됐으면 두 쿼리 다 0건)이고,
    관리자가 "구현" 단계에 직접 추가한(위 고아 목록에 없는) 다른
    role_name은 건드리지 않는다."""
    stage_row = conn.execute(
        """
        SELECT ps.id FROM pipeline_stages ps
        JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id
        WHERE pt.name = ? AND ps.name = '구현'
        """,
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    if stage_row is None:
        return
    stage_id = stage_row["id"]
    placeholders = ",".join("?" for _ in _DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES)
    conn.execute(
        "DELETE FROM pipeline_stage_agents WHERE stage_id = ? "
        f"AND role_name IN ({placeholders})",
        (stage_id, *_DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES),
    )
    current_roles = {
        r["role_name"]
        for r in conn.execute(
            "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ?",
            (stage_id,),
        ).fetchall()
    }
    implementation_stage = next(
        s for s in reference_pipeline_seeds.DEV_PIPELINE_STAGES if s[0] == "구현"
    )
    for seq, (role_name, instructions) in enumerate(implementation_stage[6], start=1):
        if role_name in current_roles:
            continue
        if (
            conn.execute(
                "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            is None
        ):
            continue
        conn.execute(
            "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
            "instructions) VALUES (?, ?, ?, ?)",
            (stage_id, role_name, seq, instructions),
        )
    conn.execute(
        "UPDATE pipeline_templates SET description = ? "
        "WHERE name = ? AND description = ?",
        (
            reference_pipeline_seeds.DEV_PIPELINE_DESCRIPTION,
            reference_pipeline_seeds.DEV_PIPELINE_NAME,
            _DEV_PIPELINE_STALE_DESCRIPTION,
        ),
    )


# "코드 검증"/"QA 기능테스트"/"품질 관리 최종 판정" — 사용자 결정
# (2026-10-28, "자율 개발 크루" 재구성 중강도 Gate 선택)으로 이
# 3단계만 min_gate_pass_ratio를 0.0(항상 통과)에서 0.5로 올린다.
_DEV_PIPELINE_GATED_STAGE_NAMES = ("코드 검증", "QA 기능테스트", "품질 관리 최종 판정")
_DEV_PIPELINE_GATE_PASS_RATIO = 0.5

# 사용자 결정(2026-10-28, 같은 재구성)으로 "자율 개발 크루" 전 멤버를
# 로컬 Ollama에서 클라우드로 전환한다 — `llm.DEFAULT_MODELS["openai"]`
# 와 같은 값(db.py는 llm.py를 import하지 않는 저수준 모듈이라 값만
# 그대로 가져와 썼다 — 이 값이 바뀌면 여기도 같이 바꿔야 한다). 이
# 배포의 `.env`에는 OPENAI_API_KEY만 있고 ANTHROPIC_API_KEY가 없어
# (실측 확인, 2026-10-28) provider를 openai로 잡는다 — anthropic으로
# 잡으면 실제 호출 시점에 API 키 누락으로 조용히 실패한다. 나중에
# ANTHROPIC_API_KEY를 추가하면 관리자가 "AI 에이전트 관리"에서 직접
# anthropic으로 바꿀 수 있다(이 마이그레이션은 `llm_provider='ollama'`
# 인 행만 건드리므로 그 이후엔 다시 안 건드림).
_DEV_CREW_CLOUD_PROVIDER = "openai"
_DEV_CREW_CLOUD_MODEL = "gpt-4o-mini"

# "구현" 단계 6명뿐이던 시절의 옛 role_label → 새 "N단계: ..." 표기.
# 정확히 이 옛 문자열 그대로인 행만 바꾼다(관리자가 이미 고친 라벨은
# 보존).
_DEV_CREW_LEGACY_ROLE_LABEL_MIGRATIONS = (
    (
        "매니저(hierarchical 실행 시 지정)",
        "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)",
    ),
    ("백엔드", "4단계: 구현(백엔드)"),
    ("프론트엔드", "4단계: 구현(프론트엔드)"),
    ("데이터/인프라", "4단계: 구현(데이터/인프라)"),
    ("풀스택(유동 배치)", "4단계: 구현(풀스택, 유동 배치)"),
    ("UI/UX", "4단계: 구현(UI/UX)"),
)


def _sync_dev_crew_pipeline_integration(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우
    파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수 있도록
    재구성해줘") — 오늘 세션에서 "@자율 개발 크루"로 만든 Breakout
    게임이 "Gate 1/5 통과"에도 동작하지 않는 코드였던 근본원인 3가지를
    메운다: (1) 팀을 "소프트웨어 개발 파이프라인"(코드 검증/QA/품질
    관리 전용 단계 + Gate 기준 자동 재시도를 갖춘 엔진)에 연결하고,
    (2) 그 3단계 Gate를 실제로 의미 있게(0.5) 걸고, (3) 전 멤버를
    약한 로컬 Ollama에서 클라우드 모델로 바꾼다. 전부 멱등 — 이미
    적용됐거나 관리자가 그 이후 직접 다른 값으로 바꾼 행은 조건절이
    더 이상 일치하지 않아 건드리지 않는다(이 파일 전체에서 쓰는
    표준 패턴과 동일 — 마이그레이션 이력 테이블이 없는 이 코드베이스
    설계상, "관리자가 의도적으로 다시 되돌린 값"과 "아직 마이그레이션
    전인 옛 값"을 완벽히 구분할 수는 없다는 한계는 그대로 따른다)."""
    template_row = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    if template_row is None:
        return
    template_id = template_row["id"]

    # (1) 팀 ↔ 파이프라인 연결
    conn.execute(
        "UPDATE teams SET pipeline_template_id = ? "
        "WHERE name = ? AND pipeline_template_id IS NULL",
        (template_id, reference_pipeline_seeds.DEV_CREW_TEAM_NAME),
    )

    # (2) Gate 임계값
    placeholders = ",".join("?" for _ in _DEV_PIPELINE_GATED_STAGE_NAMES)
    conn.execute(
        "UPDATE pipeline_stages SET min_gate_pass_ratio = ? "
        f"WHERE pipeline_template_id = ? AND name IN ({placeholders}) "
        "AND min_gate_pass_ratio = 0.0",
        (_DEV_PIPELINE_GATE_PASS_RATIO, template_id, *_DEV_PIPELINE_GATED_STAGE_NAMES),
    )

    team_row = conn.execute(
        "SELECT id FROM teams WHERE name = ?",
        (reference_pipeline_seeds.DEV_CREW_TEAM_NAME,),
    ).fetchone()

    # (3) 멤버 구성 — "구현" 단계 6명뿐이던 구성을 파이프라인 7단계
    # 전체가 참조하는 13개 역할로 넓힌다(사용자 요청, 2026-10-28,
    # "파이프라인의 에이전트 구성과 흐름을 참조해... 구성과 업무
    # 흐름을... 재구성") — "팀 관리" 화면만 봐도 이 팀이 요구분석부터
    # 품질 관리까지 전체를 수행한다는 게 보이게 한다. 이미 있는 멤버는
    # `UNIQUE(team_id, member_type, member_id)`에 걸려 INSERT OR
    # IGNORE가 건너뛴다 — 빠진 역할만 새로 추가된다. 아래 (4) 모델
    # 전환보다 **먼저** 해야 새로 추가되는 멤버도 같은 재기동에서 바로
    # 클라우드 모델로 전환된다(순서를 바꾸면 새 멤버는 다음 재기동까지
    # Ollama로 남는다).
    if team_row is not None:
        for (
            role_name,
            role_label,
        ) in reference_pipeline_seeds.DEV_CREW_TEAM_MEMBER_SPECS:
            agent_row = conn.execute(
                "SELECT id FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO team_members "
                "(team_id, member_type, member_id, role_label) "
                "VALUES (?, 'agent', ?, ?)",
                (team_row["id"], agent_row["id"], role_label),
            )
        # 기존 6명의 role_label도 새 "N단계: ..." 표기로 맞춘다 — 옛
        # 문구 그대로인 행만(관리자가 이미 직접 고친 라벨은 안 건드림).
        for old_label, new_label in _DEV_CREW_LEGACY_ROLE_LABEL_MIGRATIONS:
            conn.execute(
                "UPDATE team_members SET role_label = ? "
                "WHERE team_id = ? AND member_type = 'agent' AND role_label = ?",
                (new_label, team_row["id"], old_label),
            )

    # (4) 모델 전환 — "자율 개발 크루" 팀원 중 지금 Ollama인 agent만
    if team_row is not None:
        conn.execute(
            """
            UPDATE agent_pool SET llm_provider = ?, llm_model = ?
            WHERE llm_provider = 'ollama'
              AND id IN (
                  SELECT member_id FROM team_members
                  WHERE team_id = ? AND member_type = 'agent'
              )
            """,
            (_DEV_CREW_CLOUD_PROVIDER, _DEV_CREW_CLOUD_MODEL, team_row["id"]),
        )


def _backfill_show_in_conversation(conn: sqlite3.Connection) -> None:
    """`show_in_conversation` 컬럼이 막 추가된 직후 1회만 실행 —
    `augmentation_mode`가 `'tool'`/`'always'`이면서 연결된 도구 중
    `kind != 'native'`인 것이 있는 스킬(대화창 기본 AI 경로에서 구조적
    으로 실시간 실행이 불가능한 것들 — `pool_registry.
    unsupported_realtime_skill_names()`와 같은 기준)을 0으로 끈다."""
    for row in conn.execute(
        "SELECT id, default_tool_pool_ids FROM skill_pool "
        "WHERE augmentation_mode IN ('tool', 'always')"
    ).fetchall():
        tool_ids = json.loads(row["default_tool_pool_ids"])
        has_non_native = False
        for tool_id in tool_ids:
            trow = conn.execute(
                "SELECT kind FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
            ).fetchone()
            if trow is not None and trow["kind"] != "native":
                has_non_native = True
                break
        if has_non_native:
            conn.execute(
                "UPDATE skill_pool SET show_in_conversation = 0 WHERE id = ?",
                (row["id"],),
            )


def _seed_default_pools(conn: sqlite3.Connection) -> None:
    """사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘" — VectorDB/GraphDB를 감싼 native Tool과, 이걸 쓰는
    skill_pool 항목을 기본 제공한다. 이전까지 `agent_runtime.py`의
    `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 하드코딩이 하던 "스킬→도구"
    매핑을 데이터로 옮긴 것 — 이 시드가 없으면 기존 vectordb/graphdb
    skill_tags를 가진 Agent가 갑자기 도구를 못 받게 되므로 반드시 필요하다.
    `name` UNIQUE 제약으로 `INSERT OR IGNORE`가 매 기동마다 안전하게
    멱등이다(이미 있으면 아무 것도 안 함 — 관리자가 이후 수정해도 안
    덮어씀).

    사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
    API(가능한 무료)를 도구화해서... 추가해줘")로 `websearch.py`(Wikipedia
    검색, API 키 불필요) 기반 세 번째 native 도구를 같은 방식으로
    추가했다 — VectorDB(과거 산출물)·GraphDB(조직 관계)는 둘 다 조직
    내부 데이터만 다뤄, "조직 밖 일반 지식"이 필요한 질문엔 답을 못
    하던 갭을 메운다.

    이어서 사용자 요청(2026-09-29, "일반 웹 검색 기능을 Tool로 제공
    가능한지 확인해줘" → "진행해줘")으로 Parallel AI의 무료 공개 MCP
    서버(`mcp_remote`, 계정·키 불필요)도 추가했다 — Wikipedia는 자체
    백과사전 색인만 다뤄 "최신 뉴스" 같은 진짜 일반 웹 정보는 여전히
    못 찾았다. recommend_fix(mcp_stdio)와 같은 자리에 두되 종류만
    `mcp_remote`다."""
    native_tools = [
        (
            "VectorDB 지식검색",
            "vectorstore.search()를 감싼 네이티브 CrewAI Tool",
            "vectordb_search",
        ),
        (
            "GraphDB 관계조회",
            "graphquery.context_for_prompt()를 감싼 네이티브 CrewAI Tool",
            "graphdb_query",
        ),
        (
            "실시간 웹 검색(Tavily)",
            (
                "websearch.tavily_context_for_prompt()를 감싼 네이티브 CrewAI "
                "Tool — API 키 필요(TAVILY_API_KEY). 'Parallel Search'와 달리 "
                "순수 HTTP 호출이라 대화창(정적 주입)·Crew 실행 양쪽에서 "
                "동일하게 동작한다(사용자 요청, 2026-10-26, '대화에서 외부 "
                "검색이 가능한 방법을 제안해줘')."
            ),
            "tavily_search",
        ),
    ]
    # 사용자 요청(2026-10-26, "도구/skill에서 동작하지 않는 검색은
    # 삭제해줘(한시적인 오류인 경우 잠정 보류)") — "외부 자료 검색
    # (Wikipedia)"를 이 시드에서 뺐다. 재확인 결과 일시적 장애가 아니라
    # Wikimedia가 이 배포 환경의 IP를 robot policy 위반으로 지속
    # 차단 중이었다(세션 내내 여러 차례 재시도해도 동일 403, 참고:
    # `websearch.py` 모듈 docstring). 구현 코드(`websearch.search()`/
    # `context_for_prompt()`/`as_crewai_tool()`)는 지우지 않고 그대로
    # 남겨뒀다 — 다른 네트워크 환경에 배포하면 다시 쓸 수 있다. 되살리려면
    # 이 리스트에 항목을 다시 추가하고 `pool_registry.NATIVE_TOOL_
    # FACTORIES`에 `"web_search": websearch.as_crewai_tool`을 다시 붙이면
    # 된다. "일반웹검색"(Parallel Search, MCP)은 Crew 실행에서 정상
    # 동작하는 걸 이미 확인했으므로(대화창 전용 제약은 설계상 한계지
    # 오류가 아님) 그대로 둔다.
    tool_ids: dict[str, int] = {}
    for name, description, native_id in native_tools:
        conn.execute(
            "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
            "VALUES (?, ?, 'native', ?)",
            (name, description, json.dumps({"native_id": native_id})),
        )
        row = conn.execute(
            "SELECT id FROM tool_pool WHERE name = ?", (name,)
        ).fetchone()
        tool_ids[native_id] = row["id"]

    skills = [
        ("rag", "VectorDB 기반 지식검색 역량", "vectordb_search", "tool"),
        ("graphdb", "GraphDB 기반 관계분석 역량", "graphdb_query", "tool"),
        (
            "실시간웹검색",
            "실시간 일반 웹 검색 역량(Tavily, API 키 필요) — 대화창에서도 동작",
            "tavily_search",
            "tool",
        ),
        (
            "최신정보필수확인",
            (
                "최신 외부 정보 누락이 치명적인 역할(시황/뉴스/규정 변경 "
                "모니터링 등)용 — Agent가 검색할지 말지 스스로 판단하게 "
                "두지 않고, 매 실행마다 Tavily 검색 결과를 Task description에 "
                "강제로 끼워 넣는다. 'tool' 모드(실시간웹검색)는 Agent가 "
                "호출 여부를 스스로 정하지만, 'always'는 판단을 거치지 않고 "
                "항상 주입한다는 점이 다르다(`skill_pool.augmentation_mode` "
                "'always' 값의 실제 사용례, 사용자 요청 2026-10-27)."
            ),
            "tavily_search",
            "always",
        ),
    ]
    for name, description, native_id, augmentation_mode in skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, ?, ?)",
            (name, description, json.dumps([tool_ids[native_id]]), augmentation_mode),
        )

    # 사용자 요청(2026-09-28, "도구/스킬 Pool에 기본 노출해줘") — HAIR-Platform이
    # 모델링하는 SDLC 팀(백엔드 개발자·QA 등 역할의 AI 에이전트)이 실제로 이
    # 하니스 방법론/agent-evaluator 생태계를 따르는 조직이라는 전제 하에, 그
    # 생태계의 절차 스킬(SKILL.md)들을 Skill Pool에 기본 노출한다. 15개는
    # agent-evaluator[sdk] 기본 배포(Skills/)에 이미 포함된 것, 3개는 이
    # 저장소(AgentForge_Workbook)에서 자체 발굴했지만 아직 그 기본 배포로
    # 역이식되지 않은 것(`.claude/skills/background-real-verification` 등).
    # `augmentation_mode='none'`인 이유: 이 스킬들은 CrewAI Tool로 감쌀 수 있는
    # 실행 가능한 함수가 아니라 Claude Code/OpenCode 세션이 참고하는 절차
    # 문서라서, VectorDB/GraphDB처럼 자동으로 붙일 도구가 없다 — Agent가 이
    # skill_tag를 갖고 있다는 표시만 남긴다(도구 자동화가 생기면 'tool'로
    # 승격 가능).
    agent_eval_skills = [
        (
            "abtest-decision-workflow",
            (
                '"agent-eval abtest"로 두 버전(또는 세 버전 이상)을 비교하기 전에 '
                "주 지표(guardrail metric)를 미리 정하고, 결과를 책임 있게 해석한다."
            ),
        ),
        (
            "checklist-confidence-audit",
            (
                "승인 큐의 체크리스트 자동채점이 실제로 얼마나 정확한지 주기적으로 "
                "점검한다."
            ),
        ),
        (
            "claims-audit-ci",
            (
                'CI에 "agent-eval claims audit"를 등록해 TTL 초과·겹치는 active '
                "클레임을 병합 전에 자동으로 잡는다."
            ),
        ),
        (
            "config-silent-noop-check",
            (
                "Harness Config를 배선했는데 Gate 점수나 가드레일이 기대와 다르게 "
                "반응하지 않을 때, 그 Config가 실제로 채점/차단 경로에 연결돼 있는지 "
                "SDK 소스 레벨에서 확인하는 절차."
            ),
        ),
        (
            "eval-run-directory-hygiene",
            (
                "agent-eval trend/abtest/--baseline이 가짜 회귀를 경고하거나 조용히 "
                "아무 것도 못 찾을 때, 서로 다른 목적의 실행 결과가 한 디렉터리에 "
                "섞였는지 확인하고 정리하는 절차."
            ),
        ),
        (
            "gate-improvement-loop",
            (
                "하나의 Gate 점수를 목표선 위로 올리는 반복 절차 — 한 번에 한 가지만 "
                "바꿔 재측정하고 이터레이션 로그에 남긴다."
            ),
        ),
        (
            "harness-autopilot",
            (
                "Harness Methodology 파이프라인(Phase 0~8)의 현재 위치를 판단하고, "
                "반복되는 초안 작업을 대신 만들고, 사람이 필요한 HITL 관문에서 정확히 "
                "멈춘다."
            ),
        ),
        (
            "harness-gate-ci",
            (
                'CI에 "agent-eval gate"를 연결해 절대 임계값·baseline 회귀·골든셋 '
                "회귀·케이스 회귀를 별개의 CI 스텝으로 나누고 종료 코드별로 다른 "
                "알림으로 라우팅한다."
            ),
        ),
        (
            "liveguardrail-standard-config",
            (
                "새 프로젝트에 LiveGuardrail을 설치할 때 팀 표준 설정(위험 패턴, "
                "스코프, 브랜치 보호)을 그대로 적용한다."
            ),
        ),
        (
            "spec-driven-artifacts",
            (
                "코드를 짜기 전에 Spec·실패 모드 카탈로그·최소 골든셋·세션 목표 "
                "템플릿 + Gate 매핑 초안을 만든다."
            ),
        ),
        (
            "sync-drift-check",
            (
                "GitHub PR 라벨(Phase 상태)과 오토파일럿의 .aoo/tasks/<id>.json이 "
                "서로 어긋나지 않았는지 주기적으로 대조한다."
            ),
        ),
        (
            "tdd-ai-workflow-checklist",
            (
                "TDD-AI(Test-Driven Agent Iteration)의 red-green-refactor 순환을 "
                "실행 세션에서 체크리스트로 따라간다."
            ),
        ),
        (
            "threshold-realism-review",
            (
                "exit 75(통계적으로 애매한 통과)가 같은 사유로 5회 넘게 반복될 때, "
                "Gate 임계값 자체가 현실과 안 맞는 것인지 재검토하는 절차."
            ),
        ),
        (
            "unattended-session-recovery",
            (
                "무인으로 재트리거된 Claude Code/OpenCode 세션이 응답 없이 멈추거나 "
                "중간에 죽었을 때의 표준 복구 절차."
            ),
        ),
        (
            "background-real-verification",
            (
                "실제 API 호출이 필요한 배치 실행을 백그라운드로 돌리고, 완료 알림을 "
                "받은 뒤에만 결과를 보고하는 절차 — 아직 안 끝난 실행 결과를 추측해서 "
                "보고하지 않는다."
            ),
        ),
        (
            "evidence-based-gate-fix",
            (
                "Gate 점수가 fail/warn일 때, SDK 소스 코드를 직접 읽어 채점 공식을 "
                "확인한 뒤 조치하는 절차 — 추측으로 임계값을 조정하지 않는다."
            ),
        ),
        (
            "honest-refuted-experiment-logging",
            (
                "agent-eval experiment/improve verify 결과가 refuted로 나왔을 때, "
                "이걸 지우거나 조용히 넘어가지 않고 커밋·문서에 그대로 남기는 절차."
            ),
        ),
    ]
    for name, description in agent_eval_skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, '[]', 'none')",
            (name, description),
        )

    # 사용자 요청(2026-09-28, "CrewAI AI Agent가 개발 단계에 필요한 기능일지
    # 확인하고, 필요한 경우 AI Agent가 호출할 수 있도록 변형해줘") — 18개 중
    # 17개는 git/PR/CLI 판단·SDK 소스 읽기 같은 HITL 절차라 실행 가능한 도구로
    # 옮길 안전한 방법이 없었지만(오픈 이슈로 그대로 유지), 이거 하나는
    # 예외다: `agent_evaluator.integrations.recommend_fix_mcp`가 이미 실제
    # stdio MCP 서버로 구현돼 있고(`recommend_fix(gate, metric, value) -> str`
    # 노출), `.aoo`/평가 결과 파일이 없어도 정적 지식만으로 안전하게 동작한다
    # (읽기 전용, 부작용 없음) — 그래서 이것만 mcp_stdio Tool Pool 항목으로
    # 승격해 "recommend-fix-workflow" 스킬에 실제로 연결한다. vectordb/graphdb와
    # 같은 이유로 `INSERT OR IGNORE`만 쓴다 — 관리자가 이후 이 스킬의 연결을
    # 직접 바꿔도 재기동 때마다 덮어쓰지 않는다(이미 있으면 아무 것도 안 함).
    # `command`는 `sys.executable`을 그대로 써서, MCPServerAdapter가 스폰하는
    # 서브프로세스가 HAIR-Platform이 실행 중인 바로 그 venv(따라서 같은
    # `agent_evaluator`/`mcp` 패키지)를 쓰도록 보장한다 — PATH의 임의
    # "python"에 의존하지 않는다. `agent-evaluator[mcp]`(=`mcp` 패키지)가
    # 설치돼 있어야 실제로 접속되며, 없으면 기존 MCP 기능과 동일하게
    # `connect_mcp_tools()`의 지연 import에서 실패한다(신규 실패 모드 아님).
    conn.execute(
        "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
        "VALUES (?, ?, 'mcp_stdio', ?)",
        (
            "Gate 조치 지식 조회(recommend_fix)",
            (
                "agent_evaluator.integrations.recommend_fix_mcp — Gate A-G/지표가 "
                "나쁠 때 정적 지식으로 조치를 추천하는 읽기 전용 stdio MCP 서버. "
                ".aoo 실행 이력이 없어도 안전하게 동작한다."
            ),
            json.dumps(
                {
                    "command": sys.executable,
                    "args": ["-m", "agent_evaluator.integrations.recommend_fix_mcp"],
                }
            ),
        ),
    )
    recommend_fix_tool_row = conn.execute(
        "SELECT id FROM tool_pool WHERE name = ?",
        ("Gate 조치 지식 조회(recommend_fix)",),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, ?, ?, 'tool')",
        (
            "recommend-fix-workflow",
            (
                "Gate A-G 중 하나가 fail/warn일 때 코드를 고치기 전에 조치 후보를 "
                "조회하고, 적용 후 실제로 통했는지 검증하는 절차 — recommend_fix "
                "MCP 도구가 실제로 붙어 있어 Agent가 직접 조회를 호출할 수 있다."
            ),
            json.dumps([recommend_fix_tool_row["id"]]),
        ),
    )

    # 사용자 요청(2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지
    # 확인해줘" → "진행해줘") — `websearch.py`(native, Wikipedia)는
    # 자체 색인 안의 백과사전 내용만 다룬다. "최신 뉴스" 같은 진짜
    # 일반 웹 검색은 Wikipedia로 안 된다. 리서치 결과 Bing Search API는
    # 2025-08-11 완전 퇴역했고, Google Custom Search는 2027-01-01부로
    # 신규 고객을 안 받으며, Brave/Tavily는 카드 등록 또는 계정+키가
    # 필요해 "가능한 무료" 조건과 안 맞았다. Parallel AI의 공개 MCP
    # 서버(`https://search.parallel.ai/mcp`)만 계정·키 없이 완전
    # 무료로 접속 가능해(익명 분당 600건) 이걸 쓴다 — `mcp_remote`
    # 종류라 `websearch.py`처럼 새 Python 모듈을 쓸 필요 없이 기존
    # `connect_mcp_tools()` 메커니즘 그대로 재사용한다(recommend_fix와
    # 같은 자리, `mcp_stdio` 대신 `mcp_remote`라는 것만 다름). 실제로
    # 접속해 `web_search`/`web_fetch` 두 Tool이 열리는 것과, "2026
    # 한국 최저임금" 같은 실제 최신 뉴스 검색까지 라이브로 확인했다.
    #
    # **다른 native/recommend_fix 도구와의 차이(중요)**: 이건 상업
    # 회사(Parallel AI)의 무료 티어라 Wikipedia API처럼 안정적인 공공
    # 인프라가 아니다 — 조건이 바뀌거나 중단될 수 있다. 또한 `mcp_remote`
    # 이므로 `agent_runtime.augment_with_retrieval()`(정적 프롬프트 주입,
    # 협업 워크스페이스의 단일 LLM 호출 경로)에서는 못 쓴다 — 그 경로는
    # Agent 호출 루프 자체가 없어 native 함수 직접 호출만 가능하다.
    # "Crew 실행"/"AI 에이전트 생성(crewai)"처럼 실제 CrewAI Agent
    # 루프가 있는 경로에서만 Agent가 스스로 호출 여부를 판단해 쓸 수
    # 있다.
    conn.execute(
        "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
        "VALUES (?, ?, 'mcp_remote', ?)",
        (
            "일반 웹 검색(Parallel Search)",
            (
                "Parallel AI의 무료 공개 MCP 서버 — 인터넷 전체를 대상으로 한 "
                "일반 웹 검색·페이지 조회(API 키/계정 불필요, 익명 접근 분당 "
                "600건). Wikipedia로 못 찾는 최신 뉴스·일반 웹 정보가 필요할 "
                "때 쓴다. 상업 회사의 무료 티어라 조건이 바뀌거나 중단될 수 "
                "있다 — 접속 실패 시 이 스킬을 가진 Agent 실행이 접속 단계에서 "
                "실패한다(다른 mcp_remote/mcp_stdio 도구와 동일한 위험). "
                "CrewAI Agent 루프가 있는 경로(Crew 실행/AI 에이전트 생성)에서만 "
                "쓸 수 있다 — 협업 워크스페이스의 단일 실행 경로에서는 정적 "
                "주입만 가능해 이 도구를 못 쓴다."
            ),
            json.dumps(
                {
                    "url": "https://search.parallel.ai/mcp",
                    "transport": "streamable-http",
                }
            ),
        ),
    )
    websearch_mcp_tool_row = conn.execute(
        "SELECT id FROM tool_pool WHERE name = ?",
        ("일반 웹 검색(Parallel Search)",),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, ?, ?, 'tool')",
        (
            "일반웹검색",
            (
                "Wikipedia에 없는 최신 뉴스·일반 웹 정보가 필요할 때 인터넷 "
                "전체를 검색한다 — Parallel Search MCP 도구(web_search/"
                "web_fetch)가 실제로 붙어 있어 Agent가 직접 호출할 수 있다. "
                "CrewAI Agent 루프가 있는 실행 경로에서만 동작한다."
            ),
            json.dumps([websearch_mcp_tool_row["id"]]),
        ),
    )


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
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 시드가 전부 끝난 뒤에만
    # 돌려야 한다(위 show_in_conversation 백필과 같은 이유 — 레퍼런스
    # Agent/Tool/Skill 자체가 아직 안 생긴 시점엔 코드를 배정할 대상이
    # 없다). 이미 code가 있는 행은 건드리지 않아 매 기동마다 불러도
    # 안전하다.
    id_scheme.backfill_missing_codes(conn)
    conn.commit()
