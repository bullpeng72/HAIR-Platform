# HAIR-Platform

**AI를 도구로 "쓰는" 게 아니라, 조직의 구성원으로 "들이는" 협업 플랫폼.**
사람과 AI Agent를 하나의 조직도 위에 함께 배치하고, 업무 배분·결재·
실행·성과 리뷰까지 똑같은 절차로 처리한다 — 일단 조직에 들어온 뒤로는
Agent라고 다른 대우를 받지 않는다.

## 왜 HAIR인가

- 🧑‍🤝‍🤖 **조직에 들어온 뒤로는, 사람과 Agent가 같은 절차를 탄다** —
  업무 배치, 배분 요청, 결재 확정, 팀 구성이 사람이든 Agent든 같은
  화면·같은 흐름으로 처리된다. (Agent를 만들고 온보딩하는 화면, 사람을
  등록하는 화면은 각자 성격이 달라 당연히 따로 있다 — 통일된 건 그
  뒤의 배치·배분·결재다.)
- ✅ **승인 없이 조직에 반영되는 변화는 없다** — Agent가 무엇을 만들어도
  사람의 결재를 거쳐야 "확정된 결과"가 된다(HITL). 반복적인 단계는
  기준을 통과하면 그 자체가 승인이라 사람 개입이 최소화되지만, 완전
  무인 운영은 아니다.
- 📊 **"잘했다"는 말 대신 7개 기준으로 채점한다** — 모든 Agent 결과물은
  Gate A-G(목표달성·행동무결성·신뢰성·성능계약·보안경계·다중에이전트
  협업·관측성)로 자동 측정되어 기록에 남는다.
- 🔁 **복잡한 업무는 사람이 한 번만 승인하면 그 뒤로 알아서 돈다** —
  소프트웨어 개발·사업기획·제안서·실적보고 같은 다단계 업무를 파이프
  라인으로 등록해두면, 계획 확정 뒤로는 기준 통과까지 자동 재시도하고
  반복 실패해야만 사람에게 다시 올라온다.
- 💸 **비용 없이 시작해서 그대로 키울 수 있다** — 로컬 Ollama로 무료로
  돌려보다가, 코드 한 줄 안 바꾸고 Anthropic/OpenAI 클라우드로 바꿔
  쓸 수 있다. Agent별로 "심층추론/정밀·신속/표준" 중 어디에 돈을 쓸지
  직접 고를 수 있다.
- 💬 **가볍게 시작해도, 정식으로 키워도 같은 데이터를 쓴다** — 대화창에서
  `@Agent이름`으로 툭 던져 바로 답을 받거나, 업무 요청→결재→파이프라인
  으로 정식 운영하거나 — 같은 Agent/팀 등록을 공유해 아무 때나 오갈 수
  있다.

**지금 상태**: 위 기능 전부 실제로 동작하는 코드로 구현돼 있고(스텁이나
목업이 아니다), 테스트 1,800건 이상이 통과하는 상태로 유지된다. 개념·아키텍처
전체 설명은 `docs/OVERVIEW.md`, 설계 결정의 배경·과거 버그 수정 이력은
`git log`(커밋 메시지에 날짜·사용자 요청 원문이 그대로 남아 있다)를
참고.

## 무엇을 할 수 있나

### 1. 사람과 AI Agent를 함께 운용
- 조직도 위에 사람·Agent를 같이 배치하고 업무에 배분한다. 매칭은
  자동(완전일치 우선, 그 외엔 가용성·적합도·비용으로 점수화)이고,
  확정은 조직도 상사 체인을 따르는 다단계 결재를 거친다(자기 승인은
  막힘) — 다만 **본인을 배정하는 "셀프배정"은 결재 없이 즉시
  확정**된다.
- 한 업무에 여러 사람·여러 Agent·여러 팀이 동시에(confirmed 상태로)
  배정될 수 있다 — 교체하려면 어떤 배정을 대체하는지 명시해야 한다.
- **팀**: 사람 N명 + Agent M명을 역할과 함께 묶어 등록해두고 여러
  업무에 재사용한다. 협업 워크스페이스의 "팀 Agent 실행"으로 팀의
  Agent 멤버 전원을 실제 CrewAI Crew로 한 번에 실행하면, 결과가
  Agent별 결과물+결재 체인으로 그 업무에 그대로 연결된다.
- **협업 워크스페이스**: 결과물(deliverable) 타임라인 + 댓글. 사람이
  작성한 결과물도 "검토 요청"을 누르면 Agent 결과물과 같은 결재를
  거친다. 결과물은 일반 텍스트/마크다운/HTML/코드 중 형식을 지정할
  수 있고, 화면에서 그 형식대로 렌더링된다(마크다운 서식·코드 구문
  강조·HTML 미리보기 전부 포함, 복사/다운로드/소스 보기 지원).
  다시 낸 결과물은 버전 이력으로 묶여 이전 버전과 줄 단위로 비교할 수
  있고, Word(.docx)·Excel(.xlsx, 표마다 시트)·PowerPoint(.pptx, 제목마다 슬라이드)로
  내보낼 수 있다.
  **부분 수정 요청**으로 고른 섹션·구간만 AI가 다시 쓴 수정안을 받아, 원문과
  비교한 뒤 수락(반영)하거나 거절한다 — 나머지는 원문 그대로 유지된다.

### 2. 대화창에서 바로 일 시키기
- 로그인하면 바로 대화창부터 시작한다. 아무것도 지정하지 않으면 기본
  AI가 답하고, `@에이전트명`/`@팀명`으로 특정 Agent나 팀을 멘션해
  실행시킬 수 있다.
- 대화에서 쓸 스킬을 직접 고를 수 있는데, **"개발업무" 스킬은
  일반 사용자에게 불필요하므로 기본적으로 목록에서 숨겨진다** —
  "개발업무 스킬도 보기"를 켜야 보이고, 평소엔 일반 업무용 스킬
  (85개 중 다수)·도구(24개 중 일반업무용)만 노출된다. (Agent/Task에 미리 등록해둔
  스킬 태그나 CrewAI 실행 자체는 이 필터와 무관하게 전부 동작한다.)
- 특정 업무(Task)에 종속된 대화는 그 업무로 자동 흡수된다. 독립
  대화는 결재·Gate 채점이 필요해지면 "업무 요청으로 전환"으로 대화
  내용을 담은 업무 요청 초안을 만들어(AI 구조화는 선택) 다듬어 제출한다
  — 출처 대화가 업무 요청에 남고, 대화에도 전환 기록이 남는다.

### 3. Agent 만들고 실제로 실행하기
- Anthropic/OpenAI/Ollama 중 하나로 실제 LLM Agent를 돌릴 수 있다
  (설정하지 않으면 비용 없는 무료 스텁으로 동작). provider/model은
  "모델 레지스트리"에 등록해두면 전 화면에서 드롭다운으로 공유된다.
- 실행마다 위 **Gate A-G** 7개 기준이 자동으로 채점돼 기록에 남는다. 통과 기준(Gate별)·응답 시간 SLA·추론 표지는
  admin이 "평가 기준" 화면에서 바꾼다(다음 평가부터, `.aoo/targets.json` 내보내기·가져오기). 결과물 카드의 **"평가 리포트"**로 평가 도구
  (Agent-Evaluator)가 만든 Gate A-G 종합 HTML 리포트(지표·원인 진단·개선 제안)를 새 탭에서 본다(`HAIR_EVAL_HTML_REPORT=0`으로 끔).
- **Crew 빌더**: 여러 Agent를 즉석으로 묶어 실제 CrewAI로 같이
  실행한다(sequential/hierarchical), 위임 관계를 다이어그램으로
  보여주고, 실행 이력에서 이전 결과와 차이(diff)를 바로 비교할 수
  있다. Agent가 스스로 초안을 원 요구사항과 대조해 검토·수정하는
  "자가검토" 옵션도 있다. 성공한 조합은 팀이나 파이프라인 단계로
  저장(승격)할 수 있다.
- 과거 산출물 검색(내부지식검색)·조직 관계 질의(조직배정관계조회)·실시간 웹
  검색(Tavily)이 Agent가 필요하면 스스로 판단해 호출하는 도구로
  이미 연결돼 있고, MCP로 외부 도구를 추가 연결할 수도 있다(MCP 도구는 Crew·파이프라인뿐 아니라
  대화창 기본 AI·@Agent 멘션·워크스페이스 실행에서도 실제로 호출된다).
