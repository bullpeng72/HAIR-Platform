"""경영/사업 전략·소프트웨어 개발 레퍼런스 Agent 파이프라인 기본 배포
시드(사용자 요청, 2026-10-14, "기본 배포에 코드 및 Skill, Tool, Agent를
포함해줘").

이전까지는 이 12개 Agent(Harness Methodology 개발 파이프라인 6개 +
경영/사업 전략 파이프라인 6개)와 그 전용 custom_python Tool 5개, 절차/지식
Skill들을 일회성 스크립트로 이 세션의 `hair.db`에만 직접 심었다 — 다른
환경에 새로 배포하면(빈 DB로 시작하면) 전혀 재현되지 않았다. 이 모듈은
그 간극을 메운다 — `db.init_schema()`가 기동 때마다(최초 설치든 재기동이든)
호출해, `_seed_default_pools()`와 똑같은 `INSERT OR IGNORE`/존재 확인
멱등 패턴으로 재현한다(관리자가 이후 이름·스킬을 직접 바꿔도 재기동마다
덮어쓰지 않는다 — 이미 있으면 아무 것도 안 함).

`db.py`가 이 모듈을 import하는 방향만 있고 반대 방향은 없어 순환
임포트가 생기지 않는다(이 모듈은 `sqlite3.Connection`만 받는 순수
함수라 `jobs.py`/`pool_registry.py` 같은 상위 비즈니스 모듈을 전혀
참조하지 않는다 — Agent는 Job Queue를 거치지 않고 `data_admin.
seed_demo_data()`의 직접 INSERT 관례를 그대로 따른다)."""

from __future__ import annotations

import json
import sqlite3

SPEC_EARS_LINT_SOURCE = 'def tool_fn(query: str) -> str:\n    import json, re\n\n    ears_pattern = re.compile(\n        r"\\b(WHEN|IF|WHILE|WHERE)\\b.*\\bTHE SYSTEM SHALL\\b", re.IGNORECASE\n    )\n    lines = query.splitlines()\n    ears_lines = [ln.strip() for ln in lines if ears_pattern.search(ln)]\n    needs_clarification = [ln.strip() for ln in lines if "[NEEDS CLARIFICATION]" in ln]\n    return json.dumps(\n        {\n            "ears_requirement_count": len(ears_lines),\n            "ears_requirements": ears_lines[:20],\n            "needs_clarification_count": len(needs_clarification),\n            "needs_clarification_lines": needs_clarification[:20],\n            "blocking": len(needs_clarification) > 0,\n            "verdict": (\n                "BLOCKED: NEEDS CLARIFICATION 태그가 남아 있어 HITL 승인 불가"\n                if needs_clarification\n                else f"OK: EARS 요구사항 {len(ears_lines)}건, NEEDS CLARIFICATION 없음"\n            ),\n        },\n        ensure_ascii=False,\n    )\n'

FAILURE_MODE_DERIVER_SOURCE = 'def tool_fn(query: str) -> str:\n    import json, re\n\n    pattern = re.compile(\n        r"(WHEN|IF|WHILE|WHERE)\\s+(?P<trigger>.+?)\\s+THE SYSTEM SHALL\\s+(?P<action>.+)",\n        re.IGNORECASE,\n    )\n    failure_modes = []\n    for line in query.splitlines():\n        m = pattern.search(line.strip())\n        if not m:\n            continue\n        trigger = m.group("trigger").strip().rstrip(".,")\n        action = m.group("action").strip().rstrip(".,")\n        failure_modes.append(\n            f"FM: {trigger} 상황에서 시스템이 \'{action}\'을(를) 수행하지 "\n            "못하면 어떤 영향이 있고 무엇으로 완화할 것인가? [작성 필요]"\n        )\n    if not failure_modes:\n        return (\n            "EARS 형식(WHEN/IF/WHILE ... THE SYSTEM SHALL ...) 요구사항을 "\n            "찾지 못했습니다 — Spec 문장을 EARS 표기로 먼저 작성하세요."\n        )\n    return json.dumps(\n        {\n            "derived_failure_mode_count": len(failure_modes),\n            "failure_modes": failure_modes,\n        },\n        ensure_ascii=False,\n    )\n'

