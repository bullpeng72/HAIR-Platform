"""Phase 22 — 외부 자료 검색 도구화(사용자 요청, 2026-09-29, "도구/스킬
Pool에 외부 자료 검색이 가능한 API(가능한 무료)를 도구화해서 사용
가능하도록 만들어 추가해줘").

VectorDB(과거 산출물 검색)·GraphDB(조직 관계 조회)는 둘 다 조직 내부
데이터만 다뤄, "조직 밖 일반 지식"이 필요한 질문엔 답을 못 하던 갭을
메운다. Wikipedia 공개 검색 API(API 키 불필요·무료)를 `websearch.py`로
감싸고, `vectorstore.py`/`graphquery.py`와 같은 모양(`search()`/
`context_for_prompt()`/`as_crewai_tool()`)으로 `agent_runtime.py`(정적
주입)·`pool_registry.py`(진짜 CrewAI Tool) 양쪽에 연결한다.

이 파일은 `websearch.py` 자체의 순수 단위 테스트다(DB 불필요) — 실제
네트워크 호출은 전부 mock해 오프라인으로 검증한다(이 프로젝트의
"골든셋은 실제 LLM/외부 API 호출 없이 전부 mock" 원칙 그대로)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hair_platform import websearch


def _mock_response(payload: dict, status_ok: bool = True) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
    return resp


def test_gc191_search_parses_wikipedia_results_and_strips_html():
    payload = {
        "query": {
            "search": [
                {
                    "title": "원격근무",
                    "snippet": '<span class="searchmatch">원격근무</span>는 사무실 밖에서 일하는 방식이다.',
                },
                {"title": "재택근무", "snippet": "재택근무는 원격근무의 한 형태다."},
            ]
        }
    }
    with patch("httpx.get", return_value=_mock_response(payload)) as get_mock:
        hits = websearch.search("원격근무", top_k=2)

    assert get_mock.call_args.kwargs["params"]["srsearch"] == "원격근무"
    assert len(hits) == 2
    assert hits[0]["title"] == "원격근무"
    assert "<span" not in hits[0]["snippet"]
    assert "원격근무는 사무실 밖에서 일하는 방식이다." == hits[0]["snippet"]
    assert hits[0]["url"] == "https://ko.wikipedia.org/wiki/원격근무"


def test_gc192_search_returns_empty_list_on_network_or_http_failure():
    """오프라인/API 장애 시 예외를 올리지 않고 빈 리스트로 대체해야
    한다 — 외부 자료 검색 실패가 Agent 실행 전체를 막으면 안 된다."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("no network")):
        assert websearch.search("아무거나") == []

    with patch("httpx.get", return_value=_mock_response({}, status_ok=False)):
        assert websearch.search("아무거나") == []

    with patch("httpx.get", return_value=_mock_response({"unexpected": "shape"})):
        assert websearch.search("아무거나") == []


def test_gc193_context_for_prompt_formats_hits_or_reports_no_results():
    with patch(
        "hair_platform.websearch.search",
        return_value=[
            {"title": "A", "snippet": "설명A", "url": "https://x/A"},
        ],
    ):
        text = websearch.context_for_prompt("질문")
    assert "외부 자료 검색 결과(Wikipedia)" in text
    assert "A: 설명A (https://x/A)" in text

    with patch("hair_platform.websearch.search", return_value=[]):
        text = websearch.context_for_prompt("질문")
    assert "찾지 못했습니다" in text


def test_gc194_as_crewai_tool_wraps_context_for_prompt():
    """`conn` 인자를 받지만 실제로 쓰지 않는다(외부 API 호출이라 DB가
    필요 없음) — `pool_registry.resolve_pool_tools()`가 모든 native
    factory를 `factory(conn)`으로 동일하게 호출하기 때문에 시그니처만
    맞춘다."""
    tool = websearch.as_crewai_tool(conn=None, top_k=2)
    with patch(
        "hair_platform.websearch.context_for_prompt", return_value="검색 결과 텍스트"
    ) as ctx_mock:
        result = tool.func("질문 내용")
    ctx_mock.assert_called_once_with("질문 내용", top_k=2)
    assert result == "검색 결과 텍스트"
