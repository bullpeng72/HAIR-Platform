"""Phase 43 — "호출 받은 사람에게 브라우저 알림이 오고, 알림 클릭시
대화 참가 가능하도록 개선해줘"(2026-10-14).

브라우저 Notification API 자체(권한 요청·표시·클릭 핸들러)는 프런트엔드
전용이라 여기선 검증 대상이 아니다 — 이 테스트는 백엔드
`GET /api/conversations/my-mentions`(=`conversations.
list_my_recent_mentions()`)와 "읽음" 표시(`mark_conversation_read()`,
`GET .../messages`가 호출할 때마다 갱신)를 검증한다.

**실측 버그 재현(2026-10-14, 사용자 보고 "같은 브라우저에서 각각
로그인해 확인하고 있는데, 호출이 안되는 거 같아")**: 최초 설계는
"최초 폴링은 알림 없이 기준점만 잡는다"는 **클라이언트 메모리 커서**로
중복 알림을 막았다. 그런데 실제 재현 상황(admin이 먼저 "@pm_kim"을
세 번 보내고, pm_kim은 그 *뒤에* 로그인해 폴링을 시작)에서는 이미 와
있던 멘션 3건이 전부 "최초 폴링"으로 분류돼 영영 알림이 뜨지 않았다
(DB에 직접 질의해 백엔드가 그 메시지들을 정상적으로 찾아내는 것까지
확인한 뒤, 클라이언트 전용 설계가 원인임을 특정했다). 고친 설계는
"안 읽음" 판정 자체를 서버(`conversation_participants.
last_read_message_id`)가 들고 있어, 멘션이 온 뒤에 폴링을 시작해도
그 대화를 실제로 열기 전까지는 계속 "안 읽음"으로 남는다 — 아래
`test_pending_mentions_sent_before_first_poll_are_still_surfaced()`가
이 정확한 시나리오를 재현한다."""

from __future__ import annotations


def _login(client, username, password):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_my_mentions_empty_when_not_mentioned(client_and_conn):
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/my-mentions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_my_mentions_finds_mention_in_conversation_i_participate_in(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    resp = client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member_id},
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "@member1 이것 좀 봐주세요"},
    )
    assert resp.status_code == 201, resp.text

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/my-mentions")
    assert resp.status_code == 200
    mentions = resp.json()
    assert len(mentions) == 1
    assert mentions[0]["conversation_id"] == conv_id
    assert "@member1" in mentions[0]["content"]


def test_my_mentions_excludes_self_authored_messages(client_and_conn):
    """내가 보낸 메시지에 우연히 내 이름이 들어가도(예: 서명) 스스로에게
    알림이 가면 안 된다. `ai_auto_reply`는 꺼서 범위를 좁힌다 — 자기
    멘션은 더 이상 기본 AI 자동응답을 막지 않으므로(별도로 고친 버그,
    `test_gc_self_mention_does_not_suppress_default_ai` 참고) 켜둔 채로
    두면 AI가 실제로 응답하고, 그 응답 문구에 우연히 "@member1"이
    들어가면(인사말 등) 이 테스트가 검증하려는 것과 무관한 혼선이 생긴다."""
    client, _conn = client_and_conn
    _login(client, "member1", "memberpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    client.patch(f"/api/conversations/{conv_id}/ai-auto-reply", json={"enabled": False})
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "저는 @member1 입니다"},
    )
    assert resp.status_code == 201, resp.text
    resp = client.get("/api/conversations/my-mentions")
    assert resp.json() == []


