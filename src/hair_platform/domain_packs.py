"""업무 영역 템플릿 — 재무·영업/고객지원·법무(2026-11-24, "P2 진행해줘" — P2-12, 사용자 선택 3개 영역).

기본 제공 구성은 SW 개발·사업/기획·제안서·실적 보고뿐이라, 다른 부서는 스킬·Agent·파이프라인을
처음부터 만들어야 했다. 영역마다 아래를 기본으로 넣는다:

- **도구**(계산·점검을 모델 대신 코드로): 재무 지표 계산기·영업 퍼널 계산기·계약 조항 점검기.
  수치 근거 검사(`numeric_check`)가 잡던 "모델이 직접 계산한 틀린 % / NPV"를 애초에 줄인다.
- **스킬**(지침·출력 형식·예시·제약 — `skill_catalog`가 이 모듈의 `SKILL_GUIDES`를 합쳐 쓴다).
- **Agent**와 **파이프라인** 1개씩(요구사항 승인 1회 → 분석 → 작성 → 검증, 검증 실패 시 작성으로 되돌림).
  고객지원 상담원·규정 요약가는 대화에서 @로 부르는 용도다.

시드 규칙은 `reference_pipeline_seeds.seed()`와 같다 — 없을 때만 만들고, admin이 고친 내용은 덮어쓰지
않는다. 기존 DB에도 다음 기동 때 그대로 추가된다(검증 단계 Gate·임계·되돌림은 2026-11-21~22 일회성
마이그레이션이 정한 현재 기본값으로 바로 넣는다 — 그 마이그레이션은 기존 DB에서 이미 끝났다)."""

from __future__ import annotations

import json
import sqlite3

# ---------------------------------------------------------------------------------------- 도구

FINANCE_CALC_SOURCE = '''def tool_fn(query: str) -> str:
    import json

    try:
        p = json.loads(query)
    except (json.JSONDecodeError, ValueError):
        return '입력은 JSON이어야 합니다: {"budget": {...}, "actual": {...}} 또는 {"cash_flows": [...], "rate": 0.08}'
    if not isinstance(p, dict):
        return "입력은 JSON 객체여야 합니다."
    out = {}
    budget, actual = p.get("budget"), p.get("actual")
    if isinstance(budget, dict) and isinstance(actual, dict):
        rows, tb, ta = [], 0.0, 0.0
        for item in list(budget) + [k for k in actual if k not in budget]:
            b, a = budget.get(item), actual.get(item)
            if not isinstance(b, (int, float)) or not isinstance(a, (int, float)):
                rows.append({"항목": item, "예산": b, "실적": a, "비고": "한쪽 값 없음 — 확인 필요"})
                continue
            tb += b
            ta += a
            diff = a - b
            rows.append({
                "항목": item, "예산": b, "실적": a, "차이(실적-예산)": round(diff, 4),
                "차이율(%)": round(diff / b * 100, 2) if b else None,
                "집행률(%)": round(a / b * 100, 2) if b else None,
            })
        out["예산_대비_실적"] = rows
        out["합계"] = {
            "예산": round(tb, 4), "실적": round(ta, 4), "차이": round(ta - tb, 4),
            "차이율(%)": round((ta - tb) / tb * 100, 2) if tb else None,
        }
    flows = p.get("cash_flows")
    if isinstance(flows, list) and flows and all(isinstance(x, (int, float)) for x in flows):
        rate = p.get("rate")

        def npv(r):
            return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))

        result = {"현금흐름(0기부터)": flows}
        if isinstance(rate, (int, float)):
            result["할인율"] = rate
            result["NPV"] = round(npv(rate), 4)
        lo, hi = -0.9999, 10.0
        irr = None
        if npv(lo) * npv(hi) < 0:
            for _ in range(200):
                mid = (lo + hi) / 2
                if npv(lo) * npv(mid) <= 0:
                    hi = mid
                else:
                    lo = mid
            irr = (lo + hi) / 2
        result["IRR(%)"] = round(irr * 100, 2) if irr is not None else "계산 불가(부호 변화 없음)"
        cum, payback = 0.0, None
        for t, cf in enumerate(flows):
            prev = cum
            cum += cf
            if t > 0 and prev < 0 <= cum and cf:
                payback = round(t - 1 + (-prev) / cf, 2)
                break
        result["단순 회수기간(기)"] = payback if payback is not None else "기간 내 회수 안 됨"
        out["투자_타당성"] = result
    if not out:
        return '계산할 값이 없습니다 — {"budget": {...}, "actual": {...}} 또는 {"cash_flows": [...], "rate": ...}'
    return json.dumps(out, ensure_ascii=False)
'''

