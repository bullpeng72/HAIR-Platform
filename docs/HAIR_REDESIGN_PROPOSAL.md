# HAIR-Platform 재기획 제안 — Asana AI Teammates 철저 벤치마크

사용자 요청(2026-10-08, 1차): "Asana AI Teammates나 riido.io를
철저하게 벤치마크하고 HAIR-Platform을 재기획해줘." → (2차, 같은 날)
"업무 방식 많은 경우 챗봇처럼 대화하면서 에이전트를 추가로 초대하는
방식으로 진행하는가 같은데, 이를 확인하고 벤치마킹 대상은 asana를
목표로 해줘."

2차 요청에 따라 **Asana를 주 벤치마크 대상으로 좁히고**, "대화하면서
에이전트를 초대한다"는 가설을 실제로 확인했다 — **확인됨**(§1-1).
Riido 분석(§2)은 1차 요청 때 이미 작성한 내용을 참고용으로 남겨둔다.

`docs/COLLABORATION_UX_IMPROVEMENT_PROPOSAL.md`(2026-10-08, 오픈소스/
상용 AI 에이전트 프레임워크 벤치마크)가 **승인/거버넌스 UX**에
집중했다면, 이 문서는 "경쟁 제품"을 **제품 전체 구조**(정보 구조·
워크플로우 깊이·포지셔닝) 수준에서 뜯어보고 HAIR를 재기획한다.
**기획 문서, 코드 변경 없음.**

## 1. 벤치마크 A — Asana AI Teammates

2025년 9월 발표, 2026년 기준 베타(AI Studio Pro 플랜), FY27 상반기
정식 출시 예정.

### 1-1. "대화하면서 Agent를 초대한다"는 가설 — 확인됨

사용자 가설을 검증하기 위해 Asana 공식 헬프 문서·포럼을 다시
찾아봤다. **정확했다.** 핵심 근거:

- Asana 커뮤니티 포럼 글 제목 그 자체가 결론이다 — **"An AI teammate
  needs to be invited to a project, like a real person"**(AI
  팀메이트는 실제 사람처럼 프로젝트에 초대돼야 한다). "AI Teammates
  need to be invited to projects before they can work within them
  effectively" — 프로젝트(또는 Task) 안에서 언급되는 자원에 접근하려면
  **그 전에 멤버로 초대**돼 있어야 한다.
- 초대된 뒤의 실제 상호작용은 **Task 댓글 안에서의 @멘션**이 중심이다
  — "You can @mention your AI Teammate in any task comment to ask
  questions, request actions, or provide direction." 질문하기,
  작업 지시하기, 방향 전환하기 전부 **댓글 스레드 안에서** 일어난다.
- **진행 중에도 끼어들 수 있다** — "You don't have to wait for a
  Teammate to finish. @mention them at any time to provide feedback
  or redirect their work while it is still in progress." HAIR처럼
  "실행 버튼 누르고 끝날 때까지 기다렸다가 승인/반려"가 아니라,
  작업 **도중에** 자연스럽게 대화로 끼어들어 방향을 바꿀 수 있다.
- 공식 트리거 경로는 셋이다: (1) Task를 직접 배정(형식적 경로, HAIR의
  방식과 비슷), (2) 댓글 속 @멘션(대화형 경로), (3) Teammate가
  collaborator로 걸려 있는 Task에 누군가 그 Teammate를 향한 댓글을
  달면 자동 반응. **(2)·(3)이 사용자가 말한 "챗봇처럼 대화하면서"에
  해당한다.**
- 별도로 **AI 채팅 인터페이스**도 있다 — "What blockers are putting
  this goal at risk?" 같은 자연어 질문에 실행 가능한 계획과 작업
  배정까지 대화로 받는다(사람의 최종 확인 포함).
- 현재 알려진 제약(정직하게 같이 적어둔다): @멘션만으로는 자동으로
  collaborator가 되거나 알림이 가지 않는다 — 확실히 참여시키려면
  별도로 collaborator 추가가 필요하다. 자동화 규칙(rule)으로 AI
  Teammate를 트리거하는 것도 아직 안 된다.

