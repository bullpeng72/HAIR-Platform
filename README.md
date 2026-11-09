# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
AgentForge 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한 아키텍처 원칙
(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: 조직도 기반 배치·배분·승인, Agent 생성·실행(Gate A-G 자동
채점), CrewAI 다중 Agent 협업, ASANA 수준의 작업 구조(메모·활동
타임라인·참가자 초대+댓글 `@멘션`·상태/우선순위/의존관계 그래프
시각화·운영 가시성·탭 재구성), **대화 중심 작업공간**(로그인 직후
독립 대화 + 기본 AI 자동응답/특정 Agent·Team 멘션/skill 그라운딩 +
Task 종속 대화 흡수 + Brief 승격 공존), 단계형 개발 파이프라인 엔진
(Gate A-G 기준 자동 재시도/에스컬레이션, BMAD-METHOD식 계획 승인
1회 + 최소 HITL), Agent별 LLM 모델/설정 최적화 추천(admin), **통합
CLI**(`hair serve`/`bootstrap-admin`/`reset-data`/`eval`)까지 전부 구현돼 실제로
쓸 수 있다. `pytest` 811건 통과, API 엔드포인트 168개. 개념·아키텍처
전체 설명은
`docs/OVERVIEW.md`, 설계 결정의 배경·과거 버그 수정 이력은 `git log`
(커밋 메시지에 날짜·사용자 요청 원문이 그대로 남아 있다)를 참고.

## 핵심 기능

### 사람-Agent 협업 모델(HAIR의 축)
- 사람과 AI Agent를 조직도 위에 함께 배치·배분(결정론적 매칭
  알고리즘)·승인(조직도 매니저 체인을 따른 다단계 결재라인 + 자기승인
  방지)
- 한 할 일에 **여러 건의 confirmed 배정이 기본적으로 공존**한다(사람
  여러 명, Agent 여러 개, 또는 팀) — 교체하려면 `replaces_assignment_id`로
  명시해야 그 배정만 superseded 처리된다
- **팀(teams/team_members)** — 사람 N명 + Agent M명을 역할명과 함께
  묶어 등록해두고 할 일에 팀 전체를 한 번에 배정(`resource_type='team'`,
  한 할 일에 팀을 여러 개 배정하는 것도 가능). 결재 주체는 팀 리더
  (기본값은 배정 요청자, 확정 전까지 변경 가능). 협업 워크스페이스의
  "팀 Agent 실행" 버튼으로 팀의 Agent 멤버 전원을 실제 CrewAI Crew로
  실행하고, 결과가 Agent별 결과물+결재 체인으로 그 할 일에 그대로
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
  iframe 미리보기, 모든 형식에 복사/미리보기·소스 토글 포함) + 결과물별
  다운로드 버튼. 실제 파일(PPTX 등) 생성·저장은 범위 밖

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
- VectorDB(과거 산출물 검색)·GraphDB(조직 관계 질의)·Tavily(실시간
  웹검색, 대화창·Crew 양쪽 동작)·Parallel AI(일반 웹검색, MCP, Crew
  전용)를 전부 CrewAI Tool로 노출해 Agent가 필요 여부를 스스로 판단해
  호출(Wikipedia 검색은 이 배포 환경 IP가 지속 차단돼 있어 기본
  시드에서 뺐다 — `websearch.py`에 코드는 남아 있음)
- MCP(stdio/remote) 외부 도구를 Agent별로 연결 가능
- 로컬 파일(.md/.markdown/.txt/.html/.htm/.pdf) 업로드가 세 곳에
  있고 성격이 다르다(`documents.py`) — **대화창**과 **협업
  워크스페이스**(업무 실행 버튼 옆 "📎 참고자료")는 그 대화/그 1회
  실행에만 텍스트로 주입하고 어디에도 저장하지 않는 **휘발성**
  첨부, admin/manager 전용 **"지식 베이스"** 탭만 텍스트 직접 입력
  포함해 VectorDB에 **영구 색인**한다 — 색인되면 `rag` 스킬을 가진
  모든 Agent가 대화창 단발 호출·CrewAI 실행 양쪽에서 똑같이 찾아냄.
  "지식 베이스" 탭에서 색인된 조각 조회/삭제+통계, GraphDB 관계
  조회+통계까지 관리

