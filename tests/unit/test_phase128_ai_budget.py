"""AI 사용 한도(2026-11-25, P2-14 — `ai_budget.py`)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import ai_budget, data_admin, knowledge_access, llm, llm_usage


def _ids(conn):
    return {r["username"]: r["id"] for r in conn.execute("SELECT id, username FROM users")}


def _priced_usage(conn, user_id, usd):
    """단가 $1/1M 입력 토큰인 모델로 `usd`만큼 쓴 기록."""
    conn.execute("INSERT OR IGNORE INTO model_registry (provider, name, price_input_per_1m, price_output_per_1m) "
                 "VALUES ('openai', 'budget-model', 1.0, 0.0)")
    conn.execute("INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, user_id) "
                 "VALUES ('openai', 'budget-model', ?, 0, ?)", (int(usd * 1_000_000), user_id))
    conn.commit()


def test_defaults_change_nothing(client_and_conn):
    _client, conn = client_and_conn
    assert ai_budget.get(conn) == ai_budget.DEFAULTS
    ai_budget.check(_ids(conn)["viewer1"])  # 한도·제한 없음 — 그대로 통과


def test_user_limit_blocks_non_admin_but_not_admin(client_and_conn):
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"user_monthly_usd": 5})
    _priced_usage(conn, ids["member1"], 6)
    _priced_usage(conn, ids["admin1"], 6)
    with pytest.raises(ai_budget.BudgetExceeded, match="내 AI 비용 한도"):
        ai_budget.check(ids["member1"])
    ai_budget.check(ids["admin1"])  # admin은 사용자별 한도에서 빠진다
    ai_budget.check(ids["manager1"])  # 쓴 게 없는 사람은 그대로


def test_org_limit_applies_to_everyone_including_admin(client_and_conn):
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"org_monthly_usd": 10})
    _priced_usage(conn, ids["member1"], 4)
    ai_budget.check(ids["admin1"])
    _priced_usage(conn, None, 7)  # 요청자 모르는 사용도 조직 합계에는 들어간다
    with pytest.raises(ai_budget.BudgetExceeded, match="조직 전체"):
        ai_budget.check(ids["admin1"])


def test_unpriced_local_models_do_not_count(client_and_conn):
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"user_monthly_usd": 0.01})
    conn.execute("INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, user_id) "
                 "VALUES ('ollama', 'qwen-local', 50000000, 50000000, ?)", (ids["member1"],))
    conn.commit()
    ai_budget.check(ids["member1"])  # 단가 없는 모델(로컬) — 0원


def test_viewer_ai_can_be_disabled(client_and_conn):
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"viewer_ai_allowed": False})
    with pytest.raises(ai_budget.BudgetExceeded, match="viewer"):
        ai_budget.check(ids["viewer1"])
    ai_budget.check(ids["member1"])


def test_call_llm_is_blocked_before_calling_the_model_and_usage_gets_user(client_and_conn):
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"user_monthly_usd": 1})
    _priced_usage(conn, ids["member1"], 2)
    with patch.object(llm, "_call_openai", return_value="답") as called:
        with knowledge_access.as_viewer(ids["member1"]), pytest.raises(ai_budget.BudgetExceeded):
            llm.call_llm("openai", "budget-model", "s", "p")
        assert not called.called  # 모델을 부르지 않았다
        with knowledge_access.as_viewer(ids["manager1"]):
            assert llm.call_llm("openai", "budget-model", "s", "p") == "답"
    # 사용 기록에 요청자가 남는다
    with knowledge_access.as_viewer(ids["manager1"]):
        llm_usage.record("openai", "budget-model", 10, 5)
    row = conn.execute("SELECT user_id FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == ids["manager1"]


def test_validation_api_rbac_and_reset(client_and_conn):
    client, conn = client_and_conn
    ids = _ids(conn)
    with pytest.raises(ValueError):
        ai_budget.validate({"user_monthly_usd": -1})
    with pytest.raises(ValueError):
        ai_budget.validate({"warn_ratio": 5})
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/ai-budget").status_code == 200
    assert client.put("/api/ai-budget", json={"user_monthly_usd": 3}).status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.put("/api/ai-budget", json={"user_monthly_usd": 3, "org_monthly_usd": 50})
    assert r.status_code == 200 and r.json()["config"]["user_monthly_usd"] == 3
    assert client.put("/api/ai-budget", json={"org_monthly_usd": "많이"}).status_code == 400
    _priced_usage(conn, ids["member1"], 1.5)
    st = client.get("/api/ai-budget").json()
    assert st["org_cost"] == pytest.approx(1.5) and st["users"][0]["username"] == "member1"
    assert st["users"][0]["ratio"] == pytest.approx(0.5)
    data_admin.reset_to_factory_state(conn)  # 한도 설정은 남고 수정자만 비운다
    assert ai_budget.get(conn)["user_monthly_usd"] == 3
    assert conn.execute("SELECT updated_by FROM ai_budget_config").fetchone()[0] is None


@pytest.fixture(autouse=True)
def _reset_cache():
    ai_budget._CACHE = None
    yield
    ai_budget._CACHE = None


def test_unpriced_model_call_is_allowed_even_over_limit(client_and_conn):
    """한도에 닿아도 금액이 들지 않는 모델(로컬 Ollama·단가 미등록)은 부를 수 있다."""
    _client, conn = client_and_conn
    ids = _ids(conn)
    ai_budget.save(conn, ids["admin1"], {"user_monthly_usd": 1, "org_monthly_usd": 1})
    _priced_usage(conn, ids["member1"], 2)
    with pytest.raises(ai_budget.BudgetExceeded):
        ai_budget.check(ids["member1"], "openai", "budget-model")
    ai_budget.check(ids["member1"], "ollama", "qwen-local")
    ai_budget.check(ids["member1"], "openai", "unregistered-model")  # 단가 미등록 = 0원
    with pytest.raises(ai_budget.BudgetExceeded):
        ai_budget.check(ids["member1"])  # 어떤 모델인지 모르면 한도 적용(닫힌 쪽)
