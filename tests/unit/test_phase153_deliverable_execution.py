"""개발 결과물 실행 검증 확대(2026-11-27, 개선 계획 3-3) — JavaScript 테스트를 Node로, 화면(HTML) 결과물을 실제
브라우저(Chromium)로 실행한다. 지금까지 실제 실행은 Python 테스트뿐이었고, HTML·JS 결과물은 구문 검사까지였다."""

from __future__ import annotations

import shutil

import pytest

from hair_platform import agent_evaluation, browser_check, code_tests, models

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node가 없는 환경")

JEST_OUTPUT = """## 구현 (src/cart.js)

```javascript
function total(items) {
  if (!Array.isArray(items)) throw new TypeError("items must be array");
  return items.reduce((s, i) => s + i.price * i.qty, 0);
}
module.exports = { total };
```

## 테스트

```javascript
// cart.test.js
const { total } = require("../src/cart");

describe("total", () => {
  test("sums price x qty", () => {
    expect(total([{ price: 1000, qty: 2 }, { price: 500, qty: 1 }])).toBe(2500);
  });
  it.each([[[], 0], [[{ price: 5, qty: 1 }], 5]])("case %#", (items, want) => {
    expect(total(items)).toEqual(want);
  });
  test("rejects non-array", () => {
    expect(() => total(null)).toThrow(TypeError);
    expect(() => total(1)).toThrow("array");
  });
  test("async and mocks", async () => {
    await expect(Promise.resolve(3)).resolves.toBe(3);
    const f = jest.fn().mockReturnValue(2);
    f(1);
    expect(f).toHaveBeenCalledWith(1);
    expect({ a: 1, b: undefined }).toEqual({ a: 1 });
  });
});
```
"""

NODE_TEST_ESM = """```js
// slug.mjs
export function slug(s) { return s.trim().toLowerCase().replace(/\\s+/g, "-"); }
```

```js
// slug.test.mjs
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { slug } from "./slug.js";

describe("slug", () => {
  test("basic", () => { assert.equal(slug(" Hello World "), "hello-world"); });
  test("subtests", async (t) => { await t.test("inner", () => assert.ok(true)); });
});
```
"""

NODE_TEST_CJS = """```js
// math.js
exports.add = (a, b) => a + b;
```

```js
// math.test.js
const test = require("node:test");
const assert = require("assert");
const { add } = require("./math");
test("add", () => { assert.strictEqual(add(2, 3), 5); });
test("add wrong", () => { assert.strictEqual(add(2, 2), 5); });
```
"""

PY_AND_JS = """```python
# calc.py
def double(x):
    return x * 2
```

```python
# test_calc.py
from calc import double

def test_double():
    assert double(2) == 4
```
""" + NODE_TEST_CJS


@pytest.fixture
def local_run(monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "docker" else real_which(name))
    monkeypatch.setenv("HAIR_ALLOW_LOCAL_CODE_RUN", "1")


# ------------------------------------------------------------------ JavaScript 테스트


def test_js_files_are_named_and_relative_paths_flattened():
    files = code_tests.extract_js_files(JEST_OUTPUT)
    assert set(files) == {"cart.js", "cart.test.js"}
    assert 'require("./cart.js")' in files["cart.test.js"]  # ../src/cart → 한 디렉터리에 펼친 실제 이름
    assert code_tests.has_js_tests(files)
    esm = code_tests.extract_js_files(NODE_TEST_ESM)
    assert set(esm) == {"slug.mjs", "slug.test.mjs"}  # import/export 문법 → .mjs
    assert '"./slug.mjs"' in esm["slug.test.mjs"]


def test_jsx_and_plain_code_without_tests_are_not_run():
    jsx = "```js\n// App.jsx\nexport default () => <div/>;\n```\n```js\n// App.test.jsx\ntest('x', () => {});\n```"
    assert code_tests.extract_js_files(jsx) == {}
    g = code_tests.compute_test_execution("```js\nconsole.log(1)\n```")
    assert g["not_measured"] is True


