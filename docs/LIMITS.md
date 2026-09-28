# LIMITS — HAIR-Platform 알려진 갭 (Phase 4 검증 기준)

> AgentForge의 `docs/LIMITS_T-D8D8CE.md`와 같은 정신 — 완벽한 점수가 아니라 갭을
> 정직하게 남긴다. 이 문서는 SPEC.md §7 Phase 4(검증)에서 실제로 확인한 것과
> 확인하지 못한 것을 구분한다.

## 1. Gate A–G 실측 배선 — 배선 완료, 저장 버그 발견·수정 후 실제 gate 채점까지 성공(업데이트: 2026-09-27)

SPEC.md §7은 Phase 4에서 "Gate A–G 전부 fail→진단→해결"을 요구한다. 처음
확인했을 때는 `agent-evaluator` SDK가 vendor/import되지 않아 "검증 불가"로
적었으나, 이후 확인해보니 **`agent-evaluator`는 PyPI에 실제로 배포된 설치형
패키지**였다(vendor/import가 필요한 소스 복사가 아니었다) — 그래서 이번에
실제로 설치하고 배선까지 마쳤다.

**한 것**: `pyproject.toml`에 `agent-evaluator[sdk]` 의존성 추가, `pip install`
완료(`pip show agent-evaluator` 확인). `eval_wiring.py`가 `jobs.
create_and_run_job()`(F4 생성 파이프라인)을 `@agent_eval`로 감싸 Gate
A/B/D/E/G를 측정하도록 배선했다(AgentForge의 `eval_wiring.py`와 같은 패턴).
모듈 임포트만으로는 아무 것도 실행되지 않음을 확인했다(데코레이터는 등록만
한다).

**의도적으로 실행은 안 함**: 사용자가 "배선만, 실제 측정은 나중에 직접"을
선택했다. 다만 이 배선이 감싸는 `default_stub_generator`는 LLM을 전혀
호출하지 않고(ADR 결정 2), `llm_judge`·환각탐지도 켜지 않았으므로 **이
특정 측정 실행 자체는 API 비용이 들지 않을 것으로 보인다** — 다만 이건
이 세션의 판단이지 실측 확인은 아니므로, 실행 전 `agent-eval check`로 직접
확인하기를 권한다. 실행 명령은 `eval_wiring.py` 상단 docstring에 있다.

**측정하면 무엇이 나올지 미리 밝혀둠**: `default_stub_generator`는 역할명·
스킬을 그대로 되돌려주는 규칙기반 스텁이라, Gate A(응답품질)·G(설명가능성)가
요구하는 "실제 판단 근거"가 빈약하다 — 처음 실행하면 낮은 점수가 나올
가능성이 높다. 이건 버그가 아니라 스텁의 정직한 한계를 드러내는 결과다.
Gate C(재현성)는 스텁이 결정론적이라 항상 100%로 나와 정보가 없고, Gate
F(멀티에이전트 조율)는 에이전트 간 위임이 없어 해당 사항이 없다 — 둘 다
배선 대상에서 뺐다.

**남은 일**: 실제 AgentForge `spec_interpreter`·`code_generator`·`verifier`
연동은 여전히 별도 작업이다 — 그게 붙어야 Gate A/G 점수가 스텁의 한계가
아니라 진짜 LLM 판단 품질을 재게 된다.

