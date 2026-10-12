# 화면 UX 벤치마크 — Notion AI·ClickUp Brain·Linear·Devin·Replit Agent·CrewAI·LangGraph Studio

사용자 요청(2026-10-12, "외부 검색 등으로 경쟁제품들의 UI/UX 및 기능을 분석해
개선점을 도출해줘" → 방향 확인 질문에 "새 제품 + 실제 화면(UI) 중심으로 확장"
선택 → 후속 요청 "claude 아티팩트 수준의 UI로 HAIR-Platform 실행결과를
제공해주는 개선을 포함하고, 위 조사결과를 반영한 현재 UI/UX의 시각적 개선
방안을 포함해줘") — **기획 문서, 코드 변경 없음.** §5(Deliverable을 Claude
Artifact 수준으로 보여주는 뷰어 개선)·§6(비주얼 디자인 개선)은 후속 요청으로
추가됐다.

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
Dev Crew의 `hierarchical` 실행(QA/검증자가 매니저)에 "지금 누가 뭘 하는지"
보여주는 진행 패널이 없다는 것과 대조된다.

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
단계별 결재"를 어떤 화면으로 구현할지에 대한 구체적 답이다.

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
| 선후관계 시각화 | `webui.py` `_dependencyGraphDepths()`(1378행) — **이미 SVG DAG 그래프**(Riido 벤치마크 반영, 의존 깊이별 레이어 배치) | 이미 상당히 구현돼 있음 — 추가 투자 우선순위 낮음 |
| Crew 실행 결과 토큰/비용 | `crew_runtime.py` `run_crew()`가 `result.token_usage`를 **계산은 하지만**(584-591행 반환 dict에 없음) `audit_log`에만 참고 기록으로 남고 API 응답엔 안 들어감 | **진짜 갭** — 계산된 데이터가 화면에 못 올라감(§4-A) |
| Deliverable↔대화 연결 | 스키마 확인(`db.py`) — `deliverables`는 `conversations`/`conversation_messages`와 **아무 FK도 없다**. `agent_execution_jobs.source_comment_id`는 `deliverable_comments`(Task 결재 트랙)만 가리키고, Conversational Workspace(`conversations`)는 완전히 별도 트랙 | **진짜 갭** — Replit의 "체크포인트=코드+대화 맥락" 패턴과 대응(§4-C) |

---

## 4. 구체적 개선 제안(비용순)

### ★★★ 즉시 가능(기존 데이터 재배치만)

**(A) Crew 실행 결과에 토큰/비용/실행시간 요약 배지** — `crew_runtime.py`
`run_crew()`의 반환 dict(584-591행)에 `"token_usage": result.token_usage.
model_dump()` 한 줄만 추가하면 API 응답에 실린다. `webui.py` `renderCrew()`의
"Gate A-G 채점" 섹션(3792-3798행) 옆에 배지 한 줄만 추가하면 된다 — CrewAI
자체 Traces 탭과 같은 자리에 같은 정보를 놓는 것(§1-6).

**(B) 할 일 보드 범위 확장** — `renderBoard()`가 호출하는 `/api/my-work`
대신(또는 추가로) 팀/전체 조직 범위를 고르는 필터를 넣는다. 이미 있는
`/api/tasks` 목록 API에 `status`/`assignee` 필터만 추가하면 되는 수준(Linear의
"같은 화면에서 필터로 위임 대상까지 보기" 착안, §1-3).

**(C) Deliverable 상세에 "관련 대화 보기" 자리 표시** — 지금은 연결이 전혀
없어(§3) 할 일이 "자리 표시"부터다: 새 Deliverable 작성 시 현재 열려있는
Conversational Workspace의 `conversation_id`를 선택적으로 같이 저장할 수 있게
하는 작은 스키마 확장(Replit의 "체크포인트=코드+대화 맥락" 착안, §1-5). 이건
§6-1(SPECIALIST_AGENTS_PLAN.md, 코드분석 Tool)처럼 "필요 확인된 신규 작업"으로
분리한다 — 지금 이 문서는 기획까지만.

