"""Phase 59 — 업무 분해 LLM 제안 + 생성 시점 의존관계 연결(사용자 요청,
2026-11-04, "업무 배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너
수준의 게임... 개선 계획을 수립해줘" → "1번(업무 분해 단위 가이드)...
설계 들어가줘").

실시간 데모(park_member 계정, 로드러너 게임 웹 구현)에서 복잡한 업무를
통째로 Task 하나로 만들어 한 Agent에게 맡겼다가 재시도마다 퇴행이
반복된 문제의 보완 — `brief_decompose_llm.suggest_decomposition()`이
LLM을 호출해 독립 검증 가능한 단위 + 선행관계(`depends_on_index`)를
제안하고, `POST /api/briefs/{id}/tasks`가 그 결과를 한 배치로 받아
`task_dependencies`까지 원자적으로 생성한다.

**비용/네트워크 발생 방지**: `test_phase55_match_llm_judge.py`와 같은
원칙 — `hair_platform.llm.call_llm`을 patch한다."""

from __future__ import annotations

import json
from unittest.mock import patch

from hair_platform import brief_decompose_llm, db
from hair_platform.auth import Role, create_user


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_brief(conn, raw_text="로드러너 게임 웹 구현"):
    # `client_and_conn` 픽스처는 이미 "admin1"을 시드해두므로(다른
    # phase 테스트들과 동일), 여기서 또 만들면 UNIQUE 충돌이 난다 —
    # 있으면 재사용하고 없을 때만(순수 `db.get_connection(":memory:")`
    # 단위 테스트) 새로 만든다.
    row = conn.execute(
        "SELECT id FROM users WHERE username = 'admin1'"
    ).fetchone()
    admin_id = row["id"] if row else create_user(conn, "admin1", "adminpass", Role.ADMIN)
    cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text) VALUES (?, ?)",
        (admin_id, raw_text),
    )
    conn.commit()
    return cur.lastrowid


# ---------- suggest_decomposition() — 단위 테스트(DB 직접) ----------


def test_suggest_decomposition_unknown_brief_raises_value_error():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    try:
        brief_decompose_llm.suggest_decomposition(conn, 999)
        raise AssertionError("ValueError를 기대했습니다")
    except ValueError as exc:
        assert str(exc) == "brief_not_found"


def test_suggest_decomposition_falls_back_to_empty_on_llm_failure():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    brief_id = _make_brief(conn)
    with patch("hair_platform.llm.call_llm", side_effect=RuntimeError("boom")):
        result = brief_decompose_llm.suggest_decomposition(conn, brief_id)
    assert result == {"tasks": []}


def test_suggest_decomposition_falls_back_to_empty_on_bad_json():
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    brief_id = _make_brief(conn)
    with patch("hair_platform.llm.call_llm", return_value="이건 JSON이 아닙니다"):
        result = brief_decompose_llm.suggest_decomposition(conn, brief_id)
    assert result == {"tasks": []}


