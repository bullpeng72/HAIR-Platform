# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 함께 배치하고, 업무를 배분하고,
결재를 거쳐 확정하고, 성과를 리뷰하는 협업 플랫폼. AgentForge(Agent 생성
아키텍처를 검증하는 실습서 저장소)의 자매 프로젝트로, 그 아키텍처를
"Agent 생성"에서 "사람-Agent 협업/배분"으로 확장한다.

**현재 상태**: 조직도 기반 배치·배분·승인, Agent 생성·실행(Gate A-G
자동 채점), CrewAI 다중 Agent 협업, 로그인 직후 바로 쓰는 **대화 중심
작업공간**(기본 AI 자동응답 / 특정 Agent·Team 멘션 / 업무 요청 승격),
기본 제공 **단계형 업무 파이프라인 4종**(소프트웨어 개발·사업/기획·
제안서 작성·실적 보고 — Gate 기준 자동 재시도 + 반복 실패 시에만 사람
개입), Agent별 LLM 모델/설정 최적화 추천(admin), **통합 CLI**
(`hair serve`/`bootstrap-admin`/`reset-data`/`eval`)까지 전부 구현돼
실제로 쓸 수 있다. 테스트 917건 통과, API 엔드포인트 175개, Web UI
26개 탭. 개념·아키텍처 전체 설명은 `docs/OVERVIEW.md`, 설계 결정의
배경·과거 버그 수정 이력은 `git log`(커밋 메시지에 날짜·사용자 요청
원문이 그대로 남아 있다)를 참고.

## 무엇을 할 수 있나

### 1. 사람과 AI Agent를 함께 운용
- 조직도 위에 사람·Agent를 같이 배치하고 업무에 배분한다. 매칭은
  자동(완전일치 우선, 그 외엔 가용성·적합도·비용으로 점수화)이고,
  확정은 조직도 상사 체인을 따르는 다단계 결재를 거친다(자기 승인은
  막힘) — 다만 **본인을 배정하는 "셀프배정"은 결재 없이 즉시
  확정**된다.
- 한 업무에 여러 사람·여러 Agent·여러 팀이 동시에(confirmed 상태로)
  배정될 수 있다 — 교체하려면 어떤 배정을 대체하는지 명시해야 한다.
- **팀**: 사람 N명 + Agent M명을 역할과 함께 묶어 등록해두고 여러
  업무에 재사용한다. 협업 워크스페이스의 "팀 Agent 실행"으로 팀의
  Agent 멤버 전원을 실제 CrewAI Crew로 한 번에 실행하면, 결과가
  Agent별 결과물+결재 체인으로 그 업무에 그대로 연결된다.
- **협업 워크스페이스**: 결과물(deliverable) 타임라인 + 댓글. 사람이
  작성한 결과물도 "검토 요청"을 누르면 Agent 결과물과 같은 결재를
  거친다. 결과물은 일반 텍스트/마크다운/HTML/코드 중 형식을 지정할
  수 있고, 화면에서 그 형식대로 렌더링된다(마크다운 서식·코드 구문
  강조·HTML 미리보기 전부 포함, 복사/다운로드/소스 보기 지원).

### 2. 대화창에서 바로 일 시키기
- 로그인하면 바로 대화창부터 시작한다. 아무것도 지정하지 않으면 기본
  AI가 답하고, `@에이전트명`/`@팀명`으로 특정 Agent나 팀을 멘션해
  실행시킬 수 있다.
- 대화에서 쓸 스킬을 직접 고를 수 있는데, **"개발업무" 스킬은
  일반 사용자에게 불필요하므로 기본적으로 목록에서 숨겨진다** —
  "개발업무 스킬도 보기"를 켜야 보이고, 평소엔 일반 업무용 스킬
  (51개 중 다수)·도구(12개)만 노출된다. (Agent/Task에 미리 등록해둔
  스킬 태그나 CrewAI 실행 자체는 이 필터와 무관하게 전부 동작한다.)
- 특정 업무(Task)에 종속된 대화는 그 업무로 자동 흡수되고, 대화가
  커지면 업무 요청(Brief)으로 승격할 수 있다.

### 3. Agent 만들고 실제로 실행하기
- Anthropic/OpenAI/Ollama 중 하나로 실제 LLM Agent를 돌릴 수 있다
  (설정하지 않으면 비용 없는 무료 스텁으로 동작). provider/model은
  "모델 레지스트리"에 등록해두면 전 화면에서 드롭다운으로 공유된다.
- 실행마다 **Gate A-G**(목표달성·행동무결성·재현성·성능계약·보안
  경계·멀티에이전트조율·관찰가능성)가 자동으로 채점돼 기록에 남는다.
- **Crew 빌더**: 여러 Agent를 즉석으로 묶어 실제 CrewAI로 같이
  실행한다(sequential/hierarchical), 위임 관계를 다이어그램으로
  보여주고, 실행 이력에서 이전 결과와 차이(diff)를 바로 비교할 수
  있다. Agent가 스스로 초안을 원 요구사항과 대조해 검토·수정하는
  "자가검토" 옵션도 있다. 성공한 조합은 팀이나 파이프라인 단계로
  저장(승격)할 수 있다.