- **결과물 자동 검증**: 퍼센트 수치가 요청·조회·도구 결과 숫자로 실제
  재현되는지 계산으로 확인해 틀린 문장만 고치고(남으면 "⚠ 수치 확인
  필요" 표시), 개발 결과물의 Python·JavaScript 코드+테스트는 네트워크를 막은
  Docker에서 실제로 실행하고 HTML 결과물은 실제 브라우저(Chromium 샌드박스)로 열어 오류를 보며, 한국어 결과물에 섞인 한자·외국 문자는
  자동 교정한다(문장 교정으로 안 고쳐지면 긴 문장 속 짧은 한자 조각만 문맥과 함께 다시 교정).
  모델이 지어낸 한국어 낱말(사전에 없는 낱말)은 참고용 "낱말확인" 배지로 알린다(`korean` extra).
  인용으로 옮긴 따옴표 문장이 근거 자료에 실제로 있는지,
  근거의 낱말에서 한 글자가 빠지거나 바뀐 용어("1분기"→"1분")가 없는지도
  대조해 배지로 표시한다.
- **코드 저장소 읽기**: admin이 "연동 설정"에 Git 저장소를 등록하면
  개발 Agent가 커밋된 파일 목록·내용·코드 검색·최근 커밋을 직접 읽고
  "경로:줄"을 근거로 답한다(읽기 전용, 비밀 파일 제외).
  승인된 결과물은 admin이 "저장소에 커밋"으로 main 브랜치에 로컬 커밋할 수
  있다 — 경로가 적힌 코드 블록만, 점검(결재 완료·main 브랜치·미커밋 변경
  없음·경로·비밀 파일·문법·테스트 기록)을 모두 통과할 때만, push 없이.

### 4. 복잡한 업무는 파이프라인으로
- 기본 제공 파이프라인 **8종**: 소프트웨어 개발, 사업/기획 업무
  처리, 제안서 작성, 실적 보고, 재무 분석 보고, 영업 제안 메일, 계약서
  1차 검토, 채용 공고·면접 설계. 각 단계는 Gate 기준으로 자동
  채점·재시도되고, 반복 실패해야만 사람에게 에스컬레이션된다
  (계획 승인은 처음 한 번만 하면 되고, 그 뒤로는 사람 개입이
  최소화된다).
- 화면에서 직접 단계를 구성하는 빌더도 제공되므로, 개발 외 업무에도
  같은 엔진을 그대로 재사용할 수 있다.
- 단계 방식: 단독·순차 릴레이·병행 위임에 더해 **계획 분담**(리드가
  팀원별 하위 작업을 계획 → 팀원이 나눠 실행 → 리드가 하나로 병합). 사업/기획 파이프라인의
  "자료 조사·분석" 단계가 이 방식이고, 리드가 일을 주지 않은 팀원은 "배정확인" 배지로 보인다.
- 로컬(Ollama) 모델에서는 Agent의 작업 전 계획을 끄고 도구 반복 상한을 8회로 둔다(실측: 조사 단계가 1시간 50분 →
  24분). `HAIR_OLLAMA_AGENT_PLANNING=1`이면 계획을 켠다.
- 검토 단계는 Gate 점수뿐 아니라 **검토자의 판정**도 따른다 — `## 판정`이
  "보완 요청"이면 작성 단계로 되돌려 검토 의견대로 다시 쓰게 하고, 재시도를
  다 써도 안 되면 사람에게 올린다.
- **정기 실행**: 매일/평일/매주 정한 시각에 업무 요청 → 할 일 →
  파이프라인 실행을 자동으로 만든다(연동 설정, `hair serve`에서 동작).
- 업무를 등록할 때 LLM에게 하위 업무 분해(역할·필요 스킬·선행 업무
  의존관계)를 제안받아 한 번에 배치로 생성할 수도 있다.

### 5. 지식 쌓아서 Agent가 찾아보게 하기
- "지식 베이스" 탭(admin/manager)에서 로컬 파일(.md/.pdf/.html/.csv/
  .xlsx/.docx/.pptx)을 올리거나 텍스트를 직접 입력하면 영구적으로
  색인된다 — 색인되면
  "내부지식검색" 스킬을 가진 모든 Agent가 대화창·CrewAI 실행 양쪽에서
  찾아낸다.
- 대화창·협업 워크스페이스에서 올리는 참고자료는 **그 1회만 쓰는
  휘발성 첨부**다 — 영구 보관하려면 지식 베이스에 올려야 한다.
- 같은 탭에서 색인된 조각 조회/삭제+통계, 조직 관계 조회+통계까지
  관리한다.

### 6. 운영 관리
- **홈** 탭: **AI에게 일 맡기기**(할 일을 한 문장으로 적으면 빠른 질문·Agent·파이프라인·
  정기 실행 중 맞는 방법을 이유와 함께 추천하고 버튼 하나로 시작), 처리할 일(결재 대기·
  실행 실패·품질 점검 미달), admin 관리 점검 — 로그인하면 처음 열리는 화면. 메뉴는 업무 흐름대로
  **일하기 · 검토·결재 · 현황▾** 한 줄과, admin·manager에게만 **설정▾** 한 줄(리소스·지식·데이터·모델·
  운영 관리 — 누르면 작은 메뉴, 🔒 = admin 전용). 이용 가이드는 헤더 ❓ 도움말(지금 화면의 절)·🔍 메뉴 찾기,
  연동 설정은 헤더 ⚙ 내 설정, 내 계정 이름을 누르면 비밀번호 바꾸기.
- **업무 요청 "진행 보기"**: 요청 → 할 일 → 배정 → 결과물 → 결재 단계와 할 일마다 "다음에 할 일" 버튼을 한 화면에.
- **시스템 상태**(admin): 서버·DB·백업·디스크·작업 큐·모델 연결·외부 연결을 한 화면에, 감시 도구용 `GET /health`.
- **프로세스 성과**(현황 → admin·manager): 파이프라인별 완료까지 걸린 시간(AI 작업·사람 대기·그 밖)·한 번에 완료 비율·
  재작업·자동 처리율·건당 비용·기준 시간 대비, 단계별 병목, 결재를 기다리는 실행, 주별 추이 — 실행 기록에서 계산한다.
- **배운 점·변경 기록**: 결재 반려 사유와 되풀이되는 품질 기준 미달이 단계별 개선 제안으로 모이고, 운영자가 적용하면
  다음 실행부터 그 단계 지시문에 들어간다. 단계 설정 변경·배운 점 적용은 기록되어 전·후 30일 지표로 비교하고 되돌릴 수 있다.
- **결재 도우미·위험 기반 결재**: 결재마다 위험도·추천·점검 요약·이전 판과의 차이·확인할 점을 한 화면에. 추천과 사람 결정이
  쌓여 검증되면(낮음 20건·95% 승인) 파이프라인별로 낮은 위험 자동 승인을 켤 수 있다(5건마다 1건 표본 검토, 조건이 깨지면 자동 중단).
- **업무 설명으로 파이프라인 초안**: 하는 일을 글로 적으면 AI가 단계·담당 Agent·검토 단계·사람 승인 위치를 제안(등록된 역할·도구만,
  마지막은 사람 승인) — 사람이 고쳐서 만든다. AI를 못 쓰면 기본 초안.
- **외부 입력 트리거**: 메일 도착·웹훅(`POST /api/inbound/<번호>`, 트리거별 토큰)·다가오는 일정으로 파이프라인 시작 — 들어온 내용은
  외부 데이터로 표시되고 그 실행은 쓰기 도구가 막힌다. 중복·과거 것·시간당 상한 처리.
- **결재 후 실행 동작**(admin opt-in): 단계 결과를 승인하면서 "승인하고 실행"을 고를 때만 메일(시작한 메일에 회신 포함)·
  Slack/Teams·JSON 웹훅으로 실제로 보낸다(미리 보기, 한 번만, 활동 기록, 자동 승인 제외).
- **처리 용량**: 줄별 대기·실행 시간, 바쁜 정도, 지금 대기 중인 작업, 파이프라인별 "지금 맡기면" 걸릴 시간.
- **용량·부재 기반 매칭**: 사람마다 동시 용량(기본 3)·부재 기간, 매칭 가용성 = 남은 용량 비율(용량 초과 표시·뒤로)·부재면 불가,
  Agent는 로컬 모델 대기 반영·자동 적합도(목표 달성 평균 + 사람 승인율). 배정 요청은 막지 않고 경고, 구성원은 "내 업무"에서 자기 부재 등록.
- **자원 운용**: 할 일 기한·예상 시간·인원 채움, 사람별 앞으로 4주 활용도(주당 시간 − 부재), 기한 지남·임박, 인원 부족,
  역할별 수요(열린 일·최근 4주 시작·정기 실행 예정)와 공급, 여유 인력. 기한 지남은 대시보드 "주의 필요"에도.
- **인계·결재 위임·비용**: 퇴사·부재 전 인계 점검(남은 배정·대신 맡을 후보·대기 결재 넘기기·보고 대상·자동화), 기간 결재 위임,
  오래 머문 결재 자동 에스컬레이션(admin, 기본 끔), 업무 요청별 사람 인건비(단가×예상 시간)+AI 실측 비용·예산 경고, 팀별 비용.
- **좋은 결과 예시·비용 대비 가치**: 사람이 승인한 깨끗한 결과(외부 데이터 실행 제외)를 단계별 예시로 골라 다음 실행에 형식 참고로 붙이고,
  사람 1시간 비용을 넣으면 파이프라인별 건당 가치·AI 비용 대비 배수·추정 순절감을 본다.
- **작성 중 미리보기·진행률**: 대화·워크스페이스 AI 응답은 생성되는 글이
  그대로 미리 보이고(생각 중 → 답변 작성 중), 실행 중인 파이프라인은 몇
  번째 단계인지·지금 어느 Agent가 무엇을 하는지·경과 시간을 보여 준다.
- 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고 — 측정하지 않은 항목은 실패로 치지 않음),
  매니저 대시보드(팀 Gate 평균·대기 승인 건수·비용 추이), 검색·
  필터·활동 기록(감사로그).
- **실제 AI 사용량**: 업무별·Agent별 호출 수·토큰·금액(모델별 단가),
  파이프라인 평균 소요·사람 결재 수까지 비용 탭에서 본다.
- **연동 설정**: 개인 API 키(외부 시스템이 내 권한으로 API 호출),
  결재 대기·파이프라인 완료/실패를 Slack·Teams 웹훅과 메일(SMTP, 환경변수 `HAIR_SMTP_*`)로 알림,
  **이벤트 트리거**("파이프라인 A가 끝나면/결과물이 승인되면 → 파이프라인 B 시작", 앞 결과물을 넘겨
  파이프라인을 이어 붙임).
- **기본 구성 점검**(admin): 운영 DB가 현재 버전의 기본 Agent·스킬·
  도구·파이프라인 구성과 어디가 다른지 보여 주고 빠진 것을 반영한다.
- **보조 작업 모델**(admin): 언어·수치 교정 같은 짧은 보조 호출만 다른
  모델로 보낸다(본 모델과 동시에 돌 수 있는 모델일 때만 대기가 줄어든다).
- `hair reset-data`로 운영 데이터(사용자·업무·대화·실행 이력·팀 등)
  만 공장 초기화할 수 있다 — Agent/Tool/Skill Pool·파이프라인
  템플릿·조직 정책·"초기화 시 보호" 팀은 그대로 둔다(배포 전 깨끗한 상태로 준비할 때
  사용, 지우는 범위는 아래 "빠른 시작" 참고).

### 7. 권한(RBAC)
4개 역할(`admin`/`manager`/`member`/`viewer`) — 조회·생성은
admin+manager 공용이 많고, 삭제·사용자 생성·Agent 온보딩 승인처럼
조직 구조에 직접 영향을 주는 동작은 admin 전용.

## 처음 써보기 — 실전 시나리오

설치·설정을 시작하기 전에, 실제로 쓰면 어떤 모습인지부터 감을 잡고
싶다면 아래 4가지 중 자기 상황과 가까운 걸 골라 읽어보면 된다(용어는
전부 위 "무엇을 할 수 있나"에서 설명한 것들이다).

