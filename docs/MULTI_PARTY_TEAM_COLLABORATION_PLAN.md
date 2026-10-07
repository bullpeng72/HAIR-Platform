# 다자간 팀 협업(N명 사람 + M개 Agent/Agent 그룹) 기획안

사용자 요청(2026-10-06): "사람들과 Agent들이 협업으로 기업의 업무를
수행하도록 하는 것이 HAIR Platform의 핵심이야. 복수의 사람이 복수의
Agent와 또는 CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과
협업하도록 하는 실제 업무 적용이 가능한 플랫폼이 되도록 HAIR Platform을
다시 기획하고, (구조적 변경, Process, UI 변경 포함 가능) 개선 계획을
수립해줘" — 직전 분석(같은 세션, "Crew 실행이 독립 실행인지" 질문)에서
실측으로 확인한 두 가지 구조적 사실이 이 기획의 출발점이다.

**이 문서는 기획안이며 아직 구현하지 않았다.** `docs/COLLABORATION_
PLATFORM_PLAN.md`(Phase A-C, 1인 사람+1인 Agent 협업을 완성한 기존
기획)의 후속 — 이번엔 "여러 명"으로 확장한다.

## 1. 실측으로 확인한 현재 구조의 한계

| # | 한계 | 근거(파일:함수) |
|---|---|---|
| L1 | 한 할 일(task)에 같은 resource_type(사람 또는 Agent)이 동시에 2명 이상 `confirmed`일 수 없다 — 두 번째가 확정되면 첫 번째가 자동으로 `superseded`된다 | `assignments.resolve_assignment_decision()` — "같은 task·같은 resource_type에 이미 confirmed인 배정이 있으면 superseded로 전환" |
| L2 | "Crew 실행"(`run_crew()`, 여러 Agent를 역할별로 묶어 실제 협업시키는 기능)은 `task_id`를 받지 않는다 — `tasks`/`assignments`/`deliverables` 어디에도 연결되지 않고, 결과는 `agent_pool.last_output`만 갱신한 채 끝난다 | `CrewRunRequest`(`models.py:244`, `task_descriptions: list[str]` 자유 텍스트, `task_id` 필드 없음), `run_crew()` 본문에 `deliverables`/`assignments` 참조 0건 |
| L3 | "역할별로 묶인 Agent 그룹"을 저장·재사용할 방법이 없다 — Crew 실행 때마다 매번 Agent ID를 다시 골라야 한다(§53/§56에서 드롭다운으로 편의는 개선했지만 "그룹 자체"를 저장하진 않음) | `agent_pool`에 그룹 개념 없음, `model_registry`(§59)는 모델이지 팀이 아님 |
| L4 | CrewAI의 `allow_delegation`/`Crew.kickoff()`는 **동기 프로세스 내부 호출**이다 — 실행 도중 사람이 끼어들 지점이 설계상 없다(Phase C에서 조사해 기각한 사실, `COLLABORATION_PLATFORM_PLAN.md:285-325`) | 실측 조사 결과, `register_before_tool_call_hook`만 유일한 개입 지점인데 **미구현 상태로 남음** |

**결론**: 지금 플랫폼은 "1 사람 **또는** 1 Agent, 할 일당 각 1명"짜리
협업만 실제로 완성돼 있다(§2, Phase 1-4). "복수의 사람 + 복수의 Agent
(또는 역할 기반 Agent 그룹)"은 UI·데이터 모델 어디에도 길이 없다 —
이번 기획의 목표는 이 간극을 메우는 것이다.

## 2. 목표 — 무엇이 가능해져야 하는가

1. 관리자가 **"팀"**(사람 N명 + Agent M명, 각자 역할명 포함)을 한 번
   구성해두고 **여러 업무에 재사용**할 수 있다 — CrewAI의 "역할별
   Agent 구성" 장점을 1회성이 아니라 조직 자산으로 만든다.
2. 할 일 하나에 **팀 전체**(또는 여러 사람 + 여러 Agent를 개별적으로)를
   배정할 수 있다 — L1을 없앤다.
3. 팀에 속한 Agent 멤버들이 **실제로 CrewAI Crew로 협업 실행**되고
   (sequential/hierarchical, 실제 위임 캡처 포함, §57), 그 결과가
   **그 할 일의 협업 워크스페이스에 deliverable로 쌓인다** — L2를
   없앤다.