GOLDEN_SET_COVERAGE_SOURCE = 'def tool_fn(query: str) -> str:\n    import json, re\n\n    try:\n        payload = json.loads(query)\n    except (json.JSONDecodeError, ValueError):\n        return \'입력은 JSON 문자열이어야 합니다: {"criteria": [...], "output": "..."}\'\n    criteria = payload.get("criteria") or []\n    output = (payload.get("output") or "").lower()\n    results = []\n    passed = 0\n    for c in criteria:\n        keywords = [w for w in re.findall(r"[\\w가-힣]+", str(c).lower()) if len(w) > 1]\n        hits = [w for w in keywords if w in output]\n        ok = bool(keywords) and len(hits) / len(keywords) >= 0.6\n        if ok:\n            passed += 1\n        results.append({"criterion": c, "pass": ok, "matched_keywords": hits})\n    total = len(criteria)\n    return json.dumps(\n        {\n            "total": total,\n            "passed": passed,\n            "coverage_ratio": (passed / total) if total else 0.0,\n            "results": results,\n        },\n        ensure_ascii=False,\n    )\n'

FRAMEWORK_TEMPLATE_SOURCE = 'def tool_fn(query: str) -> str:\n    import json\n\n    FRAMEWORKS = {\n        "3C": [\n            ("Company(자사)", "우리의 핵심 역량·자원·현재 포지션은 무엇인가?"),\n            ("Customer(고객)", "목표 고객은 누구이고, 그들의 니즈/페인포인트는 무엇인가?"),\n            ("Competitor(경쟁사)", "주요 경쟁자는 누구이고, 그들과 비교한 우리의 차별점은 무엇인가?"),\n        ],\n        "4P": [\n            ("Product(제품)", "무엇을 제공하는가? 핵심 기능/품질/차별점은?"),\n            ("Price(가격)", "가격 정책은? 경쟁사 대비 포지셔닝은?"),\n            ("Place(유통)", "어떤 채널로 고객에게 도달하는가?"),\n            ("Promotion(판촉)", "어떻게 인지도를 높이고 구매를 유도하는가?"),\n        ],\n        "SWOT": [\n            ("Strengths(강점)", "내부 역량 중 경쟁우위로 작용하는 것은?"),\n            ("Weaknesses(약점)", "내부 역량 중 개선이 필요한 것은?"),\n            ("Opportunities(기회)", "외부 환경 중 활용 가능한 요인은?"),\n            ("Threats(위협)", "외부 환경 중 리스크 요인은?"),\n        ],\n    }\n    try:\n        payload = json.loads(query)\n    except (json.JSONDecodeError, ValueError):\n        return \'입력은 JSON 문자열이어야 합니다: {"framework": "3C|4P|SWOT", "subject": "..."}\'\n    framework = str(payload.get("framework", "")).upper()\n    subject = payload.get("subject") or "(대상 미지정)"\n    categories = FRAMEWORKS.get(framework)\n    if categories is None:\n        return f"지원하지 않는 framework입니다: {framework!r} (3C/4P/SWOT 중 하나)"\n    lines = [f"# {framework} 분석 — {subject}", ""]\n    for name, question in categories:\n        lines.append(f"## {name}")\n        lines.append(f"- 질문: {question}")\n        lines.append("- 답변: [작성 필요]")\n        lines.append("")\n    return "\\n".join(lines)\n'

PRESENTATION_OUTLINE_SOURCE = 'def tool_fn(query: str) -> str:\n    import re\n\n    text = query.strip()\n    if not text:\n        return "빈 입력입니다 — 슬라이드로 만들 내용을 전달하세요."\n    header_pattern = re.compile(r"^(#{1,3})\\s+(.+)$", re.MULTILINE)\n    headers = list(header_pattern.finditer(text))\n    slides = []\n    if headers:\n        for i, m in enumerate(headers):\n            title = m.group(2).strip()\n            start = m.end()\n            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)\n            body = text[start:end].strip()\n            bullets = [ln.strip("-* ").strip() for ln in body.splitlines() if ln.strip()][:5]\n            slides.append({"title": title, "bullets": bullets})\n    else:\n        paragraphs = [p.strip() for p in text.split("\\n\\n") if p.strip()]\n        for i, p in enumerate(paragraphs, 1):\n            sentences = [s.strip() for s in re.split(r"(?<=[.?!다요])\\s+", p) if s.strip()][:5]\n            slides.append({"title": f"슬라이드 {i}", "bullets": sentences})\n    lines = []\n    for i, s in enumerate(slides, 1):\n        lines.append(f"[슬라이드 {i}] {s[\'title\']}")\n        for b in s["bullets"]:\n            lines.append(f"  - {b[:80]}")\n        lines.append("")\n    return "\\n".join(lines) if lines else "슬라이드로 나눌 내용을 찾지 못했습니다."\n'

