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

# 사용자 요청(2026-11-11, "기획/전략/자료조사/보고서작성/데이터분석/
# 제안서작성/실적서작성 개선 방안을 기획해줘" → "바로 구현해줘") —
# 기존 "3C4P SWOT 분석가"는 정성 프레임워크 분석만 하고, 지금까지 이
# 플랫폼 어디에도(skill_pool 48개·pandas/numpy 포함) 실제 숫자를
# 계산하는 기능이 없었다(실측 확인 — 전수 조사 0건) — LLM이 합계·증감률
# 등을 "서술로 지어낼" 위험이 그대로 있었다. Gate A-G/`code_syntax`가
# 이미 지키는 "거짓으로 측정값을 만들지 않는다" 원칙을 그대로 적용해,
# 표준편차처럼 데이터가 부족하면 `None`을 돌려주고(개수 1개일 때),
# 증감률도 기준값이 0이면 계산하지 않고 사유를 명시한다 — 전부 Python
# `statistics` 표준 라이브러리만 쓴다(외부 의존성 추가 없음).
DATA_ANALYZER_SOURCE = """def tool_fn(query: str) -> str:
    import json
    import statistics

    try:
        payload = json.loads(query)
    except (json.JSONDecodeError, ValueError):
        return (
            '입력은 JSON 문자열이어야 합니다: '
            '{"series": {"2024": 100, "2025": 130}} (시계열 비교) 또는 '
            '{"values": [1, 2, 3]} (단순 통계) 중 하나.'
        )

    series = payload.get("series")
    if isinstance(series, dict) and series:
        periods = list(series.keys())
        nums = []
        for p in periods:
            try:
                nums.append(float(series[p]))
            except (TypeError, ValueError):
                return f"'{p}' 값이 숫자가 아닙니다: {series[p]!r}"
        growth_rates = []
        for i in range(1, len(nums)):
            prev, cur = nums[i - 1], nums[i]
            if prev == 0:
                growth_rates.append(
                    {
                        "from": periods[i - 1],
                        "to": periods[i],
                        "growth_rate_pct": None,
                        "note": "기준값이 0이라 증감률을 계산할 수 없음",
                    }
                )
            else:
                growth_rates.append(
                    {
                        "from": periods[i - 1],
                        "to": periods[i],
                        "growth_rate_pct": round((cur - prev) / prev * 100, 2),
                    }
                )
        return json.dumps(
            {
                "periods": periods,
                "values": nums,
                "sum": round(sum(nums), 4),
                "average": round(statistics.mean(nums), 4),
                "min": min(nums),
                "max": max(nums),
                "stdev": round(statistics.stdev(nums), 4) if len(nums) >= 2 else None,
                "growth_rates": growth_rates,
            },
            ensure_ascii=False,
        )

    values = payload.get("values")
    if isinstance(values, list) and values:
        try:
            nums = [float(v) for v in values]
        except (TypeError, ValueError):
            return "values 배열에 숫자가 아닌 값이 섞여 있습니다."
        return json.dumps(
            {
                "count": len(nums),
                "sum": round(sum(nums), 4),
                "average": round(statistics.mean(nums), 4),
                "min": min(nums),
                "max": max(nums),
                "stdev": round(statistics.stdev(nums), 4) if len(nums) >= 2 else None,
            },
            ensure_ascii=False,
        )

    return '입력에 "series"(시계열 비교) 또는 "values"(단순 통계) 중 하나가 있어야 합니다.'
"""

# (name, description, function_source, timeout_seconds)
TOOL_SPECS: list[tuple[str, str, str, int]] = [
    (
        "Spec EARS 린터",
        "Spec 텍스트가 EARS 표기(WHEN/IF/WHILE ... THE SYSTEM SHALL ...)를 따르는지, [NEEDS CLARIFICATION] 태그가 남아 있어 HITL 승인을 막는지 자동 점검한다.",
        SPEC_EARS_LINT_SOURCE,
        10,
    ),
    (
        "실패모드 기계적 도출",
        "Spec의 EARS 요구사항 문장을 뒤집어 실패 모드 초안(트리거+실패 시 영향 질문)을 기계적으로 도출한다 — Spec과 실패 모드 카탈로그가 같은 문법의 파생 관계라는 하니스 메서드 원칙을 그대로 구현한다.",
        FAILURE_MODE_DERIVER_SOURCE,
        10,
    ),
    (
        "골든셋 커버리지 체커",
        "골든셋 완료조건(기준 목록)과 실제 산출물 텍스트를 비교해 기준별 키워드 충족 여부와 전체 커버리지 비율을 자동 채점한다 — Gate A-G 배치 채점 전 1차 점검으로 쓴다.",
        GOLDEN_SET_COVERAGE_SOURCE,
        10,
    ),
    (
        "분석 프레임워크 템플릿 생성기",
        "3C/4P/SWOT 중 지정한 프레임워크의 표준 분석 범주와 가이드 질문을 채워야 할 템플릿 형태로 생성한다 — 분석 자체가 아니라 빠짐없이 채우도록 돕는 구조화 도구다.",
        FRAMEWORK_TEMPLATE_SOURCE,
        10,
    ),
    (
        "발표자료 아웃라인 생성기",
        "보고서·기획서 등 긴 글을 마크다운 헤더(또는 문단) 단위로 나눠 슬라이드별 제목+핵심 불릿 형태의 발표자료 아웃라인으로 재구성한다.",
        PRESENTATION_OUTLINE_SOURCE,
        10,
    ),
    (
        "테스트 케이스 기계적 도출",
        "Spec의 EARS 요구사항 문장별로 positive/negative/boundary 세 케이스 골격(Given-When-Then)을 기계적으로 도출한다 — '실패모드 기계적 도출'과 같은 파생 원칙을 테스트 케이스 설계에 적용한다.",
        TEST_CASE_DERIVER_SOURCE,
        10,
    ),
    (
        "수치 데이터 분석기",
        "시계열(연도별 등) 또는 단순 숫자 목록을 입력받아 합계·평균·최소/최대·표준편차·증감률을 실제로 계산한다 — LLM이 숫자를 서술로 지어내지 않도록, 데이터가 부족하면(표준편차는 값 2개 미만, 증감률은 기준값 0) 추측 대신 명시적으로 계산 불가를 알린다.",
        DATA_ANALYZER_SOURCE,
        10,
    ),
]

# (skill_name, description, tool_name) — tool_name은 위 TOOL_SPECS의 name과 매칭해 default_tool_pool_ids를 채운다.
TOOL_SKILL_SPECS: list[tuple[str, str, str]] = [
    (
        "요구사항검증",
        "Spec 텍스트가 EARS 표기(WHEN/IF/WHILE ... THE SYSTEM SHALL ...)를 따르는지, [NEEDS CLARIFICATION] 태그가 남아 있어 HITL 승인을 막는지 자동 점검한다.",
        "Spec EARS 린터",
    ),
    (
        "실패모드도출",
        "Spec의 EARS 요구사항 문장을 뒤집어 실패 모드 초안(트리거+실패 시 영향 질문)을 기계적으로 도출한다 — Spec과 실패 모드 카탈로그가 같은 문법의 파생 관계라는 하니스 메서드 원칙을 그대로 구현한다.",
        "실패모드 기계적 도출",
    ),
    (
        "커버리지체크",
        "골든셋 완료조건(기준 목록)과 실제 산출물 텍스트를 비교해 기준별 키워드 충족 여부와 전체 커버리지 비율을 자동 채점한다 — Gate A-G 배치 채점 전 1차 점검으로 쓴다.",
        "골든셋 커버리지 체커",
    ),
    (
        "프레임워크템플릿생성",
        "3C/4P/SWOT 중 지정한 프레임워크의 표준 분석 범주와 가이드 질문을 채워야 할 템플릿 형태로 생성한다 — 분석 자체가 아니라 빠짐없이 채우도록 돕는 구조화 도구다.",
        "분석 프레임워크 템플릿 생성기",
    ),
    (
        "발표자료구성",
        "보고서·기획서 등 긴 글을 마크다운 헤더(또는 문단) 단위로 나눠 슬라이드별 제목+핵심 불릿 형태의 발표자료 아웃라인으로 재구성한다.",
        "발표자료 아웃라인 생성기",
    ),
    (
        "테스트케이스도출",
        "Spec의 EARS 요구사항 문장별로 positive/negative/boundary 세 케이스 골격(Given-When-Then)을 기계적으로 도출한다 — '실패모드 기계적 도출'과 같은 파생 원칙을 테스트 케이스 설계에 적용한다.",
        "테스트 케이스 기계적 도출",
    ),
    (
        "데이터분석가이드",
        "시계열 또는 숫자 목록 데이터에서 합계·평균·증감률·표준편차를 실제로 계산한 뒤, 그 결과를 근거로 인사이트 문장을 도출한다 — 계산되지 않은 숫자를 서술로 지어내지 않는다.",
        "수치 데이터 분석기",
    ),
]

