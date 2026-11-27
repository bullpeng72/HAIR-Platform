"""데이터 보관기간 정책 — 사용자 결정(2026-09-29, "assignment_history/
audit_log 보관기간 정책"에 대해 "1년").

`assignment_history`는 평범한 테이블이라 그냥 DELETE하면 된다. `audit_log`는
append-only 트리거(`db.py`의 `audit_log_no_delete`)가 모든 DELETE를
막는다 — 실수로/악의적으로 지우는 것을 막기 위한 장치이지, "법적 보관기간이
지난 기록을 정책적으로 정리"하는 것까지 막을 의도는 아니다. 그래서 이
모듈의 `purge_expired()`만이 유일하게 그 트리거를 잠깐 내렸다가 반드시
다시 세우는(에러가 나도 `finally`로 보장) 통제된 경로다 — 애플리케이션의
다른 코드는 여전히 audit_log를 지울 수 없다.

`create_app()` 기동 시 자동 실행한다(`deliverables.reap_orphaned_execution_jobs()`
와 같은 자리, 같은 패턴) — 별도 스케줄러 없이도 "오래된 프로세스가 오래
켜져 있으면 정리된다"는 정도의 보장이면 충분하다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

RETENTION_DAYS = 365
# 외부 입력 본문(고객 메일·웹훅, 2026-11-28) — 실행을 시작한 뒤에는 업무 요청에 이미 옮겨져 있어 원본은 필요 없다.
# 30일 지나면 본문만 비우고 중복 방지 키·상태는 남긴다(대기 중인 것은 건드리지 않는다).
INBOUND_PAYLOAD_DAYS = 30


def purge_expired(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, int]:
    """`RETENTION_DAYS`보다 오래된 `assignment_history`/`audit_log` 행을
    지운다. 아무것도 지울 게 없으면(가장 흔한 경우 — 데모/신규 배포) DB에
    손도 대지 않는다. 지운 게 있으면 admin 계정 하나를 골라 그 사실 자체를
    새 감사로그 항목으로 남긴다(admin이 하나도 없으면 — 부트스트랩 이전 —
    조용히 건너뛴다, FK가 NOT NULL이라 기록할 주체가 없다)."""
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    history_deleted = conn.execute(
        "DELETE FROM assignment_history WHERE changed_at < ?", (cutoff,)
    ).rowcount
    inbound_cut = ((now or datetime.now(UTC)) - timedelta(days=INBOUND_PAYLOAD_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        inbound_cleared = conn.execute(
            "UPDATE inbound_events SET payload = NULL WHERE payload IS NOT NULL AND status != 'queued' "
            "AND received_at < ?", (inbound_cut,)).rowcount
        misses_deleted = conn.execute("DELETE FROM match_misses WHERE created_at < ?", (cutoff,)).rowcount
    except sqlite3.OperationalError:  # 마이그레이션 전
        inbound_cleared = misses_deleted = 0

    # audit_log만 append-only 트리거가 걸려 있다 — 이 함수 안에서만, 반드시
    # 다시 세운다는 전제로 잠깐 내린다.
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    try:
        audit_deleted = conn.execute(
            "DELETE FROM audit_log WHERE created_at < ?", (cutoff,)
        ).rowcount
    finally:
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete "
            "BEFORE DELETE ON audit_log BEGIN "
            "SELECT RAISE(ABORT, 'audit_log is append-only'); END"
        )

    if history_deleted or audit_deleted or inbound_cleared or misses_deleted:
        admin = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if admin is not None:
            from hair_platform import audit

            audit.record(
                conn,
                admin["id"],
                "retention_purge_executed",
                "system",
                None,
                after={
                    "cutoff": cutoff,
                    "retention_days": RETENTION_DAYS,
                    "assignment_history_deleted": history_deleted,
                    "audit_log_deleted": audit_deleted,
                    "inbound_payloads_cleared": inbound_cleared,
                    "match_misses_deleted": misses_deleted,
                },
            )
    # 항상 커밋한다(2026-11-25 버그 수정) — 예전엔 지운 게 있을 때만 커밋해, 지울 게 없는 평소(가장 흔한 경우)에도 위
    # DELETE/트리거 문장이 연 쓰기 트랜잭션이 열린 채 남았다. `hair serve`가 기동 때 이 함수를 부르고 한동안 아무것도
    # 쓰지 않으면 DB에 쓰기 잠금이 계속 걸려, 다른 프로세스(관리 스크립트·`hair reset-data` 등)가 "database is
    # locked"로 실패했다(실측: 기동 뒤 10분 넘게 잠금).
    conn.commit()

    return {
        "assignment_history_deleted": history_deleted,
        "audit_log_deleted": audit_deleted,
        "inbound_payloads_cleared": inbound_cleared,
        "match_misses_deleted": misses_deleted,
    }
