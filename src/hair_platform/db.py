"""SQLite 연결 및 스키마 — v4(전체) 범위까지 다룬다.

ADR 결정 5: 실시간 쓰기가 빈번한 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 쓴다.
ADR 결정 3: `approvals.step`·`approver_id`로 F7 다단계 결재라인(조직도 매니저 관계
기반 자동 라우팅)을 표현한다. NF10: `audit_log`는 트리거로 UPDATE/DELETE를 막아
append-only를 DB 레벨에서 강제한다. NF9: `human_pool.performance_consent`가
False이면 `performance_score`를 절대 채우지 않는다(F8, 사전 동의 기반 최소수집).

ADR 결정 10/11(2026-09-26): `agent_pool.framework`가 어떤 생성 경로(stub/
simple_llm/crewai)로 만들어졌는지 기록한다. `knowledge_chunks`는 지식검색
Agent(VectorDB)의 임베딩 저장소다 — 결정 11에서 정한 대로 별도 벡터DB
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
    headcount INTEGER NOT NULL DEFAULT 1
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
    mcp_servers TEXT NOT NULL DEFAULT '[]'
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
    kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native')),
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
        CHECK (augmentation_mode IN ('tool', 'always', 'none'))
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
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_jobs_task_id
    ON agent_execution_jobs(task_id);

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
    SELECT RAISE(ABORT, 'audit_log is append-only (NF10)');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (NF10)');
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
    # FK로 참조되므로 하드 삭제는 감사 이력·배정 이력을 깨뜨린다(NF10
    # append-only 원칙과 정면 충돌). Human/Agent Pool이 이미 쓰는
    # "오프보딩"(레코드는 남기고 비활성화) 패턴을 계정에도 그대로
    # 적용한다.
    _ensure_column(conn, "users", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "agent_pool", "mcp_servers", "TEXT NOT NULL DEFAULT '[]'")
    # 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    # 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    # 그라운딩(ADR 결정 11)이 실제로 프롬프트에 뭘 주입했는지 사후에
    # 확인할 방법이 어디에도 없었다(결재자가 "이 답변이 실제 근거에
    # 기반했는지" 검증 불가). 실행 job에 근거를 같이 남긴다.
    _ensure_column(conn, "agent_execution_jobs", "retrieved_context", "TEXT")


def _seed_default_pools(conn: sqlite3.Connection) -> None:
    """ADR 결정 12(사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘") — VectorDB/GraphDB를 감싼 native Tool과, 이걸 쓰는
    skill_pool 항목을 기본 제공한다. 이전까지 `agent_runtime.py`의
    `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 하드코딩이 하던 "스킬→도구"
    매핑을 데이터로 옮긴 것 — 이 시드가 없으면 기존 vectordb/graphdb
    skill_tags를 가진 Agent가 갑자기 도구를 못 받게 되므로 반드시 필요하다.
    `name` UNIQUE 제약으로 `INSERT OR IGNORE`가 매 기동마다 안전하게
    멱등이다(이미 있으면 아무 것도 안 함 — 관리자가 이후 수정해도 안
    덮어씀)."""
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
        ("vectordb", "VectorDB 기반 지식검색 역량", "vectordb_search"),
        ("rag", "VectorDB 기반 지식검색 역량(별칭)", "vectordb_search"),
        ("graphdb", "GraphDB 기반 관계분석 역량", "graphdb_query"),
        ("관계질의", "GraphDB 기반 관계분석 역량(별칭)", "graphdb_query"),
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
    # 옮길 안전한 방법이 없었지만(§20 오픈 이슈 그대로 유지), 이거 하나는
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


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    _seed_default_pools(conn)
    conn.commit()