# (skill_name, description) — augmentation_mode='none'(지식 주입, 실행 가능한 Tool 없음).
KNOWLEDGE_SKILL_SPECS: list[tuple[str, str]] = [
    (
        "설계결정기록",
        "아키텍처 결정(인터페이스 선택, 재사용 vs 신규 작성, 데이터 흐름 변경, 하위 호환 여부 등)을 내린 직후, 구현을 시작하기 전에 ADR(결정·배경·대안·결과) 형식으로 문서화한다.",
    ),
    (
        "코드리뷰체크리스트",
        "PR을 올리기 전 또는 리뷰할 때 코딩 컨벤션·에러 처리·테스트 커버리지·가독성을 점검하는 범용 체크리스트를 적용한다.",
    ),
    (
        "커밋메시지작성",
        "커밋 메시지와 PR 설명을 팀 컨벤션(무엇을/왜 바꿨는지 명확히, 요약 + 근거 구조)에 맞게 작성한다.",
    ),
    (
        "문서작성가이드",
        "README·설계 문서·API 레퍼런스 등 프로젝트 문서를 팀의 문서화 스타일(용어 통일, 예시 포함, 간결한 구조)에 맞게 작성·검토한다.",
    ),
    (
        "장애회고",
        "작업 중 심각한 오류·회귀가 발생했을 때 원인·대응·재발 방지책을 비난 없이(blameless) 기록한다.",
    ),
    (
        "승인루프",
        "Spec·ADR·설계 문서 등 HITL(Human-in-the-loop) 승인이 필요한 산출물은 사람이 승인할 때까지 피드백을 반영해 재작성한다 — HAIR의 결재함(approvals)을 통해 상신하고, 반려되면 반려 사유를 반영한 개정판을 다시 올린다(같은 사유가 반복되면 방향 자체를 재검토한다).",
    ),
    (
        "전략분석프레임워크",
        "3C(자사·고객·경쟁사), 4P(제품·가격·유통·판촉), SWOT(강점·약점·기회·위협) 분석 프레임워크로 시장·제품·전략을 구조적으로 진단한다 — 각 범주를 빠짐없이 채우고, 추측이 아니라 조사된 근거를 바탕으로 작성한다.",
    ),
    (
        "기획서작성가이드",
        "기획서는 배경(왜 필요한가)·목표(무엇을 달성하는가)·실행계획(어떻게·누가·언제)·예산·리스크와 대응방안 순으로 구성한다 — 의사결정자가 한 번에 핵심을 파악하도록 결론을 앞에 둔다. 실행 가능한 전체 소스 코드를 작성하지 않는다 — 구현은 개발 역할의 몫이고, 기획서는 요구사항·설계 방향·완료 기준(완료를 어떻게 확인할지)까지만 제시한다. 핵심 로직을 설명해야 하면 의사코드나 10줄 이내의 짧은 예시 스니펫만 쓴다.",
    ),
    (
        "사업전략수립",
        "사업 전략은 시장·경쟁 분석 → 목표 고객·포지셔닝 설정 → 차별화 전략 → 실행 로드맵 → 성과지표(KPI) 순으로 수립한다 — 분석 없이 바로 전략부터 제시하지 않는다(3C/4P/SWOT 분석 결과를 전제로 삼는다).",
    ),
    (
        "경영보고서작성",
        "C-level 보고서는 '핵심 요약(한눈에 결론)'을 맨 앞에 두고, 숫자·데이터 근거를 명시하며, 결론 → 근거 → 권고(Action) 순서로 1페이지 안에 의사결정에 필요한 정보를 압축한다 — 배경 설명을 길게 늘어놓지 않는다.",
    ),
    # --- 기획→개발 실행 파이프라인(PM·아키텍처·개발계획·UI·QA, 2026-10-14) ---
    (
        "진행관리조율",
        "기획 접수 → 요구사항 확정 → 설계 승인 → 개발 착수 → QA → 인수까지 단계별 진행 상태·담당 Agent·블로커를 추적한다. 각 단계 전환 전에 산출물이 HITL 승인을 받았는지 확인하고, 막힌 단계가 있으면 원인과 다음 조치를 명시한다 — 스스로 산출물을 만들지 않고 전체 진행을 조율·보고하는 역할이다.",
    ),
    (
        "아키텍처설계가이드",
        "애플리케이션 아키텍처는 모듈 경계(무엇을 한 모듈로 묶을지) → 모듈 간 인터페이스(어떤 데이터를 어떻게 주고받을지) → 기술 스택 선정(왜 그 기술인지 대안과 비교) 순으로 설계한다 — 요구사항/스펙에 없는 기능을 임의로 추가하지 않고, 구현 난이도가 아니라 요구사항 충족을 기준으로 모듈을 나눈다.",
    ),
    (
        "데이터모델설계가이드",
        "데이터 모델은 요구사항에서 다뤄지는 엔티티와 그 속성을 먼저 나열 → 엔티티 간 관계(1:1/1:N/N:M)를 정의 → 정규화 수준을 결정 → 조회 패턴(어떤 질의가 자주 발생하는지)에 맞춰 인덱스/비정규화 여부를 검토하는 순서로 진행한다 — ERD와 함께 왜 그 구조를 택했는지 근거를 남긴다.",
    ),
    (
        "모듈분해업무배분",
        "아키텍처 설계 산출물을 받아 독립적으로 개발 가능한 모듈 단위로 쪼개고, 모듈 간 의존성(어떤 모듈이 먼저 끝나야 다른 모듈을 시작할 수 있는지)을 표시한 뒤, 병렬로 진행 가능한 모듈은 서로 다른 개발자 Agent에 배분한다 — 배분 결과에는 각 모듈의 담당자·선행 조건·완료 기준이 포함돼야 한다.",
    ),
    (
        "프론트엔드구현가이드",
        "프론트엔드 구현은 UI 기획(화면 흐름·컴포넌트 목록)을 먼저 확인하고, 컴포넌트 단위로 상태·이벤트 흐름을 분리하며, 접근성(키보드 조작·대비)과 반응형(화면 크기 대응)을 기본으로 고려한다 — 디자인에 없는 화면/동작을 임의로 추가하지 않는다.",
    ),
    (
        "UIUX기획가이드",
        "UI/UX 기획은 사용자 시나리오(누가 어떤 목적으로 어떤 순서로 쓰는지) → 화면 흐름도(어떤 화면에서 어떤 화면으로 이동하는지) → 화면별 구성 요소와 인터랙션(클릭/입력 시 어떻게 반응하는지) 순으로 정리한다 — 요구사항/스펙에 없는 화면·기능을 임의로 추가하지 않는다.",
    ),
    (
        "테스트계획설계가이드",
        "테스트 계획은 테스트 범위(무엇을 테스트하고 무엇은 제외하는지) → 테스트 전략(단위/통합/E2E 중 어디에 집중할지) → 요구사항별 테스트 케이스(정상 케이스·예외 케이스·경계값) 순으로 작성한다 — 테스트 케이스는 실행 전 단계 산출물이라, 이 역할은 케이스를 설계만 하고 직접 실행(QA 기능테스터의 역할)하지 않는다.",
    ),
    (
        "품질인수기준가이드",
        "프로젝트 전체 품질/인수 기준은 Gate A-G 점수·테스트 커버리지·미해결 결함 심각도를 종합해 판정한다 — 개별 모듈이 통과해도 전체 인수 기준(예: 치명적 결함 0건, 핵심 요구사항 커버리지 100%)을 못 채우면 배포 불가로 판정하고, 무엇이 부족한지 구체적으로 남긴다.",
    ),
    # --- "자율 개발 크루"(10인 개발자 풀, 2026-10-14) ---
    (
        "데이터인프라구현가이드",
        "데이터/인프라 구현은 DA가 설계한 ERD·스키마를 실제 마이그레이션/DDL로 옮기고, 배포 파이프라인(CI/CD)·환경 변수·인프라 설정을 구성한다 — 설계에 없는 테이블/컬럼을 임의로 추가하지 않고, 스키마 변경은 마이그레이션 이력으로 남긴다.",
    ),
    # --- 제안서/실적서 작성(2026-11-11) ---
    (
        "제안서작성가이드",
        "제안서(RFP 대응·수주 제안)는 과제 이해(발주처가 요구하는 것) → 차별화 포인트(경쟁사 대비 우리만의 강점) → 수행 방안(구체적 실행 계획) → 일정/비용 → 기대효과 순으로 구성한다 — 기획서(내부 실행계획 문서)와 달리 '왜 우리를 선택해야 하는가'를 명시적으로 답해야 하고, 평가 기준(배점표)이 주어졌으면 항목별로 빠짐없이 대응한다.",
    ),
    (
        "실적서작성가이드",
        "실적서(KPI·성과 보고)는 목표 대비/전기(전년·전분기 등) 대비 수치표 → 달성·미달 원인 분석 → 차기 계획 순으로 구성한다 — C-level 보고서(경영진 요약 중심)와 달리 수치 비교표가 본문의 핵심이며, 분석되지 않은 숫자를 서술로 지어내지 않고 데이터 분석 결과를 그대로 인용한다.",
    ),
]

