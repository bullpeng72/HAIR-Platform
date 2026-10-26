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

from hair_platform import reference_pipeline_seeds


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

CREATE TABLE IF NOT EXISTS human_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

CREATE TABLE IF NOT EXISTS agent_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1
);

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
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
    config TEXT NOT NULL DEFAULT '{}',
    tool_names TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skill_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    default_tool_pool_ids TEXT NOT NULL DEFAULT '[]',
    augmentation_mode TEXT NOT NULL DEFAULT 'tool'
        CHECK (augmentation_mode IN ('tool', 'always', 'none')),
    show_in_conversation INTEGER NOT NULL DEFAULT 1
);

-- 사용자 요청(2026-10-06, "운영 관리에 사용 가능한 ollama 모델 및
-- openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을 불러오는
-- 방식으로 UI/UX를 개선") — 지금까지 "모델명" 입력란이 전부 자유
-- 텍스트라, 실제로 쓸 수 있는 모델이 뭔지 모른 채 타이핑해야 했다
-- (ollama의 기본값 "llama3.1"이 로컬에 없어 404가 난 사고가 실제
-- 선례). skill_pool/tool_pool과 같은 패턴으로 "등록→드롭다운에서
-- 선택" 구조를 모델명에도 적용한다.
CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'ollama')),
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
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

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_template_id INTEGER NOT NULL REFERENCES pipeline_templates(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'paused_for_approval', 'completed', 'failed', 'cancelled')),
    current_stage_seq INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_task_id
    ON pipeline_runs(task_id);

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
    finished_at TEXT
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
        conn.execute(
            """
            CREATE TABLE tool_pool_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
                config TEXT NOT NULL DEFAULT '{}',
                tool_names TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
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
    conn.execute(
        """
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
        """
    )
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
    _ensure_column(
        conn, "conversation_participants", "last_read_message_id", "INTEGER"
    )
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
    _ensure_column(conn, "skill_pool", "show_in_conversation", "INTEGER NOT NULL DEFAULT 1")


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
            "외부 자료 검색(Wikipedia)",
            "websearch.context_for_prompt()를 감싼 네이티브 CrewAI Tool — "
            "API 키 불필요, 무료",
            "web_search",
        ),
    ]
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
        ("rag", "VectorDB 기반 지식검색 역량", "vectordb_search"),
        ("graphdb", "GraphDB 기반 관계분석 역량", "graphdb_query"),
        ("외부검색", "외부 자료(Wikipedia) 검색 역량", "web_search"),
    ]
    for name, description, native_id in skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, ?, 'tool')",
            (name, description, json.dumps([tool_ids[native_id]])),
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
                {"url": "https://search.parallel.ai/mcp", "transport": "streamable-http"}
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
    conn.commit()
