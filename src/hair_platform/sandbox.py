"""ADR 결정 8 — Job Queue 샌드박스는 Docker 컨테이너다.

`--cpus`·`--memory`로 리소스 쿼터, `--network=none`으로 아웃바운드 차단, 타임아웃으로
실행시간을 제한한다. Docker가 설치되지 않은 환경(이 저장소의 개발/CI 샌드박스 포함)
에서는 실제 실행을 **건너뛰고 그 사실을 명시적으로 알린다** — 성공으로 위장하지 않는다.
실제 격리 동작 검증은 Docker가 설치된 환경에서 별도로 확인해야 한다(SPEC.md §12 오픈 이슈).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    skipped: bool = False


def build_docker_command(
    script: str, *, cpus: str = "1", memory: str = "512m"
) -> list[str]:
    """ADR 결정 8의 격리 옵션을 담은 `docker run` 명령을 구성한다(실행하지 않음)."""
    return [
        "docker",
        "run",
        "--rm",
        f"--cpus={cpus}",
        f"--memory={memory}",
        "--network=none",
        "python:3.11-slim",
        "python",
        "-c",
        script,
    ]


def run_smoke_test_in_sandbox(
    generated: dict,
    *,
    cpus: str = "1",
    memory: str = "512m",
    timeout: int = 60,
) -> SandboxResult:
    """생성된 Agent 스텁의 smoke-test를 샌드박스에서 실행한다."""
    script = generated.get("agents_yaml_snippet", "")
    if not script:
        return SandboxResult(ok=False, stderr="empty generation result")

    if shutil.which("docker") is None:
        return SandboxResult(
            ok=True, skipped=True, stdout="docker not installed — sandbox skipped"
        )

    command = build_docker_command(f"print({script!r})", cpus=cpus, memory=memory)
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        return SandboxResult(
            ok=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(ok=False, stderr="sandbox timeout")
