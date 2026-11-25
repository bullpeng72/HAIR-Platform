"""토큰 기반 컨텍스트 예산 관리 — 사용자 요청(2026-11-18, "대화에서
업무처리시 Tool, skill 활용, 메시지 길이 관리에 제약사항이 많아... 엔터
프라이즈 환경 적용이 가능한 수준의 확실한 개선 방안을 도출해줘") 개선축
B(세 축 중 최우선으로 합의된 것).

실측 근거 — 대화 #14/#20에서 엑셀 첨부파일 1개(19,321자)만으로 로컬
모델(ornith, 32,768토큰 한도)이 15분 가까이 돌고도 빈 응답을 낸 사고
(2026-11-18). 원인: `conversations._build_transcript()`가 최근 메시지
'개수'만 제한하고(`_CONTEXT_MESSAGE_LIMIT`), 그 안의 내용은 토큰 수와
무관하게 전부 원문 그대로 프롬프트에 들어갔다 — 모델이 한도를 넘기기
전에는 아무도 몰랐다.

이 모듈은 "모델이 거부하기 전에 미리 안전하게 잘라낸다"는 한 가지
역할만 한다 — 실제로 잘려 나간 내용을 더 똑똑하게 보존하는 일(관련
있는 부분만 검색해 넣기)은 개선축 C(메모리/RAG)의 몫이다.
"""

from __future__ import annotations

import functools
import sqlite3


class _CharEncoding:
    """tiktoken 인코딩 표를 못 받을 때의 대체 — 글자 1개 = 토큰 1개로 센다. 한글 위주 실측 비율(cl100k 0.63
    토큰/글자)보다 크게 세므로 "넘칠 것 같으면 미리 자르는" 이 모듈의 목적상 안전한 쪽(더 일찍 자름)이다."""

    def encode(self, text: str) -> list[str]:
        return list(text)

    def decode(self, tokens: list[str]) -> str:
        return "".join(tokens)


