"""업무 설명으로 파이프라인 초안(2026-11-27, AI Native 분석 3단계 — 3번 자연어 프로세스 설계).

- LLM 초안을 등록된 역할·도구 기준으로 정제(모르는 역할 표시, 모르는 도구 버림, 되돌아갈 단계·마지막 사람 승인 보정)
- LLM 실패·깨진 응답이면 규칙 초안
- 사람이 고친 초안으로 만들면 단계·되돌아갈 단계·필수 도구가 그대로 들어가고 변경 기록이 남는다"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hair_platform import pipeline_drafting


@pytest.fixture
def env(client_and_conn):
    client, conn = client_and_conn
    for role in ("계약 검토 전문가", "법무 검토자"):
        conn.execute("INSERT INTO agent_pool (role_name, goal, skill_tags, status) VALUES (?, '계약서 조항 검토', '[]', 'active')", (role,))
    conn.commit()
    tool = conn.execute("SELECT name FROM tool_pool WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()[0]
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    return client, conn, tool


def _llm_answer(tool):
    return "초안입니다:\n" + json.dumps({
        "name": "계약서 1차 검토", "domain": "legal", "description": "계약서 위험 조항 검토",
        "stages": [
            {"name": "조항 추출", "mode": "single", "roles": ["계약 검토 전문가"], "instructions": "조항을 표로 정리",
             "output_content_type": "markdown", "gate_ids": ["A", "C", "Z"], "required_tools": [tool, "없는도구"],
             "requires_human_approval": False, "return_to": None},
            {"name": "위험 검토", "mode": "single", "roles": ["가상의 변호사"], "instructions": "위험 조항 표시",
             "output_content_type": "pdf", "gate_ids": [], "requires_human_approval": False, "return_to": 0},
            {"name": "요약", "mode": "single", "roles": ["법무 검토자"], "instructions": "결론 요약",
             "requires_human_approval": False, "return_to": 5},
        ],
    }, ensure_ascii=False)


def test_llm_draft_is_sanitized(env):
    client, _conn, tool = env
    with patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")), \
         patch("hair_platform.llm.call_llm", return_value=_llm_answer(tool)):
        r = client.post("/api/pipeline-templates/draft", json={"description": "계약서를 받으면 위험 조항을 찾아 검토 의견을 쓰는 업무"})
    assert r.status_code == 200
    body = r.json()
    d = body["draft"]
    assert body["source"] == "llm" and d["domain"] == "legal" and len(d["stages"]) == 3
    s0, s1, s2 = d["stages"]
    assert s0["gate_ids"] == ["A", "C"] and s0["required_tools"] == [tool]
    assert s1["missing_roles"] == ["가상의 변호사"] and s1["output_content_type"] == "markdown" and s1["return_to"] == 0
    assert s2["return_to"] is None  # 뒤나 없는 단계로는 못 돌아간다
    assert s2["requires_human_approval"] is True and "안전" in s2["why_human"]  # 마지막은 사람 승인
    assert "계약 검토 전문가" in body["available_roles"]


def test_llm_failure_falls_back_to_rule_draft_and_short_text_rejected(env):
    client, _conn, _tool = env
    with patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")), \
         patch("hair_platform.llm.call_llm", side_effect=RuntimeError("down")):
        body = client.post("/api/pipeline-templates/draft", json={"description": "계약서 조항을 검토해 위험을 알려 주는 업무"}).json()
    assert body["source"] == "rule" and "기본 초안" in body["note"]
    assert [s["name"] for s in body["draft"]["stages"]] == ["초안 작성", "검토", "최종 확인"]
    assert body["draft"]["stages"][1]["return_to"] == 0 and body["draft"]["stages"][2]["requires_human_approval"]
    assert body["draft"]["stages"][0]["roles"][0] in ("계약 검토 전문가", "법무 검토자")
    with patch("hair_platform.llm.call_llm", return_value="JSON 아님"), \
         patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")):
        assert client.post("/api/pipeline-templates/draft", json={"description": "계약서 조항을 검토해 위험을 알려 주는 업무"}).json()["source"] == "rule"
    assert client.post("/api/pipeline-templates/draft", json={"description": "짧음"}).status_code == 400


def test_create_from_edited_draft(env):
    client, conn, tool = env
    with patch("hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")), \
         patch("hair_platform.llm.call_llm", return_value=_llm_answer(tool)):
        d = client.post("/api/pipeline-templates/draft", json={"description": "계약서를 받으면 위험 조항을 찾아 검토 의견을 쓰는 업무"}).json()["draft"]
    assert client.post("/api/pipeline-templates/from-draft", json=d).status_code == 400  # 지어낸 역할이 남아 있음
    d["stages"][1]["roles"] = ["법무 검토자"]
    r = client.post("/api/pipeline-templates/from-draft", json=d)
    assert r.status_code == 201
    tid = r.json()["id"]
    rows = conn.execute("SELECT id, name, requires_human_approval, on_fail_return_stage_id, required_tools FROM pipeline_stages "
                        "WHERE pipeline_template_id = ? ORDER BY seq_order", (tid,)).fetchall()
    assert [r["name"] for r in rows] == ["조항 추출", "위험 검토", "요약"]
    assert rows[1]["on_fail_return_stage_id"] == rows[0]["id"] and rows[2]["requires_human_approval"] == 1
    assert json.loads(rows[0]["required_tools"]) == [tool]
    kinds = [c["kind"] for c in client.get(f"/api/process-metrics/templates/{tid}/changes").json()]
    assert "template_created" in kinds
    # 이제 비슷한 기존 파이프라인으로 나온다
    assert any(s["template_id"] == tid for s in pipeline_drafting.similar_templates(conn, "계약서 1차 검토 위험 조항"))


def test_create_validation_and_permissions(env):
    client, _conn, _tool = env
    base = {"name": "x", "stages": [{"name": "a", "roles": ["법무 검토자"]}]}
    assert client.post("/api/pipeline-templates/from-draft", json=base).status_code == 400  # 단계 1개
    two = {"name": "x", "stages": [{"name": "a", "roles": ["법무 검토자"]}, {"name": "b", "roles": ["법무 검토자"], "required_tools": ["없는도구"]}]}
    assert client.post("/api/pipeline-templates/from-draft", json=two).status_code == 400
    client.post("/login", json={"username": "member1", "password": "memberpass"})
    assert client.post("/api/pipeline-templates/draft", json={"description": "계약서 조항을 검토하는 업무입니다"}).status_code == 403
