# HAIR-Platform 종합 기획/설계/개발 계획 (Master Plan)

사용자 요청(2026-09-28): "HAIR 관점에서 전체 업무 프로세스를 정리하고
사용자와 관리자의 역할을 명확히 해줘. 아키텍처 측면에서 지식(업무지식인
Skill, RAG 등) 및 도구(MCP, CrewAI Tool) 그라운딩 및 프로세스 관리,
프로세스 전반의 데이터 흐름 등을 고려하고, AI Agent에 대한 평가가
되도록 신뢰성/확장성을 갖추도록 설계하고, 기업에서 효과적으로 사용
가능하도록 UI/UX를 기획해 새로운 기획/설계/개발계획을 수립해줘."

**이 문서와 `docs/COLLABORATION_PLATFORM_PLAN.md`의 관계**: 그 문서는
"사람-AI 협업" 한 가지 축(결과물 전달·HITL·HOTL·Skill/Tool 재정의)을
깊게 파고든 선행 기획안이다. 이 문서는 그걸 **하나의 축으로 포함**하는
상위 마스터 플랜이다 — 전체 업무 프로세스, 역할, 지식/도구 그라운딩
아키텍처, 데이터 흐름, Agent 평가(신뢰성/확장성), 기업용 UI/UX를 추가로
다룬다. 세부 근거(파일:라인, 외부 서비스 리서치)는 중복 서술하지 않고
그 문서를 참조로 남긴다.

## 1. 전체 업무 프로세스 (End-to-End)

기존 F1–F14(SPEC.md)와 `COLLABORATION_PLATFORM_PLAN.md`의 신규 단계를
하나의 파이프라인으로 통합하면 다음과 같다.

| 단계 | 내용 | 담당(주 행위자) | 근거/상태 |
|---|---|---|---|
| 1. 업무 요청 | 브리프 제출(자연어) | 팀원/매니저 | F1, 기존 구현 |
| 2. 할 일 분해 | Task(역할·필요스킬·인원)로 나눔 | 팀원/매니저 | F1 연장, 기존 구현 |
| 3. 후보 매칭 | 완전일치→가중치 스코어링(가용40·적합35·비용25) | 매니저/관리자 | F6, 기존 구현 |
| 4. 배정 요청→결재 | 상사 체인 다단계 승인, 자기승인 방지 | 요청자→상사 체인 | F6/F7, 기존 구현 |
| 5. 배정 확정→노출 | `assignments.status='confirmed'` → "내 업무" 신규 노출 | 시스템 | **신규**(협업 플랜 4-1) |
| 6. 협업 실행 | 사람 입력→(선택)Skill 그라운딩 LLM 보조→Agent 실행(Tool 그라운딩)→산출물 교환 | 팀원(작업자)+Agent | **신규**(협업 플랜 4-2/4-3) |
| 7. Agent 산출물 평가 | Gate A–G 자동 채점, 신뢰도 기록 | 시스템 | **신규**(본 문서 §5) |
| 8. 산출물 승인(HITL) | 승인/반려/수정, 자기승인 방지 재사용 | 감독자(요청자 상사 또는 지정 리뷰어) | **신규**(협업 플랜 Phase B) |
| 9. 실행 중 개입(HOTL) | 진행 중 일시정지/재지시(옵션) | 감독자 | **신규**(협업 플랜 Phase C) |
| 10. 성과 리뷰 | 동의기반(NF9) 점수 + 자동 Gate 점수 병행 | 관리자/매니저 | F8 확장(본 문서 §5) |
| 11. 비용/공정성 집계 | 확정 배정 기준 비용·반려율 격차 | 관리자/매니저 | F10/NF4, 기존 구현 |
| 12. 감사 기록 | 전 단계 append-only 기록 | 시스템 | F13/NF10, 기존 구현 확장 |

**핵심 관찰**: 1~4·10~12는 이미 튼튼하게 구현돼 있다(이 워크북 세션의
누적 작업). 5~9가 이번에 새로 설계하는 "업무 내용 자체"의 협업 축이고,
그 축이 10(성과 리뷰)에 실제 데이터를 처음으로 공급하게 된다(현재
`performance_score`는 관리자 수동 입력뿐 — §5에서 자동화 경로 추가).

## 2. 사용자·관리자 역할 명확화

