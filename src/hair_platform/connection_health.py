"""연결 상태 한눈에(2026-11-26, 개선 계획 2-3).

사내 DB·메일 수신함·캘린더 연결은 등록할 때 "연결 확인"을 누르지 않으면, 비밀번호 환경변수가 비었거나 서버 주소가
바뀌어도 Agent가 조회하다 실패할 때까지 아무도 모른다. 연결을 여는 단계(DB 접속·IMAP 로그인·ICS 받기)의 성공·실패를
"연결 확인" 버튼과 **실제 조회** 모두에서 남기고, 홈 "관리 점검"과 연동 설정 표에 보여 준다.

Agent가 SQL을 틀리거나 메일함 이름을 잘못 쓴 것처럼 연결과 무관한 실패는 남기지 않는다(연결을 연 뒤의 일이므로).
"""

from __future__ import annotations

import sqlite3

KINDS = {
    "data_connections": "사내 DB",
    "mail_inboxes": "메일 수신함",
    "calendar_feeds": "캘린더",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS connection_health ("
        " kind TEXT NOT NULL,"
        " item_id INTEGER NOT NULL,"
        " ok INTEGER NOT NULL,"
        " error TEXT,"
        " checked_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " PRIMARY KEY (kind, item_id))"
    )


def record(conn: sqlite3.Connection, kind: str, item_id: int, ok: bool, error: str | None = None) -> None:
    """마지막 결과만 남긴다(연결마다 한 줄). 기록 실패가 조회를 막지 않는다."""
    try:
        conn.execute(
            "INSERT INTO connection_health (kind, item_id, ok, error, checked_at) VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(kind, item_id) DO UPDATE SET ok = excluded.ok, error = excluded.error, checked_at = excluded.checked_at",
            (kind, item_id, 1 if ok else 0, (error or None) and str(error)[:300]),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def summary(conn: sqlite3.Connection) -> list[dict]:
    """켜진 연결마다 마지막 확인 결과 — status: ok | failed | unchecked."""
    out: list[dict] = []
    for table, label in KINDS.items():
        try:
            rows = conn.execute(
                f"SELECT t.id, t.name, h.ok, h.error, h.checked_at FROM {table} t "  # noqa: S608 — 내부 고정 테이블
                "LEFT JOIN connection_health h ON h.kind = ? AND h.item_id = t.id WHERE t.is_active = 1 ORDER BY t.name",
                (table,),
            ).fetchall()
        except sqlite3.OperationalError:  # 테이블이 생기기 전
            continue
        for r in rows:
            status = "unchecked" if r["ok"] is None else ("ok" if r["ok"] else "failed")
            out.append({"kind": table, "kind_label": label, "id": r["id"], "name": r["name"], "status": status,
                        "error": r["error"], "checked_at": r["checked_at"]})
    return out


def warnings(conn: sqlite3.Connection) -> list[str]:
    """홈 "관리 점검"에 보일 문장 — 실패한 연결은 하나씩, 확인한 적 없는 연결은 묶어서."""
    items = summary(conn)
    out = [f"{i['kind_label']} 「{i['name']}」 연결 실패({i['checked_at']}): {i['error'] or '사유 없음'} — 연동 설정에서 확인하세요"
           for i in items if i["status"] == "failed"]
    unchecked = [f"{i['kind_label']} 「{i['name']}」" for i in items if i["status"] == "unchecked"]
    if unchecked:
        out.append("아직 한 번도 연결을 확인하지 않은 연동: " + ", ".join(unchecked) + " — 연동 설정의 \"연결 확인\"을 눌러 보세요")
    return out
