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

import contextlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Sequence

from hair_platform import (
    agent_evaluation,
    approvals,
    audit,
    dashboard,
    deliverables,
    evaluation_reports,  # 리포트 토큰을 저장·응답에서 뺀다(2026-11-25)
    llm_usage,  # 업무 맥락별 사용량(2026-11-23, P2-11)
    presentation_deck,
    requirements_traceability_llm,
)

# `plan_merge`(2026-11-23, P2-9 — `plan_merge.py`): 리드 계획 → 팀원 분담 실행 → 리드 병합을 플랫폼이
# 직접 진행한다(위임형처럼 모델의 즉흥 위임에 맡기지 않는다).
VALID_MODES = ("single", "sequential_relay", "parallel_delegated", "plan_merge")
LEAD_MODES = ("parallel_delegated", "plan_merge")
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
        "checks_requirements_traceability": bool(
            stage_row["checks_requirements_traceability"]
        ),
        "is_requirements_source": bool(stage_row["is_requirements_source"]),
        "strict_review": bool(stage_row["strict_review"]) if "strict_review" in stage_row.keys() else False,
        "on_fail_return_stage_id": stage_row["on_fail_return_stage_id"],
        "output_content_type": stage_row["output_content_type"],
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
    gate_ids: Sequence[str] | None = None,
    min_gate_pass_ratio: float = 0.0,
    max_retry: int = 2,
    requires_human_approval: bool = False,
    is_primary_deliverable: bool = False,
    checks_requirements_traceability: bool = False,
    is_requirements_source: bool = False,
    on_fail_return_stage_id: int | None = None,
    output_content_type: str = "text",
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
    if output_content_type not in ("text", "markdown", "html", "code"):
        raise ValueError(f"invalid_output_content_type:{output_content_type!r}")
    if not agents:
        raise ValueError("stage_needs_at_least_one_agent")
    if mode == "single" and len(agents) != 1:
        raise ValueError("single_mode_requires_exactly_one_agent")
    if mode in LEAD_MODES:
        if not lead_role_name:
            raise ValueError(f"{mode}_requires_lead_role_name")
        if lead_role_name not in [r for r, _ in agents]:
            raise ValueError("lead_role_name_must_be_in_agents")
        if len(agents) < 2:
            raise ValueError(f"{mode}_requires_at_least_one_coworker")
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
        "requires_human_approval, is_primary_deliverable, "
        "checks_requirements_traceability, output_content_type, expected_output, "
        "is_requirements_source, on_fail_return_stage_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            1 if checks_requirements_traceability else 0,
            output_content_type,
            expected_output,
            1 if is_requirements_source else 0,
            on_fail_return_stage_id or None,
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
        "checks_requirements_traceability",
        "is_requirements_source",
        "on_fail_return_stage_id",
        "output_content_type",
        "expected_output",
        "strict_review",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported_fields:{sorted(unknown)}")
    if "output_content_type" in fields and fields["output_content_type"] not in (
        "text",
        "markdown",
        "html",
        "code",
    ):
        raise ValueError(
            f"invalid_output_content_type:{fields['output_content_type']!r}"
        )
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
        elif key in (
            "requires_human_approval",
            "is_primary_deliverable",
            "checks_requirements_traceability",
            "is_requirements_source",
            "strict_review",
        ):
            value = 1 if value else 0
        if key == "on_fail_return_stage_id":
            value = value or None  # 0·빈 값 = 해제
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


def reap_orphaned_pipeline_runs(conn: sqlite3.Connection) -> int:
    """실측(2026-11-06, park_member 로드러너 게임 데모) — CrewAI 매니저가
    위임을 반복하다 LLM 컨텍스트 윈도우를 초과하면, 그 예외가 CrewAI/
    LiteLLM 내부에서 로그만 남기고(`ERROR:root:...`) 재시도를 무한히
    반복할 뿐 우리 코드까지 올라오지 않는다 — `run_pipeline_stage_job()`
    의 기존 `try/except`(이 파일 상단)는 "예외가 올라오면" 잡는데, 이
    경우는 예외가 아예 안 올라오고 그 자리에서 영원히 멈춘다(실측:
    `pipeline_runs.status='running'`으로 2시간 넘게 방치, 에러 기록도
    에스컬레이션도 전혀 없었다 — 직접 DB를 찾아 수동으로 'failed'로
    고쳐야 했다).

    `deliverables.reap_orphaned_execution_jobs()`와 같은 전제(이 앱은
    단일 프로세스 — 기동 시점에 `running`으로 남은 행은 전부 이전
    프로세스의 잔재로 확정할 수 있다)로, 서버 재기동 시 이런 멈춘 실행을
    전부 정리한다. 프로세스가 살아있는 동안의 실시간 복구(타임아웃
    워치독)는 이 앱 전체가 공유하는 단일 sqlite3 커넥션을 다른 스레드와
    동시에 쓰게 만들어야 해 더 위험한 변경이라 범위 밖으로 둔다 — 재기동
    (이 세션 전체가 이미 관례로 삼은 WAL-safe 백업 후 재기동)이 그 복구
    지점이 된다."""
    rows = conn.execute(
        "SELECT id FROM pipeline_runs WHERE status = 'running'"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'failed', finished_at = datetime('now') "
            "WHERE id = ?",
            (row["id"],),
        )
        # 재시작으로 중단됨 표시(2026-11-25, P1-6) — "이어서 실행"으로 진행 중이던 단계부터 다시 돌릴 수 있다.
        # 자동으로 이어 가지는 않는다: 위 사례처럼 멈춘 실행이 재시작마다 같은 비용을 반복할 수 있어 사람이 확인한다.
        try:
            conn.execute("UPDATE pipeline_runs SET interrupted_at = datetime('now') WHERE id = ?", (row["id"],))
        except sqlite3.OperationalError:
            pass
    if rows:
        conn.commit()
    return len(rows)


