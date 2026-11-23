"""스킬·도구 목록 조회 — "스킬생성" 스킬이 실제로 쓰는 네이티브 도구.

사용자 요청(2026-11-21, 우선순위 1 스킬 추가). "스킬생성"의 지침에는 "기존 스킬과
겹치는지 먼저 확인"·"등록된 도구만 참조"가 있었지만, 연결된 도구가 없어 모델이
스킬 풀을 볼 방법이 없었다. 이 모듈이 현재 등록된 스킬(이름·분류·지침 요약)과
사용 가능한 도구(이름·종류·설명 요약)를 한 덩어리 텍스트로 돌려준다.

- 대화·단일 Agent 경로: `agent_runtime.retrieve_context()`가 정적으로 주입한다.
- Crew 실행 경로: `as_crewai_tool()`로 감싼 진짜 Tool을 Agent가 스스로 호출한다.

`pool_registry`를 import하지 않는다(그쪽이 이 모듈을 import하므로 순환 방지).
"""

from __future__ import annotations

import json
import re
import sqlite3

NATIVE_ID = "skill_catalog_lookup"
TOOL_NAME = "스킬·도구 목록 조회"
TOOL_DESCRIPTION = (
    "등록된 스킬(이름·분류·지침 요약)과 사용 가능한 도구 목록을 조회하는 네이티브 Tool — "
    "새 스킬을 설계하기 전에 중복 여부와 연결할 수 있는 도구를 확인한다."
)

_SUMMARY_CHARS = 90
# 영문·숫자와 한글을 따로 끊는다("API설계" → "API", "설계").
_TOKEN_RE = re.compile(r"[0-9A-Za-z]{2,}|[가-힣]{2,}")


def _summary(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _SUMMARY_CHARS else text[: _SUMMARY_CHARS - 1] + "…"


# 거의 모든 스킬 설명에 나와 후보를 가려내지 못하는 말(어간 두 글자 기준).
_STOP_STEMS = {"스킬", "하는", "업무", "작성", "관련", "위한", "대한", "기능", "만들", "필요", "있는", "결과"}


def _stems(text: str) -> set[str]:
    """조사·어미가 붙은 한국어 단어를 맞추려고 한글 단어는 앞 두 글자(어간 근사),
    영문·숫자는 소문자 전체로 비교한다."""
    out = set()
    for tok in _TOKEN_RE.findall(text or ""):
        stem = tok[:2] if "가" <= tok[0] <= "힣" else tok.lower()
        if stem not in _STOP_STEMS:
            out.add(stem)
    return out


def _similar(query: str, rows: list[sqlite3.Row], limit: int = 5) -> list[str]:
    """질의와 어간이 겹치는 스킬 이름(중복 후보) — 의미 검색이 아니라 단순 겹침이라
    후보 제시용이다. 판단은 모델이 목록 전체를 보고 한다. 두 개 이상 겹쳐야 후보."""
    words = _stems(query)
    if not words:
        return []
    scored = []
    for r in rows:
        score = len(words & _stems(f"{r['name']} {r['description'] or ''}"))
        if score >= 2:
            scored.append((score, r["name"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored[:limit]]


def context_for_prompt(conn: sqlite3.Connection, query: str = "") -> str:
    skills = conn.execute(
        "SELECT name, category, description, augmentation_mode FROM skill_pool ORDER BY category, name"
    ).fetchall()
    tools = conn.execute(
        "SELECT name, kind, description FROM tool_pool WHERE is_active = 1 ORDER BY name"
    ).fetchall()
    lines = [f"[스킬·도구 목록 조회 결과] 스킬 {len(skills)}개, 사용 가능한 도구 {len(tools)}개"]
    similar = _similar(query, skills)
    if similar:
        lines.append("요청과 단어가 겹치는 기존 스킬(중복 후보): " + ", ".join(similar))
    lines.append("\n## 등록된 스킬 (이름 | 분류 | 방식 | 지침 요약)")
    lines += [
        f"- {r['name']} | {r['category']} | {r['augmentation_mode']} | {_summary(r['description'])}"
        for r in skills
    ]
    lines.append("\n## 사용 가능한 도구 (이름 | 종류 | 설명 요약) — tool 방식 스킬은 이 이름만 참조한다")
    lines += [f"- {r['name']} | {r['kind']} | {_summary(r['description'])}" for r in tools]
    return "\n".join(lines)


def as_crewai_tool(conn: sqlite3.Connection):
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    # 한글 함수명은 crewai가 ASCII로 정규화하며 비워 버린다(graphquery.py 참고).
    @tool("skill_tool_catalog")
    def lookup_skill_tool_catalog(query: str = "") -> str:
        """등록된 스킬과 사용 가능한 도구 목록을 조회한다. 새 스킬을 설계하기 전에
        같은 역할의 스킬이 이미 있는지, 연결할 수 있는 도구가 무엇인지 확인할 때
        호출하라. query에는 만들려는 스킬의 업무 설명을 넣는다."""
        return context_for_prompt(conn, query)

    return lookup_skill_tool_catalog


ROLE_SUGGESTION_MIN_SCORE = 2


def suggest_roles(conn: sqlite3.Connection, request_text: str, limit: int = 6) -> list[str]:
    """업무 요청 본문과 맞는 기존 Agent 역할을 고른다 — LLM 없이(무료) 역할명·목표·보유 스킬의
    이름·설명과 요청 본문의 어간이 겹치는 정도로 점수를 매긴다(역할명·목표 일치는 2배).
    하드코딩·스텁 점검(2026-11-22) 2순위: 예전에는 요청을 줄바꿈으로 나눈 줄을 "역할 후보"로
    보여 줬다(`app.parse_brief_stub`). 맞는 역할이 없으면 빈 목록 — 호출자가 줄 단위로 폴백한다."""
    query = _stems(request_text)
    if not query:
        return []
    skills = {
        r["name"]: f"{r['name']} {r['description'] or ''}"
        for r in conn.execute("SELECT name, description FROM skill_pool")
    }
    scored: list[tuple[int, str]] = []
    for row in conn.execute(
        "SELECT role_name, goal, skill_tags FROM agent_pool WHERE status = 'active'"
    ):
        try:
            raw_tags = json.loads(row["skill_tags"] or "[]")
        except ValueError:
            raw_tags = []
        # 문자열 태그만 쓴다 — JSON에 null·숫자가 섞여 있으면 join이 TypeError로 깨졌다(2026-11-23).
        tags: list[str] = [t for t in raw_tags if isinstance(t, str)] if isinstance(raw_tags, list) else []
        own = _stems(f"{row['role_name']} {row['goal'] or ''}")
        skill_text = " ".join(skills.get(t) or t for t in tags)
        score = 2 * len(query & own) + len(query & (_stems(skill_text) - own))
        if score >= ROLE_SUGGESTION_MIN_SCORE:
            scored.append((score, row["role_name"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [role for _, role in scored[:limit]]