### Tool/Skill/Model 등록 — "등록 → 재사용" 패턴
- **Tool Pool·Skill Pool**(`pool_registry.py`) — "스킬 → 자동 도구 연결"
  매핑을 코드 하드코딩 대신 데이터로 관리. 하니스 방법론 절차 스킬
  18개를 기본 노출(17개는 HITL 절차라 `augmentation_mode='none'`, 1개는
  실제 MCP 도구로 승격). `augmentation_mode`는 `'tool'`(Agent가 호출
  여부를 스스로 판단)과 `'always'`(판단 없이 Task description에 강제
  주입)로 나뉜다 — `'최신정보필수확인'`(Tavily 재사용)이 `'always'`를
  실제로 쓰는 첫 스킬
- **Agent/Tool/Skill 표시용 ID 체계**(`id_scheme.py`) — 정수 PK는 FK·
  URL 경로가 그대로 쓰므로 안 바꾸고, 종류를 바로 구분할 수 있는 코드를
  `code` 컬럼에 별도로 둔다(`AGT-REF-001`/`AGT-CUSTOM-001`, `TUL-NATIVE-
  001`, `SKL-TOOL-001` 등 — Agent는 레퍼런스 23종 vs 커스텀, Tool/Skill은
  각각 `kind`/`augmentation_mode` 그대로). 레퍼런스 데이터는 기동 시
  자동 배정(기존 DB도 마이그레이션), 신규 생성은 그 자리에서 즉시 배정,
  `id_scheme.reset_and_reassign_codes()`로 전체 초기화 후 재배정도
  가능(배정이 `id` 오름차순 결정적이라 중간 삭제가 없으면 결과는 같음).
  Web UI 관리자 테이블(AI 에이전트·Tool Pool·Skill Pool)에서는 정수
  `id` 열을 빼고 "코드"만 보여준다(사용자 요청, 2026-10-27, "노출되지
  않도록 개선") — API 응답과 수정/삭제 버튼 등 내부 로직은 `id`를 그대로
  쓰므로 기능은 안 바뀐다
- **모델 레지스트리**(`model_registry.py`) — provider별 실제 사용 가능한
  모델을 등록·활성/비활성 관리 + 모델별로 "LLM 설정 최적화"의 tier
  (심층추론/정밀·신속/표준)를 직접 매핑(모델 하나가 여러 tier 가능,
  매핑 없으면 이름 패턴 추정으로 폴백) — 새 모델 출시·provider 교체
  시 코드 수정 없이 여기서만 바꾸면 됨
- **팀**(`teams.py`) — 사람/Agent 그룹을 등록해 여러 업무에 재사용(위
  "핵심 기능" 참고) — 같은 패턴의 네 번째 적용

### 운영 관리
- 통합 인박스(결재 대기 + Agent 실행 실패 + 낮은 Gate 점수 경고),
  매니저 대시보드(팀 Gate 평균·대기 승인 건수·비용 추이)
- 검색/필터(가용성 양방향 필터 + 페이지네이션), 활동 기록
  (append-only 감사로그)
- 데이터 삭제/초기화(도구·스킬·사람·Agent·brief/task, confirmed 배정이
  있는 대상은 보호) + 보관기간 정책(`retention.py`, assignment_history
  1년 — audit_log는 append-only 트리거를 이 모듈만 통제된 방식으로 해제)
- `hair reset-data` — 운영 데이터(사용자·업무·대화·실행 이력·모델
  레지스트리·활동 기록 등) 전체를 공장 초기화. Agent(23종)/Tool·Skill
  Pool/팀/파이프라인 템플릿은 건드리지 않는다(`data_admin.py`) —
  pip 배포 전 깨끗한 상태로 준비할 때 사용
- Web UI 26개 탭을 RBAC 매트릭스 기준 "오늘 할 일"/"설정" 2구역으로
  분리(원래 "내 업무"/"공용 조회"/"운영 관리" 3구역이었으나 이후
  2구역으로 재편)

### 권한(RBAC)
4개 역할(`admin`/`manager`/`member`/`viewer`) — 생성/조회는
admin+manager 공용이 많고, 삭제·사용자 생성·Agent 온보딩 승인처럼 조직
구조에 직접 영향을 주는 동작은 admin 전용. 세션 인증(`auth.py`,
`SessionMiddleware`).

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]"
# `[dev]`만 설치하면 `tests/unit`의 CrewAI 연동 테스트 9개가 테스트
# 실행 시점에 `ModuleNotFoundError`로 깨진다(2026-10-27 패키징 점검
# 발견 — `test_documents.py`만 `pytest.importorskip()`로 이 문제를
# 스스로 막고 있었다). `all`(= crewai+mcp+llm+documents 묶음, pyproject.toml
# 참고)까지 같이 설치해야 아래 골든셋이 전부 오프라인으로 깨끗하게 통과한다.
pytest tests/ -q        # 골든셋 811건 통과(오프라인, 실제 LLM 호출 없음 — 전부 mock)
```

실행 명령은 전부 `hair <subcommand>` 하나로 모여 있다 — 각 서브커맨드의
옵션/예시는 `hair <subcommand> --help`로 확인(`src/hair_platform/cli.py`):

```bash
hair --help
```

콜드스타트(신규 배포 시 최초 ADMIN 계정 생성 — 유일한 진입 경로,
`users` 테이블이 완전히 비어 있을 때만 동작):

```bash
hair bootstrap-admin -u admin -p '...'       # --db-path 생략 시 기본 hair.db
```

운영 중 쌓인 데이터만 초기화하고(사용자·업무·대화·실행 이력·모델
레지스트리·활동 기록 등) Agent(23종)/Tool·Skill Pool/팀/파이프라인
템플릿은 그대로 둘 때(예: 배포 전 깨끗한 상태로 준비):

```bash
hair reset-data                              # 확인 프롬프트 뜸(되돌릴 수 없음)
hair reset-data --yes                        # 확인 없이 바로 실행
# 보통 바로 이어서: hair bootstrap-admin -u admin -p '...'
```

실제 CrewAI 연동을 쓰려면(무거운 의존성 — opt-in):

```bash
pip install -e ".[crewai]"
# GenerateRequest.framework="crewai" 또는 Web UI "AI 에이전트 생성" 탭에서
# framework를 crewai로 선택 — 실제 LLM API를 호출하므로 비용이 발생한다.
# "Crew 빌더" 탭에서 여러 Agent를 실제 CrewAI Crew로 묶어 협업시킬 수 있다
# (업무에 바로 실행/즉석 테스트/실행 이력 3개 모드).

