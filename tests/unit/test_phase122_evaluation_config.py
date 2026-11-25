"""평가 기준 설정(2026-11-25, `evaluation_config.py`) — admin이 화면에서 Gate별 기준·SLA·추론 표지·감점을 바꾼다."""

from __future__ import annotations

import pytest

from hair_platform import agent_evaluation, data_admin, evaluation_config


def _login_admin(client):
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200


def test_defaults_match_previous_hardcoded_values(client_and_conn):
    _client, conn = client_and_conn
    cfg = evaluation_config.get(conn)
    assert cfg["gate_default"] == agent_evaluation.DEFAULT_GATE_THRESHOLD == 0.7
    assert (cfg["sla_p95_ms"], cfg["sla_p99_ms"]) == (30000.0, 60000.0)
    assert cfg["reasoning_markers"] == agent_evaluation.KOREAN_REASONING_MARKERS
    s = agent_evaluation._current_settings()
    assert s["sla"].p95_ms == 30000.0 and "근거" in s["explainability"].reasoning_markers
    assert "따라서" in s["explainability"].reasoning_markers  # SDK 기본 표지는 그대로


def test_validation_rejects_bad_values(client_and_conn):
    _client, conn = client_and_conn
    for bad in ({"gate_default": 1.5}, {"gates": {"Z": 0.5}}, {"gates": {"A": "x"}},
                {"sla_p95_ms": 50000, "sla_p99_ms": 40000}, {"sla_p95_ms": 10},
                {"reasoning_markers": ["가" * 21]}, {"scope_violation_penalty": -0.1}):
        with pytest.raises(ValueError):
            evaluation_config.validate(bad)


def test_gate_threshold_change_applies_to_next_evaluation(client_and_conn):
    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    text = "3분기 매출은 55억 원으로 목표 대비 105.8%이므로 달성했다."
    before = agent_evaluation.run_and_score("분석가", lambda: text)["gate_scores"]["A"]
    evaluation_config.save(conn, admin, {"gates": {"A": 1.0}})
    strict = agent_evaluation.run_and_score("분석가", lambda: text)["gate_scores"]["A"]
    evaluation_config.save(conn, admin, {"gates": {"A": 0.0}})
    loose = agent_evaluation.run_and_score("분석가", lambda: text)["gate_scores"]["A"]
    assert strict["score"] == before["score"] == loose["score"]  # 점수는 같고 판정만 바뀐다
    if strict["score"] is not None and strict["score"] < 1.0:
        assert strict["passed"] is False
    assert loose["passed"] is True
    row = conn.execute("SELECT action FROM audit_log WHERE action = 'evaluation_config_updated'").fetchone()
    assert row is not None


def test_api_admin_only_targets_roundtrip_and_reset(client_and_conn):
    client, conn = client_and_conn
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/admin/evaluation-config").status_code == 403
    _login_admin(client)
    r = client.put("/api/admin/evaluation-config", json={"gate_default": 0.6, "gates": {"A": 0.8},
                                                          "sla_p95_ms": 120000, "sla_p99_ms": 300000,
                                                          "reasoning_markers": "근거\n결론적으로"})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["gates"] == {"A": 0.8} and cfg["reasoning_markers"] == ["근거", "결론적으로"]
    assert client.put("/api/admin/evaluation-config", json={"gate_default": 3}).status_code == 400
    exported = client.get("/api/admin/evaluation-config/targets.json").json()
    assert exported == {"gate_default": 0.6, "gates": {"A": 0.8, "B": 0.6, "C": 0.6, "D": 0.6, "E": 0.6, "F": 0.6, "G": 0.6}}
    r = client.post("/api/admin/evaluation-config/targets", json={"gates": {"G": 0.9}, "gate_default": 0.65})
    assert r.status_code == 200 and r.json()["config"]["gates"] == {"G": 0.9}
    assert r.json()["config"]["sla_p95_ms"] == 120000  # targets는 Gate 기준만 바꾼다
    assert client.post("/api/admin/evaluation-config/reset").json()["config"]["gates"] == {}


