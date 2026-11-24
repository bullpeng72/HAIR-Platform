"""보조 작업 모델(2026-11-24, P0-2 응답 속도 — "다음 순서 진행해줘").

언어 교정·수치 문장 교정처럼 짧은 보조 호출만 admin이 정한 작은 모델로 보낸다. 로컬 대형 모델
하나를 모든 호출이 순서대로 기다려, 1초짜리 교정이 앞 요청 뒤에서 타임아웃 나던 문제.
비워 두면 예전처럼 본 작업 모델을 쓴다.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hair_platform import db, language_qa, llm, model_options, model_registry, numeric_check
from hair_platform.auth import Role, create_user

KO = "이 보고서는 3분기 예산 집행 실적을 요약하고 4분기 조치 사항을 정리한 문서다. "


@pytest.fixture(autouse=True)
def _reset_model_cache():
    yield
    # 모듈 전역 캐시(활성 모델·보조 모델)를 빈 DB 기준으로 되돌린다 — 이 파일이 등록한 모델이
    # 다른 테스트의 기본 모델 판별에 새지 않게(실측: test_phase10 gc69가 순서에 따라 실패).
    empty = db.get_connection(":memory:")
    db.init_schema(empty)
    model_options.refresh(empty)


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _admin_with_models(conn):
    admin = create_user(conn, "aux_admin", "adminpass", Role.ADMIN)
    model_registry.create_model(conn, admin, "ollama", "big-35b")
    model_registry.create_model(conn, admin, "ollama", "small-8b")
    return admin


def test_set_get_and_clear_auxiliary_model(conn):
    admin = _admin_with_models(conn)
    assert llm.get_auxiliary_llm(conn) is None and model_options.auxiliary_model() is None
    llm.set_auxiliary_llm(conn, admin, "ollama", "small-8b")
    assert llm.get_auxiliary_llm(conn) == {"provider": "ollama", "model": "small-8b"}
    assert model_options.auxiliary_model() == ("ollama", "small-8b")
    llm.set_auxiliary_llm(conn, admin, None, None)
    assert llm.get_auxiliary_llm(conn) is None and model_options.auxiliary_model() is None
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE action = 'auxiliary_llm_config_updated'")]
    assert len(actions) == 2


def test_only_registered_active_models_accepted(conn):
    admin = _admin_with_models(conn)
    with pytest.raises(ValueError, match="model_not_registered"):
        llm.set_auxiliary_llm(conn, admin, "ollama", "not-installed")


def test_deactivated_model_is_not_used(conn):
    admin = _admin_with_models(conn)
    llm.set_auxiliary_llm(conn, admin, "ollama", "small-8b")
    conn.execute("UPDATE model_registry SET is_active = 0 WHERE name = 'small-8b'")
    conn.commit()
    model_options.refresh(conn)
    assert model_options.auxiliary_model() is None
    assert language_qa.helper_target("ollama", "big-35b") == ("ollama", "big-35b")


def test_language_repair_goes_to_auxiliary_model(conn):
    admin = _admin_with_models(conn)
    llm.set_auxiliary_llm(conn, admin, "ollama", "small-8b")
    calls = []

    def fake_call(provider, model, system, prompt):
        calls.append((provider, model))
        return json.dumps(["3개월 내 파일럿을 완료한다."])

    with patch.object(language_qa, "_call", side_effect=fake_call):
        out = language_qa.repair_output(conn, KO + "\n3个月内 파일럿을 완료한다.", "ollama", "big-35b", admin, "test")
    assert calls == [("ollama", "small-8b")]
    assert "个" not in out


def test_without_auxiliary_model_repair_uses_work_model(conn):
    admin = _admin_with_models(conn)
    calls = []

    def fake_call(provider, model, system, prompt):
        calls.append((provider, model))
        return json.dumps(["3개월 내 파일럿을 완료한다."])

    with patch.object(language_qa, "_call", side_effect=fake_call):
        language_qa.repair_output(conn, KO + "\n3个月内 파일럿을 완료한다.", "ollama", "big-35b", admin, "test")
    assert calls == [("ollama", "big-35b")]


def test_numeric_repair_goes_to_auxiliary_model(conn):
    admin = _admin_with_models(conn)
    llm.set_auxiliary_llm(conn, admin, "ollama", "small-8b")
    calls = []

    def fake_call(provider, model, system, prompt):
        calls.append((provider, model))
        return "[]"

    text = "마케팅 실적은 예산 대비 47.5% 초과했다."
    with patch.object(language_qa, "_call", side_effect=fake_call):
        numeric_check.repair_output(conn, text, "예산 400, 실적 520", "ollama", "big-35b", admin, "test")
    assert calls and all(c == ("ollama", "small-8b") for c in calls)


def test_auxiliary_config_api_is_admin_only(client_and_conn):
    client, conn = client_and_conn
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]
    model_registry.create_model(conn, admin, "ollama", "small-8b")
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    assert client.get("/api/auxiliary-llm-config").json() == {"provider": None, "model": None, "in_use": False}
    resp = client.put("/api/auxiliary-llm-config", json={"provider": "ollama", "model": "small-8b"})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/auxiliary-llm-config").json() == {"provider": "ollama", "model": "small-8b", "in_use": True}
    assert client.put("/api/auxiliary-llm-config", json={"provider": "ollama", "model": "nope"}).status_code == 400
    assert client.put("/api/auxiliary-llm-config", json={}).status_code == 200  # 해제
    assert client.get("/api/auxiliary-llm-config").json()["provider"] is None
    llm.set_auxiliary_llm(conn, admin, None, None)