# (role_name, skill_tags, tuning_config)
AGENT_SPECS: list[tuple[str, list[str], dict]] = [
    # --- Harness Methodology 개발 파이프라인(2026-10-13) ---
    (
        "요구사항 분석가",
        ["spec-driven-artifacts", "승인루프", "요구사항검증"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "스펙 작성자",
        [
            "spec-driven-artifacts",
            "문서작성가이드",
            "요구사항검증",
            "실패모드도출",
        ],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "코드베이스 분석가",
        ["지식검색", "관계조회", "설계결정기록"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        # 사용자 요청(2026-10-26, "개발자 에이전트 역할이 백엔드이면 역할도
        # 백엔드 개발자로 수정해줘") — 이 Agent는 "자율 개발 크루"에서
        # 백엔드를 담당하는데 이름이 범용 "개발자"라 역할이 드러나지
        # 않았다(프론트엔드/데이터·인프라/풀스택은 전부 이름에 영역이
        # 드러남). team_members.role_label은 이미 "백엔드"였다.
        "백엔드 개발자",
        # "장애회고" 추가(사용자 요청, 2026-10-27, "AI 에이전트,
        # 도구/skill 각각의 품질을 검사하고 개선해줘" 감사 중 발견 — 이
        # 스킬이 정의만 돼 있고 "자율 개발 크루" 그 어느 역할에도 연결돼
        # 있지 않았다. 실패 재현·회귀 대응을 실측으로 이미 여러 번 겪은
        # 팀(위 reasoning 끄기 결정 등)인데 정작 사후 기록 역량이 없었다).
        ["tdd-ai-workflow-checklist", "커밋메시지작성", "장애회고"],
        # 사용자 보고(2026-10-18, "@자율 개발 크루... 일부 응답이
        # 실패했습니다") 원인 분석 후 조치 — "자율 개발 크루" 12명
        # 전원이 reasoning=True라 Crew 실행 한 번에 LLM 왕복 호출이
        # 수십 번 체인으로 걸렸고, CrewAI 소스까지 추적해 그중 하나가
        # 빈 완성 응답을 내면 json.JSONDecodeError로 전체가 실패하는
        # 걸 확인했다(실측: 2026-10-15~18 사이 동일 원인으로 5회+
        # 재현). 사용자 결정으로 이 팀 12명 전원 reasoning을 끈다 —
        # 호출 수가 절반 이하로 줄어 실패 표면적이 그만큼 작아진다.
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "코드 검증가",
        # "장애회고" 추가 — 바로 위 "백엔드 개발자" 항목의
        # 주석 참고(2026-10-27 품질 감사).
        [
            "harness-gate-ci",
            "gate-improvement-loop",
            "evidence-based-gate-fix",
            "recommend-fix-workflow",
            "코드리뷰체크리스트",
            "장애회고",
        ],
        # 사용자 보고(2026-10-18, "@자율 개발 크루" 품질 분석 중 실측) —
        # 이 역할이 파이프라인(run_crew() 경로)에서 길어진 대화/산출물
        # 맥락을 받아 표가 많은 긴 검증 보고서를 쓸 때 reasoning의 "계획
        # 수립용 추가 LLM 호출"까지 겹쳐 응답이 매우 느려지고, 실제로
        # QA/품질관리자 호출에서 타임아웃까지 발생했다 — 아래 세 역할과
        # 함께 끈다(14번째 줄 "개발자" 12명 reasoning 끄기와 같은 원칙).
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "QA 기능테스터",
        # "장애회고" 추가 — "백엔드 개발자" 항목의 주석 참고
        # (2026-10-27 품질 감사).
        [
            "tdd-ai-workflow-checklist",
            "커버리지체크",
            "승인루프",
            "장애회고",
        ],
        {"temperature": 0.3, "max_retry_limit": 3},
    ),
    # --- 경영/사업 전략 파이프라인(2026-10-14) ---
    (
        "외부 자료 조사가",
        # "외부검색"(Wikipedia)은 사용자 요청(2026-10-26, "도구/skill에서
        # 동작하지 않는 검색은 삭제해줘")로 "실시간웹검색"(Tavily)으로
        # 대체했다 — Wikimedia가 이 배포 환경 IP를 지속 차단 중이라
        # 역할 자체가 무력화돼 있었다(db.py의 `_seed_default_pools()`
        # 주석 참고).
        ["실시간웹검색", "일반웹검색", "지식검색"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "3C4P SWOT 분석가",
        ["전략분석프레임워크", "프레임워크템플릿생성", "지식검색"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "기획서 작성자",
        [
            "기획서작성가이드",
            "문서작성가이드",
            "커버리지체크",
        ],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "사업전략 수립가",
        ["사업전략수립", "전략분석프레임워크", "지식검색"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.5},
    ),
    (
        "C-level 보고서 작성자",
        [
            "경영보고서작성",
            "문서작성가이드",
            "커버리지체크",
        ],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "프레젠테이션 자료 작성자",
        ["발표자료구성", "경영보고서작성"],
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
        ["진행관리조율", "승인루프", "spec-driven-artifacts"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "AA",
        ["아키텍처설계가이드", "설계결정기록"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    ),
    (
        "DA",
        ["데이터모델설계가이드", "설계결정기록", "관계조회"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "개발 계획 수립가",
        ["모듈분해업무배분", "설계결정기록"],
        # "자율 개발 크루" 매니저 — 아래 reasoning 끄기 조치 참고(25번 줄).
        {"temperature": 0.2},
    ),
    (
        "프론트엔드 개발자",
        # "장애회고" 추가 — "백엔드 개발자" 항목의 주석 참고
        # (2026-10-27 품질 감사).
        [
            "프론트엔드구현가이드",
            "tdd-ai-workflow-checklist",
            "커밋메시지작성",
            "장애회고",
        ],
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    # --- "자율 개발 크루" 확장(2026-10-14 도입, 2026-10-26 정리) — 사용자
    # 요청(2026-10-14): "기획서가 제시되면 이를 받아 자율적으로 개발을
    # 완료할 복수의 개발자(10명)가 포함된 crewai 에이전트 그룹 구성
    # 방안을 제안해줘(권한 위임을 통한 자율성을 보장, 가능하면 하네스
    # 방법론 적용)" — 당시엔 "Agent 10명 = 미리 쪼갠 Task 10개를 1:1
    # 배정"하는 모델로 "백엔드/프론트엔드 개발자를 각 3~4명씩" 만들었다.
    #
    # 그런데 2026-10-18 실측(`crew_runtime.py`의 `hierarchical_with_manager`
    # 주석 참고)으로 그 전제가 깨졌다 — CrewAI hierarchical은 `Task.agent`를
    # 무시하고 매니저가 Task를 **하나만** 받아 "Delegate work to coworker"
    # 도구로 그때그때 동적으로 역할별 하위 과업을 나눈다. 즉 같은 영역
    # 안의 여러 Agent는 skill_tags·tuning_config가 완전히 동일한 클론이고,
    # 매니저가 이름만 다른 동명이인을 구분해 쓸 근거가 전혀 없다(실측
    # 재현: "백엔드 모듈을 구현하세요" 안내문도 전원 글자 하나 안 다름).
    # 사용자 분석 요청(2026-10-26) 후 영역별 1명으로 정리 — 매니저가
    # 같은 코워커 한 명에게 하위 과업을 여러 번 나눠 위임하는 것도
    # 기술적으로 동일하게 동작하므로 기능 손실이 없다.
    (
        "데이터/인프라 개발자",
        # "장애회고" 추가 — "백엔드 개발자" 항목의 주석 참고
        # (2026-10-27 품질 감사).
        [
            "데이터인프라구현가이드",
            "tdd-ai-workflow-checklist",
            "커밋메시지작성",
            "장애회고",
        ],
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "풀스택 개발자",
        # "장애회고" 추가 — "백엔드 개발자" 항목의 주석 참고
        # (2026-10-27 품질 감사).
        [
            "tdd-ai-workflow-checklist",
            "커밋메시지작성",
            "프론트엔드구현가이드",
            "장애회고",
        ],
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    (
        "UI 기획자",
        ["UIUX기획가이드", "문서작성가이드"],
        {"temperature": 0.4},
    ),
    (
        "테스트 계획 수립가",
        [
            "테스트계획설계가이드",
            "테스트케이스도출",
            "요구사항검증",
        ],
        {"temperature": 0.3, "max_retry_limit": 3},
    ),
    (
        "품질 관리자",
        [
            "품질인수기준가이드",
            "harness-gate-ci",
            "gate-improvement-loop",
            "evidence-based-gate-fix",
        ],
        {"temperature": 0.2, "max_retry_limit": 3},
    ),
    # 사용자 요청(2026-11-08, "사업/기획 업무 처리 파이프라인" 신설) —
    # 라이브 DB에는 이미 수동으로 추가돼 있었지만(스킬 태그 1개짜리
    # 최소 구성) 이 목록에는 없어서, 이 파이프라인을 새로 설치하는
    # 환경에서는 "표현 검토" 단계 Agent가 하나도 안 생기는 문제가
    # 있었다(실측 확인 — `:memory:` DB로 재현). 기존 라이브 DB의
    # 스킬 태그 그대로 여기로 옮겨 정규 시드 목록에 포함시킨다.
    (
        "콘텐츠 카피라이팅",
        ["카피라이팅"],
        {"temperature": 0.5},
    ),
    # --- 데이터 분석/제안서/실적서 작성(2026-11-11, "기획/전략/자료조사/
    # 보고서작성/데이터분석/제안서작성/실적서작성 개선 방안을 기획해줘"
    # → "바로 구현해줘") ---
    (
        "데이터 분석가",
        ["데이터분석가이드", "지식검색"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.2},
    ),
    (
        "제안서 작성자",
        ["제안서작성가이드", "문서작성가이드", "실시간웹검색"],
        {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    ),
    (
        "실적 보고서 작성자",
        ["실적서작성가이드", "경영보고서작성", "문서작성가이드"],
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
# 사용자 분석 요청(2026-10-26, "같은 업무의 에이전트가 1,2,3 등으로...
# 각 영역에 하나의 에이전트만 있어도 되지 않는지") 후 정리 — 영역별
# 복수 인스턴스는 CrewAI hierarchical이 Task를 매니저에게 하나만 주고
# "Delegate work to coworker"로 동적 위임하는 실제 메커니즘(위
# `AGENT_SPECS`의 같은 날짜 주석 참고)에서 아무 기능적 차이가 없었다 —
# 영역별 1명(백엔드/프론트엔드/데이터·인프라/풀스택)으로 줄인다.
DEV_CREW_TEAM_DESCRIPTION = (
    "요구분석 → 설계 → 개발 계획 수립 → 구현 → 코드 검증 → QA 기능테스트 → "
    '품질 관리 최종 판정까지, "소프트웨어 개발 파이프라인"(`teams.'
    "pipeline_template_id`로 연결)이 Gate 기준 자동 재시도·승인 "
    "에스컬레이션까지 갖춰 실제 품질을 보장하며 수행하는 크루(사용자 "
    '요청, 2026-10-28, "파이프라인의 에이전트 구성과 흐름을 참조해 '
    "구성과 업무 흐름을 실제 개발 품질을 보장하며 수행될 수 있도록 "
    '재구성"). 대화에서 `@자율 개발 크루`로 멘션하면 이 파이프라인이 '
    "그대로 실행된다(`conversations.run_team_mention_job()` 참고) — "
    "아래 멤버 구성은 그 7단계 전체에 참여하는 13개 역할이다. "
    "Crew 빌더/팀 Agent 실행 화면에서 수동으로 hierarchical Crew를 "
    "돌리는 기존 방식도 하위 호환으로 계속 쓸 수 있다(그땐 13명 전원이 "
    "한 번에 참여하는 단일 Crew가 된다 — 단계별 Gate 검증은 파이프라인 "
    "경로에만 있다)."
)
# 사용자 요청(2026-10-28, "파이프라인의 에이전트 구성과 흐름을 참조해
# 팀 관리의 '#3 자율 개발 크루'의 구성과 업무 흐름을... 재구성") —
# 기존엔 "구현" 단계 6명(매니저 1 + 개발자 4 + UI 기획자)만 팀원으로
# 등록돼 있어, "팀 관리" 화면을 봐도 이 팀이 요구분석·설계·검증·QA·
# 품질 관리까지 포함한 전체 파이프라인을 수행한다는 사실이 전혀
# 안 보였다. `DEV_PIPELINE_STAGES`가 참조하는 13개 역할 전부를
# 등록하고, role_label에 어느 단계인지 명시해 "팀 관리" 화면만 봐도
# 전체 업무 흐름이 보이게 한다(순서는 파이프라인 단계 순서와 동일).
DEV_CREW_TEAM_MEMBER_SPECS: list[tuple[str, str]] = [
    ("요구사항 분석가", "1단계: 요구분석/계획 확정(사람 승인 1회)"),
    ("AA", "2단계: 설계(아키텍처)"),
    ("DA", "2단계: 설계(데이터 모델)"),
    (
        "개발 계획 수립가",
        "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)",
    ),
    ("백엔드 개발자", "4단계: 구현(백엔드)"),
    ("프론트엔드 개발자", "4단계: 구현(프론트엔드)"),
    ("데이터/인프라 개발자", "4단계: 구현(데이터/인프라)"),
    ("풀스택 개발자", "4단계: 구현(풀스택, 유동 배치)"),
    ("UI 기획자", "4단계: 구현(UI/UX)"),
    ("코드 검증가", "5단계: 코드 검증(Gate 0.5)"),
    ("테스트 계획 수립가", "6단계: QA 기능테스트(테스트 설계)"),
    ("QA 기능테스터", "6단계: QA 기능테스트(실행, Gate 0.5)"),
    ("품질 관리자", "7단계: 품질 관리 최종 판정(Gate 0.5)"),
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

    # 사용자 요청(2026-11-02, "기존 대화, 업무 요청, 팀 관리 내용을 모두
    # 초기화해줘" — "자율 개발 크루"도 포함해 전부 삭제)로 이 팀의 자동
    # 재생성 로직을 제거했다. `teams`/`team_members`는 더 이상 `seed()`가
    # 건드리지 않는다 — 필요하면 "팀 관리" UI에서 다시 만든다.
    _seed_dev_pipeline(conn)
    # 사용자 요청(2026-11-08, "게임 개발이 아닌 일상 사무 업무를 포함한
    # 다른 복잡한 업무 처리를 위해 일반화") — SW 개발 파이프라인과 같은
    # 구조(sequential_relay 우선, 요구사항-섹션 매핑 계획, 참여자 전원
    # 산출물 보존, 요구사항 추적성 검증)를 재사용해 비SW 도메인 파이프
    # 라인을 신설한다. `기획서 작성자`/`사업전략 수립가`/`C-level 보고서
    # 작성자`/`외부 자료 조사가`/`3C4P SWOT 분석가`/`프레젠테이션 자료
    # 작성자`/`콘텐츠 카피라이팅`/`PM` — agent_pool에 이미 있었지만
    # 자동 파이프라인에 전혀 연결 안 돼 있던 역할들(SW 역할들이 2026-
    # 10-28 이전에 겪었던 것과 같은 공백)을 이번에 연결한다. 새 Agent는
    # 전혀 만들지 않는다.
    _seed_business_planning_pipeline(conn)
    # 사용자 요청(2026-11-11, "기획/전략/자료조사/보고서작성/데이터분석/
    # 제안서작성/실적서작성... 바로 구현해줘") — 제안서/실적서는 "작성"
    # 단계에 이미 4명이 있는 "사업/기획 업무 처리 파이프라인"에 끼워
    # 넣지 않고(브리프마다 필요 없는 문서까지 매번 생기는 낭비 방지)
    # 독립 파이프라인으로 추가한다. 둘 다 "데이터 분석가"를 공유한다.
    _seed_proposal_pipeline(conn)
    _seed_performance_report_pipeline(conn)


# 사용자 요청(2026-10-18, "BMAD-METHOD(https://github.com/bmad-code-org/
# bmad-method)와 하네스 방법론을 참조해, 최소의 HITL, HOTL으로 수준높은
# 개발 결과물 생성 가능한 개발 Agent 팀을 구성(기존의 개발팀 갱신)하고
# 각 agent가 역할을 구분해 각 개발 단계별 단독 또는 복수의 에이전트가
# 순차/병행 처리하며 개발 진행") — 직전 분석에서 확인한 두 문제(①
# "자율 개발 크루"에 PM/AA/DA/요구분석/코드검증/QA/테스트계획/품질관리
# 10개 역할이 전혀 안 들어가 있음, ② Gate A-G 측정이 대화창 경로에서는
# 저장·활용되지 않고 버려짐)를 이 7단계 파이프라인이 메운다. BMAD의
# "계획(PRD+Architecture) 승인은 1회, 그 뒤 Dev→QA Gate 반복"을 그대로
# 반영해 1단계만 `requires_human_approval=1`이다. "자율 개발 크루" 팀은
# 그대로 두고(하위 호환, 수동 Crew 실행용) 이 파이프라인이 정식 개발
# 진행 경로로 추가된다 — 새 Agent는 전혀 만들지 않고 기존 22개 역할 중
# 13개를 그대로 재사용한다(2026-10-28 현행화 — "구현" 단계가 2026-10-26
# "자율 개발 크루" 12명→6명 정리에 맞춰 개발자 4명+UI기획자로 줄어든
# 뒤의 실제 참조 역할 수. `db.py`의 `_sync_dev_pipeline_implementation_
# stage_agents()`가 이미 한 번 심어진 DB도 같이 맞춘다).
# `db.py`의 `_DEV_PIPELINE_GATED_STAGE_NAMES`와 같은 값을 유지해야
# 한다(신규 DB는 여기서, 기존 DB는 그쪽 마이그레이션에서 적용 —
# `db.py`가 이 모듈을 import하므로 반대 방향 import는 순환 참조라
# 상수를 공유하지 못하고 중복 정의한다).
_DEV_PIPELINE_GATED_STAGE_NAMES = ("코드 검증", "QA 기능테스트", "품질 관리 최종 판정")

DEV_PIPELINE_NAME = "소프트웨어 개발 파이프라인"
DEV_PIPELINE_DESCRIPTION = (
    "기획서 → 요구분석(계획 확정, 사람 승인 1회) → 설계(AA→DA) → 개발 계획 수립 → "
    "구현(매니저가 개발자 4명+UI기획자에게 실제 위임) → 코드 검증 → QA 기능테스트 → "
    "품질 관리 최종 판정까지, Gate A-G 기준으로 자동 반복하며 진행하는 정식 "
    "개발 파이프라인(BMAD-METHOD 대응 — 계획 확정 시점에만 사람이 개입한다)."
)
# (name, mode, lead_role_name, instructions, requires_human_approval,
#  agents=[(role_name, instructions), ...])
# (name, mode, lead_role_name, instructions, requires_human_approval,
#  is_primary_deliverable, agents=[(role_name, instructions), ...])
#
# `is_primary_deliverable`(2026-10-19 추가) — 사용자 보고("Todo 앱 최종
# 결과물에 코드가 아니라 품질 리포트만 있다") 원인 수정. Task 협업
# 워크스페이스의 "최종 결과물"은 전엔 "가장 최근 approved된
# deliverable"을 그대로 집었는데, 코드 검증/QA/품질 판정류는 전부
# 자동 approved로 쌓이고 항상 맨 마지막에 돌아 실제 산출물(구현 단계의
# 코드)을 밀어냈다. "구현" 단계만 True로 표시해 그 단계의 산출물을
# 최종 결과물로 고정한다(나머지는 검증/보고 성격이라 False).
#
# `checks_requirements_traceability`(2026-11-08 추가, "게임이 아닌
# 일상 사무 업무도 일반화" 요청) — 기존엔 `pipeline_runtime.py`가
# `stage["name"] == "코드 검증"` 문자열 매칭으로 요구사항 추적성 LLM
# judge를 걸었다. 새 파이프라인 템플릿(사업기획서 등)이 이 단계를
# 다른 이름으로 지어도 추적성 검증을 받을 수 있도록, 단계명과 무관한
# 명시적 플래그로 뺀다 — "코드 검증" 역할의 단계만 True, 나머지는
# False(품질관리/QA는 1단계 요구사항이 아니라 다른 입력을 보는
# 단계라 그대로 제외).
#
# `output_content_type`(2026-11-10 추가, "결과물 출력 유형 다양화"
# 요청) — `db.py`의 `pipeline_stages` CREATE TABLE 설명 참고. "구현"
# 단계만 'code'(실제 코드 산출물이라 syntax highlighting 렌더링
# 혜택이 있음), 나머지는 전부 'text'(검증/보고 성격 텍스트 — 지금
# 렌더러는 text/markdown을 동일하게 처리해 실질적 차이가 아직 없지만,
# 틀린 값으로 표시하진 않는다).
DEV_PIPELINE_STAGES: list[
    tuple[str, str, str | None, str, bool, bool, list[tuple[str, str]], bool, str]
] = [
    (
        "요구분석/계획 확정",
        "single",
        None,
        (
            "요구사항을 EARS 표기(WHEN/IF/WHILE ... THE SYSTEM SHALL ...)로 정리하고, "
            "[NEEDS CLARIFICATION]이 남아있지 않은지 스스로 점검하세요."
        ),
        True,
        False,
        [("요구사항 분석가", "")],
        False,
        "text",
    ),
    (
        "설계",
        "sequential_relay",
        None,
        "앞 단계의 요구사항을 바탕으로 설계하세요.",
        False,
        False,
        [
            ("AA", "모듈 경계와 모듈 간 인터페이스를 설계하세요."),
            ("DA", "앞서 나온 아키텍처를 바탕으로 데이터 모델(ERD)을 설계하세요."),
        ],
        False,
        "text",
    ),
    (
        "개발 계획 수립",
        "single",
        None,
        (
            "설계 산출물을 독립적으로 개발 가능한 모듈 단위로 쪼개세요. 1단계에서 "
            "정리된 요구사항(EARS 문장) 각각이 어느 모듈에 배정되는지 명시적으로 "
            "매핑하세요 — 모든 요구사항이 반드시 하나 이상의 모듈에 배정돼야 하며, "
            "빠지는 요구사항이 있으면 안 됩니다. 모듈마다 '이 모듈이 커버하는 "
            "요구사항 번호'와 '구체적 완료 기준(무엇이 실제로 동작해야 완료로 "
            "보는지)'을 반드시 함께 적으세요 — 완료 기준 없이 모듈명만 나열하지 "
            "마세요."
        ),
        False,
        False,
        [("개발 계획 수립가", "")],
        False,
        "text",
    ),
    (
        "구현",
        "sequential_relay",
        None,
        (
            "앞 단계의 개발 계획(요구사항-모듈 매핑)에서 당신에게 배정된 모듈을 "
            "찾아, 그 모듈이 커버해야 할 요구사항을 전부 충족하는 실제로 동작하는 "
            "완성된 코드를 작성하세요 — 설계 논의나 의사코드로 끝내지 마세요. "
            "이전 개발자가 이미 작성한 코드는 그대로 유지하고(다시 쓰거나 지우지 "
            "말 것), 당신이 맡은 부분만 이어서 작성하세요."
        ),
        False,
        True,
        [
            ("UI 기획자", "구현에 필요한 화면 흐름/인터랙션을 먼저 정리해 다음 개발자들에게 전달하세요."),
            ("데이터/인프라 개발자", "데이터 모델/상태 구조를 구현하세요."),
            ("백엔드 개발자", "게임/업무 로직(규칙·판정·상태 전이)을 구현하세요."),
            ("프론트엔드 개발자", "화면 렌더링과 입력 처리를 구현하세요."),
            (
                "풀스택 개발자",
                ("지금까지 작성된 모든 모듈(UI 흐름/데이터/로직/렌더링)을 검토해 "
                "빠진 기능을 직접 채워 넣고, 하나의 실행 가능한 완성본으로 통합해 "
                "제출하세요."),
            ),
        ],
        False,
        "code",
    ),
    (
        "코드 검증",
        "single",
        None,
        "구현 산출물을 코드리뷰체크리스트 기준으로 검증하세요.",
        False,
        False,
        [("코드 검증가", "")],
        True,
        "text",
    ),
    (
        "QA 기능테스트",
        "sequential_relay",
        None,
        "구현 산출물을 테스트하세요.",
        False,
        False,
        [
            (
                "테스트 계획 수립가",
                "요구사항별 테스트 케이스(정상/예외/경계값)를 설계하세요.",
            ),
            ("QA 기능테스터", "앞서 설계된 테스트 케이스로 실제 기능을 검증하세요."),
        ],
        False,
        "text",
    ),
    (
        "품질 관리 최종 판정",
        "single",
        None,
        (
            "지금까지의 전체 산출물을 종합해 품질/인수 기준(치명적 결함 0건, 핵심 요구사항 "
            "커버리지 등)으로 최종 판정하세요."
        ),
        False,
        False,
        [("품질 관리자", "")],
        False,
        "text",
    ),
]


def _seed_dev_pipeline(conn: sqlite3.Connection) -> None:
    """`seed()`가 마지막에 호출한다 — `pipeline_templates.name`은 UNIQUE
    제약이 없어 이름으로 존재를 확인한다(teams와 같은 패턴). 아직 사용자가
    하나도 없는 빈 DB에서도 호출되므로 `audit.record()`(actor_id FK 필요)
    없이 직접 INSERT한다.

    **`min_gate_pass_ratio`를 대부분 0.0(기본값, 미지정)으로 둔 이유** —
    실측(2026-10-18, 실제 "자율 개발 크루" 실행)으로 확인한 바 Gate C/D/G는
    이 플랫폼의 단순한 휴리스틱 채점 특성상 구조적으로 자주 fail하거나
    not_measured로 나온다(`agent_evaluation.py` docstring의 기존 한계
    설명과 일치). 기본 시드에서부터 통과 기준을 걸면 첫 실행부터 거의
    매 단계가 에스컬레이션돼 "최소 HITL"의 의도와 반대로 동작한다 —
    Gate 측정·기록 자체는 전 단계에서 그대로 하되(감사·가시성 목적),
    실제로 진행을 막을지는 관리자가 빌더 UI에서 단계별로 직접 올리도록
    남겨둔다.

    **예외 — "코드 검증"/"QA 기능테스트"/"품질 관리 최종 판정"은
    0.5**(사용자 요청, 2026-10-28, "'자율 개발 크루'의 경우 파이프라인을
    참조해 실제 개발 품질을 보장하며 수행될 수 있도록 재구성") — 이
    3단계는 이 파이프라인이 "실제 품질을 보장"한다고 말할 수 있으려면
    적어도 중간 강도로는 막아야 한다는 사용자 결정. 나머지 단계(요구
    분석/설계/개발 계획 수립)는 위 이유 그대로 0.0 유지."""
    existing = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?", (DEV_PIPELINE_NAME,)
    ).fetchone()
    if existing is not None:
        return
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (DEV_PIPELINE_NAME, DEV_PIPELINE_DESCRIPTION, "software_dev"),
    )
    template_id = cur.lastrowid
    for seq, (
        name,
        mode,
        lead_role_name,
        instructions,
        requires_approval,
        is_primary,
        agents,
        checks_traceability,
        output_content_type,
    ) in enumerate(DEV_PIPELINE_STAGES, start=1):
        gate_ratio = 0.5 if name in _DEV_PIPELINE_GATED_STAGE_NAMES else 0.0
        cur = conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, "
            "lead_role_name, instructions, requires_human_approval, is_primary_deliverable, "
            "min_gate_pass_ratio, checks_requirements_traceability, output_content_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                seq,
                name,
                mode,
                lead_role_name,
                instructions,
                1 if requires_approval else 0,
                1 if is_primary else 0,
                gate_ratio,
                1 if checks_traceability else 0,
                output_content_type,
            ),
        )
        stage_id = cur.lastrowid
        for agent_seq, (role_name, agent_instructions) in enumerate(agents, start=1):
            agent_row = conn.execute(
                "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue  # 방어적 처리 — AGENT_SPECS에 이미 다 있어 정상 경로에선 안 탐.
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, agent_seq, agent_instructions),
            )
    # "자율 개발 크루" 팀을 이 템플릿에 연결한다 — `@팀이름` 멘션이
    # 이 파이프라인을 타게 하는 스위치(`conversations.run_team_
    # mention_job()` 참고). `teams`가 아직 안 만들어졌을 수도 있는
    # 호출 순서(둘 다 `seed()`가 호출하지만 순서를 서로 가정하지
    # 않기 위해 방어적으로 존재 확인).
    conn.execute(
        "UPDATE teams SET pipeline_template_id = ? "
        "WHERE name = ? AND pipeline_template_id IS NULL",
        (template_id, DEV_CREW_TEAM_NAME),
    )


# ---------- 사업/기획 업무 처리 파이프라인(2026-11-08) ----------
#
# 사용자 요청("게임 개발이 아닌 일상 사무 업무를 포함한 다른 복잡한
# 업무 처리를 위해 일반화") — SW 개발 파이프라인에서 실측으로 검증한
# 구조를 그대로 재사용한다:
# - 계획 수립 단계는 1단계 요구사항 각 항목을 어느 섹션이 커버하는지
#   명시적으로 매핑하고 완료 기준을 적게 한다(모호한 지시문이 "논의만
#   하고 끝" 문제를 일으켰던 실측 교훈 — "개발 계획 수립" 단계와 같은
#   원칙).
# - "작성" 단계는 `sequential_relay`를 쓴다(hierarchical/위임은 BMAD
#   코어 자체도 서드파티 애드온일 뿐이고 CrewAI 커뮤니티에 신뢰성
#   문제가 널리 보고돼 있다 — 외부 조사, 2026-11-07). 매니저도, 위임
#   도구 호출도 없어 그 신뢰성 문제 자체가 구조적으로 발생하지 않는다.
# - "작성" 단계는 `is_primary_deliverable=True`로 표시해 Task 최종
#   결과물 자리를 지킨다. 검증 단계는 `checks_requirements_
#   traceability=True`로 1단계 요구사항과 대조한다 — 둘 다 단계명과
#   무관한 플래그라(이 파일 상단 "구현"/"코드 검증" 일반화 참고)
#   "구현"/"코드 검증"이라는 이름을 전혀 안 써도 똑같이 동작한다.
#
# 새 Agent는 전혀 만들지 않는다 — `AGENT_SPECS`에 이미 있었지만
# 자동 파이프라인에 연결 안 돼 있던 역할만 재사용한다.
BUSINESS_PIPELINE_NAME = "사업/기획 업무 처리 파이프라인"
BUSINESS_PIPELINE_DESCRIPTION = (
    "브리프 → 요구사항 분석(계획 확정, 사람 승인 1회) → 자료 조사·분석 → "
    "기획 계획 수립 → 작성(기획서 작성자→사업전략 수립가→C-level 보고서 "
    "작성자→프레젠테이션 자료 작성자) → 내용 검증 → 표현 검토 → 최종 "
    "승인 판정까지, Gate A-G 기준으로 자동 반복하며 진행하는 사업/기획 "
    "업무 파이프라인(소프트웨어 개발 파이프라인과 같은 엔진·같은 구조를 "
    "비SW 도메인에 재사용 — 계획 확정 시점에만 사람이 개입한다)."
)

BUSINESS_PIPELINE_STAGES: list[
    tuple[str, str, str | None, str, bool, bool, list[tuple[str, str]], bool, str]
] = [
    (
        "요구사항 분석/계획 확정",
        "single",
        None,
        (
            "브리프를 섹션/항목별 요구사항 문장(예: '사업 개요 섹션은 ...을 "
            "포함해야 한다')으로 명확히 정리하고, 모호하거나 빠진 부분이 "
            "없는지 스스로 점검하세요."
        ),
        True,
        False,
        [("요구사항 분석가", "")],
        False,
        "text",
    ),
    (
        "자료 조사·분석",
        "sequential_relay",
        None,
        "앞 단계의 요구사항을 바탕으로 필요한 자료를 조사하고 분석하세요.",
        False,
        False,
        [
            ("외부 자료 조사가", "요구사항과 관련된 외부 자료(시장·경쟁사·통계 등)를 조사하세요."),
            (
                "데이터 분석가",
                "조사된 자료 중 수치 데이터가 있으면 합계·증감률 등을 실제로 계산해 근거로 쓸 수 있게 정리하세요.",
            ),
            (
                "3C4P SWOT 분석가",
                "앞서 조사·분석된 자료를 바탕으로 3C/4P/SWOT 등 프레임워크로 분석하세요.",
            ),
        ],
        False,
        "text",
    ),
    (
        "기획 계획 수립",
        "single",
        None,
        (
            "조사·분석 결과를 바탕으로 최종 문서를 섹션 단위로 쪼개세요. "
            "1단계에서 정리된 요구사항 각각이 어느 섹션에 배정되는지 "
            "명시적으로 매핑하세요 — 모든 요구사항이 반드시 하나 이상의 "
            "섹션에 배정돼야 하며, 빠지는 요구사항이 있으면 안 됩니다. "
            "섹션마다 '이 섹션이 커버하는 요구사항 번호'와 '구체적 완료 "
            "기준(무엇이 담겨야 완료로 보는지)'을 반드시 함께 적으세요 — "
            "완료 기준 없이 섹션명만 나열하지 마세요."
        ),
        False,
        False,
        [("PM", "")],
        False,
        "text",
    ),
    (
        "작성",
        "sequential_relay",
        None,
        (
            "앞 단계의 기획 계획(요구사항-섹션 매핑)에서 당신에게 배정된 "
            "섹션을 찾아, 그 섹션이 커버해야 할 요구사항을 전부 충족하는 "
            "실제로 완성된 내용을 작성하세요 — 개요나 논의로 끝내지 "
            "마세요. 이전 작성자가 이미 쓴 내용은 그대로 유지하고(다시 "
            "쓰거나 지우지 말 것), 당신이 맡은 부분만 이어서 작성하세요. "
            "발표용 슬라이드가 필요하면 마크다운 수평선(---)으로 슬라이드를 "
            "구분한 개요 형태로 작성하세요(슬라이드 1장당 제목 1개 + 핵심 "
            "요점 목록) — 이 단계의 산출물은 여러 작성자의 섹션이 합쳐진 "
            "하나의 텍스트 문서라, 별도 인터랙티브 HTML 파일로는 렌더링되지 "
            "않습니다."
        ),
        False,
        True,
        [
            ("기획서 작성자", "배경·목표·실행계획 섹션을 작성하세요."),
            ("사업전략 수립가", "사업전략·시장진입 섹션을 작성하세요."),
            ("C-level 보고서 작성자", "경영진 요약(Executive Summary) 섹션을 작성하세요."),
            (
                "프레젠테이션 자료 작성자",
                ("지금까지 작성된 모든 섹션을 검토해 빠진 내용을 직접 채워 "
                "넣고, 발표 가능한 하나의 완성된 문서로 통합해 제출하세요."),
            ),
        ],
        False,
        "text",
    ),
    (
        "내용 검증",
        "single",
        None,
        "작성 산출물이 1단계 요구사항을 실제로 충족하는지 검증하세요.",
        False,
        False,
        [("품질 관리자", "")],
        True,
        "text",
    ),
    (
        "표현 검토",
        "sequential_relay",
        None,
        "작성 산출물의 문체·표현·논리적 일관성을 검토하고 다듬으세요.",
        False,
        False,
        [("콘텐츠 카피라이팅", "문체와 표현을 다듬으세요.")],
        False,
        "text",
    ),
    (
        "최종 승인 판정",
        "single",
        None,
        (
            "지금까지의 전체 산출물을 종합해 품질/인수 기준(치명적 누락 0건, "
            "핵심 요구사항 커버리지 등)으로 최종 판정하세요."
        ),
        False,
        False,
        [("PM", "")],
        False,
        "text",
    ),
]

# "코드 검증"/"QA 기능테스트"/"품질 관리 최종 판정"과 같은 이유로
# "내용 검증"/"표현 검토"/"최종 승인 판정" 3단계만 0.5 — 나머지는
# 0.0(아직 임계값을 신뢰할 데이터가 없어 초기부터 걸면 "최소 HITL"
# 의도와 반대로 매번 에스컬레이션된다, `_seed_dev_pipeline()` 주석과
# 같은 근거).
_BUSINESS_PIPELINE_GATED_STAGE_NAMES = ("내용 검증", "표현 검토", "최종 승인 판정")


# ---------- 제안서 작성 파이프라인(2026-11-11) ----------
#
# 사용자 요청("기획, 전략, 자료조사, 보고서 작성, 데이터 분석, 제안서
# 작성, 실적서 작성 관련 내용들을 중심으로... 대화창 밑 업무 요청으로
# 처리할 수 있도록 개선 방안을 기획해줘" → "바로 구현해줘") — 제안서
# (RFP 대응·수주 제안)는 기획서와 달리 "경쟁사 대비 차별화"가 핵심이라
# 별도 Skill·Agent로 분리했다. 기존 "사업/기획 업무 처리 파이프라인"의
# "작성" 단계(기획서/전략/C-level/프레젠테이션 4명)에 끼워 넣지 않고
# 독립 파이프라인으로 둔 이유 — 그 단계는 브리프 하나당 매번 4종
# 문서가 전부 나오는 구조라, 제안서·실적서까지 거기 얹으면 필요 없는
# 문서까지 매번 만들어지는 낭비가 생긴다(사용자 확인 완료). 새 Agent는
# "데이터 분석가"/"제안서 작성자"만 추가하고, 나머지(요구사항 분석가/
# 외부 자료 조사가/품질 관리자/콘텐츠 카피라이팅/PM)는 기존 역할을
# 재사용한다.
PROPOSAL_PIPELINE_NAME = "제안서 작성 파이프라인"
PROPOSAL_PIPELINE_DESCRIPTION = (
    "브리프 → 요구사항 분석(계획 확정, 사람 승인 1회) → 자료 조사·분석"
    "(외부 자료 조사가→데이터 분석가) → 작성(제안서 작성자) → 내용 검증 "
    "→ 표현 검토 → 최종 승인 판정까지, Gate A-G 기준으로 자동 반복하며 "
    "진행하는 제안서(RFP 대응·수주 제안) 전용 파이프라인 — '사업/기획 "
    "업무 처리 파이프라인'과 같은 엔진·같은 구조를 재사용한다."
)

PROPOSAL_PIPELINE_STAGES: list[
    tuple[str, str, str | None, str, bool, bool, list[tuple[str, str]], bool, str]
] = [
    (
        "요구사항 분석/계획 확정",
        "single",
        None,
        (
            "브리프를 제안 요청(RFP) 항목별 요구사항 문장으로 명확히 "
            "정리하세요 — 발주처가 제시한 평가 기준(배점표)이 있다면 그 "
            "항목도 빠짐없이 요구사항으로 반영하세요."
        ),
        True,
        False,
        [("요구사항 분석가", "")],
        False,
        "text",
    ),
    (
        "자료 조사·분석",
        "sequential_relay",
        None,
        "앞 단계의 요구사항을 바탕으로 발주처·경쟁사 관련 자료를 조사하고 분석하세요.",
        False,
        False,
        [
            ("외부 자료 조사가", "발주처·경쟁사·시장 관련 자료를 조사하세요."),
            ("데이터 분석가", "조사된 자료 중 수치 데이터가 있으면 실제로 계산해 근거로 쓸 수 있게 정리하세요."),
        ],
        False,
        "text",
    ),
    (
        "작성",
        "single",
        None,
        (
            "조사·분석 결과를 바탕으로 제안서를 작성하세요 — 과제 이해, "
            "차별화 포인트(경쟁사 대비 강점), 수행 방안, 일정/비용, "
            "기대효과를 모두 포함하고, 1단계 요구사항(평가 기준 포함)을 "
            "전부 빠짐없이 반영하세요."
        ),
        False,
        True,
        [("제안서 작성자", "")],
        False,
        "text",
    ),
    (
        "내용 검증",
        "single",
        None,
        "작성 산출물이 1단계 요구사항(평가 기준 포함)을 실제로 충족하는지 검증하세요.",
        False,
        False,
        [("품질 관리자", "")],
        True,
        "text",
    ),
    (
        "표현 검토",
        "sequential_relay",
        None,
        "작성 산출물의 문체·표현·논리적 일관성을 검토하고 다듬으세요.",
        False,
        False,
        [("콘텐츠 카피라이팅", "문체와 표현을 다듬으세요.")],
        False,
        "text",
    ),
    (
        "최종 승인 판정",
        "single",
        None,
        (
            "지금까지의 전체 산출물을 종합해 품질/인수 기준(치명적 누락 "
            "0건, 평가 기준 커버리지 등)으로 최종 판정하세요."
        ),
        False,
        False,
        [("PM", "")],
        False,
        "text",
    ),
]

_PROPOSAL_PIPELINE_GATED_STAGE_NAMES = ("내용 검증", "표현 검토", "최종 승인 판정")


def _seed_proposal_pipeline(conn: sqlite3.Connection) -> None:
    """`seed()`가 `_seed_business_planning_pipeline()` 다음으로 호출한다 —
    같은 존재-확인 패턴(이름으로 중복 생성 방지)."""
    existing = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (PROPOSAL_PIPELINE_NAME,),
    ).fetchone()
    if existing is not None:
        return
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (PROPOSAL_PIPELINE_NAME, PROPOSAL_PIPELINE_DESCRIPTION, "proposal_writing"),
    )
    template_id = cur.lastrowid
    for seq, (
        name,
        mode,
        lead_role_name,
        instructions,
        requires_approval,
        is_primary,
        agents,
        checks_traceability,
        output_content_type,
    ) in enumerate(PROPOSAL_PIPELINE_STAGES, start=1):
        gate_ratio = 0.5 if name in _PROPOSAL_PIPELINE_GATED_STAGE_NAMES else 0.0
        cur = conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, "
            "lead_role_name, instructions, requires_human_approval, is_primary_deliverable, "
            "min_gate_pass_ratio, checks_requirements_traceability, output_content_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                seq,
                name,
                mode,
                lead_role_name,
                instructions,
                1 if requires_approval else 0,
                1 if is_primary else 0,
                gate_ratio,
                1 if checks_traceability else 0,
                output_content_type,
            ),
        )
        stage_id = cur.lastrowid
        for agent_seq, (role_name, agent_instructions) in enumerate(agents, start=1):
            agent_row = conn.execute(
                "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue  # 방어적 처리 — AGENT_SPECS에 이미 다 있어 정상 경로에선 안 탐.
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, agent_seq, agent_instructions),
            )


# ---------- 실적 보고 파이프라인(2026-11-11) ----------
#
# 제안서와 같은 사용자 요청 — 실적서(KPI 보고)는 정기·정형 문서라
# "표현 검토" 단계 없이 더 가볍게 구성한다(사용자 확인 완료). "최종
# 승인 판정" 단계 하나에 검증+요구사항 추적성 플래그를 함께 둔다 —
# 단계명과 무관하게 플래그로 동작한다는 기존 일반화 원칙 그대로.
PERFORMANCE_REPORT_PIPELINE_NAME = "실적 보고 파이프라인"
PERFORMANCE_REPORT_PIPELINE_DESCRIPTION = (
    "브리프 → 요구사항 분석(계획 확정, 사람 승인 1회) → 데이터 분석 → "
    "작성(실적 보고서 작성자) → 최종 승인 판정까지, Gate A-G 기준으로 "
    "자동 반복하며 진행하는 실적서(KPI·성과 보고) 전용 파이프라인 — "
    "정기·정형 문서 특성상 '사업/기획 업무 처리 파이프라인'보다 단계를 "
    "줄였다(표현 검토 단계 생략)."
)

PERFORMANCE_REPORT_PIPELINE_STAGES: list[
    tuple[str, str, str | None, str, bool, bool, list[tuple[str, str]], bool, str]
] = [
    (
        "요구사항 분석/계획 확정",
        "single",
        None,
        (
            "브리프를 실적 보고 항목(보고 기간·목표 대비·KPI 등)별 요구사항 "
            "문장으로 명확히 정리하세요."
        ),
        True,
        False,
        [("요구사항 분석가", "")],
        False,
        "text",
    ),
    (
        "데이터 분석",
        "single",
        None,
        "앞 단계의 요구사항에 해당하는 실적 수치를 목표 대비/전기 대비 등으로 실제로 계산해 분석하세요.",
        False,
        False,
        [("데이터 분석가", "")],
        False,
        "text",
    ),
    (
        "작성",
        "single",
        None,
        (
            "데이터 분석 결과를 바탕으로 실적 보고서를 작성하세요 — 목표 "
            "대비/전기 대비 수치표, 달성·미달 원인, 차기 계획을 포함하고 "
            "1단계 요구사항을 전부 빠짐없이 반영하세요."
        ),
        False,
        True,
        [("실적 보고서 작성자", "")],
        False,
        "text",
    ),
    (
        "최종 승인 판정",
        "single",
        None,
        (
            "작성 산출물이 1단계 요구사항을 실제로 충족하는지 검증하고, "
            "전체를 종합해 품질/인수 기준(수치 근거 누락 0건 등)으로 최종 "
            "판정하세요."
        ),
        False,
        False,
        [("PM", "")],
        True,
        "text",
    ),
]

_PERFORMANCE_REPORT_PIPELINE_GATED_STAGE_NAMES = ("최종 승인 판정",)


def _seed_performance_report_pipeline(conn: sqlite3.Connection) -> None:
    """`seed()`가 `_seed_proposal_pipeline()` 다음으로 호출한다 — 같은
    존재-확인 패턴(이름으로 중복 생성 방지)."""
    existing = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (PERFORMANCE_REPORT_PIPELINE_NAME,),
    ).fetchone()
    if existing is not None:
        return
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (
            PERFORMANCE_REPORT_PIPELINE_NAME,
            PERFORMANCE_REPORT_PIPELINE_DESCRIPTION,
            "performance_reporting",
        ),
    )
    template_id = cur.lastrowid
    for seq, (
        name,
        mode,
        lead_role_name,
        instructions,
        requires_approval,
        is_primary,
        agents,
        checks_traceability,
        output_content_type,
    ) in enumerate(PERFORMANCE_REPORT_PIPELINE_STAGES, start=1):
        gate_ratio = (
            0.5 if name in _PERFORMANCE_REPORT_PIPELINE_GATED_STAGE_NAMES else 0.0
        )
        cur = conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, "
            "lead_role_name, instructions, requires_human_approval, is_primary_deliverable, "
            "min_gate_pass_ratio, checks_requirements_traceability, output_content_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                seq,
                name,
                mode,
                lead_role_name,
                instructions,
                1 if requires_approval else 0,
                1 if is_primary else 0,
                gate_ratio,
                1 if checks_traceability else 0,
                output_content_type,
            ),
        )
        stage_id = cur.lastrowid
        for agent_seq, (role_name, agent_instructions) in enumerate(agents, start=1):
            agent_row = conn.execute(
                "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue  # 방어적 처리 — AGENT_SPECS에 이미 다 있어 정상 경로에선 안 탐.
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, agent_seq, agent_instructions),
            )


