"""사전에 없는 한국어 낱말 탐지(2026-11-25, 사용자 요청 "한국어 사전 기반으로 이상한 낱말 탐지").

실측(계약서·영업 파이프라인, 로컬 35B): 모델이 "이모니즘"·"분인 처리"·"순현나름"처럼 한국어 글자로 된 **뜻 없는
낱말**을 지어냈다. 한자·외국 문자 교정(`language_qa`)은 이런 낱말을 못 잡고, 용어 확인(`text_checks.term_consistency`)은
근거 자료의 낱말과 한 음절 다른 경우만 잡는다.

방법 — 형태소 분석기 Kiwi(`kiwipiepy`, 선택 설치 `korean` extra)의 **사전에 없는 형태소(OOV)**를 찾되, 오탐을 줄이려고
아래는 건너뛴다. 실측(2026-11-25): 지어낸 낱말 3/3 검출, 실측 결과물 8건에서 오탐 2건("당월"·"별지"), 정상 문서
오탐 이 플랫폼 문서 0.12~0.20건/1,000자·다른 프로젝트 문서 0~0.70건/1,000자(외래 기술 용어가 많을수록 늘어남).
분석기는 메모리를 약 360MB 쓰고(검사 1건 약 0.02초), 플랫폼 어휘 첫 빌드는 10~20초라 디스크에 캐시하고 `hair serve`
기동 때 미리 만든다. `HAIR_LEXICON_CHECK=0`으로 끌 수 있다.

- 사전 낱말로 나뉘는 합성어(지식재산=지식+재산, 미집행=미+집행, 팀이름=팀+이름) — 단 두 조각이 모두 한 음절이면
  합성어로 보지 않는다("분인"=분+인, "순현"=순+현은 잡는다)
- 조사·어미를 떼면 사전 낱말인 것(카드의·배정이·폼의)
- 플랫폼 자체 어휘(화면 문구·기본 스킬 지침·기본 Agent·파이프라인·도메인 템플릿)와 근거 자료(요청·앞 단계 결과·도구
  결과)에 나온 낱말·어절 — 빌더·웹훅·인박스·임베딩 같은 외래어와 고객사 고유명사
- 한 문서에 3번 이상 나온 낱말(의도한 용어)

한계: 다른 실제 낱말로 바뀐 오타("발표 노트"→"발포 노트", "직접 손해"→"직손해")는 사전에 있는 낱말이라 못 잡는다
(그건 `term_consistency`가 근거와 대조해 일부 잡는다). 결과는 **참고용**이다 — Gate 통과 판정·인박스 경고·결과물 요약
배지 집계에는 넣지 않고(`advisory`), 낱말 목록만 노란 배지로 보여 사람이 확인하게 한다.
"""

from __future__ import annotations

import functools
import re
import threading
from typing import Any

_HANGUL_WORD = re.compile(r"^[가-힣]{2,}$")
_HANGUL_RUN = re.compile(r"[가-힣]+")
_MIN_HANGUL_CHARS = 100  # 이보다 짧은 글은 재지 않는다(문맥이 없어 오탐만 늘어남)
_REPEAT_AS_TERM = 3  # 한 문서에 이만큼 나오면 의도한 용어로 본다
MAX_WORDS = 10

# 합성어 조각으로 인정할 한 음절 접두·접미(사전 단독 분석이 애매한 것들).
_PREFIX = set("미비재무당총오직반초신구전후부대소고저역탈피")
_SUFFIX = set("식율률성화적별팀간금비량액자형제류표용상하내외권층")
# 떼어 보고 남은 줄기를 다시 볼 조사·어미(긴 것부터).
_ENDINGS = tuple(sorted(
    ["의", "이", "가", "을", "를", "은", "는", "에", "에서", "로", "으로", "과", "와", "도", "만", "인", "이다", "입니다",
     "합니다", "니다", "에게", "께", "까지", "부터", "처럼", "보다", "이나", "나", "란", "라는", "이라는", "하는", "하고",
     "하여", "해", "한", "된", "되는", "적", "들", "기", "함", "됨"],
    key=len, reverse=True,
))

_LOCK = threading.Lock()
_KIWI: Any = None
_KIWI_ERROR: str | None = None
_VOCAB: tuple[frozenset[str], frozenset[str]] | None = None


def enabled() -> bool:
    """`HAIR_LEXICON_CHECK=0`이면 끈다 — 분석기가 메모리를 약 360MB 쓰므로(실측) 로컬 대형 모델과 메모리를 다툴 때."""
    import os

    if os.environ.get("HAIR_LEXICON_CHECK", "1") == "0":
        return False
    # 한국어 결과물 전용(2026-11-25) — 조직 결과물 언어가 한국어가 아니면 끈다(외국어 결과물을 "사전에 없는 낱말"로 오탐).
    try:
        from hair_platform import org_policy

        return org_policy.output_language() == org_policy.DEFAULT_OUTPUT_LANGUAGE
    except Exception:  # noqa: BLE001 — 정책을 못 읽으면 기본(한국어)으로 본다
        return True


