"""Crew 실행 이력 — 사용자 요청(2026-10-20, "crewai의 잠재력을 모두
끌어내고... Crew 빌더" 로드맵 3단계, "실행 이력 + 진행 가시성").

지금까지 파이프라인 실행(`pipeline_runtime.py`의 `pipeline_runs`)만
DB에 영구 기록이 남았다. 팀 Crew 실행(`deliverables.run_team_crew_job()`)
·즉석 Crew 실행(`deliverables.run_adhoc_crew_job()`)은 실행이 끝나는
순간 협업 다이어그램·토큰 사용량·Gate 점수가 전부 흩어져 사라졌다 —
`deliverables` 테이블엔 최종 텍스트만 남고, "이 Crew가 언제 누구랑 무슨
설정으로 돌았는지"는 그 순간 이후 영원히 조회할 수 없었다(admin 전용
즉석 테스트 샌드박스, `/api/crew/run`만 실행 직후 한 번 보여주고 역시
저장하지 않는다).

이 모듈이 `crew_runs` 테이블(`db.py` 참고)로 그 둘을 영구 기록한다.
`crew_runtime.run_crew()`의 기존 `task_callback` 훅(`_capture_task_
completion`)에 `on_task_complete` 콜백 하나만 추가해, Task가 완료될
때마다 `append_progress()`로 즉시 commit한다 — Crew 실행 자체는
`crew.kickoff()`가 끝날 때까지 동기로 블로킹되지만(CrewAI 자체 제약,
새 비동기 인프라 없이는 못 바꾼다), 그 안에서 Task 하나씩 끝날
때마다는 콜백이 불려 DB에 즉시 반영되므로, 같은 트랜잭션 밖에서 폴링
(`GET /api/crew-runs/{id}`)하면 "N명 중 M명 완료"를 실행 중에도 볼 수
있다 — 새 WebSocket/SSE 인프라 없이 기존 폴링 패턴만으로 달성한다."""

from __future__ import annotations

import json
import sqlite3


def create_run(
    conn: sqlite3.Connection,
    task_id: int,
    kind: str,
    requested_by: int,
    agent_ids: list[int],
    process: str,
    manager_agent_id: int | None = None,
    team_id: int | None = None,
    task_descriptions: list[str] | None = None,
) -> int:
    """`task_descriptions`(사용자 요청, 2026-10-20, "실패 시 일부
    재시도") — Agent별 원본 지시문을 그대로 보존해둔다. 실행이 중간에
    실패했을 때 `start_crew_run_retry()`가 이미 끝난 Agent를 제외한
    나머지만으로 새 adhoc 실행을 다시 구성하려면, 원래 각 Agent에게
    어떤 task description을 줬는지가 필요하기 때문이다(팀 실행은 전원
    같은 문구를 쓰지만 즉석 실행은 Agent별로 다를 수 있다 — 어느 쪽이든
    그대로 저장)."""
    if kind not in ("team", "adhoc"):
        raise ValueError(f"unsupported_kind:{kind}")
    cur = conn.execute(
        "INSERT INTO crew_runs (task_id, kind, team_id, requested_by, agent_ids, "
        "process, manager_agent_id, task_descriptions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            kind,
            team_id,
            requested_by,
            json.dumps(agent_ids),
            process,
            manager_agent_id,
            json.dumps(task_descriptions, ensure_ascii=False)
            if task_descriptions is not None
            else None,
        ),
    )
    run_id = cur.lastrowid
    assert run_id is not None
    conn.commit()
    return run_id


