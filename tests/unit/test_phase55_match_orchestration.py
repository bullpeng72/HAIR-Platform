"""Phase 55 — `POST /api/tasks/{task_id}/match`의 결정론적 매칭(역할명
부분일치 포함) → LLM judge 폴백 오케스트레이션(사용자 요청, 2026-11-02,
"LLM judge로 추천 인력을 찾도록 개선" → 이후 "1번 방식 찾기시에 3번
(직접 검색)처럼 부분일치로 찾아지도록 개선하고 3번 방식은 삭제"로
수동 검색 폴백은 제거되고 결정론적 매칭 자체가 부분일치를 보게 됨).

**비용/네트워크 발생 방지** — `hair_platform.llm.call_llm`을 patch한다
(test_phase49/test_phase55_match_llm_judge와 같은 원칙). `use_llm_judge`
를 안 주면 애초에 `match_llm_judge.judge_candidates()` 자체가 호출되지
않아야 한다(`llm.py`의 "비용 발생 호출은 상위에서 게이팅" 원칙 — 평소
매칭 화면 클릭이 조용히 LLM을 호출하면 안 된다)."""

from __future__ import annotations

import json
from unittest.mock import patch


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _make_task_no_candidates(client, role_name="존재하지-않는-역할-NC001"):
    # 2026-11-08 — 원래 기본값 "콘텐츠 카피라이팅"이 "어떤 agent_pool
    # 역할과도 안 겹친다"는 전제로 고른 placeholder였는데, 그 뒤 실제로
    # 같은 이름의 Agent가 정규 시드에 추가돼(AGENT_SPECS, 사업/기획
    # 업무 처리 파이프라인) 전제가 깨졌다 — 완전히 가상의 이름으로
    # 바꿔 "후보 없음" 테스트 의도를 그대로 지킨다.
    _login(client, "manager1", "managerpass")
    brief_id = client.post("/api/briefs", json={"raw_text": role_name}).json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": role_name, "required_skills": []}]},
    )
    return resp.json()["tasks"][0]["id"]


def test_deterministic_success_response_shape_unchanged(client_and_conn):
    """정확히 일치하는 후보가 있으면 LLM 모듈을 아예 거치지 않고 기존
    그대로 동작 — 새 키(match_mode)만 추가된다."""
    client, _conn = client_and_conn
    task_id = _make_task_no_candidates(client, "모바일 개발자")
    client.post(
        "/api/human-pool",
        json={"name": "박도전", "role": "모바일 개발자", "skill_tags": []},
    )
    with patch("hair_platform.match_llm_judge.judge_candidates") as mock_judge:
        resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generate_new"] is False
    assert body["match_mode"] == "deterministic"
    assert len(body["top"]) == 1
    assert body["top"][0]["rationale"] is None
    mock_judge.assert_not_called()


def test_deterministic_matches_via_role_name_substring(client_and_conn):
    """사용자 보고(2026-11-02, "프론트엔드 개발 찾기가 안 되는 원인
    분석") 재현 + 수정 확인 — Task role_name "프론트엔드 개발"과 Agent
    role_name "프론트엔드 개발자"(글자 "자" 하나 차이)가 부분일치로
    LLM 호출 없이 바로 매칭돼야 한다. 기본 시드에 이미 같은 이름의
    Agent가 있을 수 있어(레퍼런스 Agent 22개), id가 아니라 role_name
    으로 포함 여부를 확인한다."""
    client, conn = client_and_conn
    task_id = _make_task_no_candidates(client, "프론트엔드 개발 전용 테스트")
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) "
        "VALUES ('프론트엔드 개발 전용 테스트자', '[]', 'active')"
    ).lastrowid
    conn.commit()

    with patch("hair_platform.match_llm_judge.judge_candidates") as mock_judge:
        resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generate_new"] is False
    assert body["match_mode"] == "deterministic"
    assert any(t["candidate"]["id"] == agent_id for t in body["top"])
    mock_judge.assert_not_called()


def test_generate_new_without_use_llm_judge_never_calls_judge(client_and_conn):
    client, _conn = client_and_conn
    task_id = _make_task_no_candidates(client)
    with patch("hair_platform.match_llm_judge.judge_candidates") as mock_judge:
        resp = client.post(f"/api/tasks/{task_id}/match")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generate_new"] is True
    assert body["match_mode"] == "deterministic"
    mock_judge.assert_not_called()


def test_use_llm_judge_true_calls_judge_and_uses_result(client_and_conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, conn = client_and_conn
    # "콘텐츠 카피라이팅"이 이제 실제 seed된 역할이라(AGENT_SPECS) 그
    # 이름을 그대로 쓰면 결정론적 매칭이 먼저 완전일치를 찾아버린다 —
    # 의도(결정론적 매칭은 실패하고 LLM judge로 넘어가야 함)를 지키려고
    # 다른 이름을 쓴다.
    task_id = _make_task_no_candidates(client, "홍보물 작성 전문가")
    agent_id = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) "
        "VALUES ('카피라이팅 에이전트', '[]', 'active')"
    ).lastrowid
    conn.commit()

    fake_response = json.dumps(
        {
            "recommendations": [
                {
                    "candidate_id": agent_id,
                    "candidate_type": "agent",
                    "score": 0.85,
                    "rationale": "카피라이팅 업무와 일치",
                }
            ]
        }
    )
    with patch("hair_platform.llm.call_llm", return_value=fake_response):
        resp = client.post(
            f"/api/tasks/{task_id}/match", params={"use_llm_judge": "true"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generate_new"] is False
    assert body["match_mode"] == "llm_judge"
    assert len(body["top"]) == 1
    assert body["top"][0]["candidate"]["id"] == agent_id
    assert body["top"][0]["rationale"] == "카피라이팅 업무와 일치"


def test_use_llm_judge_true_but_llm_also_fails_stays_generate_new(
    client_and_conn, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client, _conn = client_and_conn
    task_id = _make_task_no_candidates(client)
    with patch(
        "hair_platform.llm.call_llm", side_effect=RuntimeError("boom")
    ):
        resp = client.post(
            f"/api/tasks/{task_id}/match", params={"use_llm_judge": "true"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generate_new"] is True
    assert body["match_mode"] == "llm_judge_failed"


def test_use_llm_judge_true_is_a_noop_when_deterministic_already_succeeded(
    client_and_conn,
):
    """결정론적 매칭이 이미 성공하면(generate_new=False) use_llm_judge를
    같이 보내도 LLM을 호출하지 않는다 — 불필요한 비용 지출 방지."""
    client, _conn = client_and_conn
    task_id = _make_task_no_candidates(client, "모바일 개발자")
    client.post(
        "/api/human-pool",
        json={"name": "박도전", "role": "모바일 개발자", "skill_tags": []},
    )
    with patch("hair_platform.llm.call_llm") as mock_call:
        resp = client.post(
            f"/api/tasks/{task_id}/match", params={"use_llm_judge": "true"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["match_mode"] == "deterministic"
    mock_call.assert_not_called()