SALES_FUNNEL_SOURCE = '''def tool_fn(query: str) -> str:
    import json

    try:
        p = json.loads(query)
    except (json.JSONDecodeError, ValueError):
        return '입력은 JSON이어야 합니다: {"stages": {"리드": 1200, "상담": 300, "계약": 30}} 또는 {"deals": [...]}'
    if not isinstance(p, dict):
        return "입력은 JSON 객체여야 합니다."
    out = {}
    stages = p.get("stages")
    if isinstance(stages, dict) and len(stages) >= 2:
        names = list(stages)
        counts = [stages[n] for n in names]
        if all(isinstance(c, (int, float)) for c in counts):
            steps, worst = [], None
            for i in range(1, len(names)):
                prev, cur = counts[i - 1], counts[i]
                rate = round(cur / prev * 100, 2) if prev else None
                steps.append({"구간": names[i - 1] + " → " + names[i], "전환율(%)": rate, "이탈 수": prev - cur})
                if rate is not None and (worst is None or rate < worst["전환율(%)"]):
                    worst = steps[-1]
            out["단계별_전환"] = steps
            out["전체_전환율(%)"] = round(counts[-1] / counts[0] * 100, 2) if counts[0] else None
            out["가장_낮은_전환_구간"] = worst["구간"] if worst else None
            deal = p.get("deal_value")
            if isinstance(deal, (int, float)):
                out["예상_매출(마지막 단계 수 × 건당 금액)"] = round(counts[-1] * deal, 4)
    deals = p.get("deals")
    if isinstance(deals, list) and deals:
        rows, total, weighted = [], 0.0, 0.0
        for d in deals:
            if not isinstance(d, dict):
                continue
            amt, prob = d.get("amount"), d.get("probability")
            if not isinstance(amt, (int, float)) or not isinstance(prob, (int, float)):
                rows.append({"건": d.get("name"), "비고": "금액·확률 확인 필요"})
                continue
            pr = prob / 100 if prob > 1 else prob
            total += amt
            weighted += amt * pr
            rows.append({"건": d.get("name"), "금액": amt, "확률(%)": round(pr * 100, 2), "가중 금액": round(amt * pr, 4)})
        out["영업_기회"] = rows
        out["기회_합계"] = round(total, 4)
        out["가중_파이프라인_금액"] = round(weighted, 4)
    if not out:
        return '계산할 값이 없습니다 — {"stages": {...}} (2단계 이상) 또는 {"deals": [{"name", "amount", "probability"}]}'
    return json.dumps(out, ensure_ascii=False)
'''

CONTRACT_CHECK_SOURCE = '''def tool_fn(query: str) -> str:
    import json, re

    text = query
    try:
        p = json.loads(query)
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            text = p["text"]
    except (json.JSONDecodeError, ValueError):
        pass
    if not text.strip():
        return "계약서 본문이 비어 있습니다."
    clauses = {
        "목적·업무 범위": ["목적", "업무 범위", "업무범위", "과업 범위"],
        "계약 기간": ["계약 기간", "계약기간", "유효기간", "존속기간"],
        "대금·지급 조건": ["대금", "지급", "수수료", "보수", "대가"],
        "해지·해제": ["해지", "해제"],
        "손해배상": ["손해배상", "배상"],
        "책임 제한": ["책임 제한", "책임의 한도", "책임한도", "간접손해", "책임을 지지 않"],
        "비밀유지": ["비밀유지", "비밀 유지", "기밀"],
        "지식재산권": ["지식재산", "저작권", "특허", "산출물의 소유"],
        "개인정보 보호": ["개인정보"],
        "불가항력": ["불가항력"],
        "권리·의무 양도 제한": ["양도"],
        "분쟁 해결·관할": ["관할", "중재", "분쟁"],
        "준거법": ["준거법"],
    }
    risks = [
        (r"일방적으로", "한쪽만 변경·해지할 수 있는 조항 — 상호 조건인지 확인"),
        (r"자동\\s*(갱신|연장)|자동으로\\s*(갱신|연장)", "자동 갱신 — 갱신 거절 통지 기한 확인"),
        (r"무제한|제한\\s*없이", "책임·의무 범위 무제한 — 상한 설정 검토"),
        (r"어떠한\\s*경우에도", "포괄적 면책·책임 — 적용 범위 확인"),
        (r"사전\\s*통지\\s*없이|통지\\s*없이", "통지 없는 조치 — 통지 절차 추가 검토"),
        (r"즉시\\s*해지", "즉시 해지 사유 — 사유가 구체적인지 확인"),
        (r"위약벌", "위약벌 — 손해배상과 별도 청구 가능성 확인"),
        (r"독점|경업", "독점·경업 금지 — 기간·지역·범위 확인"),
    ]
    present, missing = [], []
    for name, words in clauses.items():
        (present if any(w in text for w in words) else missing).append(name)
    flagged = []
    for line in [ln.strip() for ln in text.splitlines() if ln.strip()]:
        for pattern, reason in risks:
            if re.search(pattern, line):
                flagged.append({"문구": line[:160], "확인 사항": reason})
    return json.dumps({
        "확인된_조항": present,
        "찾지_못한_조항": missing,
        "주의_문구": flagged[:30],
        "안내": "키워드 기반 1차 점검이다 — 조항이 있어도 내용이 불리할 수 있고, 다른 표현으로 쓰인 조항은 '찾지 못함'으로 나올 수 있다. 법률 자문이 아니다.",
    }, ensure_ascii=False)
'''

