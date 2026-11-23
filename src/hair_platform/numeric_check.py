"""수치 근거 검사 — 결과물의 퍼센트 수치가 근거 자료의 숫자로 재현되는지 기계적으로 확인한다.

사용자 요청(2026-11-23, "P0부터 개선 진행해줘" — P0-1 결과물 신뢰성). 실측(같은 날 Agent 9개
라이브 실행): 도구가 계산한 숫자 자체는 정확했지만, 그 숫자를 설명하는 문장의 퍼센트가 틀렸다
— 실적 보고서의 "영업이익 목표 대비 13.8% 미달"(실제 0.8÷7 = 11.4%), "약 8.2%p 차이"(금액
차이를 성장률 차이로 착각). LLM 자체 검토로 고치게 하자 일부는 고치고 일부는 새로 틀렸고,
한 건은 19분을 쓰고 결과를 버렸다 — 숫자 검증은 LLM이 아니라 계산으로 해야 한다.

판정 규칙(보수적):
- 대상은 결과물의 퍼센트(%·%p·퍼센트) 수치만이다(코드 블록·차트 설정 안은 제외).
- 근거 자료(요청 원문·도구 결과·조회 결과·앞 단계 산출물)에 같은 값이 있으면 근거 있음.
- 근거 자료의 두 숫자로 비율(a÷b×100)·증감률(|a−b|÷b×100)을 계산해 표시된 자릿수까지 맞으면
  근거 있음. %p(퍼센트포인트)만 두 숫자의 차이(|a−b|)도 허용한다("약"이 붙으면 상대 2%까지).
  처음엔 두 숫자의 합·차이도 일반 %의 후보로 썼는데, 실측 결과물에서 "6.2+100=106.2,
  120−106.2=13.8"처럼 우연히 맞는 조합이 틀린 "13.8% 미달"을 통과시켰다 — 후보를 좁혔다.
- 근거 숫자가 너무 많으면(우연히 맞는 조합이 많아져) 검사하지 않는다 — 놓치는 쪽을 택한다.

이 검사는 **틀렸다고 단정하지 않는다** — "근거 자료로 재현되지 않는다"만 알린다. 그래서 고칠 때도
"맞으면 계산식을 덧붙이고, 틀렸으면 근거대로 고치라"고만 요청한다(잘못 걸려도 계산식이 붙을 뿐).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from itertools import permutations

# 근거 숫자가 이보다 많으면 조합 수가 커져 우연히 맞는 값이 많아진다 — 검사를 건너뛴다.
MAX_EVIDENCE_NUMBERS = 60
_MAX_CLAIMS_TO_REPAIR = 6

_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_PERCENT_RE = re.compile(r"([+\-−]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[+\-−]?\d+(?:\.\d+)?)\s*(%p|%포인트|%|퍼센트)")
_NUMBER_RE = re.compile(r"(?<![\w.])[+\-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])[+\-−]?\d+(?:\.\d+)?")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


def enabled() -> bool:
    return os.environ.get("HAIR_NUMERIC_CHECK", "1") != "0"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "").replace("−", "-"))


def _decimals(raw: str) -> int:
    raw = raw.replace(",", "")
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def evidence_numbers(evidence: str) -> list[float]:
    """근거 자료의 숫자(중복 제거). 연도처럼 보이는 정수(1900~2100)와 0은 뺀다."""
    seen: list[float] = []
    for m in _NUMBER_RE.finditer(evidence or ""):
        try:
            v = abs(_to_float(m.group(0)))
        except ValueError:
            continue
        if v == 0 or (v.is_integer() and 1900 <= v <= 2100):
            continue
        if v not in seen:
            seen.append(v)
    return seen


def percent_claims(text: str) -> list[dict]:
    """결과물의 퍼센트 수치(코드 블록 밖) — 값·표시 자릿수·"약" 여부·%p 여부·들어 있는 문장."""
    body = _FENCE_RE.sub(" ", text or "")
    claims: list[dict] = []
    for sentence in _SENTENCE_SPLIT_RE.split(body):
        for m in _PERCENT_RE.finditer(sentence):
            raw = m.group(1)
            claims.append(
                {
                    "value": abs(_to_float(raw)),
                    "decimals": _decimals(raw),
                    "approx": "약" in sentence[max(0, m.start() - 4) : m.start()],
                    "point": m.group(2) in ("%p", "%포인트"),
                    "text": m.group(0),
                    "sentence": sentence.strip(),
                }
            )
    return claims


def _candidates(nums: list[float]) -> tuple[list[float], list[float]]:
    """(일반 % 후보, %p 후보) — 둘 다 근거 숫자 자체를 포함한다."""
    ratios: list[float] = list(nums)
    diffs: list[float] = list(nums)
    for a, b in permutations(nums, 2):
        ratios.append(a / b * 100)
        ratios.append(abs(a - b) / b * 100)
        diffs.append(abs(a - b))
    return ratios, diffs


def _matches(claim: dict, cands: tuple[list[float], list[float]]) -> bool:
    ratios, diffs = cands
    pool = diffs if claim["point"] else ratios
    v = claim["value"]
    tol = 0.5 * 10 ** (-claim["decimals"]) + 1e-9
    if claim["approx"]:
        tol = max(tol, 0.02 * v)
    return any(abs(c - v) <= tol for c in pool)


def unsupported_claims(text: str, evidence: str) -> list[dict] | None:
    """근거 자료로 재현되지 않는 퍼센트 수치 목록. 검사할 수 없으면(근거 숫자가 너무 적거나
    많음) `None` — 빈 목록(전부 근거 있음)과 구분한다."""
    claims = percent_claims(text)
    if not claims:
        return []
    nums = evidence_numbers(evidence)
    if len(nums) < 2 or len(nums) > MAX_EVIDENCE_NUMBERS:
        return None
    cands = _candidates(nums)
    return [c for c in claims if not _matches(c, cands)]


def gate_score(text: str, evidence: str) -> dict:
    """파이프라인 단계 기록용 점수 — 단계 `gate_ids`에 넣지 않는 한 통과 판정에 참여하지 않는다
    (`pipeline_runtime._gate_pass_ratio()`가 단계 목록에 든 키만 센다)."""
    bad = unsupported_claims(text, evidence)
    total = len(percent_claims(text))
    if bad is None or total == 0:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "checked": total, "unsupported": []}
    score = round(1 - len(bad) / total, 3)
    return {
        "score": score,
        "status": "pass" if not bad else "warn",
        "passed": not bad,
        "not_measured": False,
        "checked": total,
        "unsupported": [{"text": c["text"], "sentence": c["sentence"][:200]} for c in bad[:10]],
    }


REPAIR_SYSTEM = (
    "너는 보고서의 수치 문장 검수자다. 각 문장의 퍼센트 수치가 [근거 자료]의 숫자로 계산되는지 "
    "직접 계산해 확인한다. 계산이 맞으면 문장 내용은 그대로 두고 수치 바로 뒤에 괄호로 계산식을 "
    "덧붙인다(예: 「(6.2÷7=88.6%)」). 틀렸으면 근거 자료로 다시 계산한 값으로 고친다. 비율(%)과 "
    "비율 차이(%p)를 구분하고, 비교 방향(무엇 대비 무엇)을 확인한다. 근거 자료에 없는 숫자는 새로 "
    "만들지 않는다. 문장 순서대로 고친 문장만 JSON 문자열 배열로 출력한다 — 다른 말은 쓰지 않는다."
)


def repair(text: str, bad: list[dict], evidence: str, call: Callable[[str, str], str]) -> tuple[str, dict]:
    """근거 없는 퍼센트가 든 문장만 한 번에 고친다(원문에서 그 문장만 바꾼다). 고친 뒤 근거 없는
    수치가 늘었거나, 응답을 해석할 수 없거나, 문장 수가 안 맞으면 원문을 그대로 둔다."""
    report = {"found": len(bad), "fixed": 0, "error": None}
    sentences: list[str] = []
    for c in bad:
        if c["sentence"] and c["sentence"] not in sentences and c["sentence"] in text:
            sentences.append(c["sentence"])
    sentences = sentences[:_MAX_CLAIMS_TO_REPAIR]
    if not sentences:
        return text, report
    prompt = (
        f"[근거 자료]\n{evidence[-6000:]}\n\n[고칠 문장]\n"
        + json.dumps(sentences, ensure_ascii=False)
    )
    try:
        raw = call(REPAIR_SYSTEM, prompt)
        m = re.search(r"\[.*\]", raw or "", re.DOTALL)
        fixed = json.loads(m.group(0)) if m else None
    except Exception as exc:  # noqa: BLE001 — 검수 실패가 응답을 막으면 안 된다
        report["error"] = f"call_failed: {exc}"[:200]
        return text, report
    if not isinstance(fixed, list) or len(fixed) != len(sentences):
        report["error"] = "unparseable_response"
        return text, report
    out = text
    rejected = 0
    for original, new in zip(sentences, fixed, strict=True):
        new = str(new).strip()
        # 너무 짧아졌거나(내용을 잘라 먹음) 너무 길어진(설명을 덧붙임) 교정은 받지 않는다.
        if not new or new == original or not (0.6 * len(original) <= len(new) <= 2.0 * len(original) + 40):
            continue
        # 문장 단위로 근거 없는 수치가 **줄었을 때만** 받는다(2026-11-23 실측 — 로컬 모델이 13.8%를
        # 11.4%로 바로 고치면서 같은 문장에 근거 없는 "8.6%p"를 새로 넣었다. 전체 개수만 보던
        # 처음 기준은 이를 통과시켰다).
        before_n = len(unsupported_claims(original, evidence) or [])
        after_n = len(unsupported_claims(new, evidence) or [])
        if after_n < before_n:
            out = out.replace(original, new, 1)
            report["fixed"] += 1
        else:
            rejected += 1
    if rejected and not report["fixed"]:
        report["error"] = "no_improvement"
    return out, report


NOTE_PREFIX = "> ⚠ 수치 확인 필요"


def unresolved_note(text: str, evidence: str) -> str:
    """교정 뒤에도 근거 자료로 재현되지 않는 퍼센트가 남으면 결과물 끝에 붙일 안내(없으면 빈 문자열)
    — 틀렸다고 단정하지 않고 사람이 확인할 위치만 알린다."""
    left = unsupported_claims(text, evidence)
    if not left:
        return ""
    items = list(dict.fromkeys(c["text"] for c in left))[:6]
    return (
        f"\n\n{NOTE_PREFIX}: {', '.join(items)} — 요청·조회·계산 결과의 숫자로 다시 계산되지 않는 "
        "수치입니다. 계산 근거를 확인해 주세요(%p는 두 비율의 차이에만 씁니다)."
    )


def repair_output(conn, text: str | None, evidence: str, provider: str | None, model: str | None,
                  actor_id: int, where: str) -> tuple[str | None, dict | None]:
    """응답 경로용 래퍼 — 근거 없는 퍼센트가 있으면 같은 provider/model로 한 번 교정하고 감사로그
    (`numeric_check_repaired`)를 남긴다. 어떤 예외도 밖으로 내보내지 않는다."""
    if not enabled() or not text or not provider or not model:
        return text, None
    bad = unsupported_claims(text, evidence)
    if not bad:
        return text, None
    from hair_platform import audit, language_qa, progress  # 지연 import — 순수 검사 함수는 의존성 없이 테스트

    progress.report(f"수치 근거 확인·교정 중({len(bad)}건)")

    repaired, report = repair(text, bad, evidence, lambda s, p: language_qa._call(provider, model, s, p))
    # 실측(2026-11-23): 로컬 모델 교정은 5건 중 1건만 바르게 고쳤다 — 남은 건 숨기지 않고 표시한다.
    note = unresolved_note(repaired, evidence)
    if note and NOTE_PREFIX not in repaired:
        repaired = repaired.rstrip() + note
        report["flagged"] = len(unsupported_claims(repaired.split(NOTE_PREFIX)[0], evidence) or [])
    try:
        audit.record(conn, actor_id, "numeric_check_repaired", "numeric_check", None,
                     after={"where": where, **report})
        conn.commit()
    except Exception:  # noqa: BLE001, S110 — 감사 기록 실패가 응답을 막으면 안 된다
        pass
    return repaired, report
