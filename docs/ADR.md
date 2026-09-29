# ADR — HAIR-Platform 아키텍처 결정

> 《하네스 메서드》 §17 ADR 형식. `adr_review` 승인 대상. SPEC.md §10의 가결정을 여기서
> 공식 재확인하고, 신규 컴포넌트 역할분리·Tier 배정·RBAC 권한 매트릭스·샌드박스 구현
> 방식 등 SPEC.md에서 "오픈 이슈" 또는 "가결정"으로 남겨뒀던 항목을 확정한다.

## 배경

SPEC.md(DRAFT, `spec_review` 대기)가 F1–F14를 정의했다. "HAIR 플랫폼 기획안" 문서의
1~5차 재검토에서 데이터 흐름의 논리적 모순은 해소했지만, 구조적 설계 결정(컴포넌트
경계, Tier 배정, 권한 매트릭스, 샌드박스 구체 기술)은 "Phase 2 ADR에서 확정"으로
미뤄둔 상태였다. 이 ADR이 그 결정들을 내린다.

## 결정 1 — 7-컴포넌트 역할 분리 (신규 모듈)

| 컴포넌트 | 담당 요구사항 | 입력 | 출력 |
|---|---|---|---|
| Resource Designer | F2 | GoldenData(Spec Interpreter 출력) | Task 분해 결과(필요 역할 목록·인원) |
| Assignment Engine | F3, F6, F14 | Task 분해 결과 + Agent Pool + Human Pool | Top3 후보 + 점수 breakdown, 배정요청(`requested`) |
| Human Pool | F12 | 온보딩 폼 입력 | ResourceCandidate(사람) |
| Approval Workflow | F7 | 배정요청(`requested`) | 승인/반려, `confirmed` 상태 전환 |
| Job Queue / Worker | F4 | 생성 트리거(부족 역할) | 신규 Agent(AgentForge 파이프라인 결과), Job 상태 |
| Auth + RBAC | F11 전반 | 로그인 요청, API 호출 | 세션, 권한 허용/거부 |
| Audit Log | F13 | 모든 쓰기 액션 이벤트(F4·F6·F7·F9) | 조회 가능한 append-only 이력 |

**결정 1-A — F2/F6 경계 확정(3차 재검토 오픈 이슈 해소)**: F2(Review UI)는 **Task
분해 결과만** 검토·조정한다(어떤 역할이 몇 명 필요한지) — 후보 매칭 결과는 포함하지
않는다. 후보 매칭(누가 그 역할을 맡을지)은 F2 **이후** Assignment Engine이 수행하고,
그 결과를 F6(배분 화면)에서 매니저가 확인·배정 요청한다. 즉 SI → Resource Designer(F2,
역할분해) → Assignment Engine(F3/F6/F14, 후보매칭) 순서이며 두 화면은 서로 다른
대상(역할구조 vs 후보인물)을 검토하므로 중복이 아니다.

> SPEC.md F2 문구("Task 분해·후보 매칭 결과를 검토")가 이 경계와 어긋나 이 ADR과 함께
> "Task 분해 결과를 검토"로 정정한다(§ SPEC.md 패치 참고).

**대안 검토**: F2와 F6을 하나의 화면으로 통합하는 안을 검토했으나 기각 — 역할분해(구조
결정, 매니저 판단 필요)와 후보선택(매칭 알고리즘 결과 확인, 다른 성격의 판단)을 한
화면에 섞으면 "이 화면에서 뭘 확정하는 건지" 사용자가 혼동하기 쉽고, 3차 재검토에서
지적된 "중복처럼 보이는" 문제를 UI 레벨에서 완전히 없애지 못한다. 두 화면을 유지하되
책임을 위처럼 명확히 분리하는 쪽을 택한다.

## 결정 2 — Tier 배정: 신규 컴포넌트 중 LLM 추론이 필요한 것은 없다

AgentForge ADR(결정 1·2)은 Spec Interpreter·Team Designer를 Tier 1(AC), Code
Generator·Verifier를 Tier 2(AOO)로 배정했다. 이 프로젝트는 이 배정을 **그대로
재사용**한다(Resource Designer는 Team Designer를 확장한 것뿐, 별도 LLM 호출 추가 없음).

**신규 컴포넌트(Assignment Engine·Human Pool·Approval Workflow·Job Queue·Auth·Audit
Log)에는 원칙5의 Tier 1/2 프레임워크가 적용되지 않는다** — 전부 결정론적 로직(CRUD,
가중치 스코어링, 상태머신, 권한 검사)이며 LLM 판단이 개입하지 않기 때문이다.

**대안 검토**: Assignment Engine의 "적합도" 계산에 LLM을 사용해 스킬 매칭을 더 정교하게
하는 안을 검토했으나 기각 — AgentForge의 Pool 매칭 자체가 이미 "역할명 겹침이라는 아주
단순한 휴리스틱"(`docs/LIMITS_T-D8D8CE.md` 실측 기록)으로 충분히 작동한 선례가 있고
(원칙4: 이미 있는 패턴 재사용), 결정론적 스코어링이라야 F14(점수 breakdown 표시)가
사용자에게 "왜 이 점수인지" 재현 가능하게 설명할 수 있다. LLM 기반 스코어링은 설명
가능성(explainability)을 해치므로 채택하지 않는다.

## 결정 3 — RBAC 역할모델 + 권한 매트릭스 확정

역할 4종(Admin/매니저/팀원/뷰어)과 F1–F14의 접근 권한을 다음과 같이 확정한다.

| F-item | Admin | 매니저 | 팀원 | 뷰어 |
|---|---|---|---|---|
| F1 브리프 제출 | O | O | O | X |
| F2 Review UI(역할분해 검토) | O | O | X | X |
| F3 Pool 검색 | O | O | O | O |
| F4 생성 트리거 | O | O | X | X |
| F5 조직도 조회 / 배치 조작 | O(조회+조작) | O(조회+조작) | O(조회만) | O(조회만) |
| F6 배정 요청 | O | O | X | X |
| F7 승인/반려 | O | O(단, 본인이 요청한 건은 승인 불가 — 결정 3-A) | X | X |
| F8 성과리뷰 | O(전체) | O(전체) | O(본인 관련만) | X |
| F9 온보딩/오프보딩 | O | X | X | X |
| F10 비용 조회 | O | O | X | X |
| F12 Human Pool 온보딩 | O | O | X | X |
| F13 감사로그 | O | O | X | X |
| F14 점수 breakdown | O | O(요청자 본인) | X | X |

**결정 3-A — Self-approval 방지**: F7에서 배정을 **요청한 사람 본인은 그 건을 승인할
수 없다**(RBAC상 매니저 권한이 있어도). Approval Workflow는 `requested_by`와
`approved_by`가 같으면 거부한다. 결재라인의 최소 신뢰 요건이며, NF3(결재라인 우회
방지)의 연장선이다.