→ **결론**: Asana의 실제 협업 단위는 "formal한 배정 트랜잭션"이
아니라 **"대화 스레드 + 그 안에 초대된 참가자(사람이든 AI든)"**다.
배정은 그 대화 참가자에게 "이 작업을 네가 주도해라"고 못박는 한
가지 방법일 뿐이고, 기본 상호작용은 댓글 대화다. HAIR는 정확히
반대다 — "배정(승인 포함)"이 유일한 공식 참여 경로이고, 댓글은
사람끼리의 사후 논의용일 뿐 Agent를 부르거나 지시하는 수단이 전혀
아니다(`deliverables.create_comment()`는 Agent를 트리거하지 않는다
— 순수 텍스트 저장).

- **정의 방식**: 미리 만들어진 Teammate(30개+) 또는 **코드 없이 역할·
  권한·책임을 정의**해 직접 만든다 — HAIR의 "AI 에이전트 생성"(F4)과
  같은 "등록→재사용" 철학이지만, 역할 정의에 **권한(permissions)**
  개념이 결합돼 있다는 점이 다르다.
- **작업 배정**: 프로젝트에 Teammate를 추가하고 "동료에게 하듯"
  Task를 배정한다 — Asana의 기존 work graph(프로젝트-Task 구조)
  **안에** Agent가 들어오지, HAIR처럼 Agent Pool이 Task 밖에 따로
  있다가 "배정"으로 연결되는 구조가 아니다.
- **체크포인트**: Agent가 맥락을 해석하고 제안·실행하되, **체크포인트
  (checkpoints)**로 사람의 개입 지점을 코스 중간에 둔다 — 이전
  제안서(4-A)에서 지적한 "HAIR는 사후 승인뿐"과 정확히 대비된다.
- **투명성**: "단계별 접근을 보여준다"(show their step-by-step
  approach), 관리자가 "Agent가 어디서 어떻게 작동 중이고 어떤 결과를
  내는지" 본다 — Gate A-G 점수 하나로 요약하는 HAIR보다 **과정 자체를
  노출**하는 쪽.
- **거버넌스**: 데이터 접근권·사용자 권한·운영 파라미터에 더해
  **크레딧(비용) 소비량까지 관리자가 통제** — HAIR의 "비용 발생"
  경고 문구 수준과 격차가 크다.

## 2. 참고 — Riido(뤼이도, 주식회사 스위그) (2차 요청으로 보조 자료화)

1차 요청 때 조사한 내용을 참고용으로 남긴다 — 2차 요청으로 주
벤치마크 대상은 Asana(§1)로 좁혔다. 광고가 "asana" 검색어를
타겟팅하는 것에서 보이듯, **Asana를 쓰는
한국 팀을 직접 겨냥한 국내 제품**이다. HAIR와 비교하기에 가장
현실적인 기준점이다 — 카피부터 그대로 옮긴다: "AI와 함께 일하는 팀의
새로운 기준. 프로젝트를 계획하고, **지금 쓰는 AI 에이전트**에게
업무를 맡기고, 한 팀으로 협업하세요."

### 2-1. 가장 먼저 드러나는 철학 차이 — "에이전트를 키운다" vs "있는 걸 연결한다"

HAIR는 Agent Pool을 직접 만들고(F4) 온보딩시키고 Gate A-G로
평가하며 "조직에 편입되는 새 구성원"으로 키운다. Riido는 정반대다
— **MCP 서버**로 "Cursor, ChatGPT, Claude" 같이 **팀이 이미 쓰고
있는 외부 AI 툴**을 Riido의 작업/맥락에 연결해, 그 도구들이 작업
생성·문서 업데이트를 하게 한다. "우리 AI를 평가한다"가 아니라
"당신의 AI 도구가 우리 작업 맥락을 알게 한다"는 설계다.

→ 이건 범위를 좁히는 게 아니라 **완전히 다른 제품 정체성**이다. HAIR가
지금처럼 "자체 Agent Pool 운영·평가" 노선을 계속 갈지, Riido처럼
"이미 쓰는 외부 AI 도구에 업무 맥락을 흘려보내는" 노선으로 무게중심을
옮길지는 **4절에서 반드시 결정해야 할 가장 큰 질문**이다.

### 2-2. 기능 구조 — HAIR에 없는 "진짜 PM 도구"의 깊이

Riido가 내세우는 기능을 4개 축으로 보면:

