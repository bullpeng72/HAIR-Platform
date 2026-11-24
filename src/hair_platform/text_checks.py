"""결과물 문장 검증 — 인용 근거·용어 일관성(2026-11-24, P0-1 남은 항목 — "go ahead").

`numeric_check`(퍼센트 수치)와 같은 원칙: LLM을 부르지 않는 결정적 검사로, 측정됐을 때만 Gate 기록에
붙는다(`agent_evaluation.attach_quality_checks`). 단계 `gate_ids`에 넣지 않는 한 통과 판정에는
참여하지 않는다(배지·기록만).

1. **인용 근거(`quote_grounding`)** — 결과물이 "…라고 했다", "…에 따르면", "출처" 같은 인용 표지와 함께
   따옴표로 옮긴 문장(15자 이상)이 이번 실행의 근거 자료(요청·조회 결과·도구 결과·앞 단계 산출물)에
   실제로 있는지 본다. 강조용 따옴표는 인용 표지가 없으면 검사하지 않는다(오탐 방지).
2. **용어 일관성(`term_consistency`)** — 근거 자료에 있는 낱말에서 한 음절이 빠지거나 바뀐 낱말이,
   앞뒤 낱말까지 같은 자리에 나오면 표시한다. 원래 분석의 실측 오류("1분기 착수" → "1분 착수",
   "발표 노트" → "발포 노트")가 이 모양이다. 앞뒤 낱말이 같아야 하므로 비슷한 다른 낱말(계획/계약)은
   잡지 않는다.
"""

from __future__ import annotations

import re
import unicodedata

MIN_QUOTE_CHARS = 15
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_QUOTE_RE = re.compile(r"“([^”]{%d,})”|\"([^\"\n]{%d,})\"|「([^」]{%d,})」|『([^』]{%d,})』" % ((MIN_QUOTE_CHARS,) * 4))
_CITE_AFTER_RE = re.compile(
    r"^\s*\)?\s*(?:\(?[^()\n]{0,20}\)?\s*)?(?:(?:이)?라고|라는|이라는|고\s*(?:밝|명시|적|썼|했|말|언급|기재|규정)|"
    r"\(?\s*(?:출처|원문|인용|p\.\s*\d|쪽))"
)
_CITE_BEFORE_RE = re.compile(r"(?:에\s*따르면|에서는|에는|원문(?:은|에)?|출처|인용|명시|규정|조항|기재)[^\n]{0,25}$")
_WORD_RE = re.compile(r"[0-9]*[가-힣]+")
_PARTICLES = sorted(
    ["은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "로", "와", "과", "도", "만", "까지",
     "부터", "에게", "께", "보다", "처럼", "이다", "입니다", "이며", "으로서", "로서", "에는", "에서는", "과의", "와의"],
    key=len, reverse=True,
)


def _prose(text: str) -> str:
    return _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub("\n", text or ""))


def _norm(text: str) -> str:
    """비교용 — 공백·문장부호·따옴표 차이를 없앤다."""
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s\W_]+", "", t).lower()


# ------------------------------------------------------------------ 1. 인용 근거


def citation_quotes(text: str) -> list[str]:
    prose = _prose(text)
    found: list[str] = []
    for m in _QUOTE_RE.finditer(prose):
        quote = next(g for g in m.groups() if g).strip()
        if not re.search(r"[가-힣A-Za-z]", quote):
            continue
        after = prose[m.end(): m.end() + 40]
        before = prose[max(0, m.start() - 40): m.start()]
        if _CITE_AFTER_RE.match(after) or _CITE_BEFORE_RE.search(before):
            if quote not in found:
                found.append(quote)
    return found


def _quote_supported(quote: str, evidence_norm: str) -> bool:
    # 중략("…", "...")이 있으면 조각마다 확인한다.
    pieces = [p for p in re.split(r"…|\.\.\.|\(중략\)|\[중략\]", quote) if len(_norm(p)) >= 4]
    return bool(pieces) and all(_norm(p) in evidence_norm for p in pieces)


def quote_grounding(text: str, evidence: str | None) -> dict:
    quotes = citation_quotes(text)
    if not quotes or not (evidence or "").strip():
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "checked": len(quotes), "unsupported": []}
    ev = _norm(evidence or "")
    bad = [q for q in quotes if not _quote_supported(q, ev)]
    return {
        "score": round(1 - len(bad) / len(quotes), 3),
        "status": "pass" if not bad else "warn",
        "passed": not bad,
        "not_measured": False,
        "checked": len(quotes),
        "unsupported": [{"text": q[:200]} for q in bad[:10]],
    }


# --------------------------------------------------------------- 2. 용어 일관성


def _stem(word: str) -> str:
    for p in _PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RE.findall(_prose(text))]


# 끝 음절이 이 글자들끼리 다르거나 빠진 것은 어미·조사 차이(배상한다/배상한다고, 한다/했다)라 오탈자로 보지 않는다.
_ENDING_SYLLABLES = set("고다며요서면도만를을은는이가에의로와과께며게지네죠야여요든데라")


