"""F4 — Job Queue(신규 Agent 생성). NF6: 상태머신 pending/running/succeeded/failed/partial.

실제 AgentForge 파이프라인(Spec Interpreter·Code Generator·Verifier)은 아직
vendor/import되지 않았다(SPEC.md §12 오픈 이슈) — `default_stub_generator`가 그
자리를 채우는 결정론적 스텁이다(F1과 같은 정신). 실제 연동 시 `generator` 인자만
교체하면 된다.

하나의 `job`은 브리프 단위 생성 배치이고, `job_items`는 Task(=역할) 단위 결과다.
전부 성공하면 job.status=succeeded, 전부 실패하면 failed, 섞이면 partial —
"실패 역할만 재시도"(NF6)는 실패한 job_item만 골라 재실행한다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from hair_platform import audit
from hair_platform.sandbox import run_smoke_test_in_sandbox

GeneratorFn = Callable[[str, list[str]], dict]


def default_stub_generator(role_name: str, required_skills: list[str]) -> dict:
    """AgentForge 실제 파이프라인 연동 전까지의 규칙기반 스텁."""
    return {
        "role_name": role_name,
        "skill_tags": required_skills,
        "agents_yaml_snippet": f"role: {role_name}, skills: {required_skills}",
    }


def _recompute_job_status(conn: sqlite3.Connection, job_id: int) -> str:
    items = conn.execute(
        "SELECT status FROM job_items WHERE job_id = ?", (job_id,)
    ).fetchall()
    statuses = {row["status"] for row in items}
    if statuses == {"succeeded"}:
        status = "succeeded"
    elif statuses == {"failed"}:
        status = "failed"
    elif statuses <= {"succeeded", "failed"}:
        status = "partial"
    else:
        status = "running"
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    return status


def _run_item(
    conn: sqlite3.Connection,
    item_id: int,
    task_row: sqlite3.Row,
    created_by: int,
    generator: GeneratorFn,
) -> None:
    conn.execute("UPDATE job_items SET status = 'running' WHERE id = ?", (item_id,))
    required_skills = json.loads(task_row["required_skills"])
    try:
        generated = generator(task_row["role_name"], required_skills)
        sandbox_result = run_smoke_test_in_sandbox(generated)
        if not sandbox_result.ok:
            raise RuntimeError(sandbox_result.stderr or "smoke test failed")

        conn.execute(
            "UPDATE job_items SET status = 'succeeded', result = ?, error = NULL WHERE id = ?",
            (json.dumps(generated, ensure_ascii=False), item_id),
        )
        cur = conn.execute(
            "INSERT INTO agent_pool (role_name, skill_tags, source_job_item_id) "
            "VALUES (?, ?, ?)",
            (
                task_row["role_name"],
                json.dumps(required_skills, ensure_ascii=False),
                item_id,
            ),
        )
        audit.record(
            conn,
            created_by,
            "agent_generation_succeeded",
            "job_item",
            item_id,
            after={"agent_pool_id": cur.lastrowid, "role_name": task_row["role_name"]},
        )
    except Exception as exc:  # noqa: BLE001 — 실패 자체를 기록하는 게 목적
        conn.execute(
            "UPDATE job_items SET status = 'failed', error = ? WHERE id = ?",
            (str(exc), item_id),
        )
        audit.record(
            conn,
            created_by,
            "agent_generation_failed",
            "job_item",
            item_id,
            after={"error": str(exc)},
        )


def create_and_run_job(
    conn: sqlite3.Connection,
    brief_id: int,
    task_ids: list[int],
    created_by: int,
    generator: GeneratorFn | None = None,
) -> dict:
    """F4 — 생성 버튼 트리거. 여러 Task를 한 배치(job)로 실행한다.

    `generator`를 기본 인자 값으로 바인딩하지 않고 호출 시점에 모듈 전역에서
    조회한다 — 그래야 테스트가 `default_stub_generator`를 patch했을 때 실제로
    반영된다(기본 인자는 모듈 로드 시 한 번만 평가되어 patch를 못 본다).
    """
    generator = generator or default_stub_generator
    cur = conn.execute(
        "INSERT INTO jobs (brief_id, status, created_by) VALUES (?, 'running', ?)",
        (brief_id, created_by),
    )
    job_id = cur.lastrowid
    assert job_id is not None
    audit.record(conn, created_by, "agent_generation_started", "job", job_id)

    for task_id in task_ids:
        task_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        item_cur = conn.execute(
            "INSERT INTO job_items (job_id, task_id, status) VALUES (?, ?, 'pending')",
            (job_id, task_id),
        )
        item_id = item_cur.lastrowid
        assert item_id is not None
        _run_item(conn, item_id, task_row, created_by, generator)

    status = _recompute_job_status(conn, job_id)
    conn.commit()
    return {"job_id": job_id, "status": status, "items": _serialize_items(conn, job_id)}


def retry_failed_item(
    conn: sqlite3.Connection,
    job_id: int,
    item_id: int,
    created_by: int,
    generator: GeneratorFn | None = None,
) -> dict:
    """NF6 — 실패한 역할(job_item)만 재시도한다."""
    generator = generator or default_stub_generator
    item = conn.execute(
        "SELECT * FROM job_items WHERE id = ? AND job_id = ?", (item_id, job_id)
    ).fetchone()
    if item is None:
        raise ValueError("job_item_not_found")
    if item["status"] != "failed":
        raise ValueError("only_failed_items_can_be_retried")

    conn.execute(
        "UPDATE job_items SET retry_count = retry_count + 1 WHERE id = ?", (item_id,)
    )
    task_row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (item["task_id"],)
    ).fetchone()
    _run_item(conn, item_id, task_row, created_by, generator)
    status = _recompute_job_status(conn, job_id)
    conn.commit()
    return {"job_id": job_id, "status": status, "items": _serialize_items(conn, job_id)}


def _serialize_items(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, task_id, status, result, error, retry_count FROM job_items "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "retry_count": row["retry_count"],
        }
        for row in rows
    ]