def append_progress(
    conn: sqlite3.Connection, run_id: int, agent_role: str, output_preview: str
) -> None:
    """`crew_runtime.run_crew()`의 `on_task_complete` 콜백에서 Task
    완료마다 호출된다 — Crew 전체(`crew.kickoff()`)가 아직 끝나지
    않았어도 즉시 commit해, 그 사이에 폴링하는 `GET /api/crew-runs/{id}`
    가 바로 볼 수 있게 한다. 존재하지 않는 run_id는 조용히 무시한다
    (백그라운드 콜백 안에서 예외로 전체 Crew 실행을 깨뜨리면 안 된다)."""
    row = conn.execute(
        "SELECT progress FROM crew_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return
    progress = json.loads(row["progress"])
    progress.append({"agent_role": agent_role, "output_preview": output_preview})
    conn.execute(
        "UPDATE crew_runs SET progress = ? WHERE id = ?",
        (json.dumps(progress, ensure_ascii=False), run_id),
    )
    conn.commit()


def complete_run(conn: sqlite3.Connection, run_id: int, result: dict) -> None:
    """`crew_runtime.run_crew()`(또는 `run_crew_for_team()`)가 정상
    반환한 `result`를 그대로 영구 기록한다 — admin 즉석 테스트
    샌드박스(`renderCrewResult()`)가 지금까지 실행 직후에만 보여주던
    협업 다이어그램·토큰 사용량·Gate 점수를, 팀/즉석 실행도 나중에
    "실행 이력"에서 똑같이 볼 수 있게 한다."""
    conn.execute(
        "UPDATE crew_runs SET status = 'completed', gate_scores = ?, tcr = ?, "
        "token_usage = ?, delegations = ?, agent_outputs = ?, final_output = ?, "
        "finished_at = datetime('now') WHERE id = ?",
        (
            json.dumps(result.get("gate_scores"), ensure_ascii=False),
            result.get("tcr"),
            json.dumps(result.get("token_usage"), ensure_ascii=False),
            json.dumps(result.get("delegations"), ensure_ascii=False),
            json.dumps(result.get("agents"), ensure_ascii=False),
            result.get("final_output"),
            run_id,
        ),
    )
    conn.commit()


def fail_run(
    conn: sqlite3.Connection,
    run_id: int,
    error: str,
    completed_agent_ids: list[int] | None = None,
) -> None:
    """`completed_agent_ids`(사용자 요청, 2026-10-20, "실패 시 일부
    재시도") — 실패 시점까지 실제로 Task를 끝내 deliverable이 만들어진
    Agent id 목록. `start_crew_run_retry()`가 `agent_ids`에서 이 목록을
    뺀 나머지만 재시도 대상으로 삼는다. 생략하면(기본값) 빈 배열 그대로
    — "아무도 안 끝남"과 같은 뜻이라 전원이 재시도 대상이 된다."""
    conn.execute(
        "UPDATE crew_runs SET status = 'failed', error = ?, completed_agent_ids = ?, "
        "finished_at = datetime('now') WHERE id = ?",
        (error, json.dumps(completed_agent_ids or []), run_id),
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "kind": row["kind"],
        "team_id": row["team_id"],
        "requested_by": row["requested_by"],
        "agent_ids": json.loads(row["agent_ids"]),
        "process": row["process"],
        "manager_agent_id": row["manager_agent_id"],
        "status": row["status"],
        "task_descriptions": (
            json.loads(row["task_descriptions"]) if row["task_descriptions"] else None
        ),
        "completed_agent_ids": json.loads(row["completed_agent_ids"]),
        "progress": json.loads(row["progress"]),
        "gate_scores": json.loads(row["gate_scores"]) if row["gate_scores"] else None,
        "tcr": row["tcr"],
        "token_usage": json.loads(row["token_usage"]) if row["token_usage"] else None,
        "delegations": json.loads(row["delegations"]) if row["delegations"] else None,
        "agents": json.loads(row["agent_outputs"]) if row["agent_outputs"] else None,
        "final_output": row["final_output"],
        "error": row["error"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM crew_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_runs_for_task(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM crew_runs WHERE task_id = ? ORDER BY id DESC", (task_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_recent_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """"Crew 빌더"의 전체 "실행 이력" 탭(admin/manager 전용, Task를
    가로지르는 조회)이 쓴다."""
    rows = conn.execute(
        "SELECT * FROM crew_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
