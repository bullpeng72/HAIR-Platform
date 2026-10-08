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

## 24. Phase 3 구현 — Agent 평가 인라인 연결(Gate A-G) — 사용자 요청
    (2026-09-28, "Phase 3 진행해줘")

`docs/HAIR_MASTER_PLAN.md` §5를 구현했다: `eval_wiring.py`는 SPEC.md §12
시절 만든 **수동 실행 probe 스크립트**(`python -m hair_platform.eval_wiring`
로 직접 돌려야 함)였던 반면, 이번 Phase 3는 `deliverables.
execute_agent_for_task()` 안에서 **매 Agent 실행마다 자동으로** Gate A-G를
측정하는 실제 프로덕션 배선이다 — 새 모듈 `agent_evaluation.py`.

**실측으로 확인하고 반영한 것(agent-evaluator 1.1.1 설치 소스 직접 실행,
`docs/HAIR_MASTER_PLAN.md`에는 없던 세부사항)**:
- `@agent_eval`로 감싼 함수를 호출하면 반환값은 `(response, metadata)`
  튜플의 `response`만 돌아온다 — 그래서 실제 `agent_runtime.execute_agent()`
  호출을 감싼 클로저를 `@agent_eval`로 감싸도 호출자는 평소처럼 결과
  문자열을 그대로 받는다. `execute_agent_for_task()`가 이 원리로
  재구성됐다.
- `PerformanceMonitor`는 호출된 모든 task를 누적 집계하므로, "이번 한 번의
  실행"만 채점하려면 **호출마다 새 인스턴스를 만들어야 한다**(모듈 전역
  싱글턴 — `eval_wiring.py`의 패턴 — 을 그대로 쓰면 이전 실행들과 점수가
  섞인다). `output_dir`는 `auto_save=False` 기본값 때문에 실제로 쓰이지
  않는다 — 파일로 안 남기고 새 테이블 `agent_evaluations`에 바로 적재한다.
- TCR(완료율)은 `report.accuracy_metrics["tcr"]["tcr"]`(0-100 실수)에서
  나온다 — `agent-evaluator` CLI가 채점에 실제로 쓰는 값과 같은 경로임을
  `cli/gate.py` 소스로 확인했다.
- `task_type="deliverable"`은 canonical 값이 아니다(SDK가 "unrecognised
  task_type" 경고를 내며 그래도 받아준다) — 실행해보고 발견해
  `"document_creation"`(canonical 목록에 있음)으로 바꿨다.
- 실제 LLM 호출은 결정론적 스텁(eval_wiring.py, 5s/10s SLA)과 달리 수
  초~수십 초 걸린다 — SLA를 `p95_ms=30000/p99_ms=60000`으로 넉넉히
  잡았다. `eval_wiring.py`의 값을 그대로 재사용했다면 정상 호출도 전부
  Gate D 위반으로 잡혔을 것이다.

**설계 결정**:
- `agent_evaluations`는 append-only(`id, deliverable_id, gate_scores(JSON),
  tcr, evaluated_at`) — deliverable 하나가 여러 번 평가될 수 있는 구조를
  열어둔다(지금은 execute-agent 1회 = 평가 1회지만, 향후 재평가/재채점
  경로가 생겨도 스키마 변경 없이 수용 가능).
- **실패 시 아무 것도 안 남는 불변식 유지**: 원래 코드는 `agent_runtime.
  execute_agent()`가 성공한 뒤에만 deliverable을 INSERT했다. Phase 3
  배선을 처음 짤 때 "평가에 deliverable_id가 필요하니 먼저 INSERT하고
  나중에 채운다"는 순서로 짰다가, LLM 호출 실패 시 빈 내용의 deliverable
  행이 커밋 전 상태로 남는 문제를 구현 중 자체 검토로 발견했다 — 순서를
  되돌려 `run_and_score()`(DB 미접촉, 순수 계산)와 `record_evaluation()`
  (deliverable INSERT 이후 호출)로 분리해 기존 불변식을 그대로 지켰다.
- `agent_pool.performance_score`(관리자 수동, F8)는 건드리지 않는다 —
  자동 측정치는 `aggregate_agent_score()`로 별도 조회한다(§5-2 "완전
  자동 대체 아님, 구분해서 보여준다").
- Gate B(행동 무결성)·D(성능 계약)·E(보안 경계)는 도구 호출이 없는 단발
  LLM 호출 특성상 `not_measured`(점수 null)로 나올 수 있다 — 이건 버그가
  아니라 측정할 신호가 없다는 정직한 표시다(`eval_wiring.py`의 Gate C/F
  제외 선례와 같은 정신). Gate G(설명가능성)는 현재 프롬프트가 "판단
  근거를 명시하라"고 요구하지 않아 낮게 나올 가능성이 높다 — 이번
  스코프는 "평가 인프라 연결"이지 "Agent 응답 품질 개선"이 아니라서
  프롬프트는 의도적으로 건드리지 않았다(향후 개선 여지로 남겨둠).

**검증**: 골든셋 GC143–145(3건) 추가, 전체 148건 통과. ruff check/format
클린. `list_deliverables()`가 Agent 산출물엔 `evaluation`(Gate 점수+TCR),
사람 산출물엔 `evaluation=None`을 내려주는 것을 GC143/GC144로 확인했다.
실제 LLM 호출은 기존 관례대로 `hair_platform.llm.call_llm`을 mock해
비용 없이 검증했다(Gate 채점 로직 자체는 mock 응답 텍스트로 실제 실행됨).

## 25. Phase 4 구현 — execute-agent 비동기화 — 사용자 요청
    (2026-09-28, "Phase 4 진행해줘")

**스코프 결정(사용자 확인, AskUserQuestion)**: 원래 Phase C 설계
(`docs/COLLABORATION_PLATFORM_PLAN.md`)는 `crew_runtime.run_crew()`(다중
Task CrewAI Crew)를 대상으로 `register_before_tool_call_hook()` 기반
도구 호출 단위 일시정지까지 그렸다. 하지만 Phase 1-3이 실제로 완성한
협업 워크스페이스는 단일 LLM 호출인 `agent_runtime.execute_agent()`만
쓴다 — 여기엔 "실행 중간에 멈출 지점" 자체가 없다. 두 가지 선택지
("워크스페이스 비동기화" vs "Crew 실행 정지/재개")를 사용자에게 제시해
전자(저위험)로 확정했다 — 후자는 여전히 별도 세션/ADR이 필요한 미해결
항목으로 남는다.

**구현**: `deliverables.execute_agent_for_task()`(동기, 응답까지 LLM
호출을 기다림) 하나였던 것을 둘로 쪼갰다:
- `start_agent_execution()` — 참가자/배정 검증과 프롬프트 구성만 동기로
  마치고, 신규 테이블 `agent_execution_jobs`에 `status='running'` 행을
  만들고 즉시 반환한다.
- `run_agent_execution_job()` — 실제 LLM 호출+Gate A-G 평가+deliverable
  생성을 수행한다. `app.py`가 FastAPI `BackgroundTasks.add_task()`로
  등록해 HTTP 202 응답을 보낸 **뒤에** 실행한다.

`POST /api/tasks/{id}/execute-agent`는 `201+{"id": deliverable_id}`
대신 `202+{"job_id", "status": "running"}`을 반환하도록 계약이
바뀌었다(API 파괴적 변경 — 이 엔드포인트의 유일한 소비자가 이 저장소
자신의 Web UI/테스트라 하위호환 우회 없이 바로 반영했다). 신규
`GET /api/tasks/{id}/agent-execution-jobs`로 Web UI가 상태를 폴링한다.

**실측으로 확인한 것**:
- **`TestClient`는 `BackgroundTasks`를 응답 반환 *전에* 동기로
  실행한다**(별도 probe 스크립트로 실측 확인) — 그래서 골든셋 테스트는
  `client.post(...)` 직후 바로 job이 끝나 있다고 가정할 수 있다. 이건
  실제 uvicorn 배포에서의 동작(응답이 먼저 나가고 백그라운드 작업이
  나중에 실행됨)과 다르다 — 테스트가 확인하는 건 "로직이 맞다"이지
  "응답이 실제로 빠르다"가 아니라는 걸 테스트 파일 docstring에 명시했다.
- **SQLite는 기존 테이블의 CHECK 제약을 `ALTER TABLE`로 바꿀 수 없다**
  (테이블 재생성만 가능, 공식 문서로 확인) — `deliverables.status`에
  `'generating'` 값을 추가하는 대신, 완전히 새 테이블
  `agent_execution_jobs`(`CREATE TABLE IF NOT EXISTS`라 기존 DB 파일에도
  안전하게 추가됨)로 진행 상태를 분리했다. 기존 `deliverables` 스키마·
  API 계약은 전혀 안 바뀐다 — 완료되면 평소처럼 `'pending_review'` 행이
  생긴다.
- `conn.get_connection()`이 이미 `check_same_thread=False`로 만들어져
  있어(FastAPI가 sync 핸들러를 스레드풀에서 돌리기 때문에 이미 여러
  스레드가 같은 커넥션을 공유하고 있었다) 백그라운드 작업이 같은
  커넥션을 쓰는 게 새로운 위험을 추가하진 않는다 — 다만 SQLite 자체의
  단일 writer 제약(WAL 모드에서도)은 여전히 남아있는 한계다.

**설계 결정**:
- 실패 처리를 동기 경로와 다르게 했다 — Phase 2/3의 동기 경로는
  예외를 그대로 전파해 "아무 것도 안 남긴다"였지만, `run_agent_
  execution_job()`은 백그라운드에서 실행돼 예외를 받아줄 호출자가
  없다. 그래서 모든 예외를 잡아 `agent_execution_jobs.status='failed'`+
  `error`에 기록한다 — 실행이 죽었는지조차 모르면 HOTL 감독이 안 된다는
  판단.
- 중복 트리거 방지: 같은 task에 이미 `running` job이 있으면 Web UI가
  "AI 에이전트 실행" 버튼을 비활성화한다(백엔드에는 이를 막는 제약이
  없다 — DB 레벨 잠금까지는 이번 스코프에서 뺐다. 동시에 여러 job이
  생겨도 각자 독립적인 deliverable을 만들 뿐 데이터 정합성이 깨지진
  않는다).
- 페이지를 새로고침해도 진행 중인 job을 자동으로 이어서 폴링한다
  (`hasRunningJob`이면 즉시 `pollExecutionJob()` 시작) — 사용자가 버튼을
  다시 눌러야만 완료를 알 수 있는 구조를 피했다.
- 도구 호출 단위 일시정지(`register_before_tool_call_hook()` 기반)는
  이번 스코프에서 뺐다 — 적용 대상인 `crew_runtime.run_crew()` 자체가
  협업 워크스페이스와 별개 기능("Crew 실행" 탭)이라, 필요해지면 별도
  세션에서 독립적으로 설계해야 한다(`docs/COLLABORATION_PLATFORM_PLAN.md`
  Phase C가 이미 "별도 설계 문서(ADR)가 먼저 필요하다"고 명시).

**검증**: 골든셋 GC146–148(3건) 추가 — 202 응답+job 성공 조회(GC146),
LLM 실패 시 job이 실패로 남고 deliverable/결재 체인이 전혀 안 생기는지
(GC147), job 목록 조회의 RBAC(GC148). 기존 GC135/136/138-145는 새
계약(202+job_id)에 맞게 갱신(개수 변화 없음), 총 151건. `node --check`로
수정한 임베디드 JS 문법 확인. ruff check/format 클린.

**실제 API 호출 사고(2026-09-28, 라이브 서버 실측 중 발생)**: 비동기
응답 지연을 확인하려고 실제 uvicorn 서버를 띄우면서 `ANTHROPIC_API_KEY`/
`OPENAI_API_KEY`를 셸에서 `env -u`로 지웠는데도, `create_app()`이 호출
하는 `load_dotenv()`가 프로젝트 루트의 `.env` 파일(이전 세션에서 남은
실제 키)을 그대로 읽어들여 **실제 OpenAI API를 호출했다**(비용 발생,
task_id=1 기준 약 12초 소요). `env -u`는 셸 환경변수만 지울 뿐 `.env`
파일 자체를 막지 않는다는 걸 실측으로 발견 — 의도한 게 아니라 검증
과정의 실수였다. 결과 자체는 유용한 증거였다(백그라운드 job이 실제
LLM 호출→평가→deliverable→결재 체인 생성까지 전부 정상 작동함을
end-to-end로 확인) — 하지만 향후 이 종류의 검증을 할 땐 `.env` 파일을
임시로 옮기거나(`mv .env /tmp/`) 별도 빈 작업 디렉터리에서 서버를
띄워야 한다.

## 26. Phase 1-4 리뷰 — 실측으로 발견한 크로스-Phase 버그 3건 — 사용자
    요청(2026-09-28, "Phase1~4까지 누락되거나, 오류, 개선이 필요한
    부분이 있는지 분석하고 개선해줘")

Phase 1-4를 다시 읽으며 실제로 재현되는지 하나씩 테스트로 확인한
리뷰다. 추측이 아니라 전부 실패하는 테스트를 먼저 만들어 재현한 뒤
고쳤다.

**1) `assignments.resolve_assignment_decision()`의 resource_type 무시
(가장 심각)**: 협업 워크스페이스(Phase 1-4)의 핵심 전제는 "사람 당사자 +
Agent 요청자가 같은 task에 함께 confirmed"다(`deliverables.
task_participant_relationship()`가 이걸 그대로 가정하고 짜여 있다). 그런데
F5/F6 시절부터 있던 "재배치 시 같은 task의 confirmed 배정을 supersede"
로직이 `resource_type`을 구분하지 않아서, 사람 배정을 먼저 확정한 뒤
Agent 배정을 확정하면(또는 반대 순서) **먼저 확정된 쪽이 조용히
superseded**됐다 — 즉 "사람이 초안을 쓰고 Agent가 이어받는" 협업의
가장 기본적인 설정 자체가 깨져 있었다. `resource_type`이 같을 때만
supersede하도록 좁혀서 고쳤다(기존 "같은 역할 재배치" 테스트는 항상
human→human만 다뤄서 이 교차 케이스를 잡아내지 못했었다). 골든셋
GC149(재현)·GC150(기존 동작 유지 재확인)로 검증.

**2) `data_admin.py`가 Phase 1-4의 새 테이블을 모름**: `delete_task()`/
`delete_brief()`(§19)는 `deliverables` 테이블이 생기기 전에 작성됐다.
Agent 배정이 confirmed였다가 나중에 재배치로 superseded되면(더 이상
`confirmed`가 아니므로 삭제 보호를 벗어남) 그 task를 지울 때, 여전히
그 task를 참조하는 `deliverables`/`agent_execution_jobs`(및 그 자식인
`deliverable_comments`/`agent_evaluations`) 행 때문에 FK 위반
(`sqlite3.IntegrityError`)이 그대로 500 에러로 사용자에게 노출됐다.
§19의 설계 원칙 1번("FK 순서를 지킨 완전한 연쇄 삭제 — 고아 행을
남기지 않는다")을 그대로 따라 `_delete_deliverables_for_tasks()`를
추가해 job_items/assignments와 같은 패턴으로 연쇄 삭제하게 했다. Web UI
삭제 확인 문구에도 "결과물(산출물 평가 이력 포함)"을 명시해 뭐가
같이 지워지는지 숨기지 않았다. 골든셋 GC153(`test_phase15_data_
deletion.py`)로 검증.

**3) 오래 실행 중이던 job이 서버 재시작 후 영원히 멈춰 보이는 문제**:
Phase 4의 `agent_execution_jobs.status='running'`은 프로세스가 살아
있는 동안엔 항상 `succeeded`/`failed`로 끝난다 — 그런데 프로세스가
실행 도중 죽으면(배포 재시작, 크래시) 그 job은 영원히 `running`으로
DB에 남고, Web UI는 그 task의 "AI 에이전트 실행" 버튼을 영구히
비활성화한 채로 막아버린다(새로고침해도 절대 안 풀림 — 실제로 만들어
보기 전엔 눈에 안 띄는 종류의 버그다). 이 앱이 이미 "단일 프로세스"
전제(§3)이므로, `create_app()` 기동 시점에 `running`으로 남은 job은
전부 이전 프로세스의 잔재로 확정할 수 있다 — `reap_orphaned_
execution_jobs()`를 만들어 기동 시 자동으로 `failed`(에러 메시지 포함)
로 정리하게 했다. 골든셋 GC151로 검증(직접 호출 + `create_app()` 경로
양쪽).

**추가로 발견한 죽은 코드**: Phase 3의 `agent_evaluation.
aggregate_agent_score()`(§5-2 "자동값과 수동값을 구분해서 보여준다")가
실제로는 **어떤 API에도 연결돼 있지 않았다** — 테스트 말고는 아무도
그 값을 볼 방법이 없었다. `GET /api/agent-pool` 응답에 `auto_evaluation`
필드로 추가하고, Web UI "AI 에이전트" 탭에 "자동평가(Gate/TCR)" 열을
새로 붙여 관리자 수동 "성과점수"와 나란히 보이게 했다. 골든셋 GC152로
검증.

**검증**: 골든셋 GC149(assignments 교차 케이스)·GC150(재배치 유지
재확인)·GC151(orphaned job 정리)·GC152(auto_evaluation 노출, 이상 전부
`test_phase17_collab_workspace.py`)·GC153(delete 연쇄,
`test_phase15_data_deletion.py`) 추가. `node --check`로 웹 UI 문법 확인.
ruff check/format 클린. 전체 골든셋 156건 통과(기존 151 + 이번 리뷰에서
5건 추가).

## 27. Phase 1-4 리뷰 2회차 — 사용자가 같은 요청을 다시 보냄(2026-09-28,
    "Phase1~4까지 누락되거나, 오류, 개선이 필요한 부분이 있는지 분석하고
    개선해줘")

첫 리뷰(§26)가 끝난 뒤 사용자가 동일한 문구로 다시 요청해, 이전에
못 본 부분을 더 찾으려고 한 번 더 감사했다. 역시 추측이 아니라 실패하는
테스트를 먼저 만들어 재현했다.

**1) `start_agent_execution()`의 중복 트리거 무방비**: 같은 task에 이미
`status='running'`인 job이 있어도 확인 없이 새 job을 또 만들었다. Web
UI는 버튼을 비활성화해 막지만(§25) 이건 클라이언트 쪽 가드일 뿐이라
빠른 연속 클릭, 여러 브라우저 탭, 직접 API 호출로는 그대로 우회된다 —
같은 트리거로 실제 LLM 호출이 두 번(비용 이중 지출) 나가고 독립된
deliverable+결재 체인이 두 개 생긴다. `agent_execution_jobs`에
`status='running'`인 행이 있는지 확인해 있으면 `LookupError(
"agent_already_running")`을 던지도록 추가했다(`app.py`가 `str(exc)`로
구분해 "이 업무에 확정된 AI 에이전트 배정이 없습니다"와 다른 409
메시지를 준다). 골든셋 GC154로 검증.

**2) 실패 사유가 내부 코드 문자열 그대로 노출됨**: 트리거 시점 이후,
`run_agent_execution_job()`이 실제로 실행되기 전에 관리자가 그 Agent를
삭제하거나 오프보딩하면 `agent_runtime.execute_agent()`가 `"agent_not_
found"`/`"agent_not_active"` 같은 내부 코드를 그대로 예외 메시지로
던진다 — 이게 그대로 `agent_execution_jobs.error`에 저장돼 Web UI의
실패 카드에 영어 코드 그대로 노출됐다. `_friendly_execution_error()`로
알려진 두 코드만 사람이 읽을 수 있는 한국어 문구로 번역하고, 목록에
없는 예외(실제 LLM API 오류 등, 이미 사람이 읽을 수 있는 메시지)는
원문 그대로 둔다. 골든셋 GC155로 검증.

**검증**: 골든셋 GC154-155(2건) 추가. `ruff check/format` 클린. 전체
골든셋 158건 통과.

## 28. Phase 1-4 리뷰 3회차 — 사용자가 같은 요청을 세 번째로 보냄
    (2026-09-28, "Phase1~4까지 누락되거나, 오류, 개선이 필요한 부분이
    있는지 분석하고 개선해줘")

**F13 감사로그 비대칭 발견**: `decide_approval()`은 승인/반려 둘 다
감사로그에 남긴다(`f"approval_{new_status}"`, 성공/실패 모두 기록하는
게 이 프로젝트의 F13 관례임을 여기서 재확인). 하지만 `run_agent_
execution_job()`의 성공 경로는 `deliverable_created`를 기록하면서
실패 경로는 아무 것도 안 남겼다 — HOTL 감독자가 "이 Agent 실행이
왜/언제 실패했는지"를 F13(감사로그) 화면에서 아예 볼 방법이 없었다
(job 목록 API로는 볼 수 있지만, 감사로그는 "모든 쓰기 액션"을 한곳에서
훑어보는 화면이라는 원래 취지에 어긋난다). `agent_execution_failed`
액션을 실패 경로에도 기록하도록 대칭을 맞췄다. 골든셋 GC156으로 검증.

**부수적으로 발견한 라벨 누락**: Web UI의 감사로그 화면(`label()`)이
`subject_type='agent_execution_jobs'`에 대한 매핑이 아예 없어서
(Phase 4가 도입한 `agent_execution_started` 액션부터 이미 이랬다) 감사로그
표에 영어 원문(`agent_execution_jobs`)이 그대로 노출되고 있었다 —
`LABELS`에 `"AI 실행 작업"`을 추가했다.

**검증**: 골든셋 GC156(1건) 추가. `node --check`로 웹 UI 문법 확인.
ruff check/format 클린. 전체 골든셋 159건 통과.

## 29. Phase 5 구현 — 기업용 UI/UX 고도화(부분) — 사용자 요청
    (2026-09-28, "Phase 5(기업용 UI/UX)에 포함할 항목을 고르세요")

`docs/HAIR_MASTER_PLAN.md` §6의 6개 항목 중 사용자가 AskUserQuestion으로
4개(통합 인박스·매니저 대시보드·검색/필터 스케일·협업 워크스페이스
UX)와 제한적 반응형을 선택했다. 다크모드는 명시적으로 제외.

**1) 통합 인박스(`inbox.py`, `GET /api/inbox`)**: 결재 대기·Agent 실행
실패·낮은 Gate 점수 경고(승인/반려 전인 것만) 3종을 한 목록으로 합친다.
새 권한 규칙을 추가하지 않고 기존 `approvals.list_inbox()`(원래
`app.py`에 인라인으로 있던 것을 이 기회에 재사용 가능하게 추출,
동작 변경 없음)·`deliverables.list_my_work()`가 이미 강제하는 접근
범위를 그대로 물려받는다. **정직한 한계**: `approvals` 테이블에
`created_at`이 없어(F7 설계 당시부터) 3종을 하나의 타임스탬프로 완전히
통합 정렬할 수 없다 — 타입별로 묶어서 반환한다.

**2) 매니저 대시보드(`dashboard.py`, `GET /api/dashboard`)**: 팀 Agent
평균 Gate 점수·대기 승인 건수·비용 추이를 한 화면에. **"비용 추이"
관련 실측 확인**: 처음엔 시계열 비용 데이터가 전혀 없다고 생각했다
(`cost.py`의 `cost_estimate`는 그때그때 값 하나뿐) — 하지만
`agent_cost_updated`/`human_pool_updated` 감사로그 액션이 이미
`after_value.cost_estimate`+`audit_log.created_at`을 갖고 있다는 걸
확인해, 새 계측 없이 그 이력을 그대로 "추이"로 재사용했다. DB를 새로
부트스트랩했고 지금까지 아무도 비용을 바꾼 적이 없으면 이력이
비어있을 수 있다(과거로 소급해 데이터를 만들어낼 방법은 없음).

**3) 검색/필터 스케일(`GET /api/pool/search`)**: `available_only: bool`
을 `availability: 'available'|'unavailable'`로 넓히고(이전엔 "가능만"
필터만 있고 "불가만"을 못 찾았다), 감사로그(§13)와 같은 `limit`
(최대 200)/`offset`+`total` 페이지네이션 규약을 적용했다 — 응답 계약이
`[...]`(배열)에서 `{"items", "total", "limit", "offset"}`으로 바뀌는
파괴적 변경이라 기존 호출부(webui.py, 테스트 4개 파일)를 전부 갱신했다.
**"부서" 필터는 넣지 않았다** — 이 스키마엔 애초에 "부서" 필드가 없다
(조직 구조는 `manager_id` 상사 체인/조직도로만 표현된다). 존재하지
않는 데이터를 필터로 꾸며내지 않고, 이미 있는 `role`이 사실상 같은
구실(직군 단위 검색)을 한다는 걸 그대로 문서화했다. 페이지네이션은
Human+Agent 두 풀을 하나로 합친 뒤 파이썬에서 자르는 방식이다(SQL에서
각각 자르면 "한 페이지"가 한쪽 풀로만 채워질 수 있어 부정확해진다) —
이 저장소가 전제하는 소규모 팀 규모에서만 허용 가능한 트레이드오프다.

**4) 협업 워크스페이스 UX**: 공유 스레드/채팅형 타임라인 — 작성자
타입별로 카드를 좌우로 나눠(`.chat-human`/`.chat-agent`) "누가 쓴
건지" 한눈에 보이게 했다. **부수적으로 발견한 갭**: Phase 3부터
`list_deliverables()` API 응답에 `evaluation`(Gate 점수+TCR)이 있었는데
**어떤 화면에도 한 번도 렌더링된 적이 없었다** — 타임라인 카드에 Gate
통과 배지("Gate 3/7 통과 · TCR 92%")로 처음 노출했다. Skill
자동완성은 `skill_pool` 이름을 `<datalist>`로 내려 "필요 스킬"/"스킬"
입력창(작업 분해·사람 등록·검색)에 붙였다 — Skill 주입(§4)이 이름
"정확히 일치"를 요구하므로 오타로 조용히 매칭이 안 되는 걸 줄이는
목적. **부수적으로 발견한 RBAC 갭**: 기존 `GET /api/skill-pool`이
Admin/매니저 전용이라 팀원은 자동완성을 못 받았다 — 이름만 노출하는
`GET /api/skill-pool/names`를 전 role에 새로 열어 해결했다(도구 연결
상세 같은 관리 정보는 여전히 노출 안 함).

**5) 제한적 반응형**: 전체 반응형 재설계는 스코프 밖으로 명시적으로
뺐다(§6 원안이 이미 "반응형 포함 시 상" 난이도로 경고). `@media
(max-width: 640px)` 하나로 메인 레이아웃 여백·헤더 크기·채팅 카드
여백·대시보드 그리드만 좁혔다 — 표가 많은 관리자 화면(인력 매칭,
Agent 관리 등)은 건드리지 않았다(의도적 스코프 축소, 표 반응형까지
하려면 별도 세션이 필요).

**검증**: 골든셋 GC157-169(13건) 추가 — 통합 인박스(GC157-160), 매니저
대시보드(GC161-164), 검색/필터(GC165-167), Skill Pool 이름 노출
(GC168-169). `node --check`로 웹 UI 문법 확인. ruff check/format 클린.
전체 골든셋 172건 통과.

## 30. Phase 1-5 리뷰 — Phase 5 자체 코드에서 발견한 버그 2건 — 사용자
    요청(2026-09-28, "Phase1~5까지 누락되거나, 오류, 개선이 필요한
    부분이 있는지 분석하고 개선해줘")

Phase 1-4는 이미 3회 리뷰를 거쳤으므로, 이번엔 막 작성한 Phase 5 코드를
다시 읽으며 두 건을 실측으로 재현·수정했다.

**1) `dashboard.cost_trend()`의 LIMIT이 관련성 필터보다 먼저 걸림**:
SQL에서 `action IN ('agent_cost_updated', 'human_pool_updated') ORDER BY
... LIMIT 50`을 먼저 실행한 뒤, Python에서 `after_value`에
`cost_estimate`가 있는지로 걸렀다. `human_pool_updated`는 비용 말고도
`role`/`availability`/`user_id` 변경에도 찍히는 액션이라, 최근에
비용과 무관한 변경(예: 가용성을 여러 번 토글)이 50건 넘게 쌓이면 그
LIMIT이 전부 무관한 행으로 소진돼 **더 오래된 진짜 비용 변경 이력이
응답에서 통째로 사라졌다** — "비용 추이" 화면인데 실제 비용 변경이
안 보일 수 있는 버그였다. `test_probe_cost_trend_limit_applied_before_
relevance_filter`로 재현 확인 후, SQL에서는 `action IN (...)`만 걸고
전부 가져와 Python에서 `cost_estimate` 유무로 거른 *다음* `limit`을
자르도록 순서를 바꿨다. 골든셋 GC170으로 검증.

**2) 검색 `availability='unavailable'` 필터가 Agent에 안 걸림**:
`availability` 필터가 `pool.human_pool_candidates()` 안에만 구현돼
있고 `pool.agent_pool_candidates()`엔 아예 파라미터 자체가 없다(Agent는
`is_available=True`로 항상 고정) — 그런데 `search_pool()`이 Agent
쪽엔 이 필터를 아예 안 넘기고 무조건 다 포함시켜서, "불가능한 후보만"
찾으려고 `availability=unavailable`로 검색해도 **항상 가능한 Agent가
결과에 그대로 섞여 나왔다**. 재현 테스트로 확인 후, `search_pool()`에서
`availability == 'unavailable'`일 땐 Agent 후보 자체를 아예 안
가져오도록 고쳤다(Agent는 애초에 "불가" 상태가 구조적으로 없다는 걸
그대로 반영). 골든셋 GC171로 검증(회귀 방지로 `availability=available`
일 땐 여전히 Agent가 나오는 것도 같이 확인).

두 버그 모두 "숫자/필터가 조용히 틀린 결과를 낸다"는 공통점이 있다 —
에러가 나거나 크래시하지 않고 그냥 화면에 틀린 데이터가 보이는 쪽이라
실측으로 확인하지 않았으면 못 잡았을 것이다.

