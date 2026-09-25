# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
AgentForge 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한 아키텍처 원칙
(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: Phase 0–6 완료 + Gate A–G 배선 완료(F1–F14 전부 구현, `pytest` 51
passed). 코드 레벨 재점검을 2회 진행해 발견한 8건(R1: 승인 우회·검색 필터·조직도
귀속·`manager_id` 누락, R2: 승인 inbox·사용자 목록·감사로그 페이지네이션·
secret_key 하드코딩)도 전부 수정 완료(`docs/LIMITS.md` §7·§8). `release_hold`
(Phase 7)는 사용자 선택으로 보류 — 알려진 갭은 `docs/LIMITS.md`, 기획 대비 실제
구현 비교는 `docs/RETROSPECTIVE.md` 참고.

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q        # 골든셋 51건(오프라인, LLM 호출 없음)
```

Gate A–G 실측(선택, 배선은 돼 있으나 실행은 보류돼 있음 — `docs/LIMITS.md` §1):

```bash
agent-eval check                     # API 키 등 설정 먼저 확인
python -m hair_platform.eval_wiring  # Gate A/B/D/E/G 측정 실행
agent-eval gate results/*.json       # 채점
```

## 구조

```
src/hair_platform/
  auth.py            # 세션 인증 + RBAC(4종 역할)
  models.py          # Pydantic v2 요청 모델
  db.py              # SQLite 스키마(WAL, audit_log append-only 트리거)
  app.py             # FastAPI 라우트(26개 엔드포인트)
  pool.py            # Human/Agent Pool → ResourceCandidate 어댑터
  assignment.py      # F6 매칭 알고리즘(결정론적)
  approvals.py       # F7 다단계 결재라인(조직도 매니저 체인)
  assignments.py     # F5/F6 배정 요청·확정(3중 기록 트랜잭션)
  jobs.py            # F4 Job Queue(+ idempotency)
  sandbox.py         # ADR 결정8 Docker 샌드박스 명령 구성
  audit.py           # F13 감사로그
  performance.py     # F8 성과리뷰(동의 기반)
  cost.py            # F10 비용 조회
  fairness.py        # 배정 공정성 지표(NF4)
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선(agent-evaluator SDK)
docs/
  SPEC.md            # 명세(F1–F14, NF1–13, 로드맵, DoD)
  ADR.md             # 설계 결정 8건
  LIMITS.md          # 알려진 갭(정직하게 기록)
  RETROSPECTIVE.md   # 기획 대비 실제 구현 비교
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 51건
```

## 로드맵

`docs/SPEC.md` §7 참고. Phase 0(부트스트랩)~Phase 6(Skills화)까지 완료,
Phase 7(`release_hold`)은 대기 중 — 진행하려면 `docs/LIMITS.md`의 갭 목록을
먼저 검토할 것을 권장한다.

## 라이선스

MIT