def available() -> bool:
    return enabled() and _kiwi() is not None


def warm_up_in_background() -> bool:
    """서버 기동 때 분석기와 플랫폼 어휘를 미리 만든다(첫 빌드 10~20초를 첫 결과물 검사가 기다리지 않게).
    분석기가 없거나 꺼져 있으면 아무것도 안 한다."""
    if not enabled():
        return False
    try:
        import importlib.util

        if importlib.util.find_spec("kiwipiepy") is None:
            return False
    except (ImportError, ValueError):
        return False

    def _run() -> None:
        try:
            if _kiwi() is not None:
                _platform_vocab()
        except Exception:  # noqa: BLE001, S110 — 미리 만들기 실패는 첫 검사 때 다시 시도된다
            pass

    threading.Thread(target=_run, name="hair-lexicon-warmup", daemon=True).start()
    return True


def _kiwi():
    global _KIWI, _KIWI_ERROR
    if not enabled():
        return None
    if _KIWI is not None or _KIWI_ERROR is not None:
        return _KIWI
    with _LOCK:
        if _KIWI is None and _KIWI_ERROR is None:
            try:
                from kiwipiepy import Kiwi  # 선택 설치(`pip install "hair-platform[korean]"`)

                _KIWI = Kiwi()
            except Exception as exc:  # noqa: BLE001 — 없거나 모델을 못 읽으면 "미평가"
                _KIWI_ERROR = f"{type(exc).__name__}: {exc}"[:200]
    return _KIWI


def _tokens(text: str) -> list:
    kiwi = _kiwi()
    return list(kiwi.tokenize(text)) if kiwi is not None and text else []


@functools.lru_cache(maxsize=50_000)
def _known(word: str) -> bool:
    """사전에 있는 낱말(형태소 하나로 분석되고 OOV가 아님)."""
    toks = _tokens(word)
    return len(toks) == 1 and not toks[0].oov and toks[0].form == word


def _part_ok(part: str, one_syllable: set[str]) -> bool:
    return _known(part) or (len(part) == 1 and part in one_syllable)


@functools.lru_cache(maxsize=50_000)
def _decomposable(word: str) -> bool:
    """사전 낱말 두 조각(이상)으로 나뉘는 합성어인가 — 적어도 한 조각은 두 음절 이상이어야 한다."""
    for i in range(1, len(word)):
        a, b = word[:i], word[i:]
        if len(a) == 1 and len(b) == 1:
            continue
        if _part_ok(a, _PREFIX) and _part_ok(b, _SUFFIX):
            return True
        if len(a) >= 2 and _known(a) and len(b) >= 3 and _decomposable(b):
            return True
    return False


def _strip_endings(word: str) -> list[str]:
    return [word[: -len(e)] for e in _ENDINGS if word.endswith(e) and len(word) > len(e)]


def _word_ok(word: str, vocab: frozenset[str] | set[str]) -> bool:
    if word in vocab or _known(word) or _decomposable(word):
        return True
    # 줄기는 두 음절 이상만 본다 — "분인"에서 "인"을 떼면 "분"(사전 낱말)이 남아 지어낸 낱말을 놓쳤다.
    return any(len(st) >= 2 and (st in vocab or _known(st) or _decomposable(st)) for st in _strip_endings(word))


def _platform_corpus() -> str:
    """플랫폼 자체 어휘의 원천 — 화면 문구(이용 가이드 포함)·기본 스킬 지침·기본 Agent·파이프라인·도메인 템플릿."""
    parts: list[str] = []
    try:
        from hair_platform import webui

        parts.append(webui.INDEX_HTML)
    except Exception:  # noqa: BLE001, S110 — 어휘가 줄 뿐 검사는 계속한다
        pass
    try:
        from hair_platform import skill_catalog

        for guide in skill_catalog.SKILL_GUIDES.values():
            parts.extend(str(getattr(guide, f) or "") for f in skill_catalog.FIELDS)
    except Exception:  # noqa: BLE001, S110
        pass
    for mod in ("reference_pipeline_seeds", "domain_packs", "pool_registry"):
        try:
            import importlib
            import inspect

            parts.append(inspect.getsource(importlib.import_module(f"hair_platform.{mod}")))
        except Exception:  # noqa: BLE001, S110
            pass
    text = "\n".join(parts)
    # 화면·가이드에 "잘못된 예"로 적은 낱말("순현금흐름"→"순현나름")을 어휘로 배우지 않게 화살표 오른쪽 인용은 뺀다.
    text = _WRONG_EXAMPLE.sub(" ", text)
    return "\n".join(line for line in text.splitlines() if _HANGUL_RUN.search(line))