TOOL_SPECS: list[tuple[str, str, str, int]] = [
    (
        "재무 지표 계산기",
        "예산 대비 실적 차이(항목별 차이·차이율·집행률·합계)와 투자안의 NPV·IRR·단순 회수기간을 계산한다. "
        '입력(JSON): {"budget": {"항목": 금액}, "actual": {"항목": 금액}} 또는 {"cash_flows": [0기, 1기, ...], "rate": 0.08} — 둘 다 넣어도 된다.',
        FINANCE_CALC_SOURCE,
        10,
    ),
    (
        "영업 퍼널 계산기",
        "영업 단계별 전환율·이탈 수·전체 전환율·가장 약한 구간, 영업 기회의 가중 파이프라인 금액을 계산한다. "
        '입력(JSON): {"stages": {"리드": 1200, "상담": 300, "계약": 30}, "deal_value": 건당금액} 또는 {"deals": [{"name", "amount", "probability"}]}.',
        SALES_FUNNEL_SOURCE,
        10,
    ),
    (
        "계약 조항 점검기",
        "계약서 본문에서 표준 조항(범위·기간·대금·해지·손해배상·책임 제한·비밀유지·지식재산·개인정보·불가항력·양도·분쟁·준거법)의 "
        "유무와 주의 문구(일방적 변경, 자동 갱신, 무제한 책임 등)를 키워드로 1차 점검한다. 입력: 계약서 본문 텍스트.",
        CONTRACT_CHECK_SOURCE,
        10,
    ),
]

TOOL_INPUT_SCHEMAS: dict[str, dict] = {
    "재무 지표 계산기": {
        "type": "object",
        "description": "예산 대비 실적(budget+actual) 또는 투자 타당성(cash_flows+rate) 중 하나 이상.",
        "properties": {
            "budget": {"type": "object", "additionalProperties": {"type": "number"}, "description": '항목 → 예산 금액(단위 없이). 예: {"인건비": 120, "마케팅": 40}'},
            "actual": {"type": "object", "additionalProperties": {"type": "number"}, "description": "항목 → 실적 금액(budget과 같은 항목명·단위)."},
            "cash_flows": {"type": "array", "items": {"type": "number"}, "description": "0기(초기 투자, 보통 음수)부터 기간별 현금흐름."},
            "rate": {"type": "number", "description": "할인율(소수). 예: 8% → 0.08"},
        },
    },
    "영업 퍼널 계산기": {
        "type": "object",
        "description": "퍼널 단계 수(stages) 또는 영업 기회 목록(deals) 중 하나 이상.",
        "properties": {
            "stages": {"type": "object", "additionalProperties": {"type": "number"}, "description": '단계 이름 → 건수, 퍼널 순서대로. 예: {"리드": 1200, "상담": 300, "계약": 30}'},
            "deal_value": {"type": "number", "description": "건당 평균 금액(선택)."},
            "deals": {
                "type": "array",
                "description": "영업 기회 목록.",
                "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "amount": {"type": "number"},
                    "probability": {"type": "number", "description": "성사 확률(0~1 또는 0~100)."},
                }},
            },
        },
    },
    "계약 조항 점검기": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "계약서 본문 전체."}},
        "required": ["text"],
    },
}

# -------------------------------------------------------------------------------------- 스킬
# (지침, 출력 형식, 예시, 제약) — `skill_catalog.SKILL_GUIDES`에 합쳐진다. "tool"은 연결 도구.

