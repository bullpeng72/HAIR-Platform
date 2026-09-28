"""Pydantic v2 요청 모델 — v4(전체) 범위까지 다룬다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

LlmProvider = Literal["anthropic", "openai", "ollama"]
GeneratorFramework = Literal["stub", "simple_llm", "crewai"]
CrewProcess = Literal["sequential", "hierarchical"]
McpTransport = Literal["sse", "streamable-http"]
ToolPoolKind = Literal["mcp_stdio", "mcp_remote", "native"]
AugmentationMode = Literal["tool", "always", "none"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    """조직도(F5/F7) 코드 재점검 후 신규 — `users.manager_id`를 Web UI로 채울 수 있는
    유일한 경로였던 부트스트랩 스크립트 의존을 없앤다(ADMIN 전용)."""

    username: str
    password: str
    role: Literal["admin", "manager", "member", "viewer"]
    manager_id: int | None = None


class ManagerUpdate(BaseModel):
    """F7 결재라인 자동라우팅이 참조하는 `users.manager_id`를 갱신한다(ADMIN 전용)."""

    manager_id: int | None = None


class BriefSubmission(BaseModel):
    """F1 — 규칙기반 스텁. 실제 Spec Interpreter(LLM) 연동은 이후 버전에서 이뤄진다."""

    raw_text: str


class HumanPoolCreate(BaseModel):
    """F12 — Human Pool 온보딩 폼."""

    name: str
    role: str
    skill_tags: list[str] = []
    availability: str = "available"
    cost_estimate: float = 0.0
    manager_id: int | None = None


class HumanPoolUpdate(BaseModel):
    """코드 재점검(R3)에서 발견 — Agent Pool엔 onboard/deprecate 라이프사이클이
    있는데 Human Pool엔 생성 후 수정할 방법이 전혀 없던 비대칭을 없앤다.
    전부 optional — 보낸 필드만 갱신한다.

    `user_id`는 협업 워크스페이스 구현(사용자 요청, 2026-09-28) 중 발견한
    진짜 갭을 메운다 — `human_pool.user_id`(F8 `review_for_member()`가 이미
    참조하던 컬럼)를 실제로 채울 API가 지금까지 전혀 없었다(항상 NULL).
    이 필드가 없으면 어떤 Human Pool 레코드도 로그인 계정과 연결될 수 없어
    F8의 "내 성과점수 조회"도, 이번 협업 워크스페이스의 "내 업무"도 사람
    쪽 결과를 절대 낼 수 없었다."""

    role: str | None = None
    skill_tags: list[str] | None = None
    availability: str | None = None
    cost_estimate: float | None = None
    user_id: int | None = None


class ApprovalDecision(BaseModel):
    approve: bool


class TaskItem(BaseModel):
    """F2 — Resource Designer가 산출한 Task 분해 결과 한 건(ADR 결정 1-A: 후보 매칭 미포함)."""

    role_name: str
    required_skills: list[str] = []
    headcount: int = 1


class TaskDecomposition(BaseModel):
    """F2 — 매니저가 검토·조정한 Task 분해 결과 전체."""

    tasks: list[TaskItem]


class ResourceCandidate(BaseModel):
    """ADR 결정 4 — Human Pool·Agent Pool 공통 인터페이스."""

    id: int
    type: Literal["human", "agent"]
    role: str
    is_available: bool
    cost: float
    skill_tags: list[str]
    performance_score: float | None = None


class AssignmentRequest(BaseModel):
    """F6 — 배분 화면에서 매니저가 후보를 배정 요청한다(상태는 항상 requested로 시작)."""

    resource_type: Literal["human", "agent"]
    resource_id: int


class DeliverableCreate(BaseModel):
    """협업 워크스페이스(사용자 요청, 2026-09-28) — 사람이 작성한 결과물을
    올린다. `docs/PHASE1_DELIVERABLE_SPEC.md` §2-3."""

    content: str


class DeliverableCommentCreate(BaseModel):
    """협업 워크스페이스 — deliverable에 대한 댓글. §2-4."""

    content: str


class AssistRequest(BaseModel):
    """협업 워크스페이스 "LLM으로 도와줘" — `provider`/`model`은 매니저/관리자만
    채울 수 있다(팀원이 채우면 app.py가 400으로 거부, §3 RBAC 결정)."""

    draft_context: str | None = None
    provider: LlmProvider | None = None
    model: str | None = None


class ExecuteAgentRequest(BaseModel):
    """협업 워크스페이스 — task에 배정된 Agent를 실행한다. §2-6."""

    provider: LlmProvider | None = None
    model: str | None = None


class GenerateRequest(BaseModel):
    """F4 — 생성 버튼. 부족한 역할(Task)들을 지정해 배치로 Agent 생성을 트리거한다.

    `use_llm=True`일 때만 실제 Anthropic/OpenAI/Ollama를 호출한다(사용자 요청,
    2026-09-26) — 기본값은 여전히 무료 규칙기반 스텁이다(비용 발생을 암묵적으로
    유발하지 않기 위해 항상 opt-in). `framework="crewai"`(ADR 결정 10, 2차 요청)면
    `use_llm`을 안 켜도 CrewAI 경로가 쓰이며(CrewAI 자체가 LLM을 호출하므로),
    `use_llm=True`+`framework="simple_llm"`(또는 미지정)는 기존 1회성 LLM 호출과
    동일하다."""

    task_ids: list[int]
    use_llm: bool = False
    framework: GeneratorFramework = "stub"
    provider: LlmProvider | None = None
    model: str | None = None


class AgentExecuteRequest(BaseModel):
    """온보딩된 Agent에게 실제 업무를 맡긴다(사용자 요청, 2026-09-26) — 항상
    실제 LLM API를 호출하므로 비용이 발생한다(Ollama는 로컬 실행). provider/
    model을 안 넘기면 그 Agent에 저장된 모델 설정(`AgentModelConfig`로 지정한
    값)을 우선 쓰고, 그것도 없으면 전역 기본값으로 판별한다."""

    prompt: str
    provider: LlmProvider | None = None
    model: str | None = None


class MCPServerConfig(BaseModel):
    """단일 MCP 서버 접속 설정(ADR 결정 10, 사용자 요청 2026-09-27 — "crewai-tools의
    MCPServerAdapter... 참조해 mcp 기능을 구현해줘"). `crewai-tools`를 실제
    설치해 `MCPServerAdapter`/`mcpadapt.core.MCPAdapt` 소스를 확인해 결정한
    형태다 — stdio(로컬 프로세스로 접속)면 `command`/`args`/`env`를, SSE·
    streamable-http(원격 URL로 접속)면 `url`/`transport`를 채운다(서버
    하나당 둘 중 하나만 유효). `tool_names`를 비워두면 그 서버의 모든
    도구를 허용하고, 채우면 그 이름들만 필터링한다.

    **정정**: ADR 결정 3 계획 당시엔 `mcp_servers: list[str]`(서버 식별자
    문자열 목록)로 적어뒀으나, 실제로 `MCPServerAdapter`를 붙여보니 접속
    정보(command/url 등) 자체가 필요해 구조화된 설정으로 바꿨다."""

    command: str | None = None
    args: list[str] = []
    env: dict[str, str] | None = None
    url: str | None = None
    transport: McpTransport = "sse"
    tool_names: list[str] = []


class AgentModelConfig(BaseModel):
    """Agent별 기본 LLM provider/model(+ MCP 서버 설정)을 저장한다(사용자 요청,
    2026-09-26, 2차 — Ollama 추가와 함께 요청된 '모델 설정 기능'; MCP는
    2026-09-27 추가). 비용이 발생하지 않는 설정 전용 엔드포인트다 — 이걸로는
    LLM/MCP를 호출하지 않는다. `mcp_servers`를 아예 안 보내면(None) 기존
    설정을 그대로 둔다 — 빈 배열 `[]`을 보내야 실제로 전부 지운다."""

    provider: LlmProvider
    model: str | None = None
    mcp_servers: list[MCPServerConfig] | None = None


class CrewRunRequest(BaseModel):
    """ADR 결정 11 — 여러 Agent를 CrewAI Crew로 묶어 실제로 협업 실행한다
    (사용자 요청, 2026-09-26, 3차). `agent_ids`와 `task_descriptions`는 순서가
    같아야 한다(N번째 Agent가 N번째 Task를 맡는다). `process="sequential"`이면
    Task.context로 앞 Agent의 산출물이 다음 Agent에게 자동 전달되고,
    `"hierarchical"`이면 매니저가 동적으로 작업을 조율한다(비용 예측이
    더 어려움 — ADR 결정 11 한계 참고). 항상 실제 LLM API를 호출한다(비용 발생)."""

    agent_ids: list[int]
    task_descriptions: list[str]
    process: CrewProcess = "sequential"
    provider: LlmProvider | None = None
    model: str | None = None
    manager_agent_id: int | None = None


class KnowledgeIndexRequest(BaseModel):
    """지식검색 Agent(VectorDB, ADR 결정 11)가 검색할 수 있도록 텍스트를
    임베딩해 저장한다. `text`를 안 주면 그 Agent의 현재 `last_output`을
    색인한다. 실제 임베딩 API를 호출하므로 비용이 발생한다(opt-in)."""

    text: str | None = None
    provider: LlmProvider | None = None
    model: str | None = None


class ConsentRequest(BaseModel):
    """F8 — 사람 성과데이터 수집 사전 동의(opt-in, NF9)."""

    consent: bool


class PerformanceScoreRequest(BaseModel):
    """F8 — 성과 점수 입력(0~1). Gate 실측 배선 전까지는 수동 입력이다."""

    score: float


class CostUpdateRequest(BaseModel):
    """F10 — Agent 비용 수동 입력(1차 범위, HR/LLM 과금 연동은 오픈 이슈)."""

    cost_estimate: float


class ToolPoolCreate(BaseModel):
    """ADR 결정 12(사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘") — 실제 도구(MCP 서버 또는 native `@tool`) 하나를
    이름으로 등록해 여러 Agent가 재사용한다. `kind="native"`면 `config`에
    `{"native_id": "vectordb_search"|"graphdb_query"}`를 넣는다(현재
    지원하는 native 도구는 이 둘뿐 — `pool_registry.NATIVE_TOOL_FACTORIES`
    참고). `kind="mcp_stdio"|"mcp_remote"`면 `config`에 `AgentModelConfig.
    mcp_servers`와 같은 형태(command/args/env 또는 url/transport)를
    넣는다."""

    name: str
    description: str = ""
    kind: ToolPoolKind
    config: dict = {}
    tool_names: list[str] = []


class ToolActiveUpdate(BaseModel):
    """Tool Pool 항목을 비활성화하면(`is_active=False`) 그 도구를 참조하는
    모든 Agent에서 즉시 빠진다 — 삭제 대신 비활성화만 지원한다(참조
    무결성 보존, `agent_tool_links`는 그대로 둔다)."""

    is_active: bool


class SkillPoolCreate(BaseModel):
    """ADR 결정 12 — 자유 문자열이던 `skill_tags`를 정식 엔티티로 승격한다.
    `augmentation_mode="tool"`이면 `default_tool_pool_ids`가 가리키는
    도구가 이 스킬을 가진 모든 Agent에 자동으로 붙는다(`run_crew()`/
    `crewai_agent_generator()`에서 매번 다시 계산 — 연결을 미리 저장해두지
    않는다). `"always"`/`"none"`은 현재 코드에서 참조하지 않는 예약값이다
    (오픈 이슈, `docs/LIMITS.md` §18)."""

    name: str
    description: str = ""
    default_tool_pool_ids: list[int] = []
    augmentation_mode: AugmentationMode = "tool"
