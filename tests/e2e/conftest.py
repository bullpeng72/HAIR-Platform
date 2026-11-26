"""화면 자동 테스트(2026-11-26, 개선 계획 2-1) — 실제 서버 + 실제 브라우저(Playwright Chromium, headless).

설치(개발용 선택): `pip install -e ".[e2e]" && python -m playwright install chromium`. Playwright나 브라우저가 없으면
이 폴더의 테스트는 전부 건너뛴다 — pip 설치·배포와 기본 테스트에는 영향이 없다.

서버는 임시 DB 파일로 `create_app()`을 만들어 이 프로세스의 스레드에서 uvicorn으로 띄운다(빈 포트). 외부 네트워크
차단(`tests/conftest.py`)은 로컬호스트를 허용한다. 브라우저는 별도 프로세스라 CDN 라이브러리(marked·DOMPurify 등)를
그대로 받는다.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright 미설치 — pip install -e '.[e2e]'")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

USERS = {"admin": ("e2e_admin", "e2e-admin-pass"), "manager": ("e2e_manager", "e2e-manager-pass"),
         "member": ("e2e_member", "e2e-member-pass"), "viewer": ("e2e_viewer", "e2e-viewer-pass")}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    import uvicorn

    from hair_platform.app import create_app
    from hair_platform.auth import Role, create_user

    db_path = Path(tmp_path_factory.mktemp("e2e")) / "e2e.db"
    app = create_app(str(db_path))
    conn = app.state.db
    for role, (name, pw) in USERS.items():
        create_user(conn, name, pw, Role(role))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "테스트 서버가 뜨지 않았다"
    yield {"url": f"http://127.0.0.1:{port}/", "conn": conn}
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except PlaywrightError as exc:
            pytest.skip(f"Chromium 없음 — python -m playwright install chromium ({str(exc)[:80]})")
        yield b
        b.close()


@pytest.fixture
def login(browser, live_server):
    """역할로 로그인한 새 페이지를 돌려준다. 페이지 JS 오류는 page.errors에 모인다."""
    pages = []

    def _login(role: str = "admin"):
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="ko-KR")
        page = ctx.new_page()
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda exc: page.errors.append(str(exc)))  # type: ignore[attr-defined]
        page.goto(live_server["url"])
        name, pw = USERS[role]
        page.fill("#login-username", name)
        page.fill("#login-password", pw)
        page.click("#login-form button[type=submit]")
        page.wait_for_selector("#app-view", state="visible")
        page.wait_for_selector("#content :is(h2,h3,section,p)")
        pages.append(ctx)
        return page

    yield _login
    for ctx in pages:
        ctx.close()
