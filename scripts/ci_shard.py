"""CI 테스트를 N개 묶음으로 나눠 병렬로 돌린다(2026-11-27, 개선 제안 6-1 — CI 한 번에 약 27분).

`tests/test_durations.json`(파일별 실행 시간, 초)을 보고 오래 걸리는 파일부터 가장 가벼운 묶음에 넣는다(시간 균형).
기록에 없는 새 파일은 테스트 함수 수 × 평균 시간으로 어림한다. 나눔 단위는 파일 — 같은 파일의 테스트는 같은 묶음에서
지금처럼 한 프로세스로 돈다(파일 안 테스트 순서·픽스처 범위가 그대로).

    python scripts/ci_shard.py 4 0          # 0번 묶음의 테스트 파일 목록(공백 구분)
    python scripts/ci_shard.py 4 --summary  # 묶음별 예상 시간

시간 기록 갱신: `PYTEST_DURATIONS_OUT=tests/test_durations.json pytest tests -q -p no:cacheprovider`
(`tests/conftest.py`가 파일별로 합쳐 저장한다).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
DURATIONS = TESTS / "test_durations.json"


def test_files() -> list[str]:
    # e2e는 별도 작업(화면 테스트)에서 돈다 — 여기 묶음에는 넣지 않는다.
    return sorted(str(p.relative_to(ROOT)) for p in TESTS.rglob("test_*.py") if "e2e" not in p.parts and ".claude" not in p.parts)


def estimate(files: list[str]) -> dict[str, float]:
    known = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    per_test = []
    counts = {}
    for f in files:
        n = len(re.findall(r"^\s*(?:async\s+)?def test_", (ROOT / f).read_text(encoding="utf-8"), re.MULTILINE))
        counts[f] = max(1, n)
        if f in known:
            per_test.append(known[f] / counts[f])
    avg = (sum(per_test) / len(per_test)) if per_test else 1.0
    return {f: float(known.get(f, counts[f] * avg)) for f in files}


def shard(n: int) -> list[list[str]]:
    files = test_files()
    cost = estimate(files)
    buckets: list[list[str]] = [[] for _ in range(n)]
    totals = [0.0] * n
    for f in sorted(files, key=lambda x: (-cost[x], x)):
        i = totals.index(min(totals))
        buckets[i].append(f)
        totals[i] += cost[f]
    return [sorted(b) for b in buckets]


def main(argv: list[str]) -> int:
    n = int(argv[1])
    buckets = shard(n)
    if argv[2] == "--summary":
        cost = estimate(test_files())
        for i, b in enumerate(buckets):
            print(f"shard {i}: {len(b)} files, ~{sum(cost[f] for f in b) / 60:.1f} min")
        return 0
    print(" ".join(buckets[int(argv[2])]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