@needs_node
def test_jest_style_tests_run_and_failures_are_reported(local_run):
    g = code_tests.compute_test_execution(JEST_OUTPUT)
    assert g["status"] == "pass" and g["total"] == 5 and g["passed_count"] == 5, g
    assert g["languages"] == {"node": {"total": 5, "passed": 5}} and g["isolation"] == "local"
    broken = JEST_OUTPUT.replace("s + i.price * i.qty", "s + i.price")
    g = code_tests.compute_test_execution(broken)
    assert g["status"] == "fail" and g["passed_count"] < g["total"]
    assert g["failures"][0]["language"] == "node" and "sums price x qty" in g["failures"][0]["test"]


@needs_node
def test_node_builtin_test_runner_esm_and_cjs(local_run):
    g = code_tests.compute_test_execution(NODE_TEST_ESM)
    assert g["status"] == "pass" and g["total"] == 3, g  # 하위 테스트(t.test)도 센다
    g = code_tests.compute_test_execution(NODE_TEST_CJS)
    assert g["total"] == 2 and g["passed_count"] == 1 and "add wrong" in g["failures"][0]["test"]


@needs_node
def test_python_and_javascript_results_are_combined(local_run):
    g = code_tests.compute_test_execution(PY_AND_JS)
    assert g["languages"] == {"python": {"total": 1, "passed": 1}, "node": {"total": 2, "passed": 1}}
    assert g["total"] == 3 and g["passed_count"] == 2 and g["passed"] is False


@needs_node
def test_unsupported_feature_is_a_failure_not_a_pass(local_run):
    out = "```js\n// t.test.js\ntest('timers', () => { jest.useFakeTimers(); });\n```"
    g = code_tests.compute_test_execution(out)
    assert g["passed"] is False and "지원하지 않는 기능" in g["failures"][0]["error"]


@needs_node
def test_hanging_js_test_times_out(local_run, monkeypatch):
    monkeypatch.setattr(code_tests, "TIMEOUT_SECONDS", 2)
    out = "```js\n// hang.test.js\ntest('loop', () => { while (true) {} });\n```"
    g = code_tests.compute_test_execution(out)
    assert g["status"] == "fail" and "초를 넘겨" in g["reason"]


