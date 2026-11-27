"""개발 결과물의 테스트 실제 실행 — 결과물에 Python 코드와 테스트가 함께 있으면 격리 환경에서
돌려 통과 수를 기록한다.

사용자 요청(2026-11-23, "P0부터 개선 진행해줘" — P0-3 개발업무 실행 검증). 지금까지 개발 결과물
검사는 구조·구문까지였다(`agent_evaluation.compute_deliverable_runnable()` — "실제 브라우저
실행·기능 테스트는 아니다"). 같은 날 백엔드 개발자 Agent의 결과물(검증 함수 + pytest 테스트)을
사람이 직접 꺼내 돌려 보고서야 19개 통과를 확인할 수 있었다 — 그 일을 플랫폼이 한다.

**보안 경계**: AI가 만든 코드는 웹 검색 결과 등 외부 입력의 영향을 받을 수 있어, 서버에서
그대로 실행하면 안 된다. Docker가 있으면 네트워크 차단·메모리·CPU·시간 제한 컨테이너에서만
실행한다. Docker가 없으면 기본적으로 **실행하지 않고** "미평가"로 남긴다 — 관리자가 위험을
알고 `HAIR_ALLOW_LOCAL_CODE_RUN=1`을 설정한 경우에만 임시 디렉터리에서 시간 제한을 걸어 로컬로
돌린다(커스텀 Python 도구의 admin 신뢰 경계와 같은 수준).

컨테이너 이미지(python:3.11-slim)에는 pytest가 없고 네트워크도 막혀 설치할 수 없다 — 그래서
흔히 쓰는 기능(`raises`·`mark.parametrize`·`fixture`·`approx`·`skip`)만 담은 최소 대체 모듈과
실행기를 같은 디렉터리에 넣어 돌린다. 대체 모듈이 못 다루는 기능을 쓰면 그 테스트는 "오류"로
집계된다(성공으로 위장하지 않는다).

**Node 테스트**(2026-11-27, 개선 계획 3-3): 결과물에 JavaScript 코드와 테스트(`describe`·`it`·`test`)가 함께
있으면 같은 격리 규칙(Docker `node:22-slim` 네트워크 차단, 없으면 `HAIR_ALLOW_LOCAL_CODE_RUN=1`일 때만 로컬
`node`)으로 돌린다. Jest 형식(`expect`·`jest.fn`)과 Node 내장 `node:test`(+`node:assert`)를 모두 받는 최소
실행기를 같은 디렉터리에 넣는다 — 컨테이너에 Jest가 없고 설치할 수도 없다. 결과는 Python과 합쳐
`test_execution` 하나로 기록하고 언어별 수는 `languages`에 남긴다(저장소 커밋 점검이 그대로 쓴다)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 60
_MAX_FILES = 12
_PY_FENCE_RE = re.compile(r"```(?:python|py)[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FILENAME_COMMENT_RE = re.compile(r"^\s*#\s*(?:file(?:name)?\s*:\s*)?([\w./-]+\.py)\s*$", re.IGNORECASE)
_FILENAME_NEAR_RE = re.compile(r"([A-Za-z_][\w-]*\.py)")
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_]\w*)\s+import|import\s+([A-Za-z_]\w*))", re.MULTILINE)
_TEST_HINT_RE = re.compile(r"^\s*(?:def test_\w+|class Test\w*|import pytest|from pytest)", re.MULTILINE)


def extract_files(content: str) -> dict[str, str]:
    """결과물의 Python 코드 블록을 파일로 나눈다 — 이름은 블록 첫 줄 주석(`# x.py`), 블록 바로
    앞 글의 파일명 언급 순으로 찾고, 없으면 테스트가 import하는 모듈 이름을 붙인다."""
    files: dict[str, str] = {}
    unnamed: list[str] = []
    for m in _PY_FENCE_RE.finditer(content or ""):
        code = m.group(1)
        first = code.lstrip().splitlines()[0] if code.strip() else ""
        name = None
        fm = _FILENAME_COMMENT_RE.match(first)
        if fm:
            name = os.path.basename(fm.group(1))
        else:
            before = content[max(0, m.start() - 160) : m.start()]
            near = _FILENAME_NEAR_RE.findall(before)
            if near:
                name = os.path.basename(near[-1])
        if name and name not in files:
            files[name] = code
        else:
            unnamed.append(code)
    # 테스트가 import하는 모듈 이름 — 이름이 붙은 테스트와 이름 없는 테스트 블록 모두에서 모은다.
    test_sources = [c for c in files.values() if _TEST_HINT_RE.search(c)]
    test_sources += [c for c in unnamed if _TEST_HINT_RE.search(c)]
    wanted: list[str] = []
    for src in test_sources:
        for a, b in _IMPORT_RE.findall(src):
            mod = a or b
            if (mod not in sys.stdlib_module_names and mod != "pytest"
                    and f"{mod}.py" not in files and mod not in wanted):
                wanted.append(mod)
    for code in unnamed:
        if _TEST_HINT_RE.search(code):
            files[f"test_generated_{len(files)}.py"] = code
        elif wanted:
            files[f"{wanted.pop(0)}.py"] = code
        else:
            files[f"module_{len(files)}.py"] = code
    return dict(list(files.items())[:_MAX_FILES])


def has_tests(files: dict[str, str]) -> bool:
    return any(
        (n.startswith("test_") or n.endswith("_test.py")) and _TEST_HINT_RE.search(c)
        for n, c in files.items()
    )


def _isolation() -> str | None:
    """'docker' / 'local' / None(실행 불가)."""
    if shutil.which("docker") is not None:
        return "docker"
    if os.environ.get("HAIR_ALLOW_LOCAL_CODE_RUN") == "1":
        return "local"
    return None


_NO_ISOLATION_REASON = ("격리 실행 환경(Docker)이 없어 테스트를 실행하지 않음 — 로컬 실행을 허용하려면 "
                        "관리자가 HAIR_ALLOW_LOCAL_CODE_RUN=1을 설정")


def compute_test_execution(content: str) -> dict:
    """Gate 형식 결과 — 테스트가 없으면 not_measured. 단계·결과물 gate_ids에 'test_execution'을
    넣지 않는 한 통과 판정에는 참여하지 않는다(기록·배지 표시). Python과 JavaScript(Node) 테스트를
    각각 돌려 합친다."""
    py_files = extract_files(content)
    js_files = extract_js_files(content)
    suites = []
    if has_tests(py_files):
        suites.append(("python", py_files))
    if has_js_tests(js_files):
        suites.append(("node", js_files))
    if not suites:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "reason": "실행할 테스트(Python·JavaScript)가 없음"}
    mode = _isolation()
    if mode is None:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "reason": _NO_ISOLATION_REASON, "languages": {lang: {"not_measured": True} for lang, _ in suites}}
    languages: dict[str, dict] = {}
    total = passed = 0
    failures: list[dict] = []
    errors: list[str] = []
    for lang, files in suites:
        if lang == "node" and mode == "local" and shutil.which("node") is None:
            languages[lang] = {"not_measured": True, "reason": "node가 설치되지 않아 JavaScript 테스트를 실행하지 않음"}
            continue
        report = run_tests(files, mode) if lang == "python" else run_js_tests(files, mode)
        t, p = report.get("total", 0), report.get("passed", 0)
        languages[lang] = {"total": t, "passed": p}
        total, passed = total + t, passed + p
        failures += [{**f, "language": lang} for f in report.get("failures", [])]
        if report.get("error") or t == 0:
            reason = report.get("error") or "수집된 테스트가 없음"
            languages[lang]["error"] = reason
            errors.append(f"{'Python' if lang == 'python' else 'JavaScript'}: {reason}")
    measured = [v for v in languages.values() if not v.get("not_measured")]
    if not measured:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "reason": "; ".join(v.get("reason", "") for v in languages.values()), "languages": languages}
    base = {"not_measured": False, "isolation": mode, "total": total, "passed_count": passed,
            "languages": languages}
    if errors or total == 0:
        return {**base, "score": 0.0 if not total else round(passed / total, 3), "status": "fail", "passed": False,
                "reason": "; ".join(errors) or "수집된 테스트가 없음", "failures": failures[:5]}
    ok = passed == total
    return {**base, "score": round(passed / total, 3), "status": "pass" if ok else "fail", "passed": ok,
            "failures": failures[:5]}


def run_tests(files: dict[str, str], mode: str, timeout: int | None = None) -> dict:
    timeout = timeout or TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="hair_tests_") as tmp:
        work = Path(tmp)
        for name, code in files.items():
            (work / name).write_text(code, encoding="utf-8")
        (work / "pytest.py").write_text(_MINI_PYTEST, encoding="utf-8")
        (work / "_hair_runner.py").write_text(_RUNNER, encoding="utf-8")
        if mode == "docker":
            command = ["docker", "run", "--rm", "--network=none", "--cpus=1", "--memory=512m",
                       "--pids-limit=128", "-e", "PYTHONDONTWRITEBYTECODE=1",
                       "-v", f"{work}:/work:ro", "-w", "/work", "python:3.11-slim",
                       "python", "/work/_hair_runner.py"]
        else:
            command = [sys.executable, str(work / "_hair_runner.py")]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                  cwd=str(work), check=False,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:
            return {"error": f"테스트 실행이 {timeout}초를 넘겨 중단됨"}
        except OSError as exc:
            return {"error": f"테스트 실행 환경을 띄우지 못함: {exc}"}
        last = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            return json.loads(last[0])
        except (json.JSONDecodeError, ValueError):
            return {"error": "테스트 실행 결과를 읽지 못함: " + (proc.stderr or proc.stdout or "")[-300:]}


# ---------- 컨테이너 안에서 쓰는 최소 pytest 대체 모듈과 실행기 ----------

_MINI_PYTEST = r'''
"""HAIR 최소 pytest 대체(컨테이너에 pytest가 없을 때) — raises·mark.parametrize·mark.skip(if)·
fixture·approx·skip·fail만 지원한다."""
import math, re as _re


class _Raises:
    def __init__(self, exc, match=None):
        self.exc, self.match, self.value = exc, match, None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        if not issubclass(et, self.exc):
            return False
        if self.match and not _re.search(self.match, str(ev)):
            raise AssertionError(f"pattern {self.match!r} not found in {str(ev)!r}")
        self.value = ev
        return True


def raises(exc, *args, match=None, **kwargs):
    if args:
        with _Raises(exc, match) as r:
            args[0](*args[1:], **kwargs)
        return r
    return _Raises(exc, match)


class _Skip(Exception):
    pass


def skip(reason=""):
    raise _Skip(reason)


def fail(reason=""):
    raise AssertionError(reason)


class _Mark:
    def parametrize(self, names, values, ids=None):
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        def deco(fn):
            fn.__dict__.setdefault("_hair_params", []).insert(0, (list(names), list(values)))
            return fn
        return deco

    def skip(self, *a, **k):
        return lambda fn: (setattr(fn, "_hair_skip", True), fn)[1]

    def skipif(self, cond, *a, **k):
        return lambda fn: (setattr(fn, "_hair_skip", bool(cond)), fn)[1]

    def __getattr__(self, name):  # usefixtures·slow 같은 표시는 무시
        return lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda fn: fn))


mark = _Mark()


def fixture(*args, **kwargs):
    def deco(fn):
        fn._hair_fixture = True
        return fn
    if args and callable(args[0]):
        return deco(args[0])
    return deco


class approx:
    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self.expected, self.rel, self.abs = expected, rel, abs

    def _eq(self, a, b):
        return math.isclose(a, b, rel_tol=self.rel, abs_tol=self.abs)

    def __eq__(self, other):
        e = self.expected
        if isinstance(e, (list, tuple)):
            return len(e) == len(other) and all(self._eq(a, b) for a, b in zip(other, e))
        if isinstance(e, dict):
            return e.keys() == other.keys() and all(self._eq(other[k], e[k]) for k in e)
        return self._eq(other, e)

    def __repr__(self):
        return f"approx({self.expected!r})"
'''

_RUNNER = r'''
import importlib, inspect, itertools, json, os, sys, tempfile, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest

results = {"total": 0, "passed": 0, "failures": []}


def expand(fn):
    params = getattr(fn, "_hair_params", [])
    if not params:
        return [{}]
    combos = [{}]
    for names, values in params:
        nxt = []
        for base in combos:
            for v in values:
                vals = v if len(names) > 1 else (v,)
                nxt.append({**base, **dict(zip(names, vals))})
        combos = nxt
    return combos


def call(fn, kwargs, fixtures, owner=None):
    sig = inspect.signature(fn)
    args = dict(kwargs)
    for name in sig.parameters:
        if name in args or name == "self":
            continue
        if name == "tmp_path":
            from pathlib import Path
            args[name] = Path(tempfile.mkdtemp())
        elif name in fixtures:
            args[name] = call(fixtures[name], {}, fixtures)
        else:
            raise RuntimeError(f"지원하지 않는 fixture: {name}")
    return fn(**args)


for fname in sorted(os.listdir(".")):
    if not fname.endswith(".py") or not (fname.startswith("test_") or fname.endswith("_test.py")):
        continue
    try:
        mod = importlib.import_module(fname[:-3])
    except Exception as exc:
        results["total"] += 1
        results["failures"].append({"test": fname, "error": f"import 실패: {type(exc).__name__}: {exc}"[:300]})
        continue
    fixtures = {n: f for n, f in vars(mod).items() if getattr(f, "_hair_fixture", False)}
    items = []
    for name, obj in vars(mod).items():
        if name.startswith("test") and inspect.isfunction(obj):
            items.append((name, obj, None))
        elif name.startswith("Test") and inspect.isclass(obj):
            for mname, m in vars(obj).items():
                if mname.startswith("test") and callable(m):
                    items.append((f"{name}.{mname}", m, obj))
    for name, fn, cls in items:
        if getattr(fn, "_hair_skip", False):
            continue
        for kwargs in expand(fn):
            results["total"] += 1
            try:
                if cls is not None:
                    inst = cls()
                    if hasattr(inst, "setup_method"):
                        inst.setup_method(fn)
                    call(getattr(inst, fn.__name__), kwargs, fixtures)
                else:
                    call(fn, kwargs, fixtures)
                results["passed"] += 1
            except pytest._Skip:
                results["total"] -= 1
            except Exception as exc:
                tb = traceback.extract_tb(exc.__traceback__)[-1]
                results["failures"].append({"test": f"{fname}::{name}{kwargs or ''}",
                                            "error": f"{type(exc).__name__}: {exc}"[:300],
                                            "line": tb.lineno})
print(json.dumps(results, ensure_ascii=False))
'''


# ---------- JavaScript(Node) 테스트 — 2026-11-27, 개선 계획 3-3 ----------

NODE_IMAGE = "node:22-slim"
_JS_FENCE_RE = re.compile(r"```(?:javascript|js|mjs|cjs|node)(?![\w-])[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_JS_NAME = r"[\w.-]+\.(?:c|m)?js"
_JS_FILENAME_COMMENT_RE = re.compile(r"^\s*(?://|/\*)\s*(?:file(?:name)?\s*:\s*)?([\w./-]+\.(?:c|m)?jsx?)\s*(?:\*/)?\s*$",
                                     re.IGNORECASE)
_JS_FILENAME_NEAR_RE = re.compile(rf"(?<![\w/.-])((?:[\w-]+/)*{_JS_NAME})\b")
_JS_TEST_HINT_RE = re.compile(r"^\s*(?:describe|it|test)\s*(?:\.\w+\s*)?\(\s*['\"`]", re.MULTILINE)
_JS_ESM_RE = re.compile(r"^\s*(?:import\s+(?:[\w{}*,\s]+\s+from\s+)?['\"]|export\s+(?:default\b|const\b|function\b|"
                        r"class\b|let\b|async\b|\{))", re.MULTILINE)
# 상대 경로 모듈 지정(require('./x')·import ... from '../src/x.js') — 파일을 한 디렉터리에 펼쳐 놓으므로 고쳐 쓴다.
_JS_REL_SPEC_RE = re.compile(r"""(?P<q>['"])(?P<spec>\.{1,2}/[^'"\n]+)(?P=q)""")


def _js_stem(name: str) -> str:
    base = os.path.basename(name)
    return re.sub(r"\.(?:c|m)?js$", "", base, flags=re.IGNORECASE)


def extract_js_files(content: str) -> dict[str, str]:
    """결과물의 JavaScript 코드 블록을 파일로 나눈다 — 이름 찾는 순서는 Python과 같다(첫 줄 주석 →
    블록 앞 글의 파일명 → 테스트가 require/import하는 이름). 파일을 한 디렉터리에 펼쳐 놓으므로 상대 경로
    모듈 지정을 실제 파일 이름으로 고치고, ES 모듈 문법(import/export)을 쓰는 파일은 `.mjs`로 둔다."""
    named: dict[str, str] = {}
    unnamed: list[str] = []
    for m in _JS_FENCE_RE.finditer(content or ""):
        code = m.group(1)
        first = code.lstrip().splitlines()[0] if code.strip() else ""
        name = None
        fm = _JS_FILENAME_COMMENT_RE.match(first)
        if fm:
            name = os.path.basename(fm.group(1))
        else:
            near = _JS_FILENAME_NEAR_RE.findall(content[max(0, m.start() - 160): m.start()])
            if near:
                name = os.path.basename(near[-1])
        if name and name.lower().endswith(".jsx"):
            continue  # JSX는 Node가 그대로 실행하지 못한다(변환 도구 필요) — 실행 대상에서 뺀다
        if name and name not in named:
            named[name] = code
        else:
            unnamed.append(code)
    wanted: list[str] = []
    for code in [*named.values(), *unnamed]:
        if not _JS_TEST_HINT_RE.search(code):
            continue
        for sm in _JS_REL_SPEC_RE.finditer(code):
            stem = _js_stem(sm.group("spec"))
            if stem and stem not in wanted and not any(_js_stem(n) == stem for n in named):
                wanted.append(stem)
    for code in unnamed:
        if _JS_TEST_HINT_RE.search(code):
            named[f"generated_{len(named)}.test.js"] = code
        elif wanted:
            named[f"{wanted.pop(0)}.js"] = code
        else:
            named[f"module_{len(named)}.js"] = code
    # 확장자 정리: ES 모듈 문법(import/export) → .mjs, 그 밖은 .js(CommonJS).
    files: dict[str, str] = {}
    for name, code in list(named.items())[:_MAX_FILES]:
        files[_js_stem(name) + (".mjs" if _JS_ESM_RE.search(code) else ".js")] = code
    by_stem = {_js_stem(fname): fname for fname in files}

    def fix(sm: re.Match) -> str:
        target = by_stem.get(_js_stem(sm.group("spec")))
        return f"{sm.group('q')}./{target}{sm.group('q')}" if target else sm.group(0)

    return {name: _JS_REL_SPEC_RE.sub(fix, code) for name, code in files.items()}


def _is_js_test_file(name: str, code: str) -> bool:
    return bool(_JS_TEST_HINT_RE.search(code))


def has_js_tests(files: dict[str, str]) -> bool:
    return any(_is_js_test_file(n, c) for n, c in files.items())


def run_js_tests(files: dict[str, str], mode: str, timeout: int | None = None) -> dict:
    timeout = timeout or TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="hair_jstests_") as tmp:
        work = Path(tmp)
        for name, code in files.items():
            (work / name).write_text(code, encoding="utf-8")
        tests = sorted(n for n, c in files.items() if _is_js_test_file(n, c))
        (work / "_hair_tests.json").write_text(json.dumps(tests), encoding="utf-8")
        (work / "_hair_runner.cjs").write_text(_JS_RUNNER, encoding="utf-8")
        (work / "_hair_shim_jest.mjs").write_text(_JS_SHIM_JEST, encoding="utf-8")
        (work / "_hair_shim_node.mjs").write_text(_JS_SHIM_NODE, encoding="utf-8")
        if mode == "docker":
            command = ["docker", "run", "--rm", "--network=none", "--cpus=1", "--memory=512m",
                       "--pids-limit=128", "-v", f"{work}:/work:ro", "-w", "/work", NODE_IMAGE,
                       "node", "/work/_hair_runner.cjs"]
        else:
            command = [shutil.which("node") or "node", str(work / "_hair_runner.cjs")]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                  cwd=str(work), check=False, env={**os.environ, "NODE_OPTIONS": ""})
        except subprocess.TimeoutExpired:
            return {"error": f"테스트 실행이 {timeout}초를 넘겨 중단됨"}
        except OSError as exc:
            return {"error": f"테스트 실행 환경을 띄우지 못함: {exc}"}
        last = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            return json.loads(last[0])
        except (json.JSONDecodeError, ValueError):
            return {"error": "테스트 실행 결과를 읽지 못함: " + (proc.stderr or proc.stdout or "")[-300:]}


