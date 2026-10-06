"""F4 — Job Queue(신규 Agent 생성). NF6: 상태머신 pending/running/succeeded/failed/partial.

실제 AgentForge 파이프라인(Spec Interpreter·Code Generator·Verifier)은 아직
vendor/import되지 않았다(SPEC.md §12 오픈 이슈) — `default_stub_generator`가 그
자리를 채우는 결정론적 스텁이다(F1과 같은 정신). 실제 연동 시 `generator` 인자만
교체하면 된다.

하나의 `job`은 브리프 단위 생성 배치이고, `job_items`는 Task(=역할) 단위 결과다.
전부 성공하면 job.status=succeeded, 전부 실패하면 failed, 섞이면 partial —
"실패 역할만 재시도"(NF6)는 실패한 job_item만 골라 재실행한다.

Phase 4 검증에서 발견: NF7(동일 역할 동시 생성요청 시 idempotency로 중복생성 방지)이
SPEC에는 결정돼 있었으나 실제로 구현되지 않았던 갭이었다 — `_existing_active_item`이
이를 메운다(같은 task에 이미 running/succeeded인 job_item이 있으면 재사용한다).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from hair_platform import audit, llm
from hair_platform.sandbox import run_smoke_test_in_sandbox

GeneratorFn = Callable[[sqlite3.Connection, str, list[str]], dict]


def default_stub_generator(
    conn: sqlite3.Connection, role_name: str, required_skills: list[str]
) -> dict:
    """AgentForge 실제 파이프라인 연동 전까지의 규칙기반 스텁.

    `conn`은 이 함수 자체는 쓰지 않는다 — `GeneratorFn` 계약이 모든 생성기에
    DB 커넥션을 균일하게 넘기도록 통일했다(사용자 요청, 2026-09-27, "MCP
    Tool처럼 동작하도록 개선해줘" — `crewai_agent_generator()`가 VectorDB/
    GraphDB Tool을 붙이려면 conn이 필요해서 계약 자체를 바꿨다)."""
    return {
        "role_name": role_name,
        "skill_tags": required_skills,
        "agents_yaml_snippet": f"role: {role_name}, skills: {required_skills}",
    }


def llm_agent_generator(
    conn: sqlite3.Connection,
    role_name: str,
    required_skills: list[str],
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """실제 LLM을 호출해 이 역할의 첫 산출물을 생성한다(사용자 요청, 비용 발생).

    `use_llm=True`를 명시적으로 요청했을 때만 이 함수가 쓰인다(app.py에서
    게이팅) — 기본 생성 경로는 여전히 무료인 `default_stub_generator`다.
    """
    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    system = llm.system_prompt_for_role(role_name, required_skills)
    prompt = (
        f"'{role_name}' 역할로서 지금 막 팀에 합류했습니다. "
        "이 역할이 프로젝트에서 처음 맡을 만한 구체적인 산출물을 하나 작성하세요."
    )
    output = llm.call_llm(resolved_provider, resolved_model, system, prompt)
    return {
        "role_name": role_name,
        "skill_tags": required_skills,
        "agents_yaml_snippet": f"role: {role_name}, skills: {required_skills}",
        "llm_provider": resolved_provider,
        "llm_model": resolved_model,
        "generated_output": output,
        "framework": "simple_llm",
    }


def _status_from_rows(rows: list[sqlite3.Row]) -> str:
    statuses = {row["status"] for row in rows}
    if not statuses or statuses == {"succeeded"}:
        return "succeeded"
    if statuses == {"failed"}:
        return "failed"
    if statuses <= {"succeeded", "failed"}:
        return "partial"
    return "running"


def _recompute_job_status(conn: sqlite3.Connection, job_id: int) -> str:
    items = conn.execute(
        "SELECT status FROM job_items WHERE job_id = ?", (job_id,)
    ).fetchall()
    status = _status_from_rows(items)
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    return status


def _existing_active_item(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    """NF7 — 같은 역할(task)에 이미 진행 중이거나 성공한 job_item이 있으면 반환한다.

    동시/반복 생성 요청이 같은 역할에 대해 Agent를 중복 생성하는 것을 막는다
    (Pool 경합 방지). task_id가 자연스러운 idempotency key 역할을 한다.
    """
    return conn.execute(
        "SELECT * FROM job_items WHERE task_id = ? AND status IN ('running', 'succeeded') "
        "ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()


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
        generated = generator(conn, task_row["role_name"], required_skills)
        sandbox_result = run_smoke_test_in_sandbox(generated)
        if not sandbox_result.ok:
            raise RuntimeError(sandbox_result.stderr or "smoke test failed")

        conn.execute(
            "UPDATE job_items SET status = 'succeeded', result = ?, error = NULL WHERE id = ?",
            (json.dumps(generated, ensure_ascii=False), item_id),
        )
        cur = conn.execute(
            "INSERT INTO agent_pool "
            "(role_name, skill_tags, source_job_item_id, llm_provider, llm_model, "
            "last_output, framework) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_row["role_name"],
                json.dumps(required_skills, ensure_ascii=False),
                item_id,
                generated.get("llm_provider"),
                generated.get("llm_model"),
                generated.get("generated_output"),
                generated.get("framework", "stub"),
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

    item_ids: list[int] = []
    reused_item_ids: set[int] = set()
    for task_id in task_ids:
        existing = _existing_active_item(conn, task_id)
        if existing is not None:
            # NF7: 이미 진행 중이거나 완료된 역할은 다시 생성하지 않고 재사용한다.
            audit.record(
                conn,
                created_by,
                "agent_generation_deduplicated",
                "task",
                task_id,
                after={"reused_item_id": existing["id"]},
            )
            item_ids.append(existing["id"])
            reused_item_ids.add(existing["id"])
            continue

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
        item_ids.append(item_id)

    rows = _fetch_items(conn, item_ids)
    status = _status_from_rows(rows)
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    conn.commit()
    return {
        "job_id": job_id,
        "status": status,
        "items": _serialize_rows(conn, rows, reused_item_ids),
        "reused_count": len(reused_item_ids),
    }


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


def _fetch_items(conn: sqlite3.Connection, item_ids: list[int]) -> list[sqlite3.Row]:
    if not item_ids:
        return []
    placeholders = ",".join("?" * len(item_ids))
    return conn.execute(
        "SELECT id, task_id, status, result, error, retry_count FROM job_items "
        f"WHERE id IN ({placeholders})",
        item_ids,
    ).fetchall()


def _serialize_rows(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    reused_item_ids: set[int] | None = None,
) -> list[dict]:
    reused_item_ids = reused_item_ids or set()
    items = []
    for row in rows:
        reused = row["id"] in reused_item_ids
        # 사용자 보고(2026-10-06, "AI Agent 생성 결과가 AI 에이전트 관리
        # 메뉴에서 확인되지 않음") — 성공(reused 여부 무관)한 job_item이
        # 실제로 어떤 agent_pool 행을 만들었는지/가리키는지 몰라 "AI
        # 에이전트 관리"의 여러 행 중 어떤 게 그건지 못 찾는 게 혼란의
        # 원인이었다. 매번(신규 생성이든 재사용이든) 그 id/role_name을
        # 직접 찾아 응답에 실어, 몇 번 항목을 보면 되는지 항상 명시한다
        # — 실패한 항목은 애초에 agent_pool 행이 없어 자연히 None.
        agent_pool_id = None
        agent_pool_role_name = None
        agent_row = conn.execute(
            "SELECT id, role_name FROM agent_pool WHERE source_job_item_id = ?",
            (row["id"],),
        ).fetchone()
        if agent_row is not None:
            agent_pool_id = agent_row["id"]
            agent_pool_role_name = agent_row["role_name"]
        items.append(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "status": row["status"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"],
                "retry_count": row["retry_count"],
                # NF7 dedup으로 기존 job_item을 재사용한 항목 — 새로 생성한 게
                # 아니라 "이미 있던 에이전트를 다시 가리킨 것"이라 UI가 이를
                # 명시적으로 보여줘야 "생성했는데 안 보인다"는 오해를 피한다.
                "reused": reused,
                "agent_pool_id": agent_pool_id,
                "agent_pool_role_name": agent_pool_role_name,
            }
        )
    return items


def _serialize_items(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, task_id, status, result, error, retry_count FROM job_items "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchall()
    return _serialize_rows(conn, rows)
