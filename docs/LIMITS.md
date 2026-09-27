# LIMITS — HAIR-Platform 알려진 갭 (Phase 4 검증 기준)

> AgentForge의 `docs/LIMITS_T-D8D8CE.md`와 같은 정신 — 완벽한 점수가 아니라 갭을
> 정직하게 남긴다. 이 문서는 SPEC.md §7 Phase 4(검증)에서 실제로 확인한 것과
> 확인하지 못한 것을 구분한다.

## 1. Gate A–G 실측 배선 — 배선 완료, 실행은 사용자 몫(업데이트: 2026-09-25)

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
재확인했다. `max_rpm`/`step_callback`/`task_callback` 미사용은 기능
공백이지 버그는 아니며(감사 로깅은 이벤트버스로 이미 처리 중), 이번
리뷰에서는 손대지 않았다(오픈 이슈로만 남김).

## 요약

| 항목 | 상태 |
|---|---|
| Gate A–G 실측 | 배선 완료(`eval_wiring.py`), 실행은 사용자 선택으로 보류 — 그때까지는 pytest 골든셋으로 대체 |
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
