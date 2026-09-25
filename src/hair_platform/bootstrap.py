"""콜드스타트 부트스트랩 — 신규 배포에서 최초 ADMIN 계정을 만드는 유일한 경로.

코드 재점검(2026-09-26, R3)에서 발견: `users` 테이블이 완전히 비어 있으면
`POST /api/users`(ADMIN 전용)조차 호출할 관리자가 없어 아무도 로그인할 수
없는 콜드스타트 불가 상태였다(테스트는 `conftest.py`가 Python으로 직접
`create_user()`를 호출해 우회했기 때문에 이 갭이 드러나지 않았다).

HTTP 엔드포인트로 제공하면 "배포 직후, 아직 첫 계정을 안 만든" 짧은 시간
동안 누구나 선점할 수 있는 위험한 공격 표면이 되므로, 운영자가 서버에 직접
접근해 실행하는 CLI로만 제공한다(Django `createsuperuser`와 같은 패턴).
`users` 테이블이 이미 비어있지 않으면 거부한다 — 이후 계정 추가는 로그인해서
`POST /api/users`를 쓴다.

사용법:
    python -m hair_platform.bootstrap --db-path hair.db --username admin --password ...
"""

from __future__ import annotations

import argparse
import sys

from hair_platform import auth, db


def bootstrap_admin(db_path: str, username: str, password: str) -> int:
    conn = db.get_connection(db_path)
    db.init_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if count > 0:
        raise RuntimeError(
            "users 테이블이 이미 비어있지 않습니다 — 콜드스타트 부트스트랩은 "
            "최초 1회만 허용됩니다. 이후 계정 추가는 로그인 후 POST /api/users를 쓰세요."
        )
    return auth.create_user(conn, username, password, auth.Role.ADMIN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HAIR-Platform 콜드스타트 — 최초 ADMIN 계정 생성"
        "(users 테이블이 완전히 비어 있을 때만 동작)"
    )
    parser.add_argument("--db-path", required=True, help="SQLite DB 파일 경로")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args(argv)

    try:
        user_id = bootstrap_admin(args.db_path, args.username, args.password)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"ADMIN 계정 생성 완료: id={user_id}, username={args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