**정정(사용자 요청, 2026-09-27, "Agent-Evaluator 적용이 되어 있는지
분석해줘" — 실제로 실행해보고 발견·수정)**: "배선 완료"라고 적어뒀지만
실제로 `python -m hair_platform.eval_wiring`을 끝까지 실행해본 적은
이번이 처음이었다. 돌려보니 **`results/`에 파일이 하나도 안 생기는
버그**가 있었다 — `PerformanceMonitor(output_dir=...)`는 `auto_save=False`
가 기본값이라 `output_dir`를 넘겨도 자동 저장이 안 된다(SDK 소스로 확인).
`monitor.task_count`로 평가 자체는 메모리에 정확히 기록됨을 확인했지만,
파일로 남기려면 `monitor.save_to_file(...)`을 명시적으로 호출해야 했다 —
`eval_wiring.py`의 `__main__` 블록에 이 호출을 추가해 고쳤다.

**고친 뒤 실제로 전체 파이프라인을 처음부터 끝까지 돌려 확인함**:
`python -m hair_platform.eval_wiring` → `results/agent_generation_eval.json`
생성 확인 → `agent-eval gate results/agent_generation_eval.json --tcr 85`
실행 → **TCR 100%로 PASS**, 요약에 "Gate A(Goal Achievement)·G
(Observability) 미달, 표본 2건이라 신뢰도 낮음"이 정확히 찍혔다 — 이
모듈이 스스로 예고했던 대로("Gate A·G가 요구하는 실제 판단 근거가
빈약해 낮은 점수가 나올 가능성이 높다") 나온 것을 실측으로 확인했다.
`agent-eval gate`까지 실제로 성공한 건 이 프로젝트에서 이번이 처음이다.

## 2. Docker 샌드박스 — 실제 격리 동작 미검증(환경 제약)

ADR 결정 8은 Job Queue의 smoke-test를 Docker 컨테이너에서 실행하기로 했다. 이
저장소가 개발된 환경에는 Docker가 설치돼 있지 않다(`which docker` 결과 없음).

**대신 실제로 한 것**:
- `sandbox.py`가 `docker run --rm --cpus=... --memory=... --network=none ...`
  명령을 정확히 구성하는지 단위테스트로 검증(`test_gc21`).
- Docker 부재 시 `run_smoke_test_in_sandbox`가 **실행을 건너뛰고 그 사실을
  `skipped=True`로 명시**하지, 성공으로 위장하지 않는다는 것을 검증.
- **신규(Phase 4)**: 샌드박스가 실패를 반환했을 때 `job_item`이 절대 `succeeded`로
  잘못 표시되지 않는다는 회귀 테스트(`test_gc38`) — 우회 경로가 없음을 코드
  레벨에서 증명했다.

**남은 일**: 실제 컨테이너 격리(리소스 쿼터가 진짜 걸리는지, `--network=none`이
진짜 아웃바운드를 막는지)는 Docker가 설치된 환경에서 별도로 확인해야 한다.

## 3. NF7(idempotency) — Phase 4에서 발견한 실제 구현 갭, 이번에 수정 완료

SPEC.md §10과 ADR은 "동일 역할 동시 생성요청 시 idempotency key로 Pool 경합·
중복생성을 방지한다"(NF7)고 결정했지만, v3에서 Job Queue를 구현할 때 이 로직이
실제로는 빠져 있었다(`grep`으로 확인, 코드에 idempotency 관련 로직 전무).

**수정**: `jobs._existing_active_item()`이 같은 `task_id`에 이미 `running` 또는
`succeeded`인 `job_item`이 있으면 재사용하고 새로 생성하지 않는다(`test_gc37`로
검증 — 반복 생성 요청이 `agent_pool`에 중복 항목을 만들지 않음을 확인).

**한계**: 이 구현은 단일 프로세스·단일 SQLite 커넥션 안에서의 순차 요청을
전제로 한다. 여러 프로세스/워커가 동시에(진짜 병렬로) 같은 task를 생성 요청하는
경쟁 상태(race condition)는 이 테스트가 재현하지 못한다 — 실제 멀티워커 배포
시에는 `task_id`에 대한 DB 레벨 unique 제약이나 분산 락이 추가로 필요할 수
있다(오픈 이슈로 남김).

## 4. 배정 공정성 지표(NF4) — 구현 완료, 해석은 사람 몫

`fairness.py`가 사람·Agent별 확정/반려 건수와 반려율 격차를 계산한다
(`test_gc39`). 단, 표본이 10건 미만이면 `low_sample_warning=true`를 반환해
비율 차이를 통계적으로 해석하기엔 이르다는 것을 알린다 — 이 프로젝트의 골든셋
규모(v0–v4 합계 42건, 그중 배정 관련은 훨씬 적음)에서는 현재 값 자체가 큰 의미를
갖지 않는다. 실사용 데이터가 쌓인 뒤 재해석해야 한다.

## 5. Phase 5(팀·PR CI) — 2인 팀 실전 클레임 충돌은 불가(단독 세션)

SPEC.md §7 Phase 5는 "실제 협업 환경에서 클레임 충돌/리뷰 검증"을, DoD는
"2인 팀 + 진짜 GitHub 원격에서 클레임 충돌 최소 1회 실전 발생·해소"를 요구한다.

**대신 실제로 한 것**: `.github/workflows/ci.yml`을 구축했다(pytest+ruff+black을
push/PR마다 실행). 이 저장소는 아직 GitHub 원격에 연결돼 있지 않다 — 원격 생성·
푸시는 사용자 계정에 실제로 저장소를 만드는 행위라 세션이 임의로 하지 않는다.

**할 수 없는 이유**: 클레임 충돌(같은 파일·같은 작업을 두 사람이 동시에 건드리는
것)은 정의상 실제 두 번째 협업자가 있어야 발생한다. 혼자 진행한 이 세션에서
이걸 시뮬레이션하면 거짓 증거를 만드는 것과 같다 — 정직하게 "불가"로 남긴다.

**남은 일**: GitHub 원격 연결(사용자 승인 필요) 후, 실제 두 번째 기여자(사람 또는
다른 세션)가 같은 파일을 동시에 수정하는 상황이 생겼을 때 이 항목을 완료 처리한다.

## 6. Phase 6(Skills화) — 1건만 3회+ 기준 충족

SPEC.md §8이 미리 지목한 4개 후보 중 `rbac-permission-audit`만 v0~v4 전체에서
5회 이상 반복된 게 확인돼 스킬화했다(`.claude/skills/rbac-permission-audit/
SKILL.md`). 나머지 3개(`human-agent-assignment-check`·`org-chart-sync`·
`async-job-status-ui`)는 각각 딱 한 버전에서 한 번씩만 구축했을 뿐 반복
검증한 적이 없다 — 원칙4("3회+ 반복 확인 후 스킬화, 억지로 미리 만들지
않는다")에 따라 지금 스킬화하지 않는다. 실사용 중 이 패턴들이 실제로
반복되면 그때 다시 판단한다.

이 저장소에는 Harness Autopilot이 설치돼 있지 않아(`.aoo/` 없음) `skill_merge`
승인이 Autopilot 워크플로 상으로 공식 발동하지는 않는다 — 이 문서와 SPEC.md §8
갱신이 그 승인 기록을 대체한다.

## 7. 코드 레벨 재점검(2026-09-26) — 4건 발견·전부 수정 완료

기획 문서가 아니라 **실제 구현 코드**를 대상으로 데이터 흐름·프로세스 오류/Gray
영역/누락을 재점검했다(그 전까지 5회는 기획 문서 대상 리뷰였다). 42건이던
골든셋 테스트가 커버하지 못하던 경로 위주로 확인했고, 발견한 4건 전부 즉시
수정하고 회귀 테스트(GC42–46, `tests/unit/test_phase5_review_fixes.py`)로
고정했다 — pytest 47건 전부 통과.

**7-1. `POST /api/approvals`가 F7 결재라인을 우회하던 심각한 구멍(수정 완료)**:
이 엔드포인트만 `require_role` 없이 `get_current_user`만 걸려 있어 VIEWER를
포함한 아무 로그인 사용자가 `subject_type=assignment`로 정식 결재라인과 무관한
rogue 승인(step=1, approver_id=NULL)을 만들 수 있었다. `decide_approval`은
`approver_id IS NULL`이면 승인자 지정 체크를 건너뛰고 `subject_id`만으로 배정을
확정하므로, 체인 밖의 임의 Admin/Manager가 이 rogue 레코드를 승인하면 진행 중인
배정을 조기 확정/반려시킬 수 있었다. **수정**: 이 엔드포인트를 설계 원래 범위인
F1(브리프) 단일승인 전용으로 고정(`subject_type != "brief"`면 400) + role을
ADMIN/MANAGER/MEMBER로 제한 + brief 존재 검증 추가.

**7-2. F3 검색의 role/skill 필터가 Agent Pool에는 적용 안 됨(수정 완료)**:
`agent_pool_candidates()`에 필터 인자 자체가 없어 검색 결과에 역할 무관 Agent가
항상 섞여 나왔다. `human_pool_candidates()`와 동일한 필터를 추가했다.

**7-3. 조직도(F5)가 Agent 배정의 관리자 귀속을 못 보여줌(수정 완료)**: Agent는
`human_pool` LEFT JOIN 대상이 아니라 전부 `manager_id=None` 버킷으로 뭉개졌다 —
"사람과 Agent를 하나의 조직도 위에"라는 핵심 가치와 어긋났다. Agent는 고용관계상
상사가 없으므로 배정을 요청·소유한 사람(`assignments.requested_by`)을 관리자로
귀속시키도록 수정했다.

**7-4. `users.manager_id`를 Web UI/API로 설정할 방법이 전무했던 누락(수정 완료)**:
F7 전체가 이 컬럼에 의존하는데 이를 채울 엔드포인트가 없어(사용자 생성 자체도
API로 노출 안 됨) 실사용 시 모든 사용자가 flat 상태로 남고, 그 결과 F7의
"지정된 승인자만" 체크가 한 번도 실전에서 발동한 적이 없었다(모든 테스트
픽스처도 `manager_id=None`이라 이 갭이 42건 골든셋을 다 통과하고도 드러나지
않았다). `POST /api/users`(ADMIN 전용, 계정 생성) + `PATCH
/api/users/{id}/manager`(ADMIN 전용, 상사 지정)를 추가해 F7을 실제로 다단계로
쓸 수 있게 했다 — GC46이 매니저 지정 후 `approver_id`가 실제로 채워지고, 체인
밖 매니저는 403으로 거부됨을 검증한다.

## 8. 코드 레벨 재점검(2026-09-26, 2회차) — 4건 발견·전부 수정 완료

7절 수정 이후 남아있던 갭을 재점검해 4건을 더 발견하고 전부 수정했다(GC47–50,
`tests/unit/test_phase6_review_fixes.py`) — pytest 51건 전부 통과.

**8-1. 승인 대기함(inbox) 조회 API 부재(수정 완료)**: 승인 생성(`POST
/api/approvals`)과 결정(`POST .../decide`)만 있고 조회 API가 없어, 실제
승인권자가 자신에게 무엇이 결재 대기 중인지 알아낼 방법이 시스템 안에 전혀
없었다 — F7이 이론상으로만 동작하는 상태였다. `GET
/api/approvals?scope=inbox|mine`을 추가했다. `inbox`는 `decide_approval`과
동일한 권한 로직(자기 요청 제외, Admin은 전체, 매니저는 미지정이거나 본인
지정 건만)을 그대로 재사용한다.

**8-2. `GET /api/users` 부재(수정 완료)**: 7절에서 추가한 `PATCH
.../manager`를 실제로 쓰려면 어떤 user_id가 있는지 조회할 방법이 있어야
하는데 없었다 — 기능은 추가했지만 발견(discovery) 경로가 빠진 절반짜리
구현이었다. ADMIN 전용으로 추가했다.

**8-3. `GET /api/audit-log`가 전체 테이블을 무제한 반환(수정 완료)**:
append-only라 시간이 지날수록 무한정 커지는데 페이지네이션/필터가 없었다.
`limit`(상한 200)·`offset`·`actor_id`·`subject_type`을 추가하고 응답을
`{items, total, limit, offset}` 형태로 바꿨다(기존 `test_gc23`도 이 형태에
맞춰 갱신).

**8-4. 세션 `secret_key`가 하드코딩된 상수(수정 완료)**: 주석은 "배포 시
환경변수로 교체한다"고 적혀 있었지만 실제로 환경변수를 읽는 코드가 없어,
배포해도 소스에 공개된 동일한 고정 키를 계속 쓰게 되는 오류였다.
`HAIR_PLATFORM_SECRET_KEY` 환경변수를 읽도록 고쳤다(미설정 시 기존 개발용
기본값으로 폴백). GC50이 서로 다른 키로 서명된 세션이 서로 교차 검증되지
않음을 확인한다.

## 9. 코드 레벨 재점검(2026-09-26, 3회차) — 3건 발견·전부 수정 완료

R1(§7)·R2(§8) 수정 이후 핵심 매칭 로직과 콜드스타트 경로를 재점검해 3건을
더 발견하고 전부 수정했다(GC51–55, `tests/unit/test_phase7_review_fixes.py`)
— pytest 57건 전부 통과.

**9-1. 가중치 스코어링이 role 관련성을 전혀 필터링하지 않던 핵심 매칭 버그
(수정 완료)**: `assignment.py`의 완전일치 실패 시 폴백 경로가 `for c in
candidates:`로 **후보 전원**을 점수 매겼다 — role이 요구사항과 전혀 다른
후보(예: 백엔드 개발자를 찾는데 Pool에 디자이너만 있음)도 "top 3 매칭
결과"로 추천되고 `generate_new=False`가 반환됐다. 기존 골든셋(GC5-8)은
전부 role이 이미 일치하는 후보만 테스트해서 이 경로가 한 번도 검증된 적이
없었다. `role_relevant` 필터를 추가해 role이 일치하는 후보로만 스코어링을
좁히고(비용 정규화도 이 부분집합 기준으로 재계산), role 일치 후보가 아예
없으면 완전 공백과 동일하게 F4 생성을 제안하도록 고쳤다.

**9-2. 콜드스타트 불가 — 최초 ADMIN 계정을 만들 방법이 전혀 없던 누락
(수정 완료)**: `POST /api/users`는 ADMIN 권한이 필요한데, 신규 배포 시
`users` 테이블이 완전히 비어 있으면 이 엔드포인트를 호출할 관리자 자체가
없어 아무도 로그인할 수 없었다(`pyproject.toml`에 CLI 진입점도 없었다).
모든 테스트가 `conftest.py`에서 Python으로 직접 `create_user()`를 호출해
우회했기 때문에 57건 골든셋을 다 통과하고도 드러나지 않았다. HTTP
엔드포인트로 만들면 "배포 직후 아직 계정이 없는" 짧은 시간에 누구나
선점할 수 있는 공격 표면이 되므로, `hair_platform.bootstrap` CLI(Django
`createsuperuser`와 같은 패턴)로 추가했다 — `users` 테이블이 이미
비어있지 않으면 거부한다.

**9-3. Human Pool에 수정/오프보딩 엔드포인트가 없던 비대칭(수정 완료)**:
Agent Pool은 `/onboard`·`/deprecate`로 생명주기가 관리되는데, Human
Pool은 생성(`POST`)·동의·성과입력뿐이고 가용성 변경·스킬 수정·퇴사
처리에 해당하는 엔드포인트가 없었다. `PATCH /api/human-pool/{id}`(부분
수정)와 `POST /api/human-pool/{id}/offboard`(검색/매칭에서 완전 배제,
단순 unavailable과 구분)를 추가했다.

## 10. 코드 레벨 재점검(2026-09-26, 4회차) — F11 Web UI 셸이 실제로는
전무했던 것을 발견·구현(수정 완료)

R1–R3(§7–9)는 전부 "이미 만들어진 API의 결함"이었다. 이번엔 관점을 바꿔
"애초에 이 신규 프로젝트를 시작한 이유 자체가 실제로 지켜졌는가"를
확인했고, 지금까지의 어떤 라운드보다 근본적인 갭을 발견했다.

**발견**: `docs/SPEC.md` §1과 F1–F14 요구사항 표는 전부 "화면"·"Web UI
폼"·"드래그 재배치"·"버튼" 같은 표현으로 실제 브라우저 그래픽 UI를
명시적으로 요구했다("브리프 제출부터 신규 Agent 생성까지 포함한 전
기능을 인증된 Web UI에서 조작 가능하게 하는 것이 AgentForge와의 핵심
차이다" — F11: "위 화면 전부를 인증+RBAC 적용된 Web UI 셸에서 제공").
그런데 v0–v4 개발과 R1–R3 코드 재점검을 거치는 동안 `find . -iname
"*.html"` 등이 전부 빈 결과를 반환했다 — **HTML/CSS/JS가 단 한 줄도
없었다.** 만들어진 것은 세션 인증+RBAC이 잘 갖춰진 JSON REST API뿐이었고,
`docs/RETROSPECTIVE.md`의 F11 행조차 이를 "계획대로 점진적 확장, 없음"
이라고 잘못 기록했었다(그 표도 이번에 함께 정정했다) — "Web UI"라는
표현을 "인증 미들웨어가 있는 API"로 좁게 오독한 것이 원인이었다.

**수정**: `webui.py`에 단일 페이지 바닐라 JS 앱을 만들었다(프레임워크·
빌드 스텝 없음, AgentForge의 f-string+html.escape·Jinja2 미사용 패턴
계승) — 기존 JSON API를 그대로 호출하는 순수 프레젠테이션 레이어다(새
비즈니스 로직 없음). `GET /`에서 서빙하며, F1(브리프)·F2(Task 분해)·
F3(검색)·F4(Agent 생성)·F5(조직도)·F6/F14(매칭·배정)·F7(승인함)·
F8(성과리뷰)·F9(Agent Pool)·F10(비용)·F12(Human Pool)·F13(감사로그)·
관리자(사용자/manager_id) 화면을 전부 구현했다. UI가 필요로 하는데
없던 읽기 전용 엔드포인트 6개(`/api/me`, `/logout`,
`GET /api/briefs`, `GET /api/briefs/{id}/tasks`, `GET /api/human-pool`,
`GET /api/agent-pool`)도 함께 추가했다(GC56–61).

**한계(정직하게 명시)**: 이 세션 환경에는 브라우저(Playwright 등)가
없어 **실제 브라우저에서 클릭해 조작하는 수동 테스트는 하지 못했다.**
대신 (1) 임베디드 JS를 `node --check`로 문법 검증했고, (2) 실제
uvicorn 서버를 띄워 `GET /`가 200으로 HTML을 반환하는지 실HTTP로
확인했고, (3) JS가 호출하는 것과 동일한 순서로 브리프→분해→매칭→
Agent생성→온보딩→배정→승인→조직도반영→감사로그까지 전체 플로우를
`TestClient`로 끝까지 실행해(`test_gc61`) API 계약이 깨지지 않았음을
검증했다. 하지만 이건 "JS가 이 계약대로 fetch를 호출한다"는 것의
증명이지, "브라우저에서 실제로 렌더링되고 클릭이 동작한다"는 것의
증명은 아니다 — 실제 브라우저 수동 확인은 오픈 이슈로 남긴다.

## 11. 실제 LLM(Anthropic/OpenAI) 연동 — 사용자 요청(2026-09-26)

사용자가 "알아서 일하는 에이전트"를 요청하면서 "생성 시 LLM 연동되는 구조는
아닌지 확인해달라"고 했다. `grep -rniE "anthropic|openai|llm" src/`로 확인한
결과 그 시점까지 실행 코드에 LLM 호출이 전무했다(ADR 결정 2 "결정론적, LLM
미사용"이 문자 그대로 지켜지고 있었다 — F1 브리프 파싱·F4 Agent 생성 전부
규칙 기반 스텁). 이번에 실제 연동을 추가했다.

**한 것**: `llm.py`(Anthropic/OpenAI 중 선택 가능한 provider/model 추상화,
지연 import라 안 쓰면 SDK 설치가 필요 없다) + `jobs.llm_agent_generator`
(F4 생성 시점에 실제 LLM 호출, `use_llm=True`일 때만 — 기본값은 여전히
무료 스텁) + `agent_runtime.execute_agent`(온보딩된 Agent에게 온디맨드로
업무를 맡기는 신규 실행 경로, `POST /api/agent-pool/{id}/execute`). Web UI의
"AI 에이전트 생성" 탭에 LLM 체크박스+provider/model 선택, "AI 에이전트" 탭에
"실행(일 시키기)" 버튼을 추가했다.

**추가(2026-09-26, 같은 날 2차 요청)**: 프로젝트 루트 `.env` 파일(git에
커밋되지 않음 — 이미 `.gitignore`에 있었고, git에도 추적된 적 없음을
`git ls-files`로 확인)에서 API 키를 읽도록 `python-dotenv`를 붙였다.
`create_app()`이 기동 시 `load_dotenv()`를 호출한다(이미 설정된 실제
환경변수는 덮어쓰지 않음, `.env`가 없으면 조용히 무시). 이제 사용자는
`export`로 매번 환경변수를 설정하지 않고 `.env`에 키를 넣어두기만 하면 된다.

**비용 발생 방지 원칙**: Gate A-G 배선 때와 같은 원칙 — `use_llm=True`를
명시적으로 요청했을 때만 실제 API를 호출한다. 자동화 테스트(GC62-68)는
`hair_platform.llm.call_llm`을 전부 mock으로 대체해 **실제 API를 절대
호출하지 않는다** — 이 저장소를 그대로 `pytest`로 돌려도 비용이 발생하지
않는다. `.env`에 실제 키가 있어도 이 mock 검증에는 영향이 없다(테스트가
필요시 `monkeypatch.delenv`로 명시적으로 지운다).

**한계(정직하게 명시, 업데이트 2026-09-26)**:
- ~~이 세션 환경에 API 키가 없어 실측 검증하지 못했다~~ — **정정**: 사용자가
  `.env`에 `OPENAI_API_KEY`를 추가한 뒤 명시적으로 요청해, SDLC 8개 역할
  에이전트 생성을 실제 OpenAI(`gpt-4o-mini`) API로 전부 성공시켜 실측
  검증을 완료했다(각 역할에 맞는 요구사항서·코드·스키마·테스트 계획 등
  실제 산출물이 나오는 것을 확인). 이 mock 기반 배선이 실제로도 정확히
  동작함을 증명한 사례다. Anthropic·Ollama 경로는 여전히 mock으로만
  검증했다(이 환경에 해당 키/로컬 서버가 없음).
- `llm.DEFAULT_MODELS`의 모델 ID(`claude-sonnet-5`, `gpt-4o-mini`)는 이
  세션이 아는 정보 기준 기본값일 뿐이다 — 실행 시점에 계정에서 실제로
  쓸 수 있는 모델 ID인지 사용자가 확인·조정해야 한다(그래서 UI·API 양쪽에
  model을 직접 지정할 수 있게 열어뒀다).
- Agent가 "자율적으로" 일하는 것은 **"프롬프트를 주면 LLM이 텍스트 산출물을
  생성해 돌려준다"** 수준이다 — 도구 호출(tool use), 여러 단계에 걸친 계획
  수립·자기 검증, 다른 Agent와의 협업 같은 진짜 "에이전틱" 동작은 이번
  범위에 없다(SPEC.md §12 오픈 이슈로 이월).
- `execute_agent`는 실행마다 `agent_pool.last_output`을 최신값으로만
  덮어쓴다 — 같은 Agent에게 여러 번 일을 시킨 전체 이력을 보려면
  `audit_log`(`action='agent_executed'`)를 조회해야 한다(전용 이력 테이블은
  없음, 필요성이 반복 확인되면 그때 추가).

**추가(2026-09-26, 3차 요청) — Ollama 지원 + Agent별 모델 설정**: 사용자가
"anthropic, openai, ollama를 포함해 AI Agent에 대한 모델 설정 기능"을
요청했다. `llm.py`에 Ollama(로컬 HTTP, 기본 `http://localhost:11434`,
`OLLAMA_BASE_URL`로 변경 가능)를 추가했고, `PATCH
/api/agent-pool/{id}/model`(신규, ADMIN/MANAGER)로 Agent별 기본
provider/model을 저장할 수 있게 했다 — 이 엔드포인트 자체는 LLM을 호출하지
않아 비용이 없다. `execute_agent`는 이제 요청에 provider/model이 없으면
①요청 파라미터 → ②이 Agent에 저장된 설정 → ③전역 자동판별 순으로
찾는다(`agent_runtime.py`). Web UI "AI 에이전트" 탭에 provider 선택+모델명
입력+"모델 설정" 저장 버튼을 추가했다.

**한계**: Ollama는 API 키 개념이 없어 자동판별 대상에서 뺐다(명시적 지정
필요) — 이 환경에는 Ollama 로컬 서버가 없어 실제 `/api/chat` 호출은
mock으로만 검증했다(GC69b). `llm.DEFAULT_MODELS["ollama"] = "llama3.1"`도
흔히 쓰이는 이름을 기본값으로 잡은 것뿐이라, 실제 로컬에 받아둔 모델
이름과 다르면 사용자가 직접 지정해야 한다.

## 12. 실제 CrewAI 연동 + VectorDB/GraphDB Agent 구현 — 사용자 요청
(2026-09-26, "실제 구현해줘" — ADR.md 결정 10·11의 실제 구현)

**한 것**: `crew_runtime.py`(CrewAI Agent/Task/Crew 실제 구성 — 단일 생성용
`crewai_agent_generator()` + 다중 Agent 협업용 `run_crew()`), `vectorstore.py`
(OpenAI 임베딩+SQLite 코사인 유사도 검색), `graphquery.py`(기존 관계형
데이터를 그래프로 취급하는 관계 질의, NF9 경계를 소스 레벨에서 강제).
`GenerateRequest.framework`(stub/simple_llm/crewai), `POST /api/crew/run`,
`PATCH /api/agent-pool/{id}/model`을 통한 모델 설정이 `agent_runtime.
execute_agent()`의 provider/model 해석에 반영되고, `skill_tags`에 따라
지식검색(VectorDB)·관계분석(GraphDB) Agent가 자동으로 RAG 증강되도록
연결했다. Web UI에 "Crew 실행" 탭과 "지식 색인" 버튼을 추가했다.

**실측 검증(중요)**: 이번엔 mock 검증에 그치지 않고 **실제로 crewai
패키지를 설치해 실제 클래스(Agent/Task/Crew)로 통합 코드를 검증**했다 —
LLM 호출 자체만 mock으로 대체했다(`crewai.Crew.kickoff` 경계). 이 과정에서
**실제 사고가 있었다**: 처음에 `crewai.LLM.call`을 patch해서 검증하려
했는데, CrewAI 내부 재시도 로직이 이를 우회해 **실제 OpenAI API를 호출해
버렸다**(`.env`의 실제 키로, 비용 발생 — `mock.called=False`인데도 진짜
응답이 돌아온 것을 보고 발견함). 원인은 두 가지였다: ① `crewai` 패키지는
**import되는 순간 자체적으로 `.env`를 읽는다**(문서화되지 않은 부작용),
② `LLM.call`은 CrewAI 내부 실행 경로의 유일한 진입점이 아니었다. 이후
`crewai.Crew.kickoff`(내 코드가 실제로 호출하는 가장 바깥쪽 경계)를
patch하는 방식으로 바꿔 안전하게 검증했다 — 이 파일(`test_phase12_*.py`)의
모든 테스트가 이 방식만 쓴다. **위임(delegation) 캡처는 실제 CrewAI 이벤트
클래스(`ToolUsageFinishedEvent`)를 만들어 실제 이벤트버스로 흘려보내
검증했다** — 캡처 로직 자체가 가짜가 아니라 실제로 동작함을 확인했다.

**부수적으로 발견한 환경 문제**: `crewai` 설치가 이 공유 개발 환경(conda
`Evaluator` env)의 다른 패키지(`arize-phoenix`)와 의존성이 어긋나면서
`pytest` 실행 자체가 전부 깨졌다(phoenix가 pytest 플러그인을 자동 등록하는데
import가 실패). 이 프로젝트와 무관한 플러그인이라 `pyproject.toml`의
`[tool.pytest.ini_options]`에 `addopts = "-p no:phoenix"`를 추가해 이
프로젝트 범위에서만 비활성화했다(다른 프로젝트의 pytest 실행에는 영향 없음).

**한계(정직하게 명시)**:
- 실제 OpenAI API로 CrewAI 전체 플로우(Agent 생성)를 실측 검증하지는
  않았다(자동화 테스트는 전부 `Crew.kickoff` mock) — 사용자가 지난
  SDLC 8역할 생성 때 `simple_llm` 경로는 실측했지만 `crewai` 경로는
  아직 실측 전이다.
- `run_crew()`의 위임 캡처는 "Delegate work to coworker"/"Ask question to
  coworker" 도구 이름으로 판별한다 — CrewAI 내부 구현이 이 이름을 바꾸면
  캡처가 조용히 실패할 수 있다(버전 고정 필요, 오픈 이슈).
- VectorDB는 OpenAI 임베딩만 지원한다(Anthropic 공개 임베딩 API 없음,
  Ollama 로컬 임베딩은 오픈 이슈). 저장 규모가 커지면 SQLite 전수 스캔
  코사인 유사도 계산은 느려진다 — `sqlite-vec` 등 전용 인덱스 재검토 필요.
- GraphDB(관계분석 Agent)는 NF9 경계를 소스 검사 테스트로 강제했지만,
  `execute_agent()`가 만드는 컨텍스트 텍스트 자체가 향후 다른 필드를
  실수로 포함하게 되면 이 테스트로는 못 잡는다(SQL 쿼리 문자열만 검사) —
  주기적 재검토 필요.
- CrewAI는 무거운 의존성이다 — 이번에 실제 설치해 검증했지만, 배포
  환경에는 `pip install -e ".[crewai]"`를 명시적으로 실행해야 한다
  (기본 설치에는 포함되지 않음).

## 13. 실제 500 에러 발견·수정 — `agent_pool` 스키마 마이그레이션 누락
(사용자 보고, 2026-09-27)

**발견 경위**: CrewAI/VectorDB/GraphDB 구현을 배포한 뒤 사용자가 실제로
"AI 에이전트 관리"·"Crew 실행" 메뉴에서 500 Internal Server Error를
겪었다 — 이 세션이 미리 예상하지 못한, 실사용 중 발견된 진짜 버그다.

**원인**: `db.py`의 `agent_pool` 테이블 정의에 `framework` 컬럼을
추가했지만, 데모 서버가 쓰던 `hair.db` 파일은 그 컬럼이 생기기 **전에**
이미 만들어진 것이었다. `init_schema()`가 매 기동 시 실행하는
`CREATE TABLE IF NOT EXISTS`는 테이블이 이미 있으면 아무 것도 하지
않는다 — 즉 새 컬럼이 기존 DB 파일에는 영영 추가되지 않는다.
`GET /api/agent-pool`가 `row["framework"]`에 접근하는 순간 SQLite
Row에 없는 키라 예외가 나 500으로 이어졌다.

**수정**: `db._migrate_schema()`를 추가해 `init_schema()`가 스키마
스크립트 실행 후 `agent_pool`의 `llm_provider`·`llm_model`·
`last_output`·`framework` 컬럼이 없으면 `ALTER TABLE ... ADD COLUMN`으로
채워 넣는다. 회귀 테스트(GC95-97)가 "새 컬럼이 생기기 전의 옛 스키마"를
실제로 재현해 마이그레이션이 데이터를 보존하면서 동작하는지 검증한다.

**한계**: 이 마이그레이션 함수는 지금까지 추가된 컬럼만 하드코딩돼 있다
— 앞으로 스키마를 바꿀 때마다 `_migrate_schema()`에 새 `_ensure_column()`
호출을 잊지 않고 추가해야 한다(자동 감지 방식이 아니다). 프로덕션급
마이그레이션 도구(Alembic 등) 도입은 오픈 이슈로 남긴다 — 지금 규모에는
과할 수 있다.

## 14. "CrewAI 기능 적절히 적용됐는지 분석" 리뷰 — 실사용 크래시 버그
1건 + 문서-코드 불일치 2건 발견·전부 수정 완료(사용자 요청, 2026-09-27)

§12에서 "구현 완료"로 표시했던 CrewAI 연동을 CrewAI의 `Crew.model_fields`
전체와 실제 검증 로직(validator 소스)을 기준으로 재검토했다. 코드를
읽는 것에 그치지 않고, 가짜 API 키(`sk-fake`)로 실제 `Crew(...)`를
생성해봐서(네트워크 호출 없이 Pydantic validation만 도는 안전한 방법)
문제를 실측으로 재현했다.

**1) [실사용 크래시 버그] `manager_agent_id` + `hierarchical`이 항상 실패**:
`run_crew()`가 매니저를 `Crew(agents=...)`에도 들어가는 바로 그
`crewai_agents` 리스트에서 골라 `manager_agent`로 지정했다. CrewAI의
`check_manager_llm` validator는 "매니저는 agents 목록에 있으면 안 된다"를
강제하는데, 가짜 키로 재현하니 예상대로
`ValidationError(manager_agent_in_agents)`가 즉시 발생했다 — Web UI
"Crew 실행" 탭의 "매니저 Agent ID" 입력란을 실제로 채우면 **항상
실패하는 상태**였다. `test_phase12_*.py`에 이 조합(`hierarchical` +
`manager_agent_id`)을 실행하는 테스트가 단 하나도 없어서(grep 결과 0건)
GC77-97 100건이 전부 통과해도 잡히지 않았다. **수정**: 매니저를
`agents`/`tasks` 목록에서 제외하고 `manager_agent`로만 전달하도록 고쳤다
— CrewAI의 hierarchical 모델 자체가 "매니저는 조율·위임만 하고 작업을
직접 수행하지 않는다"는 전제이므로, 매니저로 지정된 `agent_id`에 대응하는
`task_descriptions` 항목은 실행되지 않는다(API 계약은 그대로 두고, 그
task만 조용히 스킵됨). 회귀 테스트 GC98(정상 동작)·GC99(잘못된
`manager_agent_id` 사용 시 명확한 `ValueError`) 추가.

**2) [문서-코드 불일치] `memory=True` 약속이 코드에 없었음**: ADR 결정 10
매핑 표가 "`memory=True`로 Crew 전체가 이전 실행 맥락을 기억"이라고
명시했지만 `crew_kwargs`에 `memory` 키가 아예 없어 CrewAI 기본값
(`False`)으로 동작하고 있었다. `crew_kwargs["memory"] = True`로
반영했다 — **새로 발견한 트레이드오프**: CrewAI의 기본 메모리 임베더는
provider 선택과 무관하게 OpenAI 임베딩 API를 쓴다(Anthropic은 임베딩
API 자체가 없다). 즉 `provider="anthropic"`/`"ollama"`로 `run_crew()`를
호출해도 `OPENAI_API_KEY`가 별도로 필요해지며, 없으면 CrewAI가 메모리
저장 시점에 에러를 낸다 — 정직하게 이 문서에 남긴다(회귀 테스트 GC100이
`crew.memory is True`를 확인, 실제 임베딩 호출 자체는 mock 경계 밖이라
미실측).

**3) [알려졌으나 방치된 한계] `max_iter` 상한 미설정**: ADR 결정 11이
스스로 "반드시 걸어야 한다"고 적어뒀던 `max_iter` 상한을 `Agent(...)`
생성 시 전혀 넘기지 않아 CrewAI 기본값(25)에 암묵적으로 의존하고
있었다. `DELEGATION_MAX_ITER = 10`을 명시적으로 설정했다(회귀 테스트
GC100). CrewAI 기본값이 이미 25로 완전 무제한은 아니었지만, 우리는 Crew
전체 Agent에 위임을 동시에 켜므로 더 보수적인 상한이 맞다고 판단했다.

**4) [Gray영역, 버그 아님] `hierarchical`에서 Task.context 미체이닝**:
`sequential`과 달리 `hierarchical`에서는 앞 Task의 출력을 다음 Task에
자동 연결하지 않는다(`context=None` 고정). 매니저가 매 순간 누구에게
무엇을 넘길지 동적으로 결정하는 hierarchical 모델 특성상 의도된 설계로
판단했다 — 코드에 없던 근거 주석을 추가해 향후 "버그 아니냐"는 재질문을
막았다.

