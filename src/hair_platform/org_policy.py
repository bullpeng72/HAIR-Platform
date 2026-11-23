"""조직 정책 설정 — 사번 형식(2026-11-21, "사번 형식이랑 초기화 보호 팀 설정화 진행해줘").

지금까지 사번 형식이 `models.py`에 숫자 8~12자리 정규식으로 고정돼 있어, 형식이 다른
조직(영문 접두어·하이픈 포함 등)은 사번을 등록할 수 없었다. admin이 "운영 관리"에서
형식(정규식)과 사용자에게 보여 줄 설명을 바꿀 수 있게 한다. 기본값은 기존 규칙 그대로다.

입력 검증(`models.py`의 pydantic 검증기)은 DB 연결 없이 돌기 때문에, 값은 기동 시
(`db.init_schema`)와 변경 시(`set_employee_id_policy`)에 이 모듈의 캐시로 올린다
(`model_options.py`와 같은 방식).
"""

from __future__ import annotations

import re
import sqlite3

DEFAULT_PATTERN = r"^\d{8,12}$"
DEFAULT_HINT = "숫자 8~12자리"
_MAX_PATTERN_CHARS = 200
# 사번은 짧다 — 관리자가 넣은 정규식이 비효율적이어도 긴 입력으로 오래 돌지 않게 자른다.
_MAX_EMPLOYEE_ID_CHARS = 64

_STATE: dict = {"pattern": DEFAULT_PATTERN, "hint": DEFAULT_HINT, "regex": re.compile(DEFAULT_PATTERN)}

# 결과물 언어(2026-11-22, 하드코딩·스텁 점검 4순위) — 지금까지 모든 경로가 한국어 결과물을 전제했다
# (언어 교정 `language_qa`는 한자·가나·베트남 문자를 전부 한국어로 바꾼다). 조직이 다른 언어로
# 일하면 그 언어로 쓰게 지시하고, 한국어 전용 교정은 끈다. 코드 → (화면 이름, 지시문에 쓸 이름).
OUTPUT_LANGUAGES = {
    "ko": ("한국어", "Korean"),
    "en": ("English", "English"),
    "ja": ("日本語", "Japanese"),
    "zh": ("中文", "Chinese"),
    "vi": ("Tiếng Việt", "Vietnamese"),
}
DEFAULT_OUTPUT_LANGUAGE = "ko"
_STATE["output_language"] = DEFAULT_OUTPUT_LANGUAGE


def refresh(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute(
            "SELECT employee_id_pattern, employee_id_hint FROM org_policy WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:  # 테이블이 생기기 전(마이그레이션 도중)
        row = None
    try:
        lang_row = conn.execute("SELECT output_language FROM org_policy WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        lang_row = None
    language = lang_row[0] if lang_row and lang_row[0] in OUTPUT_LANGUAGES else DEFAULT_OUTPUT_LANGUAGE
    _STATE["output_language"] = language
    pattern = (row[0] if row and row[0] else None) or DEFAULT_PATTERN
    hint = (row[1] if row and row[1] else None) or DEFAULT_HINT
    try:
        regex = re.compile(pattern)
    except re.error:  # 저장 시 검증하지만, DB를 직접 고친 경우를 대비해 기본값으로
        pattern, hint, regex = DEFAULT_PATTERN, DEFAULT_HINT, re.compile(DEFAULT_PATTERN)
    _STATE.update(pattern=pattern, hint=hint, regex=regex)


def employee_id_policy() -> dict:
    return {
        "pattern": _STATE["pattern"],
        "hint": _STATE["hint"],
        "is_default": _STATE["pattern"] == DEFAULT_PATTERN,
    }


def validate_employee_id(value: str | None) -> str | None:
    """pydantic 검증기용 — 비어 있으면(None) 통과(사번은 선택 입력)."""
    if value is None:
        return None
    if len(value) > _MAX_EMPLOYEE_ID_CHARS or not _STATE["regex"].fullmatch(value):
        raise ValueError(f"employee_id 형식이 맞지 않습니다({_STATE['hint']})")
    return value


def set_employee_id_policy(
    conn: sqlite3.Connection, actor_id: int, pattern: str | None, hint: str | None
) -> dict:
    """사번 형식 변경(admin). `pattern`을 비우면 기본값으로 되돌린다. 반환값에 지금
    등록된 사번 중 새 형식에 맞지 않는 건수를 담아 화면이 알려 줄 수 있게 한다(기존
    값을 지우거나 바꾸지는 않는다)."""
    from hair_platform import audit

    pattern = (pattern or "").strip() or None
    hint = (hint or "").strip() or None
    if pattern is not None:
        if len(pattern) > _MAX_PATTERN_CHARS:
            raise ValueError("pattern_too_long")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid_pattern:{exc}") from None
    else:
        regex = re.compile(DEFAULT_PATTERN)
    before = employee_id_policy()
    conn.execute(
        "INSERT INTO org_policy (id, employee_id_pattern, employee_id_hint) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET employee_id_pattern = excluded.employee_id_pattern, "
        "employee_id_hint = excluded.employee_id_hint",
        (pattern, hint),
    )
    refresh(conn)
    after = employee_id_policy()
    audit.record(
        conn, actor_id, "org_policy_employee_id_changed", "org_policy", 1, before=before, after=after
    )
    conn.commit()
    mismatched = sum(
        1
        for (emp,) in conn.execute(
            "SELECT employee_id FROM users WHERE employee_id IS NOT NULL "
            "UNION ALL SELECT employee_id FROM human_pool WHERE employee_id IS NOT NULL"
        )
        if not regex.fullmatch(emp)
    )
    return {**after, "existing_mismatched": mismatched}


def output_language() -> str:
    return _STATE.get("output_language", DEFAULT_OUTPUT_LANGUAGE)


def output_language_policy() -> dict:
    code = output_language()
    return {
        "code": code,
        "name": OUTPUT_LANGUAGES[code][0],
        "options": [{"code": c, "name": n} for c, (n, _) in OUTPUT_LANGUAGES.items()],
    }


def output_language_instruction() -> str:
    """에이전트 프롬프트에 붙이는 결과물 언어 지시 — 기본(한국어)이면 빈 문자열(프롬프트가 이미
    한국어라 기존과 같다)."""
    code = output_language()
    if code == DEFAULT_OUTPUT_LANGUAGE:
        return ""
    name, english = OUTPUT_LANGUAGES[code]
    return (
        f"[결과물 언어] 모든 결과물은 {name}로 작성한다(지시문이 한국어여도). "
        f"Write every deliverable in {english}."
    )


def set_output_language(conn: sqlite3.Connection, actor_id: int, code: str) -> dict:
    from hair_platform import audit

    if code not in OUTPUT_LANGUAGES:
        raise ValueError(f"unsupported_output_language:{code}")
    before = output_language()
    conn.execute(
        "INSERT INTO org_policy (id, output_language) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET output_language = excluded.output_language",
        (code,),
    )
    refresh(conn)
    audit.record(
        conn, actor_id, "org_policy_output_language_changed", "org_policy", 1,
        before={"output_language": before}, after={"output_language": code},
    )
    conn.commit()
    return output_language_policy()
