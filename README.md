# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
AgentForge 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한 아키텍처 원칙
(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: Phase 0–6 완료 + Gate A–G 배선 완료(F1–F14 전부 구현, `pytest` 97
passed). **F11 Web UI 셸을 실제로 구현**했고, 내비게이션은 기존 RBAC
매트릭스를 근거로 "내 업무"/"관리자 도구" 2구역으로 분리했다(ADR 결정 9).
**실제 LLM(Anthropic/OpenAI/Ollama) 연동**(`llm.py`·`agent_runtime.py`) —
F4 Agent 생성·온디맨드 실행(`POST /api/agent-pool/{id}/execute`)에서 실제
API를 호출할 수 있고(기본값은 여전히 무료 스텁), `PATCH
/api/agent-pool/{id}/model`로 Agent별 기본 provider/model을 저장해둘 수
있다. **실제 CrewAI 연동 + VectorDB/GraphDB Agent도 구현 완료**(ADR 결정
10·11, `crew_runtime.py`·`vectorstore.py`·`graphquery.py`) —
`GenerateRequest.framework="crewai"`로 실제 CrewAI Agent를 생성하고,
`POST /api/crew/run`으로 여러 Agent를 실제 Crew로 묶어 `allow_delegation`+
`Task.context`로 협업시킬 수 있다. 표준 Agent 세트에 지식검색(VectorDB)·
관계분석(GraphDB, F8 성과 데이터는 소스 레벨에서 절대 조회 안 함) Agent를
추가했다. 실제 `crewai` 패키지를 설치해 통합 검증했다(`docs/LIMITS.md`
§12 — 검증 중 실제 API 호출 사고가 한 번 있었고, 원인·수정 내역을 정직하게
기록해뒀다). **실제 OpenAI API로 SDLC 8역할 에이전트 생성까지 실측
검증 완료**(`docs/LIMITS.md` §11). 그 전 라운드에서 발견한 11건(R1: 승인
우회·검색 필터·조직도 귀속·`manager_id` 누락, R2: 승인 inbox·사용자 목록·
감사로그 페이지네이션·secret_key 하드코딩, R3: 매칭 알고리즘 role 필터
버그·콜드스타트 부트스트랩·Human Pool 수정/오프보딩)도 전부 수정 완료
(`docs/LIMITS.md` §7·§8·§9). `release_hold`(Phase 7)는 사용자 선택으로
보류 — 알려진 갭은 `docs/LIMITS.md`, 기획 대비 실제 구현 비교는
`docs/RETROSPECTIVE.md` 참고.

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q        # 골든셋 97건(오프라인, 실제 LLM 호출 없음 — 전부 mock)
```

콜드스타트(신규 배포 시 최초 ADMIN 계정 생성 — 유일한 진입 경로):

```bash
hair-platform-bootstrap-admin --db-path hair.db --username admin --password '...'
```

실제 CrewAI 연동을 쓰려면(무거운 의존성 — opt-in):

```bash
pip install -e ".[crewai]"
# GenerateRequest.framework="crewai" 또는 Web UI "AI 에이전트 생성" 탭에서
# framework를 crewai로 선택 — 실제 LLM API를 호출하므로 비용이 발생한다.
# "Crew 실행" 탭에서 여러 Agent를 실제 CrewAI Crew로 묶어 협업시킬 수 있다.
```

실제 LLM 연동을 쓰려면(Anthropic·OpenAI·Ollama 중 선택):

```bash
pip install -e ".[llm]"                    # anthropic·openai·httpx SDK 설치

# 방법 A — 프로젝트 루트에 .env 파일(git에 커밋되지 않음, .gitignore 처리됨):
#   ANTHROPIC_API_KEY=...
#   OPENAI_API_KEY=...
# create_app()이 기동 시 자동으로 읽는다(python-dotenv, 이미 설정된 환경변수는
# 덮어쓰지 않음).

# 방법 B — 환경변수로 직접:
export ANTHROPIC_API_KEY=...               # 또는 OPENAI_API_KEY

# Ollama는 API 키가 없다 — 로컬 서버(기본 http://localhost:11434, OLLAMA_BASE_URL로
# 변경 가능)를 미리 띄워두고 provider="ollama"를 직접 지정해야 한다(자동판별 대상 아님).

# API 요청에 "use_llm": true 를 넣거나, Web UI "AI 에이전트 생성" 탭의
# 체크박스를 켜면 실제 API가 호출된다(비용 발생) — 안 켜면 무료 스텁 그대로.
# "AI 에이전트" 탭의 "모델 설정"으로 Agent별 기본 provider/model을 저장해두면
# (비용 없음), 이후 "실행"이 매번 지정 안 해도 그 설정을 따른다.
```

Web UI 실행(부트스트랩으로 만든 ADMIN 계정으로 로그인):

```bash
uvicorn hair_platform.app:create_app --factory --reload
# 브라우저에서 http://127.0.0.1:8000/ 접속
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
  app.py             # FastAPI 라우트(39개 엔드포인트, GET / 포함)
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
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성 CLI
  webui.py           # F11 Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임)
  vectorstore.py     # 지식검색 Agent(VectorDB) — OpenAI 임베딩+SQLite 코사인 유사도
  graphquery.py      # 관계분석 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의, NF9 경계
docs/
  SPEC.md            # 명세(F1–F14, NF1–13, 로드맵, DoD)
  ADR.md             # 설계 결정 11건
  LIMITS.md          # 알려진 갭(정직하게 기록)
  RETROSPECTIVE.md   # 기획 대비 실제 구현 비교
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 97건
```

## 로드맵

`docs/SPEC.md` §7 참고. Phase 0(부트스트랩)~Phase 6(Skills화)까지 완료,
Phase 7(`release_hold`)은 대기 중 — 진행하려면 `docs/LIMITS.md`의 갭 목록을
먼저 검토할 것을 권장한다.

## 라이선스

MIT