**확인만 하고 변경하지 않은 것**: CrewAI 네이티브 `Knowledge`/
`knowledge_sources`는 저장소로 ChromaDB류 별도 벡터스토어를 요구해
SQLite 중심 저장 정책(결정 5)과 상충한다 — `vectorstore.py`를 직접 만든
선택은 "원칙4: 이미 있는 것을 재사용" 위반이 아니라 오히려 일관됨을
재확인했다.

**5) [사용자 요청, 2026-09-27, "다른 리뷰 항목 마저 진행해줘"] `task_callback`으로
감사로그 세분화**: 기존에는 `crew_run_completed`(집계 1건)와
`agent_delegation_occurred`(위임 발생 시)만 감사로그에 남았다 —
Task 단위로 누가 무엇을 완료했는지는 최종 반환값에만 있고 감사로그에는
없었다. `run_crew()`에 `task_callback`을 연결해 Task가 완료될 때마다
`crew_task_completed`(agent_role, output_preview 200자) 감사로그를
남기도록 확장했다(회귀 테스트 GC101 — 실제 CrewAI가 하는 것과 동일하게
`crew.task_callback(task_output)`을 호출하는 kickoff side_effect로 콜백
배선 자체를 검증).

`step_callback`은 **의도적으로 안 붙였다** — CrewAI 소스(`crew_agent_
executor.py`)를 실측 확인한 결과 콜백에 넘어오는 `AgentAction`/
`AgentFinish` 객체에는 `thought`/`tool`/`tool_input`/`text`/`result`
(또는 `output`)만 있고 **어느 Agent가 낸 스텝인지 식별할 필드가 없다**
— Crew 전체에 콜백 하나만 걸 수 있는 구조라 스텝 단위로는 귀속(attribution)이
불가능하다. 식별 못 하는 로그를 대량으로 쌓는 것보다는 안 쌓는 쪽이
정직하다고 판단해 `max_rpm`과 함께 오픈 이슈로만 남긴다.

