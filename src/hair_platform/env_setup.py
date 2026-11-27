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


GROUP_OPERATION = "서버 운영(작업 프로세스·시간대)"
GROUP_ADVANCED = "고급 옵션(기본값 권장)"
GROUP_SMTP = "메일(SMTP) 알림"
# 원할 때만 묻는 그룹(`hair init`) — 값이 하나라도 있으면 기본 답이 "예".
OPTIONAL_GROUPS = {
    GROUP_SMTP: "결재·완료 등 알림을 메일로도 받으려면 설정하세요.",
    GROUP_ADVANCED: "보통은 기본값 그대로 둡니다(실행 방식·검색 엔진·자동 검사 끄기 등).",
}

# 연동 설정 화면(admin)에서 등록하는 연결이 "이름"만 저장하는 비밀 환경변수 — 이름은 등록하는 사람이 정한다
# (`hair check --db-path`가 등록된 이름을 읽어 값이 있는지 보여 준다). 표: (테이블, 열, 설명, 연결 이름 열).
CONNECTOR_SECRET_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("data_connections", "password_env", "사내 DB 비밀번호", "name"),
    ("mail_inboxes", "password_env", "메일 수신함 앱 비밀번호", "name"),
    ("calendar_feeds", "source", "캘린더 iCal 주소", "name"),
)

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
    EnvVar("HAIR_SMTP_HOST", GROUP_SMTP, "SMTP 서버 호스트(비우면 메일 알림이 비활성)"),
    EnvVar("HAIR_SMTP_PORT", GROUP_SMTP, "SMTP 포트(기본 587, HAIR_SMTP_SSL=1이면 465)"),
    EnvVar("HAIR_SMTP_USER", GROUP_SMTP, "SMTP 로그인 계정"),
    EnvVar("HAIR_SMTP_PASSWORD", GROUP_SMTP, "SMTP 로그인 비밀번호", secret=True),
    EnvVar("HAIR_SMTP_FROM", GROUP_SMTP, "발신자 표시 주소(기본: HAIR_SMTP_USER)"),
    EnvVar("HAIR_SMTP_SSL", GROUP_SMTP, '1이면 SMTPS(465), 그 외엔 STARTTLS'),
    # 서버 운영(2026-11-26, P1-6 2단계·P1-9 3단계)
    EnvVar(
        "HAIR_WORKER_MODE", GROUP_OPERATION,
        "AI 실행 방식 — embedded(기본: 웹 서버가 직접 실행) | external(웹 서버는 기록만, `hair worker`가 실행 — "
        "웹 서버를 재시작해도 실행이 계속됨. hair serve와 hair worker를 같은 값·같은 --db-path로 함께 띄운다)",
        default="embedded",
    ),
    EnvVar(
        "HAIR_TLS_CA_FILE", GROUP_OPERATION,
        "사내 인증서(CA·자체 서명) 파일 경로 — 사내 메일 서버(IMAP·SMTP) 인증서를 확인할 수 없다고 나올 때만(비우면 시스템 기본 신뢰 목록)",
    ),
    EnvVar(
        "HAIR_TIMEZONE", GROUP_OPERATION,
        "캘린더 일정을 보여 줄 시간대(IANA 이름, 예: Asia/Seoul — 비우면 서버 PC 시간대)",
    ),
    # 고급 옵션 — 기본값 그대로 두는 것을 권장. `hair init`은 원할 때만 묻는다.
    EnvVar("HAIR_STAGE_RUNTIME", GROUP_ADVANCED,
           "파이프라인 단일·순차 릴레이 단계 실행 방식 — direct(기본, 대화와 같은 직접 호출) | crewai(예전 방식)",
           default="direct"),
    EnvVar("HAIR_STAGE_STALL_SECONDS", GROUP_ADVANCED,
           "파이프라인 단계가 이 초 동안 진행 없이 멈추면 정리하고 '이어서 실행'할 수 있게 함(기본 2700=45분, 0=끔)",
           default="2700"),
    EnvVar("HAIR_VECTOR_BACKEND", GROUP_ADVANCED,
           "지식 베이스 검색 엔진 — chroma(기본) | sqlite(예전 방식, 청크가 많으면 느림)", default="chroma"),
    EnvVar("HAIR_VECTOR_DIR", GROUP_ADVANCED,
           "벡터 색인 저장 폴더(기본 <DB 파일>-vectors, 지워도 hair reindex-vectors로 다시 만듦)"),
    EnvVar("HAIR_EVAL_HTML_REPORT", GROUP_ADVANCED,
           "0이면 평가마다 만드는 Gate A-G HTML 리포트를 만들지 않음(기본 1)", default="1"),
    EnvVar("HAIR_LANGUAGE_QA", GROUP_ADVANCED, "0이면 한국어 결과물 자동 언어 교정을 끔(기본 1)", default="1"),
    EnvVar("HAIR_LEXICON_CHECK", GROUP_ADVANCED,
           "0이면 사전에 없는 한국어 낱말 탐지('낱말확인')를 끔(기본 1, 분석기 메모리 약 360MB)", default="1"),
    EnvVar("HAIR_NUMERIC_CHECK", GROUP_ADVANCED,
           "0이면 결과물 퍼센트 수치 근거 검사('수치근거')를 끔(기본 1)", default="1"),
    EnvVar("HAIR_OLLAMA_AGENT_PLANNING", GROUP_ADVANCED,
           "1이면 로컬(Ollama) 모델에서도 Agent 작업 전 계획을 켬(기본 0 — 속도 우선)", default="0"),
    EnvVar("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", GROUP_ADVANCED,
           "Ollama 모델의 최대 출력 토큰 재정의(비우면 모델 레지스트리 설정값)"),
    EnvVar("HAIR_OLLAMA_MAX_CONCURRENT", GROUP_ADVANCED,
           "Ollama 모델에 한 번에 보내는 호출 수 기본값(기본 1 — Ollama는 하나씩 처리. OLLAMA_NUM_PARALLEL을 올렸다면 "
           "그 값, 0이면 상한 없음). 넘는 호출은 기다리고 대화 응답이 파이프라인보다 먼저 들어간다. 모델별 값은 모델 "
           "레지스트리 '동시 실행 상한'", default="1"),
    EnvVar("HAIR_BACKUP_DIR", GROUP_ADVANCED,
           "백업 저장 폴더(hair backup·자동 백업·초기화 전 백업, 비우면 DB 파일 옆 backups/) — 다른 디스크·NAS를 권장",
           default=""),
    EnvVar("HAIR_BACKUP_KEEP", GROUP_ADVANCED,
           "hair serve가 하루 한 번 뜨는 자동 백업을 최근 몇 개 남길지(기본 7, 0이면 자동 백업 끔)", default="7"),
    EnvVar("HAIR_ALLOW_LOCAL_CODE_RUN", GROUP_ADVANCED,
           "1이면 Docker 샌드박스 대신 로컬에서 코드 테스트(Python·JavaScript) 실행, 브라우저 샌드박스 없이도 화면실행 "
           "점검(격리 없음 — 운영 비권장, 기본 0)", default="0"),
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

    # 서버 운영·고급 옵션(2026-11-26) — 정해진 값 밖이면 코드가 조용히 기본값으로 동작하므로 알려 준다.
    def _v(key: str) -> str:
        return (values.get(key) or "").strip()

    choices = {"HAIR_WORKER_MODE": ("embedded", "external"), "HAIR_STAGE_RUNTIME": ("direct", "crewai"),
               "HAIR_VECTOR_BACKEND": ("chroma", "sqlite")}
    for key, allowed in choices.items():
        if _v(key) and _v(key).lower() not in allowed:
            issues.append(("warn", f"{key}={_v(key)!r}는 쓸 수 없는 값입니다 — {'·'.join(allowed)} 중 하나(지금은 기본값 "
                                   f"{allowed[0]}로 동작)"))
    for key in ("HAIR_EVAL_HTML_REPORT", "HAIR_LANGUAGE_QA", "HAIR_LEXICON_CHECK", "HAIR_NUMERIC_CHECK",
                "HAIR_OLLAMA_AGENT_PLANNING", "HAIR_ALLOW_LOCAL_CODE_RUN"):
        if _v(key) and _v(key) not in ("0", "1"):
            issues.append(("warn", f"{key}={_v(key)!r} — 0 또는 1로 적으세요"))
    for key in ("HAIR_STAGE_STALL_SECONDS", "HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "HAIR_OLLAMA_MAX_CONCURRENT",
                "HAIR_BACKUP_KEEP"):
        if _v(key) and not _v(key).isdigit():
            issues.append(("error", f"{key}={_v(key)!r}가 숫자가 아닙니다"))
    if _v("HAIR_TIMEZONE"):
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(_v("HAIR_TIMEZONE"))
        except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError·ValueError
            issues.append(("warn", f"HAIR_TIMEZONE={_v('HAIR_TIMEZONE')!r}는 알 수 없는 시간대입니다(예: Asia/Seoul) — "
                                   "서버 PC 시간대로 동작합니다"))
    if _v("HAIR_TLS_CA_FILE") and not os.path.isfile(os.path.expanduser(_v("HAIR_TLS_CA_FILE"))):
        issues.append(("error", f"HAIR_TLS_CA_FILE={_v('HAIR_TLS_CA_FILE')!r} 파일이 없습니다 — 메일 서버 연결이 실패합니다"))
    if _v("HAIR_BACKUP_DIR"):
        bdir = os.path.expanduser(_v("HAIR_BACKUP_DIR"))
        parent = bdir if os.path.isdir(bdir) else os.path.dirname(os.path.abspath(bdir))
        if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
            issues.append(("error", f"HAIR_BACKUP_DIR={_v('HAIR_BACKUP_DIR')!r}에 쓸 수 없습니다 — 백업이 실패합니다"))
    if _v("HAIR_BACKUP_KEEP") == "0":
        issues.append(("warn", "HAIR_BACKUP_KEEP=0 — 자동 백업이 꺼져 있습니다(hair backup으로 직접 뜨세요)"))
    if _v("HAIR_ALLOW_LOCAL_CODE_RUN") == "1":
        issues.append(("warn", "HAIR_ALLOW_LOCAL_CODE_RUN=1 — 코드 테스트가 격리 없이 서버 PC에서 실행됩니다(운영 비권장)"))

    return issues


