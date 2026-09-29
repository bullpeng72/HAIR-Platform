# SPEC — HAIR 플랫폼 (가칭: AgentForge Workforce)

> 사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼의 명세.
> AgentForge(《AgentForge 실습서》 동봉 저장소)의 자매 프로젝트로, **별도 저장소**에서 시작한다.
> 《하네스 메서드》 §15.1 Spec 형식을 따른다.
> 상태: DRAFT — `spec_review` 승인 전까지는 확정이 아니다.
> 근거 문서: "HAIR 플랫폼 기획안"(Claude 문서, 5차 재검토 완료) — 경쟁 매트릭스·데이터 흐름 검토·설계 결정이 전부 이 문서에 반영됨.

## 1. 목표

자연어 브리프 하나로 업무를 등록하고, 사람과 AI Agent 후보를 함께 매칭·배분하며, 결재라인 승인을 거쳐 확정하고, 성과를 통합 리뷰하는 협업 플랫폼 **HAIR**를 만든다. AgentForge가 이미 검증한 파이프라인(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·FastAPI Web UI 패턴)을 재사용하되, "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다. 브리프 제출부터 신규 Agent 생성까지 포함한 전 기능을 **인증된 Web UI에서 조작 가능**하게 하는 것이 AgentForge와의 핵심 차이다.

## 2. 배경

- 외부 리서치(2026-09)로 "HAIR"라는 용어 자체는 업계 표준이 아니나(iKangAI 등 니치 조어, PeopleMatters의 "Human-AI Recruitment"와 정의 혼재), OpenAI Frontier·Workday Agent System of Record·SuperOrgs·ServiceNow Agentic Workforce Management·Salesforce Agentforce 등 "AI Agent를 관리 대상 리소스로 다루는" 상용 플랫폼이 이미 다수 존재함을 확인했다.
- 경쟁 매트릭스 분석 결과, 조사된 플랫폼 대부분은 "생성" 또는 "배분" 또는 "모니터링" 중 일부만 강점을 갖고, **팀간 Agent 유동적 재배치**·**완전한 UI 조작(생성 포함)**·**국내 조직 결재문화 대응**은 시장 공백으로 확인됐다.
- 이 프로젝트는 AgentForge가 이미 구현·검증한 컴포넌트를 재사용해 개발 리스크를 낮추고, 위 공백을 메우는 것을 목표로 시작한다(시장 경쟁력과 별개로, AgentForge와 마찬가지로 방법론 재사용 사례로서의 가치도 겸한다).
- 기획 단계에서 5차에 걸친 재검토를 거쳐 데이터 흐름의 논리적 모순(배정-승인 순서, 역할 매칭 알고리즘 부재, 스키마 불일치 등)을 전부 해소했다 — 상세 근거는 "HAIR 플랫폼 기획안" 문서의 "착수 전 재검토"~"5차 재검토" 섹션 참고.

## 3. 범위

**In-scope**
- F1–F14(§4) 전 기능을 인증된 Web UI에서 조작
- AgentForge 파이프라인(Spec Interpreter·Code Generator·Verifier) 재사용, AgentContract(Pydantic v2)·Pool(`results/*.json`+SQLite 인덱스, 배치성) 그대로 계승
- 신규 구축: Human Pool, ResourceCandidate 공통 인터페이스, Resource Designer+Review UI, Assignment Engine(+`assignments` 공유 테이블), Approval Workflow, Job Queue(+샌드박스), Auth+RBAC, Audit Log, `assignment_history`
- 출력 프레임워크: AgentForge와 동일하게 **CrewAI 하나만**(스코프 확장 안 함)
- 버전 v0–v4(§9)

**Out-of-scope (명시적 비목표)**
- 다중 조직/테넌트 지원 — 단일 팀/조직 로컬 또는 온프레미스 실행 전제(AgentForge의 L5 조직확장 밖 원칙 계승)
- 알림/통지 체계(이메일·Slack), 외부 HR/SSO 연동, BI 리포팅 API — 5차 재검토에서 식별했으나 1차 출시 범위 밖으로 명시적 이월(오픈 이슈, §10 참고)
- CrewAI 이외 프레임워크 출력, 크로스팀 Pool 공유(포트폴리오 단위) — AgentForge 범위 그대로 계승
- F14 점수 breakdown의 배정 후보 본인 공개 — 1차는 매니저(요청자) 전용, 본인 공개는 백로그

## 4. 기능 요구사항

| ID | 요구사항 | 대응 컴포넌트 | AgentForge 재사용 |
|---|---|---|---|
| F1 | Web UI 브리프 입력 폼 제출 → GoldenData 파싱 결과 미리보기 | Spec Interpreter + 신규 POST API | 로직 재사용, API 신규 |
| F2 | Review UI에서 Resource Designer가 산출한 Task 분해 결과(필요 역할·인원)를 검토·조정(후보 매칭은 포함하지 않음 — 매칭은 F6, ADR 결정 1-A) | Resource Designer + Review UI(신규) | 확장 필요 |
| F3 | Pool(Agent+사람) 통합 검색·필터(Human Pool 포함) | Composer→Resource Pool 확장 | 확장 필요 |
| F4 | 생성 버튼 → 신규 Agent 생성 트리거, Job 상태(pending/running/succeeded/failed/partial) 추적, 실패 역할만 재시도 | Code Generator+Verifier + Job Queue(샌드박스 실행) | 로직 재사용, 비동기+샌드박스 신규 |
| F5 | 조직도 화면에서 배치 조회·드래그 재배치(F6과 동일 `assignments` 테이블 공유) | Org Chart(신규) | 신규 |
| F6 | 배분 화면에서 매니저(RBAC role)가 후보를 배정 요청(`requested`) → F7 승인 후에만 `confirmed`로 확정 | Assignment Engine(신규) — 매칭: 완전일치 우선 → 없으면 가중치 스코어링(가용성40·적합도35·비용25)로 Top3 제시(F14: 점수 breakdown 함께 반환) → 후보 없으면 F4 제안. 사람 후보의 performance_score가 null이면 적합도 항목을 제외하고 가용성60·비용40으로 재조정 | 신규(AgentForge Composer 에스컬레이션 정신 계승) |
| F7 | 승인 화면에서 조직도 매니저 관계 기반 자동 라우팅 결재라인 요청·검토·승인/반려, 확정 시 F6 상태 갱신 | Approval Workflow(Autopilot 승인 kind 확장) + RBAC | 확장 필요 |
| F8 | 성과리뷰 화면에서 Gate A–G + 비즈니스 KPI 조회(사람 데이터는 동의 기반만 표시) | Performance Review(eval_wiring 재사용) | 부분 재사용 |
| F9 | Pool 화면에서 Agent 온보딩(등록) 승인, 오프보딩(deprecate) 버튼 | Pool + PoolIndex 확장 | 확장 필요 |
| F10 | 비용 화면에서 Agent 비용·사용량 조회(1차: 사람 비용은 수동입력만, HR연동은 백로그) | Cost Tracker(신규) | 신규 |
| F11 | 위 화면 전부를 인증+RBAC 적용된 Web UI 셸에서 제공 | Web UI + Auth/RBAC 미들웨어 | 패턴재사용 + 인증/RBAC 신규 |
| F12 | 매니저/HR 담당자가 사람 리소스를 Web UI 폼으로 등록·수정(역할·스킬태그·가용성) | Human Pool 온보딩 화면(신규) | 신규 |
| F13 | 감사로그 화면에서 모든 쓰기 액션(F4·F6·F7·F9)의 행위자·시각·이전값·이후값 조회(Admin·매니저 role만 접근) | Audit Log(신규, append-only) | 신규 |
| F14 | F6 배분 화면에서 Top3 후보 제시 시 점수 breakdown(가용성/적합도/비용)을 매니저에게 함께 표시 | Assignment Engine 응답 확장 | 신규(F6 확장) |

