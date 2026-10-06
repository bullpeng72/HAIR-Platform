"""팀(Team) — 사용자 요청(2026-10-06, "복수의 사람이 복수의 Agent와 또는
CrewAI 장점인 역할에 따른 Agent를 묶어 생성된 그룹과 협업하도록... 다시
기획"). `docs/MULTI_PARTY_TEAM_COLLABORATION_PLAN.md` Phase 1+2.

`human_pool`/`agent_pool` 멤버를 역할명과 함께 묶어 여러 업무에 재사용
가능한 단위로 등록한다 — `skill_pool`/`tool_pool`(`pool_registry.py`)과
같은 "등록→재사용" 패턴을 네 번째로 적용한 것뿐, 새로운 개념이 아니다.
팀 자체는 `assignments.resource_type='team'`으로 할 일에 배정된다
(`assignments.py` 참고) — 이 모듈은 팀 정의(CRUD)만 다루고 배정 로직은
건드리지 않는다(관심사 분리).
"""

from __future__ import annotations

import sqlite3

from hair_platform import audit


def create_team(
    conn: sqlite3.Connection, actor_id: int, name: str, description: str = ""
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("empty_name")
    cur = conn.execute(
        "INSERT INTO teams (name, description) VALUES (?, ?)", (name, description)
    )
    team_id = cur.lastrowid
    assert team_id is not None
    audit.record(conn, actor_id, "team_created", "team", team_id, after={"name": name})
    conn.commit()
    return team_id


def _member_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "member_type": row["member_type"],
        "member_id": row["member_id"],
        "role_label": row["role_label"],
        "member_name": row["member_name"],
    }


def get_team(conn: sqlite3.Connection, team_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None:
        return None
    members = conn.execute(
        """
        SELECT tm.id, tm.member_type, tm.member_id, tm.role_label,
               CASE WHEN tm.member_type = 'human' THEN hp.name ELSE ap.role_name END
                   AS member_name
        FROM team_members tm
        LEFT JOIN human_pool hp ON hp.id = tm.member_id AND tm.member_type = 'human'
        LEFT JOIN agent_pool ap ON ap.id = tm.member_id AND tm.member_type = 'agent'
        WHERE tm.team_id = ?
        ORDER BY tm.id
        """,
        (team_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "is_active": bool(row["is_active"]),
        "members": [_member_row_to_dict(m) for m in members],
    }


def list_teams(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id FROM teams ORDER BY id").fetchall()
    teams = [get_team(conn, r["id"]) for r in rows]
    return [t for t in teams if t is not None]


def set_team_active(
    conn: sqlite3.Connection, actor_id: int, team_id: int, is_active: bool
) -> None:
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None:
        raise ValueError("team_not_found")
    conn.execute(
        "UPDATE teams SET is_active = ? WHERE id = ?", (int(is_active), team_id)
    )
    audit.record(
        conn,
        actor_id,
        "team_active_changed",
        "team",
        team_id,
        before={"is_active": bool(row["is_active"])},
        after={"is_active": is_active},
    )
    conn.commit()


def delete_team(conn: sqlite3.Connection, actor_id: int, team_id: int) -> None:
    """사용자 요청(2026-09-28)의 "각 메뉴에서 데이터 삭제 기능" 관례를
    그대로 따른다 — 이 팀을 참조하는 `assignments.resource_id`(resource_
    type='team')가 있어도 FK가 안 걸려 있어(JSON이 아니라 정수 참조지만
    tool_pool/skill_pool과 마찬가지로 엄격한 FK는 안 둠) 막지 않는다 —
    과거 배정 이력(assignment_history)은 "그 당시 팀 이름"을 알아야
    하는 감사 목적이라 팀 자체를 지워도 레코드는 남는다(NF10과 같은
    append-only 원칙)."""
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None:
        raise ValueError("team_not_found")
    conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
    conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    audit.record(
        conn, actor_id, "team_deleted", "team", team_id, before={"name": row["name"]}
    )
    conn.commit()


def add_team_member(
    conn: sqlite3.Connection,
    actor_id: int,
    team_id: int,
    member_type: str,
    member_id: int,
    role_label: str = "",
) -> int:
    if member_type not in ("human", "agent"):
        raise ValueError(f"unsupported_member_type:{member_type}")
    team = conn.execute("SELECT id FROM teams WHERE id = ?", (team_id,)).fetchone()
    if team is None:
        raise ValueError("team_not_found")
    table = "human_pool" if member_type == "human" else "agent_pool"
    member = conn.execute(
        f"SELECT id FROM {table} WHERE id = ?", (member_id,)
    ).fetchone()
    if member is None:
        raise ValueError(f"{member_type}_not_found")
    cur = conn.execute(
        "INSERT INTO team_members (team_id, member_type, member_id, role_label) "
        "VALUES (?, ?, ?, ?)",
        (team_id, member_type, member_id, role_label),
    )
    member_row_id = cur.lastrowid
    assert member_row_id is not None
    audit.record(
        conn,
        actor_id,
        "team_member_added",
        "team",
        team_id,
        after={
            "member_type": member_type,
            "member_id": member_id,
            "role_label": role_label,
        },
    )
    conn.commit()
    return member_row_id


def remove_team_member(
    conn: sqlite3.Connection, actor_id: int, team_id: int, member_row_id: int
) -> None:
    row = conn.execute(
        "SELECT * FROM team_members WHERE id = ? AND team_id = ?",
        (member_row_id, team_id),
    ).fetchone()
    if row is None:
        raise ValueError("team_member_not_found")
    conn.execute("DELETE FROM team_members WHERE id = ?", (member_row_id,))
    audit.record(
        conn,
        actor_id,
        "team_member_removed",
        "team",
        team_id,
        before={"member_type": row["member_type"], "member_id": row["member_id"]},
    )
    conn.commit()


def human_members_with_accounts(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    """이 팀의 사람 멤버 중 로그인 계정이 연결된(human_pool.user_id) 것만
    반환한다 — `deliverables.task_participant_relationship()`이 "이
    사람이 이 팀의 멤버라서 참가자로 인정되는지" 판정할 때 쓴다(§51의
    "계정 미연결" 경고와 같은 경계 — 계정 없는 멤버는 애초에 로그인을
    못 해 구조적으로 이 판정 대상이 될 수 없다)."""
    rows = conn.execute(
        """
        SELECT hp.user_id AS user_id
        FROM team_members tm
        JOIN human_pool hp ON hp.id = tm.member_id AND tm.member_type = 'human'
        WHERE tm.team_id = ? AND hp.user_id IS NOT NULL
        """,
        (team_id,),
    ).fetchall()
    return [{"user_id": r["user_id"]} for r in rows]


def active_agent_member_ids(conn: sqlite3.Connection, team_id: int) -> list[int]:
    """이 팀의 Agent 멤버 중 온보딩(active)된 것만 — `run_crew()`는
    active 상태만 받는다(§Phase 3에서 쓸 예정, `docs/MULTI_PARTY_TEAM_
    COLLABORATION_PLAN.md`)."""
    rows = conn.execute(
        """
        SELECT ap.id AS agent_id
        FROM team_members tm
        JOIN agent_pool ap ON ap.id = tm.member_id AND tm.member_type = 'agent'
        WHERE tm.team_id = ? AND ap.status = 'active'
        """,
        (team_id,),
    ).fetchall()
    return [r["agent_id"] for r in rows]