**부수적으로 발견한 것**: `task_callback`에 클로저(nested function)를
넘기면 pydantic이 `UserWarning: function callbacks cannot be serialized
and will prevent checkpointing`을 낸다 — CrewAI의 체크포인트/재개 기능을
쓰지 않는 한 무해하지만, 나중에 그 기능을 쓰게 되면 모듈 최상위 함수로
바꿔야 한다(오픈 이슈로 기록만 해둔다).

**6) [실사용 중 발견한 버그, 2026-09-27] `task_callback`이 hierarchical에서
매니저에게 잘못 귀속**: 사용자가 Web UI "Crew 실행" 탭에서
`manager_agent_id=1`(기획자)로 hierarchical을 실제로 실행해봤더니,
분석가(agent 2)가 만든 "데이터 정의서"가 감사로그(`crew_task_completed`)에는
"기획자가 했다"고 찍혔다. CrewAI 소스(`crew.py`의 `_get_agent_to_use`)를
확인하니 hierarchical 프로세스에서는 `TaskOutput.agent`가 **항상
manager_agent의 role**로 채워진다(매니저가 내부적으로 위임하더라도, Crew
실행 루프 관점에서는 매니저 자신이 "이 Task를 실행한 Agent"이기 때문) —
5)에서 `task_output.agent`를 그대로 믿고 쓴 게 잘못이었다. **수정**:
`task_output.agent` 대신, 콜백이 몇 번째 호출됐는지(`len(task_completions)`)로
`worker_rows`를 인덱싱해 실제 담당 Agent를 알아내도록 고쳤다 — CrewAI가
`self.tasks`(=`worker_tasks`)를 그 순서 그대로 순차 실행하므로
(`_execute_tasks`) 안전하다. `agent_pool.last_output` 갱신 로직은 원래도
이 방식(위치 기반 zip)을 썼기 때문에 이 버그의 영향을 받지 않았다 —
감사로그만 잘못됐었다. 회귀 테스트 GC102 추가.

같은 재현 과정에서 **Web UI 프런트엔드 크래시**도 함께 발견했다:
`webui.py`의 `renderCrewResult()`가 `a.output.length`를 무조건 읽는데,
매니저 Agent의 `output`은 의도적으로 `null`이라(1번 항목 수정 내용)
"Cannot read properties of null (reading 'length')"로 렌더링 자체가
깨졌다. `a.output === null`일 때 "매니저는 조율만 하고 직접 산출물을 내지
않습니다"라는 안내 문구를 대신 보여주도록 고쳤다. **교훈**: 백엔드
API 계약을 바꿀 때(이번엔 `output: None`이 정상적으로 나올 수 있게 됨)
그 값을 소비하는 프런트엔드 코드도 같이 점검해야 한다는 걸 실사용
크래시로 다시 확인했다 — pytest 골든셋은 백엔드 JSON 응답만 검증하고
`webui.py`의 JS 렌더링 로직 자체는 브라우저에서 실행해보기 전까지
검증되지 않는다는 한계(§10 DoD에도 이미 기록된 한계)가 실제로 발현된
사례다.

## 15. MCP(외부 도구) 연동 — 사용자 요청(2026-09-27, "crewai-tools의
MCPServerAdapter 또는 MCPServer 도구... 등을 참조해 mcp 기능을 구현해줘")

ADR 결정 10·SPEC.md §12가 "접속 가능한 MCP 서버가 없어 미착수"로 남겨뒀던
마지막 조각이다. **문서 URL만 보고 구현하지 않았다** — `WebFetch`가 이
환경에서 scope violation으로 막혀 있어, 대신 `pip install crewai-tools`·
`pip install "crewai-tools[mcp]"`로 실제 패키지를 설치하고
`MCPServerAdapter`(`crewai_tools/adapters/mcp_adapter.py`)와 그 내부
엔진 `MCPAdapt`(`mcpadapt/core.py`)의 소스를 직접 읽어 실제 시그니처를
확인한 뒤 구현했다.

**한 것**:
- `models.py`에 `MCPServerConfig`(command/args/env 또는 url/transport,
  tool_names) 추가, `AgentModelConfig.mcp_servers: list[MCPServerConfig]
  | None`(부분 갱신 — `None`이면 기존 설정 유지, 빈 리스트면 실제로 비움)
- `db.py`에 `agent_pool.mcp_servers TEXT NOT NULL DEFAULT '[]'` 컬럼 +
  마이그레이션(§13과 같은 패턴)
- `PATCH /api/agent-pool/{id}/model`이 `mcp_servers`도 함께 저장
  (`agent_runtime.set_model_config()`)
- `crew_runtime.py`의 `run_crew()`가 Agent별 `mcp_servers` 설정으로
  `crewai_tools.MCPServerAdapter`에 실제로 접속해 도구를 붙인다
  (`_mcp_tools()`) — `ExitStack`으로 Crew 실행이 끝날 때까지 연결을
  유지했다가 정리
- Web UI "AI 에이전트" 탭에 "MCP 서버(JSON)" 입력 추가(PATCH 재사용)

**실측으로 확인한 사실(문서만 봤으면 몰랐을 것)**:
- `MCPServerAdapter(serverparams, *tool_names, connect_timeout=30)` —
  `serverparams`는 `StdioServerParameters`(stdio) 또는 `dict`(SSE·
  streamable-http, 예: `{"url": ..., "transport": "sse"}`) 하나만
  받는다(단수). "여러 서버"는 내부 `MCPAdapt`가 `list[...]`를 받아주는
  것으로 지원되는데, `MCPServerAdapter`의 타입 힌트에는 이게 드러나지
  않는다(런타임에는 동작함) — 우리는 서버별 `tool_names` 필터를 살리려고
  서버 하나당 어댑터 하나를 여는 방식을 택했다(멀티서버 리스트는 필터가
  전체 공통 적용이라 서버별 세분화가 안 됨).
- **`MCPServerAdapter(...)`는 생성 시점에 실제로 접속한다**(지연 실행이
  아니다) — `__init__` 안에서 바로 `self.start()`를 호출한다. 즉
  `mcp_servers`가 설정된 Agent를 만드는 순간 네트워크/프로세스 I/O가
  발생한다. 접속 가능한 서버가 없으면 최대 `connect_timeout`(기본
  30초)만큼 블로킹되거나 `RuntimeError`가 난다.
- `Agent.tools: list[BaseTool] | None`은 pydantic이 타입을 검증한다 —
  테스트에서 아무 객체나 가짜 도구로 못 넣고, 실제 `crewai.tools.
  BaseTool`을 상속해야 한다(GC104에서 실측 확인 후 반영).

**안전한 mock 경계**: `crewai_tools.MCPServerAdapter` 클래스 자체(GC104,
`docs/LIMITS.md` §12의 `Crew.kickoff` mock과 같은 정신) — 이 아래(mcp
패키지의 실제 stdio/SSE 클라이언트)는 절대 patch 대상으로 삼지 않는다.

**한계(정직하게 명시)**:
- **접속 가능한 실제 MCP 서버가 없어 종단간(실제 서버가 응답하는 것까지)
  검증은 못 했다** — mock 경계 안쪽(연결·핸드셰이크·도구 호출 자체)은
  crewai-tools/mcpadapt 자체의 책임 범위이지 이 프로젝트가 검증한 부분이
  아니다.
- `crewai_agent_generator()`(F4 단일 Agent 생성)에는 MCP를 연결하지
  않았다 — 그 시점엔 아직 `agent_pool` row가 없어 `mcp_servers` 설정을
  읽어올 곳이 없다(신규 생성 요청 자체에 MCP 설정을 실어 보내는 방식은
  검토했으나, 온보딩 이후 반복 실행되는 `run_crew()` 쪽이 우선순위가
  높다고 판단해 이번 범위에서는 보류).
- stdio(로컬 프로세스로 접속하는 MCP 서버) 경로는 `StdioServerParameters`
  객체 생성까지만 코드로 확인했고, 실제 서브프로세스 기동은 당연히
  이 환경에서 검증 불가.