# MCP(외부 검색 등 도구) 연동을 쓰려면 추가로:
pip install -e ".[mcp]"
# Web UI "AI 에이전트" 탭의 "MCP 서버(JSON)" 칸에
# [{"url": "http://localhost:8000/mcp", "transport": "streamable-http"}]
# 형태로 넣고 "모델 설정" 저장 — 이후 "Crew 빌더"에서 그 Agent에 실제
# MCP 도구가 붙는다. MCPServerAdapter는 생성 시점에 실제로 접속을
# 시도한다(접속 가능한 MCP 서버가 준비돼 있어야 한다).
```

실제 LLM 연동을 쓰려면(Anthropic·OpenAI·Ollama 중 선택):

```bash
pip install -e ".[llm]"                    # anthropic·openai SDK 설치(httpx는 기본 의존성에 포함)

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

대화창·협업 워크스페이스(업무 실행)·지식 베이스 세 곳 중 어디서든
`.pdf`/`.html` 파일을 업로드하려면(`.md`/`.txt`는 추가 설치 없이 바로
동작 — 셋 다 같은 `documents.extract_text()`를 공유한다):

```bash
pip install -e ".[documents]"              # pypdf·beautifulsoup4 설치
```

"실시간웹검색" 스킬(Tavily, 대화창·Crew 실행 양쪽에서 동작하는 실시간
웹 검색)을 쓰려면 `.env`에 키를 추가:

```bash
#   TAVILY_API_KEY=...                       # https://tavily.com 무료 발급
```