def _seed_business_planning_pipeline(conn: sqlite3.Connection) -> None:
    """`seed()`가 `_seed_dev_pipeline()` 다음으로 호출한다 — 같은
    존재-확인 패턴(이름으로 중복 생성 방지)."""
    existing = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (BUSINESS_PIPELINE_NAME,),
    ).fetchone()
    if existing is not None:
        return
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (BUSINESS_PIPELINE_NAME, BUSINESS_PIPELINE_DESCRIPTION, "business_planning"),
    )
    template_id = cur.lastrowid
    for seq, (
        name,
        mode,
        lead_role_name,
        instructions,
        requires_approval,
        is_primary,
        agents,
        checks_traceability,
        output_content_type,
    ) in enumerate(BUSINESS_PIPELINE_STAGES, start=1):
        gate_ratio = 0.5 if name in _BUSINESS_PIPELINE_GATED_STAGE_NAMES else 0.0
        cur = conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, "
            "lead_role_name, instructions, requires_human_approval, is_primary_deliverable, "
            "min_gate_pass_ratio, checks_requirements_traceability, output_content_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                seq,
                name,
                mode,
                lead_role_name,
                instructions,
                1 if requires_approval else 0,
                1 if is_primary else 0,
                gate_ratio,
                1 if checks_traceability else 0,
                output_content_type,
            ),
        )
        stage_id = cur.lastrowid
        for agent_seq, (role_name, agent_instructions) in enumerate(agents, start=1):
            agent_row = conn.execute(
                "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue  # 방어적 처리 — AGENT_SPECS에 이미 다 있어 정상 경로에선 안 탐.
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, agent_seq, agent_instructions),
            )
