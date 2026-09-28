# HAIR-Platform

사람과 AI Agent를 하나의 조직도 위에서 배치·배분·승인·성과리뷰하는 협업 플랫폼.
AgentForge 실습서 저장소의 자매 프로젝트로, AgentForge가 검증한 아키텍처 원칙
(Spec Interpreter·Code Generator·Verifier·Pool·eval_wiring·Web UI 패턴)을
재사용하되 "생성" 축을 "사람-Agent 협업/배분" 축으로 확장한다.

**상태**: Phase 0–6 완료 + Gate A–G가 실제 프로덕션 경로에 상시 연결됨
(F1–F14 전부 구현, `pytest` 159 passed). **F11 Web UI 셸을 실제로 구현**했고, 내비게이션은 기존 RBAC
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
기록해뒀다). 이후 "crewai 기능 적절히 적용됐는지" 재검토에서
`manager_agent_id`+`hierarchical` 조합이 항상 크래시하던 실사용 버그와
`memory=True`/`max_iter` 문서-코드 불일치를 발견해 전부 수정하고,
`task_callback`으로 Task 완료마다 감사로그를 남기도록 세분화했다. 이후
사용자가 실제로 Web UI에서 hierarchical+매니저 지정 조합을 테스트하다가
`task_callback`이 hierarchical에서는 항상 매니저에게 잘못 귀속되는
버그와, 매니저의 `output: null`을 Web UI가 렌더링하다 크래시하는 버그를
추가로 발견해 둘 다 수정했다(`docs/LIMITS.md` §14). **MCP(외부 도구)
연동도 구현 완료**(`run_crew()`가 `crewai-tools`의 `MCPServerAdapter`로
Agent별 `mcp_servers` 설정에 실제로 접속해 도구를 붙인다 —
`AgentModelConfig.mcp_servers`로 설정, `docs/LIMITS.md` §15. 접속 가능한
실제 MCP 서버가 없어 종단간 검증은 미검증). "신규 SaaS 대시보드 프로젝트,
사람+AI Agent 혼합 팀 구성" 시나리오를 F1부터 F14까지 실제로 끝까지
진행해보며 브리프 입력·역할 제안·매칭 실패 안내 문구의 UX 문제 3건을
발견해 수정했다(`docs/LIMITS.md` §16). 이어서 "CrewAI 개념을 제대로
활용하는지" 감사해 RAG 증강(VectorDB/GraphDB)이 개별 실행에만 붙어있고
Crew 협업에는 빠져 있던 실사용 갭을 발견·수정했다(`docs/LIMITS.md`
§17). **VectorDB/GraphDB를 정적 프롬프트 주입 대신 진짜 CrewAI Tool로
전환**했다 — 실제 데모 DB를 확인해보니 VectorDB/GraphDB Agent가 실사용
중엔 하나도 없었다는 것부터 확인한 뒤, `vectorstore.as_crewai_tool()`/
`graphquery.as_crewai_tool()`(`crewai.tools.tool` 데코레이터 기반)을
`run_crew()`(이미 온보딩된 Agent)와 `crewai_agent_generator()`(F4로 새로
생성되는 Agent) 양쪽에 연결해, Agent가 검색 필요 여부를 스스로 판단해
호출하게 했다. 이어서 **Tool Pool·Skill Pool도 실제로 구현했다** —
`tool_pool`·`skill_pool`·`agent_tool_links` 3개 신규 테이블,
`pool_registry.py`(CRUD+도구 조립), API, "도구/스킬 Pool" Web UI 탭까지
붙여서, 이전에 `agent_runtime.py`에 하드코딩됐던 "스킬→도구" 매핑을
데이터로 완전히 옮겼다(ADR 결정 12, `docs/LIMITS.md` §18). 이어서
**각 메뉴에 데이터 삭제/초기화 기능도 구현했다** — 사용자가 명시적으로
선택한 범위(도구/스킬 Pool·사람/AI 에이전트 Pool·brief/task, 활동
기록은 NF10 append-only 트리거 때문에 애초에 제외)에 대해
`data_admin.py`+6개 ADMIN 전용 DELETE 엔드포인트를 구현했다. FK 순서
버그(`sqlite3.IntegrityError`)를 실측으로 발견·수정했고, `confirmed`
상태 배정이 있는 대상은 삭제를 막는 안전장치를 뒀다(`docs/LIMITS.md`
§19). 이어서 사용자가 Agent-Evaluator SDK의 GitHub Skills 링크를 검토해
달라고 요청한 것을 계기로 **"도구/스킬 Pool"에 하니스 방법론 절차
스킬 18개(agent-evaluator 기본 배포 15개 + 이 워크북 자체 발굴 3개)를
기본 노출했다** — HAIR-Platform이 모델링하는 SDLC 팀(AI 에이전트)이
실제로 이 방법론을 따른다는 전제. 이어서 "CrewAI AI Agent가 실제로
필요한 기능인지 확인하고, 필요하면 호출 가능하게 변형해달라"는 요청에
18개를 하나씩 검토한 결과, 17개는 git/PR 판단·SDK 소스 읽기 같은 HITL
절차라 안전하게 도구화할 방법이 없어 `augmentation_mode='none'`(skill_tag
노출만)로 남기고, `recommend-fix-workflow` 1개는 실제 존재하는
`agent_evaluator.integrations.recommend_fix_mcp`(읽기 전용 stdio MCP
서버)를 Tool Pool에 등록해 `augmentation_mode='tool'`로 승격했다 —
mock 없이 실제 서브프로세스 연결·호출까지 실측 확인했다(`docs/LIMITS.md`
§20). 사용자가 "AI 에이전트 생성" 메뉴의 stub/simple_llm/crewai 차이를
물어본 것을 계기로, **14개 탭 전부의 안내 문구를 실제 백엔드 로직 기준으로
상세히 보강했다** — 이 과정에서 스펙 코드 노출 금지 골든셋(GC60, 코드
재점검 R5)이 새로 쓴 문구의 회귀를 실제로 잡아내 수정했다(`docs/LIMITS.md`
§21). 이어서 사용자 요청("Phase 1부터 시작해줘")에 따라 **협업
워크스페이스(사람-AI 결과물 전달) Phase 1을 실제로 구현했다** —
신규 `deliverables`/`deliverable_comments` 테이블, "내 업무" 탭 +
결과물 타임라인/댓글/"LLM으로 도와줘"/AI 에이전트 실행. 실측 중
`human_pool.user_id`를 채우는 API가 지금까지 전혀 없어 F8("내 성과점수
조회")도 실제로는 한 번도 동작한 적이 없었을 선행 버그를 발견·수정했다
(`docs/LIMITS.md` §22). 이어서 **Phase 2(산출물 승인 게이트)도 구현했다**
— Agent 산출물이 생기면 F7 다단계 결재라인·ADR 3-A 자기승인 방지를 새
로직 없이 그대로 재사용해 자동으로 결재가 걸리고, 워크스페이스 안에서
바로 승인/반려할 수 있다(`docs/LIMITS.md` §23). 이어서 사용자 요청
("Phase 3 진행해줘")에 따라 **Phase 3(Agent 평가 인라인 연결)도
구현했다** — Agent 실행 경로가 실제 실행을 매번 `@agent_eval`로 감싸
Gate A-G를 자동 측정하고 신규 `agent_evaluations` 테이블(append-only)에
쌓는다. 기존 `eval_wiring.py`는 수동 실행 probe였을 뿐 실제 서비스
경로에 연결돼 있지 않았던 것과 달리, 이번엔 매 execute-agent 호출마다
상시 측정된다. `agent_pool.performance_score`(관리자 수동, F8)는 그대로
두고 자동 측정치는 `aggregate_agent_score()`로 분리해 보여준다
(`docs/LIMITS.md` §24). 이어서 사용자 요청("Phase 4 진행해줘")에 따라
**Phase 4(execute-agent 비동기화)도 구현했다** — 원래는 LLM 응답이 올
때까지 HTTP 요청을 붙잡고 있던 동기 호출이었지만, 이제 `POST
.../execute-agent`는 검증만 마치고 202를 즉시 반환하며 실제 LLM 호출은
FastAPI `BackgroundTasks`로 응답 전송 후 처리된다. 신규
`agent_execution_jobs` 테이블(`running`/`succeeded`/`failed`)로 진행
상태를 추적하고, Web UI는 이를 폴링해 "생성 중…" 카드를 보여주다가
완료되면 갱신한다. 원래 설계(`docs/COLLABORATION_PLATFORM_PLAN.md`
Phase C)가 그렸던 CrewAI 도구 호출 단위 일시정지는 협업 워크스페이스가
실제로 쓰는 경로(단일 LLM 호출)엔 적용 대상이 없어 스코프에서 제외했다
(`docs/LIMITS.md` §25). 이어서 사용자 요청("Phase1~4까지... 분석하고
개선해줘")에 따라 **Phase 1-4를 다시 감사해 크로스-Phase 버그 3건을
실제로 재현·수정했다** — (1) 사람 당사자와 Agent 요청자를 같은 task에
함께 confirmed하면(협업의 기본 전제) F5/F6 시절 재배치 로직이
resource_type을 구분하지 않아 먼저 확정된 쪽이 조용히 superseded되던
버그, (2) `data_admin.py`가 Phase 1-4의 새 테이블(`deliverables` 등)을
몰라 남아있는 산출물 때문에 task 삭제가 FK 위반 500으로 실패하던 버그,
(3) 서버가 실행 도중 죽으면 `agent_execution_jobs`가 영원히 'running'
으로 남아 실행 버튼이 복구 불가능하게 막히던 문제. 추가로 Phase 3의
`aggregate_agent_score()`가 실제로는 어떤 화면에도 연결 안 돼 있던 걸
발견해 "AI 에이전트" 탭에 "자동평가(Gate/TCR)" 열로 노출했다
(`docs/LIMITS.md` §26). 사용자가 같은 요청을 다시 보내 **2회차 리뷰도
진행했다** — `start_agent_execution()`이 같은 task에 이미 실행 중인
job이 있어도 확인 없이 새 job을 또 만들어(Web UI 버튼 비활성화만으로는
빠른 연속 클릭·여러 탭·직접 API 호출을 못 막음) 실제 LLM 호출이
이중으로 나갈 수 있던 문제에 서버 쪽 409 가드를 추가했고, 트리거 이후
Agent가 삭제/오프보딩되면 `agent_not_active` 같은 내부 코드 문자열이
그대로 Web UI에 노출되던 것을 한국어 문구로 번역했다(`docs/LIMITS.md`
§27). 사용자가 세 번째로 같은 요청을 보내 **3회차 리뷰도 진행했다** —
`decide_approval()`은 승인/반려 둘 다 감사로그에 남기는데 Agent 실행은
성공만 기록하고 실패는 전혀 안 남아 HOTL 감독자가 실패 이력을
감사로그 화면에서 볼 수 없던 비대칭을 발견해 `agent_execution_failed`
액션을 추가했다(`docs/LIMITS.md` §28).
**실제 OpenAI API로
SDLC 8역할 에이전트 생성까지 실측
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
pytest tests/ -q        # 골든셋 159건(오프라인, 실제 LLM 호출 없음 — 전부 mock)
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
  app.py             # FastAPI 라우트(60개 엔드포인트, GET / 포함)
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
  eval_wiring.py      # Gate A/B/D/E/G 평가 배선 수동 실행 probe(agent-evaluator SDK, python -m으로 직접 실행)
  agent_evaluation.py # Phase 3 — Gate A-G 평가를 Agent 실행 경로에 상시 연결(agent_evaluations 테이블)
  bootstrap.py       # 콜드스타트 — 최초 ADMIN 계정 생성 CLI
  webui.py           # F11 Web UI 셸(단일 페이지 바닐라 JS, GET / 에서 서빙)
  llm.py             # 실제 Anthropic/OpenAI/Ollama 연동(provider/model 선택 가능)
  agent_runtime.py   # 온보딩된 Agent에게 온디맨드로 업무를 맡기는 실행 경로(RAG 증강 포함)
  crew_runtime.py    # 실제 CrewAI 연동(단일 생성 + 다중 Agent 협업/위임)
  vectorstore.py     # 지식검색 Agent(VectorDB) — OpenAI 임베딩+SQLite 코사인 유사도
  graphquery.py      # 관계분석 Agent(GraphDB) — 기존 관계형 데이터 그래프 질의, NF9 경계
  pool_registry.py   # Skill Pool·Tool Pool(ADR 결정 12) — 스킬→도구 매핑 데이터화, CrewAI Tool 조립
  data_admin.py      # brief/task/human/agent 하드 삭제(FK 연쇄 삭제 + confirmed 배정 보호)
  deliverables.py    # 협업 워크스페이스 Phase 1-4 — 결과물/댓글, "내 업무", 사람 LLM 보조, Agent 비동기 실행+평가(agent_execution_jobs)
docs/
  SPEC.md            # 명세(F1–F14, NF1–13, 로드맵, DoD)
  ADR.md             # 설계 결정 11건
  LIMITS.md          # 알려진 갭(정직하게 기록)
  RETROSPECTIVE.md   # 기획 대비 실제 구현 비교
  COLLABORATION_PLATFORM_PLAN.md  # 사람-AI 협업(결과물 전달/HITL/HOTL) 기획안 — Phase A/B 구현 완료
  HAIR_MASTER_PLAN.md             # 위 기획안을 포함하는 종합 기획/설계/개발 계획 — Phase 1-3 구현 완료
  PHASE1_DELIVERABLE_SPEC.md      # Phase 1 구현 스펙(스키마 DDL·API 계약·GC127-137) — 구현 완료
.claude/skills/rbac-permission-audit/  # 신규 발굴 스킬(3회+ 반복 확인)
.github/workflows/ci.yml               # pytest+ruff+black CI
tests/unit/                            # 골든셋 159건
```

## 로드맵

`docs/SPEC.md` §7 참고. Phase 0(부트스트랩)~Phase 6(Skills화)까지 완료,
Phase 7(`release_hold`)은 대기 중 — 진행하려면 `docs/LIMITS.md`의 갭 목록을
먼저 검토할 것을 권장한다.

## 라이선스

MIT