Web UI 실행(부트스트랩으로 만든 ADMIN 계정으로 로그인):

```bash
hair serve                # http://127.0.0.1:8000/, hair.db(기본)에 저장 — 브라우저 자동으로 열림
hair serve --reload       # 개발 중 — 코드 변경 시 자동 재시작
hair serve --no-browser   # 브라우저 자동 열기 끄기(서버만 실행)
```

사내망을 통해 다른 PC에서도 접속하려면(같은 네트워크의 팀원 여러 명이
함께 쓰는 경우) `--host 0.0.0.0`로 모든 인터페이스에 바인딩한다 —
단, 띄우기 전에 **반드시** 랜덤 세션 서명 키를 지정해야 한다(안 하면
소스에 공개된 고정값으로 세션이 서명돼, 같은 네트워크의 누구나 다른
사용자의 로그인 세션을 위조할 수 있다 — `hair serve`가 기동 시점에
이걸 감지하면 경고를 띄운다):

```bash
export HAIR_PLATFORM_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
hair serve --host 0.0.0.0 --no-browser   # 다른 PC에서 http://<이 PC의 사내망 IP>:8000/
```

`--reload`는 개발 전용이다 — 여러 명이 같이 쓰는 인스턴스에는 쓰지 않는다
(파일 변경마다 재시작되며 접속이 전부 끊긴다). 로그인 비밀번호는 평문
HTTP로 오가므로, 민감도가 높다면 앞단에 리버스 프록시로 TLS를 추가하는
걸 권장한다.

Gate A–G 수동 실측(선택, 서비스 경로는 이미 상시 연결돼 있음 —
`agent_evaluation.py`):

```bash
agent-eval check          # API 키 등 설정 먼저 확인
hair eval                 # Gate A/B/D/E/G 측정 실행(results/에 저장)
agent-eval gate results/*.json       # 채점
```

## 구조

```
src/hair_platform/
  auth.py            # 세션 인증 + RBAC(4종 역할: admin/manager/member/viewer)
  models.py          # Pydantic v2 요청 모델
  db.py              # SQLite 스키마(WAL, audit_log append-only 트리거)
  app.py             # FastAPI 라우트(168개 엔드포인트, GET / 포함)
  pool.py            # Human/Agent Pool → ResourceCandidate 어댑터
  assignment.py      # 매칭 알고리즘(결정론적)
  approvals.py       # 다단계 결재라인(조직도 매니저 체인)
  assignments.py     # 배정 요청·확정(팀 배정 + 다중 confirmed 지원)
  teams.py           # 팀(사람+Agent 그룹) CRUD — "등록→재사용" 패턴
  jobs.py            # Job Queue(+ idempotency)
  sandbox.py         # Docker 샌드박스 명령 구성
  audit.py           # 감사로그
  performance.py     # 성과리뷰(동의 기반)
  cost.py            # 비용 조회
  fairness.py        # 배정 공정성 지표
  retention.py       # 데이터 보관기간 정책(assignment_history 1년)
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선 수동 실행 probe(agent-evaluator SDK, python -m으로 직접 실행)
  agent_evaluation.py # Gate A-G 평가를 Agent 실행 경로에 상시 연결(agent_evaluations 테이블)
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성(`hair bootstrap-admin`이 호출)
  cli.py             # 통합 CLI(`hair` 콘솔 스크립트) — serve/bootstrap-admin/reset-data/eval
  webui.py           # Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙, 26개 탭)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  model_registry.py  # provider별 사용 가능 모델 등록·관리("등록→재사용") + 모델별 LLM 설정 최적화 tier(deep_reasoning/precision_fast/standard) 직접 매핑
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임 + 다이어그램)
  crew_history.py    # Crew 실행 이력(crew_runs) — 팀/즉석 실행의 진행상태·협업다이어그램·토큰사용량 영구 저장
  vectorstore.py     # 지식검색 Agent(VectorDB) — OpenAI 임베딩+SQLite 코사인 유사도
  graphquery.py      # 관계분석 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의
  websearch.py       # 외부 검색 — 실시간 웹 검색(Tavily native 도구, 대화창·Crew 양쪽 동작, TAVILY_API_KEY 필요) + Parallel AI MCP(Crew 전용). Wikipedia 검색 코드는 남아있으나 이 배포 환경 IP가 지속 차단돼 기본 시드에서 뺐다(db.py 참고)
  documents.py       # 외부 자료(.md/.pdf/.html) 업로드·텍스트 직접 입력 → 청크 분할 → VectorDB 색인
  pool_registry.py   # Skill Pool·Tool Pool — 스킬→도구 매핑 데이터화, CrewAI Tool 조립
  data_admin.py      # brief/task/human/agent 하드 삭제(FK 연쇄 삭제 + confirmed 배정 보호) + 운영 데이터 공장 초기화(Agent/Tool/Skill/팀/파이프라인은 유지, `hair reset-data`)
  deliverables.py    # 협업 워크스페이스 — 결과물/댓글, "내 업무", 사람 LLM 보조, Agent 비동기 실행+평가(agent_execution_jobs)
  inbox.py           # 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고)
  dashboard.py       # 매니저 대시보드(팀 Gate 평균+대기 승인 건수+비용 추이)
  conversations.py   # 대화 중심 작업공간 — Task 독립 대화, 기본 AI/Agent/Team 멘션 3경로, skill 그라운딩, Brief 승격
  pipeline_runtime.py # 단계형 개발 파이프라인 엔진(단독/순차 릴레이/병행 위임 단계, Gate A-G 기준 자동 재시도·에스컬레이션)
  reference_pipeline_seeds.py # 레퍼런스 Agent 23종 + Skill/Tool Pool + 소프트웨어 개발/사업·기획 파이프라인 기본 시드
  agent_optimizer.py # Agent별 LLM provider/model/tuning_config 최적화 추천(규칙 기반 + 선택적 LLM 보정, admin)
  id_scheme.py       # Agent/Tool/Skill 표시용 코드 체계(`AGT-REF-001` 등, 종류 구분용) — 정수 PK는 그대로, `code`만 별도 배정
docs/
  OVERVIEW.md        # 지향점·핵심 개념·아키텍처·주요 기능 개요(과거 설계 논의·벤치마크는 git log 참고)
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 811건 통과
```

