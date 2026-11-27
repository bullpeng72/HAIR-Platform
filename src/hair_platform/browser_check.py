"""화면 결과물(완전한 HTML 문서)을 실제 브라우저에서 띄워 보는 실행 점검(2026-11-27, 개선 계획 3-3).

지금까지 HTML 결과물 검사는 "완성본이 있고 스크립트 구문이 맞다"까지였다(`agent_evaluation.compute_deliverable_
runnable()` — 실제 브라우저 실행은 아님). 구문은 맞아도 열자마자 `TypeError`가 나거나, 아무것도 그려지지 않거나,
버튼을 누르면 오류가 나는 결과물을 사람이 직접 열어 보기 전에는 몰랐다. 이 모듈은 headless Chromium(Playwright)
으로 문서를 열고 다음을 기록한다(`browser_execution`):

- 열 때 난 스크립트 오류(잡히지 않은 예외)와 콘솔 오류
- 화면에 그려진 것이 있는지(글자·이미지·canvas·svg)
- 보이는 버튼을 차례로 눌러 본 뒤 새로 난 오류(최대 `MAX_CLICKS`개, 입력값은 넣지 않음)

**격리**: AI가 만든 스크립트는 브라우저가 원래 낯선 웹 페이지를 다루는 방식으로 다룬다 — Chromium 샌드박스를
켜고, 문서는 `about:blank`에 직접 넣어(파일·서버 주소가 아님) 로컬 파일에 접근하지 못하게 하고, 모든 네트워크
요청을 막는다(외부 리소스·전송 모두 — 결과물 지시도 "외부 리소스 참조 금지"다). 막은 요청 수는 함께 남겨,
오류가 그 때문일 수 있으면 사람이 알게 한다. 이 환경에서 Chromium 샌드박스를 켤 수 없으면(예: 루트로 도는
Linux 컨테이너) 기본적으로 실행하지 않고 "미평가"로 남긴다 — `HAIR_ALLOW_LOCAL_CODE_RUN=1`일 때만 샌드박스
없이 돌린다(코드 테스트의 로컬 실행과 같은 신뢰 경계). 브라우저는 별도 프로세스에서 띄워 시간 제한을 건다.

Playwright·Chromium은 선택 설치다(`pip install "hair-platform[e2e]"` + `python -m playwright install chromium`).
없으면 "미평가"로 남기고 설치 방법을 알려 준다. 단계 `gate_ids`에 `browser_execution`을 넣지 않는 한 통과 판정에는
참여하지 않는다(기록·배지 표시)."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 45
MAX_CLICKS = 10
# 결과물 평가 때 자동으로 돌릴지 — 테스트 묶음(`tests/conftest.py`)은 끈다(결과물마다 브라우저를 띄우지 않게).
AUTO_RUN = True
_INSTALL_HINT = 'pip install "hair-platform[e2e]" 후 python -m playwright install chromium'


def find_html_document(content: str) -> str | None:
    from hair_platform.agent_evaluation import _HTML_DOC_RE

    docs = _HTML_DOC_RE.findall(content or "")
    return max(docs, key=len) if docs else None


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _not_measured(reason: str) -> dict:
    return {"score": None, "status": "not_measured", "passed": True, "not_measured": True, "reason": reason}


def compute_browser_execution(content: str) -> dict:
    """Gate 형식 결과 — 완전한 HTML 문서가 없으면 not_measured."""
    doc = find_html_document(content)
    if doc is None:
        return _not_measured("실행할 HTML 문서가 없음")
    if not playwright_installed():
        return _not_measured("브라우저 실행 도구(Playwright)가 설치되지 않음 — " + _INSTALL_HINT)
    report = run_in_browser(doc)
    if report.get("unavailable"):
        return _not_measured(report["unavailable"])
    if report.get("error"):
        return {"score": 0.0, "status": "fail", "passed": False, "not_measured": False, "reason": report["error"]}
    reasons: list[str] = []
    load_errors, click_errors = report.get("page_errors", []), report.get("click_errors", [])
    if load_errors:
        reasons.append(f"열 때 스크립트 오류 {len(load_errors)}건")
    if click_errors:
        reasons.append(f"버튼을 누르자 스크립트 오류 {len(click_errors)}건")
    if report.get("blank"):
        reasons.append("화면에 그려진 것이 없음")
    blocked = report.get("blocked_requests", [])
    if reasons and blocked:
        reasons.append(f"외부 요청 {len(blocked)}건을 막은 상태에서 실행 — 오류가 그 때문일 수 있음")
    passed = not (load_errors or click_errors or report.get("blank"))
    return {
        "score": 1.0 if passed else 0.0,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "not_measured": False,
        "isolation": "chromium-sandbox" if report.get("sandbox") else "chromium",
        "errors": (load_errors + click_errors)[:5],
        "console_errors": report.get("console_errors", [])[:5],
        "blocked_requests": blocked[:5],
        "blocked_count": len(blocked),
        "clicked": report.get("clicked", 0),
        "text_chars": report.get("text_chars", 0),
        "reason": "; ".join(reasons),
    }


def run_in_browser(html: str, timeout: int | None = None) -> dict:
    """별도 프로세스에서 Chromium을 띄워 문서를 연다. 결과 dict — 실행 못 함은 `unavailable`, 중단은 `error`."""
    timeout = timeout or TIMEOUT_SECONDS
    allow_unsandboxed = os.environ.get("HAIR_ALLOW_LOCAL_CODE_RUN") == "1"
    with tempfile.TemporaryDirectory(prefix="hair_browser_") as tmp:
        page_file = Path(tmp) / "page.html"
        page_file.write_text(html, encoding="utf-8")
        args = [sys.executable, "-c", _RUNNER, str(page_file), str(MAX_CLICKS), "1" if allow_unsandboxed else "0"]
        try:
            # 새 프로세스 그룹 — 시간 초과 때 Chromium 자식 프로세스까지 한꺼번에 끝낸다(남아서 CPU를 쓰지 않게).
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp,
                                    start_new_session=True)
        except OSError as exc:
            return {"unavailable": f"브라우저 실행 프로세스를 띄우지 못함: {exc}"}
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return {"error": f"브라우저 실행이 {timeout}초를 넘겨 중단됨(무한 반복·멈춤 의심)"}
    last = (stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return json.loads(last[0])
    except (json.JSONDecodeError, ValueError):
        return {"error": "브라우저 실행 결과를 읽지 못함: " + (stderr or stdout or "")[-300:]}


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, AttributeError):  # Windows(killpg 없음)·이미 끝남
        proc.kill()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# 별도 프로세스에서 도는 실행기 — 인자: 문서 파일, 최대 클릭 수, 샌드박스 없이 실행 허용(1/0).
_RUNNER = r'''
import json, sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8")
max_clicks = int(sys.argv[2])
allow_unsandboxed = sys.argv[3] == "1"
out = {"page_errors": [], "click_errors": [], "console_errors": [], "blocked_requests": [], "clicked": 0}


def emit():
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


try:
    from playwright.sync_api import Error as PwError, sync_playwright
except ImportError:
    out = {"unavailable": "브라우저 실행 도구(Playwright)가 설치되지 않음"}
    emit()

with sync_playwright() as p:
    browser = None
    try:
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        out["sandbox"] = True
    except PwError as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            out = {"unavailable": "브라우저(Chromium)가 설치되지 않음 — python -m playwright install chromium"}
            emit()
        if not allow_unsandboxed:
            out = {"unavailable": "이 환경에서 브라우저 샌드박스를 켤 수 없어 실행하지 않음 — 샌드박스 없이 실행을 "
                                  "허용하려면 관리자가 HAIR_ALLOW_LOCAL_CODE_RUN=1을 설정"}
            emit()
        browser = p.chromium.launch(headless=True)
        out["sandbox"] = False
    ctx = browser.new_context(service_workers="block", accept_downloads=False, viewport={"width": 1280, "height": 800})
    ctx.set_default_timeout(3000)

    def block(route):
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            return route.continue_()
        out["blocked_requests"].append(url[:200])
        return route.abort()

    ctx.route("**/*", block)
    page = ctx.new_page()
    phase = {"name": "load"}
    page.on("pageerror", lambda e: out["click_errors" if phase["name"] == "click" else "page_errors"].append(
        (phase.get("label", "") + str(e))[:300]))
    page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
            and out["console_errors"].append(m.text[:300]))
    page.on("dialog", lambda d: d.dismiss())
    page.on("popup", lambda pp: pp.close())
    try:
        page.set_content(html, wait_until="load", timeout=10000)
    except PwError as exc:
        out["page_errors"].append(("문서를 여는 중 멈춤: " + str(exc).splitlines()[0])[:300])
    page.wait_for_timeout(500)
    try:
        info = page.evaluate("""() => {
            const text = (document.body && document.body.innerText || "").trim();
            const visual = [...document.querySelectorAll("img,canvas,svg,video")].filter(e => {
                const r = e.getBoundingClientRect(); return r.width > 4 && r.height > 4; }).length;
            return {text_chars: text.length, visual};
        }""")
    except PwError:
        info = {"text_chars": 0, "visual": 0}
    out["text_chars"] = info["text_chars"]
    out["blank"] = info["text_chars"] == 0 and info["visual"] == 0
    phase["name"] = "click"
    buttons = page.locator("button:visible, [role=button]:visible, input[type=button]:visible, input[type=submit]:visible")
    try:
        count = min(buttons.count(), max_clicks)
    except PwError:
        count = 0
    for i in range(count):
        b = buttons.nth(i)
        try:
            label = (b.inner_text(timeout=500) or b.get_attribute("value", timeout=500) or "").strip()[:30]
        except PwError:
            label = ""
        phase["label"] = f"[{label or '버튼'}] "
        try:
            if not b.is_enabled(timeout=500):
                continue
            b.click(timeout=1500, no_wait_after=True)
            out["clicked"] += 1
            page.wait_for_timeout(150)
        except PwError:
            continue  # 가려졌거나 사라진 버튼 — 오류로 치지 않는다
    browser.close()
emit()
'''