- **그냥 질문 하나만 던지고 싶다** — 메뉴 "대화"에서
  바로 입력(무엇부터 할지 모르겠으면 로그인 직후 뜨는 홈의 "AI에게 일 맡기기"에 적고 추천을 받는다). 아무도 멘션하지 않으면 기본 AI가 답한다. 결재도 없고
  가장 빠르다 — 단, 멘션 없이 보낼 때마다 실제 LLM이 호출돼
  **비용이 발생**한다는 점만 알아두면 된다.
- **특정 전문가(Agent)에게 시키고 싶다** — 대화에서 `@역할명`으로
  멘션(예: `@코드 검증가 이 함수 리뷰해줘`). 아직 그 Agent가 없으면
  먼저 만들어야 한다("AI 에이전트 생성" → 역할 지정 → 온보딩 승인,
  admin/manager만 가능).
- **여러 역할이 협업해야 하는 복잡한 업무다** — 업무 요청 제출 →
  할 일로 나누기 → 기본 제공 "소프트웨어 개발 파이프라인"(또는
  사업/기획·제안서·실적 보고 파이프라인) 실행. 사람은 계획 단계에서
  한 번만 승인하면, 그 뒤로는 Gate 기준을 통과할 때까지 자동으로
  반복되고 — 반복해도 안 되는 단계만 다시 사람에게 올라온다.
- **팀 전체(사람+Agent) 작업 흐름을 조직처럼 운영하고 싶다** — "팀
  관리"에서 역할별로 사람·Agent를 묶어 등록해두고, 업무마다 그 팀을
  배정. 결재·성과 리뷰·비용 집계가 팀 단위로도 쌓인다.

어느 경로로 시작해도 같은 Agent/팀 등록을 공유하므로, 가벼운 대화로
시작했다가 나중에 정식 업무로 넘기거나 그 반대로 가는 것도 자유롭다.
화면 안에는 이보다 훨씬 자세한 "이용 가이드"(로그인 후 "이용 가이드"
탭)가 있다 — 메뉴 이름·버튼 문구까지 그대로 따라 할 수 있게 단계별로
적어뒀다.

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]" -c constraints.txt
# `-c constraints.txt` — 전체 테스트를 통과한 버전 조합 그대로 설치한다(CI도 같은 방식). 빼면 범위 안의 최신
# 버전이 깔려 동작이 미묘하게 다를 수 있다(`hair check`가 핵심 패키지 버전 차이를 경고한다).
# `[dev]`만 설치하면 CrewAI 연동 테스트 일부가 실행 시점에 `ModuleNotFoundError`로 깨진다 — `all`까지 설치.
pytest tests/ -q        # 골든셋 1,800건 이상 통과(오프라인 — 실제 LLM·외부 네트워크 호출 없음, 전부 mock)
# 화면 자동 테스트(선택, 2026-11-26) — 실제 서버 + 브라우저. 없으면 tests/e2e는 건너뛴다
pip install -e ".[e2e]" && python -m playwright install chromium
pytest tests/e2e -q      # 모든 화면 JS 오류 0건·메뉴 찾기·대화 화면 배치·필수 도구·쓰기 표시·역할별 메뉴 등
ruff check src tests    # CI와 같은 정적 검사(규칙은 pyproject.toml `[tool.ruff.lint] select`)
```

테스트는 실행 환경과 무관하게 같은 결과가 나오도록 격리돼 있다(`tests/conftest.py`): `.env`를 읽지 않고,
셸의 API 키·provider·SMTP 설정을 지우고 고정 가짜 키를 넣으며, 외부 네트워크와 로컬 Ollama(11434) 접속을
막는다. 외부 호출이 필요한 새 테스트는 mock을 써야 한다.

의존성을 올릴 때는: 새 버전 설치 → 전체 테스트 통과 확인 → `python scripts/update_constraints.py`로
`constraints.txt`와 `src/hair_platform/verified_versions.py`를 다시 만들어 함께 커밋한다.

### 설치 옵션(extras) 한눈에 보기

PyPI에 `hair-platform`으로 배포된다 — `pip install "hair-platform[...]"`로
설치하고, 소스에서 개발할 때는 GitHub 클론 후 `pip install -e ".[...]"`로
같은 extra를 쓴다(PyPI의 최신 배포 버전이 이 저장소 버전보다 늦을 수 있다 —
`pip index versions hair-platform`으로 확인). 필요한 조합만 골라 쉼표로 묶어
한 번에 설치 가능(예: `pip install "hair-platform[crewai,documents]"`) —
아래 "빠른 시작" 각 절이 extra별 상세 설정(환경변수·API 키 등)을 안내한다.

| extra | 설치 명령 | 필요할 때 | 추가 설치되는 패키지 |
|-------|-----------|-----------|----------------------|
| *(기본)* | `pip install -e .` | 웹 UI·API 전체 + Ollama(로컬, API 키 불필요) 단발 실행·대화 — CrewAI 협업(팀·Crew 빌더·파이프라인)·클라우드 LLM·문서 업로드는 해당 extra 필요. Agent 생성 기본 방식은 crewai가 없으면 simple_llm으로 대신한다 | pydantic·fastapi·uvicorn·httpx·tiktoken 등 핵심 의존성만 |
| `llm` | `pip install -e ".[llm]"` | 실제 Anthropic/OpenAI API로 Agent를 실행할 때(Ollama는 API 키 없이 기본 의존성만으로 동작) | anthropic, openai |
| `crewai` | `pip install -e ".[crewai]"` | 여러 Agent를 실제 CrewAI Crew로 협업시킬 때("Crew 빌더"/파이프라인) | crewai, litellm |
| `mcp` | `pip install -e ".[mcp]"` | Agent에 외부 MCP 도구 서버를 연결할 때 | crewai-tools[mcp], mcp(`<2.0` 고정 — 아래 참고) |
| `documents` | `pip install -e ".[documents]"` | 대화창/지식 베이스에서 `.pdf`/`.html`/`.xlsx`/`.docx`/`.pptx` 파일을 업로드할 때(`.md`/`.txt`/`.csv`는 추가 설치 불필요) | pypdf, beautifulsoup4, openpyxl, python-docx, python-pptx |
| `korean` | `pip install -e ".[korean]"` | 결과물에서 사전에 없는 한국어 낱말(모델이 지어낸 낱말)을 찾는 참고용 "낱말확인" 배지. 분석기가 메모리 약 360MB·디스크 약 100MB를 쓴다(`HAIR_LEXICON_CHECK=0`으로 끔) | kiwipiepy |
| `dbconnect` | `pip install -e ".[dbconnect]"` | "사내 DB 조회"로 PostgreSQL·MySQL을 연결할 때(SQLite는 추가 설치 없음) | psycopg[binary], pymysql |
| `vectordb` | `pip install -e ".[vectordb]"` | 지식 베이스 검색을 벡터 DB(ChromaDB, 서버 없이 `<db>-vectors/` 폴더)로 한다 — 청크 1만 개에서 검색 1회 약 1.7초 → 약 40ms(실측, 결과 동일). crewai를 설치하면 보통 함께 깔린다. 없으면 예전 방식(SQLite 전체 비교)으로 그대로 동작 | chromadb |
| `graph` | `pip install -e ".[graph]"` | LangGraph 실행엔진(팀 관리에서 "그래프 엔진"을 켤 때 — 체크포인트·사람 승인 중단/재개). 없이 켜면 설치 안내가 뜬다 | langgraph, langgraph-checkpoint-sqlite |
| `dev` | `pip install -e ".[dev]"` | 테스트/린트를 돌릴 때 | pytest, ruff, black(선택 — CI에서는 쓰지 않음: 기존 코드가 black 형식이 아님) |
| `e2e` | `pip install -e ".[e2e]"` + `python -m playwright install chromium` | 화면 자동 테스트(`tests/e2e`)를 돌릴 때(개발용). 서버에 설치하면 HTML 결과물을 실제 브라우저로 열어 보는 "화면실행" 점검(`browser_execution`)도 켜진다(없으면 그 점검만 미평가) | playwright |
| `all` | `pip install -e ".[dev,all]"` | 전체 골든셋을 오프라인으로 깨끗하게 통과시킬 때(= `llm`+`crewai`+`mcp`+`documents`+`graph`+`korean`+`vectordb` 전부) | 위 extras 전부(dev 제외) |

> `mcp`는 `mcp>=1.10.1,<2.0`으로 상한을 고정해둔다 — `crewai-tools`가
> 쓰는 `mcpadapt==0.1.19`가 `mcp 2.x`의 API 이름 변경을 아직 못
> 따라잡아서다(따라잡으면 상한을 올릴 것). `pip install -e ".[mcp]"`만
> 따로 설치하면 이 상한이 자동으로 지켜지지만, `mcp`를 다른 이유로
> 이미 더 최신 버전을 설치해둔 환경에 나중에 이 extra를 추가하면
> pip가 `mcp`를 1.x로 다운그레이드한다는 점에 유의.

실행 명령은 전부 `hair <subcommand>` 하나로 모여 있다 — 각 서브커맨드의
옵션/예시는 `hair <subcommand> --help`로 확인(`src/hair_platform/cli.py`):

```bash
hair --help
```

### 환경변수(.env) 설정/점검 — `hair init` / `hair check`

처음 설치했을 때 가장 먼저 하면 되는 단계다. 둘 다 전부 선택(optional) 항목만
다룬다 — 아무것도 입력하지 않아도 서버는 뜨고, 필요한 기능을 쓸 때만 해당 값이
있어야 한다(정의는 `src/hair_platform/env_setup.py`, `hair init`/`hair check`가
공유하는 단일 목록).

```bash
hair init                 # LLM API 키·세션 서명 키·메일(SMTP) 알림·작업 프로세스·시간대(+원하면 고급 옵션)를 대화형으로 물어 .env에 저장
hair init --yes           # 묻지 않고 바로 저장(세션 서명 키만 없으면 자동 생성, 나머지는 기존 값 그대로)
hair init --path .env.prod   # 다른 경로에 저장(기본: ./.env)
hair check                # .env + 셸 환경변수를 합쳐 보여주고 provider/키 불일치·SMTP 누락·잘못된 값을 찾음
hair check --db-path hair.db   # + 연동 설정에 등록된 비밀 환경변수(사내 DB·메일·캘린더)와 작업 프로세스 상태(기본 hair.db)
```

`hair init`은 이미 있는 `.env`를 읽어 현재 값을 보여주고 Enter로 그대로 둘 수
있다(덮어쓰지 않음, 이 모듈이 모르는 줄은 그대로 보존). `hair check`는 셸에
이미 export된 값이 `.env`보다 우선한다는 것까지 반영해서 보여준다
(python-dotenv 기본 동작과 동일, `override=False`).

`hair init`/`hair check`가 다루는 변수 전체:

| 그룹 | 변수 | 설명 |
|------|------|------|
| LLM Provider API 키 | `ANTHROPIC_API_KEY` | Anthropic(Claude) API 키 |
| | `OPENAI_API_KEY` | OpenAI API 키 |
| 로컬 LLM(Ollama) | `OLLAMA_BASE_URL` | 로컬 Ollama 서버 주소(비우면 기본값 `http://localhost:11434`) |
| 외부 웹 검색(Tavily) | `TAVILY_API_KEY` | "실시간웹검색" 등 Tavily 기반 Skill/Tool용 키 |
| 코드 저장소 PR(GitHub) | `HAIR_GITHUB_TOKEN` | 결과물을 브랜치로 push한 뒤 PR을 자동으로 열 때 쓰는 토큰(Contents·Pull requests 쓰기). 없으면 PR 만들기 링크만 |
| 플랫폼 공통 기본 LLM(선택적 폴백) | `HAIR_PLATFORM_LLM_PROVIDER` | 웹 UI "LLM 설정"으로 아직 안 지정했을 때 쓰는 기본 provider(anthropic/openai/ollama) |
| | `HAIR_PLATFORM_LLM_MODEL` | 위 provider와 함께 지정하는 기본 모델명(provider 없이 지정하면 무시됨) |
| 서버/보안 | `HAIR_PLATFORM_SECRET_KEY` | 세션 서명 키 — 127.0.0.1/localhost 바깥으로 띄우면 필수(위 "사내망에서 여러 명이 함께 쓰기" 참고) |
| 메일(SMTP) 알림 | `HAIR_SMTP_HOST`·`HAIR_SMTP_PORT`·`HAIR_SMTP_USER`·`HAIR_SMTP_PASSWORD`·`HAIR_SMTP_FROM`·`HAIR_SMTP_SSL` | 결재 대기·파이프라인 완료/실패 메일 알림(연동 설정 화면). `HOST`가 있어야 나머지가 의미가 있고, 465 포트면 `SSL=1`(`hair init`은 원할 때만 묻는다) |
| 서버 운영(작업 프로세스·시간대) | `HAIR_WORKER_MODE` | `embedded`(기본) 또는 `external` — 아래 "작업 프로세스로 실행 유지" 참고 |
| | `HAIR_TIMEZONE` | 캘린더 일정을 보여 줄 시간대(IANA 이름, 예: `Asia/Seoul`, 비우면 서버 PC 시간대) |
| | `HAIR_TLS_CA_FILE` | 사내 인증서(사내 CA·자체 서명) 파일 경로. 메일 수신함(IMAP)·메일 알림(SMTP)은 서버 인증서를 검증한다(2026-11-26부터 — 예전엔 검증하지 않았다). 사내 메일 서버 연결이 "인증서를 확인할 수 없습니다"로 실패할 때만 지정 |
| 고급 옵션(기본값 권장) | 아래 표의 `HAIR_STAGE_RUNTIME`~`HAIR_ALLOW_LOCAL_CODE_RUN` 14개 | `hair init`은 "고급 옵션을 설정하시겠습니까?"에 y일 때만 묻는다. `hair check`는 값이 정해진 범위(선택지·0/1·숫자) 밖이면 경고 |

