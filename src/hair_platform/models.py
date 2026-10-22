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
ToolPoolKind = Literal["mcp_stdio", "mcp_remote", "native", "custom_python"]
AugmentationMode = Literal["tool", "always", "none"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    """조직도 코드 재점검 후 신규 — `users.manager_id`를 Web UI로 채울 수 있는
    유일한 경로였던 부트스트랩 스크립트 의존을 없앤다(ADMIN 전용)."""

    username: str
    password: str
    role: Literal["admin", "manager", "member", "viewer"]
    manager_id: int | None = None


class ManagerUpdate(BaseModel):
    """결재라인 자동라우팅이 참조하는 `users.manager_id`를 갱신한다(ADMIN 전용)."""

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
    참조돼 하드 삭제가 불가능하다(감사 이력 보존과 충돌). Human/
    Agent Pool의 오프보딩과 같은 "비활성화" 방식을 쓴다(ADMIN 전용)."""

    is_active: bool


class BriefSubmission(BaseModel):
    """규칙기반 스텁. 실제 LLM 기반 해석기 연동은 이후 버전에서 이뤄진다."""

    raw_text: str


class HumanPoolCreate(BaseModel):
    """Human Pool 온보딩 폼.

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
    진짜 갭을 메운다 — `human_pool.user_id`(`review_for_member()`가 이미
    참조하던 컬럼)를 실제로 채울 API가 지금까지 전혀 없었다(항상 NULL).
    이 필드가 없으면 어떤 Human Pool 레코드도 로그인 계정과 연결될 수 없어
    "내 성과점수 조회"도, 이번 협업 워크스페이스의 "내 업무"도 사람
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
    (결재 체인은 조직도 기준이지 참가 여부와 무관), 참가자 전용인 댓글
    기능으로는 반려 사유를 전혀 남길 수 없었다. 비워도 되는 선택
    필드 — 주면 감사로그에 남고, `subject_type='deliverable'`이면
    그 산출물의 댓글 타임라인에도 결재자 자격으로(참가자 여부 무관)
    남는다(`deliverables.record_decision_comment()`)."""

    approve: bool
    reason: str | None = None


class TaskItem(BaseModel):
    """Resource Designer가 산출한 Task 분해 결과 한 건(후보 매칭 미포함)."""

    role_name: str
    required_skills: list[str] = []
    headcount: int = 1


class TaskDecomposition(BaseModel):
    """매니저가 검토·조정한 Task 분해 결과 전체."""

    tasks: list[TaskItem]


class ResourceCandidate(BaseModel):
    """Human Pool·Agent Pool 공통 인터페이스.

    `user_id`는 사용자 보고(2026-10-06, "매칭/배정 화면에도 계정 미연결
    경고 추가해줘")로 추가했다 — `type="human"`인데 `user_id`가 `None`이면
    로그인 계정이 연결 안 된 사람 인력이라, 매칭·배정이 돼도 당사자가
    "내 업무"에서 그 일을 볼 방법이 없다. Agent는
    로그인 계정 개념이 없어 항상 `None`이다.

    `name`은 사용자 보고(2026-10-13, "에이전트 ID만 노출되고 사람이름이나
    에이전트명이 표시되지 않는 경우도 있어") — `role`은 직무 타이틀(예:
    "백엔드 개발자")이라 같은 역할의 사람이 여럿이면 매칭 결과 화면에서
    ID 숫자로만 구분해야 했다. `human_pool.name`(실제 사람 이름)을 채워
    그 갭을 메운다. Agent는 역할명(`role_name`)이 곧 식별자라 별도
    `name`이 없다(`None`으로 둔다 — 화면에서는 `role`을 그대로 쓴다)."""

    id: int
    type: Literal["human", "agent"]
    role: str
    is_available: bool
    cost: float
    skill_tags: list[str]
    performance_score: float | None = None
    user_id: int | None = None
    name: str | None = None


class AssignmentRequest(BaseModel):
    """배분 화면에서 매니저가 후보를 배정 요청한다(상태는 항상 requested로 시작).

    사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2)으로
    `resource_type`에 `"team"`을 추가했다 — `resource_id`는 이때 `teams.id`다.
    `team_leader_id`는 팀 배정의 결재 주체(비워두면 요청자 본인이
    기본값). `replaces_assignment_id`는 "다중 confirmed 허용"으로 바뀐
    뒤에도 "이 배정이 저 배정을 명시적으로 교체한다"를 표현하는 선택적
    필드 — 비워두면 기존 confirmed 배정들과 그냥 공존한다(교체 아님)."""

    resource_type: Literal["human", "agent", "team"]
    resource_id: int
    team_leader_id: int | None = None
    replaces_assignment_id: int | None = None


class DelegateAgentRequest(BaseModel):
    """"위임"(결재 없는 경량 연결, `AssignmentRequest`의 "배정"과
    대칭). Agent 전용이라 `resource_type` 선택지가 없다."""

    agent_id: int


class TeamLeaderUpdate(BaseModel):
    team_leader_id: int


class AssignmentCancelRequest(BaseModel):
    reason: str | None = None


class TeamCreate(BaseModel):
    name: str
    description: str = ""


class TeamActiveUpdate(BaseModel):
    is_active: bool


class TeamDefaultsUpdate(BaseModel):
    """사용자 요청(2026-10-20, "고급 에이전트 관리") — 지금까지 "팀 Agent
    실행"을 누를 때마다 process·매니저를 매번 새로 골라야 했다. 팀에
    기본값을 저장해두면 실행 화면이 이 값을 미리 채우고(여전히 그
    자리에서 바꿀 수 있다), `@팀멘션`도 이 매니저를 우선 쓴다
    (`teams.manager_agent_id_for_team()` 참고). 부분 갱신
    (`exclude_unset=True`로 읽는다) — `None`을 명시적으로 보내면 그
    기본값을 지운다(다시 매번 선택/기존 자동 추정으로 되돌림)."""

    default_process: CrewProcess | None = None
    default_manager_agent_id: int | None = None


class TeamMemberAdd(BaseModel):
    member_type: Literal["human", "agent"]
    member_id: int
    role_label: str = ""


class TeamMemberReorder(BaseModel):
    """사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 이 팀의
    현재 Agent 타입 멤버(team_members.id) 전체를 원하는 순서로 나열한
    리스트. `teams.reorder_team_members()` 참고."""

    member_row_ids: list[int]


class DeliverableCreate(BaseModel):
    """협업 워크스페이스(사용자 요청, 2026-09-28) — 사람이 작성한 결과물을
    올린다.

    `content_type`(2026-10-07, Tier 2)은 작성자가 직접 고른다 — 사람이
    쓰는 글이라 LLM 프롬프트 지시가 필요 없고, 본인이 뭘 썼는지 가장
    잘 안다."""

    content: str
    content_type: ContentType = "text"


class DeliverableCommentCreate(BaseModel):
    """협업 워크스페이스 — deliverable에 대한 댓글."""

    content: str


class TaskNotesUpdate(BaseModel):
    """2026-10-08 — Task 메모. 결과물이
    아직 없는 단계에서도 참가자들이 맥락/의사결정을 적어두는 자유 텍스트
    공간이다(마크다운으로 렌더링)."""

    notes: str


class TaskParticipantInvite(BaseModel):
    """2026-10-08, Phase 2 — 결재가
    필요 없는 가벼운 참가자 초대. `participant_id`는 `participant_type=
    'human'`이면 `users.id`(human_pool.id 아님 — db.py의 task_participants
    테이블 주석 참고), `'agent'`면 `agent_pool.id`."""

    participant_type: Literal["human", "agent"]
    participant_id: int


TaskStatus = Literal["backlog", "queued", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class TaskStatusUpdate(BaseModel):
    """2026-10-08, Phase 4 — 할 일 보드 칸반 상태."""

    status: TaskStatus


class TaskPriorityUpdate(BaseModel):
    """2026-10-08, Phase 4."""

    priority: TaskPriority


class TaskDependencyCreate(BaseModel):
    """2026-10-08, Phase 4 — "이 task는
    `depends_on_task_id`가 끝나야 시작할 수 있다". 순환 검사는
    `deliverables.add_task_dependency()`가 한다."""

    depends_on_task_id: int


class AssistRequest(BaseModel):
    """협업 워크스페이스 "LLM으로 도와줘" — `provider`/`model`은 매니저/관리자만
    채울 수 있다(팀원이 채우면 app.py가 400으로 거부, RBAC 결정)."""

    draft_context: str | None = None
    provider: LlmProvider | None = None
    model: str | None = None
    content_type: ContentType = "text"


class ExecuteAgentRequest(BaseModel):
    """협업 워크스페이스 — task에 배정된 Agent를 실행한다.

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
    """생성 버튼. 부족한 역할(Task)들을 지정해 배치로 Agent 생성을 트리거한다.

    `use_llm=True`일 때만 실제 Anthropic/OpenAI/Ollama를 호출한다(사용자 요청,
    2026-09-26) — 기본값은 여전히 무료 규칙기반 스텁이다(비용 발생을 암묵적으로
    유발하지 않기 위해 항상 opt-in). `framework="crewai"`(2차 요청)면
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
    """단일 MCP 서버 접속 설정(사용자 요청 2026-09-27 — "crewai-tools의
    MCPServerAdapter... 참조해 mcp 기능을 구현해줘"). `crewai-tools`를 실제
    설치해 `MCPServerAdapter`/`mcpadapt.core.MCPAdapt` 소스를 확인해 결정한
    형태다 — stdio(로컬 프로세스로 접속)면 `command`/`args`/`env`를, SSE·
    streamable-http(원격 URL로 접속)면 `url`/`transport`를 채운다(서버
    하나당 둘 중 하나만 유효). `tool_names`를 비워두면 그 서버의 모든
    도구를 허용하고, 채우면 그 이름들만 필터링한다.

    **정정**: 계획 당시엔 `mcp_servers: list[str]`(서버 식별자
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


class AgentTuningConfig(BaseModel):
    """사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    하는 기능을 구현해줘" → 명확화: 실제 LLM 가중치 재학습이 아니라 CrewAI
    `Agent`/`LLM`의 동작 파라미터 조정) — `crew_runtime.py`의 CrewAI 생성
    경로(`crewai_agent_generator()`/`run_crew()` 둘 다)에 그대로 전달된다.
    전부 선택 입력이고 `None`이면 CrewAI 기본값을 그대로 쓴다(지정 안 하면
    기존과 100% 동일하게 동작). `stub`/`simple_llm` 프레임워크로 만든
    Agent에는 적용되지 않는다(그 경로엔 애초에 CrewAI Agent 루프가 없다)."""

    max_iter: int | None = None
    max_rpm: int | None = None
    max_execution_time: int | None = None
    max_retry_limit: int | None = None
    reasoning: bool | None = None
    # `reasoning=True`의 짝 필드(2026-10-13, "CrewAI Agent가 최대 효과를
    # 발휘하도록 추가로 필요한 부분 분석·개선") — CrewAI 기본값(`None`)은
    # "준비됐다고 판단할 때까지 무제한 재시도"라, `reasoning`만 켜고 이걸
    # 지정하지 않으면 계획 수립 단계가 끝없이 반복될 수 있다.
    max_reasoning_attempts: int | None = None
    allow_code_execution: bool | None = None
    respect_context_window: bool | None = None
    temperature: float | None = None


class AgentPersonaConfig(BaseModel):
    """사용자 요청(2026-10-20, "CrewAI 에이전트... role, goal, backstory,
    tool... 상세조정하고 수정하기 위한 고급 에이전트 관리 기능") — 지금까지
    `Agent.goal`/`backstory`는 role_name+skill_tags로 매번 자동 생성됐고
    (`crew_runtime._agent_goal()`/`_agent_backstory()`), `Task.
    expected_output`은 플랫폼 전역 하드코딩 문구 하나뿐이었다 — 전부
    관리자가 덮어쓸 방법이 없었다. 부분 갱신(`exclude_unset=True`로
    읽는다) — 필드를 아예 안 보내면 기존 값을 그대로 둔다. **빈
    문자열(`""`)을 명시적으로 보내면** 그 필드를 자동 생성/플랫폼 공통
    기본값으로 되돌린다(`AgentTuningConfig`처럼 `stub`/`simple_llm`
    프레임워크나 솔로 "실행(일 시키기)"에는 적용되지 않는다 — CrewAI
    `Crew` 실행 경로에만 반영된다)."""

    goal: str | None = None
    backstory: str | None = None
    expected_output: str | None = None


class AgentCreateDirect(BaseModel):
    """사용자 요청(2026-10-21, "Agent 직접 생성" — CrewAI Studio UI
    참조) — Brief/Task 분해 없이 role_name·goal·backstory·
    expected_output·skill_tags를 한 번에 입력해 즉시 Agent를
    등록한다. `agent_runtime.create_agent_directly()` 참고."""

    role_name: str
    skill_tags: list[str] = []
    goal: str = ""
    backstory: str = ""
    expected_output: str = ""


class CrewRunRequest(BaseModel):
    """여러 Agent를 CrewAI Crew로 묶어 실제로 협업 실행한다
    (사용자 요청, 2026-09-26, 3차). `agent_ids`와 `task_descriptions`는 순서가
    같아야 한다(N번째 Agent가 N번째 Task를 맡는다). `process="sequential"`이면
    Task.context로 앞 Agent의 산출물이 다음 Agent에게 자동 전달되고,
    `"hierarchical"`이면 매니저가 동적으로 작업을 조율한다(비용 예측이
    더 어려움 — 위 한계 참고). 항상 실제 LLM API를 호출한다(비용 발생)."""

    agent_ids: list[int]
    task_descriptions: list[str]
    process: CrewProcess = "sequential"
    provider: LlmProvider | None = None
    model: str | None = None
    manager_agent_id: int | None = None
    # 사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고..." — Crew
    # 빌더 로드맵 4단계, "Crew 레벨 고급 옵션") — `memory`는 기본 True로
    # 기존 동작을 유지하되 provider가 openai가 아니어서 OPENAI_API_KEY
    # 없이 쓰려는 경우 꺼둘 수 있게 한다. `planning`은 추가 LLM 호출
    # 비용이 들어 기본 False. `manager_provider`/`manager_model`은
    # `manager_agent_id`가 없을 때(=CrewAI 자동 매니저)만 의미가 있다.
    memory: bool = True
    planning: bool = False
    manager_provider: LlmProvider | None = None
    manager_model: str | None = None
    # 사용자 요청(2026-10-20, "독립 작업 병렬 실행" — Crew 빌더 로드맵
    # 5단계) — `process="sequential"`에서만 의미 있다(검증은
    # `crew_runtime.run_crew()`가 한다). 여기 담긴 Agent id의 Task는
    # async_execution=True로 병렬 실행되고 context 체이닝에서 빠진다.
    parallel_agent_ids: list[int] = []


class AdhocCrewExecuteRequest(BaseModel):
    """사용자 요청(2026-10-20, "crewai의 잠재력을 모두 끌어내고...
    에이전트 그룹(crew 구성)을 직관적으로") — "Crew 빌더" 화면의
    "업무에 바로 실행" 모드가 쓴다. `CrewRunRequest`(즉석 테스트
    샌드박스, `/api/crew/run`)와 같은 입력 모양이지만 `task_id`가
    URL path에 있어 결과가 그 Task의 deliverable+결재 체인으로
    쌓인다는 점이 다르다(샌드박스는 결과가 저장되지 않는다).
    `agent_ids`/`task_descriptions`는 순서가 같아야 한다(N번째 Agent가
    N번째 Task를 맡는다 — `CrewRunRequest`와 동일 관례)."""

    agent_ids: list[int]
    task_descriptions: list[str]
    process: CrewProcess = "sequential"
    provider: LlmProvider | None = None
    model: str | None = None
    manager_agent_id: int | None = None
    content_type: ContentType = "text"
    memory: bool = True
    planning: bool = False
    manager_provider: LlmProvider | None = None
    manager_model: str | None = None
    parallel_agent_ids: list[int] = []


class TeamCrewExecuteRequest(BaseModel):
    """사용자 요청(2026-10-07, "crew 실행에서... 복수 그룹 가능하도록
    개선... 사람-에이전트 연동이 가능한 구조인지 확인") — Phase 3.
    이 할 일에
    confirmed로 배정된 팀 중 `team_id` 하나를 골라 그 팀의 Agent
    멤버들을 실제 CrewAI Crew로 실행한다(한 할 일에 팀이 여러 개
    배정돼 있어도 한 번에 하나씩 — 사용자 결정)."""

    team_id: int
    # 사용자 요청(2026-10-20, "고급 에이전트 관리") — `None`이면 "명시
    # 안 함"으로 구분해, 팀에 저장된 `default_process`(없으면
    # "sequential")로 폴백한다(이전엔 항상 "sequential" 고정값이라 팀
    # 기본값을 구분해 쓸 방법이 없었다).
    process: CrewProcess | None = None
    provider: LlmProvider | None = None
    model: str | None = None
    manager_agent_id: int | None = None
    content_type: ContentType = "text"
    memory: bool = True
    planning: bool = False
    manager_provider: LlmProvider | None = None
    manager_model: str | None = None


PipelineStageMode = Literal["single", "sequential_relay", "parallel_delegated"]
GateId = Literal["A", "B", "C", "D", "E", "F", "G"]


class PipelineTemplateCreate(BaseModel):
    """사용자 요청(2026-10-18, "BMAD-METHOD와 하네스 방법론을 참조해...
    개발 Agent 팀을 구성... 일반화") — "단계(stage) + 역할"만 아는 범용
    파이프라인 템플릿. `domain`은 UI 분류용 자유 텍스트일 뿐 엔진 동작에
    영향 없다(예: "software_dev"/"business_strategy")."""

    name: str
    description: str = ""
    domain: str = "general"


class PipelineTemplateActiveUpdate(BaseModel):
    is_active: bool


class PipelineStageAgentSpec(BaseModel):
    role_name: str
    instructions: str = ""


class PipelineStageCreate(BaseModel):
    """`mode`가 "단독 또는 복수의 에이전트가 순차/병행 처리" 요구를 그대로
    반영한다 — `single`(역할 1개) / `sequential_relay`(역할 N개가 순서대로
    앞 사람 결과를 이어받음) / `parallel_delegated`(`lead_role_name` 1명이
    나머지 코워커들에게 실제로 역할별 하위 과업을 쪼개 위임,
    `crew_runtime.run_crew()`의 hierarchical 경로)."""

    name: str
    mode: PipelineStageMode
    agents: list[PipelineStageAgentSpec]
    lead_role_name: str | None = None
    instructions: str = ""
    gate_ids: list[GateId] | None = None
    min_gate_pass_ratio: float = 0.0
    max_retry: int = 2
    requires_human_approval: bool = False
    # 사용자 보고(2026-10-19, "최종 결과물에 코드가 아니라 품질 리포트만
    # 있다") — Task 협업 워크스페이스의 "최종 결과물"이 지금까지 "가장
    # 최근 approved된 deliverable"을 집어, 코드 검증/QA/품질 판정처럼
    # 항상 맨 마지막에 도는 검토 단계의 텍스트가 실제 산출물(예: 구현
    # 단계의 코드)을 밀어냈다. 어느 단계가 "진짜 최종 산출물"인지는
    # 템플릿 작성자만 판단할 수 있어 단계 생성 시 명시하게 한다.
    is_primary_deliverable: bool = False
    # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 이 단계의
    # `Task.expected_output` 기본값. 빈 문자열(기본값)이면
    # `crew_runtime.run_crew()`의 플랫폼 공통 기본 문구를 그대로 쓴다.
    expected_output: str = ""


class PipelineStageUpdate(BaseModel):
    """부분 갱신 — 보낸 필드만 반영한다(`exclude_unset=True`로 읽는다).
    `mode`/`agents`는 구조가 바뀌므로 지원하지 않는다(단계를 지우고 새로
    만드는 쪽이 실행 이력과의 정합성을 지킨다)."""

    name: str | None = None
    instructions: str | None = None
    gate_ids: list[GateId] | None = None
    min_gate_pass_ratio: float | None = None
    max_retry: int | None = None
    requires_human_approval: bool | None = None
    is_primary_deliverable: bool | None = None
    expected_output: str | None = None


class PipelineStageReorder(BaseModel):
    direction: Literal["up", "down"]


class PipelineExecuteRequest(BaseModel):
    pipeline_template_id: int


class KnowledgeIndexRequest(BaseModel):
    """지식검색 Agent(VectorDB)가 검색할 수 있도록 텍스트를
    임베딩해 저장한다. `text`를 안 주면 그 Agent의 현재 `last_output`을
    색인한다. 실제 임베딩 API를 호출하므로 비용이 발생한다(opt-in)."""

    text: str | None = None
    provider: LlmProvider | None = None
    model: str | None = None


class ConsentRequest(BaseModel):
    """사람 성과데이터 수집 사전 동의(opt-in)."""

    consent: bool


class PerformanceScoreRequest(BaseModel):
    """성과 점수 입력(0~1). Gate 실측 배선 전까지는 수동 입력이다."""

    score: float


class CostUpdateRequest(BaseModel):
    """Agent 비용 수동 입력(1차 범위, HR/LLM 과금 연동은 오픈 이슈)."""

    cost_estimate: float


class AgentNameUpdate(BaseModel):
    """사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고...
    호출시(채팅 포함)에도 활용될 수 있도록 개선") — `role_name`(생성
    당시 역할, 식별자 역할까지 겸해 와서 같은 역할의 Agent가 여럿이면
    ID로만 구분해야 했던)과 별개로 부를 수 있는 이름. 빈 문자열/공백은
    "이름 지우기"(다시 role_name으로 표시)로 취급한다 — `app.py`에서
    `None`으로 정규화."""

    name: str | None = None


class ToolPoolCreate(BaseModel):
    """사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘" — 실제 도구(MCP 서버 또는 native `@tool`) 하나를
    이름으로 등록해 여러 Agent가 재사용한다. `kind="native"`면 `config`에
    `{"native_id": "vectordb_search"|"graphdb_query"|"web_search"}`를
    넣는다(지원하는 native 도구 전체 목록은 `pool_registry.
    NATIVE_TOOL_FACTORIES` 참고 — `web_search`는 사용자 요청(2026-09-29,
    "외부 자료 검색... 도구화") 추가, `websearch.py`의 Wikipedia 검색을
    감싼다, API 키 불필요). `kind="mcp_stdio"|"mcp_remote"`면 `config`에 `AgentModelConfig.
    mcp_servers`와 같은 형태(command/args/env 또는 url/transport)를
    넣는다. `kind="custom_python"`(2026-10-13, "@데코레이터 툴을 추가
    생성할 수 있는 기능" 추가)이면 `config`에
    `{"function_source": "def tool_fn(query: str) -> str:\\n    ...",
    "timeout_seconds": 10}`을 넣는다 — `tool_fn`이라는 이름의 함수 하나만
    정의하면 된다(admin 전용 — mcp_stdio/mcp_remote와 같은 신뢰 경계,
    `pool_registry.py` 참고)."""

    name: str
    description: str = ""
    kind: ToolPoolKind
    config: dict = {}
    tool_names: list[str] = []


class ToolPoolUpdate(BaseModel):
    """부분 갱신(`exclude_unset=True`로 읽는다) — 보낸 필드만 반영한다.
    `kind`는 바꿀 수 없다(`config`의 구조 자체가 종류마다 달라 — 바꾸려면
    삭제 후 새로 등록하는 쪽이 안전하다, `PipelineStageUpdate`가 `mode`를
    못 바꾸게 하는 것과 같은 원칙). `is_active`는 기존
    `PATCH /api/tool-pool/{id}/active`를 그대로 쓴다(여기서 중복
    제공하지 않는다). 사용자 요청(2026-10-20, "에이전트 연결 관련 영향을
    주는 도구명/스킬명은 수정 대상에서 제외") — `name`도 바꿀 수 없다:
    `skill_pool.default_tool_pool_ids`는 id로 연결되니 무관하지만, Skill
    `augmentation_mode="tool"` 매칭·Agent `required_skills`/`skill_tags`
    자동완성 등 여러 곳이 이 이름 **문자열**을 그대로 참조한다 — 바꾸면
    그 연결들이 조용히 끊어진다."""

    description: str | None = None
    config: dict | None = None
    tool_names: list[str] | None = None


class ToolActiveUpdate(BaseModel):
    """Tool Pool 항목을 비활성화하면(`is_active=False`) 그 도구를 참조하는
    모든 Agent에서 즉시 빠진다 — 삭제 대신 비활성화만 지원한다(참조
    무결성 보존, `agent_tool_links`는 그대로 둔다)."""

    is_active: bool


class SkillPoolCreate(BaseModel):
    """자유 문자열이던 `skill_tags`를 정식 엔티티로 승격한다.
    `augmentation_mode="tool"`이면 `default_tool_pool_ids`가 가리키는
    도구가 이 스킬을 가진 모든 Agent에 자동으로 붙는다(`run_crew()`/
    `crewai_agent_generator()`에서 매번 다시 계산 — 연결을 미리 저장해두지
    않는다). `"always"`/`"none"`은 현재 코드에서 참조하지 않는 예약값이다
    (오픈 이슈)."""

    name: str
    description: str = ""
    default_tool_pool_ids: list[int] = []
    augmentation_mode: AugmentationMode = "tool"
    # 사용자 요청(2026-10-22, "skill에 대화창 표시 속성 추가, 가능한
    # 것들만 표시되도록 개선") — 대화창 스킬 드롭다운(`/api/skill-pool/
    # names?conversation=true`)에 이 스킬을 보여줄지 여부. mcp_remote/
    # mcp_stdio/custom_python 도구가 붙은 스킬은 대화창 단발 호출
    # 경로에서 구조적으로 실행이 안 되므로(`pool_registry.
    # unsupported_realtime_skill_names()`) 기본값 True를 admin이 직접
    # False로 끌 수 있게 한다.
    show_in_conversation: bool = True


class SkillPoolUpdate(BaseModel):
    """부분 갱신(`exclude_unset=True`로 읽는다) — 보낸 필드만 반영한다.
    `name`은 바꿀 수 없다(`ToolPoolUpdate` 참고 — Agent의 `skill_tags`가
    이 이름 문자열과 정확히 일치해야 매칭되므로, 바꾸면 그 연결이
    조용히 끊어진다)."""

    description: str | None = None
    default_tool_pool_ids: list[int] | None = None
    augmentation_mode: AugmentationMode | None = None
    show_in_conversation: bool | None = None


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
    """2026-10-08 — 로그인 직후 바로
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
