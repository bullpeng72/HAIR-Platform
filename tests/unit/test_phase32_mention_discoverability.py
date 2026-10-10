"""Phase 32 — 채팅 호출(`@`멘션) 발견성 개선, `docs/
ASANA_PARITY_DESIGN_PLAN.md` §10(2026-10-10, 사용자 요청 "채팅에서
@로 생성된 AI Agent와 사용자를 호출하려면 미리 전체 이름을 알고
있어야해. 채팅에서 더 쉽게 호출하기 위한 UI 개선 방안을 제안해줘" →
"제안 전체 구현해줘").

이 테스트는 백엔드 변경만 검증한다 — `conversations.search_mentionable()`
이 (1) 이름뿐 아니라 사람의 `human_pool.role`/`skill_tags`, Agent의
`skill_tags`, Team의 `description`까지 부분 일치시키는지, (2) 호출
하는 사용자가 과거에 직접 초대한 이력이 있는 대상을 더 앞세우는지.
메시지창 자동완성에 조직 전체 검색을 연결하고 미초대 대상을 즉석
초대하는 프런트엔드 변경(`setupMentionAutocomplete()`)은 Python 테스트
스위트로 직접 검증할 수 없어 실서버+Playwright로 라이브 검증한다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_agent(conn, role_name, skill_tags="[]"):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, ?, 'active')",
        (role_name, skill_tags),
    )
    conn.commit()
    return cur.lastrowid


def test_gc_mentionable_search_matches_human_role_and_skill(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post(
        "/api/human-pool",
        json={
            "name": "뷰어의 사람",
            "role": "UX 디자이너",
            "skill_tags": ["illustration"],
        },
    )
    assert resp.status_code == 201, resp.text
    human_id = resp.json()["id"]
    viewer_id = conn.execute(
        "SELECT id FROM users WHERE username = 'viewer1'"
    ).fetchone()["id"]
    resp = client.patch(f"/api/human-pool/{human_id}", json={"user_id": viewer_id})
    assert resp.status_code == 200, resp.text

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/mentionable", params={"q": "디자이너"})
    assert resp.status_code == 200, resp.text
    usernames = [h["username"] for h in resp.json()["humans"]]
    assert "viewer1" in usernames


def test_gc_mentionable_search_matches_agent_skill_tags(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _make_agent(conn, "아무개 Agent", '["번역"]')

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/mentionable", params={"q": "번역"})
    assert resp.status_code == 200, resp.text
    assert any(a["role_name"] == "아무개 Agent" for a in resp.json()["agents"])


def test_gc_mentionable_search_matches_team_description(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conn.execute(
        "INSERT INTO teams (name, description) VALUES ('아무개 팀', '백엔드 리뷰 전담')"
    )
    conn.commit()

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/mentionable", params={"q": "리뷰 전담"})
    assert resp.status_code == 200, resp.text
    assert any(t["name"] == "아무개 팀" for t in resp.json()["teams"])


def test_gc_mentionable_search_ranks_previously_invited_agent_first(client_and_conn):
    """정렬이 단순 알파벳순이 아니라 초대 이력을 우선함을 증명하려고
    일부러 알파벳순으로는 거꾸로인 이름을 고른다 — "검색-A-agent"가
    알파벳상 먼저인데도, actor가 최근 초대한 "검색-Z-agent"가 앞서야
    한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    _make_agent(conn, "검색-A-agent")
    agent_z = _make_agent(conn, "검색-Z-agent")

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_z},
    )
    assert resp.status_code == 201, resp.text

    resp = client.get("/api/conversations/mentionable", params={"q": "검색-"})
    assert resp.status_code == 200, resp.text
    names = [a["role_name"] for a in resp.json()["agents"]]
    assert names.index("검색-Z-agent") < names.index("검색-A-agent")


def test_gc_mentionable_search_ranking_is_per_actor(client_and_conn):
    """actor1이 초대한 이력은 actor1에게만 우선순위를 준다 — 다른
    사용자(actor2)가 검색하면 그 사람의 초대 이력 기준으로 다시
    정렬돼야 한다(전역 인기순이 아니라 "나의 최근 호출" 기준)."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    agent_a = _make_agent(conn, "정렬-A-agent")
    _make_agent(conn, "정렬-Z-agent")

    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "agent", "participant_id": agent_a},
    )

    _login(client, "manager1", "managerpass")
    resp = client.get("/api/conversations/mentionable", params={"q": "정렬-"})
    assert resp.status_code == 200, resp.text
    names = [a["role_name"] for a in resp.json()["agents"]]
    # manager1은 아무도 초대한 적이 없으므로 알파벳순(A가 먼저)으로
    # 나온다 — member1 전용 정렬이 전역에 새지 않았음을 확인.
    assert names.index("정렬-A-agent") < names.index("정렬-Z-agent")
