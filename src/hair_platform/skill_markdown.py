"""Skill Pool ↔ 마크다운(frontmatter + 섹션) 변환.

사용자 요청(2026-11-17, "skill을 정해진 섹션 규격을 참조해 구조화하고,
외부에서 마크다운 포멧의 Skill을 import하거나, 마크다운 규격으로
export") — `pool_registry.create_skill()`/`update_skill()`이 이미
`description`/`output_format`/`examples`/`constraints` 4개 필드로
구조화돼 있으므로(앞선 분석에서 리터럴 YAML-in-text 방식 대신 선택한
전용 컬럼 설계), 이 모듈은 그 4필드 ↔ 마크다운 본문 섹션(## 지침/
## 출력 형식/## 예시/## 제약사항)을 그대로 매핑하고, frontmatter는
`create_skill()`의 나머지 메타(name/category/augmentation_mode/tool
참조)만 담당한다 — "설명 자체를 마크다운 규격 텍스트로 지어내 본문에
섞어 넣는" 방식이 아니다(그 방식은 로컬 모델이 그 포맷 안내문 자체를
산출물로 되풀이한 기존 버그와 같은 위험을 다시 만든다, `pool_registry.
SKILL_KNOWLEDGE_GUIDANCE` 참고).

PyYAML을 새 의존성으로 추가하지 않는다 — frontmatter 키가
`name`/`category`/`augmentation_mode`/`tool` 4개뿐인 단순
스칼라 맵이라, 전용 파서가 더 안전하고 예측 가능하다.
"""

from __future__ import annotations

import json
import re
import sqlite3

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)", re.DOTALL)
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

_SECTION_TO_FIELD = {
    "지침": "description",
    "출력 형식": "output_format",
    "예시": "examples",
    "제약사항": "constraints",
}
_SECTION_ORDER = ("지침", "출력 형식", "예시", "제약사항")

_ALLOWED_AUGMENTATION_MODES = ("tool", "always", "none")
_ALLOWED_CATEGORIES = ("sw_dev", "general")


def _resolve_tool_native_id(conn: sqlite3.Connection, skill: dict) -> str | None:
    """`default_tool_pool_ids`가 native Tool 1개만 가리킬 때만
    frontmatter `tool:`로 내보낸다 — mcp_remote/mcp_stdio/custom_python
    이나 여러 개를 가리키는 경우는 복원 가능한 단일 참조가 아니므로
    생략한다(import 시 `default_tool_pool_ids=[]`로 남을 뿐, 데이터
    손실 없음 — `description` 등 지시문은 그대로 보존됨)."""
    tool_ids = skill.get("default_tool_pool_ids") or []
    if len(tool_ids) != 1:
        return None
    row = conn.execute(
        "SELECT * FROM tool_pool WHERE id = ? AND is_active = 1", (tool_ids[0],)
    ).fetchone()
    if row is None or row["kind"] != "native":
        return None
    return json.loads(row["config"]).get("native_id")


def export_skill_markdown(conn: sqlite3.Connection, skill: dict) -> str:
    """`pool_registry.list_skills()`가 반환하는 dict 1건을 마크다운
    문자열로 직렬화한다."""
    lines = [
        "---",
        f"name: {skill['name']}",
        f"category: {skill['category']}",
        f"augmentation_mode: {skill['augmentation_mode']}",
    ]
    tool_native_id = _resolve_tool_native_id(conn, skill)
    if tool_native_id:
        lines.append(f"tool: {tool_native_id}")
    lines.append("---")
    lines.append("")
    for section in _SECTION_ORDER:
        value = skill.get(_SECTION_TO_FIELD[section])
        if not value:
            continue
        lines.append(f"## {section}")
        lines.append("")
        lines.append(value.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_frontmatter(text: str) -> dict[str, str]:
    fm: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _parse_sections(body: str) -> dict[str, str]:
    headers = list(_SECTION_HEADER_RE.finditer(body))
    sections: dict[str, str] = {}
    for i, m in enumerate(headers):
        heading = m.group(1).strip()
        field = _SECTION_TO_FIELD.get(heading)
        if field is None:
            continue
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        sections[field] = body[start:end].strip()
    return sections


def parse_skill_markdown(conn: sqlite3.Connection, text: str) -> dict:
    """마크다운 문자열을 `pool_registry.create_skill()`에 바로 넘길
    수 있는 kwargs dict로 변환한다. 실패 시 `ValueError` — 호출부
    (`app.py`의 import 엔드포인트)가 그대로 400으로 매핑한다."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("missing_frontmatter")
    fm = _parse_frontmatter(m.group(1))
    name = fm.get("name", "").strip()
    if not name:
        raise ValueError("missing_name")

    category = fm.get("category") or "general"
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError(f"unsupported_category:{category}")

    augmentation_mode = fm.get("augmentation_mode") or "none"
    if augmentation_mode not in _ALLOWED_AUGMENTATION_MODES:
        raise ValueError(f"unsupported_augmentation_mode:{augmentation_mode}")

    default_tool_pool_ids: list[int] = []
    tool_native_id = fm.get("tool")
    if tool_native_id:
        row = conn.execute(
            "SELECT id, config FROM tool_pool "
            "WHERE kind = 'native' AND is_active = 1"
        ).fetchall()
        match = next(
            (r for r in row if json.loads(r["config"]).get("native_id") == tool_native_id),
            None,
        )
        if match is None:
            raise ValueError(f"unknown_tool_native_id:{tool_native_id}")
        default_tool_pool_ids = [match["id"]]

    sections = _parse_sections(m.group(2))
    return {
        "name": name,
        "description": sections.get("description", ""),
        "default_tool_pool_ids": default_tool_pool_ids,
        "augmentation_mode": augmentation_mode,
        "category": category,
        "output_format": sections.get("output_format"),
        "examples": sections.get("examples"),
        "constraints": sections.get("constraints"),
    }