| 축 | Riido 기능 | HAIR 현재 |
|---|---|---|
| **AI** | 자동 속성 채우기(담당자·우선순위·라벨을 팀 데이터로 자동완성), AI 기획서 생성(팀 표준 양식), MCP로 외부 AI 툴 연결 | Agent가 결과물 전체를 생성(F4+execute-agent) — "메타데이터 자동완성"이라는 더 가벼운 AI 활용 층위가 아예 없음 |
| **계획** | 우선순위 그래프(중요도×긴급도), 3단계 워크플로우(대기→백로그→실행), 블록 기반 실시간 에디터(작업 안에 문서, 위키 분리 없음), Slack/Discord에서 버튼 하나로 작업 생성 | **우선순위·대기열 개념 자체가 없다** — Brief→Task는 즉시 실행 대상이고 "나중에 할 일"이라는 상태가 없음. 문서는 deliverable 하나로만 존재, "기획 중" 단계의 자유 형식 메모 공간 없음 |
| **실행** | 스프린트, 작업 간 의존관계 시각화, 3계층 구조(프로젝트-목표-작업), Git 워크플로우 자동화(PR↔작업 자동 동기화) | **의존관계·계층 구조 없음** — Brief(1)-Task(N) 2계층뿐, Task 간 참조도 §63 "갭 2"로 완전 차단. 외부 Git 연동 없음(Agent의 "도구"로서의 웹검색/VectorDB만 있을 뿐 사람의 개발 워크플로우와는 무관) |
| **운영** | 멤버 대시보드(업무 부하), 작업 대시보드(지연/병목 조기 발견), AI 개발 맥락 분석(GitHub 변경을 AI가 쉬운 말로 요약), 작업 보관(다음 프로젝트 자산화) | 대시보드는 Gate 평균·비용 추이·대기 승인 건수뿐 — "병목이 어디서 생기는지", "누가 과부하인지"를 보여주는 운영 가시성이 없음 |

### 2-3. 페인 포인트 진단 — HAIR가 직접 맞아떨어지는 지점

Riido가 내세우는 3대 문제의식을 HAIR 상황에 그대로 대입하면 뼈아플
정도로 들어맞는다:

1. **"제품 만드는 시간보다 관리하는 시간이 더 많다"** — HAIR는 Brief
   제출부터 결과물 승인까지 수동 폼 입력이 F1(줄바꿈 스텁)·F2(Task
   수동 분해)·F4(Agent 생성)·F5-F7(배정 요청·승인) 각 단계마다 있다.
   "관리를 위한 관리" 자체가 Riido보다 더 무겁다.
2. **"기억에 의존하는 업무 히스토리"** — HAIR의 Task 간 완전
   격리(§63 "갭 2")가 정확히 이 문제다. 같은 브리프의 다른 Task
   담당자에게 맥락을 전달할 방법이 플랫폼에 없어 결국 사람이 기억하거나
   사내 채널로 따로 전달해야 한다. Riido는 "블록 기반 에디터로 작업
   안에 문서 작성"으로 이걸 정면으로 푼다.
3. **"도구가 장벽이 되는 순간"(세팅 일주일, 온보딩 한 달)** — HAIR는
   지금 `webui.py`의 `TABS` 배열 기준 **admin에게 20개 탭**이 보이고
   (`webui.py:575`), Brief/Task/Human Pool/Agent Pool/Skill Pool/
   Tool Pool/Team/Model Registry/Gate A-G/F1-F14라는 개념을 전부
   익혀야 쓸 수 있다. 사용자가 이번 요청에서 직접 지적한 "실사용에
   너무 많이 부족해"의 실체가 바로 이 **온보딩 장벽**일 가능성이
   높다.

## 3. 세 제품 종합 비교

