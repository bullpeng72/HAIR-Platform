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
| Phase 5(코드 재점검) | 승인 우회 차단·F3 Agent 필터·조직도 Agent 귀속·`users.manager_id` API 추가(`docs/LIMITS.md` §7) | 5건(GC42–46) |

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

이 결정들은 `docs/ADR.md`(Phase 2, `adr_review` 대기)에서 공식적으로 재확인·기록된다 — 여기서의 확정은 저장소 부트스트랩을 진행하기 위한 실무적 가결정이며, `adr_review` 승인이 최종 확정이다.

## 11. 완료 정의 (Definition of Done)

- [x] F1–F14 전부 골든셋 케이스로 검증됨(v0–v4+Phase 5, 총 47건 — Gate A–G 실행 전까지는 pytest 골든셋 기준)
- [~] Gate A–G 7개 전부 최소 한 번 fail을 거쳐 `improve verify`로 confirmed 판정까지 완주 — 부분: `agent-evaluator[sdk]` 설치+`eval_wiring.py` 배선 완료(Gate A/B/D/E/G 대상, C/F는 해당없음으로 제외), 실제 실행은 사용자 선택으로 보류. `docs/LIMITS.md` §1 참고
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
- `agent-evaluator[sdk]` 설치+`eval_wiring.py` 배선은 완료 — 실제 Gate 측정 실행(사용자 직접) 및 AgentForge `spec_interpreter`·`code_generator`·`verifier` 실연동(의미 있는 점수의 전제조건)은 남음(`docs/LIMITS.md` §1)
- Docker 설치 환경에서 샌드박스 실제 격리(리소스 쿼터·네트워크 차단) 동작 검증(`docs/LIMITS.md` §2)
- 멀티프로세스/멀티워커 배포 시 NF7 idempotency의 진짜 경쟁 상태(race condition) 방지 — 현재는 단일 커넥션·순차 요청 전제(`docs/LIMITS.md` §3)
- Docker 런타임 설치를 배포 필수 요건으로 문서화(README·설치 가이드, ADR 결정 8)
