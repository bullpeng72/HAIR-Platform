"""인박스 — 새로 맡은 일·기한(2026-11-28, 신입·일반 사용자 관점). 일이 내게 배정되거나 기한이 다가와도 인박스에 아무것도
뜨지 않던 문제. 사람 당사자만, 끝난 일은 빼고, 오래된 배정은 "새로"가 아니다."""

from __future__ import annotations

from datetime import date, timedelta

from hair_platform import inbox


def _task(conn, member, h, role, due=None, status="in_progress", confirmed_ago_hours=1):
    bid = conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, 'x')", (member,)).lastrowid
    t = conn.execute("INSERT INTO tasks (brief_id, role_name, due_date, status) VALUES (?, ?, ?, ?)", (bid, role, due, status)).lastrowid
    a = conn.execute("INSERT INTO assignments (task_id, resource_type, resource_id, requested_by, status) VALUES (?, 'human', ?, ?, 'confirmed')",
                     (t, h, member)).lastrowid
    conn.execute("INSERT INTO assignment_history (assignment_id, to_status, changed_by, changed_at) "
                 "VALUES (?, 'confirmed', ?, datetime('now', ?))", (a, member, f"-{confirmed_ago_hours} hours"))
    return t


def test_new_assignments_and_due_dates_show_in_inbox(client_and_conn):
    _, conn = client_and_conn
    member = conn.execute("SELECT id FROM users WHERE username = 'member1'").fetchone()[0]
    h = conn.execute("INSERT INTO human_pool (name, role, skill_tags, user_id) VALUES ('본인', 'QA', '[]', ?)", (member,)).lastrowid
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731
    new = _task(conn, member, h, "새 일", due=d(7))
    late = _task(conn, member, h, "늦은 일", due=d(-2), confirmed_ago_hours=200)
    soon = _task(conn, member, h, "내일 기한", due=d(1), confirmed_ago_hours=200)
    _task(conn, member, h, "끝난 일", due=d(-5), status="done")
    _task(conn, member, h, "오래 전 배정, 기한 멀다", due=d(30), confirmed_ago_hours=200)
    conn.commit()
    items = inbox.get_inbox(conn, member, "member")
    due = [(i["task_id"], i["overdue"]) for i in items if i["type"] == "due_soon"]
    assert due == [(late, True), (soon, False)]  # 기한 순, 끝난 일·먼 기한 제외
    assigned = [i["task_id"] for i in items if i["type"] == "assigned_to_me"]
    assert assigned == [new]  # 최근 3일 안에 확정된 것만
    types = [i["type"] for i in items]
    assert types.index("due_soon") < types.index("assigned_to_me")
