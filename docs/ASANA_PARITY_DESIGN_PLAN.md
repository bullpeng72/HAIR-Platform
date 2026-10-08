# HAIR-Platform — Asana급 기능·UI 확보를 위한 기획/설계안

사용자 요청(2026-10-08): "HAIR-Platform이 갖고 있는 장점을 살리면서
ASANA 모방 수준의 기능 및 UI 제공을 위한 기획 및 설계안을 제안해줘."

`docs/COLLABORATION_UX_IMPROVEMENT_PROPOSAL.md`(승인/거버넌스 UX
벤치마크)와 `docs/HAIR_REDESIGN_PROPOSAL.md`(Asana 벤치마크+정체성
질문)가 **"무엇이 다른가/어느 방향으로 갈까"**였다면, 이 문서는
**"구체적으로 뭘 어떻게 만드는가"**다 — 신규 기능마다 스키마·API·
화면까지 설계하고, 단계별 로드맵으로 묶는다. **기획/설계 문서,
코드 변경 없음.** `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md`와
같은 상세도를 목표로 한다.

## 1. 설계 원칙 — 무엇을 지키고 무엇을 더하는가

### 1-1. 반드시 지킬 HAIR의 강점(Asana에 없거나 더 약한 것)

- **Agent Pool + Gate A-G 자동 평가** — 자체 Agent를 만들고 온보딩·
  평가·오프보딩까지 생애주기로 관리한다. Asana AI Teammates는
  정성적 "체크포인트"는 있지만 HAIR 같은 수치화된 품질 게이트(A-G
  7개 축, TCR)는 없다.
- **F7 다단계 결재 체인 + 자기승인 방지(ADR 3-A)** — 조직도 기반
  승인 라우팅과 self-approval 차단은 HAIR가 이미 Asana보다 엄격하다.
- **팀 Crew 그룹 실행(CrewAI)** — 실제 멀티에이전트 프레임워크로
  여러 Agent를 한 번에 묶어 실행하는 기능은 Asana에 없다(Asana
  Teammate는 1:1 배정이 기본).
- **content_type 다양성(markdown/html/code)과 안전한 렌더링**
  (sandboxed iframe, syntax highlighting) — Asana AI Teammates의
  산출물은 Asana 자체 포맷(Task/댓글)에 묶여 있어 이 정도 형식
  다양성이 없다.
- **append-only 감사로그(NF10)** — 컴플라이언스 요구가 있는 조직에
  필요한 축.

이 문서의 모든 신규 기능은 **이 5가지를 깨지 않는 선에서** 추가한다
— 예를 들어 "대화형으로 Agent를 부르는" 기능을 추가해도, 그 결과가
실제 `deliverable`이 되는 순간부터는 지금과 똑같이 Gate 평가·F7
결재를 그대로 거친다(4-A 참고).

### 1-2. Asana에서 가져올 것(HAIR_REDESIGN_PROPOSAL §4-0~4-4 구체화)

1. 대화형 협업 레이어(초대+@멘션)
2. 작업 구조 심화(우선순위·대기열·의존관계)
3. 작업 안의 메모 공간(맥락이 작업과 분리 안 됨)
4. 운영 가시성(부하·병목)
5. 통합 활동 타임라인
6. 가벼운 온보딩

## 2. 기능 설계(F15–F20, 기존 SPEC.md F1–F14 뒤에 잇는 번호)

### F15 — Task 참가자 초대 + 댓글 대화형 실행(최우선)

**목적**: 지금은 Agent가 Task에 관여하려면 반드시 배정 요청→F7
결재 승인이라는 무거운 절차를 거쳐야 한다. "가볍게 불러서 물어보는"
경로를 따로 만든다.

**스키마 변경**:
```sql
-- 신규 테이블 — "초대"는 승인이 필요 없는 가벼운 멤버십.
-- assignments(결재 필요, 작업 책임 발생)와 명확히 분리한다.
CREATE TABLE IF NOT EXISTS task_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human', 'agent')),
    participant_id INTEGER NOT NULL,
    invited_by INTEGER NOT NULL REFERENCES users(id),
    invited_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, participant_type, participant_id)
);

-- 기존 agent_execution_jobs에 "댓글에서 트리거됐다"는 출처만 추가
ALTER TABLE agent_execution_jobs ADD COLUMN source_comment_id
    INTEGER REFERENCES deliverable_comments(id);
```
`deliverable_comments.author_type`의 CHECK 제약은 이미 `'agent'`를
허용하고 있다(`db.py:225`) — 지금까지 코드가 안 쓰고 있었을 뿐이다
(`create_comment()`가 항상 `'human'`만 삽입, `deliverables.py:402`).
이번에 그 자리를 실제로 채운다.

