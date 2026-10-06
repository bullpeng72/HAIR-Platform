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

| Phase | 내용 | 리스크 |
|---|---|---|
| 1 | `teams`/`team_members` 스키마+CRUD API+"팀 관리" UI(배정과 무관하게 "팀을 정의"만) | 낮음 — 신규 테이블, 기존 흐름 무변경 |
| 2 | `assignments.resource_type='team'` + 다중-confirmed 허용 옵션 + "인력 매칭/배정"에 팀 후보 추가 | 중간 — `resolve_assignment_decision()` 핵심 로직 변경, 회귀 테스트 필수(기존 "재배치=supersede" 동작 보존 검증) |
| 3 | `run_crew(task_id=...)` → deliverables/Gate 저장 다리 | 중간 — 비용 발생 경로 확장(팀 전체 Agent가 매번 실제 LLM 호출), 트리거 전 비용 안내 UI 필수(기존 "비용 발생" 경고 패턴 재사용) |
| 4 | 협업 워크스페이스/할 일 상세 UI를 N:M 타임라인으로 확장(역할 배지, 다이어그램 재사용) | 낮음 — 표시 전용 변경 |
| 5 | (오픈 이슈, 선택) "Crew 실행" 탭을 팀 실행으로 완전히 흡수할지, 즉석 실행 용도로 분리 유지할지 | 사용자 결정 필요 |

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

## 8. 다음 액션(사용자 결정 필요)

1. Phase 1부터 순차 진행할지, 1-2를 묶어 한 번에 설계할지.
2. §3-2의 "다중 confirmed 허용"을 팀 배정에만 열지, 개별 배정(사람
   2명을 한 task에 직접 배정)에도 열지 — 후자는 "팀"이라는 그릇 없이도
   L1을 풀어주지만, 그만큼 `assignments`/결재 의미론이 더 복잡해진다.
3. 팀의 F7 승인 주체 — "팀 리더"(team_members 중 1명을 지정)로 할지,
   "팀을 배정 요청한 사람"(현재 `requested_by`)으로 그대로 할지.