## 5. 비기능 요구사항

| ID | 요구사항 | 검증 방법 |
|---|---|---|
| NF1 | 동일 브리프 재현성 | Gate C, seed 고정 |
| NF2 | Pool 재사용 에이전트 회귀 감지 | `agent-eval gate --baseline-version` |
| NF3 | 결재라인 우회 방지 — F6 배정은 `requested` 상태로만 생성되고 F7 승인 후에만 `confirmed`로 전환되어 조직도·Pool·비용에 반영된다 | 상태전이 단위테스트, E2E 우회 시도 테스트 |
| NF4 | 사람·Agent 성과 지표 공정성(같은 기준으로 비교, 편향 방지) | Phase 4 배정 공정성 지표 |
| NF5 | 모든 쓰기 액션은 RBAC(Admin/매니저/팀원/뷰어 4종, Human Pool 직급과 연동) 기반 인증된 사용자만 트리거 가능 | 권한별 API 접근 테스트 |
| NF6 | F4는 비동기 + Job 상태머신(pending/running/succeeded/failed/partial), 실패 역할만 재시도 | Job Queue 통합테스트 |
| NF7 | 동일 역할 동시 생성요청 시 idempotency key로 Pool 경합/중복생성 방지 | 동시성 테스트 |
| NF8 | Verifier smoke-test는 반드시 격리된 샌드박스(컨테이너, 리소스 쿼터·네트워크 제한·타임아웃)에서만 실행 | 샌드박스 탈출 방지 보안테스트(Phase 4) |
| NF9 | 사람 성과데이터 수집은 사전 동의(opt-in) 필요, 최소수집 원칙 | Phase 1 법무 검토, 동의 플로우 E2E 테스트 |
| NF10 | 모든 쓰기 액션은 `audit_log`에 append-only로 기록되며 수정·삭제 불가 | append-only 제약 테스트(DB 트리거/권한) |
| NF11 | `assignments` 상태 변경은 `assignment_history`에 이력으로 남아야 하며, UPDATE로 현재 상태만 덮어써서는 안 된다 | 이력 테이블 정합성 테스트 |
| NF12 | F13 감사로그는 Admin·매니저 role만 접근 가능하다(팀원·뷰어는 조회 불가) | RBAC 접근제어 테스트 |
| NF13 | F6/F7 배정 확정 처리 시 `assignments`·`assignment_history`·`audit_log` 3중 기록은 하나의 DB 트랜잭션으로 커밋(부분 실패 시 전체 롤백) | 트랜잭션 원자성 테스트(강제 실패 주입) |

## 6. 실패 모드 카탈로그

| 실패 모드 | 증상 | 대응 |
|---|---|---|
| 배정-승인 순서 위반 | 승인 없이 `assignments`가 바로 `confirmed`로 기록됨 | NF3, 상태전이 화이트리스트 검증 |
| 사람 후보 null 점수 처리 누락 | performance_score가 null인 사람 후보가 매칭에서 항상 탈락하거나 예외 발생 | F6 매칭 알고리즘 유닛테스트에 null 케이스 포함 |
| 3중 기록 트랜잭션 부분 실패 | 감사로그·이력·실제 상태가 서로 어긋남 | NF13, 강제 실패 주입 테스트(Phase 4) |
| 샌드박스 탈출 | Verifier smoke-test 실행 중 격리 우회, 호스트 자원 접근 | NF8, 컨테이너 격리 보안테스트 |
| RBAC 서버측 미검증 | 클라이언트 UI만 숨기고 API는 열려있어 권한 우회 가능 | 모든 쓰기 API에 서버측 RBAC 미들웨어 강제, 우회 시도 테스트 |
| Human Pool 미등록 상태에서 매칭 시도 | F12 없이 F6 실행 시 사람 후보 0명, 사용자가 원인 파악 못함 | F3/F6 UI에 "등록된 사람 리소스 없음" 명시적 안내 |
| 매니저-RBAC role 개념 혼동(구현 시) | F7 자동라우팅이 `manager_id`(직속상사) 대신 RBAC role만으로 잘못 라우팅 | ADR에서 두 개념을 별도 필드로 명시(§10), 통합테스트로 구분 검증 |

## 7. Autopilot 활용 계획