## 로드맵

핵심 기능은 전부 구현 완료됐고, 그 이후 반복적으로 들어온 사용자 요청에
맞춰 계속 확장돼왔다 — 다음에 뭘 할지는 `docs/OVERVIEW.md`의 "알려진
한계" 절을 참고해서 정한다.

## 변경 이력

버전별 핵심 변경만 요약한다 — 세부 배경·버그 재현 과정은 커밋 메시지
(`git log --oneline --all`)에 날짜·사용자 요청 원문과 함께 남아 있다.

### v0.1.2 (2026-11-09) — 파이프라인 Ollama 적용/구현 단계 버그 수정 + 복잡 업무 처리 로드맵 9건

- 🐛 `crew_runtime.run_crew()`가 단계별 Agent의 `llm_provider`/
  `llm_model`을 전혀 읽지 않아, Ollama로 설정한 파이프라인 단계도
  항상 OpenAI로 조용히 폴백되던 문제 수정(`_resolve_stage_llm`) +
  Ollama 전용 타임아웃 분리(5400s) + 로컬 모델 대용량 출력 시
  OpenAI 임베딩 강제호출로 크래시하던 문제 수정(`memory=False`)
- 🐛 "구현" 단계(`parallel_delegated`, 매니저 위임)가 실제 코드
  대신 위임 계획만 산출물로 남기거나 위임 자체를 빠뜨리는 문제가
  실측으로 반복 확인돼, `sequential_relay`(모듈별 사전 배정 +
  순차 실행)로 전환
- 🐛 `sequential_relay` 다단계에서 마지막 참여자 결과만 남고 앞
  참여자들의 산출물이 조용히 사라지던 버그 수정(역할별 헤더로 전부
  합성해 보존)
- 🐛 파이프라인 1단계가 Brief 원문(`raw_text`) 없이 Task의 짧은
  `role_name`만 보고 요구사항을 반복적으로 오해석하던 문제 수정
