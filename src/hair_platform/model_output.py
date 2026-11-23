"""모델 출력 정리 — 내부 추론·실행되지 않은 도구 호출 문자열을 산출물에서 걷어 낸다.

실측(2026-11-21, "파이프라인 품질 점검해줘" — 실적 보고 파이프라인 실제 실행): CrewAI 경로에서
로컬 모델(Qwen 계열)의 출력이 다음 모양 그대로 단계 산출물로 저장됐다.

- 내부 추론: "Let me analyze this task carefully …" 수십 줄 뒤 ``</think>``
- ReAct 흔적: ``Thought: …``, ``Final Answer:`` 접두어
- 실행되지 않은 도구 호출: ``<tool_call><function=custom_tool_2337> … </tool_call>``
  (2단계 '데이터 분석'은 산출물 전체가 이것뿐이라, 다음 단계가 원 요청의 수치를 잃었다)

이 함수는 그 흔적만 걷어 내고 실제 결과물은 건드리지 않는다. 결과물 본문에 나오는 일반
문장("Thought"라는 단어 등)은 줄 머리 형식이 아닐 때 그대로 둔다.
"""

from __future__ import annotations

import re

_THINK_CLOSE = "</think>"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_RE = re.compile(r"<tool_call>.*?(?:</tool_call>|\Z)", re.DOTALL | re.IGNORECASE)
_FUNCTION_CALL_RE = re.compile(r"<function=[^>]*>.*?(?:</function>|\Z)", re.DOTALL | re.IGNORECASE)
_FINAL_ANSWER_RE = re.compile(r"^\s*Final Answer\s*:\s*", re.IGNORECASE | re.MULTILINE)
_REACT_LINE_RE = re.compile(r"^\s*(Thought|Action|Action Input|Observation)\s*:.*$", re.MULTILINE)


