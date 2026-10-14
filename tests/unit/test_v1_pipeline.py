"""Phase 3 v1 골든셋 2건(API) — Task 분해 + Pool 검색 + 점수 breakdown."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc9_f2_task_decomposition_and_f3_pool_search(client_and_conn):
    client, _ = client_and_conn

    _login(client, "member1", "memberpass")
    resp = client.post(
        "/api/briefs", json={"raw_text": "백엔드 개발자 1명\n디자이너 1명"}
    )
    brief_id = resp.json()["id"]

    # 팀원은 Task 분해 등록 권한이 없다(RBAC 매트릭스)
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": ["python"]}]},
    )
    assert resp.status_code == 403

    # 매니저가 Task 분해 결과를 등록 — 후보 매칭은 포함하지 않는다
    _login(client, "manager1", "managerpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={
            "tasks": [
                {"role_name": "백엔드 개발자", "required_skills": ["python"]},
                {"role_name": "디자이너", "required_skills": ["figma"]},
            ]
        },
    )
    assert resp.status_code == 201
    task_ids = [t["id"] for t in resp.json()["tasks"]]
    assert len(task_ids) == 2

    # 사람 리소스 등록 후 Pool 통합 검색 — 뷰어도 조회는 가능하다(RBAC 매트릭스)
    resp = client.post(
        "/api/human-pool",
        json={
            "name": "김하니",
            "role": "백엔드 개발자",
            "skill_tags": ["python"],
            "cost_estimate": 80.0,
        },
    )
    assert resp.status_code == 201

    _login(client, "viewer1", "viewerpass")
    resp = client.get("/api/pool/search", params={"role": "백엔드"})
    assert resp.status_code == 200
    results = resp.json()["items"]
    # 사용자 요청(2026-10-14, "자율 개발 크루" 10인 개발자 풀)으로
    # `reference_pipeline_seeds.py`가 "백엔드 개발자 2/3/4" Agent를
    # 기본 배포에 항상 시드하게 되면서, "백엔드" 부분 일치 검색이 더 이상
    # 이 테스트가 만든 사람 1건만 돌려주지 않는다(시드된 Agent도 같이
    # 걸림) — 결과가 "그 사람 1건뿐"이 아니라 "그 사람을 포함하는지"로
    # 검증 범위를 좁힌다.
    assert any(r["type"] == "human" and r["name"] == "김하니" for r in results)


def test_gc10_matching_returns_score_breakdown_f14(client_and_conn):
    client, _ = client_and_conn

    _login(client, "manager1", "managerpass")
    resp = client.post("/api/briefs", json={"raw_text": "백엔드 개발자 1명"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={
            "tasks": [
                {"role_name": "백엔드 개발자", "required_skills": ["python", "fastapi"]}
            ]
        },
    )
    task_id = resp.json()["tasks"][0]["id"]

    # 후보가 없는 상태 — 생성 제안 신호를 받는다
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200
    assert resp.json()["generate_new"] is True

    client.post(
        "/api/human-pool",
        json={
            "name": "박도전",
            "role": "백엔드 개발자",
            "skill_tags": ["python"],
            "cost_estimate": 60.0,
        },
    )

    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200
    body = resp.json()
    assert body["generate_new"] is False
    assert len(body["top"]) == 1
    top = body["top"][0]
    assert top["candidate"]["id"] is not None
    # 성과리뷰가 아직 없는 v1에서는 사람 후보의 performance_score가 항상 null이라
    # null 예외 경로(가용성60·비용40, 적합도 제외)를 탄다.
    assert "fit" not in top["breakdown"]
    assert set(top["breakdown"].keys()) == {"availability", "cost"}

    # 사용자 결정(2026-09-29, "매칭 점수 breakdown을 배정 후보 본인에게
    # 공개")로 MEMBER도 호출은 가능해졌다 — 다만 본인 human_pool 레코드가
    # 없으니(이 테스트의 "박도전"은 member1과 연결되지 않음) top이 항상
    # 빈 목록으로 필터링된다. viewer는 여전히 차단된다(RBAC 매트릭스).
    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200
    assert resp.json()["top"] == []

    _login(client, "viewer1", "viewerpass")
    resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 403