def test_suggest_decomposition_returns_tasks_with_dependencies(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    brief_id = _make_brief(conn)
    raw = json.dumps(
        {
            "tasks": [
                {"role_name": "맵 엔진", "required_skills": ["javascript"], "depends_on_index": None},
                {"role_name": "플레이어 로직", "required_skills": [], "depends_on_index": 0},
            ]
        },
        ensure_ascii=False,
    )
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = brief_decompose_llm.suggest_decomposition(conn, brief_id)
    assert result["tasks"] == [
        {"role_name": "맵 엔진", "required_skills": ["javascript"], "depends_on_index": None},
        {"role_name": "플레이어 로직", "required_skills": [], "depends_on_index": 0},
    ]


def test_suggest_decomposition_remaps_depends_on_index_after_dropping_hallucinated_entry(
    monkeypatch,
):
    """LLM이 3개를 반환했는데 가운데 항목(인덱스 1)이 role_name 없이
    불량이라 제거되면, 그 뒤 항목(원래 인덱스 2)의 depends_on_index가
    원래 인덱스 0을 가리키던 걸 새 인덱스(1)로 재매핑해야 한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    brief_id = _make_brief(conn)
    raw = json.dumps(
        {
            "tasks": [
                {"role_name": "맵 엔진", "required_skills": [], "depends_on_index": None},
                {"role_name": "", "required_skills": [], "depends_on_index": None},  # 불량 — 제거됨
                {"role_name": "통합 QA", "required_skills": [], "depends_on_index": 0},
            ]
        },
        ensure_ascii=False,
    )
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = brief_decompose_llm.suggest_decomposition(conn, brief_id)
    assert len(result["tasks"]) == 2
    assert result["tasks"][0]["role_name"] == "맵 엔진"
    assert result["tasks"][1]["role_name"] == "통합 QA"
    assert result["tasks"][1]["depends_on_index"] == 0  # 재매핑됨(원래는 0이었고 그대로 0)


def test_suggest_decomposition_drops_dangling_depends_on_index(monkeypatch):
    """제거된 항목을 가리키던 depends_on_index는 None이 되어야 한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    brief_id = _make_brief(conn)
    raw = json.dumps(
        {
            "tasks": [
                {"role_name": "", "required_skills": [], "depends_on_index": None},  # 제거됨(인덱스 0)
                {"role_name": "솔로 작업", "required_skills": [], "depends_on_index": 0},
            ]
        },
        ensure_ascii=False,
    )
    with patch("hair_platform.llm.call_llm", return_value=raw):
        result = brief_decompose_llm.suggest_decomposition(conn, brief_id)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["depends_on_index"] is None


# ---------- API 엔드포인트 ----------


def test_decompose_suggest_endpoint_requires_admin_or_manager(client_and_conn):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "member1", "memberpass")
    resp = client.post(f"/api/briefs/{brief_id}/decompose-suggest")
    assert resp.status_code == 403


def test_decompose_suggest_endpoint_does_not_write_to_db(client_and_conn):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    raw = json.dumps(
        {"tasks": [{"role_name": "맵 엔진", "required_skills": [], "depends_on_index": None}]}
    )
    with patch("hair_platform.llm.call_llm", return_value=raw):
        resp = client.post(f"/api/briefs/{brief_id}/decompose-suggest")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tasks"][0]["role_name"] == "맵 엔진"
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE brief_id = ?", (brief_id,)
    ).fetchone()["c"]
    assert count == 0


def test_decompose_suggest_endpoint_404_for_unknown_brief(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    resp = client.post("/api/briefs/999999/decompose-suggest")
    assert resp.status_code == 404


def test_decompose_into_tasks_accepts_depends_on_index_and_creates_dependency_row(
    client_and_conn,
):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={
            "tasks": [
                {"role_name": "맵 엔진", "required_skills": []},
                {"role_name": "플레이어 로직", "required_skills": [], "depends_on_index": 0},
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    task_ids = [t["id"] for t in resp.json()["tasks"]]
    row = conn.execute(
        "SELECT * FROM task_dependencies WHERE task_id = ?", (task_ids[1],)
    ).fetchone()
    assert row is not None
    assert row["depends_on_task_id"] == task_ids[0]


def test_decompose_into_tasks_rejects_cycle_in_depends_on_index(client_and_conn):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={
            "tasks": [
                {"role_name": "A", "required_skills": [], "depends_on_index": 1},
                {"role_name": "B", "required_skills": [], "depends_on_index": 0},
            ]
        },
    )
    assert resp.status_code == 409, resp.text
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE brief_id = ?", (brief_id,)
    ).fetchone()["c"]
    assert count == 0  # 롤백 확인 — 부분 생성 상태로 안 남음


def test_decompose_into_tasks_rejects_self_reference_depends_on_index(client_and_conn):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "A", "required_skills": [], "depends_on_index": 0}]},
    )
    assert resp.status_code == 422


def test_decompose_into_tasks_rejects_out_of_range_depends_on_index(client_and_conn):
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "A", "required_skills": [], "depends_on_index": 5}]},
    )
    assert resp.status_code == 422


def test_decompose_into_tasks_omitting_depends_on_index_is_unchanged(client_and_conn):
    """회귀 가드 — 의존관계를 안 쓰는 기존 단건 흐름은 그대로 동작해야 한다."""
    client, conn = client_and_conn
    brief_id = _make_brief(conn)
    _login(client, "admin1", "adminpass")
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "백엔드 개발자", "required_skills": ["python"], "headcount": 1}]},
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["tasks"][0]["id"]
    dep_count = conn.execute(
        "SELECT COUNT(*) AS c FROM task_dependencies WHERE task_id = ?", (task_id,)
    ).fetchone()["c"]
    assert dep_count == 0
