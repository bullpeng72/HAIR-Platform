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
- [ ] Phase 4 — F16(상태/우선순위/의존관계 + 할 일 보드 + **그래프
  시각화**), F18(운영 가시성)
- [ ] Phase 5 — F20(온보딩/탭 재구성)