def test_factory_reset_keeps_criteria_but_not_dangling_user(client_and_conn):
    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    evaluation_config.save(conn, admin, {"gates": {"A": 0.9}})
    data_admin.reset_to_factory_state(conn)
    assert evaluation_config.get(conn)["gates"] == {"A": 0.9}
    assert conn.execute("SELECT updated_by FROM evaluation_config").fetchone()[0] is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# 모델 provider·파이프라인별 덮어쓰기(2026-11-25, P1-10)
def test_override_validation_and_precedence():
    cfg = evaluation_config.validate({"gate_default": 0.7, "overrides": [
        {"provider": "ollama", "gates": {"D": 0.0}},
        {"pipeline_template_id": 5, "gates": {"A": 0.9, "D": 0.4}, "gate_default": 0.5},
    ]})
    base = evaluation_config.effective(cfg)
    assert base["thresholds"]["D"] == 0.7 and base["applied"] == []
    local = evaluation_config.effective(cfg, {"ollama"})
    assert local["thresholds"]["D"] == 0.0 and local["thresholds"]["A"] == 0.7
    # 파이프라인이 가장 나중(가장 강함): D는 0.4, 나머지 기본은 0.5
    both = evaluation_config.effective(cfg, {"ollama"}, 5)
    assert both["thresholds"]["D"] == 0.4 and both["thresholds"]["A"] == 0.9 and both["thresholds"]["B"] == 0.5
    assert both["applied"] == ["provider:ollama", "pipeline:5"]
    # 호출 provider가 섞였으면 provider 덮어쓰기는 적용하지 않는다
    assert evaluation_config.effective(cfg, {"ollama", "openai"})["thresholds"]["D"] == 0.7
    for bad in ([{"provider": "gemini", "gates": {"D": 0}}], [{"gates": {"D": 0}}],
                [{"provider": "ollama"}], [{"provider": "ollama", "gates": {"Z": 0}}],
                [{"provider": "ollama", "gates": {"D": 0}}, {"provider": "ollama", "gates": {"A": 0}}]):
        with pytest.raises(ValueError):
            evaluation_config.validate({"overrides": bad})


def test_provider_override_applies_by_the_model_actually_called(client_and_conn):
    """이번 실행이 실제로 ollama만 불렀으면 ollama 덮어쓰기 기준으로 판정한다(점수는 그대로)."""
    from hair_platform import llm_usage

    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    evaluation_config.save(conn, admin, {"gates": {"A": 1.0},
                                         "overrides": [{"provider": "ollama", "gates": {"A": 0.0}}]})
    text = "3분기 매출은 55억 원으로 목표 대비 105.8%이므로 달성했다."

    def local_call():
        llm_usage.record("ollama", "qwen", 10, 20)
        return text

    def cloud_call():
        llm_usage.record("openai", "gpt", 10, 20)
        return text

    assert agent_evaluation.run_and_score("분석가", local_call)["gate_scores"]["A"]["passed"] is True
    cloud = agent_evaluation.run_and_score("분석가", cloud_call)["gate_scores"]["A"]
    if cloud["score"] is not None and cloud["score"] < 1.0:
        assert cloud["passed"] is False  # 전역 기준(1.0) 그대로


def test_pipeline_override_applies_inside_that_pipeline_run(client_and_conn):
    from hair_platform import llm_usage

    _client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    tid = conn.execute("SELECT id FROM pipeline_templates ORDER BY id LIMIT 1").fetchone()[0]
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, 'x', '[]')", (admin,)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, 'x', '[]')", (brief,)).lastrowid
    run = conn.execute("INSERT INTO pipeline_runs (pipeline_template_id, task_id, actor_id, status) "
                       "VALUES (?, ?, ?, 'running')", (tid, task, admin)).lastrowid
    conn.commit()
    evaluation_config.save(conn, admin, {"gates": {"A": 1.0},
                                         "overrides": [{"pipeline_template_id": tid, "gates": {"A": 0.0}}]})
    text = "3분기 매출은 55억 원으로 목표 대비 105.8%이므로 달성했다."
    with llm_usage.scope(pipeline_run_id=run):
        assert agent_evaluation.run_and_score("분석가", lambda: text)["gate_scores"]["A"]["passed"] is True