**API 설계**:
- `POST /api/tasks/{task_id}/participants` — `{participant_type,
  participant_id}`. **승인 불필요** — task_participant_relationship()
  판정에 "초대된 참가자"도 추가해, 이 사람/Agent가 댓글을 달 수
  있게 한다(단, deliverable 작성·실행 트리거 권한은 기존 참가자
  판정 규칙을 그대로 따른다 — 초대만으로 "작성자" 자격이 생기진
  않는다, 1-1의 원칙 유지).
- `POST /api/deliverables/{id}/comments` 확장 — 댓글 본문에
  `@에이전트이름` 패턴이 있고 그 Agent가 `task_participants`에
  있으면, 댓글 저장 직후 `BackgroundTasks`로 `start_agent_execution()`
  과 같은 경로를 **댓글 내용을 프롬프트 삼아** 트리거한다. 완료되면
  Agent가 **같은 댓글 스레드에 `author_type='agent'`로 응답 댓글을
  남긴다**(새 deliverable이 아니라 "답변"일 수도 있다는 걸 구분하기
  위해, 가벼운 질문은 댓글로만 끝내고 "결과물 제출"은 지금처럼
  별도 폼을 쓰게 한다 — 6절 열린 질문 1).
- `GET /api/tasks/{task_id}/participants` — 초대된 전체 목록.

**UI 설계**: 협업 워크스페이스 상단에 "참가자" 섹션 신규(아바타
행 + "초대" 버튼). 댓글 입력창에 `@` 입력 시 참가자 목록 자동완성
드롭다운. Agent가 응답한 댓글은 사람 댓글과 시각적으로 구분(배경색,
"AI 에이전트" 배지).

### F16 — 작업 구조 심화(우선순위·대기열·의존관계)

**스키마 변경**:
```sql
ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('backlog', 'queued', 'in_progress', 'done'));
ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'
    CHECK (priority IN ('low', 'medium', 'high', 'urgent'));

CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id),
    UNIQUE(task_id, depends_on_task_id)
);
```
순환 의존은 DB 제약으로 못 막으므로 애플리케이션 레벨에서 추가
시점에 DFS로 사이클 검사한다(Task 개수가 적어 성능 문제 없음).

**API 설계**: `PATCH /api/tasks/{id}/status`, `PATCH /api/tasks/{id}
/priority`, `POST /api/tasks/{id}/dependencies`, `GET /api/tasks/
{id}/dependencies`(의존 그래프).

**UI 설계**: 신규 "할 일 보드" 탭 — `backlog/queued/in_progress/
done` 4칼럼 칸반(드래그 없이 버튼으로 상태 이동, 단순화). 우선순위는
칸반 카드에 색 배지로 표시. 의존관계는 Task 상세에 "선행 작업" 목록
으로만 표시(그래프 시각화는 범위 밖 — Riido 수준의 그래프는 3절
로드맵 Phase 4 이후 재검토).

### F17 — Task 메모 공간

**스키마 변경**: `ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL
DEFAULT ''`. (버전 이력까지 필요하면 별도 `task_notes` 테이블로
확장 가능 — 1차는 단일 컬럼으로 충분.)

**API**: `PATCH /api/tasks/{id}/notes`.

**UI**: Task 상세 최상단에 "메모"(자유 형식 markdown, `결과물
작성`과 분리된 공간 — 제출 전 초안·배경 정보를 쌓아두는 곳).
`renderContent(notes, 'markdown')`을 그대로 재사용해 렌더링(Tier
1+2 인프라 재사용, 신규 렌더링 로직 불필요).

### F18 — 운영 가시성(부하·병목)

새 스키마 불필요 — 기존 `assignments`/`agent_execution_jobs`/
`tasks.status`(F16)만으로 집계 가능.

**API**: `GET /api/dashboard/workload`(사람/Agent별 진행 중 Task
수), `GET /api/dashboard/bottlenecks`(마감 임박 — F16의 상태/우선순위
도입 후 의미 생김 — 또는 반려 2회 이상 누적된 Task).

