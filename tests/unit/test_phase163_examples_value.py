"""5단계(2026-11-27, AI Native 분석 — 2번 학습 루프 3단계 "좋은 결과 예시", 11번 "비용 대비 가치").

- 예시 후보: 사람이 승인·1차 시도·점검 미달 없음만, 외부 데이터 실행·자동 승인·미달 결과는 제외, 단계당 후보 3개
- 예시로 쓰면 두 엔진 공통 지시문(`lessons_prompt`)에 형식 참고용으로 붙고, 단계당 2개까지, 끄기·되돌리기
- 사람 1시간 비용(admin)으로 건당 가치·가치 배수·순절감, AI 비용이 가치보다 크면 신호"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from hair_platform import process_learning, process_metrics
from hair_platform import pipeline_runtime as pr

OUT = "## 검토 의견\n" + "계약 조항별 위험도와 수정 제안을 표로 정리했습니다. " * 5


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _t(minutes_ago: float) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def tpl(client_and_conn):
    client, conn = client_and_conn
    admin = _uid(conn, "admin1")
    conn.execute("INSERT INTO agent_pool (role_name, skill_tags, status) VALUES ('검토자', '[]', 'active')")
    tid = pr.create_pipeline_template(conn, admin, "계약 검토")
    sid = pr.add_stage(conn, admin, tid, "검토 의견", "single", [("검토자", "")], requires_human_approval=True)
    conn.commit()
    return client, conn, admin, tid, sid


def _row(conn, tid, sid, admin, status="approved", attempt=1, gate=None, output=OUT, external=False, start=60, end=50):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name) VALUES (?, 'r')", (bid,)).lastrowid
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status, created_at, finished_at) "
                       "VALUES (?, ?, ?, 'completed', ?, ?)", (tid, task, admin, _t(start), _t(end))).lastrowid
    rs = conn.execute("INSERT INTO pipeline_run_stages (pipeline_run_id, stage_id, attempt, status, output, gate_scores, started_at, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (run, sid, attempt, status, output, json.dumps(gate or {"A": {"passed": True}}), _t(start), _t(end))).lastrowid
    if external:
        conn.execute("INSERT INTO tool_calls (tool_name, tool_label, ok, pipeline_run_id, external) VALUES ('mail', '메일', 1, ?, 1)", (run,))
    conn.commit()
    return run, rs


def test_example_candidates_are_filtered(tpl):
    _client, conn, admin, tid, sid = tpl
    _, good = _row(conn, tid, sid, admin)
    _row(conn, tid, sid, admin, status="passed")  # 자동 승인(사람 결재 없음)
    _row(conn, tid, sid, admin, attempt=2)  # 다시 써서 통과
    _row(conn, tid, sid, admin, gate={"numeric_grounding": {"passed": False, "not_measured": False}})
    _row(conn, tid, sid, admin, external=True)  # 고객 메일을 읽은 실행
    _row(conn, tid, sid, admin, output="짧음")
    c = process_learning.list_examples(conn, tid)["candidates"]
    assert [x["run_stage_id"] for x in c] == [good]
    for _ in range(4):
        _row(conn, tid, sid, admin)
    assert len(process_learning.list_examples(conn, tid)["candidates"]) == process_learning.EXAMPLE_CANDIDATES_PER_STAGE


def test_use_example_reaches_prompt_with_limits_and_revert(tpl):
    client, conn, admin, tid, sid = tpl
    for _ in range(3):
        _row(conn, tid, sid, admin)
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    cands = client.get(f"/api/process-metrics/templates/{tid}/examples").json()["candidates"]
    assert client.post(f"/api/process-examples/{cands[0]['id']}/use").status_code == 200
    assert client.post(f"/api/process-examples/{cands[1]['id']}/use").status_code == 200
    assert client.post(f"/api/process-examples/{cands[2]['id']}/use").status_code == 400  # 단계당 2개
    prompt = process_learning.lessons_prompt(conn, sid)
    assert "사람이 승인한 좋은 결과 예시" in prompt and prompt.count("--- 예시") == 2 and "옮겨 쓰지 마세요" in prompt
    assert client.post(f"/api/process-examples/{cands[2]['id']}/dismiss").status_code == 200
    assert client.post(f"/api/process-examples/{cands[2]['id']}/use").status_code == 400  # 무시한 것은 다시 못 씀
    ch = next(c for c in client.get(f"/api/process-metrics/templates/{tid}/changes").json() if c["kind"] == "example_on")
    assert ch["revertible"] and client.post(f"/api/process-changes/{ch['id']}/revert").status_code == 200
    assert process_learning.lessons_prompt(conn, sid).count("--- 예시") == 1
    assert client.post("/api/process-examples/1/bogus").status_code == 404


def test_value_per_case_ratio_and_signal(tpl):
    client, conn, admin, tid, sid = tpl
    for i in range(3):
        _, rs = _row(conn, tid, sid, admin, start=60 + i, end=50 + i)
    conn.execute("INSERT OR IGNORE INTO model_registry (provider, name, price_input_per_1m, price_output_per_1m) VALUES ('anthropic', 'm', 1000000, 1000000)")
    conn.execute("UPDATE model_registry SET price_input_per_1m = 1000000, price_output_per_1m = 1000000 WHERE provider = 'anthropic' AND name = 'm'")
    task_ids = [r[0] for r in conn.execute("SELECT task_id FROM pipeline_runs WHERE pipeline_template_id = ?", (tid,))]
    for t in task_ids:  # 실행마다 토큰 20개 → $20
        conn.execute("INSERT INTO llm_usage (created_at, provider, model, prompt_tokens, completion_tokens, task_id) VALUES (?, 'anthropic', 'm', 10, 10, ?)",
                     (_t(55), t))
    conn.commit()
    process_metrics.set_baseline(conn, admin, tid, 30)  # 30분
    client.post("/login", json={"username": "manager1", "password": "managerpass"})
    assert client.put("/api/process-metrics/hourly-cost", json={"hourly_cost": 40}).status_code == 403
    client.post("/login", json={"username": "admin1", "password": "adminpass"})
    assert client.put("/api/process-metrics/hourly-cost", json={"hourly_cost": -1}).status_code == 400
    assert client.put("/api/process-metrics/hourly-cost", json={"hourly_cost": "x"}).status_code == 400
    assert client.put("/api/process-metrics/hourly-cost", json={"hourly_cost": 30}).status_code == 200
    s = process_metrics.template_detail(conn, tid, 30)["summary"]
    assert s["value_per_case"] == pytest.approx(15.0)  # 0.5시간 × $30
    assert s["cost_per_run"] == pytest.approx(20.0)
    assert s["value_ratio"] == pytest.approx(0.75) and s["net_value"] == pytest.approx(-15.0)  # (15-20)×3
    assert any(x["kind"] == "cost_over_value" for x in s["signals"])
    o = client.get("/api/process-metrics").json()
    assert o["total"]["hourly_cost"] == 30 and o["total"]["net_value"] == pytest.approx(-15.0)
    client.put("/api/process-metrics/hourly-cost", json={"hourly_cost": None})
    assert process_metrics.template_detail(conn, tid, 30)["summary"]["value_per_case"] is None
