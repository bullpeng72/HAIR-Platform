"""Phase 36 — UI 개선 로드맵 Phase 1 구현(2026-10-12,
"Phase 1 (A)(B)(H1)(H2)(I5)부터 구현 시작해줘").

(A) `run_crew()`가 CrewAI의 토큰 사용량(`token_usage`)을 API 응답에 포함한다
(지금까지 `audit_log` 참고 기록에만 있었다). (B) `/api/all-work`(ADMIN/MANAGER
전용) — 할 일 보드의 "전체 조직" 범위가 쓸 org-wide 조회. (H1)(H2)(I5)(복사
버튼·미리보기/소스 토글·요약 배지 재사용)는 순수 프런트엔드(webui.py JS)라
`node --check`로만 문법 검증했고, 이 파일은 백엔드(A)(B)만 다룬다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import crew_runtime


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _crew_output(raw: str, task_raws: list[str], token_usage=None):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    kwargs = {"raw": raw, "tasks_output": tasks_output}
    if token_usage is not None:
        kwargs["token_usage"] = token_usage
    return CrewOutput(**kwargs)


def _make_two_active_agents(conn):
    ids = []
    for role, skills in [("기획자", ["요구사항정의"]), ("분석가", ["요구사항분석"])]:
        cur = conn.execute(
            "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
            (role, f'["{skills[0]}"]'),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


# ---------- (A) run_crew()가 token_usage를 반환한다 ----------


def test_gc_run_crew_returns_token_usage_in_response(client_and_conn):
    from crewai.types.usage_metrics import UsageMetrics

    _client, conn = client_and_conn
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()["id"]
    agent1, agent2 = _make_two_active_agents(conn)

    usage = UsageMetrics(
        total_tokens=321,
        prompt_tokens=200,
        completion_tokens=121,
        successful_requests=2,
    )
    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종", ["기획서", "분석서"], token_usage=usage),
    ):
        result = crew_runtime.run_crew(
            conn,
            [agent1, agent2],
            ["기획서를 작성해줘", "그 기획서를 분석해줘"],
            admin_id,
            process="sequential",
            provider="openai",
        )

    # crewai==1.6.1(현재 설치본) `UsageMetrics.model_dump()`가 실제로 내는
    # 키 집합 — 테스트 작성 당시 가정했던 reasoning_tokens/cache_creation_tokens
    # 필드는 이 버전의 모델에 없다(라이브러리 버전 drift, 우리 코드 변경과 무관).
    assert result["token_usage"] == {
        "total_tokens": 321,
        "prompt_tokens": 200,
        "cached_prompt_tokens": 0,
        "completion_tokens": 121,
        "successful_requests": 2,
    }


def test_gc_crew_run_endpoint_exposes_token_usage(client_and_conn):
    """`run_crew_endpoint`가 `run_crew()`의 반환 dict를 그대로 돌려주므로
    (app.py `return result`), API 응답에도 token_usage가 실려야 한다."""
    client, conn = client_and_conn
    agent1, agent2 = _make_two_active_agents(conn)
    _login(client, "admin1", "adminpass")

    with patch(
        "crewai.Crew.kickoff",
        return_value=_crew_output("최종", ["기획서", "분석서"]),
    ):
        resp = client.post(
            "/api/crew/run",
            json={
                "agent_ids": [agent1, agent2],
                "task_descriptions": ["a", "b"],
                "process": "sequential",
            },
        )
    assert resp.status_code == 201, resp.text
    assert "token_usage" in resp.json()
    assert resp.json()["token_usage"]["total_tokens"] == 0  # 기본값(미설정)


# ---------- (B) /api/all-work — 할 일 보드 "전체 조직" 범위 ----------


def _make_brief_and_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def test_gc_all_work_is_admin_manager_only(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/all-work")
    assert resp.status_code == 403

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/all-work")
    assert resp.status_code == 403


def test_gc_all_work_returns_org_wide_confirmed_assignments(client_and_conn):
    """member1 혼자서는 안 보이는, 다른 사람에게 확정 배정된 task까지
    admin이 `/api/all-work`로는 전부 볼 수 있어야 한다("전체 조직
    범위") — `/api/my-work`는 여전히 당사자만 좁혀 보여준다(회귀 없음)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _brief_id, task_id = _make_brief_and_task(client, "프론트엔드 개발")

    resp = client.post(
        "/api/human-pool",
        json={"name": "park의 사람", "role": "프론트엔드 개발", "skill_tags": []},
    )
    human_id = resp.json()["id"]
    member1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE human_pool SET user_id = ? WHERE id = ?", (member1_id, human_id)
    )
    conn.commit()

    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) "
        "VALUES (?, 'human', ?, ?, 'confirmed')",
        (task_id, human_id, manager_id),
    )
    conn.commit()

    # admin1 본인은 이 task의 당사자가 아니므로 /api/my-work엔 안 뜬다.
    resp = client.get("/api/my-work")
    assert task_id not in [r["task_id"] for r in resp.json()]

    # /api/all-work로는 admin1이 당사자가 아니어도 조직 전체 현황으로 보인다.
    resp = client.get("/api/all-work")
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["resource_type"] == "human"
    assert rows[0]["my_relationship"] is None