**대안 검토**: F9(온보딩/오프보딩)를 매니저에게도 허용하는 안을 검토했으나 기각 —
Agent 라이프사이클(특히 오프보딩)은 진행 중인 배정에 연쇄 영향을 주므로(§결정 8 참고)
Admin으로 좁혀 오작동 범위를 줄인다. 실사용에서 매니저 요청이 많으면 Phase 4 이후
재검토한다(오픈 이슈로 기록).

## 결정 4 — ResourceCandidate 공통 인터페이스 (SPEC §10 재확인)

`ResourceCandidate`(Pydantic v2): `id`·`type(human|agent)`·`role`·`availability`·
`cost`·`skill_tags`·`performance_score(0–1, 사람은 1차 null 허용)`. `AgentContract`·
`HumanProfile`은 각자 `to_candidate()` 어댑터로 이 인터페이스에 맞춘다. **변경 없이
확정.**

## 결정 5 — Pool 저장 정책 (SPEC §10 재확인)

평가결과(`results/*.json` + SQLite 인덱스, 배치성)는 AgentForge 하이브리드 패턴 그대로
유지한다. Human Pool·`assignments`·`approvals`·`jobs`·`audit_log`·
`assignment_history`처럼 실시간 쓰기가 빈번한 신규 엔티티는 SQLite를 **트랜잭션 RDB
(WAL 모드)**로 사용한다. **변경 없이 확정.**

## 결정 6 — Web UI 인증 스택 = 세션 기반(서버 사이드) + RBAC 미들웨어

FastAPI 내장 `SessionMiddleware`(서버 사이드 세션, httponly 쿠키) + 비밀번호 bcrypt
해시를 채택한다. JWT(클라이언트 사이드 토큰) 대신 세션을 택한 이유: AgentForge Web UI가
이미 순수 서버 렌더 HTML(f-string+`html.escape`, SPA 아님) 패턴이라(AgentForge ADR
결정 5) 세션 기반이 이 패턴과 자연스럽게 맞물린다. RBAC 미들웨어는 **서버측에서** 모든
쓰기 API 호출마다 역할을 검증한다(클라이언트 UI에서 버튼을 숨기는 것만으로는 불충분 —
SPEC.md 실패모드 카탈로그 "RBAC 서버측 미검증" 참고).

**대안 검토**: JWT 기반 무상태 인증을 검토했으나 기각 — 이 프로젝트는 SPA가 아니라
서버가 매 요청마다 페이지를 새로 렌더링하므로 무상태성의 이점이 없고, 세션 무효화
(로그아웃·강제 만료)가 JWT 블랙리스트 관리보다 단순하다.

## 결정 7 — 3중 기록 트랜잭션 원자성 구현 (SPEC §10, NF13 재확인)

F6/F7에서 배정 상태가 바뀔 때 `assignments`(UPDATE) · `assignment_history`(INSERT) ·
`audit_log`(INSERT) 3개 쓰기를 **하나의 SQLite 커넥션 트랜잭션**(`BEGIN`…`COMMIT`,
실패 시 `ROLLBACK`)으로 묶는다. 세 테이블이 물리적으로 같은 SQLite 파일(WAL 모드)에
있으므로 별도의 분산 트랜잭션(2PC) 없이 로컬 트랜잭션만으로 원자성이 보장된다. **변경
없이 확정**(로컬 재검토에서 이미 결정한 내용을 ADR에서 공식화).

## 결정 8 — Job Queue 샌드박스 = Docker 컨테이너

Verifier의 smoke-test(생성된 CrewAI 코드 실행)는 **Docker 컨테이너**에서 실행한다 —
`--cpus`·`--memory`로 리소스 쿼터, `--network=none`으로 아웃바운드 네트워크 차단,
프로세스 타임아웃(예: 60초)을 건다.

**대안 검토**:
- **컨테이너 없이 `subprocess`+`resource.setrlimit`만 사용**하는 안을 검토했으나 기각
  — CPU/메모리 제한은 가능해도 네트워크 차단(아웃바운드 콜 완전 차단)이 OS 프로세스
  격리만으로는 어렵고, 생성된 코드가 임의 코드라는 점(NF8, 실패모드 카탈로그 "샌드박스
  탈출")을 고려하면 파일시스템·네트워크까지 격리하는 컨테이너가 더 안전하다.
- **gVisor/Firecracker**(더 강한 VM급 격리)를 검토했으나 기각 — 설치·운영 복잡도가
  "로컬/온프레미스 우선, 최소 인프라"(SPEC §3) 원칙과 충돌한다. 조직 규모가 커져
  멀티테넌시로 확장할 때 재검토 대상으로 남긴다(오픈 이슈).

AgentForge의 Verifier는 현재 샌드박싱을 전혀 하지 않는다(코드베이스 확인 완료) — 재사용할
기존 패턴이 없으므로 이번에 신규로 결정한다(원칙4가 적용될 선례 부재).

## 결정 9 — Web UI 내비게이션을 "내 업무"·"관리자 도구" 두 그룹으로 분리(사용자 요청,
2026-09-26)

F11 Web UI 셸(코드 재점검 R4)이 처음 나왔을 때는 모든 탭이 한 줄에 평평하게
나열됐다 — 결정 3(RBAC 매트릭스)에 따라 서버는 이미 역할별 접근을 막고
있었지만, 화면상으로는 "이게 관리자만 쓰는 화면인지" 담당자가 한눈에
구분하기 어려웠다(예: 팀원이 못 보는 "AI 에이전트 관리" 탭과 모두가 보는
"업무 요청" 탭이 구분 없이 나란히 있었다). 사용자가 "admin 기능과 일반
사용자 기능을 구분"하도록 명시적으로 요청했다.

**결정**: 백엔드 RBAC(결정 3의 권한 매트릭스)는 그대로 두고, `webui.py`의
탭 정의에 `group: "user" | "admin"`을 추가해 내비게이션을 두 구역으로
시각적으로 분리한다 — "내 업무"(브리프/업무 요청, 결재, 인력 검색, 조직도,
성과 리뷰: MEMBER·VIEWER도 접근하는 화면)와 "관리자 도구"(매칭/배정,
AI 에이전트 생성·관리, 사람 인력 관리, 비용, 공정성 지표, 활동 기록,
사용자 관리: ADMIN/MANAGER 전용, 그중 사용자 관리만 ADMIN 전용이라 별도
배지로 표시). 접근 제어 자체는 여전히 서버가 하고(클라이언트에서 탭을
숨기는 것만으로는 불충분하다는 결정 6의 원칙 재확인), 이 분리는 순수하게
정보구조(IA) 개선이다 — 새 RBAC 규칙을 추가한 게 아니라 기존 규칙을 화면에
정직하게 반영한 것이다.

**분류 기준**: 각 탭의 기존 `roles` 배열을 그대로 근거로 삼았다 — MEMBER나
VIEWER가 하나라도 포함되면 "내 업무"(담당자가 자기 업무를 처리하는
화면), ADMIN/MANAGER만 있으면 "관리자 도구"(조직·리소스를 운영·관리하는
화면)로 분류했다. 임의로 새 기준을 만들지 않고 이미 검증된 RBAC 매트릭스를
그대로 재사용한다(원칙4의 정신과 같은 이유 — 근거 없는 재분류를 피한다).

