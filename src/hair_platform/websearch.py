"""외부 자료 검색 — 사용자 요청(2026-09-29, "도구/스킬 Pool에 외부 자료
검색이 가능한 API(가능한 무료)를 도구화해서 사용 가능하도록 만들어
추가해줘").

VectorDB(과거 산출물 검색)·GraphDB(조직 관계 조회)는 둘 다 **조직 내부
데이터**만 다룬다 — "이 정책이 업계 표준과 비교해 어떤지" 같은 조직
밖 일반 지식이 필요한 질문엔 둘 다 답을 못 한다. Wikipedia 공개 검색
API(`action=query&list=search`)를 쓴다 — API 키 등록·과금 없이 바로
쓸 수 있고(요청한 "가능한 무료" 조건에 정확히 맞음), 응답이 안정적인
JSON이라 파싱이 간단하다. `vectorstore.py`/`graphquery.py`와 같은
모양(`search()`/`context_for_prompt()`/`as_crewai_tool()`)을 그대로
따라 `agent_runtime.py`(정적 프롬프트 주입)·`pool_registry.py`(진짜
CrewAI Tool) 양쪽에 동일한 패턴으로 연결한다.

**비용 발생 지점 없음**: Wikipedia API는 무료·키 불필요라 이 모듈
호출 자체는 비용이 없다 — 다만 실제 네트워크 호출이므로 실패할 수
있고(오프라인, API 장애, 타임아웃), 실패 시 빈 결과로 조용히 대체한다
(LLM 호출 자체를 막지 않는다 — 검색 실패가 전체 Agent 실행 실패로
번지면 안 된다).

**실측으로 발견한 버그(2026-10-26, "외부 자료 검색... 실제 동작하는지
분석해줘")**: 위 "실패 시 빈 결과로 대체"가 `search()`뿐 아니라
`context_for_prompt()`에도 그대로 번져, Wikimedia가 이 서버 IP를
robot policy 위반으로 403 차단한 상황에서도 LLM 프롬프트엔 "관련된
외부 자료를 찾지 못했습니다"(검색은 됐는데 0건)라고만 떴다 — "검색
자체가 막혔다"와 "검색했는데 없다"를 구분할 방법이 전혀 없어서,
Agent가 빈손으로 답을 지어내면서도 겉으로는 정상 동작처럼 보였다.
`_search_raw()`가 실패 이유를 `WebSearchError`로 올리게 하고,
`context_for_prompt()`는 그 예외를 직접 잡아 "검색 실패"를 "결과
0건"과 다른 문구로 LLM에 전달한다(`search()`는 하위 호환을 위해
여전히 실패 시 빈 리스트로 조용히 대체하는 기존 계약을 유지한다 —
실패 사유가 필요한 호출부만 `context_for_prompt()`나 `_search_raw()`를
직접 쓴다).

**요청 간 최소 간격(같은 조사에서 함께 발견)**: 위 차단의 유력한
원인이 이 세션에서 테스트 중 간격 없이 반복 호출한 것으로 추정돼,
재발 방지로 모든 실제 호출 앞에 `_throttle()`을 둬 연속 호출 사이
최소 간격(`_MIN_REQUEST_INTERVAL_SECONDS`)을 강제한다."""

from __future__ import annotations

import re
import time

DEFAULT_LANG = "ko"
_ENDPOINT_TEMPLATE = "https://{lang}.wikipedia.org/w/api.php"
_TIMEOUT_SECONDS = 10.0
# 실측으로 발견(2026-09-29, 라이브 검증) — User-Agent 없이 호출하면
# Wikipedia가 403("Please set a user-agent and respect our robot
# policy")으로 거부한다. 정책이 요구하는 형식(앱 이름/버전 + 연락 수단
# 또는 프로젝트 URL)을 따른다: https://w.wiki/4wJS 참고.
_USER_AGENT = "HAIR-Platform/1.0 (internal collaboration-platform demo project; no public URL)"
# 실측으로 발견(2026-10-26) — 짧은 간격으로 연속 호출하면 Wikimedia
# 안티봇이 IP 단위로 403을 내리기 시작한다(User-Agent·언어와 무관하게
# ko/en 둘 다 차단되는 것까지 확인). 분당 몇백 건을 보내는 게 아니라
# 이 프로젝트 성격상(사람이 대화창에서 쓰는 속도) 1초 간격이면 충분히
# 안전하면서도 체감 지연은 거의 없다.
_MIN_REQUEST_INTERVAL_SECONDS = 1.0
_last_request_at = 0.0


class WebSearchError(Exception):
    """`_search_raw()`가 실제 네트워크/HTTP/파싱 실패를 알리는 예외 —
    `search()`는 하위 호환을 위해 이걸 삼켜 빈 리스트로 돌려주지만,
    `context_for_prompt()`는 이 예외를 직접 잡아 "검색 실패"를 "검색
    성공·결과 0건"과 다른 문구로 만든다."""


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _strip_html(text: str) -> str:
    """검색 스니펫에 섞인 `<span class="searchmatch">...</span>` 같은
    하이라이트 태그를 제거한다 — 프롬프트에 HTML 태그를 그대로 넣지
    않기 위해서다."""
    return re.sub(r"<[^>]+>", "", text)