def test_no_isolation_means_js_tests_not_run(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("HAIR_ALLOW_LOCAL_CODE_RUN", raising=False)
    g = code_tests.compute_test_execution(JEST_OUTPUT)
    assert g["not_measured"] is True and "Docker" in g["reason"] and "node" in g["languages"]


def test_js_docker_command_is_isolated(monkeypatch):
    seen = {}
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    def fake_run(command, **kwargs):
        seen["command"] = command

        class P:
            stdout = '{"total": 5, "passed": 5, "failures": []}'
            stderr = ""
        return P()

    monkeypatch.setattr(code_tests.subprocess, "run", fake_run)
    g = code_tests.compute_test_execution(JEST_OUTPUT)
    assert g["status"] == "pass" and g["isolation"] == "docker"
    cmd = " ".join(seen["command"])
    assert code_tests.NODE_IMAGE in cmd and "--network=none" in cmd and ":ro" in cmd and "--memory=512m" in cmd


def test_gate_ids_accept_execution_gates():
    stage = models.PipelineStageUpdate.model_validate({"gate_ids": ["test_execution", "browser_execution"]})
    assert stage.gate_ids == ["test_execution", "browser_execution"]


# ------------------------------------------------------------------ 화면(HTML) 실행


def _browser_or_skip(content):
    g = browser_check.compute_browser_execution(content)
    if g["not_measured"] and ("설치" in g["reason"] or "샌드박스" in g["reason"]):
        pytest.skip(g["reason"])
    return g


GOOD_PAGE = """<!DOCTYPE html><html><body><h1>할 일</h1><ul id="l"></ul><button id="a">추가</button>
<script>document.getElementById('a').onclick = () => { const li = document.createElement('li');
li.textContent = '새 항목'; document.getElementById('l').appendChild(li); };</script></body></html>"""


def test_working_page_passes_after_clicking_buttons():
    g = _browser_or_skip("결과물입니다.\n\n" + GOOD_PAGE)
    assert g["status"] == "pass" and g["clicked"] == 1 and g["errors"] == [], g


def test_script_error_on_load_and_on_click_and_blank_page_fail():
    on_load = "<!DOCTYPE html><html><body><p>앱</p><script>document.getElementById('x').textContent = 1;</script></body></html>"
    g = _browser_or_skip(on_load)
    assert g["passed"] is False and "열 때 스크립트 오류" in g["reason"] and "null" in g["errors"][0]
    on_click = ('<!DOCTYPE html><html><body><p>계산기</p><button onclick="calc()">계산</button>'
                "<script>function calc() { undefinedFn(); }</script></body></html>")
    g = _browser_or_skip(on_click)
    assert g["passed"] is False and "버튼을 누르자" in g["reason"] and g["errors"][0].startswith("[계산]")
    g = _browser_or_skip("<!DOCTYPE html><html><body><div></div></body></html>")
    assert g["passed"] is False and "그려진 것이 없음" in g["reason"]


def test_network_is_blocked_and_reported():
    page = ('<!DOCTYPE html><html><head><script src="https://cdn.example.com/chart.js"></script></head>'
            "<body><p>차트</p><script>new Chart(1)</script></body></html>")
    g = _browser_or_skip(page)
    assert g["blocked_count"] == 1 and "cdn.example.com" in g["blocked_requests"][0]
    assert g["passed"] is False and "막은 상태" in g["reason"]  # 오류가 차단 때문일 수 있음을 알린다


def test_hanging_page_is_stopped(monkeypatch):
    monkeypatch.setattr(browser_check, "TIMEOUT_SECONDS", 8)
    g = _browser_or_skip("<!DOCTYPE html><html><body><p>x</p><script>while (true) {}</script></body></html>")
    assert g["passed"] is False and "넘겨 중단" in g["reason"]


def test_missing_playwright_is_not_measured_with_install_hint(monkeypatch):
    monkeypatch.setattr(browser_check, "playwright_installed", lambda: False)
    g = browser_check.compute_browser_execution(GOOD_PAGE)
    assert g["not_measured"] is True and "playwright install chromium" in g["reason"]


def test_unavailable_browser_is_not_measured(monkeypatch):
    monkeypatch.setattr(browser_check, "playwright_installed", lambda: True)  # CI에는 Playwright가 없다
    monkeypatch.setattr(browser_check, "run_in_browser", lambda html: {"unavailable": "샌드박스를 켤 수 없어 실행하지 않음"})
    g = browser_check.compute_browser_execution(GOOD_PAGE)
    assert g["not_measured"] is True and "샌드박스" in g["reason"]
    assert browser_check.compute_browser_execution("HTML 없는 보고서")["not_measured"] is True


def test_attach_quality_checks_records_browser_execution_only_for_html(monkeypatch):
    monkeypatch.setattr(browser_check, "run_in_browser", lambda html: {
        "page_errors": [], "click_errors": [], "console_errors": [], "blocked_requests": [], "clicked": 1,
        "text_chars": 10, "blank": False, "sandbox": True})
    monkeypatch.setattr(browser_check, "playwright_installed", lambda: True)
    monkeypatch.setattr(browser_check, "AUTO_RUN", True)
    scores = agent_evaluation.attach_quality_checks({}, GOOD_PAGE)
    assert scores["browser_execution"]["status"] == "pass" and scores["browser_execution"]["isolation"] == "chromium-sandbox"
    assert "browser_execution" not in agent_evaluation.attach_quality_checks({}, "HTML 없는 보고서")
    monkeypatch.setattr(browser_check, "AUTO_RUN", False)  # 테스트 묶음 기본(conftest)
    assert "browser_execution" not in agent_evaluation.attach_quality_checks({}, GOOD_PAGE)


@needs_node
def test_syntax_error_reports_file_and_line(local_run):
    """실측(2026-11-27, Ornith 35B): 테스트 파일 30줄이 `test정확히 5만원 …', () => {`(`('` 빠짐) — 어디인지 보여 준다."""
    out = "```js\n// a.test.js\ntest('ok', () => {});\ntest정확히 5만원', () => {});\n```"
    g = code_tests.compute_test_execution(out)
    assert g["passed"] is False and "a.test.js:3" in g["failures"][0]["error"] and "test정확히" in g["failures"][0]["error"]
