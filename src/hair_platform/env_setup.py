"""`.env` 환경변수 설정/점검 — `hair init`·`hair check`(cli.py)가 공유하는 정의.

사용자 요청(2026-11-25, "hair .env 및 주요 환경변수를 설정하는 기능을 담당할
init 와 점검 기능인 check 명령어를 추가해줘").

`env.sample`(저장소 루트, 사람이 직접 읽는 샘플 파일)과 설명이 겹친다 — 그
파일은 `src/` 밖에 있어 pip 패키징에 포함되지 않으므로(`pyproject.toml`의
`[tool.setuptools.packages.find] where = ["src"]`), pip로 설치한 `hair`
명령에서는 쓸 수 없다. 여기 `ENV_VARS`가 `init`/`check`가 실제로 쓰는
단일 정의이고, `env.sample`은 저장소를 git clone한 개발자를 위한 사람이
읽는 사본이다 — 항목을 추가/수정하면 두 곳 다 맞춰 고친다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvVar:
    key: str
    group: str
    desc: str
    secret: bool = False
    default: str = ""
    generator: Callable[[], str] | None = None


def generate_secret_key() -> str:
    return secrets.token_hex(32)


# 순서·그룹은 env.sample과 맞춘다 — `render_env_file()`이 그대로 이 순서로 쓴다.
ENV_VARS: tuple[EnvVar, ...] = (
    EnvVar(
        "ANTHROPIC_API_KEY", "LLM Provider API 키",
        "Anthropic(Claude) API 키 — https://console.anthropic.com/settings/keys",
        secret=True,
    ),
    EnvVar(
        "OPENAI_API_KEY", "LLM Provider API 키",
        "OpenAI API 키 — https://platform.openai.com/api-keys",
        secret=True,
    ),
    EnvVar(
        "OLLAMA_BASE_URL", "로컬 LLM(Ollama)",
        "로컬 Ollama 서버 주소(API 키 불필요, 비우면 기본값을 쓴다)",
        default="http://localhost:11434",
    ),
    EnvVar(
        "TAVILY_API_KEY", "외부 웹 검색(Tavily)",
        '"실시간웹검색" 등 Tavily 기반 Skill/Tool용 API 키 — https://tavily.com',
        secret=True,
    ),
    EnvVar(
        "HAIR_PLATFORM_LLM_PROVIDER", "플랫폼 공통 기본 LLM(선택적 폴백)",
        "웹 UI \"LLM 설정\"으로 아직 지정하지 않았을 때 쓰는 기본 provider"
        "(anthropic/openai/ollama 중 하나)",
    ),
    EnvVar(
        "HAIR_PLATFORM_LLM_MODEL", "플랫폼 공통 기본 LLM(선택적 폴백)",
        "위 provider와 함께 지정하는 기본 모델명(provider 없이 지정하면 무시된다)",
    ),
    EnvVar(
        "HAIR_PLATFORM_SECRET_KEY", "서버/보안",
        "세션 서명 키 — 127.0.0.1/localhost 바깥으로 띄우면(`hair serve --host 0.0.0.0` 등) 필수",
        secret=True, generator=generate_secret_key,
    ),
    EnvVar(
        "HAIR_GITHUB_TOKEN", "코드 저장소 PR(GitHub)",
        "결과물을 브랜치로 push한 뒤 PR을 자동으로 열 때 쓰는 토큰(저장소 Contents·Pull requests 쓰기 권한). "
        "비우면 PR 만들기 링크만 드린다",
        secret=True,
    ),
    EnvVar("HAIR_SMTP_HOST", "메일(SMTP) 알림", "SMTP 서버 호스트(비우면 메일 알림이 비활성)"),
    EnvVar("HAIR_SMTP_PORT", "메일(SMTP) 알림", "SMTP 포트(기본 587, HAIR_SMTP_SSL=1이면 465)"),
    EnvVar("HAIR_SMTP_USER", "메일(SMTP) 알림", "SMTP 로그인 계정"),
    EnvVar("HAIR_SMTP_PASSWORD", "메일(SMTP) 알림", "SMTP 로그인 비밀번호", secret=True),
    EnvVar("HAIR_SMTP_FROM", "메일(SMTP) 알림", "발신자 표시 주소(기본: HAIR_SMTP_USER)"),
    EnvVar("HAIR_SMTP_SSL", "메일(SMTP) 알림", '1이면 SMTPS(465), 그 외엔 STARTTLS'),
)

_BY_KEY: dict[str, EnvVar] = {v.key: v for v in ENV_VARS}


def by_group() -> dict[str, list[EnvVar]]:
    out: dict[str, list[EnvVar]] = {}
    for var in ENV_VARS:
        out.setdefault(var.group, []).append(var)
    return out


def mask(value: str) -> str:
    """비밀값을 화면에 내보일 때 쓴다 — 값 자체는 그대로 노출하지 않는다."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}…{value[-2:]}"


