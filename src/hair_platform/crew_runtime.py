"""실제 CrewAI 연동 — 사용자 요청, 2026-09-26, 2~3차.

이전까지 Agent 생성은 stub(무료)와 simple_llm(1회성 LLM 호출)만 있었다.
`grep -rniE "crewai" src/`로 확인한 결과 실행 코드에 CrewAI가 실제로 들어간
적이 없었다(`agents_yaml_snippet` 필드는 CrewAI 흉내만 낸 문자열). 이 모듈이
그 갭을 실제로 메운다.

**두 가지 경로**:
1. `crewai_agent_generator()` — 생성 시점에 단일 Agent+Task+Crew(sequential)
   를 실제로 구성해 kickoff한다. `jobs.py`의 기존 `GeneratorFn`
   계약(role_name, required_skills) -> dict)을 그대로 따르므로 stub/simple_llm과
   동일한 방식으로 갈아끼울 수 있다.
2. `run_crew()` — 여러 개의 이미 온보딩된 Agent를 하나의 Crew로 묶어 실제로
   협업 실행한다 — `Task.context`로 앞 Agent의 산출물을 다음 Agent에게
   전달하고(sequential), `allow_delegation=True`로 Agent가 동료에게 실제로
   위임/질문할 수 있게 한다. 위임이 실제로 일어났는지는 CrewAI의 이벤트버스에서
   "Delegate work to coworker"/"Ask question to coworker" 도구 사용 이벤트를
   실시간으로 캡처해서 판단한다(추측이 아니라 실제 발동 여부를 기록 —
   LIMITS L7 교훈과 같은 정신).

**비용 발생 지점**: `crewai_agent_generator()`/`run_crew()`를 호출하면 실제
LLM API가 호출된다(CrewAI가 내부적으로 처리) — `use_llm`/`framework` 게이팅은
호출부(app.py)에서 한다(Gate A-G 때부터의 opt-in 원칙 재적용).

**주의(중요, 실제로 겪은 사고)**: `crewai`(그리고 그게 지연 import하는
litellm)는 **import되는 순간 자체 dotenv.load_dotenv()를 실행한다** — 이
모듈을 그냥 import하기만 해도 `.env`의 API 키가 프로세스 환경변수에 로드된다.
테스트에서는 반드시 `crewai.Crew.kickoff`(가장 바깥쪽 호출 경계)를 직접
mock해야 한다 — 그보다 안쪽(`LLM.call` 등)을 mock하면 CrewAI 내부 재시도
로직이 그걸 우회해 실제 네트워크 호출로 새어나갈 수 있다(이 세션에서 실제로
한 번 발생시켜 확인함).

**MCP(외부 도구) 연동(사용자 요청 2026-09-27)**: `run_crew()`에서
각 Agent의 `agent_pool.mcp_servers`(`AgentModelConfig.mcp_servers`로 설정)에
따라 `crewai-tools`의 `MCPServerAdapter`로 실제 MCP 서버에 접속해 도구를
붙인다(`_mcp_tools()`). `crewai_agent_generator()`(단일 생성)에는 아직
연결하지 않았다 — 그 시점엔 아직 `agent_pool` row가 없어 MCP 설정을 읽어올
곳이 없기 때문이다(온보딩 이후 `run_crew()`에서만 유효, 오픈 이슈로 명시).
`MCPServerAdapter`는 생성 시점에 실제로 네트워크/프로세스 접속을 시도하므로,
이 환경처럼 접속 가능한 MCP 서버가 없으면 mock 경계(`crewai_tools.
MCPServerAdapter` 클래스 자체)로만 검증할 수 있다.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any

from hair_platform import (
    agent_evaluation,
    audit,
    graphquery,
    llm,
    pool_registry,
    teams,
    vectorstore,
    websearch,
)

DELEGATION_TOOL_NAMES = {"Delegate work to coworker", "Ask question to coworker"}

# 사용자 결정(2026-09-29, "CrewAI 위임 캡처가 CrewAI 내부 문자열에
# 하드코딩돼 있는 리스크 — 지금 방어 로직을 추가") — 위 정확한 문자열이
# CrewAI 버전업으로 바뀌면 위임이 실제로 발생해도 `delegations`가 계속
# 비어 있는 "조용한 실패"가 된다. 정확한 이름 매칭을
# 우선하되, 대소문자·표기 변형(예: "co-worker")까지 놓치지 않도록 키워드
# 기반 완화 매칭을 fallback으로 둔다 — 이 기능의 목적(위임 발생 여부의
# 정직한 기록) 상 오탐보다 미탐이 훨씬 치명적이라는 판단.
_DELEGATION_KEYWORDS = ("delegate", "coworker", "co-worker", "ask question")


def _looks_like_delegation(tool_name: str) -> bool:
    if tool_name in DELEGATION_TOOL_NAMES:
        return True
    lowered = tool_name.lower()
    return any(keyword in lowered for keyword in _DELEGATION_KEYWORDS)


def _sanitize_role(name: str) -> str:
    """CrewAI 자체의 `BaseAgentTool.sanitize_agent_name()`과 같은 정규화
    (공백 정리+소문자화) — 아래 `_resolve_delegation_target()`이 LLM이
    자유 텍스트로 써낸 `coworker` 값을 실제 Agent role과 매칭할 때,
    CrewAI가 내부적으로 쓰는 것과 동일한 기준으로 비교해야 "매칭 안 됨"을
    우리가 따로 오탐하지 않는다."""
    return " ".join(name.split()).replace('"', "").casefold()


def _resolve_delegation_target(tool_args, known_roles: list[str]) -> str | None:
    """사용자 요청(2026-10-06, "묶인 Agent간 협업 관계를 다이어그램으로
    표시") — 위임 다이어그램을 그리려면 "누가" 뿐 아니라 "누구에게"가
    필요한데, 기존 `_capture_delegation`은 `event.tool_args`를 아예 안 읽어
    대상을 버리고 있었다. CrewAI의 위임 Tool(`DelegateWorkTool`/
    `AskQuestionTool`)은 둘 다 `coworker` 인자로 대상 role을 받는다
    (crewai 소스 확인: `delegate_work_tool.py`/`ask_question_tool.py`) —
    `ToolUsageEvent.tool_args`에 그 값이 그대로 실려 있어 꺼낼 수 있다.
    LLM이 대소문자·공백을 자유롭게 써내므로, CrewAI 자신의 매칭 방식과
    동일하게 정규화해 실제 Agent role과 맞춰본다 — 못 찾으면(예: 존재
    하지 않는 동료를 호출해 CrewAI가 에러를 반환한 경우) 원문 그대로
    돌려줘 정보를 버리지 않는다."""
    if not isinstance(tool_args, dict):
        return None
    raw = tool_args.get("coworker") or tool_args.get("co_worker")
    if not raw or not isinstance(raw, str):
        return None
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].split(",")[0]
    sanitized = _sanitize_role(raw)
    for role in known_roles:
        if _sanitize_role(role) == sanitized:
            return role
    return raw.strip()


# "반드시 걸어야 한다"고 명시했던 상한(사용자 요청,
# 2026-09-27, "crewai 기능 적절히 적용됐는지 분석" 리뷰에서 실제로 미반영 상태를
# 발견해 반영함). CrewAI Agent 기본값(25)보다 보수적으로 잡는다 — 우리는 Crew
# 전체 Agent에 allow_delegation=True를 켜서 위임 왕복이 겹칠 수 있기 때문이다.
#
# 정정(2026-10-18, "@자율 개발 크루" 품질 분석 실측 — 10명 코워커가 있는
# 실제 파이프라인 구현 단계에서 매니저가 위임 호출을 배열로 묶어 보내다
# 파싱 실패 → 복구 시도로 반복 소모 → 10회를 다 쓰고 "Maximum iterations
# reached"로 강제 종료돼 혼자 급하게 마무리한 산출물(spec 핵심 요구사항
# 누락)을 냈다. "한 번에 한 명씩" 프롬프트 유도는 유지하되(아래
# `hierarchical_description`), 코워커가 많을 때 실수로 한 번 배열로
# 묶어 보내 몇 번 허비해도 이후 한 명씩 다시 시도할 여유가 남도록
# 10 → 16으로 올린다(위임 왕복이 그만큼 더 길어질 수 있다는 뜻이라
# 비용은 늘지만, 중간에 끊겨 핵심 요구사항을 누락하는 쪽이 더 나쁘다).
DELEGATION_MAX_ITER = 16


def _mcp_tools(stack: ExitStack, mcp_servers: list[dict]) -> list:
    """`AgentModelConfig.mcp_servers`(Agent별 개별 MCP 설정)에 실제로
    접속해 CrewAI 도구 목록을 모은다 — 접속 로직 자체는
    `pool_registry.connect_mcp_tools()`에 있다(Tool Pool
    경로와 공유). `mcp_servers`가 비어 있으면 `crewai_tools`/`mcp`를 아예
    import하지 않는다(설치 안 해도 CrewAI의 다른 경로는 그대로 동작해야
    하므로 — 지연 import 원칙)."""
    if not mcp_servers:
        return []
    return [
        tool
        for cfg in mcp_servers
        for tool in pool_registry.connect_mcp_tools(stack, cfg)
    ]


def _skill_based_tools(
    conn: sqlite3.Connection,
    stack: ExitStack,
    skills: list[str],
    agent_id: int | None = None,
) -> list:
    """`skill_pool`/`agent_tool_links`(사용자 요청 2026-09-27
    — "tool_pool·skill_pool 테이블도 실제로 구현해줘")를 조회해 실제
    CrewAI Tool을 붙인다 — `pool_registry.resolve_pool_tools()`에 위임한다.

    **정정 이력**: 처음엔 `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS`를
    직접 하드코딩해서 판별했었는데(2026-09-27, "MCP Tool처럼... 동작하도록
    개선해줘"), 이번 요청으로 그 매핑 자체를 `skill_pool` 데이터로
    옮겼다 — 새 스킬을 추가할 때 코드를 안 고치고 `skill_pool`에 레코드만
    추가하면 된다."""
    return pool_registry.resolve_pool_tools(conn, stack, skills, agent_id=agent_id)


def _static_augmentation(
    conn: sqlite3.Connection, skills: list[str], description: str
) -> str:
    """사용자 결정(2026-09-29, "skill_pool.augmentation_mode의 'always'
    값 실제 구현") — `'tool'` 모드는 이미 위 `_skill_based_tools()`로 진짜
    CrewAI Tool을 붙여 Agent가 스스로 호출 여부를 판단하게 한다. `'always'`
    모드는 그와 달리 **Agent의 판단을 거치지 않고 Task description에
    검색 결과를 강제로 끼워 넣는다** — `agent_runtime.
    augment_with_retrieval()`이 도구 호출 루프가 없는 단일 실행 경로에서
    하던 정적 주입과 같은 성격이지만, 여기서는 `'tool'`과 구분되는
    별도 옵션으로만 켜진다(기본 시드 스킬은 전부 `'tool'`이라 이 함수는
    기본 동작을 바꾸지 않는다 — 관리자가 skill_pool에 `augmentation_mode
    ='always'`인 항목을 만들어야 실제 효과가 생긴다)."""
    native_ids = pool_registry.resolve_static_augmentation_targets(
        conn, skills, modes=("always",)
    )
    if not native_ids:
        return description
    parts = []
    if "vectordb_search" in native_ids:
        hits = vectorstore.search(conn, description)
        if hits:
            parts.append(
                "참고할 과거 산출물:\n" + "\n".join(f"- {h['text']}" for h in hits)
            )
    if "graphdb_query" in native_ids:
        parts.append(graphquery.context_for_prompt(conn))
    if "tavily_search" in native_ids:
        parts.append(websearch.tavily_context_for_prompt(description))
    if not parts:
        return description
    return "\n\n".join(parts) + f"\n\n요청: {description}"


def _reset_crewai_local_task_output_storage() -> None:
    """사용자 보고(2026-10-18, "@자율 개발 크루... 일부 응답이 실패했습니다:
    AI 모델이 일부 작업에서 올바른 형식의 응답을 만들지 못했습니다") 원인
    분석 — reasoning을 꺼도 재현돼 더 깊이 추적한 결과, LLM과는 전혀
    무관했다. 실측(디버그 로깅 켜고 직접 재현, 전체 traceback 확보)으로
    확인한 실제 원인: CrewAI가 **우리 hair.db와 완전히 별개로** 자기
    OS 사용자 데이터 디렉터리(`~/Library/Application Support/
    HAIR-Platform/latest_kickoff_task_outputs.db`, `crewai.utilities.
    paths.db_storage_path()`)에 Task 실행마다 결과를 계속 쌓아두는
    "replay(재실행)" 전용 캐시를 갖고 있다 — 이 플랫폼은 그 replay
    기능을 쓰지 않는다. 두 번째 이후 Task가 끝날 때마다 `crewai/
    utilities/task_output_storage_handler.py`의 `update()`가 그 캐시
    테이블 전체를 `json.loads()`로 다시 읽어들이는데(`crewai/memory/
    storage/kickoff_task_outputs_storage.py:183`), 이전에 서버를 강제
    종료(`kill`)해 그 로컬 SQLite 쓰기가 트랜잭션 중간에 끊긴 적이
    있으면 그 행 하나가 깨진 채로 영구히 남는다 — 그 뒤로는 Task가
    둘 이상인 **모든** CrewAI 실행(이 함수를 쓰는 11인 크루든 단일
    Agent 생성이든)이 그 깨진 행을 읽는 순간 똑같은
    `json.JSONDecodeError("Expecting value: line 1 column 1 (char 0)")`
    로 매번(간헐적이 아니라 결정적으로) 실패한다. 우리가 그 replay
    캐시를 전혀 쓰지 않으므로, 매 실행 전에 통째로 비워 깨진 행이
    쌓일 공간 자체를 없앤다 — "그 예외를 잡아서 재시도"가 아니라
    애초에 못 깨지게 막는 쪽이 근본적인 해결이다."""
    try:
        from crewai.memory.storage.kickoff_task_outputs_storage import (
            KickoffTaskOutputsSQLiteStorage,
        )

        KickoffTaskOutputsSQLiteStorage().delete_all()
    except Exception:  # noqa: S110, BLE001 — 이 캐시는 우리 기능에 전혀 쓰이지
        # 않는 CrewAI 내부 전용 replay 스토리지다 — 비우기 자체가 실패해도
        # (예: 파일 권한) Crew 실행 자체를 막을 이유가 없고, 아무도 보지
        # 않는 내부 캐시 정리 실패를 로그로 남길 가치도 없다.
        pass


# 사용자 보고(2026-10-28, "자율 개발 크루" 실행이 "시간 제한(15분)을
# 넘겨 중단됐습니다"로 실패) 분석 후 조치 — 이 함수가 만드는
# `crewai.LLM`에는 지금까지 `timeout`을 전혀 지정하지 않았다. `llm.py`
# 의 단발 호출 경로(900초/1800초/600초, 과거 실측 장애를 거쳐 튜닝된
# 값)와 달리 이 경로는 litellm/provider SDK의 미지의 내부 기본값에
# 그대로 노출돼 있었다 — 정작 사용자에게 뜬 "15분" 문구는
# `_describe_job_failure()`가 "timed out" 문자열만 보고 값과 무관하게
# 붙이는 고정 문구였을 뿐, 실제로 900초가 적용된 적은 없다(별도로
# `conversations.py`의 해당 메시지도 바로잡는다).
#
# Team 실행(특히 hierarchical)은 매니저가 여러 코워커의 위임 결과를
# 받아 하나의 산출물로 종합하는 호출을 포함해 입출력이 단일 Agent
# 호출보다 크다 — 사용자 결정으로 "추론 tier(1800초)보다 높게"
# 잡는다. 너무 높이면 Gate 재시도가 겹칠 때 프런트 폴링 포기 시한
# (40분)을 넘길 위험이 있어, 그 한도 안에 한 번의 재시도까지 들어오는
# 수준(2000초)으로 둔다.
CREWAI_LLM_TIMEOUT_SECONDS = 2000.0

# 실측(2026-11-08, park_member 로드러너 게임 Ollama 검증) — 로컬 Ollama
# 모델(27B~35B급)은 복잡한 "구현" 단계 호출 하나가 2000초(33분)를 넘겨
# `litellm.Timeout`으로 파이프라인 전체가 실패했다. 위 2000초는 클라우드
# provider(OpenAI/Anthropic) 기준으로 "프런트 폴링 포기 시한(40분) 안에
# 재시도 1회까지 들어오게" 튜닝된 값이라, 전역으로 올리면 그 클라우드
# UX 전제가 깨진다 — provider별로 분리한다. Ollama는 로컬 연산이라
# API 비용 걱정이 없고, 호출자가 애초에 실시간 폴링을 기대하지 않는
# 상황(백그라운드 파이프라인 검증 등)에서 주로 쓰이므로 훨씬 넉넉하게
# 잡는다.
CREWAI_LLM_TIMEOUT_SECONDS_OLLAMA = 5400.0


def _crewai_llm(provider: str, model: str, temperature: float | None = None):
    from crewai import LLM  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    # temperature(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    # 하는 기능") — Agent별 `tuning_config.temperature`가 있을 때만 그
    # Agent 전용 LLM 인스턴스를 새로 만든다(없으면 호출자가 Crew 전체가
    # 공유하는 기존 LLM 인스턴스를 그대로 쓴다 — 매번 새로 안 만들어도
    # 되는 경우까지 쪼갤 이유가 없다).
    timeout_seconds = (
        CREWAI_LLM_TIMEOUT_SECONDS_OLLAMA
        if provider == "ollama"
        else CREWAI_LLM_TIMEOUT_SECONDS
    )
    kwargs: dict[str, Any] = {"timeout": timeout_seconds}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return LLM(model=f"ollama/{model}", base_url=base_url, **kwargs)
    return LLM(model=f"{provider}/{model}", **kwargs)


# 사용자 요청(2026-10-13, "위 1~2까지 반영") — Gate A-G는 산출물이 이미
# 저장된 *뒤에* 사후 평가한다. CrewAI 자체 `Task.guardrail`로 명백히
# 빈/너무 짧은 산출물은 Crew 실행 *안에서* 바로 걸러 자동 재시도시킨다
# (CrewAI가 `guardrail_max_retries`만큼 알아서 재시도 — 기본값 그대로
# 둔다). 길이만 보는 가장 단순한 품질 바닥선이고, 내용의 적절성까지
# 보장하진 않는다 — Gate A-G의 역할을 대체하지 않고 그 앞단의 저렴한
# 1차 방어선일 뿐이라는 걸 명시해둔다.
_GUARDRAIL_MIN_CHARS = 20


def _min_length_guardrail(output):
    # 실측으로 발견한 버그(2026-10-13) — 리턴 타입 annotation을 일부러
    # 안 붙인다. `crewai/task.py`의 guardrail 검증기는
    # `inspect.signature(v).return_annotation`을 그대로 비교하는데, 이
    # 파일은 `from __future__ import annotations`라 어떤 annotation을
    # 적어도 **런타임엔 평가 안 된 문자열**로 남는다(PEP 563 지연 평가 —
    # `inspect.signature()`는 기본적으로 그 문자열을 다시 실제 타입으로
    # 풀어주지 않는다). `Tuple[bool, Any]`를 정확히 써봐도 검증기 눈엔
    # 그냥 문자열이라 매번 "If return type is annotated, it must be
    # Tuple[bool, Any]"로 거부당했다(실제 Crew 실행을 걸어보기 전까진
    # 안 드러나는 함정 — `Task(...)` 생성 시점에만 터진다). 검증기 자체가
    # "annotation이 있으면 검사하되 없으면 요구하지 않는다"고 돼 있어,
    # annotation을 아예 생략하는 게 유일하게 안전한 해법이다.
    text = (output.raw or "").strip()
    if len(text) < _GUARDRAIL_MIN_CHARS:
        return False, (
            f"산출물이 비어있거나 너무 짧습니다(최소 {_GUARDRAIL_MIN_CHARS}자 "
            "필요) — 역할에 맞는 구체적인 내용으로 다시 작성하세요."
        )
    return True, output.raw


# 플랫폼 공통 `Task.expected_output` 기본 문구 — `agent_pool.
# expected_output`/`pipeline_stages.expected_output` 둘 다 비어 있을 때
# 쓰는 최종 폴백이다. 상수로 뽑아 `app.py`(관리 화면의 "지금 적용 중인
# 값" 미리보기)와 공유한다(문자열 두 군데 흩어지면 나중에 하나만
# 바뀌는 사고가 난다).
DEFAULT_EXPECTED_OUTPUT = "실무에 바로 쓸 수 있는 구체적인 산출물"


def _agent_goal(role_name: str, skills: list[str]) -> str:
    """사용자 요청(2026-10-13, "goal도 skill 기반으로 보강") — 지금까지
    모든 Agent가 역할명만 다를 뿐 완전히 동일한 목표 문장을 썼다(스킬이
    몇 개든, 어떤 스킬이든 goal에는 전혀 반영되지 않았다). `backstory`
    (`_agent_backstory()`)는 이미 스킬 "설명 전문"을 그대로 옮겨 "각
    스킬이 구체적으로 무엇을 하라는 것인지"를 전달하므로, `goal`에
    같은 텍스트를 또 넣는 건 프롬프트만 불리고 새 정보가 없다(CrewAI의
    role/goal/backstory 관례상 goal은 "무엇을 달성해야 하는가"라는
    목표 자체를 말하는 자리다). 그래서 goal에는 스킬 "설명"이 아니라
    "스킬이 정한 완료 기준·절차를 반드시 지키라"는 목표를 얹는다 —
    예: 절차 스킬을 가진 Agent가 HITL 승인 단계를 임의로 건너뛰는 것을
    막는 효과."""
    base = f"'{role_name}' 역할로서 주어진 업무를 실무에 바로 쓸 수 있는 수준으로 완수한다."
    if not skills:
        return base
    skill_text = ", ".join(skills)
    return (
        f"{base} 보유 스킬({skill_text})이 요구하는 절차와 완료 기준을 "
        "정확히 따르고, 산출물 형식이나 승인(HITL) 단계를 임의로 생략하지 않는다."
    )


def _agent_backstory(
    conn: sqlite3.Connection, role_name: str, skills: list[str]
) -> str:
    """`conn`(2026-10-13, "skill을 적극 활용") — `llm.
    system_prompt_for_role()`과 같은 이유로 스킬 "이름"만 나열하던 것에
    `skill_pool.description`(절차/지식 안내문)을 덧붙인다 — CrewAI
    Agent 루프에서는 이게 `backstory`로 들어가 매 Task마다 참고된다."""
    skill_text = ", ".join(skills) if skills else "특별히 명시된 스킬 없음"
    backstory = f"소프트웨어 개발팀의 '{role_name}'로서 보유 역량은 {skill_text}이다."
    knowledge = pool_registry.skill_knowledge_text(conn, skills)
    return (
        f"{backstory}\n\n보유 역량 상세({pool_registry.SKILL_KNOWLEDGE_GUIDANCE})\n"
        f"{knowledge}"
        if knowledge
        else backstory
    )


def crewai_agent_generator(
    conn: sqlite3.Connection,
    role_name: str,
    required_skills: list[str],
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """생성 시점에 실제 CrewAI Agent+Task+Crew(단일, sequential)를 구성해
    kickoff한다. `jobs.GeneratorFn`과 같은 시그니처라 그대로 갈아끼울 수 있다.

    `conn`은 이 Agent가 지식검색/관계조회 스킬을 가졌을 때 실제 CrewAI
    Tool(`_skill_based_tools()`)을 붙이기 위해 필요하다(사용자 요청,
    2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘" —
    `GeneratorFn` 계약 자체에 conn을 추가해 생성 시점부터 이 Agent가
    쓸 도구를 가지고 태어나게 했다)."""
    from crewai import Agent, Crew, Process, Task
    from crewai.crews.crew_output import CrewOutput

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    crew_llm = _crewai_llm(resolved_provider, resolved_model)

    # Tool Pool 항목 중 mcp_stdio/mcp_remote 종류는 접속을 유지했다가 정리해야
    # 해서 ExitStack이 필요하다 — native(VectorDB/GraphDB)는
    # 필요 없지만 같은 해석 경로(_skill_based_tools)를 쓰므로 통일한다.
    with ExitStack() as pool_stack:
        agent = Agent(
            role=role_name,
            goal=_agent_goal(role_name, required_skills),
            backstory=_agent_backstory(conn, role_name, required_skills),
            llm=crew_llm,
            allow_delegation=False,  # 단일 Agent Crew라 위임할 동료가 없다.
            tools=_skill_based_tools(conn, pool_stack, required_skills),
            verbose=False,
        )
        task = Task(
            description=_static_augmentation(
                conn,
                required_skills,
                f"'{role_name}' 역할로서 지금 막 팀에 합류했습니다. 이 역할이 "
                "프로젝트에서 처음 맡을 만한 구체적인 산출물을 하나 작성하세요.",
            ),
            expected_output=DEFAULT_EXPECTED_OUTPUT,
            agent=agent,
            guardrail=_min_length_guardrail,
        )
        _reset_crewai_local_task_output_storage()
        crew = Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=False
        )
        result = crew.kickoff()
        # `kickoff()`의 선언 반환형은 `CrewOutput | CrewStreamingOutput`이지만
        # `stream`을 켜지 않는 이 호출에서는 항상 `CrewOutput`이다 — 정적으로
        # 그 사실을 알려줘 `.raw` 접근을 허용한다(런타임 동작 변화 없음).
        assert isinstance(result, CrewOutput)

    return {
        "role_name": role_name,
        "skill_tags": required_skills,
        "agents_yaml_snippet": f"role: {role_name}, skills: {required_skills}",
        "llm_provider": resolved_provider,
        "llm_model": resolved_model,
        "generated_output": result.raw,
        "framework": "crewai",
    }


# `deliverables.py`의 `_CONTENT_TYPE_INSTRUCTIONS`와 내용이 같다(의도적
# 중복 — 2026-10-07, "Tier 2까지 진행해줘"). 이 모듈은 `deliverables.py`를
# import할 수 없다(순환 참조 — `deliverables.py`가 이미 이 모듈을 쓴다).
_CONTENT_TYPE_INSTRUCTIONS = {
    "text": "",
    "markdown": "결과물은 Markdown 형식으로 작성하세요(제목은 #, 목록은 -, 강조는 **, "
    "코드는 ``` 블록을 쓰세요).",
    "html": "결과물은 완전한 HTML 문서로 작성하세요(<!DOCTYPE html>부터 </html>까지, "
    "스타일은 <style> 태그 안에 인라인으로만 넣고 외부 리소스는 참조하지 마세요).",
    "code": "결과물은 코드로 작성하세요. 전체를 ``` 코드 블록 안에 넣고, 필요하면 "
    "블록 앞뒤에 짧은 설명만 덧붙이세요.",
}


def run_crew(
    conn: sqlite3.Connection,
    agent_ids: list[int],
    task_descriptions: list[str],
    actor_id: int,
    process: str = "sequential",
    provider: str | None = None,
    model: str | None = None,
    manager_agent_id: int | None = None,
    expected_outputs: list[str] | None = None,
    on_task_complete: Callable[[dict], None] | None = None,
    memory: bool = True,
    planning: bool = False,
    manager_provider: str | None = None,
    manager_model: str | None = None,
    parallel_agent_ids: list[int] | None = None,
    self_review: bool = False,
) -> dict:
    """여러 온보딩된 Agent를 CrewAI Crew로 묶어 실제 협업
    실행한다. N번째 `agent_ids`가 N번째 `task_descriptions`를 맡는다.

    `process="sequential"`이면 Task.context로 앞 Agent의 산출물이 다음
    Agent에게 자동 전달된다. `"hierarchical"`이면 매니저(지정한
    `manager_agent_id` 또는 매니저 LLM)가 작업을 조율한다 — CrewAI의
    hierarchical 모델은 매니저가 작업을 직접 수행하지 않고 조율·위임만
    하므로, `manager_agent_id`로 지정된 Agent에 대응하는
    `task_descriptions` 항목은 실행되지 않는다(그 Agent는 매니저 역할만
    맡는다). 이 때문에 Task.context 체이닝도 hierarchical에서는 걸지
    않는다 — 매니저가 매 순간 누구에게 무엇을 넘길지 동적으로 결정하므로
    sequential처럼 "바로 앞 Task"만 미리 고정해 넘기는 게 의미가 없다.
    모든 Agent에 `allow_delegation=True`를 켜서 실제 동료 위임/질의가
    가능하게 한다 — 실제로 위임이 발동했는지는 CrewAI 이벤트버스에서
    캡처해 정직하게 기록한다(추측 아님).

    `memory=True`(약속했으나 누락됐던 부분, 2026-09-27
    리뷰에서 발견해 반영함, 기본값)로 Crew 전체가 이전 실행 맥락을
    기억한다 — **주의**: CrewAI의 기본 메모리 임베더는 provider와
    무관하게 OpenAI 임베딩 API를 쓴다(Anthropic은 임베딩 API 자체가
    없다). 즉 provider="anthropic"/"ollama"로 이 함수를 호출해도
    `OPENAI_API_KEY`가 별도로 필요하다 — 없으면 메모리 저장 시점에
    CrewAI가 에러를 낸다. `OPENAI_API_KEY` 없이 non-openai provider를
    쓰려면 호출자가 `memory=False`로 꺼야 한다(사용자 요청, 2026-10-20,
    "Crew 레벨 고급 옵션" — 4단계).

    `planning=False`(기본값) — `True`로 켜면 CrewAI가 Crew 실행 전에
    별도 LLM 호출로 전체 작업 계획을 먼저 세운다(`Crew(planning=True)`,
    추가 LLM 비용 발생). 복잡한 다단계 협업에서 Agent들이 더 일관된
    순서로 움직이게 하는 CrewAI 내장 기능이나, 단순한 조합에서는
    비용만 늘어날 수 있어 기본은 꺼둔다.

    `manager_provider`/`manager_model` — `manager_agent_id`가
    `None`(=CrewAI가 익명 매니저를 자동 배정하는 경로)이고
    `process="hierarchical"`일 때만 의미가 있다. 둘 중 하나라도 주어지면
    그 매니저 전용 LLM을 구성한다(워커 Agent 전체에 쓰는 `provider`/
    `model`과 분리 — 매니저는 조율만 하므로 더 가벼운/저렴한 모델을
    따로 쓸 수 있게 한다). 둘 다 `None`이면 기존처럼 워커와 같은 LLM을
    그대로 쓴다(하위 호환).

    `expected_outputs`(사용자 요청, 2026-10-20, "고급 에이전트 관리") —
    `task_descriptions`와 1:1 대응하는 `Task.expected_output` 오버라이드.
    인덱스별 우선순위: `expected_outputs[i]`(호출자가 명시, 예: 파이프라인
    단계의 `expected_output`) → 그 Agent의 저장된 기본값(`agent_pool.
    expected_output`, `PATCH .../persona`로 설정) → 플랫폼 공통 기본
    문구. `None`이면(기본값) 전부 두 번째 단계부터 평가한다.

    `parallel_agent_ids`(사용자 요청, 2026-10-20, "독립 작업 병렬 실행"
    — Crew 빌더 로드맵 5단계) — `process="sequential"`에서, 여기 담긴
    Agent id의 Task는 CrewAI의 `async_execution=True`로 표시돼 바로
    앞 Task를 기다리지 않고 즉시 병렬로 시작한다(`context` 체이닝도
    끊는다 — 독립적이라는 선언 자체가 "앞 결과를 안 본다"는 뜻이므로).
    `crew.kickoff()`는 끝나기 전에 전부 join하므로 반환값(`agents`)엔
    평소처럼 전원의 산출물이 들어있다. hierarchical(매니저가 조율/자동
    위임)은 Task 구성 방식이 달라(단일 매니저 Task이거나 CrewAI가
    `Task.agent` 배정을 무시) 이 플래그가 적용될 지점이 없으므로,
    `process != "sequential"`인데 지정하면 조용히 무시하지 않고 즉시
    거부한다.

    `self_review`(사용자 요청, 2026-11-04, "Agent 자가비평" 설계 —
    실시간 데모에서 Crew 재시도를 거듭할 때마다 새 기능을 추가하면서
    이전에 되던 기능이 조용히 퇴행하는 문제를 3라운드 수동 피드백으로
    직접 겪음) — 켜면 각 worker의 초안 Task 뒤에 **같은 Agent**의 검토
    Task를 추가한다(`context=[초안]`로 자기 초안을 보고, 원 요구사항과
    대조해 빠진/깨진 부분을 고치되 기존에 되던 기능은 보존하라고
    지시). 다음 worker는 이전 worker의 "검토 후 최종본"에서 이어받는다
    (초안이 아니라). `parallel_agent_ids`와 마찬가지로 `process=
    "sequential"`이고 `parallel_agent_ids`가 비어 있을 때만 의미가
    있으므로 그 외 조합은 즉시 거부한다(조용히 무시하지 않음 —
    체크박스를 켰는데 아무 일도 안 일어나는 상태를 피한다). 결과
    `per_agent` 각 항목에 `"stage": "draft"|"final"` 키가 붙는다
    (`self_review=False`면 항상 `"final"` — 기존 소비자와 하위호환).
    """
    if len(agent_ids) != len(task_descriptions):
        raise ValueError("agent_ids와 task_descriptions의 개수가 같아야 합니다")
    if not agent_ids:
        raise ValueError("agent_ids가 비어 있습니다")
    if parallel_agent_ids and process != "sequential":
        raise ValueError(
            "parallel_agent_ids는 process='sequential'에서만 지정할 수 있습니다"
        )
    if self_review and (process != "sequential" or parallel_agent_ids):
        raise ValueError(
            "self_review는 process='sequential'이고 parallel_agent_ids가 "
            "비어 있을 때만 사용할 수 있습니다"
        )
    if manager_agent_id is not None:
        if process != "hierarchical":
            raise ValueError(
                "manager_agent_id는 process='hierarchical'에서만 사용합니다"
            )
        if manager_agent_id not in agent_ids:
            raise ValueError(
                f"manager_agent_id가 agent_ids에 없습니다: {manager_agent_id}"
            )

    from crewai import Agent, Crew, Process, Task
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

    rows = []
    for agent_id in agent_ids:
        row = conn.execute(
            "SELECT * FROM agent_pool WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"agent_not_found:{agent_id}")
        if row["status"] != "active":
            raise PermissionError(f"agent_not_active:{agent_id}")
        rows.append(row)

    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    crew_llm = _crewai_llm(resolved_provider, resolved_model)

    # 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    # 하는 기능") — Agent별 `tuning_config`(CrewAI Agent/LLM 동작
    # 파라미터, `models.AgentTuningConfig` 참고)를 읽어온다. `temperature`
    # 만 LLM 쪽 필드라 따로 빼고, 나머지(max_iter/max_rpm/
    # max_execution_time/max_retry_limit/reasoning/allow_code_execution/
    # respect_context_window)는 `Agent(**...)`에 그대로 스프레드할 수
    # 있는 키 이름이다 — `AgentTuningConfig` 필드명을 CrewAI `Agent`
    # 생성자 키워드와 **의도적으로 동일하게** 설계해 변환 없이 바로 쓴다.
    # `max_iter`는 지정 안 하면 기존처럼 `DELEGATION_MAX_ITER`로
    # 떨어진다(여기서만 특별 취급 — 위임 Agent 루프가 너무 일찍 끊기지
    # 않게 하려고 원래부터 CrewAI 기본값보다 높여 쓰고 있었다).
    def _agent_kwargs_for(row: sqlite3.Row) -> dict:
        tuning = json.loads(row["tuning_config"])
        temperature = tuning.pop("temperature", None)
        tuning.setdefault("max_iter", DELEGATION_MAX_ITER)
        tuning["llm"] = (
            crew_llm
            if temperature is None
            else _crewai_llm(resolved_provider, resolved_model, temperature=temperature)
        )
        return tuning

    agent_kwargs_by_id = {row["id"]: _agent_kwargs_for(row) for row in rows}

    # MCP, 2026-09-27 — MCPServerAdapter는 생성 시점에 실제로
    # 접속을 시도하므로, Crew 실행이 끝날 때까지 연결을 유지했다가 정리하기
    # 위해 ExitStack으로 감싼다.
    with ExitStack() as mcp_stack:
        crewai_agents = [
            Agent(
                role=row["role_name"],
                goal=row["goal"]
                or _agent_goal(row["role_name"], json.loads(row["skill_tags"])),
                backstory=row["backstory"]
                or _agent_backstory(
                    conn, row["role_name"], json.loads(row["skill_tags"])
                ),
                **agent_kwargs_by_id[row["id"]],
                allow_delegation=True,  # 정적 릴레이가 아니라 실제 협업
                # AgentModelConfig.mcp_servers(Agent별 개별 MCP 설정)
                # + skill_pool/agent_tool_links(Tool Pool) — 둘 다 실제
                # CrewAI Tool이라 같은 리스트에 합친다.
                tools=(
                    _mcp_tools(mcp_stack, json.loads(row["mcp_servers"]))
                    + _skill_based_tools(
                        conn,
                        mcp_stack,
                        json.loads(row["skill_tags"]),
                        agent_id=row["id"],
                    )
                ),
                verbose=False,
            )
            for row in rows
        ]

        # 정정(2026-09-27, "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘"):
        # 예전에는 여기서 `agent_runtime.augment_with_retrieval()`로 검색
        # 결과를 Task description에 무조건 끼워 넣었다(정적 주입). 이제는
        # 위에서 지식검색/관계조회 스킬을 가진 Agent에게 진짜 CrewAI Tool을
        # 붙였으므로(augmentation_mode='tool'), description은 원문 그대로
        # 두고 **Agent가 스스로 도구 호출 여부를 판단**하게 한다 — 정적
        # 주입과 Tool 호출을 동시에 하면 검색 결과가 중복으로 들어가거나
        # Agent 판단이 무의미해진다.
        #
        # 재정정(2026-09-29, "skill_pool.augmentation_mode의 'always' 값
        # 실제 구현"): 위 원칙은 `'tool'` 모드에만 적용된다. `'always'`
        # 모드로 등록된 스킬은 이 원칙의 예외로 두기로 결정했다 — Agent
        # 판단에 맡기지 않고 강제로 컨텍스트를 주입하는 것 자체가
        # `'always'`의 존재 이유이므로, `_static_augmentation()`이
        # `'always'` 매칭분만 여기서 description 앞에 붙인다(기본 시드
        # 스킬은 전부 `'tool'`이라 이 변경은 기존 동작을 바꾸지 않는다).
        # CrewAI는 manager_agent가 agents 목록에 함께 있으면
        # ValidationError(manager_agent_in_agents)를 던진다 — 매니저는 작업을
        # 직접 수행하지 않는다는 hierarchical 모델과 일치한다(2026-09-27 리뷰에서
        # 실측으로 확인한 실사용 크래시 버그를 여기서 고친다).
        manager_index = (
            agent_ids.index(manager_agent_id) if manager_agent_id is not None else None
        )
        worker_agents = [a for i, a in enumerate(crewai_agents) if i != manager_index]
        worker_rows = [r for i, r in enumerate(rows) if i != manager_index]

        def _expected_output_for(i: int, fallback: str) -> str:
            if expected_outputs and i < len(expected_outputs) and expected_outputs[i]:
                return expected_outputs[i]
            return rows[i]["expected_output"] or fallback

        # 사용자 요청(2026-10-18, "매니저가 하위 과업을 실제로 쪼개서
        # 위임하도록 개선해줘") — 실측으로 확인한 문제: CrewAI는 hierarchical
        # 프로세스에서 `_get_agent_to_use()`가 `Task.agent`를 완전히
        # 무시하고 **모든 Task를 매니저 자신이 실행**한다(crew.py:1183).
        # 그런데 기존 코드는 (매니저 제외) 멤버 수만큼 **완전히 동일한
        # 지시문**을 가진 Task를 미리 1:1로 만들어뒀었다 — 그 결과 매니저가
        # "테트리스 게임을 만들어라"라는 똑같은 통짜 과업을 멤버 수만큼
        # 반복해서 그때그때 아무 코워커에게나 통째로 떠넘기는 식으로
        # 동작해, 역할별로 쪼개지지 않은 거의 동일한 완성품이 멤버
        # 수만큼(실측: "자율 개발 크루" 11명 → 완성된 테트리스 HTML
        # 11개) 쏟아졌다.
        #
        # 진짜 분업이 되려면 Task를 **하나만** 주고 "코워커들에게 역할에
        # 맞는 하위 과업을 나눠 위임한 뒤 결과를 통합해 하나의 최종
        # 산출물로 제출하라"고 명시해, 몇 명에게 어떻게 나눌지는 매니저
        # LLM이 `allow_delegation` 도구(Delegate work to coworker/Ask
        # question to coworker)를 스스로 필요한 만큼 호출해 그때그때
        # 정하게 둔다 — Task 개수를 멤버 수에 맞춰 미리 고정하지 않는다.
        hierarchical_with_manager = (
            process == "hierarchical" and manager_agent_id is not None
        )
        # 실측(2026-10-18) — 처음엔 "위임 도구를 안 쓰면 거부"하는 하드
        # guardrail을 걸어봤는데, gpt-4o-mini가 위임 자체는 제대로
        # 시도했지만(백엔드 개발자 2에게 게임 로직, 프론트엔드 개발자 3에게
        # UI처럼 실제로 역할에 맞게 쪼갰다) 한 번의 Action에 "Delegate
        # work to coworker" 호출 여러 건을 배열로 묶어 보내는 잘못된
        # 형식으로 호출해 CrewAI의 ReAct 파서가 이를 인식하지 못했고,
        # guardrail이 재시도 한도(3회)를 다 쓰고도 계속 실패해 Crew 실행
        # 전체가 예외로 **크래시**했다(이번 세션에 고친 JSONDecodeError
        # 크래시와 같은 종류의 "통째로 실패" 리스크를 새로 만드는 셈이라
        # 받아들일 수 없다). 그래서 위임 여부를 강제로 검사해 반려하는
        # 하드 guardrail은 빼고, 프롬프트로만 유도한다 — 위임을 안 하거나
        # 형식이 어긋나도 최소한 Crew 실행 자체는 항상 끝까지 성공해야
        # 한다는 게 우선이다. 대신 "한 번에 한 코워커씩만" 명시해 그
        # 배열-묶음 호출 실수를 줄인다.
        delegations: list[dict] = []
        # hierarchical_with_manager 분기에서는 안 쓰지만(그 분기의 결과
        # 귀속은 `rows[manager_index]`를 직접 쓴다) 미리 선언해둬야
        # 아래 `_capture_task_completion`/결과 루프에서 "정의 안 됨"
        # 경고 없이 안전하게 참조할 수 있다.
        worker_task_rows: list[sqlite3.Row] = []
        worker_task_stage: list[str] = []
        if hierarchical_with_manager:
            # `hierarchical_with_manager`는 `manager_agent_id is not None`일
            # 때만 참이고(위 683행), `manager_index`도 그 조건으로만
            # None이 아니게 계산된다(위 654행) — 항상 함께 성립하지만
            # 서로 다른 변수라 타입체커는 이 상관관계를 모른다.
            assert manager_index is not None
            manager_row = rows[manager_index]
            coworker_roles = ", ".join(r["role_name"] for r in worker_rows)
            base_description = task_descriptions[0] if task_descriptions else ""
            hierarchical_description = (
                f"{base_description}\n\n"
                "[진행 방식] 당신은 이 팀의 매니저입니다. 혼자 처음부터 "
                "끝까지 구현하지 말고, 아래 코워커들에게 역할에 맞는 "
                f"하위 과업을 직접 쪼개서 위임하세요(코워커: "
                f"{coworker_roles}). 한 코워커에게 전체 작업을 통째로 "
                "떠넘기지 말고, 최소 2명 이상에게 서로 다른 하위 과업"
                "(예: 화면/UI, 핵심 로직, 데이터/검증 등 역할에 맞는 "
                "부분)을 나눠 맡긴 뒤, 돌아온 결과물들을 모두 모아 하나의 "
                "완성된 최종 산출물로 통합해 제출하세요. 'Delegate work "
                "to coworker' 도구는 **한 번 호출에 코워커 한 명씩만** "
                "입력하세요 — 여러 코워커를 배열이나 목록으로 한 번에 "
                "묶어서 호출하지 말고, 코워커 수만큼 호출을 여러 번 "
                "나눠서 하세요.\n\n"
                "올바른 Action Input 예시(코워커 한 명당 정확히 이 형태로, "
                "한 번에 하나씩):\n"
                '{"task": "로그인 화면의 입력 검증 로직을 구현하세요", '
                '"context": "이메일 형식과 비밀번호 길이를 검사해야 합니다", '
                '"coworker": "'
                + (worker_rows[0]["role_name"] if worker_rows else "")
                + '"}\n'
                "절대 금지(이렇게 배열로 여러 명을 한 번에 묶어 보내면 "
                "형식 오류로 위임 자체가 실패합니다): "
                '[{"task": "...", "coworker": "A"}, {"task": "...", "coworker": "B"}]'
            )

            worker_tasks = [
                Task(
                    description=_static_augmentation(
                        conn,
                        json.loads(manager_row["skill_tags"]),
                        hierarchical_description,
                    ),
                    expected_output=_expected_output_for(
                        manager_index,
                        "코워커들에게 위임한 하위 과업들의 결과를 모두 "
                        "통합한, 실무에 바로 쓸 수 있는 하나의 완성된 최종 산출물",
                    ),
                    guardrail=_min_length_guardrail,
                )
            ]
        else:
            tasks: list[Task] = []
            # self_review(사용자 요청, 2026-11-04) — worker당 Task를 1개에서
            # 최대 2개(초안+검토)로 늘리므로, `worker_rows`(agent 1명당 1개,
            # 절대 안 늘어남)로는 더 이상 Task를 1:1로 인덱싱할 수 없다.
            # `task_owner_rows`/`worker_task_stage`를 `tasks`와 나란히
            # 쌓아 "이 Task가 어느 agent row 소속이고 초안인지 최종본인지"를
            # Task 단위로 추적한다 — 아래 `_capture_task_completion()`과
            # 최종 결과 루프가 이걸로 `worker_rows` 참조를 대체한다(실측으로
            # 발견한 버그 수정 — self_review 없이는 Task 개수와 worker_rows
            # 개수가 항상 같아 기존 동작과 100% 동일하다).
            task_owner_rows: list[sqlite3.Row] = []
            worker_task_stage: list[str] = []
            for i, description in enumerate(task_descriptions):
                is_parallel = agent_ids[i] in (parallel_agent_ids or [])
                context = (
                    None
                    if is_parallel
                    else ([tasks[-1]] if tasks and process == "sequential" else None)
                )
                draft_task = Task(
                    description=_static_augmentation(
                        conn, json.loads(rows[i]["skill_tags"]), description
                    ),
                    expected_output=_expected_output_for(
                        i, DEFAULT_EXPECTED_OUTPUT
                    ),
                    agent=crewai_agents[i],
                    context=context,
                    async_execution=is_parallel,
                    guardrail=_min_length_guardrail,
                )
                tasks.append(draft_task)
                task_owner_rows.append(rows[i])
                worker_task_stage.append("final" if not self_review else "draft")

                if self_review:
                    review_description = (
                        f"{description}\n\n"
                        "[자기 검토] 위는 당신이 방금 작성한 초안입니다. "
                        "원래 요구사항과 한 줄씩 비교해 (1) 빠졌거나 깨진 "
                        "부분을 모두 고치고, (2) 이미 잘 동작하던 기능은 "
                        "새 기능을 추가하거나 다른 문제를 고치는 과정에서 "
                        "실수로 지우거나 망가뜨리지 말고 그대로 보존하세요. "
                        "수정한 최종본 전체를 다시 제출하세요(초안을 그대로 "
                        "복사해 제출하지 마세요)."
                    )
                    review_task = Task(
                        description=_static_augmentation(
                            conn, json.loads(rows[i]["skill_tags"]), review_description
                        ),
                        expected_output="모든 요구사항이 반영되고 기존 동작은 보존된 최종본",
                        agent=crewai_agents[i],
                        context=[draft_task],
                        guardrail=_min_length_guardrail,
                    )
                    tasks.append(review_task)
                    task_owner_rows.append(rows[i])
                    worker_task_stage.append("final")

            worker_tasks = [t for i, t in enumerate(tasks) if i != manager_index]
            # `manager_index`는 이 분기(non-hierarchical)에서 항상 `None`
            # 이므로(매니저 Task는 위 `if` 분기에서만 만들어짐) 아래 둘은
            # 사실상 `tasks`/`task_owner_rows`와 동일하다 — 방어적으로
            # `worker_tasks`와 같은 필터링 규칙을 그대로 적용해둔다.
            worker_task_rows = [r for i, r in enumerate(task_owner_rows) if i != manager_index]
            worker_task_stage = [s for i, s in enumerate(worker_task_stage) if i != manager_index]

        task_completions: list[dict] = []

        def _capture_task_completion(task_output) -> None:
            # 주의(실사용 중 발견, 2026-09-27): `task_output.agent`를 그대로
            # 믿으면 안 된다 — CrewAI 소스(`crew.py`의 `_get_agent_to_use`)를
            # 확인해보니 hierarchical 프로세스에서는 이 필드가 **항상
            # manager_agent의 role**로 채워진다(매니저가 내부적으로 위임하더라도
            # Crew 실행 루프 관점에서는 매니저 자신이 "이 Task를 실행한 Agent"이기
            # 때문). 실제로 매니저=기획자, 워커=분석가로 hierarchical 실행했더니
            # 분석가가 만든 산출물인데도 `task_output.agent == "기획자"`로 찍히는
            # 걸 실측으로 확인했다. 대신 `worker_tasks`/`worker_rows`는 우리가
            # 구성한 순서이고 CrewAI는 `self.tasks`를 그 순서 그대로 순차
            # 실행하므로(`_execute_tasks`), 콜백이 몇 번째 호출됐는지로
            # `worker_rows`를 인덱싱하면 실제 담당 Agent를 정확히 알 수 있다.
            #
            # hierarchical_with_manager(2026-10-18 추가)일 때는 Task가 단
            # 하나뿐이고 그 결과가 "매니저가 코워커들에게 위임해 통합한
            # 최종 산출물" 자체이므로 — 어느 특정 워커의 몫이 아니라 —
            # `worker_rows` 인덱싱 대신 매니저 본인의 role_name을 쓴다.
            #
            # self_review(2026-11-04)로 worker당 Task가 2개(초안+검토)가
            # 될 수 있어, `worker_rows`(agent 1명당 1개, 절대 안 늘어남)
            # 대신 Task 개수만큼 늘어나는 `worker_task_rows`/
            # `worker_task_stage`로 인덱싱한다 — self_review가 꺼져있으면
            # 둘 다 `worker_rows`와 완전히 같은 길이/순서라 기존 동작과
            # 100% 동일하다.
            idx = len(task_completions)
            if hierarchical_with_manager:
                assert manager_index is not None  # 위 712행과 같은 이유
                agent_role = rows[manager_index]["role_name"]
                agent_id = None  # 위임 통합 결과 — 특정 워커 한 명의 몫이 아니다
            else:
                agent_role = (
                    worker_task_rows[idx]["role_name"]
                    if idx < len(worker_task_rows)
                    else task_output.agent
                )
                if idx < len(worker_task_rows) and worker_task_stage[idx] == "draft":
                    agent_role = f"{agent_role} (초안)"
                agent_id = (
                    worker_task_rows[idx]["id"] if idx < len(worker_task_rows) else None
                )
            # output_raw(사용자 요청, 2026-10-20, "실패 시 일부 재시도") —
            # 기존 output_preview(200자)는 화면 표시용이라 자르지만,
            # 부분 성공분을 실제 deliverable로 저장하려면 전체 텍스트가
            # 필요하다. agent_id가 있어야 어느 Agent의 산출물인지
            # DB(deliverables.author_id)에 연결할 수 있다.
            completion = {
                "agent_role": agent_role,
                "agent_id": agent_id,
                "output_preview": (task_output.raw or "")[:200],
                "output_raw": task_output.raw or "",
            }
            task_completions.append(completion)
            # 사용자 요청(2026-10-20, "Crew 빌더... 실행 이력 + 진행
            # 가시성") — `crew.kickoff()`가 끝나기 전에도(Task 하나씩
            # 끝날 때마다) 호출자가 즉시 알 수 있게 한다. 호출자
            # (`deliverables.py`)가 이 콜백 안에서 DB에 바로 commit하면,
            # Crew 실행이 끝나기 전에도 폴링으로 "N명 중 M명 완료"를
            # 볼 수 있다 — 새 WebSocket/SSE 없이 기존 폴링 패턴만으로.
            # 콜백이 예외를 던지면 CrewAI 실행 전체가 깨지므로 삼킨다
            # (진행 상황 기록 실패가 실제 Crew 작업을 막으면 안 된다).
            if on_task_complete is not None:
                try:
                    on_task_complete(completion)
                except Exception:  # noqa: S110, BLE001 — 진행 상황 기록은 부가 기능
                    pass

        crew_kwargs: dict[str, Any] = {
            "agents": worker_agents,
            "tasks": worker_tasks,
            "verbose": False,
            "memory": memory,
            # 사용자 요청(2026-09-27, "다른 리뷰 항목 마저 진행해줘")으로 Task
            # 완료마다 감사로그를 남긴다. `step_callback`은 일부러 안 쓴다 —
            # CrewAI가 콜백에 넘기는 AgentAction/AgentFinish에는 어느 Agent가
            # 낸 스텝인지 식별할 필드가 없어(실측 확인: thought/tool/tool_input/
            # text/result 뿐) 감사로그에 "누가"를 못 남긴다.
            "task_callback": _capture_task_completion,
        }
        if planning:
            crew_kwargs["planning"] = True
        if process == "hierarchical":
            crew_kwargs["process"] = Process.hierarchical
            if manager_agent_id is not None:
                assert manager_index is not None  # 위 712행과 같은 이유
                crew_kwargs["manager_agent"] = crewai_agents[manager_index]
            elif manager_provider or manager_model:
                manager_resolved_provider, manager_resolved_model = (
                    llm.resolve_provider_and_model(manager_provider, manager_model)
                )
                crew_kwargs["manager_llm"] = _crewai_llm(
                    manager_resolved_provider, manager_resolved_model
                )
            else:
                crew_kwargs["manager_llm"] = crew_llm
        elif process == "sequential":
            crew_kwargs["process"] = Process.sequential
        else:
            raise ValueError(f"지원하지 않는 process입니다: {process!r}")

        _reset_crewai_local_task_output_storage()
        crew = Crew(**crew_kwargs)

        known_roles = [r["role_name"] for r in rows]
        # 사용자 요청(2026-11-04, "파이프라인 '구현' 단계가 위임만 하고
        # 실제 코드를 합성 안 함" 버그 수정 — 로드러너 게임 데모에서
        # 실측) — `hierarchical_with_manager`는 매니저의 마지막 답변
        # 하나만 최종 결과물로 쓰는데, 매니저가 "위임 계획 요약"만
        # 쓰고 코워커의 실제 산출물(코드 등)을 안 옮겨 적는 경우가
        # 실제로 있었다. CrewAI 소스 확인 결과 매니저 LLM은 "Delegate
        # work to coworker" 도구 결과를 자기 추론 스크래치패드에서는
        # 실제로 보지만(`agent_utils.py`의 Observation), 그걸 최종
        # 답변에 그대로 옮겨 적으라고 강제하는 장치가 없다 — 매니저의
        # "기억"에 기대지 않고, 위임이 일어날 때마다 그 결과
        # (`event.output`)를 여기서 직접 수집해둔다("Ask question to
        # coworker"는 질의응답이라 제외 — 실제 작업 결과가 아님).
        delegated_work_outputs: list[dict] = []

        def _capture_delegation(source, event) -> None:
            if _looks_like_delegation(event.tool_name):
                delegations.append(
                    {
                        "from_agent_role": event.agent_role,
                        "to_agent_role": _resolve_delegation_target(
                            event.tool_args, known_roles
                        ),
                        "tool": event.tool_name,
                    }
                )
            if event.tool_name == "Delegate work to coworker" and event.output:
                delegated_work_outputs.append(
                    {
                        "to_agent_role": _resolve_delegation_target(
                            event.tool_args, known_roles
                        ),
                        "output": str(event.output),
                    }
                )

        with crewai_event_bus.scoped_handlers():
            crewai_event_bus.on(ToolUsageFinishedEvent)(_capture_delegation)
            # 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
            # 연결해줘") — 지금까지 Gate A-G는 단일 실행 경로(`agent_runtime.
            # execute_agent()`)에만 연결돼 있었다. `framework="crewai"`를 쓰면
            # SDK가 `crew.kickoff()`가 낸 CrewOutput.tasks_output에서 자동으로
            # 멀티에이전트 신호(Gate F: 합의·정보 전파·역할 준수·충돌 해결)를
            # 뽑아준다 — 반환값은 그대로 CrewOutput이라 아래 로직은 전혀
            # 안 바뀐다.
            crew_label = ", ".join(r["role_name"] for r in worker_rows)
            eval_result = agent_evaluation.run_and_score_crew(crew_label, crew.kickoff)
            result = eval_result["output"]

    per_agent = []
    if hierarchical_with_manager:
        # 매니저가 코워커들에게 하위 과업을 쪼개 위임하고 통합한 결과이므로
        # Task가 1개뿐이고, 그 결과물은 특정 워커의 몫이 아니라 매니저가
        # 제출한 **최종 통합 산출물**이다(위 `delegations`에 실제로 누구에게
        # 무엇을 위임했는지가 별도로 남는다). 그래서 워커별 산출물이 아니라
        # 매니저 1건만 돌려준다 — 예전처럼 "매니저는 산출물 없음(None)"으로
        # 두면 안 된다, 이제는 매니저 자신이 통합한 진짜 결과물이 있다.
        assert manager_index is not None  # 위 712행과 같은 이유
        manager_row = rows[manager_index]
        final_output_text = (
            result.tasks_output[0].raw if result.tasks_output else result.raw
        )
        # 실측으로 발견한 버그 수정(2026-11-04) — 매니저의 최종 답변이
        # "위임 계획 요약"뿐이고 실제 코워커 산출물(코드 등)을 전혀
        # 옮겨 적지 않는 경우가 있었다. 매니저가 요약을 잘했든 못했든
        # 실제 작업 결과가 최종 산출물에서 빠지는 일이 구조적으로
        # 없도록, 위에서 수집해둔 `delegated_work_outputs`를 무조건
        # 이어 붙인다 — 같은 코워커에게 여러 번 위임했으면 전부 보존
        # (합치거나 마지막 것만 남기지 않음, 전부 실제로 일어난 작업).
        if delegated_work_outputs:
            appendix = "\n\n---\n## 위임된 하위 작업 결과물(원본)\n\n" + "\n\n".join(
                f"### {d['to_agent_role'] or '코워커'}\n{d['output']}"
                for d in delegated_work_outputs
            )
            final_output_text = final_output_text + appendix
        conn.execute(
            "UPDATE agent_pool SET last_output = ?, framework = 'crewai' WHERE id = ?",
            (final_output_text, manager_row["id"]),
        )
        per_agent.append(
            {
                "agent_id": manager_row["id"],
                # 사용자 요청(2026-10-27, "다른 UI에도 ID가 남아 있는지
                # 확인해줘") — "Agent별 산출물" 표가 이 agent_id를 그대로
                # 노출하고 있었다. agent_pool은 이미 code가 있으니 같이
                # 실어 화면에서 PK 대신 쓸 수 있게 한다.
                "code": manager_row["code"],
                "role_name": manager_row["role_name"],
                "name": manager_row["name"],
                "output": final_output_text,
            }
        )
    else:
        # self_review(2026-11-04) — `worker_rows`(agent 1명당 1개)가 아니라
        # Task 개수만큼 늘어나는 `worker_task_rows`/`worker_task_stage`로
        # zip한다. 실측으로 발견한 버그: self_review로 Task가 2배가 되는데
        # `zip(worker_rows, result.tasks_output)`를 그대로 쓰면 `strict=False`
        # 때문에 짧은 쪽(worker_rows) 길이에서 멈춰 뒤 worker들의 결과가
        # 통째로 누락되거나 엉뚱한 agent_id에 잘못 귀속된다. self_review가
        # 꺼져있으면 `worker_task_rows`는 `worker_rows`와 완전히 같은
        # 길이/순서라 기존 동작과 100% 동일하다.
        for row, stage, task_output in zip(
            worker_task_rows, worker_task_stage, result.tasks_output, strict=False
        ):
            # 사용자 요청(2026-10-06, "model provider/모델명이 AI 생성·관리·Crew
            # 실행 단계에 모두 있는데 최종 어떻게 적용되는지 분석" → "(b)
            # 실행 후 덮어쓰기만이라도 막는 방향으로 수정") — Crew 실행은 참여
            # Agent 전원에게 요청 시점의 provider/model **하나**를 똑같이
            # 적용한다(개별 Agent의 "모델 설정" 저장값은 애초에 안 읽음,
            # `_crewai_llm()` 호출부 참고). 그런데 실행이 끝나면 그 하나의
            # 값을 모든 참여 Agent의 `llm_provider`/`llm_model`에 그대로
            # 덮어써 와서, "AI 에이전트 관리"에서 Agent별로 따로 설정해둔
            # 기본값(단일 실행 경로 `agent_runtime.execute_agent()`가 쓰는
            # 그 값)이 Crew 실행 한 번으로 조용히 사라지는 문제가 있었다.
            # `last_output`/`framework`(이번 실행이 실제로 crewai였다는
            # 사실)는 그대로 갱신하되, llm_provider/llm_model은 더 이상
            # 건드리지 않는다 — "모델 설정"은 사용자가 명시적으로
            # 바꾸기 전까지는 Crew 실행과 무관하게 유지된다.
            conn.execute(
                "UPDATE agent_pool SET last_output = ?, framework = 'crewai' WHERE id = ?",
                (task_output.raw, row["id"]),
            )
            per_agent.append(
                {
                    "agent_id": row["id"],
                    "code": row["code"],
                    "role_name": row["role_name"],
                    # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정") —
                    # CrewAI에 실제로 넘기는 `role=`(위 Agent() 생성부)은
                    # 그대로 role_name을 쓴다(위임 라우팅·known_roles 매칭이
                    # 이 문자열 그대로와 일치해야 해서 건드리면 안 됨).
                    # 화면 표시용으로만 지정한 이름을 따로 얹는다.
                    "name": row["name"],
                    "output": task_output.raw,
                    # self_review=False면 항상 "final" — 기존 소비자
                    # (run_adhoc_crew_job 등)와 하위호환.
                    "stage": stage,
                }
            )

    audit.record(
        conn,
        actor_id,
        "crew_run_completed",
        "crew_run",
        None,
        after={
            "agent_ids": agent_ids,
            "process": process,
            "delegation_count": len(delegations),
            # CrewAI가 실제로 집계해주는 토큰 사용량 — 참고용 기록일 뿐
            # 비용 조회(cost.py)의 cost_estimate 계산에는 아직 연결
            # 안 함(2026-09-27, "CrewAI 개념을 제대로 활용하는지 확인" 요청
            # 으로 확인: cost.py는 수동입력만 쓰는 게 1차 범위로 이미
            # 문서화돼 있다 — 자동 비용 갱신은 그 스코프를 벗어나는 더 큰
            # 변경이라 감사로그에 참고 데이터만 남긴다).
            "token_usage": result.token_usage.model_dump(),
            # 사용자 요청(2026-09-29, "Gate A-G를 CrewAI 멀티에이전트 경로에도
            # 연결해줘") — Agent 실행 결과의 Gate A-G도 집계값 성격이라
            # token_usage와 같은 자리(참고 기록)에 남긴다. `agent_evaluations`
            # 테이블은 `deliverables.id` 전용 FK라(협업 워크스페이스 개념)
            # `run_crew()`엔 대응하는 deliverable이 없어 재사용하지 않았다 —
            # 새 테이블을 만드는 대신 이미 있는 "감사로그 참고 기록" 패턴을
            # 그대로 재사용한다(원칙4).
            "gate_scores": eval_result["gate_scores"],
            "tcr": eval_result["tcr"],
        },
    )
    for delegation in delegations:
        audit.record(
            conn,
            actor_id,
            "agent_delegation_occurred",
            "agent_pool",
            None,
            after=delegation,
        )
    for completion in task_completions:
        audit.record(
            conn,
            actor_id,
            "crew_task_completed",
            "crew_run",
            None,
            after=completion,
        )
    conn.commit()

    return {
        "final_output": result.raw,
        "process": process,
        "delegations": delegations,
        "agents": per_agent,
        "gate_scores": eval_result["gate_scores"],
        "tcr": eval_result["tcr"],
        # 사용자 요청(2026-10-14, "대화창에 사용중인 모델이 명시되도록") —
        # 크루 전체가 공유하는 provider/model(개별 Agent 저장값과는 무관,
        # 위 `resolved_provider`/`resolved_model` 호출부 참고)을 호출자가
        # 메시지에 표시용으로 기록할 수 있게 반환값에 포함한다.
        "resolved_provider": resolved_provider,
        "resolved_model": resolved_model,
        # 사용자 요청(2026-10-12, "UI 개선 잔여 작업 진행해줘") — CrewAI가
        # 이미 집계해주는 값인데 지금까지 audit_log 참고 기록에만 남고 API
        # 응답엔 빠져 있어 화면에 보여줄 수가 없었다. 비용/실행시간은
        # CrewAI가 집계해주지 않아(UsageMetrics에 없음) 여기 포함하지
        # 않는다 — 없는 값을 지어내지 않는다.
        "token_usage": result.token_usage.model_dump(),
    }


def run_crew_for_team(
    conn: sqlite3.Connection,
    team_id: int,
    task_id: int,
    actor_id: int,
    process: str = "sequential",
    provider: str | None = None,
    model: str | None = None,
    manager_agent_id: int | None = None,
    content_type: str = "text",
    on_task_complete: Callable[[dict], None] | None = None,
    memory: bool = True,
    planning: bool = False,
    manager_provider: str | None = None,
    manager_model: str | None = None,
    parallel_agent_ids: list[int] | None = None,
) -> dict:
    """사용자 요청(2026-10-07, "crew 실행에서 묶인 에이전트 그룹의 경우
    하나의 그룹만 가능한 구조인데, 복수 그룹 가능하도록 개선, 이 그룹이
    전체 업무에서 사람-에이전트 연동이 가능한 구조인지 확인") —
    Phase 3. 기존
    `run_crew()`(즉석 실행 — `agent_ids`를 매번 직접 고름, `task_id` 없이
    결과가 `agent_pool.last_output`에만 남고 끝)와 달리, 이 함수는 **팀
    (teams.py, "등록→재사용" 그룹)을 실제 할 일(task)의 맥락에서** 실행한다
    — "복수 그룹"은 한 할 일에 팀을 여러 개 배정할 수 있다는 뜻이다("다중
    confirmed 허용"으로 이미 가능했다 — team 배정도 resource_type별
    제한 없이 여러 건 confirmed로 공존한다). 이 함수 자체는 그중 **한
    팀**을 실행한다(호출자가 어느 팀인지 고른다 — 사용자 결정, "팀 하나를
    골라서 실행").

    `task_descriptions`는 팀의 Agent 멤버 전원에게 **같은 내용**을 준다
    — 할 일의 `role_name`과 최신 사람 작성 deliverable(있으면)을
    맥락으로 묶은 것으로, `deliverables.start_agent_execution()`의
    단일-Agent 프롬프트와 같은 패턴이다(일관성 유지). CrewAI의
    sequential/hierarchical 프로세스가 그 공통 맥락을 agent들에게 어떻게
    나눠 처리할지는 평소처럼 Crew 자체가 정한다 — 여기서 Agent별로 다른
    지시를 미리 쪼개지 않는다(쪼개려면 팀 멤버의 `role_label`을 더 적극
    활용해야 하는데, 이는 스코프 밖의 더 큰 설계 변경이다).

    사람-에이전트 연동: 이 함수가 반환하는 `agents`(Agent별 산출물)는
    호출자(`deliverables.run_team_crew_job()`)가 각각 `deliverables`
    (author_type='agent') + 결재 체인으로 저장한다 — 그 순간부터
    팀 리더·팀원(사람)이 같은 할 일의 협업 워크스페이스에서 결과물을
    보고, 댓글을 달고, 승인/반려할 수 있다. 즉 "즉석 실행"이던 Crew가
    처음으로 업무 파이프라인(조직도·배정, 협업 워크스페이스, Gate A-G)에
    연결된다 — 지금까지 `run_crew()`가 완전히 격리돼 있던 것과의 핵심
    차이."""
    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team is None:
        raise ValueError("team_not_found")
    confirmed = conn.execute(
        "SELECT id FROM assignments WHERE task_id = ? AND resource_type = 'team' "
        "AND resource_id = ? AND status = 'confirmed'",
        (task_id, team_id),
    ).fetchone()
    if confirmed is None:
        raise ValueError("team_not_confirmed_for_task")

    agent_ids = teams.active_agent_member_ids(conn, team_id)
    if not agent_ids:
        raise ValueError("team_has_no_active_agents")

    # 사용자 요청(2026-10-18, "기존 개발자 크루 팀을... 업무 할당시
    # 배정하면 위 변경 내용을 모두 적용할 수 있는지 확인해줘") — 실측으로
    # 확인한 gap: 이 함수는 `manager_agent_id`를 호출자가 명시적으로
    # 넘겨야만 쓰는데, Task 협업 워크스페이스의 "팀 Agent 실행(Crew)"
    # 화면에는 매니저를 고르는 UI 자체가 없어서 늘 `None`으로 호출됐다
    # — hierarchical을 선택해도 CrewAI 자신의 익명 매니저(`manager_llm`)
    # 로 떨어져, `conversations.run_team_mention_job()` 쪽에서 이미 고친
    # "매니저가 실제로 역할별 하위 과업을 쪼개 위임"하는 경로를 전혀 못
    # 탔다. 그 함수와 똑같이 `teams.manager_agent_id_for_team()`(팀
    # 멤버의 `role_label`에 "매니저"가 들어간 활성 Agent)으로 자동
    # 탐지한다 — 호출자가 이미 명시적으로 지정했으면 그 값을 존중하고,
    # 사용자가 "sequential"을 고른 경우는 건드리지 않는다(원래 그대로
    # 릴레이로 동작해야 하는 선택이라 매니저가 있어도 강제로 바꾸지
    # 않는다).
    if process == "hierarchical" and manager_agent_id is None:
        manager_agent_id = teams.manager_agent_id_for_team(conn, team_id)

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    latest_human = conn.execute(
        "SELECT content FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    required_skills = json.loads(task["required_skills"])
    skills_text = ", ".join(required_skills) if required_skills else "없음"
    desc_parts = [f"'{task['role_name']}' 업무를 수행하세요(필요 스킬: {skills_text})."]
    if latest_human is not None:
        desc_parts.append(f"참고할 사람 작성 자료:\n{latest_human['content']}")
    instruction = _CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")
    if instruction:
        desc_parts.append(instruction)
    description = "\n\n".join(desc_parts)
    task_descriptions = [description] * len(agent_ids)

    return run_crew(
        conn,
        agent_ids,
        task_descriptions,
        actor_id,
        process=process,
        provider=provider,
        model=model,
        manager_agent_id=manager_agent_id,
        on_task_complete=on_task_complete,
        memory=memory,
        planning=planning,
        manager_provider=manager_provider,
        manager_model=manager_model,
        parallel_agent_ids=parallel_agent_ids,
    )