# ES 모듈에서 `import ... from '@jest/globals'`·`'vitest'`(Jest 형식) / `'node:test'`(Node 형식)를 받으면 이 파일로
# 돌린다 — 두 형식은 테스트 함수의 인자 뜻이 달라(Jest는 done 콜백, node:test는 컨텍스트 t) 따로 둔다.
_JS_SHIM_JEST = r'''
const h = globalThis.__hair;
export const describe = h.describe, it = h.it, test = h.test, expect = h.expect, jest = h.jest, vi = h.jest;
export const beforeAll = h.beforeAll, afterAll = h.afterAll, beforeEach = h.beforeEach, afterEach = h.afterEach;
export default h;
'''
_JS_SHIM_NODE = r'''
const n = globalThis.__hair.nodeTest;
export const test = n.test, it = n.it, describe = n.describe, suite = n.suite, mock = n.mock;
export const before = n.before, after = n.after, beforeEach = n.beforeEach, afterEach = n.afterEach;
export default n;
'''

_JS_RUNNER = r'''
"use strict";
// HAIR 최소 JavaScript 테스트 실행기 — Jest 형식(describe·it·test·expect·jest.fn)과 node:test를 받는다.
const fs = require("fs"), path = require("path"), util = require("util"), assert = require("assert");
const Module = require("module");
const { pathToFileURL } = require("url");
const DIR = __dirname;
const out = process.stdout.write.bind(process.stdout);
for (const k of ["log", "info", "warn", "error", "debug"]) {
  console[k] = (...a) => process.stderr.write(util.format(...a) + "\n");
}
const results = { total: 0, passed: 0, failures: [] };
const SKIP = Symbol("skip");
const TEST_TIMEOUT = 5000;

function newSuite(name) {
  return { name, items: [], beforeAll: [], afterAll: [], beforeEach: [], afterEach: [] };
}
let current = newSuite("");
let currentFile = "";

function register(kind, origin) {
  const fn = (name, opts, body) => {
    if (typeof opts === "function") { body = opts; opts = {}; }
    opts = opts || {};
    current.items.push({ kind, name: String(name), body, origin, skip: !!(opts.skip || opts.todo) || !body });
  };
  fn.skip = (name, a, b) => current.items.push({ kind, name: String(name), skip: true });
  fn.todo = fn.skip;
  fn.only = fn;
  fn.each = (table) => (name, body) => {
    for (const row of table) {
      const args = Array.isArray(row) ? row : [row];
      let i = 0;
      const title = String(name).replace(/%[sdifjop%]/g, (m) => m === "%%" ? "%" : util.format(m, args[i++]));
      fn(title, () => body(...args));
    }
  };
  return fn;
}

function describeFn(origin) {
  const d = (name, opts, body) => {
    if (typeof opts === "function") { body = opts; opts = {}; }
    const s = newSuite(String(name));
    s.skip = !!(opts && opts.skip);
    current.items.push({ kind: "suite", suite: s });
    const parent = current;
    current = s;
    try { if (body) body(); } finally { current = parent; }
  };
  d.skip = (name) => current.items.push({ kind: "suite", suite: Object.assign(newSuite(String(name)), { skip: true }) });
  d.only = d;
  d.each = (table) => (name, body) => { for (const row of table) d(util.format(name, ...(Array.isArray(row) ? row : [row])), () => body(...(Array.isArray(row) ? row : [row]))); };
  return d;
}

const hook = (key) => (body) => current[key].push(body);

// ---- expect ----
function fmt(v) { return util.inspect(v, { depth: 3, breakLength: 120 }); }
function looseEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime();
  if (a instanceof Map || a instanceof Set || b instanceof Map || b instanceof Set) return util.isDeepStrictEqual(a, b);
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((x, i) => looseEqual(x, b[i]));
  const ka = Object.keys(a).filter((k) => a[k] !== undefined), kb = Object.keys(b).filter((k) => b[k] !== undefined);
  return ka.length === kb.length && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && looseEqual(a[k], b[k]));
}
function matchObject(a, b) {
  if (typeof b !== "object" || b === null) return looseEqual(a, b);
  if (typeof a !== "object" || a === null) return false;
  if (Array.isArray(b)) return Array.isArray(a) && a.length === b.length && b.every((x, i) => matchObject(a[i], x));
  return Object.keys(b).every((k) => matchObject(a[k], b[k]));
}
function getPath(obj, p) {
  const parts = Array.isArray(p) ? p : String(p).replace(/\[(\w+)\]/g, ".$1").split(".");
  let cur = obj;
  for (const part of parts) {
    if (cur === null || cur === undefined || !(part in Object(cur))) return { found: false };
    cur = cur[part];
  }
  return { found: true, value: cur };
}
function thrownBy(actual) {
  if (typeof actual !== "function") return { threw: true, error: actual };
  try { actual(); } catch (e) { return { threw: true, error: e }; }
  return { threw: false };
}
function errorMatches(err, expected) {
  if (expected === undefined) return true;
  const msg = err && err.message !== undefined ? String(err.message) : String(err);
  if (typeof expected === "string") return msg.includes(expected);
  if (expected instanceof RegExp) return expected.test(msg);
  if (typeof expected === "function") return err instanceof expected;
  if (expected instanceof Error) return msg === expected.message;
  return false;
}
function isMock(f) { return typeof f === "function" && f._isMock; }

function expect(actual) {
  const make = (neg) => {
    const check = (ok, msg) => {
      if (neg ? ok : !ok) throw new assert.AssertionError({ message: (neg ? "(not) " : "") + msg });
    };
    const m = {
      toBe: (e) => check(Object.is(actual, e), `expected ${fmt(actual)} to be ${fmt(e)}`),
      toEqual: (e) => check(looseEqual(actual, e), `expected ${fmt(actual)} to equal ${fmt(e)}`),
      toStrictEqual: (e) => check(util.isDeepStrictEqual(actual, e), `expected ${fmt(actual)} to strictly equal ${fmt(e)}`),
      toBeTruthy: () => check(!!actual, `expected ${fmt(actual)} to be truthy`),
      toBeFalsy: () => check(!actual, `expected ${fmt(actual)} to be falsy`),
      toBeNull: () => check(actual === null, `expected ${fmt(actual)} to be null`),
      toBeUndefined: () => check(actual === undefined, `expected ${fmt(actual)} to be undefined`),
      toBeDefined: () => check(actual !== undefined, `expected value to be defined`),
      toBeNaN: () => check(Number.isNaN(actual), `expected ${fmt(actual)} to be NaN`),
      toBeGreaterThan: (n) => check(actual > n, `expected ${fmt(actual)} > ${fmt(n)}`),
      toBeGreaterThanOrEqual: (n) => check(actual >= n, `expected ${fmt(actual)} >= ${fmt(n)}`),
      toBeLessThan: (n) => check(actual < n, `expected ${fmt(actual)} < ${fmt(n)}`),
      toBeLessThanOrEqual: (n) => check(actual <= n, `expected ${fmt(actual)} <= ${fmt(n)}`),
      toBeCloseTo: (n, digits = 2) => check(Math.abs(actual - n) < Math.pow(10, -digits) / 2, `expected ${fmt(actual)} to be close to ${fmt(n)}`),
      toContain: (x) => check(typeof actual === "string" ? actual.includes(x) : Array.from(actual || []).includes(x), `expected ${fmt(actual)} to contain ${fmt(x)}`),
      toContainEqual: (x) => check(Array.from(actual || []).some((a) => looseEqual(a, x)), `expected ${fmt(actual)} to contain equal ${fmt(x)}`),
      toHaveLength: (n) => check(actual != null && actual.length === n, `expected length ${n}, got ${actual == null ? actual : actual.length}`),
      toHaveProperty: (p, ...v) => {
        const r = getPath(actual, p);
        check(r.found && (v.length === 0 || looseEqual(r.value, v[0])), `expected property ${fmt(p)}${v.length ? " = " + fmt(v[0]) : ""}`);
      },
      toMatch: (re) => check(typeof actual === "string" && (typeof re === "string" ? actual.includes(re) : re.test(actual)), `expected ${fmt(actual)} to match ${fmt(re)}`),
      toMatchObject: (o) => check(matchObject(actual, o), `expected ${fmt(actual)} to match object ${fmt(o)}`),
      toBeInstanceOf: (C) => check(actual instanceof C, `expected instance of ${C && C.name}`),
      toThrow: (e) => {
        const r = thrownBy(actual);
        check(r.threw && errorMatches(r.error, e), r.threw ? `thrown ${fmt(r.error && r.error.message)} does not match ${fmt(e)}` : "expected function to throw");
      },
      toHaveBeenCalled: () => check(isMock(actual) && actual.mock.calls.length > 0, "expected mock to have been called"),
      toHaveBeenCalledTimes: (n) => check(isMock(actual) && actual.mock.calls.length === n, `expected ${n} calls, got ${isMock(actual) ? actual.mock.calls.length : "not a mock"}`),
      toHaveBeenCalledWith: (...args) => check(isMock(actual) && actual.mock.calls.some((c) => looseEqual(c, args)), `expected call with ${fmt(args)}`),
      toHaveBeenLastCalledWith: (...args) => check(isMock(actual) && looseEqual(actual.mock.calls[actual.mock.calls.length - 1], args), `expected last call with ${fmt(args)}`),
      toHaveReturnedWith: (v) => check(isMock(actual) && actual.mock.results.some((r) => r.type === "return" && looseEqual(r.value, v)), `expected return ${fmt(v)}`),
    };
    m.toThrowError = m.toThrow;
    m.toBeCalled = m.toHaveBeenCalled;
    m.toBeCalledWith = m.toHaveBeenCalledWith;
    m.toBeCalledTimes = m.toHaveBeenCalledTimes;
    return m;
  };
  const pos = make(false);
  pos.not = make(true);
  const later = (settle) => {
    const build = (neg) => new Proxy({}, {
      get: (_, k) => k === "not" ? build(true) : (...a) => settle().then((v) => { const e = expect(v); return (neg ? e.not : e)[k](...a); }),
    });
    return build(false);
  };
  pos.resolves = later(() => Promise.resolve(actual));
  pos.rejects = later(() => Promise.resolve(actual).then(
    (v) => { throw new assert.AssertionError({ message: `expected promise to reject, resolved ${fmt(v)}` }); },
    (e) => e));
  return pos;
}
expect.any = (C) => ({ asymmetric: true, C });

// ---- mocks ----
function jestFn(impl) {
  const f = function (...args) {
    f.mock.calls.push(args);
    f.mock.instances.push(this);
    const use = f._once.length ? f._once.shift() : (f._impl || (() => undefined));
    try {
      const r = use.apply(this, args);
      f.mock.results.push({ type: "return", value: r });
      return r;
    } catch (e) {
      f.mock.results.push({ type: "throw", value: e });
      throw e;
    }
  };
  f._isMock = true;
  f._impl = impl;
  f._once = [];
  f.mock = { calls: [], instances: [], results: [] };
  f.mockReturnValue = (v) => { f._impl = () => v; return f; };
  f.mockReturnValueOnce = (v) => { f._once.push(() => v); return f; };
  f.mockImplementation = (i) => { f._impl = i; return f; };
  f.mockImplementationOnce = (i) => { f._once.push(i); return f; };
  f.mockResolvedValue = (v) => { f._impl = () => Promise.resolve(v); return f; };
  f.mockResolvedValueOnce = (v) => { f._once.push(() => Promise.resolve(v)); return f; };
  f.mockRejectedValue = (v) => { f._impl = () => Promise.reject(v); return f; };
  f.mockRejectedValueOnce = (v) => { f._once.push(() => Promise.reject(v)); return f; };
  f.mockClear = () => { f.mock.calls = []; f.mock.instances = []; f.mock.results = []; return f; };
  f.mockReset = () => { f.mockClear(); f._impl = undefined; f._once = []; return f; };
  f.mockName = () => f;
  return f;
}
const unsupported = (name) => () => { throw new Error("HAIR 실행기가 지원하지 않는 기능: " + name); };
const jest = {
  fn: jestFn,
  spyOn(obj, name) {
    const orig = obj[name];
    const m = jestFn(function (...a) { return orig.apply(this, a); });
    m.mockRestore = () => { obj[name] = orig; };
    obj[name] = m;
    return m;
  },
  clearAllMocks() {}, resetAllMocks() {}, restoreAllMocks() {},
  useFakeTimers: unsupported("jest.useFakeTimers"), mock: unsupported("jest.mock(모듈 대체)"),
};
const nodeMock = {
  fn(impl = () => undefined) {
    const calls = [];
    const f = function (...args) {
      let result, error;
      try { result = impl.apply(this, args); } catch (e) { error = e; }
      calls.push({ arguments: args, result, error, this: this });
      if (error) throw error;
      return result;
    };
    f.mock = { calls, callCount: () => calls.length, mockImplementation: (n) => { impl = n; }, resetCalls: () => { calls.length = 0; } };
    return f;
  },
  method(obj, name, impl) {
    const orig = obj[name];
    const f = nodeMock.fn(impl || orig);
    f.mock.restore = () => { obj[name] = orig; };
    obj[name] = f;
    return f;
  },
  restoreAll() {}, reset() {}, timers: { enable: unsupported("mock.timers") },
};

const H = {
  describe: describeFn("jest"), it: register("test", "jest"), test: register("test", "jest"),
  beforeAll: hook("beforeAll"), afterAll: hook("afterAll"), beforeEach: hook("beforeEach"), afterEach: hook("afterEach"),
  expect, jest, nodeMock,
};
const nodeTest = register("test", "node");
nodeTest.test = nodeTest;
nodeTest.describe = describeFn("node");
nodeTest.it = register("test", "node");
nodeTest.suite = nodeTest.describe;
nodeTest.before = H.beforeAll; nodeTest.after = H.afterAll;
nodeTest.beforeEach = H.beforeEach; nodeTest.afterEach = H.afterEach;
nodeTest.mock = nodeMock;
globalThis.__hair = Object.assign({}, H, { nodeTest });
Object.assign(globalThis, { describe: H.describe, it: H.it, test: H.test, expect, jest, vi: jest,
  beforeAll: H.beforeAll, afterAll: H.afterAll, beforeEach: H.beforeEach, afterEach: H.afterEach });
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "node:test" || request === "test") return nodeTest;
  if (request === "@jest/globals" || request === "vitest") return Object.assign({}, H, { vi: jest });
  return origLoad.apply(this, arguments);
};
if (typeof Module.register === "function") {
  const map = {
    "node:test": pathToFileURL(path.join(DIR, "_hair_shim_node.mjs")).href,
    "@jest/globals": pathToFileURL(path.join(DIR, "_hair_shim_jest.mjs")).href,
  };
  map["test"] = map["node:test"];
  map["vitest"] = map["@jest/globals"];
  const hooks = `const M=${JSON.stringify(map)};export async function resolve(s,c,n){if(M[s])return{url:M[s],shortCircuit:true};return n(s,c);}`;
  Module.register("data:text/javascript," + encodeURIComponent(hooks));
}

function withTimeout(p, ms, label) {
  let timer;
  return Promise.race([p, new Promise((_, rej) => { timer = setTimeout(() => rej(new Error(`${label} 시간 초과(${ms}ms)`)), ms); })])
    .finally(() => clearTimeout(timer));
}
function invoke(body, origin, ctx) {
  const wantsDone = origin === "node" ? body.length >= 2 : body.length >= 1;
  if (!wantsDone) return Promise.resolve().then(() => body(ctx));
  return new Promise((resolve, reject) => {
    const done = (err) => (err ? reject(err instanceof Error ? err : new Error(String(err))) : resolve());
    done.fail = (m) => reject(new Error(m));
    try { origin === "node" ? body(ctx, done) : body(done); } catch (e) { reject(e); }
  });
}
function errText(e) {
  const t = e && e.name ? `${e.name}: ${e.message}` : String(e);
  return t.slice(0, 300);
}
async function runTest(title, body, origin, chain) {
  results.total += 1;
  const subtests = [];
  const ctx = {
    name: title, signal: undefined, assert, mock: nodeMock,
    skip() { throw SKIP; }, todo() { throw SKIP; }, diagnostic() {}, plan() {},
    test: (n, o, b) => { if (typeof o === "function") { b = o; } const p = runTest(`${title} > ${n}`, b, "node", chain); subtests.push(p); return p; },
  };
  try {
    for (const s of chain) for (const h of s.beforeEach) await withTimeout(invoke(h, "jest", ctx), TEST_TIMEOUT, "beforeEach");
    await withTimeout(invoke(body, origin, ctx), TEST_TIMEOUT, "테스트");
    await Promise.all(subtests);
    for (const s of [...chain].reverse()) for (const h of s.afterEach) await withTimeout(invoke(h, "jest", ctx), TEST_TIMEOUT, "afterEach");
    results.passed += 1;
  } catch (e) {
    if (e === SKIP) { results.total -= 1; return; }
    results.failures.push({ test: `${currentFile}::${title}`, error: errText(e) });
  }
}
async function runSuite(s, prefix, chain) {
  if (s.skip) return;
  const here = [...chain, s];
  try {
    for (const h of s.beforeAll) await withTimeout(invoke(h, "jest", {}), TEST_TIMEOUT, "beforeAll");
  } catch (e) {
    results.total += 1;
    results.failures.push({ test: `${currentFile}::${prefix || "(최상위)"} beforeAll`, error: errText(e) });
    return;
  }
  for (const item of s.items) {
    if (item.kind === "suite") {
      await runSuite(item.suite, prefix ? `${prefix} > ${item.suite.name}` : item.suite.name, here);
    } else if (!item.skip) {
      await runTest(prefix ? `${prefix} > ${item.name}` : item.name, item.body, item.origin, here);
    }
  }
  try { for (const h of s.afterAll) await invoke(h, "jest", {}); } catch (e) { /* 정리 실패는 집계하지 않음 */ }
}

process.on("unhandledRejection", () => {});
(async () => {
  const files = JSON.parse(fs.readFileSync(path.join(DIR, "_hair_tests.json"), "utf8"));
  for (const f of files) {
    currentFile = f;
    current = newSuite("");
    const root = current;
    try {
      await import(pathToFileURL(path.join(DIR, f)).href);
    } catch (e) {
      results.total += 1;
      // 구문 오류는 어느 파일 몇째 줄인지(스택 첫 줄 "…/x.test.js:30")와 그 줄을 함께 남긴다.
      const where = String((e && e.stack) || "").split("\n").find((l) => l.includes(DIR));
      const loc = where ? where.replace(/^.*?([^\/\\]+:\d+).*$/, "$1") : "";
      let line = "";
      const m = loc.match(/^(.+):(\d+)$/);
      if (m) { try { line = fs.readFileSync(path.join(DIR, m[1]), "utf8").split("\n")[Number(m[2]) - 1].trim().slice(0, 80); } catch (_) {} }
      results.failures.push({ test: f, error: ("import 실패: " + errText(e) + (loc ? ` (${loc}${line ? ": " + line : ""})` : "")).slice(0, 300) });
      continue;
    }
    await runSuite(root, "", []);
  }
  out(JSON.stringify(results) + "\n", () => process.exit(0));
})();
'''