def connector_secret_check(db_path: str, values: dict[str, str]) -> tuple[list[tuple[str, str, str, bool]],
                                                                         list[tuple[str, str]]]:
    """DB에 등록된 연동(사내 DB·메일 수신함·캘린더)이 이름만 저장한 비밀 환경변수와, 작업 프로세스 상태.
    반환: ([(종류, 연결 이름, 환경변수 이름, 값 있음)], [(level, message)]). DB가 없으면 빈 목록."""
    import sqlite3

    rows: list[tuple[str, str, str, bool]] = []
    issues: list[tuple[str, str]] = []
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return rows, issues
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return rows, issues
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, col, label, name_col in CONNECTOR_SECRET_SOURCES:
            if table not in tables:
                continue
            where = " WHERE is_active = 1" + (" AND source_kind = 'url'" if table == "calendar_feeds" else "")
            for r in conn.execute(f"SELECT {name_col} AS n, {col} AS env FROM {table}{where} ORDER BY n"):  # noqa: S608
                env = (r["env"] or "").strip()
                if not env:
                    continue
                ok = bool((values.get(env) or os.environ.get(env) or "").strip())
                rows.append((label, r["n"], env, ok))
                if not ok:
                    issues.append(("warn", f"{label} — 연결 「{r['n']}」이 쓰는 환경변수 {env}가 비어 있습니다(.env에 넣거나 export)"))
        mode = (values.get("HAIR_WORKER_MODE") or "embedded").strip().lower()
        if mode == "external" and "worker_heartbeats" in tables:
            import time

            alive = [r for r in conn.execute("SELECT beat_at FROM worker_heartbeats") if time.time() - r[0] <= 30]
            if not alive:
                issues.append(("warn", "HAIR_WORKER_MODE=external인데 작업 프로세스가 실행되고 있지 않습니다 — "
                                       f"'hair worker --db-path {db_path}'를 함께 띄우세요(없으면 AI 실행이 쌓이기만 함)"))
    finally:
        conn.close()
    return rows, issues


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