기존 RBAC 4역할(Admin/Manager/Member/Viewer, `auth.py`)에 신규 협업
프로세스에서의 책임을 명시적으로 얹는다. **역할 자체를 늘리지 않는다**
— 기존 4역할의 책임 범위를 구체화하는 것뿐이다.

| 역할 | 조직/카탈로그 관리 | 배정·결재 | 협업 실행(신규) | 평가·정책(신규) |
|---|---|---|---|---|
| **관리자(Admin)** | 사용자 생성·상사 지정, Tool/Skill Pool 등록·삭제, Agent 온보딩/오프보딩, 전 리소스 하드 삭제 | 전체 결재 처리 가능 | 모든 워크스페이스 조회·개입 가능 | Gate 임계값·자동승인 정책 설정(신규), 평가 대시보드 열람 |
| **매니저(Manager)** | 사람/Agent Pool 등록(삭제 제외) | 업무 요청·배정 요청·자기 라인 결재 | **자신이 요청한 Agent 업무의 감독자**(HOTL 수행), 팀원 업무 리뷰 | 팀 단위 평가 대시보드 열람, 팀원 산출물 승인 |
| **팀원(Member)** | 없음 | 업무 요청 제출, 자기 배정 결재 요청 | **"내 업무"에서 배정된 작업 직접 수행**(사람 입력 작성, Skill 활용 LLM 보조 호출, Agent 실행 트리거, Agent 산출물 1차 확인) | 자기 성과점수 조회(동의 시) |
| **뷰어(Viewer)** | 없음 | 조회만 | 워크스페이스 읽기 전용 | 조회만 |

**이번에 새로 명확해지는 지점**: 지금까지 "관리자 도구" 그룹의 AI
에이전트 생성/Crew 실행은 매니저 이상만 접근 가능했는데, 협업
워크스페이스(§1의 5~9단계)는 **팀원도 자기 업무에 한해 직접 Agent를
실행·산출물을 확인**할 수 있어야 한다 — "관리자만 AI를 다룬다"가 아니라
"내 업무에 배정된 Agent와는 담당자가 직접 협업한다"는 원칙으로
바뀐다(RBAC 매트릭스 재정의 필요, 아래 §7 로드맵 항목).

## 3. 아키텍처 — 지식/도구 그라운딩 및 프로세스 관리

### 3-1. 지식 그라운딩 (Skill · RAG)

- **Skill Pool(사람용, 협업 플랜 4-3 재정의)**: 지금은 `name`+
  `description`뿐인 짧은 텍스트다. 협업 워크스페이스의 "LLM으로 도와줘"
  호출에 실질적으로 쓸모 있으려면 **더 긴 지식 본문 필드**(또는 외부
  문서 링크)가 필요할 수 있다 — 지금 시드해둔 18개 Agent-Evaluator
  절차 스킬(§20)도 DB엔 요약 설명만 있고 실제 절차 전문은
  `SKILL.md` 파일에 있다. 스키마에 `content_ref`(파일 경로/URL) 또는
  `content`(긴 텍스트) 필드 추가를 검토한다.
- **VectorDB(지식검색, `vectorstore.py`)**: 지금은 Agent 전용 native
  Tool(`vectordb_search`)이다. 같은 `vectorstore.search()`를 사람의
  LLM 보조 호출에도 재사용할 수 있다 — Skill이 "이 지식은 임베딩
  검색이 필요할 만큼 크다"고 표시(예: `retrieval_mode='embedding'`)하면
  사람 호출 시에도 관련 청크를 찾아 프롬프트에 넣는다. 이건 Tool을
  붙이는 게 아니라(Tool은 Agent 전용 원칙 유지) 같은 검색 함수를 사람
  쪽 LLM 호출 경로에서 직접 호출하는 것 — Skill Pool과 Tool Pool의
  "역할"은 분리하되 **내부 구현(임베딩 검색 함수)은 공유**한다.
- **GraphDB(관계분석, `graphquery.py`)**: 조직도/배정 이력을 그래프로
  조회하는 용도는 그대로 Agent 전용으로 유지한다(NF9 — 성과 데이터는
  소스 레벨에서 차단, 이 경계는 절대 완화하지 않는다).

### 3-2. 도구 그라운딩 (Tool · MCP · CrewAI Tool)