# 사용자 요청(2026-10-14, "PM/AA/DA/개발계획수립/복수 개발자/UI기획/
# 테스트계획수립/기능테스트/품질관리... 부족한 에이전트를 생성해줘") —
# "실패모드 기계적 도출"과 같은 패턴(EARS 문장을 기계적으로 파생)을
# 테스트 케이스 설계에도 적용한다. positive/negative/boundary 세
# 케이스 골격을 요구사항 문장별로 기계적으로 뽑아, 테스트 계획
# 수립가가 처음부터 빈 화면에서 시작하지 않게 한다.
TEST_CASE_DERIVER_SOURCE = 'def tool_fn(query: str) -> str:\n    import json, re\n\n    pattern = re.compile(\n        r"(WHEN|IF|WHILE|WHERE)\\s+(?P<trigger>.+?)\\s+THE SYSTEM SHALL\\s+(?P<action>.+)",\n        re.IGNORECASE,\n    )\n    cases = []\n    for line in query.splitlines():\n        m = pattern.search(line.strip())\n        if not m:\n            continue\n        trigger = m.group("trigger").strip().rstrip(".,")\n        action = m.group("action").strip().rstrip(".,")\n        cases.append(\n            {\n                "requirement": line.strip(),\n                "positive_case": f"Given {trigger}, When 요구사항대로 동작하면, Then \'{action}\'이(가) 수행된다.",\n                "negative_case": f"Given {trigger}가 아니거나 선행조건 미충족, When 요청이 들어오면, Then \'{action}\'이(가) 수행되지 않고 적절히 거부/에러 처리된다. [작성 필요]",\n                "boundary_case": f"Given {trigger}의 경계값(최소/최대/빈 값 등), When 요청이 들어오면, Then 예상한 경계 동작을 확인한다. [작성 필요]",\n            }\n        )\n    if not cases:\n        return (\n            "EARS 형식(WHEN/IF/WHILE ... THE SYSTEM SHALL ...) 요구사항을 "\n            "찾지 못했습니다 — Spec 문장을 EARS 표기로 먼저 작성하세요."\n        )\n    return json.dumps(\n        {"derived_test_case_count": len(cases), "test_cases": cases},\n        ensure_ascii=False,\n    )\n'

# (name, description, function_source, timeout_seconds)
TOOL_SPECS: list[tuple[str, str, str, int]] = [
    ("Spec EARS 린터", "Spec 텍스트가 EARS 표기(WHEN/IF/WHILE ... THE SYSTEM SHALL ...)를 따르는지, [NEEDS CLARIFICATION] 태그가 남아 있어 HITL 승인을 막는지 자동 점검한다.", SPEC_EARS_LINT_SOURCE, 10),
    ("실패모드 기계적 도출", "Spec의 EARS 요구사항 문장을 뒤집어 실패 모드 초안(트리거+실패 시 영향 질문)을 기계적으로 도출한다 — Spec과 실패 모드 카탈로그가 같은 문법의 파생 관계라는 하니스 메서드 원칙을 그대로 구현한다.", FAILURE_MODE_DERIVER_SOURCE, 10),
    ("골든셋 커버리지 체커", "골든셋 완료조건(기준 목록)과 실제 산출물 텍스트를 비교해 기준별 키워드 충족 여부와 전체 커버리지 비율을 자동 채점한다 — Gate A-G 배치 채점 전 1차 점검으로 쓴다.", GOLDEN_SET_COVERAGE_SOURCE, 10),
    ("분석 프레임워크 템플릿 생성기", "3C/4P/SWOT 중 지정한 프레임워크의 표준 분석 범주와 가이드 질문을 채워야 할 템플릿 형태로 생성한다 — 분석 자체가 아니라 빠짐없이 채우도록 돕는 구조화 도구다.", FRAMEWORK_TEMPLATE_SOURCE, 10),
    ("발표자료 아웃라인 생성기", "보고서·기획서 등 긴 글을 마크다운 헤더(또는 문단) 단위로 나눠 슬라이드별 제목+핵심 불릿 형태의 발표자료 아웃라인으로 재구성한다.", PRESENTATION_OUTLINE_SOURCE, 10),
    ("테스트 케이스 기계적 도출", "Spec의 EARS 요구사항 문장별로 positive/negative/boundary 세 케이스 골격(Given-When-Then)을 기계적으로 도출한다 — '실패모드 기계적 도출'과 같은 파생 원칙을 테스트 케이스 설계에 적용한다.", TEST_CASE_DERIVER_SOURCE, 10),
]

