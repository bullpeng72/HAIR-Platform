"""메일 수신함 읽기 연동(2026-11-26, P1-9 2단계 — `mail_inbox.py`). IMAP 서버는 가짜로 대신한다(hermetic)."""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from hair_platform import knowledge_access, mail_inbox


def _messages():
    m1 = EmailMessage()
    m1["From"] = "김고객 <customer@example.com>"
    m1["To"] = "support@example.com"
    m1["Subject"] = "환불 요청드립니다"
    m1["Date"] = "Wed, 25 Nov 2026 10:00:00 +0900"
    m1.set_content("주문번호 A-123 환불 부탁드립니다.\n감사합니다.")
    m1.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="영수증.pdf")
    m2 = EmailMessage()
    m2["From"] = "partner@example.com"
    m2["Subject"] = "견적 문의"
    m2["Date"] = "Thu, 26 Nov 2026 09:00:00 +0900"
    m2.set_content("<p>견적 <b>부탁</b>드립니다</p>", subtype="html")
    return {b"11": m1.as_bytes(), b"12": m2.as_bytes()}


class FakeIMAP:
    instances: list[FakeIMAP] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []
        self.msgs = _messages()
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(self.msgs)]
        if command == "FETCH":
            uid, spec = args[0].encode(), args[1]
            raw = self.msgs.get(uid)
            if raw is None:
                return "OK", [None]
            if "HEADER.FIELDS" in spec:
                raw = raw.split(b"\n\n", 1)[0] + b"\n\n"
            return "OK", [(b"1 (UID " + uid + b")", raw), b")"]
        raise AssertionError(f"허용되지 않은 IMAP 명령: {command}")  # STORE·COPY·EXPUNGE 등

    def logout(self):
        self.calls.append(("logout",))