- 과거 산출물 검색(지식검색)·조직 관계 질의(관계조회)·실시간 웹
  검색(Tavily)이 Agent가 필요하면 스스로 판단해 호출하는 도구로
  이미 연결돼 있고, MCP로 외부 도구를 추가 연결할 수도 있다.

### 4. 복잡한 업무는 파이프라인으로
- 기본 제공 파이프라인 **4종**: 소프트웨어 개발, 사업/기획 업무
  처리, 제안서 작성, 실적 보고. 각 단계는 Gate 기준으로 자동
  채점·재시도되고, 반복 실패해야만 사람에게 에스컬레이션된다
  (계획 승인은 1회, 이후는 최소 개입 — BMAD-METHOD 방식).
- 화면에서 직접 단계를 구성하는 빌더도 제공되므로, 개발 외 업무에도
  같은 엔진을 그대로 재사용할 수 있다.
- 업무를 등록할 때 LLM에게 하위 업무 분해(역할·필요 스킬·선행 업무
  의존관계)를 제안받아 한 번에 배치로 생성할 수도 있다.

### 5. 지식 쌓아서 Agent가 찾아보게 하기
- "지식 베이스" 탭(admin/manager)에서 로컬 파일(.md/.pdf/.html)을
  올리거나 텍스트를 직접 입력하면 영구적으로 색인된다 — 색인되면
  "지식검색" 스킬을 가진 모든 Agent가 대화창·CrewAI 실행 양쪽에서
  찾아낸다.
- 대화창·협업 워크스페이스에서 올리는 참고자료는 **그 1회만 쓰는
  휘발성 첨부**다 — 영구 보관하려면 지식 베이스에 올려야 한다.
- 같은 탭에서 색인된 조각 조회/삭제+통계, 조직 관계 조회+통계까지
  관리한다.

### 6. 운영 관리
- 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고),
  매니저 대시보드(팀 Gate 평균·대기 승인 건수·비용 추이), 검색·
  필터·활동 기록(감사로그).
- `hair reset-data`로 운영 데이터(사용자·업무·대화·실행 이력·팀 등)
  만 공장 초기화할 수 있다 — Agent/Tool/Skill Pool·파이프라인
  템플릿은 그대로 둔다(배포 전 깨끗한 상태로 준비할 때 사용).

