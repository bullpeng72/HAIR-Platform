"""Phase 4 검증 골든셋 — idempotency 갭 수정, 샌드박스 우회 방지 회귀,
배정 공정성 지표. "Gate A–G 전부 fail→진단→해결" 항목은 실제
AgentForge agent-evaluator SDK가 vendor/import되지 않아(오픈 이슈) 이 저장소
에서는 검증 불가능하다 — 정직하게 남긴다.
"""

from __future__ import annotations

from unittest.mock import patch

from hair_platform.jobs import default_stub_generator
from hair_platform.sandbox import SandboxResult


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief_with_task(client, role_name="백엔드 개발자"):
    resp = client.post("/api/briefs", json={"raw_text": role_name})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": ["python"]}]},
    )
    task_id = resp.json()["tasks"][0]["id"]
    return brief_id, task_id


def test_gc37_duplicate_generation_request_reuses_existing_item_nf7(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client)

    resp1 = client.post(
        f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
    )
    assert resp1.status_code == 201
    first_item_id = resp1.json()["items"][0]["id"]

    # 같은 역할(task)에 대해 다시 생성을 요청해도 새 agent_pool 항목이 또 생기면 안 된다.
    resp2 = client.post(
        f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
    )
    assert resp2.status_code == 201
    second_item_id = resp2.json()["items"][0]["id"]
    assert second_item_id == first_item_id  # 새로 만들지 않고 기존 것을 재사용

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM agent_pool WHERE source_job_item_id = ?",
        (first_item_id,),
    ).fetchone()["c"]
    assert count == 1  # 중복 생성되지 않았다

    actions = {
        row["action"] for row in conn.execute("SELECT action FROM audit_log").fetchall()
    }
    assert "agent_generation_deduplicated" in actions


def test_gc37b_dedup_response_flags_reused_items(client_and_conn):
    """dedup 재사용을 응답에 `reused`/`reused_count`로 명시한다 — UI가
    "생성했는데 안 보인다"는 오해를 피하도록(사용자 요청, 2026-10-02).

    **정정(2026-10-06, 사용자 보고 "생성은 '성공'으로 떴는데 관리 탭에
    안 보임")**: `reused` 플래그만으로는 "그래서 어떤 Agent를 보라는
    건지" 알 수 없어 혼란이 계속됐다 — 매 항목(신규 생성이든 재사용이든)
    에 실제로 연결된 `agent_pool_id`/`agent_pool_role_name`을 직접
    실어 명시한다."""
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    brief_id, task_id = _make_brief_with_task(client)

    resp1 = client.post(
        f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
    )
    assert resp1.status_code == 201
    item1 = resp1.json()["items"][0]
    assert item1["reused"] is False
    assert resp1.json()["reused_count"] == 0
    assert item1["agent_pool_id"] is not None
    assert item1["agent_pool_role_name"] == "백엔드 개발자"

    resp2 = client.post(
        f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
    )
    assert resp2.status_code == 201
    body = resp2.json()
    item2 = body["items"][0]
    assert body["reused_count"] == 1
    assert item2["reused"] is True
    # 재사용돼도 같은 기존 Agent를 가리켜야 한다 — 새로 만들어진 게 아니다.
    assert item2["agent_pool_id"] == item1["agent_pool_id"]
    assert item2["agent_pool_role_name"] == "백엔드 개발자"


def test_gc38_sandbox_failure_cannot_be_bypassed_into_success(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    # 2026-10-14 "기본 배포에 Agent를 포함해줘"로 `agent_pool`이 더는
    # 빈 상태로 시작하지 않는다(레퍼런스 파이프라인 Agent 12개가 항상
    # 시드돼 있음) — 그래서 "0건"이 아니라 "이 테스트가 시작하기 전
    # 대비 늘지 않았는지"를 기준으로 삼는다.
    baseline_count = conn.execute("SELECT COUNT(*) AS c FROM agent_pool").fetchone()[
        "c"
    ]
    brief_id, task_id = _make_brief_with_task(client, "디자이너")

    with patch(
        "hair_platform.jobs.run_smoke_test_in_sandbox",
        return_value=SandboxResult(ok=False, stderr="sandbox check failed"),
    ):
        resp = client.post(
            f"/api/briefs/{brief_id}/generate", json={"task_ids": [task_id]}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "failed"
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["agent_pool_id"] is None

    # 샌드박스가 실패라고 답했는데도 agent_pool에 항목이 생기면 우회된 것이다.
    count = conn.execute("SELECT COUNT(*) AS c FROM agent_pool").fetchone()["c"]
    assert count == baseline_count


def test_gc39_fairness_report_computes_rejection_rate_gap(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")

    def make_and_decide(role, cost, approve):
        _, task_id = _make_brief_with_task(client, role)
        resp = client.post(
            "/api/human-pool",
            json={
                "name": f"h-{task_id}",
                "role": role,
                "skill_tags": [],
                "cost_estimate": cost,
            },
        )
        human_id = resp.json()["id"]
        resp = client.post(
            f"/api/tasks/{task_id}/assignments",
            json={"resource_type": "human", "resource_id": human_id},
        )
        approval_id = resp.json()["approval_id"]
        _login(client, "admin1", "adminpass")
        client.post(f"/api/approvals/{approval_id}/decide", json={"approve": approve})
        _login(client, "manager1", "managerpass")

    make_and_decide("역할A", 10.0, True)
    make_and_decide("역할B", 10.0, False)
    make_and_decide("역할C", 10.0, True)

    _login(client, "admin1", "adminpass")
    resp = client.get("/api/fairness-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["human"]["confirmed"] == 2
    assert body["human"]["rejected"] == 1
    assert body["human"]["rejection_rate"] == 1 / 3
    assert body["low_sample_warning"] is True  # 표본 3건 < 10


def test_gc40_fairness_report_rbac_admin_manager_only(client_and_conn):
    client, _ = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/fairness-report")
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/fairness-report")
    assert resp.status_code == 200


def test_gc41_stub_generator_still_used_as_module_level_default():
    # 회귀 방지: default_stub_generator 참조가 여전히 모듈에서 임포트 가능해야 한다
    # (jobs.py 리팩터링 중 실수로 이름이 바뀌면 이 테스트가 즉시 잡는다).
    result = default_stub_generator(None, "백엔드 개발자", ["python"])
    assert result["role_name"] == "백엔드 개발자"