def parse_env_file(path: Path) -> dict[str, str]:
    """`.env` 파일에서 `KEY=VALUE` 줄만 뽑는다(주석·빈 줄은 건너뜀).
    python-dotenv와 같은 간단한 포맷만 지원한다 — 따옴표로 감싼 값은 벗긴다."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def leftover_assignments(path: Path) -> list[str]:
    """`.env`에 이미 있는, 이 모듈이 모르는 `KEY=VALUE` 줄을 그대로 보존하려고 뽑는다
    (사용자가 직접 추가한 설정을 `hair init`이 덮어써 지우지 않도록)."""
    out: list[str] = []
    for key, value in parse_env_file(path).items():
        if key not in _BY_KEY:
            out.append(f"{key}={value}")
    return out


def effective_values(path: Path) -> dict[str, str]:
    """실제로 서버가 보게 될 값 — 이미 셸에 export된 값이 `.env`보다 우선한다
    (python-dotenv 기본 동작, override=False — cli.py의 `load_dotenv()` 참고)."""
    out = parse_env_file(path)
    for key in list(out) + [v.key for v in ENV_VARS]:
        if key in os.environ:
            out[key] = os.environ[key]
    return out


def render_env_file(values: dict[str, str], extra_lines: list[str] | None = None) -> str:
    """ENV_VARS 순서·그룹으로 `.env` 내용을 만든다. 값이 없는 키는 주석으로 남긴다
    (env.sample과 같은 스타일 — 전부 선택이라 안 채워도 서버는 뜬다)."""
    lines = [
        "# HAIR-Platform .env — `hair init`으로 생성/갱신했다(직접 편집해도 된다).",
        "# 전부 선택(optional)이다 — 비워 둬도 서버는 뜬다. 점검: `hair check`",
        "",
    ]
    for group, vars_ in by_group().items():
        lines.append(f"# ---------- {group} ----------")
        for var in vars_:
            lines.append(f"# {var.desc}")
            value = (values.get(var.key) or "").strip()
            if value:
                lines.append(f"{var.key}={value}")
            else:
                lines.append(f"# {var.key}=" + (var.default or ""))
        lines.append("")
    if extra_lines:
        lines.append("# ---------- 기타(hair init이 모르는 값 — 직접 추가한 설정, 그대로 보존) ----------")
        lines.extend(extra_lines)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def cross_check(values: dict[str, str]) -> list[tuple[str, str]]:
    """개별 값만 보면 안 보이는 불일치 — 반환: (level, message) 목록. level: "warn" | "error"."""
    issues: list[tuple[str, str]] = []
    has_anthropic = bool((values.get("ANTHROPIC_API_KEY") or "").strip())
    has_openai = bool((values.get("OPENAI_API_KEY") or "").strip())
    provider = (values.get("HAIR_PLATFORM_LLM_PROVIDER") or "").strip().lower()

    if not provider and not has_anthropic and not has_openai:
        issues.append((
            "warn",
            (
                "ANTHROPIC_API_KEY/OPENAI_API_KEY/HAIR_PLATFORM_LLM_PROVIDER가 전부 없습니다 — "
                "다른 설정(플랫폼 LLM 설정 등)이 없으면 LLM 호출이 실패합니다"
                "(Ollama를 쓰려면 provider를 명시적으로 ollama로 지정해야 합니다)"
            ),
        ))
    if provider == "anthropic" and not has_anthropic:
        issues.append((
            "warn",
            "HAIR_PLATFORM_LLM_PROVIDER=anthropic인데 ANTHROPIC_API_KEY가 없습니다",
        ))
    if provider == "openai" and not has_openai:
        issues.append((
            "warn",
            "HAIR_PLATFORM_LLM_PROVIDER=openai인데 OPENAI_API_KEY가 없습니다",
        ))
    if provider and provider not in ("anthropic", "openai", "ollama"):
        issues.append((
            "warn",
            (
                f"HAIR_PLATFORM_LLM_PROVIDER={provider!r}는 지원하지 않는 provider입니다"
                "(anthropic/openai/ollama 중 하나)"
            ),
        ))
    if (values.get("HAIR_PLATFORM_LLM_MODEL") or "").strip() and not provider:
        issues.append((
            "warn",
            "HAIR_PLATFORM_LLM_MODEL만 있고 HAIR_PLATFORM_LLM_PROVIDER가 없으면 무시됩니다",
        ))

    smtp_host = (values.get("HAIR_SMTP_HOST") or "").strip()
    other_smtp = ("HAIR_SMTP_USER", "HAIR_SMTP_PASSWORD", "HAIR_SMTP_PORT", "HAIR_SMTP_FROM")
    if smtp_host:
        if not (values.get("HAIR_SMTP_USER") or "").strip():
            issues.append(("warn", "HAIR_SMTP_HOST는 있는데 HAIR_SMTP_USER가 없습니다"))
        port = (values.get("HAIR_SMTP_PORT") or "").strip()
        if port and not port.isdigit():
            issues.append(("error", f"HAIR_SMTP_PORT={port!r}가 숫자가 아닙니다 — 메일 발송 시점에 오류가 납니다"))
        ssl = (values.get("HAIR_SMTP_SSL") or "").strip()
        if ssl and ssl != "1":
            issues.append(("warn", f"HAIR_SMTP_SSL={ssl!r}는 \"1\"이 아니면 무시되고 STARTTLS로 동작합니다"))
    elif any((values.get(k) or "").strip() for k in other_smtp):
        issues.append(("warn", "HAIR_SMTP_HOST 없이 다른 SMTP 값만 있습니다 — 메일 알림이 비활성 상태입니다"))

    return issues


def dependency_check() -> list[tuple[str, str]]:
    """설치된 핵심 패키지 버전을 전체 테스트를 통과한 조합(`verified_versions.py`)과 비교한다(2026-11-25, P0-2).

    실측 사례: 운영 서버(conda)가 crewai 1.6.1, 테스트 환경이 1.15.22여서 Crew 실행 경로가 다르게 동작했다.
    메이저·마이너 버전이 다르면 경고(패치 차이는 무시), 설치 안 된 선택 패키지는 건너뛴다(그 기능을 안 쓰는 것)."""
    import importlib.metadata as md

    from hair_platform.verified_versions import VERIFIED

    def _minor(version: str) -> tuple[str, ...]:
        return tuple(version.split(".")[:2])

    issues: list[tuple[str, str]] = []
    for name, verified in VERIFIED.items():
        try:
            installed = md.version(name)
        except md.PackageNotFoundError:
            continue
        if _minor(installed) != _minor(verified):
            issues.append((
                "warn",
                f"{name} {installed} 설치됨 — 검증된 버전은 {verified}입니다. 동작이 테스트와 다를 수 있습니다 "
                f'(같은 조합으로 맞추기: pip install "hair-platform[all]" -c constraints.txt)',
            ))
    return issues