- `crewai-tools[mcp]`는 무거운 추가 의존성이다(`mcp`, `mcpadapt` 등) —
  `pip install -e ".[mcp]"`로 별도 opt-in(설치 안 해도 `mcp_servers`를
  안 쓰는 기존 crewai 경로는 그대로 동작 — `_mcp_tools()`가 빈 리스트일
  때 import 자체를 안 하는 것으로 지연 import 원칙을 지킴, GC103으로
  검증).

## 16. E2E 시나리오 실행 — "신규 SaaS 대시보드 프로젝트, 사람+AI Agent 혼합 팀
구성"(사용자 요청, 2026-09-27, "End-to-End로 시나리오를 진행하며 오류/불편하거나
불합리한 UI/기능 등을 개선해줘")

실제 데모 서버(`hair.db`)에 매니저 계정(`pm_kim`, admin 산하)을 만들고,
F1(브리프 제출) → F2(할 일 분해, 4개 역할) → F3/F6(Pool 검색·매칭) →
F12(사람 인력 온보딩, 매칭 후보 없는 역할) → F6(배정 요청) → F7(결재 승인)
→ F5(조직도 확인) → 개별 Agent 실행 → `POST /api/crew/run`(sequential +
hierarchical, 실제 OpenAI API 호출) → F8(성과리뷰+동의)·F10(비용)·
F13(감사로그)·배정공정성 리포트까지 curl로 실제 브라우저처럼 전 과정을
끝까지 실행했다.

**확인된 것(정상 동작, 회귀 아님)**: 매칭 알고리즘의 완전일치 스코어링,
매칭 실패 시 `generate_new` 플래그, 결재라인이 `manager_id` 체인을 따라
정확히 라우팅되는 것, 승인 후 `assignments`가 실제로 `confirmed`로 바뀌고
조직도에 사람·Agent가 같이 뜨는 것, `POST /api/briefs/{id}/tasks`가 API
레벨에서는 이미 여러 Task를 한 번에 받아주는 것(배치 지원), hierarchical
Crew 실행에서 매니저 오귀속 버그(§14)가 이 새 시나리오에서도 재발하지
않는 것(감사로그에 "분석가"로 정확히 찍힘), NF9 동의 플로우가 "본인 또는
Admin만" 동의 설정 가능하도록 매니저의 임의 설정을 정확히 막는 것 — 전부
실측으로 재확인됐다.

**발견·수정한 UX 문제 3건(webui.py, 전부 프런트엔드 텍스트/레이아웃 —
백엔드 계약 변경 없음)**:
1. F1 브리프 제출 textarea의 placeholder(`"예: 백엔드 개발자 1명"`)가
   1건짜리 예시만 보여줘서, 실제로 자연어 한 문장으로 여러 역할을 적으면
   (예: "기획자 1명, 백엔드 개발자 1명...") 규칙기반 스텁이 줄바꿈 단위로만
   나누기 때문에 전부 하나로 뭉쳐진 `stub_roles`가 된다(코드
   `app.py:parse_brief_stub`가 원래 그렇게 설계돼 있다 — 버그 아님, 그런데
   UI가 이 전제를 사용자에게 전혀 알려주지 않았다). placeholder를 "한
   줄에 역할 하나씩"으로 명시하도록 고침.
2. F2 "할 일로 나누기" 화면이 브리프의 `stub_roles`(제안된 역할)를 목록
   화면에 텍스트로만 보여주고 실제 입력 폼과는 전혀 연결돼 있지 않아서,
   매니저가 이미 브리프에 적은 역할명을 한 글자도 재사용 못 하고 전부
   다시 타이핑해야 했다. `renderDecomposeForm()`에 제안된 역할을 클릭하면
   "필요한 역할" 입력창에 채워지는 버튼을 추가.
3. F6 매칭 결과가 후보 0건일 때 "AI 에이전트 생성 탭에서 새로 만들어
   보세요"라고만 안내해서, 사람으로 채우는 경로(F12 사람 인력 관리)가
   있다는 걸 UI가 암시조차 안 했다 — "사람과 AI Agent를 함께 관리한다"는
   이 플랫폼의 핵심 전제와 맞지 않는 편향이었다. 두 경로 모두 안내하도록
   문구 수정.

**한계**: 이 3건은 전부 텍스트/이벤트 바인딩 수준의 프런트엔드 수정이라
`pytest` 골든셋으로는 검증 불가능하다(백엔드 계약이 그대로라 회귀
테스트를 추가할 대상이 없다) — `node --check`로 JS 문법만 확인했고, 실제
브라우저 클릭 동작은 이 세션에서 육안 검증하지 못했다(기존에도 반복
지적된 한계, §10 DoD 참고). F2를 브리프 제안 역할 기반으로 한 번에 여러
Task를 등록하는 배치 UI로 재설계하는 것(API는 이미 지원)은 이번 범위
밖으로 남긴다 — 폼 하나를 여러 줄 입력으로 재설계하는 건 지금 발견한
"제안 클릭으로 채우기"보다 범위가 커서, 실사용 중 정말 불편하다는 신호가
쌓이면 그때 다시 검토하는 게 맞다고 판단했다.

## 17. "CrewAI 기능/개념을 제대로 활용하도록 구현되어 있는지" 감사 —
실사용 갭 1건 발견·수정 + 후보 4건 실측 판단(사용자 요청, 2026-09-27)

§14 리뷰가 `manager_agent`/`memory`/`max_iter`/`task_callback` 같은 API
파라미터 수준을 다뤘다면, 이번엔 "CrewAI가 제공하는 개념을 이 도메인에
제대로 매핑했는가"를 실제 소스 확인 기반으로 다시 봤다.

**1) [실사용 갭, 수정 완료] RAG 증강이 개별 실행에만 붙고 Crew 협업에는
없었다**: `agent_runtime._augment_with_retrieval()`(ADR 결정 11 — skill_tags에
vectordb/rag·graphdb가 있으면 검색 결과를 프롬프트에 덧붙임)이
`execute_agent()`(개별 실행)에서만 호출되고 `crew_runtime.run_crew()`
(Crew 협업 — 이 프로젝트의 핵심 기능)에는 전혀 없었다(`grep`으로 확인).
즉 "지식검색 Agent"/"관계분석 Agent"를 여러 Agent와 묶어 협업시키면
RAG 증강 없이 원문 그대로 Task가 만들어져, ADR 결정 11이 약속한 기능이
정작 CrewAI 협업 경로에서는 작동하지 않고 있었다. **수정**:
`agent_runtime._augment_with_retrieval()`을 공개 함수
`augment_with_retrieval()`로 바꾸고(밑줄 제거 — 두 모듈이 공유하는
정당한 이유가 생겼다), `crew_runtime.run_crew()`의 Task 생성 루프에서
각 Agent의 `skill_tags`를 보고 동일하게 적용하도록 고쳤다. 회귀 테스트
GC106 — 실제 `Agent`/`Task`/`Crew` 객체를 구성해(`Crew.kickoff`만 mock)
`crew.tasks[1].description`에 `graphquery.context_for_prompt()`의 증강
흔적("확정된 배정")이 실제로 들어있는지, 그리고 일반 역할의 Task는
증강 없이 원문 그대로인지 확인한다. 순환 import 우려(`crew_runtime`이
`agent_runtime`을 import)는 실제로 `import hair_platform.crew_runtime`을
돌려 문제없음을 확인했다(`agent_runtime`은 `crew_runtime`을 import하지
않는다).

**2) [실측 확인, 안 고침 — 기술적으로 부적합] `Task.human_input=True`**:
CrewAI 소스(`crewai/core/providers/human_input.py`,
`crew_agent_executor.py`)를 직접 읽어 확인한 결과, 이 기능은 실행 중
**진짜 터미널 stdin에서 `input()`으로 블로킹 대기**한다(비동기 버전도
OS stdin을 논블로킹으로 읽을 뿐, HTTP 요청으로 답을 주는 방식이 아니다).
FastAPI 서버 프로세스 안에서 이걸 켜면 그 워커가 응답 없는 stdin을
기다리며 사실상 멈춘다 — 이 플랫폼처럼 서버로 배포되는 웹 서비스에는
쓸 수 없는 기능이다. `HumanInputProvider`가 교체 가능한 Protocol이라
F7 결재 큐와 연동하는 커스텀 provider를 만드는 건 이론상 가능하지만,
이는 "이미 있는 기능을 켠다" 수준이 아니라 새 통합 컴포넌트를 설계·
구현하는 별도 작업이라 이번 범위에서는 안 하고 오픈 이슈로만 남긴다.
F7 결재 워크플로가 이 도메인에 필요한 "사람의 승인/반려"를 이미 더
정교하게(다단계 결재라인, RBAC, 감사로그) 제공하므로 당장 급하지도
않다.

**3) [실측 확인, 최소 반영] `CrewOutput.token_usage` vs F10 비용 조회**:
`cost.py`는 문서에 이미 명시된 대로(`cost.py` 모듈 docstring, SPEC.md
F10 행 "1차: 수동입력만") `cost_estimate` 수동 입력만 쓰고, CrewAI가
실제로 계산해주는 `result.token_usage`(`UsageMetrics` — total/prompt/
completion 토큰 수, 성공 요청 수)는 전혀 활용하지 않고 있었다. 이건
이미 문서화된 의도적 스코프라 `cost.py` 자체를 바꾸진 않았지만,
"CrewAI가 이미 계산해주는 데이터를 완전히 버리는 건 아깝다"고 판단해
`run_crew()`의 `crew_run_completed` 감사로그에 `token_usage`를 참고용
으로 함께 남기도록 최소 반영했다(F10 자동 갱신 같은 큰 변경은 안 함 —
그건 여전히 오픈 이슈).

**4) [실측 확인, 안 고침 — 근거 부족] 모든 Agent에 `allow_delegation=True`
일괄 적용**: "품질 담당자"·"검증 엔지니어"처럼 위임보다 최종 검증을
해야 할 것 같은 역할도 위임이 켜져 있는 게 맞는지 검토했다. CrewAI
소스(`Agent.allow_delegation` 필드 설명)에는 특정 역할에 위임을
제한해야 한다는 근거가 전혀 없다 — 순수히 이 도메인의 설계 판단
영역이다. 확실한 기술적 근거 없이 임의로 역할별 차등을 두면 ADR 결정
11이 의도적으로 선택한 "정적 릴레이가 아니라 실제 협업"이라는 취지를
훼손할 위험이 더 크다고 판단해 그대로 뒀다.

**5) [검토함, 보류] `Crew(planning=True)`**: CrewAI 소스(`crew.py`의
`_handle_crew_planning`)를 확인하니 이 옵션은 실행 전 **별도의 LLM
호출로 `AgentPlanner`를 돌려 계획을 세운 뒤 그 계획을 Task에 추가**하는
구조다 — Crew 하나를 실행할 때마다 LLM 호출이 최소 1건 더 늘어난다.
ADR 결정 11이 이미 "위임만으로도 호출 수 예측이 어려워진다"는 우려를
문서화해뒀는데(§`docs/ADR.md` 결정 11 한계), 그 위에 계획 수립 호출까지
얹을 명확한 이득(이 도메인의 Task description은 이미 사용자가 구체적
으로 적어 넣는 편이라 "무엇을 할지 계획"이 크게 필요치 않아 보임)이
확신되지 않아 켜지 않았다 — 검토했으나 보류.

## 18. Skill Pool·Tool Pool + VectorDB/GraphDB의 CrewAI Tool 전환 —
구현 완료(사용자 요청, 2026-09-27, "AI Agent에서 사용할 Skills, MCP
Tool 등을 Pool로 구성하고 지식 및 도구 그라운딩... 개선점을 찾아줘" +
"이미 생성된 또는 생성하는 AI Agent에서 MCP Tool처럼, 또는 CrewAI
Tool처럼 동작하도록 개선해줘" + "tool_pool·skill_pool 테이블도 실제로
구현해줘")

**먼저 확인한 사실**: "VectorDB/GraphDB가 현재 AI Agent인지" 물었을 때
실제 데모 DB(`hair.db`)를 직접 조회해 확인한 결과, 실사용 중인 8개
SDLC Agent 중 vectordb/graphdb `skill_tags`를 가진 Agent는 **단 하나도
없었다** — 이 개념은 `tests/unit/test_phase12_*.py`가 수동으로 INSERT하는
테스트 픽스처(`'지식검색 Agent'`, `'관계분석 Agent'`)로만 존재했고, Web
UI에도 이런 Agent를 만들도록 안내하는 흐름이 전혀 없다. 즉 "VectorDB/
GraphDB Agent"는 지금 코드가 지원하는 **역량**(어떤 Agent든 skill_tags에
그 문자열을 넣으면 동작)이지, 실제로 존재하는 전용 Agent 타입이 아니다
— 정직하게 이 사실을 먼저 확인하고 넘어간다.

