"""Phase 5 — 협업 워크스페이스 UX 개선(사용자 요청, 2026-09-28, "Phase 5
(기업용 UI/UX)에 포함할 항목을 고르세요" → "협업 워크스페이스 UX 개선"
선택). "공유 스레드 패턴 차용 — 채팅형
타임라인, Skill 자동완성"의 백엔드 부분을 검증한다(좌우 정렬·Gate 배지는
순수 CSS/렌더링이라 여기선 API 계약만 확인한다).

**실측으로 발견한 부수적 갭**: `GET /api/skill-pool`이 Admin/매니저
전용이라 팀원이 스킬 자동완성을 못 받고 있었다 — 이름만 노출하는
`GET /api/skill-pool/names`를 전 role에 새로 열었다.
"""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_gc168_skill_pool_names_open_to_all_authenticated_roles(client_and_conn):
    """Admin 전용인 /api/skill-pool과 달리, 이름만 주는 엔드포인트는
    member/viewer도 접근할 수 있어야 한다(Skill 자동완성이 그들에게도
    필요하므로)."""
    client, _conn = client_and_conn

    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/skill-pool",
        json={"name": "python", "description": "파이썬", "augmentation_mode": "none"},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "member1", "memberpass")
    resp = client.get("/api/skill-pool/names")
    assert resp.status_code == 200
    assert "python" in resp.json()

    # 참고: 전체 상세(도구 연결 등)는 여전히 admin/manager 전용이다.
    resp = client.get("/api/skill-pool")
    assert resp.status_code == 403


def test_gc169_skill_pool_names_returns_plain_string_list(client_and_conn):
    """이름만 노출한다 — augmentation_mode/default_tool_pool_ids 같은
    관리 상세는 이 엔드포인트에 안 섞여야 한다."""
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/skill-pool",
        json={
            "name": "figma",
            "description": "디자인 도구",
            "augmentation_mode": "none",
        },
    )
    assert resp.status_code == 201

    resp = client.get("/api/skill-pool/names")
    assert resp.status_code == 200
    body = resp.json()
    assert all(isinstance(x, str) for x in body)
    assert "figma" in body
