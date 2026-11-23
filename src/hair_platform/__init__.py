"""HAIR-Platform — 사람·AI Agent 협업 업무 플랫폼."""


def get_version() -> str:
    """설치된 패키지 버전 — `hair --version`과 웹 UI 상단 표기가 같은 값을 쓴다(2026-11-23).
    정규 소스는 `pyproject.toml`의 `version`이며, 설치 메타데이터에서 읽는다(편집 설치는
    `pip install -e .`를 다시 해야 갱신된다). 설치되지 않은 소스 실행이면 "0.0.0"."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("hair-platform")
    except PackageNotFoundError:
        return "0.0.0"


GRAPH_ENGINE_INSTALL_HINT = (
    "LangGraph 실행엔진 의존성이 설치돼 있지 않습니다 — pip install 'hair-platform[graph]' "
    "(또는 [all])로 설치한 뒤 다시 시도하세요."
)


def graph_engine_available() -> bool:
    """LangGraph 실행엔진(선택 extra `graph`)이 설치돼 있는지(2026-11-23 pip 설치 점검) — 기본 설치만
    한 환경에서 그래프 엔진을 켜면 `ModuleNotFoundError`가 500으로 새고, 업무 실행 경로는 실행
    행만 만들어진 채 'running'으로 남았다. 쓰기 전에 이 함수로 확인하고 설치 안내를 돌려준다."""
    from importlib.util import find_spec

    try:
        return find_spec("langgraph") is not None and find_spec("langgraph.checkpoint.sqlite") is not None
    except ModuleNotFoundError:
        return False


CREWAI_INSTALL_HINT = (
    "CrewAI 의존성이 설치돼 있지 않습니다 — pip install 'hair-platform[crewai]'(또는 [all])로 "
    "설치하거나, 생성 방식을 simple_llm/stub으로 고르세요."
)


def crewai_available() -> bool:
    """CrewAI(선택 extra `crewai`)가 설치돼 있는지(2026-11-23 pip 설치 점검)."""
    from importlib.util import find_spec

    return find_spec("crewai") is not None