4. 사람 팀원은 비동기로(동시 실시간 아님 — L4는 기술적 한계로 그대로
   인정) 결과물을 보고 댓글·수정 요청·승인을 할 수 있다 — 지금의
   1:1 HITL/HOTL 패턴(Phase 2 승인 게이트, 댓글)을 N:M으로 확장한다.

## 3. 목표 아키텍처

### 3-1. 구조적 변경 — 신규 테이블 2개

```sql
CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- "SDLC 백엔드팀" 등
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    member_type TEXT NOT NULL CHECK (member_type IN ('human', 'agent')),
    member_id INTEGER NOT NULL,      -- human_pool.id 또는 agent_pool.id
    role_label TEXT NOT NULL DEFAULT '',  -- 이 팀 안에서의 역할명(선택)
    UNIQUE(team_id, member_type, member_id)
);
```

`skill_pool`/`tool_pool`과 같은 "등록 → 재사용" 패턴이다 — 완전히 새로운
개념이 아니라 이미 이 코드베이스에 세 번 검증된 패턴을 네 번째로
적용하는 것. 사람/Agent 풀 자체는 손대지 않는다(참조만 한다).

### 3-2. `assignments` 확장 — L1 해소

- `resource_type`에 `'team'` 추가(`resource_id` → `teams.id`). 팀을
  배정하면 팀 소속 사람·Agent 전원이 그 task의 정당한 참가자가 된다
  (`task_participant_relationship()`은 이미 루프로 "모든 confirmed
  배정"을 순회하므로 — 팀 배정을 멤버별 confirmed 사람/Agent 배정으로
  "전개"해서 넣으면 **기존 함수 수정이 거의 필요 없다**, §본문 L1 분석
  참고).
- 같은 task에 같은 resource_type이 여러 건 confirmed로 공존하는 걸
  허용하는 옵션을 `resolve_assignment_decision()`에 추가한다(팀
  배정이거나, 관리자가 명시적으로 "다중 확정" 모드를 쓸 때만 — 기존
  "재배치하면 이전 건 superseded" 동작은 **단독 배정의 기본값으로
  그대로 유지**해 회귀를 막는다).

### 3-3. `run_crew()`에 `task_id` 다리 놓기 — L2 해소

- `CrewRunRequest`에 `task_id: int | None = None` 추가(옵션 — 기존
  "즉석 실행" 용도는 그대로 둔다, 하위 호환).
- `task_id`가 있으면: 실행 종료 후 **Agent 멤버별로** `deliverables`에
  `author_type='agent'` 행을 생성(§57에서 이미 "Agent별 산출물"로
  분리돼 있는 `per_agent` 리스트를 그대로 매핑). 매니저 Agent(산출물
  없음)는 제외. `agent_evaluations`에 Gate A-G/TCR(이미 `run_and_score_
  crew()`가 계산 중인 값, 버리고 있었을 뿐)도 같이 기록 — **새 채점
  로직 불필요, 저장 경로만 추가**.
- 이러면 "할 일 상세"(§55, 옵션 C로 이미 구현한 패널)·"내 업무"·협업
  워크스페이스 타임라인에 팀의 Agent 멤버들이 각자 만든 산출물이
  자동으로 나타난다 — 새 UI를 또 안 만들어도 된다.

### 3-4. 사람 팀원의 비동기 참여 — L4를 인정한 위에서의 설계

CrewAI `kickoff()` 도중 실시간으로 사람이 끼어드는 건(L4) 여전히 안
한다(Phase C에서 이미 기각된 길 — 재검토하지 않는다). 대신:

1. 사람 팀원이 먼저 draft를 작성(`author_type='human'`, 기존 그대로).
2. 관리자/팀 리더가 "팀의 Agent 멤버 실행"을 누르면, 그 human
   deliverable이 `run_crew()`의 첫 Task description에 컨텍스트로
   들어간다(`agent_runtime.augment_with_retrieval()`이 이미 하는 패턴을
   `run_crew()` 호출 준비 단계에서 재사용).
3. Agent 멤버들이 역할별로 CrewAI 협업(위임 포함)해 결과를 내면 §3-3
   경로로 deliverable 타임라인에 쌓인다.
