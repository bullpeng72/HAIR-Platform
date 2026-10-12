"""Pydantic v2 요청 모델 — v4(전체) 범위까지 다룬다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

LlmProvider = Literal["anthropic", "openai", "ollama"]
# 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT, HTML
# 등 다양할 수 있을텐데... Tier 2까지 진행해줘") — `deliverables.
# content_type`과 같은 값 집합. 실제 파일(PPT 등) 생성·저장은 범위
# 밖(Tier 3)이라 여기엔 없다 — "code"는 신택스 하이라이팅만 된 텍스트로
# 저장·표시되는 것이지 실행 가능한 파일이 아니다.
ContentType = Literal["text", "markdown", "html", "code"]
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


class UserPasswordReset(BaseModel):
    """사용자 요청(2026-09-29, "비밀번호 조회/초기화 기능 추가 필요") —
    비밀번호는 bcrypt 해시라 "조회"는 원리상 불가능하다(평문을 저장하지
    않음). "초기화"만 제공한다 — 관리자가 새 비밀번호를 지정하면 그
    사용자에게 별도 채널로 전달하는 방식(ADMIN 전용)."""

    password: str


class UserStatusUpdate(BaseModel):
    """사용자 요청(2026-09-29, "사용자 삭제 기능 필요") — `users.id`는
    audit_log/assignments/approvals/human_pool.user_id에서 NOT NULL로
    참조돼 하드 삭제가 불가능하다(NF10 감사 이력 보존과 충돌). Human/
    Agent Pool의 오프보딩과 같은 "비활성화" 방식을 쓴다(ADMIN 전용)."""

    is_active: bool


class BriefSubmission(BaseModel):
    """F1 — 규칙기반 스텁. 실제 Spec Interpreter(LLM) 연동은 이후 버전에서 이뤄진다."""

    raw_text: str


class HumanPoolCreate(BaseModel):
    """F12 — Human Pool 온보딩 폼.

    `user_id`는 사용자 보고(2026-10-06, "사람 인력으로 등록되어 있지만
    사용자 관리에 등록되지 않은 경우 로그인도 안 돼 일을 할 수 없다")로
    확인한 구조적 갭을 메운다 — 이전엔 Human Pool 등록이 로그인 계정과
    완전히 무관해서, 계정 연결을 깜빡해도 매칭·배정·승인까지 전부
    경고 없이 통과됐다. 등록 시점에 바로 기존 계정을 골라 연결할 수
    있게 해 그 갭을 줄인다(여전히 optional — 계정 없는 순수 인력 계획
    레코드도 기존처럼 가능)."""

    name: str
    role: str
    skill_tags: list[str] = []
    availability: str = "available"
    cost_estimate: float = 0.0
    manager_id: int | None = None
    user_id: int | None = None


class HumanPoolUpdate(BaseModel):
    """코드 재점검(R3)에서 발견 — Agent Pool엔 onboard/deprecate 라이프사이클이
    있는데 Human Pool엔 생성 후 수정할 방법이 전혀 없던 비대칭을 없앤다.
    전부 optional — 보낸 필드만 갱신한다.

    `user_id`는 협업 워크스페이스 구현(사용자 요청, 2026-09-28) 중 발견한
    진짜 갭을 메운다 — `human_pool.user_id`(F8 `review_for_member()`가 이미
    참조하던 컬럼)를 실제로 채울 API가 지금까지 전혀 없었다(항상 NULL).
    이 필드가 없으면 어떤 Human Pool 레코드도 로그인 계정과 연결될 수 없어
    F8의 "내 성과점수 조회"도, 이번 협업 워크스페이스의 "내 업무"도 사람
    쪽 결과를 절대 낼 수 없었다.

    `manager_id`는 사용자 요청(2026-09-29, "admin, 사용자 계정을 정리해줘")
    으로 계정을 정리하려다 발견한 같은 종류의 갭을 메운다 — `GET
    /api/org-chart`(`app.py`)는 이미 `human_pool.manager_id`를 읽어 사람을
    관리자별로 묶는데, 그 값을 채울 API가 `users.manager_id`용
    `PATCH /api/users/{id}/manager`만 있었고 `human_pool` 쪽엔 전혀
    없었다(스키마엔 컬럼이 처음부터 있었지만 항상 NULL — 모든 사람이
    "관리자 없음" 버킷에 뭉뚱그려짐)."""

    role: str | None = None
    skill_tags: list[str] | None = None
    availability: str | None = None
    cost_estimate: float | None = None
    user_id: int | None = None
    manager_id: int | None = None


class ApprovalDecision(BaseModel):
    """`reason`(2026-10-07, 실사용 리뷰로 발견한 갭) — 결재자가 그
    subject(assignment/deliverable)의 task 참가자가 아닌 경우가 흔한데
    (F7 체인은 조직도 기준이지 참가 여부와 무관), 참가자 전용인 댓글
    기능으로는 반려 사유를 전혀 남길 수 없었다. 비워도 되는 선택
    필드 — 주면 감사로그에 남고, `subject_type='deliverable'`이면
    그 산출물의 댓글 타임라인에도 결재자 자격으로(참가자 여부 무관)
    남는다(`deliverables.record_decision_comment()`)."""

    approve: bool
    reason: str | None = None


class TaskItem(BaseModel):
    """F2 — Resource Designer가 산출한 Task 분해 결과 한 건(ADR 결정 1-A: 후보 매칭 미포함)."""

    role_name: str
    required_skills: list[str] = []
    headcount: int = 1


class TaskDecomposition(BaseModel):
    """F2 — 매니저가 검토·조정한 Task 분해 결과 전체."""

    tasks: list[TaskItem]


class ResourceCandidate(BaseModel):
    """ADR 결정 4 — Human Pool·Agent Pool 공통 인터페이스.

    `user_id`는 사용자 보고(2026-10-06, "매칭/배정 화면에도 계정 미연결
    경고 추가해줘")로 추가했다 — `type="human"`인데 `user_id`가 `None`이면
    로그인 계정이 연결 안 된 사람 인력이라, 매칭·배정이 돼도 당사자가
    "내 업무"에서 그 일을 볼 방법이 없다(`docs/LIMITS.md` §51). Agent는
    로그인 계정 개념이 없어 항상 `None`이다."""

    id: int
    type: Literal["human", "agent"]
    role: str
    is_available: bool
    cost: float
    skill_tags: list[str]
    performance_score: float | None = None
    user_id: int | None = None


class AssignmentRequest(BaseModel):
    """F6 — 배분 화면에서 매니저가 후보를 배정 요청한다(상태는 항상 requested로 시작).

    사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) —
    `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md`. `resource_type`에
    `"team"`을 추가했다 — `resource_id`는 이때 `teams.id`다.
    `team_leader_id`는 팀 배정의 F7 결재 주체(비워두면 요청자 본인이
    기본값). `replaces_assignment_id`는 "다중 confirmed 허용"으로 바뀐
    뒤에도 "이 배정이 저 배정을 명시적으로 교체한다"를 표현하는 선택적
    필드 — 비워두면 기존 confirmed 배정들과 그냥 공존한다(교체 아님)."""

    resource_type: Literal["human", "agent", "team"]
    resource_id: int
    team_leader_id: int | None = None
    replaces_assignment_id: int | None = None


class DelegateAgentRequest(BaseModel):
    """SCREEN_UX_BENCHMARK_PROPOSAL.md §4-D — "위임"(결재 없는 경량 연결,
    `AssignmentRequest`의 "배정"과 대칭). Agent 전용이라 `resource_type`
    선택지가 없다."""

    agent_id: int


class TeamLeaderUpdate(BaseModel):
    team_leader_id: int


class TeamCreate(BaseModel):
    name: str
    description: str = ""


class TeamActiveUpdate(BaseModel):
    is_active: bool


class TeamMemberAdd(BaseModel):
    member_type: Literal["human", "agent"]
    member_id: int
    role_label: str = ""


class DeliverableCreate(BaseModel):
    """협업 워크스페이스(사용자 요청, 2026-09-28) — 사람이 작성한 결과물을
    올린다. `docs/PHASE1_DELIVERABLE_SPEC.md` §2-3.

    `content_type`(2026-10-07, Tier 2)은 작성자가 직접 고른다 — 사람이
    쓰는 글이라 LLM 프롬프트 지시가 필요 없고, 본인이 뭘 썼는지 가장
    잘 안다."""

    content: str
    content_type: ContentType = "text"


class DeliverableCommentCreate(BaseModel):
    """협업 워크스페이스 — deliverable에 대한 댓글. §2-4."""

    content: str


class TaskNotesUpdate(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md F17(2026-10-08) — Task 메모. 결과물이
    아직 없는 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트
    공간이다(마크다운으로 렌더링)."""

    notes: str


class TaskParticipantInvite(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md F15(2026-10-08, Phase 2) — 결재가
    필요 없는 가벼운 참가자 초대. `participant_id`는 `participant_type=
    'human'`이면 `users.id`(human_pool.id 아님 — db.py의 task_participants
    테이블 주석 참고), `'agent'`면 `agent_pool.id`."""

    participant_type: Literal["human", "agent"]
    participant_id: int


TaskStatus = Literal["backlog", "queued", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class TaskStatusUpdate(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — 할 일 보드
    칸반 상태."""

    status: TaskStatus


class TaskPriorityUpdate(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4)."""

    priority: TaskPriority


class TaskDependencyCreate(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md F16(2026-10-08, Phase 4) — "이 task는
    `depends_on_task_id`가 끝나야 시작할 수 있다". 순환 검사는
    `deliverables.add_task_dependency()`가 한다."""

    depends_on_task_id: int


class AssistRequest(BaseModel):
    """협업 워크스페이스 "LLM으로 도와줘" — `provider`/`model`은 매니저/관리자만
    채울 수 있다(팀원이 채우면 app.py가 400으로 거부, §3 RBAC 결정)."""

    draft_context: str | None = None
    provider: LlmProvider | None = None
    model: str | None = None
    content_type: ContentType = "text"


class ExecuteAgentRequest(BaseModel):
    """협업 워크스페이스 — task에 배정된 Agent를 실행한다. §2-6.

    `agent_id`는 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2)
    으로 추가했다 — "다중 confirmed 허용"으로 한 task에 confirmed agent
    배정이 여러 건 공존할 수 있게 되면서, "그중 어떤 Agent를 실행할지"가
    더 이상 자명하지 않다. 비워두면 기존처럼 confirmed agent가 정확히
    1건일 때만 자동으로 그걸 쓰고, 여러 건이면 명시를 요구한다(하위
    호환 — 지금까지의 유일한 실사용 패턴인 "1 task 1 confirmed agent"는
    동작이 전혀 안 바뀐다)."""

    provider: LlmProvider | None = None
    model: str | None = None
    agent_id: int | None = None
    # 2026-10-07, Tier 2 — 비워두면(기존 동작 그대로) 'text'로 저장되고
    # 프롬프트에 형식 지시를 추가하지 않는다.
    content_type: ContentType = "text"


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


class TeamCrewExecuteRequest(BaseModel):
    """사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
    개선... 사람-에이전트 연동이 가능한 구조인지 확인") —
    `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 3. 이 할 일에
    confirmed로 배정된 팀 중 `team_id` 하나를 골라 그 팀의 Agent
    멤버들을 실제 CrewAI Crew로 실행한다(한 할 일에 팀이 여러 개
    배정돼 있어도 한 번에 하나씩 — 사용자 결정)."""

    team_id: int
    process: CrewProcess = "sequential"
    provider: LlmProvider | None = None
    model: str | None = None
    manager_agent_id: int | None = None
    content_type: ContentType = "text"


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
    `{"native_id": "vectordb_search"|"graphdb_query"|"web_search"}`를
    넣는다(지원하는 native 도구 전체 목록은 `pool_registry.
    NATIVE_TOOL_FACTORIES` 참고 — `web_search`는 사용자 요청(2026-09-29,
    "외부 자료 검색... 도구화") 추가, `websearch.py`의 Wikipedia 검색을
    감싼다, API 키 불필요). `kind="mcp_stdio"|"mcp_remote"`면 `config`에 `AgentModelConfig.
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


class ModelRegistryCreate(BaseModel):
    """사용자 요청(2026-10-06, "운영 관리에 사용 가능한 ollama 모델 및
    openai, anthropic 모델 등록하는 메뉴를 두고 등록된 모델을 불러오는
    방식으로 UI/UX를 개선") — "AI 에이전트 생성"·"모델 설정"·"Crew 실행"의
    자유 텍스트 모델명 입력을 등록된 목록에서 고르는 방식으로 바꾸기
    위한 레지스트리 항목. `GET /api/model-registry/fetch`로 provider가
    실제로 제공하는 모델 후보를 먼저 조회한 뒤, 고른 것만 여기로 등록
    한다(조회와 등록을 분리 — 조회만으로는 아무것도 저장되지 않는다)."""

    provider: LlmProvider
    name: str


class ModelActiveUpdate(BaseModel):
    is_active: bool


class ConversationCreate(BaseModel):
    """ASANA_PARITY_DESIGN_PLAN.md §9(2026-10-08) — 로그인 직후 바로
    쓸 수 있는 독립 대화, 또는 특정 Task에 종속된 대화. `linked_task_id`
    를 주면 후자(생성자가 그 Task 참가자여야 함 — `app.py`가 확인)."""

    title: str = ""
    linked_task_id: int | None = None


class ConversationParticipantInvite(BaseModel):
    """`participant_id`는 `participant_type='human'`이면 `users.id`,
    `'agent'`면 `agent_pool.id`, `'team'`이면 `teams.id`."""

    participant_type: Literal["human", "agent", "team"]
    participant_id: int


class ConversationMessageCreate(BaseModel):
    """`skill`(선택, 사용자 결정 "skill을 조회해 대화창에서 모델에 일을
    시킬 수 있음")을 주면 `skill_pool`의 그 스킬로 그라운딩된 응답을
    명시적으로 요청한다 — `ai_auto_reply` 설정과 무관하게 항상 응답."""

    content: str
    skill: str | None = None


class ConversationModelUpdate(BaseModel):
    provider: LlmProvider | None = None
    model: str | None = None


class ConversationAutoReplyUpdate(BaseModel):
    enabled: bool