_STYLE = "평서체(~한다·~이다)로 쓰고 결론을 먼저 둔다. 상투어·과장·번역투를 피한다."
_NO_INVENT = "주어진 자료·도구 결과에 없는 수치·사실을 지어내지 않는다 — 없으면 '확인 필요'로 남긴다."
_NOT_LEGAL_ADVICE = "결과물 끝에 '본 검토는 1차 검토이며 법률 자문이 아니다 — 체결 전 법무 담당 확인 필요'를 남긴다."

SKILL_SPECS: dict[str, dict] = {
    # ----------------------------------------------------------------------------- 재무
    "예산실적차이분석": {
        "tool": "재무 지표 계산기",
        "guide": (
            "예산 대비 실적을 항목별로 비교해 차이와 원인을 분석한다: 항목별 예산·실적을 '재무 지표 계산기'로 "
            "계산(차이·차이율·집행률) → 차이가 큰 항목 순으로 원인(물량·단가·일정·일회성) 정리 → 조치 제안. "
            "손익 예측이나 투자 판단은 하지 않는다(투자타당성검토).",
            "'## 요약'(총 집행률·총 차이, 주의 항목 2~3개) → '## 항목별 예산 대비 실적'(표: 항목 | 예산 | 실적 | 차이 | "
            "차이율 | 집행률 — 계산기 결과 그대로) → '## 주요 차이 원인'(항목별: 원인 | 근거 | 일회성 여부) → '## 조치 제안'.",
            "나쁜 예 '마케팅비가 많이 초과됐다' → 좋은 예 '마케팅비 실적 52 vs 예산 40, 차이 +12(+30.0%) — 3분기 "
            "신제품 캠페인(일회성) 집행 때문. 4분기 예산 재배정 필요'.",
            "① 모든 차이·비율은 '재무 지표 계산기' 결과를 그대로 쓴다 — 직접 암산한 수치를 쓰지 않는다. ② 원인은 "
            "자료에 근거가 있을 때만 단정하고, 없으면 '추정'이라고 밝힌다. ③ " + _NO_INVENT + " ④ " + _STYLE,
        ),
    },
    "투자타당성검토": {
        "tool": "재무 지표 계산기",
        "guide": (
            "투자·사업안의 재무적 타당성을 검토한다: 현금흐름과 할인율을 '재무 지표 계산기'에 넣어 NPV·IRR·회수기간을 "
            "계산 → 핵심 가정(매출·비용·할인율) 정리 → 가정이 ±10~20% 바뀔 때의 민감도(계산기를 다시 실행) → "
            "투자/보류/조건부 의견. 예산 집행 분석은 하지 않는다(예산실적차이분석).",
            "'## 결론'(투자 / 보류 / 조건부 + 한 줄 이유) → '## 재무 지표'(표: NPV | IRR | 회수기간 | 할인율) → "
            "'## 핵심 가정'(표: 가정 | 값 | 근거) → '## 민감도'(표: 바꾼 가정 | NPV | IRR) → '## 리스크와 조건'.",
            "나쁜 예 '수익성이 좋아 보인다' → 좋은 예 'NPV +1.8억(할인율 8%), IRR 14.2%, 회수 3.4년 — 매출이 15% "
            "줄면 NPV -0.3억으로 음수 전환. 1년 차 매출 검증 후 2차 투자 조건부 승인'.",
            "① NPV·IRR·회수기간과 민감도 수치는 모두 계산기 결과를 쓴다. ② 현금흐름·할인율이 자료에 없으면 계산하지 "
            "말고 필요한 값을 질문으로 남긴다. ③ 가정과 사실을 구분해 표시한다. ④ " + _STYLE,
        ),
    },
    "비용절감검토": {
        "guide": (
            "비용 구조를 보고 절감 기회를 찾는다: 비용 항목을 규모순으로 정리 → 항목마다 절감 수단(계약 재협상·통합·"
            "자동화·중단·내재화) 검토 → 예상 절감액(근거가 있는 경우만)·실행 난이도·부작용 평가 → 우선순위 제안.",
            "'## 요약'(우선 추진 3건과 예상 절감액 범위) → '## 비용 구조'(표: 항목 | 금액 | 비중) → '## 절감 기회'(표: "
            "항목 | 수단 | 예상 절감 | 난이도(상/중/하) | 부작용·리스크) → '## 실행 순서'.",
            "나쁜 예 '불필요한 비용을 줄인다' → 좋은 예 'SaaS 구독 14종 중 기능 중복 4종 통합 — 연 2,400만 원 → "
            "1,500만 원(근거: 구독 목록 금액), 난이도 하, 리스크: 부서별 전환 교육 필요'.",
            "① 절감액은 자료의 금액으로 계산할 수 있을 때만 쓰고 계산식을 남긴다 — 근거 없으면 '규모 확인 필요'. "
            "② 품질·안전·법적 의무에 영향이 있는 절감은 리스크를 반드시 적는다. ③ 인력 감축을 기본 수단으로 제안하지 "
            "않는다. ④ " + _STYLE,
        ),
    },
    # ----------------------------------------------------------------------- 영업/고객지원
    "영업제안메일작성": {
        "guide": (
            "고객에게 보낼 영업 제안 메일을 쓴다: 고객 상황·니즈 확인 → 그 니즈에 맞는 제안 1~3개(효과는 근거 수치로) → "
            "다음 단계(미팅·데모·견적) 요청 순. 제안서 문서 전체를 쓰는 일이 아니다(제안서작성).",
            "'제목:'(30자 이내, 고객 이득 중심) → 본문: 인사·맥락(1~2문장) → 고객 상황 요약 → 제안(번호 목록, 각 1~2문장) → "
            "근거(사례·수치) → 다음 단계 요청(구체적 일시·방식 2안) → 서명. 본문 250~400자 내외. 필요하면 '## 후속 메일 "
            "(1주 뒤)' 짧은 초안을 덧붙인다.",
            "나쁜 예 '저희 솔루션은 업계 최고입니다' → 좋은 예 '지난 통화에서 말씀하신 월말 정산 지연(평균 3일)은 자동 "
            "대사 기능으로 같은 규모 고객사에서 1일 이내로 줄었습니다. 다음 주 화·목 오후 중 30분 데모가 가능하실까요?'",
            "① 고객 정보·사례·수치는 주어진 자료에 있는 것만 쓴다 — 없는 레퍼런스 고객·할인율을 만들지 않는다. "
            "② 가격·계약 조건 확약은 하지 않는다(자료에 승인된 조건이 있을 때만). ③ 과장·최상급 표현을 쓰지 않는다. "
            "④ 존댓말(하십시오체/해요체 중 요청에 맞게)로 쓴다.",
        ),
    },
    "고객문의응대": {
        "guide": (
            "고객 문의·불만에 대한 답변 초안을 쓴다: 문의 유형 분류(사용법·장애·결제·환불·불만) → 지식베이스·정책에서 "
            "근거 확인 → 공감 한 문장 → 해결 방법(단계별) 또는 처리 일정 → 추가 문의 안내. 정책에 없는 보상·예외는 "
            "약속하지 않고 담당자 확인으로 넘긴다.",
            "'## 분류'(유형 | 긴급도 | 담당 이관 필요 여부) → '## 답변 초안'(고객에게 그대로 보낼 문장) → '## 내부 메모'"
            "(근거 정책·확인 필요 사항, 고객에게 보내지 않음).",
            "나쁜 예 '불편을 드려 죄송합니다. 확인 후 연락드리겠습니다.' → 좋은 예 '결제가 두 번 청구되어 많이 "
            "당황하셨을 것 같습니다. 중복 결제 1건(5월 3일, 29,000원)은 오늘 취소 요청했으며 카드사에 따라 3~5영업일 안에 "
            "환불됩니다.'",
            "① 환불·보상·일정은 정책 문서나 주어진 정보에 근거가 있을 때만 약속한다 — 없으면 내부 메모에 '담당자 확인' "
            "으로 남기고 답변에는 확인 후 안내한다고 쓴다. ② 고객을 탓하는 표현을 쓰지 않는다. ③ 개인정보(전체 카드번호·"
            "주민번호 등)를 답변에 쓰지 않는다. ④ 해요체로 짧고 분명하게 쓴다.",
        ),
    },
    "영업실적분석": {
        "tool": "영업 퍼널 계산기",
        "guide": (
            "영업 실적과 파이프라인을 분석한다: 단계별 건수·영업 기회를 '영업 퍼널 계산기'로 계산(전환율·이탈·가중 "
            "파이프라인 금액) → 가장 약한 구간과 원인 → 목표 대비 부족분을 메울 행동 제안. 매출 실적 보고서 전체를 "
            "쓰는 일은 아니다(실적서작성).",
            "'## 요약'(전체 전환율, 가장 약한 구간, 가중 파이프라인 금액) → '## 퍼널'(표: 구간 | 전환율 | 이탈 수 — "
            "계산기 결과 그대로) → '## 영업 기회'(표: 건 | 금액 | 확률 | 가중 금액) → '## 원인과 조치'(구간별).",
            "나쁜 예 '전환율이 낮다' → 좋은 예 '상담→제안 전환 40.0%(300→120)로 가장 낮음 — 제안서 작성 대기 평균 "
            "9일(자료). 템플릿화로 대기 3일 이내 목표'.",
            "① 전환율·금액은 모두 계산기 결과를 쓴다. ② 원인은 자료에 근거가 있을 때만 단정하고 아니면 '가설'로 "
            "표시한다. ③ " + _NO_INVENT + " ④ " + _STYLE,
        ),
    },
    # ------------------------------------------------------------------------------ 법무
    "계약서검토": {
        "tool": "계약 조항 점검기",
        "guide": (
            "계약서를 우리 측 입장에서 1차 검토한다: '계약 조항 점검기'로 표준 조항 유무·주의 문구를 먼저 확인 → "
            "조항별로 우리 측에 불리한 점·모호한 점 검토 → 위험도(상/중/하)와 수정 문구 제안 → 협상 우선순위. "
            "최종 법률 판단을 대신하지 않는다.",
            "'## 검토 요약'(계약 유형·우리 측 지위, 위험도 상 건수, 체결 전 꼭 고칠 3가지) → '## 조항별 검토'(표: 조항 | "
            "현재 문구(요약) | 문제점 | 위험도 | 수정 제안) → '## 빠진 조항'(점검기 결과 + 필요성) → '## 협상 우선순위' "
            "→ 면책 문장.",
            "나쁜 예 '손해배상 조항이 불리함' → 좋은 예 '제12조 손해배상 — 을의 배상 범위에 상한 없음(\"모든 손해\") | "
            "위험도 상 | 수정: \"직접 손해에 한하며, 총액은 직전 12개월 계약 대금을 한도로 한다\" 추가'.",
            "① 계약서에 실제로 있는 문구만 인용한다 — 조항 번호·문구를 지어내지 않는다. ② 점검기의 '찾지 못함'은 다른 "
            "표현으로 있을 수 있으니 본문을 직접 확인한 뒤 '빠진 조항'으로 단정한다. ③ 특정 법령 조문을 인용할 때는 "
            "자료에 근거가 있을 때만. ④ " + _NOT_LEGAL_ADVICE,
        ),
    },
    "규정요약": {
        "guide": (
            "사내 규정·정책·법령 문서를 실무자가 바로 쓸 수 있게 요약한다: 적용 대상·범위 → 꼭 지켜야 할 의무(해야 할 "
            "것·하면 안 되는 것) → 절차·기한 → 예외·위반 시 조치 순. 문서에 없는 해석을 더하지 않는다.",
            "'## 한 줄 요약' → '## 적용 대상' → '## 해야 할 것 / 하면 안 되는 것'(표: 구분 | 내용 | 근거 조항) → "
            "'## 절차와 기한'(번호 목록) → '## 예외·위반 시 조치' → '## 자주 묻는 질문'(3~5개, 문서 근거로 답).",
            "나쁜 예 '출장비는 규정에 따라 처리한다' → 좋은 예 '국내 출장 숙박비 상한 1박 10만 원(제7조 2항), 초과분은 "
            "부서장 사전 승인 시에만 정산 가능(제7조 4항)'.",
            "① 모든 의무·기한·금액에 근거 조항(조·항)을 붙인다. ② 문서가 모호한 부분은 해석하지 말고 '해석 필요 — "
            "담당 부서 확인'으로 남긴다. ③ 개정일·시행일이 있으면 밝힌다. ④ " + _STYLE,
        ),
    },
    "개인정보처리점검": {
        "guide": (
            "업무 절차·서비스·문서의 개인정보 처리를 점검한다: 수집 항목과 목적 → 수집 근거(동의 등) → 보관 기간·파기 → "
            "제3자 제공·처리 위탁 → 안전조치(접근 권한·암호화) → 정보주체 권리 대응 순으로 확인하고 미비점과 보완안을 낸다.",
            "'## 점검 요약'(미비 건수, 즉시 보완 필요 항목) → '## 처리 현황'(표: 단계 | 항목 | 현재 상태 | 근거 자료) → "
            "'## 미비점과 보완안'(표: 항목 | 문제 | 위험도 | 보완 방법) → '## 확인 필요'.",
            "나쁜 예 '개인정보 보호가 필요함' → 좋은 예 '회원가입 시 생년월일 수집 — 이용 목적(연령 확인)에 비해 과다, "
            "\"만 14세 이상 여부\" 체크로 대체 | 위험도 중'.",
            "① 자료에서 확인한 처리 현황만 적는다 — 모르는 항목은 '확인 필요'. ② 법령 조문 번호는 자료에 근거가 있을 "
            "때만 인용한다. ③ 실제 개인정보 값을 결과물에 옮기지 않는다. ④ " + _NOT_LEGAL_ADVICE,
        ),
    },
}