_WRONG_EXAMPLE = re.compile(r"(?:→|->)\s*[\"'“‘「][^\"'”’」\n]{1,40}[\"'”’」]")


def _cache_path(corpus: str):
    import hashlib
    import os
    import pathlib

    base = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache") / "hair-platform"
    digest = hashlib.sha1(corpus.encode()).hexdigest()[:16]
    return base / f"lexicon-vocab-{digest}.json"


def _vocab_from(text: str) -> tuple[set[str], set[str]]:
    """(OOV 형태소, 어절과 조사를 뗀 어절) — 이 글에서 쓰인 낱말이면 정상으로 본다."""
    oov = {t.form for t in _tokens(text) if t.oov and _HANGUL_WORD.match(t.form)}
    runs: set[str] = set()
    for run in _HANGUL_RUN.findall(text):
        if len(run) >= 2:
            runs.add(run)
            runs.update(st for st in _strip_endings(run) if len(st) >= 2)
    return oov, runs


def _platform_vocab() -> tuple[frozenset[str], frozenset[str]]:
    """플랫폼 어휘 — 처음 만들 때 수십 초가 걸려(화면·스킬·시드 문구 수십만 자 분석) 내용 해시로 디스크에 캐시한다
    (`~/.cache/hair-platform/`). 화면 문구·스킬이 바뀌면 해시가 달라져 다시 만든다."""
    global _VOCAB
    if _VOCAB is not None:
        return _VOCAB
    import json

    corpus = _platform_corpus()
    path = _cache_path(corpus)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _VOCAB = (frozenset(data["oov"]), frozenset(data["runs"]))
        return _VOCAB
    except (OSError, ValueError, KeyError):
        pass
    oov, runs = _vocab_from(corpus)
    _VOCAB = (frozenset(oov), frozenset(runs))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"oov": sorted(oov), "runs": sorted(runs)}, ensure_ascii=False), encoding="utf-8")
    except OSError:  # 캐시를 못 써도 검사는 된다(다음 기동 때 다시 만든다)
        pass
    return _VOCAB


def odd_words(text: str, evidence: str | None = None) -> list[dict] | None:
    """사전에 없는 낱말 목록(`[{"word", "context"}]`, 최대 10개). 분석기가 없거나 글이 짧으면 None(미평가)."""
    if not text or len(_HANGUL_RUN.sub("", text)) == len(text):
        return None
    if sum(len(r) for r in _HANGUL_RUN.findall(text)) < _MIN_HANGUL_CHARS or _kiwi() is None:
        return None
    p_oov, p_runs = _platform_vocab()
    e_oov, e_runs = _vocab_from(evidence or "")
    vocab = set(p_oov) | e_oov
    runs = p_runs | e_runs
    tokens = _tokens(text)
    counts: dict[str, int] = {}
    for t in tokens:
        if t.oov:
            counts[t.form] = counts.get(t.form, 0) + 1
    found: list[dict] = []
    seen: set[str] = set()
    for t in tokens:
        word = t.form
        if not t.oov or not _HANGUL_WORD.match(word) or word in seen:
            continue
        seen.add(word)
        if counts.get(word, 0) >= _REPEAT_AS_TERM or _word_ok(word, vocab):
            continue
        # 이 형태소가 들어 있는 어절 — 어절(또는 조사를 뗀 어절)이 플랫폼·근거 어휘면 정상(파이프라인→"파이프라"+"인").
        start, end = t.start, t.start + t.len
        while start > 0 and "가" <= text[start - 1] <= "힣":
            start -= 1
        while end < len(text) and "가" <= text[end] <= "힣":
            end += 1
        eojeol = text[start:end]
        if eojeol in runs or any(st in runs for st in _strip_endings(eojeol)):
            continue
        context = text[max(0, start - 15): min(len(text), end + 15)].replace("\n", " ").strip()
        found.append({"word": word, "context": context})
        if len(found) >= MAX_WORDS:
            break
    return found


def gate_score(text: str, evidence: str | None = None) -> dict:
    """참고용 Gate(`lexicon_check`) — 통과 판정·인박스 경고에는 넣지 않는다(`advisory`, `passed`는 항상 True)."""
    if not enabled():
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True, "advisory": True,
                "reason": "꺼짐(HAIR_LEXICON_CHECK=0)", "words": []}
    if _kiwi() is None:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True, "advisory": True,
                "reason": "형태소 분석기(kiwipiepy) 미설치 — pip install \"hair-platform[korean]\"", "words": []}
    words = odd_words(text, evidence)
    if words is None:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True, "advisory": True,
                "reason": "한국어 본문이 짧아 재지 않음", "words": []}
    return {
        "score": round(max(0.0, 1 - 0.2 * len(words)), 3),
        "status": "warn" if words else "pass",
        "passed": True,
        "advisory": True,
        "not_measured": False,
        "words": words,
    }
