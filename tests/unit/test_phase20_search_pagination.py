"""Phase 5 — 검색/필터 스케일(사용자 요청, 2026-09-28, "Phase 5(기업용
UI/UX)에 포함할 항목을 고르세요" → "검색/필터 스케일" 선택) — "부서/
스킬/가용성 복합 필터 + 전체 목록형 화면에 페이지네이션 일괄 적용"을
`GET /api/pool/search`에 구현한다.

**"부서" 필터를 넣지 않은 이유**: 이 스키마엔 애초에 "부서" 필드가
없다(조직 구조는 `human_pool.manager_id` 상사 체인/조직도로만
표현된다) — 존재하지 않는 데이터를 필터로 꾸며내지 않았다. 대신
`available_only: bool`을 `availability: 'available'|'unavailable'`로
넓혀 "가능만"뿐 아니라 "불가만"도 찾을 수 있게 했다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc165_search_availability_filters_both_directions(client_and_conn):
    """이전엔 'available_only'라 "불가만"을 못 찾았다 — 이제 명시적으로
    두 방향 다 필터할 수 있어야 한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "바쁜사람", "role": "테스터", "skill_tags": []},
    )
    busy_id = resp.json()["id"]
    resp = client.patch(
        f"/api/human-pool/{busy_id}", json={"availability": "unavailable"}
    )
    assert resp.status_code == 200

    resp = client.post(
        "/api/human-pool",
        json={"name": "한가한사람", "role": "테스터", "skill_tags": []},
    )
    free_id = resp.json()["id"]

    resp = client.get(
        "/api/pool/search", params={"role": "테스터", "availability": "unavailable"}
    )
    ids = {c["id"] for c in resp.json()["items"] if c["type"] == "human"}
    assert busy_id in ids
    assert free_id not in ids

    resp = client.get(
        "/api/pool/search", params={"role": "테스터", "availability": "available"}
    )
    ids = {c["id"] for c in resp.json()["items"] if c["type"] == "human"}
    assert free_id in ids
    assert busy_id not in ids


def test_gc166_search_pagination_slices_correctly(client_and_conn):
    """limit/offset이 실제로 페이지를 나누고, total이 필터링 후
    전체 개수를 정확히 반영해야 한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    for i in range(5):
        resp = client.post(
            "/api/human-pool",
            json={"name": f"페이지사람{i}", "role": "페이지테스트", "skill_tags": []},
        )
        assert resp.status_code == 201

    resp = client.get(
        "/api/pool/search",
        params={"role": "페이지테스트", "limit": 2, "offset": 0},
    )
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0

    resp = client.get(
        "/api/pool/search",
        params={"role": "페이지테스트", "limit": 2, "offset": 4},
    )
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1


def test_gc167_search_limit_is_capped(client_and_conn):
    """감사로그와 같은 관례 — limit은 200을 넘길 수 없다(무제한
    응답 방지)."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.get("/api/pool/search", params={"limit": 5000})
    assert resp.status_code == 200
    assert resp.json()["limit"] == 200


def test_gc171_unavailable_filter_excludes_agents(client_and_conn):
    """실측으로 발견한 갭(리뷰, "Phase1~5까지... 분석하고 개선해줘") —
    Agent는 항상 `is_available=True`로 고정돼 있는데
    `availability='unavailable'` 필터가 `human_pool_candidates()` 안에만
    있어서 Agent는 이 필터를 아예 안 받고 그대로 섞여 나왔다. "불가능한
    후보만" 찾는 필터인데 항상 가능한 Agent가 나오면 필터가 사실상 안
    지켜지는 것이다 — Agent는 이 필터에서 통째로 빠져야 한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        ("불가필터테스트",),
    )
    conn.commit()
    agent_id = cur.lastrowid

    resp = client.get(
        "/api/pool/search",
        params={"role": "불가필터테스트", "availability": "unavailable"},
    )
    ids = [c["id"] for c in resp.json()["items"] if c["type"] == "agent"]
    assert agent_id not in ids

    # "가능만"/필터 없음일 땐 여전히 나와야 한다(회귀 방지).
    resp = client.get(
        "/api/pool/search",
        params={"role": "불가필터테스트", "availability": "available"},
    )
    ids = [c["id"] for c in resp.json()["items"] if c["type"] == "agent"]
    assert agent_id in ids