# ------------------------------------------------------------------------------------- Agent

AGENT_SPECS: list[tuple[str, list[str], dict]] = [
    ("재무 분석가", ["예산실적차이분석", "투자타당성검토", "비용절감검토", "데이터시각화"], {"temperature": 0.2}),
    ("재무 검토자", ["문서품질검토", "투자타당성검토"], {"temperature": 0.2}),
    ("영업 분석가", ["영업실적분석", "데이터분석", "데이터시각화"], {"temperature": 0.2}),
    ("영업 제안 작성자", ["영업제안메일작성", "문서작성"], {"temperature": 0.5}),
    ("고객지원 상담원", ["고객문의응대", "내부지식검색"], {"temperature": 0.3}),
    ("계약서 검토자", ["계약서검토", "개인정보처리점검"], {"temperature": 0.2}),
    ("규정 요약가", ["규정요약", "내부지식검색"], {"temperature": 0.2}),
]

# --------------------------------------------------------------------------------- 파이프라인
# 단계: name, role, instructions, approval(요구사항 승인), primary(최종 결과물), review(요구사항 추적성 검증 —
# Gate A+추적성 둘 다 통과, 실패 시 primary 단계로 되돌림).

_REQUIREMENTS = "업무 요구사항 분석가"

PIPELINES: list[dict] = [
    {
        "name": "재무 분석 보고 파이프라인",
        "domain": "finance",
        "description": (
            "브리프 → 요구사항 확정(사람 승인 1회) → 재무 분석(재무 지표 계산기로 예산 대비 실적·투자 지표 계산) → "
            "경영진 보고서 작성 → 재무 검토자의 최종 검토(실패 시 작성 단계로 되돌려 다시 씀)."
        ),
        "stages": [
            {"name": "요구사항 분석/계획 확정", "role": _REQUIREMENTS, "approval": True,
             "instructions": "브리프를 재무 분석 요구사항(분석 기간·대상 항목·예산/실적 수치·투자안 현금흐름·할인율·보고 형식)으로 정리하세요. 요청에 있는 수치는 원문 그대로 옮기세요."},
            {"name": "재무 분석", "role": "재무 분석가",
             "instructions": "1단계 요구사항의 모든 수치를 '재무 지표 계산기'로 계산하세요(예산 대비 실적이면 budget/actual, 투자안이면 cash_flows/rate). 계산 결과표와 차이 원인·민감도를 정리하고, 계산하지 않은 수치는 쓰지 마세요."},
            {"name": "작성", "role": "C-level 보고서 작성자", "primary": True,
             "instructions": "재무 분석 결과로 경영진 보고서를 작성하세요 — 결론과 의사결정 요청을 맨 앞에, 수치표는 분석 단계 값을 그대로 쓰고 1단계 요구사항을 빠짐없이 반영하세요."},
            {"name": "최종 검토 판정", "role": "재무 검토자", "review": True,
             "instructions": "작성 산출물이 1단계 요구사항을 충족하는지, 모든 수치가 재무 분석 단계의 계산 결과와 일치하는지 검증하고 승인/보완 요청으로 판정하세요."},
        ],
    },
    {
        "name": "영업 제안 메일 파이프라인",
        "domain": "sales",
        "description": (
            "브리프 → 요구사항 확정(고객·상황·제안 목적, 사람 승인 1회) → 고객·실적 분석(수치가 있으면 영업 퍼널 계산기) → "
            "제안 메일 작성 → 문서 검토자의 최종 검토(실패 시 작성 단계로 되돌려 다시 씀)."
        ),
        "stages": [
            {"name": "요구사항 분석/계획 확정", "role": _REQUIREMENTS, "approval": True,
             "instructions": "브리프를 제안 메일 요구사항(받는 고객·담당자, 고객 상황과 니즈, 제안할 내용, 근거 자료, 원하는 다음 단계, 어조)으로 정리하세요. 요청에 없는 고객 정보는 '확인 필요'로 남기세요."},
            {"name": "고객·실적 분석", "role": "영업 분석가",
             "instructions": "요청에 퍼널·영업 기회 수치가 있으면 '영업 퍼널 계산기'로 계산하세요. 고객 상황에서 제안의 근거가 될 사실·수치와 고객이 얻을 이득을 3개 이내로 정리하세요."},
            {"name": "작성", "role": "영업 제안 작성자", "primary": True,
             "instructions": "분석 결과를 근거로 고객에게 보낼 제안 메일(제목·본문·다음 단계 요청)을 작성하세요. 1단계 요구사항을 빠짐없이 반영하고 자료에 없는 사례·조건은 쓰지 마세요."},
            {"name": "최종 검토 판정", "role": "문서 검토자", "review": True,
             "instructions": "제안 메일이 1단계 요구사항을 충족하는지, 근거 없는 사례·수치·조건 확약이 없는지 검증하고 승인/보완 요청으로 판정하세요."},
        ],
    },
    {
        "name": "계약서 1차 검토 파이프라인",
        "domain": "legal",
        "description": (
            "브리프(계약서 본문 첨부) → 검토 요청 정리(우리 측 지위·중점 사항, 사람 승인 1회) → 계약 조항 점검기 + 조항별 "
            "검토 → 문서 검토자의 최종 검토(실패 시 검토 단계로 되돌림). 법률 자문을 대신하지 않는 1차 검토용."
        ),
        "stages": [
            {"name": "검토 요청 정리", "role": _REQUIREMENTS, "approval": True,
             "instructions": "브리프를 계약 검토 요구사항(계약 유형, 상대방, 우리 측 지위(갑/을·공급/구매), 중점 검토 사항, 꼭 지켜야 할 조건)으로 정리하세요. 계약서 본문이 없으면 '확인 필요'로 남기세요."},
            {"name": "조항 검토", "role": "계약서 검토자", "primary": True,
             "instructions": "먼저 '계약 조항 점검기'에 계약서 본문을 넣어 조항 유무와 주의 문구를 확인한 뒤, 1단계의 우리 측 지위·중점 사항 기준으로 조항별 검토표(문제점·위험도·수정 제안)와 협상 우선순위를 작성하세요."},
            {"name": "최종 검토 판정", "role": "문서 검토자", "review": True,
             "instructions": "검토 결과가 1단계 중점 사항을 모두 다뤘는지, 인용한 조항·문구가 실제 계약서에 있는지, 면책 문장이 있는지 검증하고 승인/보완 요청으로 판정하세요."},
        ],
    },
]

