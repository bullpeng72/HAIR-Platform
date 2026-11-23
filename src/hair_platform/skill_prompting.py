"""스킬 활용·결과 품질 프롬프트 — Agent·대화 스킬 선택·Crew 실행이 공통으로 쓴다.

사용자 요청(2026-11-21, "에이전트들이 스킬을 충분히 활용하도록 하고, 스킬 이상의
결과품질을 생성해 내도록 개선"). 실측으로 확인한 문제:

1. **도구 안내가 사실과 달랐다** — `llm.system_prompt_for_role()`·`deliverables.
   skill_grounded_system_prompt()`가 실제 도구가 연결돼 함수 호출 루프로 실행되는
   경우에도 "도구를 호출할 수 없다"(`NO_TOOL_LOOP_GUARD`)를 항상 붙였다. 도구가
   붙은 "데이터분석"류 스킬이 계산 도구를 쓰지 말라는 지시를 같이 받았다.
2. **스킬 지침이 이름 없이 나열됐다** — 어느 지침·출력 형식이 어느 스킬 것인지
   모델이 구분할 수 없었고, 스킬 6~7개 Agent는 출력 형식 여러 개가 뒤섞였다.
3. **조합·품질 원칙이 없었다** — 여러 스킬을 어떻게 조합할지, 스킬이 정한 최소
   기준을 넘어 무엇을 더 해야 하는지 지시가 없었다.

이 모듈은 (a) 요청과 가장 관련 있는 스킬을 앞에 두고 상세 지침은 상위 몇 개만
전부 싣고, (b) 스킬 활용 원칙과 결과 품질 기준을 붙이며, (c) 실제 실행 조건(호출
가능한 도구 / 시스템이 미리 조회한 결과 / 둘 다 없음)에 맞는 도구 안내를 고른다.
"""

from __future__ import annotations

import sqlite3

from hair_platform import pool_catalog, pool_registry

# 상세 지침(출력 형식·예시·제약사항)을 싣는 최대 스킬 수 — 요청과 관련 있는 스킬만
# 상세로 싣고, 관련 없는 스킬과 이 수를 넘는 스킬은 한 줄 요약만 싣는다. 로컬 모델은
# 긴 시스템 프롬프트에서 뒤쪽 지침을 놓치기 쉽고, 출력 형식이 여러 개 섞이면 어느 것도
# 제대로 따르지 않는다.
FULL_DETAIL_LIMIT = 4

SKILL_USAGE_PRINCIPLES = (
    "[스킬 활용 원칙]\n"
    "- 보유 스킬은 이 역할의 작업 방법이다. 요청에 해당하는 스킬은 그 절차·출력 형식·"
    "제약사항을 따른다.\n"
    "- 여러 스킬이 해당하면 요청한 결과물의 종류(발표자료·보고서·기획서·코드 등)에 맞는 "
    "스킬 하나의 출력 형식을 골격으로 삼고, 나머지 스킬은 관점·점검 기준으로 반영한다. "
    "출력 형식을 여러 개 이어 붙이지 않는다.\n"
    "- 요청과 무관한 스킬은 쓰지 않는다. 스킬 예시는 형식 참고용이며 내용을 베끼지 않는다."
)

QUALITY_BAR = (
    "[결과 품질 기준 — 스킬 지침은 최소 기준이다]\n"
    "1. 요청 충족: 요청의 요구를 빠짐없이 다룬다. 요청과 스킬 형식이 충돌하면 요청을 따른다.\n"
    "2. 구체성: 일반론 대신 수치·사례·대상·기한·담당을 쓴다.\n"
    "3. 근거: 주어진 자료·조회 결과·대화에 있는 사실만 단정하고, 추정은 추정이라고 밝힌다. "
    "요청·자료에 없는 연도·날짜·출처·표본 수·담당 부서·목표치를 만들어 넣지 않는다 — 필요하면 "
    "'확인 필요'로 남긴다. 계산한 수치는 식으로 다시 확인한다.\n"
    "4. 전문가 보완: 이 역할의 숙련자라면 덧붙일 내용(전제, 리스크, 대안, 다음 행동)을 "
    "스킬이 요구하지 않아도 필요한 만큼 넣는다.\n"
    "5. 실행 가능성: 결론과 다음 행동을 앞에 두어 독자가 바로 결정·실행할 수 있게 한다.\n"
    "6. 제출 전 점검: 스킬 제약사항 위반, 빠진 섹션, 수치·명칭 불일치를 스스로 확인해 "
    "고친 뒤 결과만 출력한다(점검 과정은 출력하지 않는다)."
)

