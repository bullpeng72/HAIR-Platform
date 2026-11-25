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
    # 모델 후보 "3个月 파일럿"은 한자를 못 고친(그리고 "内"를 빠뜨린) 판본이라 쓰지 않고(2026-11-25),
    # 두 줄 모두 원문에 사전 보정만 적용된다 — 원문의 뜻("3개월 내")이 그대로 남는다.
    assert "3개월 내 파일럿" in out and "경쟁사 대비" in out
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


_LONG_KO = "이 문서는 3분기 실적 보고서의 요구사항 충족 여부와 수치 정확성을 점검한 결과를 정리한 것이다. " * 2


def test_fragment_pass_fixes_han_left_in_long_sentence_when_model_copies_it_back():
    """2026-11-25 실측: 긴 문장 속 짧은 한자를 모델이 문장째 그대로 복사해 돌려줬다(교정 0건). 남은 줄은
    한자 덩어리만 앞뒤 문맥과 함께 다시 보내 그 자리에 넣을 한국어만 받는다."""
    from hair_platform import language_qa

    line = "담당 부서는 고객 요청을 매주 월요일에 일괄处理했으며, 응답 속도를优化해 평균 처리 시간을 줄였다고 보고했다."
    text = _LONG_KO + "\n\n" + line + "\n"
    calls = []

    def fake(system, prompt):
        calls.append(system)
        if system == language_qa.FRAGMENT_SYSTEM:
            payload = json.loads(prompt.split("\n", 1)[1])
            assert [p["표현"] for p in payload] == ["处理", "优化"] and payload[0]["뒤"].startswith("했으며")
            return '["처리", "최적화"]'
        return json.dumps([line])  # 문장 교정은 그대로 복사(실측 실패 재현)

    out, report = language_qa.repair(text, fake)
    assert "일괄처리했으며" in out and "속도를최적화해" in out
    assert report == {"found": 1, "fixed": 1, "remaining": 0, "error": None}
    assert calls == [language_qa.REPAIR_SYSTEM, language_qa.FRAGMENT_SYSTEM]


def test_model_version_that_kept_han_is_not_used_even_if_dictionary_cleans_it():
    """모델이 한자는 못 고치고 다른 곳만 바꾼 판본("가장" → "가 가장")은 버리고 원문에 사전 보정만 적용."""
    from hair_platform import language_qa

    line = '| 4 | 퍼널 결론 | "가장 약한 구간"으로 재表述 |'
    broken = '| 4 | 퍼널 결론 | "가 가장 약한 구간"으로 재表述 |'
    out, report = language_qa.repair(_LONG_KO + "\n\n" + line + "\n", lambda s, p: json.dumps([broken]))
    assert '"가장 약한 구간"으로 재표현' in out and "가 가장" not in out and report["fixed"] == 1