| 축 | HAIR-Platform | Asana AI Teammates | Riido |
|---|---|---|---|
| Agent 소유 모델 | 자체 Agent Pool 생성·평가(Gate A-G) | 자체 정의하되 Asana 플랫폼 내 역할/권한 결합 | **외부 AI 도구(Cursor/ChatGPT/Claude)를 MCP로 연결** |
| 작업 구조 | Brief(1)-Task(N) 2계층, 우선순위/의존관계 없음 | 기존 Asana work graph(프로젝트-섹션-Task) 그대로 활용 | **3계층(프로젝트-목표-작업)**+우선순위 그래프+의존관계 시각화 |
| 작업 간 맥락 | **완전 격리**(§63 갭 2) | 프로젝트 컨텍스트 공유가 기본 | **작업 안에 문서 내장**(위키 분리 없음) |
| 사람 개입 시점 | 사후 승인뿐 | 중간 체크포인트 | 자동완성은 즉시 반영, 큰 결정은 사람이 |
| 과정 가시성 | Gate 점수(숫자)만, VectorDB 1건만 근거 노출 | "단계별 접근" 노출 | AI 개발 맥락을 "쉬운 말로 요약"해 팀에 공유 |
| 비용 거버넌스 | 경고 문구만 | **크레딧 소비량까지 관리자 통제** | 가격 플랜 단위(세부 불명) |
| 외부 도구 연동 | 없음(Agent의 "도구"만 있고 사람 워크플로우 도구 연동 없음) | Asana 생태계 내 | **Slack/Discord/Git/MCP 깊이 연동** |
| 온보딩 난이도 | 높음(14+ 탭, 다수 개념) | 기존 Asana 사용자는 낮음 | "애자일 공부 없이 즉시 시작" 지향 |
| 운영 가시성 | Gate 평균/비용/대기 승인 건수 | Agent별 활동 로그(관리자) | 멤버 부하/병목 대시보드 |

## 4. 재기획 제안

### 4-0. (신규, 최우선) 배정 중심 → 대화 중심으로 — §1-1 직접 반영

Asana 벤치마크에서 확인된 가장 큰 구조적 차이를 HAIR에 그대로
옮긴다. 지금의 "배정(승인 포함)"을 없애자는 게 아니라, **그 위에
가벼운 대화형 레이어를 추가**하는 것이다.

- **Task에 Agent를 "초대"하는 가벼운 동작 추가**: 지금은 Agent가
  Task에 관여하려면 반드시 `POST /api/tasks/{id}/assignments`(배정
  요청) → F7 결재 체인 승인이라는 **무거운** 절차를 거쳐야 한다.
  Asana처럼 "초대"(참가자 후보로 추가, 아직 작업 책임은 안 생김)와
  "배정"(실제 작업 책임+결재)을 분리한다 — 초대는 승인 없이 가볍게,
  실제로 그 Agent가 뭔가를 만들어 승인까지 가려면 지금처럼 배정
  절차를 거친다.
- **댓글에서 Agent를 부르면 실행되게 한다**: `deliverable_comments`
  테이블과 `create_comment()`(`deliverables.py:385`)를 확장해, 댓글에
  특정 Agent를 지칭하면(`@Agent이름` 패턴) 그 댓글 내용을 프롬프트
  삼아 `execute-agent`와 같은 경로를 **대화 안에서** 트리거한다 —
  별도 "결과물 작성" 폼까지 안 내려가도 되는 지름길.