### 7. 권한(RBAC)
4개 역할(`admin`/`manager`/`member`/`viewer`) — 조회·생성은
admin+manager 공용이 많고, 삭제·사용자 생성·Agent 온보딩 승인처럼
조직 구조에 직접 영향을 주는 동작은 admin 전용.

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]"
# `[dev]`만 설치하면 `tests/unit`의 CrewAI 연동 테스트 일부가 테스트
# 실행 시점에 `ModuleNotFoundError`로 깨진다. `all`(= crewai+mcp+llm+
# documents 묶음, pyproject.toml 참고)까지 같이 설치해야 아래 골든셋이
# 전부 오프라인으로 깨끗하게 통과한다.
pytest tests/ -q        # 골든셋 917건 통과(오프라인, 실제 LLM 호출 없음 — 전부 mock)
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
레지스트리·팀·활동 기록 등) Agent(26종)/Tool·Skill Pool/파이프라인
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
  app.py             # FastAPI 라우트(175개 엔드포인트, GET / 포함)
  pool.py            # Human/Agent Pool → ResourceCandidate 어댑터
  assignment.py      # 매칭 알고리즘(결정론적)
  match_llm_judge.py # 매칭 결과 LLM judge 폴백(역할명 부분일치로도 못 찾을 때)
  approvals.py       # 다단계 결재라인(조직도 매니저 체인)
  assignments.py     # 배정 요청·확정(팀 배정 + 다중 confirmed + 셀프배정 즉시확정 지원)
  teams.py           # 팀(사람+Agent 그룹) CRUD — "등록→재사용" 패턴
  jobs.py            # Job Queue(+ idempotency)
  sandbox.py         # Docker 샌드박스 명령 구성
  audit.py           # 감사로그
  performance.py     # 성과리뷰(동의 기반)
  cost.py            # 비용 조회(전역 + Task 단위)
  fairness.py        # 배정 공정성 지표
  retention.py       # 데이터 보관기간 정책(assignment_history 1년)
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선 수동 실행 probe(agent-evaluator SDK, python -m으로 직접 실행)
  agent_evaluation.py # Gate A-G 평가를 Agent 실행 경로에 상시 연결(agent_evaluations 테이블) + 구문검사
  content_validators.py # content_type별(html 등) pluggable 구문 검증기 디스패치
  requirements_traceability_llm.py # "코드 검증" 단계 요구사항 추적성 LLM judge 채점(정보성 지표)
  brief_decompose_llm.py # 업무 분해 LLM 제안(역할·필요 스킬·선행 업무 의존관계)
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성(`hair bootstrap-admin`이 호출)
  cli.py             # 통합 CLI(`hair` 콘솔 스크립트) — serve/bootstrap-admin/reset-data/eval
  webui.py           # Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙, 26개 탭)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  model_registry.py  # provider별 사용 가능 모델 등록·관리("등록→재사용") + 모델별 LLM 설정 최적화 tier(deep_reasoning/precision_fast/standard) 직접 매핑
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임 + 다이어그램 + 자가검토)
  crew_history.py    # Crew 실행 이력(crew_runs) — 팀/즉석 실행의 진행상태·협업다이어그램·토큰사용량 영구 저장
  vectorstore.py     # 지식검색 Agent(VectorDB) — OpenAI 임베딩+SQLite 코사인 유사도
  graphquery.py      # 관계조회 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의
  websearch.py       # 외부 검색 — 실시간 웹 검색(Tavily native 도구, 대화창·Crew 양쪽 동작, TAVILY_API_KEY 필요) + Parallel AI MCP(Crew 전용). Wikipedia 검색 코드는 남아있으나 이 배포 환경 IP가 지속 차단돼 기본 시드에서 뺐다(db.py 참고)
  documents.py       # 외부 자료(.md/.pdf/.html) 업로드·텍스트 직접 입력 → 청크 분할 → VectorDB 색인
  pool_registry.py   # Skill Pool·Tool Pool — 스킬→도구 매핑 데이터화 + 개발업무/일반업무 category 분류, CrewAI Tool 조립
  data_admin.py      # brief/task/human/agent 하드 삭제(FK 연쇄 삭제 + confirmed 배정 보호) + 운영 데이터 공장 초기화(Agent/Tool/Skill/파이프라인은 유지, 팀은 초기화, `hair reset-data`)
  deliverables.py    # 협업 워크스페이스 — 결과물/댓글, "내 업무", 사람 LLM 보조, Agent 비동기 실행+평가(agent_execution_jobs)
  inbox.py           # 통합 인박스(결재 대기+Agent 실행 실패+낮은 Gate 점수 경고)
  dashboard.py       # 매니저 대시보드(팀 Gate 평균+대기 승인 건수+비용 추이) + Task 재시도 합산
  conversations.py   # 대화 중심 작업공간 — Task 독립 대화, 기본 AI/Agent/Team 멘션 3경로, skill 그라운딩, Brief 승격
  pipeline_runtime.py # 단계형 업무 파이프라인 엔진(단독/순차 릴레이/병행 위임 단계, Gate A-G 기준 자동 재시도·에스컬레이션, Task 재시도 예산)
  reference_pipeline_seeds.py # 레퍼런스 Agent 26종 + Skill/Tool Pool + 파이프라인 템플릿 4종(소프트웨어 개발/사업·기획/제안서 작성/실적 보고) 기본 시드
  agent_optimizer.py # Agent별 LLM provider/model/tuning_config 최적화 추천(규칙 기반 + 선택적 LLM 보정, admin)
  id_scheme.py       # Agent/Tool/Skill 표시용 코드 체계(`AGT-REF-001` 등, 종류 구분용) — 정수 PK는 그대로, `code`만 별도 배정
docs/
  OVERVIEW.md        # 지향점·핵심 개념·아키텍처·주요 기능 개요(과거 설계 논의·벤치마크는 git log 참고)
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 917건 통과
```

## 로드맵

핵심 기능은 전부 구현 완료됐고, 그 이후 반복적으로 들어온 사용자 요청에
맞춰 계속 확장돼왔다 — 다음에 뭘 할지는 `docs/OVERVIEW.md`의 "알려진
한계" 절을 참고해서 정한다.

## 변경 이력

버전별 핵심 변경만 한 줄로 요약한다 — 자세한 배경·버그 재현 과정은
커밋 메시지(`git log --oneline --all`)에 날짜·사용자 요청 원문과 함께
남아 있다.

| 버전 | 날짜 | 핵심 변경 |
|------|------|-----------|
| v0.1.3 | 2026-11-11 | Skill/Tool "개발업무/일반업무" 분류(대화창 기본 숨김+필요시 노출) · HAIR 자체 Skill 이름 30개 한글화 · 제안서 작성/실적 보고 파이프라인 추가(템플릿 4종) |
| v0.1.2 | 2026-11-09 | 파이프라인 Ollama 실제 적용 + "구현" 단계 합성 버그 수정(위임 계획만 남던 문제) · 사업/기획 파이프라인 추가 · 자동 단계적 에스컬레이션·셀프배정 즉시확정·Task 재시도 예산 등 로드맵 9건 |
| v0.1.1 | 2026-11-03 | 리소스 매칭/RAG 검색 품질 향상 · 결재·GraphDB 화면 가독성 개선 · Crew 빌더 신설 · 사내망 다중 사용자 접속 준비(`hair reset-data` 포함) |
| v0.1.0 | 2026-10-26 | 외부 자료 업로드(RAG) 지원 + Admin "지식 베이스" 탭 신설 · Agent 응답 형식(`content_type`) 일관성 수정 |
| v0.0.4 | 2026-10-20 | Crew 빌더 전신(멀티 Agent 즉석 실행 통합) · 배정 취소 기능 · Agent 역할/인격 상세조정 |

## 라이선스

MIT
