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
