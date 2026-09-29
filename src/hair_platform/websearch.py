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
번지면 안 된다)."""

from __future__ import annotations

import re

DEFAULT_LANG = "ko"
_ENDPOINT_TEMPLATE = "https://{lang}.wikipedia.org/w/api.php"
_TIMEOUT_SECONDS = 10.0
# 실측으로 발견(2026-09-29, 라이브 검증) — User-Agent 없이 호출하면
# Wikipedia가 403("Please set a user-agent and respect our robot
# policy")으로 거부한다. 정책이 요구하는 형식(앱 이름/버전 + 연락 수단
# 또는 프로젝트 URL)을 따른다: https://w.wiki/4wJS 참고.
_USER_AGENT = "HAIR-Platform/1.0 (internal collaboration-platform demo project; no public URL)"


def _strip_html(text: str) -> str:
    """검색 스니펫에 섞인 `<span class="searchmatch">...</span>` 같은
    하이라이트 태그를 제거한다 — 프롬프트에 HTML 태그를 그대로 넣지
    않기 위해서다."""
    return re.sub(r"<[^>]+>", "", text)


def search(query: str, top_k: int = 3, lang: str = DEFAULT_LANG) -> list[dict]:
    """실제 외부 API 호출 지점 — Wikipedia 검색 결과를 반환한다.

    네트워크 실패(오프라인/타임아웃/API 오류)는 예외를 올리지 않고
    빈 리스트로 대체한다 — 외부 자료 검색은 "있으면 좋은" 보강이지
    Agent 실행 자체를 막을 이유가 아니다."""
    import httpx  # 지연 import — 이 경로를 안 쓰면 의존성 설치가 필요 없다.

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
    except (httpx.HTTPError, ValueError):
        return []

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


def context_for_prompt(query: str, top_k: int = 3, lang: str = DEFAULT_LANG) -> str:
    """LLM 프롬프트에 덧붙일 외부 자료 검색 결과 텍스트를 만든다
    (`graphquery.context_for_prompt()`와 같은 자리)."""
    hits = search(query, top_k=top_k, lang=lang)
    if not hits:
        return "관련된 외부 자료를 찾지 못했습니다."
    lines = [f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits]
    return "외부 자료 검색 결과(Wikipedia):\n" + "\n".join(lines)


def as_crewai_tool(conn=None, top_k: int = 3):
    """외부 자료 검색을 실제 CrewAI Tool로 감싼다(`vectorstore.py`/
    `graphquery.py`와 같은 패턴 — ADR 결정 12). `conn`은 다른 native
    tool과 시그니처를 맞추기 위한 것뿐 실제로 쓰지 않는다(외부 API
    호출이라 DB가 필요 없다) — `pool_registry.resolve_pool_tools()`가
    모든 native factory를 `factory(conn)`으로 동일하게 호출하기
    때문이다. `crewai`는 지연 import한다."""
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    @tool("외부 자료 검색")
    def search_external_sources(query: str) -> str:
        """조직 내부 데이터(과거 산출물·조직도)에 없는 일반 지식이나
        외부 사실 확인이 필요할 때 Wikipedia에서 검색한다. 내부 정보로
        충분하면 호출하지 마라."""
        return context_for_prompt(query, top_k=top_k)

    return search_external_sources