# (skill_name, description, tool_name) — tool_name은 위 TOOL_SPECS의 name과 매칭해 default_tool_pool_ids를 채운다.
TOOL_SKILL_SPECS: list[tuple[str, str, str]] = [
    ("spec-ears-lint", "Spec 텍스트가 EARS 표기(WHEN/IF/WHILE ... THE SYSTEM SHALL ...)를 따르는지, [NEEDS CLARIFICATION] 태그가 남아 있어 HITL 승인을 막는지 자동 점검한다.", "Spec EARS 린터"),
    ("failure-mode-derivation", "Spec의 EARS 요구사항 문장을 뒤집어 실패 모드 초안(트리거+실패 시 영향 질문)을 기계적으로 도출한다 — Spec과 실패 모드 카탈로그가 같은 문법의 파생 관계라는 하니스 메서드 원칙을 그대로 구현한다.", "실패모드 기계적 도출"),
    ("golden-set-coverage-check", "골든셋 완료조건(기준 목록)과 실제 산출물 텍스트를 비교해 기준별 키워드 충족 여부와 전체 커버리지 비율을 자동 채점한다 — Gate A-G 배치 채점 전 1차 점검으로 쓴다.", "골든셋 커버리지 체커"),
    ("framework-template-generator", "3C/4P/SWOT 중 지정한 프레임워크의 표준 분석 범주와 가이드 질문을 채워야 할 템플릿 형태로 생성한다 — 분석 자체가 아니라 빠짐없이 채우도록 돕는 구조화 도구다.", "분석 프레임워크 템플릿 생성기"),
    ("presentation-outline-builder", "보고서·기획서 등 긴 글을 마크다운 헤더(또는 문단) 단위로 나눠 슬라이드별 제목+핵심 불릿 형태의 발표자료 아웃라인으로 재구성한다.", "발표자료 아웃라인 생성기"),
    ("test-case-mechanical-derivation", "Spec의 EARS 요구사항 문장별로 positive/negative/boundary 세 케이스 골격(Given-When-Then)을 기계적으로 도출한다 — '실패모드 기계적 도출'과 같은 파생 원칙을 테스트 케이스 설계에 적용한다.", "테스트 케이스 기계적 도출"),
]

