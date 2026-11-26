"""AI에게 일 맡기기 마법사 — 업무를 한 문장으로 적으면 맞는 방법을 골라 준다(2026-11-24, P2-8).

원래 분석(P2-8 화면 복잡도): 탭 27개(지금 29개), 대화·Agent 멘션·팀·Crew 빌더·파이프라인·정기 실행이
서로 겹쳐 보여 "이 일을 어디서 시작해야 하나"를 사용자가 먼저 알아야 했다. 홈 탭에 방법 4가지를 설명해
뒀지만 고르는 것은 여전히 사람 몫이었다.

여기서는 LLM 없이(무료·즉시) 적은 글만 보고 추천한다.
- 정기 실행: "매주·매일·매월·평일·정기·주간 보고" 같은 반복 표지
- 파이프라인: 기본·등록 파이프라인 템플릿의 업무 영역 낱말(재무·영업·계약·채용·개발·제안서·실적·기획)
- Agent: 기존 Agent의 역할·목표·스킬과 겹치는 정도(`pool_catalog.suggest_roles`, 업무 요청과 같은 기준)
- 빠른 질문: 짧은 질문형이거나 다른 추천이 약하면 대화 기본 AI
추천마다 왜 그런지 적고, 실행은 화면이 기존 API를 이어 부른다(대화 만들기·Agent 초대, 업무 요청·할 일·
파이프라인 실행, 정기 실행 설정 화면). 권한은 그 API들이 그대로 확인한다.
"""

from __future__ import annotations

import re
import sqlite3

_RECURRING_RE = re.compile(r"매주|매일|매월|매달|평일마다|정기(?:적으로)?|주간 ?보고|월간 ?보고|주마다|매 ?분기|반복")
_QUESTION_RE = re.compile(r"\?|알려 ?줘|뭐야|무엇|어떻게|왜 |차이|설명해|요약해 ?줘")

# 템플릿 이름의 핵심어 → 그 영역을 가리키는 낱말. 이름에 핵심어가 든 템플릿에 점수를 준다(관리자가 같은 영역의
# 새 템플릿을 만들어도 이름이 맞으면 함께 후보가 된다).
_DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "재무": ("재무", "예산", "집행", "npv", "irr", "투자 타당", "회수기간", "손익", "비용 절감"),
    "영업": ("영업", "고객사", "제안 메일", "후속 메일", "데모", "리드", "구매팀", "세일즈"),
    "계약": ("계약", "조항", "약관", "손해배상", "해지", "갱신", "법무"),
    "채용": ("채용", "공고", "구인", "모집", "면접", "지원자", "인재 채용", "채용 공고"),  # 인사(2026-11-26, P1-9)
    "소프트웨어": ("개발", "구현", "코드", "api", "버그", "앱", "웹사이트", "기능 추가", "리팩터", "테스트 코드"),
    "제안서": ("제안서", "rfp", "입찰", "발주처"),
    "실적": ("실적 보고", "실적보고", "월간 보고", "분기 보고", "kpi", "성과 보고"),
    "사업": ("사업 계획", "기획서", "전략", "시장 분석", "사업성", "신사업"),
}


def _template_scores(conn: sqlite3.Connection, text: str) -> list[tuple[int, dict]]:
    low = text.lower()
    out: list[tuple[int, dict]] = []
    for row in conn.execute("SELECT id, name, description FROM pipeline_templates WHERE is_active = 1"):
        score, hits = 0, []
        for key, words in _DOMAIN_WORDS.items():
            if key not in row["name"]:
                continue
            for w in words:
                if w in low:
                    score += 2 if " " in w or len(w) >= 3 else 1
                    hits.append(w)
        if score:
            out.append((score, {"id": row["id"], "name": row["name"], "hits": hits}))
    out.sort(key=lambda x: (-x[0], x[1]["name"]))
    return out


def recommend(conn: sqlite3.Connection, text: str, role: str) -> dict:
    from hair_platform import pool_catalog

    text = (text or "").strip()
    if not text:
        raise ValueError("empty_text")
    can_manage = role in ("admin", "manager")
    recs: list[dict] = []

    recurring = _RECURRING_RE.search(text)
    if recurring:
        recs.append({
            "kind": "schedule", "score": 9,
            "title": "정기 실행으로 자동화",
            "why": f"'{recurring.group(0)}'처럼 반복되는 일입니다 — 정해진 시각마다 업무 요청 → 할 일 → 파이프라인 "
                   "실행을 자동으로 만들 수 있습니다.",
            "allowed": can_manage,
            "note": None if can_manage else "정기 실행 설정은 admin·manager가 합니다 — 업무 요청으로 먼저 올려 두세요.",
        })

    templates = _template_scores(conn, text)
    if templates:
        score, best = templates[0]
        recs.append({
            "kind": "pipeline", "score": 5 + score,
            "title": f"파이프라인 — {best['name']}",
            "why": f"요청에 '{', '.join(dict.fromkeys(best['hits']))}'가 있어 이 업무 영역의 여러 단계(요구사항 정리 → 작성 "
                   "→ 검토)를 Gate 기준으로 자동 진행하는 템플릿이 맞습니다. 사람은 처음 계획만 승인합니다.",
            "template_id": best["id"], "template_name": best["name"],
            "allowed": True,
            "note": None if can_manage else "업무 요청으로 올리면 manager가 할 일로 나눠 이 파이프라인을 실행합니다.",
        })

    roles = pool_catalog.suggest_roles(conn, text, limit=3)
    for i, role_name in enumerate(roles):
        agent = conn.execute(
            "SELECT id FROM agent_pool WHERE role_name = ? AND status = 'active' ORDER BY id LIMIT 1", (role_name,)
        ).fetchone()
        if agent is None:
            continue
        recs.append({
            "kind": "agent", "score": 6 - i,
            "title": f"@{role_name}에게 맡기기",
            "why": "이 Agent의 역할·스킬이 요청과 가장 잘 맞습니다 — 대화에서 바로 결과를 받고, 필요하면 업무 요청으로 "
                   "전환해 결재를 붙일 수 있습니다.",
            "agent_id": agent["id"], "role_name": role_name, "allowed": True,
        })

    question = len(text) <= 80 and _QUESTION_RE.search(text)
    # 짧은 질문은 빠른 대화가 먼저다 — 영역 낱말이 하나 걸린 정도("KPI 차이가 뭐야?")로 파이프라인을 앞세우지
    # 않는다(파이프라인 점수 5+2=7 < 8). 영역 낱말이 여럿 걸린 본격 업무면 파이프라인이 앞선다.
    recs.append({
        "kind": "chat", "score": 8 if question else 1,
        "title": "대화에서 빠르게 묻기(기본 AI)",
        "why": ("짧은 질문이라 결재 없이 대화에서 바로 답을 받는 것이 가장 빠릅니다." if question else
                "다른 방법이 맞지 않으면 대화 기본 AI로 먼저 시작해 보세요 — 나중에 업무 요청으로 전환할 수 있습니다."),
        "allowed": True,
    })
    recs.sort(key=lambda r: -r["score"])
    for i, r in enumerate(recs):
        r["recommended"] = i == 0
        r.pop("score", None)
    return {"text": text, "recommendations": recs}