_REVIEW_GATE_IDS = ["A", "requirements_traceability"]


def _seed_pipeline(conn: sqlite3.Connection, spec: dict) -> None:
    if conn.execute("SELECT 1 FROM pipeline_templates WHERE name = ?", (spec["name"],)).fetchone():
        return
    template_id = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (spec["name"], spec["description"], spec["domain"]),
    ).lastrowid
    primary_id = None
    for seq, st in enumerate(spec["stages"], start=1):
        review = bool(st.get("review"))
        stage_id = conn.execute(
            "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, instructions, gate_ids, "
            "min_gate_pass_ratio, requires_human_approval, is_primary_deliverable, checks_requirements_traceability, "
            "on_fail_return_stage_id, output_content_type) VALUES (?, ?, ?, 'single', ?, ?, ?, ?, ?, ?, ?, 'text')",
            (
                template_id, seq, st["name"], st["instructions"],
                json.dumps(_REVIEW_GATE_IDS if review else ["A", "B", "C", "D", "E", "F", "G"]),
                1.0 if review else 0.0,
                1 if st.get("approval") else 0,
                1 if st.get("primary") else 0,
                1 if review else 0,
                primary_id if review else None,
            ),
        ).lastrowid
        if st.get("primary"):
            primary_id = stage_id
        if conn.execute("SELECT 1 FROM agent_pool WHERE role_name = ?", (st["role"],)).fetchone():
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, instructions) VALUES (?, ?, 1, '')",
                (stage_id, st["role"]),
            )


def seed(conn: sqlite3.Connection) -> None:
    """`reference_pipeline_seeds.seed()` 끝에서 호출 — 도구·파이프라인. Agent는 `reference_pipeline_seeds.
    AGENT_SPECS`에 합쳐져 그쪽 루프가 만들고, 스킬은 `db._seed_catalog_skills`가 `skill_catalog.NEW_SKILLS`로
    만든다. 전부 없을 때만 만든다."""
    for name, description, source, timeout in TOOL_SPECS:
        conn.execute(
            "INSERT OR IGNORE INTO tool_pool (name, description, kind, config) VALUES (?, ?, 'custom_python', ?)",
            (name, description, json.dumps(
                {"function_source": source, "timeout_seconds": timeout, "input_schema": TOOL_INPUT_SCHEMAS[name]},
                ensure_ascii=False,
            )),
        )
    for spec in PIPELINES:
        _seed_pipeline(conn, spec)