@pytest.fixture
def imap(monkeypatch):
    FakeIMAP.instances.clear()
    monkeypatch.setattr(mail_inbox.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("HAIR_MAIL_SUPPORT_PASSWORD", "app-pw")
    return FakeIMAP


def _uid(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _register(conn, visibility="all", name="support"):
    mail_inbox.ensure_schema(conn)
    return mail_inbox.register(conn, _uid(conn, "admin1"), name, "imap.example.com", "support@example.com",
                               "HAIR_MAIL_SUPPORT_PASSWORD", visibility=visibility, description="고객지원 공용 메일함")


def test_search_and_read_are_read_only_and_audited(client_and_conn, imap):
    _client, conn = client_and_conn
    _register(conn)
    member = _uid(conn, "member1")
    with knowledge_access.as_viewer(member):
        listing = mail_inbox.run_action(conn, "search", query="환불")
        opened = mail_inbox.run_action(conn, "read", uid="11")
        html = mail_inbox.run_action(conn, "read", uid="12")
    assert "uid=12" in listing and "환불 요청드립니다" in listing and "김고객" in listing
    assert listing.index("uid=12") < listing.index("uid=11")  # 최신부터
    assert "주문번호 A-123" in opened and "영수증.pdf" in opened and "첨부" in opened
    assert "견적" in html and "부탁" in html and "드립니다" in html  # HTML만 있는 메일은 태그를 벗겨 본문으로
    assert "<b>" not in html
    for inst in imap.instances:
        assert ("select", '"INBOX"', True) in inst.calls  # EXAMINE(읽기 전용)
        fetches = [c for c in inst.calls if c[:2] == ("uid", "FETCH")]
        assert fetches and all("BODY.PEEK" in c[3] for c in fetches)  # 읽음 표시 변경 없음
    rows = conn.execute("SELECT actor_id, after_value FROM audit_log WHERE action = 'mail_inbox_read'").fetchall()
    assert len(rows) == 3 and all(r[0] == member for r in rows)


def test_visibility_and_unknown_requester_are_refused(client_and_conn, imap):
    _client, conn = client_and_conn
    _register(conn, visibility="admin")
    assert "확인할 수 없어" in mail_inbox.run_action(conn, "search")  # 요청자 모름
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        assert "읽을 수 있는 메일함이 없습니다" in mail_inbox.run_action(conn, "search")
        assert "읽을 수 있는 메일함이 없습니다" in mail_inbox.run_action(conn, "mailboxes")
    with knowledge_access.as_viewer(_uid(conn, "admin1")):
        assert "support" in mail_inbox.run_action(conn, "mailboxes")
        assert "uid=" in mail_inbox.run_action(conn, "search")
    assert not imap.instances or all(c[0] != "login" or c[1] == "support@example.com" for i in imap.instances for c in i.calls)


def test_missing_password_and_bad_input(client_and_conn, imap, monkeypatch):
    _client, conn = client_and_conn
    _register(conn)
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        assert "uid는" in mail_inbox.run_action(conn, "read", uid="1; DELETE")
        monkeypatch.delenv("HAIR_MAIL_SUPPORT_PASSWORD")
        assert "환경변수" in mail_inbox.run_action(conn, "search")
    admin = _uid(conn, "admin1")
    with pytest.raises(mail_inbox.MailError):
        mail_inbox.register(conn, admin, "x", "imap.example.com", "a@b.c", "lowercase-env")
    with pytest.raises(mail_inbox.MailError):
        mail_inbox.register(conn, admin, "y", "bad host!", "a@b.c", "HAIR_X_PW")
    with pytest.raises(ValueError):
        mail_inbox.register(conn, admin, "z", "imap.example.com", "a@b.c", "HAIR_X_PW", visibility="everyone")
    assert mail_inbox.get(conn, 1)["password_set"] is False


def test_update_visibility_and_toggle(client_and_conn, imap):
    _client, conn = client_and_conn
    box = _register(conn, visibility="all")
    admin = _uid(conn, "admin1")
    mail_inbox.update(conn, admin, box["id"], visibility="manager")
    with knowledge_access.as_viewer(_uid(conn, "member1")):
        assert "읽을 수 있는 메일함이 없습니다" in mail_inbox.run_action(conn, "search")
    with knowledge_access.as_viewer(_uid(conn, "manager1")):
        assert "uid=" in mail_inbox.run_action(conn, "search")
    mail_inbox.update(conn, admin, box["id"], is_active=False)
    with knowledge_access.as_viewer(admin):
        assert "읽을 수 있는 메일함이 없습니다" in mail_inbox.run_action(conn, "search")


def test_api_rbac_test_endpoint_reset_and_seed(client_and_conn, imap):
    from hair_platform import data_admin

    client, conn = client_and_conn
    assert client.post("/login", json={"username": "manager1", "password": "managerpass"}).status_code == 200
    assert client.get("/api/mail-inboxes").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    r = client.post("/api/mail-inboxes", json={"name": "support", "host": "imap.example.com", "username": "support@example.com",
                                                "password_env": "HAIR_MAIL_SUPPORT_PASSWORD", "visibility": "manager"})
    assert r.status_code == 201 and r.json()["visibility"] == "manager" and r.json()["password_set"] is True
    box = r.json()["id"]
    assert "app-pw" not in str(client.get("/api/mail-inboxes").json())  # 비밀번호 값은 어디에도 나오지 않는다
    t = client.post(f"/api/mail-inboxes/{box}/test")
    assert t.status_code == 200 and t.json()["ok"] is True
    assert client.patch(f"/api/mail-inboxes/{box}", json={"visibility": "nobody"}).status_code == 400
    assert client.patch(f"/api/mail-inboxes/{box}", json={"is_active": False}).json()["is_active"] is False
    assert client.post(f"/api/mail-inboxes/{box}/test").status_code == 400
    holders = {r[0] for r in conn.execute("SELECT role_name FROM agent_pool WHERE skill_tags LIKE '%메일수신함조회%'")}
    assert holders == {"고객지원 상담원", "영업 제안 작성자"}
    data_admin.reset_to_factory_state(conn)
    assert conn.execute("SELECT COUNT(*) FROM mail_inboxes").fetchone()[0] == 0
