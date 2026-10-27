"""Agent/Tool/Skill 표시용 코드 체계 — 사용자 요청(2026-10-27, "에이전트,
도구/skill ID 체계를 수립하고(종류 구분 가능) 초기화하여 정리해줘").

`agent_pool.id`/`tool_pool.id`/`skill_pool.id`(정수 AUTOINCREMENT)는
`agent_tool_links`/`team_members`/`crew_runs`/`conversation_jobs`/
`knowledge_chunks` 등 여러 테이블이 FK로, `/api/agent-pool/{id}`류
URL 경로가 그대로 쓰고 있어 바꾸지 않는다(사용자 확인, 2026-10-27 —
"기본키 교체"가 아니라 "표시용 코드 추가"를 선택). 대신 사람이 보고
바로 "무슨 종류인지" 알 수 있는 코드를 `code` 컬럼에 별도로 둔다.

형식: `<PREFIX>-<종류>-<일련번호 3자리>` (예: `AGT-REF-001`,
`TUL-NATIVE-001`, `SKL-TOOL-001`). 종류 축은 풀마다 이미 있는 분류를
그대로 재사용한다(새 분류 체계를 억지로 만들지 않는다):

- **Agent**(`AGT-`): `reference_pipeline_seeds.AGENT_SPECS`에 있는
  22종은 `REF`, 그 외(관리자가 "AI 에이전트 생성"으로 만들었거나
  데모 시드로 만든 것)는 `CUSTOM`.
- **Tool**(`TUL-`): `tool_pool.kind` 그대로(`NATIVE`/`MCPSTDIO`/
  `MCPRMT`/`PYEXEC`).
- **Skill**(`SKL-`): `skill_pool.augmentation_mode` 그대로(`TOOL`/
  `ALWAYS`/`NONE`).

일련번호는 같은 (테이블, PREFIX, 종류) 조합 안에서 `code`에 이미 쓰인
최댓값 다음 번호다 — 등록 순서가 아니라 "그 종류 안에서 몇 번째로
코드가 배정됐는지"라서, 중간 행이 지워져도 다음 배정 때 번호가 꼬이지
않는다(빈 번호가 재사용되지 않을 뿐).

**새로 생성될 때**: `pool_registry.create_tool()`/`create_skill()`,
Agent 생성 경로(`jobs.py`/`agent_runtime.py`/`data_admin.
seed_demo_data()`)가 INSERT 직후 바로 `assign_code()`를 불러 그
자리에서 코드를 배정한다 — API 응답에 생성 직후부터 코드가 보인다.
**기존/레퍼런스 데이터**: `backfill_missing_codes()`가
`db.init_schema()`에서 시드가 전부 끝난 뒤 한 번 돌아, 아직 `code`가
없는 행에만 배정한다(이미 코드가 있으면 손대지 않음 — 몇 번을 다시
불러도 안전)."""

from __future__ import annotations

import sqlite3

# tool_pool.kind / skill_pool.augmentation_mode 실제 값 -> 코드 세그먼트.
# 새 kind/augmentation_mode가 추가되면 여기도 같이 늘려야 한다(안 늘리면
# assign_code()가 ValueError로 바로 알려준다 — 조용히 틀린 코드를 만들지
# 않는다).
TOOL_KIND_SEGMENT: dict[str, str] = {
    "native": "NATIVE",
    "mcp_stdio": "MCPSTDIO",
    "mcp_remote": "MCPRMT",
    "custom_python": "PYEXEC",
}
SKILL_MODE_SEGMENT: dict[str, str] = {
    "tool": "TOOL",
    "always": "ALWAYS",
    "none": "NONE",
}
AGENT_REF_SEGMENT = "REF"
AGENT_CUSTOM_SEGMENT = "CUSTOM"

_TABLE_PREFIX: dict[str, str] = {
    "agent_pool": "AGT",
    "tool_pool": "TUL",
    "skill_pool": "SKL",
}