@functools.lru_cache(maxsize=1)
def _encoding():
    """cl100k_base — 처음 쓸 때 불러온다(2026-11-25). 예전에는 import 시점에 불러와, 인코딩 표가 캐시에 없고
    인터넷이 막힌 서버(사내 폐쇄망)에서는 tiktoken이 표를 내려받지 못해 **서버 시작(import) 자체가 실패**했다
    (CI에서 외부 네트워크를 막자 드러남). 못 불러오면 `_CharEncoding`으로 대신한다."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 — 네트워크·캐시 문제로 토큰 예산 기능이 서버를 막지 않게
        return _CharEncoding()

# 실측 보정(2026-11-18) — 대화 #14 전체(101,409자, 한글 위주 혼합 콘텐츠)
# 기준 cl100k_base 추정치는 63,899토큰(비율 0.630)이었다. 반면 같은
# 대화에서 실제 Ollama 요청이 한 번 토큰 한도 초과로 거부됐을 때(job 61,
# 2026-11-18) 서버가 보고한 실측 토큰 수는 33,839였고, 그 시점의
# system+transcript 길이는 34,417자(비율 0.983)였다 — cl100k_base가
# 이 로컬 모델의 실제 토큰화보다 상당히 적게 센다는 뜻이다("덜 세서
# 넘치는" 쪽은 안전하지 않고 "더 세서 여유 있게 자르는" 쪽이 안전하므로)
# 0.983 / 0.630 ≈ 1.56을 올림한 1.6을 안전 배수로 둔다.
_OLLAMA_SAFETY_MULTIPLIER = 1.6

# provider별 보수적 기본값 — `model_registry.context_window`가
# NULL(admin이 아직 등록 안 함)일 때만 쓰인다. 실제보다 작게 잡는 쪽이
# 안전하므로, 확실히 아는 하한값(클라우드는 공개된 최소 사양, Ollama는
# 가장 작은 흔한 설정)으로 보수적으로 둔다 — 관리자가 "모델 레지스트리"
# 화면에서 실제 값을 등록하면 그 값이 항상 우선한다.
DEFAULT_CONTEXT_WINDOW_BY_PROVIDER: dict[str, int] = {
    "anthropic": 200_000,
    "openai": 128_000,
    "ollama": 8_192,
}

# 입력(시스템 프롬프트+대화록)에 쓸 수 있는 비율 — 나머지는 모델이 실제로
# 답변을 생성할 토큰(출력)과 chat 템플릿/특수 토큰 오버헤드를 위한 여유분.
# 2026-11-18 사고(19KB 첨부 하나가 입력의 거의 전부를 차지해 출력 여유가
# 0에 가까워져 빈 응답)가 정확히 이 여유를 안 두어서 생긴 문제였다.
_INPUT_BUDGET_RATIO = 0.6
_FIXED_SAFETY_MARGIN_TOKENS = 256
_OMISSION_MARKER = "\n...(길이 제한으로 이하 생략 — 전체 내용은 대화 화면에서 확인하세요)"


def count_tokens(text: str, provider: str) -> int:
    """`provider`의 실제 토크나이저가 아니라 cl100k_base 근사치 — Ollama는
    위 실측 보정 배수를 곱해 과소추정을 보정한다. 정확한 과금 계산이
    아니라 "넘길 것 같으면 미리 잘라내는" 안전장치 목적이라, 다소
    보수적으로(실제보다 크게) 세는 쪽을 택한다."""
    if not text:
        return 0
    base = len(_encoding().encode(text))
    if provider == "ollama":
        return int(base * _OLLAMA_SAFETY_MULTIPLIER)
    return base


def resolve_context_window(
    conn: sqlite3.Connection, provider: str, model: str
) -> int:
    """admin이 "모델 레지스트리"에 등록한 실제 값(`model_registry.
    context_window`)을 우선 쓰고, 없으면 provider별 보수적 기본값으로
    폴백한다."""
    row = conn.execute(
        "SELECT context_window FROM model_registry WHERE provider = ? AND name = ?",
        (provider, model),
    ).fetchone()
    if row is not None and row["context_window"]:
        return row["context_window"]
    return DEFAULT_CONTEXT_WINDOW_BY_PROVIDER.get(provider, 8_192)


def input_budget_tokens(
    conn: sqlite3.Connection, provider: str, model: str, *, reserved_tokens: int = 0
) -> int:
    """대화록(transcript)에 쓸 수 있는 토큰 예산 — 전체 컨텍스트 윈도우의
    `_INPUT_BUDGET_RATIO`만큼에서, 이미 확정된 다른 소비처(시스템
    프롬프트 등, `reserved_tokens`로 호출자가 미리 세어 넘김)와 고정
    안전 여유분을 뺀 값. 음수가 되면 0으로 바닥을 둔다(그래도 호출
    자체는 막지 않는다 — 최소한의 내용은 넣어야 모델이 뭘 요청받았는지
    알 수 있으므로, 완전히 막는 판단은 호출자의 몫으로 남긴다)."""
    window = resolve_context_window(conn, provider, model)
    budget = int(window * _INPUT_BUDGET_RATIO) - reserved_tokens - _FIXED_SAFETY_MARGIN_TOKENS
    return max(budget, 0)


def fit_text_to_budget(text: str, provider: str, budget_tokens: int) -> str:
    """`text`가 `budget_tokens`를 넘으면 **뒷부분**을 잘라내고 생략
    표시를 붙인다(앞부분을 우선 보존 — 대화록에서는 오래된 메시지가
    앞에 오므로 이 함수 자체보다 호출자의 "최근 메시지부터 채우기"
    전략이 더 중요하지만, 메시지 1건 자체가 예산보다 큰 경우— 예:
    거대한 첨부파일 1개 — 를 위한 마지막 안전장치다)."""
    if budget_tokens <= 0:
        return ""
    multiplier = _OLLAMA_SAFETY_MULTIPLIER if provider == "ollama" else 1.0
    cl100k_budget = max(0, int(budget_tokens / multiplier))
    encoded = _encoding().encode(text)
    if len(encoded) <= cl100k_budget:
        return text
    # 버그 수정 — 생략 표시 문구 자체도 토큰을 쓰므로, 본문 절삭 길이에서
    # 그만큼을 미리 빼야 "본문+표시 문구" 합계가 예산을 넘지 않는다
    # (실측: 안 빼면 꾸준히 몇 % 초과했다).
    marker_tokens = len(_encoding().encode(_OMISSION_MARKER))
    body_budget = max(0, cl100k_budget - marker_tokens)
    truncated = _encoding().decode(encoded[:body_budget])
    return truncated + _OMISSION_MARKER


def fit_lines_to_budget(lines: list[str], provider: str, budget_tokens: int) -> str:
    """`lines`(시간순, 오래된 것이 앞)를 **최신 메시지부터** 거꾸로 채워
    넣다가 예산을 넘기면 멈춘다 — 오래된 메시지가 먼저 잘리는 게 최근
    맥락이 먼저 잘리는 것보다 안전하다는 기존 `_CONTEXT_MESSAGE_LIMIT`
    (개수 기준 절삭)와 같은 우선순위 원칙을 토큰 기준으로 그대로
    옮긴 것이다. 최신 메시지 1건만으로도 예산을 넘기면(예: 거대한 첨부
    파일), 드롭하지 않고 `fit_text_to_budget()`로 잘라서라도 넣는다 —
    가장 최근 요청은 내용이 없으면 모델이 아예 무엇을 할지 알 수 없기
    때문이다."""
    if budget_tokens <= 0 or not lines:
        return ""
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        remaining = budget_tokens - used
        if remaining <= 0:
            break
        line_tokens = count_tokens(line, provider)
        if line_tokens <= remaining:
            kept.append(line)
            used += line_tokens
        else:
            kept.append(fit_text_to_budget(line, provider, remaining))
            used = budget_tokens
            break
    kept.reverse()
    return "\n".join(kept)