- ✨ 파이프라인 중단 시 orphan `running` 실행을 서버 재기동 시
  자동으로 `failed` 처리(`reap_orphaned_pipeline_runs`)
- ✨ Gate `code_syntax`/`requirements_traceability` 주입 조건을
  하드코딩된 한글 단계명 매칭에서 `is_primary_deliverable`/
  `checks_requirements_traceability` 플래그로 전환해 개발 외
  도메인에도 일반화
- ✨ **사업/기획 업무 처리 파이프라인**(7단계) 신규 시드 — 소프트웨어
  개발 외 도메인에도 같은 파이프라인 엔진이 적용됨을 보여주는 두
  번째 템플릿, 레퍼런스 Agent 콘텐츠 카피라이팅 추가(23종)
- ✨ 업무 분해 LLM 제안 + 생성 시점 의존관계 연결, Crew 빌더 Agent
  자기검토(self_review), 자동 단계적 에스컬레이션(마지막 자동
  재시도에 self_review 적용 + 승인자 실제 대화 초대/멘션), 콘텐츠
  타입별(html 등) pluggable 구문 검증기, Crew 빌더 실행 결과 diff
  모드, 셀프배정 결재 단순화(본인 배정은 결재 없이 즉시 확정),
  Task 레벨 비용/재시도 예산 추적·상한 설정
- `hair reset-data` 전용 Gate 통과 로직 불변 확인(기존 7개 Gate
  A-G만으로 계산한 결과와 신규 측정 지표 추가 후 결과가 동일) —
  정보성 지표 추가가 기존 통과/차단 기준을 바꾸지 않음을 회귀
  테스트로 확인

### v0.1.1 (2026-11-03) — 리소스 매칭/RAG 품질 향상 + 결재·GraphDB 가독성 + 사내망 접속 준비

- ✨ 리소스 매칭: 역할명 부분일치(substring) 매칭 + LLM judge 폴백 도입,
  "직접 검색해서 배정"(수동 3번째 단계) 제거
- ✨ RAG 검색 품질 향상 — 관련성 임계값(min_score) + admin 설정 가능한
  top_k/min_score, 청크 오버랩, 코사인+키워드 하이브리드 재순위화
- ✨ 결재 화면 — "내 승인 대기함"에 요청자, "내가 요청한 건"에 결재권자
  (또는 "미지정") 표시
- ✨ GraphDB 관계 조회 화면 가독성 개선 — 원시 ID 대신 요청자명/업무명/
  리소스명/업무 상태로 표시
- ✨ Crew 빌더(즉석 Agent 조합 실행+저장), 배정 취소, TF팀 확장 +
  파이프라인 배정 통합, 자율 개발 크루 파이프라인 재구성
- ✨ 사내망 다중 사용자 접속 준비 — 비루프백 호스트+시크릿 키 미설정
  조합 기동 시 경고, SQLite busy_timeout 적용, Tavily 실시간 웹검색,
  `hair reset-data` 공장 초기화 커맨드, 임베딩 모델 설정 admin 구성화
- 🔧 설정 메뉴 재구성 — "모델 관리"(LLM 설정 최적화·모델 레지스트리)
  admin 전용화, 리소스 관리 그룹 구분선/정렬 정리
- 🔧 화면 상단 도움말 39곳 현행화 + 접기 UI(기본 닫힘) 전환, `hair`
  명령어 도움말 현행화(`reset-data` 설명 보완 + 사내망 접속 가이드)
- 🔧 Team 성과/비용 집계 + 실행 이력 필터, 조직도 용어 재정리(TF팀 배지),
  Agent/Tool/Skill 코드 체계 + 사번 체계 정비
- 🐛 `teams` 인덱스 생성이 기존 DB 마이그레이션보다 먼저 실행돼 서버
  기동이 실패하던 문제 수정

### v0.1.0 (2026-10-26) — 외부 자료 업로드(RAG) + Admin 지식 베이스 관리 + 응답 형식 일관성