def assign_code(
    conn: sqlite3.Connection, table: str, row_id: int, segment: str, width: int = 3
) -> str:
    """`table`(`agent_pool`/`tool_pool`/`skill_pool`)의 `row_id` 행에
    `"<PREFIX>-<segment>-<일련번호>"` 코드를 배정해 저장하고 그 코드를
    반환한다. `table`은 호출자가 하드코딩한 상수만 넘기는 내부 전용
    함수라(`_TABLE_PREFIX`에 없는 값이면 바로 KeyError) SQL 문자열에
    그대로 꽂아도 인젝션 위험이 없다."""
    prefix = _TABLE_PREFIX[table]
    head = f"{prefix}-{segment}-"
    rows = conn.execute(
        f"SELECT code FROM {table} WHERE code LIKE ?", (f"{head}%",)
    ).fetchall()
    max_seq = 0
    for row in rows:
        code = row["code"]
        if not code:
            continue
        suffix = code[len(head) :]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    code = f"{head}{max_seq + 1:0{width}d}"
    conn.execute(f"UPDATE {table} SET code = ? WHERE id = ?", (code, row_id))
    return code


def _tool_segment(kind: str) -> str:
    try:
        return TOOL_KIND_SEGMENT[kind]
    except KeyError as exc:
        raise ValueError(f"unknown_tool_kind_for_code:{kind}") from exc


def _skill_segment(augmentation_mode: str) -> str:
    try:
        return SKILL_MODE_SEGMENT[augmentation_mode]
    except KeyError as exc:
        raise ValueError(
            f"unknown_augmentation_mode_for_code:{augmentation_mode}"
        ) from exc


def backfill_missing_codes(conn: sqlite3.Connection) -> dict[str, int]:
    """`code`가 아직 없는 모든 `agent_pool`/`tool_pool`/`skill_pool`
    행에 배정한다. `id` 오름차순(= 등록된 순서)으로 돌아, 같은 세션
    안에서 여러 번 불러도 이미 코드가 있는 행은 건드리지 않는다
    (`db.init_schema()`가 매 기동마다 부르므로 반드시 이래야 한다).
    반환값은 이번 호출에서 새로 코드를 받은 행 수(테이블별)."""
    from hair_platform.reference_pipeline_seeds import AGENT_SPECS

    reference_role_names = {role_name for role_name, _skills, _tuning in AGENT_SPECS}

    counts = {"agent_pool": 0, "tool_pool": 0, "skill_pool": 0}

    for row in conn.execute(
        "SELECT id, role_name FROM agent_pool WHERE code IS NULL ORDER BY id"
    ).fetchall():
        segment = (
            AGENT_REF_SEGMENT
            if row["role_name"] in reference_role_names
            else AGENT_CUSTOM_SEGMENT
        )
        assign_code(conn, "agent_pool", row["id"], segment)
        counts["agent_pool"] += 1

    for row in conn.execute(
        "SELECT id, kind FROM tool_pool WHERE code IS NULL ORDER BY id"
    ).fetchall():
        assign_code(conn, "tool_pool", row["id"], _tool_segment(row["kind"]))
        counts["tool_pool"] += 1

    for row in conn.execute(
        "SELECT id, augmentation_mode FROM skill_pool WHERE code IS NULL ORDER BY id"
    ).fetchall():
        assign_code(
            conn, "skill_pool", row["id"], _skill_segment(row["augmentation_mode"])
        )
        counts["skill_pool"] += 1

    if any(counts.values()):
        conn.commit()
    return counts


def reset_and_reassign_codes(conn: sqlite3.Connection) -> dict[str, int]:
    """사용자 요청(2026-10-27, "기존 ID를 수립하고 초기화하여 재
    부여해줘") — 세 테이블의 `code`를 전부 지우고 `backfill_missing_
    codes()`로 처음부터 다시 배정한다. 배정 로직이 `id` 오름차순으로
    결정적이라(`assign_code()` 참고) 중간에 삭제된 행이 없다면 결과
    코드는 기존과 동일하다 — "초기화 후 재배정"을 실제로 수행했음을
    보장하기 위한 것이지, 코드 값 자체를 바꾸려는 목적이 아니다."""
    for table in ("agent_pool", "tool_pool", "skill_pool"):
        conn.execute(f"UPDATE {table} SET code = NULL")
    conn.commit()
    return backfill_missing_codes(conn)