RETRIEVAL_ONLY_GUIDANCE = (
    "[조회 결과 사용] 이 실행에서는 도구를 직접 호출하지 않는다. 필요한 검색·조회는 "
    "시스템이 미리 수행해 그 결과를 함께 넣었다 — 그 결과를 근거로 쓰고, '검색할 수 "
    "없는 환경'이라는 식의 변명은 넣지 않는다. tool_call이나 함수 호출 형식은 출력하지 "
    "않는다."
)


def _relevance(request: str, name: str, description: str | None) -> int:
    """요청과 겹치는 어간 수 — 스킬 이름과 겹치면 2점을 더 준다(이름이 그 스킬의
    핵심 기능이라 지침 본문의 우연한 겹침보다 강한 신호다)."""
    words = pool_catalog._stems(request)
    if not words:
        return 0
    return len(words & pool_catalog._stems(description or "")) + 2 * len(
        words & pool_catalog._stems(name)
    )


def rank_skills(conn: sqlite3.Connection, skills: list[str], request: str | None) -> list[tuple[str, int]]:
    """(스킬명, 관련도) — 관련도 높은 순, 같으면 원래 순서. 요청이 없으면 원래 순서·0."""
    if not skills:
        return []
    rows = {
        r["name"]: r["description"]
        for r in conn.execute(
            f"SELECT name, description FROM skill_pool WHERE name IN ({','.join('?' * len(skills))})",
            skills,
        )
    }
    scored = [
        (name, _relevance(request or "", name, rows.get(name)), i)
        for i, name in enumerate(skills)
        if name in rows
    ]
    scored.sort(key=lambda x: (-x[1], x[2]))
    return [(name, score) for name, score, _ in scored]


def knowledge_block(
    conn: sqlite3.Connection, skills: list[str], request: str | None = None
) -> str:
    """스킬 지식 — 요청과 관련 있는 스킬을 앞에 두고, 관련 스킬이 많으면 상위
    `FULL_DETAIL_LIMIT`개만 상세(지침·출력 형식·예시·제약사항, 함께 적용 스킬 포함)
    로, 나머지는 '[이름] 지침' 한 줄로 싣는다. 요청과 겹치는 스킬이 없으면(판단
    근거 없음) 전부 상세로 싣는다(기존 동작)."""
    ranked = rank_skills(conn, skills, request)
    if not ranked:
        return ""
    score_of = dict(ranked)
    has_signal = ranked[0][1] > 0
    # "주 스킬"은 분명히 앞설 때만 표시한다(2026-11-21 실측) — 단어 겹침은 거친 신호라,
    # 프레젠테이션 Agent에서 덱 스킬(프레젠테이션작성, 겹침 1)보다 개요 스킬(발표자료구성,
    # 겹침 4)이 "주 스킬"로 표시돼 모델이 덱 디자인 시스템을 버리고 자체 스타일을 썼다.
    # 1위가 2점 이상이고 2위의 2배 이상일 때만 주 스킬로 표시하고 그 순서로 싣는다.
    # 아니면 admin이 정한 스킬 순서를 그대로 두고, 어느 스킬의 형식을 쓸지는 모델이
    # "요청한 결과물의 종류"로 고른다(`SKILL_USAGE_PRINCIPLES`).
    top = ranked[0][1]
    second = ranked[1][1] if len(ranked) > 1 else 0
    clear_primary = top >= 2 and top >= 2 * second
    names = [n for n, _ in ranked] if clear_primary else [n for n in skills if n in score_of]
    if not has_signal:
        full, brief = names, []
    else:
        # 관련도로 가른다(2026-11-21, 5회 반복 A/B 결과 반영) — 처음엔 "상위 3개"로
        # 잘랐더니, 스킬 4개 Agent에서 관련도 3위인 보조 스킬(사업성분석)이 한 줄
        # 요약으로 밀려 손익분기·계산식이 줄었다. 요청과 조금이라도 겹치는 스킬은
        # 상세로(최대 FULL_DETAIL_LIMIT개), 전혀 겹치지 않는 스킬만 요약으로 싣는다.
        relevant = [n for n in names if score_of[n] > 0]
        full = relevant[:FULL_DETAIL_LIMIT]
        brief = [n for n in names if n not in full]
    parts: list[str] = []
    if clear_primary:
        parts.append(f"이번 요청의 주 스킬(판단 참고): {names[0]}")
    detail = pool_registry.skill_knowledge_text(conn, full)
    if detail:
        parts.append(detail)
    if brief:
        rows = {
            r["name"]: r["description"]
            for r in conn.execute(
                f"SELECT name, description FROM skill_pool WHERE name IN ({','.join('?' * len(brief))})",
                brief,
            )
        }
        parts.append("그 밖의 보유 스킬(요청에 해당할 때만 적용):")
        parts += [f"- [{n}] {rows[n]}" for n in brief if rows.get(n)]
    return "\n".join(parts)