**1) tool_pool·skill_pool·agent_tool_links — 실제 구현 완료**: `db.py`에
3개 신규 테이블을 추가하고, `pool_registry.py`(신규 모듈)에 CRUD 함수
(`create_tool`/`list_tools`/`set_tool_active`, `create_skill`/
`list_skills`, `link_agent_tool`/`unlink_agent_tool`/`list_agent_tool_ids`)
와 실제 도구 조립 함수(`resolve_pool_tools()`)를 구현했다. `db.
_seed_default_pools()`가 매 `init_schema()` 호출마다(멱등, `INSERT OR
IGNORE`) VectorDB/GraphDB native Tool 2개와 vectordb/rag/graphdb/관계질의
skill_pool 4개를 자동으로 채워, 이전에 `agent_runtime.VECTORDB_SKILL_
TAGS`/`GRAPHDB_SKILL_TAGS`가 코드로 하드코딩했던 매핑을 데이터로 완전히
대체했다 — `crew_runtime._skill_based_tools()`는 이제 이 두 상수를 전혀
참조하지 않는다(GC115가 두 상수를 빈 집합으로 patch해도 `run_crew()`가
여전히 도구를 붙이는 것으로 하드코딩 의존이 실제로 끊겼음을 확인). API
(`GET/POST /api/tool-pool`, `PATCH /api/tool-pool/{id}/active`, `GET/POST
/api/skill-pool`, `POST/DELETE /api/agent-pool/{id}/tools/{tool_pool_id}`)
와 Web UI("도구/스킬 Pool" 탭 + "AI 에이전트" 탭의 도구 연결/해제
입력)까지 실제로 붙였다(GC108-115).

**2) VectorDB/GraphDB를 진짜 CrewAI Tool로 전환 — 실제 구현 완료**:
`crewai.tools.tool` 데코레이터(실제 설치된 패키지 소스를 `inspect`로
확인)를 이용해 `vectorstore.as_crewai_tool(conn)`/`graphquery.
as_crewai_tool(conn)`을 새로 만들었다 — 각각 "지식 베이스 검색"/"조직도
관계 조회"라는 이름의 실제 `Tool` 인스턴스를 반환한다. `crew_runtime.
_skill_based_tools()`가 이제 `pool_registry.resolve_pool_tools()`에
위임해 `skill_pool`/`agent_tool_links` 데이터로 Agent의 도구를 조립한다
(하드코딩된 `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 판별은 완전히
제거했다).

- **`run_crew()`(이미 생성된/온보딩된 Agent)**: Agent별로 MCP 도구
  (`AgentModelConfig.mcp_servers` 경로, `_mcp_tools()`)와 Tool Pool
  도구(스킬 매칭 + 그 Agent에 명시적으로 연결한 것, `_skill_based_tools()`
  → `resolve_pool_tools(..., agent_id=row["id"])`)를 합쳐서 붙인다.
  이전에는(2026-09-27 초반 리뷰에서) `agent_runtime.
  augment_with_retrieval()`로 Task description에 검색 결과를 무조건
  주입했었는데, **정적 주입을 완전히 대체**했다 — Task description은
  이제 사용자가 입력한 원문 그대로이고, Agent가 Tool 호출 여부를 스스로
  판단한다(GC106).
- **`crewai_agent_generator()`(F4로 새로 생성되는 Agent)**: `GeneratorFn`
  계약(`jobs.py`) 자체에 `conn: sqlite3.Connection`을 첫 인자로 추가해
  — `default_stub_generator`·`llm_agent_generator`·`crewai_agent_
  generator` 전부 시그니처를 맞췄다 — F4 생성 시점부터 `required_skills`
  에 따라 Tool이 붙은 Agent가 태어난다(GC107, `agent_id=None`이라 스킬
  매칭만 적용되고 `agent_tool_links`는 아직 없다 — 그 Agent 자체가 아직
  `agent_pool`에 없기 때문에 당연하다). 이전까지는 conn 자체가 이
  함수에 전달되지 않아 애초에 불가능했다.
- **정적 주입은 `execute_agent()`에만 남았다**: 도구 호출 루프가 없는
  단순 1회 LLM 호출 경로라 Agent가 Tool을 스스로 호출할 방법이 구조적으로
  없다 — 이 경로에서는 정적 주입이 유일한 선택지이므로 의도적으로
  유지했다.

**한계(정직하게 명시)**:
- `skill_pool.augmentation_mode`는 스키마·API 레벨로는 `"tool"`/
  `"always"`/`"none"` 셋 다 저장 가능하지만, `resolve_pool_tools()`는
  `"tool"`만 실제로 읽는다 — `"always"`를 선택해도 정적 주입을 실제로
  트리거하는 로직은 아직 없다(선택은 되는데 효과가 없는 상태 — 오픈
  이슈로 명시).
- Tool Pool 관리 API/Web UI에 수정(`PUT`류 전체 갱신)·삭제는 없다 — 도구
  비활성화(`is_active=False`)만 지원한다(참조 무결성을 지키기 위한
  의도적 선택). 이름을 잘못 등록했으면 비활성화하고 새로 만들어야 한다.
- Web UI의 "도구 연결"은 Tool Pool ID를 사용자가 직접 숫자로 입력해야
  한다(드롭다운으로 이름을 골라 연결하는 UX는 아직 없음) — 오픈 이슈.
- Tool 방식으로 전환한 뒤 Agent가 실제로 검색을 얼마나 자주/적절히
  호출하는지, 이게 정적 주입 대비 비용을 늘리는지 줄이는지는 실측
  전까지 알 수 없다 — 접속 가능한 실제 사용 데이터가 쌓이기 전까지는
  가정으로 남는다.
- `GeneratorFn` 계약 변경(`conn` 추가)으로 `jobs.default_stub_generator`·
  `jobs.llm_agent_generator`·`crew_runtime.crewai_agent_generator`와
  이들을 직접 호출하는 기존 테스트 4건의 시그니처를 전부 맞춰야 했다 —
  하위호환 shim 없이 계약 자체를 바꿨다(이 프로젝트가 지금까지 지켜온
  "필요하면 계약을 정직하게 바꾼다" 원칙과 일치, 사용하지 않는 `conn`은
  `default_stub_generator`/`llm_agent_generator`에서 그냥 무시한다).

## 19. 데이터 삭제/초기화 기능 — 사용자 요청(2026-09-28, "각 메뉴에서 데이터
    삭제나, 초기화 기능 추가")

사용자에게 범위를 먼저 물었다(파괴적·되돌릴 수 없는 작업이라 그냥 진행하지
않고 `AskUserQuestion`으로 확인) — 사용자는 세 가지 추가 범위를 전부
선택했다: **도구/스킬 Pool + 사람/AI 에이전트 Pool + 업무 요청/할 일
(brief/task)**. **활동 기록(감사로그)은 처음부터 선택지에서 제외했다** —
NF10(append-only)에 따라 `db.py`의 DB 트리거가 `audit_log`에 대한
UPDATE/DELETE 자체를 막고 있어 삭제 API를 만들어도 트리거가 막는다.

**구현**(`data_admin.py` 신규 + `pool_registry.py`에 2개 함수 추가 +
`app.py`에 6개 DELETE 엔드포인트, 전부 ADMIN 전용):

- `DELETE /api/tool-pool/{id}`, `/api/skill-pool/{id}` — Tool/Skill Pool
  항목 삭제. Tool은 `agent_tool_links`가 참조하면(FK) 그 연결까지 같이
  지운다.
- `DELETE /api/human-pool/{id}`, `/api/agent-pool/{id}` — 사람/AI 에이전트
  레코드 삭제. Agent는 `knowledge_chunks`·`agent_tool_links`까지 연쇄
  삭제한다.
- `DELETE /api/briefs/{id}`, `/api/briefs/{brief_id}/tasks/{task_id}` —
  업무 요청/할 일 삭제. brief 삭제는 그 아래 tasks·jobs·job_items·
  assignments·assignment_history·(subject로 참조하는) approvals까지
  전부 연쇄 삭제한다.

**안전장치(공통)**: `status='confirmed'`인 배정이 걸려 있는 대상은 삭제를
막는다(`PermissionError("confirmed_assignment_exists")` → HTTP 409) —
이미 승인돼 조직도에 실제로 반영된 배정이 조용히 깨지는 것을 막기 위한
설계다. `'requested'`(승인 전)·`'superseded'`(이미 지나간 이력)는 막지
않는다. 실제 행은 지워도 `audit.record(...)`로 "누가 언제 무엇을
지웠는지"는 감사로그에 남긴다.

**실제로 발견·수정한 버그(TDD로 실측하다 발견, 코드 리뷰만으로는 못
찾음)**:

1. **FK 순서 버그** — `PRAGMA foreign_keys=ON`이 걸려 있는 상태에서,
   assignment를 subject로 하는 approvals를 assignments보다 먼저 지우려
   했더니 `assignments.approval_id`가 그 approval을 여전히 참조하고 있어
   `sqlite3.IntegrityError: FOREIGN KEY constraint failed`가 실제로
   났다(`test_gc116`/`test_gc118`로 재현). assignments를 먼저 지워
   참조를 끊은 뒤 approvals를 지우도록 순서를 바꿔 수정했다.
2. **테스트 자체의 버그** — 골든셋 테스트 헬퍼(`_confirm_assignment`)가
   호출 시점의 로그인 세션에 의존했는데, 직전에 admin 전용 API를 호출한
   뒤라 admin 세션인 채로 배정을 "요청"해버려서, 같은 admin이 승인까지
   하려다 **ADR 결정 3-A(본인이 요청한 건은 스스로 승인 불가)**에
   막혀 403이 났다(의도된 정상 동작이 테스트 버그를 드러낸 사례). 헬퍼
   맨 앞에 명시적으로 요청자 계정으로 재로그인하도록 고쳤다.

**알려진 트레이드오프(정직하게 기록)**:

- brief/task를 하드 삭제하면 그 아래 `assignment_history`도 완전히
  같이 지워진다 — NF11("이력 보존")의 취지와는 반대 방향이다. 이번
  범위는 사용자가 명시적으로 "삭제/초기화"를 요청한 것이므로 의도된
  동작이지만, 삭제된 brief/task의 배정 이력은 `audit_log`의 "무엇을
  지웠는지" 스냅샷(before/after) 이상으로는 복원할 수 없다.
- `approvals.subject_id`는 폴리모픽 컬럼(brief/assignment 구분은
  `subject_type`)이라 formal FK가 없다 — brief/assignment 삭제 시
  `subject_type`+`subject_id`로 직접 지우는 것은 위생 처리일 뿐,
  SQLite가 강제하는 것은 아니다.
- `assignments.resource_id`(human_pool.id 또는 agent_pool.id)에도
  formal FK가 없다 — `confirmed` 배정이 없는 human/agent를 지우면
  `requested`/`superseded` 배정에 고아 참조가 남을 수 있다(수용 가능한
  위험으로 판단 — 조직도에 실제로 반영된 `confirmed` 배정만 안전장치로
  막는다).
- 삭제 API 6개 전부 ADMIN 전용으로 정했다(기존 소프트 상태변경 API보다
  엄격 — 예: 사람 오프보딩은 ADMIN+MANAGER, 에이전트 온보딩/오프보딩은
  ADMIN 전용인데, 하드 삭제는 되돌릴 수 없어 한 단계 더 엄격하게
  잡았다) — 별도 검토 없이 내가 내린 판단이라 오픈 이슈로 남긴다.

골든셋 GC116–GC124(`tests/unit/test_phase15_data_deletion.py`, 9건)로
FK 연쇄 삭제·안전장치·RBAC·에러 매핑을 전부 검증했다. 총 127건 통과.

## 20. Agent-Evaluator/하니스 방법론 절차 스킬을 Skill Pool에 기본 노출 —
    사용자 요청(2026-09-28)

사용자가 GitHub 저장소(`bullpeng72/Agent-Evaluator`, `Agent-Evaluator`
브랜치)의 `Skills/` 링크를 주며 "검토하고 호환가능하면 기본 배포에
포함되도록 해줘"라고 요청했다. 조사해보니:

