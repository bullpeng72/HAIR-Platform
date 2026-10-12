# 화면 UX 벤치마크 — Notion AI·ClickUp Brain·Linear·Devin·Replit Agent·CrewAI·LangGraph Studio

사용자 요청(2026-10-12, "외부 검색 등으로 경쟁제품들의 UI/UX 및 기능을 분석해
개선점을 도출해줘" → 방향 확인 질문에 "새 제품 + 실제 화면(UI) 중심으로 확장"
선택 → 후속 요청 "claude 아티팩트 수준의 UI로 HAIR-Platform 실행결과를
제공해주는 개선을 포함하고, 위 조사결과를 반영한 현재 UI/UX의 시각적 개선
방안을 포함해줘" → 추가 피드백 "칸반, SVG 등 기존 코드들을 적극 활용해줘")
— **기획 문서, 코드 변경 없음.** §5(Deliverable을 Claude Artifact 수준으로
보여주는 뷰어 개선)·§6(비주얼 디자인 개선)은 후속 요청으로 추가됐고, §3-1은
마지막 피드백으로 추가됐다 — `crewDiagramSvg()`(webui.py, Crew 실행 결과를
구조+실제 위임까지 SVG로 그리는 기존 함수)를 처음엔 놓쳐 §4-G를 "신규 화면"
으로 잘못 분류했던 것을 §3-1에서 바로잡았다(코드 재확인 후 비용 등급 하향).

## 0. 조사 범위와 기존 문서와의 관계

이 주제로 이미 세 문서가 있다. 이 문서는 그것들이 다루지 **않은** 축(실제
화면 레이아웃·시각 패턴)만 다룬다 — 중복 조사하지 않았다.

| 기존 문서 | 다룬 축 |
|---|---|
| `HAIR_REDESIGN_PROPOSAL.md` | Asana AI Teammates·Riido — 제품 전체 구조(정보구조·워크플로우 깊이·포지셔닝) |
| `COLLABORATION_UX_IMPROVEMENT_PROPOSAL.md` | LangGraph·AutoGen·CrewAI 기본기능·HumanLayer·Copilot Studio·ServiceNow·UiPath·Sierra — 승인/거버넌스 **철학** |
| `COLLABORATION_PLATFORM_PLAN.md` | flow.team·GitHub Copilot·Relay.app·Lindy.ai — HITL/HOTL 메커니즘 |
| **이 문서** | Notion AI·ClickUp Brain·Linear·Devin·Replit Agent·CrewAI·LangGraph Studio — **실제 화면 레이아웃·멀티에이전트 시각화·대시보드 UI** |

조사는 웹 검색(공식 문서·블로그·제품투어 설명)으로 수행했다 — **스크린샷 이미지
자체를 직접 보지는 못했다**(검색 결과는 전부 텍스트 서술). "화면 사실"로 적은
내용은 공식 소스 서술에 근거하며, 확인되지 않은 세부(정확한 색상·배지 모양 등)는
항목별로 "확인 안 됨"이라 명시했다 — 추측 없음.

---

## 1. 제품별 화면 패턴

### 1-1. Notion AI (Notion 3.0, 2025)

**화면 사실**: 사이드바에 Home/Agents 전용 섹션이 분리돼 있다. Agent는 계획을
세워 수백 페이지·최대 20분까지 작업을 이어가고, 중단 후 맥락을 유지한 채
재개한다(Notion 공식 Help "A new sidebar"; tomsguide.com "Notion 3.0 launches
AI agents"). 진행률 UI의 시각적 디테일은 확인 안 됨.

**HAIR 적용 포인트**: "Agent 전용 사이드바 섹션 분리"는 HAIR의 "AI 에이전트
관리"가 "설정" 그룹의 다른 7개 탭(Pool 5종+Team+Model Registry)과 섞여 있는 것과
대조된다. "중단 후 맥락 유지 재개"는 Conversational Workspace에 적용 가치가 있다.

### 1-2. ClickUp Brain

**화면 사실**: 같은 작업 데이터를 List/Board/Gantt/Calendar/Timeline/Table/Mind
Map 등 15개 이상 뷰로 전환해 볼 수 있고, 프롬프트로 즉석 칸반 보드를 생성한다
(clickup.com "AI-Powered Kanban Boards"). AI 생성 항목과 사람이 만든 항목의 시각적
구분 여부는 확인 안 됨.

**HAIR 적용 포인트**: §3에서 HAIR의 실제 현황(이미 칸반 보드가 있다)과 정밀 대조.

### 1-3. Linear

**화면 사실**: 이슈는 단일 assignee(소유권)이고, **"Delegation"(위임)은 별도
개념**으로 AI Agent가 처리하되 사람이 소유권은 유지한다. 카드의 assignee
아바타를 클릭해 바로 재할당한다(linear.app/docs "assigning-issues"; ona.com
"configure linear"). Agent 전용 배지의 정확한 모양은 확인 안 됨.

**HAIR 적용 포인트**: "배정(소유권 이전, 무거움)"과 "위임(가벼움)"을 **같은
UI 슬롯(아바타 클릭)에서 전환**하는 패턴 — HAIR가 "배정=F7 결재 체인"으로만
다루는 것과 대조된다. `HAIR_REDESIGN_PROPOSAL.md` §4-0의 "초대/배정 분리"
제안을 더 구체적인 인터랙션(아바타 클릭 한 번)으로 좁혀준다.

### 1-4. Devin (Cognition)

**화면 사실**: 세션 UI는 Shell/IDE/Desktop/Progress 4패널 구조다. Progress
탭이 셸·코드·브라우저 활동을 통합해 보여주고, 코드 변경은 "Global Work
Diff"로 제공된다. **Ask 모드(계획만) → Agent 모드(자율 실행)** 2단계 전환을
권장한다. 병렬 작업은 메인 세션이 coordinator가 돼 하위 "managed Devin"에게
작업을 나누고 각각 독립 세션 링크로 모니터링한다(docs.devin.ai
"devin-session-tools"; cognition.com "Devin can now Manage Devins"). 하위
세션들을 한 화면 리스트로 보여주는 대시보드 존재 여부는 확인 안 됨.

**HAIR 적용 포인트**: ① "계획만 먼저 보여주고 사람 확인 후 실행"이라는 2모드
전환은 HAIR의 "폼 작성 → 즉시 실행" 1단계 흐름에 중간 확인 지점을 넣을
구체적 근거다. ② "코디네이터가 하위 Agent들에게 작업을 나누고 모니터링"은
Dev Crew의 `hierarchical` 실행(QA/검증자가 매니저)과 대조된다 — 다만 §3-1에서
확인한 대로 HAIR는 이미 `crewDiagramSvg()`(webui.py 3693행)로 매니저↔워커
구조+실제 위임을 SVG로 그린다. 진짜 차이는 "완료 후 1회"냐 "실행 중
실시간"이냐뿐이다(§4-G).

### 1-5. Replit Agent

**화면 사실**: 체크포인트는 코드+프로젝트 파일+**AI 대화 맥락**+연결된
DB까지 포함하는 완전한 상태 스냅샷이다. 과거 체크포인트로 롤백하면 이후
변경은 "대체 분기"가 된다(docs.replit.com "checkpoints-and-rollbacks"). 타임라인
UI의 시각 형태는 확인 안 됨.

**HAIR 적용 포인트**: "체크포인트에 AI 대화 맥락까지 포함"은 §3에서 확인한
HAIR의 실제 스키마 갭(Deliverable↔Conversation 연결 없음)과 정확히 대응된다.
"되돌리면 그 지점부터 분기"는 반려 시 "처음부터 재제출"뿐인 HAIR 결재
흐름에 개선 방향을 시사한다.

### 1-6. CrewAI(Enterprise/AMP — HAIR가 쓰는 프레임워크 그 자체)

**화면 사실**: 공식 UI가 실제로 있다 — AMP Dashboard(배포+실시간 모니터링),
Crew Studio(자연어+시각적 워크플로우 에디터), **Traces 탭**(Agent 사고·Task
실행 상세·도구 사용·**토큰 소비·실행시간·추정비용**을 상단에 합계로 표시,
`CREWAI_TRACING_ENABLED=true`로 활성화)(docs.crewai.com/enterprise/introduction;
docs.crewai.com "traces"). 실시간 갱신 여부·"역할별 Agent 카드" 시각화 존재
여부는 확인 안 됨.

**HAIR 적용 포인트**: §3에서 확인한 대로, HAIR는 이미 이 수치(`token_usage`)를
계산해두고도 화면에 안 보여준다 — **가장 싸고 직접적인 개선**(§4-A).

### 1-7. LangGraph Studio/Platform

**화면 사실**: 플로차트형 캔버스에서 노드/엣지로 멀티에이전트 그래프를
보여주고, 노드 단위로 멈춰(step-through) 그 자리에서 결과·로직을 수정할 수
있다. `interrupt()`가 멈춘 지점의 payload(질문·수정 가능한 텍스트·UI
컨트롤)를 호출자에게 넘기고, **UI가 그 payload를 그대로 렌더링**해 사람이
무엇을 기다리는지 보여준다(blog.langchain.com "LangGraph Studio: The first
agent IDE"; LangChain 공식 docs "human-in-the-loop"). 멈춘 노드의 시각적
강조 방식은 확인 안 됨.

**HAIR 적용 포인트**: "노드(=단계) 단위로 멈추고 그 지점 결과를 즉시
보여준 뒤 사람이 승인해야 다음으로 넘어간다"는 구조는, `SPECIALIST_AGENTS_
PLAN.md` §2-0-1이 이미 제안한 "Dev Crew 5단계를 Task 5개 체인으로 쪼개
단계별 결재"를 어떤 화면으로 구현할지에 대한 구체적 답이다 — 그리고 그
"노드/엣지 그래프"를 그릴 렌더러를 새로 만들 필요가 없다: `crewDiagramSvg()`
(sequential 모드, 3709행)가 이미 agent를 좌→우로 배치해 구조선을 그리고,
`renderDependencyGraphSvg()`(1398행)는 깊이별 레이어 DAG를 그린다. 둘 중
하나를 확장하면 된다(§3-1).

---

## 2. 반복되는 패턴 2가지 (종합)

1. **"같은 데이터를 다른 틀로" + "요약 수치를 한 행에"** — ClickUp의
   뷰 전환, CrewAI Traces의 상단 합계 배지. 둘 다 새 데이터 수집이
   필요 없다 — 이미 있는 데이터를 다르게 배치만 하면 된다.
2. **"가벼운 전환 vs 무거운 이전을 같은 UI 슬롯에서 분리"** — Linear의
   위임/배정, Devin의 Ask/Agent 모드. `HAIR_REDESIGN_PROPOSAL.md` §4-0의
   "초대/배정 분리" 제안을 구체적 인터랙션 수준까지 좁혀준다.

---

## 3. HAIR-Platform 현재 상태와의 정밀 대조(코드로 직접 확인)

조사 과정에서 "HAIR의 Task는 테이블 뷰 하나뿐"이라고 추정했으나, 코드를
직접 확인해보니 **부정확했다** — 정정한다.

| 항목 | 실제 상태(코드 확인) | 진짜 갭 |
|---|---|---|
| 할 일 보드 | `webui.py` `renderBoard()`(1309행) — **이미 4칼럼 칸반**(백로그/대기/진행중/완료, ASANA_PARITY F16) | 범위가 "내 업무"(개인)로 고정돼 있다 — 팀/조직 단위로 못 본다 |
| 선후관계 시각화 | `webui.py` `_dependencyGraphDepths()`+`renderDependencyGraphSvg()`(1378/1398행) — **이미 SVG DAG 그래프**(Riido 벤치마크 반영, 의존 깊이별 레이어 배치, 노드 클릭으로 워크스페이스 열림) | 이미 상당히 구현돼 있음 — 추가 투자 우선순위 낮음 |
| Crew 구조·위임 다이어그램 | `webui.py` `crewDiagramSvg()`(3693행, 2026-10-06 추가) — **이미 SVG로** sequential(좌→우 구조선)/hierarchical(매니저↔워커) 구조 + 실제 위임(주황 점선, 곡선, 사용 도구 라벨)을 그린다. `renderCrewResult()`가 Crew 완료 후 호출 | **진짜 갭은 "실시간"이 아니라는 것뿐** — 지금은 `crew.kickoff()`가 전부 끝난 뒤 1회만 그려진다(§4-G) |
| Crew 실행 결과 토큰/비용 | `crew_runtime.py` `run_crew()`가 `result.token_usage`를 **계산은 하지만**(584-591행 반환 dict에 없음) `audit_log`에만 참고 기록으로 남고 API 응답엔 안 들어감 | **진짜 갭** — 계산된 데이터가 화면에 못 올라감(§4-A) |
| Deliverable↔대화 연결 | 스키마 확인(`db.py`) — `deliverables`는 `conversations`/`conversation_messages`와 **아무 FK도 없다**. `agent_execution_jobs.source_comment_id`는 `deliverable_comments`(Task 결재 트랙)만 가리키고, Conversational Workspace(`conversations`)는 완전히 별도 트랙 | **진짜 갭** — Replit의 "체크포인트=코드+대화 맥락" 패턴과 대응(§4-C) |

### 3-1. 기존 칸반/SVG 컴포넌트 재사용 매핑

사용자 피드백(2026-10-12, "칸반, SVG 등 기존 코드들을 적극 활용해줘") — 아래
제안 중 "보드"나 "그래프"가 필요한 항목은 **전부 새 렌더러를 만들지 않고**
이미 있는 3개 함수를 확장/재사용한다. 이번 정정 전까지 이 문서가 §4-G를
"신규 화면, 큰 구조 변경"으로 잘못 분류했었다 — `crewDiagramSvg()`의 존재를
놓쳤기 때문이다(아래 표로 바로잡는다).

| 기존 함수 | 지금 하는 일 | 이 문서에서 재사용할 곳 |
|---|---|---|
| `renderBoard()`의 `STATUS_COLUMNS` 카드 템플릿(1319-1339행) | "내 업무"를 4칼럼 칸반으로 그림 | (B) 할 일 보드 범위 확장 — 데이터 소스(my-work→team/org)만 바꾸면 카드 템플릿은 그대로 |
| `crewDiagramSvg()`(3693행) | sequential/hierarchical 구조선 + 실제 위임 점선을 SVG로 그림(완료 후 1회) | (E) Dev Crew 단계별 확인 모드, (G) 코디네이터 패널 — **같은 함수를 "완료 후 1회"가 아니라 "Task 완료마다" 다시 호출**하도록 트리거만 옮기면 됨. 새 그래프 렌더러 불필요 |
| `_dependencyGraphDepths()`+`renderDependencyGraphSvg()`(1378/1398행) | Task 의존관계를 깊이별 레이어 DAG로 그림 | Dev Crew/Biz Crew의 Task 체인(`SPECIALIST_AGENTS_PLAN.md` §2-0-1이 이미 `task_dependencies`로 구성하자고 제안함) 시각화에 **그대로 적용 가능** — 그 체인도 결국 `task_dependencies` 테이블이라 같은 API/렌더러가 그대로 먹는다 |

이 재사용 때문에 §4의 (E)/(G) 비용 등급을 아래에서 하향 조정했다 — "신규
화면"이 아니라 "기존 SVG 함수의 호출 시점/빈도 확장"이기 때문이다.

---

## 4. 구체적 개선 제안(비용순)

### ★★★ 즉시 가능(기존 데이터 재배치만)

**(A) Crew 실행 결과에 토큰/비용/실행시간 요약 배지 — 완료(2026-10-12)**
— `crew_runtime.run_crew()` 반환 dict에 `token_usage` 추가, `webui.py`
`renderCrewResult()`에 `.stat-row`(§I5) 카드로 총 토큰/입력/출력/LLM
호출 수를 표시한다. 비용/실행시간은 CrewAI가 집계해주지 않아(§1-6) 넣지
않았다.

**(B) 할 일 보드 범위 확장 — 완료(2026-10-12)** — `deliverables.
list_all_work()` + `GET /api/all-work`(ADMIN/MANAGER 전용) 신설.
`renderBoard()`에 "내 업무"/"전체 조직" 범위 selector를 추가했고, 칸반
카드 템플릿(`STATUS_COLUMNS`)은 그대로 재사용했다(§3-1 원칙 그대로 적용).

**(C) Deliverable 상세에 "관련 대화 보기" — 정정: 이미 돼 있다, 구현 불필요**
— 구현 착수 전 코드를 다시 확인해보니 §3의 "연결이 전혀 없다"는 진단이
**틀렸다**. `conversations.linked_task_id`(ASANA_PARITY §9-5/F21-1)로
Task와 대화가 이미 연결되고, `webui.py`의 `_loadTaskConversation()`이
모든 Task 워크스페이스(`renderCollabWorkspace()`)에서 Deliverable
목록과 **같은 화면에 그 대화를 자동으로 임베드**한다(링크가 아니라
인라인 — Replit의 "체크포인트=코드+대화 맥락"보다 더 강하게 통합돼
있다). 남는 차이는 "이 특정 Deliverable이 대화의 몇 번째 메시지에서
나왔는지"처럼 메시지 단위까지 더 좁히는 것뿐인데, Task 단위로 이미
같은 화면에서 보이므로 가치가 낮다 — 신규 구현 없음으로 종료한다.

### ★★☆ 중간 비용(UI 흐름 재설계, 스키마 변경 적음)

**(D) Task 카드에 "위임"/"배정" 버튼 분리 — 완료(2026-10-12)** — 사용자
결정("위임은 Gate A-G 그대로 타고, audit_log에도 남겨줘")에 따라
`assignments.create_delegation()`(`assignments.py`)을 신설했다.
`create_assignment_request()`("배정")와 달리 F7 결재 체인을 전혀 만들지
않고 **즉시 `status='confirmed'`**로 만든다(`approval_id=NULL`) —
`agent_delegated`라는 별도 audit action으로 `assignment_confirmed`와
구분해 남긴다. Gate A-G는 추가 코드 없이 그대로 적용된다 —
`agent_evaluation.run_and_score()`가 Deliverable 생성 시점(=`execute-
agent`)에 무조건 돌아가는 로직이라, "어떻게 confirmed가 됐는지"와
무관하다. `POST /api/tasks/{id}/delegate`(ADMIN/MANAGER 전용, `배정`
요청과 같은 권한 경계) 신설, "인력 매칭/배정" 결과 표에 Agent
후보에만 "위임" 버튼을 추가했다(사람/팀은 Linear식 "위임" 대상이
아니다). self-approval 방지(ADR 3-A)는 적용하지 않는다 — 승인 단계
자체가 없어 "누가 승인하는지"가 성립하지 않는다(위임은 요청자 본인이
즉시 확정한다는 게 설계 그 자체).

**(E) Dev Crew 단계별 확인 모드** — `SPECIALIST_AGENTS_PLAN.md` §2-0-1이
제안한 "Task 5개 체인"을, 매 단계 완료마다 그 결과를 즉시 화면에 띄우고
사람이 확인해야 다음 단계로 넘어가는 모드(LangGraph Studio의 노드 단위
`interrupt()` 착안, §1-7). 그래프 자체는 §3-1의 `renderDependencyGraphSvg()`
(Task 체인은 이미 `task_dependencies`라 같은 API로 그려짐)를 그대로 쓴다 —
새로 만들 건 "단계 완료 시 다음 Task 결재를 자동 생성"하는 백엔드 흐름뿐이다.

**(G) Dev Crew/Biz Crew 실행 중 다이어그램 갱신(코디네이터 뷰)** — §3-1에서
확인한 대로 `crewDiagramSvg()`가 구조+위임 다이어그램을 **이미** 그린다(2026-
10-06 추가 기능). 지금은 `crew.kickoff()`가 전부 끝난 뒤 `renderCrewResult()`가
1회 호출하는 것뿐이다 — Devin의 "코디네이터가 하위 작업을 모니터링"(§1-4)에
대응시키려면, `task_callback`(이미 Task 완료마다 호출됨, `crew_runtime.py`
419행 `_capture_task_completion`)이 잡은 중간 상태를 폴링 API로 노출하고
`renderCrewResult()`를 완료 후가 아니라 **폴링마다 다시 호출**하도록 트리거
지점만 옮기면 된다 — 같은 SVG 함수, 같은 데이터 구조. (★★☆ 중간 — 원래
"큰 구조 변경"으로 분류했던 걸 §3-1 재확인으로 하향)

### ★☆☆ 큰 구조 변경(장기, 이번 기획 범위 밖으로 명시)

**(F) 반려 시 "그 지점부터 재시도"** — 지금은 반려되면 처음부터 재제출뿐이다
(Replit의 체크포인트 롤백/분기 모델 착안, §1-5). 결재 흐름 자체의 재설계가
필요해 장기 과제로 분리한다.

---

## 5. Claude Artifact 수준의 결과물 뷰어

사용자 요청(2026-10-12, "claude 아티팩트 수준의 UI로 HAIR-Platform 실행결과를
제공해주는 개선을 포함") — 지금 Deliverable 렌더링(`webui.py` `renderContent()`,
228-261행)의 실제 상태를 코드로 확인한 뒤 Claude Artifact와 정밀 대조한다.

| 항목 | 현재 상태(코드 확인) | Artifact 수준과의 차이 |
|---|---|---|
| 뷰어 chrome(틀) | 카드 내부에 `<div>`로 그냥 끼워 넣음 — 전용 헤더·확장/축소·새 창 열기 없음 | Artifact는 제목·액션 버튼이 있는 전용 패널로 분리됨 |
| html 미리보기 | `sandbox="allow-scripts"` iframe으로 **미리보기만**(233행) — 원본 HTML 텍스트를 보거나 복사할 길이 없음 | Artifact는 미리보기 ↔ 소스 코드 토글 제공 |
| code 하이라이팅 | `highlightCode()`(184-193행)가 키워드/문자열/주석/숫자 4종만 정규식으로 구분, **언어 인식 없음**(모든 언어에 같은 키워드 세트 적용 추정) | Artifact는 언어별 정확한 하이라이팅 |
| 복사 | **없음** — `downloadDeliverable()`(269행)로 파일 다운로드만 가능, 클립보드 복사 버튼 없음 | Artifact는 "복사" 버튼이 기본 |
| 버전 히스토리 | `deliverables.revises_id`(스키마엔 있음, `db.py` 236행 — 자기참조 FK)지만, 실제로 이 컬럼에 값을 써넣는 코드가 **어디에도 없다**(`deliverables.py` 55행 docstring — "'수정 후 재제출'은 Phase 2 스코프 밖으로 남긴다"가 그 이유). "데이터는 있는데 화면이 없다"가 아니라 **쓰기 경로부터 없다** — 아래 (H3)에서 정정 | Artifact는 버전 스위처(이전 버전 보기/되돌리기)가 핵심 기능 |

### 제안(비용순)

**(H1) 복사 버튼 — 완료(2026-10-12)** — `renderContent()`가 모든
content_type에 복사 버튼을 포함하는 `.artifact-block`을 반환하도록
바꿨다. 원본 텍스트는 숨겨진 `<textarea>`에 보관(HTML 엔티티 왕복으로
안전하게 원문 복원)하고, 전역 클릭 위임(`document.addEventListener`)
하나로 모든 호출부(대화/Task 워크스페이스/Crew 결과 등 5곳)에 자동
적용했다 — 호출부를 일일이 안 고쳤다.

**(H2) html/code "미리보기 ↔ 소스" 토글 — 완료(2026-10-12)** — 같은
`.artifact-block`에 html/code content_type에만 토글 버튼을 추가했다.
새 데이터 없이 같은 `raw` 문자열을 두 번 렌더링하는 것뿐이다(§2 패턴
적용).

**(H3) 버전 히스토리 뷰어 — 정정: 전제가 틀렸다, 재설계 필요(보류)**
— 착수 전 재확인 결과 `revises_id`는 **한 번도 설정된 적이 없는
컬럼**이다(`deliverables.py`에 그 컬럼에 값을 쓰는 코드가 전무). 즉
"API 1개+UI만 있으면 된다"던 원래 추정이 틀렸다 — 실제로는 ① "수정
후 재제출" 시 `revises_id`를 채우는 **쓰기 경로**(어떤 Task의 기존
deliverable을 "개정"으로 묶을지 판단하는 로직, 결재 반려→재제출 흐름과
얽힘)를 먼저 만들고, ② 그 다음에야 히스토리 조회 API+UI를 얹을 수
있다. ①은 결재 흐름(F7)의 "반려 후 재제출" 정책과 맞물리는 더 큰
설계 결정이라, 이번 라운드에서는 구현하지 않고 **별도 기획으로
분리**한다(§9 열린 질문).

**(H4) 언어 인식 하이라이팅 — 완료(2026-10-12, 축소 범위)** —
`language` 스키마 컬럼을 새로 추가하는 대신(그러려면 Agent가 그 값을
채워야 하는데 지금 아무 생성 경로도 언어를 알려주지 않는다 — 없는
신호를 억지로 만들지 않는다는 원칙), `highlightCode()`의 키워드 세트를
JS/Python 중심에서 SQL·Go·Java/C#·Rust의 흔한 키워드까지 넓히고
대소문자 무관 매칭(`/gi`, SQL 관례)으로 바꿨다. 블록 주석(`/* */`,
C/Go/Rust/SQL 공용)과 SQL의 `--` 줄 주석도 추가했다 — "어떤 언어인지
몰라도 더 많이 알아보는" 더 싼 버전으로 목표를 달성했다.

**(H5) 전용 뷰어 패널(확장/새 창)** — 카드 내부 고정 레이아웃에서, 클릭 시
모달/전체화면으로 확장되는 뷰(html은 지금도 `<iframe>`이라 `target="_blank"`로
새 창에 그대로 띄우는 것도 코드 변경 거의 없이 가능)로 승격. 전체 레이아웃
구조를 건드리므로(★☆☆ 장기) 이번 로드맵에서는 H1-H4를 먼저 하고 그 다음에
검토한다.

---

## 6. 시각적(비주얼) 개선 — 조사 결과 반영

지금까지(§1-§5)는 "화면에 무슨 정보/인터랙션이 있는가"였다. 이 절은
"어떻게 보이는가"(색상·타이포·밀도) 쪽을 코드로 확인하고 제안한다.

**현재 상태(코드 확인)**: `webui.py`의 `<style>` 블록은 색상을 `:root` CSS
변수로 추출하지 않고 **각 규칙에 직접 hex 값을 하드코딩**한다(예: `background:
#14213d` 가 `header`에, 같은 남색이 다른 규칙에도 따로 적혀 있다). 또한
`:root { color-scheme: light; }`로 **의도적으로 라이트 전용**이다 — 주석에
그 이유가 이미 있다("OS가 다크모드일 때 네이티브 폼 컨트롤만 다크로 바뀌어
배경(라이트)과 글자색이 충돌해 안 보이는 버그를 실측으로 발견해서 막은
것"). 이건 되돌릴 결정이 아니라, **제대로 된 다크모드를 만들려면 네이티브
`color-scheme`에 맡기지 않고 전체를 명시적으로 오버라이드해야 한다**는 뜻으로
읽어야 한다.

### 제안

**(I1) 색상 토큰화 — 완료(2026-10-12)** — `primary`/`accent`/`admin`/
`border`/`bg`/`surface`/`text`/`muted` 등 두 번 이상 반복되는 색을
`:root` CSS 변수로 추출했다. 값은 그대로라 이 부분은 화면이 안
바뀐다. 채팅 버블·코드 하이라이팅처럼 한 자리에서만 쓰는 색은 이번
1차 작업 범위 밖으로 남겼다(아래 §8 "다음 액션"에 후속 과제로 남김).

**(I2) 명시적 다크모드(I1 이후)** — `:root[data-theme="dark"]`처럼 데이터
속성으로 전체 색상 변수를 다시 선언하고, 폼 컨트롤에도 명시적 배경/글자색을
줘서 기존에 실측으로 발견한 "네이티브 다크 폼 컨트롤" 버그를 재발 없이
피한다(★☆☆ — I1 의존, 장기 — I1이 끝났으니 착수 조건은 충족됨).

**(I3) 상태 배지 색상 통일 — 완료(2026-10-12)** — `.gate-ok`/`.msg.ok`,
`.gate-warn`/`.msg.warn`, `.msg.error`가 각자 다른 톤을 쓰던 걸
`--status-success/warn/danger-{bg,fg}` 3종으로 통일했다("info"는 지금
쓰는 곳이 없어 만들지 않았다 — 안 쓰는 토큰을 미리 만들지 않는다는
이 프로젝트 관례). `.msg.ok`/`.msg.warn`의 정확한 색조가 `.gate-badge`
쪽 값에 맞춰 아주 살짝 바뀌었다 — 이건 버그가 아니라 "통일"이라는
I3의 목적 그 자체다(두 군데가 이제 실제로 같은 색).

**(I4) 정보 밀도(Linear 벤치마크 반영, §1-3)** — Linear의 "고밀도+키보드
중심" 철학을 그대로 가져오긴 크지만, 가장 싼 조각만 떼어오면: 테이블
`td`/`th`의 `padding`(지금 `0.4rem 0.5rem`)을 줄이는 "컴팩트 모드" 토글을
개인 설정(`localStorage`)으로 추가하는 정도는 CSS 클래스 하나 추가+토글
버튼으로 가능하다(★★☆).

**(I5) 요약 배지 행 — 기존 클래스 재사용** — §4의 (A)(Crew 실행 토큰/비용
배지)를 실제로 그릴 때, 새 CSS를 만들 필요가 없다 — 대시보드에 이미 있는
`.dashboard-grid .stat-row`(flex+좌우배치, 이미 정의됨) 패턴을 Crew 실행
결과 화면에도 그대로 재사용하면 된다(★★★ — §4-A와 함께 즉시 가능, 이 자체는
비용 0).

---

## 7. 로드맵 제안

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | (A) 토큰/비용 배지, (B) 할 일 보드 필터 확장, (H1) 복사 버튼, (H2) 미리보기↔소스 토글, (I5) 요약 배지 행 재사용 | **완료(2026-10-12)** |
| 2 | (C) Deliverable↔대화 연결, (H3) 버전 히스토리 뷰어, (H4) 언어 인식 하이라이팅, (I1) 색상 토큰화, (I3) 상태 배지 색상 통일, (D) 위임/배정 버튼 분리 | (C) 재확인 결과 이미 구현돼 있어 작업 불필요 / (D)(H4)(I1)(I3) **완료(2026-10-12)** / (H3) 전제(`revises_id` 쓰기 경로 없음)가 틀렸던 걸 발견해 재설계 필요 → 보류(§9) |
| 3 | (E) Dev Crew 단계별 확인 모드, (G) Crew 실행 중 다이어그램 갱신(기존 `crewDiagramSvg()` 재사용) — `SPECIALIST_AGENTS_PLAN.md` Phase 2(Dev Crew 실측) 때 같이 검토, (I4) 컴팩트 모드 | 미착수 |
| 4 | (F) 반려 재시도, (H5) 전용 뷰어 패널(확장/새 창), (I2) 명시적 다크모드(I1 완료로 착수 조건은 충족) | 미착수(장기, 승인 전 보류) |

---

## 8. 조사의 한계(정직하게)

7개 제품 중 어느 것도 실제 스크린샷 이미지를 직접 보지 못했다 — 전부
공식 문서·블로그의 텍스트 서술에 근거했다. "확인 안 됨"으로 표시한
항목(Notion 진행률 바 모양, ClickUp AI 생성/사람 작성 구분 배지, Linear
Agent 배지 모양, Devin 하위 세션 리스트 대시보드, Replit 타임라인 시각
형태, CrewAI Traces 실시간 여부, LangGraph 멈춘 노드 강조 방식)은 실제
비주얼을 참고하려면 각 제품의 공식 데모 영상이나 docs 임베드 이미지를
직접 열어봐야 한다 — 이 문서의 제안은 "화면에 무슨 정보/인터랙션이
있는가"까지만 근거가 확실하고, 정확한 비주얼 디자인(색상·간격·타이포)은
HAIR의 기존 디자인 언어(`webui.py`의 `:root` 색상 변수 등)를 그대로
따르는 게 안전하다.

## 9. 다음 액션(열린 질문)

**Phase 1·2 구현 완료(2026-10-12)** — (A)(B)(D)(H1)(H2)(H4)(I1)(I3)(I5)
전부 반영했고(§4·§5·§6에 완료 표시), (C)는 재확인 결과 이미 구현돼
있어 추가 작업이 불필요했다. (D)는 사용자 결정("위임은 Gate A-G 그대로
타고, audit_log에도 남겨줘")에 따라 구현했다 — "위임→배정 승격 전환
지점"은 이번 범위에 넣지 않았다(승격 개념 없이 "위임=즉시 confirmed"
하나로 끝나는 설계를 채택 — 아래 1번 참고). 아래만 남아 있다.

1. **(D) 후속 — "위임→배정 승격" 지점이 정말 필요 없는지** — 지금
   구현은 위임=즉시 `confirmed`라 "나중에 정식 승인으로 올리는" 개념
   자체가 없다(이미 confirmed라 올릴 데가 없다). 실사용 중 "위임을
   취소하고 정식 배정(F7 결재)으로 바꾸고 싶다"는 요구가 나오면,
   그때 "위임 철회" 액션(현재 confirmed 배정을 superseded로 바꾸고
   새 배정 요청을 만드는 흐름 — 기존 `replaces_assignment_id` 메커니즘
   재사용 가능)을 추가하는 걸 검토한다.
2. **(H3) 버전 히스토리 뷰어** — `revises_id`를 실제로 채우는 "수정 후
   재제출" 쓰기 경로부터 설계해야 한다(결재 반려→재제출 정책과
   얽힘) — 별도 기획으로 분리할지, 이 문서에 §10으로 이어서 설계할지
   결정이 필요하다.
3. (E)(G)(I4) — Phase 3, `SPECIALIST_AGENTS_PLAN.md` Phase 2(Dev Crew
   실측)와 함께 진행할지 먼저 할지는 그대로 열려 있다.
4. (F)(H5)(I2) — Phase 4, 장기 보류 그대로.
5. (I1)에서 범위 밖으로 남긴 나머지 색(채팅 버블 파랑/보라, 코드
   하이라이팅 신택스 색)도 토큰화할지 — 지금 쓰는 곳이 적어(각 1-2곳)
   가치가 낮다고 보고 우선순위를 낮춰뒀다.