### ★★☆ 중간 비용(UI 흐름 재설계, 스키마 변경 적음)

**(D) Task 카드에 "위임"/"배정" 버튼 분리** — 지금 `assignments` 테이블은
F7 결재가 항상 붙는 하나의 경로다. "위임"(결재 없이 가볍게 Agent를 Task에
연결)과 "배정"(결재 필요, 기존 그대로)을 Task 카드에 나란히 두는 안
(Linear 위임/배정, Devin Ask/Agent 모드 착안, §1-3·§1-4) — `HAIR_REDESIGN_
PROPOSAL.md` §4-0이 이미 제안한 것의 더 구체적인 UI 구현.

**(E) Dev Crew 단계별 확인 모드** — `SPECIALIST_AGENTS_PLAN.md` §2-0-1이
제안한 "Task 5개 체인"을, 매 단계 완료마다 그 결과를 즉시 화면에 띄우고
사람이 확인해야 다음 단계로 넘어가는 모드로 구현(LangGraph Studio의
노드 단위 `interrupt()` 착안, §1-7).

### ★☆☆ 큰 구조 변경(장기, 이번 기획 범위 밖으로 명시)

**(F) 반려 시 "그 지점부터 재시도"** — 지금은 반려되면 처음부터 재제출뿐이다
(Replit의 체크포인트 롤백/분기 모델 착안, §1-5). 결재 흐름 자체의 재설계가
필요해 장기 과제로 분리한다.

**(G) Dev Crew 하위 작업 "코디네이터 패널"** — `hierarchical` 실행 중 QA/
검증자(매니저)가 요구사항분석가/설계자에게 실시간으로 무엇을 위임하고
있는지 보여주는 신규 화면(Devin의 "Devin이 Devin들을 관리" 대시보드 착안,
§1-4). 지금 `run_crew()`는 완료 후에만 위임 내역을 보여준다(3804-3807행) —
실행 중 스트리밍이 필요해 구조 변경이 크다.

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
| 버전 히스토리 | `deliverables.revises_id`(스키마에 **이미 존재**, `db.py` 236행 — 자기참조 FK로 수정 이력을 체인으로 저장)인데, `renderContent()` 호출부 주석이 "현재 최종본으로만 간주 — revises_id 기반 버전 체계는 스코프 밖"(1595행)이라고 명시 — **데이터는 있는데 화면이 없다** | Artifact는 버전 스위처(이전 버전 보기/되돌리기)가 핵심 기능 |

### 제안(비용순)

**(H1) 복사 버튼** — `navigator.clipboard.writeText(content)` 한 줄이면 된다.
`renderContent()`가 돌려주는 HTML 블록 옆에 버튼 하나 추가하는 수준(★★★ 즉시).

**(H2) html/code "미리보기 ↔ 소스" 토글** — 지금 `renderContent()`는
content_type 하나당 한 가지 뷰만 그린다. html은 iframe만, code는 하이라이팅된
텍스트만 보여준다. 토글 버튼으로 같은 content를 두 가지로 전환하는 건 **새
데이터 없이 같은 `raw` 문자열을 두 번 렌더링**하는 것뿐이다(★★★ 즉시 — §2의
"같은 데이터를 다른 틀로" 패턴을 여기 그대로 적용).

**(H3) 버전 히스토리 뷰어** — `revises_id` 체인을 따라가는 조회 API 하나
(`GET /api/deliverables/{id}/history` 같은 것, 새 컬럼 불필요)와, 그 리스트를
드롭다운/화살표로 전환하는 UI만 있으면 된다. Claude Artifact의 버전 스위처와
정확히 같은 개념이 **이미 스키마에 있다** — 꺼내 쓰기만 하면 되는, 이 문서
전체에서 가장 "가성비 좋은" 항목(★★☆ 중간 — API 1개+UI 전환 로직).

