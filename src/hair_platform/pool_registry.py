"""Skill Pool·Tool Pool — ADR.md 결정 12(사용자 요청, 2026-09-27, "tool_pool·
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

from hair_platform import audit, graphquery, vectorstore

NATIVE_TOOL_FACTORIES = {
    "vectordb_search": vectorstore.as_crewai_tool,
    "graphdb_query": graphquery.as_crewai_tool,
}


def mcp_serverparams(cfg: dict):
    """MCP 서버 접속 파라미터를 만든다 — `command`가 있으면 stdio(로컬
    프로세스), 없으면 `url` 기반 SSE/streamable-http로 판별한다(`crewai-
    tools`/`mcpadapt` 실제 소스로 확인한 형태, ADR 결정 10)."""
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


def create_tool(
    conn: sqlite3.Connection,
    actor_id: int,
    name: str,
    description: str,
    kind: str,
    config: dict,
    tool_names: list[str] | None = None,
) -> int:
    if kind not in ("mcp_stdio", "mcp_remote", "native"):
        raise ValueError(f"unsupported_kind:{kind}")
    if kind == "native" and config.get("native_id") not in NATIVE_TOOL_FACTORIES:
        raise ValueError(f"unknown_native_id:{config.get('native_id')}")
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
) -> int:
    if augmentation_mode not in ("tool", "always", "none"):
        raise ValueError(f"unsupported_augmentation_mode:{augmentation_mode}")
    cur = conn.execute(
        "INSERT INTO skill_pool "
        "(name, description, default_tool_pool_ids, augmentation_mode) "
        "VALUES (?, ?, ?, ?)",
        (
            name,
            description,
            json.dumps(default_tool_pool_ids or []),
            augmentation_mode,
        ),
    )
    skill_id = cur.lastrowid
    assert skill_id is not None
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
            "name": r["name"],
            "description": r["description"],
            "default_tool_pool_ids": json.loads(r["default_tool_pool_ids"]),
            "augmentation_mode": r["augmentation_mode"],
        }
        for r in rows
    ]


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
    연결도 함께 합친다 — F4 생성 시점(`crewai_agent_generator`)에는 아직
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
        else:
            tools.extend(connect_mcp_tools(stack, config))
    return tools
