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


def test_gc_context_for_prompt_distinguishes_search_failure_from_empty_results():
    """실측으로 발견한 버그(2026-10-26, "외부 자료 검색... 실제
    동작하는지 분석해줘") — Wikimedia가 서버 IP를 403으로 막은 상황에서
    재현: 지금까지는 "검색이 막혔다"와 "검색했는데 0건"이 똑같이
    "관련된 외부 자료를 찾지 못했습니다"로 보여서 LLM이 실패를 인지할
    방법이 없었다. 이번엔 `httpx.get` 자체를 mock해(`search()`를
    mock하지 않음) 실제 `_search_raw()` → `search(_raise_on_error=True)`
    경로를 그대로 태워 검증한다."""
    import httpx

    with patch("httpx.get", return_value=_mock_response({}, status_ok=False)):
        text = websearch.context_for_prompt("질문")
    assert "검색 실패" in text
    assert "찾지 못했습니다" not in text  # "0건"과 다른 문구여야 한다

    with patch("httpx.get", side_effect=httpx.ConnectError("no network")):
        text = websearch.context_for_prompt("질문")
    assert "검색 실패" in text

    # 대조군 — 진짜 0건(정상 응답, 검색 결과만 없음)은 기존 문구 그대로.
    with patch("httpx.get", return_value=_mock_response({"query": {"search": []}})):
        text = websearch.context_for_prompt("질문")
    assert "찾지 못했습니다" in text
    assert "검색 실패" not in text


def test_gc_throttle_enforces_minimum_interval_between_real_calls():
    """연속 호출 사이 최소 간격을 강제하는지 — 실제로 잠들지 않고
    `time.monotonic`/`time.sleep`만 mock해 로직만 검증한다(테스트
    속도 유지)."""
    clock = [1000.0]

    def fake_monotonic():
        return clock[0]

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        clock[0] += seconds

    # `_last_request_at`은 모듈 전역이라 테스트가 끝나도 합성 clock 값이
    # 그대로 남으면, 나중에 실제 `time.monotonic()`을 쓰는 다른 테스트가
    # (시스템 부팅 시점에 따라) 잘못된 양수 `wait`를 계산해 실제로 몇
    # 분씩 잠들 위험이 있다 — 반드시 원래 값(0.0, 모듈 로드 시 기본값)
    # 으로 되돌린다.
    try:
        with patch("hair_platform.websearch.time.monotonic", side_effect=fake_monotonic):
            websearch._last_request_at = 0.0  # 이전 테스트의 잔여 상태 제거
            with patch("hair_platform.websearch.time.sleep", side_effect=fake_sleep):
                websearch._throttle()  # 마지막 호출 이후 한참 지남 — 안 기다림
                assert sleep_calls == []
                websearch._throttle()  # 바로 다음 호출 — 최소 간격만큼 기다려야 함
                assert len(sleep_calls) == 1
                assert sleep_calls[0] == websearch._MIN_REQUEST_INTERVAL_SECONDS
    finally:
        websearch._last_request_at = 0.0


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