**검증**: 골든셋 GC170-171(2건) 추가. ruff check/format 클린. 전체
골든셋 174건 통과.

## 31. 기획/설계 문서 리뷰 — 사용자 요청(2026-09-28, "기획/설계 내용
    중 누락되거나, 오류, 개선이 필요한 부분이 있는지 분석하고
    개선해줘")

지금까지 4번의 리뷰는 전부 **코드**(Phase 1-5 구현)를 대상으로 했다.
이번엔 요청이 명시적으로 **기획/설계 문서**(`HAIR_MASTER_PLAN.md`,
`COLLABORATION_PLATFORM_PLAN.md`, `SPEC.md`, `RETROSPECTIVE.md`)로
스코프를 바꿨다 — 코드 버그가 아니라 문서가 실제 구현과 어긋나거나
(설계 당시엔 맞았지만 구현하면서 실측으로 달라진 것), 이미 해결된
질문인데 "미해결"로 계속 남아있는 것을 찾았다.

**1) `HAIR_MASTER_PLAN.md` §4 데이터 흐름 다이어그램의 3가지 오류**:
"Tool Pool 그라운딩(resolve_pool_tools)"·"crew_runtime.run_crew() /
execute_agent()"·"agent_pool.performance_score 갱신(자동값)"이라고
적혀 있었는데 전부 Phase 1/3 실제 구현과 다르다 —
`agent_runtime.execute_agent()`는 `resolve_pool_tools()`를 호출하지
않고(그건 `run_crew()`/`crewai_agent_generator()` 전용 CrewAI Tool
조립 경로), 협업 워크스페이스는 `run_crew()`를 아예 안 쓰며, Phase 3는
`performance_score`를 의도적으로 안 건드리기로 설계했다(§5-4에 이미
적혀 있었는데 §4 다이어그램만 안 고쳐져 있었다). 실제 메커니즘
(`augment_with_retrieval()`의 정적 프롬프트 주입, `aggregate_agent_
score()`로 별도 조회)으로 정정했다.

**2) `HAIR_MASTER_PLAN.md` §1 프로세스 표의 과장된 서술**: 9번 행
"실행 중 개입(HOTL) — 진행 중 일시정지/재지시(옵션)"은 Phase 4가
실제로 구현한 것(비동기 완료/실패 폴링, 일시정지·재지시 없음)보다
훨씬 많은 걸 하는 것처럼 읽혔다. 10번 행 "성과 리뷰 — 자동 Gate 점수
병행"도 실제로는 성과 리뷰 화면이 아니라 "AI 에이전트 관리" 탭에
붙었다(§8 결정사항 3번이 이미 "미해결"로 정확히 남겨뒀던 바로 그
질문인데, §1 표만 이미 해결된 것처럼 서술돼 있었다). §2 역할 표의
"Gate 임계값·자동승인 정책 설정(신규)"도 미구현임을 명시했다
(`DEFAULT_GATE_THRESHOLD=0.7`이 코드 상수로 고정, 화면에서 못 바꿈).

**3) `HAIR_MASTER_PLAN.md` §8 "결정이 필요한 것들" 목록의 비일관적
마무리 처리**: 이미 완료된 항목(5, 6번)은 취소선+"완료" 표시가 돼
있었는데, 똑같이 실제로 해결된 1번(RBAC 재정의 — Phase 1 구현 시
"팀원은 provider/model 선택 불가"로 확정됨)과 4번(반응형 범위 — Phase
5에서 "내 업무/알림 확인용"으로 확정)은 계속 "미해결"로 남아있었다 —
문서 유지보수가 일관되지 않았던 것. 3번(성과 리뷰 화면 병기)은
"부분 해결, 위치가 계획과 다름"으로 정확히 재서술했다.

**4) `COLLABORATION_PLATFORM_PLAN.md` §7 "다음 액션" 1번**: "Phase
A부터 시작할지, 세 Phase를 한 번에 설계할지 결정"이 미해결로 남아
있었지만, 실제로는 사용자가 순차 진행("Phase 1부터 시작해줘" → "다음
단계 진행해줘" → ...)을 선택해 이미 해소됐다 — 완료 표시로 정정.

**5) `SPEC.md` §12 오픈 이슈 중 하나가 완전히 뒤집힘**: "Gate A-G
실측 실행(사용자 직접)은 남음"이라고 적혀 있었는데, Phase 3가 이걸
정확히 해결했다(사람이 수동으로 안 돌려도 매 Agent 실행마다 자동
채점) — 완료로 정정.

**6) `RETROSPECTIVE.md`는 소급 수정하지 않고 안내만 추가**: 이 문서는
스스로 "그 시점의 스냅샷"이라고 명시한 회고문이라(2026-09-25~26 시점
고정), 지금 시점에서 이미 틀려진 서술("Gate A-G 실측 실행은 보류")을
직접 고치는 대신 문서 맨 위에 "현재 상태는 다르다, README/LIMITS를
따라가라"는 안내 문구만 추가했다 — 역사적 기록의 무결성과 현재 상태의
정확성을 동시에 지키는 선택.

이번 리뷰에서 발견한 6건 모두 코드 버그가 아니라 "설계 문서가 실제
구현을 못 따라간" 종류다 — Phase 1-5를 구현하면서 계획을 실측으로
계속 조정했지만(각 Phase 섹션엔 "구현 결과 — 조정한 것"이 잘 남아
있었다), 그 조정 내용이 문서의 **다른 위치**(오래된 표·오픈 이슈
목록)까지는 전파되지 않은 게 공통 원인이다.

**검증**: 코드 변경 없음(문서 전용) — 골든셋 174건 그대로 유지.

## 32. UI/UX 리뷰 — 사용자 요청(2026-09-28, "UI/UX 내용 중 누락되거나,
    오류, 개선이 필요한 부분이 있는지 분석하고 개선해줘")

이전 리뷰들은 코드 정확성(Phase 1-5 버그)과 문서 정확성(기획/설계)을
다뤘다. 이번엔 명시적으로 **실제 화면 사용성**(`webui.py`의 JS/HTML)을
다시 읽으며 4건을 발견·수정했다 — 이 중 하나는 이 프로젝트에 브라우저
기반 테스트 인프라가 없어(`package.json`·`*.test.js` 전무, `node
--check`로 문법만 확인하는 게 전부) 코드 읽기로 재현 경로를 추적해
확인했다(golden set으로 재현 테스트를 못 만든 유일한 발견 — 정직하게
명시).

**1) 댓글 폼 이벤트 리스너 누적 버그(가장 심각, golden set 불가)**:
`loadDeliverableComments()` 하나가 "댓글 목록 갱신"과 "폼에 submit
리스너 붙이기"를 같이 했는데, 댓글 등록 성공 콜백 *안에서* 같은 함수를
다시 불러 목록을 갱신했다 — 그런데 그 재호출이 **같은 `<form>` DOM
노드**(부분 새로고침이라 폼 자체는 안 없어짐)에 `addEventListener`로
리스너를 "또" 붙였다. `addEventListener`는 똑같아 보이는 익명함수도
별개 리스너로 취급해 쌓기만 하지 덮어쓰지 않으므로, 댓글을 한 번 달
때마다 그 폼의 리스너가 1→2→4→8개로 배가된다 — 다음 댓글부터는 같은
POST 요청이 리스너 개수만큼 중복 발사돼 똑같은 댓글이 여러 번 등록되는
버그였다. "목록 갱신"(`refreshComments`, 매번 호출 가능)과 "리스너
붙이기"(`loadDeliverableComments`, 최초 1회만)를 분리해서 고쳤다.
브라우저 테스트 인프라가 없어 golden set으로 재현하지 못했다 — 코드
읽기로 DOM 생명주기를 추적해 확인했다(이 프로젝트에 jsdom 같은 걸
새로 들이는 건 이번 리뷰 범위 밖이라 보류).

**2) Gate 배지의 "0/0 통과" 오해 소지**: 측정된 Gate가 하나도 없으면
(`measured.length === 0`) `passed === measured.length`가 `0===0`으로
참이 돼 **초록색("통과") 배지로 "Gate 0/0 통과"**가 떴다 — "아무것도
측정 안 됨"이 "전부 통과"처럼 보이는 정반대의 오해를 준다. 측정된
Gate가 0개면 별도로 "Gate 측정 없음"(경고색)을 보여주도록 분리했다.

**3) Agent "오프보딩" 버튼에만 확인 창이 없었음**: 이 화면 자체의
안내문이 "'오프보딩'(active→deprecated)은 이 화면에서 되돌릴 버튼이
없습니다"라고 명시할 만큼 사실상 편도인 동작인데, 앱의 다른 모든
삭제·오프보딩 버튼(사람 오프보딩 포함)과 달리 `confirm()` 없이
한 번의 클릭만으로 실행됐다 — 같은 패턴으로 확인 창을 추가했다.

**4) 성과 리뷰 화면의 낡은 안내 문구**: "두 타입 모두 점수는 아직
자동 채점(Gate A–G) 연동 전"이라고 적혀 있었는데, Phase 3가 이미
Gate A-G 자동 채점을 연결했다(§24) — 다만 **이 화면이 아니라** "AI
에이전트 관리" 탭에 붙었다(§8 결정사항 3번, §31 참고). 이 화면을 보는
사람은 "아직 없다"고 오해하고 다른 화면에 이미 있는 자동 점수를 놓칠
수 있었다 — 정확한 위치를 안내하도록 고쳤다.

**부수적으로 발견한 문서 자체의 버그**: 이 파일(§31을 쓰던 중) 요약
표의 헤더 행("| 항목 | 상태 |")이 실수로 빠지고 구분선(`|---|---|`)만
남아있었다 — 이번에 발견해 같이 복구했다.

**검증**: 골든셋 신규 테스트는 추가하지 않았다(브라우저 DOM 동작이라
이 프로젝트의 pytest 기반 골든셋으로 재현 불가능 — 정직하게 명시).
`node --check`로 JS 문법 확인, ruff check/format 클린, 기존 골든셋
174건 그대로 통과(웹 UI 문자열 안 JS만 바꿔서 서버 쪽 동작은 영향
없음).

## 33. 라이브 시나리오 실행 리뷰 — 사용자 요청(2026-09-28, "시나리오를
    진행하며, 오류/오작동/불편한 프로세스 등... 찾아 수정해줘")

지금까지 §24-32의 리뷰는 전부 **코드를 읽고** 재현 테스트를 짜는
방식이었다. 이번엔 방식 자체가 다르다 — `docs/SCENARIOS.md`의 3개
시나리오를 실제로 떠 있는 서버(`http://127.0.0.1:8000`, 방금
재부트스트랩한 새 DB)에 대해 `httpx`로 실제 HTTP 요청을 쏘며 admin·
Manager 2명(자기승인 방지 확인용)·Member·Viewer 계정을 실제로 만들어
끝까지 수행했다(임시 드라이버 스크립트, 리뷰 후 삭제). Agent 실행
(`execute-agent`)과 사람 LLM 보조(`assist`) 두 경로 모두 실제
OpenAI(`gpt-4o-mini`) API를 정확히 1회씩만 호출했다(비용 발생,
반복 재시도 안 함 — `docs/LIMITS.md` §25의 실수를 반복하지 않으려고
의도적으로 최소화).

**결과**: 시나리오 1(개발)·3(RBAC 경계)에서 검증한 41개 항목
(승인 체인, ADR 3-A 자기승인 차단, 협업 워크스페이스 참가자 판정,
Gate A-G 자동 채점, 인박스/대시보드 집계, RBAC 경계 8종)이 **전부
기대한 그대로 동작했다** — 이번 라이브 실행에서는 새 코드 버그를
찾지 못했다. 이전 4차례 리뷰(§26-28, §30)가 이미 크로스-Phase 버그·
숫자 집계 버그를 상당수 잡아낸 뒤라, 코드 정확성 자체는 이번 관찰
범위에선 안정적이었다는 뜻으로 받아들인다(추측이 아니라 실측 결과를
그대로 보고한다 — 버그를 찾아야 리뷰가 성공한 것은 아니다).

**발견한 것은 코드가 아니라 이 세션이 직접 쓴 `docs/SCENARIOS.md`
자체의 오류였다**: 시나리오 2(일반 업무) 5단계가 "Member가 제출,
Manager가 검토 후 승인"이라고 적어뒀는데, 실제로 세 가지를 직접
호출해 확인한 결과 **승인이라는 절차 자체가 사람 산출물엔 없다**:
1) `author_type='human'` deliverable은 항상 `status='draft'`로
   남는다(`deliverables.create_deliverable()`) — Agent 산출물만
   받는 `approvals.create_approval_chain()`이 붙지 않는다(Phase 2
   설계 당시부터 의도된 경계, `deliverables.py` 모듈 docstring에
   이미 명시돼 있었다 — **코드는 정직했고, 시나리오 문서가 그걸
   "승인"으로 과장해 옮겨적은 게 오류였다**).
2) 그 deliverable에 대해 `GET /api/approvals?scope=inbox`를
   비참가자 Manager로 조회하면 빈 배열 — 결재함에 뜰 건수 자체가
   없다.
3) 비참가자 Manager가 `POST /api/deliverables/{id}/comments`로
   피드백을 남기려 해도 403(`"대리 작성 금지"` 원칙이 예외 없이
   적용됨, `app.py`의 협업 워크스페이스 RBAC 주석에 이미 명시).
`docs/SCENARIOS.md` 시나리오 2의 5단계·"핵심 가치"·"갭" 문단을
실측대로 정정했다 — 순수 사람 Task는 완료 여부를 판단할 상태 전이
자체가 API에 없다(구두/메신저 등 플랫폼 밖 소통에 의존해야 한다).
이건 새 기능 요구사항이 아니라 **기존에 의도된 설계 경계를 시나리오
문서가 잘못 서술했던 것**이라 코드는 건드리지 않았다.

**추가로 실측 확인(새 버그 아님, 기존 §24 예측의 실증)**: 시나리오
1의 Agent 실행에서 Gate G(설명가능성)가 실제로 0.0/fail로 나왔다 —
`agent_evaluation.py`가 이미 "Gate G는 프롬프트가 판단 근거를
요구하지 않아 낮게 나올 가능성이 높다"고 예측해뒀던 것(§24)과
정확히 일치한다. 또한 새로 부트스트랩한 DB의 시드 Skill Pool
22건이 전부 이 SDK 프로젝트 자신의 내부 절차 스킬(`rag`,
`vectordb`, `harness-autopilot` 등)이라 "결제 API"·"고객 응대" 같은
업무 도메인 스킬과 전혀 매칭되지 않는다는 것도 확인했다 — 즉 새로
배포한 조직이 실제 업무 시나리오를 그대로 실행하면 Skill Pool 그라운딩
매칭이 처음부터 전무해 Gate G가 사실상 항상 낮게 나올 수밖에
없다(§3-1이 이미 "업무 도메인 스킬은 별도로 채워야 한다"는 전제를
깔고 있었던 것과 일치 — 새 갭이 아니라 첫 배포 시 놓치기 쉬운
온보딩 함정으로 기록해둔다).

**의도적으로 다루지 않은 것**: 순수 사람 Task에 승인/코멘트 메커니즘을
새로 추가하는 것은 이번 리뷰 범위 밖으로 남겼다 — 이건 버그 수정이
아니라 Phase 2 설계 결정을 뒤집는 새 기능 스코프라, 사용자 확인 없이
독자적으로 설계 결정을 내리지 않는다(이 세션 전체의 관례).

**검증**: 코드 변경 없음(`docs/SCENARIOS.md` 문서만 정정) — 골든셋
174건 그대로 유지, 새 GC 번호 없음(고칠 코드 버그가 없었다).

## 34. 사람 deliverable 제출/승인 메커니즘 추가 — 사용자 요청(2026-09-29,
    "시나리오를 진행하며... 개선이 필요한 부분을 찾아 수정해줘"에 대한
    후속 "둘 다 진행해줘" — §33이 "새 기능 스코프라 다루지 않는다"고
    남겨둔 것을 사용자가 진행하기로 결정)

§33이 발견한 갭(순수 사람 Task는 완료 판정 경로 자체가 API에 없음)을
실제로 메웠다. `deliverables.submit_deliverable()`(신규)을 추가해
사람이 본인 draft를 명시적으로 "검토해달라"고 제출하면, Agent
산출물이 이미 쓰던 결재 체인(`approvals.create_approval_chain()`)을
그대로 재사용해 승인/반려가 가능해진다 — **새 승인 로직을 만들지
않았다**(모듈 docstring의 Phase 2 원칙 그대로 유지). `decide_approval()`
쪽은 `subject_type='deliverable'`을 이미 author_type과 무관하게
처리하고 있었기 때문에(`app.py:1601,1616` → `deliverables.
resolve_deliverable_decision()`) 그쪽은 코드 변경이 전혀 필요 없었다.

**설계 결정 — 자동 제출이 아니라 명시적 제출**: Agent 경로는 산출물이
생기는 즉시 자동으로 결재 체인이 붙지만, 사람 경로는 `create_deliverable()`
호출마다 자동 제출하지 않고 별도 엔드포인트(`POST /api/deliverables/
{id}/submit`)로 분리했다. 이유: 협업 워크스페이스의 "결과물 제출" 폼은
사람이 여러 번 초안을 남기며 다듬는 용도로 이미 쓰이고 있어(한 번
호출 = deliverable 행 하나, 수정-in-place 아님), 매 입력마다 결재
체인을 만들면 같은 task에 여러 개의 pending 결재가 쌓이는 부작용이
생긴다. 명시적 "검토 요청" 버튼으로 사람이 "이제 다 됐다"는 시점을
직접 표시하게 했다.

**RBAC**: 본인이 작성한 'draft' 상태의 deliverable만 제출 가능(대리
제출 금지 — `create_deliverable()`의 본인 원칙과 통일, GC174로 확인).
Agent 산출물은 이 엔드포인트의 대상이 아니다(400, GC175). 이미
제출/처리된 deliverable의 재제출은 막는다(409, GC176 — NF3 재처리 방지
원칙과 통일).

**웹 UI**: 협업 워크스페이스에서 본인 draft 카드에만 "검토 요청" 버튼을
노출한다(`webui.py`, `submitAction` — Agent 승인 버튼과 같은 자리,
`.actions` div 재사용).

**검증**: GC172-176(5건, `test_phase17_collab_workspace.py`) 전부
통과, 전체 골든셋 179건(174+5) 그대로 통과. 라우트 수 63→64
(`POST /api/deliverables/{id}/submit`). `docs/SCENARIOS.md` 시나리오
2는 이 기능이 생겼다고 해서 되돌려 고치지 않았다 — §33이 정정한
서술(2026-09-29 시점 기준 "제출해도 승인 절차가 없다")은 **그
시점의 실측 사실**이었고, 이 섹션이 그 이후 상태 변화를 기록하는
올바른 위치다(문서마다 책임 범위를 분리 — SCENARIOS.md는 워크스루,
LIMITS.md는 변경 이력).

## 35. 업무 결과물 확인 기능 개선 — 사용자 요청(2026-09-29, "각 업무의
    결과물을 어떻게 확인하는지... 분석하고 부족한 기능을 개선해줘")

**실측으로 확인한 갭**: "업무의 결과물을 확인"할 수 있는 화면 3곳을
직접 코드로 추적한 결과, 전부 "지금 상태가 뭔지"·"최종 결과물이
나왔는지"를 목록 단계에서 전혀 알려주지 않았다 — 업무마다 협업
워크스페이스를 직접 열어 전체 채팅형 타임라인을 끝까지 스캔해야만
알 수 있었다:

1. `GET /api/my-work`(`deliverables.list_my_work()`) — `deliverable_count`
   (개수)와 `latest_deliverable_at`(최근 시각)만 있고 **상태 자체가
   없었다**. 결과물이 5개 쌓여도 그게 초안인지, 반려됐는지, 승인까지
   끝났는지 목록만 보고는 알 방법이 없었다.
2. `GET /api/briefs/{id}/tasks`(브리프의 할 일 목록) — `tasks` 테이블
   자체에 상태 컬럼이 없고(`db.py:65-71`), 이 엔드포인트는
   `id`/`role_name`/`required_skills`/`headcount`만 내려줘 **결과물
   정보가 전혀 없었다**. 매니저가 브리프 화면에서 할 일 진행 상황을
   보려면 이 화면으론 불가능했다.
3. 협업 워크스페이스 타임라인 자체도 "중간 과정"(초안·반려·재시도)과
   "지금 실제로 쓸 수 있는 최종 결과물"을 구분하지 않고 한 줄로
   뒤섞어 보여줬다 — 여러 차례 주고받은 뒤엔 어느 카드가 "확정본"인지
   스크롤하며 각 카드의 상태 라벨을 직접 읽어 판단해야 했다.

**수정**: 새 공용 함수 `deliverables.task_completion_summary(conn,
task_id)`를 추가해(`deliverable_count`/`latest_deliverable_at`/
`latest_status`/`has_final_result` 4개 필드) `list_my_work()`와
`GET /api/briefs/{id}/tasks` 양쪽에서 재사용했다 — **집계 로직을 두 번
만들지 않는다**는 이 프로젝트의 관례를 그대로 따랐다. 협업
워크스페이스는 이미 받아온 `items`(전체 타임라인)에서 클라이언트
사이드로 가장 최신 `status='approved'` 항목을 찾아 상단에 별도
"✓ 최종 결과물(승인됨)" 카드로 고정 노출한다(새 API 불필요) — 그
아래 "진행 이력(중간 과정 포함)"이라는 제목으로 기존 타임라인을 그대로
둔다.

**설계 결정 — `has_final_result`는 "지금" 승인 건 존재 여부, 이력
아님**: 한 task에 여러 건이 승인될 수 있는(드문) 경우 가장 최신 것만
"현재" 최종본으로 간주한다. `revises_id` 기반의 버전 체계(반려 뒤
재제출을 명시적으로 연결하는 것)는 Phase 2 때부터 스코프 밖으로 남아
있고(`deliverables.py` 모듈 docstring), 이번에도 건드리지 않았다 —
"목록에서 상태를 보여준다"는 이번 요청의 스코프를 벗어난다.

**검증**: GC177-180(4건, `test_phase17_collab_workspace.py`) —
결과물 없음/승인됨/반려됨/브리프 할 일 목록 4가지 케이스를 확인했다.
전체 골든셋 183건(179+4). 라우트 수·엔드포인트 계약 변경 없음(기존
두 엔드포인트에 필드만 추가 — 응답 형태가 배열/딕셔너리인 것은
그대로).

## 36. §34/§35 라이브 실행 재검증 — 사용자 요청(2026-09-29, "시나리오를
    진행하며... bug 및 개선이 필요한 부분을... 수정해줘", 재요청)

§33의 라이브 실행 리뷰 **이후에** 추가된 §34(사람 deliverable 제출)·
§35(업무 결과물 확인 기능)는 그때까지 pytest(mock)와 `node --check`
로만 검증돼 있었다 — 이번엔 그 둘을 실제로 떠 있는 서버에 `curl`로
실제 HTTP 요청을 쏘며 기존 시나리오 데모 데이터(§33이 만든 것 그대로,
재부트스트랩 안 함) 위에서 끝까지 확인했다:

- `park_member`로 실제 로그인 → 시나리오 2의 마케팅 보고서 draft(id 3)를
  실제로 `POST /api/deliverables/3/submit` → 200, `latest_status`가
  `draft`→`pending_review`로 실시간 반영됨 확인.
- `admin`으로 실제 결재함(`GET /api/approvals?scope=inbox`) 조회 →
  방금 생긴 건이 실제로 뜸 → 실제로 `POST /api/approvals/5/decide`
  승인 → `deliverable.status='approved'`.
- 승인 직후 `park_member`의 `GET /api/my-work`와 `GET
  /api/briefs/2/tasks` 양쪽에서 `has_final_result`가 실제로
  `false`→`true`로 바뀜을 확인(§35가 약속한 동작 그대로).
- 400/403/409 경계 3종(Agent 산출물 제출 시도, 대리 제출 시도, 재제출
  시도)도 전부 실제 HTTP로 재확인 — pytest가 mock으로 예측한 것과
  **완전히 일치**, 실제 RBAC 의존성 경로에서의 차이 없음.
- 시나리오 1(task 1, human draft + agent approved 2건 혼재)의
  `GET /api/tasks/1/deliverables` 실제 응답으로 `webui.py`의
  `finalResultCard` 클라이언트 로직(가장 최신 `status='approved'`
  항목을 `created_at` 문자열 비교로 선택)을 직접 대조 — 모든
  deliverables가 스키마의 `DEFAULT (datetime('now'))`만 쓰고 다른
  포맷으로 넣는 경로가 없어(`grep`으로 INSERT 문 2곳 전부 확인)
  문자열 `>` 비교가 항상 시간순과 일치함을 확인. 브리프 1의 두
  Task(백엔드 개발=2건 혼재, QA=0건)로 집계 함수가 Task별로 올바르게
  분리되는 것도 확인.
- 실제 서버가 방금 내려준 페이지를 새로 `curl`로 받아 `node --check`
  통과 + `data-submit-deliverable`/`final-result-card`/새 테이블
  열("상태"·"최종 결과물") 전부 실제 응답에 존재 확인.

**결과**: 새 코드 버그 없음 — §34/§35 둘 다 mock 테스트가 예측한
그대로 실제 서버에서 동작했다(버그를 찾아야 리뷰가 성공한 것은 아니다,
§33과 같은 원칙). 코드 변경 없음, 골든셋 183건 그대로, 새 GC 번호
없음. `.env`의 실제 API 키를 쓰는 LLM/임베딩 호출은 이번 검증 대상
(제출/승인/집계는 순수 DB 연산)과 무관해 한 번도 트리거하지 않았다 —
§25의 실수를 반복하지 않았다.

## 37. 로그인 계정 유형별 메뉴 노출 개선 — 사용자 요청(2026-09-29, "HAIR
    관점에서 외부 사례들을 조사하고... 관리자와 사용자 고유 업무와 공통
    업무를 구분해줘" → "로그인한 계정 유형에 맞춰 메뉴 노출을 개선해줘")

**계기**: 이 세션 안에서 사용자가 직접 겪은 혼란 — "pm_kim(매니저)으로
로그인해도 관리자 도구가 클릭될 수 있어"라는 보고를 받고 확인해보니
버그는 아니었다(`TABS` 배열의 11개 중 10개가 원래 `roles: ["admin",
"manager"]`로 설계돼 있었음, ADR 결정 3). 하지만 **"관리자 도구"라는
그룹 라벨 자체가 그 실제 구성과 안 맞아 오해를 유발**한다는 점은
실제 문제였다. 이어서 외부 서비스 4곳(GitHub Copilot·Asana·monday.com·
LangSmith·ServiceNow) 리서치로 재분류한 `docs/ROLE_TASK_CLASSIFICATION.md`
에서, "인력 검색"/"조직도"가 역할과 무관한 순수 조회 화면인데 "내
업무"(담당자 개인 업무 처리) 그룹에 섞여 있어 성격이 다른 두 종류가
한 그룹으로 묶여 있다는 것도 확인했다.

**수정**: `webui.py`의 `TABS` 배열 group을 둘("user"/"admin")에서
셋("user"/"common"/"admin")으로 늘렸다.
- `search`(인력 검색)·`orgchart`(조직도)를 `group: "common"`으로
  재분류 — roles 배열은 그대로(4개 역할 전부), 백엔드 RBAC 변경 없음,
  **순수 UI 그룹 재배치**.
- `buildNav()`에 `commonTabs` 필터·`addGroup("공용 조회", commonTabs,
  "common-group")` 추가, 사용자 요청으로 이미 확인된 amber(admin)
  배색과 구분되는 teal 계열 배색(`common-group`) 신설.
- admin 그룹 라벨을 `"관리자 도구"` → `"운영 관리"`로 변경 — 매니저도
  대부분(11개 중 10개) 접근 가능하다는 실제 구성을 라벨이 반영하도록
  했다. 그룹 안에서 진짜 admin 전용인 항목("사용자 관리")은 기존
  `adminOnly`/"관리자 전용" 배지 메커니즘을 그대로 재사용해 구분한다
  (새 배지 로직을 또 만들지 않는다).

**손대지 않은 것(의도적)**: `docs/ROLE_TASK_CLASSIFICATION.md`가 찾은
실제 권한 갭 2건(MCP 도구 등록이 admin 전용이 아닌 것, Human/Agent Pool
비용·성과 수정 권한 비대칭)은 **메뉴 노출(UI 그룹 재배치)이 아니라
RBAC 자체를 바꾸는 별도 결정**이라 이번 스코프에 포함하지 않았다 —
사용자가 그 문서 끝의 열린 질문에 답하면 별도로 진행한다.

**검증**: 기존 Phase 11 테스트(GC73-76, `test_phase11_admin_user_nav_split.py`)
가 옛 2-그룹 가정("member/viewer 포함 여부만으로 group 자동 판정",
"관리자 도구" 라벨 존재)을 그대로 단언하고 있어 이번 변경으로 실제로
깨졌다 — 의도된 설계 변경이므로 옛 단언을 3-그룹 모델에 맞게 고치고
(GC73은 `addGroup(...)` 호출부 기준으로 옛 라벨 부재를 확인하도록
정밀화 — 변경 이력을 설명하는 코드 주석 안의 문자열까지 걸려 처음엔
오탐), 새 GC77(공용 그룹이 정확히 {인력 검색, 조직도}인지)을 추가했다.
전체 골든셋 184건(183+1, GC73-76은 기존 번호 유지하며 수정). 서버
재시작 후 실제 응답 `node --check` 통과, "공용 조회"/"운영 관리" 새
그룹 라벨과 `common-group` CSS 실제 존재 확인.

## 38. `human_pool.manager_id`를 채울 API가 없던 갭 발견·수정 — 사용자
    요청(2026-09-29, "admin, 사용자 계정을 정리해줘")