**UI**: 대시보드에 "팀 부하" 바 차트, "주의 필요" 목록 위젯 추가.

### F19 — Task 단위 통합 활동 타임라인

새 스키마 불필요 — `audit_log`가 이미 모든 액션(배정/실행/승인/
댓글 등)을 기록 중이다. 지금까지 Task 단위로 필터링해 보여주는
조회 경로만 없었다.

**API**: `GET /api/tasks/{task_id}/activity` — `audit_log`에서
`subject_id`가 이 task_id와 연관된(직접 또는 `assignments`/
`deliverables` 경유) 행을 시간순으로.

**UI**: Task 상세에 "활동" 탭(지금의 "진행 이력"과는 다른 축 —
"진행 이력"은 deliverable 중심, "활동"은 배정·승인·댓글까지
포함한 전체 이벤트 로그).

### F20 — 온보딩 간소화

**코드 변경(스키마 없음)**: `POST /api/demo/seed`(admin 전용) —
샘플 Brief 1건+Task 2건+Agent 1개+Human 1명을 한 번에 생성해
즉시 체험 가능한 상태로 만든다. 기존 "데이터 삭제" 기능(§19)과
대칭.

**UI**: 로그인 첫 화면에 "샘플로 체험하기" 버튼. 탭 재구성 —
`webui.py:575`의 `TABS` 배열을 "오늘 할 일"(인박스·내 업무·할 일
보드·결재) / "설정"(Pool 5종·Team·Model Registry 등, 자주 안
바뀌는 것들)으로 2구역화해 신규 사용자가 매일 보는 화면을 좁힌다.

## 3. 로드맵(의존관계 순서대로)

| Phase | 포함 기능 | 이유 |
|---|---|---|
| **1(즉시)** | F17(메모), F19(활동 타임라인) | 새 스키마가 거의/전혀 없고 기존 인프라(renderContent, audit_log) 재사용 — 가장 안전 |
| **2** | F15(초대 — 결재 없는 가벼운 참가자 추가까지만) | `task_participants` 테이블 하나, `task_participant_relationship()` 판정 로직 확장만 필요 |
| **3** | F15(댓글 @멘션 자동 트리거) | Phase 2의 초대 기능 위에 얹는다 — 체감 효과가 가장 크지만 "결과물 vs 단순 답변" 구분(6절 질문 1) 선결 필요 |
| **4** | F16(상태/우선순위/의존관계 + 할 일 보드), F18(운영 가시성) | 가장 큰 스키마 변경, Task 모델 자체를 바꾸는 일이라 충분히 검증 후 진행 |
| **5** | F20(온보딩/탭 재구성) | 다른 기능이 쌓인 뒤에 전체 정보 구조를 다시 짜는 게 효율적 |

## 4. 화면 설계 요약

| 화면 | 상태 | 변경 내용 |
|---|---|---|
| 협업 워크스페이스 | 기존 | "참가자" 섹션 신규(F15), "메모" 블록 신규(F17), "활동" 탭 신규(F19) |
| 할 일 보드 | **신규** | F16 — 상태별 칸반 4칼럼 |
| 대시보드 | 기존 | "팀 부하"·"주의 필요" 위젯 추가(F18) |
| 로그인 직후 첫 화면 | 기존 | "샘플로 체험하기" 버튼(F20) |
| 탭 전체 구조 | 기존 | "오늘 할 일"/"설정" 2구역 재편(F20) |

## 5. 리스크

- **F15의 범위 설정이 가장 민감하다** — 댓글 대화가 너무 쉽게
  deliverable을 만들어버리면 F7 결재 체인을 우회하는 뒷문이 될 수
  있다. "질문/답변"과 "정식 산출물 제출"을 명확히 구분해야 한다
  (6절 질문 1).
- **F16은 기존 `tasks` 스키마에 CHECK 제약 3개를 더하는 것**이라
  §13(스키마 마이그레이션) 관례대로 `_ensure_column()`으로 기존
  DB에도 안전하게 적용 가능 — 기술적 리스크는 낮다.
- **F20의 탭 재구성은 기존 사용자의 습관을 깨는 변경**이라 가장
  마지막(Phase 5)으로 미뤘다.

## 6. 다음 액션 제안(열린 질문)