4. 사람 팀원 전원이 그 결과를 보고 댓글/수정요청, 팀 리더가 승인(F7
   결재 체인 재사용 — 새 승인 로직 불필요).

즉 "동시 실시간 협업"이 아니라 **"턴제 비동기 협업을 N:M으로 확장"**
하는 것 — 지금 1:1로 되는 걸 그대로 다자간으로 넓히는 설계다(새로운
협업 패러다임을 발명하지 않는다, 검증된 패턴 재사용 원칙).

## 4. Process 변경

```
(신규) 팀 구성 ─┐
                ├─→ 업무 요청 → 할 일로 나누기 → 인력 매칭/배정
(기존) 개별 사람/Agent ─┘        (사람 | Agent | 팀 중 선택)
                                        ↓ 결재(팀이면 멤버 전체 confirmed)
                                 협업 워크스페이스(N:M 타임라인)
                                   ├─ 사람 팀원: draft 작성/댓글
                                   └─ "팀 Agent 실행" → run_crew(task_id=...)
                                        → Agent 멤버별 deliverable
                                        → Gate A-G(팀 전체, 기존 계산 재사용)
                                        → F7 승인(팀 리더 1人 제출 기준)
```

## 5. UI 변경

| 화면 | 변경 |
|---|---|
| **(신규) 팀 관리**(운영 관리) | 팀 생성/편집 — 사람 인력·AI 에이전트 풀에서 멤버 선택(체크박스+역할명 입력, §53의 패턴 재사용), 활성/비활성, 삭제(admin 전용) |
| **인력 매칭/배정** | 후보 유형에 "팀" 추가 — 필요 역할과 팀 멤버 역할 커버리지를 비교해 추천(완전일치 로직 확장) |
| **할 일 상세**(§55 옵션 C 패널) | 배정 현황에 팀 멤버 전원 표시, "팀 Agent 실행" 버튼(→ `run_crew(task_id=...)`) |
| **협업 워크스페이스** | 타임라인 카드에 "역할명" 배지 추가(지금은 author_type만 구분 — N명이 되면 "누구"인지 구분 필요), §57 다이어그램을 여기도 재사용해 "이 할 일에서 실제 협업 관계" 시각화 |
| **Crew 실행** | 유지하되 `task_id`(선택) 필드 추가 — "즉석 실행"과 "실제 업무 실행" 겸용. 장기적으로 "할 일 상세"의 "팀 Agent 실행" 버튼이 내부적으로 이 API를 호출하는 쪽으로 수렴 |
| **조직도** | 팀 단위 묶음 보기 추가(기존 매니저별 묶음과 별도 탭/토글) |

## 6. 단계별 로드맵

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | `teams`/`team_members` 스키마+CRUD API+"팀 관리" UI(배정과 무관하게 "팀을 정의"만) | **구현 완료**(2026-10-06) |
| 2 | `assignments.resource_type='team'` + 다중-confirmed 허용 옵션 + "인력 매칭/배정"에 팀 후보 추가 | **구현 완료**(2026-10-06) — "인력 매칭/배정"의 팀 후보 추천만 미구현(§9) |
| 3 | `run_crew(task_id=...)` → deliverables/Gate 저장 다리 | **구현 완료**(2026-10-07, §9 "Phase 3 구현") — 비용 안내는 기존 "비용 발생" 경고 패턴 재사용 |
| 4 | 협업 워크스페이스/할 일 상세 UI를 N:M 타임라인으로 확장(역할 배지, 다이어그램 재사용) | 미구현 |
| 5 | (오픈 이슈, 선택) "Crew 실행" 탭을 팀 실행으로 완전히 흡수할지, 즉석 실행 용도로 분리 유지할지 | 미결정 — 지금은 "Crew 실행" 탭(즉석)과 "팀 Agent 실행"(업무 연동)을 별개 화면으로 공존시킴 |

## 7. 명시적으로 스코프 밖에 두는 것

- **실시간 동시 협업**(사람이 CrewAI Crew 실행 도중 끼어드는 것, L4) —
  Phase C에서 이미 조사·기각(`register_before_tool_call_hook`은 구현
  비용 대비 `docs/LIMITS.md` §3의 "단일 프로세스·동기 요청" 전제를
  건드리는 아키텍처 변경이라 리스크가 가장 크다고 판단됨). 재검토하려면
  별도 ADR이 먼저 필요하다.