- 그 링크의 내용은 이미 이 워크북이 의존하는 `agent-evaluator[sdk]`
  패키지(로컬 editable 설치, `/Users/fomalhaut/Projects/Agent-Evaluator`)의
  기본 배포(`Skills/`, 15개)와 완전히 동일했다(`git diff`로 확인, 차이
  없음) — 그 자체로는 추가할 게 없었다.
- 오히려 반대 방향의 갭이 있었다: 이 워크북 저장소(`AgentForge_Workbook`)
  의 `.claude/skills/`에는 그 15개 외에 `background-real-verification`·
  `evidence-based-gate-fix`·`honest-refuted-experiment-logging` 3개가
  더 있는데, 아직 Agent-Evaluator의 기본 배포로 역이식되지 않은
  상태였다(과거 CHANGELOG.md 이력을 보면 이런 역이식을 두 차례 해온
  패턴이 있음).

1차 질의응답에서 사용자에게 "URL 내용은 이미 기본 배포에 포함돼 있어
추가로 할 일이 없다 vs. 우리 쪽 3개를 역으로 Agent-Evaluator에
백포트한다" 둘 중 뭘 원하는지 물었더니 **둘 다 아니었다** — 사용자의
실제 의도는 **HAIR-Platform의 "도구/스킬 Pool" 화면(§18)에 이 절차
스킬들을 기본으로 노출하라**는 것이었다(HAIR-Platform이 모델링하는
SDLC 팀 — 백엔드 개발자·QA 등 역할의 AI 에이전트 — 이 실제로 이
방법론을 따르는 조직이라는 전제).

**구현**: `db._seed_default_pools()`에 18개 절차 스킬(agent-evaluator
기본 배포 15개 + 이 워크북 자체 발굴 3개)을 `skill_pool`에 추가로
시드했다. `augmentation_mode='none'`으로 등록했다 — VectorDB/GraphDB
스킬과 달리 이 절차 스킬들은 CrewAI Tool로 감쌀 수 있는 실행 가능한
함수가 아니라 Claude Code/OpenCode 세션이 참고하는 절차 문서(SKILL.md)
이기 때문에, 자동으로 붙일 도구가 없다 — Agent가 이 skill_tag를
"보유하고 있다"는 표시만 남기고, 실제 자동화(예: 에이전트가 이 절차를
CLI로 직접 실행)는 향후 과제로 남긴다.

기존 골든셋(GC108, GC114)이 skill_pool/tool_pool 시드 개수를 각각 4개·
2개로 못박고 있었는데(정확히 vectordb/rag/graphdb/관계질의 4개, VectorDB/
GraphDB 도구 2개만 있다고 단언), 이번 변경으로 각각 22개·3개가 되므로
두 테스트를 "정확히 N개"에서 "그 N개가 부분집합으로 포함돼 있다"는
형태로 완화했다 — 실제 동작 변화를 반영한 의도된 수정이지 회귀가
아니다.

이어서 사용자가 "CrewAI AI Agent가 개발 단계에 필요한 기능일지 확인하고,
필요한 경우 AI Agent가 호출할 수 있도록 변형해줘"라고 재요청해서 18개를
하나씩 실제로 검토했다:

- **17개**는 git/PR 라벨 판단, SDK 소스 코드 직접 읽기, CI 신호 해석,
  `.aoo/` 상태 파일 대조 같은 HITL(사람 또는 Claude Code 세션의 판단)
  절차라서, 안전하게 단일 함수 호출로 옮길 방법이 없었다 — 억지로
  "도구"로 만들면 속 빈 no-op이 되거나, AI Agent에게 임의 CLI 실행/저장소
  쓰기 권한을 새로 열어줘야 해서(보안 위험 확대) 그대로 `augmentation_mode
  ='none'`(skill_tag 노출만) 유지했다.
- **`recommend-fix-workflow`만 예외였다** — 실제로 `agent_evaluator.
  integrations.recommend_fix_mcp`가 이미 읽기 전용 stdio MCP 서버로
  구현돼 있었다(`recommend_fix(gate, metric, value) -> str`, 노출 도구
  단 하나). `.aoo` 실행 이력이 없어도 정적 지식만으로 안전하게 동작하고
  부작용이 없어서, `tool_pool`에 `kind='mcp_stdio'`로 등록하고
  (`command=sys.executable`, HAIR-Platform이 실행 중인 바로 그 venv를
  보장) `recommend-fix-workflow` 스킬을 `augmentation_mode='tool'`로
  승격해 실제로 연결했다.

**실측 검증(추측 아님)**: `agent_pool`에 `skill_tags=["recommend-fix-
workflow"]`인 Agent를 만들고 `pool_registry.resolve_pool_tools()`를 mock
없이 그대로 호출해, `recommend_fix` MCP 서버가 실제로 서브프로세스로
뜨고(`ListToolsRequest`/`CallToolRequest` 로그 확인) `gate="D"` 호출이
"[Gate D — Performance Contract] SLA threshold exceeded..." 같은 실제
정적 지식을 반환하는 것까지 확인했다.

신규 골든셋 GC125–126(`tests/unit/test_phase16_default_skill_exposure.py`)
으로 17개는 `augmentation_mode='none'`+빈 도구 목록, `recommend-fix-
workflow` 하나만 `'tool'`+`mcp_stdio` 도구 연결인 것과 멱등성, 매니저
role의 조회 권한을 검증했다. 총 129건 통과.

**오픈 이슈**:
- 나머지 17개는 여전히 "노출"만 됐을 뿐 실제 실행 경로가 없다 — 향후
  "AI Agent가 `agent-eval` CLI를 직접 호출하게 한다" 같은 요구가 생기면
  그때 개별적으로 안전한 wrapping 방법(예: 화이트리스트된 서브커맨드만
  허용하는 native Tool)을 다시 설계해야 한다.
- `recommend_fix` 도구는 `agent-evaluator[mcp]`(=`mcp` 패키지)가 설치돼
  있어야 실제로 접속된다 — 없으면 기존 MCP 기능과 동일하게
  `connect_mcp_tools()`의 지연 import에서 실패한다(신규 실패 모드
  아님, HAIR-Platform 자체 venv에는 이미 설치돼 있어 데모에서는
  문제없이 동작한다).
- **실측으로 발견한 진짜 마이그레이션 갭**: 이 데모 서버는 `recommend-
  fix-workflow`가 `augmentation_mode='none'`이던 이전 커밋 상태로 이미
  한 번 `init_schema()`가 실행된 `hair.db`를 갖고 있었다. `INSERT OR
  IGNORE`(관리자가 이후 수정해도 재기동 때 안 덮어쓰는 설계, §18/§20
  공통 원칙)는 `name` UNIQUE 제약으로 그 기존 skill_pool 행을 그대로
  둬서, 코드를 배포하고 서버를 재시작해도 `recommend-fix-workflow`가
  자동으로 `'tool'`로 승격되지 않았다(재시작 후 API로 직접 확인해서
  발견) — **시드 값 자체가 바뀌는 마이그레이션은 `INSERT OR IGNORE`로
  커버되지 않는다**는 일반적 한계다. 이번 데모 인스턴스는 `sqlite3
  hair.db`로 그 한 행을 수동으로 맞춰줬다(운영 배포라면 별도 데이터
  마이그레이션 스크립트가 필요하다는 뜻).

## 21. 각 메뉴 화면에 사용자/운영자용 상세 설명 보강 — 사용자 요청(2026-09-28,
    "각 메뉴별로 위 분석 내용처럼 사용자/운영자가 알아야 할 내용들에 대한
    친절하게 상세히 설명되도록 개선해줘")

직전에 "AI 에이전트 생성" 메뉴의 stub/simple_llm/crewai 세 옵션 차이를
채팅으로 자세히 설명한 뒤, 사용자가 그 수준의 설명을 Web UI 자체(각 탭의
안내 문구)에도 반영해달라고 요청했다. 14개 탭 전부를 실제 백엔드 로직
(assignment.py 스코어링 가중치, approvals.py 다단계 결재·self-approval,
performance.py NF9 동의 게이팅, cost.py/fairness.py 계산식, audit.py
append-only, pool_registry.py augmentation_mode 의미, jobs.py/
crew_runtime.py 생성 경로 차이 등)을 다시 읽어 근거를 확인한 뒤, 각 탭의
`<p class="muted">` 안내 문구를 추가/보강했다 — 특히 다음처럼 코드를 직접
확인하기 전까지는 몰랐을 만한(또는 추측만으로 잘못 쓸 뻔한) 내용을
정확히 반영했다:

- **조직도**: 사람 배정은 그 사람의 "상사"로 묶이지만, AI 에이전트는
  상사가 없어서 그 배정을 **요청한 사람**으로 귀속된다(코드 재점검
  R1에서 고친 실제 동작 그대로 설명).
- **사람 인력 관리**: "오프보딩"은 이 화면에서 되돌릴 버튼이 없다(API에도
  역방향 엔드포인트가 없음 — 직접 코드로 확인) — 처음엔 "가용성만
  바꾸는 되돌릴 수 있는 조치"라고 쓰려다가, `app.py`의 실제 엔드포인트
  목록을 확인해서 "사실상 편도"로 정정했다.
- **AI 에이전트 관리**: "오프보딩"(active→deprecated) 후 온보딩 승인
  버튼은 pending_onboarding 상태에서만 보이므로, 백엔드 자체는 상태
  무관하게 승인 처리를 허용해도 이 화면에서는 되돌릴 방법이 없다.
- **비용**: "합계"는 실제 LLM API 청구액이 아니라 수동 입력한 단가 ×
  확정 배정 수일 뿐이라는 것을 명시(1차 범위 한계, `cost.py` docstring
  그대로 반영).
- **사용자 관리**: 상사 순환 참조는 본인 직접 지정만 막혀 있고
  A→B→A 같은 2단계 이상 순환은 막지 않는다(`app.py`의
  `set_user_manager` 실제 검증 로직 확인) — 다단계 체인 계산 시
  조용히 그 지점에서 끊길 뿐 무한루프는 안 난다는 것까지 설명.

**실측으로 발견한 회귀**: 골든셋 GC60(`test_gc60_root_page_serves_html_
shell`)이 스펙 코드(`"F8 "` 등)가 화면에 그대로 노출되면 안 된다고
단언하고 있었는데(코드 재점검 R5, 2026-09-26 — 스펙 코드가 화면에 그대로
노출돼 일반 사용자가 이해하기 어렵다는 지적으로 이미 한 번 고쳐진
이력), 사람 인력 관리 탭 설명에 "F8 성과 리뷰"라고 무심코 썼다가 이
테스트가 실제로 잡아냈다 — `pytest`를 실행하지 않았다면 놓쳤을 회귀다.
"F8 " 대신 그냥 "성과 리뷰"로 고쳐 통과시켰다. 전체 129건 통과 그대로
유지.

`node --check`로 JS 문법 재검증, `ruff`/`black` 클린, 데모 서버 재기동
후 `curl`로 실제 페이지에 새 안내 문구가 서빙되는 것까지 확인했다.

## 22. Phase 1 구현 — 협업 워크스페이스(결과물 엔티티 + 내 업무 + Skill/
    Tool 역할 재정의) — 사용자 요청(2026-09-28, "Phase 1부터 시작해줘")

`docs/PHASE1_DELIVERABLE_SPEC.md`를 그대로 구현했다: 신규 테이블
`deliverables`/`deliverable_comments`, 신규 모듈 `deliverables.py`,
`app.py`에 `GET /api/my-work` 등 7개 엔드포인트, `webui.py`에 "내 업무"
탭 + 협업 워크스페이스(결과물 타임라인 + 댓글 + "LLM으로 도와줘" +
"AI 에이전트 실행"). 골든셋 GC127–137(11건) 추가, 전체 140건 통과.