- **Tool Pool(Agent 전용, 원칙 유지)**: native(VectorDB/GraphDB),
  mcp_stdio(예: §20의 `recommend_fix` MCP 서버), mcp_remote. 이미 있는
  `resolve_pool_tools()`(스킬 매칭+명시적 연결 병합) 로직을 그대로
  협업 워크스페이스의 "Agent 실행" 트리거 경로에 재사용한다 — **새
  도구 조립 로직을 또 만들지 않는다.**
- **신규 고려(오픈 이슈)**: Agent 자신이 "내 이전 산출물 평가가 어땠는지"
  를 조회할 수 있는 native Tool(§5의 평가 기록을 감싼 조회 함수)을
  둘지는 이번 문서에서 결정하지 않는다 — Agent가 자기 평가를 참조해
  스스로 개선하는 루프는 흥미롭지만 범위가 커서 별도 검토가 필요하다.

### 3-3. 프로세스 관리 (상태 머신)

기존 상태 머신에 신규 상태를 얹는다(전부 additive — 기존 값 의미는
안 바꾼다):

- `assignments.status`: `requested → confirmed | superseded`(기존, 불변).
- `deliverables.status`(신규): `draft → pending_review → approved |
  rejected → revised`(반려 시 사람이 수정해 다시 `pending_review`로).
- `job_items.status`(기존에 Phase C가 추가): `pending → running →
  waiting_for_human(신규) → succeeded | failed | partial`.
- `agent_evaluations`(신규, §5): 상태가 아니라 매 산출물마다 생기는
  불변 기록(append-only, `audit_log`와 같은 철학 — 평가 결과를 나중에
  고쳐 쓰면 안 된다).

## 4. 데이터 흐름 (End-to-End)

```
브리프 제출 (briefs)
  └─ 할 일 분해 (tasks)
       └─ 매칭·배정요청·결재 (assignments, approvals) ── 확정(confirmed)
            └─ "내 업무" 노출 (GET /api/my-work, 신규)
                 └─ 협업 워크스페이스 진입 (task 단위)
                      ├─ 사람 입력 작성
                      │    └─ (선택) Skill 그라운딩 LLM 호출
                      │         · Skill Pool 조회(skill_tags 매칭)
                      │         · (선택) VectorDB 임베딩 검색
                      │         · llm.call_llm() 1회 호출
                      │    └─ deliverable(author=human) 저장
                      ├─ Agent 실행 트리거 (task로 스코프)
                      │    · deliverable(human) 을 Task context에 포함
                      │    · Tool Pool 그라운딩 (resolve_pool_tools)
                      │    · crew_runtime.run_crew() / execute_agent()
                      │    └─ deliverable(author=agent) 저장
                      │         └─ Gate A–G 자동 평가 (agent_evaluations, 신규)
                      │              └─ agent_pool.performance_score 갱신(자동값)
                      └─ 승인 게이트 (approvals.subject_type='deliverable', 신규)
                           ├─ 승인 → deliverable.status='approved' (최종 산출물)
                           ├─ 반려 → 'rejected' (사람이 재작성)
                           └─ 수정 → 새 deliverable(author=human, revises=이전 id)
  └─ 전 단계 → audit_log (append-only, 신규 이벤트 타입 추가)
  └─ 성과 리뷰 (performance-review): 수동 점수 + agent_evaluations 자동 점수 병행 표시
  └─ 비용/공정성 리포트: 기존 confirmed 배정 집계 로직 그대로(변화 없음)
```

**데이터 흐름에서 지켜야 할 원칙**(기존 NF 원칙과 충돌하지 않게):
- NF9(성과 데이터 사전 동의) — 자동 Gate 점수도 사람 리소스에는 동의
  없이 노출하지 않는다. Agent는 동의 개념이 없으니 그대로 노출.
- NF10(append-only 감사) — `agent_evaluations`도 감사로그와 같은
  철학으로 수정·삭제 불가하게 설계한다(트리거로 강제할지는 구현 시
  결정).
- ADR 3-A(자기승인 방지) — deliverable 승인에도 그대로 적용(이미
  §협업 플랜 Phase B에서 명시).

## 5. AI Agent 평가 — 신뢰성·확장성 설계

### 5-1. 현재 상태의 정직한 인정