def tool_guidance(callable_tools: list | None, has_retrieval: bool) -> str:
    """실제 실행 조건에 맞는 도구 안내."""
    if callable_tools:
        from hair_platform import tool_calling

        lines = []
        for t in callable_tools:
            try:
                desc = tool_calling.tool_schema_openai(t)["function"]["description"]
            except Exception:  # noqa: BLE001 — 설명을 못 읽어도 이름은 알린다
                desc = getattr(t, "description", "") or ""
            name = getattr(t, "name", "도구")
            # 400자(2026-11-22) — 설명 끝에 붙인 입력 형식(`reference_pipeline_seeds.
            # TOOL_INPUT_NOTES`)이 120자에서 잘려 나가지 않게 한다.
            lines.append(f"- {name}: {' '.join(str(desc).split())[:400]}")
        # 마지막 두 문장(2026-11-22 실측) — 스펙 작성자가 린터·실패모드 결과만 내고 정작
        # 요청한 Spec 본문을 빼먹었고, 실적 보고서 작성자는 차트를 만들고도 답에 넣지 않았다.
        text = (
            "[도구] 이 실행에서 실제로 호출할 수 있는 도구:\n" + "\n".join(lines) + "\n"
            "계산·조회가 필요한 부분은 추측하지 말고 도구를 호출해 그 결과를 근거로 쓴다. "
            "도구 결과의 수치는 그대로 옮긴다. 위 목록에 없는 도구는 호출하지 않는다. "
            "도구는 점검·계산 수단일 뿐이다 — 요청한 산출물 본문(문서·Spec·코드 등)은 "
            "도구에 넘긴 내용까지 포함해 최종 답에 전부 쓰고, 도구가 만든 차트 설정은 "
            "```chart 코드펜스로 답에 넣는다."
        )
        if has_retrieval:
            text += " 시스템이 미리 조회해 넣은 결과도 함께 근거로 쓴다."
        return text
    if has_retrieval:
        return RETRIEVAL_ONLY_GUIDANCE
    return pool_registry.NO_TOOL_LOOP_GUARD