def _one_syllable_off(found: str, expected: str) -> bool:
    if found == expected or len(expected) < 2 or len(found) < 1:
        return False
    if len(expected) == len(found) + 1:  # 한 음절 빠짐
        positions = [i for i in range(len(expected)) if expected[:i] + expected[i + 1:] == found]
        return any(not (i == len(expected) - 1 and expected[i] in _ENDING_SYLLABLES) for i in positions)
    if len(expected) == len(found) and len(found) >= 2:  # 한 음절 바뀜(한글 음절끼리만)
        diff = [i for i in range(len(found)) if found[i] != expected[i]]
        if len(diff) != 1:
            return False
        i = diff[0]
        if not ("가" <= found[i] <= "힣" and "가" <= expected[i] <= "힣"):
            return False
        if i == len(found) - 1 and found[i] in _ENDING_SYLLABLES and expected[i] in _ENDING_SYLLABLES:
            return False
        # 바뀐 음절은 첫소리가 같아야 한다(발포/발표 — ㅍ). 실측: 첫소리가 다른 쌍(결정/결론·위치/수치·
        # 집행률/실행률)은 거의 다 서로 다른 정상 낱말이었다.
        return _choseong(found[i]) == _choseong(expected[i])
    return False


def _choseong(syllable: str) -> int:
    return (ord(syllable) - 0xAC00) // 588


# 용언 조각(어간+어미 일부) — 조사를 떼고 남은 "크므"·"주므"·"하나"·"않으면" 같은 조각은 낱말 비교에서 뺀다
# (실측 오탐의 대부분). 명사 위주로 본다.
_VERBAL_LAST = set("므고하한히게지면며다서요니나던된될할함됨운린른는인음임")


def _comparable(word: str) -> bool:
    hangul = re.sub(r"[0-9]", "", word)
    if not hangul:
        return False
    if len(hangul) < 2 and not word[:1].isdigit():
        return False
    return hangul[-1] not in _VERBAL_LAST


def _meaningful_neighbor(word: str) -> bool:
    return len(re.sub(r"[0-9]", "", word)) >= 2


def term_suspects(text: str, evidence: str | None) -> list[dict] | None:
    """(찾은 낱말, 근거의 낱말, 문맥) 목록. 근거에 한국어가 거의 없으면 None(검사 불가)."""
    ev_words = _words(evidence or "")
    if len(ev_words) < 20:
        return None
    vocab = set(ev_words)
    # 근거 낱말의 앞뒤 낱말(문맥 짝)
    neighbors: dict[str, set[str]] = {}
    for i, w in enumerate(ev_words):
        if not _comparable(w):
            continue
        ctx = neighbors.setdefault(w, set())
        if i > 0 and _meaningful_neighbor(ev_words[i - 1]):
            ctx.add("<" + ev_words[i - 1])
        if i + 1 < len(ev_words) and _meaningful_neighbor(ev_words[i + 1]):
            ctx.add(">" + ev_words[i + 1])
    out_words = _words(text)
    suspects: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, w in enumerate(out_words):
        if w in vocab or not _comparable(w):
            continue
        ctx = set()
        if i > 0 and _meaningful_neighbor(out_words[i - 1]):
            ctx.add("<" + out_words[i - 1])
        if i + 1 < len(out_words) and _meaningful_neighbor(out_words[i + 1]):
            ctx.add(">" + out_words[i + 1])
        for cand, cand_ctx in neighbors.items():
            if (w, cand) in seen or not _one_syllable_off(w, cand):
                continue
            # 3음절 이상 낱말에서 한 음절이 빠진 경우(인건비→건비)는 다른 정상 낱말일 가능성이 낮아 문맥 없이도
            # 표시한다(실측: 표 칸 "IT인프라(−30백만 원)·건비(−20백만"는 앞뒤 낱말이 근거와 달랐다).
            # 끝 음절이 빠진 경우(검증식→검증, 사용자→사용, 경로용→경로)는 접미사를 뗀 정상 낱말이라 이 예외에서 뺀다.
            dropped_long = (len(cand) == len(w) + 1 and len(re.sub(r"[0-9]", "", cand)) >= 3
                            and not cand.startswith(w))
            if not dropped_long and not (ctx & cand_ctx):
                continue
            seen.add((w, cand))
            left = out_words[i - 1] + " " if i > 0 else ""
            right = " " + out_words[i + 1] if i + 1 < len(out_words) else ""
            suspects.append({"found": w, "expected": cand, "context": f"{left}{w}{right}"})
    return suspects


def term_consistency(text: str, evidence: str | None) -> dict:
    suspects = term_suspects(text, evidence)
    if suspects is None:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True, "suspects": []}
    return {
        "score": round(max(0.0, 1 - 0.2 * len(suspects)), 3),
        "status": "pass" if not suspects else "warn",
        "passed": not suspects,
        "not_measured": False,
        "suspects": suspects[:10],
    }