**실측으로 발견한 진짜 선행 버그(이번 작업 전부터 있었던 것)**:
`human_pool.user_id`는 F8 `performance.review_for_member()`("팀원용 —
본인과 연결된 Human Pool 레코드만")가 이미 참조하고 있었는데, **그
컬럼을 실제로 채우는 API가 지금까지 전혀 없었다** — `POST /api/
human-pool`도, 기존 `PATCH /api/human-pool/{id}`(`HumanPoolUpdate`)도
`user_id` 필드가 없었다. 즉 어떤 Human Pool 레코드도 로그인 계정과
연결될 방법이 없어서, F8의 "내 성과점수 조회"는 v4에서 구현된 이래
지금까지 어떤 팀원에게도 실제로 동작한 적이 없었을 것이다(누구든
호출하면 항상 빈 배열). `HumanPoolUpdate`에 `user_id` 필드를 추가하고
"사람 인력 관리" 탭에 "계정 연결" UI를 넣어서 메웠다 — Phase 1의 "내
업무" 기능뿐 아니라 F8도 이번에 덤으로 처음 실제 동작하게 됐다.

**설계 시 조정한 것**: 스펙 초안은 §2-6(Agent 실행)에
`crew_runtime.run_crew()`(여러 Agent를 Crew로 묶는 함수)를 쓰라고
적었으나, "이 task에 배정된 Agent 하나만 실행"하는 용도엔 이미 있는
단일 Agent 온디맨드 실행 경로 `agent_runtime.execute_agent()`가 더
적합해 그걸로 바꿔 구현했다(Agent별 저장된 provider/model, RAG 증강을
그대로 재사용). `llm.system_prompt_for_role()`("당신은... AI
에이전트입니다")도 사람의 LLM 보조 맥락과 안 맞아
`system_prompt_for_human_assist()`를 새로 추가했다.

**RBAC 설계 확정**(`HAIR_MASTER_PLAN.md` §8 결정사항 1번 해소): 팀원은
LLM 보조/Agent 실행을 직접 트리거할 수 있지만 provider/model은 선택할
수 없다(비용 통제) — 매니저/관리자만 가능. "작성"(deliverable/댓글)은
그 task의 참가자 본인만 가능하고 관리자/매니저도 대리 작성은 못 하지만,
"실행"(execute-agent)은 감독자 대리를 허용한다(저자성이 안 흐려지므로).

**실측 검증**: 로컬 데모 서버(포트 8123)에서 브리프→할일→사람 등록→
계정 연결→배정 요청→승인→"내 업무" 조회→결과물 제출→댓글 작성→
확정 Agent 배정이 없을 때의 409까지 실제 HTTP 요청으로 확인했다.
`assist`/`execute-agent`의 실제 LLM 호출 성공 경로는 비용 문제로
라이브 서버에선 실행하지 않고, GC133/GC135의 mock 기반 검증(호출
인자 자체를 확인)으로 대신했다.

**Phase 1 스코프 밖(의도적으로 안 한 것)**: deliverable 승인/반려
(Phase 2), Agent 자동 평가(Phase 3), 실행 중 일시정지(Phase 4) — 모두
`docs/PHASE1_DELIVERABLE_SPEC.md` §7 참고.

## 23. Phase 2 구현 — 산출물 승인 게이트(HITL) — 사용자 요청(2026-09-28,
    "다음 단계 진행해줘")

`docs/COLLABORATION_PLATFORM_PLAN.md` Phase B를 구현했다: Agent가 만든
deliverable(`status='pending_review'`)이 생기는 즉시
`approvals.create_approval_chain()`으로 결재 체인을 만든다
(`requested_by`=execute-agent를 트리거한 사람, `subject_type=
'deliverable'`) — **새 승인 로직을 만들지 않고 F7 다단계 결재라인·ADR
3-A 자기승인 방지를 그대로 재사용**했다. `app.py`의 `decide_approval()`
(원래 `subject_type=='assignment'`만 특수 처리하던 지점)에
`subject_type=='deliverable'` 분기를 추가해 `deliverables.
resolve_deliverable_decision()`을 호출하도록 확장했다.

**설계 결정**:
- 사람이 작성한 deliverable(`'draft'`)은 승인 대상이 아니다 — Agent
  산출물만 게이트를 탄다.
- 결재 체인의 `requested_by`는 execute-agent를 **호출한 사람**(`actor_id`)
  이다 — 감독자 대리 실행이었다면 그 감독자가 자기 트리거의 승인자가
  될 수 없다(자기 트리거 = 자기 요청이라는 원칙, ADR 3-A와 동일 정신).
- `GET /api/tasks/{id}/deliverables`가 `pending_review`인 항목에 한해
  지금 결정 가능한 결재 id를 `approval_id`로 함께 내려준다 — Phase 1
  설계 그대로 "결재 탭으로 안 보내고 워크스페이스 안에서 바로
  승인/반려"가 되도록 Web UI에 인라인 승인/반려 버튼을 추가했다(관리자/
  매니저에게만 노출 — `decide_approval` 자체가 그 role만 허용하므로
  팀원에게 버튼을 보여줘 봐야 403만 난다).
- **"수정 후 재제출"(`revises_id`)은 Phase 2 스코프에서도 뺐다** —
  반려된 산출물에 새 실행 결과를 어떤 기준으로 연결할지(가장 최근
  반려건? 명시적 선택?)가 아직 안 정해져서, 임의로 정하면 나중에
  바꾸기 어려운 결정을 지금 섣불리 내리는 게 된다. 지금은 반려돼도
  같은 task에서 execute-agent를 다시 실행하면 완전히 새로운
  deliverable+결재 체인이 생길 뿐 — 이전 반려 기록과의 명시적 연결은
  없다(감사로그로 이력 추적은 가능).

**검증**: 골든셋 GC138–142(5건) 추가, 전체 145건 통과. `POST /api/
approvals/{id}/decide` 응답에 `deliverable` 키를 추가했다(기존
`assignment` 키와 나란히, 하위호환 — 기존 소비자는 새 키를 무시하면
그만). ruff/black 클린, 데모 서버 재기동 후 기존 엔드포인트(`/api/
my-work`, `/api/approvals?scope=inbox`) 회귀 없음을 확인했다 — 실제
LLM 호출이 필요한 승인 생성 자체는 비용 문제로 라이브 서버에서 실행하지
않고 mock 기반 골든셋으로 검증했다(Phase 1과 같은 판단).

## 요약

| 항목 | 상태 |
|---|---|
| Gate A–G 실측 | 배선 완료(`eval_wiring.py`) — **저장 버그(`auto_save=False`) 발견·수정 후 `agent-eval gate` 채점까지 실제로 성공**(TCR 100% PASS, Gate A/G 미달 예상대로 확인). AgentForge 실제 생성기 연동은 여전히 오픈 이슈 |
| Docker 샌드박스 실제 격리 | 미검증(환경에 Docker 없음) — 명령 구성·우회 방지는 검증 완료 |
| 2인 팀 실전 클레임 충돌 | 불가(단독 세션) — CI 인프라는 구축 완료 |
| Skills 신규 발굴 | 1/4건 완료(`rbac-permission-audit`), 나머지는 3회+ 기준 미충족 |
| NF7 idempotency | **구현 갭 발견 → 수정 완료** |
| NF4 배정 공정성 지표 | 구현 완료, 표본 부족으로 해석 보류 |
| 코드 레벨 재점검 1회차(승인 우회·검색 필터·조직도 귀속·manager_id 누락) | **전부 발견 → 전부 수정 완료(GC42–46)** |
| 코드 레벨 재점검 2회차(승인 inbox·사용자 목록·감사로그 페이지네이션·secret_key) | **전부 발견 → 전부 수정 완료(GC47–50)** |
| 코드 레벨 재점검 3회차(매칭 role 필터·콜드스타트 부트스트랩·Human Pool 수정/오프보딩) | **전부 발견 → 전부 수정 완료(GC51–55)** |
| 코드 레벨 재점검 4회차(F11 Web UI 셸 전무 → 구현) | **발견 → 구현 완료(GC56–61), 단 실제 브라우저 수동 조작은 미검증** |
| 실제 LLM(Anthropic/OpenAI) 연동 | 배선 완료(GC62–68, mock 검증) + **실제 OpenAI API로 실측 완료**(SDLC 8역할 에이전트 생성) |
| Ollama 지원 + Agent별 모델 설정 | 배선 완료(GC69–72, mock 검증), 이 환경에 Ollama 로컬 서버가 없어 실측은 미검증 |
| 실제 CrewAI 연동(단일 생성+다중 Agent 협업) | 구현 완료, 실제 crewai 패키지로 통합 검증(GC77–82, LLM 호출만 mock) — 검증 중 실제 API 호출 사고 발생 → `Crew.kickoff` 경계로 수정 |
| VectorDB 지식검색 Agent + GraphDB 관계분석 Agent | 구현 완료(GC83–92), NF9 경계 소스 검사로 강제. 실제 임베딩 API 실측은 미검증(mock만) |
| crewai 설치로 인한 pytest 전역 파손(arize-phoenix 충돌) | **발견 → 수정 완료**(`addopts = "-p no:phoenix"`) |
| 실사용 중 발견된 500 에러(agent_pool 스키마 마이그레이션 누락) | **사용자 보고 → 발견·수정 완료**(`db._migrate_schema()`, GC95–97) |
| CrewAI 기능 적용 리뷰: manager_agent_id+hierarchical 크래시 | **발견 → 수정 완료**(GC98–99) |
| CrewAI 기능 적용 리뷰: memory=True·max_iter 문서-코드 불일치 | **발견 → 수정 완료**(GC100) |
| CrewAI 기능 적용 리뷰: task_callback 감사로그 세분화 | **구현 완료**(GC101), step_callback은 Agent 식별 불가로 의도적 보류 |
| 실사용 발견: hierarchical에서 task_callback 매니저 오귀속 + Web UI null 출력 크래시 | **사용자 실제 테스트 중 발견 → 발견·수정 완료**(GC102) |
| MCP(외부 도구) 연동 — `run_crew()`에 MCPServerAdapter 실제 연결 | **구현 완료**(GC103–105), 접속 가능한 실제 MCP 서버가 없어 종단간 검증은 미검증(mock 경계: `MCPServerAdapter`) |
| E2E 시나리오(사람+AI 혼합 팀 구성) 전 과정 실행 | **F1–F14 핵심 흐름 실측 확인, UX 문제 3건 발견·수정**(브리프 placeholder, 제안 역할 클릭 채우기, 매칭 실패 안내 문구) |
| CrewAI 개념 활용도 감사 — RAG 증강이 Crew 협업에서 누락됨 | **실사용 갭 발견 → 수정 완료**(당시 GC106), human_input/token_usage/delegation/planning 4건 실측 판단(§17) — 이후 §18에서 정적 주입 자체를 Tool 방식으로 대체하며 GC106 내용도 갱신됨 |
| Skill Pool·Tool Pool + VectorDB/GraphDB의 CrewAI Tool 전환 | **구현 완료**(GC106-115) — 3개 신규 테이블·`pool_registry.py`·CRUD API·Web UI까지 실제 구현, 하드코딩된 스킬→도구 매핑을 데이터로 완전 대체(§18) |
| 데이터 삭제/초기화 기능(도구/스킬/사람/에이전트/brief/task) | **구현 완료**(GC116-124) — FK 순서 버그·테스트 self-approval 버그 실측으로 발견·수정, confirmed 배정 안전장치, 활동 기록은 NF10 트리거로 삭제 불가라 범위 제외(§19) |
| Agent-Evaluator/하니스 방법론 절차 스킬 18개를 Skill Pool에 기본 노출 | **구현 완료**(GC125-126) — 17개는 `augmentation_mode='none'`(HITL 절차라 안전한 도구화 불가), `recommend-fix-workflow` 1개는 실제 `recommend_fix` stdio MCP 도구로 승격해 실측 연결까지 확인. 기존 GC108/GC114 시드 개수 단언을 실제 동작에 맞게 완화(§20) |
| 각 메뉴 화면 사용자/운영자용 상세 설명 보강 | **구현 완료** — 14개 탭 전부 백엔드 로직을 재확인해 안내 문구 추가/보강, 스펙 코드 노출 금지 회귀(GC60)를 실측으로 발견·수정(§21) |
| Phase 1: 협업 워크스페이스(결과물 엔티티+내 업무+Skill/Tool 재정의) | **구현 완료**(GC127-137) — `human_pool.user_id`를 채울 API가 지금까지 전혀 없어 F8도 실제로는 한 번도 동작 안 했을 실측 선행 버그 발견·수정. execute-agent는 스펙의 `run_crew()` 대신 `agent_runtime.execute_agent()`로 구현(§22) |
| Phase 2: 산출물 승인 게이트(HITL) | **구현 완료**(GC138-142) — 새 승인 로직 없이 F7 다단계 결재라인+ADR 3-A 자기승인 방지를 `subject_type='deliverable'`로 그대로 재사용. "수정 후 재제출" 연결 기준은 아직 미정이라 의도적으로 스코프 밖(§23) |
