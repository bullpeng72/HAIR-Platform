"""`constraints.txt` 생성기(2026-11-25, P0-2) — 지금 환경에서 검증된 버전으로 의존성 전체를 고정한다.

`hair-platform[dev,all]`이 실제로 설치하는 패키지(전이 의존성 포함)만 골라 현재 설치 버전을 `==`로 적는다.
pip의 constraints 파일은 "설치할 경우 이 버전으로"라는 뜻이라, 요청하지 않은 패키지를 더 깔지는 않는다.

    python scripts/update_constraints.py            # constraints.txt 갱신
    pip install -e ".[dev,all]" -c constraints.txt  # 같은 조합으로 설치(CI도 이렇게 한다)

의존성을 올릴 때: 새 버전을 설치 → 전체 테스트 통과 확인 → 이 스크립트로 다시 생성해 함께 커밋한다.
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ("dev", "all")
# 플랫폼마다 휠 구성이 달라 고정하면 오히려 설치가 깨지기 쉬운 빌드 도구는 뺀다.
SKIP = {"pip", "setuptools", "wheel"}
# 동작이 버전에 민감했던(실측) 핵심 패키지 — `hair check`가 비교한다.
KEY_PACKAGES = ("crewai", "crewai-tools", "litellm", "pydantic", "chromadb", "langgraph", "openai", "anthropic", "mcp")


def _closure() -> dict[str, str]:
    env: dict = dict(default_environment())
    seen: dict[str, str] = {}
    root = md.distribution("hair-platform")
    queue: list[tuple[Requirement, frozenset[str]]] = []
    for raw in root.requires or []:
        req = Requirement(raw)
        if req.marker is None or any(req.marker.evaluate({**env, "extra": e}) for e in ("", *EXTRAS)):
            queue.append((req, frozenset(req.extras)))
    while queue:
        req, extras = queue.pop()
        name = canonicalize_name(req.name)
        if name in SKIP or name == "hair-platform":
            continue
        try:
            dist = md.distribution(req.name)
        except md.PackageNotFoundError:
            continue  # 이 플랫폼에서 설치되지 않는 의존성(마커로 제외됨 등)
        first = name not in seen
        seen[name] = dist.version
        if not first and not extras:
            continue
        for raw in dist.requires or []:
            sub = Requirement(raw)
            ok = sub.marker is None or any(sub.marker.evaluate({**env, "extra": e}) for e in ("", *extras))
            if ok:
                queue.append((sub, frozenset(sub.extras)))
    return seen


def main() -> int:
    pins = _closure()
    lines = [
        "# 자동 생성 — scripts/update_constraints.py (직접 고치지 말 것)",
        f"# 검증 환경: Python {sys.version.split()[0]} · hair-platform[{','.join(EXTRAS)}] · 패키지 {len(pins)}개",
        "# 사용: pip install -e \".[dev,all]\" -c constraints.txt",
    ]
    lines += [f"{name}=={pins[name]}" for name in sorted(pins)]
    out = ROOT / "constraints.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{out} — {len(pins)}개 고정")
    # `hair check`가 비교할 핵심 패키지 버전(패키지에 함께 배포 — constraints.txt는 wheel에 안 들어간다)
    key = {n: pins[n] for n in KEY_PACKAGES if n in pins}
    body = "\n".join(f"    {n!r}: {v!r}," for n, v in key.items())
    (ROOT / "src" / "hair_platform" / "verified_versions.py").write_text(
        '"""자동 생성 — scripts/update_constraints.py (직접 고치지 말 것).\n\n'
        '전체 테스트를 통과한 조합의 핵심 패키지 버전. `hair check`가 설치된 버전과 비교해 경고한다."""\n\n'
        f"VERIFIED = {{\n{body}\n}}\n",
        encoding="utf-8",
    )
    bad = [n for n in pins if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", n)]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
