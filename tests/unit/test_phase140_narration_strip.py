"""결과물 영어 작업 독백 제거(2026-11-26, 개선 계획 1-4 — `language_qa.strip_narration`)."""

from __future__ import annotations

import pytest

from hair_platform import language_qa

KO = ("## 채용 공고\n결제 서비스팀에서 백엔드 개발자를 채용합니다. 담당 업무는 결제 API 설계와 운영이며, "
      "자격 요건은 Python 백엔드 개발 3년 이상입니다.\n\n")


@pytest.mark.parametrize("line", [
    "Now I'll run the announcement text through the checker.",  # 인사 실측에서 나온 문장
    "Let me check the requirements first.",
    "I will now verify the numbers with the calculator.",
    "Next, I need to compare the two drafts.",
    "Okay, here is the final version:",
    "Here's my draft:",
])
def test_narration_lines_are_removed(line):
    out, removed = language_qa.strip_narration(KO + line + "\n\n점검 결과: 차별 소지 문구 0건.\n")
    assert removed == 1 and line not in out and "점검 결과" in out and "채용 공고" in out


@pytest.mark.parametrize("keep", [
    "> \"Let me be clear\" — 고객 인용",              # 인용
    "| I will | 표 머리 |",                            # 표
    "# Let me Go 프로젝트",                             # 제목
    "Now I'll 확인합니다",                               # 한글이 섞인 줄
    "Move fast and fix things — 팀 슬로건",             # 독백 아닌 영어
    "The API returns 200 when the request is valid.",  # 독백 아닌 영어 문장
])
def test_non_narration_is_kept(keep):
    text = KO + keep + "\n"
    out, removed = language_qa.strip_narration(text)
    assert removed == 0 and out == text


def test_code_blocks_html_and_non_korean_text_are_untouched():
    code = KO + "```python\nI will run tests\nLet me see\n```\n"
    assert language_qa.strip_narration(code) == (code, 0)
    english = "Now I'll run the checker.\nThis whole answer is English, as the user asked."
    assert language_qa.strip_narration(english) == (english, 0)
    html = "<!doctype html><html><body><p>" + KO + "</p><p>Let me check.</p></body></html>"
    assert language_qa.strip_narration(html) == (html, 0)


def test_repair_output_strips_without_llm_and_audits(client_and_conn, monkeypatch):
    _client, conn = client_and_conn
    monkeypatch.setenv("HAIR_LANGUAGE_QA", "1")
    called = []
    monkeypatch.setattr(language_qa, "_call", lambda *a, **k: called.append(a) or "[]")
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin1'").fetchone()[0]
    out = language_qa.repair_output(conn, KO + "Now I'll run the checker.\n\n결과는 0건입니다.\n", "ollama", "m", admin,
                                    "pipeline_stage")
    assert "Now I'll" not in out and "결과는 0건" in out
    assert called == []  # 한자 등 다른 문제가 없으면 LLM을 부르지 않는다
    row = conn.execute("SELECT after_value FROM audit_log WHERE action = 'language_qa_narration_removed'").fetchone()
    assert row and '"lines": 1' in row[0]