- 팀 간 팀(팀을 멤버로 갖는 팀, 중첩 구조)은 1차 범위 밖 — `team_members`
  는 사람/Agent만 참조한다.
- 팀 단위 비용 자동 집계(현재도 `cost.py`는 수동 입력 중심 — 팀이라고
  달라질 이유 없음, §58 패턴과 동일하게 감사로그 재사용으로 충분).

## 8. 다음 액션(사용자 결정 필요) — 결정됨(2026-10-06)

1. Phase 1부터 순차 진행할지, 1-2를 묶어 한 번에 설계할지.
   → **"1-2를 묶어 한 번에 설계"**로 결정.
2. §3-2의 "다중 confirmed 허용"을 팀 배정에만 열지, 개별 배정(사람
   2명을 한 task에 직접 배정)에도 열지 — 후자는 "팀"이라는 그릇 없이도
   L1을 풀어주지만, 그만큼 `assignments`/결재 의미론이 더 복잡해진다.
   → **"다중 confirmed 허용의 경우 개별 배정에도"**로 결정 — 팀뿐
   아니라 사람/사람, Agent/Agent 등 개별 배정도 기본적으로 여러 건이
   공존한다. "교체"하고 싶을 때만 `replaces_assignment_id`를 명시한다
   (§9 구현 참고).
3. 팀의 F7 승인 주체 — "팀 리더"(team_members 중 1명을 지정)로 할지,
   "팀을 배정 요청한 사람"(현재 `requested_by`)으로 그대로 할지.
   → **"팀의 F7 승인 팀 리더가(팀 리더의 경우 팀을 배정 요청한 사람이
   default로 지정, 변경 가능)"**로 결정 — `team_leader_id` 컬럼을
   추가하고 기본값은 요청자, `requested` 상태일 때만 변경 가능.

## 9. 구현 현황(Phase 1+2 2026-10-06/07, Phase 3 2026-10-07 완료)

위 결정에 따라 Phase 1(팀 정의 CRUD)과 Phase 2(`resource_type='team'`
배정 + 다중 confirmed 허용)를 한 번에 구현했고, 이어서 사용자 요청
("crew 실행에서... 복수 그룹 가능하도록 개선, 이 그룹이 전체 업무에서
사람-에이전트 연동이 가능한 구조인지 확인")으로 Phase 3(`run_crew()`
↔ `task_id` 다리, §3-3)도 구현했다. Phase 4(협업 워크스페이스 N:M
타임라인 UI, §5의 역할 배지·다이어그램 재사용)만 **아직 미구현**으로
남는다.

**구현된 것**:

- `teams`/`team_members` 스키마(§3-1 그대로) — `db.py::SCHEMA`.
- `teams.py` — `skill_pool`/`tool_pool`/`model_registry`와 동일한
  "등록 → 재사용" CRUD 패턴(`create_team`/`get_team`/`list_teams`/
  `set_team_active`/`delete_team`/`add_team_member`/
  `remove_team_member`). `active_agent_member_ids()`는 Phase 3에서
  쓸 예정으로 미리 추가해둔 헬퍼(아직 호출부 없음).
- `assignments.create_assignment_request()`/`resolve_assignment_
  decision()` — `resource_type='team'` 지원, `team_leader_id`(기본값
  `requested_by`) 기반 F7 결재라인, `replaces_assignment_id`로
  명시적 교체 의도 표현. **"같은 task·같은 resource_type이면 자동
  supersede"였던 기존 동작을 완전히 제거**하고 "명시적으로 지목한
  것만 supersede"로 바꿨다 — 결정 2(§8)에 따라 팀뿐 아니라 개별
  배정에도 적용.
- `assignments.update_team_leader()` + `PATCH /api/assignments/
  {id}/team-leader` — `requested` 상태일 때만 변경 허용(확정 후엔
  결재라인과 어긋나 금지).
- `deliverables.task_participant_relationship()`에 `team_leader`/
  `team_member` 판정 추가 — 기존 "모든 confirmed 배정을 순회" 구조라
  수정 범위가 작았다(§3-2에서 예측한 대로).