#### 작업 프로세스로 실행 유지 — `HAIR_WORKER_MODE`

기본(`embedded`)은 웹 서버가 AI 실행(대화 응답·Agent/Crew·파이프라인·지식 등록)을 직접 돌려, `hair serve`를
재시작하면 실행 중이던 것이 "중단됨"이 된다(화면에서 "이어서 실행"). `external`로 두면 웹 서버는 작업을 기록만
하고 별도 작업 프로세스가 실행해 **웹 서버를 재시작해도 실행이 계속된다**.

```bash
# .env에 한 줄(또는 hair init의 "서버 운영"에서 external 입력)
HAIR_WORKER_MODE=external
# 같은 폴더(같은 .env)에서 터미널 두 개 — 같은 --db-path
hair serve --db-path hair.db
hair worker --db-path hair.db --concurrency 4
hair check --db-path hair.db      # 작업 프로세스가 떠 있는지까지 점검
```

- 작업 프로세스는 **하나만** 띄운다(살아 있는 다른 작업 프로세스가 있으면 시작을 거부).
- 작업 프로세스를 재시작하면 실행 중이던 것은 "중단됨"으로 정리되고 화면에서 이어서 실행할 수 있다. 꺼져 있는 동안
  들어온 작업은 정리하지 않고 다음 기동 때 실행한다.
- 작업 프로세스가 꺼져 있으면 홈 "관리 점검"(admin)과 `hair check --db-path`에 경고가 뜬다.

#### 연동 비밀값 — 이름은 연동 설정 화면에서 정한다

사내 DB·메일 수신함·캘린더 연결은 비밀번호·iCal 주소를 **DB에 저장하지 않고 환경변수 이름만** 저장한다. 이름은
등록하는 admin이 정하고(대문자·숫자·`_`), 값은 서버 PC의 `.env`(또는 export)에 넣는다 — `hair init`은 모르는 줄도
그대로 보존한다. `hair check --db-path`가 등록된 이름마다 값이 있는지 보여 준다(값 자체는 표시하지 않음).

| 연결(연동 설정 화면) | 저장되는 것 | `.env` 예시 |
|------|------|------|
| 사내 DB(PostgreSQL·MySQL) | 비밀번호 환경변수 이름 | `HAIR_DB_SALES_PASSWORD=...` |
| 메일 수신함(IMAP) | 앱 비밀번호 환경변수 이름 | `HAIR_MAIL_SUPPORT_PASSWORD=...` |
| 캘린더(iCal 주소) | iCal 주소 환경변수 이름(https만) | `HAIR_CAL_SALES_URL=https://calendar.google.com/calendar/ical/.../basic.ics` |
| 코드 저장소 PR | 고정 이름 | `HAIR_GITHUB_TOKEN=...`(위 표) |

웹 서버와 작업 프로세스(`hair worker`)는 같은 `.env`를 읽어야 한다 — 같은 폴더에서 실행한다.

고급 옵션(기본값을 그대로 둬도 된다 — `hair init`은 원할 때만 묻고, `hair check`가 값을 점검한다):

| 변수 | 기본값 | 효과 |
|------|--------|------|
| `HAIR_EVAL_HTML_REPORT` | `1`(켬) | `0`이면 평가마다 만드는 Gate A-G HTML 리포트(결과물 카드 "평가 리포트")를 만들지 않음 |
| `HAIR_OLLAMA_AGENT_PLANNING` | `0`(끔) | `1`이면 로컬(Ollama) 모델에서도 Agent 작업 전 계획(추론 모드)을 켬(기본은 속도를 위해 꺼짐) |
| `HAIR_OLLAMA_MAX_OUTPUT_TOKENS` | 모델 레지스트리 설정값 | Ollama 모델의 최대 출력 토큰을 재정의 |
| `HAIR_OLLAMA_MAX_CONCURRENT` | `1` | Ollama 모델에 한 번에 보내는 호출 수의 기본값. Ollama는 한 모델에 요청을 하나씩 처리하므로 1이면 처리량은 그대로이고, 넘는 호출은 플랫폼에서 기다리며 **대화 응답이 파이프라인·Crew보다 먼저** 다음 자리를 받는다(실측: 긴 생성 3개가 줄 서 있을 때 대화 응답 107~121초 → 39~42초). `OLLAMA_NUM_PARALLEL`을 올린 Ollama면 그 값, `0`이면 상한 없음(예전 동작). 모델별 값은 모델 레지스트리 "동시 실행 상한" |
| `HAIR_LANGUAGE_QA` | `1`(켬) | `0`이면 한국어 결과물 자동 언어 교정(한자·외국 문자 교정)을 끔 |
| `HAIR_LEXICON_CHECK` | `1`(켬) | `0`이면 사전에 없는 한국어 낱말 탐지("낱말확인" 배지)를 끔(분석기가 메모리 약 360MB를 씀, `korean` extra 필요) |
| `HAIR_NUMERIC_CHECK` | `1`(켬) | `0`이면 결과물 퍼센트 수치가 근거 숫자로 재현되는지 검사("수치근거" 배지)를 끔 |
| `HAIR_BACKUP_DIR` | (비어 있음 = DB 파일 옆 `backups/`) | 백업 저장 폴더 — `hair backup`·자동 백업·`hair reset-data` 전 백업이 여기에 `.tar.gz`로 쌓인다. 디스크 고장에 대비해 다른 디스크·NAS를 권장 |
| `HAIR_BACKUP_KEEP` | `7` | `hair serve`가 하루 한 번 뜨는 자동 백업을 최근 몇 개 남길지. `0`이면 자동 백업 끔(수동 `hair backup`은 그대로) |
| `HAIR_ALLOW_LOCAL_CODE_RUN` | `0`(끔) | `1`이면 Docker 샌드박스 대신 로컬에서 코드 테스트(Python·JavaScript)를 실행하고, 브라우저 샌드박스를 켤 수 없는 환경에서도 화면실행 점검을 돌림(Docker가 없는 개발 환경용 — 격리가 없어지므로 운영에는 권장하지 않음) |
| `HAIR_STAGE_RUNTIME` | `direct` | 파이프라인 단일·순차 릴레이 단계의 Agent 실행 방식. `direct`는 대화와 같은 직접 호출(실측: 품질·판정 동일, 영업 메일 −18% 시간·−29% 호출), `crewai`는 예전 CrewAI 방식. 병행 위임 단계·팀 멘션·Crew 빌더는 늘 CrewAI |
| `HAIR_STAGE_STALL_SECONDS` | `2700`(45분) | 파이프라인 단계가 이 시간 동안 진행 표시·작성 중 미리보기 없이 멈추면 정리하고(모든 스레드 스택을 활동 기록에 남김) "이어서 실행"할 수 있게 함. `0`이면 끔 |
| `HAIR_VECTOR_BACKEND` | `chroma` | 지식 베이스 검색 엔진. `sqlite`면 예전 방식(SQLite의 임베딩 전체와 비교 — 정확하지만 청크가 많으면 느림). chromadb가 없거나 메모리 DB면 자동으로 `sqlite` |
| `HAIR_VECTOR_DIR` | `<db 파일>-vectors` | 벡터 색인(ChromaDB) 저장 폴더. 원본은 SQLite에 있어 지워도 `hair reindex-vectors`(또는 다음 검색)가 비용 없이 다시 만듦 |

