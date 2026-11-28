"""신입·일반 사용자 관점 Phase B(2026-11-28) — 추천 이유가 구체적(겹친 낱말·하는 일), 자료가 필요한 일에 첨부 안내 한 번,
누구에게 맡길까 목록(로그인한 누구나, 설정은 숨김)."""

from __future__ import annotations

from hair_platform import pool_catalog, wizard


def test_agent_recommendations_explain_matched_words_and_what_agent_does(client_and_conn):
    _, conn = client_and_conn
    r = wizard.recommend(conn, "지난 분기 매출 자료를 한 장으로 요약해 주세요", "member")
    agents = [x for x in r["recommendations"] if x["kind"] == "agent"]
    assert agents
    whys = [a["why"] for a in agents]
    assert len(set(whys)) == len(whys)  # 예전엔 모두 같은 문장
    for a in agents:
        assert a["matched"] and "요청과 맞는 낱말:" in a["why"]
        assert all(w in a["why"] for w in a["matched"])
    assert r["tip"] and "📎" in r["tip"]  # 자료가 필요한 일 — 한 번만(결과 위)
    assert all("tip" not in x for x in r["recommendations"])
    assert wizard.recommend(conn, "KPI가 뭐야?", "member")["tip"] is None


def test_agent_does_prefers_goal_then_main_skill(client_and_conn):
    _, conn = client_and_conn
    conn.execute("INSERT INTO skill_pool (name, description) VALUES ('테스트스킬', '첫 마디다: 둘째 마디 — 셋째')")
    conn.commit()
    assert pool_catalog.agent_does(conn, "목표 문장\n둘째 줄", ["테스트스킬"]) == "목표 문장"
    assert pool_catalog.agent_does(conn, "", ["테스트스킬", "없는스킬"]) == "첫 마디다"
    assert pool_catalog.agent_does(conn, None, []) == ""
    conn.execute("INSERT INTO skill_pool (name, description) VALUES ('기호스킬', '**굵게** 쓴 `코드` 설명. 둘째')")
    conn.commit()
    assert pool_catalog.agent_does(conn, "", ["기호스킬"]) == "굵게 쓴 코드 설명"


def test_agent_directory_for_any_logged_in_user_without_settings(client_and_conn):
    client, _ = client_and_conn
    assert client.get("/api/agent-directory").status_code == 401
    client.post("/login", json={"username": "viewer1", "password": "viewerpass"})
    rows = client.get("/api/agent-directory").json()
    assert len(rows) > 10
    assert set(rows[0]) == {"id", "role_name", "does", "skills", "dev"}  # 모델·비용·프롬프트는 주지 않는다
    assert any(r["dev"] for r in rows) and any(not r["dev"] for r in rows)  # 개발 Agent 구분
    assert sum(1 for r in rows if r["does"]) > len(rows) // 2  # 기본 Agent도 하는 일이 보인다(스킬 설명에서)