def test_pending_mentions_sent_before_first_poll_are_still_surfaced(client_and_conn):
    """실측 버그 재현 — admin이 member1을 세 번 멘션한 *뒤에* member1이
    처음으로 로그인해 `/my-mentions`를 폴링해도(이 테스트에서의 "최초
    폴링"), 세 건 모두 보여야 한다. 고친 설계 전에는 "최초 폴링은 기준점만
    잡고 알림 생략"이라는 클라이언트 로직 때문에 이 세 건이 전부 묵살됐다
    — 지금은 그 판단 자체를 서버가 하므로(안 읽었으면 몇 번째 폴링이든
    돌려줌) 이 테스트가 그걸 직접 검증한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member_id},
    )
    for i in range(3):
        resp = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": f"@member1 김 PM? ({i})"},
        )
        assert resp.status_code == 201, resp.text

    # member1은 지금 처음 로그인해 폴링한다(이게 바로 실측 버그 재현 상황).
    _login(client, "member1", "memberpass")
    mentions = client.get("/api/conversations/my-mentions").json()
    assert len(mentions) == 3


def test_opening_conversation_marks_mentions_as_read(client_and_conn):
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    client.post(
        f"/api/conversations/{conv_id}/participants",
        json={"participant_type": "human", "participant_id": member_id},
    )
    client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "@member1 첫 호출"}
    )

    _login(client, "member1", "memberpass")
    assert len(client.get("/api/conversations/my-mentions").json()) == 1

    # 대화를 실제로 연다(메시지 목록 조회) — 이 시점까지 읽음 처리된다.
    resp = client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    assert client.get("/api/conversations/my-mentions").json() == []

    # 그 뒤에 온 새 멘션은 다시 "안 읽음"으로 떠야 한다.
    _login(client, "admin1", "adminpass")
    client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "@member1 두 번째 호출"},
    )
    _login(client, "member1", "memberpass")
    second = client.get("/api/conversations/my-mentions").json()
    assert len(second) == 1
    assert "두 번째 호출" in second[0]["content"]


def test_my_mentions_does_not_leak_mentions_from_conversations_i_am_not_in(
    client_and_conn,
):
    client, _conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "admin만의 방"}).json()[
        "id"
    ]
    # member1을 초대하지 않는다 — 참가자가 아니면 멘션돼도 안 보여야 한다.
    client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "@member1 안 초대됨"}
    )

    _login(client, "member1", "memberpass")
    resp = client.get("/api/conversations/my-mentions")
    assert resp.json() == []


def test_backfill_treats_active_participant_as_already_read(client_and_conn):
    """DB 마이그레이션 백필 검증(`db._backfill_conversation_last_read()`)
    — 이 대화에서 이미 직접 메시지를 보낸 적 있는 참가자는 "적극적으로
    읽고 있었다"고 보고 지금 시점까지 읽음 처리되고(알림 폭탄 방지), 한
    번도 말한 적 없는 참가자는 안 읽음으로 남아(실측 버그의 pm_kim과
    같은 경우) 보류 중인 멘션이 계속 보여야 한다."""
    client, conn = client_and_conn
    _login(client, "admin1", "adminpass")
    conv_id = client.post("/api/conversations", json={"title": "방"}).json()["id"]
    manager_id = conn.execute(
        "SELECT id FROM users WHERE username = 'manager1'"
    ).fetchone()["id"]
    member_id = conn.execute(
        "SELECT id FROM users WHERE username = 'member1'"
    ).fetchone()["id"]
    for uid in (manager_id, member_id):
        client.post(
            f"/api/conversations/{conv_id}/participants",
            json={"participant_type": "human", "participant_id": uid},
        )
    client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "@manager1 @member1 상태 공유"},
    )
    # manager1은 그 멘션 *이후에* 이 대화에서 직접 답했다(= 그 멘션까지는
    # 적극적으로 읽고 있었다는 증거) — 백필은 "지금 시점"이 아니라
    # "참가자 자신의 마지막 메시지 id"를 기준으로 삼아야 한다(아래 주석
    # 참고, 실측으로 바로잡은 지점).
    conn.execute(
        "INSERT INTO conversation_messages (conversation_id, author_type, author_id, content) "
        "VALUES (?, 'human', ?, '네 확인했습니다')",
        (conv_id, manager_id),
    )
    conn.commit()

    # 마이그레이션이 막 이 컬럼을 추가한 것처럼 last_read_message_id를
    # 전부 NULL로 되돌린 뒤 백필을 다시 돌려본다.
    from hair_platform import db

    conn.execute(
        "UPDATE conversation_participants SET last_read_message_id = NULL "
        "WHERE conversation_id = ?",
        (conv_id,),
    )
    conn.commit()
    db._backfill_conversation_last_read(conn)

    _login(client, "manager1", "managerpass")
    assert client.get("/api/conversations/my-mentions").json() == []  # 이미 읽음 처리됨

    _login(client, "member1", "memberpass")
    pending = client.get("/api/conversations/my-mentions").json()
    assert len(pending) == 1  # 한 번도 말한 적 없어 안 읽음으로 남음