# (skill_name, description) — augmentation_mode='none'(지식 주입, 실행 가능한 Tool 없음).
KNOWLEDGE_SKILL_SPECS: list[tuple[str, str]] = [
    ("adr-template", "아키텍처 결정(인터페이스 선택, 재사용 vs 신규 작성, 데이터 흐름 변경, 하위 호환 여부 등)을 내린 직후, 구현을 시작하기 전에 ADR(결정·배경·대안·결과) 형식으로 문서화한다."),
    ("code-review-checklist", "PR을 올리기 전 또는 리뷰할 때 코딩 컨벤션·에러 처리·테스트 커버리지·가독성을 점검하는 범용 체크리스트를 적용한다."),
    ("commit-pr-message-rules", "커밋 메시지와 PR 설명을 팀 컨벤션(무엇을/왜 바꿨는지 명확히, 요약 + 근거 구조)에 맞게 작성한다."),
    ("docs-style-guide", "README·설계 문서·API 레퍼런스 등 프로젝트 문서를 팀의 문서화 스타일(용어 통일, 예시 포함, 간결한 구조)에 맞게 작성·검토한다."),
    ("incident-postmortem", "작업 중 심각한 오류·회귀가 발생했을 때 원인·대응·재발 방지책을 비난 없이(blameless) 기록한다."),
    ("human-approval-loop", "Spec·ADR·설계 문서 등 HITL(Human-in-the-loop) 승인이 필요한 산출물은 사람이 승인할 때까지 피드백을 반영해 재작성한다 — HAIR의 결재함(approvals)을 통해 상신하고, 반려되면 반려 사유를 반영한 개정판을 다시 올린다(같은 사유가 반복되면 방향 자체를 재검토한다)."),
    ("3c-4p-swot-framework", "3C(자사·고객·경쟁사), 4P(제품·가격·유통·판촉), SWOT(강점·약점·기회·위협) 분석 프레임워크로 시장·제품·전략을 구조적으로 진단한다 — 각 범주를 빠짐없이 채우고, 추측이 아니라 조사된 근거를 바탕으로 작성한다."),
    ("business-plan-writing-guide", "기획서는 배경(왜 필요한가)·목표(무엇을 달성하는가)·실행계획(어떻게·누가·언제)·예산·리스크와 대응방안 순으로 구성한다 — 의사결정자가 한 번에 핵심을 파악하도록 결론을 앞에 둔다."),
    ("business-strategy-formulation", "사업 전략은 시장·경쟁 분석 → 목표 고객·포지셔닝 설정 → 차별화 전략 → 실행 로드맵 → 성과지표(KPI) 순으로 수립한다 — 분석 없이 바로 전략부터 제시하지 않는다(3C/4P/SWOT 분석 결과를 전제로 삼는다)."),
    ("executive-report-writing-style", "C-level 보고서는 '핵심 요약(한눈에 결론)'을 맨 앞에 두고, 숫자·데이터 근거를 명시하며, 결론 → 근거 → 권고(Action) 순서로 1페이지 안에 의사결정에 필요한 정보를 압축한다 — 배경 설명을 길게 늘어놓지 않는다."),
    # --- 기획→개발 실행 파이프라인(PM·아키텍처·개발계획·UI·QA, 2026-10-14) ---
    ("pm-delivery-coordination", "기획 접수 → 요구사항 확정 → 설계 승인 → 개발 착수 → QA → 인수까지 단계별 진행 상태·담당 Agent·블로커를 추적한다. 각 단계 전환 전에 산출물이 HITL 승인을 받았는지 확인하고, 막힌 단계가 있으면 원인과 다음 조치를 명시한다 — 스스로 산출물을 만들지 않고 전체 진행을 조율·보고하는 역할이다."),
    ("application-architecture-design-guide", "애플리케이션 아키텍처는 모듈 경계(무엇을 한 모듈로 묶을지) → 모듈 간 인터페이스(어떤 데이터를 어떻게 주고받을지) → 기술 스택 선정(왜 그 기술인지 대안과 비교) 순으로 설계한다 — 요구사항/스펙에 없는 기능을 임의로 추가하지 않고, 구현 난이도가 아니라 요구사항 충족을 기준으로 모듈을 나눈다."),
    ("data-model-design-guide", "데이터 모델은 요구사항에서 다뤄지는 엔티티와 그 속성을 먼저 나열 → 엔티티 간 관계(1:1/1:N/N:M)를 정의 → 정규화 수준을 결정 → 조회 패턴(어떤 질의가 자주 발생하는지)에 맞춰 인덱스/비정규화 여부를 검토하는 순서로 진행한다 — ERD와 함께 왜 그 구조를 택했는지 근거를 남긴다."),
    ("module-decomposition-and-task-assignment", "아키텍처 설계 산출물을 받아 독립적으로 개발 가능한 모듈 단위로 쪼개고, 모듈 간 의존성(어떤 모듈이 먼저 끝나야 다른 모듈을 시작할 수 있는지)을 표시한 뒤, 병렬로 진행 가능한 모듈은 서로 다른 개발자 Agent에 배분한다 — 배분 결과에는 각 모듈의 담당자·선행 조건·완료 기준이 포함돼야 한다."),
    ("frontend-ui-implementation-guide", "프론트엔드 구현은 UI 기획(화면 흐름·컴포넌트 목록)을 먼저 확인하고, 컴포넌트 단위로 상태·이벤트 흐름을 분리하며, 접근성(키보드 조작·대비)과 반응형(화면 크기 대응)을 기본으로 고려한다 — 디자인에 없는 화면/동작을 임의로 추가하지 않는다."),
    ("ui-ux-planning-guide", "UI/UX 기획은 사용자 시나리오(누가 어떤 목적으로 어떤 순서로 쓰는지) → 화면 흐름도(어떤 화면에서 어떤 화면으로 이동하는지) → 화면별 구성 요소와 인터랙션(클릭/입력 시 어떻게 반응하는지) 순으로 정리한다 — 요구사항/스펙에 없는 화면·기능을 임의로 추가하지 않는다."),
    ("test-plan-and-case-design-guide", "테스트 계획은 테스트 범위(무엇을 테스트하고 무엇은 제외하는지) → 테스트 전략(단위/통합/E2E 중 어디에 집중할지) → 요구사항별 테스트 케이스(정상 케이스·예외 케이스·경계값) 순으로 작성한다 — 테스트 케이스는 실행 전 단계 산출물이라, 이 역할은 케이스를 설계만 하고 직접 실행(QA 기능테스터의 역할)하지 않는다."),
    ("quality-acceptance-criteria-guide", "프로젝트 전체 품질/인수 기준은 Gate A-G 점수·테스트 커버리지·미해결 결함 심각도를 종합해 판정한다 — 개별 모듈이 통과해도 전체 인수 기준(예: 치명적 결함 0건, 핵심 요구사항 커버리지 100%)을 못 채우면 배포 불가로 판정하고, 무엇이 부족한지 구체적으로 남긴다."),
    # --- "자율 개발 크루"(10인 개발자 풀, 2026-10-14) ---
    ("data-infra-implementation-guide", "데이터/인프라 구현은 DA가 설계한 ERD·스키마를 실제 마이그레이션/DDL로 옮기고, 배포 파이프라인(CI/CD)·환경 변수·인프라 설정을 구성한다 — 설계에 없는 테이블/컬럼을 임의로 추가하지 않고, 스키마 변경은 마이그레이션 이력으로 남긴다."),
]