`eval_wiring.py`는 **`default_stub_generator`(결정론적 규칙기반 스텁)
만 감싼 오프라인 배치 스크립트**다 — 실제 Agent 실행 경로
(`crewai_agent_generator`/`run_crew`/`agent_runtime.execute_agent`)에는
**전혀 연결돼 있지 않다**. `python -m hair_platform.eval_wiring`을
사람이 수동으로 실행해야만 결과가 나오고, 그 결과도 웹 UI 어디에도
노출되지 않는다(`docs/LIMITS.md` §1). 이번 협업 워크스페이스가 실제
Agent 산출물을 만들어내기 시작하면, 이 갭을 더는 미룰 수 없다 —
"신뢰할 수 있는 AI 팀원"이라는 HAIR의 핵심 가치는 실제 산출물이 계속
평가받아야 성립한다.

### 5-2. 제안 — 실행 경로에 평가를 인라인으로 연결

- `crew_runtime.run_crew()`/`crewai_agent_generator()`/
  `agent_runtime.execute_agent()` 완료 시점마다, `eval_wiring.py`가 쓰는
  것과 같은 `EvalMetadata`+`PerformanceMonitor` 패턴을 적용해 **자동으로
  Gate A/B/D/E/G를 채점**한다(Gate C는 LLM 호출 특성상 항상 낮게
  나올 수 있어 참고용, Gate F는 위임이 실제로 발생한 Crew 실행에서만
  의미 있음 — 이미 있는 델리게이션 캡처 로직(`crew_runtime.py`의
  "Delegate work to coworker" 캡처)을 그대로 재사용).
- 신규 테이블 `agent_evaluations`: `id, deliverable_id, gate_scores(JSON),
  tcr, evaluated_at` — 산출물 하나당 하나, append-only.
- `agent_pool.performance_score`는 **자동값과 관리자 수동값을 구분해서
  보여준다**(완전 자동 대체 아님) — 자동 평가가 아직 불완전할 수 있다는
  걸 정직하게 인정하는 설계(F8의 "1차 범위: 관리자 수동 입력" 한계를
  갑자기 "완전 자동"으로 과장하지 않는다).
- Gate E(보안 경계)/B(행동 무결성) 위반이 감지되면 **승인 게이트를
  자동으로 필수화**한다(Phase B의 "이 Task는 리뷰 필요?" 토글이 위반
  시 강제로 켜지는 형태) — 낮은 신뢰도 산출물이 사람 검토 없이 그냥
  통과되는 걸 막는다.

### 5-3. 확장성 고려

- 평가 자체가 LLM Judge를 쓰면 느려질 수 있으므로, Phase C(비동기
  실행)와 같은 인프라를 공유한다 — 평가도 "실행 완료 후 별도 백그라운드
  작업"으로 돌리고, 워크스페이스 타임라인에는 "평가 중…" 상태로
  표시했다가 완료되면 갱신한다(폴링, WebSocket 아님 — §6 밖으로 명시한
  범위와 일관).
- Agent/Task 수가 늘어나도 `agent_evaluations`는 append-only 로그라
  집계(대시보드용 평균 Gate 점수 등)는 별도 집계 쿼리/캐시로 분리해
  실시간 페이지 로딩을 막지 않게 한다.

## 6. 기업용 UI/UX 기획

현재 UI(단일 페이지 vanilla JS, 탭 14개, "내 업무"/"관리자 도구" 2구역,
고정 1100px 레이아웃, 라이트 전용)는 소규모 팀엔 충분했지만, 기업
규모로 가려면 다음이 필요하다.

| 개선 항목 | 현재 상태 | 제안 |
|---|---|---|
| 통합 인박스 | 결재/워크스페이스 대기/평가 경고가 각 탭에 흩어져 있음 | "내게 필요한 조치" 통합 인박스 — 결재 대기·내 업무 응답 대기·낮은 Gate 점수 경고를 한 곳에 |
| 매니저 대시보드 | 없음(탭마다 표를 따로 봐야 함) | 팀 Agent 평균 Gate 점수, 대기 승인 건수, 비용 추이를 한 화면에 |
| 검색/필터 스케일 | 인력검색 필터 2개, 페이지네이션 없음(감사로그만 있음) | 부서/스킬/가용성 복합 필터 + 전체 목록형 화면에 페이지네이션 일괄 적용 |
| 협업 워크스페이스 UX | (신규 설계 중) | 리서치에서 확인한 "공유 스레드" 패턴(GitHub PR/Asana 댓글) 차용 — 채팅형 타임라인, Skill 자동완성 |
| 반응형/모바일 | 없음(고정 1100px) | 최소한 "내 업무" 인박스·승인 알림 확인은 반응형 필요 — 1차 범위 포함 여부는 사용자 결정 필요(§8) |
| 다크모드/접근성 | 라이트 전용으로 의도적 고정(과거 실사용 버그 회피) | 기업 배포 시 재검토 필요 — 단순 다크모드 CSS가 아니라 폼 컨트롤 대비 문제를 제대로 풀어야 함(과거 회귀 원인 그대로 재발 방지) |