- `app.py` — `GET/POST /api/teams`, `PATCH .../active`,
  `DELETE /api/teams/{id}`, `POST/DELETE .../members`,
  `PATCH /api/assignments/{id}/team-leader` 8개 엔드포인트. RBAC는
  기존 pool_registry 관례(생성/조회/멤버 관리는 admin+manager, 삭제는
  admin 전용)를 그대로 따름.
- `GET /api/tasks/{id}/assignments` — **구현 중 실측으로 발견한 갭**:
  이 엔드포인트가 `human_pool`/`agent_pool`만 JOIN하고 `teams`는
  JOIN하지 않아 팀 배정의 `resource_name`이 항상 `null`로 나왔다
  (§55 "할 일 상세" 패널이 이 엔드포인트에 의존하므로 팀 배정을 그려줄
  방법이 없었다는 뜻). `LEFT JOIN teams` + `team_leader_id` 컬럼 노출로
  수정 — `tests/unit/test_phase22_team_collaboration.py::test_gc231`로
  회귀 확인.
- **UI**(`webui.py`) — §5 표의 "(신규) 팀 관리" 탭(생성·멤버 추가/제외·
  활성 토글·삭제)과 "할 일 상세" 패널 갱신: 복수 confirmed/requested
  배정을 모두 표시(기존엔 `.find()`로 1건만), 팀 리더 변경 입력,
  "팀에 배정 요청" 폼. "인력 매칭/배정" 탭의 팀 후보 추천(§5 두 번째
  행, "필요 역할과 팀 멤버 역할 커버리지 비교")은 **아직 구현하지
  않았다** — 지금은 "할 일 상세"에서 활성 팀 목록 중 고르는 방식만
  있고, 매칭 점수 로직에 팀을 넣지는 않았다.

**검증(Phase 1+2)**: `tests/unit/test_phase22_team_collaboration.py`
(GC226-234, 9건) + 기존 4건 수정(`test_v2_pipeline.py::test_gc17/18`,
`test_phase17_collab_workspace.py::test_gc150/217` — "다중 confirmed
허용"으로 자동 supersede가 없어져 명시적 `replaces_assignment_id`가
필요해졌다). 실제 데모 서버(`hair.db`)에 팀 생성→멤버 추가→배정
요청→F7 결재→확정까지 실측 완료(`docs/LIMITS.md` §61 참고).

### Phase 3 구현(2026-10-07) — `run_crew()` ↔ `task_id` 다리

**구현된 것**:

- `crew_runtime.run_crew_for_team(conn, team_id, task_id, actor_id, ...)`
  — 팀이 그 task에 confirmed로 배정돼 있는지 검증 → `teams.
  active_agent_member_ids()`(Phase 1+2에서 미리 만들어둔 헬퍼, 드디어
  호출부가 생겼다)로 active Agent 멤버만 추출 → 전원에게 같은
  task_description(할 일 `role_name` + 최신 사람 작성 deliverable,
  `deliverables.start_agent_execution()`의 단일 Agent 프롬프트와 같은
  패턴)을 주고 기존 `run_crew()`를 그대로 호출.
- `deliverables.start_team_crew_execution()` / `run_team_crew_job()`
  — 단일 Agent 비동기 실행(Phase 4)과 같은 2단계 패턴(검증+job 행
  생성은 동기, 실제 CrewAI kickoff은 `BackgroundTasks`). 팀은 Agent가
  여럿이라 `agent_execution_jobs` 행도 참여 Agent 수만큼 만들고
  (매니저로 지정된 Agent는 산출물이 없어 제외), Crew 결과의 Agent별
  산출물 각각을 `deliverables`(author_type='agent') + F7 결재 체인
  으로 저장한다 — **이 저장이 L2를 실제로 없앤다**: 그 순간부터 팀
  리더·팀원(사람)이 협업 워크스페이스에서 그 결과물을 보고 댓글을
  달고 승인/반려할 수 있다.
- `POST /api/tasks/{id}/execute-team-crew` — 참가자 판정은 단일 Agent
  실행(`요청자 본인만`)보다 넓게 뒀다: 팀 리더·팀원 둘 다 트리거
  가능(admin/manager는 기존처럼 감독 대리 허용) — 팀은 원래 여럿이
  함께 쓰는 단위라는 전제.
