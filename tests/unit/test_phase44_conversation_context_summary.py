"""Phase 44 — "10건 이전의 메시지는 요약 형태로 전달되도록 개선해줘"
(2026-10-14).

`_build_transcript()`(모든 응답 경로가 공유)는 최근
`_CONTEXT_MESSAGE_LIMIT`(10)건 밖으로 밀려난 메시지를 그냥 버렸다 —
10건보다 긴 대화에서 Agent/Team/기본 AI 전부 오래된 맥락을 전혀 못 봤다.
`conversations.context_summary`/`context_summary_through_id`로 캐시된
요약을 만들어 그 자리를 채운다. 증분 갱신(비용 관리)이 핵심이라 — 창이
그대로면 LLM을 다시 안 부르고, 창이 밀리면 "새로 밀려난 메시지만" 기존
요약에 얹어 갱신하는지를 집중적으로 검증한다."""

from __future__ import annotations

from unittest.mock import patch

from hair_platform import conversations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _send(client, conv_id, text):
    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": text})
    assert resp.status_code == 201, resp.text


def test_no_summary_when_within_window(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    for i in range(5):
        _send(client, conv_id, f"메시지 {i}")

    with patch("hair_platform.llm.call_llm") as mock_summary:
        transcript = conversations._build_transcript(conn, conv_id)
        mock_summary.assert_not_called()
    assert "이전 대화 요약" not in transcript
    assert "메시지 0" in transcript  # 10건 이내라 전부 그대로 보임


def test_summary_generated_once_window_exceeded(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    for i in range(13):  # 10건 창을 넘기려면 11건 이상 필요
        _send(client, conv_id, f"메시지 {i}")

    with patch(
        "hair_platform.llm.call_llm", return_value="요약: 메시지 0~2 관련 논의"
    ) as mock_summary:
        transcript = conversations._build_transcript(conn, conv_id)
        mock_summary.assert_called_once()

    assert "이전 대화 요약: 요약: 메시지 0~2 관련 논의" in transcript
    # 창 안(최근 10건 = 메시지 3~12)은 그대로, 창 밖(0~2)은 원문이 아니라
    # 요약으로만 나와야 한다.
    assert "메시지 3" in transcript
    assert "메시지 12" in transcript
    assert "메시지 0\n" not in transcript and not transcript.startswith(
        "사용자: 메시지 0"
    )

    row = conn.execute(
        "SELECT context_summary, context_summary_through_id FROM conversations WHERE id = ?",
        (conv_id,),
    ).fetchone()
    assert row["context_summary"] == "요약: 메시지 0~2 관련 논의"
    assert row["context_summary_through_id"] is not None


def test_summary_reused_without_llm_call_when_window_unchanged(client_and_conn):
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    for i in range(13):
        _send(client, conv_id, f"메시지 {i}")

    with patch("hair_platform.llm.call_llm", return_value="첫 요약"):
        conversations._build_transcript(conn, conv_id)

    # 그 사이 새 메시지가 하나도 안 늘었으면(창이 안 밀렸으면) 두 번째
    # 호출은 LLM을 다시 부르지 않고 캐시된 요약을 그대로 써야 한다.
    with patch("hair_platform.llm.call_llm") as mock_summary:
        transcript = conversations._build_transcript(conn, conv_id)
        mock_summary.assert_not_called()
    assert "첫 요약" in transcript


def test_summary_incrementally_updated_as_window_slides(client_and_conn):
    """비용 관리 핵심 검증 — 창이 더 밀리면(새 메시지가 더 쌓이면) 처음부터
    다시 요약하지 않고, "새로 밀려난 메시지만" 기존 요약에 얹어 보낸다."""
    client, conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    for i in range(13):
        _send(client, conv_id, f"메시지 {i}")

    with patch("hair_platform.llm.call_llm", return_value="1차 요약(0~2)"):
        conversations._build_transcript(conn, conv_id)

    # 메시지를 3건 더 보내 창을 더 밀어낸다(이제 0~5가 창 밖으로 나감 —
    # 0~2는 이미 요약됐고, 3~5가 새로 밀려난 분).
    for i in range(13, 16):
        _send(client, conv_id, f"메시지 {i}")

    with patch(
        "hair_platform.llm.call_llm", return_value="2차 요약(0~5)"
    ) as mock_summary:
        transcript = conversations._build_transcript(conn, conv_id)
        mock_summary.assert_called_once()
        prompt_sent = mock_summary.call_args[0][
            3
        ]  # call_llm(provider, model, system, prompt)

    # 기존 요약(1차)과, 새로 밀려난 메시지(3~5)만 프롬프트에 들어가야
    # 한다 — 이미 요약에 들어간 0~2 원문을 또 보내면 비용이 매번 커진다.
    assert "1차 요약(0~2)" in prompt_sent
    assert "메시지 3" in prompt_sent
    assert "메시지 5" in prompt_sent
    assert "메시지 0" not in prompt_sent  # 이미 요약된 과거 원문은 재전송 안 함

    assert "2차 요약(0~5)" in transcript
