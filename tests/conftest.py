"""모든 테스트 공통 — 실행 환경과 무관하게 같은 결과가 나오도록 격리한다(2026-11-25, P0-1).

GitHub CI가 40회 연속 실패한 원인: `create_app()`(과 crewai·litellm import)이 `load_dotenv()`로 작업 폴더의
`.env`를 읽어, 개발 PC에서는 `.env`의 `OPENAI_API_KEY` 등이 테스트에 몰래 들어갔다. 키가 없는 CI에서는 그 키에
기대던 테스트 142건이 실패했다("OPENAI_API_KEY is required" 등). 여기서:

1. `.env`를 읽지 않게 한다 — 이 파일은 다른 모든 import보다 먼저 실행되므로, 이후 `from dotenv import
   load_dotenv`로 가져가는 쪽(hair_platform.app·cli, crewai, litellm)은 모두 아무것도 하지 않는 함수를 받는다.
2. 셸에 설정된 API 키·provider·SMTP·벡터 저장 위치 같은 환경 의존 변수를 지운다.

키가 필요한 테스트는 `monkeypatch.setenv("OPENAI_API_KEY", "sk-test")`처럼 스스로 설정한다.
"""

from __future__ import annotations

import os

import dotenv


def _no_dotenv(*_args, **_kwargs) -> bool:
    return False


dotenv.load_dotenv = _no_dotenv  # type: ignore[assignment]

_ENV_DEPENDENT = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "PARALLEL_API_KEY",
    "HAIR_PLATFORM_LLM_PROVIDER",
    "HAIR_PLATFORM_LLM_MODEL",
    "HAIR_PLATFORM_SECRET_KEY",
    "HAIR_PLATFORM_DB_PATH",
    "HAIR_VECTOR_BACKEND",
    "HAIR_VECTOR_DIR",
    "HAIR_OLLAMA_AGENT_PLANNING",
    "HAIR_OLLAMA_MAX_OUTPUT_TOKENS",
)
for _name in list(os.environ):
    if _name in _ENV_DEPENDENT or _name.startswith("HAIR_SMTP_"):
        del os.environ[_name]

# 고정 가짜 키 — 많은 테스트가 "OpenAI 키가 있으면 openai로 자동 판별"하는 기본 경로를 전제로 쓰였다(개발 PC `.env`에
# 실제 키가 있었다). 어느 PC·CI에서도 같은 값이 되도록 가짜 값으로 고정한다. 실제 호출은 모두 mock이어야 하며,
# 가짜 키로 실패하는 테스트가 있다면 그 테스트가 외부 서비스를 실제로 호출하고 있다는 뜻이다.
os.environ["OPENAI_API_KEY"] = "sk-test-hermetic-not-a-real-key"
os.environ["TAVILY_API_KEY"] = "tvly-test-hermetic-not-a-real-key"

# 외부 네트워크 차단 — 테스트가 실제 외부 서비스(OpenAI·Tavily·PyPI 등)에 접속하면 PC·CI 네트워크 상태에 따라 결과가
# 달라지고 비용이 들 수 있다. 같은 PC 안(localhost — 테스트용 HTTP 서버·로컬 Ollama 흉내)만 허용한다.
import ipaddress  # noqa: E402
import socket  # noqa: E402

_real_connect = socket.socket.connect


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # 유닉스 소켓 등
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# 로컬 Ollama(11434)도 막는다 — CI에는 Ollama가 없어, 실제로 호출하는 테스트는 이 PC에서만 통과한다.
_BLOCKED_LOCAL_PORTS = {11434}


def _guarded_connect(self, address):
    if isinstance(address, tuple) and len(address) >= 2 and address[1] in _BLOCKED_LOCAL_PORTS:
        raise OSError(f"테스트 중 로컬 Ollama 접속 차단: {address!r} — LLM·임베딩 호출은 mock으로 바꾸세요(tests/conftest.py)")
    if not _is_local(address):
        raise OSError(f"테스트 중 외부 네트워크 접속 차단: {address!r} — 외부 호출은 mock으로 바꾸세요(tests/conftest.py)")
    return _real_connect(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
