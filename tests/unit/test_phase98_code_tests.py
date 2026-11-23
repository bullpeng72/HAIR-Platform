"""개발 결과물 테스트 실제 실행(2026-11-23, "P0부터 개선 진행해줘" — P0-3)."""

from __future__ import annotations

import shutil

import pytest

from hair_platform import agent_evaluation, code_tests

# 실측(2026-11-22) 백엔드 개발자 Agent 결과물과 같은 모양 — 파일명은 블록 앞 제목에만 있다.
OUTPUT = """## 구현 (phone_validator.py)

```python
import re

_P = re.compile(r"^010\\d{8}$")


def is_valid(phone):
    if not isinstance(phone, str):
        raise TypeError("phone must be str")
    return bool(_P.match(phone.replace("-", "")))
```

## 테스트

```python
# test_phone_validator.py
import pytest
from phone_validator import is_valid


@pytest.mark.parametrize("phone", ["010-1234-5678", "01012345678"])
def test_valid(phone):
    assert is_valid(phone) is True


class TestInvalid:
    @pytest.mark.parametrize("phone,expected", [("0111234567", False), ("", False)])
    def test_invalid(self, phone, expected):
        assert is_valid(phone) is expected

    def test_type(self):
        with pytest.raises(TypeError, match="str"):
            is_valid(None)


def test_approx_and_fixture(sample):
    assert 0.1 + 0.2 == pytest.approx(0.3)
    assert sample == 3


@pytest.fixture
def sample():
    return 3
```
"""


@pytest.fixture
def local_run(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)  # Docker 없음
    monkeypatch.setenv("HAIR_ALLOW_LOCAL_CODE_RUN", "1")


def test_extract_files_names_blocks_from_heading_and_comment():
    files = code_tests.extract_files(OUTPUT)
    assert set(files) == {"phone_validator.py", "test_phone_validator.py"}
    assert code_tests.has_tests(files)


def test_unnamed_module_gets_the_name_tests_import():
    out = "```python\ndef f():\n    return 1\n```\n```python\nfrom calc import f\n\ndef test_f():\n    assert f() == 1\n```"
    files = code_tests.extract_files(out)
    assert "calc.py" in files and any(n.startswith("test_") for n in files)


def test_runs_tests_with_the_built_in_pytest_shim(local_run):
    g = code_tests.compute_test_execution(OUTPUT)
    assert g["status"] == "pass" and g["total"] == 6 and g["passed_count"] == 6, g
    assert g["isolation"] == "local"


def test_failures_are_reported_not_hidden(local_run):
    broken = OUTPUT.replace('return bool(_P.match(phone.replace("-", "")))', "return True")
    g = code_tests.compute_test_execution(broken)
    assert g["status"] == "fail" and g["passed"] is False and g["passed_count"] < g["total"]
    assert g["failures"] and "test_invalid" in g["failures"][0]["test"]


def test_no_isolation_means_not_run(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("HAIR_ALLOW_LOCAL_CODE_RUN", raising=False)
    g = code_tests.compute_test_execution(OUTPUT)
    assert g["not_measured"] is True and "Docker" in g["reason"]


def test_docker_command_is_isolated(monkeypatch):
    seen = {}
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_run(command, **kwargs):
        seen["command"] = command

        class P:
            stdout = '{"total": 1, "passed": 1, "failures": []}'
            stderr = ""
        return P()

    monkeypatch.setattr(code_tests.subprocess, "run", fake_run)
    g = code_tests.compute_test_execution(OUTPUT)
    assert g["status"] == "pass" and g["isolation"] == "docker"
    cmd = " ".join(seen["command"])
    assert "--network=none" in cmd and "--memory=512m" in cmd and ":ro" in cmd


def test_timeout_is_a_failure(local_run, monkeypatch):
    slow = OUTPUT.replace("def test_type(self):", "def test_type(self):\n        import time; time.sleep(5)")
    monkeypatch.setattr(code_tests, "TIMEOUT_SECONDS", 1)
    g = code_tests.compute_test_execution(slow)
    assert g["status"] == "fail" and "초를 넘겨" in g["reason"]


def test_attach_quality_checks_only_adds_measured_scores(local_run):
    scores = agent_evaluation.attach_quality_checks({}, "테스트 없는 보고서. 매출 105.8% 달성.", "매출 55억, 목표 52억")
    assert "test_execution" not in scores and scores["numeric_grounding"]["passed"] is True
    scores = agent_evaluation.attach_quality_checks({}, OUTPUT)
    assert scores["test_execution"]["status"] == "pass" and "numeric_grounding" not in scores