~~두 구역("내 업무"/"관리자 도구")으로 분리한다~~ — **정정(2026-09-29,
외부 서비스 4곳 리서치 기반 재분류 + "로그인한 계정 유형에 맞춰 메뉴
노출을 개선해줘" 요청)**: 세 구역("내 업무"/"공용 조회"/"운영 관리")
으로 늘렸다 — "인력 검색"·"조직도"는 `roles` 배열은 "내 업무"와 같지만
(MEMBER·VIEWER 포함) 역할과 무관한 순수 조회 화면이라 담당자 개인
업무 처리 화면과 성격이 달라 별도 "공용 조회" 그룹으로 뺐다. "관리자
도구"는 실측해보니 11개 중 10개가 admin+manager 공용이라 라벨이
실제 구성과 안 맞아 "운영 관리"로 바꿨다(`docs/LIMITS.md` §37,
`docs/ROLE_TASK_CLASSIFICATION.md`). 분류 기준(§163-167)의 "MEMBER/
VIEWER 포함 여부"만으로는 "내 업무"와 "공용 조회"를 자동 구분할 수
없다는 한계가 이때 드러났다 — 그 둘의 경계는 이제 역할이 아니라
"담당자 개인 업무 처리 vs 순수 참조"라는 별도 기준으로 수동 분류한다.

**대안 검토**: 관리자 화면을 아예 별도 경로(`/admin`)나 별도 페이지로
분리하는 안도 검토했으나 기각 — 이 프로젝트는 클라이언트 라우터가 없는
단일 페이지 셸(결정 6)이고, 관리자와 매니저가 같은 브라우저 세션 안에서
업무 화면과 관리 화면을 자주 오갈 것으로 예상되어(예: 배정 요청 → 결재는
"내 업무", 그 배정에 쓸 Agent 생성은 "관리자 도구") 완전히 분리된 페이지는
오히려 흐름을 끊는다. 같은 셸 안에서 시각적으로만 구역을 나누는 쪽을
택했다.

## 결정 10 — AI Agent 생성 방식을 3단계(stub·단순 LLM·CrewAI)로 계층화하고,
CrewAI Agent에 MCP 도구를 연결한다(사용자 요청, 2026-09-26 — 3단계 프레임워크
구현 완료; MCP는 2026-09-27, "crewai-tools의 MCPServerAdapter... 참조해
mcp 기능을 구현해줘" 요청으로 `run_crew()`에 실제 연결 완료 — `docs/LIMITS.md`
§15)

**배경**: SPEC.md §3은 "출력 프레임워크: AgentForge와 동일하게 CrewAI
하나만"이라고 이미 정해뒀고, AgentForge의 실제 `code_generator/generator.py`는
LLM에게 `from crewai import Agent`로 시작하는 진짜 CrewAI 코드를 만들라고
프롬프트한다(코드 확인 완료). 하지만 HAIR-Platform은 이 계획을 아직
구현하지 않았다 — `jobs.default_stub_generator`는 CrewAI 흉내만 내는
문자열(`agents_yaml_snippet`)을 만들 뿐이고, 이번 세션에서 추가한
`jobs.llm_agent_generator`도 CrewAI 구조 없이 LLM에 자유형식 텍스트를
한 번 요청할 뿐이다(SPEC.md §12 오픈 이슈로 이미 기록됨). 사용자가
"① 단순 LLM 호출 / CrewAI 중 선택 ② CrewAI 핵심 기능 반영 ③ MCP로 외부
검색 등 도구 연동"을 요청했다 — 이 결정은 그 세 가지를 하나의 일관된
아키텍처로 계획한다.

**결정 1 — 생성 전략 3단계, 항상 opt-in(비용 원칙 유지)**:
`GenerateRequest`/`AgentExecuteRequest`에 `framework:
"stub" | "simple_llm" | "crewai" = "stub"`을 추가한다(기본값은 여전히
비용 없는 stub — Gate A-G·LLM 연동 때부터 지켜온 "명시적으로 요청했을
때만 비용 발생" 원칙을 그대로 잇는다).
- `stub`: 지금의 `default_stub_generator`(무료, 결정론적).
- `simple_llm`: 지금의 `llm_agent_generator`(1회 LLM 호출, 자유형식
  텍스트 산출물 — 빠르고 저렴하지만 다단계 추론·도구 사용·협업 불가).
- `crewai`: 이 결정에서 새로 설계하는 경로(아래).

**결정 2 — CrewAI 핵심 개념을 HAIR 도메인에 매핑**:
| CrewAI 개념 | HAIR-Platform 매핑 |
|---|---|
| `Agent(role, goal, backstory, tools, llm)` | `role` = task의 `role_name`, `goal` = 브리프 맥락+역할 목표를 LLM에게 한 번 요약시켜 채움, `backstory` = `required_skills` 목록을 문장화, `llm` = `llm.py`가 이미 판별한 provider/model(`ChatAnthropic`/`ChatOpenAI`/`ChatOllama` 래퍼로 감싼다) |
| `Task(description, expected_output, agent, context)` | `description` = `execute_agent()`에 넘기던 prompt, `expected_output` 신규 필드(사용자가 원하는 산출물 형식을 명시하게 함 — 지금까지 없던 것), `context`로 **앞 단계 Task의 출력**을 다음 Task 입력에 자동 연결(예: 기획자→분석가로 산출물이 실제로 전달됨 — 지금의 `execute_agent()`는 Agent를 독립적으로만 호출해 이게 불가능했다) |
| `Crew(agents, tasks, process, memory)` | SDLC 8역할(기획→분석→설계→FE/BE/DB→검증→품질)을 **하나의 Crew**로 묶어 `Process.sequential`로 실행 — "따로따로 만든 8개 Agent"가 아니라 "서로 산출물을 주고받으며 실제 협업하는 팀"이 된다. `memory=True`로 Crew 전체가 이전 실행 맥락을 기억(**정정, 2026-09-27 리뷰 — 실제로 반영함**: 이 표만 있고 `crew_runtime.py`에는 `memory` 키 자체가 누락돼 CrewAI 기본값 `False`로 동작하던 문서-코드 불일치를 발견해 `crew_kwargs["memory"] = True`로 고쳤다. **주의**: CrewAI 기본 메모리 임베더는 provider와 무관하게 OpenAI 임베딩 API를 쓴다 — Anthropic/Ollama로 이 경로를 써도 `OPENAI_API_KEY`가 별도로 필요하다. LIMITS.md §12 참고) |
| Tools | 결정 3(MCP)에서 채운다 |

이게 "CrewAI 핵심 기능 반영"의 실질적 의미다 — 단순히 라이브러리 이름만
바꾸는 게 아니라, 지금은 없는 **Task 간 산출물 자동 전달**과 **여러
Agent의 순차 협업**을 처음으로 가능하게 한다.

**결정 3 — 외부 도구는 MCP로 연결한다(자체 도구 구현 대신)**:
CrewAI Agent의 `tools`는 `crewai-tools`의 MCP 어댑터(`MCPServerAdapter`
계열, stdio/SSE 방식 MCP 서버 접속 지원)로 채운다 — HAIR-Platform이
직접 검색 API를 통합하지 않고, 이미 있는 MCP 서버 생태계(웹 검색, 사내
문서 검색 등)를 그대로 재사용한다(원칙4: 이미 있는 것을 재사용, 새로
안 만든다). Agent별로 연결할 MCP 서버 목록은 `AgentModelConfig`를 확장해
`mcp_servers: list[str]`로 저장한다(결정 9와 같은 패턴 — Agent별 설정을
`agent_pool`에 영속화).
**정정(2026-09-27, "crewai-tools의 MCPServerAdapter... 참조해 mcp 기능을
구현해줘" 요청) — 실제로 구현하며 타입을 바꿈**: `crewai-tools`를 실제
설치해 `MCPServerAdapter`/`mcpadapt.core.MCPAdapt` 소스를 읽어보니 서버
식별자 문자열만으로는 접속이 안 되고 접속 정보(command/url 등) 자체가
필요했다 — `mcp_servers: list[str]`을 `list[MCPServerConfig]`(command·
args·env 또는 url·transport·tool_names)로 바꿨다. 상세는
`docs/LIMITS.md` §15 참고.

**대안 검토**:
- **자체 웹 검색 도구를 직접 구현**하는 안을 검토했으나 기각 — MCP
  서버 생태계가 이미 검색·파일시스템·DB 커넥터를 표준 프로토콜로
  제공하므로, 직접 구현은 이미 해결된 문제를 다시 푸는 것이다(원칙4).
- **LangChain 전체 스택으로 전환**하는 안도 검토했으나 기각 — SPEC.md
  §3이 이미 "CrewAI 하나만"으로 스코프를 확정했고, AgentForge와의
  프레임워크 일관성이 이 프로젝트의 존재 이유(§1: "AgentForge가 검증한
  파이프라인 재사용")와 직결된다.

**한계(정직하게 명시, 기획 단계 — 실측 없음)**:
- CrewAI는 무거운 의존성(langchain 계열 하위 의존성 포함)이라 별도
  optional extra(`pip install -e ".[crewai]"`)로 유지해야 한다 — 기존
  `[llm]` extra와 분리(설치 안 하면 `stub`/`simple_llm` 경로는 그대로
  동작해야 하므로, 지연 import 원칙을 CrewAI에도 동일하게 적용).
- Crew가 8개 Agent를 순차 실행하면 LLM API 호출 횟수가 단순 LLM 호출
  대비 수 배로 늘어난다(Agent당 최소 1콜 + MCP 도구 호출당 추가 콜) —
  비용이 이번 세션에서 실측한 SDLC 8역할 생성(개당 1콜)보다 훨씬 커질
  수 있다는 것을 사용자가 실행 전에 알아야 한다.
- MCP를 통한 외부 검색 결과는 **데이터이지 지시가 아니다** — 검색
  결과에 프롬프트 인젝션이 섞여 들어올 위험을 Task 설계 시 반영해야
  한다(이 세션 자체의 원칙과 동일한 이유).
- ~~이 결정은 아직 코드로 구현되지 않았다~~ — **정정(2026-09-26, 같은 날
  4차 요청)**: 사용자가 "실제 구현해줘"라고 명시적으로 요청해 `framework`
  파라미터·`crew_runtime.crewai_agent_generator()`까지 실제로 구현·검증
  했다(`docs/LIMITS.md` §12).
  ~~MCP 도구 연결만 접속 가능한 서버가 없어 여전히 미착수다.~~ —
  **정정(2026-09-27)**: `run_crew()`에 실제로 연결했다(`_mcp_tools()`,
  `crewai_tools.MCPServerAdapter`) — 단, 접속 가능한 실제 MCP 서버가
  이 환경에 없어 종단간 검증은 못 했고(mock 경계: `MCPServerAdapter`
  클래스 자체), `crewai_agent_generator()`(F4 단일 생성)에는 아직 연결
  안 했다(그 시점엔 `agent_pool` row가 없어 MCP 설정을 읽을 곳이 없음 —
  오픈 이슈). `docs/LIMITS.md` §15 참고.
  ~~접속 가능한 실제 MCP 서버가 이 환경에 없어 종단간 검증은 못 했고~~ —
  **정정(2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지 확인해줘"
  요청)**: Parallel AI의 공개 원격 MCP 서버(계정·키 불필요)를 실제로
  찾아 접속했다 — `pool_registry.connect_mcp_tools()`로 `web_search`/
  `web_fetch` Tool을 실제로 열고 검색 호출까지 성공했다(`docs/LIMITS.md`
  §44). 연결 함수 직접 호출로 검증한 것이고 `run_crew()`를 통한 LLM의
  자율 호출까지 재현한 건 아니다 — `crewai_agent_generator()` 미연결과
  함께 그 부분은 여전히 오픈 이슈다.

## 결정 11 — CrewAI Agent 협업(위임·계층 조율)을 심화하고, 기본 Agent
세트에 VectorDB·GraphDB 기반 Agent를 추가한다(사용자 요청, 2026-09-26 —
구현 완료, 결정 10을 확장)

**배경**: 결정 10은 Task.context로 SDLC 역할 간 산출물을 순서대로
전달하는 것(Process.sequential)까지만 계획했다 — 이건 "정해진 순서대로
릴레이"이지, CrewAI가 실제로 제공하는 **동적 협업**(필요할 때 다른
Agent에게 되묻기, 매니저가 작업을 재배분하기)은 아니었다. 사용자가
"Agent 연결/협업 기능을 추가"하고, 기본 Agent 세트에 **VectorDB·GraphDB
기반 Agent**를 포함하도록 요청했다 — 이 둘은 서로 맞물린다: 지식·관계
검색 전문 Agent가 있어야 다른 Agent들이 "위임"할 대상이 생기기 때문이다.

**결정 1 — 협업 방식 확장(정적 릴레이 → 동적 위임)**:
- `Crew(process=...)`를 Crew별로 `sequential`(결정 10 기본값, 정해진
  순서) 또는 `hierarchical`(매니저가 동적으로 작업을 배분·재배분)로
  선택 가능하게 한다. SDLC 세트는 기본 `sequential`을 유지하되, 복잡한
  브리프(여러 역할이 서로의 결과를 여러 번 주고받아야 하는 경우)는
  `hierarchical`을 옵션으로 켤 수 있게 한다.
- 모든 Agent에 `allow_delegation=True`를 기본으로 켠다 — 예: Backend
  개발자 Agent가 작업 중 설계 근거가 불명확하면 설계자 Agent에게 직접
  질문하고 답을 받아 이어간다(CrewAI가 내부적으로 처리하는 co-worker
  질의). 이게 결정 10의 "정해진 순서 전달"과 다른, 진짜 "협업"이다.
- `hierarchical` 모드의 매니저는 `manager_llm`(LLM이 직접 조율) 또는
  SDLC 세트 안의 특정 역할(예: "기획자")을 `manager_agent`로 지정하는
  두 방식을 모두 허용한다 — 브리프 성격에 따라 사용자가 고른다.
- 위임이 실제로 발생하면 `audit_log`에 `agent_delegation_occurred`
  이벤트로 남긴다(누가 누구에게 무엇을 위임했는지) — 결정 7(3중 기록
  원자성)과 같은 정신으로, "협업이 실제로 일어났다"는 것을 증명 가능한
  데이터로 남긴다(LIMITS L7 교훈 재적용: 상태만이 아니라 실제 발동
  증거를 남긴다).

**결정 2 — 기본 Agent 세트에 VectorDB·GraphDB Agent 추가**:
지금까지의 "표준 8역할"(기획·분석·설계·FE·BE·DB·검증·품질)은 전부
Task를 직접 수행하는 Agent였다. 여기에 다른 Agent들이 **위임해서 물어볼
수 있는 지식 전문 Agent** 두 종류를 추가한다.

| 신규 Agent | 저장소 | 역할 | 다른 Agent가 위임하는 예시 질문 |
|---|---|---|---|
| 지식검색 Agent(VectorDB) | 임베딩 기반 벡터 스토어 | 과거 산출물·문서를 의미 기반으로 검색(RAG) | "이전에 비슷한 결제 모듈 설계를 한 적 있어?" |
| 관계분석 Agent(GraphDB) | 그래프 데이터베이스 | 조직도·시스템 의존성 같은 관계형 데이터를 질의 | "이 컴포넌트를 실제로 담당하는 사람/Agent는 누구야?" |

- **데이터 소스**: 처음부터 새 데이터를 수집하지 않는다 — 이미 있는
  HAIR-Platform 자체 데이터를 색인 대상으로 삼는다(원칙4: 있는 것을
  재사용). 지식검색 Agent는 `agent_pool.last_output`(과거 다른 Agent들이
  만든 산출물)을 임베딩해 색인한다. 관계분석 Agent는 `assignments`·
  `human_pool.manager_id`·조직도 조회 결과(F5)를 그래프로 변환해 적재한다.
- **VectorDB 구현체**: SPEC.md §3의 "로컬/온프레미스 우선" 원칙에 맞춰
  임베디드(서버 프로세스 불필요) 방식을 1순위로 검토한다 — 예:
  Chroma의 로컬 persistent 모드, 또는 SQLite 확장(`sqlite-vec`)으로
  기존 `db.py`의 SQLite 인프라를 그대로 재사용. Pinecone류 외부 SaaS
  벡터DB는 온프레미스 원칙과 충돌해 1차 범위에서 제외한다(오픈 이슈로
  이월 — 조직 규모가 커지면 재검토).
- **GraphDB 구현체**: 같은 원칙으로 정식 Neo4j 서버 운영은 1차 범위에서
  기각하고, 경량 대안(`networkx`를 프로세스 내에서 쓰거나, 관계를
  SQLite 테이블로 모델링해 재귀 쿼리로 순회)을 1순위로 검토한다 — 이미
  `db.py`가 SQLite(WAL)를 트랜잭션 저장소로 쓰기로 확정한 결정 5와
  일관된다. 실사용 규모·질의 복잡도가 커지면 그때 Neo4j를 재검토한다
  (오픈 이슈).
- **CrewAI 연결**: 이 두 Agent도 결정 10의 `framework="crewai"` 경로에서만
  의미가 있다(VectorDB/GraphDB 질의 자체가 "도구를 가진 Agent"라는
  개념이라 `stub`/`simple_llm` 경로엔 해당 사항 없음). 도구 연결은
  결정 10과 같은 방식(MCP 어댑터 우선 검토, 자체 도구는 MCP로 감쌀 수
  없는 경우에만) — 내부 구현이라도 결정 10의 "재사용 우선" 원칙을 지킨다.
- **Agent Pool 표현**: 새 컬럼을 추가하지 않고 기존 컨벤션으로 표현한다
  — `role_name="지식검색 Agent"`/`"관계분석 Agent"`, `skill_tags=
  ["vectordb","rag"]`/`["graphdb","관계질의"]`. 실제 구현 중 검색만으로
  부족하면 그때 `agent_kind` 컬럼 추가를 재검토한다(최소 변경 원칙).

**대안 검토**:
- **모든 협업을 `hierarchical`로 강제**하는 안을 검토했으나 기각 —
  매니저 LLM이 매 단계 조율 판단을 하므로 `sequential` 대비 LLM 호출이
  더 늘고, SDLC처럼 순서가 명확한 워크플로에는 과한 유연성이다. 기본은
  `sequential`, 필요할 때만 `hierarchical`로 올리는 옵트인 구조를 택했다.
- **VectorDB/GraphDB를 외부 관리형 서비스로 붙이는 안**을 검토했으나
  기각 — SPEC.md §3 "로컬/온프레미스 우선" 원칙, 그리고 이미 확정된
  SQLite 중심 저장 정책(결정 5)과의 일관성 때문이다.
- **지식검색·관계분석 Agent를 "도구"로만 두고 별도 Agent로 안 만드는
  안**(즉 기존 8역할 Agent에 함수 하나씩 붙이는 방식)도 검토했으나
  기각 — CrewAI의 `allow_delegation`은 "다른 **Agent**에게 위임"하는
  모델이라, 별도 Agent로 존재해야 다른 Agent들이 자연스럽게 위임할
  대상이 된다. 함수형 도구로 두면 이 위임·질의 흐름을 못 쓴다.

**한계(정직하게 명시, 기획 단계 — 실측 없음)**:
- `allow_delegation=True`를 기본으로 켜면 Agent 간 왕복 질의가 몇 차례
  일어날지 예측이 어려워 **LLM API 호출 횟수(=비용)가 결정 10보다도
  더 불확실**해진다 — 실행 전 예상 호출 수 상한(`max_iter`, CrewAI
  지원)을 반드시 걸어야 한다.
  **정정(2026-09-27, "crewai 기능 적절히 적용됐는지 분석해줘" 리뷰) —
  실제로 반영함**: 리뷰 전까지는 이 문장만 있고 코드에는 `max_iter`가
  전혀 전달되지 않아 CrewAI 기본값(25)에 그대로 의존하고 있었다(즉 "반드시
  걸어야 한다"고 스스로 적어두고 안 지킨 상태). `crew_runtime.py`의
  `DELEGATION_MAX_ITER = 10`을 모든 협업 Agent에 명시적으로 설정해 해소함
  — LIMITS.md §12 참고.
- 임베딩 생성(VectorDB 색인)도 별도 API 호출이다(Anthropic/OpenAI
  임베딩 엔드포인트, 또는 로컬 임베딩 모델) — `llm.py`의 "비용 발생은
  항상 opt-in" 원칙을 임베딩 호출에도 그대로 적용해야 한다.
- 관계분석 Agent가 조직도·배정 데이터를 그래프로 노출하면, F8 성과리뷰의
  NF9(동의 기반 최소수집) 원칙과 충돌하지 않는지 재검토가 필요하다 —
  예를 들어 "이 사람이 최근 반려당한 배정이 몇 건이냐" 같은 질문에
  관계분석 Agent가 답하면 사실상 성과 데이터를 우회 노출하는 셈이다.
  **정정(2026-09-26, 4차 요청) — 실제로 구현하며 해소함**: `graphquery.py`
  자체가 성과 관련 컬럼(`performance_score`·`performance_consent`)을 어떤
  쿼리에서도 SELECT하지 않도록 만들었고, 회귀 테스트(`test_gc86`)가 실제
  SQL 쿼리 문자열을 검사해 이를 강제한다 — 우려가 코드 수준에서 구조적으로
  막혀 있다.
- ~~이 결정도 결정 10과 마찬가지로 아직 코드로 구현되지 않았다.~~ —
  **정정**: 실제 구현 완료(`docs/LIMITS.md` §12).

## 결정 12 — Skill Pool·Tool Pool로 Agent의 지식·도구 그라운딩을 데이터
중심화하고, VectorDB/GraphDB를 CrewAI 네이티브 Tool로 전환한다(사용자 요청,
2026-09-27 — "AI Agent에서 사용할 Skills, MCP Tool 등을 Pool로 구성하고
지식 및 도구 그라운딩... CrewAI의 데코레이터 도구선언 기능과 비교해 개선점을
찾아줘" — **구현 완료**. 단계별로: VectorDB/GraphDB의 CrewAI Tool화는
"이미 생성된 또는 생성하는 AI Agent에서 MCP Tool처럼, 또는 CrewAI
Tool처럼 동작하도록 개선해줘" 요청으로, `skill_pool`·`tool_pool`·
`agent_tool_links` 3개 신규 테이블과 그 CRUD API·Web UI는 이어진
"tool_pool·skill_pool 테이블도 실제로 구현해줘" 요청으로 각각 실제
구현했다 — `docs/LIMITS.md` §18 참고)

**배경 — 실제 구현을 분석해 찾은 두 가지 구조적 갭**:

1. **skill_tags가 자유 문자열이고, 특수 취급이 코드에 하드코딩돼 있다.**
   `agent_runtime.py`의 `VECTORDB_SKILL_TAGS = {"vectordb", "rag"}`,
   `GRAPHDB_SKILL_TAGS = {"graphdb", "관계질의"}`가 유일한 "스킬 → 동작"
   매핑이다 — 새 스킬(예: "웹검색")을 추가하려면 코드를 고쳐야 하고, 이걸
   가진 Agent가 몇 명이든 매핑은 전역으로 하나뿐이다.
2. **MCP 서버 설정이 Agent마다 개별 저장이라 재사용이 안 된다.**
   `AgentModelConfig.mcp_servers`는 Agent 레코드 하나하나에 JSON을 통째로
   저장한다(`agent_pool.mcp_servers` 컬럼) — 같은 사내 검색 MCP 서버를
   10개 Agent가 같이 쓰려면 JSON을 10번 붙여넣어야 하고, URL이 바뀌면 10곳을
   다 고쳐야 한다. "이미 있는 걸 재사용"(원칙4)이 Agent 레벨에서는 되는데
   Tool 레벨에서는 안 된다.
3. **VectorDB/GraphDB가 CrewAI의 진짜 "Tool"이 아니라 정적 프롬프트
   주입이다.** `crewai.tools.tool` 데코레이터(실제 설치된 패키지에서
   `inspect`로 소스 확인함 — 함수+독스트링+타입힌트를 `Tool` 인스턴스로
   변환해 `Agent(tools=[...])`에 넣으면, **LLM이 스스로 호출 여부·횟수·
   질의어를 판단**하는 진짜 에이전틱 도구가 된다)와 달리, 지금
   `agent_runtime.augment_with_retrieval()`/`crew_runtime.py`의 같은 함수
   호출은 **Task를 만들기 전에 우리가 무조건 검색 결과를 프롬프트 앞에
   붙인다** — Agent에게 선택권이 없고, 검색이 필요 없는 Task에도 항상
   임베딩 API 비용이 발생한다. MCP 도구는 `MCPServerAdapter`로 이미 진짜
   Tool이 돼 있으니 이 갭은 VectorDB/GraphDB 두 가지에만 해당한다.

**결정 — 두 신규 Pool + Tool화 전환**:

| 엔티티 | 목적 | 핵심 필드 |
|---|---|---|
| `tool_pool`(신규) | 실제 도구(MCP 서버 또는 native `@tool`) 하나를 이름으로 등록해 여러 Agent가 재사용 | `id, name(고유), description, kind(mcp_stdio\|mcp_remote\|native), config(JSON), tool_names(JSON, MCP 필터), is_active` |
| `agent_tool_links`(신규, N:M) | 어떤 Agent가 어떤 Tool Pool 항목을 쓰는지 | `agent_id, tool_pool_id` |
| `skill_pool`(신규) | 지금의 자유문자열 `skill_tags`를 정식 엔티티로 승격 | `id, name(고유, 기존 skill_tag와 매핑), description, default_tool_pool_ids(JSON), augmentation_mode("always"\|"tool"\|"none"), knowledge_collection(선택)` |

- `tool_pool`은 MCP든 native(`vectorstore.search`/`graphquery.context_for_prompt`를
  `@tool`로 감싼 것)든 한 표에 등록한다 — Agent 생성/실행 시점에
  `agent_tool_links`를 조회해 실제 CrewAI `Tool` 목록으로 조립한다.
- `skill_pool.augmentation_mode`로 두 방식을 스킬별로 선택 가능하게 한다:
  - `"tool"`(신규 권장 기본값): `@tool`로 감싼 함수를 `tools=`에 등록 —
    Agent가 필요할 때만 호출(진짜 에이전틱 사용, 비용 절감 가능성).
  - `"always"`(지금 방식 유지): 지금처럼 Task 생성 전에 무조건 프롬프트에
    주입 — "에이전트가 검색을 깜빡할 위험이 없어야 하는" 케이스(예: NF9
    경계처럼 반드시 매번 적용돼야 하는 안전장치성 로직)에 남겨둔다.
  - `"none"`: 아무 것도 안 함(순수 LLM 역할).
- `skill_pool.knowledge_collection`으로 지식 그라운딩 범위를 명시한다 —
  지금 `vectorstore.search()`는 `embedding_provider`/`model`로만 필터링하고
  나머지는 **전역 공유 지식 풀**(누가 색인했든 다 검색 대상)이다. 이건
  실측 확인한 현재 동작이지 버그가 아니다 — 다만 조직이 커지면 "이 스킬은
  이 컬렉션의 지식만 봐야 한다"는 경계가 필요해질 수 있어, `knowledge_
  chunks`에 `collection` 컬럼을 추가해 스킬 단위로 지식을 분리할 수 있게
  설계 여지를 남긴다(당장 구현하지 않음 — 오픈 이슈로 명시).
- Agent 생성(F4)/온보딩 시 `skill_tags`가 `skill_pool`의 이름과 일치하면
  그 스킬의 `default_tool_pool_ids`를 자동으로 `agent_tool_links`에
  연결한다(수동으로 Tool Pool을 매번 고르지 않아도 되도록) — 그래도 Web
  UI에서 Agent별로 추가/제거는 가능해야 한다(자동 연결은 기본값이지 강제가
  아니다).

**대안 검토**:
- **Skill Pool 없이 Tool Pool만 두고 Agent에 직접 연결**: 검토했으나 기각
  — "이 역할군은 항상 이 도구 세트를 쓴다"는 재사용 패턴(모든 지식검색
  역할이 vectorstore 도구를 쓰는 것)이 이미 존재하므로, Skill을 거치는
  한 단계 간접화가 신규 Agent 생성 시 스킬 태그만으로 도구가 자동 연결되는
  이점을 준다.
- **기존 `skill_tags` 문자열 컬럼은 그대로 두고 Skill Pool을 "참고 문서"로만
  둠(코드 매핑은 하드코딩 유지)**: 검토했으나 기각 — 그러면 지금 문제
  (새 스킬 추가 시 코드 수정 필요)가 그대로 남아 "Pool로 구성"하라는 요청의
  취지(데이터 중심화)를 충족하지 못한다.
- **VectorDB/GraphDB를 전부 `"tool"` 모드로 강제 전환**: 검토했으나 기각 —
  Agent가 검색을 안 부르면 `graphquery.py`의 NF9 안전장치(성과 데이터
  절대 미노출)는 여전히 지켜지지만(그 로직은 SQL 자체에 있어 tool
  호출 여부와 무관), "관계분석 Agent가 정말 필요할 때 관계 데이터를 참고
  못 하는" 리스크가 생긴다 — 스킬별 선택(`augmentation_mode`)으로
  두 방식을 다 지원하는 쪽이 안전하다.
- **MCP `tool_pool`을 아예 안 만들고 지금처럼 Agent별 JSON 유지**: 검토했으나
  기각 — 이번 요청이 명시적으로 "Pool로 구성"을 요구했고, 실측으로 확인한
  "URL 하나 바뀌면 N군데 수정" 문제가 실사용에서 반복될 것으로 예상된다.

**한계(정직하게 명시)**:
- ~~`tool_pool`/`skill_pool`/`agent_tool_links` 테이블, 관리 API(CRUD),
  Web UI 화면은 전부 신규 설계일 뿐 구현되지 않았다.~~ — **정정
  (2026-09-27, "이미 생성된 또는 생성하는 AI Agent에서... 개선해줘"
  요청)**: 3개 신규 테이블 자체는 여전히 미구현이다(그대로 오픈 이슈).
  다만 이 결정의 핵심 아이디어인 **VectorDB/GraphDB를 정적 프롬프트
  주입 대신 진짜 CrewAI Tool로 전환**하는 부분은 Pool 테이블 없이도
  구현했다 — `vectorstore.as_crewai_tool(conn)`/`graphquery.
  as_crewai_tool(conn)`이 `crewai.tools.tool` 데코레이터로 실제 Tool을
  만들고, `crew_runtime._skill_based_tools()`가 skill_tags를 보고 그
  Tool을 `run_crew()`와 `crewai_agent_generator()` 양쪽(**이미 생성된
  Agent** + **생성되는 Agent** 둘 다) Agent에 붙인다(GC106-107). 정적
  주입(`agent_runtime.augment_with_retrieval()`)은 도구 호출 루프가 없는
  `execute_agent()`(단순 1회 LLM 호출) 경로에만 남겼다 — 그 경로는
  Agent가 스스로 도구를 호출할 방법이 구조적으로 없다. `GeneratorFn`
  계약(`jobs.py`)에 `conn`을 추가해 F4 생성 시점부터 Tool을 붙일 수 있게
  계약 자체를 바꿨다.
- ~~`tool_pool`/`skill_pool`/`agent_tool_links`(도구 재사용 레지스트리,
  MCP 서버를 여러 Agent가 이름으로 공유하는 것) 자체는 여전히 미구현~~
  — **정정(2026-09-27, "tool_pool·skill_pool 테이블도 실제로 구현해줘"
  요청)**: 3개 테이블·`pool_registry.py`(CRUD+`resolve_pool_tools()`)·
  CRUD API(`/api/tool-pool`, `/api/skill-pool`, `/api/agent-pool/{id}/
  tools/{tool_pool_id}`)·Web UI("도구/스킬 Pool" 탭 + "AI 에이전트" 탭의
  도구 연결/해제)까지 실제 구현했다(GC108-115). `crew_runtime.py`의
  `_skill_based_tools()`가 이제 `agent_runtime.VECTORDB_SKILL_TAGS`/
  `GRAPHDB_SKILL_TAGS` 하드코딩 대신 `skill_pool` 데이터를 조회한다 —
  GC115가 그 두 상수를 빈 집합으로 patch해도 `run_crew()`가 여전히
  도구를 붙이는 것으로 하드코딩 의존이 실제로 끊겼음을 확인했다.
  `db.init_schema()`가 매 기동마다 VectorDB/GraphDB native Tool과
  vectordb/rag/graphdb/관계질의 스킬을 멱등하게 시드해 기존 동작과의
  호환을 보장한다.
- `skill_pool.augmentation_mode`는 API/스키마 레벨로는 `"tool"`/
  `"always"`/`"none"` 셋 다 저장 가능하지만, `resolve_pool_tools()`는
  `"tool"`만 실제로 읽는다 — `"always"`(정적 주입) 모드를 skill_pool에서
  선택했을 때 실제로 정적 주입을 트리거하는 로직은 아직 없다(오픈 이슈).
- `"tool"` 모드로 전환했을 때 Agent가 실제로 검색을 얼마나 자주/적절히
  호출하는지(비용이 늘지 줄지)는 실측 전까지 예측할 수 없다 — "always"
  대비 우월하다고 미리 단정하지 않는다.
- 기존 8개 SDLC Agent·Human Pool의 `skill_tags` 문자열 데이터는 이미
  `skill_pool`에 등록된 이름(vectordb/rag/graphdb/관계질의)과 겹치면
  자동으로 매칭된다(마이그레이션 스크립트 불필요 — `resolve_pool_tools()`
  가 매번 다시 계산하기 때문). 다만 이 4개 외의 새 스킬(예: "웹검색")을
  쓰려면 관리자가 Web UI에서 Tool Pool·Skill Pool을 직접 등록해야 한다.
- `knowledge_chunks.collection` 컬럼 추가(지식 그라운딩 범위 분리)는 이
  결정에서 여지만 남기고 실제 스키마 변경은 하지 않는다 — 실사용에서
  전역 공유 지식 풀이 실제로 문제가 될 때 재검토한다.

## 결과 / 트레이드오프

- 결정 3(RBAC 매트릭스)에 따라 "팀원"은 F1(브리프 제출)과 F3(Pool 조회)·F5(조직도
  조회)·F8(본인 성과)만 가능하다 — 실사용 중 팀원의 셀프서비스 요구(예: 본인이 직접
  배정 요청)가 크면 Phase 4 이후 권한 매트릭스를 완화하는 재검토가 필요할 수 있다.
- 결정 8(Docker 샌드박스)로 Job Queue/Worker 실행 환경에 Docker 런타임 의존성이
  생긴다 — README·설치 가이드에 Docker 필수 요건으로 명시해야 한다(v3 작업 시 반영).
- 결정 1-A(F2/F6 경계 확정)로 SPEC.md F2 문구를 정정했다 — 이미 배포된 문서가 있다면
  동기화 필요(현재는 SPEC.md 자체가 아직 `spec_review` 전 DRAFT라 영향 없음).
- 결정 9(내 업무/관리자 도구 분리)는 순수 UI 변경이라 백엔드 RBAC·API 계약에
  영향이 없다 — 다만 새 탭을 추가할 때마다 `group`을 명시해야 하는 규칙이
  하나 늘었다(안 하면 어느 그룹에도 안 뜬다는 것을 코드 리뷰 체크리스트에
  남긴다).
- 결정 10(CrewAI+MCP)은 **구현 완료(2026-09-26, "실제 구현해줘" 요청 +
  2026-09-27, "crewai-tools의 MCPServerAdapter... 참조해 mcp 기능을
  구현해줘" 요청)** — `framework` 파라미터·`crew_runtime.
  crewai_agent_generator()`·실제 crewai 패키지 설치·통합 검증(GC77-78)에
  이어, `run_crew()`에 `_mcp_tools()`(`crewai_tools.MCPServerAdapter`
  실제 연결, GC103-105)까지 마쳤다.
  ~~접속 가능한 실제 MCP 서버로의 종단간 검증은 오픈 이슈로 남는다~~ —
  **정정(2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지 확인해줘"
  요청)**: Parallel AI의 공개 원격 MCP 서버(계정·키 불필요)로 `mcp_remote`
  접속 자체는 실제로 검증했다 — `pool_registry.connect_mcp_tools()`를
  직접 호출해 `web_search`/`web_fetch` 두 Tool이 정상 접속되고, 실제
  검색 호출까지 성공했다(`docs/LIMITS.md` §44). 다만 이건 연결 함수를
  직접 호출한 검증이고, `run_crew()`/`crewai_agent_generator()`를 통해
  실제 LLM이 이 Tool을 스스로 호출하는 것까지 끝까지 재현한 건 아니다
  — 그 부분과 `crewai_agent_generator()`(F4 단일 생성)의 MCP 미연결은
  여전히 오픈 이슈다.
- 결정 11(Agent 협업 심화 + VectorDB/GraphDB Agent)도 **구현 완료
  (2026-09-26)** — `allow_delegation=True`+`Process.hierarchical` 옵션,
  `run_crew()`의 Task.context 체이닝, 실제 CrewAI 이벤트버스 기반 위임
  캡처, `vectorstore.py`(VectorDB)·`graphquery.py`(GraphDB, NF9 경계를
  소스 검사 테스트로 강제)까지 구현·검증했다(GC79-92). F8 우회 위험은
  `graphquery.py`가 성과 컬럼을 아예 SELECT하지 않는 것으로 해소했다 —
  회귀 테스트(`test_gc86`)가 실제 SQL 쿼리 문자열을 검사해 이를 강제한다.
  상세 실측 범위·한계는 `docs/LIMITS.md` §12 참고.
  **정정(2026-09-27, "crewai 기능 적절히 적용됐는지 분석해줘" 리뷰) —
  실사용 크래시 버그 발견·수정**: `manager_agent_id`를 지정한 채
  `hierarchical`을 쓰면 그 Agent가 `Crew(agents=...)`와 `manager_agent=`
  양쪽에 동시에 들어가 CrewAI 자체 validator가
  `ValidationError(manager_agent_in_agents)`를 던지는 100% 재현 버그가
  있었다 — Web UI "Crew 실행" 탭의 매니저 지정 입력을 실제로 쓰면 항상
  실패했다(회귀 테스트가 없어 GC77-97 100건 통과에도 안 잡혔다). 매니저를
  `agents`/`tasks` 목록에서 제외하고 `manager_agent`로만 넘기도록 고쳤다
  (CrewAI의 hierarchical 모델 자체가 "매니저는 조율만, 작업은 직접
  수행하지 않는다"는 전제와 일치) — 회귀 테스트 GC98-99 추가.
  `hierarchical`에서 Task.context 체이닝을 걸지 않는 것은 버그가 아니라
  의도된 설계임을 코드 주석으로 명시했다(매니저가 동적으로 조율하므로
  "바로 앞 Task"만 고정 전달하는 sequential 방식이 의미가 없다).
  **정정(2026-09-27, "CrewAI 기능/개념을 제대로 활용하도록 구현되어
  있는지 확인해줘" 요청) — RAG 증강이 Crew 협업에서 빠져 있던 실사용
  갭 발견·수정**: 지식검색(VectorDB)·관계분석(GraphDB) Agent의 RAG
  증강(`agent_runtime.augment_with_retrieval()`, 옛 이름
  `_augment_with_retrieval`)이 개별 실행(`execute_agent`)에서만 호출되고
  `run_crew()`(이 결정의 핵심인 Crew 협업)에는 전혀 연결돼 있지 않았다
  — 이 Agent들을 Crew에 넣어 협업시켜도 RAG 증강 없이 원문 그대로
  실행됐다. `run_crew()`의 Task 생성 루프에 동일하게 연결해 고쳤다
  (GC106). 함께 검토한 CrewAI 개념 4건(`human_input`, `token_usage`,
  `allow_delegation` 역할별 차등, `planning`)의 판단 근거는
  `docs/LIMITS.md` §17 참고.
- 결정 12(Skill Pool·Tool Pool + VectorDB/GraphDB Tool화)는 **구현
  완료**(2026-09-27, 같은 날 세 단계로 진행). 계획 시점엔 "기획 단계 —
  아직 코드 미반영"이었으나, "이미 생성된 또는 생성하는 AI Agent에서
  MCP Tool처럼, 또는 CrewAI Tool처럼 동작하도록 개선해줘" 요청으로 핵심
  전환(VectorDB/GraphDB를 `crewai.tools.tool` 데코레이터 기반 진짜
  CrewAI Tool로 전환, `run_crew()`+`crewai_agent_generator()` 양쪽에
  연결, `GeneratorFn` 계약에 `conn` 추가, GC106-107)을, 이어서
  "tool_pool·skill_pool 테이블도 실제로 구현해줘" 요청으로 `tool_pool`·
  `skill_pool`·`agent_tool_links` 3개 신규 테이블과 CRUD API·Web UI까지
  실제로 구현했다(GC108-115) — 상세는 `docs/LIMITS.md` §18.
