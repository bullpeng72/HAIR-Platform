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

## 요약

| 항목 | 상태 |
|---|---|
| Gate A–G 실측 | 배선 완료(`eval_wiring.py`), 실행은 사용자 선택으로 보류 — 그때까지는 pytest 골든셋으로 대체 |
| Docker 샌드박스 실제 격리 | 미검증(환경에 Docker 없음) — 명령 구성·우회 방지는 검증 완료 |
| 2인 팀 실전 클레임 충돌 | 불가(단독 세션) — CI 인프라는 구축 완료 |
| Skills 신규 발굴 | 1/4건 완료(`rbac-permission-audit`), 나머지는 3회+ 기준 미충족 |
| NF7 idempotency | **구현 갭 발견 → 수정 완료** |
| NF4 배정 공정성 지표 | 구현 완료, 표본 부족으로 해석 보류 |
| 코드 레벨 재점검 4건(승인 우회·검색 필터·조직도 귀속·manager_id 누락) | **전부 발견 → 전부 수정 완료(GC42–46)** |
