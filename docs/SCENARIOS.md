# HAIR 대표 시나리오 — 개발 업무 / 일반 업무 / 고객 지원 / Tool·Skill 활용 / 멀티 사용자·멀티 에이전트 팀 협업 / 멀티 provider 신규 Agent 개발

사용자 요청(2026-09-28): "HAIR 대표시나리오 (개발, 일반 업무 등) 몇개를
기획해줘". 이 문서는 실제 구현된 화면·엔드포인트·데이터 흐름만으로
구성한 **워크스루**다 — 아직 없는 기능(예: F1의 AI 기반 자동 분해)은
쓰지 않고, 지금 그대로 실행하면 재현되는 순서를 적는다. 각 시나리오
끝에 "이 시나리오가 보여주는 HAIR의 핵심 가치"와 "현재 구현과의 정합성
갭"을 덧붙인다.

**추가(2026-09-29)**: 사용자 요청("계정 정보는 그대로 유지하고, 업무
정보 삭제 후... End-to-End 전 과정을... 실행하며... Tool, Skill을
적극 활용하는 시나리오를 기획해 진행해줘")에 따라 시나리오 4를
추가했다 — 시나리오 1-3은 실제로 실행해보니 전부 Tool/Skill 그라운딩
(ADR 결정 11)을 거치지 않는 조합이었다는 게 드러나, VectorDB 검색을
실제로 쓰는 시나리오로 그 갭을 메웠다(실행 중 발견한 갭은
`docs/LIMITS.md` §41 참고).

**추가(2026-10-07)**: 사용자 요청("멀티 사용자, 멀티 에이전트 구조의
실용적인 시나리오로... 개선해줘")에 따라 시나리오 5를 추가했다 —
시나리오 1-4는 전부 "할 일 하나당 사람 당사자 1명 또는 Agent 요청자
1명"(혼합이어도 각 역할 최대 1명)이었다 — 복수의 사람과 복수의
Agent가 한 할 일에 동시에 협업하는 구조는 한 번도 보여준 적이
없었다. 다자간 팀 협업 기획 Phase 1+2(`docs/MULTI_PARTY_TEAM_
COLLABORATION_PLAN.md`, `docs/LIMITS.md` §61)로 실제 가능해진
"팀(teams) 배정 + 다중 confirmed 배정 공존"을 그대로 실행하는
시나리오를 추가했다 — 이 시나리오도 아직 없는 기능(팀의 Agent 멤버
자동 실행 등, Phase 3)은 쓰지 않고 지금 그대로 재현되는 순서만
적는다.

**추가(2026-10-07)**: 사용자 요청("생성 결과물이 소스코드, 마크다운이나
HTML 방식의 리포트가 되도록 시나리오를 보완하고, 해당 시나리오를
실행하면서 전체적인 기능을 점검해줘")에 따라 결과물 형식(`content_type`,
`docs/LIMITS.md` §68 Tier 1+2)이 실제 업무 흐름에서 쓰이도록 세
시나리오를 보완했다 — 시나리오 1(버그 패치)은 `content_type="code"`,
시나리오 2(마케팅 보고서)는 `content_type="markdown"`, 시나리오 4(정책
문의 응답)는 `content_type="html"`로 각각의 산출물 성격에 맞춰
지정한다. 형식 지정 자체가 Agent/LLM 프롬프트에 "이 형식으로 써
달라"는 지시를 자동으로 덧붙이므로(`deliverables.
_CONTENT_TYPE_INSTRUCTIONS`), 사람이 매번 "마크다운으로 써줘"라고
직접 요청할 필요가 없어진다. 세 시나리오 모두 실제 리뷰 서버(포트
8010, 실제 `hair.db` 아님 — §62 사고 재발 방지)에 라이브로 실행해
확인했다(`docs/LIMITS.md` §69).

**추가(2026-10-07)**: 사용자 요청("시나리오 내용도 crew 에이전트
그룹을 포함하도록 개선해줘")에 따라 시나리오 5를 "개별 Agent 배정"
방식에서 **"팀 Agent 실행(Crew)" 그룹 실행**(Phase 3) 중심으로
다시 썼다 — 사람 멤버도 Agent 멤버도 팀에 등록해두는 것만으로 충분해
개별 Agent 배정이 아예 필요 없어졌고, 산출물은 `content_type=
"markdown"`으로 받는다. 이 재작성·라이브 실행 과정에서 `GET /api/
my-work`가 팀 전용 배정을 전혀 인식하지 못하던 실제 버그를 발견해
바로 고쳤다(`docs/LIMITS.md` §70).

**추가(2026-10-08)**: 사용자 요청("HAIR-Platform 업무 요청으로
HAIR-Platform으로 개발 가능한 수준의 게임 기획-개발 업무를 진행하는
시나리오를 작성하고 해당 시나리오를 수행해줘 — 새로운 에이전트 개발,
openAI 및 Ollama 모델 활용")에 따라 시나리오 6을 추가했다 — 지금까지
모든 시나리오가 **OpenAI 하나만** 썼다는 게 드러나(provider 파라미터
자체는 시나리오 1부터 있었지만 실제로 Ollama를 골라 쓴 적은 한 번도
없었다), 서로 다른 provider를 쓰는 Agent 2개(기획=OpenAI, 코드 구현=
로컬 Ollama의 전문 코딩 모델)를 한 업무 안에서 함께 쓰는 시나리오로
그 갭을 메웠다. "새로운 에이전트 개발"은 기존 시나리오의 Agent를
재사용하지 않고 이 시나리오 전용으로 새로 만든다는 뜻이다.

**추가(2026-10-08)**: 사용자 요청("게임의 기능에 openai나 ollama를
이용하는 AI 기능이 들어가도록 시나리오를 구성하고, 복수의 사람과
crewai 에이전트 그룹이 협업하는 업무가 되도록 기획하고 HAIR-Platform
으로 실행해줘")에 따라 시나리오 7을 추가했다 — 시나리오 5(팀 Crew
그룹 실행)와 시나리오 6(신규 Agent·멀티 provider)을 합치되, 이번엔
**산출물(게임) 자체가 런타임에 로컬 Ollama를 직접 호출하는 AI
기능을 내장**해야 한다는 새 요구사항이 더해진다. "복수의 사람"은
팀 리더+팀원, "crewai 에이전트 그룹"은 이 시나리오 전용 신규 Agent
2개를 "팀 Agent 실행(Crew)" 한 번으로 묶어 실행한다.

**중요한 실측 전제**: F1(브리프 제출)의 "역할 후보 추출"은 AI 분해가
아니라 **줄바꿈 단위 스텁**이다(`app.py:92-95` `parse_brief_stub` —
브리프 텍스트를 줄 단위로 쪼갠 게 전부, 키워드 인식조차 없음). 실제
Task 분해(F2, `POST /api/briefs/{id}/tasks`)는 Manager/Admin이
`stub_roles`를 참고해 **role_name·required_skills를 직접 입력**하는
수동 폼이다(ADR 결정 1-A — 후보 매칭은 분해에 포함 안 함). 아래
시나리오는 전부 이 실제 흐름을 따른다.

---

## 시나리오 1 — 소프트웨어 개발: "결제 실패 버그 수정"

**배경**: Manager(`pm_kim`, manager role)가 운영 중 발견한 결제 API
버그를 브리프로 등록하고, 백엔드 수정은 AI Agent에게, 최종 코드 리뷰는
사람에게 맡긴다.

1. **브리프 제출(F1)** — Manager가 로그인해 브리프 폼에 다음을 입력:
   ```
   결제 확정 API에서 특정 카드사 응답 시 500 에러 발생
   재현 로그: POST /payments/confirm, provider=hanacard일 때만 실패
   백엔드에서 원인 분석 및 패치 필요
   QA가 재현 케이스로 회귀 테스트 필요
   ```
   `POST /api/briefs` → `parse_brief_stub()`가 줄 단위로 4개 후보
   문자열을 `stub_roles`에 저장(그 이상의 분석은 없음 — Manager가 직접
   봐야 한다).
2. **Task 분해(F2)** — Manager가 `stub_roles`를 참고해 직접 2개 Task를
   등록: `role_name="백엔드 개발"`(`required_skills=["python","결제 API"]`),
   `role_name="QA"`(`required_skills=["회귀 테스트"]`). `POST
   /api/briefs/{id}/tasks`.
3. **배정 요청·승인(F5-F7)** — "백엔드 개발" Task는 Agent Pool에서
   `role_name` 매칭되는 Agent(예: 이전에 온보딩된 backend Agent)를
   배정 요청, "QA" Task는 Human Pool에서 사람을 배정 요청. 둘 다
   Manager 본인이 아닌 다른 승인자가 승인해야 확정된다(ADR 3-A
   자기승인 방지 — Manager가 요청자면 동일인이 승인 불가).
4. **협업 워크스페이스 진입** — 확정된 "백엔드 개발" Task를 연
   pm_kim(이 Task의 Agent 배정 요청자)이 재현 로그·기대 동작을 사람
   입력으로 작성해 `deliverable(author=human)`로 저장. **실측으로
   정정(2026-10-07, 라이브 시나리오 실행)**: 이 글은 원래 "QA 담당자
   (또는 Manager)"라고 썼지만, QA 담당자는 "QA"라는 **별개의 Task**에
   배정된 사람이라 "백엔드 개발" Task의 참가자가 아니다 —
   실제로 글을 쓰면 403("이 업무의 협업 워크스페이스에 접근할 권한이
   없습니다", `task_participant_relationship()`). 지금 가능한 유일한
   경로는 그 Task의 Agent 요청자(pm_kim) 본인이 쓰는 것뿐이다 — 같은
   브리프의 Task끼리도 서로 격리돼 있어 QA 담당자가 "백엔드 개발"
   Task에 직접 맥락을 남길 방법이 플랫폼에 없다는 뜻이다(`docs/
   LIMITS.md` §63 "갭 2").
5. **AI 실행 트리거(Phase 4, 결과물 형식 지정)** — `POST /api/tasks/{id}/
   execute-agent {"content_type": "code"}` 호출(2026-10-07 추가 —
   패치 자체가 코드이므로 `content_type`을 명시한다) → 사람 입력을
   Task context에 포함하고 "결과물은 코드로 작성하세요... ``` 코드
   블록 안에 넣고..."라는 지시를 프롬프트에 자동으로 덧붙여
   `agent_runtime.execute_agent()`가 백그라운드로 LLM을 1회 호출 →
   완료되면 `deliverable(author=agent, content_type='code')`(패치
   설명·원인 분석) 생성. 협업 워크스페이스에선 이 산출물이 구문
   강조된 코드 블록으로 렌더링되고, "다운로드" 버튼으로
   `deliverable-{id}.txt`로 바로 받을 수 있다.
6. **Gate A-G 자동 평가** — Agent 산출물 저장 직후 자동으로 채점되고
   Gate 배지(초록/노랑)가 협업 워크스페이스에 표시된다. Gate 점수가
   낮으면(`인박스`의 "낮은 Gate 점수" 항목으로도 뜸) 사람이 그 근거를
   보고 반려 여부를 판단한다.
7. **승인/반려** — 사람이 Agent 산출물을 검토해 `approved`(다음 QA
   Task로 이어짐) 또는 `rejected`(Agent 재실행 또는 사람이 직접 수정).

**핵심 가치**: HOTL(Agent 실행은 비동기, 사람은 언제든 진행 상태를
`GET /api/tasks/{id}/agent-execution-jobs`로 확인) + Gate A-G로
"AI가 짠 패치를 그냥 믿지 않고 근거로 검증"하는 신뢰성 확보.

**갭**: Agent가 실제로 코드를 수정하거나 테스트를 실행하지는 않는다 —
LLM 호출 1회로 "패치 설명 텍스트"를 만들 뿐이다(코드 저장소 접근·실제
패치 적용·CI 연동은 스코프 밖, `docs/LIMITS.md` 참고).

**라이브 실행 확인(2026-10-07)**: 1-7단계 전부를 리뷰 서버(포트 8010)에
실제 API 호출로 재현 — 브리프 제출부터 Agent 생성·온보딩·배정(자기승인
방지로 pm_kim 본인 승인 거부 확인)·사람 입력 작성·`content_type="code"`
실행·Gate A-G 채점·승인까지 끊김 없이 통과했다. 협업 워크스페이스를
Playwright로 열어 결과물이 구문 강조된 다크 코드 블록(키워드/주석 색
구분)으로 정확히 렌더링되고 "다운로드" 버튼이 `deliverable-{id}.txt`로
실제 다운로드되는 것까지 확인했다. `docs/LIMITS.md` §69 참고.

---

## 시나리오 2 — 일반 업무: "월간 마케팅 성과 보고서 작성"

**배경**: Manager가 매달 반복하는 보고서 작성을 팀원(Member)에게
배정하고, 팀원은 LLM 보조 초안 작성 기능을 쓴다 — Agent를 전혀 쓰지
않는, 순수 사람 중심 시나리오(HAIR가 "AI 강제"가 아님을 보여준다).

1. **브리프 제출** — Manager가 "9월 마케팅 캠페인 성과 정리, 지난달
   대비 CTR·전환율 비교, 다음 달 제안 포함"을 제출.
2. **Task 분해** — `role_name="마케팅 보고서 작성"`,
   `required_skills=["데이터 요약","보고서 작성"]` 1건 등록.
3. **배정·승인** — Human Pool에서 스킬 태그가 맞는 Member를 검색해
   배정 요청(§검색/필터 스케일 — `GET /api/pool/search?availability=
   available`), Manager 아닌 다른 승인자가 승인.
4. **협업 워크스페이스 — "LLM으로 도와줘"(결과물 형식 지정)** — Member가
   지난달 수치를 붙여넣고 형식 선택을 "Markdown"으로 바꾼 뒤(2026-10-07
   추가 — 보고서는 제목·목록·표가 있는 게 자연스러우므로) "LLM으로
   도와줘"를 누르면 `POST /api/tasks/{id}/assist {"draft_context":
   "...", "content_type": "markdown"}` 호출 → `system_prompt_for_
   human_assist()` 기반 프롬프트에 "결과물은 Markdown 형식으로
   작성하세요(제목은 #, 목록은 -...)" 지시가 자동으로 덧붙어 LLM이
   마크다운 초안(`# 9월 마케팅 캠페인 성과` 같은 제목·`-` 목록 포함)을
   제안한다("최종 결정과 책임은 사람에게 있습니다"라는 문구가 시스템
   프롬프트에 명시돼 있음 — Agent 실행과 달리 이건 **사람이 작성한
   것으로 저장**된다).
5. **제출·검토 요청** — Member가 초안을 다듬어 `POST /api/tasks/{id}/
   deliverables {"content": "...", "content_type": "markdown"}`로
   제출한다 — 같은 형식을 유지해야 워크스페이스에서 계속 마크다운으로
   렌더링된다(4단계 초안과 다른 `content_type`을 주면 그 값으로
   저장되므로, 형식을 바꾸고 싶으면 제출 시점에 다시 선택하면 된다).
   **2차 실측 정정(2026-10-07, 라이브 시나리오
   재실행)**: 이 문서는 한동안 "사람 산출물엔 승인 절차 자체가 없다"
   (2026-09-28 1차 정정)고 적어뒀는데, 그 갭은 이미 `docs/LIMITS.md`
   §34(사람 deliverable 제출/승인 메커니즘, GC172-176)로 **해결된 뒤
   문서만 갱신이 안 돼 있었다** — 이번에 다시 라이브로 돌려 확인했다.
   실제로는 본인이 `POST /api/deliverables/{id}/submit`("검토 요청"
   버튼)을 눌러야 `status='pending_review'`로 바뀌며 Agent 산출물과
   **같은 F7 결재 체인**(`approvals.create_approval_chain()`)이 붙는다
   — 자동 제출이 아니라 명시적 트리거라는 점만 Agent와 다르다.
6. **승인/반려** — Manager(pm_kim)가 결재 체인을 통해 승인하면
   `status='approved'`로 확정되고(`PATCH` 아님 — `POST /api/approvals/
   {id}/decide`), 협업 워크스페이스에도 Agent 산출물과 동일하게
   승인/반려 버튼이 뜬다(`isSupervisor && d.approval_id` 조건은
   author_type을 가리지 않는다). 반려 시 "결재" 탭 또는 워크스페이스
   양쪽에서 처리할 수 있다.
7. **대시보드 확인(Phase 5)** — Manager는 `GET /api/dashboard`에서
   이번 달 완료된 보고서 Task를 확인한다 — 단, 팀 평균 Gate 점수는
   Agent 산출물에만 있는 `agent_evaluations`를 집계하므로(§24) 이
   보고서처럼 순수 사람 산출물은 Gate 평균에 반영되지 않는다.

**핵심 가치**: 협업 워크스페이스가 "Agent 실행"과 "사람 LLM 보조"를
같은 화면에서 다룰 뿐 아니라, **승인 게이트도 §34 이후로는 author_type
에 상관없이 같은 F7 체인을 공유**한다 — "검토 요청"을 누르느냐(사람)
자동 제출되느냐(Agent)만 다르다. Gate A-G 자동 채점만 Agent 전용으로
남는다(사람 산출물은 애초에 "채점"할 대상이 아니라는 설계 — §24).

**갭(2026-10-07 재실측으로 축소)**: 완료 판정 메커니즘 자체는 §34로
이미 메워졌다 — 1차 정정(2026-09-28)·`docs/LIMITS.md` §33이 지적한
"승인·반려도 코멘트도 API로 불가능하다"는 더 이상 사실이 아니다.
남은 진짜 갭은: (1) Member가 "검토 요청"을 누르는 걸 잊으면 draft
상태로 영원히 남을 수 있다(알림·기한 없음), (2) Gate A-G는 여전히
사람 산출물엔 안 붙어 품질을 정량적으로 볼 방법이 없다(이건 의도된
설계지 버그가 아니다 — 사람 작업을 Agent 채점 기준으로 재는 게 더
이상할 것이다).

**라이브 실행 확인(2026-10-07)**: 1-7단계 전부를 리뷰 서버에 실제 API
호출로 재현 — `content_type="markdown"`으로 "LLM으로 도와줘"(가짜 LLM이
실제로 `#`/`-` 마크다운을 반환) → 같은 형식으로 제출 → "검토 요청" →
승인까지 통과. 협업 워크스페이스에서 제목(`<h1>`)·목록(`<li>`)이 정확히
렌더링되고 `deliverable-{id}.md`로 다운로드되는 것까지 확인했다.
`docs/LIMITS.md` §69 참고.

---

## 시나리오 3 — 고객 지원(CS): "반복 문의 응답 초안 + 에스컬레이션"

**배경**: Viewer 권한만 가진 1선 상담원이 문의를 브리프로 못 올리고
(F1은 Admin/Manager 전용, RBAC), Manager가 대신 등록 → Agent가 초안을
만들고 Member가 최종 확인 후 고객에게 보낸다. RBAC 4종 역할이 실제로
갈리는 지점을 보여주는 시나리오.

1. **문의 접수** — 상담원(Viewer)은 문의 내용을 사내 채널로 Manager에게
   전달(HAIR 밖의 일 — 플랫폼은 F1부터 관여).
2. **브리프 제출** — Manager가 "환불 정책 문의 반복 다수, 표준 응답
   템플릿 필요, 애매한 케이스는 에스컬레이션 문구 포함" 제출.
3. **(사전 준비) Skill Pool에 "고객 응대" 등록** — **실측으로 정정
   (2026-10-07, 라이브 시나리오 실행)**: "고객 응대"는 기본 시드
   데이터에 없다 — Skill Pool에 먼저 등록하지 않으면 `required_skills`
   에 넣어도 **아무 효과 없는 빈 라벨**일 뿐이다(`skill_pool`에 없는
   태그는 `augmentation_mode` 매칭 자체가 안 걸린다). "도구/스킬
   Pool" 탭에서 `name="고객 응대"`(모드는 `always` 또는 `tool`)로
   먼저 등록해야 아래 4단계의 "정적 프롬프트 주입"이 실제로 일어난다.
4. **Task 분해·배정** — **실측으로 정정**: 원래 이 글은 "CS 응답
   초안"과 "최종 검수"를 별개 Task로 나눴는데, 그러면 "최종 검수"
   담당자는 "CS 응답 초안" Task의 참가자가 아니라서(§1·§3 시나리오에서
   반복 확인된 패턴 — 같은 브리프의 다른 Task끼리는 완전히 격리돼
   있다, `docs/LIMITS.md` §63 "갭 2") 정작 그 Agent 산출물에 댓글조차
   못 남긴다. "다중 confirmed 허용"(§61)을 이용해 **Task 1개**
   (`role_name="CS 응답 초안"`, `required_skills=["고객 응대"]`)에
   Agent Pool 배정과 Member(검수자) 배정을 **둘 다** 요청한다 — 같은
   Task에 사람+Agent가 함께 confirmed로 공존하므로 Member가 진짜
   참가자가 된다.
5. **AI 실행** — `execute-agent` 트리거 — Skill Pool에 등록해둔
   "고객 응대" 스킬이 `augmentation_mode`에 따라 정적 프롬프트로
   주입되어(§3-1) 일관된 톤의 초안 생성.
6. **Gate 평가 확인** — Gate 배지가 "측정 없음"(노랑)이면 — 예를 들어
   TCR(Task Completion Rate) 관련 Gate가 채점 불가 상태면 — 검수자가
   이를 신호로 더 꼼꼼히 검토한다(이번 세션 UI 리뷰에서 고친
   "Gate 측정 없음" 오탐 방지 배지가 정확히 이 장면에 쓰인다).
7. **최종 검수** — **실측으로 정정**: Member는 이제 같은 Task의
   참가자라 초안에 **댓글**은 남길 수 있다(애매한 케이스에 에스컬레이션
   필요성을 적는 식). 다만 **승인/반려 자체는 여전히 admin/manager만
   가능**하다(`decide_approval()`은 `require_role(ADMIN, MANAGER)` —
   Member는 F7 결재 체인에 참여할 수 없다, ADR 결정 3). 즉 "최종
   검수"는 Member가 의견을 남기고 실제 승인은 Manager가 하는 HOTL
   구조이지, Member가 단독으로 승인권을 갖는 구조가 아니다 — 시나리오
   2(사람 산출물 승인)와 같은 역할 분담 원칙이 여기도 그대로 적용된다.
8. **인박스로 흐름 확인** — Manager는 로그인 시 `GET /api/inbox`에서
   "승인 대기" 항목으로 이 Task를 바로 확인한다(탭을 일일이 돌아다닐
   필요 없음, Phase 5).

**핵심 가치**: RBAC 4종이 실제로 "누가 브리프를 못 올리는지, 누가
승인하는지"를 가르는 걸 보여준다 + 인박스가 "내가 지금 뭘 해야
하는가"를 역할 무관하게 한곳에 모은다 + "다중 confirmed 허용"이
Member를 "댓글만 남기는 진짜 참가자"로 끌어들이는 데 실제로 쓰인다.

**갭**: (1) Viewer가 상담원 역할이라는 전제는 시나리오상 설정일 뿐 —
실제 "상담원용 화면"이 따로 있는 건 아니다(Viewer는 읽기 전용, 브리프
제출조차 못 함). 실사용 시 상담원이 Viewer보다 넓은 권한(예: Member)
을 가져야 할 수 있다. (2) "최종 검수"를 별도 Task로 분리하고 싶다면
(예: 검수 이력을 Task 단위로 따로 집계하고 싶을 때) 4단계의 우회는
쓸 수 없다 — 이 경우 검수자가 그 Agent 결과물에 댓글을 남길 방법이
플랫폼에 아예 없다는 뜻이고, 이건 "다중 confirmed"로 못 돌아가는
근본적인 Task 간 격리 한계다(`docs/LIMITS.md` §63 "갭 2").

---

## 시나리오 4 — 일반 업무(Tool/Skill 적극 활용): "신규 입사자 원격근무
정책 문의 대응"

**배경**: 시나리오 1-3은 전부 Agent를 썼어도 `skill_tags`가
`vectordb`/`rag`/`graphdb`에 안 걸려 있어 **실제 검색 그라운딩(ADR
결정 11)을 한 번도 거치지 않았다** — 순수 LLM 호출과 다를 게 없었다.
이 시나리오는 그 갭을 메운다: Manager가 실제 사내 규정을 Agent에게
미리 학습(색인)시키고, Agent가 그 규정을 "참고 자료"로 검색해 근거
있는 답변을 만드는 과정을 끝까지 실행한다(2026-09-29, 실측 완료).

1. **브리프 제출** — `pm_kim`이 "신규 입사자 원격근무 정책 문의 급증,
   사내 규정 기반으로 정확한 답변이 필요함"을 제출.
2. **Task 분해 — Skill 태그가 핵심** — `role_name="정책 문의 응답"`,
   `required_skills=["rag","인사 정책"]`로 등록한다. `"rag"`가
   `agent_runtime.VECTORDB_SKILL_TAGS`와 일치해야 나중에 실제 검색이
   붙는다 — 이 스펠링이 시나리오 1-3엔 없었던 이유다.
3. **AI 에이전트 생성(F4, 무료 스텁)** — `POST /api/briefs/{id}/generate`
   로 이 Task 기준 Agent를 생성한다. 스텁 생성기는 `skill_tags`를
   Task의 `required_skills`에서 그대로 상속하므로(`jobs.
   default_stub_generator`), 별도 설정 없이 `skill_tags=["rag","인사
   정책"]`인 Agent가 나온다(비용 없음 — 실제 LLM 호출은 아래 6단계
   에서만 발생).
4. **온보딩 + 지식 색인(실제 비용 발생)** — Admin이 Agent를 온보딩한
   뒤, `POST /api/agent-pool/{id}/index-knowledge`로 실제 규정 텍스트
   ("원격근무는 입사 후 첫 3개월간 사용 불가, 이후 주 3일까지...")를
   색인한다 — 실제 OpenAI 임베딩 API 호출 1회.
5. **배정·승인** — pm_kim이 Agent 배정을 요청하고 admin이 승인(ADR
   3-A 자기승인 방지로 pm_kim 본인은 승인 불가 — 실측으로 403 확인).
6. **AI 실행(실제 비용 발생, 결과물 형식 지정)** — pm_kim이 "입사
   1개월차인데 재택근무 가능한가요?"를 사람 입력으로 남기고
   `execute-agent {"content_type": "html"}` 트리거(2026-10-07 추가 —
   사내 공지처럼 포맷팅된 응답이 자연스러우므로). "결과물은 완전한
   HTML 문서로 작성하세요(`<!DOCTYPE html>`부터...)" 지시가 프롬프트에
   자동으로 덧붙는다. Agent가 **색인해둔 규정을 정확히 인용해** 제목
   (`<h1>원격근무 정책 안내</h1>`)과 강조 박스를 갖춘 HTML 답변
   ("입사 후 첫 3개월 동안은 원격근무를 사용할 수 없습니다... 3개월이
   지나신 후 주 3일까지 재택근무가 가능합니다")을 만든다 — 지어낸 게
   아니라 실제 검색 결과에 근거했다는 걸 답변 내용으로 직접 확인했다.
   협업 워크스페이스에선 이 HTML이 `sandbox="allow-scripts"` iframe
   안에서 실제 포맷(색상·레이아웃)대로 미리보기된다.
7. **근거 확인(신규)** — 결재자는 이제 워크스페이스의 "참고한
   근거(Tool/Skill 검색 결과)" 접이식 블록을 열어 Agent가 실제로 뭘
   검색해 답변에 반영했는지 확인할 수 있다(`docs/LIMITS.md` §41에서
   메운 갭 — 원래는 이 정보가 실행 후 완전히 사라졌다).
8. **Gate 평가·승인·확인** — Gate G(설명가능성)가 1.0/pass로 나왔다
   (§24가 예측한 "근거를 안 요구하는 프롬프트라 낮게 나오는 경향"과
   반대 — 실제로 근거 있는 답변이라 그런 것으로 보인다). admin이
   승인하면 `has_final_result=true`로 확인 가능.

**핵심 가치**: "Tool Pool·Skill Pool"이 관리 화면의 CRUD 목록으로만
존재하는 게 아니라, 실제 업무 실행 경로(`agent_runtime.
augment_with_retrieval()`)에서 답변 품질을 실제로 바꾼다는 걸 내용
으로 증명한다 — 그리고 그 증명 과정 자체가 이전엔 실행 후 사라지는
데이터였다는 갭도 같이 드러냈다.

**갭**: 정적 프롬프트 주입 방식(§3-1)은 Agent가 검색 여부를 스스로
판단하지 못한다 — `skill_tags`에 매칭되는 태그가 있으면 무조건
검색한다(관련 없는 질문에도 검색 API를 호출해 비용 발생). 실제
CrewAI Tool 방식(`run_crew()`)처럼 Agent가 필요할 때만 호출하게
하려면 협업 워크스페이스도 CrewAI 경로로 옮겨야 하는데, 이는 이번
시나리오 스코프 밖이다.

**라이브 실행 확인(2026-10-07)**: 1-8단계 전부를 리뷰 서버에 실제 API
호출로 재현(이 환경엔 실제 LLM/임베딩 API 키가 없어 `llm.call_llm()`과
`vectorstore.embed_text()`를 pytest 골든셋과 같은 방식으로 결정적
가짜로 교체 — 비용 없이 같은 코드 경로를 검증) — Agent 생성·온보딩·
지식 색인·배정(자기승인 방지 확인)·`content_type="html"` 실행·근거
블록 생성·Gate A-G 채점(`retrieved_context` 포함)·승인까지 전부
통과했다. 워크스페이스를 Playwright로 열어 HTML이 sandboxed iframe
안에서 실제 스타일(색·레이아웃)대로 렌더링되고
`deliverable-{id}.html`로 다운로드되는 것까지 확인했다.
`docs/LIMITS.md` §69 참고.

---

## 시나리오 5 — 멀티 사용자·멀티 에이전트 팀 협업(Crew 그룹 실행): "신규 기능 런칭 발표자료 준비"

**배경**: 시나리오 1-4는 모두 "할 일 하나 = 사람 당사자 1명 또는 Agent
요청자 1명"(혼합이어도 각 역할 최대 1명)이었다. 이 시나리오는 다자간
팀 협업 기획 Phase 1-3(`docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md`)
으로 실제로 가능해진 것을 전부 쓴다 — **복수의 사람(팀)과 복수의
Agent가 한 할 일에 동시에 참여**하고, 그 Agent들은 개별로 하나씩
실행하는 게 아니라 **"팀 Agent 실행(Crew)" 한 번으로 실제 CrewAI
Crew로 함께 묶여 실행**된다(Phase 3, `docs/LIMITS.md` §64). Manager
(`pm_kim`)가 팀 리더, Member(`park_member`)가 팀원이고 Agent 2개
(카피 초안/슬라이드 디자인 가이드)가 멤버인 "런칭 캠페인팀"을 구성해
그 팀 **하나의 배정만으로** 할 일을 진행한다 — 사람 멤버도 Agent
멤버도 이 할 일에 **개별로 배정할 필요가 전혀 없다**(2026-10-06 이전
버전의 글에서 썼던 "Agent를 둘 다 따로 배정"은 Phase 3 이전의 우회
방법이었다 — 이제는 팀에 등록해두는 것만으로 충분하다. 그 우회법 자체는
여전히 유효해 아래 "대안 경로"로 남겨둔다). 산출물은 `content_type=
"markdown"`으로 지정해 둘 다 제목·목록을 갖춘 마크다운 리포트로
받는다.

1. **Agent 2개 생성 + 팀으로 번들링(F4 + §65 번들링)** — Manager가
   "AI 에이전트 생성" 화면에서 "카피 초안"·"슬라이드 디자인 가이드"용
   임시 Task 2건을 함께 선택해 무료 스텁으로 Agent 2개를
   `POST /api/briefs/{id}/generate`로 한 번에 생성한다. 생성 결과
   화면의 "체크한 Agent를 팀으로 묶기" 폼에 팀 이름 "런칭 캠페인팀"을
   입력해 제출하면 `POST /api/teams` + `POST /api/teams/{id}/members`
   (`member_type="agent"`) 두 번이 자동으로 실행돼, 새 팀이 Agent
   2개를 멤버로 둔 채 만들어진다(§65에서 추가한 "AI 에이전트 생성"
   결과 화면의 번들링 기능 — Agent를 만들자마자 바로 그룹으로 묶는
   실제 흐름).
2. **팀에 사람 멤버 추가("팀 관리" 탭)** — Admin/Manager가 Human Pool
   에서 pm_kim·park_member에 연결된 사람 레코드를 같은 "런칭
   캠페인팀"에 `member_type="human"`으로 추가한다(각각 `role_label=
   "캠페인 리드"`/`"디자인 검토"`). 이 팀은 이제 사람 2명 + Agent 2개,
   총 4명이 섞인 **재사용 가능한 자산**이다(ADR 결정 12의 "등록→재사용"
   패턴).
3. **Agent 온보딩** — Admin이 방금 생성한 Agent 2개를 온보딩해
   `active` 상태로 만든다 — "팀 Agent 실행(Crew)"은 팀의 **온보딩된
   (active) Agent 멤버만** 실행 대상으로 삼으므로(`teams.
   active_agent_member_ids()`) 이 단계를 건너뛰면 Crew 실행 대상이
   0명이 돼 `team_has_no_active_agents` 오류가 난다.
4. **브리프 제출·Task 분해** — pm_kim이 "신규 기능 X 런칭, 발표자료
   준비 — 카피 초안과 슬라이드 디자인 가이드 둘 다 필요"를 제출하고
   `role_name="런칭 발표자료 준비"` Task 1건으로 분해한다(F1-F2,
   시나리오 1-4와 동일한 수동 분해 흐름).
5. **팀 배정 요청·승인(F5-F7, 팀 리더 결재)** — pm_kim이
   `POST /api/tasks/{id}/assignments {"resource_type":"team",
   "resource_id":<런칭 캠페인팀 id>}`를 보낸다. admin이 승인하면
   **pm_kim·park_member가 즉시 이 Task의 참가자**(각각
   `team_leader`/`team_member`)가 된다 — 팀의 Agent 멤버는 **팀을
   통해서만** 이 할 일과 연결될 뿐, 개별 `agent` 배정은 하나도 만들지
   않는다(`GET /api/tasks/{id}/assignments`로 확인하면 confirmed
   배정이 `team` 1건뿐이다 — §61 "다중 confirmed agent 배정" 우회가
   더는 필요 없다).
6. **협업 워크스페이스 — 참가자들이 맥락 입력** — pm_kim이 캠페인
   목표·타겟 고객을, park_member가 디자인 톤앤매너 참고 자료를 각각
   사람 입력으로 작성해 `deliverable(author=human)`로 남긴다(팀
   리더·팀원 둘 다 참가자라 작성 가능).
7. **팀 Agent 실행(Crew) — content_type="markdown"** — pm_kim이
   협업 워크스페이스의 "팀 Agent 실행(Crew)" 카드에서 형식을
   "Markdown"으로 고르고 실행 버튼을 누른다 → `POST /api/tasks/{id}/
   execute-team-crew {"team_id":<팀 id>,"process":"sequential",
   "content_type":"markdown"}`. 팀의 온보딩된 Agent 멤버 전원(카피
   초안 Agent·디자인 가이드 Agent)이 **실제 CrewAI Crew**로 함께
   실행되고, 공유 Task 컨텍스트(역할명 + 사람 입력 2건)에 "Markdown
   형식으로 작성하세요" 지시가 자동으로 덧붙는다. 완료되면 Agent별로
   독립된 `deliverable(author=agent, content_type='markdown')` + F7
   결재 체인이 각각 생긴다 — "즉석 실행"이던 Crew가 처음으로 팀/업무
   파이프라인에 연결되는 지점(Phase 3).
8. **Gate A-G·댓글·승인** — 두 Agent 산출물 각각 자동으로 Gate
   채점되고, park_member가 카피 초안에 댓글로 피드백을 남기고(팀원도
   참가자라 댓글 가능 — role 제한 없음), pm_kim이 팀 리더로서 둘 다
   검토해 승인한다(F7 결재 체인을 Agent 산출물에도 그대로 재사용 —
   시나리오 1과 같은 메커니즘).
9. **최종 확인** — "내 업무"에서 pm_kim·park_member 둘 다 이 Task를
   본다(각각 "내 팀이 할 일(팀 리더)"/"내 팀이 할 일(팀원)"). 워크
   스페이스 상단엔 승인된 산출물이 보이지만, 아래 "갭"에서 보듯 가장
   최근 승인 1건만 고정 노출되므로 둘 다 확인하려면 "진행 이력"
   타임라인을 같이 봐야 한다.

**대안 경로(개별 Agent 배정)**: 팀 멤버로 등록하지 않고 Agent를 이
Task에 직접(`resource_type="agent"`) 배정해 `agent_id`를 지정한
`execute-agent`를 각각 호출하는 것도 여전히 유효하다(§61의 "다중
confirmed 허용") — Agent마다 provider/model이나 실행 시점을 완전히
독립적으로 제어하고 싶을 때 쓸 수 있다. 다만 여러 Agent를 **항상
함께**(하나의 Crew로) 실행하고 싶다면 위 7단계처럼 팀 멤버로 등록해
"팀 Agent 실행" 한 번으로 끝내는 쪽이 호출 수가 훨씬 적다.

**핵심 가치**: "팀"이 사람과 Agent를 동시에 담는 **그릇이자 실행
단위**가 된다 — 팀 하나를 한 번만 배정·승인하면 사람 리더·팀원이
한꺼번에 참가자가 되고, "팀 Agent 실행" 버튼 한 번으로 그 팀의 Agent
전원이 실제 CrewAI Crew로 함께 움직여 역할별 산출물(이번엔 둘 다
마크다운 리포트)을 낸다 — "복수의 사람 + 복수의 Agent가 함께 업무를
수행"한다는 HAIR의 핵심 가치가 "팀"이라는 단일 개념으로 통합돼 실제
API 호출 몇 번으로 재현된다.

**갭(정직하게)**:
- **팀 Crew 실행은 "전원 실행" 단위다** — 멤버 중 일부만 골라
  실행할 수 없다(매니저로 지정한 Agent를 산출물 없는 "조율자"로 뺄 수는
  있지만, 나머지는 전원 실행된다). 특정 Agent 하나만 먼저 돌려보고
  싶으면 위 "대안 경로"(개별 Agent 배정 + `agent_id` 지정 실행)를
  써야 한다.
- **Agent 2개를 만들려면 여전히 임시 Task가 필요하다** — "AI 에이전트
  생성"은 Task의 `role_name`에 1:1로 묶여 있어(NF7 중복 방지), 서로
  다른 전문성의 Agent 여러 개를 만들려면 역할별 임시 Task가 있어야
  한다는 전제 자체는 그대로다 — §65 번들링은 "생성 직후 바로 팀에
  추가"를 한 번에 해줄 뿐, 이 전제를 없애지는 않는다(`docs/LIMITS.md`
  §63).
- **"최종 결과물" 카드는 1건만 고정 노출한다** — 산출물이 여러 건
  승인되면(이 시나리오처럼 카피+디자인 가이드 둘 다) 가장 최근
  승인된 것만 상단에 고정되고 나머지는 "진행 이력" 타임라인을
  스크롤해야 찾을 수 있다 — N:M으로 확장된 워크스페이스 UI는 아직
  "표시 전용" 개선이 더 필요하다(`MULTI_PARTY_TEAM_COLLABORATION_
  PLAN.md` §5 Phase 4, 미구현).

**라이브 실행 중 발견해 바로 고친 버그(2026-10-07)**: 이 시나리오를
"개별 Agent 배정 없이 팀 배정 하나만"으로 재작성해 리뷰 서버에 실제로
돌려보니, 9단계("내 업무"에서 확인)에서 pm_kim·park_member 둘 다
**목록에 아예 안 떴다**. 원인은 `deliverables.list_my_work()`(`GET
/api/my-work`)가 `resource_type='team'`을 전혀 모르는 채 'human'/
'agent' 두 경우만 보던 것 — 같은 파일의 `task_participant_
relationship()`(워크스페이스 **접근 권한** 판정)은 Phase 1+2 때
이미 team_leader/team_member를 인식하도록 고쳤는데, "내 업무"
**목록** 조회는 완전히 별개의 SQL이라 그때 같이 안 고쳐졌다 —
지금까지 모든 팀 배정 시나리오(이 문서의 구 버전 포함)가 개별 사람/
Agent 배정도 같이 썼던 우연 덕에 이 갭이 가려져 있었다. `list_my_work()`
의 SQL에 team_leader_id/team_members 조건을 추가하고, `my_relationship`
값도 `team_leader`/`team_member`로 세분화했다(Web UI "내 업무" 표의
"관계" 열도 "내 팀이 할 일(팀 리더)"/"내 팀이 할 일(팀원)"으로 갱신).
`tests/unit/test_phase24_team_crew_bridge.py::test_gc249_...`로
회귀 테스트 추가. `docs/LIMITS.md` §70 참고.

**라이브 실행 확인(2026-10-07)**: 재작성한 1-9단계 전부(버그 수정
반영 후)를 리뷰 서버에 실제 API 호출로 재현 — Agent 2개 생성+번들링
→사람 멤버 추가→Agent 온보딩→브리프/Task→팀 배정·승인(confirmed
배정이 `team` 1건뿐인 것 확인)→사람 입력 2건→`execute-team-crew
{"content_type":"markdown"}`(실제 `crewai.Crew.kickoff()`를
pytest 골든셋과 같은 방식으로 결정적 가짜로 교체 — 비용 없이 같은
코드 경로 검증)→Gate A-G 채점(Agent 2건 각각)→댓글→승인→"내 업무"
(pm_kim="team_leader", park_member="team_member" 둘 다 노출)까지
통과했다. 두 Agent 산출물 모두 `content_type="markdown"`으로 저장돼
협업 워크스페이스에서 각각 독립된 마크다운 카드(제목/목록)로 렌더링
되는 것, "내 업무" 표의 "관계" 열이 "내 팀이 할 일(팀원)"으로 정확히
뜨는 것을 Playwright로 확인했다.

---

## 시나리오 6 — 멀티 provider 신규 Agent 개발: "HAIR 업무 요청으로 진행하는 게임 기획·개발"

**배경**: 시나리오 1-5는 전부 `execute-agent`/`execute-team-crew`의
`provider` 파라미터가 있는데도 실제로는 **OpenAI 하나만** 썼다(기본값
자동 판별이 `ANTHROPIC_API_KEY` 다음으로 `OPENAI_API_KEY`를 보기
때문 — `llm.resolve_provider_and_model()`). 이 시나리오는 그 갭을
메운다: pm_kim이 "HAIR 자체의 협업 구조를 게임화한 신규 웹 브라우저
게임"을 업무 요청으로 제출하고, **이 업무 전용으로 새로 만든** Agent
2개 — "게임 기획 Agent"(기획 문서 작성)·"게임 프로토타입 개발
Agent"(실제 코드 작성) — 에게 각각 **서로 다른 provider**(OpenAI
vs 로컬 Ollama의 코딩 전문 모델)를 지정해 실행한다. 결과물 형식도
업무 성격에 맞춰 기획은 `content_type="markdown"`, 코드는
`content_type="code"`로 받는다(§68 Tier 1+2 그대로 재사용).

1. **브리프 제출(F1)** — pm_kim이 "HAIR의 협업 구조(업무 요청→배정→
   Gate 평가→승인)를 가볍게 즐길 수 있는 웹 브라우저 미니게임을
   기획하고, 동작 가능한 수준의 프로토타입까지 개발이 필요함"을
   제출한다.
2. **Task 분해(F2)** — pm_kim이 2개 Task로 나눈다: `role_name="게임
   기획"`(`required_skills=["게임기획","UX 라이팅"]`), `role_name=
   "게임 프로토타입 개발"`(`required_skills=["프론트엔드",
   "JavaScript"]`).
3. **새 Agent 2개 생성(F4, 무료 스텁)** — `POST /api/briefs/{id}/
   generate`로 이 두 Task 기준 Agent를 만든다 — 기존 시나리오 1-5의
   Agent를 재사용하지 않는, **이 업무 전용 신규 Agent**다(생성
   자체는 무료 스텁이라 provider 선택과 무관 — provider는 아래
   6단계의 **실행 시점**에 지정한다).
4. **온보딩·배정·승인(F5-F7)** — admin이 Agent 2개를 온보딩한 뒤,
   pm_kim이 각 Task에 배정을 요청하고 admin이 승인한다(ADR 3-A
   자기승인 방지로 pm_kim 본인은 승인 불가).
5. **협업 워크스페이스 — 사람 입력** — pm_kim이 "플레이어는 HAIR의
   매니저 역할 — 의뢰를 사람/AI 중 누구에게 맡길지, 품질(Gate
   점수)을 보고 승인/반려할지 매 턴 선택한다. 예산과 평판이
   버티는 한 계속 진행"이라는 핵심 아이디어를 "게임 기획" Task에
   사람 입력으로 남긴다.
6. **AI 실행 — provider를 서로 다르게 지정**:
   - "게임 기획" Task → `POST /api/tasks/{id}/execute-agent
     {"content_type": "markdown", "provider": "openai", "model":
     "gpt-4o-mini"}` — 기획 문서(장르·핵심 루프·간단한 화면
     구성)를 마크다운으로 받는다.
   - "게임 프로토타입 개발" Task → 기획 산출물 요약을 사람 입력으로
     추가한 뒤 `POST /api/tasks/{id}/execute-agent {"content_type":
     "code", "provider": "ollama", "model": "qwen3-coder:latest"}`
     — 비용이 드는 OpenAI 대신, 이 머신에 이미 떠 있는 **로컬 Ollama
     코딩 전문 모델**로 실제 동작하는 HTML/JS 프로토타입 코드를
     받는다(`llm._call_ollama()`가 `OLLAMA_BASE_URL`(기본
     `http://localhost:11434`)로 직접 호출 — 네트워크 비용 없음).
7. **Gate A-G 평가 확인** — 두 Agent 산출물 각각 자동 채점.
8. **승인** — admin이 기획·코드 산출물을 각각 검토해 승인한다.
9. **최종 확인** — "내 업무"에서 pm_kim이 두 Task 모두 "최종 결과물
   승인됨"으로 확인한다 — 하나는 마크다운 기획서, 하나는 구문
   강조된 코드 블록으로 서로 다르게 렌더링된다.

**핵심 가치**: provider 선택이 Task/Agent 단위로 독립적이라, 같은
업무 안에서도 "창의적 기획은 범용 클라우드 모델, 반복적인 코드
생성은 무료 로컬 전문 모델"처럼 **작업 성격에 맞는 모델을 섞어 쓸 수
있다**는 걸 보여준다 — 이미 Task 4(시나리오 4)부터 있던 기능인데
실제로 Ollama를 선택해 실행한 적은 이번이 처음이다.

**갭**: (1) Ollama 응답 품질은 로컬에 설치된 모델 크기·양자화 수준에
전적으로 좌우된다 — 같은 프롬프트라도 OpenAI만큼의 코드 완성도를
보장하지 못한다(실측 결과는 아래 "라이브 실행 확인" 참고). (2) "AI
에이전트 생성"(F4) 단계의 provider와 "실행"(execute-agent) 단계의
provider가 분리돼 있다는 게 이번에 명확히 드러났다 — Agent 생성은
무료 스텁이고, 실제 비용/연산은 오로지 execute-agent 시점에
지정하는 provider를 따른다(기존부터 그랬지만 지금까지 시나리오들이
둘 다 OpenAI만 써서 구분이 안 보였다).

**라이브 실행 확인(2026-10-08)**: 1-9단계 전부를 실제 서버(포트
8000, 진짜 `hair.db`)에 실제 호출로 재현 — 브리프 제출→Task 분해→
새 Agent 2개 생성(id 6·7, 기존 1-5개 Agent와 완전히 별개)→온보딩→
배정·승인(자기승인 방지 확인)→사람 입력→**OpenAI(gpt-4o-mini)**로
기획 실행→**로컬 Ollama(qwen3-coder:latest, `http://localhost:11434`)**
로 코드 실행→Gate A-G 채점→승인→"내 업무" 확인까지 전부 통과했다.

- **기획 산출물**(OpenAI, markdown, 1286자): 장르·핵심 루프 4단계·
  화면 구성(상단 바/의뢰 리스트/결과 화면)·업그레이드 요소까지
  구체적으로 작성됨. Gate A(정확성) 0.85 pass, C(안전성) 1.0 pass,
  D(설명가능성) 0.638 warn, G 0.0 fail(측정 방식상 근거 인용이 없는
  창작 문서라 구조적으로 낮게 나옴 — §24에서 이미 지적된 경향과 일치).
- **코드 산출물**(로컬 Ollama, code, 6464자): 실제로 동작하는 완전한
  HTML 문서(인라인 CSS+JS, 상단 바·의뢰 리스트·버튼 이벤트 핸들러
  포함)를 생성했다 — 응답 전체가 ` ```html ` 펜스 하나로 감싸여
  있었는데 `stripOuterFence()`(§71)가 content_type="code"에도 동일하게
  적용돼 펜스 없이 깨끗한 코드 블록으로 렌더링되는 것까지 확인했다
  (fence의 언어 태그가 "html"이고 content_type은 "code"로 서로 달라도
  정규식이 언어 태그를 가리지 않고 매칭하므로 문제없이 작동). 다만
  Gate C(안전성) 0.4 fail·D(설명가능성) 0.0 fail·G 0.0 fail로 OpenAI
  쪽보다 전반적으로 낮게 나왔다 — 코드 자체는 실제로 열리는 완성된
  문서였지만, 자동 채점 기준에서 로컬 모델의 한계가 수치로 드러난
  정직한 결과다(사람이 직접 열어 확인한 "실제로 동작하는가"와 Gate
  자동 채점은 별개 — admin은 그래도 "실제 코드가 나왔다"는 점에서
  승인했다). Playwright로 두 워크스페이스를 열어 마크다운 기획서와
  구문 강조된 코드 블록이 각각 깨끗하게 렌더링되는 것을 스크린샷으로
  확인했다. `docs/LIMITS.md` §72 참고.

---

## 시나리오 7 — AI 기능 내장 게임을 팀 Crew 그룹으로 개발: "복수 사람 + 복수 Agent 협업 + 게임 안의 실시간 AI"

**배경**: 시나리오 5는 "복수의 사람 + 복수의 Agent가 한 업무를
협업"하는 구조를, 시나리오 6은 "신규 Agent + 서로 다른 LLM provider"
를 각각 보여줬다. 이 시나리오는 둘을 합치면서 한 가지를 더한다 —
**팀이 만드는 산출물(게임) 자체가 플레이 중 로컬 Ollama를 실시간으로
호출하는 AI 기능을 갖고 있어야 한다.** 즉 AI는 이번에 두 가지
역할을 동시에 맡는다: (1) 게임을 **만드는** 주체(팀 Agent 멤버),
(2) 게임 **안에서 실행되는** 기능(브라우저가 플레이 중 Ollama를
직접 호출). 사전 확인 — 이 머신의 로컬 Ollama 서버는 `curl -i
-X OPTIONS .../api/chat -H "Origin: http://127.0.0.1:8000"`로
확인한 결과 `Access-Control-Allow-Origin: http://127.0.0.1:8000`을
그대로 돌려줘, HAIR-Platform 페이지에서 직접 `fetch()`로 호출하는
게 CORS 차단 없이 가능하다(배포 환경이 바뀌면 Ollama의
`OLLAMA_ORIGINS` 설정에 따라 막힐 수 있다 — 아래 "갭" 참고).

1. **새 Agent 2개 생성(F4, 무료 스텁)** — pm_kim이 임시 Task 2건
   ("게임 UI 개발"·"AI 연동 로직 개발")을 함께 선택해 이 시나리오
   전용 신규 Agent 2개를 만든다(시나리오 6의 Agent와도, 시나리오
   5의 Agent와도 별개).
2. **팀 번들링(§65) + 사람 멤버 추가** — 생성 결과 화면에서 "체크한
   Agent를 팀으로 묶기"로 "게임 AI팀"을 만들고, "팀 관리"에서
   pm_kim(팀 리더)·park_member(팀원)를 사람 멤버로 추가한다 —
   이제 팀은 사람 2명 + Agent 2개, 총 4명이다.
3. **Agent 온보딩** — admin이 둘 다 온보딩(active)한다.
4. **브리프 제출·Task 분해** — pm_kim이 "HAIR 협업 구조를 게임화한
   웹 브라우저 미니게임에, 플레이 중 로컬 LLM이 실시간으로 의뢰
   설명·피드백 문구를 생성해주는 AI 기능을 반드시 포함해서 기획과
   동작하는 프로토타입 코드를 함께 완성해야 함"을 제출하고,
   `role_name="AI 기능 내장 미니게임 개발"`(`required_skills=
   ["프론트엔드","AI 연동"]`) Task 1건으로 분해한다.
5. **팀 배정 요청·승인(F5-F7)** — pm_kim이 "게임 AI팀"을 배정
   요청, admin이 승인하면 pm_kim·park_member 둘 다 즉시 이 Task의
   참가자(team_leader/team_member)가 된다.
6. **협업 워크스페이스 — 참가자들이 맥락 입력** — pm_kim이 게임
   목표·핵심 루프 요약을, park_member가 AI 기능의 구체 스펙("브라우저
   에서 `fetch('http://localhost:11434/api/chat', {method:'POST',
   body: JSON.stringify({model:'exaone3.5:7.8b', messages:[...],
   stream:false})})`로 로컬 Ollama를 직접 호출해 매 턴 의뢰 설명을
   실시간 생성해줘")를 각각 사람 입력으로 남긴다.
7. **팀 Agent 실행(Crew) — provider="ollama"** — pm_kim이 "팀 Agent
   실행(Crew)" 카드에서 형식을 "코드"로 고르고 실행한다 →
   `POST /api/tasks/{id}/execute-team-crew {"team_id":<게임 AI팀
   id>,"process":"sequential","content_type":"code","provider":
   "ollama","model":"qwen3-coder:latest"}`. 팀의 온보딩된 Agent
   멤버 전원(게임 UI 개발 Agent·AI 연동 로직 개발 Agent)이 실제
   CrewAI Crew로 함께 실행된다(CrewAI의 `memory=True`는 provider와
   무관하게 OpenAI 임베딩 API를 쓰므로 — `crew_runtime.run_crew()`
   자체 docstring에 이미 기록된 제약 — provider="ollama"로 호출해도
   `OPENAI_API_KEY`가 유효해야 메모리 저장이 실패하지 않는다).
8. **Gate A-G·댓글·승인** — 두 Agent 산출물 각각 자동 채점되고,
   park_member가 댓글로 피드백을 남기고, admin이 둘 다 검토해
   승인한다.
9. **최종 확인 + 실제 AI 기능 동작 검증** — "내 업무"에서 pm_kim·
   park_member 둘 다 이 Task를 확인한다. 승인된 코드 산출물 중
   하나를 실제 HTML 파일로 저장해 브라우저로 열고, AI 기능 버튼을
   눌러 로컬 Ollama 호출이 **실제로 성공하는지**까지 확인한다(아래
   "라이브 실행 확인" 참고 — 코드가 "그럴듯해 보이는 것"과 "실제
   브라우저에서 동작하는 것"은 다른 질문이라, 직접 열어보지 않으면
   확인할 수 없다).

**핵심 가치**: "복수의 사람 + 복수의 Agent"(시나리오 5)와 "신규
Agent + 멀티 provider"(시나리오 6)가 한 업무 안에서 동시에 성립할
뿐 아니라, AI가 "산출물을 만드는 주체"와 "산출물 안에서 실행되는
기능"이라는 두 층위를 동시에 맡을 수 있다는 걸 보여준다 — HAIR로
만든 게임이 HAIR 바깥에서도(브라우저에서 직접) 같은 로컬 LLM
인프라를 재사용하는 구조다.

**갭(정직하게)**:
- **팀 Crew 그룹 실행은 두 Agent에게 같은 task description을
  준다**(§71에서 이미 발견한 제약 — `run_crew_for_team()`은 Agent별
  `role_label`을 프롬프트에 반영하지 않는다). "게임 UI 개발"과 "AI
  연동 로직 개발"이라는 서로 다른 역할명을 Agent에 붙였어도, 실제
  실행에서 두 산출물이 의미 있게 다를지는 보장되지 않는다 — 실측
  결과는 아래 "라이브 실행 확인"에 정직하게 남긴다.
- **브라우저에서 로컬 Ollama를 직접 호출하는 방식은 이 머신·이
  네트워크 환경 전제다** — Ollama의 CORS는 `OLLAMA_ORIGINS`
  환경변수로 허용 출처를 제어하는데, 지금은 운 좋게 HAIR-Platform의
  origin(`http://127.0.0.1:8000`)이 허용돼 있었을 뿐이다. 다른
  환경(예: 사용자 브라우저가 다른 포트로 HAIR를 띄웠거나, Ollama가
  없는 머신)에서는 이 AI 기능이 그대로 동작하지 않는다 — 실제
  서비스라면 서버 쪽에서 프록시하는 게 맞다(이번 프로토타입은 "같은
  로컬 머신에서 실행"을 전제로 한 데모 수준).
- **API 키 없이 쓸 수 있는 건 Ollama라서지 OpenAI라서가 아니다** —
  사용자 요청의 "openai나 ollama"에서 이번엔 Ollama를 택했다(브라우저
  코드에 `OPENAI_API_KEY`를 노출하는 건 명백한 보안 사고이므로,
  게임 안에 내장하는 AI 기능은 애초에 Ollama 쪽이 유일하게 안전한
  선택이다 — 이것도 실행하며 깨달은 설계상 제약이라 기록해둔다).

**라이브 실행 확인(2026-10-08)**: 1-9단계 전부를 실제 서버(포트
8000, 진짜 `hair.db`)에 실제 호출로 재현했다.

- **팀 구성 중 실제로 막힌 지점**: 처음엔 pm_kim·park_member용 사람
  레코드를 새로 만들어 연결하려다 409("이미 다른 사람 인력(id=3)에
  연결된 계정입니다")로 막혔다 — 한 로그인 계정은 `human_pool`
  레코드 **하나에만** 연결할 수 있다는 제약을 실측으로 처음 확인했다
  (시나리오 5에서 이미 pm_kim=human_pool#3, park_member=human_pool#2
  로 연결돼 있었던 것과 충돌). 새 레코드 대신 **기존 레코드를 그대로
  재사용**해 팀 멤버로 추가하는 것으로 해결했다 — "한 사람은 조직
  전체에서 인력 레코드 하나"라는 게 HAIR의 실제 데이터 모델이라는
  뜻이다.
- **팀 Agent 실행(Crew) 소요 시간**: `provider="ollama"`, 30.5B
  파라미터 모델(`qwen3-coder:latest`)로 Agent 2개를 sequential
  실행하니 **약 6분** 걸렸다(OpenAI 1회 호출이 보통 수 초인 것과
  대조적 — 로컬 추론은 비용이 없는 대신 시간이 든다는 트레이드오프를
  그대로 보여준다).
- **두 Agent 산출물이 이번엔 실제로 달랐다**(시나리오 5·§71에서는
  완전히 동일했던 것과 다른 결과): "게임 UI 개발" Agent(153자)는
  "AI 연동 로직 개발자에게 요청한... 구현하였습니다"라고만 쓰고
  실제 코드 없이 여는 ` ``` ` 펜스로 끝나버렸다(Gate C 0.4 fail·D
  0.0 fail·G 0.0 fail) — 다른 Agent에게 "위임"하는 듯한 텍스트만
  남기고 정작 자기 몫을 만들지 않은 것으로 보인다. "AI 연동 로직
  개발" Agent(4556자)는 완전한 HTML 채팅 UI(CSS+JS 포함)를 만들었다.
  admin이 HOTL 원칙대로 전자는 **반려**(사유: "실제 코드 없이 설명만
  있음 — 재작업 필요"), 후자는 **승인**했다 — "Gate 점수가 애매하면
  무조건 승인하지 않는다"는 이 프로젝트의 리뷰 원칙을 실제 반려로도
  보여준다.
- **AI 기능이 실제로 동작하는지 끝까지 확인**: 승인된 코드를 그대로
  `.html` 파일로 저장해 로컬 정적 서버(포트 5500, Ollama의 CORS가
  `localhost`/`127.0.0.1` 계열 출처는 포트 무관하게 전부 허용하는 걸
  `curl -X OPTIONS`로 먼저 확인)로 띄우고, Playwright로 실제 브라우저
  에서 열어 입력창에 "너는 누구야?"를 치고 전송 버튼을 눌렀다 —
  **로컬 Ollama(exaone3.5:7.8b)가 실제로 응답**("저는 LG AI
  Research에서 개발한 EXAONE 3.5입니다")했고 채팅 UI에 정확히
  표시됐다(콘솔 에러 0건). AI가 만든 게임 코드 안의 AI 기능이, 실제
  AI 서버를 호출해서, 실제로 동작하는 것까지 3중으로 확인한 셈이다.

`docs/LIMITS.md` §73 참고.

---

## 공통 관찰 — 일곱 시나리오가 함께 보여주는 것

| 축 | 시나리오 1(개발) | 시나리오 2(일반 업무) | 시나리오 3(CS) | 시나리오 4(Tool/Skill) | 시나리오 5(멀티 팀) | 시나리오 6(멀티 provider) | 시나리오 7(팀+멀티 provider+내장 AI) |
|---|---|---|---|---|---|---|---|
| Agent 관여 | O(코드 패치 설명) | X(사람 LLM 보조만) | O(응답 초안) | O(정책 문의 응답) | O(Crew 2개, 각자 다른 산출물) | O(신규 Agent 2개) | O(신규 Crew 2개) |
| 결과물 형식(content_type) | code | markdown | text(기본) | html | markdown | markdown + code | code |
| LLM provider | OpenAI | OpenAI | OpenAI | OpenAI | OpenAI | **OpenAI + 로컬 Ollama** | **로컬 Ollama**(게임 안 AI 기능도 Ollama) |
| 사람 당사자 수 | 1명 | 1명 | 1명 | 1명 | **2명(팀 리더+팀원)** | 1명 | **2명(팀 리더+팀원)** |
| Gate A-G 적용 | O | X(사람 산출물) | O | O(Gate G=1.0, 근거 있는 답변) | O(Agent 산출물 2건 각각) | O(provider별로 점수 차이 실측) | O(한 건은 반려될 만큼 낮게 나옴) |
| RBAC 경계 노출 | 낮음(Manager/Member만 등장) | 낮음 | **높음**(Viewer 배제가 핵심) | 낮음 | 낮음 | 낮음 | 낮음 |
| Tool/Skill 그라운딩 | X | X | X(스킬 태그는 있으나 vectordb/graphdb 아님) | **O**(VectorDB 실제 검색) | X | X | X |
| 다자간(팀/Crew 그룹 실행) | X | X | X | X | **O**(team 1건 confirmed, Agent는 개별 배정 없이 "팀 Agent 실행(Crew)"로 전원 함께 실행) | X | **O**(시나리오 5와 동일 패턴 + 신규 Agent) |
| 승인 단계 수 | 2단계(백엔드→QA) | 1단계 | 2단계(초안→검수) | 2단계(배정→산출물) | 2단계(팀 배정 1건)+산출물 승인 2건 | 2단계(기획→개발) | 팀 배정 1건 + 산출물 승인 1건·반려 1건 |

일곱 시나리오 모두 **F1 브리프 → F2 수동 분해 → F5-F7 배정/승인 →
협업 워크스페이스 → (선택)Gate 평가 → 승인**이라는 동일한 골격을
쓴다 — HAIR의 핵심은 이 골격이 "사람만", "Agent만", "혼합", "Tool
그라운딩 있음/없음", "복수의 사람+복수의 Agent", "같은 업무 안에서
서로 다른 LLM provider 혼용", 그리고 이제 "AI가 산출물을 만드는
주체이자 그 산출물 안에서 실행되는 기능"이라는 조합까지 어느
조합에도 똑같이 적용된다는 것이다(시나리오 2의 "핵심 가치", 시나리오
5의 5·7번 단계, 시나리오 6의 6번 단계, 시나리오 7의 7·9번 단계 참고).

## 다음 액션 제안

이 문서는 기획까지다. 실제로 쓸모 있으려면:

1. 위 7개 시나리오를 `docs/USER_MANUAL.md`(챗봇 기획,
   `docs/HAIR_ASSISTANT_PLAN.md` §2)의 "예시" 섹션으로 재사용할 수
   있다 — 매뉴얼 작성 시 이 문서를 소스로 삼는 걸 권장한다.
2. 시나리오 1·3·5·6·7처럼 Agent가 관여하는 흐름은 데모/QA 시 **실제 재현
   테스트 스크립트**로 옮길 수 있다(TDD-AI 골든셋과는 별개로, "제품
   데모용 시나리오 러너" 성격) — 필요하면 별도로 논의한다.
3. 시나리오 5의 "갭"에서 드러난 "최종 결과물" 카드 1건 고정 노출
   (여러 건 승인돼도 가장 최근 것만 상단에 보임)은
   `MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` §5 Phase 4 범위다 — 팀
   기능을 실사용에 넣으려면 이 UI 작업을 먼저 하는 게 좋다.