- **UI**(`webui.py`) — 협업 워크스페이스에 "팀 Agent 실행(Crew)" 카드
  신설. "복수 그룹"(사용자 질문의 핵심)은 한 할 일에 팀이 여러 개
  confirmed로 공존하는 것 자체는 이미 Phase 2의 "다중 confirmed
  허용"으로 가능했으므로, 이 카드는 그중 **팀 하나를 골라 실행**하는
  드롭다운을 보여준다(사용자 결정 — "팀 하나를 골라서 실행", 전체
  동시 실행 아님). 한 할 일에 confirmed Agent가 2개 이상일 때 실행할
  Agent를 고르게 한 §63의 패턴을 팀 단위로 그대로 확장한 것이다.

**사용자 질문에 대한 답**: "이 그룹이 전체 업무에서 사람-에이전트
연동이 가능한 구조인가" — Phase 3 이전엔 **아니다**였다(`run_crew()`
결과가 `agent_pool.last_output`에만 남고 끝, `assignments`/
`deliverables`/결재 체인 어디에도 안 이어짐, §63에서 재확인). 이제는
**그렇다** — 팀의 Agent 그룹을 실행하면 각자의 산출물이 그 할 일의
deliverable로 쌓이고 F7 결재·Gate A-G·댓글까지 기존 1:1 협업과 동일한
파이프라인을 그대로 탄다.

**검증(Phase 3)**: 신규 `tests/unit/test_phase24_team_crew_bridge.py`
(GC237-241, 5건) — 미확정 팀 거부, active 멤버만 포함(오프보딩 Agent
제외), Agent별 deliverable+결재 체인 생성(§3의 "저장이 L2를 없앤다"를
직접 검증), 비참가자 403, **한 할 일에 팀 2개가 공존하며 각각 독립
실행**(복수 그룹의 핵심 시나리오). 전체 골든셋 246건(241+5) 통과.

**알려진 한계(사용자에게 그대로 노출)**:

- 팀의 Agent 멤버 중 **매니저로 지정된 1명**은(hierarchical) 산출물이
  없어 deliverable이 안 생긴다 — `run_crew()`의 기존 설계(매니저는
  조율만 함)를 그대로 물려받은 것이지 버그가 아니다.
- "팀 Agent 실행" UI는 provider/model은 고를 수 있지만 `manager_agent_id`
  (팀 멤버 중 매니저로 지정)는 UI에 없다 — API(`TeamCrewExecuteRequest.
  manager_agent_id`)는 지원하지만, 그걸 고르려면 팀 멤버 상세 조회가
  필요한데 `GET /api/teams/{id}`가 admin/manager 전용이라(팀원인
  member 역할은 403) 당장은 단순화를 위해 뺐다 — 후속 작업으로 남긴다.
- "인력 매칭/배정"의 자동 추천은 여전히 사람/Agent 후보만 대상이다 —
  팀은 "할 일 상세"에서 수동으로 골라야 한다(Phase 4 범위).
- 여러 job이 동시에 도는 중 하나만 완료되면 Web UI가 "실행 중" 표시를
  살짝 이르게 내릴 수 있다(표시만의 문제 — 서버 쪽 "이미 실행 중"
  가드는 전체 job이 끝날 때까지 정확히 막는다, 중복 과금 위험 없음).

### Phase 3 UX 후속(2026-10-07) — 그룹 생성 경로를 업무 흐름 안으로

Phase 3가 "실행"(업무 연동)은 해결했지만, 사용자가 "현재 UI가 복수의
에이전트 그룹 생성... HAIR 관점의 연동에 적합한 경우는 아님"이라고
다시 짚었다 — **그룹을 어디서 만드는가**가 여전히 "Crew 실행"이라는
업무 무관 즉석 화면에 고립돼 있었다. "AI 에이전트 생성"(여러 할 일을
한 번에 골라 역할별 Agent를 동시 생성하는 기능이 이미 있었음)의 결과
화면에 "체크한 Agent를 팀으로 묶기"를 추가하고, "AI 에이전트 관리"에
Agent별 소속 팀을 노출해(`teams.team_names_for_agent()`) 그룹 생성·
가시성을 업무 생성 흐름 안으로 옮겼다. "Crew 실행" 탭은 "업무와
연결되지 않는 즉석 실험용"으로 안내 문구를 명확히 해 역할을 분리했다
— 자세한 내용은 `docs/LIMITS.md` §65.