def clean(text: str | None) -> str:
    """내부 추론·ReAct 흔적·실행되지 않은 도구 호출을 걷어 낸 본문(앞뒤 공백 제거)."""
    if not text:
        return ""
    out = _THINK_BLOCK_RE.sub("", text)
    if _THINK_CLOSE in out:  # 여는 태그 없이 닫는 태그만 있는 경우(추론이 출력 앞머리에 섞임)
        out = out.rsplit(_THINK_CLOSE, 1)[1]
    finals = list(_FINAL_ANSWER_RE.finditer(out))
    if finals:  # ReAct 최종 답 — 마지막 "Final Answer:" 뒤만 결과물이다
        out = out[finals[-1].end():]
    out = _TOOL_CALL_RE.sub("", out)
    out = _FUNCTION_CALL_RE.sub("", out)
    out = _REACT_LINE_RE.sub("", out)
    # 앞머리의 영어 작업 계획 문단("Let me …", "I will …") — 실측: </think> 뒤에 결과물 대신
    # 계획 한 문단 + 도구 호출만 남은 경우가 있었다. 맨 앞 문단들만 보고, 결과물 본문이
    # 시작되면 멈춘다(본문 중간의 영어 문장은 건드리지 않는다).
    paragraphs = re.split(r"\n\s*\n", out.strip())
    while paragraphs and _PLANNING_RE.match(paragraphs[0]):
        paragraphs.pop(0)
    out = "\n\n".join(paragraphs)
    out = _drop_english_preface(out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# 실측(2026-11-21, "모든 파이프라인 점검" — 사업/기획 파이프라인): 덱 담당 Agent 출력 앞머리에
# "The context provided includes: … My role: … Let me plan the deck structure …" 같은 영어 작업
# 메모가 수십 줄 붙은 채 저장됐다. 위 `_PLANNING_RE`는 정해진 시작 문구만 보므로 놓쳤다.
# 줄 머리에서 시작하는 마크업만 본다 — 메모 문장 안에 인용된 `<main class="hair-deck">`는 아니다.
_MARKUP_START_RE = re.compile(
    r"^[ \t]*(?:```html|<!doctype html|<html\b|<main\b[^>]*hair-deck)",
    re.IGNORECASE | re.MULTILINE,
)
_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_NON_PROSE_START_RE = re.compile(r"^\s*(```|<|#|\||!\[)")


def _hangul_share(text: str) -> float:
    hangul = len(_HANGUL_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    return hangul / (hangul + latin) if hangul + latin else 0.0


def _drop_english_preface(out: str) -> str:
    """한국어 결과물 앞에 붙은 영어 작업 메모를 걷어 낸다.
    1) HTML/덱 마크업 앞부분이 대부분 영어(한글 비율 30% 미만)면 마크업부터만 남긴다 —
       덱은 마크업만 결과물로 쓰이므로(`presentation_deck`) 앞부분은 원래 버려지는 메모다.
    2) 본문이 한국어(한글 비율 50% 이상)인데 맨 앞 문단들이 한글 없는 영어 산문이면 뺀다.
       코드 블록·마크업·제목·표로 시작하는 문단은 산문이 아니라 건드리지 않는다.
    영어로 쓴 결과물(본문 한글 비율이 낮음)은 어느 규칙에도 걸리지 않는다."""
    markup = _MARKUP_START_RE.search(out)
    if markup and markup.start() > 0:
        prefix = out[: markup.start()]
        if len(_LATIN_RE.findall(prefix)) >= 40 and _hangul_share(prefix) < 0.3:
            return out[markup.start():]
    paragraphs = re.split(r"\n\s*\n", out)
    dropped = 0
    while dropped < len(paragraphs) - 1:
        para = paragraphs[dropped]
        # 한글 비율 25% 미만(2026-11-22 — "…four deliverables (기획서·전략분석·…)…"처럼 한국어 용어를
        # 몇 개 섞은 영어 메모가 "한글 전혀 없음" 기준을 빠져나갔다).
        is_english_prose = (
            not _NON_PROSE_START_RE.match(para)
            and _hangul_share(para) < 0.25
            and len(re.findall(r"[A-Za-z]{2,}", para)) >= 6
        )
        if not is_english_prose:
            break
        dropped += 1
    if dropped and _hangul_share("\n\n".join(paragraphs[dropped:])) >= 0.5:
        return "\n\n".join(paragraphs[dropped:])
    return out


_PLANNING_RE = re.compile(
    r"^\s*(Let me|Let's|I will|I'll|I need to|I should|I now|Now I|Now let me|First,? I|Okay,|OK,)\b",
    re.IGNORECASE,
)


def leaked(text: str | None) -> bool:
    """정리할 흔적이 있었는지(감사·측정용)."""
    return bool(text) and clean(text) != (text or "").strip()


# 실측(2026-11-21, "모든 파이프라인 점검" — 사업/기획 파이프라인 4단계): 덱 담당 Agent가 결과물
# 대신 작업 과정 전체를 출력했다 — 슬라이드 조각을 쓰고 "Hmm, let me reconsider…", "Wait, I need
# to…" 같은 영어 메모를 사이사이 80번 넘게 끼우며 같은 슬라이드를 여러 번 다시 썼다(덱 감싸기
# `<main class="hair-deck">`도 없어 화면에 스타일 없이 보였다). 메모와 초안이 섞여 있어 정리로는
# 최종본을 안전하게 골라낼 수 없다 — 감지해서 최종 결과물만 다시 내도록 재시도시킨다.
_FENCED_BLOCK_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
WORK_IN_PROGRESS_MIN_NOTES = 3


def work_in_progress_notes(text: str | None) -> int:
    """한국어 결과물 사이에 끼어 있는 영어 작업 메모 문단 수(코드 블록·마크업 밖).
    결과물 본문이 한국어가 아니면(영어 문서) 0 — 영어로 쓴 결과물은 대상이 아니다."""
    if not text:
        return 0
    prose = _TAG_RE.sub(" ", _FENCED_BLOCK_RE.sub("\n\n", text))
    # 결과물의 언어 — 마크업 태그(영어 class 이름 등)를 뺀 글로 보되, 메모가 많을수록 전체 비율이
    # 낮아지므로 코드 블록 안(초안 결과물) 비율과 큰 쪽을 쓴다.
    blocks = " ".join(_FENCED_BLOCK_RE.findall(text))
    share = max(_hangul_share(_TAG_RE.sub(" ", text)), _hangul_share(_TAG_RE.sub(" ", blocks)))
    if share < 0.3:
        return 0
    notes = 0
    for para in re.split(r"\n\s*\n", prose):
        words = re.findall(r"[A-Za-z]{2,}", para)
        if len(words) >= 6 and _hangul_share(para) < 0.25:
            notes += 1
    return notes


def work_in_progress(text: str | None) -> bool:
    """결과물이 아니라 작업 과정(영어 메모와 초안이 번갈아 나옴)으로 보이는지 — 한국어 결과물 속
    영어 메모(3문단+) 또는 언어와 무관한 혼잣말 문단(5개+, `self_talk_paragraphs`)."""
    return (
        work_in_progress_notes(text) >= WORK_IN_PROGRESS_MIN_NOTES
        or self_talk_paragraphs(text) >= SELF_TALK_MIN_PARAGRAPHS
    )


# 실측(2026-11-21, "모든 파이프라인 점검" — 제안서 파이프라인 6단계 최종 승인 판정): PM Agent가
# 판정문 대신 영어 추론을 같은 문단째 67번 반복하다 출력 한도(16384토큰)에서 끊겼다. 대부분 영어라
# `work_in_progress`(한국어 결과물 대상)에 걸리지 않았고 Gate A도 통과시켰다. 언어와 무관하게
# "같은 긴 줄이 여러 번 반복"되는 출력을 잡는다.
_REPEAT_MIN_LINE = 20
_TABLE_RULE_RE = re.compile(r"^[\s|:\-=+*_#>`]+$")


def repetition_loop(text: str | None, min_repeats: int = 5) -> bool:
    """같은 긴 줄(20자 이상, 표 구분선 제외)이 `min_repeats`번 이상 반복되고, 긴 줄 가운데
    서로 다른 줄이 절반이 안 되면 반복 루프로 본다."""
    if not text:
        return False
    lines = [
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) >= _REPEAT_MIN_LINE and not _TABLE_RULE_RE.match(line)
    ]
    if len(lines) < 20:
        return False
    counts: dict[str, int] = {}
    for line in lines:
        counts[line] = counts.get(line, 0) + 1
    return max(counts.values()) >= min_repeats and len(counts) / len(lines) < 0.5


# 실측(2026-11-22, "모든 파이프라인 점검" — SW 개발 파이프라인 4단계 구현): 풀스택 개발자가 최종
# 파일 대신 "코드 조각 → Wait, there's an issue… → Hmm, let me reconsider…"를 6만 자 동안 반복하다
# 출력 한도에서 문장 중간에 끊겼다. 코드 산출물은 영어 비율이 높아 위 한국어 기준에서 빠졌다.
# 결과물 언어와 무관하게 "혼잣말" 문단(Wait/Hmm/Actually/Let me…로 시작)의 개수를 센다 — 영어로
# 쓴 정식 문서에 이런 문단이 여러 개 나오는 일은 거의 없다.
_SELF_TALK_RE = re.compile(
    r"^\s*(?:\*\*)?(Wait|Hmm+|Actually|Let me|Let's|Now,? let me|Now I|I need to|I think|I'll|"
    r"I will|OK,|Okay,|But wait|Oh,|Alternatively,|On second thought)\b",
    re.IGNORECASE,
)
SELF_TALK_MIN_PARAGRAPHS = 5


def self_talk_paragraphs(text: str | None) -> int:
    """코드 블록 밖에서 혼잣말로 시작하는 문단 수."""
    if not text:
        return 0
    prose = _FENCED_BLOCK_RE.sub("\n\n", text)
    return sum(1 for para in re.split(r"\n\s*\n", prose) if _SELF_TALK_RE.match(para))


# 실측(2026-11-22 — 사업/기획 덱): 출력 한도(사고 토큰 포함)에 걸려 덱이 표 칸 중간(`<td>20`)에서
# 끊긴 채 작성 단계를 통과했다. 연 슬라이드·덱 태그가 닫히지 않았으면 잘린 출력으로 본다.
# 줄 머리의 태그만 센다 — 검토 문서가 문장 안에서 `<main class="hair-deck">`를 언급한 것은 아니다.
_SECTION_OPEN_RE = re.compile(r"^[ \t]*<section\b", re.IGNORECASE | re.MULTILINE)
_SECTION_CLOSE_RE = re.compile(r"</section\s*>", re.IGNORECASE)


def truncated_markup(text: str | None) -> bool:
    """HTML 덱/문서가 끝까지 쓰이지 않고 잘렸는지 — `<section>`이 닫힌 것보다 많이 열렸거나,
    `<main>`·`<html>`을 열고 닫지 않았다."""
    if not text:
        return False
    opened = len(_SECTION_OPEN_RE.findall(text))
    if opened and opened > len(_SECTION_CLOSE_RE.findall(text)):
        return True
    for tag in ("main", "html"):
        if re.search(rf"^[ \t]*<{tag}\b", text, re.IGNORECASE | re.MULTILINE) and not re.search(
            rf"</{tag}\s*>", text, re.IGNORECASE
        ):
            return True
    return False