def resume_interrupted_run(conn: sqlite3.Connection, actor_id: int, run_id: int) -> str:
    """재시작으로 중단된 실행을 다시 `running`으로 돌린다(2026-11-25, P1-6). 돌려주는 값은 엔진("legacy"|"graph") —
    호출자가 그 엔진의 실행 함수를 백그라운드로 건다. 끝난 단계는 다시 하지 않는다: 기본 엔진은 진행 중이던 단계
    (`current_stage_seq`)부터, 그래프 엔진은 마지막 체크포인트부터 이어 간다."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("실행을 찾을 수 없습니다")
    keys = run.keys()
    if run["status"] != "failed" or "interrupted_at" not in keys or not run["interrupted_at"]:
        raise ValueError("서버 재시작으로 중단된 실행만 이어서 실행할 수 있습니다")
    conn.execute(
        "UPDATE pipeline_runs SET status = 'running', finished_at = NULL, interrupted_at = NULL WHERE id = ?",
        (run_id,),
    )
    audit.record(conn, actor_id, "pipeline_run_resumed", "pipeline_runs", run_id,
                 before={"interrupted_at": run["interrupted_at"]},
                 after={"stage_seq": run["current_stage_seq"], "engine": run["engine"] if "engine" in keys else "legacy"})
    conn.commit()
    return (run["engine"] if "engine" in keys else None) or "legacy"


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


def request_pipeline_run_cancel(
    conn: sqlite3.Connection, actor_id: int, run_id: int
) -> dict:
    """사용자 요청(2026-11-17, "Human Agency Scale... 코파일럿... 실행
    중단 요청" → "구현 시작"). `status='running'`이면 지금 당장 멈추지
    않고 `cancel_requested` 플래그만 세워 `_execute_stage_attempt()`가
    다음 안전한 지점(다음 시도/다음 단계 시작 직전)에서 알아서
    멈추게 한다. `status='paused_for_approval'`이면 그 사이에 실행
    중인 배경 작업이 아예 없으므로(사람 결재를 기다리며 그냥 멈춰
    있음) 즉시 'cancelled'로 전이한다. 이미 끝난 실행(`completed`/
    `failed`/`cancelled`)은 취소할 대상이 없어 거부한다."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("pipeline_run_not_found")
    if run["status"] == "running":
        conn.execute(
            "UPDATE pipeline_runs SET cancel_requested = 1 WHERE id = ?", (run_id,)
        )
        audit.record(
            conn, actor_id, "pipeline_run_cancel_requested", "pipeline_runs", run_id
        )
    elif run["status"] == "paused_for_approval":
        conn.execute(
            "UPDATE pipeline_runs SET status = 'cancelled', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        audit.record(
            conn, actor_id, "pipeline_run_cancelled", "pipeline_runs", run_id
        )
    else:
        raise ValueError("pipeline_run_already_finished")
    conn.commit()
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    assert run is not None
    return dict(run)


def _gate_pass_ratio(gate_scores: dict, gate_ids=None) -> float:
    """통과 비율 — `gate_ids`(단계 설정)를 주면 그 목록에 든 점수만 센다(2026-11-21,
    "모든 파이프라인 점검"). 예전엔 단계의 `gate_ids` 설정을 무시하고 측정된 모든 점수를
    셌다 — 파이프라인(CrewAI) 경로에서 C(실행 안정성)·D(응답 30초 SLA)·F(크루 협업
    구조)·G(추론 노출)는 내용과 무관하게 늘 같은 값이라, 검증 단계가 내용과 상관없이
    매번 기준 미달로 사람 검토에 넘어갔다. 목록에 없는 점수도 기록·표시는 그대로 한다."""
    if gate_ids is not None:
        wanted = set(gate_ids)
        gate_scores = {k: v for k, v in gate_scores.items() if k in wanted}
    # 참고용 검사(`advisory` — 낱말확인 등, 2026-11-25)는 통과 판정에 넣지 않는다.
    measured = [g for g in gate_scores.values() if not g.get("not_measured") and not g.get("advisory")]
    if not measured:
        return 1.0  # 측정 신호가 없으면 막을 근거가 없다(기존 webui Gate 표시 원칙과 동일).
    passed = [g for g in measured if g.get("passed")]
    return len(passed) / len(measured)


def requirements_traceability_score(
    conn: sqlite3.Connection, run_id: int, stage, stage_output: str
) -> dict | None:
    """요구사항 추적성(1단계 요구사항 대비 이 단계 산출물) — `checks_requirements_traceability`
    단계에서만 잰다. 두 실행 엔진 공통(2026-11-21 — 그래프 엔진은 지금까지 아예 재지
    않았다). 단계 `gate_ids`에 'requirements_traceability'가 있으면 통과 판정에 참여하고,
    없으면 예전처럼 정보 제공용(not_measured)으로만 남긴다."""
    if not stage["checks_requirements_traceability"]:
        return None
    # 기준 문서 = '요구사항 정의 단계'로 지정된 단계의 최신 통과 산출물(2026-11-22). 지정이 없으면
    # 1단계(기존 동작). 여러 단계를 지정했으면 이 단계보다 앞선 것 중 가장 뒤의 것.
    requirements_row = conn.execute(
        "SELECT prs.output FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? AND prs.status IN ('passed', 'approved') "
        "AND ps.is_requirements_source = 1 AND ps.seq_order < ? "
        "ORDER BY ps.seq_order DESC, prs.id DESC LIMIT 1",
        (run_id, stage["seq_order"]),
    ).fetchone() or conn.execute(
        "SELECT prs.output FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? AND ps.seq_order = 1 "
        "AND prs.status IN ('passed', 'approved') "
        "ORDER BY prs.id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if requirements_row is None:
        return None
    traceability = requirements_traceability_llm.assess_traceability(
        conn, requirements_row["output"], stage_output
    )
    gate_ids = json.loads(stage["gate_ids"]) if stage["gate_ids"] else []
    if "requirements_traceability" not in gate_ids:
        traceability["not_measured"] = True
    return traceability


def return_target_stage(conn: sqlite3.Connection, stage) -> sqlite3.Row | None:
    """검토 실패 시 되돌아갈 단계(`on_fail_return_stage_id`) — 같은 템플릿의, 이 단계보다 앞선
    단계일 때만. 지정이 없거나 잘못됐으면 None(예전처럼 이 단계만 재시도)."""
    target_id = stage["on_fail_return_stage_id"]
    if not target_id:
        return None
    target = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ? AND pipeline_template_id = ?",
        (target_id, stage["pipeline_template_id"]),
    ).fetchone()
    if target is None or target["seq_order"] >= stage["seq_order"]:
        return None
    return target


_REWORK_REVIEW_CHARS = 6000


_VERDICT_HEADING_RE = re.compile(r"^#{1,4}\s*(?:최종\s*)?판정\s*$", re.MULTILINE)
_VERDICT_REWORK_WORDS = ("보완 요청", "보완요청", "반려", "재작성", "수정 요청")
# 승인하면서 앞으로 고칠 곳을 요구하는 표현(조건부 승인) — "수정하면 완성도가 높아집니다", "고쳐야", "오타가 남아 있다".
# 이미 고친 것을 설명하는 문장("'화미팅' 오타→'화상 미팅' 수정 … 반영되었고" — 영업 실측 2차 판정)은 걸리지 않게
# "오타" 단독은 보지 않고, 앞으로의 요구(…하면·…해야·…필요)나 "남아 있음/발견" 같은 현재형만 본다.
_CONDITIONAL_FIX_RE = re.compile(
    r"(?:수정|보완|정정|교정)(?:하면|해야|이 필요|가 필요| 필요|을 권|를 권|하시기|해 주)"
    r"|고치면|고쳐야|바로잡아야|(?:통일|삭제|추가|보강|명시|정리)해야|맞춰야"
    # 2026-11-26 실측(P1-7 영업 메일)에서 놓친 표현 — "승인(조건부) … 확인 후 발송하세요", "… 반영하면 좋습니다"
    r"|조건부|반영하면|반영해야|반영이 필요|확인 후 (?:발송|진행|제출|사용|배포|공유)"
    r"|(?:오타|오기|오탈자|오류)[^.\n]{0,30}?(?:남아|있습니다|있음|있으며|있어|발견)"
)


def _stage_flag(stage, name: str) -> bool:
    """단계 행(sqlite3.Row 또는 dict)의 선택 플래그 — 예전 스키마(열 없음)면 False."""
    try:
        return bool(stage[name])
    except (IndexError, KeyError):
        return False


def review_verdict(output: str) -> dict:
    """검토 산출물의 '## 판정' 절에서 승인/보완 요청을 읽는다(2026-11-25). 절 첫 머리(400자)만 본다 —
    본문 다른 곳의 "보완 요청" 언급(예: 과거 이력 설명)으로 잘못 막지 않으려고. 절이 없거나 둘 다
    없으면 `not_measured`."""
    text = output or ""
    m = _VERDICT_HEADING_RE.search(text)
    if not m:
        return {"score": None, "status": "not_measured", "not_measured": True, "passed": False, "verdict": None}
    head = text[m.end(): m.end() + 400]
    nxt = re.search(r"^#{1,4}\s", head, re.MULTILINE)
    if nxt:
        head = head[: nxt.start()]
    rework_at = min((head.find(w) for w in _VERDICT_REWORK_WORDS if w in head), default=-1)
    approve_at = head.find("승인")
    if rework_at < 0 and approve_at < 0:
        return {"score": None, "status": "not_measured", "not_measured": True, "passed": False, "verdict": None}
    rework = rework_at >= 0 and (approve_at < 0 or rework_at <= approve_at)
    if rework:
        return {"score": 0.0, "status": "fail", "not_measured": False, "passed": False, "verdict": "보완 요청"}
    # 조건부 승인(2026-11-25, P0-4 실측 — 계약서 검토): "승인 — … 단, 용어 오타 2곳(면약·지지재권)을 수정하면
    # 완성도가 높아집니다"처럼 승인하면서 고칠 곳을 짚으면, 그 오타가 결과물에 그대로 남았다. 판정 절에서 수정
    # 요구 표현을 찾아 "조건부 승인"으로 표시한다. 기본은 통과(재작성 시간이 들지 않게) — 단계의 "엄격 검토"를
    # 켜면 보완 요청과 같이 되돌린다(`evaluate_stage_gates`).
    conditional = _CONDITIONAL_FIX_RE.search(head[approve_at:] if approve_at >= 0 else head)
    if conditional:
        start = max(head.rfind(".", 0, conditional.start()), head.rfind("\n", 0, conditional.start()), -1) + 1
        end_candidates = [i for i in (head.find(".", conditional.end()), head.find("\n", conditional.end())) if i >= 0]
        note = head[start: (min(end_candidates) + 1) if end_candidates else len(head)].strip(" -—*\n")
        return {"score": 0.5, "status": "conditional", "not_measured": False, "passed": True,
                "verdict": "조건부 승인", "conditional": True, "note": note[:300]}
    return {"score": 1.0, "status": "pass", "not_measured": False, "passed": True, "verdict": "승인"}


def rework_feedback(conn: sqlite3.Connection, run_id: int, stage) -> str:
    """이 단계로 되돌아온 검토 의견(2026-11-22, 하드코딩·스텁 점검 4순위) — 이 단계를 되돌아갈
    곳으로 지정한 검토 단계가 이 단계의 마지막 시도 뒤에 Gate 미달로 끝났으면, 그 검토 산출물과
    미통과 사유를 다시 쓰기 지시로 돌려준다. 예전에는 검토 단계만 재시도해, 검토자가 결함을
    짚어도 고칠 사람이 없어 결국 사람 검토로 넘어갔다(사업/기획 2차 실측)."""
    last_own = conn.execute(
        "SELECT MAX(id) FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_id = ?",
        (run_id, stage["id"]),
    ).fetchone()[0] or 0
    row = conn.execute(
        "SELECT prs.output, prs.gate_scores, ps.name FROM pipeline_run_stages prs "
        "JOIN pipeline_stages ps ON ps.id = prs.stage_id "
        "WHERE prs.pipeline_run_id = ? AND ps.on_fail_return_stage_id = ? "
        "AND prs.status = 'failed_retrying' AND prs.id > ? ORDER BY prs.id DESC LIMIT 1",
        (run_id, stage["id"], last_own),
    ).fetchone()
    if row is None:
        return ""
    summary = _gate_failure_summary(json.loads(row["gate_scores"] or "{}"))
    review = (row["output"] or "")[:_REWORK_REVIEW_CHARS]
    return (
        f"\n\n[되돌아온 검토 결과 — '{row['name']}' 단계, 미통과: {summary}]\n{review}\n\n"
        "위 검토 의견을 반영해 직전 초안을 고친 완성본 전체를 다시 작성하세요. 지적되지 않은 "
        "부분은 유지하고, 사실·수치는 근거 없이 바꾸지 마세요."
    )


def _gate_failure_summary(gate_scores: dict) -> str:
    failed = [
        f"{gid}({g.get('score')})"
        for gid, g in gate_scores.items()
        if isinstance(g, dict) and not g.get("not_measured") and not g.get("passed")
    ]
    # 엄격 검토로 되돌아온 조건부 승인(2026-11-25, P0-4) — 고칠 곳을 그대로 짚어 준다.
    verdict = gate_scores.get("review_verdict")
    if isinstance(verdict, dict) and verdict.get("conditional") and verdict.get("note"):
        failed.append(f"검토자가 고치라고 한 곳: {verdict['note']}")
    return ", ".join(failed) if failed else "(세부 사유 없음)"


def _stage_initial_input(conn: sqlite3.Connection, task_id: int) -> str:
    """1단계(이전 산출물이 없는 단계)의 입력 — `run_crew_for_team()`과
    같은 패턴: Task의 role_name/required_skills + 최신 사람 작성
    deliverable.

    실측 버그(2026-11-08, park_member 로드러너 게임 데모) — 이 함수가
    지금까지 Task를 낳은 Brief의 `raw_text`(사용자가 실제로 쓴 요청
    원문, 예: "격자 기반 맵에서 플레이어가 금을 모으고... 바닥을
    일시적으로 뚫어 적을 가두는 고전 아케이드 게임")를 **전혀 안
    읽었다** — 1단계(요구사항 분석가)는 Task의 짧은 role_name만 보고
    매번 그 이름을 제멋대로 해석했다("로드러너"를 "Road Runner" 점프
    게임으로 오해하는 식). Brief 본문을 추가로 넘겨 이 공백을 메운다
    — Task 1건이 Brief 1건에 N:1로 묶이므로(`tasks.brief_id`), 다른
    Task와 같은 브리프를 공유해도 전혀 문제없다."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    required_skills = json.loads(task["required_skills"])
    skills_text = ", ".join(required_skills) if required_skills else "없음"
    parts = [f"'{task['role_name']}' 업무를 수행하세요(필요 스킬: {skills_text})."]
    brief = conn.execute(
        "SELECT raw_text FROM briefs WHERE id = ?", (task["brief_id"],)
    ).fetchone()
    if brief is not None and brief["raw_text"].strip():
        parts.append(f"원 업무 요청(Brief) 전문:\n{brief['raw_text']}")
    latest_human = conn.execute(
        "SELECT content FROM deliverables WHERE task_id = ? AND author_type = 'human' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if latest_human is not None:
        parts.append(f"참고할 사람 작성 자료:\n{latest_human['content']}")
    return "\n\n".join(parts)


def with_brief_reference(conn: sqlite3.Connection, task_id: int, prior_output: str) -> str:
    """2단계 이후의 입력 — 이전 단계 산출물 + 원 업무 요청(Brief) 전문(참고용).

    실측(2026-11-21, 실적 보고 파이프라인 실제 실행): Brief는 1단계에만 들어가고 이후
    단계는 직전 산출물만 받았다. 2단계 산출물이 깨지자(도구 호출 문자열뿐) 3·4단계가
    원 요청의 목표 52억·영업이익·지역 비중을 잃고 '확인 필요'로 채웠고, 4단계 요구사항
    추적성 검사가 바로 그 항목들을 누락으로 잡았다. 앞 단계가 무엇을 내든 원 요청을
    다시 확인할 수 있게 모든 단계에 함께 넣는다(두 실행 엔진 공통)."""
    task = conn.execute("SELECT brief_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    brief = (
        conn.execute("SELECT raw_text FROM briefs WHERE id = ?", (task["brief_id"],)).fetchone()
        if task is not None
        else None
    )
    if brief is None or not (brief["raw_text"] or "").strip():
        return prior_output
    return (
        f"{prior_output}\n\n[원 업무 요청(Brief) — 모든 단계 공통 참고, 수치·조건은 여기서 "
        f"확인하세요]\n{brief['raw_text']}"
    )


def _resolve_role_agent_id(conn: sqlite3.Connection, role_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM agent_pool WHERE role_name = ? AND status = 'active'",
        (role_name,),
    ).fetchone()
    if row is None:
        raise ValueError(f"stage_role_not_active_or_missing:{role_name!r}")
    return row["id"]


def repair_stage_language(
    conn: sqlite3.Connection,
    stage_output: str,
    stage_provider: str | None,
    stage_model: str | None,
    actor_id: int,
) -> str:
    """언어 품질 검사 + 자동 교정 1회(2026-11-24, P2-12 실측) — 대화·댓글·단독 실행 경로는
    이미 `language_qa.repair_output`을 거치는데 파이프라인 단계 산출물만 빠져 있어, 재무
    보고서 "작성" 단계에 "1周内" 같은 한자가 그대로 남아 검토 단계에서야 지적됐다. 두 엔진
    (classic·graph)이 Gate 채점 전에 이 함수로 교정해 다음 단계·최종 결과물이 교정본을 받는다.
    교정 모델은 단계가 실제로 쓴 모델(대표 Agent 설정 → 플랫폼 기본)이고, 문제 조각이 없으면
    LLM을 호출하지 않는다."""
    from hair_platform import language_qa
    from hair_platform import llm as _llm

    try:
        provider, model = _llm.resolve_provider_and_model(stage_provider, stage_model)
    except Exception:  # noqa: BLE001 — 교정 모델을 못 정하면 교정 없이 진행
        return stage_output
    return language_qa.repair_output(conn, stage_output, provider, model, actor_id, "pipeline_stage")


def _resolve_stage_llm(
    conn: sqlite3.Connection, stage: sqlite3.Row
) -> tuple[str | None, str | None]:
    """실측(2026-11-06, park_member 로드러너 게임 데모) — `crew_runtime.
    run_crew()`는 호출자가 넘긴 단 하나의 `provider`/`model`로 크루
    전체의 LLM을 통일한다(함수 자체 설계 — Crew 하나에 LLM 하나).
    그런데 이 모듈의 `_execute_stage_attempt()`는 지금까지 `provider`/
    `model`을 전혀 안 넘겼다 — 그래서 매번 "지정 없음" → 자동판별
    (Anthropic 키 없으면 OpenAI) 경로를 탔다. "AI 에이전트 관리" 화면
    에서 각 역할별로 Ollama 모델을 지정해둬도(`agent_pool.llm_provider`/
    `llm_model`) **파이프라인 경로에서는 그 설정이 전혀 적용되지 않고
    있었다** — 설정과 실제 실행이 완전히 어긋나 있던 실제 버그.

    이 단계의 "대표 Agent"(parallel_delegated면 리드, 아니면 첫 번째
    참여 Agent — `run_crew()`가 크루 전체에 LLM 하나만 쓰는 설계이므로
    여러 참여 Agent 중 하나를 대표로 고를 수밖에 없다) 저장된 provider/
    model을 그대로 돌려준다. 그 Agent에 provider/model이 아직 설정 안
    돼 있으면(`NULL`) `(None, None)`을 돌려줘 기존 동작(자동판별)을
    그대로 유지한다 — 이 수정으로 "설정 안 한 기존 Task"가 갑자기
    달라지지 않는다."""
    representative_role = (
        stage["lead_role_name"]
        if stage["mode"] in LEAD_MODES and stage["lead_role_name"]
        else None
    )
    if representative_role is None:
        first_agent = conn.execute(
            "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ? "
            "ORDER BY seq_order, id LIMIT 1",
            (stage["id"],),
        ).fetchone()
        if first_agent is None:
            return None, None
        representative_role = first_agent["role_name"]
    agent_row = conn.execute(
        "SELECT llm_provider, llm_model FROM agent_pool WHERE role_name = ? "
        "AND status = 'active'",
        (representative_role,),
    ).fetchone()
    if agent_row is None:
        return None, None
    return agent_row["llm_provider"], agent_row["llm_model"]


@llm_usage.with_scope(task_id=llm_usage.task_of_pipeline_run, pipeline_run_id="run_id")
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
    except StageAbandoned:
        # 멈춤 감시가 이미 정리한 시도가 뒤늦게 깨어났다 — 실행 상태는 감시가(또는 "이어서 실행"한 새 시도가) 정한다.
        # 여기서 실패로 덮어쓰면 이어서 실행 중인 정상 실행을 망가뜨린다.
        conn.rollback()
        return
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


def record_stage_started_at(conn: sqlite3.Connection, run_id: int, run_stage_id: int | None) -> None:
    """방금 만든 단계 기록에 이 단계의 시작 시각(실행 행의 `stage_started_at`)을 남긴다(2026-11-25, P0-3).
    예상 남은 시간(`estimate_remaining`)이 과거 단계 소요 시간을 계산하는 데 쓴다. 커밋은 호출자."""
    if run_stage_id is None:
        return
    try:
        conn.execute(
            "UPDATE pipeline_run_stages SET started_at = "
            "(SELECT stage_started_at FROM pipeline_runs WHERE id = ?) WHERE id = ?",
            (run_id, run_stage_id),
        )
    except sqlite3.OperationalError:  # 마이그레이션 전 — 추정만 못 할 뿐 실행은 계속
        pass


# 예상 남은 시간(2026-11-25, P0-3) — 같은 템플릿 같은 단계의 최근 기록(최대 5회)의 평균 소요 시간으로 추정한다.
# 사람 승인을 기다린 시간은 단계 시작 전이라 들어가지 않는다. 기록이 없는 단계가 하나라도 있으면 그만큼 모른다고 표시.
_ETA_HISTORY_RUNS = 5


def estimate_remaining(conn: sqlite3.Connection, run) -> dict | None:
    """{"remaining_seconds", "known_stages", "unknown_stages", "basis"} — 실행 중이 아니거나 추정할 기록이 없으면 None."""
    if run["status"] != "running":
        return None
    try:
        stages = conn.execute(
            "SELECT id, seq_order FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order >= ? "
            "ORDER BY seq_order",
            (run["pipeline_template_id"], run["current_stage_seq"] or 1),
        ).fetchall()
        total = 0.0
        known = unknown = 0
        basis = 0
        for st in stages:
            rows = conn.execute(
                "SELECT (julianday(created_at) - julianday(started_at)) * 86400.0 AS sec FROM pipeline_run_stages "
                "WHERE stage_id = ? AND started_at IS NOT NULL AND pipeline_run_id != ? "
                "AND status IN ('passed', 'approved', 'awaiting_approval') ORDER BY id DESC LIMIT ?",
                (st["id"], run["id"], _ETA_HISTORY_RUNS),
            ).fetchall()
            secs = [r["sec"] for r in rows if r["sec"] is not None and r["sec"] >= 0]
            if not secs:
                unknown += 1
                continue
            avg = sum(secs) / len(secs)
            if st["seq_order"] == run["current_stage_seq"] and run["stage_started_at"]:
                elapsed = conn.execute(
                    "SELECT (julianday('now') - julianday(?)) * 86400.0", (run["stage_started_at"],)
                ).fetchone()[0] or 0.0
                avg = max(avg - elapsed, 0.0)
            total += avg
            known += 1
            basis = max(basis, len(secs))
    except sqlite3.OperationalError:
        return None
    if known == 0:
        return None
    return {"remaining_seconds": int(total), "known_stages": known, "unknown_stages": unknown, "basis": basis}


class StageAbandoned(RuntimeError):
    """멈춤 감시가 이미 이 단계 시도를 정리했다 — 뒤늦게 끝난 결과는 쓰지 않는다."""


class StageWatchdog:
    """단계 멈춤 감시(2026-11-26, P1-7 3단계 — 실측: 그래프 엔진 + 직접 실행에서 도구 호출 직후 프로세스가 잠금 대기로
    21분간 멈췄고, 다시 돌리면 재현되지 않았다(간헐). 원인을 찾을 때까지 운영이 무한정 묶이지 않게 한다).

    단계가 도는 동안 진행 문구·작성 중 미리보기(`progress.touch`)가 `stall_seconds` 동안 한 번도 없으면 멈춘 것으로 본다:
    모든 스레드의 파이썬 스택을 활동 기록에 남기고(원인 추적용), 실행을 "재시작으로 중단됨"과 같은 상태로 표시해
    "이어서 실행"으로 다시 돌릴 수 있게 한다. 멈춘 스레드는 강제로 끝낼 수 없으므로, 나중에 깨어나도 결과를 쓰지 못하게
    이 시도를 `abandoned`로 표시한다(`stage_progress`가 블록을 나올 때 `StageAbandoned`). 이어서 실행한 새 시도는
    자기 감시 객체를 따로 가지므로 영향이 없다."""

    def __init__(self, conn: sqlite3.Connection, run_id: int, stage, stall_seconds: float, poll_seconds: float):
        import threading

        self.conn, self.run_id, self.stage = conn, run_id, stage
        self.stall_seconds, self.poll_seconds = stall_seconds, poll_seconds
        self.abandoned = False
        self._stop = threading.Event()
        self._started = None
        self._thread = threading.Thread(target=self._watch, name=f"hair-stage-watchdog-{run_id}", daemon=True)

    def start(self) -> None:
        import time

        self._started = time.monotonic()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _idle_seconds(self) -> float:
        import time

        from hair_platform import progress

        last = progress.last_activity("pipeline_runs", self.run_id) or 0.0
        return time.monotonic() - max(last, self._started or 0.0)

    def _watch(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            idle = self._idle_seconds()
            if idle >= self.stall_seconds:
                self._abandon(idle)
                return

    def _abandon(self, idle: float) -> None:
        import faulthandler
        import tempfile

        self.abandoned = True
        stacks = ""
        try:
            with tempfile.TemporaryFile("w+") as f:
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.seek(0)
                stacks = f.read()[-30000:]
        except Exception:  # noqa: BLE001 — 스택을 못 남겨도 정리는 한다
            stacks = "(스택을 남기지 못했습니다)"
        minutes = int(idle // 60)
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE pipeline_runs SET status = 'failed', finished_at = datetime('now'), "
                    "interrupted_at = datetime('now'), progress = ? WHERE id = ? AND status = 'running'",
                    (f"멈춤 감지 — {minutes}분 동안 진척이 없어 중단했습니다(이어서 실행 가능)", self.run_id),
                )
            actor = self.conn.execute("SELECT actor_id FROM pipeline_runs WHERE id = ?", (self.run_id,)).fetchone()
            if actor is not None:
                audit.record(self.conn, actor[0], "pipeline_stage_stalled", "pipeline_runs", self.run_id,
                             after={"stage": self.stage["name"], "idle_seconds": int(idle), "stacks": stacks})
                self.conn.commit()
        except sqlite3.Error:
            pass


def _stall_seconds() -> float:
    import os

    try:
        return float(os.environ.get("HAIR_STAGE_STALL_SECONDS", "2700"))
    except ValueError:
        return 2700.0


@contextlib.contextmanager
def stage_progress(conn: sqlite3.Connection, run_id: int, stage) -> Iterator[None]:
    """단계 실행 중 진행 표시(2026-11-24, P0-2 "파이프라인 진행률") — 두 엔진이 단계를 돌리는 동안 감싼다.
    단계 시작 시각과 첫 문구를 남기고, 그 안의 `progress.report()`(CrewAI 작업·도구 이벤트, 단일 Agent
    호출의 조회·생성·교정 단계)와 작성 중 미리보기가 이 실행 행에 기록된다. 멈춤 감시(`StageWatchdog`,
    `HAIR_STAGE_STALL_SECONDS`, 기본 45분 — 0이면 끔)도 같이 돈다."""
    import os

    from hair_platform import progress

    try:
        conn.execute(
            "UPDATE pipeline_runs SET stage_started_at = datetime('now'), progress = ?, "
            "progress_at = datetime('now') WHERE id = ?",
            (f"'{stage['name']}' 시작", run_id),
        )
        conn.commit()
    except sqlite3.OperationalError:  # 컬럼이 생기기 전(마이그레이션 도중) — 표시 없이 진행
        pass
    stall = _stall_seconds()
    watchdog = None
    if stall > 0:
        poll = float(os.environ.get("HAIR_STAGE_WATCHDOG_POLL_SECONDS", "30") or 30)
        watchdog = StageWatchdog(conn, run_id, stage, stall, poll)
        watchdog.start()
    try:
        with progress.reporting(conn, "pipeline_runs", run_id):
            yield
    finally:
        if watchdog is not None:
            watchdog.stop()
    if watchdog is not None and watchdog.abandoned:
        raise StageAbandoned(f"pipeline_run {run_id}: 멈춤 감시가 정리한 단계 시도 — 결과를 쓰지 않습니다")


def assemble_stage_output(stage, result: dict, fallback_agent_id: int) -> tuple[str, int]:
    """단계 산출물과 그 작성 Agent — 두 실행 엔진 공통(2026-11-25 기술 부채 정리: classic `_execute_stage_attempt`와
    graph `execution_graph._node_run_stage`에 같은 코드가 따로 있었다)."""
    # 실측 발견(2026-11-07, "구현" 단계를 sequential_relay로 전환하며
    # 드러남) — 기존엔 `sequential_relay`든 뭐든 항상 **마지막 참여자의
    # 답변만** stage_output으로 캡처했다(`result["agents"][-1]`). "설계"
    # (AA→DA)/"QA 기능테스트"(테스트계획→QA테스터)도 이미 이 버그를
    # 안고 있었다 — 앞 참여자의 실제 산출물이 조용히 버려지고 있었는데,
    # 그 단계들이 `is_primary_deliverable`이 아니라 아무도 눈치 못
    # 챘을 뿐이다. "구현"이 sequential_relay가 되면서(5명이 각자 다른
    # 모듈을 작성) 즉시 치명적으로 드러난다 — 4명분 코드가 사라지는
    # 셈이라, 로드맵 3번(위임 결과 합성)과 같은 원칙으로 **참여자 전원의
    # 최종 산출물을 순서대로 이어붙인다**. self_review로 draft/final이
    # 섞여 있으면(`"stage"` 키) final만 남긴다 — draft는 중간 산물이라
    # 최종 결과물에 포함하지 않는다(기존 adhoc/team crew 경로와 동일
    # 원칙). 참여자가 1명뿐이면(single, 또는 hierarchical의 매니저 1건)
    # 이어붙일 게 없으니 그 1건 그대로 — 기존 동작과 100% 동일하다.
    final_stage_entries = [
        a for a in result["agents"] if a.get("stage", "final") == "final"
    ] or result["agents"]
    if stage["mode"] == "sequential_relay" and len(final_stage_entries) > 1:
        stage_output = "\n\n---\n\n".join(
            f"### {a['role_name']}\n\n{a['output']}" for a in final_stage_entries
        )
    elif final_stage_entries:
        stage_output = final_stage_entries[-1]["output"]
    else:
        stage_output = result["final_output"]
    author_agent_id = (
        final_stage_entries[-1]["agent_id"]
        if final_stage_entries
        else fallback_agent_id
    )
    return stage_output, author_agent_id


def score_stage_output(
    conn: sqlite3.Connection, run_id: int, stage, result: dict, stage_output: str, evidence: str
) -> tuple[dict, bool]:
    """단계 산출물 채점과 통과 판정 — 두 실행 엔진 공통(2026-11-25 기술 부채 정리). 예전엔 두 엔진에 같은 코드가 따로
    있어 새 검사(검토 판정 등)를 양쪽에 손으로 넣어야 했다. `evidence`는 근거(업무 요청·앞 단계 산출물·이번 단계
    도구 결과). 반환: (gate_scores, passed)."""
    # 사용자 요청(2026-11-05, 로드맵 4번 "Gate 구문검사 + 요구사항
    # 추적성 자동 채점") — Gate A-G는 순수 휴리스틱이라 "구현" 단계
    # 산출물이 실제로 파싱 가능한 코드인지 기계적으로 확인하지 않았다.
    # `_gate_pass_ratio()`(아래)는 제네릭하게 모든 키를 순회하므로,
    # 실제 차단 임계값(min_gate_pass_ratio=0.5)이 걸린 "코드 검증"/
    # "QA 기능테스트"/"품질 관리 최종 판정" 단계에까지 이 점수를
    # 무심코 끼워 넣으면(그 단계들의 리포트성 산출물이 코드를 인용하는
    # 경우) 기존 통과/차단 동작이 조용히 바뀔 위험이 있다 — 그래서
    # "이 단계의 산출물이 Task의 진짜 최종 결과물인 단계"에만 한정해
    # 실제 점수로 참여시킨다. 다른 단계는 아예 이 키를 추가하지 않는다.
    #
    # 사용자 요청(2026-11-08, "게임이 아닌 일상 사무 업무도 일반화") —
    # 원래는 `stage["name"] == "구현"` 문자열 매칭이었다. 다른 도메인
    # 파이프라인(사업기획서 등)의 "작성" 단계가 이름을 다르게 지어도
    # 똑같이 검사받도록, 이미 있던 `is_primary_deliverable` 플래그로
    # 바꾼다 — "이 단계 산출물이 Task의 진짜 최종 결과물"이라는 뜻이
    # 정확히 "실제로 뭔가를 만들어내는 단계"와 일치해 의미상으로도
    # 더 맞다. 펜스 코드 블록이 없는 산출물(기획서 프로즈 등)은
    # 기존처럼 `not_measured`로 떨어질 뿐이라 안전하다.
    gate_scores = dict(result["gate_scores"])
    if stage["is_primary_deliverable"]:
        gate_scores["code_syntax"] = agent_evaluation.compute_code_syntax_check(
            stage_output
        )
    # 수치 근거·테스트 실제 실행(2026-11-23, P0-1·P0-3 — `agent_evaluation.attach_quality_checks`).
    # 근거는 이 단계 입력(앞 단계 산출물 + 원 업무 요청). 단계 gate_ids에 넣지 않으면
    # `_gate_pass_ratio()`가 세지 않으므로 통과 판정은 바꾸지 않는다(정보 제공·배지 표시).
    # 근거 = 브리프·앞 단계 산출물 + 이번 단계의 도구 결과(2026-11-24 — 계산기 결과 수치 오탐 방지).
    agent_evaluation.attach_quality_checks(
        gate_scores, stage_output, evidence
    )
    # 실행 가능성 Gate(2026-11-22 C안) — 단계 gate_ids에 있을 때만 잰다(SW 개발 "구현").
    if "deliverable_runnable" in json.loads(stage["gate_ids"] or "[]"):
        gate_scores["deliverable_runnable"] = agent_evaluation.compute_deliverable_runnable(
            stage_output
        )

    # 실측(2026-11-06, park_member 로드러너 게임 데모) — "구현" 단계
    # 매니저가 'Delegate work to coworker'를 한 번도 호출하지 않고
    # 자기 전문 분야(모듈 분해)대로 계획 문서만 다시 쓴 채 그대로
    # 통과한 사례가 재현됨(crew_runtime.py의 hierarchical_description
    # 안내문이 이미 위임을 강하게 유도하지만, LLM이 안 따를 수 있다는
    # 건 2026-10-18에 이미 받아들인 리스크 — crew_runtime.py 주석
    # 참고). 그 결과 빈 계획 문서가 "코드 검증" 단계로 넘어가서야
    # `requirements_traceability`에 뒤늦게 걸려 재시도를 낭비했다.
    # code_syntax와 달리 이건 "내용 품질"이 아니라 "이 단계의 핵심
    # 메커니즘(위임) 자체가 작동했는지"를 보는 구조적 확인이라 —
    # min_gate_pass_ratio=0.0(절대 차단 안 함, 위 code_syntax 주석과
    # 같은 이유)을 그대로 두는 대신 `passed`를 직접 덮어써 "구현"
    # 단계 안에서 바로 재시도시킨다.
    if stage["mode"] == "parallel_delegated":
        delegated_real_work = any(
            d.get("tool") == "Delegate work to coworker"
            for d in result.get("delegations", [])
        )
        gate_scores["delegation_check"] = {
            "score": 1.0 if delegated_real_work else 0.0,
            "status": "pass" if delegated_real_work else "fail",
            "passed": delegated_real_work,
            "not_measured": False,
        }

    # 이 단계는 바로 위 `prior` 조회처럼 이전 단계 출력만 보고 1단계
    # (요구분석/계획 확정)의 요구사항 문서를 자동으로 보지 못한다 —
    # 별도로 직접 조회해 LLM judge로 요구사항 추적성을 채점한다.
    # 실제 차단 임계값이 걸려 있을 수 있는 단계라, 아직 임계값을
    # 신뢰할 데이터가 없는 이 새 점수가 그 판정을 조용히 바꾸지 않도록
    # `not_measured`를 강제한다 — 정보 제공 전용(v1), 실제 점수/세부
    # 내역은 그대로 저장돼 화면에는 보인다.
    #
    # 사용자 요청(2026-11-08, "게임이 아닌 일상 사무 업무도 일반화") —
    # 원래는 `stage["name"] == "코드 검증"` 문자열 매칭이었다. 다른
    # 도메인 파이프라인의 검증 단계가 이름을 다르게 지어도 똑같이
    # 요구사항 추적성을 검사받도록, 단계명과 무관한 명시적 플래그
    # (`checks_requirements_traceability`)로 바꾼다.
    stage_gate_ids = json.loads(stage["gate_ids"]) if stage["gate_ids"] else None
    traceability = requirements_traceability_score(conn, run_id, stage, stage_output)
    if traceability is not None:
        gate_scores["requirements_traceability"] = traceability

    # 검토 판정 반영(2026-11-25 실측 — 계약서·영업 파이프라인): 검토 단계가 '## 판정'에 "보완 요청"을 써도
    # 통과/미통과는 Gate 점수로만 정해져, 고칠 곳을 짚은 검토가 그대로 최종 결과가 됐다. 되돌아갈 단계가
    # 지정된 검토 단계에서는 판정을 읽어 "보완 요청"이면 미통과로 보고 작성 단계로 돌려보낸다(재시도 상한을
    # 넘으면 지금처럼 사람 검토). 판정을 찾지 못하면 측정 안 함 — 막지 않는다.
    verdict = None
    if stage["on_fail_return_stage_id"]:
        verdict = review_verdict(stage_output)
        gate_scores["review_verdict"] = verdict

    ratio = _gate_pass_ratio(gate_scores, stage_gate_ids)
    passed = ratio >= stage["min_gate_pass_ratio"]
    if stage["mode"] == "parallel_delegated" and not gate_scores["delegation_check"]["passed"]:
        passed = False
    if verdict is not None and not verdict["not_measured"] and not verdict["passed"]:
        passed = False
    # 엄격 검토(2026-11-25, P0-4): 조건부 승인도 되돌려 지적된 곳을 고치게 한다(재시도 상한 안에서).
    if verdict is not None and verdict.get("conditional") and _stage_flag(stage, "strict_review"):
        verdict["passed"] = False
        verdict["status"] = "fail"
        verdict["strict"] = True
        passed = False
    return gate_scores, passed

def _execute_stage_attempt(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
    stage: sqlite3.Row,
    actor_id: int,
    on_stage_resolved: Callable[[], None] | None = None,
) -> None:
    run_id = run["id"]
    stage_id = stage["id"]

    # 사용자 요청(2026-11-17, "Human Agency Scale... 코파일럿... 실행
    # 중단 요청 정도가 최소한의 첫 단계" → "구현 시작") — 비용이 드는
    # Crew 호출을 시작하기 직전, 가장 안전한 지점에서만 멈춘다. 이미
    # 진행 중인 Crew 호출 자체를 끊지는 않는다(그건 더 큰 작업).
    if run["cancel_requested"]:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'cancelled', finished_at = datetime('now') "
            "WHERE id = ?",
            (run_id,),
        )
        audit.record(
            conn, actor_id, "pipeline_run_cancelled", "pipeline_runs", run_id
        )
        conn.commit()
        if on_stage_resolved is not None:
            on_stage_resolved()
        return

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
        with_brief_reference(conn, run["task_id"], prior["output"])
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
            last_gate_scores = json.loads(last_attempt["gate_scores"])
            summary = _gate_failure_summary(last_gate_scores)
            retry_feedback = (
                f"\n\n[이전 시도({attempt - 1}차) Gate 결과] 다음 기준을 통과하지 "
                f"못했습니다: {summary}. 이를 구체적으로 보완해 다시 작성하세요."
            )
            # 실측(2026-11-06, park_member 로드러너 게임 데모) — "구현"
            # 단계 매니저가 위임을 한 번도 안 하고 모듈 분해 문서만
            # 다시 써서, 아래 delegation_check 가드가 걸려도 똑같은
            # 실수를 반복했다. 일반 Gate 피드백만으로는 "무엇을 다르게
            # 해야 하는지"가 분명하지 않아 — 이 경우에만 더 구체적으로
            # 지시한다.
            delegation_check = last_gate_scores.get("delegation_check")
            if delegation_check is not None and not delegation_check.get("passed"):
                retry_feedback += (
                    "\n\n[중요] 이전 시도에서 'Delegate work to coworker' 도구를 "
                    "한 번도 호출하지 않고 직접 계획 문서만 작성했습니다. 이번에는 "
                    "절대 직접 코드를 작성하지 말고, 반드시 각 모듈을 코워커에게 "
                    "'Delegate work to coworker' 도구로 위임해 실제 코드를 받아오세요."
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
    retry_feedback += rework_feedback(conn, run_id, stage)
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

    # 계획 분담 단계에서만 채운다(아래 실행부도 같은 조건에서만 쓴다 — 타입 검사기용 초기값).
    plan_lead: dict | None = None
    plan_workers: list[dict] = []
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
    elif stage["mode"] == "plan_merge":
        # 계획 분담(2026-11-23, P2-9) — 실행은 아래에서 `plan_merge.run()`이 한다.
        lead_agent_id = _resolve_role_agent_id(conn, stage["lead_role_name"])
        plan_lead = {"agent_id": lead_agent_id, "role_name": stage["lead_role_name"], "instructions": ""}
        plan_workers = [
            {"agent_id": _resolve_role_agent_id(conn, a["role_name"]), "role_name": a["role_name"],
             "instructions": a["instructions"]}
            for a in stage_agents if a["role_name"] != stage["lead_role_name"]
        ]
        run_agent_ids = [lead_agent_id] + [w["agent_id"] for w in plan_workers]
        task_descriptions = [combined_base] * len(run_agent_ids)
        process = "sequential"
        manager_agent_id = None
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
    stage_provider, stage_model = _resolve_stage_llm(conn, stage)

    # 진행 표시(2026-11-24, P0-2 파이프라인 진행률) — 단계 시작 시각·지금 하는 일·작성 중 미리보기를
    # 실행 행에 남긴다(`stage_progress`).
    with stage_progress(conn, run_id, stage):
        if stage["mode"] == "plan_merge" and plan_lead is not None:
            from hair_platform import plan_merge

            result = plan_merge.run(
                conn, plan_lead, plan_workers, combined_base, actor_id,
                expected_output=stage["expected_output"], provider=stage_provider, model=stage_model,
                eval_task_type=agent_evaluation.eval_task_type_for(stage["output_content_type"]),
            )
        else:
            # 두 엔진 공통 단계 실행기(2026-11-25, P1-7 — `stage_runner.py`): 기본은 CrewAI(지금까지와 같음, CrewAI
            # 메모리는 끔 — 2026-11-06 실측: 숨은 OpenAI 임베딩 의존), HAIR_STAGE_RUNTIME=direct면 대화와 같은 직접 호출.
            from hair_platform import stage_runner

            result = stage_runner.run_stage_agents(
                conn,
                stage,
                run_agent_ids,
                task_descriptions,
                actor_id,
                process=process,
                eval_task_type=agent_evaluation.eval_task_type_for(stage["output_content_type"]),
                provider=stage_provider,
                model=stage_model,
                manager_agent_id=manager_agent_id,
                expected_outputs=expected_outputs,
                self_review=use_self_review_tier,
            )

    tcr = result["tcr"]

    stage_output, author_agent_id = assemble_stage_output(stage, result, run_agent_ids[-1])
    stage_output = repair_stage_language(conn, stage_output, stage_provider, stage_model, actor_id)
    # 채점·통과 판정은 두 엔진 공통 함수(2026-11-25) — 근거 = 브리프·앞 단계 산출물 + 이번 단계의 도구 결과.
    gate_scores, passed = score_stage_output(
        conn, run_id, stage, result, stage_output,
        "\n\n".join(filter(None, [combined_base, result.get("tool_evidence")])),
    )

    # 사용자 요청(2026-11-17, "Human Agency Scale... 사람 고유 능력을
    # 보호하는 Task는 Gate 점수와 무관하게 항상 사람에게 올라가야
    # 한다" → "구현 시작") — 지금까지 사람 개입 여부는 전부 성과
    # 기반(Gate 통과율)이었고, 업무 '종류'(협상·민감한 커뮤니케이션
    # 등) 자체로 항상 사람이 봐야 하는 Task를 지정할 방법이 없었다.
    # `stage["requires_human_approval"]`(파이프라인 템플릿이 고정하는
    # 단계별 설정)과 OR로 묶어, Task 하나에만 이 플래그를 켜면 그
    # Task가 어떤 파이프라인을 타든 모든 단계에서 Gate 통과 자체를
    # 승인으로 치지 않게 만든다 — 템플릿을 따로 만들 필요 없음.
    task_row = conn.execute(
        "SELECT max_retry_budget, requires_human_judgment FROM tasks WHERE id = ?",
        (run["task_id"],),
    ).fetchone()
    stage_requires_human = bool(
        stage["requires_human_approval"] or task_row["requires_human_judgment"]
    )

    cur = conn.execute(
        "INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, "
        "output, gate_scores, tcr) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            stage_id,
            attempt,
            "passed" if passed else "failed_retrying",
            stage_output,
            json.dumps(evaluation_reports.public(gate_scores), ensure_ascii=False),  # 내부 항목(리포트 토큰) 제외
            tcr,
        ),
    )
    run_stage_id = cur.lastrowid
    record_stage_started_at(conn, run_id, run_stage_id)
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
        # 그대로 재사용(코드 중복 없음). `task_row`는 바로 위에서
        # (이 함수 앞부분에서) 이미 한 번 조회해뒀다 — 재조회 없이
        # 재사용한다.
        budget_exceeded = False
        if task_row["max_retry_budget"] is not None:
            retries_so_far = dashboard.task_retry_summary(conn, run["task_id"])[
                "total_retries"
            ]
            budget_exceeded = retries_so_far >= task_row["max_retry_budget"]

        if attempt < stage["max_retry"] and not budget_exceeded:
            target = return_target_stage(conn, stage)
            if target is not None:
                # 검토 실패 → 지정한 앞 단계(작성)로 돌아가 검토 의견을 반영해 다시 쓰게 한다. 그
                # 단계가 통과하면 평소처럼 이 단계까지 다시 진행되고, 이 단계의 시도 횟수는 계속
                # 늘어나 `max_retry`를 넘으면 사람 검토로 넘어간다(무한 반복 없음).
                conn.execute(
                    "UPDATE pipeline_runs SET current_stage_seq = ? WHERE id = ?",
                    (target["seq_order"], run_id),
                )
                audit.record(
                    conn, actor_id, "pipeline_stage_rework_requested", "pipeline_run_stage",
                    run_stage_id, after={"return_to_stage_id": target["id"], "attempt": attempt},
                )
                conn.commit()
                if on_stage_resolved is not None:
                    on_stage_resolved()
                run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
                _execute_stage_attempt(conn, run, target, actor_id, on_stage_resolved)
                return
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
                "gate_scores": evaluation_reports.public(gate_scores),
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
    # 핵심 — 결재함을 거치지 않고 즉시 다음 단계로 전진한다). 단,
    # Task 자체에 `requires_human_judgment`가 켜져 있으면 이 단계의
    # 템플릿 설정과 무관하게 항상 사람에게 올린다(`stage_requires_human`).
    deliverable_status = "pending_review" if stage_requires_human else "approved"
    cur = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, "
        "content_type, status) VALUES (?, 'agent', ?, ?, ?, ?)",
        (
            run["task_id"],
            author_agent_id,
            # HAIR 덱(2026-11-20) — 발표자료 단계 산출물이면 디자인 시스템을
            # 넣은 HTML로 완성하고 content_type을 html로 바로잡는다.
            *presentation_deck.finalize_with_type(stage_output, stage["output_content_type"]),
            deliverable_status,
        ),
    )
    deliverable_id = cur.lastrowid
    assert deliverable_id is not None
    agent_evaluation.record_evaluation(conn, deliverable_id, gate_scores, tcr)
    conn.execute(
        "UPDATE pipeline_run_stages SET deliverable_id = ?, finished_at = datetime('now') "
        "WHERE id = ?",
        (deliverable_id, run_stage_id),
    )

    if stage_requires_human:
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
    # 결정 대상 확인(2026-11-22, "모든 파이프라인 점검" 실측) — 지금까지는 아무 단계 행이나 받아,
    # 5단계가 사람 검토로 넘어간(escalated) 실행에서 이미 끝난 4단계 행을 승인하자 5단계를 건너뛰고
    # 6단계로 진행됐다. 실행이 승인 대기 중이고, 그 행이 지금 단계의 결정 대기 행일 때만 받는다.
    if (
        run["status"] != "paused_for_approval"
        or run_stage["status"] not in ("passed", "escalated")
        or stage["seq_order"] != run["current_stage_seq"]
    ):
        raise ValueError("pipeline_run_stage_not_awaiting_decision")

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
        # 반려 = "다시 해와"(기존 승인루프 스킬 안내와 일치) —
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


def _run_progress_fields(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    stages = conn.execute(
        "SELECT seq_order, name FROM pipeline_stages WHERE pipeline_template_id = ? ORDER BY seq_order",
        (run["pipeline_template_id"],),
    ).fetchall()
    current = next((s for s in stages if s["seq_order"] == run["current_stage_seq"]), None)
    keys = run.keys()
    return {
        "total_stages": len(stages),
        "current_stage_index": (stages.index(current) + 1) if current else None,
        "current_stage_name": current["name"] if current else None,
        "progress": run["progress"] if "progress" in keys else None,
        "progress_at": run["progress_at"] if "progress_at" in keys else None,
        "stage_started_at": run["stage_started_at"] if "stage_started_at" in keys else None,
        "partial_output": run["partial_output"] if "partial_output" in keys else None,
        "eta": estimate_remaining(conn, run) if "stage_started_at" in keys else None,
    }


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
                "cancel_requested": bool(run["cancel_requested"]),
                # 사용자 요청(2026-11-18, "다음 개선 진행해줘") — 이 run이
                # 기존 엔진인지 신규 LangGraph 엔진(`execution_graph.py`)
                # 인지 화면에서 구분할 수 있게 노출한다(`run["engine"]`,
                # 기본 `'legacy'`).
                "engine": run["engine"],
                "created_at": run["created_at"],
                "finished_at": run["finished_at"],
                # 서버 재시작으로 중단됨(2026-11-25, P1-6) — 화면이 "이어서 실행" 버튼을 보여 준다.
                "interrupted_at": run["interrupted_at"] if "interrupted_at" in run.keys() else None,
                # 진행률(2026-11-24, P0-2) — 실행 중인 단계의 이름·지금 하는 일·시작 시각·작성 중 미리보기.
                **_run_progress_fields(conn, run),
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