1. **F15 — "질문/답변"과 "정식 제출"을 어떻게 구분할까**: (a) 댓글
   트리거 결과는 항상 댓글로만 남기고 별도 "이걸 결과물로 제출"
   버튼을 눌러야 deliverable이 되게 할지, (b) 댓글에서도 바로
   deliverable이 생기되 Gate/승인 체인은 그대로 타게 할지.
2. **로드맵 진행 순서**: 위 표 그대로 Phase 1→5 순서로 갈지, 체감
   효과가 큰 F15(Phase 2-3)를 F17/F19보다 먼저 할지.
3. **F16의 의존관계 시각화 수준**: 1차는 "선행 작업 목록"으로
   충분한지, 아니면 Riido 수준의 그래프 시각화까지 바로 갈지.

이 중 진행할 Phase/기능을 알려주면 그 범위부터 실제 코드로 구현한다.

## 7. 결정 및 구현 현황(2026-10-08)

사용자 결정:
1. **F15 질문**: **(a)** 댓글 트리거 결과는 항상 댓글로만 남기고,
   사람이 "이걸 결과물로 제출" 버튼을 눌러야 비로소 deliverable이
   되고 F7 결재 체인을 탄다 — Gate/승인 우회 뒷문 리스크를 구조적
   으로 차단하는 선택.
2. **로드맵 순서**: 표(3절)의 Phase 1→5 순서 그대로 진행.
3. **F16 의존관계 시각화**: 1차 버전(선행 작업 목록)이 아니라
   **Riido 수준의 그래프 시각화**로 바로 간다 — Phase 4의 범위가
   이 결정으로 커졌다.

구현 진행 상황은 각 Phase 항목 옆에 표시한다(진행하며 갱신):
- [x] Phase 1 — F17(메모), F19(활동 타임라인) — 완료(2026-10-08). `tasks.notes`
  컬럼 + `GET/PATCH /api/tasks/{id}`·`/notes`(참가자 전용, `create_comment()`와
  같은 권한 판정 재사용), `GET /api/tasks/{id}/activity`(`audit_log`를
  subject_type 6갈래로 `UNION ALL`해 task_id로 역추적 — `approval`은
  `approvals.subject_type`으로 한 번 더 분기, `team_crew_execution_started`는
  `subject_id=None`이라 `json_extract(after_value,'$.task_id')`로 별도 처리).
  단위 테스트 11건 추가(`tests/unit/test_phase26_asana_parity_phase1.py`,
  전체 스위트 그대로 통과), 실서버(port 8000, 실제 hair.db)에 라이브
  배포해 Playwright로 검증 — 메모 저장/조회, task별로 활동이 섞이지
  않는지, 비참가자 403까지 전부 실측 확인.
