"""Phase 7 코드 재점검(2026-09-26, R3) 골든셋 — R1/R2 수정 이후 남아있던 3건을
발견·수정하고 회귀 테스트로 고정한다.

1. 가중치 스코어링이 role 관련성을 전혀 필터링하지 않아 role이 완전히
   다른 후보까지 "매칭 결과"로 추천되던 핵심 매칭 로직 버그를 고쳤다.
2. `users` 테이블이 비어 있으면 아무도 로그인할 수 없던 콜드스타트
   불가 상태를 `hair_platform.bootstrap` CLI로 해결했다.
3. Agent Pool엔 onboard/deprecate 라이프사이클이 있는데 Human Pool은
   생성 후 수정·오프보딩할 방법이 없던 비대칭을 `PATCH /api/human-pool/{id}`·
   `POST /api/human-pool/{id}/offboard`로 해소했다.
"""

from __future__ import annotations

import pytest

from hair_platform.assignment import match_candidates
from hair_platform.bootstrap import bootstrap_admin, main
from hair_platform.models import ResourceCandidate


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _candidate(**kwargs) -> ResourceCandidate:
    base = {
        "id": 1,
        "type": "human",
        "role": "백엔드 개발자",
        "is_available": True,
        "cost": 100.0,
        "skill_tags": ["python"],
        "performance_score": None,
    }
    base.update(kwargs)
    return ResourceCandidate(**base)


def test_gc51_unrelated_role_candidates_are_never_recommended():
    designer = _candidate(id=1, role="디자이너", skill_tags=["figma"], cost=10.0)
    qa = _candidate(id=2, role="QA", skill_tags=["selenium"], cost=20.0)

    # Pool에 후보는 있지만(완전 비어있지 않음) 전부 role이 무관하다.
    result = match_candidates("백엔드 개발자", ["python"], [designer, qa])

    assert result.top == []
    assert result.generate_new is True  # 수정 전엔 무관한 후보가 top으로 나왔다.


def test_gc51b_role_matching_candidates_still_scored_normally():
    a = _candidate(id=1, role="백엔드 개발자", cost=10.0)
    b = _candidate(id=2, role="백엔드 개발자", cost=100.0)
    unrelated = _candidate(id=3, role="디자이너", cost=0.0)

    result = match_candidates("백엔드 개발자", ["python", "django"], [a, b, unrelated])

    assert result.generate_new is False
    ids = {sc.candidate.id for sc in result.top}
    assert 3 not in ids  # 무관한 role은 점수판에서 아예 빠진다.
    assert (
        result.top[0].candidate.id == 1
    )  # 비용이 싼 쪽이 1등(cost 정규화도 관련 후보끼리만)


def test_gc52_bootstrap_admin_creates_first_user_then_refuses(tmp_path):
    db_path = str(tmp_path / "hair.db")

    user_id = bootstrap_admin(db_path, "admin", "adminpass")
    assert user_id > 0

    with pytest.raises(RuntimeError):
        bootstrap_admin(db_path, "someone-else", "pw")


def test_gc53_bootstrap_cli_exit_codes(tmp_path, capsys):
    db_path = str(tmp_path / "hair.db")

    code = main(["--db-path", db_path, "--username", "admin", "--password", "pw"])
    assert code == 0
    assert "생성 완료" in capsys.readouterr().out

    code = main(["--db-path", db_path, "--username", "another", "--password", "pw"])
    assert code == 1
    assert "최초 1회만" in capsys.readouterr().err


def test_gc54_human_pool_update_endpoint(client_and_conn):
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]

    _login(client, "viewer1", "viewerpass")
    resp = client.patch(
        f"/api/human-pool/{human_id}", json={"availability": "unavailable"}
    )
    assert resp.status_code == 403

    _login(client, "manager1", "managerpass")
    resp = client.patch(
        f"/api/human-pool/{human_id}", json={"skill_tags": ["python", "fastapi"]}
    )
    assert resp.status_code == 200
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    assert "fastapi" in row["skill_tags"]

    # 사용자 결정(2026-09-29,
    # "(b) Human Pool도 admin 전용으로 좁히기") — cost_estimate는 이제
    # 매니저가 이 일반 PATCH로 못 고친다.
    resp = client.patch(f"/api/human-pool/{human_id}", json={"cost_estimate": 150.0})
    assert resp.status_code == 403

    _login(client, "admin1", "adminpass")
    resp = client.patch(f"/api/human-pool/{human_id}", json={"cost_estimate": 150.0})
    assert resp.status_code == 200
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    assert row["cost_estimate"] == 150.0

    resp = client.patch("/api/human-pool/999999", json={"cost_estimate": 1.0})
    assert resp.status_code == 404


def test_gc181_human_pool_manager_id_can_be_set(client_and_conn):
    """사용자 요청(2026-09-29, "admin, 사용자 계정을 정리해줘")으로 실제
    계정을 정리하려다 발견한 갭 — `GET /api/org-chart`는 이미
    `human_pool.manager_id`를 읽어 사람을 관리자별로 묶는데, 그 값을
    채울 API가 `HumanPoolUpdate`에 없어 전 계정이 "관리자 없음" 버킷에
    뭉뚱그려지고 있었다."""
    client, conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]

    manager1_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]

    resp = client.patch(
        f"/api/human-pool/{human_id}", json={"manager_id": manager1_id}
    )
    assert resp.status_code == 200, resp.text
    row = conn.execute("SELECT * FROM human_pool WHERE id = ?", (human_id,)).fetchone()
    assert row["manager_id"] == manager1_id

    resp = client.patch(f"/api/human-pool/{human_id}", json={"manager_id": 999999})
    assert resp.status_code == 404

    resp = client.patch(f"/api/human-pool/{human_id}", json={})
    assert resp.status_code == 400


def test_gc55_human_offboard_excludes_from_search_and_matching(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "manager1", "managerpass")
    resp = client.post(
        "/api/human-pool",
        json={"name": "김하니", "role": "백엔드 개발자", "skill_tags": ["python"]},
    )
    human_id = resp.json()["id"]

    resp = client.get("/api/pool/search", params={"role": "백엔드"})
    assert human_id in {c["id"] for c in resp.json()["items"] if c["type"] == "human"}

    resp = client.post(f"/api/human-pool/{human_id}/offboard")
    assert resp.status_code == 200
    assert resp.json()["availability"] == "offboarded"

    # 오프보딩 후엔 검색에서 완전히 사라진다(단순 unavailable과 다르다).
    resp = client.get("/api/pool/search", params={"role": "백엔드"})
    assert human_id not in {
        c["id"] for c in resp.json()["items"] if c["type"] == "human"
    }

    resp = client.post("/api/human-pool/999999/offboard")
    assert resp.status_code == 404
