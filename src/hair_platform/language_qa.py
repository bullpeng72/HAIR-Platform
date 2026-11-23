"""한국어 산출물 언어 품질 검사 + 자동 교정 1회.

사용자 요청(2026-11-20, "1번부터 진행" — 직전 분석의 "저장 직전 언어 품질
검사 + 자동 교정"). 실측: 로컬 모델(Ornith 35B, Qwen 계열)이 한국어 발표자료·
제안서에 중국어("3个月内", "竞争", "组织", "统一的")를 섞었고, 스킬 제약사항에
"중국어·한자 문자를 섞지 않는다"를 넣은 뒤에도 세 번 연속 재발했다 — 프롬프트
규칙만으로는 막히지 않는다. 깨진 글자(U+FFFD, "옮�니다")도 함께 나왔다.

동작:
1. **검사** — 화면에 보이는 텍스트만 본다(HTML은 텍스트 노드, 마크다운은
   코드블록 밖). 한자(CJK 통합 한자)·일본어 가나·깨진 글자(U+FFFD)를 찾는다.
   한국어가 주인 응답일 때만 검사한다(사용자가 중국어 답을 원한 경우 제외).
2. **교정** — 문제가 있는 문장만 모아 같은 모델에 한 번 다시 써 달라고 요청하고,
   원문에서 그 문장만 바꾼다(HTML 구조·나머지 내용은 그대로). 문제없으면 호출
   자체가 없다.
3. **안전** — 교정이 실패하거나 결과가 이상하면(개수 불일치, 교정본에도 문제가
   남음) 그 문장은 원문 그대로 둔다. 응답 자체를 실패시키지 않는다.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import overload

_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]")
_HANGUL_RE = re.compile(r"[가-힣]")
# 베트남어 전용 문자(2026-11-21 실측 — 발표자료에 "tiết약"(절약)이 섞였다). 베트남어 성조
# 확장 블록(U+1EA0–U+1EF9)과 베트남어에만 쓰는 ă·đ·ơ·ư만 잡는다 — é·ü 같은 일반 라틴
# 악센트(café, Müller)는 한국어 문서에도 정상적으로 나올 수 있어 넣지 않는다.
_VIET = "\u1ea0-\u1ef9\u0102\u0103\u0110\u0111\u01a0\u01a1\u01af\u01b0"
_ISSUE_RE = re.compile("[㐀-䶿一-鿿豈-﫿぀-ヿ�" + _VIET + "]")

_SKIP_BLOCKS_RE = re.compile(
    r"<(style|script|code|pre)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE
)
_HTML_TEXT_NODE_RE = re.compile(r">([^<>]+)<")
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

_MAX_SEGMENTS = 40
_MAX_SEGMENT_CHARS = 400

REPAIR_SYSTEM = (
    "당신은 한국어 교정자입니다. 주어진 문장 목록에서 중국어·한자·일본어 문자, 베트남어 문자"
    "(예: tiết약 → 절약), 깨진 문자(�)를 "
    "문맥에 맞는 자연스러운 한국어로 바꿉니다. 그 외의 단어·수치·고유명사·영문·기호·문장 구조는 "
    "그대로 둡니다. 설명 없이 JSON 문자열 배열 하나만, 입력과 같은 개수·같은 순서로 출력합니다."
)


def enabled() -> bool:
    """한국어 결과물 전용 교정 — 조직 결과물 언어가 한국어일 때만(2026-11-22). 다른 언어로 일하는
    조직에서 켜 두면 일본어·중국어 결과물을 한국어로 바꿔 망가뜨린다."""
    from hair_platform import org_policy

    if org_policy.output_language() != org_policy.DEFAULT_OUTPUT_LANGUAGE:
        return False
    return os.environ.get("HAIR_LANGUAGE_QA", "1") != "0"


def _is_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith(("<!doctype html", "<html")) or "<main" in text[:2000]


def _visible_segments(text: str) -> list[str]:
    """교정 대상 후보 — 화면에 보이는 텍스트 조각(HTML 텍스트 노드 또는 줄)."""
    if _is_html(text):
        visible = _SKIP_BLOCKS_RE.sub("<x>", text)
        return [m.group(1) for m in _HTML_TEXT_NODE_RE.finditer(visible)]
    visible = _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub("\n", text))
    return visible.splitlines()


def is_mostly_korean(text: str) -> bool:
    body = " ".join(_visible_segments(text))
    hangul = len(_HANGUL_RE.findall(body))
    han = len(_HAN_RE.findall(body))
    return hangul >= 30 and han <= hangul * 0.2


def find_issue_segments(text: str | None) -> list[str]:
    """문제(한자·가나·깨진 글자)가 있는 보이는 텍스트 조각 — 중복 제거, 순서 유지.
    한국어가 주인 텍스트가 아니면 빈 목록."""
    if not text or not _ISSUE_RE.search(text) or not is_mostly_korean(text):
        return []
    seen: list[str] = []
    for seg in _visible_segments(text):
        seg = seg.strip()
        if seg and _ISSUE_RE.search(seg) and seg not in seen:
            seen.append(seg)
    return seen


def _parse_json_list(raw: str, expected: int) -> list[str] | None:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) != expected or not all(isinstance(x, str) for x in data):
        return None
    return data


def repair(text: str | None, call: Callable[[str, str], str]) -> tuple[str | None, dict]:
    """문제 문장을 `call(system, prompt)`로 한 번 교정한다. `(교정된 텍스트,
    보고)`를 돌려준다 — 보고: found(발견 조각 수)·fixed(교정 반영 수)·
    remaining(교정 후에도 남은 조각 수)·error(호출/해석 실패 사유)."""
    report = {"found": 0, "fixed": 0, "remaining": 0, "error": None}
    segments = find_issue_segments(text)
    if not segments or text is None:
        return text, report
    segments = [s for s in segments if len(s) <= _MAX_SEGMENT_CHARS][:_MAX_SEGMENTS]
    report["found"] = len(segments)
    if not segments:
        return text, report
    # 실측(2026-11-20): 11문장을 한 번에 주면 로컬 모델이 앞의 2문장만 고치고
    # 나머지는 그대로 복사했다 — 3문장씩 나눠 요청한다(사고 과정 끈 Ollama 기준
    # 묶음당 약 1초).
    corrections: dict[str, str] = {}
    errors: list[str] = []
    for start in range(0, len(segments), _BATCH_SIZE):
        batch = segments[start : start + _BATCH_SIZE]
        prompt = "다음 문장들을 교정하세요:\n" + json.dumps(batch, ensure_ascii=False)
        try:
            fixed = _parse_json_list(call(REPAIR_SYSTEM, prompt), len(batch))
        except Exception as exc:  # noqa: BLE001 — 교정 실패가 응답을 막으면 안 된다
            errors.append(f"call_failed: {exc}"[:200])
            continue
        if fixed is None:
            errors.append("unparseable_response")
            continue
        for original, corrected in zip(batch, fixed, strict=True):
            corrections[original] = corrected.strip()

    def acceptable(original: str, candidate: str) -> bool:
        # 비었거나, 문제가 남았거나, 원문과 너무 다르면(길이 2배 이상 차이 —
        # 문장을 통째로 새로 쓴 경우) 받지 않는다.
        return (
            bool(candidate)
            and not _ISSUE_RE.search(candidate)
            and 0.5 <= len(candidate) / max(len(original), 1) <= 2.0
        )

    out = text
    for original in segments:
        # 후보 순서: 모델 교정(+사전 보정) → 원문에 사전 보정만. 둘 다 안 되면 원문 유지.
        candidates = [_apply_known_fixes(corrections[original])] if original in corrections else []
        candidates.append(_apply_known_fixes(original))
        corrected = next((c for c in candidates if acceptable(original, c)), None)
        if corrected is None:
            report["remaining"] += 1
            continue
        if original != corrected:
            out = out.replace(original, corrected)
            report["fixed"] += 1
    if errors:
        report["error"] = errors[0]
    return out, report


_BATCH_SIZE = 3

# 모델이 교정하지 못하고 남긴 흔한 중국어 누출어의 결정적 대체(실측 기반, 긴 것부터).
# 모델 교정이 우선이고, 이 사전은 그래도 남은 조각에만 적용된다.
_KNOWN_FIXES: tuple[tuple[str, str], ...] = (
    ("个月内", "개월 내"),
    ("个月", "개월"),
    ("竞争", "경쟁"),
    ("組織", "조직"),
    ("组织", "조직"),
    ("统一的", "통일된"),
    ("统一", "통일"),
    ("个人", "개인"),
    ("自动生成", "자동 생성"),
    ("自动", "자동"),
    ("生成", "생성"),
    # 2026-11-21 보강 — 기획서 5회 반복 A/B(20건)에서 실제로 나온 누출어. 문맥 없이
    # 바꿔도 뜻이 분명한 단어만 넣는다("低"·"正"처럼 한 글자 치환이 문장을 어색하게
    # 만드는 것은 넣지 않고 모델 교정에 맡긴다). 긴 것부터.
    ("总投资", "총투자"),
    ("投资", "투자"),
    ("经济性", "경제성"),
    ("覆盖率", "커버리지"),
    ("运营", "운영"),
    ("運營", "운영"),
    ("节省", "절감"),
    ("節省", "절감"),
    ("资料", "자료"),
    ("資料", "자료"),
    ("语义", "의미"),
    ("語義", "의미"),
    ("向量", "벡터"),
    ("以内", "이내"),
    ("本部", "본부"),
    ("搜索", "검색"),
    ("事项", "사항"),
    ("事項", "사항"),
    ("现有", "기존"),
    ("現有", "기존"),
    ("分级", "등급"),
    ("续费", "갱신 비용"),
    ("变动", "변동"),
    ("變動", "변동"),
    ("回収", "회수"),
    ("回수", "회수"),
    ("表", "표"),
    ("库", "저장소"),
    # 베트남어 누출(2026-11-21 발표자료 실측) — "tiết kiệm"(절약)의 앞 음절이 한글과 섞였다.
    ("tiết kiệm", "절약"),
    ("tiết약", "절약"),
)


def _apply_known_fixes(text: str) -> str:
    for src, dst in _KNOWN_FIXES:
        # 모델이 한글 첫 음절을 쓰고 이어서 같은 뜻의 한자를 붙인 경우("총总投资",
        # "경竞争사")는 그 음절까지 함께 바꿔 "총총투자"·"경경쟁사" 같은 중복을 막는다.
        text = re.sub(f"{re.escape(dst[0])}?{re.escape(src)}", dst, text)
    # 모델이 한글 첫 음절을 쓰고 이어서 같은 뜻의 한자를 붙인 경우("경竞争사" →
    # "경경쟁사")의 중복 음절을 정리한다.
    return re.sub(r"(경)경쟁", r"경쟁", text).replace("3 개월", "3개월")


_OLLAMA_REPAIR_TIMEOUT_SECONDS = 120.0


def _call(provider: str, model: str, system: str, prompt: str) -> str:
    """교정 호출. Ollama는 사고 과정(thinking)을 끄고 짧은 타임아웃으로 직접
    호출한다 — 실측(2026-11-20, Ornith 35B 추론형 모델): 공통 `llm.call_llm()`
    (사고 과정 켜짐)로는 11문장 교정에 18분 15초가 걸리고 형식이 깨져 한 문장도
    반영되지 못했다. 같은 요청을 `think: false`로 보내면 1초에 정확한 JSON이
    나왔다. 다른 provider는 공통 호출을 그대로 쓴다."""
    if provider != "ollama":
        from hair_platform import llm

        return llm.call_llm(provider, model, system, prompt)
    import httpx

    # `think: false`는 특정 모델용 설정이 아니라 "기계적 교정에는 추론 단계를
    # 쓰지 않는다"는 작업 정책이다 — 실측(2026-11-21): 추론형 모델(Ornith)은 사고
    # 과정이 꺼지고, 추론 기능이 없는 모델(exaone3.5)은 오류 없이 무시한다(오류는
    # think=true를 지원하지 않는 모델에 보낼 때만 난다). `think` 필드를 모르는
    # 옛 Ollama가 400을 주면 그 필드 없이 한 번 다시 보낸다. 샘플링은 교정 작업에
    # 맞춰 temperature 0만 쓰고, 모델별 옵션은 모델 레지스트리 값을 따른다.
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    from hair_platform import model_options

    body = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {**model_options.for_model("ollama", model), "temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    resp = httpx.post(f"{base_url}/api/chat", json=body, timeout=_OLLAMA_REPAIR_TIMEOUT_SECONDS)
    if resp.status_code == 400 and "think" in resp.text:
        body.pop("think")
        resp = httpx.post(
            f"{base_url}/api/chat", json=body, timeout=_OLLAMA_REPAIR_TIMEOUT_SECONDS
        )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


@overload
def repair_output(
    conn, text: str, provider: str | None, model: str | None, actor_id: int, where: str
) -> str: ...


@overload
def repair_output(
    conn, text: None, provider: str | None, model: str | None, actor_id: int, where: str
) -> None: ...


def repair_output(
    conn,
    text: str | None,
    provider: str | None,
    model: str | None,
    actor_id: int,
    where: str,
) -> str | None:
    """응답 경로용 래퍼 — 같은 provider/model로 한 번 교정하고, 발견이 있었으면
    감사로그(`language_qa_repaired`)를 남긴다. 어떤 예외도 밖으로 내보내지 않는다."""
    if not enabled() or not provider or not model or not find_issue_segments(text):
        return text
    from hair_platform import audit  # 지연 import — 순수 검사 함수는 의존성 없이 테스트

    repaired, report = repair(text, lambda system, prompt: _call(provider, model, system, prompt))
    try:
        audit.record(
            conn,
            actor_id,
            "language_qa_repaired",
            "language_qa",
            None,
            after={"where": where, **report},
        )
        conn.commit()
    except Exception:  # noqa: BLE001, S110 — 감사 기록 실패가 응답을 막으면 안 된다
        pass
    return repaired