- **실행 중 재지시 허용**: 지금은 `execute-agent`가 한 번 트리거되면
  끝날 때까지 개입 지점이 없다(`docs/COLLABORATION_UX_IMPROVEMENT_
  PROPOSAL.md` 4-A와 같은 뿌리의 문제, 이번엔 Asana가 실제로 "진행
  중에도 @멘션으로 피드백 가능"이라는 구체적 선례로 확인됨). 당장
  전체를 LangGraph식 `interrupt()`로 바꾸기는 크지만, 최소한 "재실행
  시 이전 댓글들을 컨텍스트에 포함"하는 정도는 지금 구조에서도 작은
  변경으로 가능하다.
- **왜 최우선인가**: 사용자가 두 차례에 걸쳐 지적한 "실사용에 너무
  부족하다"는 체감과 가장 직접적으로 연결된다 — 지금 HAIR는 Agent와
  "대화"할 방법이 구조적으로 없고, 매번 "결과물 작성 폼 채우기 →
  실행 버튼 → 기다리기 → 승인/반려"라는 무거운 사이클만 있다. 실제
  업무에서 사람들이 AI와 일하는 가장 흔한 방식(가볍게 말 걸고,
  진행 중에 방향을 바꾸고, 필요하면 다른 Agent를 더 부르는 것)과
  가장 거리가 먼 지점이 여기다.

### 4-1. 가장 먼저 결정할 것 — HAIR의 정체성 축

**(A) "에이전트를 키운다" 노선 유지**: Agent Pool·Gate A-G·온보딩·
평가라는 HAIR만의 차별점(이 세션 내내 공들여 만든 것)을 그대로
살린다. 대신 Riido가 지적한 약점(작업 구조 깊이·맥락 보존·온보딩
장벽)을 보완한다. **이 문서는 이 노선을 기본값으로 제안한다** —
지금까지의 투자(Gate A-G, Crew 그룹 실행, content_type, 팀 협업)를
버리지 않는 길이기 때문이다.

**(B) "외부 도구 연결" 노선으로 피벗**: Riido처럼 MCP로 사용자가
이미 쓰는 Claude/ChatGPT/Cursor를 HAIR 작업 맥락에 연결하는 쪽으로
무게중심을 옮긴다. Agent Pool/Gate A-G의 상당 부분이 쓸모없어지는
큰 전환이라 권장하지 않지만, "사람들이 이미 쓰는 AI를 또 다른
전용 Agent로 대체하길 원치 않는다"는 피드백이 실사용에서 나온다면
재검토할 옵션으로 남겨둔다.

**(C) 혼합**: Agent Pool은 유지하되, execute-agent/execute-team-crew
와 별개로 "외부 AI 도구 연동용 MCP 엔드포인트"를 추가해 두 모델을
공존시킨다 — 장기 로드맵 후보.

### 4-2. 작업 구조 자체를 보강한다(Riido 벤치마크 직접 반영)

- **우선순위·대기열 상태 추가**: Task에 "대기/백로그/실행" 같은
  상태 필드를 추가해, Brief 제출 즉시 실행 대상이 되는 게 아니라
  "나중에 할 일"로 쌓아둘 수 있게 한다.
- **Task 간 참조 링크**(이전 제안서 4-F와 동일 — 여기서도 똑같이
  최우선으로 재확인됨): §63 "갭 2"를 근본적으로 푸는 가장 작은
  변경이다.
- **작업 안에 자유 형식 메모 공간**: 지금은 `deliverable`이 유일한
  콘텐츠 단위다(구조화된 제출물). Riido의 "블록 에디터"처럼, 제출
  전 자유롭게 쓰고 고치는 "작업 메모"를 Task에 붙여 맥락이 Task
  자체에 쌓이게 한다.
- **의존관계(최소 버전)**: "이 Task는 저 Task가 끝나야 시작"
  정도의 단순 선후관계만이라도 추가하면, Task 분해(F2) 단계에서
  이미 쓸모가 생긴다.

### 4-3. 온보딩 장벽을 낮춘다

- **"빠른 시작" 경로 설계**: 지금은 Brief 제출 전에 Human Pool/Agent
  Pool/Skill Pool을 전부 미리 세팅해야 제대로 동작한다(이번 세션에서
  시나리오 3의 "고객 응대" 스킬을 먼저 등록 안 해서 효과가 없었던
  것처럼, **사전 세팅을 빼먹으면 조용히 실패한다**는 게 가장 큰
  온보딩 함정이다). Riido의 "복잡한 설정이나 학습 불필요" 철학을
  빌려, 최소 한 번은 "샘플 Brief→Task→Agent→실행→승인"을 사전 세팅
  없이 바로 체험할 수 있는 **데모 경로**를 만든다.
- **탭 수 줄이기/재구성**: admin 기준 20개 탭을 "오늘 할 일"(실행 중심) /
  "설정"(Pool·Team·Model 등록, 자주 안 바뀜) 둘로 더 과감하게
  나눠, 신규 사용자가 매일 보는 화면을 좁힌다.

### 4-4. 운영 가시성을 추가한다

- **멤버/Agent 부하 대시보드**: 누가(또는 어떤 Agent가) 지금 몇 건을
  진행 중인지, 과부하인지 — 현재 대시보드엔 전혀 없는 축.
  `agent_execution_jobs`/`assignments` 테이블에 이미 있는 데이터로
  바로 만들 수 있다.
- **병목 조기 발견**: 마감 임박+미배정 Task, 반려 반복 Task 같은
  "주의가 필요한 항목"을 대시보드 상단에 모은다(이미 있는 인박스
  개념의 확장).

### 4-5. 외부 워크플로우 도구와의 연동(장기)

Riido의 Slack/Discord/Git 연동, Asana의 생태계 통합은 HAIR가 당장
따라가긴 큰 투자다. 다만 **가장 싼 첫걸음**으로 "Slack에 결재 대기
알림 보내기" 정도는 인박스 개념을 확장하는 수준으로 가능하다 —
새 기능이 아니라 기존 인박스 데이터를 다른 채널로 미러링하는 것.

## 5. 로드맵 제안(우선순위)

1. **즉시 — 최우선(2차 요청으로 격상)**: 대화 중심 레이어(4-0) —
   Task에 Agent "초대"(가벼운 동작, 승인 불필요) + 댓글 @멘션으로
   Agent 트리거. 기존 `assignments`/배정-승인 체계를 안 건드리고
   **그 위에 얹는** 추가 기능이라 리스크가 낮다.
2. **즉시(싸고 효과 큼)**: Task 간 참조 링크(4-2), Task 메모
   공간(4-2), 멤버/Agent 부하 대시보드(4-4) — 전부 기존 스키마에
   컬럼/테이블 하나씩 추가하는 수준.
3. **단기**: 실행 중 재지시(4-0, 댓글 히스토리를 재실행 컨텍스트에
   포함), 우선순위/대기열 상태(4-2), "빠른 시작" 데모 경로(4-3).
4. **중기**: 탭 재구성(4-3), 병목 조기 발견 대시보드(4-4),
   `docs/COLLABORATION_UX_IMPROVEMENT_PROPOSAL.md`의 ★★★ 항목들
   (CrewAI 위임 로그 노출, Gate 통과 시 자동 승인)과 병행.
5. **장기**: 외부 워크플로우 연동(4-5), 정체성 축 재검토(4-1의 (B)/(C)).

## 6. 다음 액션 제안(열린 질문)

1. **4-0(대화 중심 레이어)의 범위**: "초대"만 먼저 추가할지, 댓글
   @멘션 트리거까지 한 번에 할지 — 후자가 체감 효과는 크지만 구현
   범위도 더 크다.
2. **4-1의 정체성 축**: (A)(B)(C) 중 어느 쪽으로 확정할지 — 이
   결정이 나머지 모든 우선순위에 영향을 준다.
3. **작업 구조 보강(4-2)**의 범위: 우선순위/대기열/의존관계/메모
   공간 4개 중 전부 할지, 일부만 먼저 할지.
4. **온보딩 재설계(4-3)**: 탭 재구성처럼 기존 사용자 경험을 크게
   바꾸는 변경을 지금 할지, 기능이 더 쌓인 뒤로 미룰지.

이 중 진행할 항목을 알려주면 각각 별도로 구현한다.

---

Sources(2026-10-08 리서치):
- [Asana announces AI Teammates](https://www.businesswire.com/news/home/20250925695672/en)
- [A look under the hood: Asana's new 30+ prebuilt AI Teammates](https://asana.com/fr/inside-asana/ai-teammates-research)
- [Asana AI Teammates: What They Are, How to Build Them](https://cirface.com/blog/asana-ai-teammates)
- [Asana's Next-Gen AI Teammates Are Built for Teams](https://theaieconomy.substack.com/p/asana-ai-teammates-cross-functional-teams)
- [Asana help: triggering AI Teammates](https://help.asana.com/s/article/triggering-ai-teammates)
- [Asana help: AI Teammates](https://help.asana.com/s/article/ai-teammates)
- [Forum: "An AI teammate needs to be invited to a project, like a real person"](https://forum.asana.com/t/an-ai-teammate-needs-to-be-invited-to-a-project-like-a-real-person/1111581)
- [Forum: Adding teammates to work, AI Teammates as collaborators](https://forum.asana.com/t/adding-teammates-to-work-ai-teammates-as-collaborators-not-allowed/1114854)
- [Asana unveils AI Teammates (chat interface, multi-player state)](https://www.businesswire.com/news/home/20240605959051/en)
- https://www.riido.io/ (직접 접속, 2026-10-08, 참고용)
