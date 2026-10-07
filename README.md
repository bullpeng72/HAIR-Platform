# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
AgentForge 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한 아키텍처 원칙
(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: Phase 0–6 완료, F1–F14/NF1–13 전부 구현, `pytest` 256건 통과,
API 엔드포인트 83개. 알려진 갭·미구현 범위는 전부 `docs/LIMITS.md`에
정직하게 기록돼 있다(가장 최신: §71).

## 핵심 기능

### 사람-Agent 협업 모델(HAIR의 축)
- 사람과 AI Agent를 조직도 위에 함께 배치(F5)·배분(F6, 결정론적 매칭
  알고리즘)·승인(F7, 조직도 매니저 체인을 따른 다단계 결재라인 + 자기승인
  방지)
- 한 할 일에 **여러 건의 confirmed 배정이 기본적으로 공존**한다(사람
  여러 명, Agent 여러 개, 또는 팀) — 교체하려면 `replaces_assignment_id`로
  명시해야 그 배정만 superseded 처리된다
- **팀(teams/team_members)** — 사람 N명 + Agent M명을 역할명과 함께
  묶어 등록해두고 할 일에 팀 전체를 한 번에 배정(`resource_type='team'`,
  한 할 일에 팀을 여러 개 배정하는 것도 가능). F7 결재 주체는 팀 리더
  (기본값은 배정 요청자, 확정 전까지 변경 가능). 협업 워크스페이스의
  "팀 Agent 실행" 버튼으로 팀의 Agent 멤버 전원을 실제 CrewAI Crew로
  실행하고, 결과가 Agent별 결과물+F7 결재 체인으로 그 할 일에 그대로
  연결된다(팀이 여러 개 배정돼 있으면 그중 하나를 골라 실행). "AI
  에이전트 생성"에서 여러 할 일을 한 번에 골라 역할별 Agent를 만든
  직후 그 결과 화면에서 바로 "팀으로 묶기"도 가능 — "AI 에이전트
  관리"에는 Agent별 소속 팀이 노출된다
- 협업 워크스페이스 — 결과물(deliverable) 타임라인, 댓글, 사람이 작성한
  결과물도 "검토 요청"을 누르면 Agent 결과물과 같은 결재 체인을 거침,
  승인된 최종 결과물은 타임라인 위에 고정 노출
- 결과물 형식(`content_type`: 일반 텍스트/Markdown/HTML/코드) — 작성자·
  Agent·팀 Crew 모두 형식을 지정할 수 있고(LLM/Agent 경로는 프롬프트에
  형식 지시 자동 추가), 화면에서 형식에 맞게 그대로 렌더링(Markdown
  헤딩/목록/굵게, 코드 구문 강조, HTML은 `sandbox="allow-scripts"`
  iframe 미리보기) + 결과물별 다운로드 버튼. 실제 파일(PPTX 등) 생성·
  저장은 범위 밖(`docs/LIMITS.md` §68)

### Agent 실행 · LLM 연동
- 실제 Anthropic / OpenAI / Ollama 연동(`llm.py`, `agent_runtime.py`) —
  무료 스텁(`stub`)·단발 LLM 호출(`simple_llm`)·CrewAI(`crewai`) 세
  프레임워크 중 선택
- provider/model은 자유 입력 대신 **모델 레지스트리**(`model_registry.py`)에
  등록해두고 전 화면(Agent 생성/관리/Crew 실행/협업 워크스페이스)에서
  드롭다운으로 공유 선택
- Agent 실행은 비동기 처리(`POST .../execute-agent` → 202 + job_id,
  `agent_execution_jobs` 테이블로 진행 상태 추적 + 폴링)
- 실행마다 Gate A–G를 자동 측정해 append-only `agent_evaluations`에 기록
  (`agent_evaluation.py`, 단일 실행·CrewAI 양쪽 경로 모두 연결)

### CrewAI 다중 Agent 협업
- 실제 `crewai` 패키지 연동(`crew_runtime.py`) — sequential/hierarchical
  프로세스, `allow_delegation`, 실제 위임(delegation) 관계를 이벤트로
  캡처해 협업 관계 다이어그램(SVG)으로 시각화
- VectorDB(과거 산출물 검색)·GraphDB(조직 관계 질의)·Wikipedia(외부
  일반 지식)·Parallel AI(일반 웹검색, MCP)를 전부 CrewAI Tool로 노출해
  Agent가 필요 여부를 스스로 판단해 호출
- MCP(stdio/remote) 외부 도구를 Agent별로 연결 가능

### Tool/Skill/Model 등록 — "등록 → 재사용" 패턴
- **Tool Pool·Skill Pool**(`pool_registry.py`) — "스킬 → 자동 도구 연결"
  매핑을 코드 하드코딩 대신 데이터로 관리. 하니스 방법론 절차 스킬
  18개를 기본 노출(17개는 HITL 절차라 `augmentation_mode='none'`, 1개는
  실제 MCP 도구로 승격)
- **모델 레지스트리**(`model_registry.py`) — provider별 실제 사용 가능한
  모델을 등록·활성/비활성 관리
- **팀**(`teams.py`) — 사람/Agent 그룹을 등록해 여러 업무에 재사용(위
  "핵심 기능" 참고) — 같은 패턴의 네 번째 적용

### 운영 관리
- 통합 인박스(결재 대기 + Agent 실행 실패 + 낮은 Gate 점수 경고),
  매니저 대시보드(팀 Gate 평균·대기 승인 건수·비용 추이)
- 검색/필터(가용성 양방향 필터 + 페이지네이션), 활동 기록
  (append-only 감사로그, NF10)
- 데이터 삭제/초기화(도구·스킬·사람·Agent·brief/task, confirmed 배정이
  있는 대상은 보호) + 보관기간 정책(`retention.py`, assignment_history
  1년 — audit_log는 append-only 트리거를 이 모듈만 통제된 방식으로 해제)
- Web UI 20개 탭을 RBAC 매트릭스 기준 "내 업무"/"공용 조회"/"운영 관리"
  3구역으로 분리(ADR 결정 9)

### 권한(RBAC)
4개 역할(`admin`/`manager`/`member`/`viewer`, ADR 결정 3) — 생성/조회는
admin+manager 공용이 많고, 삭제·사용자 생성·Agent 온보딩 승인처럼 조직
구조에 직접 영향을 주는 동작은 admin 전용. 세션 인증(`auth.py`,
`SessionMiddleware`).

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q        # 골든셋 256건(오프라인, 실제 LLM 호출 없음 — 전부 mock)
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

# MCP(외부 검색 등 도구) 연동을 쓰려면 추가로:
pip install -e ".[mcp]"
# Web UI "AI 에이전트" 탭의 "MCP 서버(JSON)" 칸에
# [{"url": "http://localhost:8000/mcp", "transport": "streamable-http"}]
# 형태로 넣고 "모델 설정" 저장 — 이후 "Crew 실행"에서 그 Agent에 실제
# MCP 도구가 붙는다. MCPServerAdapter는 생성 시점에 실제로 접속을
# 시도한다(접속 가능한 MCP 서버가 준비돼 있어야 한다).
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
# (비용 없음), 이후 "실행"이 매번 지정 안 해도 그 설정을 따른다. provider/model
# 목록은 "모델 레지스트리" 탭에 등록해두면 전 화면에서 드롭다운으로 공유된다.
```

Web UI 실행(부트스트랩으로 만든 ADMIN 계정으로 로그인):

```bash
uvicorn hair_platform.app:create_app --factory --reload
# 브라우저에서 http://127.0.0.1:8000/ 접속
```

Gate A–G 수동 실측(선택, 서비스 경로는 이미 상시 연결돼 있음 —
`agent_evaluation.py`):

```bash
agent-eval check                     # API 키 등 설정 먼저 확인
python -m hair_platform.eval_wiring  # Gate A/B/D/E/G 측정 실행
agent-eval gate results/*.json       # 채점
```

## 구조

```
src/hair_platform/
  auth.py            # 세션 인증 + RBAC(4종 역할: admin/manager/member/viewer)
  models.py          # Pydantic v2 요청 모델
  db.py              # SQLite 스키마(WAL, audit_log append-only 트리거)
  app.py             # FastAPI 라우트(83개 엔드포인트, GET / 포함)
  pool.py            # Human/Agent Pool → ResourceCandidate 어댑터
  assignment.py      # F6 매칭 알고리즘(결정론적)
  approvals.py       # F7 다단계 결재라인(조직도 매니저 체인)
  assignments.py     # F5/F6 배정 요청·확정(팀 배정 + 다중 confirmed 지원)
  teams.py           # 팀(사람+Agent 그룹) CRUD — "등록→재사용" 패턴
  jobs.py            # F4 Job Queue(+ idempotency)
  sandbox.py         # ADR 결정8 Docker 샌드박스 명령 구성
  audit.py           # F13 감사로그
  performance.py     # F8 성과리뷰(동의 기반)
  cost.py            # F10 비용 조회
  fairness.py        # 배정 공정성 지표(NF4)
  retention.py       # 데이터 보관기간 정책(assignment_history 1년)
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선 수동 실행 probe(agent-evaluator SDK, python -m으로 직접 실행)
  agent_evaluation.py # Gate A-G 평가를 Agent 실행 경로에 상시 연결(agent_evaluations 테이블)
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성 CLI
  webui.py           # Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙, 20개 탭)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  model_registry.py  # provider별 사용 가능 모델 등록·관리("등록→재사용")
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임 + 다이어그램)
  vectorstore.py     # 지식검색 Agent(VectorDB) — OpenAI 임베딩+SQLite 코사인 유사도
  graphquery.py      # 관계분석 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의, NF9 경계
  websearch.py       # 외부 일반 지식 검색(Wikipedia native 도구 + Parallel AI MCP)
  pool_registry.py   # Skill Pool·Tool Pool(ADR 결정 12) — 스킬→도구 매핑 데이터화, CrewAI Tool 조립
  data_admin.py      # brief/task/human/agent 하드 삭제(FK 연쇄 삭제 + confirmed 배정 보호)
  deliverables.py    # 협업 워크스페이스 — 결과물/댓글, "내 업무", 사람 LLM 보조, Agent 비동기 실행+평가(agent_execution_jobs)
  inbox.py           # 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고)
  dashboard.py       # 매니저 대시보드(팀 Gate 평균+대기 승인 건수+비용 추이)
docs/
  SPEC.md            # 명세(F1–F14, NF1–13, 로드맵, DoD)
  ADR.md             # 설계 결정 목록
  LIMITS.md          # 알려진 갭(정직하게 기록, §71까지)
  RETROSPECTIVE.md   # 기획 대비 실제 구현 비교
  ROLE_TASK_CLASSIFICATION.md      # 관리자/사용자 고유·공통 업무 재분류(외부 사례 리서치)
  SCENARIOS.md                     # 대표 시나리오 워크스루(개발/일반업무/CS/Tool·Skill 활용)
  COLLABORATION_PLATFORM_PLAN.md   # 사람-AI 협업(결과물 전달/HITL/HOTL) 기획안 — 구현 완료
  HAIR_MASTER_PLAN.md              # 위 기획안을 포함하는 종합 기획/설계/개발 계획 — 구현 완료
  MULTI_PARTY_TEAM_COLLABORATION_PLAN.md  # N명 사람+M개 Agent(또는 역할 기반 Agent 그룹) 팀 협업 기획안 — Phase 1-3 구현 완료, Phase 4-5는 미구현(§9)
  HAIR_ASSISTANT_PLAN.md           # HAIR 매뉴얼 기반 챗봇 도우미 기획안 — 미구현(기획만)
  PHASE1_DELIVERABLE_SPEC.md       # 협업 워크스페이스 구현 스펙(스키마 DDL·API 계약·GC127-137) — 구현 완료
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 256건
```

## 로드맵

`docs/SPEC.md` §7 참고. Phase 0(부트스트랩)~Phase 6(Skills화)까지 완료,
Phase 7(`release_hold`)은 대기 중 — 진행하려면 `docs/LIMITS.md`의 갭 목록을
먼저 검토할 것을 권장한다.

## 라이선스

MIT