| Phase | 무엇을 | 승인 kind | 기간(안) |
|---|---|---|---|
| 0 부트스트랩 | 신규 저장소 생성, AgentForge에서 Spec Interpreter/Code Generator/Verifier/Pool 계층 추출·공용패키지화 검토 | — | 1주 |
| 1 분석 | 본 SPEC.md 확정 | `spec_review` | 1주 |
| 2 설계 | Resource Designer/Human Pool/Assignment Engine/Approval Workflow/Job Queue/Auth/Audit Log ADR + RBAC 역할모델·ResourceCandidate 공통인터페이스·Pool 소스오브트루스 정책 확정 | `adr_review` | 2주 |
| 3 개발(TDD-AI) v0 | Auth 최소구현(팀 로그인)+RBAC 4종 역할 + F1 브리프 제출 폼 + F12 Human Pool 온보딩 폼 + 최소 승인(단일 승인자) 워크플로우 | — | 2–3주 |
| 3 v1 | Assignment Engine 매칭 알고리즘 구현(F2 Review UI 포함) + F3 Pool 브라우저에 Human Pool 통합 검색 + F14 점수 breakdown 응답 확장 | — | 2주 |
| 3 v2 | Org Chart + 배분 UI(F5·F6, 공유 `assignments` 테이블) + `assignment_history` 이력 테이블 도입 | — | 2주 |
| 3 v3 | Approval Workflow 다단계 결재라인 확장(F7) + Job Queue+샌드박스 실행환경 도입, F4 비동기 전환 + F13 감사로그 구축 | — | 3주 |
| 3 v4 | Performance Review(F8)+Cost Tracker(F10)+F9 온보딩/오프보딩 버튼 | — | 2주 |
| 4 검증 | 완료(부분) — Gate A–G는 의존성 갭으로 불가, 배정 공정성 지표(NF4) 구현, NF7 idempotency 갭 발견·수정, 샌드박스 우회 방지 회귀 검증. 상세: `docs/LIMITS.md` | `threshold_review`(계획적 발동, pytest 골든셋 42건 근거로 대체) | 2주 |
| 5 팀·PR CI | 완료(부분) — `.github/workflows/ci.yml`(pytest+ruff+black) 구축. **2인 팀 실전 클레임 충돌은 불가**(단독 세션, `docs/LIMITS.md` §5) | — | 1주 |
| 6 Skills화 | 완료(부분) — `rbac-permission-audit` 1건 발굴·`skill_merge`(아래 §8 참고), 나머지 3건은 3회+ 기준 미충족으로 보류 | `skill_merge` | 진행 중 수시 |
| 7 릴리스 후보 | v3→v4 배포 전 검토(인증/RBAC/샌드박스/감사로그 포함 여부 확정) | `release_hold` | 1주 — **사람 승인 대기** |
| 8 운영 | 최종 릴리스 | `deploy` | — — **사람 승인 대기** |

총 예상 기간(Phase 0–7): 약 18–20주.

## 8. Skills 활용 계획

**AgentForge에서 그대로 재사용**: `spec-driven-artifacts`(본 SPEC 작성) · `harness-gate-ci` · `gate-improvement-loop` · `recommend-fix-workflow` · `abtest-decision-workflow` · `tdd-ai-workflow-checklist` · `config-silent-noop-check` · `eval-run-directory-hygiene` · `claims-audit-ci` · `checklist-confidence-audit` · `threshold-realism-review` · `sync-drift-check` · `harness-autopilot` · `unattended-session-recovery`.

**신규 발굴 완료**(`skill_merge`, Phase 6): `rbac-permission-audit`(`.claude/skills/rbac-permission-audit/SKILL.md`) — v0~v4 5개 버전 전부에서 "역할별 접근 경계"를 반복 검증한 패턴이 3회+ 기준을 채워 스킬화했다.

**아직 스킬화하지 않음**(3회+ 기준 미충족, 원칙4 — 억지로 미리 만들지 않는다):
- `human-agent-assignment-check` — Assignment Engine 매칭 로직은 v1에서 한 번 구축한 뒤 반복 재검증한 적이 없다.
- `org-chart-sync` — F5/F6 assignments 공유 정합성도 v2 한 번뿐이다.
- `async-job-status-ui` — Job Queue 상태머신 패턴도 v3 한 번이다.

## 9. 버전 궤적 / 골든셋