def compose(
    base: str,
    conn: sqlite3.Connection,
    skills: list[str],
    *,
    request: str | None = None,
    callable_tools: list | None = None,
    has_retrieval: bool = False,
    knowledge_label: str = "참고 지식",
) -> str:
    """역할 소개(base) + 스킬 지식 + 스킬 활용 원칙 + 결과 품질 기준 + 도구 안내 + 결과물 언어."""
    from hair_platform import org_policy

    language = org_policy.output_language_instruction()
    knowledge = knowledge_block(conn, skills, request)
    if not knowledge:
        return f"{base}\n\n{language}" if language else base
    composed = (
        f"{base}\n\n{knowledge_label}({pool_registry.SKILL_KNOWLEDGE_GUIDANCE})\n{knowledge}"
        f"\n\n{SKILL_USAGE_PRINCIPLES}\n\n{QUALITY_BAR}"
        f"\n\n{tool_guidance(callable_tools, has_retrieval)}"
    )
    return f"{composed}\n\n{language}" if language else composed


# ---------------------------------------------------------------- 자체 검토(선택)

def self_review_system(role: str, knowledge: str) -> str:
    return (
        f"당신은 '{role}' 역할 산출물의 최종 검토·개선 담당이다. 요청과 아래 기준에 비추어 "
        "초안을 점검하고, 고친 **최종본 전체만** 출력한다. 검토 의견·변경 내역·머리말은 "
        "쓰지 않는다. 초안의 사실·수치·출처는 근거 없이 바꾸거나 새로 지어내지 않는다. "
        "초안의 형식(마크다운·HTML·코드)을 유지하고, 이미 좋은 부분은 그대로 둔다.\n\n"
        f"[스킬 기준]\n{knowledge}\n\n{QUALITY_BAR}"
    )


# 근거 자료 상한 — 검토 호출 프롬프트가 초안보다 커지지 않게 한다.
SELF_REVIEW_EVIDENCE_MAX_CHARS = 6000


def self_review_prompt(request: str, draft: str, evidence: str | None = None) -> str:
    """수치 점검(2026-11-22, "스킬, 툴, 에이전트 각각의 실행 품질을 점검해줘" P2) — 실측에서
    숫자 자체는 도구 결과대로 맞았는데 라벨·단위·서술이 틀렸다("4분기 대비 1분기" 방향 뒤집힘,
    "9,098억 달러(90.98억 달러)" 단위 환산, "11.3%p"를 비율에 씀, 증가액이 줄었다고 쓰고 바로
    "줄지 않았다"고 쓴 자기모순). 검토자가 초안만 보면 대조할 근거가 없으므로, 실행 중 받은
    도구 결과·조회 결과를 함께 넘기고 이 항목을 첫 점검 대상으로 둔다."""
    evidence_block = ""
    if evidence and evidence.strip():
        evidence_block = (
            "[근거 자료 — 실행 중 받은 도구 결과·조회 결과]\n"
            f"{evidence.strip()[:SELF_REVIEW_EVIDENCE_MAX_CHARS]}\n\n"
        )
    return (
        f"[요청]\n{request}\n\n{evidence_block}[초안]\n{draft}\n\n"
        "점검할 것: ① 수치·단위·기간 라벨·비교 방향(무엇 대비 무엇)이 요청과 근거 자료에 "
        "맞는지, 표와 서술이 서로 모순되지 않는지, %와 %p를 바르게 썼는지 — 틀린 곳만 "
        "근거대로 고치고 근거에 없는 숫자는 새로 만들지 않는다. ② 요청 중 빠진 요구, 스킬 "
        "출력 형식·제약사항 위반, 근거 없는 단정, 일반론으로 남은 부분(수치·사례·담당·기한으로 "
        "구체화), 숙련자라면 덧붙일 내용(리스크·대안·다음 행동). 고친 최종본 전체를 출력하라."
    )


def accept_review(draft: str, revised: str | None) -> bool:
    """검토본을 받을지 — 비었거나, 초안보다 크게 짧거나(60% 미만 — 내용을 잘라
    먹은 경우), 머리말만 있는 경우는 받지 않는다."""
    if not revised or not revised.strip():
        return False
    return len(revised.strip()) >= 0.6 * len(draft.strip())