- [x] Phase 2 — F15 초대(결재 없는 가벼운 참가자 추가) — 완료(2026-10-08).
  `task_participants` 테이블(새 테이블이라 `_ensure_column()` 불필요) +
  `POST/GET /api/tasks/{id}/participants`(초대 자체는 admin/manager 또는
  이미 댓글 가능한 사람만 — 완전한 외부인의 무단 초대는 막음) +
  `can_comment_on_task()`(정식 참가자 판정 OR 초대 여부, `create_comment()`
  /댓글 조회에 적용 — 결과물 작성·실행 트리거는 여전히 기존의 엄격한
  `task_participant_relationship()`만 통과). 참가자 ID는 `human_pool.id`가
  아니라 `users.id`/`agent_pool.id`(초대는 리소스 매칭이 아니라 "이
  계정을 대화에 부른다"는 개념이라 human_pool을 거치지 않음). webui.py에
  "참가자" 카드(목록 + 초대 폼) 추가. 신규 테스트 8건(`tests/unit/
  test_phase27_asana_parity_phase2.py`, "초대된 사람이 댓글은 가능하되
  결과물 작성은 여전히 금지"되는 경계를 명시적으로 확인). 실서버 라이브
  배포 + Playwright로 참가자 카드 렌더링·초대 플로우 실측 확인.
- [x] Phase 3 — F15 댓글 `@멘션` 트리거 — 완료(2026-10-08). 댓글 본문에
  `@역할명`이 있고 그 Agent가 `task_participants`에 초대돼 있으면
  `create_comment()` 직후 `BackgroundTasks`로 가볍게 실행(결재·Gate·
  deliverable 생성 전부 생략 — `agent_execution_jobs.source_comment_id`
  로 "버튼 실행"과 구분). 응답은 **항상** 같은 댓글 스레드에
  `author_type='agent'`로만 남는다(결정 1-(a) 그대로 구현 — `deliverables`
  테이블엔 아무 행도 안 생김을 테스트로 직접 확인). "이걸 결과물로
  제출"은 **새 엔드포인트 없이** 구현했다 — 버튼이 그 댓글 내용을 기존
  "결과물 작성" textarea에 채워 넣기만 하고, 실제 제출은 기존
  `ws-submit-form`(기존 F7 체인)을 그대로 탄다. 신규 테스트 6건
  (`tests/unit/test_phase28_asana_parity_phase3.py` — 뒷문 차단
  검증이 핵심). 실서버에서 **실제 로컬 Ollama(qwen3-coder:latest, 무료)
  Agent**를 초대해 멘션으로 호출, 진짜 응답이 댓글로 돌아오고
  deliverable은 하나도 안 늘어난 것까지 라이브로 확인(비용 0원).
- [x] Phase 4 — F16+F18 — 완료(2026-10-08). `tasks.status`/`priority` +
  `task_dependencies` 테이블(자기참조는 CHECK, 간접 순환은 `_would_
  create_cycle()` DFS로 차단) + `PATCH .../status`·`/priority`·
  `POST/DELETE/GET .../dependencies`. **그래프 시각화는 외부 라이브러리
  없이 순수 SVG**로 구현(depth 기반 레이어 레이아웃, 우선순위별 테두리
  색, 노드 클릭 시 그 업무로 이동) — "할 일 보드" 탭(4칼럼 칸반, select
  기반 상태 이동)도 함께 구현. F18은 `GET /api/dashboard/workload`·
  `/bottlenecks`(새 계측 없음 — 마감일 개념이 없어 "마감 임박"은 범위
  밖, 대신 "선행 작업 막힘"+"결과물 2회 이상 반려"로 "주의 필요" 구현)
  를 대시보드에 바 차트/목록 위젯으로 추가. 신규 테스트 14건(`tests/
  unit/test_phase29_asana_parity_phase4.py`). 실서버 라이브 검증 중
  `workload()`가 team 배정을 집계 못 하는 비대칭 갭을 발견해
  `docs/LIMITS.md` §77에 정직하게 기록(다음 리뷰 과제로 남김).
- [x] Phase 5 — F20(온보딩/탭 재구성) — 완료(2026-10-08). **마지막
  단계 — Phase 1→5 전부 완료.** `POST /api/demo/seed`(admin 전용 —
  샘플 Brief 1건+Task 2건+Human 1명+Agent 1개를 결재 전 상태로 생성,
  기존 "데이터 삭제"와 대칭) + 인박스가 비어 있을 때 admin에게만
  보이는 "샘플로 체험하기" 버튼. 탭 재구성은 기존 3구역("내 업무"/
  "공용 조회"/"운영 관리")을 "오늘 할 일"/"설정" 2구역으로 렌더링
  단계에서 병합(`TABS` 배열 자체의 group 값은 그대로 — "user"+
  "common"→"오늘 할 일", "admin"→"설정"). 설계 문서가 예시로 든
  "인박스·내 업무·할 일 보드·결재" 4개만 남기는 더 좁은 해석도
  있었지만, "업무 요청"처럼 매일 쓰는 화면을 "설정"에 숨기면 더
  불편해질 위험이 커서 "admin 전용 여부" 기준으로 묶었다(`docs/
  LIMITS.md` §78에 이 해석 기록). 기존 Phase 11 회귀 테스트(`test_
  phase11_admin_user_nav_split.py`의 `test_gc73`)를 새 구조에 맞게
  갱신 + 신규 테스트 4건(`tests/unit/test_phase30_asana_parity_
  phase5.py`). 실서버 라이브 검증 — "샘플로 체험하기" 클릭 → 샘플
  데이터 생성 → "업무 요청" 탭 자동 이동까지 전체 흐름 확인.

## 8. 구현 완결성 감사(2026-10-08)

사용자 요청("...구현에 누락 부분이 없는지 확인해줘")으로 §2(F15-F20)
전체를 코드와 1:1 대조했다. API·스키마·감사로그는 전부 존재(상세는
`docs/LIMITS.md` §79). 실제 누락 1건 발견 후 즉시 보완, 의도된
UI 단순화 2건은 사용자 결정으로 기록만 남기고 코드는 유지한다.

- **보완함**: F15 UI 설계의 "@ 입력 시 참가자 목록 자동완성
  드롭다운" — 1차 구현 땐 정적 힌트 텍스트로 대체돼 있었다.
  `setupMentionAutocomplete()`로 실제 구현 완료, 실서버+Playwright로
  검증.
- **기록만 남김(§79)**: "할 일 보드"가 조직 전체가 아니라 "내
  업무" 범위로 좁음 / F19 "활동"이 탭이 아니라 접이식 섹션 / 참가자
  UI가 아바타가 아니라 텍스트 목록(순수 코스메틱).

## 9. 대화 중심 작업공간(Conversational Workspace) — 확장 제안(2026-10-08)

**경과**: 1차 요청("Asana처럼 대화창에서...")에 대한 1차 답으로 아래
9-1의 구조적 한계 분석과 "Task에 종속된 대화 스레드(F21-1, `task_
messages`)"를 제안했다. **사용자가 이어서 더 근본적인 방향을
지시**했다: "현재 방식(Brief→팀 구성→확정→수행)은 유연성/Agility가
부족하다. 사용자가 로그인하면 기본적으로 대화창이 주어지고, 거기서
다른 사용자나 에이전트(에이전트 그룹)를 호출하거나 skill을 사용해
업무를 수행하는 방식으로 UX를 개선했으면 한다. 기존 방식과 공존
(업무 성격에 따라 선택)하는 방향도 좋다." 이 절은 그 지시를 반영해
**Task 종속 여부와 무관한, 로그인 시 바로 쓸 수 있는 독립된 "대화"
엔티티**로 설계를 확장한다 — F21-1의 `task_messages`는 아래 설계의
특수 케이스로 흡수되므로 별도로 만들지 않는다(9-4 참고).

### 9-1. 현재 상태 점검(확인) — 기존 구조의 한계

1. **무거운 선행 경로가 유일한 입구다.** 지금 Agent/사람이 업무에
   관여하려면 Brief 제출 → Task 분해 → 배정 요청 → F7 결재 승인
   (확정) → 실행이라는 순서를 반드시 거친다. 빠른 질문 하나, 가벼운
   검토 요청에도 이 전체 절차가 필요해 Agility가 떨어진다(사용자
   지적 그대로).
2. **F15(Phase 1-3)로 만든 Task 참가자 초대+댓글 @멘션도 Task
   존재가 전제다.** 그리고 결과물이 0건이면 댓글창 자체가 안 뜬다
   (`webui.py:1022,1093`) — Task를 막 만들고 참가자를 초대해도
   누군가 먼저 "결과물 작성"을 해야 대화가 열린다.
3. **@멘션 트리거가 대화 맥락을 안 본다** — 직전 메시지 한 줄만
   프롬프트로 쓰여(`app.py:1267-1288`) 멀티턴 대화가 자연스럽게
   이어지지 않는다.
4. **초대 안 된 이름을 멘션해도 조용히 무시된다**(`deliverables.py:494`
   `detect_mentioned_agents()`가 이미 초대된 Agent만 본다) — "대화
   하다가 자연스럽게 끌어들인다"는 경험과 거리가 멀다.
5. **대화창이 실시간 갱신 안 됨** — 수동 새로고침 필요.
6. **"에이전트 그룹(Team)"을 가볍게 부르는 경로가 아예 없다** — Team
   Crew 실행(`start_team_crew_execution()`)은 그 Task에 **팀이
   confirmed 배정돼 있어야만** 호출 가능하다(`deliverables.py:1036`)
   — 가벼운 대화 중에 "이 팀 불러서 같이 봐줘"가 안 된다.

(§8/§79에서 이미 정리한 "@ 자동완성 미구현"은 보완 완료 — 범위 제외.)

### 9-2. 핵심 설계 — `conversations` : Task와 독립적인 대화 엔티티

```sql
-- "대화"는 Task가 있어야만 존재하는 게 아니다 — 로그인한 사용자가
-- 아무 때나 새로 열 수 있는 독립된 공간이다. linked_task_id를 두면
-- "이 Task 전용 대화"(F21-1이 원래 노리던 것)도 같은 테이블로
-- 표현된다 — 별도 task_messages 테이블을 만들 필요가 없어진다.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    linked_task_id INTEGER REFERENCES tasks(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- task_participants(F15)와 같은 모양이지만 participant_type에
-- 'team'(에이전트 그룹)을 추가한다 — "에이전트 그룹을 호출"을
-- 표현하려면 꼭 필요하다(task_participants는 F15 범위상 human/agent
-- 둘뿐이었다).
CREATE TABLE IF NOT EXISTS conversation_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    participant_type TEXT NOT NULL CHECK (participant_type IN ('human', 'agent', 'team')),
    participant_id INTEGER NOT NULL,
    invited_by INTEGER NOT NULL REFERENCES users(id),
    invited_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(conversation_id, participant_type, participant_id)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```
생성자(`created_by`)는 자동으로 첫 참가자. `linked_task_id`가
채워지면 그 Task 워크스페이스 안의 "대화" 섹션이 되고, `NULL`이면
완전히 독립된 "내 대화"(로그인 직후 랜딩)가 된다 — **하나의 모델로
두 유스케이스를 다 커버**한다.

**API**:
- `POST /api/conversations` — `{title?, linked_task_id?}`, 생성자
  자동 참가.
- `GET /api/conversations` — 내가 참가 중인 대화 목록(최근순).
- `GET/POST /api/conversations/{id}/messages`, `GET/POST /api/
  conversations/{id}/participants` — F15의 `can_comment_on_task()`
  /`detect_mentioned_agents()`/`start_comment_triggered_execution()`
  을 `task_id` 대신 `conversation_id` 기준으로 일반화한 버전을
  그대로 재사용한다(세 함수 모두 이미 특정 task_id를 받는 구조라
  매개변수 교체 수준의 작업 — 중복 구현 아님).
- `POST /api/conversations/{id}/promote-to-brief` — 대화를 정식
  업무 요청(Brief)으로 승격(9-3 "공존" 경로). 기존 `POST /api/briefs`
  를 그대로 호출 — 새 생성 로직을 만들지 않는다.

**Agent 그룹(Team) 호출**: 멘션 대상이 `participant_type='team'`
이면, `crew_runtime.run_crew_for_team()`(`deliverables.py:1134`에서
이미 `run_team_crew_job()`이 호출하는 바로 그 저수준 함수)을 **배정
확정 여부 확인 없이** 직접 호출한다 — F15 Phase 3가 `start_agent_
execution()`(확정 배정 필요) 대신 `agent_runtime.execute_agent()`
(확정 불필요)를 썼던 것과 완전히 같은 패턴. 결과는 Gate 평가·
`deliverables` 생성·F7 체인 없이, 참여 Agent별로(또는 매니저 조율
결과 하나로 — 9-3 열린 질문 2) `conversation_messages`에
`author_type='agent'`로만 쌓인다.

**Skill 사용**: 기존 `assist()`/`_skill_grounded_system_prompt()`
(Task 워크스페이스의 "LLM으로 도와줘")를 대화에도 그대로 노출한다
— 메시지 작성 시 skill_pool에서 하나를 골라 "이 스킬로 보강된 AI
응답"을 받는 보조 액션. 새 그라운딩 메커니즘을 만들지 않는다(9-3
열린 질문 3 — 사용자가 말한 "skill 사용"이 이 의미가 맞는지 확인
필요).

**UI**:
- 로그인 직후 "오늘 할 일" 탭군 맨 앞에 "대화"(홈) 탭 신설 — 내
  대화 목록 + "새 대화 시작" 버튼. 빈 상태면 바로 새 대화를 열 수
  있게 한다(브리프/태스크 없이).
- 대화 화면: F15/F21-1에서 이미 만든 chat-human/chat-agent 카드
  스타일, `@` 자동완성(`setupMentionAutocomplete()`), 참가자 초대
  폼(F15 "참가자" 카드 확장 — human/agent/**team** 선택 가능하게)을
  그대로 재사용.
- "정식 업무 요청으로 전환" 버튼 — 대화가 무거워지면 Brief로 승격
  (9-3 "공존" 경로 1).
- Task 워크스페이스의 "대화" 섹션(F21-1 당초 설계)은 `linked_task_id`
  가 채워진 대화 하나를 그 자리에 그대로 임베드하는 것으로 구현한다
  — **완전히 같은 컴포넌트**를 두 자리(독립 탭/Task 워크스페이스
  내부)에서 재사용.

### 9-3. "기존 방식과 공존" — 두 경로의 연결 지점

사용자가 "업무 성격에 따라 선택"을 언급했다 — 설계는 **강제 분기가
아니라 승격 경로**로 구현한다(어느 쪽이 "맞는" 방식인지 시스템이
미리 정하지 않는다):
- **가벼운 질문/검토 → 대화만으로 끝**: `conversations`에서 시작해
  필요한 사람/Agent/Team을 불러 끝낸다. Gate/F7을 전혀 안 거친다.
- **대화가 커지면 → Brief로 승격**: "정식 업무 요청으로 전환" 버튼
  으로 지금까지의 대화를 `raw_text`로 넘겨 기존 Brief 제출 흐름에
  태운다 — 이후론 기존 Task 분해·배정·승인 파이프라인 그대로.
- **이미 Task가 있는 작업 → Task에 종속된 대화**(F21-1): Task
  워크스페이스 안에서 가볍게 주고받다가 "결과물로 제출" 버튼으로
  정식 deliverable(F7 체인)로 승격 — 이미 설계된 경로 그대로.

이 세 경로 전부 "격식 있는 파이프라인 진입은 사람이 명시적으로
눌러야 한다"는 원칙(1-1, F15 Phase 3 결정 a)을 공유한다 — 대화
자체는 어떤 경우에도 자동으로 Gate/승인을 우회해 정식 결과물이
되지 않는다.

### 9-4. F21-1(`task_messages`)과의 관계

1차안에서 제안했던 `task_messages` 테이블은 **별도로 만들지 않고
`conversations`(+`linked_task_id`)로 흡수한다** — 두 테이블을 같이
만들면 "Task 종속 대화"가 두 가지 메커니즘으로 중복 구현된다. F21-1
에서 이미 확인한 재사용 가능성(`can_comment_on_task()`/`detect_
mentioned_agents()`/`start_comment_triggered_execution()`이 모두
task_id 기반)은 그대로 `conversation_id` 기준으로 옮겨 재사용한다.

F21-2(대화 맥락 포함)·F21-4(경량 폴링)는 이번 확장에도 그대로
유효한 하위 과제로 유지한다 — 번호만 아래 로드맵에 재배치.

### 9-5. 로드맵 제안

| 단계 | 범위 |
|---|---|
| 1 | `conversations`/`conversation_participants`(human·agent만)/`conversation_messages` 스키마 + 기본 CRUD + 메시지 작성(멘션 없이 사람 참가자끼리 대화만) |
| 2 | 단일 Agent 멘션 트리거(F15 Phase 3 로직 재사용) + Task 워크스페이스의 "대화" 섹션(linked_task_id 활용, F21-1 당초 목표 달성) |
| 3 | Team(에이전트 그룹) 멘션 호출(`run_crew_for_team()` 재사용) |
| 4 | "정식 업무 요청으로 전환" 승격 버튼, 로그인 랜딩을 "대화" 탭으로 |
| 5 | 대화 맥락 포함(구 F21-2), 경량 폴링(구 F21-4), 미초대 대상 멘션 시 초대 플로우(구 F21-3) |

### 9-6. 열린 질문

1. **1인당 대화가 여러 개(주제별)인지, 기본 1개(항상 같은 방)인지**
   — Slack/Asana류는 여러 개가 일반적이라 "새 대화 시작" 허용을
   추천하지만 확인 필요.
2. **Team 호출 시 워커 Agent별 메시지를 각각 올릴지, 매니저가 합친
   응답 하나만 올릴지** — 기존 Team Crew 실행(Task 워크스페이스)은
   "Agent별 deliverable 여러 개" 방식이었다. 대화에서도 그대로
   가져오면 대화가 시끄러워질 수 있다.
3. **"skill을 사용하여"의 정확한 의미** — (a) 메시지에 스킬을 명시
   적으로 붙여 AI 응답을 보강하는 것(기존 assist 메커니즘)인지,
   (b) `/명령어`처럼 스킬 자체를 직접 실행하는 별도 트리거인지.
   설계는 1차로 (a)를 가정했다.
4. **미초대 대상 멘션 시 검색 범위** — 조직 전체 vs 같은 Brief/
   Task 범위로 좁힐지(구 F21-3).
5. **로드맵 범위** — 9-5의 어디까지 이번에 진행할지(1단계만 먼저
   확인하고 싶을 수도 있다).

이 중 방향을 확인해주면 그 범위부터 실제 코드로 구현한다.