| 버전 | 내용 | 골든셋 최소 사례 |
|---|---|---|
| v0 | Auth 최소구현+F1 브리프 제출+F12 온보딩 폼, 규칙기반 스텁 | 4건(파이프라인 형태 증명) |
| v1 | Assignment Engine 매칭 알고리즘 구현(결정론적, LLM 미사용 — ADR 결정 2)(F2·F3·F14 포함) | 6건(완전일치/스코어링/null예외 케이스 포함) |
| v2 | Org Chart+배분 UI(F5·F6), `assignment_history` 도입 | 8건(재배치 시나리오 포함) |
| v3 | Approval Workflow 다단계(F7), Job Queue+샌드박스(F4 비동기), Audit Log(F13) | 8건(승인 경로 3단계 이상 1건 포함) |
| v4 | Performance Review(F8)+Cost Tracker(F10)+F9 | 10건(성과리뷰·비용 통합 검증) |
| 코드 재점검 R1 | 승인 우회 차단·F3 Agent 필터·조직도 Agent 귀속·`users.manager_id` API 추가(`docs/LIMITS.md` §7) — §7 로드맵의 Phase 5/6과는 별개로, 위 v0–v4 완료 후 추가로 진행한 재점검 회차다 | 5건(GC42–46) |
| 코드 재점검 R2 | 승인 inbox 조회·`GET /api/users`·감사로그 페이지네이션·secret_key 환경변수화(`docs/LIMITS.md` §8) | 4건(GC47–50) |
| 코드 재점검 R3 | 매칭 알고리즘 role 필터 버그·콜드스타트 부트스트랩 CLI·Human Pool 수정/오프보딩(`docs/LIMITS.md` §9) | 5건(GC51–55) |
| 코드 재점검 R4 | **F11 Web UI 셸이 실제로는 전무했던 것을 발견·구현**(`webui.py`, `docs/LIMITS.md` §10) | 6건(GC56–61) |
| LLM 연동(사용자 요청) | Anthropic/OpenAI 선택 가능한 실제 LLM 연동(`llm.py`·`agent_runtime.py`, `docs/LIMITS.md` §11) — 이후 실제 OpenAI API로 실측 검증 완료 | 7건(GC62–68, mock 기반) |
| Ollama + Agent별 모델 설정(사용자 요청) | Ollama 추가 + `PATCH /api/agent-pool/{id}/model`(`docs/LIMITS.md` §11) | 4건(GC69–72, mock 기반) |
| 관리자/일반 사용자 UI 분리(사용자 요청) | Web UI 내비게이션 "내 업무"/"관리자 도구" 2구역화(ADR 결정 9) | 4건(GC73–76, 소스 검사 기반) |
| 실제 CrewAI 연동 + VectorDB/GraphDB Agent(사용자 요청, "실제 구현해줘") | `crew_runtime.py`(단일 생성+다중 협업)·`vectorstore.py`·`graphquery.py` 구현(ADR 결정 10·11, `docs/LIMITS.md` §12) | 18건(GC77–94, 실제 crewai 패키지로 통합 검증) |
| 실사용 500 에러 수정(사용자 보고) | `agent_pool` 스키마 마이그레이션 누락 발견·수정(`db._migrate_schema()`, `docs/LIMITS.md` §13) | 3건(GC95–97) |
| CrewAI 기능 적용 리뷰(사용자 요청, "crewai 기능을 적절히 적용되었는지 분석해줘" + "다른 리뷰 항목 마저 진행해줘") | `manager_agent_id`+`hierarchical` 실사용 크래시 버그, `memory=True`/`max_iter` 문서-코드 불일치 발견·수정 + `task_callback` 기반 감사로그 세분화 + 사용자 실사용 중 발견한 hierarchical 오귀속·Web UI null 크래시 수정(`docs/LIMITS.md` §14) | 5건(GC98–102) |
| MCP(외부 도구) 연동(사용자 요청, "crewai-tools의 MCPServerAdapter... 참조해 mcp 기능을 구현해줘") | `run_crew()`에 `MCPServerAdapter` 실제 연결, `AgentModelConfig.mcp_servers` 추가(`docs/LIMITS.md` §15) | 3건(GC103–105) |
| Agent-Evaluator 적용 분석(사용자 요청, "Agent-Evaluator 적용이 되어 있는지 분석해줘") | `eval_wiring.py`를 실제로 끝까지 실행해 `PerformanceMonitor` 저장 버그(`auto_save=False`) 발견·수정, `agent-eval gate` 채점까지 실제 성공 확인(`docs/LIMITS.md` §1) | 실측 검증(신규 golden case 없음, 기존 코드 버그 수정) |
| E2E 시나리오 실행(사용자 요청, "개발업무 인간-AI Agent 협업 팀 구성... End-to-End로 진행하며 개선해줘") | 신규 SaaS 대시보드 프로젝트 팀 구성을 F1→F14까지 실제로 끝까지 진행, UX 문제 3건 발견·수정(`docs/LIMITS.md` §16) | 실측 검증(프런트엔드 텍스트 수정, golden case 대상 아님) |
| CrewAI 개념 활용도 감사(사용자 요청, "전체적으로 CrewAI 기능/개념을 제대로 활용하도록 구현되어 있는지 확인해줘") | RAG 증강이 개별 실행에만 있고 Crew 협업에는 없던 실사용 갭 발견·수정, `human_input`/`token_usage`/`allow_delegation`/`planning` 4건 실측 판단(`docs/LIMITS.md` §17) | 1건(GC106, 이후 아래 항목에서 Tool 방식으로 갱신) |
| Skill Pool·Tool Pool + VectorDB/GraphDB의 CrewAI Tool 전환(사용자 요청, "Skills, MCP Tool 등을 Pool로 구성... 개선점을 찾아줘" + "MCP Tool처럼, CrewAI Tool처럼 동작하도록 개선해줘" + "tool_pool·skill_pool 테이블도 실제로 구현해줘") | 실제 데모 DB 확인 결과 VectorDB/GraphDB Agent가 실사용 중엔 하나도 없었음을 먼저 확인. VectorDB/GraphDB를 정적 프롬프트 주입 대신 `crewai.tools.tool` 기반 진짜 CrewAI Tool로 전환(`run_crew()`+`crewai_agent_generator()` 양쪽 연결, `GeneratorFn` 계약에 `conn` 추가)한 데 이어, `tool_pool`/`skill_pool`/`agent_tool_links` 3개 신규 테이블+`pool_registry.py`+CRUD API+Web UI까지 실제 구현해 하드코딩된 스킬→도구 매핑을 데이터로 완전 대체(ADR 결정 12, `docs/LIMITS.md` §18) | 10건(GC106 갱신+GC107–115 신규) |
| 데이터 삭제/초기화 기능(사용자 요청, "각 메뉴에서 데이터 삭제나, 초기화 기능 추가") | 사용자가 명시적으로 선택한 범위(도구/스킬 Pool+사람/AI 에이전트 Pool+brief/task, 활동 기록은 NF10 트리거로 애초에 불가해 제외)에 대해 `data_admin.py`(brief/task/human/agent)+`pool_registry.py`(tool/skill) 연쇄 삭제 로직과 6개 ADMIN 전용 DELETE 엔드포인트, Web UI 삭제 버튼까지 구현. FK 순서 버그(`sqlite3.IntegrityError`)와 테스트 self-approval 세션 버그를 실측으로 발견·수정, `confirmed` 배정 보호 안전장치 설계(`docs/LIMITS.md` §19) | 9건(GC116–124) |
| Agent-Evaluator/하니스 방법론 절차 스킬을 Skill Pool에 기본 노출(사용자 요청, GitHub Skills 링크 검토 후 "도구/스킬 Pool에 기본 노출해줘" + "CrewAI AI Agent가 개발 단계에 필요한 기능일지 확인하고, 필요한 경우 AI Agent가 호출할 수 있도록 변형해줘") | `agent-evaluator[sdk]` 기본 배포(Skills/) 15개 + 이 워크북 자체 발굴 3개, 총 18개 절차 스킬을 `db._seed_default_pools()`에서 `skill_pool`에 추가 시드. 17개는 HITL 판단 절차라 `augmentation_mode='none'`(skill_tag 노출만)로 두고, `recommend-fix-workflow` 1개는 실제 존재하는 `agent_evaluator.integrations.recommend_fix_mcp`(읽기 전용 stdio MCP 서버)를 `tool_pool`에 등록해 `augmentation_mode='tool'`로 승격 — mock 없이 실제 서브프로세스 연결·호출까지 실측 확인. 기존 GC108/GC114의 "정확히 N개" 단언을 부분집합 검사로 완화(`docs/LIMITS.md` §20) | 2건(GC125–126) |
| 각 메뉴 화면 사용자/운영자용 상세 설명 보강(사용자 요청, "AI 에이전트 생성 메뉴의 stub, simple_llm, crewAI 옵션에 따른 에이전트 생성을 구분해서 설명해줘" → "각 메뉴별로 위 분석 내용처럼... 친절하게 상세히 설명되도록 개선해줘") | 14개 탭 전부 백엔드 로직(스코어링 가중치·다단계 결재·NF9 동의 게이팅·비용/공정성 계산식·append-only·augmentation_mode 의미 등)을 재확인해 `<p class="muted">` 안내 문구를 추가/보강. 실측 중 골든셋 GC60(스펙 코드 노출 금지, 코드 재점검 R5)이 "F8 성과 리뷰" 문구를 실제로 잡아내 회귀를 발견·수정(`docs/LIMITS.md` §21) | 신규 golden case 없음(기존 GC60이 회귀를 잡음), 전체 129건 유지 |
| Phase 1: 협업 워크스페이스(사용자 요청, "Phase 1부터 시작해줘" — `docs/PHASE1_DELIVERABLE_SPEC.md` 구현) | 신규 `deliverables`/`deliverable_comments` 테이블, `deliverables.py` 모듈, `GET /api/my-work` 등 7개 엔드포인트, "내 업무" 탭+협업 워크스페이스(결과물 타임라인+댓글+LLM 보조+Agent 실행). 실측 중 `human_pool.user_id`를 채우는 API가 전혀 없어 F8도 실제로는 동작한 적이 없었을 선행 버그를 발견·수정(`HumanPoolUpdate.user_id` 추가). execute-agent는 스펙의 `crew_runtime.run_crew()` 대신 단일 Agent 온디맨드 실행 경로 `agent_runtime.execute_agent()`로 구현(`docs/LIMITS.md` §22) | 11건(GC127–137) |
| Phase 2: 산출물 승인 게이트(사용자 요청, "다음 단계 진행해줘") | Agent 산출물(`status='pending_review'`) 생성 즉시 `approvals.create_approval_chain(subject_type='deliverable')`로 결재 체인 자동 생성 — 새 승인 로직 없이 F7 다단계 결재라인·ADR 3-A 자기승인 방지를 그대로 재사용. `decide_approval()`에 `deliverable` 분기 추가, 응답에 `deliverable` 키 추가(하위호환). Web UI 워크스페이스에 인라인 승인/반려 버튼. "수정 후 재제출" 연결은 기준 미정이라 의도적으로 스코프 밖(`docs/LIMITS.md` §23) | 5건(GC138–142) |
| Phase 3: Agent 평가 인라인 연결(사용자 요청, "Phase 3 진행해줘" — `docs/HAIR_MASTER_PLAN.md` §5 구현) | 신규 모듈 `agent_evaluation.py` + `agent_evaluations`(append-only) 테이블. `execute_agent_for_task()`가 실제 `agent_runtime.execute_agent()` 호출을 `@agent_eval`로 감싸 매 실행마다 Gate A-G를 자동 측정 — `eval_wiring.py`(수동 probe 스크립트)와 달리 실제 프로덕션 경로에 상시 연결된다. `agent_pool.performance_score`(관리자 수동, F8)는 건드리지 않고 `aggregate_agent_score()`로 자동/수동을 분리해 보여준다. 실측으로 `task_type='deliverable'`이 non-canonical임을 발견해 `document_creation`으로 수정, 실제 LLM 호출 지연에 맞춰 SLA를 재보정(`eval_wiring.py`의 스텁 기준 5s/10s 대신 30s/60s). "실패 시 아무 것도 안 남는다" 불변식을 지키기 위해 평가(`run_and_score`, DB 미접촉)와 기록(`record_evaluation`, deliverable INSERT 이후)을 분리(`docs/LIMITS.md` §24) | 3건(GC143–145) |
| Phase 4: execute-agent 비동기화(사용자 요청, "Phase 4 진행해줘" — `docs/COLLABORATION_PLATFORM_PLAN.md` Phase C 스코프를 AskUserQuestion으로 좁혀 구현) | 원래 Phase C 설계(`crew_runtime.run_crew()` 대상 CrewAI 도구 호출 단위 일시정지)는 협업 워크스페이스가 실제로 쓰는 단일 LLM 호출 경로엔 적용 대상이 없어, 저위험 대안인 "워크스페이스 비동기화"로 스코프를 확정했다. `execute_agent_for_task()`를 `start_agent_execution()`(검증+프롬프트, 즉시 반환)과 `run_agent_execution_job()`(실제 LLM 호출, `BackgroundTasks`로 응답 후 실행)으로 분리, 신규 `agent_execution_jobs` 테이블(`running`/`succeeded`/`failed`)로 진행 상태 추적. `POST .../execute-agent`는 201+deliverable_id 대신 202+job_id를 반환(API 파괴적 변경, 유일한 소비자가 자체 Web UI/테스트라 즉시 반영). SQLite가 기존 테이블 CHECK 제약을 ALTER로 못 바꾼다는 걸 실측 확인해 `deliverables.status` 확장 대신 새 테이블을 선택. `TestClient`가 `BackgroundTasks`를 응답 전에 동기 실행한다는 것도 실측 확인(`docs/LIMITS.md` §25) | 3건(GC146–148) |
| Phase 1-4 리뷰(사용자 요청, "Phase1~4까지 누락되거나, 오류, 개선이 필요한 부분이 있는지 분석하고 개선해줘") | 추측이 아니라 매번 실패하는 재현 테스트를 먼저 만들어 확인한 크로스-Phase 버그 3건: (1) `assignments.resolve_assignment_decision()`이 resource_type을 구분 않고 supersede해 "사람+Agent가 같은 task에 함께 confirmed"라는 협업 워크스페이스의 기본 전제 자체가 깨져 있던 버그, (2) `data_admin.py`(§19)가 Phase 1-4의 새 테이블을 몰라 deliverable이 남은 task 삭제 시 FK 위반 500 에러, (3) 서버가 실행 중 죽으면 `agent_execution_jobs`가 영원히 'running'으로 남아 실행 버튼이 복구 불가능하게 막히던 문제(`reap_orphaned_execution_jobs()` 신설, `create_app()`에 연결). 추가로 Phase 3의 `aggregate_agent_score()`가 실제로는 어떤 API/화면에도 연결 안 돼 있던 죽은 코드를 발견해 `GET /api/agent-pool`+Web UI에 연결(`docs/LIMITS.md` §26) | 5건(GC149–153) |
| Phase 1-4 리뷰 2회차(사용자가 같은 요청을 다시 보냄) | (1) `start_agent_execution()`이 같은 task에 이미 'running' job이 있어도 확인 없이 새 job을 또 만들어 빠른 연속 클릭·여러 탭·직접 API 호출로 실제 LLM 호출이 이중으로 나갈 수 있던 문제 — `agent_already_running` 409 가드 추가. (2) 트리거 이후·실행 전 Agent가 삭제/오프보딩되면 `agent_not_active` 같은 내부 코드 문자열이 그대로 Web UI에 노출되던 것을 한국어 문구로 번역(`docs/LIMITS.md` §27) | 2건(GC154–155) |
| Phase 1-4 리뷰 3회차(사용자가 같은 요청을 세 번째로 보냄) | `decide_approval()`은 승인/반려 둘 다 감사로그에 남기는 F13 관례가 있는데, Agent 실행은 성공(`deliverable_created`)만 기록하고 실패는 전혀 기록하지 않아 HOTL 감독자가 실패 이력을 감사로그 화면에서 볼 수 없었다 — `agent_execution_failed` 액션 추가로 대칭을 맞춤. 부수적으로 `agent_execution_jobs` subject_type의 Web UI 라벨이 없어 감사로그 표에 영어 원문이 노출되던 것도 수정(`docs/LIMITS.md` §28) | 1건(GC156) |
| Phase 5: 기업용 UI/UX 고도화, 부분(사용자 요청, "Phase 5(기업용 UI/UX)에 포함할 항목을 고르세요" — 6개 중 4개+제한적 반응형 선택) | 통합 인박스(`inbox.py`, `GET /api/inbox` — 결재 대기+Agent 실행 실패+낮은 Gate 점수 경고), 매니저 대시보드(`dashboard.py`, `GET /api/dashboard` — 팀 Gate 평균+대기 승인 건수+비용 추이. "비용 추이"는 새 계측 없이 기존 `agent_cost_updated`/`human_pool_updated` 감사로그를 재사용), 검색/필터 스케일(`GET /api/pool/search`가 `available_only: bool`→`availability` 양방향 필터+`limit`/`offset`+`total` 페이지네이션으로 파괴적 변경, "부서" 필터는 스키마에 데이터가 없어 의도적 제외), 협업 워크스페이스 UX(채팅형 타임라인 좌우 정렬+Gate 배지 최초 노출+Skill `<datalist>` 자동완성, 이를 위해 `GET /api/skill-pool/names`를 전 role에 신규 개방). 다크모드는 명시적으로 스코프 밖(`docs/LIMITS.md` §29) | 13건(GC157–169) |
| Phase 1-5 리뷰(사용자 요청, "Phase1~5까지... 분석하고 개선해줘") | 막 작성한 Phase 5 코드에서 "조용히 틀린 결과"를 내는 버그 2건: (1) `dashboard.cost_trend()`가 SQL LIMIT을 관련성 필터(`cost_estimate` 유무)보다 먼저 적용해, 비용과 무관한 `human_pool_updated`(availability 토글 등)가 최근에 몰리면 더 오래된 진짜 비용 이력이 응답에서 통째로 사라지던 버그 — 필터 순서를 바꿔 수정. (2) 검색의 `availability='unavailable'` 필터가 `pool.agent_pool_candidates()`엔 아예 안 걸려 있어(Agent는 구조적으로 항상 `is_available=True`), "불가능한 후보만" 찾아도 Agent가 그대로 섞여 나오던 버그 — `search_pool()`에서 그 경우 Agent 후보 자체를 빼도록 수정. 둘 다 에러 없이 조용히 잘못된 데이터를 보여주는 종류라 재현 테스트 없이는 발견하기 어려웠다(`docs/LIMITS.md` §30) | 2건(GC170–171) |