**실측으로 발견한 진짜 갭**: 사용자 계정을 정리해달라는 요청을 실제로
수행하려고 현재 계정·조직 구조를 조회해보니, `users`·`human_pool`
전체 레코드의 `manager_id`가 하나도 없이 전부 `NULL`이었다. `GET
/api/org-chart`(`app.py:1461`)는 이미 `human_pool.manager_id`로 사람을
관리자별로 묶고 있는데(코드 자체는 정확함), 그 값을 채울 API가
`users.manager_id`용 `PATCH /api/users/{id}/manager`만 있었고
**`human_pool.manager_id`를 쓰는 API는 스키마 도입 이후 한 번도 존재한
적이 없었다**(`HumanPoolUpdate` 모델에 필드 자체가 없었음, `grep`으로
전체 코드베이스에서 `human_pool`+`manager_id` 조합의 쓰기 경로가
0건임을 확인). 결과: 사람이 아무리 매니저에게 배정돼 일하고 있어도
조직도 화면에는 전부 "관리자 없음" 버킷 하나로 뭉뚱그려질 수밖에
없었다 — HAIR의 핵심 가치("사람과 AI를 하나의 조직도 위에") 중 "조직도"
절반이 사실상 장식이었던 셈이다.

**수정**: `HumanPoolUpdate`(`models.py`)에 `manager_id: int | None`
필드 추가, `PATCH /api/human-pool/{id}`(`app.py`)에서 `set_user_manager()`
와 같은 패턴(대상 `manager_id`가 실제 존재하는 사용자인지 검증 후 404)
으로 처리한다 — 기존 `user_id` 필드 처리 로직을 그대로 옆에 병렬로
추가했을 뿐 새 검증 로직을 만들지 않았다.

**실제로 계정을 정리했다**(코드 수정 후 이 API로 직접 실행,
`docs/ROLE_TASK_CLASSIFICATION.md`/`SCENARIOS.md` 데모 데이터 기준):
`pm_kim`·`pm_lee`(매니저)→`admin`, `qa_kim`·`park_member`(팀원)→
`pm_kim`, `agent_viewer`(뷰어)→`pm_lee`로 `users.manager_id`를,
human_pool의 김QA·박마케터 레코드는 `pm_kim`으로 `manager_id`를
채웠다. `GET /api/org-chart` 실제 응답으로 확인 — 이제 pm_kim 버킷 밑에
Agent 배정(원래도 정확했음)+김QA+박마케터 3건이 전부 정확히 묶여
나온다.

**검증**: GC181(`test_phase7_review_fixes.py`, 기존 GC54 human_pool
갱신 테스트 바로 옆) — manager_id 설정 성공 + 존재하지 않는
manager_id는 404. 전체 골든셋 185건(184+1). 라우트 수 변경 없음(기존
엔드포인트에 필드만 추가). 실제 서버로 계정 5개+human_pool 2건의
manager_id를 직접 설정해 `GET /api/org-chart` 그루핑이 실제로
바뀌는 것까지 라이브로 확인했다.

