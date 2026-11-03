"""Phase 58 — SQLite busy_timeout(사용자 요청, 2026-11-03, "사내망을
통해 다른 사용자도 접속하도록 개선 방안을 제안해줘" → "1번, 2번
진행").

`db.get_connection()`은 프로세스 내부적으로 커넥션 하나를 모든 요청이
공유한다 — `sqlite3.threadsafety == 3`(SERIALIZED, 이 환경에서 직접
확인됨)이라 앱 자신의 스레드 간 경합은 SQLite 뮤텍스가 이미 안전하게
처리한다(데이터 손상 위험 없음). 실제 위험은 DB 파일을 직접 여는
외부 프로세스(백업 스크립트, `sqlite3` CLI로 직접 조회 등 — 이번
세션에도 디버깅 중 실제로 이렇게 한 적 있음)와 쓰기가 겹칠 때다 —
기본 `busy_timeout`(0)에서는 잠금 경합 시 기다리지 않고 즉시
`sqlite3.OperationalError: database is locked`를 던진다.

아래 테스트는 이 정확한 시나리오를 재현한다: 외부 커넥션이 쓰기
트랜잭션을 잠깐 쥐고 있는 동안, 앱의 커넥션(`db.get_connection()`)이
같은 테이블에 쓰기를 시도한다."""

from __future__ import annotations

import sqlite3
import threading
import time

from hair_platform import db


def test_busy_timeout_is_set_on_file_based_connection(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000


def test_app_connection_waits_out_external_write_lock_instead_of_failing_immediately(
    tmp_path,
):
    db_path = str(tmp_path / "hair.db")
    app_conn = db.get_connection(db_path)
    db.init_schema(app_conn)

    # 외부 프로세스를 흉내낸다 — `db.get_connection()`을 거치지 않은
    # 일반 커넥션(busy_timeout 기본값 0). `check_same_thread=False`는
    # 아래 releaser 스레드에서 commit()하기 위함일 뿐, "앱이 이 락을
    # 어떻게 풀지"와는 무관하다(외부 프로세스라면 애초에 별도 프로세스라
    # 이 제약 자체가 없다 — 테스트 내부 구현 편의일 뿐).
    external_conn = sqlite3.connect(db_path, check_same_thread=False)
    external_conn.execute("BEGIN IMMEDIATE")
    external_conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('ext', 'x', 'member')"
    )

    def _release_after_delay():
        time.sleep(0.5)
        external_conn.commit()

    releaser = threading.Thread(target=_release_after_delay)
    releaser.start()

    started = time.monotonic()
    # busy_timeout(5000ms) 덕분에 외부 커넥션이 락을 쥐고 있는 0.5초
    # 동안 기다렸다가, 풀리면 바로 성공해야 한다 — 즉시 실패하면 안 된다.
    app_conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('app', 'x', 'member')"
    )
    app_conn.commit()
    elapsed = time.monotonic() - started
    releaser.join()

    assert elapsed >= 0.4  # 즉시 실패/성공이 아니라 실제로 기다렸다는 증거
    row = app_conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE username = 'app'"
    ).fetchone()
    assert row["c"] == 1


def test_without_busy_timeout_external_lock_fails_immediately(tmp_path):
    """수정 전 동작을 대조군으로 재현 — `busy_timeout=0`인 커넥션은
    외부 락과 겹치면 기다리지 않고 바로 터진다(이 수정이 실제로
    뭘 막아주는지 증명)."""
    db_path = str(tmp_path / "hair.db")
    app_conn = db.get_connection(db_path)
    db.init_schema(app_conn)
    app_conn.execute("PRAGMA busy_timeout=0")

    external_conn = sqlite3.connect(db_path)
    external_conn.execute("BEGIN IMMEDIATE")
    external_conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('ext', 'x', 'member')"
    )

    started = time.monotonic()
    raised = False
    try:
        app_conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('app', 'x', 'member')"
        )
    except sqlite3.OperationalError as exc:
        raised = True
        assert "locked" in str(exc)
    elapsed = time.monotonic() - started

    external_conn.rollback()
    assert raised
    assert elapsed < 0.3