자동 설정·내부용(직접 설정할 일이 거의 없다 — `hair init`/`hair check`가 다루지 않음):

| 변수 | 기본값 | 효과 |
|------|--------|------|
| `HAIR_PLATFORM_DB_PATH` | `:memory:` | SQLite DB 경로. 보통 `hair serve --db-path`가 기동 시 이 값을 자동으로 맞춰 주므로 직접 설정할 일은 거의 없음 |
| `HAIR_AUTOMATION` | `hair serve`가 `1`로 설정 | 정기 실행·이벤트 트리거·외부 알림 자동화 루프. `hair serve`로 띄운 서버에서만 돈다(테스트·외부 uvicorn 실행에서는 꺼짐). `0`으로 export하고 `hair serve`를 띄우면 그 서버에서는 자동화가 돌지 않음 |
| `HAIR_STAGE_WATCHDOG_POLL_SECONDS` | `30` | 파이프라인 단계 멈춤 감시(`HAIR_STAGE_STALL_SECONDS`)가 확인하는 간격(초). 시험용 |

콜드스타트(신규 배포 시 최초 ADMIN 계정 생성 — 유일한 진입 경로,
`users` 테이블이 완전히 비어 있을 때만 동작):

```bash
hair bootstrap-admin -u admin -p '...'       # --db-path 생략 시 기본 hair.db
```

운영 중 쌓인 데이터만 초기화할 때(예: 배포 전 깨끗한 상태로 준비):

| 구분 | 내용 |
|------|------|
| 지운다 | 사용자·API 키, 사람 인력, 업무 요청·할 일·배정·결재, 대화, 결과물(댓글·버전·부분 수정 제안·저장소 커밋 기록 포함)·Gate 평가·평가 리포트, 파이프라인·Crew·Agent 실행 이력, 지식 베이스(벡터 색인 포함), 정기 실행·이벤트 트리거·알림 발송 기록, 모델 레지스트리·성능 테스트 기록·임베딩 설정, 코드 저장소 등록·사내 DB·메일 수신함·캘린더 연결, 작업 큐 기록, Agent에 직접 붙인 도구 연결, 활동 기록, AI 사용량 기록, 팀("초기화 시 보호" 팀 제외) |
| 남긴다 | Agent(39종)·스킬·도구·파이프라인 템플릿, 조직 정책(사번 형식·결과물 언어·알림 웹훅·메일 수신자), 평가 기준, 플랫폼 공통 기본 LLM·보조 작업 모델·지식 검색 설정, "초기화 시 보호" 팀 |
| 되돌린다 | Agent의 모델 지정·비용·성과점수·마지막 결과물은 비운다. 상태는 기본 Agent만 '활성'으로 되돌리고, 관리자가 만든 Agent는 그대로 둔다(온보딩 대기 Agent가 승인 없이 활성이 되지 않음) |


```bash
hair reset-data                              # 확인 프롬프트 뜸 — 지우기 전에 백업을 먼저 뜬다(--no-backup으로 생략)
hair reset-data --yes                        # 확인 없이 바로 실행
# 보통 바로 이어서: hair bootstrap-admin -u admin -p '...'
```

#### 백업·복원

백업 하나는 `.tar.gz` 파일 하나다 — DB(SQLite 온라인 백업이라 서버가 켜져 있어도 일관된 사본, 업로드 문서·평가 리포트 포함)와
지식 베이스 벡터 폴더. 저장 위치는 `HAIR_BACKUP_DIR`(비우면 DB 파일 옆 `backups/`). `hair serve`가 켜져 있으면 하루에 한 번
자동으로 뜨고 최근 `HAIR_BACKUP_KEEP`개(기본 7)만 남긴다. 시스템 상태 화면(설정 → 운영 관리)에 마지막 백업 시각이 보이고,
이틀 넘게 새 백업이 없으면 경고한다. DB 파일을 그냥 복사하면 WAL 모드라 최근 변경이 빠질 수 있으니 이 명령을 쓴다.

```bash
hair backup                                  # 지금 백업(서버가 켜져 있어도 됨)
hair backup --dest /mnt/nas/hair-backups     # 다른 디스크·NAS에
hair backup --list                           # 목록
hair restore backups/hair-backup-20261127-030000-auto.tar.gz   # 서버(hair serve·hair worker)를 끄고 실행
```

복원은 지금 데이터를 지우지 않고 `hair.db.before-restore-<시각>`(벡터 폴더도 같은 방식)으로 옮겨 둔 뒤 바꾼다. 백업 파일 안의
경로(바깥 경로·링크 거부)와 DB 무결성을 확인한 뒤에만 진행한다.

지식 베이스 벡터 색인(`vectordb` extra)을 SQLite 원본에 맞춰 다시 만들 때(색인 폴더를 지웠거나 옮겼을 때 —
평소에는 검색할 때 자동으로 맞춰진다. 저장된 임베딩을 그대로 써서 임베딩 API를 다시 부르지 않는다):

```bash
hair reindex-vectors                         # 빠진 것 채우고 지워진 것 정리
hair reindex-vectors --rebuild               # 색인을 비우고 처음부터 다시 만듦
```

실제 CrewAI 연동을 쓰려면(무거운 의존성 — opt-in):

```bash
pip install -e ".[crewai]"
# GenerateRequest.framework="crewai" 또는 Web UI "AI 에이전트 생성" 탭에서
# framework를 crewai로 선택 — 실제 LLM API를 호출하므로 비용이 발생한다.
# "Crew 빌더" 탭에서 여러 Agent를 실제 CrewAI Crew로 묶어 협업시킬 수 있다
# (업무에 바로 실행/즉석 테스트/실행 이력 3개 모드).

# MCP(외부 검색 등 도구) 연동을 쓰려면 추가로:
pip install -e ".[mcp]"
# Web UI "AI 에이전트 관리" 탭의 "MCP 서버(JSON)" 칸에
# [{"url": "http://localhost:8000/mcp", "transport": "streamable-http"}]
# 형태로 넣고 "모델 설정" 저장 — 이후 "Crew 빌더"에서 그 Agent에 실제
# MCP 도구가 붙는다. MCPServerAdapter는 생성 시점에 실제로 접속을
# 시도한다(접속 가능한 MCP 서버가 준비돼 있어야 한다).
```

실제 LLM 연동을 쓰려면(Anthropic·OpenAI·Ollama 중 선택):

```bash
pip install -e ".[llm]"                    # anthropic·openai SDK 설치(httpx는 기본 의존성에 포함)

# 방법 A — hair init으로 대화형 설정(.env에 저장, git에 커밋되지 않음, .gitignore 처리됨):
hair init
hair check   # 뭐가 설정됐는지/빠졌는지 확인

# 방법 B — 프로젝트 루트에 .env 파일을 직접 작성:
#   ANTHROPIC_API_KEY=...
#   OPENAI_API_KEY=...
# create_app()이 기동 시 자동으로 읽는다(python-dotenv, 이미 설정된 환경변수는
# 덮어쓰지 않음).

# 방법 C — 환경변수로 직접:
export ANTHROPIC_API_KEY=...               # 또는 OPENAI_API_KEY

# Ollama는 API 키가 없다 — 로컬 서버(기본 http://localhost:11434, OLLAMA_BASE_URL로
# 변경 가능)를 미리 띄워두고 provider="ollama"를 직접 지정해야 한다(자동판별 대상 아님).

# API 요청에 "use_llm": true 를 넣거나, Web UI "AI 에이전트 생성" 탭의
# 체크박스를 켜면 실제 API가 호출된다(비용 발생) — 안 켜면 무료 스텁 그대로.
# "AI 에이전트 관리" 탭의 "모델 설정"으로 Agent별 기본 provider/model을 저장해두면
# (비용 없음), 이후 "실행"이 매번 지정 안 해도 그 설정을 따른다. provider/model
# 목록은 "모델 레지스트리" 탭에 등록해두면 전 화면에서 드롭다운으로 공유된다.
```

### 권장 모델 & API 토큰(키) 발급

**토큰(API 키) 발급처** — Ollama는 로컬 실행이라 키가 필요 없다.
클라우드 provider는 먼저 가입해 키를 받아야 한다(가입 시 결제 수단
등록 필요, 사용한 만큼 과금):

| provider | 발급 위치 | 비용 |
|----------|-----------|------|
| Anthropic | https://console.anthropic.com/ (API Keys 메뉴) | 사용량 과금 |
| OpenAI | https://platform.openai.com/api-keys | 사용량 과금 |
| Ollama | https://ollama.com 에서 설치 후 로컬 실행 | 무료(로컬 컴퓨팅 자원만 소비) |
| Tavily(실시간 웹검색) | https://tavily.com | 무료 티어 있음 |

받은 키는 `.env`(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`TAVILY_API_KEY`)
에 넣거나 환경변수로 export — 위 "실제 LLM 연동을 쓰려면" 참고.