## 10. 설계 결정

- **AgentContract·생성결과 스키마**: Pydantic v2 통일(AgentForge 계승). **Human Pool과는 동일 스키마가 아니라 공통 `ResourceCandidate` 인터페이스**(id/type(human|agent)/role/availability/cost/skill_tags/performance_score(0–1, 사람은 1차 null 허용))를 통해 어댑터로 연결한다(사람과 Agent의 속성이 본질적으로 다르므로).
- **Pool 저장**: 평가결과(`results/*.json`+SQLite 인덱스, 배치성)는 AgentForge 하이브리드 패턴 그대로 유지. 단, **Human Pool·`assignments`·`approvals`·`jobs`·`audit_log`·`assignment_history`처럼 실시간 쓰기가 빈번한 신규 엔티티는 SQLite를 트랜잭션 RDB(WAL 모드)로 사용**한다(조직 확장 시 PostgreSQL 교체 가능하도록 ORM 추상화).
- **Assignment Engine 매칭 알고리즘**: 완전일치 우선 → 없으면 가중치 스코어링(가용성40·적합도35·비용25)으로 Top3 제시(F14 점수 breakdown 포함) → 후보 없으면 F4 제안. 사람 후보의 performance_score가 null이면 적합도 항목을 제외하고 가용성60·비용40으로 재조정(AgentForge Composer의 3단 에스컬레이션 정신 계승).
- **`manager_id`(직속상사)와 RBAC role은 별개 개념**: Human Pool 레코드는 `manager_id`(F7 결재라인 자동라우팅에 사용)와 `user_id`(nullable, Auth 계정과 선택적 1:1 연결— 모든 사람이 로그인 계정을 갖는 것은 아님)를 갖는다.
- **Web UI 인증**: AgentForge와 달리 **Day-1부터 필수**(뒤집힌 결정) — 결재라인·생성 트리거 등 쓰기 액션이 존재하므로. RBAC 4종(Admin/매니저/팀원/뷰어), 조직 확장 시 SSO 검토.
- **`audit_log`는 append-only**이며 수정·삭제를 허용하지 않는다(LIMITS L7 교훈 반영 — 상태값만이 아니라 이전/이후 값을 함께 저장해야 실제 발동을 증명 가능). F13은 Admin·매니저 role만 접근 가능하다.
- **`assignments`의 상태 변경은 `assignment_history`에 스냅샷으로 남긴다**(현재 상태 UPDATE만으로 이력을 대체하지 않는다) — F6 핵심 차별화(팀간 유동적 재배치)의 신뢰성 증거 데이터.
- **F6/F7 배정 확정 처리 시 `assignments`·`assignment_history`·`audit_log` 3중 기록은 하나의 DB 트랜잭션으로 커밋**한다(부분 실패 시 전체 롤백).
- **F2(Review UI)와 F6(배분 화면)의 역할 경계**: F2는 Task 분해 결과(필요 역할·인원)만 검토하고, 후보 매칭·배정은 F6이 전담한다 — 별개 화면 유지로 확정(ADR 결정 1-A).
- **RBAC 권한 매트릭스**: F1–F14 × Admin/매니저/팀원/뷰어 접근권한 확정(ADR 결정 3), **F7 self-approval 방지**(배정 요청자 본인은 그 건을 승인 불가, ADR 결정 3-A) 포함.
- **Job Queue 샌드박스**: Docker 컨테이너(`--cpus`·`--memory` 쿼터, `--network=none`, 타임아웃)로 확정(ADR 결정 8) — Docker 런타임이 배포 요건에 추가된다.
- **Web UI 내비게이션 = "내 업무"/"관리자 도구" 2구역 분리**(사용자 요청, ADR 결정 9): 접근 제어 규칙은 새로 만들지 않고 기존 RBAC 매트릭스(결정 3)를 그대로 근거로 삼는다 — 탭의 `roles`에 팀원/뷰어가 하나라도 있으면 "내 업무", 없으면 "관리자 도구"로 분류한다. 사용자 관리(계정 생성·상사 지정)만 ADMIN 전용이라 "관리자 전용" 배지로 추가 표시한다.
- **AI Agent 생성 3단계(stub/simple_llm/crewai) + 실제 CrewAI 협업 + VectorDB/GraphDB Agent — 구현 완료**(사용자 요청, ADR 결정 10·11, `crew_runtime.py`·`vectorstore.py`·`graphquery.py`): `GenerateRequest.framework`로 생성 방식을 고르고, `POST /api/crew/run`으로 여러 Agent를 실제 CrewAI Crew로 묶어 `allow_delegation`+`Task.context`로 협업시킨다. 표준 Agent 세트에 지식검색(VectorDB, OpenAI 임베딩+SQLite 코사인 유사도)·관계분석(GraphDB, 기존 관계형 데이터를 그래프로 취급) Agent를 추가했다 — 관계분석 Agent는 성과 데이터(NF9)를 소스 레벨에서 절대 조회하지 않는다. MCP 도구 연결은 `run_crew()`에 실제로 구현했다(`MCPServerAdapter`, `docs/LIMITS.md` §15) — 접속 가능한 실제 MCP 서버가 없어 종단간 검증만 오픈 이슈로 남는다.
- **Skill Pool·Tool Pool + VectorDB/GraphDB의 CrewAI Tool화 — 구현 완료**(사용자 요청, ADR 결정 12): `tool_pool`(도구 재사용 레지스트리)·`skill_pool`(스킬 정식 엔티티화)·`agent_tool_links`(N:M) 3개 신규 테이블, `pool_registry.py`(CRUD+`resolve_pool_tools()`), CRUD API, Web UI("도구/스킬 Pool" 탭)까지 전부 구현했다. VectorDB/GraphDB는 `vectorstore.as_crewai_tool()`/`graphquery.as_crewai_tool()`이 `crewai.tools.tool`로 만든 진짜 Tool을 `run_crew()`(이미 온보딩된 Agent)와 `crewai_agent_generator()`(F4로 새로 생성되는 Agent) 양쪽에 붙여 Agent가 호출 여부를 스스로 판단하게 한다(`GeneratorFn` 계약에 `conn` 추가). `agent_runtime.VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 하드코딩은 더 이상 참조되지 않는다 — 매 기동 시 `db._seed_default_pools()`가 같은 매핑을 데이터로 자동 시드한다. 상세: `docs/LIMITS.md` §18.
- **데이터 삭제/초기화 — 구현 완료**(사용자 요청): 도구/스킬 Pool·사람/AI 에이전트 Pool·brief/task에 대한 하드 삭제를 `data_admin.py`/`pool_registry.py`+6개 ADMIN 전용 DELETE 엔드포인트로 구현했다. `PRAGMA foreign_keys=ON` 하에서 FK 순서를 지킨 완전한 연쇄 삭제, `confirmed` 배정 보호 안전장치, 삭제해도 `audit_log`에는 기록을 남기는 설계다. 활동 기록(감사로그) 자체는 NF10 append-only 트리거가 UPDATE/DELETE를 막아 삭제 범위에서 제외했다. 상세: `docs/LIMITS.md` §19.

이 결정들은 `docs/ADR.md`(Phase 2, `adr_review` 대기)에서 공식적으로 재확인·기록된다 — 여기서의 확정은 저장소 부트스트랩을 진행하기 위한 실무적 가결정이며, `adr_review` 승인이 최종 확정이다.

## 11. 완료 정의 (Definition of Done)

- [x] F1–F14 전부 골든셋 케이스로 검증됨(v0–v4+코드 재점검 R1/R2/R3/R4+LLM 연동+Ollama/모델설정+UI 구역분리+CrewAI/VectorDB/GraphDB+500에러수정+CrewAI 기능 적용 리뷰+MCP 연동+CrewAI 개념 활용도 감사+Skill/Tool Pool 구현+데이터 삭제/초기화 기능+Agent-Evaluator 절차 스킬 기본 노출+협업 워크스페이스 Phase 1/Phase 2/Phase 3/Phase 4+Phase 1-4 리뷰(1·2·3회차)+Phase 5(부분)+Phase 1-5 리뷰+기획/설계 문서 리뷰+UI/UX 리뷰+라이브 시나리오 실행 리뷰+사람 deliverable 제출/승인 메커니즘 추가+업무 결과물 확인 기능 개선+§34/§35 라이브 재검증+역할별 메뉴 노출 개선(3구역 재구성)+human_pool.manager_id API 추가+AI 에이전트 실행 권한 사전 확인+워크스페이스 UI/UX 개선+사용자 관리 화면 보강(비밀번호 초기화/계정 비활성화), 총 192건 — pytest 골든셋 기준. Phase 3부터 Gate A–G가 실제 프로덕션 경로에 상시 연결됨. Phase 4부터 execute-agent가 비동기(202+job 폴링)로 동작함. 리뷰에서 사람+Agent 동시 confirmed 시 조용히 깨지던 크로스-Phase 버그, 중복 실행 트리거 무방비, 감사로그 비대칭, Phase 5의 대시보드/검색 필터 버그 2건을 발견·수정함. Phase 5부터 통합 인박스·매니저 대시보드·페이지네이션된 검색이 생김. 라이브 실행 리뷰(`httpx`로 실제 HTTP 요청)에서 새 코드 버그는 없었으나 시나리오 문서 자체의 과장을 발견·정정했고, 그 리뷰가 범위 밖으로 남긴 갭(사람 deliverable에 승인 경로 없음)을 이어서 메웠다)
- [x] F11 — 인증된 Web UI 셸에서 F1–F14 전부 조작 가능(`webui.py`, 단일 페이지 바닐라 JS, R4에서 뒤늦게 구현 — `docs/LIMITS.md` §10 참고. 실제 브라우저 수동 조작은 미검증, TestClient 전체 플로우 통합 테스트로 대체)
- [~] Gate A–G 7개 전부 최소 한 번 fail을 거쳐 `improve verify`로 confirmed 판정까지 완주 — 부분: `agent-evaluator[sdk]` 설치+`eval_wiring.py` 배선 완료(Gate A/B/D/E/G 대상, C/F는 해당없음으로 제외), 저장 버그 발견·수정 후 `agent-eval gate` 채점까지 실제로 성공(TCR 100% PASS, Gate A/G 미달 확인) — `improve verify`로 confirmed까지 완주하는 반복은 여전히 사용자 선택으로 보류. `docs/LIMITS.md` §1 참고
- [ ] 6개 승인 kind 전부 설계된 지점에서 실제 발동 — `spec_review`·`adr_review` 완료, `threshold_review`는 Phase 4 검증(아래)으로 대체 발동
- [~] RBAC·샌드박스·Audit Log 3중 기록 트랜잭션 원자성에 대한 보안/무결성 테스트 통과 — 부분: 샌드박스 우회 방지·NF7 idempotency·append-only 트리거는 검증 완료, Docker 실제 격리 동작은 미검증(`docs/LIMITS.md` §2)
- [ ] 2인 팀 + 진짜 GitHub 원격에서 클레임 충돌 최소 1회 실전 발생·해소 — **불가(단독 세션)**: CI 인프라(`.github/workflows/ci.yml`)는 구축했으나 실전 클레임 충돌은 실제 2인 이상 협업에서만 발생한다, `docs/LIMITS.md` §5 참고
- [~] Skills 3개+ 신규 발굴 및 `skill_merge` 승인 — 부분: `rbac-permission-audit` 1건 완료(3회+ 기준 충족), 나머지 3건은 기준 미충족으로 보류
- [x] 배정 공정성 지표(사람·Agent 성과 비교) 구현 및 API 제공(`GET /api/fairness-report`) — 표본 부족으로 정량 해석은 보류(`docs/LIMITS.md` §4)

기획(이 문서·ADR.md·"HAIR 플랫폼 기획안" Claude 문서) 대비 실제 구현의 상세 비교는 `docs/RETROSPECTIVE.md` 참고 — git 커밋·`pytest` 실행 결과 기반 실측 대조다.

## 12. 오픈 이슈 (1차 출시 범위 밖, 추후 검토)

- 알림/통지 체계(F7 승인 요청 시 이메일/Slack 연동)
- 외부 시스템 연동(HR 시스템·SSO)
- 리포팅/데이터 내보내기(BI 연동 API)
- F14 점수 breakdown의 배정 후보 본인 공개 여부
- `assignment_history`·`audit_log` 보관기간 정책(개인정보보호법 대응)
- Audit Log의 SQLite 쓰기 부하 확장성(실사용 규모 부하테스트 필요)
- 초기 타깃 업종/조직 규모, 배포 모델(온프레미스 vs SaaS) 확정
- 팀원의 셀프서비스 요구(예: 본인이 직접 배정 요청)가 실사용에서 크면 RBAC 권한 매트릭스(ADR 결정 3) 완화 재검토
- ~~`agent-evaluator[sdk]` 설치+`eval_wiring.py` 배선은 완료 — 실제 Gate 측정 실행(사용자 직접)은 남음~~(`docs/LIMITS.md` §1) — **Phase 3에서 해결(2026-09-28)**: 협업 워크스페이스의 실제 실행 경로(`deliverables.execute_agent_for_task()`)에 Gate A-G 측정이 인라인으로 상시 연결돼, 매 Agent 실행마다 사람이 수동으로 돌릴 필요 없이 자동으로 채점된다(`agent_evaluation.py`, `docs/LIMITS.md` §24). `eval_wiring.py`는 이제 별개의 수동 실행 probe 스크립트로만 남아 있다.
- Docker 설치 환경에서 샌드박스 실제 격리(리소스 쿼터·네트워크 차단) 동작 검증(`docs/LIMITS.md` §2)
- 멀티프로세스/멀티워커 배포 시 NF7 idempotency의 진짜 경쟁 상태(race condition) 방지 — 현재는 단일 커넥션·순차 요청 전제(`docs/LIMITS.md` §3)
- Docker 런타임 설치를 배포 필수 요건으로 문서화(README·설치 가이드, ADR 결정 8)
- **CrewAI를 통한 MCP 도구 연결(외부 검색 등)** — `run_crew()`에는 실제로 연결 완료(ADR 결정 10, `docs/LIMITS.md` §15). 접속 가능한 실제 MCP 서버가 없어 종단간 검증은 못 했고, `crewai_agent_generator()`(F4 단일 생성)에는 아직 연결 안 함(그 시점엔 `agent_pool` row가 없어 MCP 설정을 읽을 곳이 없음)이 남은 오픈 이슈다.
- **VectorDB 지식검색 Agent의 Ollama 로컬 임베딩 지원** — 현재는 OpenAI 임베딩만 지원한다(`vectorstore.py`). Anthropic은 공개 임베딩 API가 없다.
- **CrewAI 위임(delegation) 도구 이름 하드코딩 위험** — `run_crew()`의 위임 캡처가 "Delegate work to coworker"/"Ask question to coworker"라는 CrewAI 내부 문자열에 의존한다 — CrewAI 버전업으로 이름이 바뀌면 조용히 캡처가 실패할 수 있다(`docs/LIMITS.md` §12).
- **Skill Pool·Tool Pool(ADR 결정 12) — `skill_pool.augmentation_mode`의 "always"/"none"은 저장만 되고 아직 효과 없음** — `resolve_pool_tools()`는 `"tool"` 모드만 실제로 읽는다. Tool Pool 관리 API/UI에 수정·삭제는 없고 비활성화만 지원한다(참조 무결성 유지 목적). MCP 서버는 여전히 `AgentModelConfig.mcp_servers`(Agent별 개별 저장)와 `tool_pool`(재사용 레지스트리) 두 경로가 공존한다 — 전자를 후자로 완전히 대체하는 마이그레이션은 하지 않았다(둘 다 유효한 경로로 유지). 상세: `docs/LIMITS.md` §18.
