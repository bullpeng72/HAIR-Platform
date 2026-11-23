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
집계된다(성공으로 위장하지 않는다)."""

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


def compute_test_execution(content: str) -> dict:
    """Gate 형식 결과 — 테스트가 없으면 not_measured. 단계·결과물 gate_ids에 'test_execution'을
    넣지 않는 한 통과 판정에는 참여하지 않는다(기록·배지 표시)."""
    files = extract_files(content)
    if not has_tests(files):
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "reason": "실행할 Python 테스트가 없음"}
    mode = _isolation()
    if mode is None:
        return {"score": None, "status": "not_measured", "passed": True, "not_measured": True,
                "reason": "격리 실행 환경(Docker)이 없어 테스트를 실행하지 않음 — 로컬 실행을 허용하려면 "
                          "관리자가 HAIR_ALLOW_LOCAL_CODE_RUN=1을 설정"}
    report = run_tests(files, mode)
    total, passed = report.get("total", 0), report.get("passed", 0)
    if report.get("error") or total == 0:
        return {"score": 0.0, "status": "fail", "passed": False, "not_measured": False,
                "isolation": mode, "total": total, "passed_count": passed,
                "reason": report.get("error") or "수집된 테스트가 없음", "failures": report.get("failures", [])}
    ok = passed == total
    return {"score": round(passed / total, 3), "status": "pass" if ok else "fail", "passed": ok,
            "not_measured": False, "isolation": mode, "total": total, "passed_count": passed,
            "failures": report.get("failures", [])[:5]}


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