**(H4) 언어 인식 하이라이팅** — `content_type='code'`에 언어 메타데이터가
없다(스키마에 `language` 컬럼 없음). Agent가 코드를 생성할 때 언어를 함께
표시하도록 요청하거나(prompt 수준, 코드 변경 없음) `models.py`에 선택적
`language` 필드를 추가해 `highlightCode()`가 언어별 키워드 세트를 고르게
하는 확장(★★☆ 중간 — 스키마 변경 1개).

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

**(I1) 색상 토큰화(리팩터링, 시각적 변화 없음)** — 산재한 hex 값을 `:root`
CSS 변수(`--color-primary`, `--color-bg` 등)로 추출한다. 이 자체는 화면이
안 바뀌지만, 이후 다크모드/브랜드 색 변경이 "변수 값 하나"로 끝나게 만드는
전제 작업이다(★★☆ — 이 리팩터링 없이는 아래 I2/I3 둘 다 비용이 커진다).

**(I2) 명시적 다크모드(I1 이후)** — `:root[data-theme="dark"]`처럼 데이터
속성으로 전체 색상 변수를 다시 선언하고, 폼 컨트롤에도 명시적 배경/글자색을
줘서 기존에 실측으로 발견한 "네이티브 다크 폼 컨트롤" 버그를 재발 없이
피한다(★☆☆ — I1 의존, 장기).

**(I3) 상태 배지 색상 통일** — 지금 `.gate-ok`/`.gate-warn`/`.msg.error`/
`.msg.ok`/`.msg.warn`/`.badge-adminonly`/`.badge-reused`가 전부 각자 다른
초록/주황/빨강 계열 hex를 따로 쓴다. 공통 상태색 4종(success/warn/danger/
info)으로 통일하면 새 배지를 추가할 때마다 색을 새로 정하지 않아도 된다
(★★☆ — I1과 함께 하면 비용이 거의 0에 가까움).

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

| Phase | 내용 | 비용 |
|---|---|---|
| 1 | (A) 토큰/비용 배지, (B) 할 일 보드 필터 확장, **(H1) 복사 버튼, (H2) 미리보기↔소스 토글, (I5) 요약 배지 행 재사용** | 코드 변경 적음(기존 API 확장/기존 클래스 재사용) |
| 2 | (C) Deliverable↔대화 연결 스키마, (D) 위임/배정 버튼 분리, **(H3) 버전 히스토리 뷰어, (H4) 언어 인식 하이라이팅, (I1) 색상 토큰화, (I3) 상태 배지 색상 통일** | 중간(스키마 변경 1-2개 또는 CSS 리팩터링) |
| 3 | (E) Dev Crew 단계별 확인 모드 — `SPECIALIST_AGENTS_PLAN.md` Phase 2(Dev Crew 실측) 때 같이 검토, **(I4) 컴팩트 모드** | 중간(Task 체인 설계 의존 또는 I1 의존) |
| 4 | (F) 반려 재시도, (G) 코디네이터 패널, **(H5) 전용 뷰어 패널(확장/새 창), (I2) 명시적 다크모드** | 큼(장기, 승인 전 보류) |

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

1. §4의 (A)-(C)(즉시 가능)부터 바로 착수해도 되는가, 아니면 우선순위
   조정이 필요한가?
2. (D)-(E)는 `SPECIALIST_AGENTS_PLAN.md` Phase 2(Dev Crew 실제 Task 실측)와
   함께 진행하는 게 맞는가, 별도로 먼저 하는 게 맞는가?
3. (F)-(G)는 이번 로드맵에서 보류로 명시해도 되는가?
4. §5(H1-H5, Artifact 수준 뷰어)와 §6(I1-I5, 비주얼 토큰화)은 이번
   로드맵(§7)에 바로 합류시켰다 — 이대로 진행해도 되는가, 별도 문서로
   분리하는 게 나은가?