**Agent별 LLM 설정 최적화**(admin, "LLM 설정 최적화" 탭)는 역할을
**심층추론**(기획/분석/설계)/**정밀·신속**(구현/리뷰/QA)/**표준** 3단계
tier로 분류해 모델을 추천한다(Web UI "모델 레지스트리"/"⚡ 최적화 추천"
화면에 뜨는 표기와 동일). provider별 권장 기준:

| tier | 클라우드 권장 | 로컬(Ollama) 권장 | 비고 |
|------|---------------|---------------------|------|
| **표준**(일반 응답) | Haiku/GPT-4o-mini급(코드 기본값: `gpt-4o-mini`) | `llama3.1`(코드 기본값) 등 7B~8B급 경량 모델 | 가장 빠른 응답이 필요한 티어 |
| **정밀·신속**(구현/리뷰/QA) | Sonnet/GPT-4o급(코드 기본값: `claude-sonnet-5`) | 20B+ 급 코드 역량 모델(예: `qwen3.8:27b-mlx` — 이 플랫폼의 "개발자" 계열 Agent가 실제로 쓰고 있는 모델, 라이브 DB로 직접 확인) | 이름에 `code`/`coder`가 들어간 모델은 추천 랭킹에서 자동 우대되지만, 그렇지 않은 모델(`qwen3.8:27b-mlx` 포함)도 "모델 레지스트리"에서 admin이 이 tier로 직접 매핑해두면 패턴 추정보다 항상 먼저 추천된다 |
| **심층추론**(기획/분석/설계) | Opus급 | 30B+ 대형 로컬 모델도 가능하지만 **느리다**(아래 실측 참고) | 응답을 실시간으로 봐야 하면 클라우드 권장 |

**로컬(Ollama) 모델 쓸 때 실측으로 확인된 점**:
- 모델은 미리 받아둬야 한다(`ollama pull <모델명>`) — 받지 않은 모델을
  지정하면 그 자리에서 404로 실패한다(실제 사고 사례: 코드 기본값
  `llama3.1`이 로컬에 없어서 발생).
- **대형 모델일수록 느리다** — 실측 사례로 35B급 모델이 **단일 호출에
  337.7초**(약 5.6분) 걸렸고, "자율 개발 크루"처럼 매니저가 여러 차례
  위임까지 하는 멀티 Agent 실행은 **12분을 넘긴** 사례가 있다. 그래서
  백엔드 타임아웃도 그에 맞춰 넉넉히 잡혀 있다 — **클라우드보다
  Ollama가 항상 더 길고**, 대화든 업무든 같은 기준을 쓴다(단일 호출
  기본 클라우드 900초/Ollama 1800초, **심층추론** tier Agent는
  클라우드 1800초/Ollama 3600초, CrewAI 멀티 Agent 경로는 클라우드
  2000초/Ollama 5400초). 대화창에서 실시간 응답을 기다리는 용도라면
  7B~14B급으로, 그보다 큰 모델은 파이프라인처럼 비동기로 도는
  배치성 작업에 쓰는 걸 권장한다.
- **임베딩(내부지식검색/VectorDB) 모델**: 코드 기본값은 `mxbai-embed-large:latest`
  인데, 이는 "이미 받아져 있던 모델"이라서 고른 값일 뿐 성능상 최선은
  아니다 — 컨텍스트 한도가 **512토큰뿐**이라 조금만 긴 문서도 많이
  잘려나간다. `nomic-embed-text`(8192토큰, 16배 더 넓음)를 대신 받아서
  (`ollama pull nomic-embed-text`) "모델 레지스트리"에서 임베딩 모델로
  바꿔주면 장문 산출물 검색 품질이 더 좋다.

대화창·협업 워크스페이스(업무 실행)·지식 베이스 세 곳 중 어디서든
`.pdf`/`.html`/`.xlsx`/`.docx`/`.pptx` 파일을 업로드하려면(`.md`/`.txt`/
`.csv`는 추가 설치 없이 바로 동작 — 셋 다 같은 `documents.
extract_text()`를 공유한다):

```bash
pip install -e ".[documents]"              # pypdf·beautifulsoup4·openpyxl·python-docx·python-pptx 설치
```

"실시간웹검색" 스킬(Tavily, 대화창·Crew 실행 양쪽에서 동작하는 실시간
웹 검색)을 쓰려면 `.env`에 키를 추가:

```bash
#   TAVILY_API_KEY=...                       # https://tavily.com 무료 발급
```

Web UI 실행(부트스트랩으로 만든 ADMIN 계정으로 로그인):

```bash
hair serve                # http://127.0.0.1:8000/, hair.db(기본)에 저장 — 브라우저 자동으로 열림
hair serve --reload       # 개발 중 — 코드 변경 시 자동 재시작
hair serve --no-browser   # 브라우저 자동 열기 끄기(서버만 실행)
# 웹 서버를 재시작해도 AI 실행이 멈추지 않게(.env에 HAIR_WORKER_MODE=external) — 터미널을 하나 더 열어:
hair worker               # 작업 프로세스(같은 폴더·같은 --db-path, 하나만)
```

### 사내망에서 여러 명이 함께 쓰기

> **계정 보호**(2026-11-27): 같은 아이디로 비밀번호를 5번 틀리면 그 아이디를 15분, 한 접속 주소에서 여러 아이디로 20번
> 틀리면 그 주소를 15분 막는다(막힌 동안은 맞는 비밀번호도 거절, 활동 기록 `login_locked`). 관리자가 비밀번호를 재설정하면
> 잠금이 풀린다. 사용자는 헤더의 내 계정 이름을 눌러 비밀번호를 바꿀 수 있고, 비밀번호가 바뀌면 다른 곳의 로그인은 끊긴다.

같은 네트워크(사내망)의 팀원 여러 명이 한 서버에 각자 PC 브라우저로
접속해 쓰려면 아래 순서를 따른다. 서버를 띄우는 PC(관리자 PC) 1대만
있으면 되고, 나머지는 브라우저만 열면 된다 — 팀원 PC에 따로 설치할
필요 없음.

1. **(관리자 PC) 랜덤 세션 서명 키를 먼저 지정한다** — `--host
   0.0.0.0`처럼 루프백이 아닌 주소로 띄우면서 이 키를 안 주면, 세션이
   소스에 공개된 고정값으로 서명돼 같은 네트워크의 누구나 다른
   사용자(admin 포함)의 로그인 세션을 위조할 수 있다(`hair serve`가
   기동 시점에 이걸 감지하면 경고를 띄운다):

   ```bash
   hair init   # "세션 서명 키 — 지금 자동으로 생성할까요?"에 Enter(Y) → .env에 저장
   hair serve --host 0.0.0.0 --no-browser
   ```

   (`hair init` 없이 직접 하려면: `export HAIR_PLATFORM_SECRET_KEY="$(python3 -c
   'import secrets; print(secrets.token_hex(32))')"` 또는 같은 값을 `.env`에
   `HAIR_PLATFORM_SECRET_KEY=...`로 저장 — `hair check`로 제대로 들어갔는지
   확인할 수 있다.) **`--reload`는 여기서 절대 쓰지 않는다** — 개발 전용
   옵션이라 파일이 바뀔 때마다 서버가 재시작되며 접속 중인 팀원 전체가
   끊긴다.

2. **방화벽에서 포트를 연다** — 기본 포트는 `8000`(`--port`로 변경
   가능). macOS는 "시스템 설정 → 네트워크 → 방화벽"에서, Windows는
   "Windows Defender 방화벽 → 고급 설정"에서 해당 포트의 인바운드 연결을
   허용해야 다른 PC에서 접속된다.

3. **관리자 PC의 사내망 IP를 확인한다** — macOS/Linux는
   `ifconfig`(또는 `ip addr`), Windows는 `ipconfig`로 확인(보통
   `192.168.x.x`/`10.x.x.x` 형태).

4. **(관리자) 팀원 계정을 만들어준다** — 로그인 후 "사용자 관리" 탭
   (admin 전용)의 "사용자 생성" 폼에서 아이디/비밀번호/역할
   (`admin`/`manager`/`member`/`viewer`)을 지정해 계정을 만든다(API를
   직접 호출할 필요 없음). 생성 직후엔 조직도 상사가 지정돼 있지 않아
   그 사용자가 배정을 요청하면 결재가 단일 단계(승인자 미지정)로
   처리된다 — 필요하면 "조직도" 탭에서 상사를 지정해준다.

5. **(각 팀원 PC) 브라우저로 접속해 로그인한다** — 주소창에
   `http://<관리자 PC의 사내망 IP>:8000/`을 입력하고, 발급받은
   아이디/비밀번호로 로그인한다. 로그인 세션은 브라우저별 쿠키 기반이라
   팀원마다 독립적으로 유지된다(한 명이 로그아웃해도 다른 팀원 세션에
   영향 없음).

**동시 사용 시 알아둘 점**:
- `hair serve`는 단일 프로세스로 뜬다(멀티 워커 미지원) — 동시 접속
  자체는 문제없지만, 여러 명이 동시에 LLM 호출(Agent 실행/CrewAI/파이프라인)
  을 겹쳐서 돌리면 응답이 순서대로 밀려서 늦게 나올 수 있다. 실시간
  응답이 중요한 조직이면 인원 규모에 맞춰 여러 인스턴스(다른 포트·다른
  `--db-path`)로 분산하는 것도 방법이다(단, 데이터가 분리된다는 점
  유의).
- SQLite(WAL 모드)라 읽기는 동시에 여러 건 처리되지만, 쓰기(결재 승인,
  배정 확정 등)는 짧게 직렬화된다 — 일반적인 팀 사용 규모(수 명~수십 명)
  에서는 체감되지 않는다.
- 로그인 비밀번호는 평문 HTTP로 오간다 — 민감도가 높은 조직이라면
  앞단에 리버스 프록시로 TLS(HTTPS)를 추가하는 걸 권장한다.

Gate A–G 수동 실측(선택, 서비스 경로는 이미 상시 연결돼 있음 —
`agent_evaluation.py`):

```bash
agent-eval check          # API 키 등 설정 먼저 확인
hair eval                 # Gate A/B/D/E/G 측정 → results/agent_generation_eval.json + .html(종합 리포트)
agent-eval gate results/agent_generation_eval.json --tcr 85   # 채점(결과 파일은 하나씩)
```

## 구조

```
src/hair_platform/
  auth.py            # 세션 인증 + RBAC(4종 역할: admin/manager/member/viewer)
  models.py          # Pydantic v2 요청 모델
  db.py              # SQLite 스키마(WAL, audit_log append-only 트리거)
  app.py             # FastAPI 앱 조립(create_app — 기동 준비·로그인·GET /) + 아래 routes_*.register() 호출(266개 엔드포인트)
  routes_users.py    # 사용자 관리 API
  routes_work.py     # 업무 요청·할 일·내 업무·인박스·배정·실행 작업·AI에게 일 맡기기·후보 매칭 API
  routes_approvals.py # 결재 API
  routes_conversations.py # 대화·지식 등록 결재·지식 조각·문서 색인·조직 관계 그래프 API
  routes_deliverables.py # 결과물·댓글·버전·내보내기·부분 수정 제안 API
  routes_resources.py # AI 에이전트·사람 인력·도구/스킬 Pool·팀·Crew·파이프라인 템플릿 API
  routes_models.py   # 모델 레지스트리·임베딩·검색 설정·기본/보조 모델·사용량·조직 정책 API
  routes_reports.py  # 대시보드·비용·공정성·성과 리뷰·활동 기록·조직도 API
  routes_integrations.py # 연동 설정 API(API 키·코드 저장소·기본 구성 점검·외부 알림·정기 실행·이벤트 트리거)
  routes_common.py   # 여러 routes_* 모듈이 함께 쓰는 헬퍼(업로드 크기 제한·지식 등록 오류)
  pool.py            # Human/Agent Pool → ResourceCandidate 어댑터
  assignment.py      # 매칭 알고리즘(결정론적)
  match_llm_judge.py # 매칭 결과 LLM judge 폴백(역할명 부분일치로도 못 찾을 때)
  approvals.py       # 다단계 결재라인(조직도 매니저 체인)
  assignments.py     # 배정 요청·확정(팀 배정 + 다중 confirmed + 셀프배정 즉시확정 지원)
  teams.py           # 팀(사람+Agent 그룹) CRUD — "등록→재사용" 패턴
  jobs.py            # Job Queue(+ idempotency)
  sandbox.py         # Docker 샌드박스 명령 구성
  audit.py           # 감사로그
  performance.py     # 성과리뷰(동의 기반)
  cost.py            # 비용 조회(전역 + Task 단위)
  fairness.py        # 배정 공정성 지표
  retention.py       # 데이터 보관기간 정책(assignment_history 1년)
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선 수동 실행 probe(agent-evaluator SDK, python -m으로 직접 실행)
  agent_evaluation.py # Gate A-G 평가를 Agent 실행 경로에 상시 연결(agent_evaluations 테이블) + 구문검사 + 실행 가능성 Gate(주 산출물이 그대로 실행되는 완성본인지)
  content_validators.py # content_type별(html 등) pluggable 구문 검증기 디스패치
  requirements_traceability_llm.py # 요구사항 추적성 LLM judge 채점(요구사항 정의 단계 산출물 대비 — 단계 Gate 목록에 넣으면 통과 판정에 참여)
  brief_decompose_llm.py # 업무 분해 LLM 제안(역할·필요 스킬·선행 업무 의존관계)
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성(`hair bootstrap-admin`이 호출)
  cli.py             # 통합 CLI(`hair` 콘솔 스크립트) — init/check/serve/worker/bootstrap-admin/reset-data/backup/restore/eval
  env_setup.py       # `.env` 환경변수 정의·파싱·점검(`hair init`/`hair check`가 공유)
  webui.py           # Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙, 31개 탭) — 아래 webui_assets/를 기동 때 조립
  webui_assets/      # 화면 원본(2026-11-26 분리): index.html(셸)·app.css·manual.md(이용 가이드 원문)·js/NN-화면.js 30개(js/ORDER 순서로 이어 붙임)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  model_registry.py  # provider별 사용 가능 모델 등록·관리("등록→재사용") + 모델별 LLM 설정 최적화 tier(deep_reasoning/precision_fast/standard) 직접 매핑
  model_benchmark.py # 모델 성능 테스트 — 빠른 측정(예열 후 3회: 생성 tok/s·첫 응답·첫 로드·생각 끔) / 전체 점검(도구·JSON·수치·한국어·생각 끄기·긴 문서 → 단계 예상 시간으로 tier 추천)
  model_recommend.py # 모델 레지스트리 컨텍스트 한도·생성 옵션 추천(Ollama 모델 정보 + PC 메모리 / 클라우드 계열 표)
  checker_crosscheck.py # 검토 승인 후 규칙 기반 점검기를 최종본에 다시 돌려 판정이 다르면 사람 결재로
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임 + 다이어그램 + 자가검토)
  crew_history.py    # Crew 실행 이력(crew_runs) — 팀/즉석 실행의 진행상태·협업다이어그램·토큰사용량 영구 저장
  vectorstore.py     # 내부지식검색 Agent(VectorDB) — 임베딩(OpenAI/Ollama)·색인·검색(코사인+키워드 재순위화), 원본은 SQLite
  ai_budget.py       # AI 사용 한도 — 사용자별·조직 전체 월 한도(USD, 모델 레지스트리 단가 기준)·viewer AI 허용, AI 호출 직전에 확인
  data_connectors.py # 사내 DB 조회(SQLite·PostgreSQL·MySQL, 읽기 전용 두 겹·행 수/시간 상한·조회 감사) — `database_query` 도구
  mail_inbox.py      # 메일 수신함 읽기(IMAP, 읽기 전용·읽음 표시 유지·메일함별 공개 범위·조회 감사) — `mail_inbox` 도구
  calendar_feeds.py  # 캘린더 읽기(iCal/ICS 주소·파일, 반복 일정 펼침·공개 범위·조회 감사) — `calendar_events` 도구
  job_queue.py       # 백그라운드 작업 큐 + 별도 작업 프로세스(`hair worker`, HAIR_WORKER_MODE=external — 웹 서버 재시작에도 실행 유지)
  tool_audit.py      # 도구 사용 기록(실제로 부른 도구·외부 데이터·막힌 쓰기 도구)·단계 필수 도구·외부 데이터 경계(지시가 아닌 참고 자료로 감싸기)
  knowledge_access.py # 지식 베이스 문서별 공개 범위(전체·manager 이상·admin만·팀) — 요청자 기준으로 검색 결과를 거름
  vector_backends.py # 지식 검색 벡터 백엔드 — ChromaDB 임베디드(HNSW, `<db>-vectors/`) 또는 SQLite 전체 비교, SQLite 원본과 자동 동기화
  graphquery.py      # 조직배정관계조회 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의
  websearch.py       # 외부 검색 — 실시간 웹 검색(Tavily native 도구, 대화창·Crew 양쪽 동작, TAVILY_API_KEY 필요) + Parallel AI MCP(대화창·Crew 양쪽, MCP 서버 접속 시). Wikipedia 검색 코드는 남아있으나 이 배포 환경 IP가 지속 차단돼 기본 시드에서 뺐다(db.py 참고)
  documents.py       # 외부 자료(.md/.txt/.csv/.html/.pdf/.xlsx/.docx/.pptx) 업로드·텍스트 직접 입력 → 청크 분할 → VectorDB 색인
  pool_registry.py   # Skill Pool·Tool Pool — 스킬→도구 매핑 데이터화 + 개발업무/일반업무 category 분류, CrewAI Tool 조립
  data_admin.py      # brief/task/human/agent 하드 삭제(FK 연쇄 삭제 + confirmed 배정 보호) + 운영 데이터 공장 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 "초기화 시 보호" 팀만 유지, `hair reset-data`)
  deliverables.py    # 협업 워크스페이스 — 결과물/댓글, "내 업무", 사람 LLM 보조, 할 일 설정·선행 관계·활동·버전 이력
  deliverable_execution.py # 할 일에서 Agent·팀 Crew·즉석 Crew 실행(실행 가능 여부·시작·백그라운드 실행·재시도·평가)
  inbox.py           # 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고)
  dashboard.py       # 매니저 대시보드(팀 Gate 평균+대기 승인 건수+비용 추이) + Task 재시도 합산
  conversations.py   # 대화 중심 작업공간 — Task 독립 대화·참가자·메시지·문맥 구성, skill 그라운딩
  conversation_jobs.py # 대화 응답 실행 — 기본 AI/Agent/Team 멘션 3경로 작업, 작업 마감, 파이프라인 진행 알림
  pipeline_runtime.py # 단계형 업무 파이프라인 엔진(단독/순차 릴레이/병행 위임 단계, 단계별 Gate 목록 기준 자동 재시도·에스컬레이션, 검토 실패 시 작성 단계로 되돌림, Task 재시도 예산)
  reference_pipeline_seeds.py # 레퍼런스 Agent 39종 + Skill/Tool Pool + 파이프라인 템플릿 8종(소프트웨어 개발/사업·기획/제안서 작성/실적 보고 + domain_packs.py의 재무·영업·법무·인사) 기본 시드
  agent_optimizer.py # Agent별 LLM provider/model/tuning_config 최적화 추천(규칙 기반 + 선택적 LLM 보정, admin)
  id_scheme.py       # Agent/Tool/Skill 표시용 코드 체계(`AGT-REF-001` 등, 종류 구분용) — 정수 PK는 그대로, `code`만 별도 배정
  execution_graph.py # LangGraph 기반 에이전틱 실행엔진(팀별 선택) — 체크포인트·사람 승인 중단·재개
  skill_catalog.py   # 기본 스킬 가이드(정본) + 신규 스킬 시드 + 한 번만 적용하는 Agent-스킬 연결
  skill_markdown.py  # 스킬 구조화 필드 ↔ 마크다운 import/export
  skill_prompting.py # 스킬 그라운딩 프롬프트 조립(관련 스킬 상세·활용 원칙·품질 기준·도구 안내·결과물 언어)
  pool_catalog.py    # "스킬·도구 목록 조회" 내장 도구 + 업무 요청 → 기존 Agent 역할 제안(LLM 없음)
  presentation_deck.py # 발표자료 덱 디자인 시스템(CSS·차트 런타임 주입, 슬라이드 클래스 정규화·린트)
  context_policy.py  # 대화 컨텍스트 정책(스킬별 기본값·Agent별 덮어쓰기)
  conversation_memory.py # 대화 첨부·장기 기억 색인(표 데이터 집계 포함)
  token_budget.py    # 모델 컨텍스트 한도에 맞춘 프롬프트 예산·자르기
  tool_calling.py    # provider 공통 Tool 호출 루프 보조
  knowledge_submissions.py # 지식 등록 제출·결재(admin 승인 후 VectorDB 색인)
  language_qa.py     # 한국어 결과물 언어 품질 검사·자동 교정(조직 결과물 언어가 한국어일 때만)
  model_output.py    # 모델 출력 정리·감지(추론·도구 호출 흔적, 영어 메모, 작업 과정·혼잣말, 반복 루프, 잘린 마크업)
  model_options.py   # 모델별 생성 옵션·출력 한도·Ollama num_ctx(레지스트리 값, 특정 모델 하드코딩 없음)
  llm_usage.py       # 실제 LLM 토큰 사용량 기록(직접 호출·CrewAI 공통) + 모델별 단가로 금액 집계
  org_policy.py      # 조직 정책(사번 형식·결과물 언어)
  numeric_check.py   # 결과물 퍼센트 수치가 요청·조회·도구 결과 숫자로 재현되는지 검증·문장 교정(numeric_grounding)
  text_checks.py     # 인용 문장 근거 대조(quote_grounding)·한 음절 빠짐/바뀜 용어 탐지(term_consistency)
  korean_lexicon.py  # 사전에 없는 한국어 낱말 탐지(lexicon_check, 참고용 — Kiwi 형태소 분석기, korean extra)
  evaluation_reports.py # 평가마다 Agent-Evaluator HTML 리포트 생성·압축 저장·결과물에서 열기(격리 출처)
  evaluation_config.py # 평가 기준 설정(Gate별 통과 기준·SLA·추론 표지·도구 범위 감점, .aoo/targets.json 호환)
  code_tests.py      # 개발 결과물의 Python·JavaScript(Jest·node:test) 코드+테스트를 네트워크 차단 Docker에서 실제 실행(test_execution)
  browser_check.py   # HTML 결과물을 Chromium(샌드박스·네트워크 차단)으로 열어 스크립트 오류·빈 화면·버튼 오류 기록(browser_execution)
  login_guard.py     # 로그인 시도 제한(아이디별 5회·접속 주소별 20회 틀리면 15분 잠금)
  backup.py          # 백업·복원(.tar.gz 한 파일 = DB 온라인 백업 + 지식 벡터, 자동 백업·보관 개수·초기화 전 백업)
  system_status.py   # 시스템 상태 한눈에(서버·DB·디스크·백업·작업 큐·모델 연결) + /health
  process_metrics.py # 프로세스 성과(파이프라인별 리드타임 분해·한 번에 완료·재작업·자동 처리율·기준 시간·단계별 병목)
  process_learning.py # 배운 점(반려 사유·반복 미달 → 개선 제안 → 적용 시 단계 지시문에 주입) + 변경 기록·전후 비교·되돌리기
  approval_assist.py # 결재 도우미(점검 요약·이전 판 비교·확인할 점) + 위험도 추천·사람 결정 대조·낮은 위험 자동 승인(검증·표본)
  pipeline_drafting.py # 업무 설명 → 파이프라인 초안(LLM, 등록 역할·도구로 정제, 실패 시 규칙 초안) + 비슷한 기존 파이프라인
  inbound.py         # 외부 입력 트리거(메일·웹훅·일정 → 파이프라인, 외부 데이터 경계·중복·시간당 상한)
  stage_actions.py   # 결재 후 실행 동작(메일·Slack/Teams·JSON 웹훅 — 승인하고 실행일 때만, 미리 보기·한 번·기록)
  capacity.py        # 처리 용량(줄별 대기·실행 시간, 바쁜 정도, 지금 맡기면 걸릴 시간)
  resource_capacity.py # 사람 동시 용량·부재 기간·부하(끝나지 않은 확정 배정), 매칭 가용성(남은 용량·모델 대기)·배정 경고
  resource_planning.py # 할 일 기한·예상 시간·인원 채움, 4주 활용도(주당 시간 − 부재), 지연·임박, 역할별 수요와 공급
  resource_lifecycle.py # 인계 점검·결재 넘기기·결재 위임·에스컬레이션, 시간 기반 비용(단가×예상 시간 + AI 실측)·예산
  progress.py        # 실행 중 현재 단계·경과 시간 기록(대화·워크스페이스 진행 표시)
  integrations.py    # 개인 API 키(Bearer) + 외부 알림(Slack/Teams 웹훅·메일)
  automation.py      # 파이프라인 정기 실행(매일/평일/매주) 루프(hair serve)
  exports.py         # 결과물 Word(.docx)·Excel(.xlsx, 표마다 시트)·PowerPoint(.pptx, 제목마다 슬라이드) 내보내기
  config_check.py    # 기본 구성 점검(운영 DB vs 현재 버전 기본 구성, 빠진 것 반영)
  plan_merge.py      # 계획 분담 단계 모드(리드 계획 → 팀원 분담 실행 → 리드 병합)
  domain_packs.py    # 업무 영역 템플릿(재무·영업/고객지원·법무·인사 도구·스킬·Agent·파이프라인)
  code_repos.py      # 코드 저장소 읽기 연동(등록 Git 저장소의 커밋된 파일 목록·내용·검색·커밋, 읽기 전용)
  repo_commit.py     # 승인된 결과물 → 등록 저장소 main 로컬 커밋(경로 명시 블록만, 점검 통과 시, push 없음)
  triggers.py        # 이벤트 트리거(파이프라인 완료·결과물 승인 → 다음 파이프라인, 연쇄 3단계 제한)
  wizard.py          # AI에게 일 맡기기 마법사(한 문장 → 빠른 질문·Agent·파이프라인·정기 실행 추천, LLM 없음)
  edit_proposals.py  # 결과물 부분 재작성 제안(섹션·구간만 AI가 다시 씀) + 수락·거절(수락 시 기존 편집 경로로 반영)
docs/
  OVERVIEW.md        # 지향점·핵심 개념·아키텍처·주요 기능 개요(과거 설계 논의·벤치마크는 git log 참고)
.claude/skills/rbac-permission-audit/  # RBAC 권한 설정을 테스트로 감사하는 Claude Code 스킬
.github/workflows/ci.yml               # pytest+ruff CI(constraints.txt로 같은 버전 조합 설치) + 화면 테스트(Playwright) 작업
constraints.txt                        # 전체 테스트를 통과한 의존성 버전 조합(scripts/update_constraints.py가 생성)
tests/unit/                            # 골든셋 1,800건 이상 통과(CI는 4묶음 병렬 — scripts/ci_shard.py)
```

## 로드맵

핵심 기능은 전부 구현 완료됐고, 그 이후 반복적으로 들어온 사용자 요청에
맞춰 계속 확장돼왔다 — 다음에 뭘 할지는 `docs/OVERVIEW.md`의 "알려진
한계" 절을 참고해서 정한다.

## 변경 이력

버전별 핵심 변경만 한 줄로 요약한다 — 자세한 배경·버그 재현 과정은
커밋 메시지(`git log --oneline --all`)에 날짜·사용자 요청 원문과 함께
남아 있다.

| 버전 | 날짜 | 핵심 변경 |
|------|------|-----------|
| v0.3.2 | 2026-11-27 | 사용성 개선(첫 화면 홈·업무 흐름 메뉴·업무 요청 진행 보기·빈 상태 안내·위험 버튼 정리·쉬운 말·관리 화면 요약·이용 가이드 이용자/운영자 편 재구성) · 시스템 상태·`/health` · 백업·복원(`hair backup`/`hair restore`, 자동·초기화 전) · 로그인 시도 제한·내 비밀번호·비밀번호 변경 시 세션 끊김 · 작업 프로세스(`hair worker`)·모델별 동시 호출 상한·대화 우선 · 성능 테스트 정교화(능력 점검·단계 예상 시간)·최적화가 결과 반영·추천값 · Ollama 추론 끄기·단계별 모델 · 도구 사용 기록·필수 도구·외부 데이터 경계·점검기 대조 · JS 테스트·HTML 화면 실행 검증 · 사내 DB·메일·캘린더 읽기 · 인사 템플릿 · 벡터 DB · AI 사용 한도 · PR 커밋 · 메뉴 찾기(Ctrl+K) · CI 병렬(약 27분 → 8분) |
| v0.3.1 | 2026-11-25 | 평가 리포트·평가 기준 화면 · 인용·용어 검사·검토 판정 반영 · 코드 저장소 읽기·커밋 · 작성 중 미리보기·진행률 · 부분 수정·Excel/PowerPoint 내보내기 · 메일 알림·이벤트 트리거 · 홈 "AI에게 일 맡기기" · `create_app` 분리 · 삭제 오류·DB 잠금 수정 |
| v0.3.0 | 2026-11-24 | 결과물 자동 검증(수치·테스트 실행·언어) · 대화→업무 전환 · 정기 실행·API 키·알림 · 결과물 버전 비교·Word · 계획 분담 단계 · 업무별 AI 사용량 · 재무·영업·법무 템플릿 · Ollama 호출 회귀 수정 |
| v0.2.0 | 2026-11-23 | LangGraph 실행엔진(선택) · 지식 등록 결재·스킬 구조화·발표자료 덱/차트 · 파이프라인·스킬·도구 실측 점검 후 품질 개선 · 실제 토큰 사용량·모델별 단가·결과물 언어 정책 · 이용 가이드·도움말 현행화 |
| v0.1.6 | 2026-11-12 | 모델 레지스트리 성능 테스트(실측 속도 기반 tier 추천) 추가 · LLM 호출 타임아웃 기준 통일(Ollama가 클라우드보다 항상 길게, 대화·업무 공통) · 대화 HTML 첨부 미리보기 깨짐·즉시 전송 버그 수정 · 샘플 체험 기능 제거 |
| v0.1.5 | 2026-11-12 | `hair serve` 로그 노이즈 제거 · 업무 진행상황 가시성 개선(실행 중 표시·완료 알림·보드 배지) · 피드백/확인 UX 통일 |
| v0.1.4 | 2026-11-12 | PyPI 첫 배포 준비(라이선스 SPDX 전환, LICENSE/프로젝트 URL 추가) + 실제 배포 · pending_onboarding 팀이 활성화 토글로 온보딩 절차를 우회할 수 있던 버그 수정 |
| v0.1.3 | 2026-11-11 | Skill/Tool "개발업무/일반업무" 분류(대화창 기본 숨김+필요시 노출) · HAIR 자체 Skill 이름 30개 한글화 · 제안서 작성/실적 보고 파이프라인 추가(템플릿 4종) |
| v0.1.2 | 2026-11-09 | 파이프라인 Ollama 실제 적용 + "구현" 단계 합성 버그 수정(위임 계획만 남던 문제) · 사업/기획 파이프라인 추가 · 자동 단계적 에스컬레이션·셀프배정 즉시확정·Task 재시도 예산 등 로드맵 9건 |
| v0.1.1 | 2026-11-03 | 리소스 매칭/RAG 검색 품질 향상 · 결재·GraphDB 화면 가독성 개선 · Crew 빌더 신설 · 사내망 다중 사용자 접속 준비(`hair reset-data` 포함) |
| v0.1.0 | 2026-10-26 | 외부 자료 업로드(RAG) 지원 + Admin "지식 베이스" 탭 신설 · Agent 응답 형식(`content_type`) 일관성 수정 |
| v0.0.4 | 2026-10-20 | Crew 빌더 전신(멀티 Agent 즉석 실행 통합) · 배정 취소 기능 · Agent 역할/인격 상세조정 |

## 라이선스

MIT
