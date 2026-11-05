"""Phase 62 — 콘텐츠 타입별 pluggable 검증기 구조(사용자 요청,
2026-11-05, "업무 배정, 파이프라인, crew 팀 구성 등을 이용해 로드러너
수준의 게임... 개선 계획을 수립해줘" → 로드맵 5번 "콘텐츠 타입별
pluggable 검증기 구조... 다음 개선 진행해줘").

설계 조사 중 이미 배포된 Gate 구문검사(로드맵 4번)의 실제 버그를
발견했다 — `node --check`를 `.html` 확장자 파일에 돌리면 내용과
무관하게 항상 `ERR_UNKNOWN_FILE_EXTENSION`으로 실패한다(직접 실행해
확인). 이 파일은 그 버그 수정(인라인 `<script>`만 `.js`로 추출해
검사)과, `content_type="html"`로 명시 요청했을 때(펜스 없는 raw
전체 HTML 문서가 정상 케이스) 검증 공백을 메우는 신규 보조 검증기를
검증한다.

**비용/네트워크 발생 방지**: LLM 호출 없음(결정론적 구문 검사만).
Node 관련 테스트는 이 저장소 전체의 "Node 있으면 쓰고 없으면 조용히
건너뛰기" 설계를 그대로 따라 `shutil.which("node")`가 없으면 skip —
모킹이 아니라 **실제 node 바이너리로 돌려야** 로드맵 4번에서 놓친
버그(모킹만 해서 디스패치 로직만 확인하고 실제 CLI 호출 모양은
검증 안 함)가 재발하지 않는다."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch

import pytest

from hair_platform import agent_evaluation, content_validators

_HAS_NODE = shutil.which("node") is not None
_skip_no_node = pytest.mark.skipif(not _HAS_NODE, reason="node가 PATH에 없음")

_VALID_SCRIPT_HTML = (
    "<!DOCTYPE html>\n<html><head></head><body>\n"
    "<script>\nfunction f() { return 1; }\n</script>\n"
    "</body></html>"
)
_BROKEN_SCRIPT_HTML = (
    "<!DOCTYPE html>\n<html><body>\n"
    "<script>\nfunction f( { broken\n</script>\n"
    "</body></html>"
)
_NO_SCRIPT_HTML = "<!DOCTYPE html>\n<html><body><p>안녕</p></body></html>"


# ---------- _extract_inline_scripts() ----------


def test_extract_inline_scripts_multiple():
    html = "<script>a();</script><p>x</p><script>b();</script>"
    assert agent_evaluation._extract_inline_scripts(html) == ["a();", "b();"]


def test_extract_inline_scripts_none():
    assert agent_evaluation._extract_inline_scripts("<p>텍스트만</p>") == []


def test_extract_inline_scripts_external_src_excluded():
    html = '<script src="app.js"></script><script>real();</script>'
    assert agent_evaluation._extract_inline_scripts(html) == ["real();"]


# ---------- compute_code_syntax_check() html 펜스 — 실제 node, 버그 회귀 ----------


@_skip_no_node
def test_html_fence_with_valid_script_passes_with_real_node():
    content = f"```html\n{_VALID_SCRIPT_HTML}\n```"
    result = agent_evaluation.compute_code_syntax_check(content)
    # 수정 전엔 .html 확장자 때문에 무조건 fail이었다 — 핵심 회귀 테스트.
    assert result["status"] == "pass"
    assert result["checked_blocks"] == 1


@_skip_no_node
def test_html_fence_with_broken_script_fails_with_real_node():
    content = f"```html\n{_BROKEN_SCRIPT_HTML}\n```"
    result = agent_evaluation.compute_code_syntax_check(content)
    assert result["status"] == "fail"
    assert result["errors"] and "html" in result["errors"][0]


@_skip_no_node
def test_html_fence_without_script_tag_is_not_measured():
    content = f"```html\n{_NO_SCRIPT_HTML}\n```"
    result = agent_evaluation.compute_code_syntax_check(content)
    # 이전 버그라면 .html 파일을 그대로 node에 넘겨 무조건 fail이었다.
    assert result["checked_blocks"] == 0
    assert result["status"] == "not_measured"


# ---------- compute_raw_html_syntax_check() ----------


@_skip_no_node
def test_raw_html_check_valid_script_passes():
    result = agent_evaluation.compute_raw_html_syntax_check(_VALID_SCRIPT_HTML)
    assert result["status"] == "pass"
    assert result["passed"] is True
    assert result["checked_blocks"] == 1


@_skip_no_node
def test_raw_html_check_broken_script_fails():
    result = agent_evaluation.compute_raw_html_syntax_check(_BROKEN_SCRIPT_HTML)
    assert result["status"] == "fail"
    assert result["passed"] is False


def test_raw_html_check_no_script_is_not_measured():
    result = agent_evaluation.compute_raw_html_syntax_check(_NO_SCRIPT_HTML)
    assert result["status"] == "not_measured"
    assert result["score"] is None


def test_raw_html_check_node_unavailable_is_not_measured_without_subprocess_call():
    with patch.object(agent_evaluation.shutil, "which", return_value=None):
        with patch.object(agent_evaluation.subprocess, "run") as mock_run:
            result = agent_evaluation.compute_raw_html_syntax_check(_VALID_SCRIPT_HTML)
    mock_run.assert_not_called()
    assert result["status"] == "not_measured"


# ---------- content_validators.validate_by_content_type() ----------


def test_validate_html_no_fence_falls_back_to_raw_check():
    # `_SUPPLEMENTARY_VALIDATORS`는 모듈 로드 시점에 함수 객체를 직접
    # 담아두는 `NATIVE_TOOL_FACTORIES`(pool_registry.py)와 같은 패턴이라
    # `agent_evaluation.compute_raw_html_syntax_check`를 patch해도 이미
    # 캡처된 참조는 안 바뀐다 — 레지스트리 자체를 patch.dict한다.
    mock_raw = MagicMock(
        return_value={"score": 1.0, "status": "pass", "checked_blocks": 1}
    )
    with patch.dict(content_validators._SUPPLEMENTARY_VALIDATORS, {"html": mock_raw}):
        result = content_validators.validate_by_content_type("html", _NO_SCRIPT_HTML)
    mock_raw.assert_called_once_with(_NO_SCRIPT_HTML)
    assert result["status"] == "pass"


@_skip_no_node
def test_validate_html_with_fence_present_does_not_call_raw_fallback():
    content = "```html\n" + _VALID_SCRIPT_HTML + "\n```"
    mock_raw = MagicMock()
    with patch.dict(content_validators._SUPPLEMENTARY_VALIDATORS, {"html": mock_raw}):
        result = content_validators.validate_by_content_type("html", content)
    mock_raw.assert_not_called()
    assert result["checked_blocks"] >= 1


def test_validate_code_content_type_uses_base_fence_scan_only():
    content = "```python\ndef f():\n    return 1\n```"
    direct = agent_evaluation.compute_code_syntax_check(content)
    via_registry = content_validators.validate_by_content_type("code", content)
    assert via_registry == direct


def test_validate_text_content_type_matches_roadmap4_behavior():
    content = "```python\ndef g(:\n```"
    direct = agent_evaluation.compute_code_syntax_check(content)
    via_registry = content_validators.validate_by_content_type("text", content)
    assert via_registry == direct


def test_validate_markdown_content_type_falls_back_to_base_when_unregistered():
    result = content_validators.validate_by_content_type("markdown", "그냥 텍스트")
    assert result["status"] == "not_measured"


def test_validate_unknown_content_type_falls_back_to_base():
    content = "```python\ndef f():\n    return 1\n```"
    direct = agent_evaluation.compute_code_syntax_check(content)
    via_registry = content_validators.validate_by_content_type("bogus", content)
    assert via_registry == direct


# ---------- deliverables.run_adhoc_crew_job() 종단 — content_type="html" ----------


def _crew_output(raw: str, task_raws: list[str]):
    from crewai.crews.crew_output import CrewOutput
    from crewai.tasks.task_output import TaskOutput

    tasks_output = [
        TaskOutput(description=f"task-{i}", agent="agent", raw=r)
        for i, r in enumerate(task_raws)
    ]
    return CrewOutput(raw=raw, tasks_output=tasks_output)


def _make_active_agent(conn, role_name):
    cur = conn.execute(
        "INSERT INTO agent_pool (role_name, skill_tags, status) VALUES (?, '[]', 'active')",
        (role_name,),
    )
    conn.commit()
    return cur.lastrowid


@_skip_no_node
def test_run_adhoc_crew_job_html_content_type_measures_unfenced_document(
    client_and_conn,
):
    client, conn = client_and_conn
    resp = client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert resp.status_code == 200, resp.text
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin1'").fetchone()[
        "id"
    ]

    resp = client.post("/api/briefs", json={"raw_text": "역할"})
    brief_id = resp.json()["id"]
    resp = client.post(
        f"/api/briefs/{brief_id}/tasks",
        json={"tasks": [{"role_name": "역할", "required_skills": []}]},
    )
    task_id = resp.json()["tasks"][0]["id"]

    agent1 = _make_active_agent(conn, "프론트엔드 개발자")

    from hair_platform import deliverables

    job = deliverables.start_adhoc_crew_execution(
        conn, task_id, admin_id, [agent1], allow_supervisor_override=True
    )

    def _kickoff_capture(self, *args, **kwargs):
        return _crew_output("최종", [_VALID_SCRIPT_HTML])

    with patch("crewai.Crew.kickoff", new=_kickoff_capture):
        deliverables.run_adhoc_crew_job(
            conn,
            job["job_by_agent_id"],
            task_id,
            admin_id,
            [agent1],
            ["작업1"],
            "sequential",
            None,
            None,
            None,
            content_type="html",
        )

    row = conn.execute(
        "SELECT ae.gate_scores FROM deliverables d "
        "JOIN agent_evaluations ae ON ae.deliverable_id = d.id "
        "WHERE d.task_id = ?",
        (task_id,),
    ).fetchone()
    import json

    gate_scores = json.loads(row["gate_scores"])
    # content_type="text"(기본값)였다면 펜스가 없는 이 산출물은
    # not_measured였을 것 — html 보조 검증기가 실제로 측정했는지 확인.
    assert gate_scores["code_syntax"]["status"] == "pass"
    assert gate_scores["code_syntax"]["checked_blocks"] == 1