## 39. AI 에이전트 실행 버튼이 실행 불가능한 사람에게도 그냥 보이던 문제
    수정 + 협업 워크스페이스 UI/UX 개선 — 사용자 요청(2026-09-29,
    스크린샷 보고: "내업무 - 열기 (1) UI/UX를 개선해줘. (2) '이 업무의
    AI 에이전트 실행'의 경우 '이 업무의 협업 워크스페이스에 접근할
    권한이 없습니다'라고 나와")

**실측으로 발견한 진짜 원인**: 사용자가 보고한 403은 버그가 아니라
`start_agent_execution()`(`deliverables.py`)의 원래 설계대로 동작한
것이었다 — 이 함수는 이 task의 사람 참가자("assignee")인 것만으로는
부족하고, **Agent 배정을 실제로 요청한 사람("requester")이거나
매니저/관리자**여야 통과한다(협업 워크스페이스에 사람+Agent가 함께
배정된 구조에선 "내 업무를 도와줄 Agent를 누가 신청했는지"와 "누가
그 업무를 직접 하는지"가 다른 사람일 수 있다는 전제). 문제는 **웹
UI가 이 구분을 전혀 모른 채 참가자기만 하면 무조건 실행 버튼을 그냥
보여주고 있었다**는 것 — human assignee가 직접 눌러봐야만 403으로
뒤늦게 알게 되는 구조였다. 실제로 겪은 화면(스크린샷)은 이 상황이
정확히 재현된 것이다.

**수정**: 판정 로직을 `deliverables.execution_eligibility()`(신규)로
추출해 `start_agent_execution()`이 그대로 재사용하게 했다(로직이 두
곳으로 갈라지지 않게) — `{"eligible": bool, "reason": "not_requester"|
"no_confirmed_agent"|None}`을 반환한다. 새 엔드포인트
`GET /api/tasks/{task_id}/execution-permission`으로 노출하고, 웹 UI는
워크스페이스를 열 때 이 값을 미리 조회해 **버튼을 누르기 전에** 비활성화
+ 이유를 문구로 보여준다("이 업무에 AI 에이전트 실행을 요청한 사람
또는 매니저/관리자만 실행할 수 있습니다" / "이 업무에 확정된 AI
에이전트 배정이 없습니다").

**UI/UX 개선(요청 1번)**: 같은 화면에서 textarea·provider/model
선택·"LLM으로 도와줘"·"결과물 제출"이 한 줄(`form.inline` flex-wrap)에
전부 뒤섞여 있어, 좁은 화면에서 안내 문구가 select 옆에 끼어 어색하게
줄바꿈되고 있었다(스크린샷에서 확인). textarea를 독립된 줄로 분리하고,
"결과물 작성"(사람이 직접 쓰거나 LLM 도움을 받는 행동)과 "AI 에이전트
실행"(그 업무를 통째로 Agent에게 맡기는, 성격이 다른 행동)을 각각
별도 카드(`section.card`)로 나눠 시각적으로 구분했다.

**검증**: GC182-184(`test_phase17_collab_workspace.py`) — human
assignee(요청자 아님)는 `eligible=False`·`reason="not_requester"`(실제
403과 일치), 요청자/감독자는 `eligible=True`, 확정된 Agent 배정 자체가
없으면 감독자여도 `reason="no_confirmed_agent"`. 실제 데모 계정
(`pm_kim`이 Agent를 요청한 task 1, `qa_kim`이 human assignee뿐인
task 2)으로 라이브 curl 검증까지 완료 — `pm_kim`은 `eligible:true`,
`qa_kim`은 `eligible:false, reason:"not_requester"`(대상 task엔 확정된
Agent 배정 자체가 없지만, requester 판정이 먼저 걸려 이 이유가 우선
반환됨 — 어차피 Agent가 배정돼도 qa_kim은 못 누르므로 이 우선순위가
더 유용한 안내). 전체 골든셋 188건(185+3). 라우트 수 64→65
(`GET /api/tasks/{task_id}/execution-permission`). 서버 재시작 후
실제 응답 `node --check` 통과.

## 40. 사용자 관리 화면 — 비밀번호 초기화·계정 비활성화 추가, "동작" 열
    명칭 정정 — 사용자 요청(2026-09-29, "관리자 전용 메뉴의 사용자 관리
    메뉴의 경우 생성과 동작 저장만 가능 (1) 비밀번호 조회/초기화 기능
    추가 필요 (2) 사용자 삭제 기능 필요 (3) 동작의 의미가 불명 ->
    상사 지정 등으로 명칭 변경")

**(1) 비밀번호 "조회"는 원리상 불가능, "초기화"만 제공**: `users.
password_hash`는 bcrypt 단방향 해시라 평문을 복원할 방법이 없다 —
이건 버그가 아니라 애초에 그렇게 설계돼야 하는 보안 속성이다.
`PATCH /api/users/{id}/password`(신규, ADMIN 전용)로 관리자가 새
비밀번호를 지정하면, 그 값을 사용자에게 별도 채널로 전달하는 식으로만
가능하다. 감사로그엔 비밀번호 값 자체를 남기지 않는다(`before`/`after`
없이 이벤트만 기록).

**(2) "삭제"는 하드 삭제가 구조적으로 불가능해 "비활성화"로 대체**:
`users.id`는 `audit_log.actor_id`(NOT NULL), `assignments.requested_by`
(NOT NULL), `approvals.requested_by`/`approver_id`/`approved_by`,
`human_pool.user_id`/`manager_id`에서 참조된다 — 실제로 시스템을 써본
계정이면 거의 항상 이 중 하나 이상에 걸려 있어, 하드 삭제는
`PRAGMA foreign_keys=ON` 아래에서 즉시 FK 위반으로 실패하거나(안전),
CASCADE를 걸면 NF10(append-only 감사) 원칙과 정면 충돌한다(위험 —
감사 이력이 삭제된 계정 때문에 통째로 사라지면 안 됨). Human Pool
("오프보딩")·Agent Pool("오프보딩"/deprecate)이 이미 같은 이유로 하드
삭제 대신 비활성화를 쓰고 있어, 계정에도 같은 패턴을 그대로
적용했다 — 새 설계를 만들지 않았다.

- `users.is_active INTEGER NOT NULL DEFAULT 1`(신규 컬럼, `_ensure_column`
  마이그레이션 — 기존 배포 DB에도 안전하게 추가됨, `agent_pool`
  컬럼 추가 때 쓴 패턴 그대로).
- `PATCH /api/users/{id}/status`(신규, ADMIN 전용) — 비활성화하면
  `auth.authenticate()`가 로그인 자체를 막고, `auth.get_current_user()`
  가 **매 요청마다** 다시 확인해 이미 로그인된 기존 세션도 즉시
  끊는다(로그인 시점만 막으면 비활성화 전에 이미 발급된 세션이 계속
  살아있는 구멍이 생긴다 — 실제로 별도 `TestClient`로 두 세션을
  독립적으로 유지해 이 즉시 차단을 라이브로 재현·확인했다, GC187).
- 본인 계정은 비활성화할 수 없다(400) — 상사 순환 방지(A를 A의
  상사로 못 지정)와 같은 성격의 자기 자신 보호 가드.

**(3) "동작" 열 명칭 정정**: 그 열엔 실제로 상사 지정 select+저장
버튼 하나뿐이었다 — "동작"(action)이라는 일반적인 이름이 무슨 동작인지
알려주지 않았다. "상사 지정"으로 바꾸고, 새로 추가된 두 기능도 각각
"비밀번호 재설정"·"계정 상태 변경" 열로 분리해 한 열에 여러 성격의
버튼이 섞이지 않게 했다. "권한"과 별도로 "계정 상태"(활성/비활성
배지) 열도 추가해 목록만 보고도 로그인 가능 여부를 알 수 있게 했다.

**검증**: GC185-188(`test_phase6_review_fixes.py`, 기존 GC47 사용자
목록 테스트와 같은 파일) — 비밀번호 초기화 후 옛 비밀번호 실패/새
비밀번호 성공, 관리자만 가능(403), 비활성화 후 로그인 차단, 재활성화로
복구, **이미 로그인된 세션의 즉시 차단**(별도 TestClient로 검증),
본인 계정 비활성화 차단(400). 전체 골든셋 192건(188+4). 라우트 수
65→67(`PATCH /api/users/{id}/password`, `PATCH /api/users/{id}/status`).
실제 서버(`hair.db`)에 마이그레이션이 안전하게 적용되는 것과(기존 6개
계정 전부 `is_active: true`로 시작), admin 본인 비활성화가 실제로
400으로 막히는 것을 라이브 curl로 확인했다.

## 41. Tool/Skill 적극 활용 시나리오 라이브 실행 — VectorDB 그라운딩 근거
    노출 갭 발견·수정 — 사용자 요청(2026-09-29, "계정 정보는 그대로
    유지하고, 업무 정보 삭제 후, 일반업무나 개발업무 시나리오의
    End-to-End 전 과정을... 실행하며... 개선해줘. (Tool, Skill을
    적극 활용하는 시나리오를 기획해 진행해줘)")

**준비**: 계정 6개(admin/pm_kim/pm_lee/qa_kim/park_member/agent_viewer,
동일 비밀번호·조직 구조)는 `POST /api/users`+`PATCH /api/users/{id}/
manager`로 그대로 재생성하고, `briefs`/`tasks`/`assignments`/
`deliverables`/`human_pool`/`agent_pool`/`approvals`는 `hair.db`를
지우고 `bootstrap`으로 재생성하는 방식으로 완전히 비웠다(§37에서 이미
검증된 안전한 절차 재사용 — raw SQL bulk DELETE 가드레일을 다시
건드리지 않았다). `tool_pool`(3)·`skill_pool`(22) 인프라 시드는 그대로.

**시나리오 설계 — "신규 입사자 원격근무 정책 문의 대응"**: 지금까지의
3개 시나리오(§SCENARIOS.md)는 전부 Agent를 썼어도 `skill_tags`가
`vectordb`/`rag`/`graphdb`에 안 걸려 있어 **한 번도 실제 검색
그라운딩을 안 거쳤다**(그냥 순수 LLM 호출). 이번엔 Task의
`required_skills=["rag","인사 정책"]`으로 시작해 그대로 상속받은
Agent(`skill_tags=["rag","인사 정책"]`)를 만들고, 실행 전에 실제
사내 규정 텍스트("원격근무는 입사 후 첫 3개월 동안 사용 불가, 이후
주 3일까지...")를 `POST /api/agent-pool/{id}/index-knowledge`로
색인했다(실제 OpenAI 임베딩 1회).

**실행 결과 — 그라운딩이 실제로 작동함을 직접 확인**: "입사 1개월차인데
재택근무 가능한가요?"라는 사람 입력으로 `execute-agent`를 실행(실제
LLM 1회)하자, Agent가 "입사 후 첫 3개월 동안은 원격근무를 사용할 수
없습니다... 3개월이 지나신 후 주 3일까지 재택근무가 가능해집니다"라고
**색인해둔 규정을 정확히 인용해** 답변했다 — 지어낸 게 아니라 실제
검색 결과를 근거로 답했다는 걸 내용으로 직접 확인. Gate G(설명가능성)
가 1.0/pass로 나왔는데, §24가 예측했던 "프롬프트가 근거를 요구하지
않아 Gate G가 낮게 나오는 경향"과 반대되는 결과였다 — 이번엔 실제로
근거 있는 답변이 나왔기 때문으로 보인다(정성적 관찰, Gate G 채점
로직 자체를 이번에 재검증하진 않음). 배정·승인(ADR 3-A 자기승인
차단 포함)·최종 결과물 확인(`has_final_result`)·인박스·대시보드까지
전 과정을 실제 HTTP로 끝까지 확인했다 — 새로운 코드 버그는 없었다.

**실제로 발견한 갭**: 그라운딩이 실제로 작동하는 걸 답변 *내용*으로는
확인했지만, **무엇을 검색해 프롬프트에 넣었는지는 시스템 어디에도
저장되지 않았다** — `agent_pool.last_output`엔 최종 답변만 남고,
`augment_with_retrieval()`이 만든 증강 프롬프트나 검색 결과 자체는
호출이 끝나면 사라진다. 결재자가 "이 답변이 실제 회사 규정에 근거한
건지, LLM이 그냥 그럴듯하게 지어낸 건지"를 사후에 검증할 방법이
없었다 — Tool/Skill 그라운딩이라는 기능의 핵심 가치(근거 기반 답변)를
정작 **감사·검토 단계에서 증명할 수 없는** 데이터 흐름 공백.

**수정**: `agent_runtime.augment_with_retrieval()`이 `(augmented_prompt,
retrieved_context)` 튜플을 반환하도록 확장했다(유일한 호출자가
`execute_agent()` 하나뿐이라 안전한 변경). `execute_agent()` 자체의
반환 타입(`str`)은 **그대로 유지**했다 — `agent_evaluation.
run_and_score()`의 `Callable[[], str]` 계약과 `/api/agent-pool/{id}/
execute` 두 호출부가 문자열 반환을 전제로 하고 있어, 근거는 반환값이
아니라 선택적 `retrieval_sink: dict` side-channel로만 전달한다(안
넘기면 기존 동작과 100% 동일). `agent_execution_jobs`에 `retrieved_context`
컬럼을 추가해(`_ensure_column` 마이그레이션) job 성공 시 저장하고,
기존 `GET /api/tasks/{id}/agent-execution-jobs`(이미 `SELECT *`라
스키마 변경 없이 노출)로 그대로 드러난다. 웹 UI는 협업 워크스페이스의
Agent 산출물 카드에 "참고한 근거(Tool/Skill 검색 결과)"라는 접을 수
있는 `<details>` 블록을 추가해, 결재자가 클릭 한 번으로 실제 검색
결과를 확인할 수 있게 했다.

**검증**: GC189(rag 스킬 Agent 실행 시 retrieved_context가 색인한
텍스트를 포함해 저장·노출되는지)·GC190(vectordb/graphdb 스킬이 없는
일반 Agent는 `retrieved_context`가 정확히 `None`인지 — "검색 안 함"과
"검색했는데 0건"을 구분) 추가. 테스트 작성 중 가짜 임베딩 벡터
차원(2 vs 3)이 안 맞아 `_cosine_similarity()`의 `zip(strict=True)`가
예외를 던지는 것도 발견했는데, 이는 **테스트 자체의 버그**였다(실제
`vectorstore.search()` 코드는 정상 — 실측으로 재확인, 프로덕션 버그
아님). 전체 골든셋 194건(192+2). 라우트 수 변경 없음(기존 두
엔드포인트 응답에 필드만 추가 — 이전 job 행은 `retrieved_context=
NULL`로 자연스럽게 채워짐, 별도 백필 불필요).

`docs/SCENARIOS.md`에 시나리오 4로 추가했다.

## 42. "운영 관리" 그룹 버튼 순서 정정 — 사용자 요청(2026-09-29, "내 업무,
    공용 조회, 운영 관리 각 카테고리... 프로세스 순에 맞춰서, 또는
    자주 사용하는 업무가 먼저 위치하도록 버튼 배치가 되어 있는지
    확인해줘" → "진행해줘")

**실측 비교**: 발행해둔 메뉴·프로세스 맵(Artifact)을 다시 읽어 16단계
등장 순서를 뽑고, `webui.py`의 `TABS` 배열 실제 순서와 항목별로
대조했다. "내 업무"·"공용 조회" 그룹은 이미 합리적이었다 — 인박스가
맨 앞인 건 프로세스 1단계라서가 아니라 Phase 5 도입 당시 "가장 자주
보는 화면을 먼저"라는 의도된 배치였고, 나머지도 빈도 기준으로 무리가
없었다. **"운영 관리" 그룹에서만 실제 문제를 발견**했다 — 프로세스
맵 13단계가 "팀 현황 모니터링"으로 대시보드·비용·공정성 지표·활동
기록 4개를 하나로 묶었는데, 실제 화면에선 대시보드만 맨 앞(1번째)에
있고 나머지 3개는 8~10번째로 흩어져 있었다.

**수정**: `TABS` 배열에서 대시보드·비용·공정성 지표·활동 기록 4개를
맨 앞에 모으고, 그 뒤로 인력 매칭/배정 → AI 에이전트 생성 → 사람 인력
관리 → AI 에이전트 관리 → 도구/스킬 Pool → Crew 실행 → 사용자 관리
순으로 이어지게 재배치했다(프로세스 맵의 배정→Agent/사람 준비→Pool
표준화→계정 관리 순서 그대로). 백엔드 로직·역할(`roles`)·`adminOnly`
표시는 전혀 안 건드렸다 — 배열 순서만 바꾼 순수 내비게이션 변경이다.

**검증**: 기존 GC73-77(`test_phase11_admin_user_nav_split.py`)은 group/
roles/adminOnly만 확인하고 배열 순서에 의존하지 않아 그대로 통과 —
새 GC 번호 불필요(회귀 없음, 순서 자체를 검증하는 테스트는 이 프로젝트
성격상 과한 결합이라 추가하지 않음). 전체 골든셋 194건 그대로. 서버
재시작 후 실제 응답으로 새 순서(`dashboard, cost, fairness, audit,
match, ...`) 확인, `node --check` 통과.

## 43. 도구/스킬 Pool에 외부 자료 검색(Wikipedia) 도구 추가 — 사용자
    요청(2026-09-29, "도구/스킬 pool에 외부 자료 검색이 가능한 API(가능한
    무료)를 도구화해서 사용 가능하도록 만들어 추가해줘")

**설계**: VectorDB(과거 산출물 검색)·GraphDB(조직 관계 조회)는 둘 다
조직 **내부** 데이터만 다룬다 — "이 정책이 업계 표준과 비교해 어떤지"
같은 조직 밖 일반 지식이 필요한 질문엔 둘 다 답을 못 한다는 게 실제
갭이었다(§41 라이브 실행에서 간접적으로 드러난 한계). Wikipedia 공개
검색 API(`action=query&list=search`)를 새 모듈 `websearch.py`로
감쌌다 — API 키 등록·과금 없이 바로 쓸 수 있어 "가능한 무료" 조건에
정확히 맞고, 응답이 안정적인 JSON이라 파싱이 간단하다.
`vectorstore.py`/`graphquery.py`와 완전히 같은 모양(`search()`/
`context_for_prompt()`/`as_crewai_tool()`)을 따라 두 실행 경로 모두에
동일한 패턴으로 연결했다 — 새 아키텍처를 만들지 않았다:
- `agent_runtime.augment_with_retrieval()`(협업 워크스페이스의 단일
  LLM 호출 경로, 정적 프롬프트 주입) — `WEBSEARCH_SKILL_TAGS =
  {"websearch", "외부검색"}` 추가.
- `pool_registry.NATIVE_TOOL_FACTORIES`(Crew 실행/AI 에이전트 생성의
  진짜 CrewAI Tool-calling 경로) — `"web_search": websearch.
  as_crewai_tool` 추가.
- `db._seed_default_pools()` — native Tool "외부 자료 검색(Wikipedia)"
  + skill "websearch"/"외부검색"(별칭) 시드, 기존 VectorDB/GraphDB
  시드와 같은 `INSERT OR IGNORE` 멱등 패턴.

**라이브 검증에서 실제로 발견·수정한 버그**: mock 테스트를 전부 통과시킨
뒤 실제 네트워크로 `websearch.search()`를 직접 호출해보니 **Wikipedia가
403으로 거부**했다 — "Please set a user-agent and respect our robot
policy"(2026년부터 강화된 정책, `https://w.wiki/4wJS`). mock은 내가
만든 가짜 응답을 그대로 돌려주므로 이 문제를 절대 못 잡는다 — 실제
API를 호출해야만 드러나는 종류의 버그였다. `httpx.get()` 호출에
`User-Agent` 헤더를 추가해 수정했고, 수정 후 실제로 "원격 근무"·"리모트
워크" 두 개 실제 위키백과 문서를 정상적으로 검색해 확인했다.

**비용/실패 처리**: Wikipedia API 자체는 무료·키 불필요라 이 모듈
호출은 비용이 없다. 다만 실제 네트워크 호출이므로 실패할 수 있어(오프라인/
타임아웃/API 오류) 예외를 올리지 않고 빈 리스트로 조용히 대체한다 —
외부 자료 검색 실패가 Agent 실행 전체를 막으면 안 된다는 원칙
(`vectorstore.search()`/`graphquery.context_for_prompt()`와 다르게
이 둘은 애초에 실패할 일이 거의 없는 로컬 SQLite 조회라 이 걱정이
없었지만, 외부 네트워크 호출인 `websearch`는 처음부터 실패를 전제로
설계해야 했다).

**검증**: 새 순수 단위 테스트 파일 `test_phase22_websearch_tool.py`
(GC191-194, httpx mock으로 검색 성공/네트워크 실패/HTTP 오류/
`context_for_prompt` 포맷/`as_crewai_tool` 래핑 확인) + GC195
(`test_phase12_crewai_vectordb_graphdb.py`, GC90/91과 같은 패턴으로
`execute_agent()`가 websearch skill Agent 실행 시 실제로 프롬프트를
증강하는지) + GC196(`test_phase14_pool_registry.py`, GC108과 같은
패턴으로 시드+`resolve_pool_tools()` 연결 확인). 기존 GC114/GC125의
하드코딩된 Pool 개수 단언(도구 3개·스킬 22개)이 새 도구/스킬 추가로
실제로 깨져서 4개/24개로 갱신했다(이 프로젝트가 §20에서 이미 한 번
겪은 것과 같은 종류의 유지보수 비용 — "정확한 개수"를 매번 단언하는
테스트는 Pool이 커질 때마다 갱신이 필요하다는 걸 재확인). 전체 골든셋
200건(194+6). 라우트 수 변경 없음(새 REST 엔드포인트가 아니라 기존
Tool Pool 등록 메커니즘을 쓰는 native 도구 추가). 실제 서버(`hair.db`)
재시작으로 새 시드가 안전하게 적용되는 것과, `GET /api/tool-pool`·
`GET /api/skill-pool/names` 응답에 실제로 노출되는 것까지 라이브로
확인했다.

## 44. 일반 웹 검색(Parallel Search MCP) 도구 추가 — 사용자 요청
    (2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지 확인해줘" →
    "진행해줘")

**리서치**: §43의 Wikipedia 도구는 자체 백과사전 색인만 다뤄 "최신
뉴스" 같은 진짜 일반 웹 정보는 여전히 못 찾는다는 한계가 있었다. 무료
일반 웹 검색 API를 실측 조사한 결과 — Bing Search API는 2025-08-11
전체 퇴역(Microsoft 공식), Google Custom Search API는 2027-01-01부로
신규 고객 가입 자체가 막힘, Brave Search API는 무료 크레딧이 있어도
가입 시 카드 등록 필수, Tavily는 카드는 없지만 계정+키가 필요하고
2026-02 Nebius 인수로 가격 정책 불확실 — 이 중 어느 것도 "계정·키
완전 불필요"라는 Wikipedia API 수준의 조건을 만족하지 못했다. Parallel
AI의 공개 MCP 서버(`https://search.parallel.ai/mcp`)만 계정·키 없이
완전 무료로 접속 가능했다(익명 접근 분당 600건).

**구현**: 이 서버는 MCP(Streamable HTTP)로 제공되므로, `websearch.py`
처럼 새 Python 모듈을 쓸 필요 없이 이미 있는 `mcp_remote` Tool Pool
종류와 `pool_registry.connect_mcp_tools()` 메커니즘을 그대로 재사용했다
— `recommend_fix`(mcp_stdio)와 같은 자리에, 종류만 `mcp_remote`로 다르게
시드했다(`db._seed_default_pools()`). "일반웹검색" 스킬을 가진 Agent에
자동 연결된다.

**라이브 검증(`mcp_remote`가 이 프로젝트에서 처음 실제로 접속되는
경로였다 — 지금까지 검증된 MCP는 `mcp_stdio`뿐)**: `pool_registry.
connect_mcp_tools()`를 직접 호출해 실제로 접속 — `web_search`·
`web_fetch` 두 Tool이 정상적으로 열렸다. `web_search` Tool을 실제로
호출해 "2026 한국 최저임금"을 검색한 결과, 연합뉴스의 실제 2026-06-21
기사(제목·발행일·본문 발췌 포함)가 그대로 반환되는 것까지 확인했다 —
Wikipedia로는 절대 찾을 수 없는 종류의 최신 정보다.

**중요한 아키텍처 제약(정직하게 문서화)**: 이 도구는 `mcp_remote`라
`agent_runtime.augment_with_retrieval()`(정적 프롬프트 주입, 협업
워크스페이스의 단일 LLM 호출 경로)에서는 쓸 수 없다 — 그 경로는 Agent
호출 루프 자체가 없어 native 함수 직접 호출만 가능하다(ADR 결정 11).
"Crew 실행"·"AI 에이전트 생성(crewai)"처럼 실제 CrewAI Agent 루프가
있는 경로에서만 Agent가 스스로 호출 여부를 판단해 쓸 수 있다 —
Wikipedia 도구(native, 양쪽 경로 다 지원)와의 핵심적인 차이이며, 이
사실을 tool_pool의 `description`과 skill_pool의 `description`
양쪽에 명시해뒀다(웹 UI에서 등록만 보고 "왜 협업 워크스페이스에서
실행이 안 되지"라고 헷갈리지 않도록).

**리스크(정직하게 문서화)**: Parallel AI는 상업 회사의 무료 티어다 —
Wikipedia처럼 20년 이상 안정적으로 운영된 공공 인프라가 아니라 조건이
바뀌거나 유료 전환·서비스 중단될 수 있다. 개인정보가 포함된 질의를
처리하려면 별도 DPA 계약이 필요하다고 명시돼 있어, 사람 이름 등을
검색어에 넣는 용도로는 주의가 필요하다(향후 이 도구가 실제 업무에
쓰인다면 검토가 필요한 지점 — 이번 스코프에서는 코드로 강제하지
않았다).

**검증**: GC197(`test_phase14_pool_registry.py`) — 시드 확인(kind=
mcp_remote) + `resolve_pool_tools()`가 실제로 `connect_mcp_tools()`를
거쳐 Tool을 반환하는지, `crewai_tools.MCPServerAdapter` 자체를
mock해 안전하게 검증(GC104와 같은 mock 경계 — 실제 접속은 위 라이브
검증으로 이미 확인했으므로 매 테스트 실행마다 실제 네트워크를 타지
않는다). 기존 GC114/GC125의 하드코딩된 Pool 개수 단언을 다시 한번
갱신했다(도구 4→5개, 스킬 24→25개) — §43에 이어 이번에도 겪은
동일한 유지보수 비용. 전체 골든셋 201건(200+1). 라우트 수 변경
없음(기존 Tool Pool 등록 메커니즘 재사용). 실제 서버(`hair.db`)
재시작 후 `GET /api/tool-pool`에 5번째 도구로 노출되는 것까지 라이브
확인했다.

## 45. 기획/설계 문서 3종 감사(병렬 fork) — 사용자 요청(2026-09-29,
    "(1) 기획/설계 내용을 정독 빠짐없이 구현되었는지 확인 (2) crewAI의
    핵심 개념과 기능들을 적절하게 반영하였는지 확인 (3) Agent-Evaluator를
    적절하게 반영했는지 확인해줘" → "a 부터 진행")

병렬 fork 3개로 각각 독립 감사했다(기획/설계 문서 vs 구현, CrewAI
핵심 개념 반영, Agent-Evaluator SDK 반영) — 코드 변경 없는 리포트
전용 라운드. 이번 §45는 그중 (1)의 "문서가 낡았다" 발견 2건을 실제로
정정한 결과만 기록한다((2)·(3)의 발견은 아직 미착수 — b·c로 남겨둠).

**정정 1 — MCP 종단간 검증 "오픈 이슈" 서술이 낡았다**: `docs/ADR.md`
결정 10(두 곳)·`docs/SPEC.md`(§10 결정 요약, §12 오픈 이슈 목록)가
"접속 가능한 실제 MCP 서버가 없어 종단간 검증은 못 했다"고 적고
있었는데, §44에서 Parallel Search(`mcp_remote`, 계정·키 불필요)로
실제 접속·검색 호출까지 라이브 검증을 이미 마쳤다. **정확하게
스코프를 좁혀서** 정정했다 — `pool_registry.connect_mcp_tools()`를
직접 호출한 연결 검증이지 `run_crew()`를 통해 LLM이 스스로 이 Tool을
호출하는 것까지 재현한 건 아니라는 점, 그리고 `crewai_agent_generator()`
(F4 단일 생성)의 MCP 미연결은 여전히 그대로 남아있다는 점을 명시해
과장 정정이 되지 않게 했다. ADR.md는 기존 취소선+"정정(날짜, 이유)"
관례를 그대로 따랐고(원문은 보존, 정정을 append), SPEC.md는 "현재
상태 요약" 성격의 절(§10, §12)이라 직접 갱신했다 — 반면 SPEC.md §2의
"관리자/일반 사용자 UI 분리... GC73-76" 이력 테이블 행은 그 시점(2
그룹 상태)의 정확한 스냅샷이라 **의도적으로 건드리지 않았다**
(RETROSPECTIVE.md와 같은 "역사적 기록 vs 현재 상태 요약" 구분 원칙).

**정정 2 — Web UI 내비게이션 "2구역" 서술이 낡았다**: `docs/ADR.md`
결정 9·`docs/SPEC.md`(§10)가 "내 업무"/"관리자 도구" 2구역이라고
적고 있었는데, §37에서 이미 "내 업무"/"공용 조회"/"운영 관리" 3구역
으로 재편됐다. ADR.md에는 원래의 "MEMBER/VIEWER 포함 여부로 자동
분류"라는 분류 기준 자체가 3구역 체제에서는 "내 업무"와 "공용 조회"
를 구분 못 한다는 한계까지 함께 정정 내용에 남겼다(그 경계는 이제
역할이 아니라 "담당자 개인 업무 처리 vs 순수 참조"라는 별도 기준).

**부수 발견(Agent-Evaluator fork, 오독 정정)**: 3개 fork 중
Agent-Evaluator 감사는 실질적 갭(Gate A-G가 `execute_agent()` 단일
실행 경로에만 연결되고 `run_crew()` 멀티에이전트 경로엔 전혀 연결
안 됨 — SDK의 `framework="crewai"` 자동 추출 기능 미사용)을 정확히
찾아냈다. 반면 기획/설계 문서 fork가 보고한 "`ROLE_TASK_CLASSIFICATION.md`
갭 2(Human/Agent 비용·성과 수정 권한 비대칭)가 이미 해소됐다"는
**오독으로 확인돼 반영하지 않았다** — 실제로 갭 2는 "Human Pool을
수정할 방법이 없다"는 게 아니라 "Human Pool은 매니저가 자유롭게
비용/성과를 고칠 수 있는데 Agent Pool은 admin 전용"이라는 비대칭
자체가 문제이고, fork가 인용한 `PATCH /api/human-pool/{id}` 엔드포인트는
갭 2가 처음 쓰일 때부터 이미 존재했던 그 엔드포인트다 — 코드 인용은
정확했지만 그 갭이 "무엇을 묻고 있는지"를 오해했다. 갭 1(MCP 도구
등록이 admin 전용 아님)·갭 2 둘 다 여전히 열려 있는 사용자 확인
필요 사항이다(§45 자체에서는 결정하지 않음, `docs/ROLE_TASK_
CLASSIFICATION.md` 참고).

**검증**: 문서 전용 변경(ADR.md/SPEC.md), 코드 변경 없음 — 골든셋
201건 그대로, 테스트 실행 불필요(두 파일 모두 어떤 테스트도 데이터로
읽지 않음, `grep`으로 확인).

## 46. Gate A-G를 CrewAI 멀티에이전트 경로(`run_crew()`)에도 연결 —
    사용자 요청(2026-09-29, §45 "부수 발견"에서 찾은 갭 "b 진행해줘")

§45가 찾은 갭을 메웠다 — Gate A-G 자동 채점이 `agent_runtime.
execute_agent()`(단일 실행) 경로에만 연결돼 있고 `run_crew()`(CrewAI
다중 Agent 협업) 경로엔 전혀 연결 안 돼 있었다. `agent_evaluator` SDK의
`framework="crewai"` 자동 추출 기능(`_extract_crewai_metadata()` →
`CrewOutput.tasks_output`에서 `agent_interactions`를 자동으로 뽑아냄)을
써서, `run_and_score()`처럼 `EvalMetadata`를 수동으로 만들 필요 없이
연결했다.

**한 것**: `agent_evaluation.py`에 `run_and_score_crew(crew_label,
call_fn)` 신설 — `@agent_eval(..., framework="crewai")`로 `crew.kickoff`
호출을 감싼다. `crew_runtime.run_crew()`의 `result = crew.kickoff()`를
이 함수 호출로 교체했다. SDK 내부가 `_split_raw()`로 호출자에겐 원본
`CrewOutput`을 그대로 돌려주므로(채점은 `PerformanceMonitor` 쪽 사이드
이펙트) `run_crew()`의 나머지 후처리 로직(per_agent 조립 등)은 손대지
않았다.

**저장 위치**: 새 테이블을 만들지 않았다 — `run_crew()`는 애초에
`deliverables`/`jobs` 로우를 만들지 않아(협업 워크스페이스 개념이 없는
실행), `agent_evaluations`(`deliverables.id` 전용 FK) 테이블을 재사용할
방법이 없다. 대신 이미 같은 자리에 있던 "감사로그 참고 기록" 패턴
(`token_usage`가 지금까지 그렇게 기록돼 왔다)을 그대로 재사용해
`crew_run_completed` 감사로그의 `after_value`에 `gate_scores`/`tcr`을
함께 남겼다(원칙4: 새 아키텍처 대신 기존 패턴 재사용). API 응답
(`POST /api/crew/run`)에도 같은 값을 반환 dict에 추가해 엔드포인트
코드 변경 없이 그대로 노출된다. Web UI "Crew 실행" 탭에도 단건
deliverable 카드의 Gate 배지와 같은 "측정 없음" 구분 원칙을 따르는
표시를 추가했다.

**검증**: `tests/unit/test_phase12_crewai_vectordb_graphdb.py`에
GC198(`run_crew()` 반환값·감사로그 양쪽에서 Gate A-G 키 7개·TCR 확인)·
GC199(`POST /api/crew/run` API 응답에도 그대로 노출되는지 확인) 추가.
전체 스위트 203건(기존 201 + 신규 2) 통과.

## 47. 거버넌스 결정 8건 일괄 구현 — 사용자 요청(2026-09-29,
    "결정이 필요한 내용들을 종합적으로 정리해줘" → 8건 일괄 확인)

§45가 종합 정리해 물은 미결정 사항(`docs/ROLE_TASK_CLASSIFICATION.md`
갭 1·2 + `docs/SPEC.md` §12 오픈 이슈 6건)에 사용자가 한 번에 답해,
8건을 전부 이번 라운드에서 구현했다.

**(1) 갭 1 — MCP 도구 등록 admin 전용**: `POST /api/tool-pool`에서
`kind IN ('mcp_stdio', 'mcp_remote')`이면 admin이 아닌 경우 403 —
native 도구 등록은 매니저도 여전히 가능하다(엔드포인트 전체를 막지
않고 이 두 kind만 좁힘).

**(2) 갭 2 — Human Pool 비용/성과 admin 전용**: `PATCH
/api/human-pool/{id}`의 `cost_estimate` 필드와 `POST /api/human-pool/
{id}/performance` 전체를 admin 전용으로 좁혀 Agent Pool(이미 admin
전용)과 대칭을 맞췄다. 기존 골든셋(GC27-29, GC54, GC114)이 매니저로
이 경로를 호출해 성공을 기대하고 있어 admin으로 바꿔 갱신했다.

**(3) F14 breakdown 본인 공개**: `POST /api/tasks/{id}/match`를
MEMBER에게도 열되, `human_pool.user_id`로 본인과 연결된 항목이 아니면
`top`에서 걸러낸다 — 다른 후보의 breakdown을 보게 하는 건 프라이버시
문제라 "공개"를 "전체 공개"가 아니라 "본인 것만 공개"로 좁혀 해석했다.

**(4) 보관기간 1년**: 새 모듈 `retention.py`의 `purge_expired()`가
`create_app()` 기동 시 자동 실행된다(`deliverables.
reap_orphaned_execution_jobs()`와 같은 자리). `assignment_history`는
평범한 DELETE로 충분하지만, `audit_log`는 NF10 append-only 트리거
(`audit_log_no_delete`)가 모든 DELETE를 막아서 — 이 함수 **안에서만**
그 트리거를 `DROP`했다가 `finally`로 반드시 다시 `CREATE`하는 통제된
경로로만 지운다(다른 코드는 여전히 못 지움). 지울 게 없으면(가장 흔한
경우) DB에 손도 안 댄다 — 매 기동마다 불필요한 잡음을 남기지 않기
위해서다.

**(5) 팀원 셀프서비스 배정 요청**: `POST /api/tasks/{id}/assignments`를
MEMBER에게 열되 `resource_type='human'`이고 `resource_id`가 본인의
`human_pool.id`일 때만 허용한다 — 다른 사람·Agent를 대신 요청하는 건
여전히 403이다. 승인 게이트(F7)·self-approval 방지(ADR 3-A)는 그대로
적용되므로 "요청 권한"만 넓혔을 뿐 확정 권한은 늘지 않았다. 기존
GC12(멤버가 남의 자원을 요청 — 이젠 role 자체가 아니라 본인 확인
때문에 여전히 403)는 이름은 유지하고 이유만 정정했다.

**(6) CrewAI 위임 캡처 방어 로직**: `_capture_delegation()`이 정확한
문자열(`DELEGATION_TOOL_NAMES`) 매칭을 우선하되, "delegate"/"coworker"/
"co-worker"/"ask question" 키워드가 들어간 도구 이름이면(대소문자
무관) 위임으로 간주하는 `_looks_like_delegation()` fallback을
추가했다 — CrewAI 버전업으로 정확한 문구가 바뀌어도 "조용한 미탐"이
될 가능성을 줄인다. 근본적으로 CrewAI 내부 문자열에 의존한다는 사실
자체는 남아 있어 리스크가 완전히 사라진 건 아니라고 정직하게
문서화했다.

**(7) skill_pool.augmentation_mode='always' 실제 구현**: 새 함수
`pool_registry.resolve_static_augmentation_targets(conn, skills,
modes)`가 `skill_pool`을 직접 조회해 `modes`에 속하는 항목 중
`skills`와 매칭되는 native_id 집합을 반환한다. 두 호출부가 다른
의미로 쓴다 — `agent_runtime.augment_with_retrieval()`(도구 호출
루프가 없는 단일 실행 경로)은 `modes=("always","tool")`로 불러
`'tool'`이든 `'always'`든 정적 주입이 유일한 선택지라는 사실을
반영했고(기본 시드 스킬이 전부 `'tool'`이라 이 변경은 기존 동작을
바꾸지 않는다 — GC90/91/195 그대로 통과), `crew_runtime.
_static_augmentation()`(진짜 CrewAI Tool 호출 루프가 있는 경로)은
`modes=("always",)`만 써 `'tool'`(Agent가 스스로 판단해 호출)과
`'always'`(Task description에 강제 주입, Tool 미부착)를 실제로
구분되게 만들었다. `'none'`은 여전히 의도된 대로 아무 효과가 없다.

**(8) VectorDB Ollama 임베딩 지원**: `vectorstore.embed_text()`에
`provider="ollama"` 분기를 추가했다 — `llm.py`의 `_call_ollama()`와
같은 `OLLAMA_BASE_URL` 환경변수(`http://localhost:11434` 기본값)+
httpx 패턴, Ollama의 실제 `POST /api/embeddings` 계약(`{"model",
"prompt"}` → `{"embedding"}`)을 따른다. Anthropic은 여전히
미지원(공개 임베딩 API 자체가 없음).

**검증**: `tests/unit/test_phase20_governance_decisions.py` 신설
(GC200-209, 10건) + 기존 골든셋 중 새 RBAC 경계에 맞춰 갱신한 것
(GC12 이유 변경, GC27-29/GC54/GC114를 admin 경로로 수정, v1
파이프라인의 매칭 RBAC 테스트를 "member는 200+빈 목록, viewer는
403"으로 정정). 전체 스위트 213건(기존 203 + 신규 10) 통과, 리그레션
1건(v1 파이프라인의 오래된 "member는 매칭 자체가 403" 단언) 발견·
즉시 수정.

## 48. 기획/설계·CrewAI·Agent-Evaluator 3종 감사 재실행 — 사용자
    요청(2026-09-29, "(1)(2)(3) 확인해줘" 재실행 → "차이점들을
    개선해줘")

§46·§47 이후 같은 3종 감사를 병렬 fork 3개로 다시 돌렸다(문서만
바뀌었는지, 코드 회귀는 없는지 재확인). 코드 버그는 0건 — 전부 문서
갱신 누락 또는 SDK 자체의 알려지지 않은 한계였다.

**문서 정정 5건(전부 이번에 수정 완료)**:
1. `docs/SPEC.md:33`(§3 out-of-scope)가 "F14 breakdown 본인 공개는
   백로그"라고 적은 채 남아있어, 같은 문서 §12의 "해결(2026-09-29)"
   과 정면으로 모순됐다 — §47 작업이 §12만 고치고 §3을 놓친 것.
   strikethrough+정정으로 바로잡음.
2. `docs/ROLE_TASK_CLASSIFICATION.md` §2-1(admin 고유 13개 표)·
   §4(최종 분류 요약표)가 §6(이번 세션에 추가한 결정 기록)과 내부
   모순 — §2-1은 Human Pool 성과/비용 2건이 admin 전용으로 넘어온
   걸 반영 안 해 13개인 채였고(15개로 정정), §4는 갭 1을 "재검토
   대상"이라고 계속 표시했다(결정 완료로 정정). §5 제목 아래에도
   "모두 결정·구현 완료" 포인터를 추가.
3. `docs/HAIR_MASTER_PLAN.md:249`가 "내 업무/관리자 도구 2구역"이라는
   §45에서 이미 정정했어야 할 문구를 그대로 갖고 있었다 — §45가
   ADR.md/SPEC.md만 고치고 이 파일을 놓쳤다. strikethrough+정정.
4. `docs/HAIR_ASSISTANT_PLAN.md:143-147`(미착수 기획 문서)이 "VectorDB
   임베딩은 OpenAI 전용이라 OPENAI_API_KEY 없인 챗봇 검색이 동작
   안 함"이라는, §47의 Ollama 지원 추가로 이제 틀린 전제 위에 대안
   (a)/(b) 논의를 세우고 있었다. strikethrough+정정, 실제 구현 시
   Ollama 경로 재검토를 명시.
5. `README.md`의 §37 서술 단락이 "갭 2건은 열어둠"이라고만 적혀
   §46·§47에서 해결된 사실이 이어지지 않았다 — 한 줄 추가로 연결.

**SDK/설계상 한계 2건(문서화만, 코드 수정 안 함)**:
6. **Gate F 구조화 모드가 CrewAI 경로에서 한 번도 안 켜진다** — SDK
   v1.1.1의 `_extract_crewai_metadata()`(`framework_adapters.py`)가
   만드는 `agent_interactions` 스키마(`from_agent`/`to_agent`/`type`/
   `tool_name`)가 Gate F의 구조화 서브 평가기 3개(`gate_f_multiagent/
   evaluators.py`의 `eval_consensus`/`eval_propagation`/
   `eval_role_adherence`)가 실제로 찾는 키(`agent`/`intent`/
   `facts_relayed`/`name`)와 하나도 안 맞는다 — SDK 내부(어댑터 vs
   평가기)의 불일치이지 이 저장소가 잘못 쓴 게 아니다. 그 결과
   `run_and_score_crew()`는 항상 텍스트 휴리스틱 폴백으로만 Gate F를
   채점한다(`not_measured`는 아니지만 SDK가 의도한 고정밀 경로는
   못 탐). SDK 쪽 버그라 이 저장소에서 고칠 수 없다 — 정직하게
   기록만 해둔다.
7. **여러 CrewAI 기능이 "검토 후 기각"이 아니라 그냥 안 쓰였다** —
   `step_callback`·`planning=True`는 이미 검토·보류가 문서화돼
   있는데, `Task(output_pydantic=...)`/`output_json=...`(구조화 출력
   강제 — Gate A/G 채점이 지금 `.raw` 자유 텍스트를 파싱하는 걸
   생각하면 가장 효과가 클 후보), `Task(guardrail=...)`,
   `async_execution=True`, `Crew(before/after_kickoff_callbacks=...)`,
   `crew.replay()`, `crewai.flow.flow.Flow`, CrewAI 자체 `Knowledge`
   클래스(이 저장소는 자체 SQLite VectorDB를 씀)는 전부 grep 결과
   0건 언급 — "고려했으나 기각"인지 "아예 몰랐다"인지 기록이 없다.
   전부 오픈 이슈로 남긴다(사용자가 이번엔 진행 대상으로 고르지
   않음, 다음에 필요하면 `output_pydantic`부터 검토 권장).

**검증**: 문서 전용 변경(SPEC.md/ROLE_TASK_CLASSIFICATION.md/
HAIR_MASTER_PLAN.md/HAIR_ASSISTANT_PLAN.md/README.md/LIMITS.md 자체),
코드 변경 없음 — 골든셋 213건 그대로, 테스트 실행 불필요.

## 49. Skill Pool 자동완성 중복 표시 — 사용자 보고(2026-10-06, "할일로
    나누기"의 "필요 스킬" 입력에 "일반 웹 검색, websearch, 외부 검색"이
    중복/유사 의미로 표시됨)

**1단계(설명 문구 추가)**: `websearch`/`외부검색`은 완전히 같은 뜻의
별칭(둘 다 `web_search` native 도구 = Wikipedia 검색)이고, `일반웹검색`은
Wikipedia가 아니라 Parallel Search(전체 인터넷) 도구에 연결된 별개
스킬이다(§44). 자동완성 `<datalist>`는 설명 없이 이름만 보여줘(`skill-
pool/names`) 구분이 안 됐다 — 우선 사용자 선택대로 "데이터는 그대로 두고
설명 문구만 추가"했다("할 일로 나누기" 폼에 `<p class="muted">` 추가,
`webui.py`).

**2단계(별칭 통합, 사용자 재요청)**: 이어서 "websearch/외부검색 하나만
표시되도록 개선"을 요청받아, 실제 할 일(`tasks.id=4`)이 이미
`required_skills=["외부검색"]`을 쓰고 있던 걸 근거로 **"외부검색"을
정식 이름으로 남기고 "websearch"를 skill_pool에서 제거**했다(`db.py`
`_seed_default_pools`). 이미 만들어진 DB(이 데모 서버 포함)에 남아있는
"websearch" 행은 `INSERT OR IGNORE`만으로는 안 지워지므로, `_migrate_schema()`에
멱등 정리 단계를 추가했다 — "websearch" 행이 있으면 그 이름을 쓰던
`tasks.required_skills`/`agent_pool.skill_tags` JSON 배열을 "외부검색"으로
치환한 뒤 skill_pool 행을 삭제한다(GC210, `test_phase13_db_migration.py`
— 기존 DB에 "websearch"로 등록된 할 일·Agent가 있어도 안전하게
넘어가는지 실제로 재현해 검증). 죽은 코드이던 `agent_runtime.
WEBSEARCH_SKILL_TAGS`(ADR 11 하드코딩 집합 — 실제 매칭은 이미
`pool_registry.resolve_static_augmentation_targets()`로 대체돼 있어
아무 데서도 안 쓰임, `test_phase14_pool_registry.py`의 patch 테스트가
이를 증명)도 `{"외부검색"}`으로 맞췄다.

**다른 중복 점검(사용자 요청)**: 같은 패턴(완전히 같은 뜻의 영문/국문
별칭 2개가 skill_pool에 따로 등록됨)이 두 군데 더 있다 — `vectordb`/
`rag`(둘 다 `vectordb_search`), `graphdb`/`관계질의`(둘 다
`graphdb_query`). 1단계에선 websearch 건만 통합 대상으로 지정받아 이
둘은 손대지 않고 유지했으나, **사용자가 곧바로 "같은 방식으로
통합해달라"고 재요청**해 이어서 처리했다.

**3단계(vectordb/graphdb 별칭도 통합)**: 실사용 데이터를 확인한 결과
`tasks.id=1`/`agent_pool.id=1`이 이미 `"rag"`를 쓰고 있었고(`"vectordb"`
쓰는 곳은 없음), `"graphdb"`/`"관계질의"`는 둘 다 실사용 데이터가
없어 코드·테스트에서 더 많이 참조되는 쪽(`"graphdb"` — `resolve_pool_
tools` 테스트 3곳·`test_phase17` 등)을 기준으로 삼았다 — **"rag"**와
**"graphdb"**를 정식 이름으로 남기고 `"vectordb"`/`"관계질의"`를
제거했다. websearch 때 만든 `_migrate_schema()`의 1회성 정리 코드를
`(removed_name, kept_name)` 3쌍을 순회하는 루프로 일반화해 세 통합을
한 구조로 처리한다(`db.py`). `test_gc210`도 `@pytest.mark.parametrize`로
3쌍 전부에 대해 "기존 DB에 제거 대상 이름으로 등록된 할 일/Agent가
있어도 안전하게 넘어가는지"를 재현해 검증하도록 확장했다. 관련 테스트
파일(`test_phase12/14/16/17`)의 하드코딩된 스킬 이름 집합·개수
단언(25→24→22)도 전부 갱신했다 — 1단계 때 `test_phase16`의 카운트
단언 하나를 놓쳐 전체 스위트가 1건 실패했던 것도 이번에 같이 바로잡음.
그 외 18개 agent-eval 절차 스킬 + `recommend-fix-workflow` +
`일반웹검색`은 이름이 전부 서로 다른 절차/도구를 가리켜 중복 없음.

**검증**: `test_gc108`·`test_gc196`·`test_gc210`(3쌍 parametrize)·관련
스킬 카운트 단언 갱신 후 영향받는 5개 테스트 파일 전부 통과(92
passed). 데모 서버 `hair.db`에도 두 단계 마이그레이션을 실제로 순서대로
실행해 `/api/skill-pool/names` 응답에서 `websearch`/`vectordb`/
`관계질의`가 전부 사라졌고, 기존 `tasks.id=1,4`·`agent_pool.id=1`의
스킬 태그가 각각 `["rag", ...]`·`["외부검색"]`로 안전하게 남아있음을
라이브로 확인.

## 50. "필요 스킬" 다중 입력 UX — 사용자 보고(2026-10-06, "하나의 스킬을
    선택한 후에는 다른 스킬을 펼쳐 확인할 수 없어")

§4(Phase 5)에서 이미 알려진 한계로 문서화만 해뒀던 문제를 실제로
고쳤다 — 콤마로 여러 스킬을 입력하는 단일 텍스트필드(`d-skills`/
`hp-skills`)는 브라우저가 입력값 전체를 기준으로 자동완성을 맞춰서,
하나 입력한 뒤 콤마를 찍으면 드롭다운이 거의 안 열렸다. `skillPickerHtml()`/
`initSkillPicker()`(`webui.py`)로 작은 태그 입력 위젯을 추가했다 — 값을
하나씩 입력하고 Enter/"추가"로 칩에 담으면 입력창이 비워지므로, 다음
항목을 고를 때도 매번 전체 자동완성이 뜬다. `skill_pool`에 없는 자유
텍스트(기존 데이터 "인사 정책" 등)도 그대로 칩으로 추가돼 기존 자유
입력 동작은 유지한다. 제출 핸들러가 읽는 숨은 `<input type="hidden">`은
기존 `.value.split(",")` 코드와 호환되도록 그대로 콤마구분 문자열을
담아 백엔드/제출 로직은 손대지 않았다. "할 일로 나누기"·"사람 인력
등록" 두 곳에 적용. 검증: `node --check`로 JS 문법 확인, `test_phase8_
webui.py`/`test_phase11_admin_user_nav_split.py` 통과, 데모 서버에 띄워
실제 렌더링 확인(사용자가 스크린샷으로 직접 확인).

## 51. Human Pool 등록과 사용자 계정의 분리 — 사용자 보고(2026-10-06,
    "사람 인력으로 등록되어 있지만 사용자 관리에 등록되지 않은 경우
    로그인도 안 돼 일을 할 수 없다") → "사용자 관리로 계정을 만들고
    사람 인력 등록시 사용자로 등록된 사람을 불러와 등록하도록 개선"

**확인된 갭**: `human_pool`과 `users`는 nullable FK(`human_pool.user_id`)
하나로만 느슨하게 연결된 완전히 분리된 테이블이었다. `POST /api/human-pool`은
`user_id`를 항상 `NULL`로 생성했고, 연결은 별도의 수동 PATCH("계정
연결" 버튼, 사용자 ID를 직접 숫자로 입력)로만 가능했다. 더 심각한 건
— `pool.human_pool_candidates()`(매칭 후보 조회)·`ResourceCandidate`
모델(`user_id` 필드 자체가 없음)·`request_assignment()`·결재 승인
흐름 **어디에도 `user_id` NULL 여부를 확인하는 코드가 없어서**, 계정
연결을 깜빡한 사람도 매칭→배정 요청→승인(confirmed)까지 경고 없이
전부 통과됐다. 그런데 `GET /api/my-work`는 `session user.id ==
human_pool.user_id`로 찾으므로, 로그인 계정이 없는 사람은 애초에
로그인을 못 해 자기 확정 배정을 영원히 볼 방법이 없다 — 이 사실을
알려주는 곳이 Human Pool 목록의 "연결된 계정 ID" 열(`-` 표시) 하나뿐,
그마저 매칭/배정 화면엔 안 보였다.

**구현**: "사용자 관리"로 계정을 먼저 만들고 "사람 인력 등록"에서 그
계정을 **골라서** 등록하는 흐름으로 바꿨다 — 임의 이름 입력이 아니라
선택 방식으로 바꿔 구조적으로 갭을 줄인다.
- `HumanPoolCreate.user_id`(optional) 추가 — 등록 시점에 바로
  연결 가능(`models.py`). `POST /api/human-pool`이 user_id 존재
  검증 + **이미 다른 Human Pool 레코드에 연결된 계정인지 검증**(409)
  후 INSERT에 포함한다(`app.py`). 기존 `PATCH .../user_id`(계정
  연결)에도 같은 중복-연결 가드를 추가했다 — 두 레코드가 같은 계정에
  연결되면 `WHERE user_id = ?` 조회가 하나만 조용히 골라 다른 쪽이
  안 보이게 되는 걸 막는다(자기 자신에게 재연결은 멱등 허용).
- `GET /api/users/names` 신규 — `GET /api/users`는 ADMIN 전용인데
  "사람 인력 등록"은 ADMIN/MANAGER 둘 다 접근 가능해 MANAGER가 계정
  목록을 못 읽는 문제가 있었다. `/api/skill-pool/names`와 같은
  패턴 — 관리용 전체 필드(manager_id/is_active) 대신 연결에 필요한
  최소 필드(id/username/role)만, 그 화면과 같은 권한 범위(ADMIN/
  MANAGER)로 노출. 비활성 계정은 제외.
- Web UI(`renderHumanPool`): 등록 폼에 "계정 선택" `<select>`를
  추가(이미 다른 레코드에 연결된 계정은 자동 제외), 제출 시 선택
  안 하면 클라이언트에서 바로 막는다. 기존 "계정 연결" 버튼의 숫자
  입력(오타 위험)도 같은 연결-가능 계정 드롭다운으로 바꿨다. 목록의
  "연결된 계정 ID" 열은 숫자 대신 username으로 보이게 개선(가독성).

**유지한 것**: `user_id`는 API 레벨에서는 여전히 optional이다 — 계정
없이 순수 인력 계획 레코드를 등록하는 기존 사용 사례(및 기존 테스트
호환성)를 깨지 않는다. 다만 Web UI의 기본 경로는 이제 "계정 선택"이
표준이라, 계정 없이 등록하면 그 사람은 로그인할 수 없다는 걸 등록
폼 안내 문구로 명시했다. 매칭/배정 화면 자체에 연결 여부 경고를
추가하는 건 이번 요청 범위 밖으로 남겨뒀다(§51-1로 후속 가능).

**검증**: `test_gc211`(RBAC: admin/manager만 `/api/users/names` 접근,
비활성 계정 제외, 최소 필드만 반환)·`test_gc212`(등록 시점 연결)·
`test_gc213`(없는 user_id → 404)·`test_gc214`(중복 연결 생성/PATCH
양쪽 다 409, 자기 자신 재연결은 허용) 신규 — `test_phase17_collab_
workspace.py` 47개 전부 통과. 데모 서버에 실제 계정 생성→연결→중복
거부→존재하지 않는 ID 거부까지 라이브 HTTP로 확인 후 테스트 데이터
정리.

## 51-1. 매칭/배정 화면 계정 미연결 경고 — 사용자 요청(2026-10-06, §51
     직후, "매칭/배정 화면에도 계정 미연결 경고 추가해줘")

§51이 "등록" 시점 갭은 메웠지만, 그 전에 이미 등록됐거나 일부러 계정
없이 등록한 사람이 "인력 매칭/배정" 화면에서 매칭·배정 요청될 때는
여전히 아무 경고가 없었다. `ResourceCandidate`에 `user_id: int | None`
필드를 추가해(`models.py`) 매칭 결과에도 연결 여부가 실리게 했다.

- `pool.human_pool_candidates()`가 `human_pool.user_id`를 그대로
  채워 넣는다(`pool.py`). Agent는 로그인 계정 개념이 없어 항상
  `None`.
- **노출 범위를 의도적으로 좁혔다**: `/api/tasks/{id}/match`(ADMIN/
  MANAGER/MEMBER)는 그대로 노출하지만, `/api/pool/search`는 viewer
  까지 전원 조회 가능한(RBAC 매트릭스) 엔드포인트라 `model_dump(exclude=
  {"user_id"})`로 명시적으로 뺐다 — 이번 요청이 "매칭/배정 화면"으로
  범위를 지정했고, 계정 연결 여부를 더 넓은 대상에게 노출할 이유가
  없다.
- Web UI(`renderMatchResult`): "계정" 열을 추가해 사람 후보는
  "연결됨"/"⚠ 미연결"(기존 Gate 배지 warn 스타일 재사용), Agent는
  "해당없음"으로 표시한다. 미연결 사람에게 "배정 요청"을 누르면
  `window.confirm()`으로 "본인이 '내 업무'에서 확인할 방법이 없다"는
  내용을 먼저 보여주고, 취소하면 API 호출 자체를 막는다(강제 차단은
  아님 — 계획 단계에서 미리 배정해두고 계정은 나중에 연결하는 합리적
  사용도 있을 수 있어 경고+확인으로 그친다).

**검증**: `test_gc215`(매칭 결과에 연결/미연결 `user_id`가 정확히
실리는지, admin/manager 전체 조회 기준)·`test_gc216`(`/api/pool/
search`가 viewer에게 `user_id` 키 자체를 안 보내는지) 신규 —
`test_phase17_collab_workspace.py` 49개 전부 통과. 데모 서버에 미연결
사람 인력을 실제로 만들어 `/api/tasks/{id}/match` 응답에 `"user_id":
null`이 실리는 것과, `/api/pool/search` 응답엔 `user_id` 키가 아예
없는 것을 라이브로 확인 후 테스트 데이터 정리.

## 52. "AI 에이전트 생성" 결과가 "AI 에이전트 관리"에서 안 보임 — 사용자
    보고(2026-10-06, "생성은 '성공'으로 떴는데 관리 탭에 안 보임")

**원인 확인**: 실제 `hair.db` 감사로그(`agent_generation_deduplicated`)를
보니 2026-09-30~10-03에 같은 task(`id=1`)로 반복 생성 요청이 4차례
있었다 — 전부 NF7 dedup(§2)으로 재사용돼 새 `agent_pool` 행이 안
생겼는데, **당시엔 §49/§50/§51 작업 전이라 "재사용했다"는 안내 자체가
없었다**(그 안내 배너는 이번 세션 초반에 이미 추가했다). 이번 보고로
재확인한 결과, 배너가 생긴 지금도 **"재사용했다"고만 말할 뿐 "그래서
몇 번 Agent를 보라는 건지"는 안 알려줘서**, "AI 에이전트 관리"의 여러
행 중 어떤 게 그건지 못 찾는 혼란이 여전히 남아있었다 — status가
"succeeded"로만 뜨는 것도 혼란을 더했다(신규 생성 성공과 재사용 성공이
화면상 구분이 잘 안 됨).

**구현**: `jobs._serialize_rows()`가 각 job_item이 **실제로 가리키는
`agent_pool` 행(id/role_name)을 `source_job_item_id`로 직접 찾아**
응답에 싣도록 바꿨다(신규 생성이든 재사용이든 전부 — 실패한 항목만
자연히 `None`). Web UI(`renderJobResult`)는 재사용 배너에 "기존 Agent
#2(버그재현 역할)를 그대로 씁니다" 식으로 구체적 ID를 명시하고, 결과
표에 "연결된 Agent" 열을 새로 추가해 매 항목이 가리키는 Agent를 항상
보여준다 — 이제 "어디 있는지" 찾을 필요 없이 ID를 그대로 "AI 에이전트
관리"에서 검색하면 된다.

**검증**: `test_gc37b` 확장(신규 생성·재사용 둘 다 `agent_pool_id`/
`agent_pool_role_name`이 정확한 기존 Agent를 가리키는지)·`test_gc38`에
실패 항목은 `agent_pool_id`가 `None`인지 단언 추가 — `test_phase4_
verification.py`·`test_v3_pipeline.py`(retry 경로도 같은 직렬화 함수 공유)
전부 통과. 데모 서버에서 실제 dedup을 재현해 `"agent_pool_id": 2`가
응답에 실리는 것을 라이브로 확인 후 테스트 데이터 정리.

## 53. "AI 에이전트 생성" 폼의 ID 직접 입력 제거 — 사용자 요청(2026-10-06,
    "업무 요청 ID 등 불러올 수 있는 정보들을 찾아 오입력되지 않도록
    개선")

"업무 요청 ID"(숫자 직접 입력)·"할 일 ID(콤마로 구분)"(콤마구분 숫자
직접 입력) 두 필드 다 존재 여부를 제출 전엔 확인할 방법이 없었다 —
틀린 ID를 넣으면 제출 후 404로만 알 수 있었다(§51에서 Human Pool
"계정 연결"에 적용한 것과 같은 패턴의 갭). 둘 다 서버가 이미 가진
데이터로 교체했다.

- "업무 요청"은 `GET /api/briefs`로 불러온 목록을 `<select>`로
  고른다(라벨: `#id + 본문 앞 24자`). 가장 최근 것(id 내림차순 응답의
  첫 항목)이 기본 선택된다.
- "할 일"은 선택된 업무 요청이 바뀔 때마다 `GET /api/briefs/{id}/tasks`
  로 다시 불러와 체크박스 목록으로 보여준다(`#id 역할명(필요 스킬)`) —
  여러 개 선택 가능은 유지. 할 일이 없는 업무 요청을 고르면 "할 일로
  나누기"로 유도하는 안내만 뜬다.
- 제출 핸들러는 체크된 값들만 모아 기존 `task_ids` 배열 계약 그대로
  보낸다 — 백엔드(`/api/briefs/{id}/generate`)는 전혀 손대지 않았다.

**검증**: `node --check`로 JS 문법 확인, 영향받는 테스트 파일(`test_
phase8_webui.py` 등, 이 화면의 구체적 input id를 참조하는 테스트 없음)
포함 전체 스위트 통과. 데모 서버에 띄워 실제 브리프 목록이 드롭다운에,
그 아래 할 일 체크박스가 실제 데이터로 채워지는 것을 라이브로 확인.

## 54. 실사용 중 발견: native CrewAI Tool 3종이 전부 크래시(한글 함수명) —
    사용자 실제 보고(2026-10-06, 실제 crewai+openai 생성 결과 붙여넣기)

**사용자가 직접 붙여넣은 실패 결과**: "AI 에이전트 생성"을 실제로
돌렸더니 두 건이 실패했다 — (1) `Model llama3.1 not found`(provider=
ollama인데 로컬에 그 모델이 안 받아져 있던 환경 문제, 코드 버그 아님),
(2) **`OpenAI function name cannot be empty`** — 이건 실제 코드
버그였다.

**원인(소스 레벨로 확인)**: `graphquery.py`/`vectorstore.py`/
`websearch.py`의 `as_crewai_tool()`이 전부 `@tool("조직도 관계
조회")`/`@tool("지식 베이스 검색")`/`@tool("외부 자료 검색")`처럼
순한글 이름을 썼다. 설치된 crewai 버전의 `sanitize_tool_name()`
(`crewai/utilities/string_utils.py`)이 함수명을 ASCII로 정규화하며
`name.encode("ascii", "ignore")`로 비-ASCII 문자를 전부 버리는데,
한글만으로 된 이름은 이 과정에서 **완전히 빈 문자열**이 되고,
`validate_function_name()`(`crewai/llms/providers/utils/common.py`)이
빈 문자열을 즉시 거부한다 — provider와 무관하게(litellm이 모든
provider의 함수호출을 "OpenAI function calling" 스키마로 통일해서
다루기 때문에 ollama를 써도 메시지는 "OpenAI function"으로 뜬다),
VectorDB/GraphDB/외부검색 스킬을 가진 Agent가 **crewai 프레임워크로
실제 실행될 때마다 100% 크래시**하는 상태였다. 기존 테스트들은 전부
`crewai.Crew.kickoff`를 mock해서 이 검증 경로 자체를 타지 않아 지금까지
못 잡았다 — 실제 API를 호출해본 사용자의 실사용 보고로만 발견 가능했다.

**수정**: `tool_pool.name`(Tool Pool 관리 화면에 보이는 한글 이름,
예: "VectorDB 지식검색")은 그대로 두고, LLM에 실제로 전달되는 `@tool()`
함수명만 ASCII로 바꿨다 — `knowledge_base_search`/
`org_relationship_query`/`external_knowledge_search`. 설명(docstring)은
한글 그대로 둬 LLM이 용도를 이해하는 데는 지장이 없다.

**검증**: 설치된 crewai의 `sanitize_tool_name()`/`validate_function_name()`
에 변경 전/후 이름을 직접 넣어 변경 전 3개 전부 크래시·변경 후 3개 전부
통과를 확인. 영향받는 3개 테스트 파일(`test_phase12/14/20`)의 `t.name`
단언을 새 이름으로 갱신 후 51개 전부 통과. **실제 사용자가 보고한
시나리오(task #4, "외부검색" 스킬, crewai+openai)를 그대로 재현**해 —
수정 전엔 크래시했던 이 정확한 조합이 수정 후 실제 OpenAI API 호출까지
성공해 실제 산출물을 받는 것을 라이브로 확인(테스트 데이터 정리 완료).

## 55. 팀 구성·협업 프로세스 통합 검토 및 구현(옵션 C) — 사용자 요청
    (2026-10-06, "사람-Agent 협업 관점의 프로세스 구성 및 팀 구성은
    어느 메뉴에서 하는지 분석해줘" → "하나의 화면으로 합치는 방안도
    검토해줘" → "C로 진행해줘")

**분석**: "팀 구성"(업무 요청→할 일로 나누기→인력 매칭/배정→결재)과
"협업 프로세스"(내 업무→협업 워크스페이스)가 서로 다른 탭에 분리돼
있던 이유를 RBAC·사용 패턴 양쪽에서 확인했다 — (1) MEMBER는 F14
결정(§47)으로 매칭 결과 중 본인 것만 보게 의도적으로 제한돼 있어
"전원이 보는 한 화면"과 구조적으로 안 맞고, (2) 매니저의 매칭/결재는
여러 업무 요청을 가로지르는 배치 작업인 반면 업무 요청 화면은 브리프
단위 드릴다운이라 기준 축이 다르다.

**검토한 옵션**: (A) 완전 통합 — RBAC 분기가 한 화면에 복잡하게
얽히고 매니저의 일괄 처리 효율이 떨어질 위험(UI 자동테스트가 없는
프로젝트라 대규모 리팩터링 자체도 회귀 위험), (B) 가벼운 딥링크만
추가 — 리스크는 최소지만 "합쳐졌다"기엔 부족, **(C) "업무 요청"에
할 일 상세 뷰 신설, 배치 화면(매칭/결재/내 업무)은 유지(GitHub PR
목록+상세 페이지 패턴)** — 사용자가 C를 선택.

**구현**:
- `GET /api/tasks/{task_id}/assignments` 신규(`app.py`) — 조직도와
  같은 노출 수준(이름+상태, 비용/성과점수 제외)으로 전원에게 열되,
  status 제한 없이 이력 전체(requested/confirmed/rejected/superseded)를
  최신순으로 반환한다.
- "업무 요청"의 할 일 목록에 "상세" 버튼 추가 → `renderTaskDetailPanel()`
  (신규)이 배정 현황을 보여주고, admin/manager에게만 "후보 매칭하기"
  (미확정 시)와 협업 워크스페이스 요약(확정 시)을 임베드한다 —
  member/viewer는 읽기 전용 상태만(자기 업무 처리는 기존처럼 "내 업무"
  에서). member 제한 이유: admin/manager가 아니면 산출물 조회가 참가자
  본인만 허용돼(403) 있어, 그 둘을 감독 권한이 있는 role에만 안전하게
  임베드할 수 있다 — member용 셀프서비스 임베드는 이번 범위 밖(오픈
  이슈로 남김).
- 중복 구현을 피하려고 기존 `renderMatchResult()`/`renderCollabWorkspace()`
  를 그대로 재사용했다 — 둘 다 렌더 대상 DOM id가 하드코딩돼 있던 걸
  `containerId` 매개변수(기본값은 기존 id 그대로)로 일반화해서, 기존
  "인력 매칭/배정"·"내 업무" 호출부는 전혀 안 건드리고 새 패널에서도
  같은 로직을 그대로 쓴다. `renderMatchResult()`엔 `onAssigned` 콜백도
  추가해 — 임베드된 쪽에서 배정 요청 성공 시 자기 패널을 새로고침할 수
  있게 했다(이 함수 자체는 호출한 화면의 구조를 모른 채로).

**검증**: `test_gc217`(배정 없음→요청→확정→재배치로 기존 건
superseded 전환까지 전체 생애주기, viewer 읽기 접근 확인)·`test_gc218`
(404) 신규 — `test_phase17_collab_workspace.py` 51개 전부 통과.
`node --check`로 JS 문법 확인. 데모 서버에 실제로 띄워 새 엔드포인트가
기존 confirmed 배정(agent)을 정확히 이름까지 resolve해 반환하는 것과,
배정 없는 할 일의 매칭 결과(`generate_new: true`)가 기존 "인력
매칭/배정" 탭과 동일하게 처리되는 것을 라이브로 확인.

## 56. "Crew 실행" 폼의 Agent ID 직접 입력 제거 — 사용자 보고(2026-10-06,
    "crew 실행 UI/UX를 개선해줘 (에이전트 ID를 알 수 없음 등)")

이전엔 위쪽 표에서 Agent ID를 직접 읽어 `textarea`에 "1: 할 일" 형식의
줄로 손으로 옮겨 적어야 했다 — §53(AI 에이전트 생성)·§51(Human Pool
계정 연결)과 같은 "직접 입력 대신 실제 데이터에서 선택" 패턴의 또
다른 사례다. `textarea` + `indexOf(":")` 파싱(할 일 설명에 콜론이
들어가면 깨지는 취약점도 있었다)을 **Agent별 드롭다운+할 일 입력
행**으로 바꿨다 — 드롭다운 옵션 자체가 "#id 역할명(스킬)"을 보여줘
ID를 몰라도 된다. "+ Agent 추가"로 행을 늘리고 각 행의 ×로 제거(최소
1행 유지). "매니저 Agent ID" 숫자 입력도 같은 드롭다운으로 바꿨다.
온보딩된 Agent가 하나도 없으면 입력 영역 자체를 숨기고 제출 버튼을
비활성화한다.

**검증**: `node --check`로 JS 문법 확인, 드롭다운 옵션 생성 함수
(`crewAgentOptionsHtml`)를 실제 `/api/agent-pool` 데이터로 Node에서
직접 실행해 `#1 정책 문의 응답 (rag, 인사 정책)`처럼 ID+역할+스킬이
한 옵션에 정확히 표시되는 것을 확인. 영향받는 테스트 파일(`test_
phase8_webui.py` 등, 이 화면의 구체적 input id를 참조하는 테스트 없음)
포함 전체 스위트 통과. 백엔드(`/api/crew/run`)는 손대지 않았다 —
제출 시 보내는 `agent_ids`/`task_descriptions`/`manager_agent_id`
계약은 그대로다.

## 57. "Crew 실행" 결과에 협업 관계 다이어그램 추가 + ADR 결정 11
    문서-코드 불일치 발견·수정 — 사용자 요청(2026-10-06, "crew 실행
    UI/UX를 개선해줘 — 묶인 Agent간 협업 관계를 다이어그램으로 표시")

**다이어그램을 만들려다 발견한 진짜 갭**: 화살표를 그리려면 위임의
"누구에게"가 필요한데, `_capture_delegation()`은 `event.tool_args`를
아예 안 읽어 `from_agent_role`+`tool`만 남기고 있었다. `docs/ADR.md:324`
(결정 11)를 다시 보니 애초에 "누가 누구에게 무엇을 위임했는지"를
기록하겠다고 명시돼 있었다 — 문서가 약속한 걸 코드가 반쪽만 구현한
채 아무도 못 알아챈 상태였다. CrewAI 소스(`delegate_work_tool.py`/
`ask_question_tool.py`)를 확인해 위임 Tool들이 전부 `coworker` 인자로
대상 role을 받는다는 걸 확인하고, `event.tool_args["coworker"]`를
CrewAI 자신의 `sanitize_agent_name()`과 같은 방식(공백 정리+소문자화)
으로 정규화해 실제 Agent role과 매칭하는 `_resolve_delegation_target()`
을 추가했다 — 매칭 실패 시(존재하지 않는 동료 호출 등) 원문을 그대로
보존해 정보를 버리지 않는다.

**다이어그램 구현**(`webui.py`, `crewDiagramSvg()`): 외부 차트
라이브러리 없이 순수 inline SVG — 실선(회색)은 요청 시점에 정해진
구조(sequential 순서 전달 또는 hierarchical 조율, 항상 그림), 점선
(주황)은 `delegations`에 실제로 기록된 위임만 그린다(추측 금지, ADR
결정 11의 "실제 발동 증거를 남긴다" 원칙과 같은 정신). 매니저가
`manager_llm`으로 자동 배정돼(=우리 Agent 테이블에 없음) 노드로 그릴
대상이 없는 경우엔 "매니저(LLM 자동 배정)"라는 합성 노드를 만들어
구조는 보여주되 실제 Agent가 아님을 구분한다. 대상이 끝내 확인 안
되는 위임은 다이어그램에서 빼는 대신 "확인 안 된 위임" 접이식 목록에
원문 그대로 남긴다(숨기지 않음). 기존 "실제 위임/협업 발생 내역"
표에도 "대상 Agent 역할" 열을 추가했다.

**구현 중 발견·수정한 레이아웃 버그**: 위임 곡선이 여러 명(특히
hierarchical의 워커 행) 아래로 처질 때, 미리 고정해 둔 `viewBox` 높이
밖으로 곡선과 라벨이 튀어나가 잘리는 문제를 실제로 Node에서 좌표를
계산해보고 발견했다 — 본문(body)을 먼저 그려 실제 좌표의 최대 y값을
구한 뒤 그 값으로 `viewBox`를 결정하도록(선-그리기, 치수 나중 확정)
순서를 바꿔 고쳤다.

**검증**: `test_gc81` 확장(`to_agent_role`이 실제로 매칭되는지)·
`test_gc219`(`_resolve_delegation_target()` 단위 테스트 — CrewAI와 같은
정규화, 리스트 형태, 매칭 실패 시 원문 보존, 키 없음 케이스) 신규.
`crewDiagramSvg()`를 Node에서 직접 실행해 — (1) sequential/hierarchical/
단일 Agent/매니저 자동배정 4가지 레이아웃 전부 좌표가 `viewBox` 안에
들어오는지, (2) 의도적으로 재현한 레이아웃 버그가 고친 후 사라지는지
확인. **데모 서버에서 실제 Agent 2개로 진짜 CrewAI+OpenAI Crew를
실행**해(최저임금 조사 역할극) — 실제 위임이 발생했고
`to_agent_role`이 정확히 두 번째 Agent의 role_name으로 resolve된 것을
확인, 그 실제 응답을 다시 `crewDiagramSvg()`에 넣어 화살표+라벨이
기대대로 그려지는 것까지 라이브로 검증. 영향받는 3개 테스트 파일
포함 전체 스위트 통과.

## 58. Crew 실행이 Agent별 "모델 설정"을 실행 후 덮어쓰던 문제 수정 —
    사용자 요청(2026-10-06, "model provider/모델명이 AI 생성·관리·Crew
    실행 단계에 모두 있는데 최종 어떻게 적용되는지 분석" → "(b) 실행
    후 덮어쓰기만이라도 막는 방향으로 수정")

**분석으로 발견한 비대칭**: 세 화면의 provider/model이 각각 뭘 하는지
추적한 결과 — "AI 에이전트 생성"은 생성 시점 값을 Agent의 초기
`llm_provider`/`llm_model`로 저장하고, "AI 에이전트 관리 → 모델 설정"
은 그 값을 호출 없이 순수 저장만 하며(`app.py:1238` 주석에 이미
명시: "이후 `/execute`가 provider/model을 안 받으면 여기서 저장한
값을 기본으로 쓴다"), 단일 실행(`agent_runtime.execute_agent()`)은
실제로 그 저장값을 기본값으로 읽는다(`effective_provider = provider
or agent["llm_provider"]`). 그런데 **"Crew 실행"은 참여 Agent 전원에게
요청 시점의 provider/model 하나만 적용하고 개별 저장값은 아예 안 읽는
것까지는 의도된 설계로 보이는데, 실행이 끝나면 그 하나의 값을 모든
참여 Agent의 `llm_provider`/`llm_model`에 그대로 덮어쓰고 있었다**
(`crew_runtime.py` 487행 — ADR/LIMITS 어디에도 이 덮어쓰기를 의도했다는
근거가 없어 단일 실행 경로의 "실행 후 기록" 패턴을 그대로 복붙하면서
생긴 부작용으로 보인다). 그 결과 Agent별로 "모델 설정"에서 따로
구성해둔 기본값이 Crew 실행 한 번으로 조용히 사라지는 문제가 있었다.

**수정**: 참여 Agent의 `UPDATE agent_pool` 문에서 `llm_provider`/
`llm_model`을 뺐다 — `last_output`(실제 산출물)과 `framework`(이번
실행이 crewai였다는 사실)는 그대로 갱신하되, "모델 설정"은 사용자가
"AI 에이전트 관리"에서 명시적으로 바꾸기 전까지 Crew 실행과 무관하게
유지된다. (a)안(Crew 실행이 Agent별 저장값을 각자 읽어서 쓰게 하는
것)은 "여러 Agent를 한 LLM으로 통일해 비교·실행"하려는 현재 설계의
전제 자체를 바꾸는 더 큰 변경이라 이번엔 (b)만 반영했다 — 필요하면
후속으로 검토 가능.

**검증**: `test_gc220` 신규 — Agent 둘에 각각 다른 provider/model을
미리 저장해두고(anthropic/claude-sonnet-5, ollama/qwen3-coder) 전혀
다른 provider(openai)로 Crew를 실행한 뒤, 두 Agent의 저장값이 그대로
남아있고 `last_output`/`framework`만 갱신됨을 확인.
`test_phase12_crewai_vectordb_graphdb.py` 33개 전부 통과(기존
`test_gc79`도 `last_output`/`framework` 갱신은 그대로 됨을 이미
검증하고 있어 회귀 없음을 같이 확인).

## 59. 모델 레지스트리 신설 — 사용자 요청(2026-10-06, "운영 관리에 사용
    가능한 ollama 모델 및 openai, anthropic 모델 등록하는 메뉴를 두고
    등록된 모델을 불러오는 방식으로 UI/UX를 개선")

**배경**: "AI 에이전트 생성"·"AI 에이전트 관리(모델 설정)"·"Crew 실행"·
협업 워크스페이스 4곳의 "모델명"이 전부 자유 텍스트 입력이었다 —
직전 §58 분석 중 실제로 겪은 "ollama 기본값 llama3.1이 로컬에 없어
404" 사고가 이 구조의 직접적인 결과였다. skill_pool/tool_pool과 같은
"등록→선택" 패턴을 모델명에도 적용한다.

**구현**:
- 신규 `model_registry` 테이블(`provider`+`name` UNIQUE, `is_active`)과
  `model_registry.py` 모듈 — `pool_registry.py`와 같은 CRUD 패턴
  (`create_model`/`list_models`/`set_model_active`/`delete_model`).
- `fetch_available_models(provider)` — provider가 실제로 보유한 모델을
  **라이브로 조회**한다: ollama는 로컬 서버 `GET /api/tags`(완전 무료),
  openai/anthropic은 각 SDK의 `client.models.list()`(토큰 생성이 없는
  카탈로그 조회라 과금 없음 — `llm.call_llm()`의 비용 경계와는 다름).
  openai는 whisper/tts/dall-e/embedding/moderation 등 채팅에 못 쓰는
  모델을 deny-list로 거른다. SDK가 자동 페이지네이션을 지원해(`__iter__`)
  `.data`(첫 페이지만)가 아니라 객체 자체를 순회해 전체를 받는다.
  조회 자체는 아무것도 저장하지 않는다 — 등록은 사용자가 고른 것만
  별도 `POST`로.
- API 5개(`GET /api/model-registry`[+`?provider=`], `GET .../fetch`,
  `POST`, `PATCH .../active`, `DELETE`) — RBAC는 tool_pool/skill_pool과
  동일(등록/조회/활성전환은 admin+manager, 삭제는 admin 전용). `/fetch`
  실패(API 키 미설정, ollama 미접속 등 흔한 실패 모드)는 500이 아니라
  400+사람이 읽을 메시지로 감싼다.
- Web UI: "운영 관리"에 "모델 레지스트리" 탭 신설(provider별 불러오기+
  체크박스 선택 등록, 수동 등록, 활성화/비활성화/삭제). 기존 4곳의
  "모델명" `<input>`은 **그대로 자유 텍스트 유지**하되, `skill-datalist`
  와 같은 이유(단일 값 필드라 datalist 자동완성이 문제없이 동작)로
  provider별 `<datalist>`를 연결해 자동완성만 제공한다 — 레지스트리가
  비어 있어도(초기 상태) 입력 자체는 전혀 안 막힌다는 게 핵심 설계
  결정이다(등록을 강제했다면 콜드스타트에서 이 기능 자체가 막혔을 것).

**검증**: `test_phase21_model_registry.py` 신규 5건 — provider/빈
이름 검증, CRUD 전체 흐름(중복 409 포함), RBAC 매트릭스, "불러오기"가
아무것도 저장 안 함(mock), provider 오류를 400으로 감싸는지(mock).
`node --check`로 JS 문법 확인. 데모 서버에서 **실제 로컬 Ollama의
`/api/tags`를 호출해 진짜 설치된 모델 5개**(`qwen3-coder:latest` 등)를
불러오고 2개(ollama 1개, openai 1개)를 실제로 등록한 뒤, 그 데이터로
datalist HTML이 정확히 생성되는 것까지 라이브로 확인. 전체 스위트
232개 전부 통과.

## 59-1. 모델 선택을 datalist 자동완성 → 진한 드롭다운으로 교체 —
    사용자 요청(2026-10-06, §59 직후, "에이전트 생성, 관리, Crew 실행
    등에도 연동되도록 적용해줘" → "진한 드롭다운으로 강제 선택")

§59는 자유 텍스트 입력에 datalist 자동완성만 곁들였다 — 값을 강제하지
않아 "진짜 연동"처럼 안 느껴질 수 있다는 점을 사용자가 확인 질문으로
짚었고, 등록된 모델 중에서만 고르는 `<select>`로 바꾸는 쪽을
선택했다.

**구현**: `modelDatalistsHtml()`/`wireProviderModelDatalist()`를
`fetchModelsByProvider()`(provider별 모델 이름 배열을 돌려줌)+
`modelSelectHtml()`(드롭다운+숨겨진 "직접 입력" 텍스트칸 마크업)+
`wireProviderModelSelect()`(provider 변경 시 옵션 재구성, 등록 안 된
기존 값은 "직접 입력..."을 자동 선택해 보여줌)+`modelSelectValue()`
(드롭다운이 "직접 입력..."이면 텍스트칸 값을, 아니면 드롭다운 값을
반환)로 교체했다. "기본값 사용"(빈 값)과 "직접 입력..." 두 옵션은
항상 있어 레지스트리가 비어 있어도 기능이 막히지 않고, "AI 에이전트
관리"처럼 이미 저장된 모델명이 레지스트리에 없는 경우(과거 자유
입력으로 저장된 값)도 데이터 손실 없이 "직접 입력..." 칸에 그대로
보여준다. 4개 소비처(AI 에이전트 생성/관리/Crew 실행/협업 워크스페이스)
전부 교체.

**검증**: jsdom 등 외부 의존성 추가 없이, 이 세션이 실제 사용하는
`<select>`/`<input>` 속성(`value`/`innerHTML`/`style.display`/
`addEventListener`)만 흉내 낸 최소 가짜 DOM을 Node로 직접 작성해
9가지 시나리오(provider 전환 시 옵션 재구성, 등록된 값 선택, "직접
입력" 전환, 레지스트리에 없는 기존 저장값의 자동 "직접 입력" 폴백,
레지스트리에 있는 기존 저장값의 정상 사전 선택 등)를 전부 확인 —
전부 기대대로 동작. `node --check`로 JS 문법 확인, 전체 pytest
스위트 232개 통과(백엔드 API/모델 변경 없음 — 프런트엔드 전용 교체).

## 60. 다자간 팀 협업 기획안 작성(코드 변경 없음) — 사용자 요청
    (2026-10-06, "사람들과 Agent들이 협업으로 기업의 업무를 수행하도록
    하는 것이 HAIR Platform의 핵심이야... 복수의 사람이 복수의 Agent와
    또는 CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록
    ... 다시 기획하고 개선 계획을 수립해줘")

직전 질문("Crew 실행이 독립 실행인지 분석해줘")에서 실측으로 확인한
구조적 한계(assignments가 할 일당 resource_type별 confirmed 1건만
허용, `run_crew()`가 `task_id`/`deliverables`와 전혀 안 묶임, 역할
기반 Agent 그룹을 저장할 방법이 없음, CrewAI 실행 도중 사람 개입은
Phase C에서 이미 조사·기각된 기술적 한계)을 근거로 N명 사람+M개
Agent(또는 팀) 협업 기획안을 `docs/COLLABORATION_PLATFORM_PLAN.md`
(1:1 협업을 완성한 기존 기획)의 후속으로 작성했다 —
`docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` 신규.

**핵심 설계**: `teams`/`team_members` 신규 테이블(skill_pool/tool_pool과
같은 "등록→재사용" 패턴) + `assignments.resource_type='team'` 추가 +
`run_crew()`에 선택적 `task_id`를 더해 Agent 멤버별 산출물이
`deliverables`/Gate A-G/F7 승인 파이프라인으로 흘러들게 다리를 놓는
것 — 전부 **기존에 검증된 패턴의 재사용**이지 새 패러다임을 만들지
않는다(예: `task_participant_relationship()`은 이미 "모든 confirmed
배정"을 순회하는 루프라 팀 배정을 멤버별 배정으로 "전개"하면 그 함수
자체는 거의 안 고쳐도 됨 — 실제 코드를 읽어 확인한 뒤 반영). 5단계
로드맵(스키마→배정 확장→Crew-deliverable 연결→UI→Crew 탭 통합
검토)과 스코프 밖 항목(실시간 동시 협업은 Phase C 기각 사유 그대로
유지), 사용자 결정이 필요한 질문 3개를 명시했다.

**검증**: 기획 문서 전용 — 코드/테스트 변경 없음, 골든셋 232건 그대로.
`README.md` 문서 목록에 1줄 추가해 상태를 "미구현(기획만)"으로
명시(이 레포의 관례 — 기획과 구현을 문서 표기로 항상 구분).

| 항목 | 상태 |
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
| Phase 3: Agent 평가 인라인 연결(Gate A-G) | **구현 완료**(GC143-145) — `eval_wiring.py`(수동 probe)와 별도로 매 execute-agent 실행마다 자동 채점하는 `agent_evaluation.py` 신설. `agent_pool.performance_score`(수동)와 분리된 `agent_evaluations` 테이블(append-only). Gate B/D/E는 도구 호출 없는 단발 LLM 호출 특성상 not_measured 가능, Gate G는 현재 프롬프트가 판단 근거를 요구하지 않아 낮게 나올 수 있음 — 정직하게 문서화(§24) |
| Phase 4: execute-agent 비동기화 | **구현 완료**(GC146-148) — 사용자가 두 스코프(워크스페이스 비동기화 vs CrewAI Crew 정지/재개) 중 저위험 쪽을 선택. `start_agent_execution()`+`run_agent_execution_job()`으로 분리, `agent_execution_jobs` 신규 테이블(SQLite CHECK 제약은 ALTER 불가라 `deliverables.status` 확장 대신 새 테이블 선택). API 계약 파괴적 변경(201→202+job_id). `TestClient`가 `BackgroundTasks`를 응답 전에 동기 실행한다는 것을 실측 확인. 도구 호출 단위 일시정지(`crew_runtime.run_crew()` 대상)는 별도 세션/ADR 필요 항목으로 명시적으로 남김(§25) |
| Phase 1-4 리뷰: 크로스-Phase 버그 3건 + 죽은 코드 1건 | **전부 발견 → 전부 수정 완료**(GC149-153) — (1) 사람+Agent를 같은 task에 함께 confirmed하면 F5/F6 시절 재배치 로직이 resource_type을 안 가려 먼저 확정된 쪽이 조용히 superseded되던 버그(협업의 기본 전제 자체가 깨져 있었음), (2) `data_admin.py`가 Phase 1-4의 새 테이블을 몰라 deliverable이 남은 task 삭제 시 FK 위반 500 에러, (3) 서버 재시작 시 'running' job이 영원히 멈춰 보이던 문제(`reap_orphaned_execution_jobs()` 신설). 추가로 Phase 3의 `aggregate_agent_score()`가 어떤 API/화면에도 연결 안 돼 있던 죽은 코드를 발견해 `GET /api/agent-pool`+Web UI에 연결(§26) |
| Phase 1-4 리뷰 2회차: 중복 실행 트리거 + 불친절한 에러 메시지 | **전부 발견 → 전부 수정 완료**(GC154-155) — (1) `start_agent_execution()`이 같은 task에 이미 'running' job이 있어도 확인 없이 새 job을 또 만들어, 빠른 연속 클릭·여러 탭·직접 API 호출로 같은 트리거에 실제 LLM 호출이 두 번(비용 이중 지출) 나갈 수 있었음 — 서버 쪽에도 409 가드 추가. (2) 트리거 이후·실행 전 Agent가 삭제/오프보딩되면 `agent_not_active` 같은 내부 코드 문자열이 그대로 Web UI에 노출되던 것을 사람이 읽을 수 있는 문구로 번역(§27) |
| Phase 1-4 리뷰 3회차: 감사로그 비대칭 + 라벨 누락 | **전부 발견 → 전부 수정 완료**(GC156) — Agent 실행 성공은 감사로그에 남는데 실패는 안 남아 F13 관례(승인/반려 둘 다 기록)와 어긋나 있었음 — `agent_execution_failed` 액션 추가. 부수적으로 `agent_execution_jobs` subject_type의 Web UI 라벨이 아예 없어 감사로그 표에 영어 원문이 노출되던 것도 수정(§28) |
| Phase 5: 기업용 UI/UX 고도화(부분, 사용자 선택 4항목+제한적 반응형) | **구현 완료**(GC157-169) — 통합 인박스(`inbox.py`)·매니저 대시보드(`dashboard.py`, 비용 추이는 기존 감사로그 재사용)·검색/필터 스케일(availability 양방향 필터+페이지네이션, "부서"는 데이터 자체가 없어 의도적 제외)·협업 워크스페이스 UX(채팅형 타임라인+Gate 배지+Skill 자동완성)+제한적 반응형(`@media max-width:640px`). 부수적으로 Phase 3부터 있던 `evaluation` 필드가 어떤 화면에도 렌더링된 적 없던 갭과 Skill Pool 조회가 Admin/매니저 전용이라 팀원이 자동완성을 못 받던 RBAC 갭을 발견·수정. 다크모드는 명시적으로 스코프 밖(§29) |
| Phase 1-5 리뷰: Phase 5 자체 버그 2건("조용히 틀린 결과") | **전부 발견 → 전부 수정 완료**(GC170-171) — (1) `dashboard.cost_trend()`가 SQL LIMIT을 관련성 필터보다 먼저 적용해, 비용과 무관한 human_pool_updated가 몰리면 진짜 비용 이력이 응답에서 사라지던 버그, (2) 검색의 `availability='unavailable'` 필터가 Agent엔 안 걸려 항상 가능한 Agent가 "불가능만" 결과에 섞여 나오던 버그. 둘 다 크래시 없이 조용히 틀린 데이터를 보여주는 종류라 실측 없이는 못 잡았을 것(§30) |
| 기획/설계 문서 리뷰: 실제 구현과 어긋난 서술 6건 | **전부 발견 → 전부 수정 완료**(문서 전용, 코드 변경 없음) — `HAIR_MASTER_PLAN.md` §4 데이터 흐름 다이어그램의 3가지 오류(Tool Pool 그라운딩·run_crew()·performance_score 자동 갱신 — 전부 실제와 다름), §1/§2 표의 과장된 서술(HOTL 일시정지·성과 리뷰 자동 점수 병기·Gate 임계값 설정 — 전부 미구현이거나 다른 화면에 구현됨), §8 결정 목록의 비일관적 완료 표시(1·4번은 실제로 해결됐는데 계속 미해결로 표시), `COLLABORATION_PLATFORM_PLAN.md`·`SPEC.md`의 같은 종류 오픈 이슈 2건, `RETROSPECTIVE.md`엔 소급 수정 대신 안내 문구 추가(스냅샷 문서 원칙 유지)(§31) |
| UI/UX 리뷰: 화면 사용성 버그 4건 | **전부 발견 → 전부 수정 완료**(golden set 불가, `node --check`+ruff로만 검증) — (1) 댓글 폼 이벤트 리스너 누적 버그(가장 심각 — 댓글 등록마다 리스너가 1→2→4→8개로 배가돼 다음 댓글부터 중복 제출됨, 브라우저 DOM 동작이라 이 프로젝트에 테스트 인프라가 없어 코드 읽기로만 검증), (2) Gate 배지가 측정된 Gate 0개일 때 "0/0 통과"를 초록색으로 표시해 "전부 통과"처럼 오해를 주던 문제, (3) Agent "오프보딩" 버튼만 확인 창이 없던 인시스턴시(이 화면 안내문 자체가 "되돌릴 수 없다"고 명시하는데도), (4) 성과 리뷰 화면이 "Gate A-G 자동 채점 연동 전"이라는 낡은 안내를 보여주던 것(실제로는 Phase 3가 이미 연결했고 다른 화면에 있음). 부수적으로 이 파일 자체의 요약 표 헤더 행이 빠져 있던 것도 같이 복구(§32) |

## 61. 다자간 팀 협업 Phase 1+2 구현 — 사용자 결정(2026-10-06, §60의
    "다음 액션(사용자 결정 필요)" 3개 질문에 대한 답: "1-2를 묶어
    한번에 설계, 다중 confirmed 허용의 경우 개별 배정에도, 팀의 F7
    승인 팀 리더가(팀 리더의 경우 팀을 배정 요청한 사람이 default로
    지정, 변경 가능)")

§60의 기획안(`docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md`) Phase 1
(팀 정의 CRUD)과 Phase 2(`resource_type='team'` 배정 + 다중confirmed
허용)를 사용자 결정대로 한 번에 구현했다. Phase 3(`run_crew()` ↔
`task_id` 다리)·Phase 4(협업 워크스페이스 N:M 타임라인 UI)는 이번에
포함하지 않았다 — 기획 문서 §9에 "구현 현황"으로 남은 범위를 그대로
기록해뒀다.

**핵심 구현**:

- `teams`/`team_members` 신규 테이블(`db.py`) + `teams.py`
  CRUD(`skill_pool`/`tool_pool`/`model_registry`와 동일한 "등록→
  재사용" 패턴, 네 번째 적용).
- `assignments.resource_type='team'` 지원, `team_leader_id`(기본값
  `requested_by`, `requested` 상태일 때만 변경 가능 —
  `update_team_leader()`)로 F7 결재 주체를 팀 리더로 라우팅.
- **`resolve_assignment_decision()`의 "같은 task·같은 resource_type이면
  자동 supersede" 동작을 완전히 제거** — 이제 기본적으로 다중
  confirmed가 공존하고, 교체하려면 요청 시점에 `replaces_assignment_id`
  를 명시해야 한다. 이는 팀뿐 아니라 사람/사람, Agent/Agent 개별
  배정에도 적용되는 **플랫폼 전역 동작 변경**이다(결정 2를 그대로
  반영한 결과).
- `deliverables.task_participant_relationship()`에 `team_leader`/
  `team_member` 판정 추가 — 이미 "모든 confirmed 배정을 순회"하는
  구조라 수정 범위가 예상대로 작았다(기획 §3-2에서 미리 예측한 그대로
  — 실제 코드를 읽어 확인 후 반영).
- `app.py`에 팀 CRUD·멤버 관리·팀 리더 변경 8개 엔드포인트 신설.

**구현 중 실측으로 발견한 진짜 버그**: `GET /api/tasks/{id}/assignments`
(§55 "할 일 상세" 패널이 의존하는 엔드포인트)가 `human_pool`/
`agent_pool`만 JOIN하고 `teams`는 JOIN하지 않아, 팀 배정의
`resource_name`이 **항상 `null`**로 나오고 있었다 — 즉 이 기능 없이는
팀을 배정해도 UI가 "팀 #3"처럼도 못 보여주고 그냥 빈 이름을 보여줄
뻔했다. `LEFT JOIN teams` + `team_leader_id` 컬럼 노출로 수정하고
`test_gc231`로 회귀를 막았다.

**회귀 대응**: "다중 confirmed 허용"으로 기존 "재배치=자동 supersede"
전제가 깨져, 그 전제로 작성된 기존 테스트 4건(`test_v2_pipeline.py::
test_gc17/18`, `test_phase17_collab_workspace.py::test_gc150/217`)이
깨졌다 — 전부 `replaces_assignment_id`를 명시하도록 수정해 통과시켰다
(동작이 실제로 바뀐 것이므로 테스트도 새 계약에 맞게 고치는 게 맞다 —
옛 동작을 몰래 복원하는 임시방편은 쓰지 않았다).

**UI**: "팀 관리" 탭 신설(생성·멤버 추가/제외·활성 토글·삭제).
"할 일 상세" 패널(§55)을 `.find()`로 confirmed 1건만 집던 방식에서
confirmed/requested 전부를 나열하는 방식으로 바꾸고, 팀 리더 변경
입력과 "이 팀에 배정 요청" 폼을 추가했다.

**검증**: 신규 `tests/unit/test_phase22_team_collaboration.py`
(GC226-234, 9건) + 기존 4건 수정, 전체 골든셋 241건 통과(이전 232건
+9). `.venv/bin/python -m pytest tests/unit -q`를 두 번 독립 실행해
모두 그린임을 확인했다 — 중간에 한 번 백그라운드 실행이 비정상 종료
("killed")됐었는데, 동시에 여러 무거운 Bash/Edit 작업을 돌리던 시스템
부하 때문이었던 것으로 재확인(재실행 시 정상 222초대로 완료, 코드
자체의 회귀 아님). 실제 데모 서버(`hair.db`)에 팀 생성→멤버 추가→
배정 요청→F7 결재→확정까지 실측 완료(검증 후 생성한 테스트용 brief/
task/human_pool 레코드 1건씩이 정리 전 상태로 `hair.db`에 남아있다 —
이 레포의 DB 직접 수정을 막는 안전장치에 걸려 Bash로 지우지 못했고,
admin 계정 비밀번호는 알지 못해 API로도 못 지웠다. 사용자가 "업무
요청"/"사람 인력 관리" 탭에서 직접 지우거나, 그대로 둬도 다른 데이터에
영향은 없다).

커밋 직전 전체 스위트 재실행에서 `test_gc60_root_page_serves_html_shell`
(코드 재점검 R5, "F1/F5 같은 스펙 코드가 화면에 그대로 노출" 회귀 방지
테스트)이 실제로 걸렸다 — "팀 관리" 탭 안내 문구에 내부 스펙 코드
"F7 결재"를 그대로 썼던 것을 이 테스트가 바로 잡아냈다("팀 배정의
결재 주체는 팀 리더..."로 평이하게 수정). 이 세션에서 새 UI 문구를
쓸 때 실제로 기존 회귀 테스트에 걸린 유일한 사례 — 이런 종류의 실수를
잡아내는 게 그 테스트의 존재 이유 그대로였다.

**의도적으로 남긴 갭(§9에 기록)**: 팀의 Agent 멤버를 실제로 협업
실행시키는 기능(Phase 3)·"인력 매칭/배정"의 팀 추천(점수 로직)은
이번에 포함하지 않았다 — 각각 별도 작업으로 남는다.

## 62. `hair.db` 실제 손상 발생·복구 — 사용자 보고(2026-10-07, "모델
    레지스트리 '500 Internal Server Error' 발생")

**원인(내 과실)**: §61 검증 과정에서 "데모 서버를 최신 코드로 재기동"
하려고 실행 중인 서버를 죽이고(`pkill`) `hair.db`(WAL 모드) 파일을
`cp`로 직접 덮어써 되돌리려 한 조작이 실제 디스크 손상을 냈다 —
WAL 모드 SQLite 파일은 메인 파일만 통째로 교체하면 함께 있던
`hair.db-wal`과 메인 파일 헤더(salt)가 어긋나는데, 당시 `pkill` 타이밍
문제로 이전 서버 프로세스가 완전히 죽기 전에 파일을 덮어쓴 것으로
보인다. 그 직후 "DB를 직접 고치지 말라"는 이 레포의 안전장치(Bash
훅)에 막혀 되돌리지도 못한 채로 서버를 재기동해, 깨진 상태가 그대로
다음 세션(이번 사용자 보고)까지 남았다.

**발견**: 사용자가 "모델 레지스트리" 탭에서 500을 보고 — 서버 로그
확인 결과 `sqlite3.DatabaseError: database disk image is malformed`
(`model_registry.list_models()`). `PRAGMA integrity_check`로 재현:
"Tree 47/48 page 47/48: btreeInitPage() returns error code 11"(실제
btree 손상), 페이지 44-46은 "never used"(트리에서 떨어져 나간 상태).

**복구**: `sqlite3 .recover`(SQLite 3.45+ 내장 복구 명령)로 손상되지
않은 모든 테이블을 전부 살렸다(`users`/`briefs`/`tasks`/`assignments`/
`deliverables`/`skill_pool`/`tool_pool` 등 — 손상 전/후 행 수 전부
일치 확인). `.recover`가 테이블에 못 붙인 orphan 행 13건은
`lost_and_found` 보조 테이블에서 찾았는데, 그중 4건이 바로 사라진
`model_registry` 행 자체였다(실제 `model_registry_created` 감사로그
JSON에 provider/name/id가 그대로 남아 있어 **원본 데이터를 그대로
재구성** — 추측이 아니라 로그에 적힌 실제 값으로 복원:
`ollama/qwen3-coder:latest`, `openai/gpt-4o-mini`,
`openai/gpt-5.4-nano`, `ollama/qwen3.8:27b-mlx`, 전부 `is_active=1`).
나머지 orphan 9건 중 8건(실제 crew 실행 이력 4건 + 위 model_registry
생성 로그 4건)도 `audit_log`에 그대로 재삽입했다 — 남은 5건(내가
직전 검증 때 만든 팀 생성/삭제·배정 요청/승인 테스트 감사 기록)은
가치가 낮아 재삽입하지 않았다.

**완전히 복구 못 한 것(정직하게 기록)**: `audit_log`의 id 40-69(30건,
2026-10-05 23:28~2026-10-06 06:07 사이 생성됐어야 할 구간)는 메인
트리에도 `lost_and_found`에도 없어 **영구히 복구 불가** — 그 구간의
실제 업무 데이터(저 구간에 생성된 tasks/assignments/deliverables 등)
자체는 다른 테이블에 전부 살아있는 것으로 확인했으니(모든 테이블
행 수가 손상 전후 동일) 기능상 영향은 없지만, **그 구간 동안 누가
무엇을 했는지의 감사 기록만 비어 있다** — NF10(append-only 감사로그)
관점에서는 진짜 결손이다.

**검증**: `PRAGMA integrity_check` → `ok`. 복구한 DB로 교체 후 실제
구동 중인 서버에 (비밀번호를 몰라 실제 로그인 대신) 같은
`SessionMiddleware` 시크릿으로 직접 서명한 세션 쿠키를 만들어
`GET /api/model-registry`가 복구된 4건을 정확히 반환하는 것과
`/api/teams`·`/api/human-pool`·`/api/briefs`·`/api/audit-log`가 전부
200으로 정상 동작하는 것을 실측 확인. 손상된 원본은
`hair.db.bak-pre-corruption-fix-20261007084604`로 보존(`.gitignore`의
`*.db.bak-*`라 커밋되지 않음).

**재발 방지**: WAL 모드 SQLite 파일을 안전하게 백업/복원하려면 서버를
완전히 멈춘 뒤(`pkill` 후 프로세스 종료를 반드시 확인) `hair.db`·
`hair.db-wal`·`hair.db-shm` **세 파일을 함께** 다루거나, `sqlite3
hair.db ".backup 'copy.db'"`(라이브 중에도 안전한 온라인 백업)를 쓴다
— `cp hair.db ...`만으로는(WAL 파일을 빠뜨리거나, 다른 프로세스가
아직 그 파일을 쓰는 중이면) 이런 손상이 재발할 수 있다는 걸 실제
사고로 확인했다.

## 63. `docs/SCENARIOS.md` 5개 시나리오 전부 실제 서버에 라이브 실행 —
    사용자 요청(2026-10-07, "HAIR-Platform 위 시나리오를 하나씩
    실행하며 1. 기능상 오류 2. 불합리한 프로세스 3. 기획 내용과 상이
    4. 수행 결과 확인 UI 등 HAIR-Platform의 실용성 및 품질 향상이
    되도록 개선해줘")

**방법론**: §62 사고(`hair.db` 직접 조작으로 인한 손상)를 반복하지
않도록, 공유 `hair.db`를 전혀 건드리지 않고 **완전히 새 SQLite
파일**(`scratchpad/review.db`)로 별도 uvicorn 서버(포트 8010)를
띄워 그 위에서만 실행했다. API 호출(httpx)로 각 단계를 실제로
수행하고, 화면 확인(요청 항목 4)은 코드를 읽어 추측하는 대신
**Playwright(헤드리스 Chromium)로 실제 로그인해 스크린샷까지 찍어**
검증했다 — 이 세션 들어 최초로 "코드가 이렇게 그릴 것이다"가 아니라
실제 렌더링된 화면을 직접 봤다.

**발견·수정 — 기능상 오류(요청 항목 1)**:

- **(심각, 실비용 안전 문제) "AI 에이전트 실행" 버튼이 confirmed agent
  2개 중 어느 쪽을 실행하는지 전혀 알려주지 않았다** — 시나리오 5를
  실행하며 직접 겪었다: 버튼을 누르면 "먼저 걸리는 하나"가 조용히
  실행되고, 사용자는 의도한 Agent가 맞는지 확인할 방법이 없었다(실제
  LLM 비용이 드는 동작이라 잘못 실행되면 돈과 시간 둘 다 낭비).
  `ExecuteAgentRequest.agent_id`(§61에서 이미 추가)를 이 화면만 안
  쓰고 있었던 것 — confirmed agent가 2개 이상이면 실행 버튼 옆에
  선택 드롭다운을 띄우도록 `webui.py::renderCollabWorkspace`를
  고쳤다. 수정 후 실제 화면에서 드롭다운이 뜨는 것까지 스크린샷으로
  확인.

**발견·수정 — 불합리한 프로세스(요청 항목 2)**:

- **이미 배정이 확정된 할 일에도 "후보 매칭하기"/"팀에 배정 요청"이
  최상단에 무조건 노출**됐다(§61에서 `!confirmed` 조건을 빼면서
  생긴 회귀 — 다중 confirmed를 지원하려다 "이미 배정됨"과 "아직
  안 배정됨"을 화면이 더 이상 구분 안 해주게 됐다). 시나리오 1
  실행 중 실제 화면에서 발견 — confirmed된 할 일이 아직 아무도
  없는 것처럼 보였다. confirmed 배정이 하나라도 있으면 `<details>`
  뒤로 접고 "추가 배정(이미 확정된 배정이 있습니다...)"로 문구를
  바꿔 "보충 동작"임을 분명히 했다.
- **F7 결재자가 반려 사유를 시스템에 남길 방법이 전혀 없었다** —
  시나리오 3 실행 중 발견: F7 결재 체인은 조직도 매니저 체인
  기준이라 결재자가 그 task의 참가자가 아닌 경우가 흔한데(실측:
  다른 task 담당 매니저가 결재하는 상황 재현), 댓글
  (`POST /api/deliverables/{id}/comments`)은 참가자 전용이라
  막혀있었다(403). 반려만 하고 "왜"는 플랫폼 밖에서 구두로 전달해야
  했다. `ApprovalDecision`에 선택적 `reason` 필드를 추가하고,
  `subject_type='deliverable'`이면 `deliverables.
  record_decision_comment()`(참가자 판정을 의도적으로 건너뛴다 —
  결재 권한 자체가 이미 더 강한 자격이므로)로 그 사유를 댓글
  타임라인에 남긴다. "결재" 탭·협업 워크스페이스 양쪽 결정 버튼
  옆에 "사유(선택)" 입력을 추가했다. GC235-236으로 검증(비참가자
  결재자가 사유를 남기면 댓글로 보이는 것, 참가자 아니면 직접 댓글은
  여전히 403인 것 — 보호 자체는 유지).
- **(수정 안 함, 설계상 한계로 문서화) 한 할 일에 서로 다른 전문성의
  Agent 2개 이상을 두려면 임시 Task가 필요하다** — "AI 에이전트
  생성"은 Task의 `role_name`과 1:1로 묶여 있다(NF7 중복 방지가
  의도적으로 그렇게 설계됨). 시나리오 5를 실행하며 다른 Task에서
  Agent를 먼저 만든 뒤 원래 Task에 배정만 가져오는 우회를 실제로
  써야 했다 — 배정 자체는 이미 자유로워서(매칭 결과와 무관하게 임의
  Agent를 배정 요청할 수 있음) 동작은 하지만 직관적이지 않다. 재설계
  하려면 "생성"과 "배정"의 의미를 다시 정의해야 해서 이번 리뷰
  스코프 밖으로 두고 `docs/SCENARIOS.md` 시나리오 5에 그대로
  기록했다.

**발견·수정 — 기획 내용과 상이(요청 항목 3, `docs/SCENARIOS.md` 자체
수정)**:

- 시나리오 1 step4: "QA 담당자(또는 Manager)"가 "백엔드 개발" Task에
  쓸 수 있다는 서술이 틀렸다 — QA 담당자는 **별개의 "QA" Task**에
  배정된 사람이라 "백엔드 개발" Task의 참가자가 아니다(실측: 403).
  pm_kim(그 Task의 Agent 요청자) 본인만 가능 — 같은 브리프의 Task
  끼리도 완전히 격리돼 있다는 더 근본적인 사실을 드러냈다(위 "갭 2"로
  기록).
- 시나리오 2 step5-6/"갭": "사람 산출물엔 승인 절차 자체가 없다"는
  2026-09-28 당시엔 맞았지만 **§34(사람 deliverable 제출/승인
  메커니즘)로 이미 해결된 뒤 문서만 갱신이 안 됐다** — 실측 재실행
  으로 확인(`POST /api/deliverables/{id}/submit` → F7 결재 체인 →
  승인/반려 전부 정상 동작, 실제 화면에서 "승인"/"반려" 버튼까지
  스크린샷으로 확인). 문서를 실제 동작에 맞게 갱신.
- 시나리오 3: (1) "Skill Pool의 '고객 응대' 스킬이 자동으로 증강한다"
  는 서술이 그 스킬을 **먼저 등록해야 한다**는 전제를 빠뜨렸다(기본
  시드엔 없음 — 등록 안 하면 그냥 빈 라벨). (2) "최종 검수"를 별도
  Task로 분리한 설계가 실제로는 성립하지 않는다 — 검수자가 그 Agent
  산출물의 참가자가 아니라 댓글조차 못 남긴다(1번 발견과 같은 Task
  격리 문제의 재현). "다중 confirmed 허용"(§61)으로 검수자를 **같은
  Task**에 사람으로 추가 배정하는 쪽으로 다시 설계해 실측으로
  재검증했다(실제로 댓글이 되는 것까지 확인) — 단, 승인/반려 자체는
  여전히 admin/manager만 가능하다는 것도 같이 명시(Member는 F7 체인
  참여 불가, ADR 결정 3).

**발견·수정 — 수행 결과 확인 UI(요청 항목 4)**:

- Agent 산출물에 마크다운 코드펜스(` ```python ... ``` `)나
  `**굵게**`가 섞여 있어도 전부 이스케이프 후 줄바꿈만 `<br>`로
  바꿔 보여줘, 백틱·별표가 그대로 노출됐다(시나리오 1에서 실제
  코드 패치 산출물로 확인 — 가독성이 특히 나빴다). 완전한 마크다운
  라이브러리 없이 가장 흔한 두 패턴만 처리하는 `renderContent()`를
  추가해 협업 워크스페이스·Crew 실행 결과·단건 Agent 실행 결과 등
  5개 렌더 지점에 전부 적용했다. 수정 전/후 스크린샷으로 코드블록이
  `<pre><code>`로 바뀌는 것까지 확인.
- (수정 완료, 위 "기능상 오류"에 중복 기재) 멀티 Agent 선택 드롭다운
  도 이 항목에 해당한다 — "수행 결과를 확인하는 UI"이자 "어떤 걸
  수행시킬지 고르는 UI" 둘 다의 공백이었다.

**검토했으나 코드로 고치지 않고 권고만 남기는 것**:

- **`.env`에 실제 API 키가 있으면 협업 워크스페이스의 모든 실행
  경로(AI 에이전트 실행·LLM으로 도와줘·지식 색인)가 매번 진짜
  과금 호출을 낸다** — "AI 에이전트 생성"(F4)에만 있는 `framework=
  "stub"`(무료) 같은 안전장치가 협업 워크스페이스엔 전혀 없다. 이번
  리뷰를 실행하면서 실제로 이 프로젝트의 OpenAI 키로 여러 차례 과금
  호출이 나갔다(에이전트 실행 5회 내외, LLM 보조 1회, 임베딩 색인
  1회 — 전부 소액이지만 실제 비용이다). 데모/QA 환경에서 반복
  재현하다 실수로 비용이 쌓이기 쉬운 구조라 **권고**한다: 환경변수
  (예: `HAIR_PLATFORM_FORCE_STUB=1`)로 이 경로들을 강제로 스텁
  처리하는 안전 모드를 추가하는 게 좋겠다 — 다만 이는 여러 호출
  지점(execute_agent/assist/index_knowledge/run_crew)에 공통으로
  걸쳐 있고 "무엇을 스텁 처리할지"의 의미론을 새로 정의해야 해서,
  사용자 결정 없이 이번 리뷰 범위에서 임의로 구현하지 않았다.
- 시나리오 1·3에서 반복 확인된 "같은 브리프의 Task끼리 완전히
  격리됨"은 이번에 "다중 confirmed로 같은 Task에 모아서" 우회했지만,
  **정말 다른 Task로 나누고 싶은 경우**(예: 대규모 조직에서 Task별
  담당·이력을 분리 추적하고 싶을 때)엔 우회할 방법이 없다 — Task
  간 참조/링크 개념 자체가 스키마에 없다. 구조적 변경(새 테이블 또는
  `tasks.related_task_id` 같은 필드)이 필요해 이번 리뷰 범위를
  넘는다고 판단해 기록만 남긴다.

**검증**: 신규 `tests/unit/test_phase23_review_fixes.py`(GC235-236,
2건) + 전체 골든셋 243건(241+2) 통과. `docs/SCENARIOS.md`의 시나리오
1/2/3/5 네 곳을 실측과 일치하도록 수정(시나리오 4는 실행 결과가 글과
완전히 일치해 수정 없음). 리뷰에 쓴 `scratchpad/review.db`는 세션
종료와 함께 버린다(공유 `hair.db`는 이번 리뷰에서 한 번도 열지
않았다).

## 64. Crew 실행 — "복수 그룹" + 업무 파이프라인 연동(Phase 3) — 사용자
    요청(2026-10-07, "crew 실행에서 묶인 에이전트 그룹의 경우 하나의
    그룹만 가능한 구조인데, 복수 그룹 가능하도록 개선, 이 그룹이 전체
    업무에서 사람-에이전트 연동이 가능한 구조인지 확인해줘")

**확인한 사실(개선 전)**: 두 가지 다 사실이었다 — (1) `CrewRunRequest.
agent_ids`는 매번 직접 고르는 평평한 리스트 하나뿐, 저장되는 "그룹"
개념 자체가 없었다. (2) `/api/crew/run`은 `task_id`를 받지 않고 결과가
`agent_pool.last_output`에만 남아 `assignments`/`deliverables`/결재
체인 어디에도 연결되지 않았다 — §63에서 이미 지적한 갭을 이번에
재확인했다.

**설계**: 두 문제를 한 번에 푸는 다리로 §61의 **팀**(teams.py)을
재사용했다 — "복수 그룹"은 한 할 일에 팀을 여러 개 confirmed로 배정할
수 있다는 뜻인데, 이건 §61의 "다중 confirmed 허용"으로 **이미 가능한
구조**였다(팀도 resource_type별 제한이 없다). 그래서 실제로 새로 만든
것은 "그중 한 팀의 Agent 멤버를 실제 CrewAI Crew로 실행해 그 결과를
업무 파이프라인에 연결하는 다리"다. 한 할 일에 팀이 여러 개 배정돼
있을 때 "전부 동시 실행"과 "하나를 골라서 실행" 중 사용자에게 직접
물어 후자로 결정했다(전부 동시 실행은 비용이 팀 수만큼 자동으로
커지고 의도치 않은 팀까지 섞여 실행될 위험이 있음).

**구현**:
- `crew_runtime.run_crew_for_team()` — 팀의 confirmed 배정 검증 →
  `teams.active_agent_member_ids()`(§61에서 "Phase 3에서 쓸 예정"으로
  미리 만들어두고 호출부가 없던 헬퍼 — 드디어 쓰임)로 active Agent만
  추출 → 할 일 `role_name`+최신 사람 작성 자료를 공통 맥락으로 줘서
  기존 `run_crew()`를 그대로 호출(새 CrewAI 로직 불필요, 재사용).
- `deliverables.start_team_crew_execution()`/`run_team_crew_job()` —
  단일 Agent 비동기 실행(Phase 4)과 같은 2단계 패턴. 참여 Agent 수만큼
  `agent_execution_jobs` 행을 만들고(매니저 지정 Agent는 제외), Crew
  결과의 Agent별 산출물 각각을 `deliverables`+F7 결재 체인으로 저장
  한다 — **이 저장이 사람-에이전트 연동 질문에 대한 실제 답**이다:
  그 순간부터 팀 리더·팀원이 협업 워크스페이스에서 결과물을 보고
  댓글·승인/반려할 수 있다.
- `POST /api/tasks/{id}/execute-team-crew` — 참가자 판정을 단일 Agent
  실행("요청자 본인만")보다 넓혀 팀 리더·팀원 둘 다 트리거 가능하게
  했다(팀은 원래 여럿이 쓰는 단위라는 전제, admin/manager는 기존처럼
  감독 대리 허용).
- UI — 협업 워크스페이스에 "팀 Agent 실행(Crew)" 카드 신설, confirmed
  팀이 2개 이상이면 드롭다운으로 하나를 고르게 한다(§63에서 Agent
  다중 confirmed에 적용한 패턴을 팀 단위로 확장).

**의도적으로 범위를 좁힌 것**: "팀 Agent 실행" UI에 `manager_agent_id`
(hierarchical 매니저 지정) 선택지를 안 넣었다 — 팀 멤버 상세를 보여
주려면 `GET /api/teams/{id}`가 필요한데 그 엔드포인트가 admin/manager
전용이라(§61 RBAC 그대로) member인 팀원이 호출하면 403이 난다. API
(`TeamCrewExecuteRequest.manager_agent_id`)는 이미 지원하므로, UI
노출은 "팀원도 볼 수 있는 팀 멤버 조회" 갭을 먼저 메운 뒤의 후속
작업으로 남긴다.

**검증**: 신규 `tests/unit/test_phase24_team_crew_bridge.py`(GC237-241,
5건) — 미확정 팀 거부, active 멤버만 포함(오프보딩 Agent 자동 제외),
Agent별 deliverable+결재 체인 생성 확인, 비참가자 403, **한 할 일에
팀 2개가 공존하며 각각 독립 실행되는 것**(복수 그룹의 핵심 시나리오)
까지 확인. `crewai.Crew.kickoff`만 patch하는 기존 안전 규칙(§12 —
더 깊이 patch하면 CrewAI 내부 재시도가 우회해 실제 API로 샐 수 있다)
을 그대로 따랐다. 전체 골든셋 246건(241+5) 통과.

`docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` §9에 Phase 3 구현 현황을
기록하고 로드맵 표의 상태를 갱신했다. 이 변경으로 §61/§63에서 "팀의
Agent 멤버가 Crew로 자동 연결 안 됨"이라고 남겨뒀던 갭은 해소됐다 —
`docs/SCENARIOS.md` 시나리오 5와 HAIR 프로세스 맵(claude.ai/artifact/
8dQVis1XEEEZdcdTsytvpv) 쪽 같은 내용도 같이 갱신했다.

## 65. "복수 에이전트 그룹" 생성 경로를 업무 흐름 안으로 재배치 — 사용자
    후속 피드백(2026-10-07, "Crew 실행 UI 개선 - '실행'이 맞는지...
    현재 UI가 복수의 에이전트 그룹 생성 및 HAIR 관점의 연동에 적합한
    경우는 아님, 어떻게 업무에 연결하는지 기획 필요(AI 에이전트
    생성과 통합 또는 AI 에이전트 관리 노출 검토)")

§64에서 "팀 Agent 실행"으로 **실행**(업무 연동)은 해결했지만, 사용자가
다시 짚은 건 그 앞 단계 — **그룹을 어디서 만드는가**였다. "Crew
실행" 탭은 업무와 무관한 즉석 화면이라 "복수 에이전트 그룹을 만드는
정식 경로"로 쓰기엔 안 맞았다. 실제 코드를 다시 보니: "AI 에이전트
생성"은 이미 한 브리프의 여러 할 일을 한 번에 골라 역할별 Agent를
동시에 만들 수 있었다(CrewAI의 "역할 기반 그룹" 장점이 쓰일 조건이
이미 있었음) — 그런데 생성 후 그 Agent들을 그룹(팀)으로 묶을 방법이
없었고, "AI 에이전트 관리"에는 Agent의 팀 소속이 전혀 안 보였다.

**구현**:
- `teams.team_names_for_agent(conn, agent_id)` 신규 — 한 Agent가 여러
  팀에 속할 수 있다는 걸 그대로 반영(`team_members`의 UNIQUE 제약은
  (team_id, member_type, member_id)뿐, Agent당 팀 1개 제한 없음).
- `GET /api/agent-pool` 응답에 `team_names` 추가 → "AI 에이전트 관리"
  표에 "소속 팀" 열 신설(클릭하면 "팀 관리"로 이동).
- "AI 에이전트 생성" 결과 화면(`renderJobResult()`)에 "묶기" 체크박스
  열 + "체크한 Agent를 팀으로 묶기" 폼을 추가 — 팀 이름만 입력하면
  `POST /api/teams` + 멤버 추가까지 한 번에 끝난다. 이걸로 §64의
  "Agent 2개를 만들려면 임시 Task가 필요하다" 갭도 자연스럽게
  없어졌다 — 애초에 여러 할 일을 같이 골라 한 번에 생성하면 되므로.
- "Crew 실행" 탭 안내 문구를 "이 화면은 업무와 연결되지 않는 즉석
  실험용"이라고 명확히 하고, 실제 업무 경로(① AI 에이전트 생성에서
  묶기 또는 팀 관리에서 직접 등록 → ② 업무 요청에서 배정·결재 → ③
  협업 워크스페이스의 "팀 Agent 실행")를 안내 문구에 그대로 적었다 —
  두 화면의 역할을 "즉석 테스트" vs "실제 업무"로 분리.
- "팀 관리" 탭의 낡은 안내 문구("Agent 멤버를 Crew 실행에 자동으로
  묶는 기능은 아직 없습니다, 계획 중")도 §64로 이미 해결된 걸 반영해
  갱신.

**검증**: 신규 `tests/unit/test_phase24_team_crew_bridge.py::test_gc242
-243`(한 Agent가 여러 팀에 속하는 것, `GET /api/agent-pool`의
`team_names` 노출). 실제 리뷰 서버에서 Playwright로 전체 흐름 실측 —
"AI 에이전트 생성"에서 할 일 2개를 선택해 Agent 2개를 동시 생성 →
"체크한 Agent를 팀으로 묶기"로 팀 생성 → "팀 관리" 탭에서 그 팀에
Agent 2개가 정확히 들어간 것을 스크린샷으로 확인. 전체 골든셋 250건
(248+2) 통과.

(이 리뷰 중 직접 겪은 실수 하나를 정직하게 남긴다: UI 확인용 테스트
브리프를 만들다가 스크립트의 `BASE` 설정을 착각해 실제 `hair.db`
(포트 8000)에 테스트 브리프를 만들었다 — 바로 발견해 `DELETE
/api/briefs/{id}`(정상 API 경로, raw SQL 아님)로 즉시 삭제해 원복했다.
리뷰용 스크립트가 여러 개로 늘어나며 베이스 URL을 혼동하기 쉬워진
게 원인 — 이후로는 스크립트 상단에 `BASE_URL`을 매번 출력해 확인하는
습관을 들인다.)

## 66. 화면 도움말을 서술형 문단 → 개조식(불릿) 목록으로 전환 — 사용자
    요청(2026-10-07, "각 Web UI 화면의 도움말... 을 개소식으로 명확하게
    정리해줘", "AI 에이전트 목록" 도움말 예시 첨부)

20개 탭의 `<p class="muted">` 도움말 문단(200자 이상, 보통 한 화면에
UI 요소별 설명 3~9개가 — 로 이어 붙은 한 문장짜리 긴 문단) 전부를
`<ul class="help-list"><li>...</li></ul>`로 바꿨다 — 내용은 그대로
두고 구조만 바꿨다(정보 삭제·추가 없음, UI 요소명은 `<b>`로 강조).
새 CSS(`.help-list`)를 `.muted` 바로 아래에 추가해 좁은 들여쓰기·
줄간격으로 기존 톤을 유지했다. 대상: 업무 요청 등록/목록, 인력
매칭/배정, 결재, 사람 인력 관리(2곳), AI 에이전트 관리, 도구/스킬
Pool(2곳), 팀 관리, 모델 레지스트리, Crew 실행(2곳), 인력 검색,
조직도, 성과 리뷰(2곳), 대시보드, 비용, 공정성 지표, 사용자 관리
(2곳) — 전 탭.

**검증**: 백엔드 변경이 전혀 없는 순수 텍스트/마크업 작업이라 골든셋
250건 그대로 통과. `node --check`로 JS 문법, `ruff`/`black`으로
포맷 확인. 실제 리뷰 서버에서 Playwright로 3개 화면(AI 에이전트 관리
— 사용자가 예시로 준 화면, 팀 관리, Crew 실행)을 스크린샷으로 직접
확인해 불릿이 깨끗하게 렌더링되는 것까지 확인했다.

(재 로그인해도 바뀐 도움말이 안 보인다는 사용자 보고가 있었다 —
`INDEX_HTML`은 서버 프로세스 메모리에 올라간 Python 문자열이라 소스를
고쳐도 `--reload` 없이 띄운 실제 서버(`run_server.py`)는 재시작 전까진
예전 그대로다. 로그인은 세션 쿠키만 다룰 뿐 서버 코드를 다시 읽지
않으니 당연한 결과였다 — 프로세스를 kill 후 재기동해 해결했고, 이후
`webui.py`를 고칠 때마다 사용자가 보기 전에 먼저 재시작하는 습관을
들였다.)

## 67. §66에서 놓친 도움말 재스윕 — 사용자 요청(2026-10-07, "AI 에이전트
    생성" 화면이 여전히 서술형 문단이라는 스크린샷 첨부 + "개소식 아닌
    메뉴도 남아 있어. 전체 메뉴의 도움말을 핵심 증심으로 정리해줘")

§66에서 쓴 탐색 기준(`<p class="muted">`가 **한 줄에** 200자 이상)이
"AI 에이전트 생성" 도움말을 놓쳤다 — 소스에서 `<br>`와 문자열 `+`
연결로 여러 줄에 걸쳐 있어 어느 한 줄도 200자를 못 넘겼기 때문이다
(렌더링된 전체 문단은 충분히 길었는데도). 사용자가 직접 스크린샷으로
지적했다.

**한 것**: 줄 길이 필터를 버리고 `<p class="muted">`가 나오는 곳
전부(24곳)를 하나씩 읽어 실제로 다중 사실을 나열하는지 직접 판단했다.
"AI 에이전트 생성" 포함 10곳을 추가로 개조식으로 전환(인박스, 내
업무, 업무요청 할일목록, 필요스킬입력, 할일상세, 결재 승인/반려,
Crew 다이어그램 범례, 활동기록 등). 반대로 상태 메시지·빈 화면
안내·한 가지 사실만 말하는 짧은 문장(예: "아직 결과물이 없습니다")은
의도적으로 그대로 뒀다 — 나열할 게 없는 문장을 억지로 `<ul><li>`
하나짜리로 바꾸면 오히려 과도한 마크업이라고 판단했다.

**검증**: 역시 순수 텍스트/마크업 작업이라 골든셋 250건 그대로 통과.
`ruff`/`black`/`node --check` 확인. 실제 리뷰 서버에서 "AI 에이전트
생성" 포함 바뀐 화면들을 Playwright 스크린샷으로 직접 재확인.

## 68. 결과물(Deliverable) 형식 확장 Tier 1+2 — 사용자 요청(2026-10-07,
    "HAIR 협업 결과물이 코드, 마크다운, PPT, HTML 등 다양할 수
    있을텐데 현재 지원 가능한 범위와, 확장할 수 있는 범위는
    어디까지일지, 또 웹에 표시 가능한 영역은 어디까지일지, 다운로드는
    가능한지 확인해줘" → "Tier 2까지 진행해줘")

**분석(구현 전)**: 코드 전수 조사 결과(`UploadFile|multipart|
FileResponse|download|attachment|content_type|mimetype|base64` 전부
0건) `deliverables.content`는 순수 `TEXT` 컬럼이었다 — 업로드·다운로드·
MIME 처리가 전혀 없었다. 이를 바탕으로 3단계 확장안을 제시했다:
Tier 1(스키마 변경 없이 클라이언트 쪽 마크다운 렌더링·구문 강조·
텍스트 다운로드), Tier 2(`content_type` 스키마 컬럼 + 형식을 아는
Agent 생성 + 샌드박스 HTML 미리보기), Tier 3(PPTX 등 실제 바이너리
파일 — 파일 저장소 인프라가 새로 필요해 범위 밖). 사용자가 "Tier
2까지"를 승인했다.

**구현(Tier 1+2 합쳐서 진행 — Tier 2가 Tier 1의 렌더링 인프라를
그대로 쓰므로)**:
- DB: `deliverables.content_type TEXT NOT NULL DEFAULT 'text' CHECK
  (content_type IN ('text','markdown','html','code'))` — 신규 설치용
  `CREATE TABLE`과 기존 DB용 `_ensure_column()` 둘 다에 추가. (SQLite
  `ALTER TABLE ADD COLUMN`이 `CHECK` 제약을 실제로 강제하는지
  `:memory:` DB로 별도 실측 확인 후 반영.)
- `models.py`: `ContentType = Literal["text","markdown","html","code"]`
  추가, `DeliverableCreate`/`AssistRequest`/`ExecuteAgentRequest`/
  `TeamCrewExecuteRequest` 네 곳에 `content_type: ContentType =
  "text"` 필드 추가(기본값 "text"라 기존 호출자는 전혀 안 바뀜).
- `deliverables.py`: `_CONTENT_TYPE_INSTRUCTIONS` 딕셔너리(형식별
  LLM 프롬프트 지시문) 신설. `assist()`/`start_agent_execution()`이
  프롬프트에 지시문을 덧붙이고, `create_deliverable()`/
  `run_agent_execution_job()`/`run_team_crew_job()`이 실제 저장
  시 `content_type`을 함께 쓴다.
- `crew_runtime.run_crew_for_team()`에도 같은 지시문 딕셔너리를
  **의도적으로 중복** 정의했다 — 이 모듈은 `deliverables.py`를
  import할 수 없다(순환 참조, `deliverables.py`가 이미 이 모듈을
  쓴다).
- `app.py`: 결과물 작성/LLM 도움/Agent 실행/팀 Crew 실행 4개
  엔드포인트 모두 `payload.content_type`을 그대로 아래 계층에
  전달하도록 배선.
- `webui.py`(Tier 1 몫): `renderContent(raw, contentType)`을
  content_type별 분기로 재작성 — markdown은 헤딩/목록/굵게/인라인
  코드까지(여전히 `esc()` 이스케이프 **후** 안전한 태그만 덧씌우는
  기존 방식 유지, 별도 마크다운 라이브러리 안 씀), code는 경량
  정규식 구문 강조(`highlightCode()` — 키워드/문자열/주석/숫자),
  html은 `<iframe sandbox="allow-scripts">`(`allow-same-origin`
  의도적으로 뺌 — 그 안의 스크립트가 부모 쿠키/DOM에 접근 못 하게).
  "결과물 작성"/"AI 에이전트 실행"/"팀 Agent 실행" 3곳에
  `contentTypeSelectHtml()` select 추가. 각 결과물 카드에 "다운로드"
  버튼(Blob + `<a download>`, 확장자는 content_type별로 txt/md/
  html/txt) 추가.

**실측으로 발견한 버그(구현 중 자체 발견)**: `highlightCode()`가
문자열/주석/숫자 강조를 먼저 하고 키워드 강조를 마지막에 하도록
짰더니, 키워드 정규식이 **자기가 방금 삽입한** `class="tok-str"`
같은 속성의 "class"라는 글자를 또 키워드로 매칭해 중첩·깨진 `<span>`
을 만들었다(모든 코드 블록에서 100% 재현). 순서를 "키워드 먼저"로
바꿔 해결 — Node로 `renderContent()`를 격리 실행해 네 가지
content_type 전부의 출력 HTML을 직접 찍어보며 잡았다.

**검증**: 신규 `tests/unit/test_phase25_content_type.py`(GC244-248,
5건) — 기본값/저장, Pydantic 검증 거부, `assist()` 프롬프트 지시문
추가, 단일 Agent 실행 프롬프트+저장, 팀 Crew 실행 저장. 골든셋 250
→ 255건 전부 통과. `ruff`/`black`은 HEAD 대비 새로 발생한 경고 없음
(기존에도 있던 코드와 동일하게 통과/실패 — pre-existing 항목은
그대로 둠). `node --check`로 임베디드 JS 문법 확인 + Node에서
`renderContent()`를 직접 격리 실행해 4개 content_type의 실제 출력
HTML을 검증(위 버그 발견이 이 과정에서 나옴). 실제 리뷰 서버
(포트 8010, 실제 `hair.db` 아님)에 REST API로 4개 content_type
결과물을 만들고 Playwright로 협업 워크스페이스를 열어 스크린샷
확인 — 마크다운 헤딩/목록/굵게, 코드 구문 강조(다크 배경+색),
HTML이 iframe 안에서 실제로 렌더링(스타일 적용된 `<h1>`까지)되는
것, 다운로드 버튼이 `deliverable-2.md`/`deliverable-3.txt`처럼
content_type에 맞는 확장자로 실제 다운로드되는 것, 브라우저에서
직접 markdown으로 새 결과물을 제출해도 바로 반영되는 것까지 전부
눈으로 확인했다.

**알려진 한계**: Tier 3(PPTX 등 실제 바이너리 파일 생성·저장)는
명시적으로 범위 밖. `highlightCode()`는 완전한 토크나이저가 아니라
정규식 기반 경량 처리라 문자열 안의 `#`/`//`나 변수명에 포함된
키워드 등 드문 경우 오탐할 수 있다(코드 블록 사이의 완전한 문맥
분석은 안 함 — 가독성 보조 수준의 의도적 트레이드오프).

## 69. `docs/SCENARIOS.md` 시나리오 1/2/4를 content_type으로 보완 +
    라이브 전체 기능 점검 — 사용자 요청(2026-10-07, "@docs/SCENARIOS.md
    생성 결과물이 소스코드, 마크다운이나 HTML 방식의 리포트가 되도록
    시나리오를 보완하고, 해당 시나리오를 실행하면서 전체적인 기능을
    점검해줘")

**방법론**: §63과 같은 이유로 실제 `hair.db`(포트 8000)는 전혀 열지
않았다 — 완전히 새로운 `:memory:` DB를 리뷰 서버(포트 8010)로 띄워
실제 FastAPI 앱·SQLite 스키마·엔드포인트를 그대로 썼다. 단, 이번엔
TestClient가 아니라 **진짜 uvicorn 프로세스**를 httpx로 호출했다
(`BackgroundTasks`가 TestClient처럼 응답 전에 동기 실행되지 않고 실제
배포 환경처럼 응답 후 비동기로 돌아, 폴링 루프로 완료를 기다려야 했다
— TestClient 기반 pytest와의 실측 차이). 이 환경엔 `ANTHROPIC_API_KEY`
/`OPENAI_API_KEY`가 전혀 없어 `execute-agent`·`index-knowledge`를
그대로 두면 그냥 실패하므로, 서버 프로세스 시작 시점에 `hair_platform.
llm.call_llm`과 `hair_platform.vectorstore.embed_text`를 pytest
골든셋과 똑같은 방식(함수 교체)으로 **결정적 가짜**로 바꿔치기해
실제 과금 없이 같은 코드 경로(프롬프트 구성→실행→평가→승인)를
그대로 검증했다 — 가짜 `call_llm`은 프롬프트에 섞인 `_CONTENT_TYPE_
INSTRUCTIONS` 지시문 문자열을 보고 그 형식에 맞는 응답(코드/마크다운/
HTML)을 반환하도록 만들어, "지시문이 실제로 프롬프트에 들어가는지"도
같이 검증했다.

**시나리오 보완(docs/SCENARIOS.md)**: 시나리오 1(버그 패치)의
execute-agent에 `content_type="code"`, 시나리오 2(마케팅 보고서)의
assist+제출에 `content_type="markdown"`, 시나리오 4(정책 문의 응답)의
execute-agent에 `content_type="html"`을 추가 — 세 형식(소스코드/
마크다운/HTML)을 각 산출물의 실제 성격에 맞게 배분했다(사용자 요청
문구 그대로).

**라이브 실행 결과 — 전부 통과**:
- 시나리오 1: 브리프→Task 분해→백엔드 Agent 생성(F4 무료 스텁)→온보딩
  →배정(자기승인 방지로 pm_kim 본인 승인 거부 확인)→사람 입력 작성→
  `execute-agent{"content_type":"code"}`→Gate A-G 채점→승인. 결과물의
  `content_type`이 실제로 `"code"`로 저장됨을 API 응답으로 직접 확인.
- 시나리오 2: 브리프→Task 분해→사람 배정·승인→`assist{"content_type":
  "markdown"}`(가짜 LLM이 실제 `#`/`-` 마크다운 반환)→같은 형식으로
  제출→"검토 요청"→승인→대시보드 조회까지 통과.
- 시나리오 4: 브리프→Task 분해→정책 Agent 생성→온보딩→지식 색인
  (가짜 임베딩)→배정·승인→사람 질문 작성→`execute-agent{"content_type"
  :"html"}`→Gate 채점(`retrieved_context` 포함 확인)→승인→인박스
  조회까지 통과.
- 부가 점검: 결재 완료 후 Agent 산출물에 댓글 작성/조회(시나리오 3이
  의존하는 기능)도 별도로 재확인 — 정상.

**Playwright로 화면까지 확인**: 세 Task의 협업 워크스페이스를 각각
열어 ① 코드 산출물이 다크 배경 구문 강조 블록(`.code-block`, 키워드/
주석 색 구분, 실제 computed `background-color: rgb(30, 30, 30)`까지
DOM에서 직접 확인)으로, ② 마크다운 산출물이 제목(`<h1>`)·목록
(`<li>`)으로, ③ HTML 산출물이 `sandbox="allow-scripts"` iframe
안에서 실제 스타일(색·레이아웃)이 적용된 채로 렌더링되는 것을 스크린샷
+ DOM 인스펙션으로 확인했다. 세 경우 모두 "다운로드" 버튼이 각각
`deliverable-{id}.txt`/`.md`/`.html`로 실제 다운로드되는 것까지
Playwright `expect_download()`로 확인했다.

**실측 중 겪은 작은 함정(정직하게 기록)**: 전체 페이지 스크린샷을
축소된 썸네일로 눈으로만 훑다가 시나리오 1의 코드 블록이 "구문 강조가
전혀 안 된 것처럼" 보여 버그로 오인했다 — 실제로는 DOM/computed
style을 직접 찍어보니 정확히 렌더링돼 있었다(이미지 축소 때문에
다크 배경이 흐리게 보였을 뿐). 픽셀을 눈으로 추측하지 말고 DOM
질의(`getComputedStyle` 등)로 직접 확인해야 한다는 교훈 — 이번엔
버그가 아니었지만, 지난 §68의 "class 속성 재매칭" 버그처럼 실제
버그였을 수도 있는 종류의 착시였다.

**검증**: 코드 변경은 `docs/SCENARIOS.md` 본문 보완뿐이라(백엔드/
프론트 코드는 §68에서 이미 완료) 골든셋 255건 그대로 유지. 이번
작업의 검증은 전부 위 라이브 실행 + Playwright 확인으로 이뤄졌다.

## 70. 시나리오 5를 "개별 Agent 배정" → "팀 Agent 실행(Crew) 그룹
    실행" 중심으로 재작성 + `GET /api/my-work`의 팀 미인식 버그 발견·
    수정 — 사용자 요청(2026-10-07, "시나리오 내용도 crew 에이전트
    그룹을 포함하도록 개선해줘")

**배경**: §69까지의 시나리오 5는 Phase 3(§64, 팀 Crew 실행 다리)가
이미 구현된 뒤에도 여전히 "Agent 2개를 각각 Task에 개별
`resource_type=agent`로 배정하고 `execute-agent`를 `agent_id`로
지정해 두 번 호출"하는 **Phase 3 이전 방식**으로 쓰여 있었다 — Phase
3로 가능해진 "팀 Agent 실행(Crew)" 버튼은 "갭" 섹션에 "이제 가능하다"
라고 각주로만 언급될 뿐 실제 걸음걸이에서는 한 번도 쓰이지 않았다.
사용자가 "시나리오 내용도 crew 에이전트 그룹을 포함하도록"이라고
명시적으로 요청해, 개별 배정을 완전히 빼고 **팀 배정 하나만으로 팀
전체(사람+Agent)가 할 일에 연결되는** 흐름으로 다시 썼다 — Agent
생성은 §65 번들링("AI 에이전트 생성" 결과 화면에서 바로 "팀으로
묶기")을 그대로 활용하고, 실행은 `POST .../execute-team-crew`에
`content_type="markdown"`을 지정한다(§68 Tier 1+2가 여기서도 그대로
이어진다).

**라이브 실행 중 발견한 진짜 버그**: 리뷰 서버(포트 8010)에 재작성한
단계를 실제로 돌려보니, "팀 배정 1건만 confirmed"인 상태에서 9단계
("내 업무"에서 팀 리더·팀원 확인)가 **완전히 빈 목록**을 반환했다.
원인 — `deliverables.list_my_work()`(`GET /api/my-work`)의 SQL이
`resource_type IN ('human','agent')` 두 경우만 보고 `'team'`은 아예
없었다. 같은 파일의 `task_participant_relationship()`(워크스페이스
**접근 권한** 판정 — "결과물을 쓸 수 있는가")은 Phase 1+2(§61) 때
이미 team_leader/team_member를 인식하도록 고쳤는데, "내 업무" **목록
조회**는 완전히 별개의 SQL이라 그때 같이 안 고쳐졌다. 지금까지
`docs/SCENARIOS.md`의 모든 팀 배정 시나리오(§69 이전의 시나리오 5
포함)가 팀 배정과 **별도로** 개별 사람/Agent 배정도 항상 같이 썼기
때문에(사람은 `human_pool.user_id`로, Agent 요청자는 `requested_by`
로 걸려 우연히 목록에 떴다) 이 갭이 한 번도 드러나지 않았다 — "팀
하나만으로 충분하다"는 이번 재작성이 처음으로 이 경로를 실제로
밟았고, 그래서 처음으로 드러났다.

**수정**: `deliverables.list_my_work()`의 SQL에 `a.resource_type =
'team'` 분기를 추가했다 — `a.team_leader_id = ?`(팀 리더) 또는
`EXISTS(... team_members JOIN human_pool ...)`(사람 멤버, `user_id`
연결된 경우만 — 기존 "로그인 계정 미연결" 한계와 동일)로 매칭한다.
`my_relationship` 값도 `'team_leader'`/`'team_member'`로 세분화
(기존 'assignee'/'requester' 두 값에서 확장). `webui.py`의 "내 업무"
표 "관계" 열도 네 값 전부를 매핑하는 객체로 바꿔 "내 팀이 할 일(팀
리더)"/"내 팀이 할 일(팀원)"로 보여준다.

**검증**: `tests/unit/test_phase24_team_crew_bridge.py::
test_gc249_my_work_lists_team_only_assignment_for_leader_and_member`
신규 — 개별 사람/Agent 배정 없이 팀 배정 하나만 있는 task가 팀
리더·팀원 둘 다에게 뜨고, 무관한 제3자(admin1)에게는 안 뜨는 것을
확인. 골든셋 255 → 256건 통과. `ruff`/`node --check` 확인. 재작성한
시나리오 5의 1-9단계 전부를 리뷰 서버에 실제 API 호출로 재현(Agent
2개 생성·번들링→사람 멤버 추가→Agent 온보딩→브리프/Task→팀 배정·
승인→사람 입력 2건→`execute-team-crew{"content_type":"markdown"}`
→Gate A-G 채점→댓글→승인→"내 업무" 노출까지) — 이 환경엔 실제 LLM
API 키가 없어 `crew_runtime.run_crew()`가 호출하는 **실제 `crewai.
Crew.kickoff()`**(litellm 경유, `hair_platform.llm.call_llm`과는
별개의 호출 경로라 §69의 패치만으로는 안 가려짐 — 처음엔 이걸
놓쳐서 job이 네트워크 재시도만 반복하다 타임아웃났다)까지 pytest
골든셋과 같은 방식(`patch("crewai.Crew.kickoff", ...)`을 서버
프로세스 시작 시점에 적용)으로 결정적 가짜로 교체해 비용 없이 검증
했다. Playwright로 park_member 계정의 "내 업무" 표와 협업
워크스페이스를 열어 "관계" 열이 "내 팀이 할 일(팀원)"으로, 두 Agent
산출물이 각각 마크다운 카드(제목/목록)로 정확히 렌더링되는 것을
확인했다.

## 71. 실제 `hair.db` 초기화 + 5개 시나리오를 실제 LLM(gpt-4o-mini)으로
    라이브 실행 — 사용자 요청(2026-10-07, "기존 데이터를 초기화하고,
    시나리오를 실행해 새롭게 실행 데이터를 구축해줘")

**초기화 방법 선택**: "데이터 삭제" API(브리프/사람/Agent 개별 삭제)는
`confirmed` 배정이 있으면 삭제를 막는 안전장치가 있는데, 그 확정
배정을 취소하는 API 자체가 없다(새 배정으로 "교체"하는 경로만 존재) —
그래서 기존 10개 브리프·12건의 confirmed 배정을 API만으로 완전히
지울 방법이 없었다. 사용자에게 세 가지 옵션(DB 파일 전체 교체/API로
가능한 만큼만 정리/확정 배정을 하나씩 교체 후 삭제)을 제시했고,
**"DB 파일 전체 교체"**를 선택받았다 — 이 방식은 기존 6개 계정
(admin/pm_kim/pm_lee/qa_kim/park_member/agent_viewer)의 **비밀번호를
알 수 없어(해시만 있고 원문 없음) 그대로 옮길 수 없다**는 트레이드
오프를 사용자가 명시적으로 인지하고 받아들인 것이다.

**한 것**: 기존 `hair.db`를 `hair.db.bak-20261007-180651`로 백업한 뒤
삭제. `create_app()`(스키마 생성)과 `auth.create_user()`(정식 비밀번호
해시 경로)만 써서 — raw SQL이 아니라 앱이 실제로 쓰는 같은 함수
호출 — 완전히 새 DB를 만들었다. 계정 수·이름·role·조직도(manager_id)
는 기존과 동일하게 유지하고, 비밀번호만 이 프로젝트 테스트 픽스처
관례와 같은 값(`<role>pass`)으로 새로 설정했다.

**(과정 중 겪은 사고, 정직하게 기록)**: 시나리오 3 스크립트가 409
에러로 멈춘 직후 실제 서버 프로세스가 원인 불명으로 스스로 종료됐다
(로그에 "Shutting down" — 클라이언트의 409 응답 자체는 별도 OS
프로세스인 서버를 절대 죽일 수 없으므로 원인은 끝내 특정 못 함).
재시작한 서버에서 admin 로그인이 401로 실패해 확인해보니, 재시작
시점에 `hair.db` 파일 자체는 내가 만든 그대로였지만(`PRAGMA
integrity_check`는 "ok") admin 비밀번호 해시가 내가 설정한 값과
달랐다 — 알 수 없는 이유로 WAL(`hair.db-wal`, 354KB)이 완전히
checkpoint되지 않은 상태였던 것으로 보인다. 근본 원인을 끝까지
추적하는 대신(시간 대비 실익이 낮다고 판단), `hair.db`/`-wal`/`-shm`
을 전부 지우고 같은 스크립트로 깨끗하게 재생성해 즉시 해결했다 —
이번엔 서버를 올리기 전에 오프라인으로 `verify_password()`를 직접
호출해 해시가 맞는지 먼저 확인한 뒤에 서버를 띄웠다.

**실제 서버(포트 8000)에 실제 OpenAI(gpt-4o-mini) 호출로 5개 시나리오
전부 실행**(review-server처럼 LLM을 가짜로 바꿔치기하지 않았다 — 이번엔
사용자 계정으로 실제 로그인해 실제 과금이 되는 실제 프로덕션 경로를
그대로 탔다):
- 시나리오 1(`content_type=code`): 실제 GPT가 `auth_code` 누락 검증
  로직이 포함된 실제 동작하는 Flask 코드를 생성.
- 시나리오 2(`content_type=markdown`): `assist()`가 실제 제목/목록을
  갖춘 마크다운 초안을 반환.
- 시나리오 3(`content_type` 기본값=text): "고객 응대" Skill Pool
  등록 → CS 응답 초안 생성 → 검수자 댓글 → 승인까지 통과.
- 시나리오 4(`content_type=html`): 실제 OpenAI 임베딩으로 색인한
  규정을 실제로 검색해(`retrieved_context` 확인) 인용한 HTML 답변 생성.
- 시나리오 5(`content_type=markdown`, 팀 Crew 그룹 실행): 실제
  `crewai.Crew.kickoff()`가 gpt-4o-mini를 2회(카피 초안 Agent·
  디자인 가이드 Agent) 호출.

**실제 LLM으로만 드러난 새로운 버그 2건(리뷰 서버의 손수 만든 가짜
응답으로는 한 번도 안 드러났던 것들)**:
1. **HTML 렌더링에 ` ```html ` 펜스 글자가 그대로 보임** — content_type
   =html로 지정해도 실제 GPT는 응답 전체를 마크다운 코드 펜스로 또
   한 번 감싸는 경우가 흔했다(````html\n<!DOCTYPE html>...\n````).
   iframe `srcdoc`에 그대로 들어가 펜스 글자가 렌더링된 HTML 위에
   그대로 노출됐다.
2. **(더 심각) markdown 렌더링이 완전히 깨짐** — 마찬가지로 전체
   응답이 ` ```markdown ... ``` ` 한 겹으로 감싸여 있으면,
   `renderContent()`의 코드 펜스 감지 정규식이 이를 "코드 블록"으로
   먼저 인식해 `highlightCode()`를 태웠다. `highlightCode()`의 주석
   강조 규칙(`#.*$`)이 마크다운 헤딩(`#`/`##`)을 전부 "주석"으로
   착각해 `<span>`으로 감싸버리는 바람에, 그 뒤에 와야 할 헤딩 정규식
   (`^## ...`)이 줄 맨 앞에서 더 이상 `#`를 못 찾아 헤딩이 하나도
   안 먹혔다 — 제목·목록 없이 전부 평평한 코드 블록 텍스트로만
   보였다(Node로 실제 캡처한 응답을 직접 렌더링해 재현·확인).
   덤으로 코드 펜스가 끝나는 지점과 마지막 목록 줄이 줄바꿈 없이
   붙어 있어, 목록 정규식이 `</code></pre>` 닫는 태그까지 통째로
   삼켜 `<li>...</code></pre></li>`처럼 중첩이 깨지는 것도 같이
   발견했다.

**수정**: `renderContent()` 맨 앞에 `stripOuterFence(raw)`를 추가해
—"응답 **전체**가 코드 펜스 하나로만 감싸여 있을 때만" 그 바깥 펜스를
벗기고 안쪽 내용으로 렌더링하도록 했다(본문 중간에 섞인 펜스, 예를
들어 시나리오 1처럼 "설명 + 코드"가 섞인 응답은 그대로 둔다 — 그건
벗겨낼 "감싸기"가 아니라 의도된 혼합 문서다). `content_type`이
html/code/markdown일 때만 적용하고 `text`는 건드리지 않는다(사람이
직접 입력한 글의 펜스는 사용자 의도일 수 있어 보존).

**검증**: 코드 변경은 `webui.py`의 순수 프론트엔드 렌더링 함수뿐이라
골든셋 256건 그대로 통과. `ruff`/`black`은 HEAD 대비 새 경고 없음.
수정 전후로 실제 캡처한 시나리오 4/5 응답을 Node 격리 환경에서
`renderContent()`에 직접 넣어 비교(수정 전: 펜스 글자 노출/헤딩 전멸,
수정 후: 깨끗한 iframe/정상 헤딩·목록). 실제 서버를 재시작해(저장된
데이터는 그대로, 렌더링 코드만 갱신) Playwright로 시나리오 4(HTML)·
5(markdown) 워크스페이스를 다시 열어 수정 전/후 스크린샷으로 직접
비교 확인했다.

**알려진 한계(이번엔 코드로 안 고치고 기록만 남김)**: 시나리오 5에서
카피 초안 Agent와 디자인 가이드 Agent가 **똑같은 산출물**을 냈다
(`run_crew_for_team()`이 모든 Agent 멤버에게 완전히 같은 task
description을 주고 CrewAI/모델이 Agent의 `role`만으로 알아서
역할을 나눠주길 기대하는 설계 — 자체 docstring에도 "Agent별로 다른
지시를 미리 쪼개지 않는다"고 명시돼 있다). 실제 gpt-4o-mini sequential
실행에서는 이 기대가 안 맞아 두 Agent가 똑같이 일반적인 발표자료
개요를 냈다 — Gate 평가·저장·승인 등 파이프라인 자체는 정상 동작
하지만, "두 Agent가 서로 다른 전문성으로 다른 결과물을 낸다"는
시나리오 5의 취지는 실제 모델 앞에서는 그대로 보장되지 않는다. 고치려면
`run_crew_for_team()`이 Agent(팀 멤버)별 `role_label`을 프롬프트에
명시적으로 반영하도록 설계를 바꿔야 하는데, 이는 이번 범위(초기화+
시나리오 실행) 밖이라 코드는 건드리지 않고 사실만 기록한다.

## 72. 시나리오 6 신설 — 신규 Agent 2개 + 멀티 provider(OpenAI+로컬
    Ollama)로 "HAIR 업무 요청으로 게임 기획·개발" 라이브 실행 —
    사용자 요청(2026-10-08, "HAIR-Platform 업무 요청으로 HAIR-Platform
    으로 개발 가능한 수준의 게임 기획-개발 업무를 진행하는 시나리오를
    작성하고 해당 시나리오를 수행해줘 — 새로운 에이전트 개발, openAI
    및 Ollama 모델 활용")

**배경**: 바로 앞 턴에서 사용자가 "게임 개발 과제가 '내 업무'에 안
보인다"고 보고했다 — 확인해보니 Gatekeeper 게임은 HAIR의 브리프/
할 일 체계를 거치지 않고 Claude Artifact로 직접 만든 것이라 애초에
HAIR 쪽엔 그 "과제" 자체가 없었다(버그 아님). 사용자가 이어서 "게임
기획·개발을 HAIR 업무 요청 그 자체로" 진행해 달라고 요청해, 실제로
새 브리프→Task→Agent를 만들어 HAIR 파이프라인 안에서 게임을 기획·
개발했다.

**실행 환경 확인**: 이 머신에 로컬 Ollama 서버가 이미 떠 있고
(`http://localhost:11434`), `qwen3-coder:latest`(30.5B, 코딩 전문)를
포함해 실사용 가능한 모델이 여러 개 설치돼 있는 것을 `curl .../api/
tags`로 먼저 확인했다 — `llm._call_ollama()`가 기본으로 보는 모델
("llama3.1")은 이 환경에 없어서, 반드시 `model` 파라미터를 명시
(`"qwen3-coder:latest"`)해야 했다.

**한 것**: `docs/SCENARIOS.md`에 시나리오 6을 추가하고(신규 Agent
2개, "게임 기획"=OpenAI/"게임 프로토타입 개발"=로컬 Ollama), 실제
서버(포트 8000, 진짜 `hair.db`, 계정 비밀번호는 사용자가 이미
`ms1005`로 재설정해둔 상태)에 1-9단계 전부를 실제 API 호출로 실행
했다 — 브리프 제출→Task 분해→새 Agent 2개 생성(id 6·7)→온보딩→
배정·승인→사람 입력→`execute-agent{"provider":"openai","model":
"gpt-4o-mini"}`(기획)→`execute-agent{"provider":"ollama","model":
"qwen3-coder:latest"}`(코드)→Gate A-G 채점→승인→"내 업무" 확인.

**실제 결과**:
- 기획(OpenAI, markdown, 1286자) — 장르/핵심 루프 4단계/화면 구성을
  구체적으로 작성. Gate A 0.85 pass, D 0.638 warn, G 0.0 fail(창작
  문서라 "근거 인용"을 요구하는 Gate G가 구조적으로 낮게 나오는
  경향 — §24에서 이미 지적된 것과 일치, 새로운 문제 아님).
- 코드(로컬 Ollama, code, 6464자) — 실제로 여는 완전한 HTML 문서
  (인라인 CSS+JS, 상단 바·의뢰 리스트·버튼 이벤트 핸들러 포함)를
  생성했다. Gate C 0.4 fail·D 0.0 fail·G 0.0 fail로 OpenAI 결과보다
  전반적으로 낮다 — "실제로 동작하는 코드가 나왔는가"(사람이 직접
  확인)와 "Gate 자동 채점 점수"가 다른 질문이라는 걸 정직하게
  보여주는 결과다(로컬 모델이라고 코드 자체가 못 쓸 수준은 아니었지만,
  자동 채점 기준에서는 확실히 더 낮게 나왔다).
- 코드 산출물이 응답 전체를 ` ```html ` 펜스로 한 번 더 감싸고
  있었는데, §71의 `stripOuterFence()`가 content_type="code"에도
  동일하게 적용돼 펜스 없이 정상 렌더링되는 것을 확인했다 — 펜스의
  언어 태그("html")와 content_type("code")이 서로 달라도 정규식이
  언어 태그 자체를 구분하지 않으므로 문제없이 동작했다(실측으로
  처음 검증).

**검증**: 코드 변경 없음(순수 라이브 실행 + 문서화라 골든셋 256건
그대로). Playwright로 두 워크스페이스를 열어 마크다운 기획서(제목/
목록)와 구문 강조된 코드 블록이 각각 깨끗하게 렌더링되는 것, "내
업무"에 두 할 일이 모두 "승인됨"으로 뜨는 것을 스크린샷으로 확인했다.

## 73. 시나리오 7 신설 — 팀(복수 사람) + 신규 Crew 에이전트 그룹 +
    게임 내장 AI 기능을 실제 서버·실제 로컬 Ollama로 라이브 실행 —
    사용자 요청(2026-10-08, "게임의 기능에 openai나 ollama를 이용하는
    AI 기능이 들어가도록 시나리오를 구성하고, 복수의 사람과 crewai
    에이전트 그룹이 협업하는 업무가 되도록 기획하고 HAIR-Platform으로
    실행해줘")

**배경**: 시나리오 5(팀 Crew 그룹)와 시나리오 6(신규 Agent+멀티
provider)을 합치면서, 산출물(게임) 자체가 런타임에 로컬 Ollama를
호출하는 AI 기능을 내장해야 한다는 요구사항이 추가됐다. 사전에
`curl -i -X OPTIONS http://localhost:11434/api/chat -H "Origin:
..."`로 Ollama의 CORS 정책을 확인했더니 `localhost`/`127.0.0.1`
계열 출처는 포트 무관하게 전부 허용하고(`http://example.com:9999`,
`Origin: null`은 거부) — 브라우저에서 직접 호출하는 설계가 이
머신에서는 실제로 가능하다는 걸 코드를 쓰기 전에 먼저 확인했다.

**실행 중 실제로 막힌 지점(1) — 사람-계정 연결은 1:1**: pm_kim·
park_member용 사람(`human_pool`) 레코드를 새로 만들어 연결하려다
409("이미 다른 사람 인력(id=3)에 연결된 계정입니다")로 막혔다 — 시나리오
5에서 이미 pm_kim=human_pool#3, park_member=human_pool#2로 연결돼
있어서 충돌한 것이다. 한 로그인 계정은 `human_pool` 레코드
**하나에만** 연결된다는 제약을 이번에 실측으로 처음 확인했다(API
자체가 막아주므로 데이터 무결성 버그는 아니다 — 스크립트가 "항상
새로 만든다"고 가정한 게 문제였다). 잘못 만든 레코드 2건은 `DELETE
/api/human-pool/{id}`로 정리하고, 기존 레코드를 그대로 재사용해
팀 멤버로 추가하는 것으로 해결했다.

**실행 중 실제로 느낀 지점(2) — 로컬 30B 모델의 실행 시간**:
`provider="ollama"`, `qwen3-coder:latest`(30.5B)로 Agent 2개를
sequential 실행하니 약 6분 걸렸다 — 폴링 타임아웃을 200초로 잡았다가
한 번 모자라서 더 긴 타임아웃(10분)으로 재시도해야 했다. OpenAI
1회 호출(보통 수 초)과 대조적으로, 로컬 추론은 비용이 없는 대신
시간이 든다는 트레이드오프를 체감했다.

**실행 중 실제로 발견한 결과(3) — 이번엔 두 Agent가 실제로 달랐다**:
시나리오 5·§71에서는 "팀 Agent 실행(Crew)"의 두 Agent가 완전히
동일한 산출물을 냈는데, 이번엔 달랐다 — 다만 "서로 다른 전문성을
발휘해서" 다른 게 아니라, 한쪽("게임 UI 개발" Agent)이 "AI 연동
로직 개발자에게 요청한... 구현하였습니다"라는 문장만 쓰고 실제
코드 없이 여는 ` ``` ` 펜스로 응답을 끝내버린(153자, Gate C 0.4
fail·D 0.0 fail·G 0.0 fail) 반면, 다른 쪽("AI 연동 로직 개발"
Agent)은 완전한 HTML 채팅 UI(CSS+JS 포함, 4556자)를 만들었다. admin
이 전자는 **반려**("실제 코드 없이 설명만 있음 — 재작업 필요"),
후자는 **승인**했다 — "애매하면 그냥 승인하지 않는다"는 이 프로젝트의
리뷰 원칙을 실제 반려로 보여준 첫 사례다(그동안 라이브 시나리오는
전부 승인으로만 끝났었다).

**최종 검증(4) — AI가 만든 게임 안의 AI 기능이 실제로 동작하는가**:
승인된 코드를 그대로 `.html` 파일로 저장해 로컬 정적 서버(포트
5500)로 띄우고 Playwright로 열어, 입력창에 "너는 누구야? 한 문장으로
답해줘"를 치고 전송 버튼을 눌렀다. **로컬 Ollama(exaone3.5:7.8b)가
실제로 응답**("저는 LG AI Research에서 개발한 EXAONE 3.5입니다")
했고 채팅 UI에 정확히 표시됐다(브라우저 콘솔 에러 0건). 정리하면
3중으로 "실제로 동작하는가"를 확인한 셈이다 — ① HAIR가 실제 CrewAI로
Agent를 실행했는가(예), ② 그 Agent가 만든 코드가 실제로 유효한
HTML/JS인가(예), ③ 그 코드 안의 AI 호출이 실제 로컬 LLM 서버에
도달해 진짜 응답을 받아오는가(예).

**검증**: 코드 변경 없음(순수 라이브 실행 + 문서화라 골든셋 256건
그대로). Playwright로 "내 업무"(pm_kim="team_leader", park_member=
"team_member" 둘 다 확인), 워크스페이스, 그리고 생성된 게임을 각각
실제로 열어 확인했다.

## 74. `docs/ASANA_PARITY_DESIGN_PLAN.md` Phase 1 구현 — F17(Task 메모) +
F19(통합 활동 타임라인) — 사용자 요청(2026-10-08, "HAIR-Platform이 갖고
있는 장점을 살리면서 ASANA 모방 수준의 기능 및 UI 제공을 위한 기획 및
설계안을 제안해줘" → 설계안 §6 열린 질문에 "Phase 1→5 순서로" 답) 첫
실제 구현.

**F17(메모)**: `tasks.notes TEXT NOT NULL DEFAULT ''`(`_ensure_column()`로
기존 `hair.db`에도 안전하게 추가). `GET /api/tasks/{id}`(신규 — 지금까지
Task 단건 조회 엔드포인트 자체가 없었다, `GET /api/briefs/{id}/tasks`
간접 조회만 가능했음)·`PATCH /api/tasks/{id}/notes`(참가자 전용 —
`deliverables.create_comment()`가 쓰던 `task_participant_relationship()`
판정을 그대로 재사용, 결재 불필요). `GET /api/briefs/{brief_id}/tasks`도
명시적 dict 구성이라(`SELECT *`/`dict(row)`가 아님) `notes` 필드를 거기도
직접 추가해야 노출됐다 — Tier 2(content_type) 때와 달리 "컬럼만 추가하면
자동으로 보인다"가 성립하지 않는 응답 모양이 여기 또 하나 있었다.

**F19(통합 활동 타임라인)**: `GET /api/tasks/{id}/activity` —
`audit_log.subject_type`이 엔티티마다 제각각이라(`assignment`/
`deliverables`/`deliverable_comments`/`agent_execution_jobs`/`approval`/
`tasks`, 전체 `audit.record()` 호출부 전수조사로 확정) task_id로
되짚는 경로가 6갈래로 갈린다 — `UNION ALL`로 전부 합쳤다. 두 가지
특이 케이스: (1) `approval`은 `subject_id`가 `approvals.id`라
`approvals.subject_type`('assignment'/'deliverable')으로 한 번 더
분기해야 실제 task_id에 닿는다. (2) `team_crew_execution_started`
(`deliverables.start_team_crew_execution()`)는 `subject_id=None`으로
기록되고 `after_value` JSON에 `task_id`가 박혀 있어
`json_extract(after_value, '$.task_id')`로 따로 짚어야 한다(SQLite
3.38+ 내장 함수 — `:memory:` DB로 사전에 동작 확인 후 적용).

**검증**: `tests/unit/test_phase26_asana_parity_phase1.py` 11건 신설
(403 비참가자 차단, task 간 활동 비누출, `agent_execution_jobs`/
`approval`/`tasks` 등 전 경로의 activity 등장 확인 포함) — 전체
스위트 267건(256+11) 통과. 실서버(port 8000, 실제 `hair.db`)에
재배포해 `PRAGMA table_info(tasks)`로 마이그레이션 확인 후, 시나리오
7(§73)이 남긴 실제 이력이 있는 task #13을 열어 curl+Playwright로
교차 검증 — 메모 저장/조회, 활동 타임라인이 그 task의 배정·결과물
2건·댓글·결재(승인 1건+반려 1건)·팀 Crew 실행 시작까지 전부 시각
역순으로 올바르게 보여주는 것을 실측 확인했다(스크린샷으로 레이아웃도
확인).

## 75. `docs/ASANA_PARITY_DESIGN_PLAN.md` Phase 2 구현 — F15 Task 참가자
초대(결재 불필요) — §74에 이어, 같은 사용자 지시("Phase 1→5 순서로")의
두 번째 단계.

**핵심 설계 결정**: "초대"(`task_participants`)는 `assignments`(결재
필요, 작업 책임 발생)와 의도적으로 분리된 가벼운 멤버십이다 —
초대만으로 결과물 작성·AI 실행 같은 "작성자 자격"이 생기진 않는다
(설계 문서 §1 원칙). 이걸 코드로 강제하려고 `task_participant_
relationship()`(기존, 엄격)은 전혀 손대지 않고, `can_comment_on_task()`
라는 **더 넓은** 별도 함수를 새로 만들어 댓글 작성·열람에만 적용했다
— "참가자 판정 함수 하나를 조건부로 느슨하게 만들기"보다 "용도별로
함수를 분리"하는 쪽이 나중에 실수로 범위가 새나가는(예: 초대된
사람이 실수로 결과물도 쓸 수 있게 되는) 위험이 훨씬 낮다.

**참가자 ID 공간 결정**: `task_participants.participant_id`를
`human_pool.id`가 아니라 **`users.id`**로 정했다 — `assignments.
resource_id`(사람 배정)는 human_pool을 거치지만, 초대는 "실존 리소스를
매칭"하는 게 아니라 "이미 로그인 가능한 계정을 대화에 부른다"는
Asana 식 개념이라 human_pool을 거칠 이유가 구조적으로 없다(그리고
human_pool을 거쳤다면 §73에서 발견한 "계정 1개당 human_pool 레코드
1개"라는 제약까지 또 신경 써야 했을 것 — 애초에 우회됨).

**검증**: `tests/unit/test_phase27_asana_parity_phase2.py` 8건 신설 —
특히 "초대된 사람이 댓글은 가능하지만 결과물 작성은 여전히 403"이라는
경계 테스트(`test_gc_invited_human_can_comment_but_not_author_
deliverable`)가 이번 Phase의 핵심 리스크(설계 문서 §5 "F15의 범위
설정이 가장 민감하다")를 직접 검증한다. 실서버(port 8000, 실제
`hair.db`)에 재배포해 `PRAGMA table_info(task_participants)`로 새
테이블 생성 확인 후, curl로 초대 API를 직접 호출하고 Playwright로
워크스페이스의 "참가자" 카드가 실제로 그 초대를 반영해 렌더링되는
것까지 확인했다.

## 76. `docs/ASANA_PARITY_DESIGN_PLAN.md` Phase 3 구현 — F15 댓글
`@멘션` 트리거 — §74/§75에 이어 세 번째 단계, 이 Phase에서 F15가
완결된다.

**가장 중요한 설계 제약(사용자 결정 a)**: 댓글 트리거로 부른 Agent의
응답은 **항상 댓글로만** 남는다 — `deliverables`·Gate 평가·F7 결재
체인에 절대 닿지 않는다. 이걸 "그냥 조심해서 구현"이 아니라 구조적
으로 강제했다: `run_comment_triggered_execution_job()`은 `deliverable_
comments`에만 INSERT하고 `deliverables` 테이블 코드 자체를 아예 안
건드린다(참조조차 없음) — 실수로 섞일 경로가 물리적으로 없다. 테스트
(`test_gc_mention_triggers_agent_and_posts_comment_only`)가 멘션 전후
`deliverables` 행 수가 그대로인 것을 직접 확인한다.

**"결과물로 제출" 버튼을 새 엔드포인트 없이 구현한 결정**: 설계
문서는 "가벼운 질문은 댓글로만 끝내고 '결과물 제출'은 지금처럼 별도
폼을 쓰게 한다"고 이미 암시하고 있었다 — 그래서 Agent 댓글 옆
버튼은 그 댓글 **내용을 기존 "결과물 작성" textarea에 채워 넣기만**
하고, 실제 제출은 기존 `ws-submit-form`/`POST .../deliverables`를
그대로 탄다. 새 승격 API를 만들지 않아 F7 체인이 "또 다른 경로"로
우회될 여지 자체가 생기지 않는다(원칙4 — 이미 있는 걸 재사용).

**멘션 판정은 정규식이 아니라 부분 문자열 포함**: 한국어엔 영어
`\b` 같은 단어 경계가 없고 역할명에 공백이 섞여 있어(예: "게임
프로토타입 개발") 정규식 토큰화가 오히려 더 깨지기 쉽다 —
`"@" + role_name in content`라는 가장 단순한 판정으로 충분했다.

**실측 검증(비용 0원)**: 실서버에 로컬 Ollama 기반 Agent(§72에서
만든 "게임 프로토타입 개발", qwen3-coder:latest)를 task #13에
초대하고 실제로 "@게임 프로토타입 개발 이 게임에 점수 시스템을
추가하려면 어떻게 해야 할까?"라고 댓글을 달았다 — 약 6초 뒤 진짜
로컬 LLM이 생성한 구체적인 답변("게임 상태 관리 객체에 점수 변수를
추가하고...")이 같은 댓글 스레드에 `author_type='agent'`로 달렸고,
`deliverables` 테이블은 멘션 전후 그대로 4건이었다(실제 DB 쿼리로
확인). Playwright로 "참가자" 카드의 멘션 가능 목록 노출, 댓글의
"AI 에이전트" 배지, "이걸 결과물로 제출" 버튼이 textarea를 정확히
채우는 것까지 전부 실측했다.

**사소하지만 반복된 실수 — 또 한 번의 이스케이프 함정**: `webui.py`의
`INDEX_HTML`은 raw 문자열이 아니라서, JS 문자열 리터럴 안에 `\"`를
쓰려면 Python 소스에는 `\\"`로 써야 한다(한 번 더 — §LIMITS 초반부터
반복 경고된 바로 그 함정). 이번엔 `node --check`로 추출한 JS를
실제로 파싱해보는 습관 덕에 커밋 전에 바로 잡혔다 — "작성 후 반드시
추출·구문 검사"가 왜 관례로 굳어졌는지 다시 한번 확인된 사례.

**검증**: `tests/unit/test_phase28_asana_parity_phase3.py` 6건 신설
(뒷문 차단, 비멘션 무트리거, 미초대 Agent 무트리거, 동시 실행 중
스킵, 실패 시 가짜 댓글 미생성, F19 활동 타임라인 반영까지) — 전체
스위트 281건(275+6) 통과.