def _search_raw(query: str, top_k: int, lang: str) -> list[dict]:
    """실제 API 호출 지점 — 실패하면 조용히 삼키지 않고 `WebSearchError`
    를 올린다(호출부가 "실패"와 "0건"을 구분할 수 있게)."""
    import httpx  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

    _throttle()
    url = _ENDPOINT_TEMPLATE.format(lang=lang)
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": top_k,
    }
    try:
        resp = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise WebSearchError(
            f"Wikipedia API가 {exc.response.status_code} 오류를 반환했습니다"
        ) from exc
    except httpx.HTTPError as exc:
        raise WebSearchError(f"Wikipedia API 접속에 실패했습니다: {exc}") from exc
    except ValueError as exc:
        raise WebSearchError(f"Wikipedia API 응답을 해석할 수 없습니다: {exc}") from exc

    results = []
    for item in data.get("query", {}).get("search", [])[:top_k]:
        title = item.get("title", "")
        results.append(
            {
                "title": title,
                "snippet": _strip_html(item.get("snippet", "")),
                "url": f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
            }
        )
    return results


def search(
    query: str,
    top_k: int = 3,
    lang: str = DEFAULT_LANG,
    *,
    _raise_on_error: bool = False,
) -> list[dict]:
    """실제 외부 API 호출 지점 — Wikipedia 검색 결과를 반환한다.

    네트워크 실패(오프라인/타임아웃/API 오류)는 기본적으로 예외를
    올리지 않고 빈 리스트로 대체한다 — 외부 자료 검색은 "있으면 좋은"
    보강이지 Agent 실행 자체를 막을 이유가 아니다.

    `_raise_on_error`(내부용, `context_for_prompt()` 전용)는
    `WebSearchError`를 그대로 올린다 — 호출부가 "실패"와 "0건"을
    구분해야 할 때만 쓴다. 테스트가 `hair_platform.websearch.search`
    자체를 mock으로 바꿔치기하는 기존 방식(`patch(...).return_value`)과
    호환되도록 일부러 `search()`를 그대로 두고 플래그만 추가했다 —
    `context_for_prompt()`가 `_search_raw()`를 직접 부르게 바꿨더니
    기존에 `search()`를 mock하던 테스트 3개가 전부 실제 네트워크를
    타버린 걸 재현으로 발견하고 이 방식으로 고쳤다(mock은 인자를
    가리지 않고 설정된 값을 그대로 돌려주므로, 이 플래그와 무관하게
    계속 그대로 작동한다)."""
    try:
        return _search_raw(query, top_k, lang)
    except WebSearchError:
        if _raise_on_error:
            raise
        return []


def context_for_prompt(query: str, top_k: int = 3, lang: str = DEFAULT_LANG) -> str:
    """LLM 프롬프트에 덧붙일 외부 자료 검색 결과 텍스트를 만든다
    (`graphquery.context_for_prompt()`와 같은 자리). 검색 자체가
    실패한 경우(네트워크/HTTP 오류)는 "결과 0건"과 다른 문구를 반환해,
    LLM이 "자료가 없다"를 "검색이 막혔다"로 착각해 답을 지어내지
    않게 한다."""
    try:
        hits = search(query, top_k=top_k, lang=lang, _raise_on_error=True)
    except WebSearchError as exc:
        return (
            f"(외부 자료 검색 실패 — {exc}. 이건 \"관련 자료가 없다\"는 뜻이 "
            "아니라 검색 자체가 되지 않았다는 뜻입니다 — 이 사실을 사용자에게 "
            "명시적으로 알리고, 검색 결과를 추측하거나 지어내지 마세요.)"
        )
    if not hits:
        return "관련된 외부 자료를 찾지 못했습니다."
    lines = [f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits]
    return "외부 자료 검색 결과(Wikipedia):\n" + "\n".join(lines)


def as_crewai_tool(conn=None, top_k: int = 3):
    """외부 자료 검색을 실제 CrewAI Tool로 감싼다(`vectorstore.py`/
    `graphquery.py`와 같은 패턴). `conn`은 다른 native
    tool과 시그니처를 맞추기 위한 것뿐 실제로 쓰지 않는다(외부 API
    호출이라 DB가 필요 없다) — `pool_registry.resolve_pool_tools()`가
    모든 native factory를 `factory(conn)`으로 동일하게 호출하기
    때문이다. `crewai`는 지연 import한다."""
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    # 사용자 보고(2026-10-06, 실제 크루 실행에서 "OpenAI function name
    # cannot be empty" 크래시) — 설치된 crewai 버전의 `sanitize_tool_name()`
    # 이 함수명을 ASCII로 정규화하며 비-ASCII 문자를 전부 버린다(`name.
    # encode("ascii", "ignore")`). 한글만으로 된 이름("외부 자료 검색")은
    # 빈 문자열이 돼 즉시 예외가 난다 — Tool Pool 한글 이름은 그대로 두고
    # LLM에 실제로 전달되는 함수명만 ASCII로 바꾼다.
    @tool("external_knowledge_search")
    def search_external_sources(query: str) -> str:
        """조직 내부 데이터(과거 산출물·조직도)에 없는 일반 지식이나
        외부 사실 확인이 필요할 때 Wikipedia에서 검색한다. 내부 정보로
        충분하면 호출하지 마라."""
        return context_for_prompt(query, top_k=top_k)

    return search_external_sources