- ✨ 대화창·협업 워크스페이스(업무 실행)·지식 베이스에서 로컬 파일
  (.md/.pdf/.html) 업로드 지원(`documents.py` 신설) — 지식 베이스만
  텍스트 직접 입력 포함 VectorDB에 영구 색인하고, 대화창·협업
  워크스페이스는 그 1회만 쓰는 휘발성 첨부다. 청크 분할 없이 통째로
  넣던 기존 색인 경로의 "긴 문서는
  대부분 잘려나가는" 문제도 같이 수정
- ✨ Admin "지식 베이스" 탭 신설 — 색인된 조각(Chunk) 조회/삭제+통계,
  GraphDB 관계 조회+통계(GraphDB는 별도 저장소가 없어 조회·통계만)
- ✨ 임베딩(VectorDB) provider/모델 하드코딩을 admin이 바꿀 수 있는
  설정으로 분리
- 🔧 "자율 개발 크루"의 영역별 중복 개발자(백엔드 4명·프론트엔드 3명·
  데이터/인프라 2명)를 영역별 1명으로 정리(레퍼런스 Agent 28→22개,
  팀 12→6명) — CrewAI hierarchical이 Task를 매니저에게 하나만 주고
  동적으로 위임하는 실제 메커니즘에서 skill_tags가 완전히 동일한
  클론을 여러 개 둬도 기능 차이가 없었다(실측 확인). 남은 "개발자"는
  역할이 드러나도록 "백엔드 개발자"로 개명
- 🐛 Agent persona `expected_output`이 지금까지 CrewAI 실행에서만
  반영되고 대화창 `@멘션` 단발 호출에는 무시되던 비일관 수정
- 🐛 외부 검색(Wikipedia) 실패를 "관련 자료 없음"으로 은폐하던 문제,
  `rag`+`외부검색`처럼 native 스킬을 2개 이상 가진 Agent가 if-체인
  때문에 첫 번째 매칭만 쓰던 버그 수정
- 🐛 member/viewer 권한에서도 모델 레지스트리 조회가 되도록 엔드포인트
  분리, 대화창 스킬 그라운딩 실시간 실행 갭 수정 + 멘션 UX 개선

### v0.0.4 (2026-10-20) — Crew 빌더(멀티에이전트 실행 통합) + 배정 취소 + 버그 수정

- ✨ **Crew 빌더** 신설(기존 "Crew 실행" 탭 대체) — 팀 사전 등록·배정·
  승인 없이 Task 참가자가 Agent를 즉석으로 묶어 바로 실행("업무에 바로
  실행"), 성공한 조합을 팀 또는 파이프라인 단계로 저장("저장/승격"),
  팀·즉석 Crew 실행 전체의 진행 상태·협업 다이어그램·토큰 사용량을
  영구 기록하는 "실행 이력"(`crew_runs` 테이블) 3개 모드로 구성.
- ✨ **배정 취소** 기능 추가 — confirmed/requested 배정을 admin/manager가
  취소할 수 있고, 늦게 들어오는 결재가 취소된 배정을 되살리지 못하게
  대기 중인 결재 단계도 함께 정리.
- ✨ **Agent 역할/인격 상세조정** — Agent별 `goal`/`backstory`/
  `expected_output`을 저장해 CrewAI 자동 생성값을 덮어쓸 수 있고(빈
  값이면 기존처럼 자동 생성), 팀별 기본 `process`/매니저 Agent, 파이프라인
  단계별 `expected_output`도 함께 지원.
- 🐛 같은 업무가 "내게 필요한 조치"에 중복 표시되던 문제 수정 — 파이프라인
  단계 결재 승인 시 `deliverables.status`가 동기화되지 않아 이미 승인된
  결과물이 계속 재노출되던 근본 원인 해결.
- 🐛 파이프라인 Gate B/E가 전혀 표시되지 않던 문제 수정 — 측정 대상이
  아닌 Gate를 숨기는 대신 "미평가" 배지로 표시하고 색상 범례 추가.
- 🔧 Tool/Skill Pool에 수정 기능 추가 — Agent/Skill 매칭에 쓰이는
  이름(`name`)은 백엔드·UI 양쪽에서 수정 대상 제외, 코드 입력창 확대,
  수정 패널을 대상 행 바로 아래에 띄우도록 개선.

## 라이선스

MIT