# (role_name, skill_tags, tuning_config)
AGENT_SPECS: list[tuple[str, list[str], dict]] = [
    # --- Harness Methodology 개발 파이프라인(2026-10-13) ---
    (
        "요구사항 분석가",
        ["spec-driven-artifacts", "human-approval-loop", "spec-ears-lint"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "스펙 작성자",
        ["spec-driven-artifacts", "docs-style-guide", "spec-ears-lint", "failure-mode-derivation"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "코드베이스 분석가",
        ["rag", "graphdb", "adr-template"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "개발자",
        ["tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "코드 검증가",
        ["harness-gate-ci", "gate-improvement-loop", "evidence-based-gate-fix", "recommend-fix-workflow", "code-review-checklist"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "QA 기능테스터",
        ["tdd-ai-workflow-checklist", "golden-set-coverage-check", "human-approval-loop"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    # --- 경영/사업 전략 파이프라인(2026-10-14) ---
    (
        "외부 자료 조사가",
        ["외부검색", "일반웹검색", "rag"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "3C4P SWOT 분석가",
        ["3c-4p-swot-framework", "framework-template-generator", "rag"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "기획서 작성자",
        ["business-plan-writing-guide", "docs-style-guide", "golden-set-coverage-check"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "사업전략 수립가",
        ["business-strategy-formulation", "3c-4p-swot-framework", "rag"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.5},
    ),
    (
        "C-level 보고서 작성자",
        ["executive-report-writing-style", "docs-style-guide", "golden-set-coverage-check"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "프레젠테이션 자료 작성자",
        ["presentation-outline-builder", "executive-report-writing-style"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    # --- 기획→개발 실행 파이프라인(PM·아키텍처·개발계획·UI·QA, 2026-10-14) ---
    # 사용자 요청: "기획 내용을 받아 요구사항을 분석하고 스펙을 정리해
    # 원하는 개발을 진행할 수 있도록... PM과 AA, DA 등 아키텍터, 요구사항
    # 분석, 설계, 개발 계획 수립(모듈을 나누고 개발자 Agent별 업무 분배),
    # 복수의 개발자, UI기획, 테스트 계획 수립 및 테스트 케이스 작성,
    # 기능 테스트, 품질 관리... 부족한 에이전트를 생성해줘." — 요구사항
    # 분석가(22)·스펙 작성자(23)·개발자(25, 백엔드/범용)·코드 검증가(26)·
    # QA 기능테스터(27)는 이미 있어 재사용하고, 그 사이·앞뒤로 빠진
    # 역할만 새로 만든다.
    (
        "PM",
        ["pm-delivery-coordination", "human-approval-loop", "spec-driven-artifacts"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "AA",
        ["application-architecture-design-guide", "adr-template"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "DA",
        ["data-model-design-guide", "adr-template", "graphdb"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "개발 계획 수립가",
        ["module-decomposition-and-task-assignment", "adr-template"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "프론트엔드 개발자",
        ["frontend-ui-implementation-guide", "tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    # --- "자율 개발 크루" 확장(10인 개발자 풀, 2026-10-14) — 사용자 요청:
    # "기획서가 제시되면 이를 받아 자율적으로 개발을 완료할 복수의
    # 개발자(10명)가 포함된 crewai 에이전트 그룹 구성 방안을 제안해줘
    # (권한 위임을 통한 자율성을 보장, 가능하면 하네스 방법론 적용)" —
    # 제안 승인 후 구현. 기존 "개발자"(백엔드 1)·"프론트엔드 개발자"
    # (프론트 1)에 8명을 더해 총 10명을 만든다. `crew_runtime.run_crew(
    # process="hierarchical", manager_agent_id=<개발 계획 수립가>)`로
    # 실행하면 매니저가 설계 산출물을 받아 이 10명(+UI 기획자)에게
    # 모듈을 동적으로 위임한다(`allow_delegation=True`가 이미
    # `run_crew()` 전체 기본값이라 추가 코드 없이 바로 동작).
    (
        "백엔드 개발자 2",
        ["tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "백엔드 개발자 3",
        ["tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "백엔드 개발자 4",
        ["tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "프론트엔드 개발자 2",
        ["frontend-ui-implementation-guide", "tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "프론트엔드 개발자 3",
        ["frontend-ui-implementation-guide", "tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "데이터/인프라 개발자 1",
        ["data-infra-implementation-guide", "tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "데이터/인프라 개발자 2",
        ["data-infra-implementation-guide", "tdd-ai-workflow-checklist", "commit-pr-message-rules"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "풀스택 개발자",
        ["tdd-ai-workflow-checklist", "commit-pr-message-rules", "frontend-ui-implementation-guide"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "UI 기획자",
        ["ui-ux-planning-guide", "docs-style-guide"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "테스트 계획 수립가",
        ["test-plan-and-case-design-guide", "test-case-mechanical-derivation", "spec-ears-lint"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "품질 관리자",
        ["quality-acceptance-criteria-guide", "harness-gate-ci", "gate-improvement-loop", "evidence-based-gate-fix"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
]

# 사용자 보고(2026-10-14, "위 Agent 그룹 확인이 안되") — 실측으로 발견한
# 갭: 10명 개발자를 포함한 "자율 개발 크루"를 설계·구현했지만, Agent를
# `agent_pool`에 개별 행으로만 만들었을 뿐 이들을 하나의 "그룹"으로
# 묶는 `teams` 레코드는 한 번도 만들지 않았다 — "팀 관리" 탭에는 당연히
# 아무것도 안 보였다. `개발 계획 수립가`를 매니저로 지정해
# hierarchical로 실행하려면(`crew_runtime.run_crew()`의 검증 —
# manager_agent_id는 agent_ids 목록 안에 있어야 함) 매니저도 팀
# 멤버여야 하므로 함께 넣는다. (role_name, is_manager)
DEV_CREW_TEAM_NAME = "자율 개발 크루"
DEV_CREW_TEAM_DESCRIPTION = (
    "기획서 → 요구사항/스펙/설계 산출물을 받아 모듈을 쪼개고 10명의 개발자"
    "(+UI 기획자)에게 자율적으로 위임해 개발을 완료하는 크루. 실행 시 "
    "Crew 실행(또는 팀 배정 후 'Crew 실행') 화면에서 process=hierarchical, "
    "매니저=이 팀의 '개발 계획 수립가'로 지정하면 매니저는 조율·위임만 "
    "하고 나머지 11명이 실제로 모듈을 구현한다(allow_delegation이 전원 "
    "기본 적용돼 서로 위임·질의도 가능)."
)
DEV_CREW_TEAM_MEMBER_SPECS: list[tuple[str, str]] = [
    ("개발 계획 수립가", "매니저(hierarchical 실행 시 지정)"),
    ("개발자", "백엔드"),
    ("백엔드 개발자 2", "백엔드"),
    ("백엔드 개발자 3", "백엔드"),
    ("백엔드 개발자 4", "백엔드"),
    ("프론트엔드 개발자", "프론트엔드"),
    ("프론트엔드 개발자 2", "프론트엔드"),
    ("프론트엔드 개발자 3", "프론트엔드"),
    ("데이터/인프라 개발자 1", "데이터/인프라"),
    ("데이터/인프라 개발자 2", "데이터/인프라"),
    ("풀스택 개발자", "풀스택(유동 배치)"),
    ("UI 기획자", "UI/UX"),
]


def seed(conn: sqlite3.Connection) -> None:
    """`db.init_schema()`가 `_seed_default_pools(conn)` 다음으로 호출한다.
    전부 `INSERT OR IGNORE`(tool_pool.name/skill_pool.name UNIQUE 제약)
    또는 명시적 존재 확인(agent_pool.role_name은 UNIQUE 제약이 없어
    SELECT로 직접 확인) 기반이라 몇 번을 다시 불러도 안전하다 — 이미
    있으면 아무 것도 안 하고, 관리자가 이후 내용을 수정해도 재기동 때
    덮어쓰지 않는다."""
    tool_ids: dict[str, int] = {}
    for name, description, source, timeout_seconds in TOOL_SPECS:
        conn.execute(
            "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) "
            "VALUES (?, ?, 'custom_python', ?)",
            (
                name,
                description,
                json.dumps(
                    {"function_source": source, "timeout_seconds": timeout_seconds},
                    ensure_ascii=False,
                ),
            ),
        )
        row = conn.execute(
            "SELECT id FROM tool_pool WHERE name = ?", (name,)
        ).fetchone()
        tool_ids[name] = row["id"]

    for skill_name, description, tool_name in TOOL_SKILL_SPECS:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, ?, 'tool')",
            (skill_name, description, json.dumps([tool_ids[tool_name]])),
        )

    for skill_name, description in KNOWLEDGE_SKILL_SPECS:
        conn.execute(
            "INSERT OR IGNORE INTO skill_pool "
            "(name, description, default_tool_pool_ids, augmentation_mode) "
            "VALUES (?, ?, '[]', 'none')",
            (skill_name, description),
        )

    for role_name, skill_tags, tuning_config in AGENT_SPECS:
        existing = conn.execute(
            "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            "INSERT INTO agent_pool (role_name, skill_tags, status, framework, tuning_config) "
            "VALUES (?, ?, 'active', 'crewai', ?)",
            (
                role_name,
                json.dumps(skill_tags, ensure_ascii=False),
                json.dumps(tuning_config, ensure_ascii=False),
            ),
        )

    # `teams.create_team()`/`add_team_member()`는 `audit.record()`를 거쳐
    # `actor_id`가 실제 `users` 행을 가리켜야 한다(FK 제약) — `init_schema()`
    # 는 아직 사용자가 하나도 없는 완전히 빈 DB에서도 호출되므로, 위
    # agent_pool처럼 감사로그 없이 직접 INSERT한다(`teams.id`는 UNIQUE
    # 제약이 없어 이름으로 존재를 확인한다).
    team_row = conn.execute(
        "SELECT id FROM teams WHERE name = ?", (DEV_CREW_TEAM_NAME,)
    ).fetchone()
    if team_row is None:
        cur = conn.execute(
            "INSERT INTO teams (name, description, is_active) VALUES (?, ?, 1)",
            (DEV_CREW_TEAM_NAME, DEV_CREW_TEAM_DESCRIPTION),
        )
        team_id = cur.lastrowid
        for role_name, role_label in DEV_CREW_TEAM_MEMBER_SPECS:
            agent_row = conn.execute(
                "SELECT id FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue  # 방어적 처리 — AGENT_SPECS에 이미 다 있어 정상 경로에선 안 탐.
            conn.execute(
                "INSERT OR IGNORE INTO team_members "
                "(team_id, member_type, member_id, role_label) "
                "VALUES (?, 'agent', ?, ?)",
                (team_id, agent_row["id"], role_label),
            )