## 7. 통합 개발 로드맵

`COLLABORATION_PLATFORM_PLAN.md`의 Phase A/B/C에 본 문서의 §5/§6을
더해 5단계로 재구성한다.

| Phase | 내용 | 난이도 | 의존성 |
|---|---|---|---|
| 1 | 결과물 엔티티 + 내 업무 진입 흐름 + Skill/Tool 역할 재정의 (구 Phase A) — **구현 완료**(`docs/PHASE1_DELIVERABLE_SPEC.md`, `docs/LIMITS.md` §22) | 중 | 없음 |
| 2 | 산출물 승인 게이트 (구 Phase B) | 중 | Phase 1 |
| 3 | **Agent 평가 인프라 인라인 연결(신규, §5)** | 중-상 | Phase 1(평가 대상인 deliverable 필요) |
| 4 | 비동기 실행 + 일시정지/재개 (구 Phase C) | 상(구조 변경) | Phase 1 |
| 5 | **기업용 UI/UX 고도화(신규, §6)** | 중(반응형 포함 시 상) | Phase 1–3 완료 후가 효율적(대시보드가 보여줄 데이터가 그때 갖춰짐) |

Phase 3(평가)을 Phase 4(비동기)보다 먼저 두는 이유: 평가는 기존 동기
구조에서도(약간의 지연을 감수하면) 바로 붙일 수 있어 리스크가 낮고,
"AI Agent를 신뢰할 수 있는가"라는 HAIR의 핵심 가치를 가장 빨리
증명하는 항목이기 때문이다.

## 8. 이 문서의 경계와 다음 액션

- 아직 기획 문서다 — 코드 변경 없음. `COLLABORATION_PLATFORM_PLAN.md`와
  함께 검토해야 전체 그림이 보인다.
- 결정이 필요한 것들:
  1. RBAC 재정의(§2) — "팀원이 자기 업무에서 직접 Agent 실행" 허용
     범위를 얼마나 넓힐지(예: 팀원이 임의 provider/model을 골라 비용을
     발생시킬 수 있게 할지, 관리자가 사전 승인한 기본값만 쓰게 할지).
  2. Skill Pool 지식 본문 필드 추가(§3-1) 여부와 형식(긴 텍스트 vs
     외부 문서 링크).
  3. `agent_evaluations` 자동 점수와 관리자 수동 점수를 성과 리뷰
     화면에서 어떻게 병기할지(§5-2) — UI 시안 필요.
  4. 반응형/모바일 지원 범위(§6) — 1차 로드맵에 넣을지, 이후로 미룰지.
  5. ~~이 마스터 플랜 전체를 팀 리뷰용으로 공유할 계획이면 Artifact(웹
     페이지)로 발행 가능~~ — 완료(2026-09-28). 이 문서와
     `COLLABORATION_PLATFORM_PLAN.md`를 합쳐 Artifact로 발행함.
  6. ~~Phase 4(비동기/HOTL)의 실행 프레임워크 결정~~ — **완료
     (2026-09-28)**. CrewAI를 유지하고 LangGraph/AutoGen 등으로 전환하지
     않는다(AutoGen은 2025-10-02부로 Microsoft 유지보수 모드 전환,
     LangGraph는 CrewAI의 role 기반 모델과 1:1 이식이 안 돼 마이그레이션
     비용이 큼). conda 환경 `HAIR`에 crewai 1.6.1을 실제로 설치해 소스를
     직접 확인한 결과, 처음 리서치가 주장한 `@human_feedback` 데코레이터는
     **존재하지 않았다**(오류로 정정) — 대신 `crewai.hooks.
     register_before_tool_call_hook()`(도구 호출 직전 차단 가능한 전역
     훅)을 실제로 확인해, 여기에 HAIR 자체 대기 로직(DB 폴링/
     `threading.Event`)을 얹어 도구 호출 단위 HITL을 구현하기로 확정했다
     (자세한 내용: `COLLABORATION_PLATFORM_PLAN.md` Phase C "정정 2").
