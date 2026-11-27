"""기본 도구·스킬 Pool 시드(`db.py`에서 분리 — 2026-11-27, 개선 제안 6-2).

`db.init_schema()`가 부른다.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from hair_platform.db_migrations import (
    _FUNCTIONAL_SKILL_DESCRIPTIONS,
    _rename_native_tools,
)

def _seed_default_pools(conn: sqlite3.Connection) -> None:
    """사용자 요청, 2026-09-27, "tool_pool·skill_pool 테이블도
    실제로 구현해줘" — VectorDB/GraphDB를 감싼 native Tool과, 이걸 쓰는
    skill_pool 항목을 기본 제공한다. 이전까지 `agent_runtime.py`의
    `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS` 하드코딩이 하던 "스킬→도구"
    매핑을 데이터로 옮긴 것 — 이 시드가 없으면 기존 vectordb/graphdb
    skill_tags를 가진 Agent가 갑자기 도구를 못 받게 되므로 반드시 필요하다.
    `name` UNIQUE 제약으로 `INSERT OR IGNORE`가 매 기동마다 안전하게
    멱등이다(이미 있으면 아무 것도 안 함 — 관리자가 이후 수정해도 안
    덮어씀).

    사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료 검색이 가능한
    API(가능한 무료)를 도구화해서... 추가해줘")로 `websearch.py`(Wikipedia
    검색, API 키 불필요) 기반 세 번째 native 도구를 같은 방식으로
    추가했다 — VectorDB(과거 산출물)·GraphDB(조직 관계)는 둘 다 조직
    내부 데이터만 다뤄, "조직 밖 일반 지식"이 필요한 질문엔 답을 못
    하던 갭을 메운다.

    이어서 사용자 요청(2026-09-29, "일반 웹 검색 기능을 Tool로 제공
    가능한지 확인해줘" → "진행해줘")으로 Parallel AI의 무료 공개 MCP
    서버(`mcp_remote`, 계정·키 불필요)도 추가했다 — Wikipedia는 자체
    백과사전 색인만 다뤄 "최신 뉴스" 같은 진짜 일반 웹 정보는 여전히
    못 찾았다. recommend_fix(mcp_stdio)와 같은 자리에 두되 종류만
    `mcp_remote`다."""
    native_tools = [
        (
            "VectorDB 내부지식검색",
            "vectorstore.search()를 감싼 네이티브 CrewAI Tool",
            "vectordb_search",
        ),
        (
            "GraphDB 조직배정관계조회",
            "graphquery.context_for_prompt()를 감싼 네이티브 CrewAI Tool",
            "graphdb_query",
        ),
        (
            "스킬·도구 목록 조회",
            (
                "등록된 스킬(이름·분류·지침 요약)과 사용 가능한 도구 목록을 조회하는 "
                "네이티브 Tool — 새 스킬을 설계하기 전에 중복 여부와 연결할 수 있는 "
                "도구를 확인한다(2026-11-21, '스킬생성' 스킬용)."
            ),
            "skill_catalog_lookup",
        ),
        (
            "실시간 웹 검색(Tavily)",
            (
                "websearch.tavily_context_for_prompt()를 감싼 네이티브 CrewAI "
                "Tool — API 키 필요(TAVILY_API_KEY). 'Parallel Search'와 달리 "
                "순수 HTTP 호출이라 대화창(정적 주입)·Crew 실행 양쪽에서 "
                "동일하게 동작한다(사용자 요청, 2026-10-26, '대화에서 외부 "
                "검색이 가능한 방법을 제안해줘')."
            ),
            "tavily_search",
        ),
        (
            "실시간 뉴스 검색(Tavily, 최근 7일)",
            (
                "websearch.tavily_news_context_for_prompt()를 감싼 네이티브 "
                "CrewAI Tool — API 키 필요(TAVILY_API_KEY). 일반 검색과 달리 "
                "Tavily의 topic='news'+days 파라미터로 결과를 실제로 최근 N일 "
                "이내로 제한한다(관련도 순 일반 검색은 '최신'을 보장 못 함) "
                "— 사용자 요청(2026-11-17, '최신(1주일) 뉴스와 인사이트를 "
                "제공해 주는 Skill 추가')."
            ),
            "tavily_news_search",
        ),
        (
            "코드 저장소 조회",
            (
                "관리자가 등록한 Git 저장소의 커밋된 파일을 읽는 네이티브 Tool(읽기 전용) — 파일 목록·"
                "파일 내용(줄 범위)·코드 검색·최근 커밋. 미추적·무시된 파일과 비밀 파일(.env·키 등)은 "
                "읽지 않는다(2026-11-24, P0-3 저장소 연동 1단계, `code_repos.py`)."
            ),
            "code_repository_read",
        ),
        (
            "사내 DB 조회",
            (
                "관리자가 등록한 업무 DB(SQLite·PostgreSQL·MySQL)를 읽기 전용으로 조회하는 네이티브 Tool — 연결 목록·"
                "테이블 목록·열 구조·SELECT 조회(행 수·시간 상한). 쓰기·DDL은 SQL 검사와 읽기 전용 연결 두 겹으로 막고, "
                "조회마다 활동 기록을 남긴다(2026-11-26, P1-9, `data_connectors.py`)."
            ),
            "database_query",
        ),
        (
            "메일 수신함 조회",
            (
                "관리자가 등록한 업무 메일함(IMAP)을 읽기 전용으로 조회하는 네이티브 Tool — 메일함 목록·최근 메일 검색·"
                "본문 읽기(첨부는 이름·크기만). 읽음 표시도 바꾸지 않고, 메일함별 공개 범위로 요청자를 확인하며, "
                "조회마다 활동 기록을 남긴다(2026-11-26, P1-9 2단계, `mail_inbox.py`)."
            ),
            "mail_inbox_read",
        ),
        (
            "캘린더 조회",
            (
                "관리자가 등록한 업무 캘린더(iCal/ICS 주소 또는 .ics 파일)의 일정을 읽기 전용으로 조회하는 네이티브 Tool — "
                "기간·낱말로 일정 목록(반복 일정 펼침, 예외·변경 회차 반영, 취소 일정 제외). 캘린더별 공개 범위로 요청자를 "
                "확인하고 조회마다 활동 기록을 남긴다(2026-11-26, P1-9 3단계, `calendar_feeds.py`)."
            ),
            "calendar_read",
        ),
    ]
    # 사용자 요청(2026-10-26, "도구/skill에서 동작하지 않는 검색은
    # 삭제해줘(한시적인 오류인 경우 잠정 보류)") — "외부 자료 검색
    # (Wikipedia)"를 이 시드에서 뺐다. 재확인 결과 일시적 장애가 아니라
    # Wikimedia가 이 배포 환경의 IP를 robot policy 위반으로 지속
    # 차단 중이었다(세션 내내 여러 차례 재시도해도 동일 403, 참고:
    # `websearch.py` 모듈 docstring). 구현 코드(`websearch.search()`/
    # `context_for_prompt()`/`as_crewai_tool()`)는 지우지 않고 그대로
    # 남겨뒀다 — 다른 네트워크 환경에 배포하면 다시 쓸 수 있다. 되살리려면
    # 이 리스트에 항목을 다시 추가하고 `pool_registry.NATIVE_TOOL_
    # FACTORIES`에 `"web_search": websearch.as_crewai_tool`을 다시 붙이면
    # 된다. "일반웹검색"(Parallel Search, MCP)은 Crew 실행에서 정상
    # 동작하는 걸 이미 확인했으므로(대화창 전용 제약은 설계상 한계지
    # 오류가 아님) 그대로 둔다.
    _rename_native_tools(conn)
    tool_ids: dict[str, int] = {}
    for name, description, native_id in native_tools:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
            "VALUES (?, ?, 'native', ?)",
            (name, description, json.dumps({"native_id": native_id})),
        ).rowcount
        if inserted and native_id == "code_repository_read":
            # 개발업무 도구(2026-11-24) — 처음 만들 때만 분류를 정한다(이후 admin 변경 존중).
            conn.execute("UPDATE tool_pool SET category = 'sw_dev' WHERE name = ?", (name,))
        row = conn.execute(
            "SELECT id FROM tool_pool WHERE name = ?", (name,)
        ).fetchone()
        tool_ids[native_id] = row["id"]

    skills = [
        (
            "내부지식검색",
            _FUNCTIONAL_SKILL_DESCRIPTIONS["내부지식검색"][1],
            "vectordb_search",
            "tool",
        ),
        (
            "조직배정관계조회",
            _FUNCTIONAL_SKILL_DESCRIPTIONS["조직배정관계조회"][1],
            "graphdb_query",
            "tool",
        ),
        (
            "실시간웹검색",
            "실시간 일반 웹 검색 역량(Tavily, API 키 필요) — 대화창에서도 동작",
            "tavily_search",
            "tool",
        ),
        (
            "최신정보필수확인",
            (
                "최신 외부 정보 누락이 치명적인 역할(시황/뉴스/규정 변경 "
                "모니터링 등)용 — Agent가 검색할지 말지 스스로 판단하게 "
                "두지 않고, 매 실행마다 Tavily 검색 결과를 Task description에 "
                "강제로 끼워 넣는다. 'tool' 모드(실시간웹검색)는 Agent가 "
                "호출 여부를 스스로 정하지만, 'always'는 판단을 거치지 않고 "
                "항상 주입한다는 점이 다르다(`skill_pool.augmentation_mode` "
                "'always' 값의 실제 사용례, 사용자 요청 2026-10-27)."
            ),
            "tavily_search",
            "always",
        ),
        # 사용자 요청(2026-11-17, "딥리서치 skill과, AI 빅테크와 AI기반
        # 개발/일반 업무 혁신 관련 최신(1주일) 뉴스와 인사이트를 제공해
        # 주는 Skill을 추가해줘") — 둘 다 `augmentation_mode='tool'`로
        # 둔다('always'로 하면 대화/스킬 드롭다운 경로에서 사용자가 입력한
        # 문장을 그대로 검색어로 쓰는데, 주제와 무관한 일반 문장이면 엉뚱한
        # 걸 검색해버린다 — 'tool'이면 최소한 진짜 CrewAI Agent 실행
        # 경로에서는 Agent가 스스로 더 적절한 검색어를 지어 호출할 수
        # 있다). "실시간웹검색"과 같은 Tavily 일반 검색 Tool을 그대로
        # 재사용하되(새 Tool 불필요), 설명만 "여러 번 교차 검색하는
        # 심층 조사 방법론"으로 다르게 지시한다.
        (
            "딥리서치",
            (
                "한 번의 검색으로 끝내지 않는 심층 조사 방법론이다 — 주제를 "
                "여러 하위 질문으로 쪼개 각각 검색하고, 서로 다른 출처의 "
                "내용을 교차 검증한 뒤, 결과를 종합해 근거(출처 URL 포함)와 "
                "함께 구조화된 보고서로 정리한다. 최소 3회 이상 검색한 "
                "뒤에만 결론을 낸다 — 검색 한 번의 결과만으로 단정하지 "
                "않는다."
            ),
            "tavily_search",
            "tool",
        ),
        (
            "AI업계동향브리핑",
            (
                "OpenAI·Anthropic·Google·Microsoft·Meta 등 AI 빅테크와 AI "
                "기반 개발도구·업무자동화 분야의 최근 1주일 이내 뉴스를 "
                "실제로 검색해 가져온다 — 검색되지 않은 내용을 추측해서 "
                "지어내지 않는다. 단순 뉴스 나열이 아니라, 검색된 사실과 "
                "그로부터 이끌어낼 수 있는 인사이트(이 조직의 개발/업무에 "
                "주는 시사점)를 구분해 제시한다."
            ),
            "tavily_news_search",
            "tool",
        ),
    ]
    for name, description, native_id, augmentation_mode in skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, ?, ?)",
            (name, description, json.dumps([tool_ids[native_id]]), augmentation_mode),
        )

    # 사용자 요청(2026-09-28, "도구/스킬 Pool에 기본 노출해줘") — HAIR-Platform이
    # 모델링하는 SDLC 팀(백엔드 개발자·QA 등 역할의 AI 에이전트)이 실제로 이
    # 하니스 방법론/agent-evaluator 생태계를 따르는 조직이라는 전제 하에, 그
    # 생태계의 절차 스킬(SKILL.md)들을 Skill Pool에 기본 노출한다. 15개는
    # agent-evaluator[sdk] 기본 배포(Skills/)에 이미 포함된 것, 3개는 이
    # 저장소(AgentForge_Workbook)에서 자체 발굴했지만 아직 그 기본 배포로
    # 역이식되지 않은 것(`.claude/skills/background-real-verification` 등).
    # `augmentation_mode='none'`인 이유: 이 스킬들은 CrewAI Tool로 감쌀 수 있는
    # 실행 가능한 함수가 아니라 Claude Code/OpenCode 세션이 참고하는 절차
    # 문서라서, VectorDB/GraphDB처럼 자동으로 붙일 도구가 없다 — Agent가 이
    # skill_tag를 갖고 있다는 표시만 남긴다(도구 자동화가 생기면 'tool'로
    # 승격 가능).
    agent_eval_skills = [
        (
            "abtest-decision-workflow",
            (
                '"agent-eval abtest"로 두 버전(또는 세 버전 이상)을 비교하기 전에 '
                "주 지표(guardrail metric)를 미리 정하고, 결과를 책임 있게 해석한다."
            ),
        ),
        (
            "checklist-confidence-audit",
            (
                "승인 큐의 체크리스트 자동채점이 실제로 얼마나 정확한지 주기적으로 "
                "점검한다."
            ),
        ),
        (
            "claims-audit-ci",
            (
                'CI에 "agent-eval claims audit"를 등록해 TTL 초과·겹치는 active '
                "클레임을 병합 전에 자동으로 잡는다."
            ),
        ),
        (
            "config-silent-noop-check",
            (
                "Harness Config를 배선했는데 Gate 점수나 가드레일이 기대와 다르게 "
                "반응하지 않을 때, 그 Config가 실제로 채점/차단 경로에 연결돼 있는지 "
                "SDK 소스 레벨에서 확인하는 절차."
            ),
        ),
        (
            "eval-run-directory-hygiene",
            (
                "agent-eval trend/abtest/--baseline이 가짜 회귀를 경고하거나 조용히 "
                "아무 것도 못 찾을 때, 서로 다른 목적의 실행 결과가 한 디렉터리에 "
                "섞였는지 확인하고 정리하는 절차."
            ),
        ),
        (
            "gate-improvement-loop",
            (
                "하나의 Gate 점수를 목표선 위로 올리는 반복 절차 — 한 번에 한 가지만 "
                "바꿔 재측정하고 이터레이션 로그에 남긴다."
            ),
        ),
        (
            "harness-autopilot",
            (
                "Harness Methodology 파이프라인(Phase 0~8)의 현재 위치를 판단하고, "
                "반복되는 초안 작업을 대신 만들고, 사람이 필요한 HITL 관문에서 정확히 "
                "멈춘다."
            ),
        ),
        (
            "harness-gate-ci",
            (
                'CI에 "agent-eval gate"를 연결해 절대 임계값·baseline 회귀·골든셋 '
                "회귀·케이스 회귀를 별개의 CI 스텝으로 나누고 종료 코드별로 다른 "
                "알림으로 라우팅한다."
            ),
        ),
        (
            "liveguardrail-standard-config",
            (
                "새 프로젝트에 LiveGuardrail을 설치할 때 팀 표준 설정(위험 패턴, "
                "스코프, 브랜치 보호)을 그대로 적용한다."
            ),
        ),
        (
            "spec-driven-artifacts",
            (
                "코드를 짜기 전에 Spec·실패 모드 카탈로그·최소 골든셋·세션 목표 "
                "템플릿 + Gate 매핑 초안을 만든다."
            ),
        ),
        (
            "sync-drift-check",
            (
                "GitHub PR 라벨(Phase 상태)과 오토파일럿의 .aoo/tasks/<id>.json이 "
                "서로 어긋나지 않았는지 주기적으로 대조한다."
            ),
        ),
        (
            "tdd-ai-workflow-checklist",
            (
                "TDD-AI(Test-Driven Agent Iteration)의 red-green-refactor 순환을 "
                "실행 세션에서 체크리스트로 따라간다."
            ),
        ),
        (
            "threshold-realism-review",
            (
                "exit 75(통계적으로 애매한 통과)가 같은 사유로 5회 넘게 반복될 때, "
                "Gate 임계값 자체가 현실과 안 맞는 것인지 재검토하는 절차."
            ),
        ),
        (
            "unattended-session-recovery",
            (
                "무인으로 재트리거된 Claude Code/OpenCode 세션이 응답 없이 멈추거나 "
                "중간에 죽었을 때의 표준 복구 절차."
            ),
        ),
        (
            "background-real-verification",
            (
                "실제 API 호출이 필요한 배치 실행을 백그라운드로 돌리고, 완료 알림을 "
                "받은 뒤에만 결과를 보고하는 절차 — 아직 안 끝난 실행 결과를 추측해서 "
                "보고하지 않는다."
            ),
        ),
        (
            "evidence-based-gate-fix",
            (
                "Gate 점수가 fail/warn일 때, SDK 소스 코드를 직접 읽어 채점 공식을 "
                "확인한 뒤 조치하는 절차 — 추측으로 임계값을 조정하지 않는다."
            ),
        ),
        (
            "honest-refuted-experiment-logging",
            (
                "agent-eval experiment/improve verify 결과가 refuted로 나왔을 때, "
                "이걸 지우거나 조용히 넘어가지 않고 커밋·문서에 그대로 남기는 절차."
            ),
        ),
    ]
    for name, description in agent_eval_skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, '[]', 'none')",
            (name, description),
        )

    # 사용자 요청(2026-11-17, "주어진 업무를 잘 처리할 수 있는 skill을
    # 생성는 skill을 추가해줘(대화창에서 활용)") — 바로 앞 라운드
    # ("기존 skill들을... 최적의 품질을 낼 수 있도록 개선")에서 만든
    # output_format/examples/constraints 구조를 이 메타 스킬 자체가
    # 가장 먼저 활용한다. augmentation_mode='none'(실제 등록은 사람이
    # '마크다운으로 스킬 가져오기'로 직접 승인해야 하는 HITL 경계를
    # 유지 — 이 Skill이 Agent에게 스킬을 자동으로 등록할 권한을 주는
    # 건 아니다). `skill_markdown.py`가 만드는 것과 똑같은 frontmatter+
    # 섹션 규격으로 결과를 내게 지시해, 사람이 그 출력을 그대로 import
    # 화면에 붙여넣기만 하면 되도록 설계했다 — 이번 세션에 추가한
    # import/export 기능과 바로 이어지는 활용 사례.
    conn.execute(
        "INSERT OR IGNORE INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode, "
        "category, output_format, examples, constraints) "
        "VALUES (?, ?, '[]', 'none', 'general', ?, ?, ?)",
        (
            "스킬생성",
            (
                "주어진 업무를 가장 잘 수행할 수 있는 새 Skill을 설계한다 — "
                "먼저 그 업무의 목적과 전형적인 산출물을 파악하고, 이미 "
                "등록된 Skill Pool에 같은 역할을 하는 스킬이 있는지 먼저 "
                "확인한다(있으면 새로 만들지 않고 그 스킬의 설명을 "
                "보완하도록 안내한다). 그다음 augmentation_mode를 고른다 — "
                "실시간 검색/외부 조회가 꼭 필요하면 'tool'(이미 등록된 "
                "Tool Pool 항목만 연결하고, 없는 도구를 지어내지 않는다), "
                "매 실행마다 판단 없이 무조건 끼워 넣어야 하는 치명적 "
                "정보면 'always', 순수 지식·절차 안내라 도구가 필요 없으면 "
                "'none'. 이름은 그 업무를 한 번에 짐작할 수 있는 한글 "
                "명사(예: '...가이드'/'...브리핑', 기존 명명 관례를 따름)로 "
                "짓고, 지침에는 그 업무를 '어떻게' 수행하는지(순서·기준·"
                "금지사항)를 한 단락으로 명확히 적는다."
            ),
            (
                "frontmatter(name/category/augmentation_mode/tool) + "
                "## 지침/## 출력 형식/## 예시/## 제약사항 섹션으로 구성된 "
                "마크다운 — '도구/스킬 Pool' 화면의 '마크다운으로 스킬 "
                "가져오기'에 그대로 붙여넣을 수 있는 형태로 작성한다. "
                "'tool' 모드일 때만 frontmatter에 tool: 항목을 추가하고, "
                "실제 존재하는 native Tool의 식별자만 쓴다."
            ),
            (
                "요청: \"매주 받는 고객 VOC(문의/불만) 원문을 카테고리별로 "
                "분류하고 요약해주는 스킬을 만들어줘\"\n\n"
                "---\nname: VOC분류요약가이드\ncategory: general\n"
                "augmentation_mode: none\n---\n\n"
                "## 지침\n\n고객 VOC 원문을 카테고리(배송/품질/환불/기타 등 "
                "내용에 맞게)별로 분류한 뒤, 카테고리별로 건수와 대표 "
                "사례 1~2건을 요약한다.\n\n"
                "## 출력 형식\n\n카테고리별 건수 표 + 카테고리별 대표 사례 "
                "요약 목록.\n\n"
                "## 제약사항\n\n원문에 없는 내용을 추측해서 보완하지 "
                "않는다. 분류 기준이 모호하면 '기타'로 두고 임의로 "
                "끼워맞추지 않는다."
            ),
            (
                "실제로 등록되지 않은 Tool Pool 항목을 augmentation_mode="
                "'tool'로 참조하지 않는다(연결이 비어 있는 빈 스킬이 "
                "된다). 이미 있는 Skill과 기능이 겹치면 새로 만들지 말고 "
                "기존 스킬의 description을 보완하도록 안내한다. 한 Skill에 "
                "서로 관련 없는 여러 책임을 몰아넣지 않는다(업무가 여러 "
                "역할로 나뉘면 Skill도 나눈다)."
            ),
        ),
    )

    # 사용자 요청(2026-09-28, "CrewAI AI Agent가 개발 단계에 필요한 기능일지
    # 확인하고, 필요한 경우 AI Agent가 호출할 수 있도록 변형해줘") — 18개 중
    # 17개는 git/PR/CLI 판단·SDK 소스 읽기 같은 HITL 절차라 실행 가능한 도구로
    # 옮길 안전한 방법이 없었지만(오픈 이슈로 그대로 유지), 이거 하나는
    # 예외다: `agent_evaluator.integrations.recommend_fix_mcp`가 이미 실제
    # stdio MCP 서버로 구현돼 있고(`recommend_fix(gate, metric, value) -> str`
    # 노출), `.aoo`/평가 결과 파일이 없어도 정적 지식만으로 안전하게 동작한다
    # (읽기 전용, 부작용 없음) — 그래서 이것만 mcp_stdio Tool Pool 항목으로
    # 승격해 "recommend-fix-workflow" 스킬에 실제로 연결한다. vectordb/graphdb와
    # 같은 이유로 `INSERT OR IGNORE`만 쓴다 — 관리자가 이후 이 스킬의 연결을
    # 직접 바꿔도 재기동 때마다 덮어쓰지 않는다(이미 있으면 아무 것도 안 함).
    # `command`는 `sys.executable`을 그대로 써서, MCPServerAdapter가 스폰하는
    # 서브프로세스가 HAIR-Platform이 실행 중인 바로 그 venv(따라서 같은
    # `agent_evaluator`/`mcp` 패키지)를 쓰도록 보장한다 — PATH의 임의
    # "python"에 의존하지 않는다. `agent-evaluator[mcp]`(=`mcp` 패키지)가
    # 설치돼 있어야 실제로 접속되며, 없으면 기존 MCP 기능과 동일하게
    # `connect_mcp_tools()`의 지연 import에서 실패한다(신규 실패 모드 아님).
    conn.execute(
        "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
        "VALUES (?, ?, 'mcp_stdio', ?)",
        (
            "Gate 조치 지식 조회(recommend_fix)",
            (
                "agent_evaluator.integrations.recommend_fix_mcp — Gate A-G/지표가 "
                "나쁠 때 정적 지식으로 조치를 추천하는 읽기 전용 stdio MCP 서버. "
                ".aoo 실행 이력이 없어도 안전하게 동작한다."
            ),
            json.dumps(
                {
                    "command": sys.executable,
                    "args": ["-m", "agent_evaluator.integrations.recommend_fix_mcp"],
                }
            ),
        ),
    )
    recommend_fix_tool_row = conn.execute(
        "SELECT id FROM tool_pool WHERE name = ?",
        ("Gate 조치 지식 조회(recommend_fix)",),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, ?, ?, 'tool')",
        (
            "recommend-fix-workflow",
            (
                "Gate A-G 중 하나가 fail/warn일 때 코드를 고치기 전에 조치 후보를 "
                "조회하고, 적용 후 실제로 통했는지 검증하는 절차 — recommend_fix "
                "MCP 도구가 실제로 붙어 있어 Agent가 직접 조회를 호출할 수 있다."
            ),
            json.dumps([recommend_fix_tool_row["id"]]),
        ),
    )

    # 사용자 요청(2026-09-29, "일반 웹 검색 기능을 Tool로 제공 가능한지
    # 확인해줘" → "진행해줘") — `websearch.py`(native, Wikipedia)는
    # 자체 색인 안의 백과사전 내용만 다룬다. "최신 뉴스" 같은 진짜
    # 일반 웹 검색은 Wikipedia로 안 된다. 리서치 결과 Bing Search API는
    # 2025-08-11 완전 퇴역했고, Google Custom Search는 2027-01-01부로
    # 신규 고객을 안 받으며, Brave/Tavily는 카드 등록 또는 계정+키가
    # 필요해 "가능한 무료" 조건과 안 맞았다. Parallel AI의 공개 MCP
    # 서버(`https://search.parallel.ai/mcp`)만 계정·키 없이 완전
    # 무료로 접속 가능해(익명 분당 600건) 이걸 쓴다 — `mcp_remote`
    # 종류라 `websearch.py`처럼 새 Python 모듈을 쓸 필요 없이 기존
    # `connect_mcp_tools()` 메커니즘 그대로 재사용한다(recommend_fix와
    # 같은 자리, `mcp_stdio` 대신 `mcp_remote`라는 것만 다름). 실제로
    # 접속해 `web_search`/`web_fetch` 두 Tool이 열리는 것과, "2026
    # 한국 최저임금" 같은 실제 최신 뉴스 검색까지 라이브로 확인했다.
    #
    # **다른 native/recommend_fix 도구와의 차이(중요)**: 이건 상업
    # 회사(Parallel AI)의 무료 티어라 Wikipedia API처럼 안정적인 공공
    # 인프라가 아니다 — 조건이 바뀌거나 중단될 수 있다. 또한 `mcp_remote`
    # 이므로 `agent_runtime.augment_with_retrieval()`(정적 프롬프트 주입,
    # 협업 워크스페이스의 단일 LLM 호출 경로)에서는 못 쓴다 — 그 경로는
    # Agent 호출 루프 자체가 없어 native 함수 직접 호출만 가능하다.
    # "Crew 실행"/"AI 에이전트 생성(crewai)"처럼 실제 CrewAI Agent
    # 루프가 있는 경로에서만 Agent가 스스로 호출 여부를 판단해 쓸 수
    # 있다.
    conn.execute(
        "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
        "VALUES (?, ?, 'mcp_remote', ?)",
        (
            "일반 웹 검색(Parallel Search)",
            (
                "Parallel AI의 무료 공개 MCP 서버 — 인터넷 전체를 대상으로 한 "
                "일반 웹 검색·페이지 조회(API 키/계정 불필요, 익명 접근 분당 "
                "600건). Wikipedia로 못 찾는 최신 뉴스·일반 웹 정보가 필요할 "
                "때 쓴다. 상업 회사의 무료 티어라 조건이 바뀌거나 중단될 수 "
                "있다 — 접속 실패 시 이 스킬을 가진 Agent 실행이 접속 단계에서 "
                "실패한다(다른 mcp_remote/mcp_stdio 도구와 동일한 위험). "
                "CrewAI Agent 루프가 있는 경로(Crew 실행/AI 에이전트 생성)에서만 "
                "쓸 수 있다 — 협업 워크스페이스의 단일 실행 경로에서는 정적 "
                "주입만 가능해 이 도구를 못 쓴다."
            ),
            json.dumps(
                {
                    "url": "https://search.parallel.ai/mcp",
                    "transport": "streamable-http",
                }
            ),
        ),
    )
    websearch_mcp_tool_row = conn.execute(
        "SELECT id FROM tool_pool WHERE name = ?",
        ("일반 웹 검색(Parallel Search)",),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, ?, ?, 'tool')",
        (
            "일반웹검색",
            (
                "Wikipedia에 없는 최신 뉴스·일반 웹 정보가 필요할 때 인터넷 "
                "전체를 검색한다 — Parallel Search MCP 도구(web_search/"
                "web_fetch)가 실제로 붙어 있어 Agent가 직접 호출할 수 있다. "
                "CrewAI Agent 루프가 있는 실행 경로에서만 동작한다."
            ),
            json.dumps([websearch_mcp_tool_row["id"]]),
        ),
    )
