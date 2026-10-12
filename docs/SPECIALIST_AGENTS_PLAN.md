# 전문 Agent 기획 — 개발 5단계(요구사항분석·코드분석·설계·구현·검증) + 일반 업무(시장조사·사업기획·전략수립)

사용자 요청(2026-10-12, "~/Projects/Agent-Evaluator/Media/Harness_Methodology/ 내용을 참조해
개발 '요구사항분석, 코드분석, 설계, 구현, 검증' 각 단계별 개발 업무를 수행할 전문 Agent 및
시장조사, 사업기획, 전략수립 등 일반 업무 수행을 위한 전문 Agent 들을 기획해줘. (Tool 및
Skill 등 재활용 가능한 기존 컴포넌트를 적극 활용하고, 필요하면 추가로 구성하도록 기획),
Agent 간 시퀀셜한 방식 또는 위임 방식으로 그룹화 가능하여야 하고 사람과 N:N 협업이
가능해야해. 우선 개발 및 일반 업무에 HAIR-Platform이 실용적으로 적용되어 활용될 수 있도록
필요한 에이전트들을 기획해줘").

**이 문서는 기획(설계) 문서다 — 코드/DB 변경은 포함하지 않는다.** 실행 전 코드베이스 실측
조사(Tool Pool/Skill Pool/Team/Crew/Conversation/Approval/Gate 전체)를 먼저 했고, 그
결과 **이 기획이 요구하는 메커니즘(시퀀셜·위임 그룹화, N:N 협업, HITL 검증 게이트, Gate
A-G 자동평가)은 전부 이미 구현돼 있다** — 새로 필요한 건 "어떤 전문 Agent를, 어떤
`skill_tags`로, 어떤 Team 구성으로 등록하느냐"는 **데이터 설계**뿐이다. 코드 변경이 필요한
지점은 §6에 명시적으로 분리해 적었다.

## 0. 재사용 가능성 조사 결론(요약)

| 요구사항 | 기존 컴포넌트 | 상태 |
|---|---|---|
| Agent 등록 | `agent_pool`(role_name/skill_tags/status/framework) | 재사용 |
| 역량(스킬)→도구 자동 연결 | `skill_pool`/`tool_pool`/`agent_tool_links`(`pool_registry.resolve_pool_tools()`) | 재사용 |
| 시퀀셜 그룹화 | `crew_runtime.run_crew(process="sequential")` — Task.context 자동 체이닝 | 재사용 |
| 위임 그룹화 | `crew_runtime.run_crew(process="hierarchical", manager_agent_id=...)` — `allow_delegation=True`, 위임 이벤트 실측 캡처 | 재사용 |
| 재사용 가능한 그룹 등록 | `teams.py`(`teams`/`team_members`) + `run_crew_for_team()` | 재사용 |
| 사람 N : AI Agent N 동시 협업 | `conversations.py`(사람+Agent+Team 동시 참가, `@멘션`별 독립 병렬 실행) | 재사용 |
| 결과물 HITL 승인 게이트 | `approvals.py`(`subject_type='deliverable'`, 조직도 매니저 체인, self-approval 방지) — Agent deliverable 생성 즉시 자동 적용 | 재사용 |
| 품질 자동 측정 | `agent_evaluation.run_and_score()`→`agent_evaluations`(Gate A-G, TCR) — Agent 산출물 생성 경로마다 이미 자동 호출 | 재사용 |
| Harness Methodology 절차 지식 | `skill_pool`에 15개 절차 Skill이 이미 시드됨(`augmentation_mode='none'`) | 재사용 |
| 저장소 코드 직접 읽기/수정 | 없음 — Tool Pool에 코드베이스 접근 Tool이 없음 | **신규 필요(§6)** |
| Team 멤버별 다른 지시 분배 | `run_crew_for_team()`은 전원 동일 description(설계 한계) — 다만 `run_crew()`를 직접 쓰면 Agent별로 다른 `task_descriptions`를 줄 수 있어 실제로는 막혀 있지 않음 | 우회 경로 있음 |

---

## 1. 설계 원칙

1. **새 실행 경로를 만들지 않는다.** 모든 전문 Agent는 `agent_pool`의 평범한 행이다 — `framework='crewai'`로 등록해 CrewAI Agent 루프(도구 호출 가능)를 쓴다. `'simple_llm'`은 단일 LLM 호출이라 도구 호출이 불가능해 전문 Agent 용도로는 부적합하다(제외).
2. **역량은 코드가 아니라 데이터다.** 전문성의 차이는 `role_name`+`skill_tags`+`agent_tool_links`로만 표현한다. 같은 "코드분석가" role이라도 `skill_tags`에 무엇을 넣느냐가 실제 능력을 결정한다.
3. **그룹화는 두 층위로 나눈다.** (a) 즉석 테스트 — "Crew 실행" 탭에서 임시로 `agent_ids`+`process`만 골라 실행(업무에 연결 안 됨, 비용만 발생). (b) 운영 — `teams.py`에 역할 세트로 등록 → Task에 배정 → 결재 → `run_crew_for_team()` 실행 → 결과가 Task의 Deliverable+결재 체인+Gate A-G로 자동 연결. 전문 Agent "그룹"은 (b)로 설계한다.
4. **검증은 별도 엔진이 아니라 기존 파이프라인의 해석자다.** Gate A-G는 이미 모든 Agent 산출물에 자동 적용된다 — "검증 Agent"의 일은 그 점수를 해석해 사람에게 조치를 제안하는 것이다(`recommend-fix-workflow` 스킬 재사용).
5. **N:N은 Conversational Workspace를 기본 통로로 쓴다.** Task 결재 파이프라인은 "역할별 전문 Agent가 Task를 순서대로/위임으로 처리"하는 정식 업무 흐름에, 대화(conversations)는 "사람 여러 명이 필요할 때 전문 Agent/Team을 가볍게 불러 상담·질의"하는 흐름에 쓴다 — 둘 다 같은 `agent_pool`/`teams` 데이터를 공유하므로 이중 등록이 없다.

---

## 1-2. 하니스 메서드 3축 역할 체계와의 대응

《하니스 메서드》(Agent-Evaluator, `Media/Harness_Methodology/Part_I_Harness_Methodology_방법론의_정의/`)는 "역할"을 하나의 축이 아니라 **3축**으로 분해한다 — 이 기획의 모든 Agent/사람 배치는 이 3축에 그대로 대응시킨다.

| 축 | 의미 | HAIR-Platform 대응 |
|---|---|---|
| 축① 기계 실행 주체 | Tier 1(분석·설계, 큰 모델)/Tier 2(실행, 작은 모델)/LiveGuardrail/배치 Gate A-G | `agent_pool`의 `framework='crewai'` Agent 자체(요구사항분석가~QA/검증자는 Tier 1형 작업, 구현자는 Tier 2형 작업) |
| 축② HITL/HOTL | 사전승인(사람이 기다림) vs 사후감독(사람이 안 기다림) | `approvals.py`(사전형, 결재 대기) vs Gate A-G 자동채점(사후형, `agent_evaluation.run_and_score()`가 즉시 계산해두고 사람은 나중에 확인) |
| 축③ 사람 분업(책임자) | "누가 초안을 썼는가"가 아니라 "**누가 승인 책임(Accountable)을 지는가**" | Task의 결재 체인(`assignments`/`approvals`의 조직도 매니저) — 전문 Agent는 항상 축①만 채우고, 축③(Accountable)은 반드시 사람이 맡는다(ADR 결정 3-A self-approval 방지와 동일한 원칙) |

하니스 메서드가 예시로 쓰는 **5인 캐스팅**(분석가 정민 / 설계자 유진 / 개발자 수아·태호 / QA·거버넌스 하나)은 이 기획의 Dev Crew 5단계와 정확히 대응된다 — 다만 하니스 메서드의 "정민/유진/수아·태호"는 사람 역할이고, 이 기획에서는 그 **역할의 Tier 1/Tier 2 실행 축만** 전문 Agent로 대체한다("하나"의 QA·거버넌스 역할은 Agent화하지 않고 §4-1의 결재 체인을 쥔 사람이 그대로 맡는다 — 책임 소재를 Agent에게 넘기지 않는다는 원칙).

| 하니스 캐스팅 | 책임(Tier) | 이 기획의 전문 Agent | 비고 |
|---|---|---|---|
| 정민(분석가) | Tier 1 | 요구사항분석가, 코드분석가 | Spec·실패모드카탈로그·골든셋·의존관계지도 초안 담당과 동일 |
| 유진(설계자) | Tier 1 | 설계자 | ADR 초안 담당과 동일 |
| 수아·태호(개발자) | Tier 2 | 구현자 | TDD-AI(§2 "구현자 Agent의 의도된 한계" 참고) |
| 하나(QA·거버넌스) | Tier 1 분석 + HOTL 감사 | QA/검증자 **Agent** 부분(Gate 해석·조치 제안) | 승인 권한(Accountable)은 Agent화하지 않음 — §1-2 축③ 참고 |

---

## 2. 개발 5단계 전문 Agent

Harness Methodology(Agent-Evaluator, `~/Projects/Agent-Evaluator/Media/Harness_Methodology/`)가 정의하는 "요구사항분석(Phase 0-1) → 설계(Phase 2-3, SPEC/ADR) → 구현(Phase 4-5) → 검증(Phase 6-8, Gate A-G)" 파이프라인에 "코드분석"(기존 코드베이스 이해 — 신규 기능을 기존 설계에 맞게 넣기 위한 전제 단계, 요구사항분석과 설계 사이)을 끼워 5단계로 구성한다.

| # | role_name | 담당 Phase | skill_tags(재사용) | 신규 필요 | 산출물 content_type |
|---|---|---|---|---|---|
| 1 | 요구사항분석가 | Phase 0-1(문제 정의, SPEC 초안) | `외부검색`, `일반웹검색`, `spec-driven-artifacts`, `rag`(과거 유사 요구사항 Deliverable 검색) | 없음 | `markdown` |
| 2 | 코드분석가 | 요구사항→설계 전제(기존 코드 이해) | `rag`(과거 설계/산출물 검색) | **저장소 코드 읽기 Tool(§6-1)** | `markdown` |
| 3 | 설계자 | Phase 2-3(SPEC/ADR) | `spec-driven-artifacts`, `rag`, `외부검색`(레퍼런스 아키텍처 조사) | 없음 | `markdown` |
| 4 | 구현자 | Phase 4-5(실제 코드 작성) | `tdd-ai-workflow-checklist`, `rag`(설계 문서 참조) | **저장소 코드 읽기 Tool(§6-1, 선택)** | `code` |
| 5 | QA/검증자 | Phase 6-8(Gate A-G 해석, 조치 제안) | `recommend-fix-workflow`, `gate-improvement-loop`, `checklist-confidence-audit`, `threshold-realism-review` | 없음 | `markdown` |

**구현자 Agent의 의도된 한계**: 이 Agent는 실제 파일을 수정하지 않는다 — CrewAI Agent는 Tool Pool에 등록된 도구만 호출할 수 있고, 지금 어떤 Tool도 파일시스템 쓰기 권한을 주지 않는다(HAIR-Platform의 HITL 원칙과 일치 — "AI가 결과물을 내고, 사람이 승인 후 실제로 적용"). 구현 Agent의 산출물은 `deliverables.content_type='code'`인 **코드 텍스트 Deliverable**이고, 그걸 실제 저장소에 반영하는 건 사람의 몫이다. 이게 버그가 아니라 설계다 — Agent-Evaluator 하니스가 말하는 "HITL 관문"과 정확히 같은 경계.

**검증 Agent가 실제로 하는 일(코드 기준)**: Gate A-G 점수(`agent_evaluations.gate_scores`/`tcr`)는 구현 Agent가 Deliverable을 낸 "순간 이미" 자동으로 계산돼 있다(`agent_evaluation.run_and_score()`가 `run_agent_execution_job()`/`run_team_crew_job()` 안에서 호출됨). 검증 Agent의 Task는 그 Deliverable을 입력으로 받아, 낮은 Gate를 짚어 `recommend_fix` MCP 도구(이미 Tool Pool에 `recommend-fix-workflow` 스킬로 연결돼 있음)로 조치를 조회하고, 사람이 승인/반려를 결정할 수 있게 **사람이 읽을 요약**을 만든다. 이렇게 하면 "또 다른 LLM 호출로 품질을 재평가"하는 중복 없이, 결정론적 Gate 숫자 + LLM 해석을 분리해 둘 다 신뢰할 수 있다.

### 2-0. QA/검증자가 해석하는 Gate A-G(하니스 메서드 정의 그대로)

| Gate | 채점 대상 |
|---|---|
| A 목표달성 | 지시한 것을 실제로 해냈는가 |
| B 행동무결성 | 루프·상태불일치·스코프이탈 없이 일관됐는가 |
| C 신뢰성 | 재현가능·장애내구성 |
| D 성능계약 | 지연시간/자원/비용 약속범위 |
| E 보안경계 | 권한·컴플라이언스 경계 |
| F 다중에이전트협업 | 합의·역할·충돌해결(Dev Crew처럼 여러 Agent가 한 Task를 처리할 때 특히 이 Gate가 중요해진다) |
| G 관측성 | 사후 설명·진단 가능성 |

`agent_evaluations.gate_scores`에 이미 A-G 전부가 저장돼 있다 — QA/검증자 Agent는 이 중 낮은 Gate를 짚어 `recommend_fix`로 조치를 제안하고, 어떤 Gate가 왜 낮은지를 사람이 읽을 말로 번역하는 역할이다(점수 재계산은 하지 않는다).

### 2-0-1. 6대 산출물 — 저작/승인 매트릭스(R=작성 책임, A=승인 책임)

하니스 메서드 부록 G가 정의하는 6대 산출물은 "누가 썼는가(R)"와 "누가 승인했는가(A)"를 분리한다 — 이 기획에서도 **A는 절대 Agent가 아니다**(축③, §1-2).

| 산출물 | R(작성) | A(승인) | 생성 단계 |
|---|---|---|---|
| Spec | 요구사항분석가 Agent | 사람(QA·거버넌스/결재자) | 요구사항분석 |
| 실패모드 카탈로그 | 요구사항분석가 Agent | 사람 | 요구사항분석 |
| 골든셋 | 요구사항분석가 Agent | 사람 | 요구사항분석 |
| 의존관계 지도 | 코드분석가 Agent | 사람 | 코드분석 |
| ADR | 설계자 Agent | 사람 | 설계 |
| 세션 목표 템플릿 | 구현자 Agent(설계자 참여) | 구현자 Agent·사람 공동 | 구현 |

실무 적용: Dev Crew가 시퀀셜로 산출물을 만들 때마다 **단계마다** `approvals.py` 결재(또는 최소한 `deliverable_comments`로 사람 확인)를 끼워 넣는 것을 권장한다 — "구현까지 다 끝난 뒤 한 번에 승인"이 아니라 하니스 메서드가 원래 의도한 "단계별 HITL"에 맞춘다. HAIR-Platform 결재는 Task 1건당 체인이라 지금은 "Task 전체 완료 후 1회 결재"가 기본 흐름이지만, Dev Crew 5단계를 **Task 5개의 체인**(요구사항분석 Task → 코드분석 Task → … )으로 쪼개 등록하면 단계별 결재가 자연스럽게 들어간다(`task_dependencies`로 순서 고정). 이 분리는 데이터 작업이라 로드맵 Phase 2에서 바로 시도할 수 있다(§7).

### 2-1. Dev Crew — Team 등록안

```
teams.create_team("Dev Crew — 5단계 개발팀", "요구사항분석→코드분석→설계→구현→검증")
team_members: agent(요구사항분석가), agent(코드분석가), agent(설계자), agent(구현자), agent(QA/검증자)
```

**시퀀셜 실행(기본 추천)**: `run_crew(agent_ids=[1,2,3,4,5], task_descriptions=[...5개...], process="sequential")`. `Task.context`로 요구사항분석 결과가 코드분석에, 코드분석 결과가 설계에 … 자동 전달된다 — 별도 수작업 핸드오프가 없다.

**위임 실행(대안)**: QA/검증자를 `manager_agent_id`로 지정한 `process="hierarchical"` — 검증자가 작업 전체를 조율하며 필요할 때 요구사항분석가/설계자에게 동적으로 재질의(`allow_delegation=True`)한다. 품질 기준이 모호해 반복 왕복이 필요한 과제(요구사항이 자주 바뀌는 과제)에 적합. 다만 CrewAI hierarchical은 비용 예측이 더 어렵다(ADR 결정 11 한계, 기존 문서에 이미 기록됨) — 기본값은 시퀀셜로 두고, 모호성이 큰 과제에서만 위임형을 선택하도록 안내한다.

**이견 처리(하니스 메서드 에스컬레이션 4단계, Agent 버전)**: Agent 간 결과가 충돌하거나(예: 코드분석가가 "불가능"이라 하는데 설계자는 "가능"이라 가정) 위임형 매니저(QA/검증자)가 자체적으로 해소 못 하면, Level 0(단일 소유자=Task 담당자가 결정, 대부분 여기서 끝남) → Level 1(담당자간 직접 조율 — 대화(§4-2)로 사람이 양쪽 Agent를 불러 동시 질의) → Level 2(QA·거버넌스 역할의 사람이 재정) → Level 3(조직 단위 에스컬레이션)로 올린다. **LiveGuardrail류 안전장치(승인 체인·self-approval 방지)가 정당한 작업을 막는 경우에도 우회하지 말고 반드시 에스컬레이션한다**는 원칙은 그대로 유지한다.

**HITL 유형 구분**: 하니스 메서드는 HITL을 문서형(diff/문서를 사람이 직접 판단)과 정책형(게이트 실행결과 수치를 사람이 판단)으로 나눈다. Dev Crew에선 요구사항분석/설계 단계 결재가 **문서형**(Spec/ADR 문서 자체를 읽고 판단), 검증 단계 결재는 **정책형**(Gate A-G 수치·TCR을 보고 판단)이다 — 같은 "결재"라도 승인자가 봐야 할 게 다르므로, Task 결재 화면에 Gate 점수(정책형 단계)와 Deliverable 원문(문서형 단계)을 구분해 보여주는 것이 바람직하다(현재 Web UI는 둘 다 같은 화면에서 보여주므로 추가 코드 변경 없이도 가능 — §6 범위 밖).

**운영 경로**: `teams.py`에 Dev Crew를 등록 → 실제 개발 Task에 `resource_type='team'`으로 배정 → F7 결재(팀 리더 지정 가능, `assignments.team_leader_id`) → 확정 후 "팀 Agent 실행(Crew)"으로 `run_crew_for_team()` 호출. 결과가 Task의 Deliverable+결재 체인+Gate A-G로 자동 연결된다(§0 표 참고) — 새 파이프라인을 만들 필요가 없다.

**Team 멤버별 다른 지시가 필요한 이유**: `run_crew_for_team()`은 모든 멤버에 같은 설명을 준다(한 Task를 5개 역할이 다른 관점으로 보게 하려는 의도와 안 맞을 수 있음). 지금 당장은 `run_crew()`를 **직접** 호출(5개 Agent + 5개 역할별 task_description)하는 방식으로 우회 가능 — 이건 코드 변경이 필요 없다(이미 지원되는 파라미터). `run_crew_for_team()`을 "멤버별 다른 지시"까지 지원하도록 확장하는 건 §6-2의 선택적 개선 항목으로 남긴다.

---

## 3. 일반 업무 전문 Agent

| # | role_name | skill_tags(재사용) | 신규 필요 | 산출물 content_type |
|---|---|---|---|---|
| 6 | 시장조사원 | `외부검색`(Wikipedia), `일반웹검색`(Parallel AI MCP — 최신 뉴스/일반 웹), `rag`(과거 조사 자료 재사용) | 없음 | `markdown` |
| 7 | 사업기획가 | `rag`(과거 기획서 참조), `외부검색`, `일반웹검색` | 없음 | `markdown` |
| 8 | 전략수립가 | `graphdb`(조직 관계 조회 — "우리 조직의 가용 인력/스킬 현황"을 전략 수립에 바로 활용), `rag`, `외부검색` | 없음 | `markdown` |

**GraphDB 재사용의 의미**: `graphquery.context_for_prompt()`는 조직도(`users.manager_id`)·배정 이력(`assignments`)·Human Pool을 그래프로 조회한다 — 개발 Agent에겐 안 맞던 이 도구가 전략수립 Agent에겐 정확히 맞는 용도다("우리 조직이 지금 어떤 역량을 갖고 있는지"는 전략 수립의 기초 자료). 코드분석 Agent 기획 때 GraphDB를 억지로 끼워 맞추지 않고(§2에서 제외) 전략수립 Agent로 옮긴 것이 이 기획의 핵심 판단 중 하나다.

### 3-0. 일반 업무로 확장해도 되는 근거(하니스 메서드 §2)

하니스 메서드는 스스로 "① 철학·② 프로세스·③ 역할·④ 실천법·⑤ 산출물·⑥ 도구·⑦ 지표·⑧ 거버넌스·⑨ 학습경로"라는 **9대 구성요소**로 완전정의되고, "AOO Stack(agent-evaluator)은 참조 구현일 뿐, 다른 도구로도 같은 9개를 채울 수 있다"고 명시한다 — 즉 방법론 자체가 개발 도메인에 종속되지 않는 범용 틀이라는 근거가 책에 직접 있다. 핵심 패턴은 "①실행 전 사람이 전제를 정함(Spec류 문서) → ②Tier 1 분석 → ③Tier 2 실행 → ④정량 Gate로 증명"이며, 이는 시장조사·사업기획에도 "조사 전제(기획 Brief) → 시장조사/분석 → 기획안 작성 → 정량 검증" 구조로 그대로 옮길 수 있다.

**단, Gate A-G 자체는 코드 품질 전용이라 그대로 못 쓴다** — 지금 HAIR-Platform에는 사업기획 산출물용 자동 채점기가 없다(정직하게 인정). 당장은 Gate A-G의 자리를 **Task 결재(HITL, 문서형)**가 대신한다 — 즉, Biz Crew의 "정량 검증" 단계는 지금 사람이 전적으로 메운다. 이후 "사업기획용 평가 기준"(예: 시장 데이터 출처 신뢰도, 추정 근거의 명시 여부 같은 체크리스트)을 만들면 Gate A-G와 유사한 자동 사전검사를 추가할 수 있다 — 이건 이 기획 범위 밖의 후속 과제로 남긴다.

### 3-1. Biz Crew — Team 등록안(위임형 추천)

```
teams.create_team("Biz Crew — 경영기획팀", "시장조사+사업기획을 전략수립이 종합")
team_members: agent(시장조사원), agent(사업기획가), agent(전략수립가)
```

`process="hierarchical", manager_agent_id=전략수립가` — 전략수립가가 매니저로서 시장조사원·사업기획가에게 작업을 위임하고 결과를 종합한다(매니저는 직접 실행하지 않고 조율만 — CrewAI 모델과 정확히 일치). 사업 기획은 본질적으로 "총괄이 하위 조사를 지시하고 종합"하는 구조라 위임형이 시퀀셜보다 자연스럽다. 시퀀셜이 더 맞는 경우(예: 시장조사 완료가 사업기획의 전제조건으로 고정된 과제)는 §2-1과 같은 방식으로 `process="sequential"`로 바꿀 수 있다 — Team 구성은 그대로 두고 실행 시점에 `process`만 바꾸면 된다(재등록 불필요).

---

## 4. 사람과 N:N 협업

HAIR-Platform에는 이미 두 가지 N:N 통로가 있고, 이 전문 Agent들은 둘 다 그대로 쓸 수 있다.

### 4-1. Task 결재 파이프라인(정식 업무)

여러 사람(팀 리더+팀원)이 하나의 Task에서 Dev Crew/Biz Crew의 Deliverable을 같이 보고, 댓글(`deliverable_comments`)로 토론하고, 결재(F7 다단계)로 승인/반려한다 — `task_participants`로 사람을 더 초대할 수도 있다(결재자 자동 라우팅은 조직도 기준, 참가자 초대는 가벼운 멤버십). 이게 "N명의 사람이 Agent 팀의 결과물을 함께 검토"하는 N:N이다.

### 4-2. Conversational Workspace(가벼운 상담)

`conversations.py`가 이미 지원하는 조합:
- **대화에 여러 사람 + 전문 Agent 개별 + Team 전체를 동시에 초대**할 수 있다(`conversation_participants.participant_type IN ('human','agent','team')`).
- 예: 기획 회의 중 사람 3명이 같은 대화에서 `@전략수립가`로 직접 질의하거나, `@Biz Crew`로 Team 전체를 호출해 위임형 실행 결과를 받아볼 수 있다 — 결재 절차 없이 가볍게.
- `@멘션`마다 독립된 `conversation_jobs` 행으로 **병렬** 실행되므로, 사람 A가 `@요구사항분석가`를 부르고 동시에 사람 B가 `@검증자`를 불러도 서로 안 막힌다(실제 그룹 채팅 모델).
- `search_mentionable()`이 역할/스킬 텍스트로도 검색되므로, 사람이 Agent의 정확한 이름을 몰라도 "코드분석"/"전략"으로 검색해 호출할 수 있다.

**권장 사용 구분**: 결과가 공식 산출물로 남고 결재가 필요하면 §2-1/§3-1의 Task 경로, 빠른 질의·상담이면 대화 경로 — 둘 다 같은 `agent_pool`/`teams` 등록을 공유하므로 전문 Agent를 두 번 만들 필요가 없다.

---

## 5. Skill Pool에 이미 있는 절차 지식 — 그대로 태깅

`db.py`의 `_seed_default_pools()`가 이미 15개 Harness Methodology 절차 Skill을 `augmentation_mode='none'`으로 심어뒀다(코드 변경 없이 바로 쓸 수 있음). 이 기획의 각 전문 Agent에 다음과 같이 태그를 붙인다(데이터 작업):

| Skill | 붙일 Agent |
|---|---|
| `spec-driven-artifacts` | 요구사항분석가, 설계자 |
| `tdd-ai-workflow-checklist` | 구현자 |
| `recommend-fix-workflow` | QA/검증자 (이미 `tool` 모드로 `recommend_fix` MCP 연결됨 — 유일하게 **실행 가능한** 도구가 붙은 절차 스킬) |
| `gate-improvement-loop`, `checklist-confidence-audit`, `threshold-realism-review` | QA/검증자 |
| `harness-autopilot` | (선택) QA/검증자 또는 별도 "파이프라인 코디네이터" — Phase 0-8 전체 진행 상태 판단. Dev Crew의 hierarchical 매니저로 QA/검증자 대신 이 역할을 쓰는 변형도 가능(§2-1 대안) |

나머지(`eval-run-directory-hygiene`, `sync-drift-check` 등)는 운영/CI 성격이라 지금 기획한 8개 Agent에는 직접 붙이지 않는다 — 필요해지면 그때 추가(이것도 데이터 작업).

---

## 6. 코드 변경이 필요한 지점(정직하게 분리)

### 6-1. 코드분석/구현 Agent용 "저장소 코드 읽기" Tool (필수성: 코드분석 Agent에 필수, 구현 Agent에는 선택)

지금 Tool Pool의 native 도구 3종(VectorDB/GraphDB/Wikipedia)은 전부 **DB 안의 데이터**를 다룬다 — 실제 git 저장소의 소스 파일을 읽는 기능이 없다. 코드분석 Agent가 "기존 코드가 어떻게 생겼는지"를 보려면 다음 중 하나가 필요하다:
- (a) **읽기 전용 파일시스템 MCP 서버**를 `tool_pool`에 `kind='mcp_stdio'`로 등록(예: 범용 filesystem MCP 레퍼런스 서버, 저장소 루트로 스코프 제한) — 가장 적은 코드.
- (b) 새 native Tool(`pool_registry.NATIVE_TOOL_FACTORIES`에 함수 추가) — 예: `ripgrep` 래퍼로 코드 검색만 노출(파일 쓰기 권한 없음, HITL 원칙 유지에 더 안전).

이 기획은 (b)를 권장한다 — 범용 파일시스템 MCP는 쓰기 권한까지 함께 열릴 위험이 있고, HAIR-Platform의 다른 모든 Tool이 "읽기 전용 조회"라는 일관된 경계를 지키고 있다(VectorDB/GraphDB/Wikipedia 모두 조회만). 구현 범위는 이 기획 문서 밖(별도 작업 승인 필요).

### 6-2. `run_crew_for_team()`의 멤버별 지시 분배 (필수성: 선택)

§2-1에서 설명했듯 지금은 `run_crew()` 직접 호출로 우회 가능하므로 **당장 막히지 않는다**. 다만 Web UI "팀 Agent 실행(Crew)" 버튼이 내부적으로 `run_crew_for_team()`만 호출하므로, 운영 화면에서 바로 "역할별 다른 지시"를 주려면 이 함수(또는 그 버튼이 호출하는 API)에 `team_members.role_label`을 반영해 Agent별로 다른 description을 구성하는 확장이 필요하다. 지금 기획의 1차 적용(§7 로드맵 Phase 1-2)에는 영향 없음 — 운영이 자리잡은 뒤 개선 과제로 제안한다.

---

## 7. 도입 로드맵

| Phase | 내용 | 코드 변경 | 상태 |
|---|---|---|---|
| 1 | 8개 전문 Agent를 `agent_pool`에 등록(role_name/skill_tags/framework='crewai') + 스킬이 가리키는 도구를 `agent_tool_links`에도 명시적으로 연결 | 없음(데이터만) | **완료(2026-10-12)** — `agent_pool.id=13~20`, 아래 §8 참고. Team(Dev Crew/Biz Crew) 등록은 범위에서 제외(이번 요청은 Agent 관리 등록까지) — 필요 시 별도 요청으로 진행 |
| 2 | 실제 Task 1-2건에 Dev Crew를 시퀀셜로 배정→실행해 Gate A-G 점수와 결재 흐름을 실측 확인. Biz Crew는 대화(Conversational Workspace)에서 위임형으로 먼저 가볍게 시험 | 없음 | 미착수 |
| 3 | §6-1(코드분석용 읽기 전용 Tool) 추가 — 코드분석 Agent가 실제 저장소를 참조하도록 보강 | 있음(native Tool 1개) | 미착수 |
| 4 | 운영 지표(Gate A-G 추이, 결재 소요시간, 대화 호출 빈도)를 보고 §6-2 등 추가 개선 결정 | 필요시 | 미착수 |

## 8. Phase 1 등록 결과(`agent_pool`, 2026-10-12)

| id | role_name | skill_tags | 연결된 Tool(`agent_tool_links`) |
|---|---|---|---|
| 13 | 요구사항분석가 | spec-driven-artifacts, rag, 외부검색, 일반웹검색 | VectorDB 지식검색, 외부 자료 검색(Wikipedia), 일반 웹 검색(Parallel Search) |
| 14 | 코드분석가 | rag | VectorDB 지식검색 |
| 15 | 설계자 | spec-driven-artifacts, rag, 외부검색 | VectorDB 지식검색, 외부 자료 검색(Wikipedia) |
| 16 | 구현자 | tdd-ai-workflow-checklist, rag | VectorDB 지식검색 |
| 17 | QA/검증자 | recommend-fix-workflow, gate-improvement-loop, checklist-confidence-audit, threshold-realism-review | Gate 조치 지식 조회(recommend_fix) |
| 18 | 시장조사원 | 외부검색, 일반웹검색, rag | 외부 자료 검색(Wikipedia), 일반 웹 검색(Parallel Search), VectorDB 지식검색 |
| 19 | 사업기획가 | rag, 외부검색, 일반웹검색 | VectorDB 지식검색, 외부 자료 검색(Wikipedia), 일반 웹 검색(Parallel Search) |
| 20 | 전략수립가 | graphdb, rag, 외부검색 | GraphDB 관계조회, VectorDB 지식검색, 외부 자료 검색(Wikipedia) |

`status='active'`로 즉시 등록했다(F9 온보딩 대기 없이 — `data_admin.py`의 기존 "직접 active로 등록" 패턴과 동일, 사람이 직접 판단해 만든 전문 Agent라 생성 후 별도 검토 단계를 요구하지 않는다). `spec-driven-artifacts`/`tdd-ai-workflow-checklist`/`gate-improvement-loop`/`checklist-confidence-audit`/`threshold-realism-review`는 `augmentation_mode='none'`인 절차 스킬이라 연결된 Tool이 없다(의도된 정상 상태 — Agent가 참고하는 절차 지식일 뿐, 호출 가능한 도구가 아니다). `audit_log`에 `action='specialist_agent_registered'`로 8건 모두 기록했다(`actor_id=1`, admin).

등록 스크립트는 1회성이라 저장소에 남기지 않았다(스크래치패드에서 실행 후 폐기) — 재현이 필요하면 위 표의 role_name/skill_tags를 그대로 `POST /api/briefs`→`.../tasks`→`.../generate`(또는 Web UI "AI 에이전트 관리" 화면의 수동 등록 경로)로 다시 만들 수 있다.
