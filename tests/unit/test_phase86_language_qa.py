"""Phase 86 — 한국어 산출물 언어 품질 검사 + 자동 교정 1회(사용자 요청,
2026-11-20, "1번부터 진행" — 로컬 모델이 한국어 발표자료·제안서에 중국어
"3个月内"·"竞争"·깨진 글자 "옮�니다"를 섞은 실측 사례).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hair_platform import conversations, language_qa

_KO = "사람과 AI가 같은 결재 흐름으로 일한다. 파일럿은 실무 한 영역에서 먼저 가동한다. " * 3


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()["id"]


def test_detects_han_kana_and_broken_chars_only_in_visible_korean_text():
    html = (
        "<!DOCTYPE html><html><head><style>.x{content:'中'}</style></head><body>"
        f"<main class='hair-deck'><p>{_KO}</p><h2>3个月内 파일럿</h2><p>일을 옮�니다</p>"
        "<code>中文代码</code></main></body></html>"
    )
    assert language_qa.find_issue_segments(html) == ["3个月内 파일럿", "일을 옮�니다"]
    md = _KO + "\n경竞争사 대비 우위\n```python\nprint('中文')\n```\n`変数`"
    assert language_qa.find_issue_segments(md) == ["경竞争사 대비 우위"]


def test_skips_non_korean_answers():
    assert language_qa.find_issue_segments("这是中文回答。人工智能平台。" * 10) == []
    assert language_qa.find_issue_segments(_KO) == []  # 문제 없음


def test_repair_replaces_only_flagged_segments_and_keeps_structure():
    html = f"<main class='hair-deck'><p>{_KO}</p><h2>3个月内 파일럿</h2><p>경竞争사 대비</p></main>"
    calls = []

    def fake_call(system, prompt):
        calls.append(prompt)
        return json.dumps(["3개월 내 파일럿", "경쟁사 대비"], ensure_ascii=False)

    out, report = language_qa.repair(html, fake_call)
    assert len(calls) == 1
    assert "<h2>3개월 내 파일럿</h2>" in out and "<p>경쟁사 대비</p>" in out
    assert out.replace("3개월 내 파일럿", "").count("<") == html.count("<")  # 구조 유지
    assert report == {"found": 2, "fixed": 2, "remaining": 0, "error": None}


def test_bad_corrections_fall_back_to_known_fixes_or_keep_original():
    # 사전에 있는 누출어("个月内", "竞争")는 모델 교정이 거절돼도 사전 보정으로 고친다.
    text = _KO + "\n3个月内 파일럿\n경竞争사 대비"
    out, report = language_qa.repair(
        text, lambda s, p: json.dumps(["3个月 파일럿", "경쟁사 대비 우리 회사의 압도적인 우위를 보여주는 새로운 문장"])
    )
    # 모델 후보 "3个月 파일럿"에 사전 보정이 적용된 "3개월 파일럿"이 먼저 채택되고,
    # 모델 후보가 거절된 두 번째는 원문에 사전 보정만 적용된다.
    assert "3개월 파일럿" in out and "경쟁사 대비" in out
    assert report["fixed"] == 2 and report["remaining"] == 0

    # 사전에 없는 한자는 모델 교정이 거절되면 원문을 그대로 둔다.
    text = _KO + "\n藍色 계획"
    out, report = language_qa.repair(text, lambda s, p: json.dumps(["藍色 계획"]))
    assert out == text
    assert report["fixed"] == 0 and report["remaining"] == 1

    out, report = language_qa.repair(text, lambda s, p: "죄송하지만 교정할 수 없습니다")
    assert out == text and report["error"] == "unparseable_response"

    def boom(s, p):
        raise RuntimeError("ollama down")

    out, report = language_qa.repair(text, boom)
    assert out == text and "ollama down" in report["error"]


def test_default_ai_reply_is_repaired_before_saving(client_and_conn):
    _client, conn = client_and_conn
    admin_id = _admin_id(conn)
    conv_id = conversations.create_conversation(conn, admin_id, "언어 QA")["id"]
    conversations.create_message(conn, conv_id, admin_id, "파일럿 계획을 알려줘")
    job_id = conversations.start_default_ai_job(conn, conv_id, admin_id)
    answers = iter([_KO + "\n3个月内 파일럿을 완료한다.", json.dumps(["3개월 내 파일럿을 완료한다."])])
    with patch(
        "hair_platform.llm.resolve_provider_and_model", return_value=("ollama", "m")
    ), patch("hair_platform.llm.call_llm", side_effect=lambda *a, **k: next(answers)):
        conversations.run_default_ai_job(conn, job_id, conv_id, admin_id)

    reply = conn.execute(
        "SELECT content FROM conversation_messages WHERE conversation_id = ? "
        "AND author_type = 'agent' ORDER BY id DESC LIMIT 1",
        (conv_id,),
    ).fetchone()["content"]
    assert "3개월 내 파일럿을 완료한다." in reply and "个" not in reply
    audit = conn.execute(
        "SELECT after_value FROM audit_log WHERE action = 'language_qa_repaired'"
    ).fetchone()
    assert json.loads(audit["after_value"])["fixed"] == 1


def test_disabled_by_env(monkeypatch, client_and_conn):
    _client, conn = client_and_conn
    monkeypatch.setenv("HAIR_LANGUAGE_QA", "0")
    text = _KO + "\n3个月内"
    assert language_qa.repair_output(conn, text, "ollama", "m", 1, "test") == text


# ---------- 모델별 생성 옵션(2026-11-21, "특정 모델만을 위한 하드코딩은 없도록") ----------


class _Resp:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": "답변"}}


def test_ollama_sends_no_sampling_options_unless_registered(monkeypatch):
    """기본값 하드코딩 없음 — 레지스트리에 등록한 모델에만, 등록한 값만 보낸다."""
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")  # 샘플링 값만 본다(출력 한도는 phase94)
    from hair_platform import llm, model_options

    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.clear()
        sent.update(json or {})
        return _Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(model_options, "_CACHE", {})
    llm.call_llm("ollama", "m", "sys", "q")
    assert sent["options"] == {}

    model_options.set_cached("ollama", "m", {"top_k": 20, "top_p": 0.8})
    llm.call_llm("ollama", "m", "sys", "q")
    assert sent["options"] == {"top_k": 20, "top_p": 0.8}
    llm.call_llm("ollama", "other", "sys", "q")  # 다른 모델은 영향 없음
    assert sent["options"] == {}


def test_options_are_filtered_per_provider(monkeypatch):
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")  # 샘플링 값만 본다(출력 한도는 phase94)
    from hair_platform import model_options

    monkeypatch.setattr(model_options, "_CACHE", {})
    for provider in ("ollama", "anthropic", "openai"):
        model_options.set_cached(provider, "m", {"top_k": 40, "top_p": 0.9})
    assert model_options.for_model("ollama", "m") == {"top_k": 40, "top_p": 0.9}
    assert model_options.for_model("anthropic", "m") == {"top_k": 40, "top_p": 0.9}
    assert model_options.for_model("openai", "m") == {"top_p": 0.9}  # top_k 미지원


def test_validate_rejects_unknown_or_out_of_range():
    from hair_platform import model_options

    assert model_options.validate({"top_k": "20", "top_p": None}) == {"top_k": 20}
    for bad in ({"temperature": 1}, {"top_p": 0}, {"top_p": 1.5}, {"top_k": 0}):
        with pytest.raises(ValueError):
            model_options.validate(bad)


def test_crewai_llm_receives_registered_options(monkeypatch):
    import sys
    import types

    from hair_platform import crew_runtime, model_options

    captured = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "crewai", types.SimpleNamespace(LLM=FakeLLM))
    monkeypatch.setattr(model_options, "_CACHE", {})
    crew_runtime._crewai_llm("ollama", "m")
    assert "top_k" not in captured and "top_p" not in captured

    model_options.set_cached("openai", "g", {"top_k": 5, "top_p": 0.7})
    crew_runtime._crewai_llm("openai", "g")
    assert captured["top_p"] == 0.7 and "top_k" not in captured


def test_sampling_options_api_is_admin_only_and_persists(client_and_conn, monkeypatch):
    monkeypatch.setenv("HAIR_OLLAMA_MAX_OUTPUT_TOKENS", "0")  # 샘플링 값만 본다(출력 한도는 phase94)
    from hair_platform import model_options

    client, conn = client_and_conn
    conn.execute("INSERT INTO model_registry (provider, name) VALUES ('ollama', 'opt-test')")
    conn.commit()
    mid = conn.execute("SELECT id FROM model_registry WHERE name='opt-test'").fetchone()[0]
    url = f"/api/model-registry/{mid}/sampling-options"

    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.patch(url, json={"top_k": 20}).status_code == 403

    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.patch(url, json={"top_k": 20, "top_p": 0.8})
    assert r.status_code == 200, r.text
    assert model_options.for_model("ollama", "opt-test") == {"top_k": 20, "top_p": 0.8}
    listed = next(m for m in client.get("/api/model-registry").json() if m["id"] == mid)
    assert listed["sampling_options"] == {"top_k": 20, "top_p": 0.8}

    assert client.patch(url, json={"top_p": 2}).status_code == 422
    assert client.patch(url, json={}).status_code == 200  # 비우면 기본값으로
    assert model_options.for_model("ollama", "opt-test") == {}


def test_repair_call_retries_without_think_when_rejected(monkeypatch):
    """`think` 필드를 모르는 옛 Ollama가 400을 주면 그 필드 없이 다시 보낸다."""
    calls = []

    class _Bad(_Resp):
        status_code = 400
        text = '{"error":"unknown field think"}'

    def fake_post(url, json=None, timeout=None):
        calls.append(dict(json or {}))
        return _Bad() if len(calls) == 1 else _Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    assert language_qa._call("ollama", "m", "s", "p") == "답변"
    assert "think" in calls[0] and "think" not in calls[1]


def test_known_fixes_cover_words_seen_in_proposal_runs():
    """2026-11-21 보강 — 기획서 5회 반복 A/B에서 실제로 나온 누출어. 모델 교정이
    놓쳐도 사전이 마지막으로 고친다."""
    cases = {
        "연运营비 3,000만": "연운영비 3,000만",
        "인건비节省 효과": "인건비절감 효과",
        "제공资料": "제공자료",
        "确保된 经济性": "确保된 경제성",  # 사전에 없는 말은 그대로(모델 교정 몫)
        "총总投资 1.2억": "총투자 1.2억",  # 앞 음절 중복 방지
        "경竞争사": "경쟁사",
        "흑자回수": "흑자회수",
        "매핑表": "매핑표",
    }
    for src, expected in cases.items():
        assert language_qa._apply_known_fixes(src) == expected
