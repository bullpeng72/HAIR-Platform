"""Skill Pool·Tool Pool — 사용자 요청(2026-09-27, "tool_pool·
skill_pool 테이블도 실제로 구현해줘").

이전까지 "어떤 skill_tags를 가진 Agent가 어떤 도구를 쓰는지"는 두 곳에
흩어져 있었다: `agent_runtime.py`의 `VECTORDB_SKILL_TAGS`/`GRAPHDB_SKILL_TAGS`
하드코딩(스킬→동작 매핑), 그리고 `AgentModelConfig.mcp_servers`(Agent마다
개별 저장하는 MCP 설정, 재사용 불가). 이 모듈이 그 둘을 데이터로 통합한다.

- `tool_pool` — 실제 도구(MCP 서버 또는 native `@tool`) 하나를 이름으로
  등록해 여러 Agent가 재사용한다.
- `skill_pool` — 자유 문자열이던 `skill_tags`를 정식 엔티티로 승격한다.
  `augmentation_mode="tool"`인 스킬은 `default_tool_pool_ids`가 가리키는
  도구를 자동으로 붙인다.
- `agent_tool_links` — Agent별로 Tool Pool 항목을 명시적으로 추가/제거할
  수 있는 N:M 연결(스킬 매칭과 무관하게 개별 Agent에 도구를 하나 더
  붙이고 싶을 때 쓴다).

`resolve_pool_tools()`가 이 셋을 합쳐 실제 CrewAI `Tool` 인스턴스 목록을
만든다 — `crew_runtime.py`가 `run_crew()`/`crewai_agent_generator()`에서
그대로 쓴다. MCP 접속(`connect_mcp_tools`)은 `crew_runtime._mcp_tools()`
(`AgentModelConfig.mcp_servers` 경로)와도 공유한다 — 같은 접속 로직을
두 곳에 중복시키지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack

from hair_platform import audit, graphquery, id_scheme, sandbox, vectorstore, websearch

NATIVE_TOOL_FACTORIES = {
    "vectordb_search": vectorstore.as_crewai_tool,
    "graphdb_query": graphquery.as_crewai_tool,
    # "web_search"(Wikipedia)는 사용자 요청(2026-10-26, "도구/skill에서
    # 동작하지 않는 검색은 삭제해줘")로 뺐다 — Wikimedia가 이 배포
    # 환경의 IP를 지속 차단 중이라 db.py 시드에서도 빠졌다(세부 사유는
    # `db.py`의 `_seed_default_pools()` 주석 참고). 코드 자체
    # (`websearch.as_crewai_tool`)는 지우지 않았다 — 되살리려면 여기에
    # 다시 추가.
    "tavily_search": websearch.as_tavily_crewai_tool,
}


def mcp_serverparams(cfg: dict):
    """MCP 서버 접속 파라미터를 만든다 — `command`가 있으면 stdio(로컬
    프로세스), 없으면 `url` 기반 SSE/streamable-http로 판별한다(`crewai-
    tools`/`mcpadapt` 실제 소스로 확인한 형태)."""
    if cfg.get("command"):
        from mcp import StdioServerParameters  # 지연 import — crewai-tools[mcp] 필요

        return StdioServerParameters(
            command=cfg["command"], args=cfg.get("args") or [], env=cfg.get("env")
        )
    return {"url": cfg["url"], "transport": cfg.get("transport", "sse")}


def connect_mcp_tools(stack: ExitStack, cfg: dict) -> list:
    """MCP 서버 설정 하나에 실제로 접속해 CrewAI Tool 목록을 연다.

    **주의(실측 확인)**: `MCPServerAdapter(...)`는 생성 시점에 실제로
    접속을 시도한다(기본 `connect_timeout=30초`) — 지연 실행이 아니다.
    접속 가능한 실제 MCP 서버가 없는 환경에서는 이 함수를 안전하게 mock
    하려면 `crewai_tools.MCPServerAdapter` 클래스 자체를 patch해야 한다."""
    from crewai_tools import MCPServerAdapter  # 지연 import — 안 쓰면 설치 불필요

    serverparams = mcp_serverparams(cfg)
    tool_names = cfg.get("tool_names") or []
    adapter = stack.enter_context(MCPServerAdapter(serverparams, *tool_names))
    return list(adapter)


# 사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능을
# 제공해줘") — 지금까지 `NATIVE_TOOL_FACTORIES`가 하드코딩 dict라
# 관리자가 새 `@tool` 함수를 쓰려면 반드시 개발자가 코드를 고쳐야 했다
# (자유도 낮음, 직전 분석에서 지적한 지점). `tool_pool.kind=
# 'custom_python'`으로 Python 함수 소스를 등록하면 코드 수정 없이 새
# 도구를 추가할 수 있게 한다.
#
# **신뢰 경계를 솔직히 기록한다**: 이건 테넌트 격리가 아니라 admin
# 신뢰다 — admin은 이미 `mcp_stdio` 도구로 임의 명령을 서버에서 띄울 수
# 있으므로(`app.py`의 "(a) MCP 종류만 admin 전용으로 좁히기" 결정 참고),
# 이 기능이 admin에게 새로 주는 권한은 사실상 없다(그래서 같은 등급으로
# admin 전용으로 묶는다 — 아래 `app.py` 쪽 게이팅 참고). Docker가 있으면
# `--network=none`으로 격리 실행하고, 없으면 평범한 subprocess로
# 폴백한다(`sandbox.run_python_script()`) — smoke-test와 달리 "조용히
# 건너뛰기"는 못 한다. 실행 중인 Agent가 그 턴을 완료하려면 이 호출이
# 실제 값을 반환해야 하기 때문이다.
_CUSTOM_TOOL_MAX_OUTPUT_CHARS = 4000


def _validate_custom_python_source(source: str) -> None:
    """등록 시점에 최소한의 안전성(문법 오류·함수 존재)만 확인한다 —
    실제 실행은 호출 시점에 샌드박스/서브프로세스에서 한다(등록 시점에
    미리 돌려보지 않는다 — 등록 자체는 부작용이 없어야 한다)."""
    if not source or not source.strip():
        raise ValueError("empty_function_source")
    if "def tool_fn(" not in source:
        raise ValueError("function_source_must_define_tool_fn")
    try:
        compile(source, "<custom_tool>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"function_source_syntax_error:{exc}") from None


def _run_custom_python_tool(source: str, query: str, timeout: int) -> str:
    runner_script = (
        source + "\nimport sys, json\nprint(json.dumps(tool_fn(sys.argv[1])))\n"
    )
    result = sandbox.run_python_script(runner_script, [query], timeout=timeout)
    if not result.ok:
        return f"(도구 실행 오류: {(result.stderr or '알 수 없는 오류').strip()[:300]})"
    try:
        output = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        output = result.stdout.strip()
    return str(output)[:_CUSTOM_TOOL_MAX_OUTPUT_CHARS]


def _custom_python_crewai_tool(row: sqlite3.Row):
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    config = json.loads(row["config"])
    source = config.get("function_source", "")
    timeout = int(config.get("timeout_seconds") or 10)
    description = row["description"] or "관리자가 등록한 커스텀 도구"
    # ASCII 전용 함수명 필요(2026-10-06 실측 버그 — vectorstore.as_crewai_tool()
    # 참고) — 한글 이름을 그대로 쓰면 crewai의 sanitize_tool_name()이 전부
    # 날려 빈 함수명 예외가 난다. tool_pool.id가 유일하므로 ASCII로
    # 안전하다.
    ascii_tool_name = f"custom_tool_{row['id']}"

    def _dynamic(query: str) -> str:
        return _run_custom_python_tool(source, query, timeout)

    _dynamic.__doc__ = description
    return tool(ascii_tool_name)(_dynamic)


def create_tool(
    conn: sqlite3.Connection,
    actor_id: int,
    name: str,
    description: str,
    kind: str,
    config: dict,
    tool_names: list[str] | None = None,
) -> int:
    if kind not in ("mcp_stdio", "mcp_remote", "native", "custom_python"):
        raise ValueError(f"unsupported_kind:{kind}")
    if kind == "native" and config.get("native_id") not in NATIVE_TOOL_FACTORIES:
        raise ValueError(f"unknown_native_id:{config.get('native_id')}")
    if kind == "custom_python":
        _validate_custom_python_source(config.get("function_source", ""))
    cur = conn.execute(
        "INSERT INTO tool_pool (name, description, kind, config, tool_names) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            name,
            description,
            kind,
            json.dumps(config, ensure_ascii=False),
            json.dumps(tool_names or []),
        ),
    )
    tool_id = cur.lastrowid
    assert tool_id is not None
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 레퍼런스 시드 쪽은
    # `id_scheme.backfill_missing_codes()`가 다음 기동 때 채우지만,
    # admin이 지금 막 만든 건 API 응답에 바로 코드가 보여야 하므로
    # 여기서 즉시 배정한다.
    id_scheme.assign_code(conn, "tool_pool", tool_id, id_scheme.TOOL_KIND_SEGMENT[kind])
    audit.record(
        conn,
        actor_id,
        "tool_pool_created",
        "tool_pool",
        tool_id,
        after={"name": name, "kind": kind},
    )
    conn.commit()
    return tool_id


def _tool_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "description": row["description"],
        "kind": row["kind"],
        "config": json.loads(row["config"]),
        "tool_names": json.loads(row["tool_names"]),
        "is_active": bool(row["is_active"]),
    }


def list_tools(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM tool_pool ORDER BY id").fetchall()
    return [_tool_row_to_dict(r) for r in rows]


def set_tool_active(
    conn: sqlite3.Connection, actor_id: int, tool_id: int, is_active: bool
) -> None:
    row = conn.execute("SELECT * FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
    if row is None:
        raise ValueError("tool_not_found")
    conn.execute(
        "UPDATE tool_pool SET is_active = ? WHERE id = ?", (int(is_active), tool_id)
    )
    audit.record(
        conn,
        actor_id,
        "tool_pool_active_changed",
        "tool_pool",
        tool_id,
        before={"is_active": bool(row["is_active"])},
        after={"is_active": is_active},
    )
    conn.commit()


def update_tool(
    conn: sqlite3.Connection, actor_id: int, tool_id: int, **fields
) -> None:
    """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을 추가해줘")
    — 지금까지 Tool Pool은 등록·활성화 토글·삭제만 있고, 설명이나
    config를 고치려면 지우고 다시 만들어야 했다. `kind`는 바꿀 수
    없다(`config`의 구조 자체가 종류마다 다르다 — 바꾸려면 삭제 후
    새로 등록하는 쪽이 안전하다, `pipeline_runtime.update_stage()`가
    `mode`를 못 바꾸게 하는 것과 같은 원칙). `name`도 바꿀 수 없다
    (사용자 요청, 2026-10-20, "에이전트 연결 관련 영향을 주는
    도구명/스킬명은 수정 대상에서 제외" — Skill `augmentation_mode=
    "tool"` 매칭이 이 이름 문자열을 그대로 참조하므로 바꾸면 기존
    연결이 조용히 끊어진다). `config`를 바꾸면 `create_tool()`과
    똑같은 검증(native_id가 실제 native 도구를 가리키는지,
    custom_python 소스가 안전한 모양인지)을 다시 돌린다 — 수정 경로로
    검증을 우회해 등록 시점보다 느슨해지면 안 된다."""
    allowed = {"description", "config", "tool_names"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported_fields:{sorted(unknown)}")
    row = conn.execute("SELECT * FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
    if row is None:
        raise ValueError("tool_not_found")
    if not fields:
        return
    if "config" in fields:
        config = fields["config"]
        kind = row["kind"]
        if kind == "native" and config.get("native_id") not in NATIVE_TOOL_FACTORIES:
            raise ValueError(f"unknown_native_id:{config.get('native_id')}")
        if kind == "custom_python":
            _validate_custom_python_source(config.get("function_source", ""))
    set_clauses = []
    values: list[object] = []
    for key, value in fields.items():
        if key in ("config", "tool_names"):
            value = json.dumps(value, ensure_ascii=False)
        set_clauses.append(f"{key} = ?")
        values.append(value)
    values.append(tool_id)
    conn.execute(
        f"UPDATE tool_pool SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    audit.record(
        conn,
        actor_id,
        "tool_pool_updated",
        "tool_pool",
        tool_id,
        before={"name": row["name"], "description": row["description"]},
        after=fields,
    )
    conn.commit()


def delete_tool(conn: sqlite3.Connection, actor_id: int, tool_id: int) -> None:
    """Tool Pool 항목을 완전히 지운다(사용자 요청, 2026-09-28, "각 메뉴에서
    데이터 삭제나, 초기화 기능 추가"). `agent_tool_links`가 이 도구를
    참조하면(FK) 같이 지운다 — `skill_pool.default_tool_pool_ids`는 JSON
    문자열이라 FK가 안 걸려 있는데, `resolve_pool_tools()`가 이미
    `WHERE id = ? AND is_active = 1`로 존재 확인을 하므로 지워진 도구를
    가리키는 오래된 참조는 조용히 무시된다(에러 안 남)."""
    row = conn.execute("SELECT * FROM tool_pool WHERE id = ?", (tool_id,)).fetchone()
    if row is None:
        raise ValueError("tool_not_found")
    conn.execute("DELETE FROM agent_tool_links WHERE tool_pool_id = ?", (tool_id,))
    conn.execute("DELETE FROM tool_pool WHERE id = ?", (tool_id,))
    audit.record(
        conn,
        actor_id,
        "tool_pool_deleted",
        "tool_pool",
        tool_id,
        before={"name": row["name"], "kind": row["kind"]},
        after=None,
    )
    conn.commit()


def create_skill(
    conn: sqlite3.Connection,
    actor_id: int,
    name: str,
    description: str,
    default_tool_pool_ids: list[int] | None = None,
    augmentation_mode: str = "tool",
    show_in_conversation: bool = True,
) -> int:
    if augmentation_mode not in ("tool", "always", "none"):
        raise ValueError(f"unsupported_augmentation_mode:{augmentation_mode}")
    cur = conn.execute(
        "INSERT INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode, "
        "show_in_conversation) VALUES (?, ?, ?, ?, ?)",
        (
            name,
            description,
            json.dumps(default_tool_pool_ids or []),
            augmentation_mode,
            show_in_conversation,
        ),
    )
    skill_id = cur.lastrowid
    assert skill_id is not None
    id_scheme.assign_code(
        conn, "skill_pool", skill_id, id_scheme.SKILL_MODE_SEGMENT[augmentation_mode]
    )
    audit.record(
        conn,
        actor_id,
        "skill_pool_created",
        "skill_pool",
        skill_id,
        after={"name": name, "augmentation_mode": augmentation_mode},
    )
    conn.commit()
    return skill_id


def list_skills(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM skill_pool ORDER BY id").fetchall()
    return [
        {
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "default_tool_pool_ids": json.loads(r["default_tool_pool_ids"]),
            "augmentation_mode": r["augmentation_mode"],
            "show_in_conversation": bool(r["show_in_conversation"]),
        }
        for r in rows
    ]


def update_skill(
    conn: sqlite3.Connection, actor_id: int, skill_id: int, **fields
) -> None:
    """사용자 요청(2026-10-20, "도구/스킬 Pool에 수정 기능을 추가해줘")
    — `create_skill()`과 같은 검증(augmentation_mode 허용값)을 다시
    돌린다. `name`은 바꿀 수 없다(`update_tool()` 참고 — Agent의
    `skill_tags`가 이 이름 문자열과 정확히 일치해야 매칭되므로, 바꾸면
    그 연결이 조용히 끊어진다).

    사용자 요청(2026-10-27, "skill 생성시 ID에 tool/always/none이
    포함됨, 수정에서 모드 변경이 ID가 바뀌는 게 아니라면 수정시 모드
    변경을 막아야 함") — `augmentation_mode`도 `name`/`kind`와 같은
    이유로 더는 수정 대상이 아니다: `id_scheme.assign_code()`가 생성
    시점에 모드를 코드에 세그먼트로 박아 넣는데(`SKL-TOOL-001` 등),
    수정은 그 코드를 다시 배정하지 않으므로 모드만 바꾸면 코드와 실제
    모드가 어긋난다. 모드를 바꾸려면 삭제 후 새로 등록해야 한다(새
    코드가 새 모드로 배정됨)."""
    allowed = {
        "description",
        "default_tool_pool_ids",
        "show_in_conversation",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported_fields:{sorted(unknown)}")
    row = conn.execute("SELECT * FROM skill_pool WHERE id = ?", (skill_id,)).fetchone()
    if row is None:
        raise ValueError("skill_not_found")
    if not fields:
        return
    set_clauses = []
    values: list[object] = []
    for key, value in fields.items():
        if key == "default_tool_pool_ids":
            value = json.dumps(value)
        set_clauses.append(f"{key} = ?")
        values.append(value)
    values.append(skill_id)
    conn.execute(
        f"UPDATE skill_pool SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    audit.record(
        conn,
        actor_id,
        "skill_pool_updated",
        "skill_pool",
        skill_id,
        before={"name": row["name"], "description": row["description"]},
        after=fields,
    )
    conn.commit()


def delete_skill(conn: sqlite3.Connection, actor_id: int, skill_id: int) -> None:
    """Skill Pool 항목을 완전히 지운다(사용자 요청, 2026-09-28). 이
    이름과 같은 `skill_tags`를 가진 Agent/사람은 그대로 남고, 다음
    `run_crew()`/`crewai_agent_generator()` 실행부터 이 스킬 기반 자동
    도구 연결만 더 이상 적용되지 않는다(스킬 이름 자체를 지우는 게
    아니라 Skill Pool 레코드만 지우기 때문)."""
    row = conn.execute("SELECT * FROM skill_pool WHERE id = ?", (skill_id,)).fetchone()
    if row is None:
        raise ValueError("skill_not_found")
    conn.execute("DELETE FROM skill_pool WHERE id = ?", (skill_id,))
    audit.record(
        conn,
        actor_id,
        "skill_pool_deleted",
        "skill_pool",
        skill_id,
        before={"name": row["name"]},
        after=None,
    )
    conn.commit()


def link_agent_tool(
    conn: sqlite3.Connection, actor_id: int, agent_id: int, tool_pool_id: int
) -> None:
    if (
        conn.execute("SELECT id FROM agent_pool WHERE id = ?", (agent_id,)).fetchone()
        is None
    ):
        raise ValueError("agent_not_found")
    if (
        conn.execute(
            "SELECT id FROM tool_pool WHERE id = ?", (tool_pool_id,)
        ).fetchone()
        is None
    ):
        raise ValueError("tool_not_found")
    conn.execute(
        "INSERT OR IGNORE INTO agent_tool_links (agent_id, tool_pool_id) VALUES (?, ?)",
        (agent_id, tool_pool_id),
    )
    audit.record(
        conn,
        actor_id,
        "agent_tool_linked",
        "agent_pool",
        agent_id,
        after={"tool_pool_id": tool_pool_id},
    )
    conn.commit()


def unlink_agent_tool(
    conn: sqlite3.Connection, actor_id: int, agent_id: int, tool_pool_id: int
) -> None:
    conn.execute(
        "DELETE FROM agent_tool_links WHERE agent_id = ? AND tool_pool_id = ?",
        (agent_id, tool_pool_id),
    )
    audit.record(
        conn,
        actor_id,
        "agent_tool_unlinked",
        "agent_pool",
        agent_id,
        after={"tool_pool_id": tool_pool_id},
    )
    conn.commit()


def list_agent_tool_ids(conn: sqlite3.Connection, agent_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT tool_pool_id FROM agent_tool_links WHERE agent_id = ?", (agent_id,)
    ).fetchall()
    return [r["tool_pool_id"] for r in rows]


def resolve_static_augmentation_targets(
    conn: sqlite3.Connection,
    skills: list[str],
    modes: tuple[str, ...] = ("always",),
) -> set[str]:
    """사용자 결정(2026-09-29, "skill_pool.augmentation_mode의 'always'
    값 실제 구현") — 지금까지 `augmentation_mode='always'`는 CHECK
    제약에만 존재하고 저장 외에는 아무 효과가 없었다(`resolve_pool_tools()`
    가 `'tool'`만 읽었다). `augmentation_mode`가 `modes`에 속하는
    skill_pool 항목 중 `skills`와 이름이 매칭되는 것의 `native_id`
    집합을 반환한다 — MCP류는 정적 텍스트로 만들 방법이 없으므로
    `kind='native'`인 tool_pool만 본다(비-native는 조용히 건너뛴다).

    호출부가 다른 의미로 쓴다: `agent_runtime.augment_with_retrieval()`
    (도구 호출 루프가 없는 단일 실행 경로)은 `modes=("always", "tool")`로
    불러 — 그 경로는 Agent가 스스로 도구를 호출할 방법이 없으니
    `'tool'`이든 `'always'`든 정적 주입이 유일한 선택지다.
    `crew_runtime._static_augmentation()`(CrewAI Agent 루프가 있는 경로)은
    기본값 `modes=("always",)`만 써 — `'tool'`은 이미 진짜 CrewAI Tool로
    붙으므로(Agent가 스스로 판단) 정적 주입까지 하면 중복이다."""
    skill_set = set(skills)
    placeholders = ",".join("?" for _ in modes)
    rows = conn.execute(
        f"SELECT * FROM skill_pool WHERE augmentation_mode IN ({placeholders})",
        modes,
    ).fetchall()
    native_ids: set[str] = set()
    for row in rows:
        if row["name"] not in skill_set:
            continue
        for tool_id in json.loads(row["default_tool_pool_ids"]):
            trow = conn.execute(
                "SELECT * FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
            ).fetchone()
            if trow is None or trow["kind"] != "native":
                continue
            native_id = json.loads(trow["config"]).get("native_id")
            if native_id:
                native_ids.add(native_id)
    return native_ids


def skill_knowledge_text(conn: sqlite3.Connection, skills: list[str]) -> str:
    """`skill_tags`에 담긴 이름과 일치하는 `skill_pool.description`을
    모아 프롬프트에 그대로 끼워 넣을 수 있는 텍스트로 만든다.

    사용자 요청(2026-10-13, "Harness Methodology 기준 개발 단계별
    에이전트 생성... skill을 적극 활용") 전까지는 `tdd-ai-workflow-
    checklist`·`spec-driven-artifacts` 같은 절차 스킬이 `skill_pool`에
    등록돼 있어도 **이름만** Agent의 역할/백스토리에 나열될 뿐, 그 스킬이
    실제로 무엇을 하라는 것인지(`description`)는 어디에도 전달되지
    않았다(실측 확인) — `augmentation_mode='tool'`은 실행 가능한 CrewAI
    Tool을 붙여줄 뿐 설명 문구를 전달하지 않고, `'always'`는 vectordb/
    graphdb/web_search 3종 native 검색에만 쓰이는 특수 경로였다.
    `deliverables.skill_grounded_system_prompt()`(사람의 LLM 활용 업무
    지원 경로)만 이 조회 로직을 갖고 있었던 것을 여기로 공용화해 Agent
    자율 실행 경로(`llm.system_prompt_for_role()`, `crew_runtime.
    _agent_backstory()`)에도 똑같이 적용한다 — `augmentation_mode`
    값과 무관하게 이름이 일치하는 모든 스킬의 설명을 모은다(설명 자체가
    '이 스킬이 왜/언제 필요한지' 안내문 역할을 하므로, 'tool' 모드처럼
    실행 가능한 Tool이 이미 붙어 있어도 중복이 아니다)."""
    if not skills:
        return ""
    placeholders = ",".join("?" * len(skills))
    rows = conn.execute(
        f"SELECT description FROM skill_pool WHERE name IN ({placeholders})",
        skills,
    ).fetchall()
    return "\n".join(f"- {r['description']}" for r in rows if r["description"])


# 사용자 보고(2026-10-14, "개발자 Agent의 결과가 게임 코드가 아닌 이상한
# 내용이 출력되고 있어") — 실제 재현 확인: 절차 스킬(tdd-ai-workflow-
# checklist 등)의 description("...체크리스트로 따라간다")이 아무 안내 없이
# "참고 지식:" 뒤에 그대로 붙다 보니, 로컬 모델(qwen3-coder:latest, Ollama)이
# 그 체크리스트/커밋 규칙 설명 자체를 "산출물"로 착각해 그대로 되풀이해
# 출력했다(실제 게임 코드는 전혀 생성 안 함). 세 호출부(`llm.
# system_prompt_for_role()`, `deliverables.skill_grounded_system_prompt()`,
# `crew_runtime._agent_backstory()`) 모두 "참고 지식"을 같은 방식으로
# 이어붙이므로, 이 안내문 하나를 공용 상수로 만들어 세 곳에 동일하게 적용한다.
SKILL_KNOWLEDGE_GUIDANCE = (
    "아래는 작업 수행 방식에 대한 내부 참고 지침일 뿐입니다 — 이 설명 문구나 "
    "체크리스트 형식 자체를 최종 산출물로 출력하지 마세요. 반드시 사용자가 "
    "실제로 요청한 결과물로 응답하세요(예: 코드를 요청받았다면 설명이 아니라 "
    "실행 가능한 코드로 답하세요)."
)

# 사용자 보고(2026-10-22, "일반웹검색 스킬 점검해줘" 중 재현된 실측 크래시) —
# `skill_pool.description`(예: "일반웹검색": "...Agent가 직접 호출할 수
# 있다")이 CrewAI Tool 루프가 있는 경로(`crew_runtime._agent_backstory()`)
# 기준으로 써 있어, 실제로는 도구가 전혀 안 붙는 단일 LLM 호출 경로
# (`llm.system_prompt_for_role()`, `deliverables.
# skill_grounded_system_prompt()`)에도 그대로 복사된다. 로컬 Ollama의
# qwen3.5/qwen3-coder 계열 모델(`tools` capability가 있는 모델)이 이
# 설명을 믿고 실제 tool_call 형식 토큰을 출력하려 하면, `tools=`를
# 전달하지 않은 이 경로에서 Ollama 서버 자체가 "tool call parsing
# failed: EOF"로 500을 반환한다(재현 확인 — `ollama/server.log`).
# `skill_knowledge_text()`가 비지 않을 때만(= 참고 지식에 도구 언급이
# 있을 수 있을 때만) 이 경고를 덧붙인다 — 두 단일 호출 경로에만 쓴다
# (`crew_runtime._agent_backstory()`는 실제 도구가 붙으므로 적용 금지 —
# 거기서 쓰면 진짜 Tool 호출을 모델이 스스로 포기할 위험이 있다).
NO_TOOL_LOOP_GUARD = (
    "중요: 이 실행 경로에는 실제로 호출 가능한 도구가 연결되어 있지 않습니다 — "
    "위 참고 지식에 어떤 도구의 이름이나 기능이 언급되어 있어도 그 도구를 실제로 "
    "호출할 수 없습니다. tool_call이나 함수 호출 형식의 출력을 절대 생성하지 "
    "말고, 알고 있는 지식과 위 참고 자료만으로 순수 텍스트 답변을 작성하세요."
)


def unsupported_realtime_skill_names(
    conn: sqlite3.Connection, skills: list[str]
) -> list[str]:
    """`skills` 중 실시간 검색/조회를 의도한(`augmentation_mode`가
    `'tool'`/`'always'`이고 `default_tool_pool_ids`가 비지 않은) 스킬
    인데, 그 도구가 `kind='native'`가 아닌(`mcp_remote`/`mcp_stdio`)
    것들의 이름을 반환한다.

    사용자 요청(2026-10-22, "일반웹검색 스킬 점검해줘" 조사 결과) —
    `resolve_static_augmentation_targets()`는 그런 스킬을 조용히
    건너뛴다(native만 정적 텍스트 주입이 가능하다, 주석 참고). 그 결과
    CrewAI Agent 루프가 없는 호출부(`conversations.run_default_ai_job()`)
    에서 이런 스킬을 고르면 모델이 "검색하겠습니다"라고 의도만 서술하고
    끝나는 것처럼 보인다(실측 확인된 버그 리포트) — 호출부가 이 함수로
    해당 여부를 미리 알아 사용자에게 "이 스킬은 대화창에서 실시간 검색이
    안 되고 Team 실행에서만 동작한다"고 알릴 수 있게 한다."""
    if not skills:
        return []
    placeholders = ",".join("?" for _ in skills)
    rows = conn.execute(
        f"SELECT * FROM skill_pool WHERE name IN ({placeholders}) "
        "AND augmentation_mode IN ('tool', 'always')",
        skills,
    ).fetchall()
    names = []
    for row in rows:
        tool_ids = json.loads(row["default_tool_pool_ids"])
        for tool_id in tool_ids:
            trow = conn.execute(
                "SELECT kind FROM tool_pool WHERE id = ? AND is_active = 1",
                (tool_id,),
            ).fetchone()
            if trow is not None and trow["kind"] != "native":
                names.append(row["name"])
                break
    return names


def resolve_pool_tools(
    conn: sqlite3.Connection,
    stack: ExitStack,
    skills: list[str],
    agent_id: int | None = None,
) -> list:
    """skill_pool 매칭(`augmentation_mode="tool"`)과 `agent_tool_links`
    (Agent별 명시적 연결)를 합쳐 실제 CrewAI Tool 인스턴스 목록을 만든다.

    skill 기반 매칭은 매번 다시 계산한다(연결을 미리 저장해두지 않는다) —
    그래야 기존에 만들어진 Agent도 `skill_pool`이 나중에 갱신되면 재실행
    시점에 바로 반영된다. `agent_id`가 있으면(이미 온보딩된 Agent) 명시적
    연결도 함께 합친다 — Agent 생성 시점(`crewai_agent_generator`)에는 아직
    `agent_pool` row가 없어 `agent_id=None`으로 스킬 매칭만 적용된다."""
    tool_pool_ids: set[int] = set()
    skill_set = set(skills)
    for row in conn.execute(
        "SELECT * FROM skill_pool WHERE augmentation_mode = 'tool'"
    ).fetchall():
        if row["name"] in skill_set:
            tool_pool_ids.update(json.loads(row["default_tool_pool_ids"]))
    if agent_id is not None:
        tool_pool_ids.update(list_agent_tool_ids(conn, agent_id))

    tools: list = []
    for tool_id in tool_pool_ids:
        row = conn.execute(
            "SELECT * FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
        ).fetchone()
        if row is None:
            continue
        config = json.loads(row["config"])
        if row["kind"] == "native":
            factory = NATIVE_TOOL_FACTORIES.get(config.get("native_id"))
            if factory is None:
                continue
            tools.append(factory(conn))
        elif row["kind"] == "custom_python":
            tools.append(_custom_python_crewai_tool(row))
        else:
            tools.extend(connect_mcp_tools(stack, config))
    return tools


def suggest_agents_for_task(
    conn: sqlite3.Connection, task_id: int, limit: int = 5
) -> list[dict]:
    """사용자 요청(2026-10-20, "Crew 구성 추천/자동 채움" — Crew 빌더
    로드맵 5단계) — "업무에 바로 실행" 모드가 빈 Agent 선택 행 하나로
    시작해 매번 수동으로 찾아 추가해야 하던 수고를 줄인다.

    `assignment.match_candidates()`는 **Task당 역할 하나**를 전제로
    설계됐다(요청 역할과 완전 일치하는 후보만 스코어링 대상) — Crew는
    여러 다른 역할을 한 Task에 묶는 게 핵심이라 그 함수를 그대로 못
    쓴다. 대신 `required_skills` 교집합 기준으로 **역할이 달라도** 관련
    있는 Agent를 전부 후보로 삼는 결정론적(LLM 미사용) 랭킹을 새로
    둔다 — `assignment.py`와 같은 "완전일치 우선, 그 외엔 점수" 원칙을
    공유하되 역할 다양성을 허용한다는 점만 다르다.

    정렬: role_name 완전일치(대소문자 무시) 우선 → skill_tags와
    required_skills의 교집합 개수 내림차순 → agent id 오름차순(동점일
    때 결정론적 순서 보장). 교집합이 0개이면서 role도 다른 Agent는
    애초에 후보에서 제외한다(무관한 Agent를 섞지 않는다)."""
    task = conn.execute(
        "SELECT role_name, required_skills FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    required_skills = set(json.loads(task["required_skills"]))
    required_role = task["role_name"].strip().lower()

    candidates = []
    for row in conn.execute(
        "SELECT * FROM agent_pool WHERE status = 'active'"
    ).fetchall():
        exact_role = row["role_name"].strip().lower() == required_role
        overlap = len(required_skills & set(json.loads(row["skill_tags"])))
        if overlap == 0 and not exact_role:
            continue
        candidates.append((exact_role, overlap, row))

    candidates.sort(key=lambda t: (not t[0], -t[1], t[2]["id"]))
    return [
        {
            "id": row["id"],
            "role_name": row["role_name"],
            "name": row["name"],
            "skill_tags": json.loads(row["skill_tags"]),
            "exact_role_match": exact_role,
            "overlap_count": overlap,
        }
        for exact_role, overlap, row in candidates[:limit]
    ]
