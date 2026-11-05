"""단계형 개발 파이프라인 — 사용자 요청(2026-10-18, "BMAD-METHOD
(https://github.com/bmad-code-org/bmad-method)와 하네스 방법론을 참조해,
최소의 HITL, HOTL으로 수준높은 개발 결과물 생성 가능한 개발 Agent 팀을
구성... 각 agent가 역할을 구분해 각 개발 단계별 단독 또는 복수의
에이전트가 순차/병행 처리하며 개발 진행... 다른 업무(시장조사-분석-전략/
기획 수립 등)에도 적용될 수 있도록 일반화").

**배경(직전 분석에서 확인한 두 문제)**:
1. `agent_pool`에는 PM/AA/DA/요구분석/스펙작성/코드검증/QA/테스트계획/
   품질관리 등 22개 SW개발 역할이 명확히 구분돼 있는데, 실제 자동 협업
   ("자율 개발 크루")에는 12명만 들어가 있고 나머지 10개 역할은 개별
   `@멘션`으로만 호출 가능할 뿐 자동 파이프라인에 전혀 안 들어간다.
2. Gate A-G 측정은 `crew_runtime.run_crew()` 안에서 항상 계산되지만,
   대화창 `@멘션` 경로는 그 결과를 저장/활용하지 않고 버린다. 결재함
   (HITL)·품질 기준 연동은 "할 일(Task) 협업 워크스페이스" 경로
   (`deliverables.py`)에만 있다.

**이 모듈의 설계 원칙(BMAD-METHOD 대응)** — BMAD는 Analyst/PM/Architect가
만든 계획(PRD+Architecture)을 사람이 **한 번** 승인하면, 그 뒤 Scrum
Master가 컨텍스트를 전부 담은 story로 쪼개 Dev→QA Gate(YAML 기준)
반복으로 진행하고, 반복해도 기준을 못 넘기는 경우에만 다시 사람에게
올라간다. 이 모듈은 그 패턴을 그대로 구현한다:
- `pipeline_stages.requires_human_approval`로 "계획 확정" 같은 핵심
  변곡점에만 사람 승인을 두고(기본 시드는 1단계뿐), 나머지는 Gate A-G
  (`min_gate_pass_ratio`) 통과 여부로 자동 진행한다 — 이게 "최소 HITL"의
  실제 구현이다.
- Gate가 fail하면 "무엇이 왜 fail했는지"를 다음 시도의 지시문에 그대로
  적어 재시도시킨다(`max_retry`까지) — "측정만 하고 버림"이 아니라
  실제 피드백 반영 반복개선이다.
- `max_retry`를 넘겨도 fail하면 그때만 사람에게 에스컬레이션한다 —
  "최소지만 0은 아닌" HOTL.

**범용성** — 이 엔진은 "단계(stage) + 역할(role_name)"만 알고 그 역할이
개발자인지 시장조사가인지 전혀 모른다. `pipeline_stages.mode`는 사용자가
요청한 "단독 또는 복수의 에이전트가 순차/병행 처리"를 그대로 반영한다:
`single`(역할 1명) / `sequential_relay`(역할 N명, 앞사람 결과를 이어받아
순서대로) / `parallel_delegated`(리드 1명이 코워커 N명에게 실제로 역할별
하위 과업을 쪼개 위임 — `crew_runtime.run_crew()`의 hierarchical_with_
manager 경로, 2026-10-18에 이 세션에서 고치고 실측 확인한 그 경로를
그대로 재사용한다).

**기존 코드와의 관계** — 새 LLM 호출/평가 로직을 만들지 않는다.
`crew_runtime.run_crew()`가 이미 반환하는 `gate_scores`/`tcr`/`agents`를
그대로 쓰고, `approvals.create_approval_chain()`/`agent_evaluation.
record_evaluation()`을 그대로 재사용한다. 이 모듈 자체(단계 실행/Gate
재시도/에스컬레이션 로직)는 호출자가 누구든 전혀 모른다 — "자율 개발
크루" 팀(`teams`/`team_members`)이 Task 협업 워크스페이스에서 직접
호출하든, 대화 `@팀이름` 멘션이 호출하든 똑같이 동작한다.

(2026-10-28 갱신 — 처음 추가됐을 땐 "자율 개발 크루" 팀은 건드리지
않고 하위 호환용 수동 Crew 실행으로만 남겨뒀었다. 사용자 요청
("'자율 개발 크루'의 경우 파이프라인을 참조해 실제 개발 품질을
보장하며 수행될 수 있도록 재구성")으로 이제 그 팀도
`teams.pipeline_template_id`를 통해 이 엔진에 연결돼 있다 —
`conversations.run_team_mention_job()`/`_run_team_mention_via_
pipeline()` 참고. 연결 안 된 다른 팀은 여전히 기존 단발 Crew 실행
그대로다.)"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from hair_platform import (
    agent_evaluation,
    approvals,
    audit,
    crew_runtime,
    dashboard,
    deliverables,
    requirements_traceability_llm,
)

VALID_MODES = ("single", "sequential_relay", "parallel_delegated")
GATE_IDS = ["A", "B", "C", "D", "E", "F", "G"]


# ---------- 템플릿/단계 CRUD (빌더 UI가 호출) ----------


def create_pipeline_template(
    conn: sqlite3.Connection,
    actor_id: int,
    name: str,
    description: str = "",
    domain: str = "general",
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("empty_name")
    cur = conn.execute(
        "INSERT INTO pipeline_templates (name, description, domain) VALUES (?, ?, ?)",
        (name, description, domain or "general"),
    )
    template_id = cur.lastrowid
    assert template_id is not None
    audit.record(
        conn,
        actor_id,
        "pipeline_template_created",
        "pipeline_template",
        template_id,
        after={"name": name, "domain": domain},
    )
    conn.commit()
    return template_id


def _stage_to_dict(conn: sqlite3.Connection, stage_row: sqlite3.Row) -> dict:
    agents = conn.execute(
        "SELECT role_name, seq_order, instructions FROM pipeline_stage_agents "
        "WHERE stage_id = ? ORDER BY seq_order, id",
        (stage_row["id"],),
    ).fetchall()
    return {
        "id": stage_row["id"],
        "pipeline_template_id": stage_row["pipeline_template_id"],
        "seq_order": stage_row["seq_order"],
        "name": stage_row["name"],
        "mode": stage_row["mode"],
        "lead_role_name": stage_row["lead_role_name"],
        "instructions": stage_row["instructions"],
        "gate_ids": json.loads(stage_row["gate_ids"]),
        "min_gate_pass_ratio": stage_row["min_gate_pass_ratio"],
        "max_retry": stage_row["max_retry"],
        "requires_human_approval": bool(stage_row["requires_human_approval"]),
        "is_primary_deliverable": bool(stage_row["is_primary_deliverable"]),
        "expected_output": stage_row["expected_output"],
        "agents": [
            {
                "role_name": a["role_name"],
                "seq_order": a["seq_order"],
                "instructions": a["instructions"],
            }
            for a in agents
        ],
    }


def get_pipeline_template(conn: sqlite3.Connection, template_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        return None
    stages = conn.execute(
        "SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? "
        "ORDER BY seq_order, id",
        (template_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "domain": row["domain"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "stages": [_stage_to_dict(conn, s) for s in stages],
    }


def list_pipeline_templates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM pipeline_stages s WHERE s.pipeline_template_id = t.id) "
        "AS stage_count FROM pipeline_templates t ORDER BY t.id"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "domain": r["domain"],
            "is_active": bool(r["is_active"]),
            "stage_count": r["stage_count"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def set_pipeline_template_active(
    conn: sqlite3.Connection, actor_id: int, template_id: int, is_active: bool
) -> None:
    row = conn.execute(
        "SELECT is_active FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        raise ValueError("pipeline_template_not_found")
    conn.execute(
        "UPDATE pipeline_templates SET is_active = ? WHERE id = ?",
        (1 if is_active else 0, template_id),
    )
    audit.record(
        conn,
        actor_id,
        "pipeline_template_active_changed",
        "pipeline_template",
        template_id,
        before={"is_active": bool(row["is_active"])},
        after={"is_active": is_active},
    )
    conn.commit()


def delete_pipeline_template(
    conn: sqlite3.Connection, actor_id: int, template_id: int
) -> None:
    """`teams.delete_team()`과 같은 수동 FK-safe 연쇄 삭제 패턴 — `PRAGMA
    foreign_keys=ON`이 전역으로 걸려 있어(`db.get_connection()`) 자식
    행이 남아있으면 그냥 DELETE가 IntegrityError로 막힌다. 실행 이력
    (`pipeline_runs`)이 있으면 지우지 않는다 — assignment_history와 같은
    "과거 이력은 감사 목적으로 남긴다" 원칙."""
    row = conn.execute(
        "SELECT * FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        raise ValueError("pipeline_template_not_found")
    has_runs = conn.execute(
        "SELECT 1 FROM pipeline_runs WHERE pipeline_template_id = ? LIMIT 1",
        (template_id,),
    ).fetchone()
    if has_runs is not None:
        raise ValueError("pipeline_template_has_run_history")
    stage_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ?",
            (template_id,),
        ).fetchall()
    ]
    for stage_id in stage_ids:
        conn.execute(
            "DELETE FROM pipeline_stage_agents WHERE stage_id = ?", (stage_id,)
        )
    conn.execute(
        "DELETE FROM pipeline_stages WHERE pipeline_template_id = ?", (template_id,)
    )
    conn.execute("DELETE FROM pipeline_templates WHERE id = ?", (template_id,))
    audit.record(
        conn,
        actor_id,
        "pipeline_template_deleted",
        "pipeline_template",
        template_id,
        before={"name": row["name"]},
    )
    conn.commit()


def add_stage(
    conn: sqlite3.Connection,
    actor_id: int,
    template_id: int,
    name: str,
    mode: str,
    agents: list[tuple[str, str]],
    lead_role_name: str | None = None,
    instructions: str = "",
    gate_ids: list[str] | None = None,
    min_gate_pass_ratio: float = 0.0,
    max_retry: int = 2,
    requires_human_approval: bool = False,
    is_primary_deliverable: bool = False,
    expected_output: str = "",
) -> int:
    """`agents`는 `(role_name, instructions)` 튜플 목록, 입력 순서가
    `sequential_relay`의 처리 순서가 된다(`parallel_delegated`는 순서
    무의미 — 리드가 알아서 나눠 위임). `single`은 `agents`가 정확히
    1개여야 한다.

    `expected_output`(사용자 요청, 2026-10-20, "고급 에이전트 관리") —
    이 단계의 `Task.expected_output` 기본값. 빈 문자열이면
    `crew_runtime.run_crew()`의 플랫폼 공통 기본 문구를 그대로 쓴다."""
    name = name.strip()
    if not name:
        raise ValueError("empty_stage_name")
    if mode not in VALID_MODES:
        raise ValueError(f"invalid_mode:{mode!r}")
    if not agents:
        raise ValueError("stage_needs_at_least_one_agent")
    if mode == "single" and len(agents) != 1:
        raise ValueError("single_mode_requires_exactly_one_agent")
    if mode == "parallel_delegated":
        if not lead_role_name:
            raise ValueError("parallel_delegated_requires_lead_role_name")
        if lead_role_name not in [r for r, _ in agents]:
            raise ValueError("lead_role_name_must_be_in_agents")
        if len(agents) < 2:
            raise ValueError("parallel_delegated_requires_at_least_one_coworker")
    template = conn.execute(
        "SELECT id FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if template is None:
        raise ValueError("pipeline_template_not_found")

    next_seq = conn.execute(
        "SELECT COALESCE(MAX(seq_order), 0) + 1 AS next_seq FROM pipeline_stages "
        "WHERE pipeline_template_id = ?",
        (template_id,),
    ).fetchone()["next_seq"]
    cur = conn.execute(
        "INSERT INTO pipeline_stages (pipeline_template_id, seq_order, name, mode, "
        "lead_role_name, instructions, gate_ids, min_gate_pass_ratio, max_retry, "
        "requires_human_approval, is_primary_deliverable, expected_output) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            template_id,
            next_seq,
            name,
            mode,
            lead_role_name,
            instructions,
            json.dumps(gate_ids or GATE_IDS, ensure_ascii=False),
            min_gate_pass_ratio,
            max_retry,
            1 if requires_human_approval else 0,
            1 if is_primary_deliverable else 0,
            expected_output,
        ),
    )
    stage_id = cur.lastrowid
    assert stage_id is not None
    for seq, (role_name, agent_instructions) in enumerate(agents, start=1):
        conn.execute(
            "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
            "instructions) VALUES (?, ?, ?, ?)",
            (stage_id, role_name, seq, agent_instructions),
        )
    audit.record(
        conn,
        actor_id,
        "pipeline_stage_added",
        "pipeline_stage",
        stage_id,
        after={"template_id": template_id, "name": name, "mode": mode},
    )
    conn.commit()
    return stage_id


def update_stage(
    conn: sqlite3.Connection, actor_id: int, stage_id: int, **fields
) -> None:
    """허용 필드만 갱신한다 — `agents`/`mode` 변경은 구조가 바뀌므로
    지원하지 않는다(단계를 지우고 새로 만드는 쪽이 안전하다 — 실행
    이력이 `stage_id`를 FK로 참조하는데 구성을 바꿔버리면 과거 이력의
    의미가 틀어진다)."""
    allowed = {
        "name",
        "instructions",
        "gate_ids",
        "min_gate_pass_ratio",
        "max_retry",
        "requires_human_approval",
        "is_primary_deliverable",
        "expected_output",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported_fields:{sorted(unknown)}")
    row = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    if row is None:
        raise ValueError("pipeline_stage_not_found")
    if not fields:
        return
    set_clauses = []
    values: list[object] = []
    for key, value in fields.items():
        if key == "gate_ids":
            value = json.dumps(value, ensure_ascii=False)
        elif key in ("requires_human_approval", "is_primary_deliverable"):
            value = 1 if value else 0
        set_clauses.append(f"{key} = ?")
        values.append(value)
    values.append(stage_id)
    conn.execute(
        f"UPDATE pipeline_stages SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    audit.record(
        conn,
        actor_id,
        "pipeline_stage_updated",
        "pipeline_stage",
        stage_id,
        after=fields,
    )
    conn.commit()


def delete_stage(conn: sqlite3.Connection, actor_id: int, stage_id: int) -> None:
    row = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    if row is None:
        raise ValueError("pipeline_stage_not_found")
    has_runs = conn.execute(
        "SELECT 1 FROM pipeline_run_stages WHERE stage_id = ? LIMIT 1", (stage_id,)
    ).fetchone()
    if has_runs is not None:
        raise ValueError("pipeline_stage_has_run_history")
    conn.execute("DELETE FROM pipeline_stage_agents WHERE stage_id = ?", (stage_id,))
    conn.execute("DELETE FROM pipeline_stages WHERE id = ?", (stage_id,))
    audit.record(
        conn,
        actor_id,
        "pipeline_stage_deleted",
        "pipeline_stage",
        stage_id,
        before={"name": row["name"]},
    )
    conn.commit()


def reorder_stage(
    conn: sqlite3.Connection, actor_id: int, stage_id: int, direction: str
) -> None:
    """인접 단계와 `seq_order`를 교체한다(`direction`: "up"|"down")."""
    if direction not in ("up", "down"):
        raise ValueError(f"invalid_direction:{direction!r}")
    row = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    if row is None:
        raise ValueError("pipeline_stage_not_found")
    comparator = "<" if direction == "up" else ">"
    order_by = "DESC" if direction == "up" else "ASC"
    neighbor = conn.execute(
        f"SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? "
        f"AND seq_order {comparator} ? ORDER BY seq_order {order_by} LIMIT 1",
        (row["pipeline_template_id"], row["seq_order"]),
    ).fetchone()
    if neighbor is None:
        return  # 이미 맨 위/맨 아래 — 조용히 아무 것도 안 함(UI 쪽에서 버튼 비활성 처리와 중복 방어)
    # 실측으로 발견한 버그 — UNIQUE(pipeline_template_id, seq_order) 제약은
    # SQLite에서 즉시(문장 단위로) 검사된다. 두 행의 seq_order를 그대로
    # 맞바꾸면 첫 UPDATE가 상대방이 아직 갖고 있는 값과 충돌해
    # IntegrityError가 난다 — 음수 임시값(절대 실제 seq_order로 쓰이지
    # 않음, `next_seq`는 항상 MAX+1)을 거쳐 3단계로 교체한다.
    conn.execute("UPDATE pipeline_stages SET seq_order = -1 WHERE id = ?", (row["id"],))
    conn.execute(
        "UPDATE pipeline_stages SET seq_order = ? WHERE id = ?",
        (row["seq_order"], neighbor["id"]),
    )
    conn.execute(
        "UPDATE pipeline_stages SET seq_order = ? WHERE id = ?",
        (neighbor["seq_order"], row["id"]),
    )
    audit.record(
        conn,
        actor_id,
        "pipeline_stage_reordered",
        "pipeline_stage",
        stage_id,
        before={"seq_order": row["seq_order"]},
        after={"seq_order": neighbor["seq_order"]},
    )
    conn.commit()


# ---------- 실행 엔진 ----------


def start_pipeline_run(
    conn: sqlite3.Connection, actor_id: int, template_id: int, task_id: int
) -> int:
    template = conn.execute(
        "SELECT * FROM pipeline_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if template is None:
        raise ValueError("pipeline_template_not_found")
    if not template["is_active"]:
        raise ValueError("pipeline_template_inactive")
    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("task_not_found")
    stage_count = conn.execute(
        "SELECT COUNT(*) AS c FROM pipeline_stages WHERE pipeline_template_id = ?",
        (template_id,),
    ).fetchone()["c"]
    if stage_count == 0:
        raise ValueError("pipeline_template_has_no_stages")
    already_running = conn.execute(
        "SELECT id FROM pipeline_runs WHERE task_id = ? AND status IN "
        "('running', 'paused_for_approval')",
        (task_id,),
    ).fetchone()
    if already_running is not None:
        raise LookupError("pipeline_already_running_for_task")
    cur = conn.execute(
        "INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id) "
        "VALUES (?, ?, ?)",
        (template_id, task_id, actor_id),
    )
    run_id = cur.lastrowid
    assert run_id is not None
    audit.record(
        conn,
        actor_id,
        "pipeline_run_started",
        "pipeline_run",
        run_id,
        after={"template_id": template_id, "task_id": task_id},
    )
    conn.commit()
    return run_id


def _gate_pass_ratio(gate_scores: dict) -> float:
    measured = [g for g in gate_scores.values() if not g.get("not_measured")]
    if not measured:
        return 1.0  # 측정 신호가 없으면 막을 근거가 없다(기존 webui Gate 표시 원칙과 동일).
    passed = [g for g in measured if g.get("passed")]
    return len(passed) / len(measured)


def _gate_failure_summary(gate_scores: dict) -> str:
    failed = [
        f"{gid}({g.get('score')})"
        for gid, g in gate_scores.items()
        if not g.get("not_measured") and not g.get("passed")
    ]
    return ", ".join(failed) if failed else "(세부 사유 없음)"


def _stage_initial_input(conn: sqlite3.Connection, task_id: int) -> str:
    """1단계(이전 산출물이 없는 단계)의 입력 — `run_crew_for_team()`과
    같은 패턴: Task의 role_name/required_skills + 최신 사람 작성
    deliverable."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    required_skills = json.loads(task["required_skills"])
    skills_text = ", ".join(required_skills) if required_skills else "없음"
    parts = [f"'{task['role_name']}' 업무를 수행하세요(필요 스킬: {skills_text})."]
    latest_human = conn.execute(
        "SELECT content FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if latest_human is not None:
        parts.append(f"참고할 사람 작성 자료:\n{latest_human['content']}")
    return "\n\n".join(parts)


def _resolve_role_agent_id(conn: sqlite3.Connection, role_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = ? AND status = 'active'",
        (role_name,),
    ).fetchone()
    if row is None:
        raise ValueError(f"stage_role_not_active_or_missing:{role_name!r}")
    return row["id"]


def run_pipeline_stage_job(
    conn: sqlite3.Connection,
    run_id: int,
    actor_id: int,
    on_stage_resolved: Callable[[], None] | None = None,
) -> None:
    """`start_pipeline_run()`/`resolve_stage_decision()`이 BackgroundTasks로
    예약한다 — 현재 단계 1개만 실행하고 끝낸다(통과하면 즉시 다음 단계를
    재귀적으로 실행, 승인 대기/에스컬레이션이면 멈춘다). 백그라운드
    경계(`deliverables.run_team_crew_job()`과 같은 원칙) — 예외는 여기서
    끝내고 `pipeline_runs.status='failed'`로 기록한다.

    `on_stage_resolved`(2026-10-28, 사용자 보고 "결재 승인 되었으나,
    대화창의 경우 이후 진행 확인되지 않음") — 승인 불필요 단계가
    여러 개 연달아 통과하면(Gate 0.0인 단계들) `_execute_stage_attempt()`
    가 같은 호출 안에서 그 전부를 재귀로 조용히 통과시킨다. 대화에서
    트리거된 실행은 전체가 끝날 때까지(수 분~수십 분) 아무 메시지도
    안 올라와 "멈췄나?"로 보였다 — 이 콜백을 매 단계 resolve 시점마다
    불러 `conversations.post_pipeline_run_status_message()`가 그때그때
    진행 메시지를 올릴 수 있게 한다. Task 협업 워크스페이스 경로(콜백
    안 넘김)는 기존과 100% 동일하게 동작한다."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["status"] not in ("running",):
        return  # 이미 멈춰있거나(승인대기/완료/실패) 삭제됨 — 조용히 무시.

    stage = conn.execute(
        "SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (run["pipeline_template_id"], run["current_stage_seq"]),
    ).fetchone()
    if stage is None:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'completed', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()
        return

    try:
        _execute_stage_attempt(conn, run, stage, actor_id, on_stage_resolved)
    except Exception as exc:  # noqa: BLE001 — 백그라운드 작업의 최종 경계
        conn.rollback()
        conn.execute(
            "UPDATE pipeline_runs SET status = 'failed', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        audit.record(
            conn,
            actor_id,
            "pipeline_run_failed",
            "pipeline_run",
            run_id,
            after={"error": str(exc)},
        )
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()


def _execute_stage_attempt(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
    stage: sqlite3.Row,
    actor_id: int,
    on_stage_resolved: Callable[[], None] | None = None,
) -> None:
    run_id = run["id"]
    stage_id = stage["id"]
    attempt = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt FROM pipeline_run_stages "
        "WHERE pipeline_run_id = ? AND stage_id = ?",
        (run_id, stage_id),
    ).fetchone()["next_attempt"]

    stage_agents = conn.execute(
        "SELECT * FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order, id",
        (stage_id,),
    ).fetchall()
    agent_ids = [_resolve_role_agent_id(conn, a["role_name"]) for a in stage_agents]

    prior = conn.execute(
        "SELECT output FROM pipeline_run_stages WHERE pipeline_run_id = ? "
        "AND status IN ('passed', 'approved') ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    base_input = (
        prior["output"]
        if prior is not None
        else _stage_initial_input(conn, run["task_id"])
    )

    retry_feedback = ""
    if attempt > 1:
        last_attempt = conn.execute(
            "SELECT status, gate_scores, decision_reason FROM pipeline_run_stages "
            "WHERE pipeline_run_id = ? AND stage_id = ? AND attempt = ? "
            "ORDER BY id DESC LIMIT 1",
            (run_id, stage_id, attempt - 1),
        ).fetchone()
        # 사용자 요청(2026-10-28, "반려 사유를 재시도 지시문에 직접
        # 끼워 넣는 식으로 개선") — 이전 시도가 사람 반려였으면(Gate는
        # 이미 통과했던 상태라 `_gate_failure_summary()`로는 "세부
        # 사유 없음"만 나와 사람이 왜 반려했는지 전혀 안 알려줬다)
        # 사람이 적은 사유를 그대로 다음 시도 지시문에 끼워 넣는다 —
        # Gate 자동 실패 재시도(기존 로직)와는 별개 분기.
        if last_attempt is not None and last_attempt["status"] == "rejected":
            if last_attempt["decision_reason"]:
                retry_feedback = (
                    f"\n\n[이전 시도({attempt - 1}차) 사람 반려 사유] "
                    f"{last_attempt['decision_reason']} 이를 구체적으로 반영해 "
                    "다시 작성하세요."
                )
            else:
                retry_feedback = (
                    f"\n\n[이전 시도({attempt - 1}차)] 사람이 반려했습니다(구체적 "
                    "사유 입력 없음) — 내용을 다시 점검해 더 신중하게 작성하세요."
                )
        elif last_attempt is not None and last_attempt["gate_scores"]:
            summary = _gate_failure_summary(json.loads(last_attempt["gate_scores"]))
            retry_feedback = (
                f"\n\n[이전 시도({attempt - 1}차) Gate 결과] 다음 기준을 통과하지 "
                f"못했습니다: {summary}. 이를 구체적으로 보완해 다시 작성하세요."
            )

    # 사용자 요청(2026-11-05, 로드맵 6번 "자동 단계적 에스컬레이션") —
    # 지금까지 에스컬레이션은 완전히 이진(재시도 N번 → 바로 사람)이었다.
    # 사람에게 올라가기 직전(마지막 자동 시도)은 일반 재시도보다 한
    # 단계 더 꼼꼼한 self_review(로드맵 2번 "Agent 자가비평")를 써서,
    # "일반 재시도"와 "사람 에스컬레이션" 사이에 자동 단계를 하나 더
    # 끼워 넣는다. `parallel_delegated`(process="hierarchical")는
    # self_review와 구조적으로 호환되지 않는다(`crew_runtime.
    # run_crew()` 자체의 검증과 동일한 제약) — 그 모드는 제외하고
    # 기존 동작 그대로 둔다(현재 시드된 "코드 검증"/"QA 기능테스트"/
    # "품질 관리 최종 판정" — 실제로 에스컬레이션 가능한 유일한 3단계
    # — 는 전부 `single`/`sequential_relay`라 영향받음).
    use_self_review_tier = (
        attempt == stage["max_retry"] and stage["mode"] != "parallel_delegated"
    )
    if use_self_review_tier:
        retry_feedback += (
            "\n\n[마지막 자동 재시도] 이번이 자동 재시도의 마지막 기회입니다 — "
            "스스로 작성한 초안을 원 요구사항과 한 줄씩 대조해 빠지거나 "
            "깨진 부분을 반드시 수정하세요. 이번에도 기준을 충족하지 못하면 "
            "사람에게 결재 요청이 올라갑니다."
        )

    stage_instructions = stage["instructions"] + retry_feedback
    combined_base = (
        f"{base_input}\n\n{stage_instructions}" if stage_instructions else base_input
    )

    if stage["mode"] == "parallel_delegated":
        lead_agent_id = _resolve_role_agent_id(conn, stage["lead_role_name"])
        coworker_rows = [
            a for a in stage_agents if a["role_name"] != stage["lead_role_name"]
        ]
        coworker_guides = "\n".join(
            f"- {a['role_name']}: {a['instructions']}"
            for a in coworker_rows
            if a["instructions"]
        )
        lead_description = combined_base
        if coworker_guides:
            lead_description += f"\n\n[코워커별 참고 가이드]\n{coworker_guides}"
        run_agent_ids = [lead_agent_id] + [
            _resolve_role_agent_id(conn, a["role_name"]) for a in coworker_rows
        ]
        task_descriptions = [lead_description] * len(run_agent_ids)
        process = "hierarchical"
        manager_agent_id = lead_agent_id
    elif stage["mode"] == "sequential_relay":
        run_agent_ids = agent_ids
        task_descriptions = [
            combined_base
            + (
                f"\n\n[이 단계에서 당신의 구체적 역할] {a['instructions']}"
                if a["instructions"]
                else ""
            )
            for a in stage_agents
        ]
        process = "sequential"
        manager_agent_id = None
    else:  # single
        run_agent_ids = agent_ids
        task_descriptions = [combined_base]
        process = "sequential"
        manager_agent_id = None

    # 사용자 요청(2026-10-20, "고급 에이전트 관리") — 이 단계에 저장된
    # `expected_output`(없으면 빈 문자열)을 모든 참여 Agent에 동일하게
    # 전달한다. `run_crew()`가 빈 값이면 그 Agent 자신의 저장된 기본값 →
    # 플랫폼 공통 문구 순으로 추가 폴백한다.
    expected_outputs = [stage["expected_output"]] * len(run_agent_ids)

    result = crew_runtime.run_crew(
        conn,
        run_agent_ids,
        task_descriptions,
        actor_id,
        process=process,
        manager_agent_id=manager_agent_id,
        expected_outputs=expected_outputs,
        self_review=use_self_review_tier,
    )
    tcr = result["tcr"]

    stage_output = (
        result["agents"][-1]["output"] if result["agents"] else result["final_output"]
    )
    author_agent_id = (
        result["agents"][-1]["agent_id"] if result["agents"] else run_agent_ids[-1]
    )

    # 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사 + 요구사항
    # 추적성 자동 채점") — Gate A-G는 순수 휴리스틱이라 "구현" 단계
    # 산출물이 실제로 파싱 가능한 코드인지 기계적으로 확인하지 않았다.
    # `_gate_pass_ratio()`(아래)는 제네릭하게 모든 키를 순회하므로,
    # 실제 차단 임계값(min_gate_pass_ratio=0.5)이 걸린 "코드 검증"/
    # "QA 기능테스트"/"품질 관리 최종 판정" 단계에까지 이 점수를
    # 무심코 끼워 넣으면(그 단계들의 리포트성 산출물이 코드를 인용하는
    # 경우) 기존 통과/차단 동작이 조용히 바뀔 위험이 있다 — 그래서
    # "구현" 단계(min_gate_pass_ratio=0.0, 절대 차단 안 됨)에만 한정해
    # 실제 점수로 참여시킨다. 다른 단계는 아예 이 키를 추가하지 않는다.
    gate_scores = dict(result["gate_scores"])
    if stage["name"] == "구현":
        gate_scores["code_syntax"] = agent_evaluation.compute_code_syntax_check(
            stage_output
        )

    # "코드 검증" 단계는 바로 위 `prior` 조회처럼 이전 단계 출력만 보고
    # 1단계(요구분석/계획 확정)의 요구사항 문서를 자동으로 보지 못한다
    # — 별도로 직접 조회해 LLM judge로 요구사항 추적성을 채점한다.
    # "코드 검증"은 이미 `min_gate_pass_ratio=0.5`인 실제 차단 임계값이
    # 걸려 있는 단계라, 아직 임계값을 신뢰할 데이터가 없는 이 새 점수가
    # 그 판정을 조용히 바꾸지 않도록 `not_measured`를 강제한다 —
    # 정보 제공 전용(v1), 실제 점수/세부 내역은 그대로 저장돼 화면에는
    # 보인다.
    if stage["name"] == "코드 검증":
        requirements_row = conn.execute(
            "SELECT prs.output FROM pipeline_run_stages prs "
            "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
            "WHERE prs.pipeline_run_id = ? AND ps.seq_order = 1 "
            "AND prs.status IN ('passed', 'approved') "
            "ORDER BY prs.id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if requirements_row is not None:
            traceability = requirements_traceability_llm.assess_traceability(
                conn, requirements_row["output"], stage_output
            )
            traceability["not_measured"] = True
            gate_scores["requirements_traceability"] = traceability

    ratio = _gate_pass_ratio(gate_scores)
    passed = ratio >= stage["min_gate_pass_ratio"]

    cur = conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, "
        "output, gate_scores, tcr) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            stage_id,
            attempt,
            "passed" if passed else "failed_retrying",
            stage_output,
            json.dumps(gate_scores, ensure_ascii=False),
            tcr,
        ),
    )
    run_stage_id = cur.lastrowid
    assert run_stage_id is not None

    if not passed:
        # 사용자 요청(2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도
        # 예산 추적") — `stage["max_retry"]`는 "이 단계 안에서"만의
        # 상한이라, 여러 단계·여러 파이프라인 실행·여러 Crew 재시도에
        # 걸쳐 전체적으로 계속 실패를 반복하는 Task를 막지 못한다.
        # `tasks.max_retry_budget`(기본 NULL=제한 없음, 설정 시에만
        # 적용)이 Task 전체 누적 재시도 수를 넘겼으면, 이 단계의
        # 재시도가 아직 남아 있어도(`attempt < max_retry`) 더 재시도
        # 하지 않고 바로 에스컬레이션한다 — 기존 "재시도 소진" 분기를
        # 그대로 재사용(코드 중복 없음).
        task_row = conn.execute(
            "SELECT max_retry_budget FROM tasks WHERE id = ?", (run["task_id"],)
        ).fetchone()
        budget_exceeded = False
        if task_row["max_retry_budget"] is not None:
            retries_so_far = dashboard.task_retry_summary(conn, run["task_id"])[
                "total_retries"
            ]
            budget_exceeded = retries_so_far >= task_row["max_retry_budget"]

        if attempt < stage["max_retry"] and not budget_exceeded:
            conn.commit()
            if on_stage_resolved is not None:
                on_stage_resolved()
            _execute_stage_attempt(conn, run, stage, actor_id, on_stage_resolved)
            return
        conn.execute(
            "UPDATE pipeline_run_stages SET status = 'escalated' WHERE id = ?",
            (run_stage_id,),
        )
        conn.execute(
            "UPDATE pipeline_runs SET status = 'paused_for_approval' WHERE id = ?",
            (run_id,),
        )
        approvals.create_approval_chain(
            conn, actor_id, "pipeline_run_stage", run_stage_id
        )
        audit.record(
            conn,
            actor_id,
            "pipeline_stage_escalated",
            "pipeline_run_stage",
            run_stage_id,
            after={
                "attempt": attempt,
                "gate_scores": gate_scores,
                "retry_budget_exceeded": budget_exceeded,
            },
        )
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()
        return

    # 통과 — 사람이 볼 수 있게 deliverables에 남긴다(요청대로 Task 협업
    # 워크스페이스 경로에 연결). 승인이 필요 없는 단계는 Gate 통과 자체가
    # 승인이라는 뜻이므로 'approved'로 바로 올린다(이게 "최소 HITL"의
    # 핵심 — 결재함을 거치지 않고 즉시 다음 단계로 전진한다).
    deliverable_status = (
        "pending_review" if stage["requires_human_approval"] else "approved"
    )
    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, 'agent', ?, ?, 'text', ?)",
        (run["task_id"], author_agent_id, stage_output, deliverable_status),
    )
    deliverable_id = cur.lastrowid
    assert deliverable_id is not None
    agent_evaluation.record_evaluation(conn, deliverable_id, gate_scores, tcr)
    conn.execute(
        "UPDATE pipeline_run_stages SET deliverable_id = ?, finished_at = datetime('now') "
        "WHERE id = ?",
        (deliverable_id, run_stage_id),
    )

    if stage["requires_human_approval"]:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'paused_for_approval' WHERE id = ?",
            (run_id,),
        )
        approvals.create_approval_chain(
            conn, actor_id, "pipeline_run_stage", run_stage_id
        )
        audit.record(
            conn,
            actor_id,
            "pipeline_stage_awaiting_approval",
            "pipeline_run_stage",
            run_stage_id,
            after={"deliverable_id": deliverable_id},
        )
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()
        return

    next_seq = run["current_stage_seq"] + 1
    has_next = conn.execute(
        "SELECT 1 FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (run["pipeline_template_id"], next_seq),
    ).fetchone()
    if has_next is None:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'completed', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        audit.record(conn, actor_id, "pipeline_run_completed", "pipeline_run", run_id)
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()
        return
    conn.execute(
        "UPDATE pipeline_runs SET current_stage_seq = ? WHERE id = ?",
        (next_seq, run_id),
    )
    conn.commit()
    if on_stage_resolved is not None:
        on_stage_resolved()
    next_run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    next_stage = conn.execute(
        "SELECT * FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
        (run["pipeline_template_id"], next_seq),
    ).fetchone()
    _execute_stage_attempt(conn, next_run, next_stage, actor_id, on_stage_resolved)


def resolve_stage_decision(
    conn: sqlite3.Connection,
    actor_id: int,
    pipeline_run_stage_id: int,
    approved: bool,
    reason: str | None = None,
) -> int:
    """`app.py`의 `decide_approval()`이 `subject_type == 'pipeline_run_stage'`
    일 때 호출한다. 반환값은 `run_id` — 호출자가 승인(다음 단계 실행)이면
    `background_tasks.add_task(run_pipeline_stage_job, conn, run_id, actor_id)`
    로 이어서 돌려야 한다(이 함수 자체는 DB 상태만 바꾸고 LLM을 호출하지
    않는다 — 결재 요청·응답 자체는 빠르게 끝나야 한다)."""
    run_stage = conn.execute(
        "SELECT * FROM pipeline_run_stages WHERE id = ?", (pipeline_run_stage_id,)
    ).fetchone()
    if run_stage is None:
        raise ValueError("pipeline_run_stage_not_found")
    run = conn.execute(
        "SELECT * FROM pipeline_runs WHERE id = ?", (run_stage["pipeline_run_id"],)
    ).fetchone()
    assert run is not None
    stage = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ?", (run_stage["stage_id"],)
    ).fetchone()
    assert stage is not None
    run_id = run["id"]

    if approved:
        conn.execute(
            "UPDATE pipeline_run_stages SET status = 'approved' WHERE id = ?",
            (pipeline_run_stage_id,),
        )
        # 실측 버그(2026-10-19, 사용자 보고 — "내게 필요한 조치"에 같은
        # 업무가 중복 표시됨): 여기서 `pipeline_run_stages.status`만
        # 'approved'로 바꾸고 그 단계가 만든 `deliverables.status`는
        # 그대로 'pending_review'에 남겨뒀었다 — Gate 경고가 있는 채로
        # 사람 승인까지 끝난 단계인데도 `inbox.get_inbox()`의
        # `low_gate_score` 항목(판정 기준: status=='pending_review' +
        # 낮은 Gate)이 계속 그 deliverable을 "아직 조치 필요"로 집어
        # 올렸다. 같은 task가 파이프라인을 여러 번 돌리면(재시도 등)
        # 회차마다 다른 deliverable_id가 남아, 화면엔 똑같은 문구의
        # 카드가 회차 수만큼 중복으로 보였다. 사람 승인 자체가 그
        # deliverable에 대한 최종 결정이므로, 파이프라인 단계 결정을
        # deliverable 상태에도 그대로 반영한다.
        if run_stage["deliverable_id"] is not None:
            deliverables.resolve_deliverable_decision(
                conn, run_stage["deliverable_id"], True
            )
        next_seq = run["current_stage_seq"] + 1
        has_next = conn.execute(
            "SELECT 1 FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = ?",
            (run["pipeline_template_id"], next_seq),
        ).fetchone()
        if has_next is None:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'completed', finished_at = datetime('now') "
                "WHERE id = ?",
                (run_id,),
            )
        else:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'running', current_stage_seq = ? WHERE id = ?",
                (next_seq, run_id),
            )
        audit.record(
            conn,
            actor_id,
            "pipeline_stage_approved",
            "pipeline_run_stage",
            pipeline_run_stage_id,
            after={"reason": reason},
        )
    else:
        # 반려 = "다시 해와"(기존 human-approval-loop 스킬 안내와 일치) —
        # max_retry를 아직 안 넘었으면 사유를 피드백으로 반영해 같은
        # 단계를 재시도시킨다. 이미 다 썼으면 파이프라인을 종료한다.
        # `reason`을 `decision_reason`에 저장해두면(2026-10-28, 사용자
        # 요청 "반려 사유를 재시도 지시문에 직접 끼워 넣는 식으로
        # 개선") 재시도 시 `_execute_stage_attempt()`가 그대로 읽어
        # 다음 시도의 프롬프트에 끼워 넣는다 — 여기서 비워뒀으면 그냥
        # `audit_log`에만 남고 끝(기존 동작과 동일).
        conn.execute(
            "UPDATE pipeline_run_stages SET status = 'rejected', decision_reason = ? "
            "WHERE id = ?",
            (reason, pipeline_run_stage_id),
        )
        if run_stage["deliverable_id"] is not None:
            deliverables.resolve_deliverable_decision(
                conn, run_stage["deliverable_id"], False
            )
        if run_stage["attempt"] < stage["max_retry"]:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'running' WHERE id = ?", (run_id,)
            )
        else:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'failed', finished_at = datetime('now') "
                "WHERE id = ?",
                (run_id,),
            )
        audit.record(
            conn,
            actor_id,
            "pipeline_stage_rejected",
            "pipeline_run_stage",
            pipeline_run_stage_id,
            after={"reason": reason},
        )
    conn.commit()
    return run_id


def list_pipeline_runs_for_task(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """Task 협업 워크스페이스 진행상황 카드가 폴링한다."""
    runs = conn.execute(
        "SELECT * FROM pipeline_runs WHERE task_id = ? ORDER BY id DESC", (task_id,)
    ).fetchall()
    result = []
    for run in runs:
        template = conn.execute(
            "SELECT name FROM pipeline_templates WHERE id = ?",
            (run["pipeline_template_id"],),
        ).fetchone()
        stage_results = conn.execute(
            "SELECT prs.*, ps.name AS stage_name, ps.seq_order AS stage_seq_order, "
            "ps.is_primary_deliverable AS stage_is_primary_deliverable "
            "FROM pipeline_run_stages prs "
            "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
            "WHERE prs.pipeline_run_id = ? ORDER BY ps.seq_order, prs.attempt",
            (run["id"],),
        ).fetchall()
        result.append(
            {
                "id": run["id"],
                "pipeline_template_id": run["pipeline_template_id"],
                "pipeline_template_name": template["name"] if template else None,
                "status": run["status"],
                "current_stage_seq": run["current_stage_seq"],
                "created_at": run["created_at"],
                "finished_at": run["finished_at"],
                "stage_results": [
                    {
                        "id": sr["id"],
                        "stage_name": sr["stage_name"],
                        "stage_seq_order": sr["stage_seq_order"],
                        "attempt": sr["attempt"],
                        "status": sr["status"],
                        "gate_scores": (
                            json.loads(sr["gate_scores"]) if sr["gate_scores"] else None
                        ),
                        "tcr": sr["tcr"],
                        "deliverable_id": sr["deliverable_id"],
                        "is_primary_deliverable": bool(
                            sr["stage_is_primary_deliverable"]
                        ),
                    }
                    for sr in stage_results
                ],
            }
        )
    return result
