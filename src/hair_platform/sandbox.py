"""Job Queue 샌드박스는 Docker 컨테이너다.

`--cpus`·`--memory`로 리소스 쿼터, `--network=none`으로 아웃바운드 차단, 타임아웃으로
실행시간을 제한한다. Docker가 설치되지 않은 환경(이 저장소의 개발/CI 샌드박스 포함)
에서는 실제 실행을 **건너뛰고 그 사실을 명시적으로 알린다** — 성공으로 위장하지 않는다.
실제 격리 동작 검증은 Docker가 설치된 환경에서 별도로 확인해야 한다(오픈 이슈).
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
    script: str,
    *,
    cpus: str = "1",
    memory: str = "512m",
    args: list[str] | None = None,
) -> list[str]:
    """격리 옵션을 담은 `docker run` 명령을 구성한다(실행하지 않음).

    `args`(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능" 추가) —
    `python -c script`에 이어붙는 추가 인자(`sys.argv[1:]`). 커스텀 Tool
    실행 시 질의 문자열을 코드에 문자열 포매팅으로 끼워 넣지 않고 별도
    프로세스 인자로 넘기기 위함(인젝션 방지 — 기존 smoke-test 호출부는
    `args`를 안 넘기므로 동작이 그대로 유지된다)."""
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
        *(args or []),
    ]


# 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능을
# 제공해줘") — 관리자가 Tool Pool에 Python 함수 소스를 직접 등록하면
# "실행 중인 CrewAI Agent가 그 Tool을 부를 때마다" 이 함수가 그 소스를
# 돌린다. `run_smoke_test_in_sandbox()`와 달리 **Docker가 없다고
# 조용히 건너뛸 수 없다** — smoke-test는 "검증을 생략해도 생성 자체는
# 끝난다"지만, 이건 실제로 그 자리에서 값을 반환해야 하는 살아있는 Tool
# 호출이라 건너뛰면 Agent가 그 턴을 완료할 수 없다. 그래서 Docker가 있으면
# 격리 실행하고, 없으면 **평범한 subprocess로 폴백**한다(네트워크/자원
# 격리는 없음 — 프로세스 분리와 타임아웃만 보장). 이 함수를 호출하는
# 쪽(`pool_registry.create_tool()`)이 `custom_python` 종류를 admin
# 전용으로 막아, "이미 mcp_stdio로 임의 프로세스를 띄울 수 있는" 같은
# 신뢰 수준의 사람만 쓸 수 있게 한다 — 이건 테넌트 격리가 아니라 관리자
# 신뢰 경계라는 걸 정직하게 기록해둔다.
def run_python_script(
    script: str,
    args: list[str] | None = None,
    *,
    cpus: str = "1",
    memory: str = "512m",
    timeout: int = 10,
) -> SandboxResult:
    """`script`(`python -c`로 실행할 소스)를 가능하면 Docker 격리로,
    없으면 일반 subprocess로 실행한다. 둘 다 타임아웃을 건다."""
    if shutil.which("docker") is not None:
        command = build_docker_command(script, cpus=cpus, memory=memory, args=args)
    else:
        command = ["python3", "-c", script, *(args or [])]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        return SandboxResult(
            ok=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(ok=False, stderr="sandbox timeout")
    except OSError as exc:
        return SandboxResult(ok=False, stderr=f"sandbox launch failed: {exc}")


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
